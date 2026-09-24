"""Alaska Department of Snow (ADS) - Backend Chat Engine (chat_engine.py).

Implements multi-turn chat grounded strictly in uploaded operational documents:
- Grounded initialization with PDF/text document context.
- Model Armor validation on incoming user questions.
- Gemini 2.5 Flash execution with strict safety thresholds (BLOCK_LOW_AND_ABOVE).
- Model Armor & DLP sanitization on model responses.
- Interaction audit logging to BigQuery (ads_operations.chat_logs).
"""

import os
import uuid
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timezone

from google import genai
from google.genai import types

import database
from logger import get_logger
import security
import synthesizer

logger = get_logger("ads_chat_engine")

DEFAULT_PROJECT_ID = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "qwiklabs-gcp-02-1c7c179a8d73"
DEFAULT_LOCATION = os.environ.get("LOCATION") or "us-central1"
DEFAULT_MODEL = "gemini-2.5-flash"

STRICT_SYSTEM_INSTRUCTION = (
    "You are an AI assistant for the Alaska Department of Snow. "
    "Answer user questions ONLY using the facts present in the uploaded document. "
    "If the information is not in the document, state clearly: "
    "'This information is not contained in the uploaded operational document.'"
)


class DocumentChatSession:
    """Manages a multi-turn document-grounded chat session with security and audit logging."""

    def __init__(
        self,
        gemini_chat: Any,
        session_id: str,
        document_name: str,
        system_instruction: str,
        client: Optional[Any] = None,
        model_name: str = DEFAULT_MODEL,
        project_id: str = DEFAULT_PROJECT_ID,
        location: str = DEFAULT_LOCATION,
    ):
        self.gemini_chat = gemini_chat
        self.session_id = session_id
        self.document_name = document_name
        self.system_instruction = system_instruction
        self.client = client
        self.model_name = model_name
        self.project_id = project_id
        self.location = location
        self.history: List[Dict[str, Any]] = []

    def send_message(self, user_message: str) -> Dict[str, Any]:
        """Convenience method delegating to send_chat_message."""
        return send_chat_message(self, user_message)


def initialize_document_chat(
    file_bytes: Union[bytes, str],
    file_type: str = "txt",
    document_name: Optional[str] = None,
    system_instruction: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    model_name: str = DEFAULT_MODEL,
) -> DocumentChatSession:
    """Initializes a document-grounded multi-turn Gemini chat session.

    Args:
        file_bytes: Raw bytes or string content of the operational document.
        file_type: MIME type or extension (e.g., 'application/pdf', 'pdf', 'txt').
        document_name: Display name of the operational file.
        system_instruction: Optional system instruction override.
        session_id: Optional custom session ID. If omitted, generates a UUID.
        project_id: GCP project ID.
        location: GCP regional endpoint.
        model_name: Target Gemini model name.

    Returns:
        DocumentChatSession ready for multi-turn conversations.
    """
    proj = project_id or DEFAULT_PROJECT_ID
    loc = location or DEFAULT_LOCATION
    doc_name = document_name or f"operational_doc_{uuid.uuid4().hex[:8]}"
    sess_id = session_id or f"session-{uuid.uuid4()}"
    sys_instruction = system_instruction or STRICT_SYSTEM_INSTRUCTION

    logger.info(
        f"Initializing document chat session '{sess_id}' for '{doc_name}'",
        extra={"session_id": sess_id, "document_name": doc_name, "model": model_name},
    )

    # 1. Initialize Vertex AI GenAI Client
    client = genai.Client(vertexai=True, project=proj, location=loc)

    # 2. Configure Gemini Safety Settings and GenerateContentConfig
    safety_settings = security.get_safety_settings()
    config = types.GenerateContentConfig(
        system_instruction=sys_instruction,
        safety_settings=safety_settings,
        temperature=0.1,  # Low temperature for strict factual grounding
    )

    # 3. Create Gemini Chat Session
    gemini_chat = client.chats.create(model=model_name, config=config)

    # 4. Prepare Document Part (PDF or Text)
    doc_part = synthesizer.prepare_content_parts(file_bytes=file_bytes, file_type=file_type)

    # 5. Prime the Chat Session with the Operational Document
    init_prompt = (
        "Here is the official Alaska Department of Snow operational document. "
        "Review this document thoroughly. All subsequent answers must be strictly grounded "
        "in the factual details of this document. Please acknowledge receipt."
    )
    prime_contents = [doc_part, init_prompt] if isinstance(doc_part, types.Part) else [f"{doc_part}\n\n{init_prompt}"]

    prime_response = gemini_chat.send_message(prime_contents)
    logger.info(
        f"Document context loaded into chat session '{sess_id}'. Model ack: {prime_response.text[:80]}...",
        extra={"session_id": sess_id, "document_name": doc_name},
    )

    # Return wrapper session
    return DocumentChatSession(
        gemini_chat=gemini_chat,
        session_id=sess_id,
        document_name=doc_name,
        system_instruction=sys_instruction,
        client=client,
        model_name=model_name,
        project_id=proj,
        location=loc,
    )


def send_chat_message(
    chat_session: Union[DocumentChatSession, Any],
    user_message: str,
    document_name: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Sends a user query through the secure document chat pipeline.

    Step A: Validates user_message against Model Armor (security.sanitize_user_input).
            If flagged, returns a security warning without calling the model.
    Step B: Sends the message to the Gemini chat session.
    Step C: Sanitizes model output against Model Armor (security.sanitize_model_output)
            and DLP data masking.
    Step D: Logs the interaction into BigQuery (ads_operations.chat_logs).

    Args:
        chat_session: DocumentChatSession or raw Gemini Chat object.
        user_message: Incoming operator prompt/question.
        document_name: Document name (if chat_session is not DocumentChatSession).
        session_id: Session ID (if chat_session is not DocumentChatSession).
        project_id: GCP project ID.

    Returns:
        Dictionary containing response_text, status, sanitization_status, and metadata.
    """
    turn_id = f"chat-{uuid.uuid4()}"
    ts = datetime.now(timezone.utc).isoformat()
    log_history = []

    # Unpack session attributes
    if isinstance(chat_session, DocumentChatSession):
        raw_chat = chat_session.gemini_chat
        doc_name = chat_session.document_name
        sess_id = chat_session.session_id
        proj = chat_session.project_id
        model_used = chat_session.model_name
    else:
        raw_chat = chat_session
        doc_name = document_name or "operational_document"
        sess_id = session_id or f"session-{uuid.uuid4()}"
        proj = project_id or DEFAULT_PROJECT_ID
        model_used = DEFAULT_MODEL

    log_history.append(f"Turn {turn_id} initiated at {ts}")
    logger.info(
        f"Processing chat message for session '{sess_id}'",
        extra={"chat_id": turn_id, "session_id": sess_id, "document_name": doc_name},
    )

    # -------------------------------------------------------------------------
    # Step A: Model Armor Input Sanitization
    # -------------------------------------------------------------------------
    input_scan = security.sanitize_user_input(prompt_text=user_message, project_id=proj)
    log_history.append(f"Model Armor Input Scan: {input_scan['sanitization_status']}")

    if not input_scan["is_safe"]:
        violations = input_scan.get("violations", ["security_policy_violation"])
        security_warning = (
            f"🚨 [Security Alert]: Your message violated security guidelines ({', '.join(violations)}). "
            f"Model Armor blocked this request to protect ADS systems. The query was not sent to the model."
        )
        log_history.append(f"Blocked by Model Armor. Violations: {violations}")
        logger.warning(
            f"User chat message blocked by Model Armor in session '{sess_id}'",
            extra={"chat_id": turn_id, "violations": violations},
        )

        # Log blocked turn to BigQuery
        database.insert_chat_log(
            session_id=sess_id,
            user_message=user_message,
            model_response=security_warning,
            document_name=doc_name,
            sanitization_status="BLOCKED",
            violations=violations,
            log_history=" | ".join(log_history),
            chat_id=turn_id,
            timestamp=ts,
            project_id=proj,
        )

        res_payload = {
            "status": "BLOCKED",
            "chat_id": turn_id,
            "session_id": sess_id,
            "response_text": security_warning,
            "sanitization_status": "BLOCKED",
            "violations": violations,
            "document_name": doc_name,
            "model_used": model_used,
            "timestamp": ts,
            "log_history": " | ".join(log_history),
        }
        if isinstance(chat_session, DocumentChatSession):
            chat_session.history.append(res_payload)
        return res_payload

    # -------------------------------------------------------------------------
    # Step B: Pass Message to Gemini Chat Session
    # -------------------------------------------------------------------------
    try:
        log_history.append(f"Calling Gemini ({model_used}) multi-turn session")
        model_turn = raw_chat.send_message(user_message)
        raw_output_text = model_turn.text or ""
        log_history.append(f"Gemini response generated ({len(raw_output_text)} chars)")
    except Exception as e:
        logger.error(f"Gemini chat turn failed: {e}", extra={"chat_id": turn_id, "session_id": sess_id})
        error_msg = f"Error generating response from model: {str(e)}"
        log_history.append(f"Gemini Exception: {str(e)}")

        database.insert_chat_log(
            session_id=sess_id,
            user_message=user_message,
            model_response=error_msg,
            document_name=doc_name,
            sanitization_status="ERROR",
            violations=[],
            log_history=" | ".join(log_history),
            chat_id=turn_id,
            timestamp=ts,
            project_id=proj,
        )

        return {
            "status": "ERROR",
            "chat_id": turn_id,
            "session_id": sess_id,
            "response_text": error_msg,
            "sanitization_status": "ERROR",
            "violations": [],
            "document_name": doc_name,
            "model_used": model_used,
            "timestamp": ts,
            "log_history": " | ".join(log_history),
        }

    # -------------------------------------------------------------------------
    # Step C: Model Armor & DLP Output Sanitization
    # -------------------------------------------------------------------------
    output_scan = security.sanitize_model_output(response_text=raw_output_text, project_id=proj)
    sanitization_status = output_scan.get("sanitization_status", "PASSED")
    log_history.append(f"Model Armor Output Scan: {sanitization_status}")

    if not output_scan["is_safe"]:
        violations = output_scan.get("violations", ["unsafe_model_output"])
        warning_response = (
            f"🚨 [Security Alert]: Model output was blocked by Model Armor due to detected violations ({', '.join(violations)})."
        )
        log_history.append(f"Model output blocked. Violations: {violations}")

        database.insert_chat_log(
            session_id=sess_id,
            user_message=user_message,
            model_response=warning_response,
            document_name=doc_name,
            sanitization_status="BLOCKED",
            violations=violations,
            log_history=" | ".join(log_history),
            chat_id=turn_id,
            timestamp=ts,
            project_id=proj,
        )

        res_payload = {
            "status": "BLOCKED",
            "chat_id": turn_id,
            "session_id": sess_id,
            "response_text": warning_response,
            "sanitization_status": "BLOCKED",
            "violations": violations,
            "document_name": doc_name,
            "model_used": model_used,
            "timestamp": ts,
            "log_history": " | ".join(log_history),
        }
        if isinstance(chat_session, DocumentChatSession):
            chat_session.history.append(res_payload)
        return res_payload

    # Apply DLP sensitive data scrubbing on sanitized model output
    clean_text = security.mask_sensitive_data(raw_output_text, project_id=proj)
    if clean_text != raw_output_text:
        log_history.append("DLP masked sensitive SPII in model output")

    # -------------------------------------------------------------------------
    # Step D: Log Interaction to BigQuery (ads_operations.chat_logs)
    # -------------------------------------------------------------------------
    database.insert_chat_log(
        session_id=sess_id,
        user_message=user_message,
        model_response=clean_text,
        document_name=doc_name,
        sanitization_status=sanitization_status,
        violations=[],
        log_history=" | ".join(log_history),
        chat_id=turn_id,
        timestamp=ts,
        project_id=proj,
    )
    log_history.append(f"Logged to BigQuery ads_operations.chat_logs (ID: {turn_id})")

    result = {
        "status": "SUCCESS",
        "chat_id": turn_id,
        "session_id": sess_id,
        "response_text": clean_text,
        "sanitization_status": sanitization_status,
        "violations": [],
        "document_name": doc_name,
        "model_used": model_used,
        "timestamp": ts,
        "log_history": " | ".join(log_history),
    }

    if isinstance(chat_session, DocumentChatSession):
        chat_session.history.append(result)

    logger.info(
        f"Chat message successfully completed in session '{sess_id}' (Turn ID: {turn_id})",
        extra={"chat_id": turn_id, "session_id": sess_id},
    )
    return result
