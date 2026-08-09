#!/usr/bin/env bash
# Sets up and sanity-checks syllabus-agent locally.
# Run with `source setup.sh` (not `./setup.sh`) so the venv stays active in
# your shell afterwards.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"
PYTHON_BIN="${PYTHON_BIN:-/opt/anaconda3/bin/python3.12}"

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtualenv at $VENV_DIR (using $PYTHON_BIN)..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo "Creating .env from .env.example — add your real GEMINI_API_KEY (and TAVILY_API_KEY, once you have one) before wiring up real calls."
  cp .env.example .env
else
  echo ".env already exists, leaving it untouched."
fi

echo "Running tests..."
pytest -q

cat <<'EOF'

Setup complete. The venv is active in this shell.

Next steps:
  python -m syllabus_agent.cli "data structures"    # run the pipeline once via CLI
  uvicorn syllabus_agent.main:app --reload           # run the FastAPI server
  # then: curl -X POST localhost:8000/syllabus -H "Content-Type: application/json" -d '{"subject": "data structures"}'
  # or open http://localhost:8000/docs for the Swagger UI
EOF
