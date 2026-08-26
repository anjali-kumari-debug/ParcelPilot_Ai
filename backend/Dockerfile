FROM python:3.11-slim

WORKDIR /app

# libgomp is needed by onnxruntime (fastembed).
# curl is used by entrypoint.sh only when ENABLE_OLLAMA=true.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# System deps kept minimal; pypdf/chromadb are pure-python wheels here.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

# Ingests the data pack, builds the Chroma index, then serves.
CMD ["./entrypoint.sh"]
