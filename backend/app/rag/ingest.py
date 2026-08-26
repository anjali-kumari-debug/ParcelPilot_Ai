"""Build the document knowledge base (RAG index).

Pipeline: PDF -> text -> chunks -> embeddings (Ollama nomic-embed-text) -> Chroma.

Each chunk carries metadata that the trust layer relies on:
  * source_file    - which PDF it came from (for citations)
  * authority_tier - contract / current / guide / deprecated (see config)
  * doc_version    - human label ("v3 CURRENT", "v2 DEPRECATED", ...)
  * account_id     - the owning account for contracts; "ALL" for general docs

Run standalone:  python -m app.rag.ingest
"""

from __future__ import annotations

from pathlib import Path

import chromadb
from pypdf import PdfReader

from .. import config
from ..ollama_client import embed_texts

COLLECTION_NAME = "parcelpilot_docs"

# Static map describing the deliberately-messy source pack. This is where we
# encode "which document is more trustworthy" and "which account owns a contract".
SOURCE_META: dict[str, dict] = {
    "01_Support_Policy_v3_CURRENT.pdf": {
        "authority_tier": "current", "doc_version": "Support Policy v3 (CURRENT)", "account_id": "ALL"},
    "02_Support_Policy_v2_DEPRECATED.pdf": {
        "authority_tier": "deprecated", "doc_version": "Support Policy v2 (DEPRECATED)", "account_id": "ALL"},
    "03_Cancellation_and_Service_Credit_SOP_v4.pdf": {
        "authority_tier": "current", "doc_version": "Cancellation & Service Credit SOP v4", "account_id": "ALL"},
    "04_Product_Operations_Guide_and_Known_Issues.pdf": {
        "authority_tier": "guide", "doc_version": "Product Operations Guide & Known Issues", "account_id": "ALL"},
    "05_Northstar_Logistics_Enterprise_Agreement.pdf": {
        "authority_tier": "contract", "doc_version": "Northstar Enterprise Agreement", "account_id": "ACCT-001"},
    "06_LumenWorks_Service_Agreement.pdf": {
        "authority_tier": "contract", "doc_version": "LumenWorks Service Agreement", "account_id": "ACCT-002"},
}


def _extract_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    return [(page.extract_text() or "") for page in reader.pages]


def _chunk(text: str, size: int, overlap: int) -> list[str]:
    """Character-based chunking with overlap. Simple and predictable."""
    text = " ".join(text.split())  # collapse whitespace
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
        if start <= 0:
            start = end
    return chunks


def get_client() -> chromadb.ClientAPI:
    config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(config.CHROMA_DIR))


def build_index() -> dict:
    """(Re)build the Chroma collection from the PDFs in DOC_DIR."""
    client = get_client()
    # Fresh build each run so ingestion is idempotent.
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for filename, meta in SOURCE_META.items():
        pdf_path = config.DOC_DIR / filename
        if not pdf_path.exists():
            print(f"[warn] missing {pdf_path}")
            continue
        pages = _extract_pages(pdf_path)
        for page_no, page_text in enumerate(pages, start=1):
            for c_idx, chunk in enumerate(_chunk(page_text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)):
                ids.append(f"{filename}::p{page_no}::c{c_idx}")
                documents.append(chunk)
                metadatas.append({
                    "source_file": filename,
                    "authority_tier": meta["authority_tier"],
                    "doc_version": meta["doc_version"],
                    "account_id": meta["account_id"],
                    "page": page_no,
                })

    if not documents:
        raise RuntimeError("No document chunks produced - check DOC_DIR / PDFs.")

    # Embed in batches to keep requests reasonable.
    embeddings: list[list[float]] = []
    batch = 32
    for i in range(0, len(documents), batch):
        embeddings.extend(embed_texts(documents[i:i + batch]))

    collection.add(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
    return {"chunks": len(documents), "files": len(SOURCE_META)}


if __name__ == "__main__":
    print("PDFs -> Chroma complete:", build_index())
