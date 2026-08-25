"""Trust & Reliability helpers (Problem 2).

The agent prompt tells the model HOW to weigh sources; this module gives us
deterministic, code-side support for:
  * de-duplicating and ranking retrieved passages by authority,
  * detecting version conflicts (a CURRENT doc and a DEPRECATED doc both matched),
  * building the citation list shown to the user,
  * a heuristic confidence signal used to nudge toward escalation.

Keeping this in code (not just the prompt) is the whole point of Problem 2: we
make source-reliability decisions we can explain and test.
"""

from __future__ import annotations

from typing import Any

from .. import config


def collect_citations(retrieved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce all retrieved chunks to a unique, authority-sorted citation list."""
    by_source: dict[str, dict[str, Any]] = {}
    for hit in retrieved:
        src = hit.get("source_file")
        if not src:
            continue
        existing = by_source.get(src)
        if existing is None or hit.get("authority_rank", 0) > existing.get("authority_rank", 0):
            by_source[src] = {
                "source_file": src,
                "doc_version": hit.get("doc_version"),
                "authority_tier": hit.get("authority_tier"),
                "authority_rank": hit.get("authority_rank", 0),
                "page": hit.get("page"),
            }
    citations = list(by_source.values())
    citations.sort(key=lambda c: -c["authority_rank"])
    return citations


def analyze(retrieved: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise the reliability picture of what was retrieved."""
    tiers = [h.get("authority_tier") for h in retrieved]
    has_current = "current" in tiers or "contract" in tiers
    has_deprecated = "deprecated" in tiers
    best_rank = max((h.get("authority_rank", 0) for h in retrieved), default=0)

    # Simple, explainable confidence heuristic.
    if not retrieved:
        confidence = "low"
    elif best_rank >= config.AUTHORITY_TIERS["current"]:
        confidence = "high"
    elif best_rank >= config.AUTHORITY_TIERS["guide"]:
        confidence = "medium"
    else:
        confidence = "low"

    notes: list[str] = []
    if has_deprecated and has_current:
        notes.append(
            "A deprecated policy version also matched; the current policy/contract "
            "was used and the deprecated one ignored for the decision."
        )
    if not retrieved:
        notes.append("No supporting documents were retrieved.")

    return {
        "confidence": confidence,
        "conflict_detected": has_deprecated and has_current,
        "notes": notes,
        "citations": collect_citations(retrieved),
    }
