import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { getIdentities, sendChat, confirmAction, getSignals, getActions, getProviders } from "./api.js";

const TOOL_LABELS = {
  search_documents: "Document search",
  get_account: "Account lookup",
  get_order: "Order lookup",
  list_tickets: "Ticket lookup",
  cancellation_eligibility: "Cancellation calc",
  service_credit_check: "Service-credit calc",
  create_escalation: "Prepare escalation",
  update_ticket: "Prepare ticket update",
  create_followup_task: "Prepare follow-up task",
};

const EXAMPLES = [
  "Can I cancel ORD-1001 without a cancellation fee? Explain why.",
  "A pickup is hours late due to carrier fault. Am I owed a service credit for ORD-2002?",
  "What are my P1 first-response SLA targets?",
  "Please escalate ticket TKT-501 - all shipment creation is failing.",
];

function summarizeResult(name, result) {
  if (!result) return "";
  if (result.error) return `error: ${result.error}`;
  if (name === "search_documents") return `${result.count} passages (${(result.tiers_present || []).join(", ")})`;
  if (name === "cancellation_eligibility") return `${result.status}, default fee INR ${result.default_sop_outcome?.fee_inr}`;
  if (name === "service_credit_check") return `delay ${result.delay_minutes_past_window} min, default INR ${result.default_sop?.credit_inr}`;
  if (name === "get_order") return `${result.order_id}: ${result.status}`;
  if (name === "get_account") return `${result.account_id}: ${result.plan}`;
  if (name === "list_tickets") return `${result.count} tickets`;
  if (result.requires_confirmation) return "prepared (awaiting confirmation)";
  return "done";
}

function Login({ identities, onPick }) {
  return (
    <div className="login">
      <div className="login-card">
        <h1>ParcelPilot AI Support</h1>
        <p>Choose an identity to sign in (mocked authentication).</p>
        <div className="login-group">
          <h3>Customers</h3>
          {identities.filter((i) => i.role === "customer").map((i) => (
            <button key={i.login_id} className="id-btn" onClick={() => onPick(i)}>
              <strong>{i.user_name}</strong>
              <span>{i.account_id}</span>
            </button>
          ))}
        </div>
        <div className="login-group">
          <h3>Internal</h3>
          {identities.filter((i) => i.role === "internal").map((i) => (
            <button key={i.login_id} className="id-btn internal" onClick={() => onPick(i)}>
              <strong>{i.user_name}</strong>
              <span>operations</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ToolTrace({ tools }) {
  if (!tools || tools.length === 0) return null;
  return (
    <div className="tool-trace">
      {tools.map((t, i) => (
        <div key={i} className={`tool-chip ${t.status}`}>
          <span className="dot" />
          <span className="tool-name">{TOOL_LABELS[t.name] || t.name}</span>
          {t.summary ? <span className="tool-summary">{t.summary}</span> : null}
        </div>
      ))}
    </div>
  );
}

function Citations({ citations }) {
  if (!citations || citations.length === 0) return null;
  return (
    <div className="citations">
      <span className="cite-label">Sources:</span>
      {citations.map((c, i) => (
        <span key={i} className={`cite tier-${c.authority_tier}`} title={c.authority_tier}>
          {c.doc_version || c.source_file}
        </span>
      ))}
    </div>
  );
}

function MessageBody({ role, content }) {
  if (!content) {
    return role === "assistant" ? <span className="thinking">working…</span> : null;
  }
  // Agent replies are markdown (bold, lists, etc.); user/system stay plain text.
  if (role === "assistant") {
    return (
      <div className="md">
        <ReactMarkdown>{content}</ReactMarkdown>
      </div>
    );
  }
  return content;
}

function Message({ m }) {
  return (
    <div className={`msg ${m.role}`}>
      <div className="msg-role">{m.role === "user" ? "You" : m.role === "assistant" ? "Agent" : "System"}</div>
      <ToolTrace tools={m.tools} />
      <div className={`msg-body ${m.role === "assistant" ? "msg-body-md" : ""}`}>
        <MessageBody role={m.role} content={m.content} />
      </div>
      {m.confidence && m.confidence !== "n/a" ? (
        <div className={`confidence ${m.confidence}`}>confidence: {m.confidence}</div>
      ) : null}
      {m.notes && m.notes.length ? <div className="trust-notes">{m.notes.map((n, i) => <div key={i}>⚠ {n}</div>)}</div> : null}
      <Citations citations={m.citations} />
    </div>
  );
}

function ConfirmModal({ action, onConfirm, onCancel, busy }) {
  if (!action) return null;
  return (
    <div className="modal-overlay">
      <div className="modal">
        <h3>Confirm action</h3>
        <p className="action-type">{action.action_type}</p>
        <p className="action-summary">{action.summary}</p>
        <pre className="action-payload">{JSON.stringify(action.payload, null, 2)}</pre>
        <p className="modal-note">Nothing is changed until you confirm.</p>
        <div className="modal-actions">
          <button className="btn-secondary" disabled={busy} onClick={onCancel}>Cancel</button>
          <button className="btn-primary" disabled={busy} onClick={onConfirm}>Confirm</button>
        </div>
      </div>
    </div>
  );
}

function SignalsPanel({ loginId }) {
  const [data, setData] = useState(null);
  const [actions, setActions] = useState([]);
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const [signals, actionLog] = await Promise.all([
        getSignals(loginId),
        getActions(loginId),
      ]);
      setData(signals);
      setActions(actionLog.actions || []);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);
  if (loading && !data) return <div className="panel">Loading signals…</div>;
  if (!data) return <div className="panel">No data.</div>;
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Proactive signals</h2>
        <button className="btn-secondary" onClick={load}>Refresh</button>
      </div>
      <div className="cards">
        <div className="card"><div className="card-n">{data.summary.open_tickets}</div><div>Open tickets</div></div>
        <div className="card alert"><div className="card-n">{data.summary.sla_breaches}</div><div>SLA breaches</div></div>
        <div className="card alert"><div className="card-n">{data.summary.p1_tickets}</div><div>P1 tickets</div></div>
        <div className="card"><div className="card-n">{data.summary.clusters}</div><div>Clusters</div></div>
      </div>

      <h3>SLA risk</h3>
      <table className="tbl">
        <thead><tr><th>Ticket</th><th>Account</th><th>Sev</th><th>Age</th><th>Target</th><th>Status</th><th>Subject</th></tr></thead>
        <tbody>
          {data.sla_risk.map((s) => (
            <tr key={s.ticket_id} className={s.sla_status}>
              <td>{s.ticket_id}</td><td>{s.account_name || s.account_id}</td><td>{s.severity}</td>
              <td>{s.age_minutes}m</td><td>{s.target_minutes}m</td>
              <td><span className={`pill ${s.sla_status}`}>{s.sla_status}</span></td>
              <td>{s.subject}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {data.clusters.length > 0 && (
        <>
          <h3>Ticket clusters</h3>
          {data.clusters.map((c, i) => (
            <div key={i} className="cluster">
              <strong>{c.label}</strong> — {c.size} tickets ({c.ticket_ids.join(", ")})
              {c.multi_customer ? <span className="pill breached">multi-customer</span> : null}
            </div>
          ))}
        </>
      )}

      {data.account_hotspots.length > 0 && (
        <>
          <h3>Account hotspots</h3>
          {data.account_hotspots.map((h, i) => (
            <div key={i} className="cluster">{h.account_id}: {h.open_tickets} open tickets</div>
          ))}
        </>
      )}

      <h3>Confirmed actions (audit log)</h3>
      {actions.length === 0 ? (
        <p className="empty-note">No escalations, ticket updates, or follow-ups confirmed yet.</p>
      ) : (
        <table className="tbl">
          <thead><tr><th>ID</th><th>Type</th><th>Account</th><th>Target</th><th>By</th><th>When</th></tr></thead>
          <tbody>
            {actions.map((a) => (
              <tr key={a.action_id}>
                <td>{a.action_id}</td>
                <td>{a.action_type}</td>
                <td>{a.account_id || "—"}</td>
                <td>{a.target_id || "—"}</td>
                <td>{a.created_by}</td>
                <td>{a.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ModelToggle({ providers, provider, onChange, disabled }) {
  if (!providers || providers.length === 0) return null;
  return (
    <div className="model-toggle">
      <div className="ex-title">Model</div>
      <div className="seg">
        {providers.map((p) => (
          <button
            key={p.id}
            className={`seg-btn ${provider === p.id ? "active" : ""}`}
            disabled={disabled || !p.available}
            title={p.available ? p.model : `${p.label} unavailable`}
            onClick={() => onChange(p.id)}
          >
            {p.label}
            {!p.available ? <span className="seg-off">off</span> : null}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [identities, setIdentities] = useState([]);
  const [identity, setIdentity] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  const [tab, setTab] = useState("chat");
  const [providers, setProviders] = useState([]);
  const [provider, setProvider] = useState(null);
  const scroller = useRef(null);
  const currentId = useRef(null);

  useEffect(() => { getIdentities().then(setIdentities); }, []);
  useEffect(() => {
    getProviders().then((data) => {
      const list = data.providers || [];
      setProviders(list);
      // Prefer the configured default if usable, else the first available provider.
      const usable = list.filter((p) => p.available);
      const preferred = usable.find((p) => p.id === data.default) || usable[0] || list[0];
      if (preferred) setProvider(preferred.id);
    });
  }, []);
  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [messages]);

  const updateAssistant = (updater) => {
    setMessages((prev) => prev.map((m) => (m.id === currentId.current ? updater(m) : m)));
  };

  const handleEvent = (ev) => {
    if (ev.type === "session") { setSessionId(ev.session_id); return; }
    if (ev.type === "tool_call") {
      updateAssistant((m) => ({ ...m, tools: [...m.tools, { name: ev.name, args: ev.arguments, status: "running" }] }));
    } else if (ev.type === "tool_result") {
      updateAssistant((m) => {
        const tools = [...m.tools];
        for (let i = tools.length - 1; i >= 0; i--) {
          if (tools[i].name === ev.name && tools[i].status === "running") {
            tools[i] = { ...tools[i], status: ev.result?.error ? "error" : "done", summary: summarizeResult(ev.name, ev.result), result: ev.result };
            break;
          }
        }
        return { ...m, tools };
      });
    } else if (ev.type === "pending_action") {
      // #region agent log
      fetch('http://127.0.0.1:7905/ingest/d67efbcf-c69f-4fbc-b761-93d21b1cff09',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cc4176'},body:JSON.stringify({sessionId:'cc4176',location:'App.jsx:handleEvent',message:'UI received pending_action',data:{action_type:ev.action?.action_type,target_id:ev.action?.target_id},timestamp:Date.now(),hypothesisId:'C',runId:'run1'})}).catch(()=>{});
      // #endregion
      setPending(ev.action);
    } else if (ev.type === "message") {
      // #region agent log
      fetch('http://127.0.0.1:7905/ingest/d67efbcf-c69f-4fbc-b761-93d21b1cff09',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cc4176'},body:JSON.stringify({sessionId:'cc4176',location:'App.jsx:handleEvent',message:'UI received message (no modal)',data:{content_preview:(ev.content||'').slice(0,120),confidence:ev.confidence},timestamp:Date.now(),hypothesisId:'A',runId:'run1'})}).catch(()=>{});
      // #endregion
      updateAssistant((m) => ({ ...m, content: ev.content, citations: ev.citations, confidence: ev.confidence, notes: ev.trust_notes }));
    } else if (ev.type === "action_executed") {
      setMessages((prev) => [...prev, { id: "s" + Date.now(), role: "system", content: ev.result?.message || "Action executed.", tools: [] }]);
    } else if (ev.type === "action_cancelled") {
      setMessages((prev) => [...prev, { id: "s" + Date.now(), role: "system", content: "Action cancelled.", tools: [] }]);
    } else if (ev.type === "error") {
      updateAssistant((m) => ({ ...m, content: "Error: " + ev.message }));
    }
  };

  const send = async (text) => {
    const q = (text ?? input).trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    const userMsg = { id: "u" + Date.now(), role: "user", content: q, tools: [] };
    const asstId = "a" + Date.now();
    currentId.current = asstId;
    setMessages((prev) => [...prev, userMsg, { id: asstId, role: "assistant", content: "", tools: [], citations: [] }]);
    try {
      await sendChat({ loginId: identity.login_id, message: q, sessionId, provider }, handleEvent);
    } catch (e) {
      updateAssistant((m) => ({ ...m, content: "Network error: " + e.message }));
    } finally {
      setBusy(false);
    }
  };

  const resolveAction = async (approved) => {
    setBusy(true);
    const asstId = "a" + Date.now();
    currentId.current = asstId;
    setPending(null);
    setMessages((prev) => [...prev, { id: asstId, role: "assistant", content: "", tools: [], citations: [] }]);
    try {
      await confirmAction({ loginId: identity.login_id, sessionId, approved }, handleEvent);
    } finally {
      setBusy(false);
    }
  };

  if (!identity) return <Login identities={identities} onPick={setIdentity} />;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">ParcelPilot<span>AI Support</span></div>
        <div className="whoami">
          <div className="avatar">{identity.user_name[0]}</div>
          <div>
            <div className="who-name">{identity.user_name}</div>
            <div className="who-role">{identity.role}{identity.account_id ? ` · ${identity.account_id}` : ""}</div>
          </div>
        </div>
        <nav className="nav">
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>Chat</button>
          {identity.role === "internal" && (
            <button className={tab === "signals" ? "active" : ""} onClick={() => setTab("signals")}>Proactive signals</button>
          )}
        </nav>
        <ModelToggle providers={providers} provider={provider} onChange={setProvider} disabled={busy} />
        <div className="examples">
          <div className="ex-title">Try asking</div>
          {EXAMPLES.map((e, i) => (
            <button key={i} className="ex" disabled={busy} onClick={() => send(e)}>{e}</button>
          ))}
        </div>
        <button className="signout" onClick={() => { setIdentity(null); setMessages([]); setSessionId(null); }}>Sign out</button>
      </aside>

      <main className="main">
        {tab === "signals" ? (
          <SignalsPanel loginId={identity.login_id} />
        ) : (
          <>
            <div className="messages" ref={scroller}>
              {messages.length === 0 && (
                <div className="empty">Ask about entitlements, cancellations, service credits, SLAs, or tickets.</div>
              )}
              {messages.map((m) => <Message key={m.id} m={m} />)}
            </div>
            <div className="composer">
              <input
                value={input}
                placeholder="Ask a question…"
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && send()}
                disabled={busy}
              />
              <button className="btn-primary" disabled={busy || !input.trim()} onClick={() => send()}>Send</button>
            </div>
          </>
        )}
      </main>

      <ConfirmModal action={pending} busy={busy} onConfirm={() => resolveAction(true)} onCancel={() => resolveAction(false)} />
    </div>
  );
}
