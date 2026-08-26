"""Central configuration for the ParcelPilot AI support backend.

Everything that another engineer might want to tweak (model names, file paths,
the dataset "snapshot time") lives here so there is a single source of truth.

Why a fixed snapshot time?
--------------------------
The assessment workbook says its data is a snapshot taken at
2026-08-16 11:00 Asia/Kolkata. All "is this late?", "is the cancellation
window still open?", SLA-age, etc. calculations must be measured against that
instant - NOT against the real wall clock - otherwise answers would change
every day and become impossible to test. So we treat this constant as "now".
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Load a local .env (secrets like GROQ_API_KEY) if present. Optional: in Docker
# the values arrive as real environment variables, so a missing file is fine.
try:
    from dotenv import load_dotenv

    _BACKEND_DIR = Path(__file__).resolve().parents[1]
    load_dotenv(_BACKEND_DIR / ".env")
    load_dotenv(_BACKEND_DIR.parent / ".env")  # repo-root .env as a fallback
except Exception:
    pass

# --- Time -------------------------------------------------------------------
# IST is UTC+05:30. We hard-code the snapshot instant from the workbook README.
IST = timezone(timedelta(hours=5, minutes=30))
SNAPSHOT_TIME: datetime = datetime(2026, 8, 16, 11, 0, 0, tzinfo=IST)


def now() -> datetime:
    """Return the reference 'now' for the whole system (the dataset snapshot)."""
    return SNAPSHOT_TIME


# --- LLM provider -----------------------------------------------------------
# Which chat backend to use by default: "ollama" (local, free, no key) or
# "groq" (free cloud tier, needs GROQ_API_KEY). The UI can override this
# per-request via a toggle, so this is just the fallback default.
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama").lower()

# --- Ollama (local LLM runtime) --------------------------------------------
# Inside docker-compose the backend reaches Ollama at host "ollama".
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
# Primary chat model must support tool-calling. llama3.1 does; qwen2.5 is a fallback.
CHAT_MODEL: str = os.getenv("CHAT_MODEL", "llama3.1:8b")
EMBED_MODEL: str = os.getenv("EMBED_MODEL", "nomic-embed-text")

# --- Groq (free cloud LLM runtime) -----------------------------------------
# OpenAI-compatible API. Get a free key at https://console.groq.com/keys and
# set GROQ_API_KEY in the environment (or backend/.env). Embeddings still come
# from Ollama - Groq is chat-only. Default model supports tool-calling.
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com")
GROQ_CHAT_MODEL: str = os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-120b")

# --- Paths ------------------------------------------------------------------
# DOC_DIR points at the given data pack ("Doc Folder"). STATE_DIR holds anything
# we generate (SQLite db + Chroma vector store) so the source pack stays read-only.
_DEFAULT_DOC_DIR = Path(__file__).resolve().parents[2] / "Doc Folder"
DOC_DIR: Path = Path(os.getenv("DOC_DIR", str(_DEFAULT_DOC_DIR)))

STATE_DIR: Path = Path(os.getenv("STATE_DIR", str(Path(__file__).resolve().parents[1] / "state")))
DB_PATH: Path = Path(os.getenv("DB_PATH", str(STATE_DIR / "app.db")))
CHROMA_DIR: Path = Path(os.getenv("CHROMA_DIR", str(STATE_DIR / "chroma")))

STATE_DIR.mkdir(parents=True, exist_ok=True)

# --- Retrieval / agent tuning ----------------------------------------------
RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "6"))
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "1000"))       # characters, not tokens (simpler + predictable)
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "150"))
AGENT_MAX_STEPS: int = int(os.getenv("AGENT_MAX_STEPS", "8"))  # safety cap on tool loops

# --- Source authority tiers -------------------------------------------------
# Higher number == more authoritative. Used by the trust layer to resolve
# conflicts between documents. See docs/DECISIONS.md.
AUTHORITY_TIERS: dict[str, int] = {
    "contract": 100,     # customer-specific agreement (overrides general policy)
    "current": 80,       # current policy / SOP (v3 / v4)
    "guide": 60,         # product operations guide
    "deprecated": 20,    # superseded policy (v2) - kept only to detect conflicts
    "historical": 10,    # past ticket resolutions - context only, may be WRONG
}
