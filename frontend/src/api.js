// Minimal client for the backend, including a POST-based SSE reader.
//
// EventSource only supports GET, but our chat/confirm endpoints are POST and
// stream `text/event-stream`. So we read the response body ourselves and split
// on the SSE record separator ("\n\n"), invoking onEvent for each JSON payload.

export async function getIdentities() {
  const res = await fetch("/api/identities");
  return (await res.json()).identities;
}

export async function getHealth() {
  const res = await fetch("/api/health");
  return res.json();
}

export async function getSignals(loginId) {
  const res = await fetch(`/api/signals?login_id=${encodeURIComponent(loginId)}`);
  return res.json();
}

async function streamPost(url, body, onEvent) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const record = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = record.split("\n").find((l) => l.startsWith("data: "));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(6)));
        } catch (e) {
          /* ignore malformed keep-alives */
        }
      }
    }
  }
}

export function sendChat({ loginId, message, sessionId }, onEvent) {
  return streamPost("/api/chat", { login_id: loginId, message, session_id: sessionId }, onEvent);
}

export function confirmAction({ loginId, sessionId, approved }, onEvent) {
  return streamPost("/api/confirm", { login_id: loginId, session_id: sessionId, approved }, onEvent);
}
