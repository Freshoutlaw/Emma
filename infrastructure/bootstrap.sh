#!/usr/bin/env bash
# Emma bootstrap — sets up the Python environment, pulls LLM models, and
# prepares the .env file. Safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "── Emma bootstrap ──────────────────────────────────────────────"

# 1. Python environment
if [ ! -d .venv ]; then
  echo "Creating virtualenv (.venv)…"
  python3 -m venv .venv || python -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate || source .venv/Scripts/activate
python -m pip install --upgrade pip -q
echo "Installing core dependencies…"
pip install -r requirements.txt -q

# 2. Environment file
if [ ! -f .env ]; then
  echo "Creating .env from infrastructure/.env.example"
  cp infrastructure/.env.example .env
else
  echo ".env already exists — leaving it untouched"
fi

# 3. Ollama models (best-effort; skip if Ollama is not installed)
if command -v ollama >/dev/null 2>&1; then
  echo "Pulling Ollama models (this can take a while)…"
  ollama pull "${EMMA_LOCAL_MODEL:-qwen3:5.4b}" || true
  ollama pull "${EMMA_EMBEDDING_MODEL:-nomic-embed-text}" || true
else
  echo "Ollama not found — local inference will be unavailable until it is installed"
  echo "  (https://ollama.com). Groq cloud fallback still works with GROQ_API_KEY."
fi

# 4. Runtime data dir
mkdir -p data

echo ""
echo "✔ Bootstrap complete."
echo "  Run:  uvicorn backend.main:app --host 0.0.0.0 --port 8000"
echo "  HUD:  http://localhost:8000"
echo "  Docs: http://localhost:8000/docs"
