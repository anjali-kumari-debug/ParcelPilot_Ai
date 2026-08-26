"""TEMPORARY debug-session logging helper (session cc4176). Remove after debugging.

Appends NDJSON lines to the session log. In Docker we bind-mount the workspace
.cursor dir to /app/.cursor so lines land on the host log file the debugger reads.
Safe no-op on any error.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def dlog_cc(location: str, message: str, data: dict | None = None,
            hypothesis: str = "", runId: str = "run1") -> None:
    entry = {
        "sessionId": "cc4176",
        "runId": runId,
        "hypothesisId": hypothesis,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(entry, default=str) + "\n"
    for raw in (
        "/app/.cursor/debug-cc4176.log",
        "/Users/anjalikumari/ParcelPilot_Assessment/.cursor/debug-cc4176.log",
    ):
        try:
            p = Path(raw)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(line)
        except Exception:
            pass
