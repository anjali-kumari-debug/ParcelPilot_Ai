# AI Tool Usage

I used **Cursor** as the coding assistant while building this repo.

How I used it:

- Turned the assessment brief and data pack into the agent loop, tools, RAG
  index, and the two chat personas.
- Wired access control in the data layer (not only in the prompt) and the
  confirm-before-action flow.
- Built the React chat (tool chips, citations, confirm modal, Ops signals).
- Added the e2e prompt runner against the live `/api/chat` stream so core
  paths can be replayed after a change.
- Packaged a single Docker image for Render (Groq + fastembed; Ollama off
  unless `ENABLE_OLLAMA=true` locally).

I still chose the product calls: contract beats SOP, deprecated policy is for
conflict detection only, calculators return facts not the last word, and ops
gets a proactive view rather than chat-only. I read the PDFs and workbook,
checked answers against those sources, and ran the e2e battery myself.

Nothing in production depends on the assistant at runtime. The running system
is Groq (chat) + fastembed (search) + this codebase.
