# ParcelPilot AI Support

An agentic AI support system for **ParcelPilot** (a B2B logistics platform),
built for the CalQuity AI Engineer assessment. It answers customer and internal
support questions by reasoning over the supplied policies, SOPs, customer
contracts, and operational data - and it knows when to escalate to a human.

Chat defaults to **Groq** (free cloud tier). Ollama still runs for **embeddings**
only (document search). No paid API required beyond a free Groq key.

---

## What it does

- **One agent, two contexts** - a customer-facing assistant and an internal
operations assistant, chosen at a (mocked) login.
- **Three+ tools the agent chooses between**
  1. `search_documents` - RAG over the 6 PDFs, authority-aware and account-scoped.
  2. structured lookups + calculators - `get_order`, `get_account`, `list_tickets`,
    `cancellation_eligibility`, `service_credit_check`.
  3. state-changing actions - `create_escalation`, `update_ticket`,
    `create_followup_task` (mocked), each **requiring explicit confirmation**.
    Confirmed actions are written to a local SQLite `actions` audit log
    (visible under Ops → Proactive signals).
- **Multi-step reasoning** - e.g. order → account → contract → SOP → calculation
→ decision.
- **Access control in the data layer** - customers can only ever read their own
account's data and contract; enforced in SQL/vector filters, not just the prompt.
- **Trust & reliability (Problem 2)** - source authority ranking
(`contract > current policy/SOP > product guide > deprecated > historical`),
version-conflict detection, citations, a confidence signal, and escalation.
- **Proactive detection (Problem 1)** - an internal dashboard surfacing SLA
breaches (plan defaults + contract overrides), ticket clusters, multi-customer
issues, and account hotspots.
- **Chat UI** that shows **which tool is being used** in real time.

See [docs/ARCHITECTURE_NOTE.md](docs/ARCHITECTURE_NOTE.md) and
[docs/PRODUCT_NOTE.md](docs/PRODUCT_NOTE.md) for the write-ups, and
[docs/DECISIONS.md](docs/DECISIONS.md) / [docs/CHANGELOG.md](docs/CHANGELOG.md)
for the full reasoning and step-by-step build log. New to AI? Start with
[docs/GLOSSARY.md](docs/GLOSSARY.md).

---

## Quick start (Docker - recommended)

Requirements: Docker Desktop, and a free [Groq API key](https://console.groq.com/keys).

```bash
# repo-root .env
echo 'GROQ_API_KEY=your_key_here' > .env

docker compose up --build
```

Then open **[http://localhost:8090](http://localhost:8090)**. The UI defaults to
**Cloud (Groq)** for chat.

What happens on first boot: the backend waits for Ollama, pulls only
`nomic-embed-text` (embeddings — not the large chat model), loads the workbook
into SQLite, builds the Chroma index, then serves the API.

> Fully local chat (no Groq):  
> `LLM_PROVIDER=ollama docker compose up --build`  
> That also pulls `llama3.1:8b` (~5 GB).

### Logins (mocked)

- Customers: **Northstar** (ACCT-001), **LumenWorks** (ACCT-002),
**Beacon** (ACCT-003), **Axis** (ACCT-004)
- Internal: **ParcelPilot Ops** (cross-account + proactive dashboard)

### Try

- "Can I cancel ORD-1001 without a cancellation fee? Explain why." (as Northstar)
- "Am I owed a service credit for ORD-2002?" (as LumenWorks)
- "Escalate TKT-501 - all shipment creation is failing." (confirm the action)
- Sign in as Ops → **Proactive signals** (SLA risk + confirmed actions log).

---

## Run without Docker (dev)

You need Ollama running locally with the embedding model (and the chat model
only if you use local chat):

```bash
ollama pull nomic-embed-text
# only if LLM_PROVIDER=ollama:
# ollama pull llama3.1:8b
```

Backend:

```bash
cd backend
cp .env.example .env   # set GROQ_API_KEY
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.data.ingest_xlsx      # workbook -> SQLite
python -m app.rag.ingest            # PDFs -> Chroma
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api to :8000)
```

---

## Architecture (at a glance)

```
React UI ──SSE──> FastAPI ──> Agent loop (Groq chat by default; Ollama optional)
                                 ├─ search_documents  -> Chroma (Ollama embeddings)
                                 ├─ structured/calc    -> SQLite (account-scoped)
                                 └─ actions (confirmed) -> SQLite actions log
                    └─ /signals -> proactive detection (internal only)
```

Reference time for all calculations is the dataset snapshot
**2026-08-16 11:00 IST** (from the workbook README).

## Repo layout

```
backend/     FastAPI app, agent, tools, RAG + data ingestion, proactive signals
frontend/    Vite + React chat UI (tool trace, citations, confirm modal, signals)
docs/        decision log, changelog, glossary, architecture & product notes
Doc Folder/  the supplied data pack (read-only source of truth)
docker-compose.yml
```
