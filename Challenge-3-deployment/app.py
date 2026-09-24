"""
Flask Web Application for Customer Service Transcript Parser.
Allows users to upload transcripts, parse them using Gemini AI,
persist structured records into BigQuery, and display/post the extracted metadata.
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash

from parser import (
    parse_transcript,
    get_bigquery_client,
    get_default_project_id,
    DEFAULT_DATASET,
    DEFAULT_TABLE,
    DEFAULT_MODEL,
)

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("transcript_web_ui")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "transcript-parser-secret-key-2026")

# Maximum upload size: 16 MB
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


def fetch_recent_records(limit: int = 10) -> List[Dict[str, Any]]:
    """Fetches recent transcript records from BigQuery."""
    try:
        client = get_bigquery_client()
        query = f"""
            SELECT call_id, date, customer, agent, product, issue, resolution, escalate, status, processed_at
            FROM `{client.project}.{DEFAULT_DATASET}.{DEFAULT_TABLE}`
            ORDER BY processed_at DESC
            LIMIT {limit}
        """
        query_job = client.query(query)
        rows = list(query_job.result())
        records = []
        for row in rows:
            record_dict = dict(row)
            if record_dict.get("processed_at"):
                record_dict["processed_at"] = record_dict["processed_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
            records.append(record_dict)
        return records
    except Exception as e:
        logger.warning(f"Could not retrieve recent records from BigQuery: {e}")
        return []


@app.route("/", methods=["GET"])
def index():
    """Renders the main upload page with recent history."""
    recent_records = fetch_recent_records(limit=8)
    project_id = get_default_project_id()
    return render_template(
        "index.html",
        project_id=project_id,
        dataset_id=DEFAULT_DATASET,
        table_id=DEFAULT_TABLE,
        recent_records=recent_records,
        result=None,
    )


@app.route("/upload", methods=["POST"])
def upload():
    """
    Handles transcript upload via file or direct text paste.
    Parses with Gemini semantic pipeline, persists to BigQuery,
    and posts the extracted metadata back to the user interface.
    """
    transcript_text = ""
    source_filename = "manual_entry.txt"

    # Check for uploaded file
    uploaded_file = request.files.get("transcript_file")
    if uploaded_file and uploaded_file.filename:
        source_filename = uploaded_file.filename
        try:
            transcript_text = uploaded_file.read().decode("utf-8", errors="replace")
        except Exception as e:
            flash(f"Error reading uploaded file: {str(e)}", "danger")
            return redirect(url_for("index"))

    # If no file uploaded, check for pasted text input
    pasted_text = request.form.get("transcript_text", "").strip()
    if not transcript_text and pasted_text:
        transcript_text = pasted_text
        source_filename = request.form.get("source_name", "pasted_transcript.txt").strip() or "pasted_transcript.txt"

    if not transcript_text.strip():
        flash("Please upload a transcript file or paste transcript text to process.", "warning")
        return redirect(url_for("index"))

    # Execute end-to-end parsing & BigQuery storage
    try:
        logger.info(f"Processing transcript upload: {source_filename} ({len(transcript_text)} chars)")
        result = parse_transcript(
            content=transcript_text,
            source_file=source_filename,
            model_name=DEFAULT_MODEL,
            write_to_bq=True,
            dataset_id=DEFAULT_DATASET,
            table_id=DEFAULT_TABLE,
        )

        metadata_dict = result.parsed_transcript.model_dump()
        raw_json_str = json.dumps(metadata_dict, indent=2)

        recent_records = fetch_recent_records(limit=8)
        project_id = get_default_project_id()

        return render_template(
            "index.html",
            project_id=project_id,
            dataset_id=DEFAULT_DATASET,
            table_id=DEFAULT_TABLE,
            recent_records=recent_records,
            result=result,
            metadata=metadata_dict,
            raw_json=raw_json_str,
            source_filename=source_filename,
        )

    except Exception as e:
        logger.error(f"Error processing transcript: {e}", exc_info=True)
        flash(f"An unexpected error occurred during processing: {str(e)}", "danger")
        return redirect(url_for("index"))


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """
    REST API endpoint for programmatic uploads.
    Accepts multipart/form-data or JSON payloads and returns structured metadata.
    """
    transcript_text = ""
    source_filename = "api_request.txt"

    if request.is_json:
        data = request.get_json(silent=True) or {}
        transcript_text = data.get("transcript", "")
        source_filename = data.get("filename", "api_payload.txt")
    elif "transcript_file" in request.files:
        f = request.files["transcript_file"]
        source_filename = f.filename or "api_upload.txt"
        transcript_text = f.read().decode("utf-8", errors="replace")
    elif "transcript" in request.form:
        transcript_text = request.form.get("transcript", "")
        source_filename = request.form.get("filename", "api_form.txt")

    if not transcript_text.strip():
        return jsonify({"success": False, "error": "No transcript content provided."}), 400

    try:
        result = parse_transcript(
            content=transcript_text,
            source_file=source_filename,
            model_name=DEFAULT_MODEL,
            write_to_bq=True,
            dataset_id=DEFAULT_DATASET,
            table_id=DEFAULT_TABLE,
        )

        return jsonify({
            "success": True,
            "status": result.status,
            "call_id": result.call_id,
            "metadata": result.parsed_transcript.model_dump(),
            "execution_time_seconds": result.execution_time_seconds,
            "bigquery": {
                "dataset": DEFAULT_DATASET,
                "table": DEFAULT_TABLE,
                "persisted": True,
            },
            "log_history": [entry.model_dump() for entry in result.log_history],
        }), 200

    except Exception as e:
        logger.error(f"API processing error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/transcripts", methods=["GET"])
def api_transcripts():
    """API endpoint to retrieve recent stored records from BigQuery."""
    limit = request.args.get("limit", default=10, type=int)
    records = fetch_recent_records(limit=min(limit, 50))
    return jsonify({"success": True, "count": len(records), "records": records})


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "customer-service-transcript-parser",
        "project": get_default_project_id(),
        "bigquery_dataset": DEFAULT_DATASET,
        "bigquery_table": DEFAULT_TABLE,
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Flask server on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
