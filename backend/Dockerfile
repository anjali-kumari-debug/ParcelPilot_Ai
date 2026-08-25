FROM python:3.11-slim

WORKDIR /app

# curl is used by entrypoint.sh to wait for Ollama and trigger model pulls.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# System deps kept minimal; pypdf/chromadb are pure-python wheels here.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

# The entrypoint waits for Ollama, pulls models, ingests data, then serves.
CMD ["./entrypoint.sh"]
