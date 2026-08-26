# Architecture Note

ParcelPilot is one agent with two seats: a customer (own account only) and an
internal ops user (cross-account). The UI talks to FastAPI over SSE. Chat runs
on Groq. Document search uses an in-process embedder (fastembed) so hosted
deploys do not need Ollama. Ollama is an optional local flag (`ENABLE_OLLAMA`).

```
Customer / Ops
      │
      ▼
 React chat  ──SSE──►  FastAPI
                          │
                          ▼
                     Agent loop
                    (Groq tools)
                 ┌────────┼────────┐
                 ▼        ▼        ▼
           documents   lookups   actions
           (Chroma)   + calcs   (confirm
                       (SQLite)   then log)
                          │
                          └── Ops only: /signals
```

## Agent design

A bounded loop: the model may call tools, we run them, results go back, repeat
until a final answer or a step cap. Customer vs ops is a system prompt plus a
real `AuthContext` on every tool — the prompt is not the access control.

Typical path: look up the order → see if a contract exists → search that
agreement and the SOP → run the calculator → decide. If the model tries to
answer a fee before reading a contract that might override the SOP, the loop
nudges it to search first.

Steps stream as events (`tool_call`, `pending_action`, `message`), not as
tokens, so the UI can show which tool is running.

## Tool design

One registry, three kinds:

1. **Search** — policies, SOPs, product guide, the two contracts.
2. **Lookups and calculators** — account, order, tickets; cancellation
   eligibility and failed-pickup credit. Calculators return *facts* and the
   *default SOP number*, plus `contract_may_override` when a signed agreement
   exists. Binding numbers stay in the PDFs.
3. **Actions** — escalate, update a ticket, create a follow-up. These only
   *prepare*. The UI asks for confirm; nothing is written until then. The write
   is a local audit row, not a real ticketing system.

## Documents and structured data

PDFs are chunked into Chroma with `source_file`, `authority_tier`, version, and
`account_id` (contracts are scoped; everything else is `ALL`). A customer query
can only retrieve `ALL` plus their own contract.

The workbook lands in SQLite (`accounts`, `orders`, `tickets`). Every SQL
filter is pinned to the caller’s account unless they are ops.

“Now” is the dataset snapshot **2026-08-16 11:00 IST**, so cancellation windows,
pickup delay, and SLA age do not drift with the wall clock.

## Source reliability and conflicts

Authority is ranked, not averaged:

`contract > current policy/SOP > product guide > deprecated > historical`

v2 of the support policy is kept so we can *see* a conflict, not so we can
answer from it. If current and deprecated both match, the trust layer cites the
higher tier, notes the clash, and sets confidence. Closed-ticket write-ups are
context only — the pack itself says they may be wrong. Missing data or a
one-off exception goes to a human instead of a guess.

## Trade-offs

- **Groq over a local chat model** — better tool-calling on a free key; the
  cost is a network dependency. Ollama stays behind a flag for offline demos.
- **fastembed over an embedding server** — one container on Render; vectors are
  slightly different from `nomic-embed-text`, so the index is rebuilt on boot.
- **Event stream, not token stream** — you see the tools; you do not see the
  answer type out.
- **Facts in code, policy in documents** — extra tool hops, but Northstar’s
  “no cancel fee” and LumenWorks’ INR 300 credit cannot silently rot in a
  calculator.
- **SQLite, in-memory sessions, one worker** — enough for the assessment, not
  a multi-region support desk.
