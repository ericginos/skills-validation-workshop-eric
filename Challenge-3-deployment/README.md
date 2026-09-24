# Customer Servce Parser

Transcript parsing utility used by support operations to convert plain-text call notes into JSON records.

This package provides a shell-based workflow for parsing support call transcripts into structured JSON output files.

## Purpose

The parser reads one transcript text file and attempts to extract key metadata fields:

- call_id
- date
- customer
- agent
- product
- issue
- resolution
- escalate

It writes a JSON file into the local output folder.

## Package contents

- parse_transcript.sh: main parser script.
- transcripts/transcript-clean.txt: sample transcript that follows expected label format.
- transcripts/transcript-messy.txt: sample transcript that demonstrates parser failure modes.
- output/: destination directory for generated JSON files.

## Prerequisites

- Bash-compatible shell (macOS/Linux or WSL).
- Standard Unix text tools available on PATH:
	- grep
	- sed
	- head
	- basename

## Quick start

From this folder:

```bash
chmod +x parse_transcript.sh
./parse_transcript.sh transcripts/transcript-clean.txt
```

Generated file:

```text
output/transcript-clean.json
```

## Run with both sample files

```bash
./parse_transcript.sh transcripts/transcript-clean.txt
./parse_transcript.sh transcripts/transcript-messy.txt
```

## Input format expectations

The script is line-oriented and expects exact labels at the start of lines, such as:

- Call ID:
- Date:
- Customer:
- Agent:
- Product:
- Issue:
- Resolution:
- Escalate:

Only the first match per label is used. Any variation in spelling, punctuation, field order, or multiline values can produce empty or inaccurate results.

## Output behavior

- Output file name is derived from the input file name.
- Files are written to output/<input_filename>.json.
- Existing output files are overwritten.
- Missing fields are emitted as empty strings.

## Known limitations

- No schema validation.
- Minimal error handling.
- No logging beyond a single success line.
- Naive JSON escaping.
- Cannot reliably parse free-form or conversational transcripts.

## Troubleshooting

- Error "file not found": verify the input path is correct.
- Empty JSON fields: confirm transcript labels exactly match expected format.
- Permission denied: run chmod +x parse_transcript.sh once.

## Modernization note

This package has been modernized with an intelligent Gemini API semantic parser, BigQuery warehouse persistence, and an interactive Flask Web UI.

## Web Application (Flask UI)

A modern, responsive Flask-based web interface allows users to upload customer service transcripts, automatically parse them with Gemini AI, persist the structured records into BigQuery (`customer_service.transcripts`), and inspect the extracted metadata.

### Starting the Web UI

```bash
python3 app.py
```

By default, the server runs on port `8080` (accessible via Cloud Shell Web Preview or `http://localhost:8080`). You can configure a custom port via the `PORT` environment variable:

```bash
PORT=8080 python3 app.py
```

### Features

- **File Upload & Drag-and-Drop**: Upload `.txt`, `.log`, or `.json` call transcript files.
- **Direct Text Input**: Alternative paste tab to quickly test raw call transcripts.
- **BigQuery Persistence**: Automatically saves structured records into dataset `customer_service`, table `transcripts`.
- **Posted Metadata Display**: Instantly displays extracted Call ID, Date, Customer, Agent, Product, Issue, Resolution, and Escalation status.
- **Structured JSON & Audit Logs**: Inspect JSON payload and lifecycle stages directly from the web interface.
- **Recent Transcripts History**: Real-time table querying recent BigQuery records.
- **REST API Endpoints**:
  - `POST /api/upload`: Programmatic upload returning JSON metadata.
  - `GET /api/transcripts`: Fetch recent BigQuery records.
  - `GET /health`: System health and BigQuery connectivity check.

