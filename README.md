# ParcelPilot AI Support

An agentic AI support system for **ParcelPilot** (a B2B logistics platform),
built for the CalQuity AI Engineer assessment. It answers customer and internal
support questions by reasoning over the supplied policies, SOPs, customer
contracts, and operational data - and it knows when to escalate to a human.

Chat defaults to **Groq** (free cloud tier). Document search uses an in-process
embedding model (**fastembed**). **Ollama is off by default** (`ENABLE_OLLAMA=false`)
and is only for optional local use. A free Groq API key is the only credential
needed to deploy.

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

Assessment write-ups:

- [Architecture note](docs/ARCHITECTURE_NOTE.md)
- [Product note](docs/PRODUCT_NOTE.md)
- [AI tool usage](docs/AI_TOOL_USAGE.md)

---

## Deploy on Render (hosted URL)

Connect the GitHub repo — Render builds the root `Dockerfile` (API + UI in one
container, Groq chat, fastembed embeddings, **no Ollama**).

1. Push this repo to GitHub.
2. Open [render.com](https://render.com) → **New** → **Blueprint** (uses `render.yaml`)
   or **Web Service** → connect the repo → runtime **Docker**.
3. Add the secret:
   - `GROQ_API_KEY` = your key from [console.groq.com/keys](https://console.groq.com/keys)
4. Deploy. Render gives you a `https://….onrender.com` URL.

First boot ingests the workbook + PDFs (a minute or two). The free instance
sleeps after idle (first request after that is a cold start). If the build or
boot runs out of memory, switch the service to **Starter**. Groq itself stays free.

---

## Quick start (Docker - local)

Requirements: Docker Desktop, and a free [Groq API key](https://console.groq.com/keys).

```bash
# repo-root .env
echo 'GROQ_API_KEY=your_key_here' > .env

docker compose up --build
```

Then open **[http://localhost:8090](http://localhost:8090)**. The UI defaults to
**Cloud (Groq)** for chat.

What happens on first boot: the backend loads the workbook into SQLite, builds
the Chroma index with **fastembed**, then serves the API. **Ollama is off**
(`ENABLE_OLLAMA=false`).

> Optional **local** Ollama chat:  
> `ENABLE_OLLAMA=true LLM_PROVIDER=ollama docker compose --profile ollama up --build`  
> That starts Ollama and pulls `llama3.1:8b`. Do not set this on Render.

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

```bash
cd backend
cp .env.example .env   # set GROQ_API_KEY
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.data.ingest_xlsx      # workbook -> SQLite
python -m app.rag.ingest            # PDFs -> Chroma (downloads the embedding model once)
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api to :8000)
```

To use a local Ollama chat model instead of Groq, add to `backend/.env`:

```
ENABLE_OLLAMA=true
LLM_PROVIDER=ollama
```

and run `ollama pull llama3.1:8b`. Leave `ENABLE_OLLAMA` unset/false on Render.

---

## Architecture (at a glance)

See the [architecture note](docs/ARCHITECTURE_NOTE.md) for agent design, tools,
data handling, trust, and trade-offs.

```
React UI ──SSE──> FastAPI ──> Agent loop (Groq; Ollama only if ENABLE_OLLAMA=true)
                                 ├─ search_documents  -> Chroma (fastembed)
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
docs/
  ARCHITECTURE_NOTE.md
  PRODUCT_NOTE.md
  AI_TOOL_USAGE.md
Doc Folder/  the supplied data pack (read-only source of truth)
Dockerfile   single-container image (Render)
docker-compose.yml
render.yaml
```
