"""Document Processing Core (Gemini API & Parsing Engine) for Alaska Department of Snow (ADS).

Orchestrates the synthesis pipeline:
1. Document Ingestion: Supports PDF bytes and text logs converted to Gemini API parts.
2. Step A: Model Armor input sanitization (security.sanitize_user_input).
3. Step B: Gemini 2.5 Flash synthesis with Pydantic structured output enforcement.
4. Step C: Model Armor output sanitization (security.sanitize_model_output) and DLP PII masking.
5. Step D: BigQuery audit logging via database.insert_synthesis_log.
6. Multithreaded batch document processing support.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import io
import json
import os
from typing import Any, Dict, List, Optional, Union

from google import genai
from google.genai import types
import pypdf

import database
from logger import get_logger
from schemas import DocumentSynthesisOutput, RiskAssessment, StructuredPayload
import security

logger = get_logger("ads_synthesizer")

TARGET_MODEL = "gemini-2.5-flash"
SYSTEM_INSTRUCTION = (
    "You are the senior operational document synthesizer for the Alaska Department of Snow (ADS). "
    "Your responsibility is to analyze dense winter storm logs, dispatcher reports, and operational PDFs. "
    "Extract an accurate executive summary, structured operational payloads (plow dispatches, road closures, "
    "weather conditions, equipment readiness), and provide a thorough public safety risk assessment "
    "(LOW, MEDIUM, HIGH, CRITICAL) with actionable emergency mitigation steps. "
    "Enforce strict data accuracy and follow department safety protocols."
)


class SecurityValidationError(Exception):
    """Raised when incoming document content or prompt is blocked by Model Armor."""
    def __init__(self, message: str, violations: Optional[List[str]] = None, status: str = "BLOCKED"):
        super().__init__(message)
        self.violations = violations or []
        self.status = status


def get_genai_client(
    project_id: Optional[str] = None,
    location: Optional[str] = None,
) -> genai.Client:
    """Initializes and returns a Google GenAI Client configured for Vertex AI.

    Args:
        project_id: GCP project ID.
        location: Regional location for Vertex AI.

    Returns:
        genai.Client instance.
    """
    proj = project_id or os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-02-1c7c179a8d73")
    loc = location or os.environ.get("LOCATION", "us-central1")
    logger.debug(f"Initializing GenAI Vertex client for project={proj}, location={loc}")
    return genai.Client(vertexai=True, project=proj, location=loc)


def extract_text_from_document(
    file_bytes: Union[bytes, str],
    file_type: str,
) -> str:
    """Extracts plain text from document bytes for security scanning and inspection.

    Args:
        file_bytes: Document bytes or raw string.
        file_type: File type ('pdf', 'txt', 'log', etc.).

    Returns:
        Extracted text string.
    """
    if isinstance(file_bytes, str):
        return file_bytes

    normalized_type = file_type.lower().strip(".")
    if normalized_type == "pdf" or "pdf" in normalized_type:
        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            extracted_pages = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    extracted_pages.append(text)
            return "\n".join(extracted_pages)
        except Exception as e:
            logger.warning(f"Could not extract text from PDF via pypdf ({e}). Using raw byte representation.")
            return str(file_bytes[:2048])
    else:
        try:
            return file_bytes.decode("utf-8", errors="replace")
        except Exception:
            return str(file_bytes)


def prepare_content_parts(
    file_bytes: Union[bytes, str],
    file_type: str,
    prompt: Optional[str] = None,
) -> List[Any]:
    """Converts uploaded document bytes or text into standard Gemini API content parts.

    Args:
        file_bytes: Raw bytes or string content.
        file_type: MIME type or extension ('pdf', 'application/pdf', 'txt', 'text/plain').
        prompt: Optional specific user query or instruction.

    Returns:
        List of content parts for gemini models.generate_content.
    """
    contents: List[Any] = []
    norm_type = file_type.lower().strip(".")

    is_pdf = norm_type == "pdf" or "application/pdf" in norm_type

    if is_pdf:
        pdf_data = file_bytes if isinstance(file_bytes, bytes) else file_bytes.encode("latin1")
        part = types.Part.from_bytes(data=pdf_data, mime_type="application/pdf")
        contents.append(part)
    else:
        text_content = file_bytes if isinstance(file_bytes, str) else file_bytes.decode("utf-8", errors="replace")
        contents.append(types.Part.from_text(text=text_content))

    instruction_text = (
        prompt or (
            "Analyze this operational document. Extract an executive operational summary, "
            "all plow dispatches, road/highway closures, equipment status, weather metrics, "
            "and a public safety risk assessment."
        )
    )
    contents.append(instruction_text)
    return contents


def process_document(
    file_bytes: Union[bytes, str],
    file_name: str,
    file_type: str,
    user_prompt: Optional[str] = None,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    raise_on_block: bool = False,
) -> Dict[str, Any]:
    """Full core logic function orchestrating the synthesis pipeline.

    Orchestrates:
    Step A: Security scan of prompt & document content via Model Armor (abort if flagged).
    Step B: Gemini 2.5 Flash document synthesis with Pydantic structured output enforcement.
    Step C: Model Armor output sanitization and DLP sensitive data protection / masking.
    Step D: BigQuery audit logging via database.insert_synthesis_log.

    Args:
        file_bytes: Raw bytes or string content of the document.
        file_name: Name of the uploaded file (e.g. 'storm_dispatch_ak1.pdf').
        file_type: File format or MIME type ('pdf', 'txt', 'log').
        user_prompt: Optional user instructions accompanying the document.
        project_id: Target GCP project ID.
        location: Regional location for Model Armor and Vertex AI.
        raise_on_block: Whether to raise SecurityValidationError if Model Armor blocks input.

    Returns:
        Dict containing synthesis result, metadata, risk assessment, and BigQuery logging info.

    Raises:
        SecurityValidationError: If incoming prompt or document triggers Model Armor violation
                                  and raise_on_block is True.
    """
    logger.info(
        f"Starting document synthesis for '{file_name}' (type={file_type})",
        extra={"file_name": file_name, "file_type": file_type},
    )
    log_history_entries: List[str] = [
        f"[{datetime.now(timezone.utc).isoformat()}] Received document '{file_name}' ({file_type})."
    ]

    # -------------------------------------------------------------------------
    # STEP A: Sanitize User Input & Document Content via Model Armor
    # -------------------------------------------------------------------------
    text_to_scan = extract_text_from_document(file_bytes, file_type)
    scan_target = f"{user_prompt}\n{text_to_scan}".strip() if user_prompt else text_to_scan

    # Limit scan target to 15,000 characters to prevent RPC payload overload if document is huge
    input_security_result = security.sanitize_user_input(
        prompt_text=scan_target[:15000] if scan_target else "Analyze document",
        project_id=project_id,
        location=location,
    )

    if not input_security_result["is_safe"]:
        err_msg = (
            f"Document synthesis aborted: Model Armor blocked input for '{file_name}'. "
            f"Violations: {input_security_result['violations']}"
        )
        logger.warning(
            err_msg,
            extra={
                "file_name": file_name,
                "sanitization_status": "BLOCKED",
                "violations": input_security_result["violations"],
            },
        )
        log_history_entries.append(
            f"[{datetime.now(timezone.utc).isoformat()}] Step A BLOCKED: {input_security_result['violations']}"
        )

        # Log security block event in BigQuery
        blocked_record = database.insert_synthesis_log(
            document_name=file_name,
            operational_summary="DOCUMENT SYNTHESIS ABORTED DUE TO SECURITY POLICY VIOLATION.",
            structured_payload={"security_block": True, "violations": input_security_result["violations"]},
            risk_assessment_level="CRITICAL",
            sanitization_status="BLOCKED",
            log_history=" | ".join(log_history_entries),
            project_id=project_id,
        )

        if raise_on_block:
            raise SecurityValidationError(
                err_msg,
                violations=input_security_result["violations"],
                status="BLOCKED",
            )

        return {
            "synthesis_id": blocked_record["synthesis_id"],
            "status": "BLOCKED",
            "document_name": file_name,
            "sanitization_status": "BLOCKED",
            "risk_assessment_level": "CRITICAL",
            "violations": input_security_result["violations"],
            "operational_summary": "Document synthesis aborted due to enterprise security violations.",
            "structured_payload": None,
            "risk_assessment": None,
            "log_history": " | ".join(log_history_entries),
        }

    log_history_entries.append(
        f"[{datetime.now(timezone.utc).isoformat()}] Step A PASSED: Input cleared Model Armor validation."
    )

    # -------------------------------------------------------------------------
    # STEP B: Gemini 2.5 Flash Synthesis with Pydantic Structured Output
    # -------------------------------------------------------------------------
    client = get_genai_client(project_id=project_id, location=location)
    content_parts = prepare_content_parts(file_bytes, file_type, prompt=user_prompt)

    generate_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=DocumentSynthesisOutput,
        temperature=0.1,
        top_p=0.95,
        system_instruction=SYSTEM_INSTRUCTION,
        safety_settings=security.get_safety_settings(threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE),
    )

    logger.info(
        f"Invoking {TARGET_MODEL} for structured extraction of '{file_name}'",
        extra={"model": TARGET_MODEL, "file_name": file_name},
    )

    response = client.models.generate_content(
        model=TARGET_MODEL,
        contents=content_parts,
        config=generate_config,
    )
    raw_response_text = response.text or "{}"
    log_history_entries.append(
        f"[{datetime.now(timezone.utc).isoformat()}] Step B COMPLETED: Extracted output from {TARGET_MODEL}."
    )

    # Validate output with Pydantic (with retry for transient model issues)
    try:
        synthesis_output = DocumentSynthesisOutput.model_validate_json(raw_response_text)
    except Exception as parse_err:
        logger.warning(f"Initial Pydantic parse failed ({parse_err}). Retrying model generation...")
        try:
            retry_response = client.models.generate_content(
                model=TARGET_MODEL,
                contents=content_parts,
                config=generate_config,
            )
            raw_response_text = retry_response.text or "{}"
            synthesis_output = DocumentSynthesisOutput.model_validate_json(raw_response_text)
        except Exception as retry_err:
            logger.error(f"Pydantic validation error after retry: {retry_err}. Raw: {raw_response_text[:300]}")
            raise ValueError(f"Failed to parse Gemini output against schema: {retry_err}")

    # -------------------------------------------------------------------------
    # STEP C: Model Armor Output Sanitization & DLP PII Masking
    # -------------------------------------------------------------------------
    output_security_result = security.sanitize_model_output(
        response_text=raw_response_text[:15000],
        project_id=project_id,
        location=location,
    )

    sanitization_status = "PASSED" if output_security_result["is_safe"] else "FLAGGED"
    log_history_entries.append(
        f"[{datetime.now(timezone.utc).isoformat()}] Step C Model Armor: {sanitization_status}."
    )

    # DLP & Regex Masking: Scrub driver IDs, phone numbers, GPS coordinates before storage
    raw_payload_dict = synthesis_output.structured_payload.model_dump()
    sanitized_payload_dict = security.sanitize_payload_for_storage(
        raw_payload_dict,
        project_id=project_id,
    )
    sanitized_summary = security.mask_sensitive_data(
        synthesis_output.operational_summary,
        project_id=project_id,
    )
    log_history_entries.append(
        f"[{datetime.now(timezone.utc).isoformat()}] Step C DLP: Sensitive data scrubbed."
    )

    # -------------------------------------------------------------------------
    # STEP D: Write Processed Results & Sanitization Flags to BigQuery
    # -------------------------------------------------------------------------
    combined_log_history = " | ".join(log_history_entries)
    risk_level = synthesis_output.risk_assessment.risk_level.upper()

    bq_record = database.insert_synthesis_log(
        document_name=file_name,
        operational_summary=sanitized_summary,
        structured_payload=sanitized_payload_dict,
        risk_assessment_level=risk_level,
        sanitization_status=sanitization_status,
        log_history=combined_log_history,
        project_id=project_id,
    )

    logger.info(
        f"Document synthesis pipeline succeeded for '{file_name}' (ID: {bq_record['synthesis_id']})",
        extra={
            "synthesis_id": bq_record["synthesis_id"],
            "risk_level": risk_level,
            "sanitization_status": sanitization_status,
        },
    )

    return {
        "synthesis_id": bq_record["synthesis_id"],
        "status": "SUCCESS",
        "document_name": file_name,
        "operational_summary": sanitized_summary,
        "structured_payload": sanitized_payload_dict,
        "risk_assessment": synthesis_output.risk_assessment.model_dump(),
        "sanitization_status": sanitization_status,
        "log_history": combined_log_history,
        "model_used": TARGET_MODEL,
        "timestamp": bq_record["timestamp"],
    }


def process_documents_batch(
    documents: List[Dict[str, Any]],
    max_workers: int = 4,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Synthesizes multiple operational documents concurrently using a thread pool.

    Args:
        documents: List of dicts, each containing:
                   {'file_bytes': bytes/str, 'file_name': str, 'file_type': str, 'user_prompt': Optional[str]}
        max_workers: Maximum concurrent threads.
        project_id: GCP project ID.
        location: Regional location.

    Returns:
        List of synthesis result dictionaries.
    """
    logger.info(f"Batch processing {len(documents)} documents with {max_workers} worker threads.")
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_doc = {
            executor.submit(
                process_document,
                file_bytes=doc["file_bytes"],
                file_name=doc["file_name"],
                file_type=doc.get("file_type", "txt"),
                user_prompt=doc.get("user_prompt"),
                project_id=project_id,
                location=location,
                raise_on_block=False,
            ): doc["file_name"]
            for doc in documents
        }

        for future in as_completed(future_to_doc):
            doc_name = future_to_doc[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as exc:
                logger.error(f"Error processing document '{doc_name}' in batch: {exc}")
                results.append({
                    "document_name": doc_name,
                    "status": "ERROR",
                    "error": str(exc),
                })

    return results
