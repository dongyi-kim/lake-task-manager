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
  if (!r.ok) {
    // 서버가 남긴 이유를 그대로 올린다 — 'HTTP 502' 만 보여 주면 사용자도 우리도 알 수 없다.
    let msg = "";
    try { const b = await r.clone().json(); msg = (b && (b.error || b.detail)) || ""; } catch (e) { /* 본문이 JSON 이 아님 */ }
    throw new Error(msg || "HTTP " + r.status);
  }
  return r.json();
}

const _memo = new Map();
function get(path) {
  if (_memo.has(path)) return _memo.get(path);
  const p = req(path).catch((e) => { _memo.delete(path); throw e; });  // 실패는 캐시 안 함
  _memo.set(path, p);
  return p;
}
// 쓰기 후 그 티켓 관련 GET memo 를 비워 다음 조회가 최신을 읽게 한다(코멘트·첨부·타임라인 등).
function evict(sub) { for (const k of Array.from(_memo.keys())) if (k.includes(sub)) _memo.delete(k); }

function jsonReq(path, method, body) {
  return req(path, { method, headers: { "Content-Type": "application/json" },
                     body: JSON.stringify(body || {}) });
}

export const api = {
  health: () => req("/api/health"),                                    // 로그인 상태 — memo 제외
  raw: (path, opts) => req(path, opts),                                // memo 없이 매번 조회(설정 메뉴 등)
  login: () => req("/api/login", { method: "POST" }),
  prefs: () => req("/api/prefs"),
  setPrefs: (body) => jsonReq("/api/prefs", "PUT", body),
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
  myTasks: (opts) => {                                                // 내 Task(옵션은 서버 질의 조건)
    const o = opts || {};
    return req("/api/mytasks?scope=" + encodeURIComponent(o.scope || "assignee")
      + "&openFilter=" + encodeURIComponent(o.openFilter || "all")
      + "&doneFilter=" + encodeURIComponent(o.doneFilter || "1w"));
  },
  search: (q, scope, only) => req("/api/search?q=" + encodeURIComponent(q)
    + "&scope=" + encodeURIComponent(scope || "scoped")
    + (only ? "&only=" + encodeURIComponent(only) : "")),               // only=jira|confluence
  // fresh: 캐시를 건너뛴다(본문 편집 시작·편집 중 충돌 감시). 평소엔 쓰지 마라 — 상류 왕복이다.
  // ★ 이때는 memo(get)를 타면 안 된다 — 프로미스 캐시가 URL 이 같다는 이유로 **늘 첫 응답**을
  //   돌려준다. 서버가 캐시를 건너뛰고 no-store 를 붙여도 여기서 막히면 아무 소용이 없다.
  ticket: (key, fresh) => {
    const u = "/api/ticket/" + encodeURIComponent(key);
    return fresh ? req(u + "?fresh=1") : get(u);
  },
  ticketBadge: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/badge"),
  ticketAncestors: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/ancestors"),
  ticketSiblings: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/siblings"),
  ticketTimeline: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/timeline"),
  ticketChildren: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/children"),
  ticketRelated: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/related"),
  ticketAttachments: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/attachments"),
  ticketDocuments: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/documents"),
  ticketComments: (key) => get("/api/issue/" + encodeURIComponent(key) + "/comments"),

  // ── 링크 걸기(관련 티켓 / 관련문서) ──
  linkTypes: () => get("/api/linktypes"),                              // 관계 선택지(캐시됨)
  linkAdd: (key, body) =>                                              // 관련 티켓
    jsonReq("/api/ticket/" + encodeURIComponent(key) + "/link", "POST", body)
      .then((r) => { evict(encodeURIComponent(key)); evict(encodeURIComponent(body.key)); return r; }),
  linkDelete: (key, linkId, other) =>
    req("/api/ticket/" + encodeURIComponent(key) + "/link/" + encodeURIComponent(linkId)
        + (other ? "?other=" + encodeURIComponent(other) : ""), { method: "DELETE" })
      .then((r) => { evict(encodeURIComponent(key)); if (other) evict(encodeURIComponent(other)); return r; }),
  documentAdd: (key, body) =>                                          // 관련문서(remote link)
    jsonReq("/api/ticket/" + encodeURIComponent(key) + "/document", "POST", body)
      .then((r) => { evict(encodeURIComponent(key)); return r; }),

  // ── 최근 열어본 항목(서버 저장 — 브라우저가 달라도 같은 목록) ──
  recent: (limit) => req("/api/recent" + (limit ? "?limit=" + limit : "")),   // memo 제외(자주 바뀜)
  recentAdd: (item) => jsonReq("/api/recent", "POST", item),
  recentClear: (url) => req("/api/recent" + (url ? "?url=" + encodeURIComponent(url) : ""),
                            { method: "DELETE" }),

  // ── 쓰기(편집) — 코멘트 작성/수정/삭제 + 이미지 첨부. 성공 후 그 티켓 memo 무효화 ──
  me: () => get("/api/me"),
  // 전이 목록은 **캐시하지 않는다** — 현재 상태에 따라 매번 달라지고, 낡은 목록은 곧 400 이다.
  timetracking: () => get("/api/timetracking"),
  openExternal: (url) => jsonReq("/api/open", "POST", { url }),
  // 편집 가능 필드 — 상태에 따라 바뀌므로 캐시하지 않는다
  editmeta: (key) => req("/api/ticket/" + encodeURIComponent(key) + "/editmeta"),
  options: (kind, q) => req("/api/options/" + kind + (q ? "?q=" + encodeURIComponent(q) : "")),
  updateFields: (key, body) => jsonReq("/api/ticket/" + encodeURIComponent(key) + "/fields",
                                       "PUT", body).then((r) => { evict(key); return r; }),
  childTypes: (key) => req("/api/options/childtypes?q=" + encodeURIComponent(key)),
  createChild: (key, body) => jsonReq("/api/ticket/" + encodeURIComponent(key) + "/child",
                                      "POST", body)
    // 만든 직후 부모의 하위 목록을 다시 받아야 한다 — memo 를 안 비우면 **늘 만들기 전 목록**이
    // 돌아온다(프로미스 캐시라 서버가 최신을 줘도 소용없다).
    .then((r) => { evict(encodeURIComponent(key)); return r; }),
  ticketMenu: (key) => req("/api/ticket/" + encodeURIComponent(key) + "/menu"),
  setAssignee: (key, assignee) => jsonReq("/api/ticket/" + encodeURIComponent(key) + "/assignee",
                                          "PUT", { assignee }).then((r) => { evict(key); return r; }),
  deleteTicket: (key) => req("/api/ticket/" + encodeURIComponent(key), { method: "DELETE" })
                           .then((r) => { evict(key); return r; }),
  transitions: (key) => req("/api/ticket/" + encodeURIComponent(key) + "/transitions"),
  doTransition: (key, body) => jsonReq("/api/ticket/" + encodeURIComponent(key) + "/transition",
                                       "POST", body).then((r) => { evict(key); return r; }),                                             // 본인 댓글 판정
  mentionUsers: (q, key) => req("/api/mention/users?q=" + encodeURIComponent(q || "")   // @사람 자동완성
    + (key ? "&key=" + encodeURIComponent(key) : "")),                                  // 빈 쿼리 시 티켓 관련 우선
  linkTitle: (u) => req("/api/linktitle?u=" + encodeURIComponent(u || "")),             // 링크 뱃지 제목(og:title)
  commentCreate: (key, html) =>
    jsonReq("/api/ticket/" + encodeURIComponent(key) + "/comment", "POST", { html })
      .then((r) => { evict(encodeURIComponent(key)); return r; }),
  commentUpdate: (key, cid, html) =>
    jsonReq("/api/ticket/" + encodeURIComponent(key) + "/comment/" + encodeURIComponent(cid), "PUT", { html })
      .then((r) => { evict(encodeURIComponent(key)); return r; }),
  commentDelete: (key, cid) =>
    req("/api/ticket/" + encodeURIComponent(key) + "/comment/" + encodeURIComponent(cid), { method: "DELETE" })
      .then((r) => { evict(encodeURIComponent(key)); return r; }),
  commentSource: (key, cid) =>                                         // 수정 로드(markdown), memo 제외
    req("/api/ticket/" + encodeURIComponent(key) + "/comment/" + encodeURIComponent(cid) + "/source"),
  attachmentUpload: (key, file) => {                                   // multipart — Content-Type 자동
    const fd = new FormData();
    fd.append("file", file, file.name || "paste.png");
    return req("/api/ticket/" + encodeURIComponent(key) + "/attachment", { method: "POST", body: fd })
      .then((r) => { evict(encodeURIComponent(key)); return r; });
  },
  documentDelete: (key, lid) =>
    req("/api/ticket/" + encodeURIComponent(key) + "/document/" + encodeURIComponent(lid),
        { method: "DELETE" })
      .then((r) => { evict(encodeURIComponent(key)); return r; }),
  attachmentDelete: (key, aid) =>                                      // 롤백
    req("/api/ticket/" + encodeURIComponent(key) + "/attachment/" + encodeURIComponent(aid), { method: "DELETE" })
      .then((r) => { evict(encodeURIComponent(key)); return r; }),
};
