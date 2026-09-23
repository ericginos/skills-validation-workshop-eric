"""
Data models and schemas for customer service transcript parsing.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TranscriptRecord(BaseModel):
    """Structured representation of extracted customer service transcript data."""
    call_id: str = Field(
        default="",
        description="Unique identifier for the call, ticket, support session, or reference ID."
    )
    date: str = Field(
        default="",
        description="Date and time of the interaction, including timezone if available."
    )
    customer: str = Field(
        default="",
        description="Name of the customer or caller."
    )
    agent: str = Field(
        default="",
        description="Name of the agent, representative, or specialist handling the call."
    )
    product: str = Field(
        default="",
        description="Product, service, or system being discussed."
    )
    issue: str = Field(
        default="",
        description="Concise description or summary of the problem, request, or issue reported."
    )
    resolution: str = Field(
        default="",
        description="Actions taken, troubleshooting steps performed, or resolution reached."
    )
    escalate: str = Field(
        default="no",
        description="Whether the issue requires escalation (e.g. 'no', 'yes', or reason/team if specified)."
    )


class LogEntry(BaseModel):
    """Audit log entry capturing a specific stage in the transcript processing pipeline."""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of the log event."
    )
    level: str = Field(
        default="INFO",
        description="Severity level: INFO, WARNING, ERROR."
    )
    stage: str = Field(
        ...,
        description="Pipeline stage: INITIALIZATION, EXTRACTION, VALIDATION, BIGQUERY, COMPLETED, FAILED."
    )
    message: str = Field(
        ...,
        description="Detailed description of what occurred."
    )
    details: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional structured metadata or diagnostic information."
    )


class ProcessingResult(BaseModel):
    """Complete result of parsing a transcript, including metadata and audit logs."""
    call_id: Optional[str] = None
    parsed_transcript: TranscriptRecord
    original_transcript: str
    log_history: List[LogEntry] = Field(default_factory=list)
    status: str = "SUCCESS"  # SUCCESS, PARTIAL, FAILED
    source_file: Optional[str] = None
    processed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model_name: str = "gemini-2.5-flash"
    execution_time_seconds: float = 0.0

    def to_output_json_dict(self) -> Dict[str, Any]:
        """Returns the dictionary format matching the legacy output schema."""
        return self.parsed_transcript.model_dump()

    def to_bigquery_row(self) -> Dict[str, Any]:
        """Returns the dictionary format matching the BigQuery table schema."""
        import json
        return {
            "call_id": self.call_id or self.parsed_transcript.call_id or "",
            "date": self.parsed_transcript.date,
            "customer": self.parsed_transcript.customer,
            "agent": self.parsed_transcript.agent,
            "product": self.parsed_transcript.product,
            "issue": self.parsed_transcript.issue,
            "resolution": self.parsed_transcript.resolution,
            "escalate": self.parsed_transcript.escalate,
            "parsed_transcript": self.parsed_transcript.model_dump(),
            "original_transcript": self.original_transcript,
            "log_history": [
                {
                    "timestamp": entry.timestamp,
                    "level": entry.level,
                    "stage": entry.stage,
                    "message": entry.message,
                    "details": json.dumps(entry.details) if entry.details is not None else None,
                }
                for entry in self.log_history
            ],
            "status": self.status,
            "source_file": self.source_file,
            "processed_at": self.processed_at,
            "model_name": self.model_name,
            "execution_time_seconds": self.execution_time_seconds,
        }

