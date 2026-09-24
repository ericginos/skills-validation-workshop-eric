"""Centralized Cloud Logging setup for Alaska Department of Snow (ADS).

Configures Python standard logging to output structured JSON logs compatible
with Google Cloud Logging standards (severity, timestamp, sourceLocation, etc.).
"""

import datetime
import json
import logging
import os
import sys
import traceback
from typing import Any, Dict, Optional


class StructuredJsonFormatter(logging.Formatter):
    """JSON log formatter adhering to Google Cloud Logging structured format specifications."""

    # Map Python log levels to Google Cloud Logging Severity levels
    SEVERITY_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a structured JSON object."""
        now = datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc)
        timestamp_str = now.isoformat()

        severity = self.SEVERITY_MAP.get(record.levelno, "DEFAULT")

        # Base structured log entry
        log_entry: Dict[str, Any] = {
            "severity": severity,
            "message": record.getMessage(),
            "timestamp": timestamp_str,
            "logger": record.name,
            "logging.googleapis.com/sourceLocation": {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            },
        }

        # Handle exception information if present
        if record.exc_info:
            log_entry["exception"] = "".join(traceback.format_exception(*record.exc_info))

        # Include custom extra fields if provided
        standard_record_attrs = {
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "id", "levelname", "levelno", "lineno", "module",
            "msecs", "message", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread", "threadName"
        }

        extra_fields = {
            k: v for k, v in record.__dict__.items()
            if k not in standard_record_attrs and not k.startswith("_")
        }
        if extra_fields:
            log_entry["context"] = extra_fields

        return json.dumps(log_entry, default=str)


def setup_logger(
    name: str = "ads_document_synthesis",
    level: int = logging.INFO,
    enable_cloud_logging_client: bool = False,
) -> logging.Logger:
    """Configures and returns a logger instance with structured JSON logging.

    Args:
        name: Name of the logger.
        level: Logging level (e.g. logging.INFO).
        enable_cloud_logging_client: Whether to also initialize Google Cloud Logging client handler.

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid adding duplicate handlers if logger was already configured
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    # Optionally initialize Google Cloud Logging client if running in GCP environment
    if enable_cloud_logging_client or os.environ.get("ENABLE_CLOUD_LOGGING", "").lower() in ("true", "1"):
        try:
            from google.cloud import logging as cloud_logging
            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
            client = cloud_logging.Client(project=project_id)
            client.setup_logging(log_level=level)
        except Exception as err:
            logger.warning(
                f"Could not initialize google-cloud-logging API client; falling back to stdout structured JSON: {err}"
            )

    return logger


def get_logger(name: str = "ads_document_synthesis") -> logging.Logger:
    """Convenience accessor to get or create a structured logger."""
    return setup_logger(name)


# Initialize default module logger
logger = get_logger("ads_document_synthesis")
