# Single-container image for Render (and any Docker host).
# Chat: Groq (cloud). Embeddings: in-process fastembed. No Ollama.
#
# Build locally:  docker build -t parcelpilot .
# Run:            docker run -p 8080:8080 -e PORT=8080 -e GROQ_API_KEY=... parcelpilot

# --- Frontend ---------------------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Backend + static UI ----------------------------------------------------
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image so first boot does not hit Hugging Face.
ENV FASTEMBED_CACHE_PATH=/root/.cache/fastembed
ENV HF_HUB_DISABLE_TELEMETRY=1
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

COPY backend/app ./app
COPY backend/entrypoint.sh .
RUN chmod +x entrypoint.sh
COPY --from=frontend /fe/dist /app/static
COPY ["Doc Folder", "/data/docs"]

ENV DOC_DIR=/data/docs
ENV STATE_DIR=/data/state
ENV STATIC_DIR=/app/static
ENV LLM_PROVIDER=groq
ENV EMBED_PROVIDER=fastembed
ENV ENABLE_OLLAMA=false
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["./entrypoint.sh"]
