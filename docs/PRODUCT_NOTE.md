# Product Note

## Additional client problems

I built both.

**Trust (problem 2)** is the product, not a badge. Every chunk carries an
authority tier. Northstar’s and LumenWorks’ agreements beat the generic SOP.
The deprecated policy is in the index so we can say “v2 also matched; we used
v3,” not so we can quote v2. Citations and a confidence label sit on the
answer. Old ticket resolutions are treated as possibly wrong. If the request
needs a goodwill exception or the data is incomplete, we escalate rather than
invent a fee.

**Proactive detection (problem 1)** is the Ops **Proactive signals** view.
Open tickets are scored against plan SLA *and* contract overrides (Northstar
P1 is 15 minutes, not the Enterprise default). Similar tickets cluster, including
ones that span more than one customer. Accounts with several open tickets show
up as hotspots. Confirmed agent actions appear in the same panel so ops can see
what the bot already did.

A reactive chat only helps after someone types. The dashboard is what the
20-person ops team would open on a Monday morning.

## What I would build next

1. **A real escalation queue** — the mock write is a demo; production needs a
   ticket with the order, contract clause, and tool trace attached so nothing
   falls on the floor.
2. **A labelled eval set in CI** — cancellation, credit, SLA, and “must
   refuse.” We already replay 13 live prompts; the next step is expected
   *decisions*, not just keywords.
3. **A numbers check** — before send, every INR amount and SLA minute must
   appear in a citation.
4. **Policy effective dates** — so a new SOP replaces v3 without a hand-edited
   tier map.
5. **SSO and an immutable audit log** — mocked login is allowed here; it would
   not survive a real customer.

## Left out on purpose

- Real login (the brief allows a mock). Scoping in SQL/Chroma is real.
- Real side effects (no Zendesk/Jira).
- A business-hours calendar (SLA uses clock minutes against the snapshot).
- Scale (one process, memory sessions).
- Token streaming and page-level PDF links.

## One metric

**Share of chats the agent finishes that a human would not reverse**, with
almost no high-confidence answers that were actually wrong.

Deflection without that second half is how you lose the ops team. I would track
both: autonomous resolution that survives review, and false-confident rate near
zero.
