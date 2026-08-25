"""FastAPI application: chat (SSE), action confirmation, identities, and signals.

Sessions are kept in memory (a dict) - fine for a single-worker assessment app.
Each session holds the running transcript and any pending (unconfirmed) action.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from . import auth
from .agent import loop as agent_loop
from .agent.prompts import system_prompt
from .ollama_client import health as ollama_health
from .proactive import signals as proactive
from .db import get_conn

app = FastAPI(title="ParcelPilot AI Support")

# Dev convenience; in Docker the frontend is proxied to the same origin.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# session_id -> {"login_id", "messages": [...], "pending": proposal | None}
SESSIONS: dict[str, dict[str, Any]] = {}


class ChatRequest(BaseModel):
    login_id: str
    message: str
    session_id: str | None = None


class ConfirmRequest(BaseModel):
    login_id: str
    session_id: str
    approved: bool


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "ollama": ollama_health()}


@app.get("/api/identities")
def identities() -> dict:
    return {"identities": auth.list_identities()}


@app.get("/api/dataset-meta")
def dataset_meta() -> dict:
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM dataset_meta").fetchall()
        return {"meta": {r["key"]: r["value"] for r in rows}}
    except Exception:
        return {"meta": {}}


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    ctx = auth.get_identity(req.login_id)
    if ctx is None:
        return JSONResponse({"error": "invalid_login"}, status_code=401)

    # Resolve or create the session transcript.
    session_id = req.session_id or uuid.uuid4().hex
    session = SESSIONS.get(session_id)
    if session is None or session.get("login_id") != req.login_id:
        session = {"login_id": req.login_id,
                   "messages": [{"role": "system", "content": system_prompt(ctx)}],
                   "pending": None}
        SESSIONS[session_id] = session

    # A new user message supersedes any un-confirmed action.
    session["pending"] = None
    session["messages"].append({"role": "user", "content": req.message})

    def stream() -> Iterator[str]:
        yield _sse({"type": "session", "session_id": session_id,
                    "role": ctx.role, "user_name": ctx.user_name})
        for event in agent_loop.run(ctx, session["messages"]):
            if event.get("type") == "pending_action":
                session["pending"] = event["action"]
            yield _sse(event)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/confirm")
def confirm_endpoint(req: ConfirmRequest):
    ctx = auth.get_identity(req.login_id)
    if ctx is None:
        return JSONResponse({"error": "invalid_login"}, status_code=401)
    session = SESSIONS.get(req.session_id)
    if session is None or session.get("login_id") != req.login_id:
        return JSONResponse({"error": "unknown_session"}, status_code=404)
    proposal = session.get("pending")
    if not proposal:
        return JSONResponse({"error": "no_pending_action"}, status_code=400)

    def stream() -> Iterator[str]:
        for event in agent_loop.confirm(ctx, session["messages"], proposal, req.approved):
            yield _sse(event)
        session["pending"] = None

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/signals")
def signals_endpoint(login_id: str):
    """Proactive issue detection - internal role only."""
    ctx = auth.get_identity(login_id)
    if ctx is None:
        return JSONResponse({"error": "invalid_login"}, status_code=401)
    if not ctx.is_internal:
        return JSONResponse({"error": "forbidden", "message": "Internal users only."}, status_code=403)
    return proactive.detect_signals()


@app.get("/api/actions")
def actions_log(login_id: str):
    """View the mocked action audit log - internal role only."""
    ctx = auth.get_identity(login_id)
    if ctx is None or not ctx.is_internal:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM actions ORDER BY created_at DESC").fetchall()
    return {"actions": [{k: r[k] for k in r.keys()} for r in rows]}
