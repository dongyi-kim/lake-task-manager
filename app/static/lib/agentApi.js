// agentApi.js — `/api/agent/*` 호출.
//
// api.js 와 나눠 둔 이유가 둘 있다.
//  1) api.js 는 GET 을 **memo(프로미스 캐시)** 한다. 대화는 같은 요청이라도 매번 새 답이라
//     캐시를 타면 안 된다.
//  2) 에이전트는 **선택 설치**다. 라우트가 아예 없을 수 있으므로 호출부가 그 사실을
//     구분해 다뤄야 한다(`/api/prefs` 의 agentEnabled 로 먼저 가른다).
//
// SSE 를 EventSource 로 받지 않는 이유: EventSource 는 GET 만 된다. 사용자의 발화는
// 길고 줄바꿈이 있어 쿼리스트링에 실을 것이 못 된다 — fetch + ReadableStream 으로 POST 한다.

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    let msg = "";
    try { const b = await r.clone().json(); msg = (b && (b.error || b.detail)) || ""; } catch (e) {}
    throw new Error(msg || "HTTP " + r.status);
  }
  return r.json();
}

async function put(path, body) {
  const r = await fetch(path, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    let msg = "";
    try { const b = await r.clone().json(); msg = (b && (b.error || b.detail)) || ""; } catch (e) {}
    throw new Error(msg || "HTTP " + r.status);
  }
  return r.json();
}

const getJson = (p) => fetch(p).then((r) => {
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
});

export const agentApi = {
  status: () => getJson("/api/agent/status"),
  saveSettings: (body) => put("/api/agent/settings", body),
  probe: () => post("/api/agent/probe", {}),
  indexStats: () => getJson("/api/agent/index"),
  resetIndex: () => post("/api/agent/index/reset", {}),

  ask: (body) => post("/api/agent/chat", body),
  snapshot: (tid) => getJson("/api/agent/snapshot/" + encodeURIComponent(tid)),
  approve: (threadId, token) => post("/api/agent/approve", { threadId, token }),
  cancel: (threadId, token) => post("/api/agent/cancel", { threadId, token }),

  /**
   * SSE. `onEvent(ev)` 가 진행 상황을 받는다. 마지막 `{type:"final", ...}` 이 결과다.
   * 반환값은 abort 함수 — 사용자가 대화를 떠나면 서버 일을 계속 시킬 이유가 없다.
   */
  stream(body, onEvent) {
    const ctrl = new AbortController();
    (async () => {
      try {
        const r = await fetch("/api/agent/chat/stream", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body || {}), signal: ctrl.signal,
        });
        if (!r.ok || !r.body) throw new Error("HTTP " + r.status);
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          // SSE 는 빈 줄로 이벤트를 가른다. 마지막 조각은 아직 안 끝났을 수 있어 남겨 둔다.
          const parts = buf.split("\n\n");
          buf = parts.pop();
          for (const p of parts) {
            const line = p.split("\n").find((x) => x.startsWith("data: "));
            if (!line) continue;
            try { onEvent(JSON.parse(line.slice(6))); } catch (e) { /* 깨진 조각은 흘린다 */ }
          }
        }
      } catch (e) {
        if (e && e.name === "AbortError") return;      // 사용자가 떠난 것 — 오류가 아니다
        onEvent({ type: "error", message: (e && e.message) || "연결이 끊겼습니다" });
      }
    })();
    return () => ctrl.abort();
  },
};
