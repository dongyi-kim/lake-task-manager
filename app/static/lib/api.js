// api.js — 백엔드 리소스 호출 래퍼. 401 needLogin 을 전역 이벤트로 알린다(LoginOverlay 가 수신).
// GET 응답을 URL 키로 memo(프로미스 캐시) → 탭 전환/중복요청 재fetch 방지. refresh() 에서 clear.
// updated: 2026-07-09
async function req(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) {
    let b = {}; try { b = await r.clone().json(); } catch (e) {}
    if (b && b.needLogin) window.dispatchEvent(new CustomEvent("need-login"));
    throw new Error("HTTP 401");
  }
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

const _memo = new Map();
function get(path) {
  if (_memo.has(path)) return _memo.get(path);
  const p = req(path).catch((e) => { _memo.delete(path); throw e; });  // 실패는 캐시 안 함
  _memo.set(path, p);
  return p;
}

export const api = {
  health: () => req("/api/health"),                                    // 로그인 상태 — memo 제외
  login: () => req("/api/login", { method: "POST" }),
  refresh: () => req("/api/refresh", { method: "POST" }).then((r) => { _memo.clear(); return r; }),
  wbs: () => get("/api/wbs"),
  epicTree: (key) => get("/api/epic/" + encodeURIComponent(key) + "/tree"),
  vit: () => get("/api/vit"),
  vitShell: () => get("/api/vit/shell"),
  vitModule: (m) => get("/api/vit/module/" + encodeURIComponent(m)),
  vitDetail: (key) => get("/api/vit/" + encodeURIComponent(key)),
  workload: () => get("/api/workload"),
  workloadShell: () => get("/api/workload/shell"),
  workloadModule: (m) => get("/api/workload/module/" + encodeURIComponent(m)),
  workloadBucket: (u, b) => get("/api/workload/" + encodeURIComponent(u) + "/" + b),
  workloadDetail: (user) => get("/api/workload/" + encodeURIComponent(user)),
  activity: (user) => get("/api/activity/" + encodeURIComponent(user)),
  search: (q, scope) => req("/api/search?q=" + encodeURIComponent(q) + "&scope=" + encodeURIComponent(scope || "scoped")),
  ticket: (key) => get("/api/ticket/" + encodeURIComponent(key)),
  ticketBadge: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/badge"),
  ticketAncestors: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/ancestors"),
  ticketSiblings: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/siblings"),
  ticketTimeline: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/timeline"),
  ticketComments: (key) => get("/api/issue/" + encodeURIComponent(key) + "/comments"),
};
