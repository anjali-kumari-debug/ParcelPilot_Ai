"""TEMPORARY debug-session logging helper (session ebac8f).

Appends NDJSON lines to DEBUG_LOG_PATH. In Docker we bind-mount the workspace
.cursor dir to /app/.cursor and point DEBUG_LOG_PATH there, so lines land on the
host log file the debugger reads. Safe no-op on any error. Remove after debugging.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_SESSION = "ebac8f"
_PATH = os.getenv(
    "DEBUG_LOG_PATH",
    "/Users/anjalikumari/ParcelPilot_Assessment/.cursor/debug-ebac8f.log",
)
_RUN_ID = os.getenv("DEBUG_RUN_ID", "run1")


def dlog(location: str, message: str, data: dict | None = None, hypothesis: str = "") -> None:
    try:
        entry = {
            "sessionId": _SESSION,
            "runId": _RUN_ID,
            "hypothesisId": hypothesis,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        p = Path(_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass
