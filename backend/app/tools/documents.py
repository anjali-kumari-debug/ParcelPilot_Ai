"""Document-search tool (RAG) with access control + authority annotation.

* Access control: a customer only ever retrieves general documents (account_id
  "ALL") plus THEIR OWN contract. They can never pull another customer's
  agreement, because we filter on `account_id` inside the vector query.
* Authority annotation: every hit is tagged with its `authority_tier` and a
  numeric `authority_rank` (from config.AUTHORITY_TIERS). The trust layer
  (app/agent/trust.py) uses these to resolve conflicts and build citations.
"""

from __future__ import annotations

from typing import Any

from ..auth import AuthContext
from ..ollama_client import embed_texts
from ..rag.ingest import get_client, COLLECTION_NAME
from .. import config


def _account_filter(ctx: AuthContext, account_id: str | None) -> dict | None:
    """Build the Chroma `where` clause enforcing document access control."""
    if ctx.is_customer:
        # Own contract + all general docs.
        return {"account_id": {"$in": [ctx.account_id, "ALL"]}}
    # Internal user: optionally narrow to one account's contract + general docs.
    if account_id:
        return {"account_id": {"$in": [account_id, "ALL"]}}
    return None  # internal, no filter: all documents


def search_documents(
    ctx: AuthContext,
    query: str,
    account_id: str | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    """Semantic search over the policy/contract PDFs, scoped and authority-tagged."""
    top_k = top_k or config.RAG_TOP_K
    try:
        collection = get_client().get_collection(COLLECTION_NAME)
    except Exception:
        return {"error": "index_missing", "message": "Document index not built yet."}

    query_vec = embed_texts([query])[0]
    where = _account_filter(ctx, account_id)

    result = collection.query(
        query_embeddings=[query_vec],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    hits: list[dict[str, Any]] = []
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    dists = result.get("distances", [[]])[0]
    for text, meta, dist in zip(docs, metas, dists):
        tier = meta.get("authority_tier", "guide")
        hits.append({
            "text": text,
            "source_file": meta.get("source_file"),
            "doc_version": meta.get("doc_version"),
            "authority_tier": tier,
            "authority_rank": config.AUTHORITY_TIERS.get(tier, 0),
            "page": meta.get("page"),
            "account_id": meta.get("account_id"),
            "distance": round(float(dist), 4),
        })

    # Present most-authoritative first, then most semantically relevant, so the
    # model reads the binding source before weaker ones.
    hits.sort(key=lambda h: (-h["authority_rank"], h["distance"]))

    tiers_present = sorted({h["authority_tier"] for h in hits})
    conflict_warning = (
        "deprecated" in tiers_present and "current" in tiers_present
    )
    return {
        "query": query,
        "count": len(hits),
        "results": hits,
        "tiers_present": tiers_present,
        "possible_version_conflict": conflict_warning,
        "note": "Prefer higher authority_rank on conflict "
                "(contract > current > guide > deprecated > historical). "
                "Historical ticket notes are context only and may be wrong.",
    }
