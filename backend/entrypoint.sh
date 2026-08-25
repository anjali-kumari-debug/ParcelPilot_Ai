#!/usr/bin/env bash
set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://ollama:11434}"
CHAT_MODEL="${CHAT_MODEL:-llama3.1:8b}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"

echo "[entrypoint] Waiting for Ollama at ${OLLAMA_HOST} ..."
until curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; do
  sleep 2
done
echo "[entrypoint] Ollama is up."

# Pull models and VERIFY they are present (retry a few times). The verify step
# matters because a pull request can return before the model is actually usable.
model_present () {
  curl -sf "${OLLAMA_HOST}/api/tags" 2>/dev/null | grep -q "$1"
}
pull_model () {
  local model="$1"
  for attempt in 1 2 3 4 5; do
    if model_present "${model}"; then
      echo "[entrypoint] Model present: ${model}"; return 0
    fi
    echo "[entrypoint] Pulling model (attempt ${attempt}): ${model}"
    curl -s "${OLLAMA_HOST}/api/pull" -d "{\"name\":\"${model}\"}" >/dev/null || true
    sleep 3
  done
  echo "[entrypoint] WARN: could not confirm model ${model}; continuing anyway."
}
pull_model "${EMBED_MODEL}"
pull_model "${CHAT_MODEL}"

# Build the knowledge base on first boot (idempotent; safe to re-run).
echo "[entrypoint] Ingesting workbook -> SQLite ..."
python -m app.data.ingest_xlsx
echo "[entrypoint] Building document index -> Chroma ..."
python -m app.rag.ingest

echo "[entrypoint] Starting API ..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
