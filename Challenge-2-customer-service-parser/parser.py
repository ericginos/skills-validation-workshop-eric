"""
Semantic customer service transcript parser using the Gemini API and BigQuery.
Extracts structured JSON from unstructured call transcripts, provides automated
error handling with retry logic and fallbacks, logs lifecycle events, and persists
results to BigQuery and local disk.
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

from google import genai
from google.genai import types
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

from models import TranscriptRecord, LogEntry, ProcessingResult

# Configure module-level logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("transcript_parser")

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_DATASET = "customer_service"
DEFAULT_TABLE = "transcripts"
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0  # seconds


def get_default_project_id() -> str:
    """Resolve the active Google Cloud project ID from environment variables."""
    return (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("DEVSHELL_PROJECT_ID")
        or "qwiklabs-gcp-02-55471b419fd7"
    )


def get_gemini_client(project_id: Optional[str] = None) -> genai.Client:
    """
    Initializes and returns a Google GenAI Client.
    Prefers Vertex AI backend when running on Google Cloud Shell / GCP VMs.
    """
    project = project_id or get_default_project_id()
    # If GEMINI_API_KEY is explicitly set, use it; otherwise use Vertex AI authentication
    if os.environ.get("GEMINI_API_KEY"):
        return genai.Client()
    return genai.Client(vertexai=True, project=project, location="us-central1")


def get_bigquery_client(project_id: Optional[str] = None) -> bigquery.Client:
    """Initializes and returns a Google Cloud BigQuery client."""
    project = project_id or get_default_project_id()
    return bigquery.Client(project=project)


def ensure_bigquery_table(
    client: Optional[bigquery.Client] = None,
    dataset_id: str = DEFAULT_DATASET,
    table_id: str = DEFAULT_TABLE
) -> bigquery.Table:
    """
    Ensures that the target dataset and table exist in BigQuery with the expected schema.
    Also creates helper views for alternate naming conventions if needed.
    """
    if client is None:
        client = get_bigquery_client()

    dataset_ref = bigquery.DatasetReference(client.project, dataset_id)
    table_ref = dataset_ref.table(table_id)

    schema = [
        bigquery.SchemaField("call_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("date", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("customer", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("agent", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("product", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("issue", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("resolution", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("escalate", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("parsed_transcript", "RECORD", mode="NULLABLE", fields=[
            bigquery.SchemaField("call_id", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("date", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("customer", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("agent", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("product", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("issue", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("resolution", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("escalate", "STRING", mode="NULLABLE"),
        ]),
        bigquery.SchemaField("original_transcript", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("log_history", "RECORD", mode="REPEATED", fields=[
            bigquery.SchemaField("timestamp", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("level", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("stage", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("message", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("details", "STRING", mode="NULLABLE"),
        ]),
        bigquery.SchemaField("status", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("source_file", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("processed_at", "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("model_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("execution_time_seconds", "FLOAT", mode="NULLABLE"),
    ]

    table = bigquery.Table(table_ref, schema=schema)
    table = client.create_table(table, exists_ok=True)
    return table


def write_result_to_bigquery(
    result: ProcessingResult,
    dataset_id: str = DEFAULT_DATASET,
    table_id: str = DEFAULT_TABLE,
    client: Optional[bigquery.Client] = None
) -> Tuple[bool, Optional[str]]:
    """
    Inserts a ProcessingResult record into BigQuery using load_table_from_json.
    Commits directly to permanent storage so row count, byte size, and table preview
    in the BigQuery console update immediately.
    """
    try:
        if client is None:
            client = get_bigquery_client()

        target_table = ensure_bigquery_table(client, dataset_id, table_id)
        row = result.to_bigquery_row()

        job_config = bigquery.LoadJobConfig(
            schema=target_table.schema,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        # Commit immediately via BigQuery Load Job
        load_job = client.load_table_from_json([row], target_table.reference, job_config=job_config)
        load_job.result()

        # Also keep customer_service_transcripts in sync if table_id is transcripts
        if table_id == DEFAULT_TABLE:
            try:
                alt_table_ref = bigquery.DatasetReference(client.project, dataset_id).table("customer_service_transcripts")
                alt_job = client.load_table_from_json([row], alt_table_ref, job_config=job_config)
                alt_job.result()
            except Exception as alt_err:
                logger.debug(f"Could not mirror to alternate table customer_service_transcripts: {alt_err}")

        logger.info(f"Successfully recorded result to BigQuery: `{client.project}.{dataset_id}.{table_id}` (call_id={result.call_id})")
        return True, None
    except Exception as e:
        logger.warning(f"Load job failed, attempting fallback to insert_rows_json: {e}")
        try:
            table_ref = f"{client.project}.{dataset_id}.{table_id}"
            row = result.to_bigquery_row()
            errors = client.insert_rows_json(table_ref, [row])
            if errors:
                err_msg = f"BigQuery insert error: {errors}"
                logger.error(err_msg)
                return False, err_msg
            logger.info(f"Successfully recorded result via streaming buffer: `{table_ref}`")
            return True, None
        except Exception as fb_err:
            err_msg = f"Failed to persist record to BigQuery: {str(fb_err)}"
            logger.error(err_msg)
            return False, err_msg


def fallback_regex_extraction(text: str) -> TranscriptRecord:
    """
    Fallback parser using regex heuristics if Gemini API is temporarily unavailable.
    Attempts to extract standard keys or return safe defaults.
    """
    import re

    def find_field(pattern: str) -> str:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else ""

    call_id = find_field(r"^(?:Call ID|Support Session Reference|Ticket ID|Reference ID)[:\- ]+(.+)$")
    date = find_field(r"^(?:Date|Recorded on)[:\- ]+(.+)$")
    customer = find_field(r"^(?:Customer|Caller Name)[:\- ]+(.+)$")
    agent = find_field(r"^(?:Agent|Representative)[:\- ]+(.+)$")
    product = find_field(r"^(?:Product|Service)[:\- ]+(.+)$")
    issue = find_field(r"^(?:Issue|Problem Summary)[:\- ]+(.+)$")
    resolution = find_field(r"^(?:Resolution|What happened next)[:\- ]+(.+)$")
    escalate = find_field(r"^(?:Escalate|Escalation)[:\- ]+(.+)$")

    return TranscriptRecord(
        call_id=call_id,
        date=date,
        customer=customer,
        agent=agent,
        product=product,
        issue=issue,
        resolution=resolution,
        escalate=escalate if escalate else "no",
    )


def extract_with_gemini(
    content: str,
    client: genai.Client,
    model_name: str = DEFAULT_MODEL
) -> Tuple[TranscriptRecord, Dict[str, Any]]:
    """
    Calls Gemini API with structured JSON output schema to extract transcript fields.
    Implements exponential backoff retry logic.
    """
    prompt = f"""You are an expert customer service transcript intelligence system.
Analyze the following unstructured customer service transcript and extract all required fields into structured JSON matching the provided schema.

Guidelines:
- call_id: Extract the ticket number, call reference ID, support session code, or generate a sensible reference if mentioned.
- date: Interaction timestamp or date including timezone if specified.
- customer: Name or handle of the customer/caller.
- agent: Name of the support representative or specialist.
- product: The product, service, software, or device discussed.
- issue: Concise summary of the problem, error, or request reported.
- resolution: Specific troubleshooting steps, fixes, or outcomes reached.
- escalate: Determine whether escalation is required ('no', 'yes', or reason/target team if specified).

Transcript:
\"\"\"
{content}
\"\"\"
"""
    last_exception = None
    backoff = INITIAL_BACKOFF

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Calling Gemini API (model={model_name}, attempt={attempt}/{MAX_RETRIES})...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TranscriptRecord,
                    temperature=0.1,
                )
            )
            raw_json = response.text
            record = TranscriptRecord.model_validate_json(raw_json)
            return record, {"attempt": attempt, "raw_response": raw_json}
        except Exception as e:
            last_exception = e
            logger.warning(f"Gemini API attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2

    raise last_exception or RuntimeError("Gemini API call failed after retries")


def parse_transcript(
    content: str,
    source_file: Optional[str] = None,
    model_name: str = DEFAULT_MODEL,
    write_to_bq: bool = True,
    dataset_id: str = DEFAULT_DATASET,
    table_id: str = DEFAULT_TABLE,
    gemini_client: Optional[genai.Client] = None,
    bq_client: Optional[bigquery.Client] = None,
) -> ProcessingResult:
    """
    Full end-to-end processing pipeline:
    1. INITIALIZATION: Setup clients and validate input.
    2. EXTRACTION: Semantic extraction with Gemini API (with automated retry logic).
    3. VALIDATION: Pydantic schema validation.
    4. BIGQUERY: Persistence into BigQuery dataset & table.
    5. COMPLETED / FAILED: Finalize audit log and return ProcessingResult.
    """
    start_time = time.time()
    log_history = []
    status = "SUCCESS"

    def log(stage: str, level: str, message: str, details: Optional[Dict[str, Any]] = None):
        entry = LogEntry(stage=stage, level=level, message=message, details=details)
        log_history.append(entry)
        logger.info(f"[{stage}] {message}")

    # STAGE 1: INITIALIZATION
    log("INITIALIZATION", "INFO", f"Starting transcript processing pipeline (source={source_file or 'in-memory'})")

    if not content or not content.strip():
        err_msg = "Transcript content is empty"
        log("INITIALIZATION", "ERROR", err_msg)
        empty_record = TranscriptRecord()
        return ProcessingResult(
            call_id=None,
            parsed_transcript=empty_record,
            original_transcript=content,
            log_history=log_history,
            status="FAILED",
            source_file=source_file,
            model_name=model_name,
            execution_time_seconds=round(time.time() - start_time, 3),
        )

    # Initialize Gemini client
    try:
        client = gemini_client or get_gemini_client()
        log("INITIALIZATION", "INFO", f"Initialized Gemini API client for model '{model_name}'")
    except Exception as e:
        err_msg = f"Failed to initialize Gemini client: {str(e)}"
        log("INITIALIZATION", "ERROR", err_msg)
        client = None

    # STAGE 2: EXTRACTION
    parsed_record = None
    if client:
        try:
            log("EXTRACTION", "INFO", f"Invoking Gemini semantic extraction using model '{model_name}'")
            parsed_record, diag = extract_with_gemini(content, client, model_name)
            log("EXTRACTION", "INFO", "Gemini semantic extraction completed successfully", diag)
        except Exception as e:
            err_msg = f"Gemini semantic extraction encountered error after retries: {str(e)}"
            log("EXTRACTION", "WARNING", err_msg, {"error": str(e)})
            log("EXTRACTION", "INFO", "Falling back to regex heuristic parser")
            parsed_record = fallback_regex_extraction(content)
            status = "PARTIAL"
    else:
        log("EXTRACTION", "WARNING", "Gemini client unavailable, using regex heuristic fallback")
        parsed_record = fallback_regex_extraction(content)
        status = "PARTIAL"

    # STAGE 3: VALIDATION
    try:
        log("VALIDATION", "INFO", "Validating parsed fields against TranscriptRecord schema")
        # Ensure it's a valid TranscriptRecord instance
        if not isinstance(parsed_record, TranscriptRecord):
            parsed_record = TranscriptRecord.model_validate(parsed_record)
        call_id = parsed_record.call_id or "UNKNOWN"
        log("VALIDATION", "INFO", f"Schema validation passed for Call ID: {call_id}")
    except Exception as e:
        err_msg = f"Validation warning: {str(e)}"
        log("VALIDATION", "WARNING", err_msg)
        call_id = parsed_record.call_id if parsed_record else "UNKNOWN"
        if not parsed_record:
            parsed_record = TranscriptRecord()
            status = "FAILED"

    # Create the ProcessingResult object
    execution_time = round(time.time() - start_time, 3)
    result = ProcessingResult(
        call_id=parsed_record.call_id or None,
        parsed_transcript=parsed_record,
        original_transcript=content,
        log_history=log_history,
        status=status,
        source_file=source_file,
        model_name=model_name,
        execution_time_seconds=execution_time,
    )

    # STAGE 4: BIGQUERY
    if write_to_bq:
        log("BIGQUERY", "INFO", f"Writing extracted data, original transcript, and audit logs to BigQuery dataset '{dataset_id}'")
        bq_success, bq_error = write_result_to_bigquery(
            result, dataset_id=dataset_id, table_id=table_id, client=bq_client
        )
        if bq_success:
            log("BIGQUERY", "INFO", f"Successfully written to BigQuery table `{dataset_id}.{table_id}`")
        else:
            log("BIGQUERY", "WARNING", f"BigQuery write failed: {bq_error}", {"error": bq_error})
            if status == "SUCCESS":
                result.status = "PARTIAL"
    else:
        log("BIGQUERY", "INFO", "Skipping BigQuery persistence (write_to_bq=False)")

    # STAGE 5: COMPLETED
    result.execution_time_seconds = round(time.time() - start_time, 3)
    log("COMPLETED", "INFO", f"Pipeline completed in {result.execution_time_seconds}s with status={result.status}")

    return result


def process_file(
    input_file: str,
    output_dir: str = "output",
    write_to_bq: bool = True,
    model_name: str = DEFAULT_MODEL,
    dataset_id: str = DEFAULT_DATASET,
    table_id: str = DEFAULT_TABLE,
) -> ProcessingResult:
    """
    Processes a transcript file on disk and writes the output JSON file.
    Preserves exact backward compatibility with the legacy shell script output format.
    """
    path = Path(input_file)
    if not path.is_file():
        raise FileNotFoundError(f"Error: file not found: {input_file}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    result = parse_transcript(
        content=content,
        source_file=str(path),
        model_name=model_name,
        write_to_bq=write_to_bq,
        dataset_id=dataset_id,
        table_id=table_id,
    )

    # Write output JSON to output directory
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{path.stem}.json"

    # Emit legacy-compatible structured JSON
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result.to_output_json_dict(), f, indent=2)

    logger.info(f"Wrote parsed JSON to {out_file}")
    return result
