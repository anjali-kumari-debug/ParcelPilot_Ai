#!/usr/bin/env bash
set -euo pipefail

# Render / Docker inject PORT; local default 8000.
PORT="${PORT:-8000}"
ENABLE_OLLAMA="${ENABLE_OLLAMA:-false}"
LLM_PROVIDER="${LLM_PROVIDER:-groq}"
EMBED_PROVIDER="${EMBED_PROVIDER:-fastembed}"
OLLAMA_HOST="${OLLAMA_HOST:-http://ollama:11434}"
CHAT_MODEL="${CHAT_MODEL:-llama3.1:8b}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"

flag_on () {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

if flag_on "${ENABLE_OLLAMA}"; then
  echo "[entrypoint] ENABLE_OLLAMA=true — waiting for Ollama at ${OLLAMA_HOST} ..."
  until curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; do
    sleep 2
  done
  echo "[entrypoint] Ollama is up."

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

  if [ "${LLM_PROVIDER}" = "ollama" ]; then
    pull_model "${CHAT_MODEL}"
  fi
  if [ "${EMBED_PROVIDER}" = "ollama" ]; then
    pull_model "${EMBED_MODEL}"
  fi
else
  echo "[entrypoint] ENABLE_OLLAMA=false — Groq chat + fastembed (skipping Ollama)."
fi

echo "[entrypoint] Ingesting workbook -> SQLite ..."
python -m app.data.ingest_xlsx
echo "[entrypoint] Building document index -> Chroma ..."
python -m app.rag.ingest

echo "[entrypoint] Starting API on 0.0.0.0:${PORT} ..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
