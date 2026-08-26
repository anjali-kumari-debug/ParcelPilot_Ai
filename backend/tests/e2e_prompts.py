"""End-to-end functional test for the ParcelPilot agent (the 13 demo prompts).

What this does
--------------
Replays a fixed battery of natural-language prompts against a RUNNING backend,
each under a specific (mocked) identity, exactly as the UI would:

    POST /api/chat     -> reads the SSE stream (tool_call / tool_result /
                          pending_action / message / done)
    POST /api/confirm  -> for prompts that prepare a state-changing action,
                          auto-confirms it so the action path is exercised too.

For every prompt it records the tools the agent chose, the tool results, the
final answer, citations, confidence, trust notes, and any confirmed action, then
runs a few LIGHT expectation checks (keywords / tools / action executed). Because
the answers are LLM text, keyword checks are treated as SOFT signals ("warn"),
while structural checks (no error, action actually executed) are HARD ("error").

Everything is written to a timestamped JSON file under tests/results/ so you can
diff runs and eyeball anything that drifts after you add a feature.

This is a black-box test: it only speaks HTTP, so start the stack first, e.g.

    docker compose up            # then, in another shell:
    python backend/tests/e2e_prompts.py

    # or against a local dev server / different host:
    python backend/tests/e2e_prompts.py --base-url http://localhost:8000
    python backend/tests/e2e_prompts.py --provider groq --only C1,C6,O3

Exit code is non-zero if any HARD check fails (usable in CI as a smoke gate).
Reference "now" for all data is the dataset snapshot 2026-08-16 11:00 IST.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_BASE_URL = "http://localhost:8000"
REQUEST_TIMEOUT = 240.0  # seconds; multi-step tool loops on a cloud LLM can be slow


# --- Test battery -----------------------------------------------------------
# Each case:
#   id          - short stable handle (used by --only and in the JSON)
#   identity    - login_id from backend/app/auth.py (northstar/lumenworks/beacon/axis/ops)
#   title       - human description of what functionality it exercises
#   prompt      - the natural-language user message
#   confirm     - if True, auto-confirm the prepared action (approved=True)
#   expect_tools- tool names we expect the agent to use (SOFT)
#   expect_any  - list of groups; each group is satisfied if ANY string appears
#                 (case-insensitive) in the final answer (SOFT)
#   expect_absent-strings that must NOT appear in the final answer (SOFT)
#   expect_action-action_type that must be prepared AND executed (HARD when set)
CASES: list[dict[str, Any]] = [
    {
        "id": "C1",
        "identity": "northstar",
        "title": "Cancellation with contract override (no fee)",
        "prompt": "Can I cancel ORD-1001 without a cancellation fee? Explain why.",
        "expect_tools": ["cancellation_eligibility", "search_documents"],
        "expect_any": [
            ["no fee", "no cancellation fee", "without a fee", "free of charge", "0"],
            ["agreement", "contract", "enterprise"],
        ],
    },
    {
        "id": "C2",
        "identity": "northstar",
        "title": "Access control - refuses another account's data",
        "prompt": "What is LumenWorks' P1 SLA, and can I see order ORD-2002?",
        "expect_any": [
            ["can only access", "cannot share", "can't share", "not authorised",
             "another customer", "different account", "authorised parcelpilot"],
        ],
        "expect_absent": ["ORD-2002", "2 business hours", "INR 300"],
    },
    {
        "id": "C3",
        "identity": "northstar",
        "title": "SLA target from contract (beats policy/deprecated)",
        "prompt": "What are my P1 first-response SLA targets?",
        "expect_tools": ["search_documents"],
        "expect_any": [["15 min", "15-minute", "15 minutes"]],
        "expect_absent": ["1 hour", "30 minutes"],
    },
    {
        "id": "C4",
        "identity": "northstar",
        "title": "Known issue - webhook delay (still BOOKED after pickup)",
        "prompt": "A driver picked up my parcel about 10 minutes ago but it still shows BOOKED. Did the pickup fail?",
        "expect_tools": ["search_documents"],
        "expect_any": [["webhook", "KI-211", "20 min", "delay"]],
    },
    {
        "id": "C5",
        "identity": "northstar",
        "title": "State-changing action - escalate (with confirmation)",
        "prompt": "Please escalate ticket TKT-501 - all shipment creation is failing.",
        "confirm": True,
        "expect_action": "create_escalation",
        "expect_any": [["escalat"]],
    },
    {
        "id": "C6",
        "identity": "lumenworks",
        "title": "Service credit with contract override (fixed INR 300)",
        "prompt": "A pickup is hours late due to carrier fault. Am I owed a service credit for ORD-2002?",
        "expect_tools": ["service_credit_check", "search_documents"],
        "expect_any": [["300"]],
    },
    {
        "id": "C7",
        "identity": "lumenworks",
        "title": "Cancellation with SOP fee (no waiver)",
        "prompt": "Can I cancel ORD-2001 without a fee?",
        "expect_tools": ["cancellation_eligibility"],
        "expect_any": [["250", "fee applies", "a fee"]],
    },
    {
        "id": "C8",
        "identity": "lumenworks",
        "title": "Known issue + ignores wrong historical ticket",
        "prompt": "My 4,200-row bulk CSV fails around 70%. What's going on, and is the 3,000-row limit real?",
        "expect_tools": ["search_documents"],
        "expect_any": [["KI-208", "bulk"], ["5,000", "5000", "split", "3,000", "3000"]],
    },
    {
        "id": "C9",
        "identity": "beacon",
        "title": "Cancellation free within window (standard, no contract)",
        "prompt": "Can I cancel ORD-3001 without a fee?",
        "expect_tools": ["cancellation_eligibility"],
        "expect_any": [["no fee", "free", "within 30", "0"]],
    },
    {
        "id": "C10",
        "identity": "axis",
        "title": "Cancellation refused - order already DELIVERED",
        "prompt": "Can I cancel ORD-4001?",
        "expect_tools": ["get_order"],
        "expect_any": [["deliver", "cannot be cancelled", "can't be cancelled", "cannot cancel"]],
    },
    {
        "id": "O1",
        "identity": "ops",
        "title": "Internal - cross-account SLA / P1 triage",
        "prompt": "Which open tickets are SLA-breached or P1, and which should we escalate first?",
        "expect_tools": ["list_tickets"],
        "expect_any": [["TKT-501", "TKT-505"], ["P1", "breach", "escalat"]],
    },
    {
        "id": "O2",
        "identity": "ops",
        "title": "Internal - multi-customer product issue",
        "prompt": "Bulk upload is failing for large CSVs. Which accounts and tickets are affected, and is this a known issue?",
        "expect_any": [["KI-208", "bulk"], ["TKT-502", "TKT-451", "LumenWorks", "multiple"]],
    },
    {
        "id": "O3",
        "identity": "ops",
        "title": "Internal - create follow-up task (with confirmation)",
        "prompt": "Create a follow-up task to run a security review for the possible API key exposure on TKT-505, due tomorrow.",
        "confirm": True,
        "expect_action": "create_followup_task",
        "expect_any": [["follow-up", "follow up", "task", "created"]],
    },
]


# --- SSE plumbing -----------------------------------------------------------

def _stream_sse(client: httpx.Client, url: str, body: dict) -> list[dict]:
    """POST `body` and collect the JSON payloads from the SSE `data:` lines."""
    events: list[dict] = []
    with client.stream("POST", url, json=body) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                # keep-alives / partials - ignore, mirroring the frontend reader
                pass
    return events


def _trim_result(name: str, result: Any) -> Any:
    """Keep tool results readable in JSON: shorten long document passages."""
    if not isinstance(result, dict):
        return result
    if name == "search_documents" and isinstance(result.get("results"), list):
        slim = dict(result)
        slim["results"] = [
            {
                "source_file": r.get("source_file"),
                "doc_version": r.get("doc_version"),
                "authority_tier": r.get("authority_tier"),
                "account_id": r.get("account_id"),
                "text": (str(r.get("text", ""))[:300] + ("…" if len(str(r.get("text", ""))) > 300 else "")),
            }
            for r in result["results"]
        ]
        return slim
    return result


# --- One case ---------------------------------------------------------------

def _distill(events: list[dict]) -> dict:
    """Turn a raw event list into a compact, inspectable summary."""
    session_id = None
    tools: list[dict] = []
    final_message = None
    citations: list = []
    confidence = None
    trust_notes: list = []
    pending_action = None
    action_executed = None
    action_cancelled = False
    error = None

    for ev in events:
        t = ev.get("type")
        if t == "session":
            session_id = ev.get("session_id")
        elif t == "tool_call":
            tools.append({"name": ev.get("name"), "arguments": ev.get("arguments"), "result": None})
        elif t == "tool_result":
            name = ev.get("name")
            res = _trim_result(name, ev.get("result"))
            # attach to the most recent tool of this name that has no result yet
            for entry in reversed(tools):
                if entry["name"] == name and entry["result"] is None:
                    entry["result"] = res
                    break
            else:
                tools.append({"name": name, "arguments": None, "result": res})
        elif t == "pending_action":
            a = ev.get("action") or {}
            pending_action = {
                "action_type": a.get("action_type"),
                "target_id": a.get("target_id"),
                "summary": a.get("summary"),
                "payload": a.get("payload"),
            }
        elif t == "message":
            final_message = ev.get("content")
            citations = ev.get("citations") or []
            confidence = ev.get("confidence")
            trust_notes = ev.get("trust_notes") or []
        elif t == "action_executed":
            action_executed = (ev.get("result") or {}).get("message") or "executed"
        elif t == "action_cancelled":
            action_cancelled = True
        elif t == "error":
            error = ev.get("message")

    return {
        "session_id": session_id,
        "tools": tools,
        "final_message": final_message,
        "citations": citations,
        "confidence": confidence,
        "trust_notes": trust_notes,
        "pending_action": pending_action,
        "action_executed": action_executed,
        "action_cancelled": action_cancelled,
        "error": error,
    }


def _check(case: dict, summary: dict) -> list[dict]:
    """Evaluate light expectations. kind='hard' failures set the run's exit code."""
    checks: list[dict] = []
    text = (summary.get("final_message") or "").lower()
    tool_names = [t["name"] for t in summary["tools"]]

    # HARD: the request itself must have produced a final answer without erroring.
    checks.append({
        "check": "completed_without_error",
        "kind": "hard",
        "ok": summary.get("error") is None and summary.get("final_message") is not None,
        "detail": summary.get("error") or "final message received",
    })

    # HARD: prepared action must be prepared AND executed after confirmation.
    if case.get("expect_action"):
        pa = summary.get("pending_action") or {}
        prepared_ok = pa.get("action_type") == case["expect_action"]
        executed_ok = summary.get("action_executed") is not None
        checks.append({
            "check": f"action_prepared[{case['expect_action']}]",
            "kind": "hard",
            "ok": prepared_ok,
            "detail": f"prepared={pa.get('action_type')}",
        })
        checks.append({
            "check": "action_executed",
            "kind": "hard",
            "ok": executed_ok,
            "detail": summary.get("action_executed") or "not executed",
        })

    # SOFT: expected tools chosen by the agent.
    for tool in case.get("expect_tools", []):
        checks.append({
            "check": f"used_tool[{tool}]",
            "kind": "soft",
            "ok": tool in tool_names,
            "detail": f"tools={tool_names}",
        })

    # SOFT: each group satisfied if ANY of its phrases appears in the answer.
    for group in case.get("expect_any", []):
        hit = next((p for p in group if p.lower() in text), None)
        checks.append({
            "check": "answer_contains_any" + json.dumps(group, ensure_ascii=False),
            "kind": "soft",
            "ok": hit is not None,
            "detail": f"matched={hit!r}",
        })

    # SOFT: forbidden phrases must be absent.
    for phrase in case.get("expect_absent", []):
        checks.append({
            "check": f"answer_absent[{phrase}]",
            "kind": "soft",
            "ok": phrase.lower() not in text,
            "detail": "absent" if phrase.lower() not in text else "PRESENT",
        })

    return checks


def _status(checks: list[dict]) -> str:
    if any(c["kind"] == "hard" and not c["ok"] for c in checks):
        return "error"
    if any(not c["ok"] for c in checks):
        return "warn"
    return "pass"


def run_case(client: httpx.Client, base_url: str, case: dict, provider: str | None) -> dict:
    started = time.monotonic()
    body = {"login_id": case["identity"], "message": case["prompt"], "session_id": None}
    if provider:
        body["provider"] = provider

    try:
        events = _stream_sse(client, f"{base_url}/api/chat", body)
    except Exception as exc:  # network / server down / non-200
        return {
            **_case_meta(case),
            "latency_sec": round(time.monotonic() - started, 2),
            "status": "error",
            "checks": [{"check": "request", "kind": "hard", "ok": False, "detail": str(exc)}],
            "summary": {},
            "raw_events": [],
        }

    summary = _distill(events)

    # If an action was prepared and we're asked to confirm, resume via /api/confirm.
    if case.get("confirm") and summary.get("pending_action") and summary.get("session_id"):
        confirm_body = {
            "login_id": case["identity"],
            "session_id": summary["session_id"],
            "approved": True,
        }
        try:
            confirm_events = _stream_sse(client, f"{base_url}/api/confirm", confirm_body)
            events = events + confirm_events
            confirm_summary = _distill(confirm_events)
            # Merge the confirmation outcome into the primary summary.
            summary["action_executed"] = confirm_summary.get("action_executed")
            summary["action_cancelled"] = confirm_summary.get("action_cancelled")
            if confirm_summary.get("final_message"):
                summary["final_message"] = confirm_summary["final_message"]
            if confirm_summary.get("error"):
                summary["error"] = confirm_summary["error"]
        except Exception as exc:
            summary["error"] = f"confirm failed: {exc}"

    checks = _check(case, summary)
    return {
        **_case_meta(case),
        "latency_sec": round(time.monotonic() - started, 2),
        "status": _status(checks),
        "checks": checks,
        "summary": summary,
        "raw_events": events,
    }


def _case_meta(case: dict) -> dict:
    return {
        "id": case["id"],
        "title": case["title"],
        "identity": case["identity"],
        "prompt": case["prompt"],
        "confirm": bool(case.get("confirm")),
    }


# --- Runner -----------------------------------------------------------------

_SYMBOL = {"pass": "PASS", "warn": "WARN", "error": "FAIL"}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ParcelPilot agent end-to-end prompt test.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"Backend base URL (default {DEFAULT_BASE_URL}).")
    parser.add_argument("--provider", default=None, choices=["groq", "ollama"],
                        help="LLM backend to force (Ollama only if the server has ENABLE_OLLAMA=true).")
    parser.add_argument("--only", default=None,
                        help="Comma-separated case ids to run, e.g. C1,C6,O3.")
    parser.add_argument("--out", default=None,
                        help="Output JSON path (default: tests/results/e2e_<timestamp>.json).")
    args = parser.parse_args(list(argv) if argv is not None else None)

    base_url = args.base_url.rstrip("/")
    wanted = {c.strip() for c in args.only.split(",")} if args.only else None
    cases = [c for c in CASES if wanted is None or c["id"] in wanted]
    if not cases:
        print(f"No cases matched --only={args.only!r}. Known ids: {[c['id'] for c in CASES]}")
        return 2

    # Fail fast with a clear message if the backend isn't reachable.
    try:
        with httpx.Client(timeout=10.0) as probe:
            health = probe.get(f"{base_url}/api/health").json()
        print(f"Backend up at {base_url} | health: {health.get('status')} "
              f"(groq={health.get('groq')}, ollama={health.get('ollama')}, "
              f"enable_ollama={health.get('enable_ollama')}, embed={health.get('embed')})")
    except Exception as exc:
        print(f"ERROR: could not reach backend at {base_url} ({exc}).\n"
              f"Start it first (e.g. `docker compose up`) then re-run.")
        return 2

    print(f"Running {len(cases)} prompts"
          f"{' on provider=' + args.provider if args.provider else ''}…\n")

    results: list[dict] = []
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for case in cases:
            res = run_case(client, base_url, case, args.provider)
            results.append(res)
            soft_fail = sum(1 for c in res["checks"] if c["kind"] == "soft" and not c["ok"])
            hard_fail = sum(1 for c in res["checks"] if c["kind"] == "hard" and not c["ok"])
            print(f"  [{_SYMBOL[res['status']]}] {res['id']:<3} {res['title']}"
                  f"  ({res['latency_sec']}s"
                  f"{f', {hard_fail} hard-fail' if hard_fail else ''}"
                  f"{f', {soft_fail} soft-warn' if soft_fail else ''})")

    counts = {"pass": 0, "warn": 0, "error": 0}
    for r in results:
        counts[r["status"]] += 1

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"e2e_{stamp}.json"
    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "base_url": base_url,
            "provider": args.provider or "server-default",
            "snapshot_reference_time": "2026-08-16 11:00 IST",
            "total": len(results),
            "counts": counts,
        },
        "results": results,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))

    print(f"\nSummary: {counts['pass']} pass, {counts['warn']} warn, {counts['error']} fail")
    print(f"Full transcript written to: {out_path}")
    print("Tip: 'warn' means a soft keyword/tool check missed - open the JSON and "
          "read final_message to decide if the answer is still correct.")

    # Non-zero exit only when a HARD check failed (real functional breakage).
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
