#!/usr/bin/env bash

# Legacy parser script (intentionally brittle for refactoring challenge)
# Usage: ./parse_transcript.sh <input_transcript.txt>

set -e

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <input_transcript.txt>"
  exit 1
fi

INPUT_FILE="$1"

if [ ! -f "$INPUT_FILE" ]; then
  echo "Error: file not found: $INPUT_FILE"
  exit 1
fi

mkdir -p output

BASE_NAME=$(basename "$INPUT_FILE")
BASE_NAME="${BASE_NAME%.*}"
OUTPUT_FILE="output/${BASE_NAME}.json"

# Very brittle extraction rules:
# - Requires exact labels and order assumptions
# - Only captures one line per field
# - Minimal escaping
call_id=$(grep -E '^Call ID:' "$INPUT_FILE" | head -n1 | sed 's/^Call ID:[ ]*//')
date=$(grep -E '^Date:' "$INPUT_FILE" | head -n1 | sed 's/^Date:[ ]*//')
customer=$(grep -E '^Customer:' "$INPUT_FILE" | head -n1 | sed 's/^Customer:[ ]*//')
agent=$(grep -E '^Agent:' "$INPUT_FILE" | head -n1 | sed 's/^Agent:[ ]*//')
product=$(grep -E '^Product:' "$INPUT_FILE" | head -n1 | sed 's/^Product:[ ]*//')
issue=$(grep -E '^Issue:' "$INPUT_FILE" | head -n1 | sed 's/^Issue:[ ]*//')
resolution=$(grep -E '^Resolution:' "$INPUT_FILE" | head -n1 | sed 's/^Resolution:[ ]*//')
escalate=$(grep -E '^Escalate:' "$INPUT_FILE" | head -n1 | sed 's/^Escalate:[ ]*//')

# Naive quote escaping for JSON
escape_json() {
  echo "$1" | sed 's/\\/\\\\/g' | sed 's/"/\\"/g'
}

call_id=$(escape_json "$call_id")
date=$(escape_json "$date")
customer=$(escape_json "$customer")
agent=$(escape_json "$agent")
product=$(escape_json "$product")
issue=$(escape_json "$issue")
resolution=$(escape_json "$resolution")
escalate=$(escape_json "$escalate")

cat > "$OUTPUT_FILE" <<EOF
{
  "call_id": "$call_id",
  "date": "$date",
  "customer": "$customer",
  "agent": "$agent",
  "product": "$product",
  "issue": "$issue",
  "resolution": "$resolution",
  "escalate": "$escalate"
}
EOF

echo "Wrote parsed JSON to $OUTPUT_FILE"
