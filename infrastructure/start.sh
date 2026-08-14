#!/bin/bash
# Start Ollama in the background (it proxies cloud-tagged models to
# ollama.com), pre-pull the models Emma actually uses, then run the app.
#
# Fail-fast: if Ollama never becomes ready or a required model can't be
# pulled, exit non-zero so the platform backs off and restarts instead of
# serving an app that is "healthy" but LLM-dead.

set -u

# Ollama authenticates to ollama.com via OLLAMA_API_KEY when proxying
# cloud-tagged models. If the platform injected the key (Render/Railway
# secrets), export it so the background `ollama serve` process inherits it.
if [ -n "${OLLAMA_API_KEY:-}" ]; then
  export OLLAMA_API_KEY
else
  echo "WARNING: OLLAMA_API_KEY is not set - Ollama Cloud models will not authenticate" >&2
fi

# Local fallback + embedding models.  Defaults mirror the deployment env so
# the script also works on a bare `docker run` without those variables set.
LOCAL_MODEL="${EMMA_LOCAL_MODEL:-qwen3.5:2b}"
EMBEDDING_MODEL="${EMMA_EMBEDDING_MODEL:-nomic-embed-text}"

ollama serve &
OLLAMA_PID=$!

ready=0
echo "Waiting for Ollama to start..."
for i in {1..30}; do
  if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "Ollama is ready!"
    ready=1
    break
  fi
  echo "Waiting for Ollama... ($i/30)"
  sleep 2
done

if [ "$ready" -ne 1 ]; then
  echo "ERROR: Ollama did not become ready within 60s" >&2
  kill "$OLLAMA_PID" 2>/dev/null || true
  wait "$OLLAMA_PID" 2>/dev/null || true
  exit 1
fi

# Pre-pull the models Emma uses at runtime: the local fallback (served when
# cloud quota/auth fails or the circuit breaker opens) and the embedding
# model (episodic memory + RAG).  A container that ships without them would
# 404 on the fallback path.  Fail fast if either cannot be pulled.
for model in "$LOCAL_MODEL" "$EMBEDDING_MODEL"; do
  echo "Pulling model: $model"
  if ! ollama pull "$model"; then
    echo "ERROR: failed to pull $model" >&2
    kill "$OLLAMA_PID" 2>/dev/null || true
    wait "$OLLAMA_PID" 2>/dev/null || true
    exit 1
  fi
done

# Start Emma (replaces the shell; ollama serve keeps running as its child).
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
