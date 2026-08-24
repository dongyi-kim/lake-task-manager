// api.js — 백엔드 리소스 호출 래퍼. 401 needLogin 을 전역 이벤트로 알린다(LoginOverlay 가 수신).
// GET 응답을 URL 키로 memo(프로미스 캐시) → 탭 전환/중복요청 재fetch 방지. refresh() 에서 clear.
// updated: 2026-07-09
const REQUEST_TIMEOUT_MS = 35 * 1000;
// 타임라인은 보조 정보이고 하위 changelog까지 모아 일반 조회보다 비싸다. 이 요청 하나가
// 다이얼로그를 오래 붙잡지 않게 더 짧게 끊고, 해당 패널에서만 다시 시도하게 한다.
const TIMELINE_TIMEOUT_MS = 15 * 1000;

async function req(path, opts) {
  // 브라우저 fetch 기본값은 무한 대기다. 서버/SSO worker 가 비정상 상태여도 화면의 버튼과
  // 새로고침은 다시 눌릴 수 있어야 하므로 모든 요청에 상한을 둔다. 로그인·업로드처럼 사람이
  // 오래 기다리는 동작만 호출부에서 timeoutMs 를 넓힌다.
  const o = Object.assign({}, opts || {});
  const timeoutMs = Number(o.timeoutMs) || REQUEST_TIMEOUT_MS;
  delete o.timeoutMs;
  const ctl = new AbortController();
  o.signal = ctl.signal;
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  let r;
  try {
    r = await fetch(path, o);
  } catch (e) {
    if (ctl.signal.aborted) throw new Error("요청 시간이 초과되었습니다. 앱은 계속 실행 중이며 잠시 후 다시 시도할 수 있습니다.");
    throw e;
  } finally {
    clearTimeout(timer);
  }
  if (r.status === 401) {
    let b = {}; try { b = await r.clone().json(); } catch (e) {}
    if (b && b.needLogin) { watchAuth(); window.dispatchEvent(new CustomEvent("need-login")); }
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

/** Read newline-delimited JSON incrementally.  The timeout is inactivity-based: every completed
 *  leaf resets it, while a filter change aborts immediately through the caller's signal. */
async function streamNdjson(path, onEvent, signal) {
  const ctl = new AbortController();
  let timedOut = false, timer = null;
  const abortFromCaller = () => ctl.abort();
  if (signal) {
    if (signal.aborted) ctl.abort();
    else signal.addEventListener("abort", abortFromCaller, { once: true });
  }
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { timedOut = true; ctl.abort(); }, REQUEST_TIMEOUT_MS);
  };
  arm();
  try {
    const response = await fetch(path, {
      signal: ctl.signal, headers: { Accept: "application/x-ndjson" }, cache: "no-store",
    });
    if (response.status === 401) {
      watchAuth(); window.dispatchEvent(new CustomEvent("need-login"));
      throw new Error("HTTP 401");
    }
    if (!response.ok) throw new Error("HTTP " + response.status);
    const decoder = new TextDecoder();
    let pending = "";
    const consume = (text) => {
      pending += text;
      const lines = pending.split("\n");
      pending = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "error") {
          if (event.needLogin) { watchAuth(); window.dispatchEvent(new CustomEvent("need-login")); }
          throw new Error(event.error || "Task 스트림을 불러오지 못했습니다.");
        }
        onEvent(event);
      }
    };
    if (!response.body || !response.body.getReader) {
      consume(await response.text() + "\n");
      return;
    }
    const reader = response.body.getReader();
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      arm();
      consume(decoder.decode(part.value, { stream: true }));
    }
    consume(decoder.decode() + (pending ? "\n" : ""));
  } catch (error) {
    if (timedOut) throw new Error("요청 시간이 초과되었습니다. 앱은 계속 실행 중이며 잠시 후 다시 시도할 수 있습니다.");
    throw error;
  } finally {
    clearTimeout(timer);
    if (signal) signal.removeEventListener("abort", abortFromCaller);
  }
}

// memo 는 **LRU 상한**을 둔다 — 앱 창은 트레이 상주로 며칠씩 살아 있는데, 무한 memo 면
// 열람한 모든 티켓의 본문·코멘트·타임라인 JSON 이 힙에 계속 쌓인다(장기 사용 메모리 증가의 주범).
// 최근 300개면 "다시 열면 즉시" 체감은 유지되면서 힙은 일정 수준에서 멈춘다.
const _memo = new Map();
const MEMO_MAX = 300;
function get(path, opts) {
  if (_memo.has(path)) {
    const p = _memo.get(path);
    _memo.delete(path); _memo.set(path, p);   // 재삽입 = 최근 사용 표시(Map 은 삽입순 유지)
    return p;
  }
  const p = req(path, opts).catch((e) => { _memo.delete(path); throw e; });  // 실패는 캐시 안 함
  _memo.set(path, p);
  if (_memo.size > MEMO_MAX) _memo.delete(_memo.keys().next().value);  // 가장 오래된 것부터
  return p;
}
// 쓰기 후 그 티켓 관련 GET memo 를 비워 다음 조회가 최신을 읽게 한다(코멘트·첨부·타임라인 등).
function evict(sub) { for (const k of Array.from(_memo.keys())) if (k.includes(sub)) _memo.delete(k); }
// 하위/형제/조상 **목록**은 **다른 티켓의** 것도 이 티켓 변화에 낡는다(부모 키를 프론트가 모름).
// 상태·담당·제목 변경, 하위 생성/삭제 후엔 이 목록 memo 를 통째로 비워, **부모 다이얼로그의 자식
// 목록**(리포트된 버그: 하위 상태 바꿨는데 부모의 자식목록엔 반영 안 됨)이 최신을 받게 한다.
function evictLists() { evict("/children"); evict("/siblings"); evict("/ancestors"); }

// ── 인증 복귀 감시 ────────────────────────────────────────────────────────────
// **리포트된 버그**: prod 첫 기동에서 각 탭이 '불러오는 중' 에서 안 끝난다. 원인은 두 겹이었다.
//
//  1) 런처(run.py)가 최초 1회 SSO 를 **서버 자기 창으로** 돌린다. 그 경로에서는 프론트가
//     /api/login 을 부른 적이 없어 auth-ok 를 **영영 못 받는다**. 인증이 끝나도 화면은 그걸
//     모르니 아무도 다시 받지 않는다. (상단 배너만 혼자 'ok' 로 바뀌고 입을 다물었다.)
//  2) 설령 auth-ok 가 와도 아래 memo 가 **세션 없이 떠난 그 요청의 프로미스를 그대로** 돌려줘,
//     재시도가 죽은 요청에 다시 매달렸다. Ctrl+Shift+R 만 들었던 이유가 이것이다(새 페이지 =
//     빈 memo). 그래서 사용자에겐 '강제 새로고침 전엔 안 뜬다' 로 보였다.
//
// → 미인증을 알게 되면 /api/status(**Jira 를 타지 않는** 경량 상태)를 지켜보다가, 인증이 서면
//   memo 를 비우고 auth-ok 를 쏜다. 각 화면은 이미 auth-ok 에서 다시 받도록 돼 있다.
const AUTH_POLL_MS = 2500, AUTH_WATCH_MAX = 15 * 60 * 1000;
let _authTimer = null, _authUntil = 0;

function stopAuthWatch() { if (_authTimer) { clearInterval(_authTimer); _authTimer = null; } }

/** 미인증을 알게 됐을 때 부른다. 인증이 서는 순간을 잡아 화면들을 깨운다(중복 호출 안전). */
export function watchAuth() {
  _authUntil = Date.now() + AUTH_WATCH_MAX;      // 되살아날 기미가 없으면 영원히 돌지는 않는다
  if (_authTimer) return;
  _authTimer = setInterval(() => {
    if (Date.now() > _authUntil) return stopAuthWatch();
    req("/api/status").then((s) => {
      if (!s || s.needLogin) return;
      _memo.clear();                             // ★ 죽은 요청을 버린다 — 안 비우면 재시도가 거기 매달린다
      stopAuthWatch();
      window.dispatchEvent(new CustomEvent("auth-ok"));
    }).catch(() => { /* 서버가 잠깐 안 닿는 것 — 다음 주기에 다시 본다 */ });
  }, AUTH_POLL_MS);
}

// LoginOverlay 가 직접 로그인해 성공한 경로도 같은 청소가 필요하다. 이 리스너는 **모듈 평가
// 시점**에 걸리므로 어떤 컴포넌트의 auth-ok 처리보다 **먼저** 돈다 — 화면들이 다시 받을 때는
// memo 가 이미 비어 있어 진짜 새 요청이 나간다(등록 순서가 곧 실행 순서다).
window.addEventListener("auth-ok", () => { _memo.clear(); stopAuthWatch(); });

function jsonReq(path, method, body) {
  return req(path, { method, headers: { "Content-Type": "application/json" },
                     body: JSON.stringify(body || {}) });
}

// 유휴 시간에 **미리 데워 둔다** — get() 은 memo 라, 이때 받아 두면 나중에 팝업/다이얼로그가
// 열릴 때 네트워크 없이 즉시 뜬다(실패는 조용히 무시 — 어차피 필요할 때 다시 받는다).
function idle(fn) { (window.requestIdleCallback || ((f) => setTimeout(f, 250)))(fn); }
function warmGet(u) { try { get(u).catch(() => {}); } catch (e) { /* noop */ } }

export const api = {
  health: () => req("/api/health"),                                    // 로그인 상태 — memo 제외
  raw: (path, opts) => req(path, opts),                                // memo 없이 매번 조회(설정 메뉴 등)
  evict: (key) => evict(encodeURIComponent(key)),                      // 이 티켓 관련 memo 비우기(강제 새로고침)
  ticketRefresh: (key) =>                                              // 서버측 파생 캐시까지 비우기
    jsonReq("/api/ticket/" + encodeURIComponent(key) + "/refresh", "POST", {})
      .then((r) => { evict(encodeURIComponent(key)); return r; }).catch(() => {}),
  // ── 유휴 시 미리 데우기(핫캐시) — 로그인 후에만 호출한다(prod 는 세션 준비 전 조회 시 로그인 유발) ──
  warmGlobals: () => idle(() => {                                       // 전역 FieldEdit 기본목록
    warmGet("/api/options/labels"); warmGet("/api/options/components"); warmGet("/api/mention/users?q=");
  }),
  warmTicket: (key) => idle(() => {                                     // 이 티켓의 담당/보고 기본 + 상태 전이 메뉴
    const k = encodeURIComponent(key);
    warmGet("/api/mention/users?q=&key=" + k);
    warmGet("/api/ticket/" + k + "/menu");
  }),
  login: () => req("/api/login", { method: "POST", timeoutMs: 310 * 1000 }),
  updateInfo: () => req("/api/update"),                                // 업데이트 가능 여부(배포 repo) — memo 제외
  updateRestart: () => req("/api/app/update-restart", { method: "POST" }),   // git pull + 재시작(트레이 경로)
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
  // days = '최근 완료' 로 볼 기간(7·14·28). URL 에 넣어야 memo 도 기간별로 갈린다.
  workloadPerson: (u, days) => get("/api/workload/person/" + encodeURIComponent(u) + "?days=" + (days || 7)),
  workloadBucket: (u, b, days) => get("/api/workload/" + encodeURIComponent(u) + "/" + b
                                      + "?days=" + (days || 7)),
  workloadDetail: (user) => get("/api/workload/" + encodeURIComponent(user)),
  activity: (user) => get("/api/activity/" + encodeURIComponent(user)),
  myTasks: (opts) => {                                                // 내 Task(옵션은 서버 질의 조건)
    const o = opts || {};
    return req("/api/mytasks?scope=" + encodeURIComponent(o.scope || "assignee")
      + "&openFilter=" + encodeURIComponent(o.openFilter || "all")
      + "&progFilter=" + encodeURIComponent(o.progFilter || "all")
      + "&doneFilter=" + encodeURIComponent(o.doneFilter || "1w")
      + "&deferred=1");
  },
  myTasksStream: (opts, onEvent, signal) => {
    const o = opts || {};
    return streamNdjson("/api/mytasks/stream?scope=" + encodeURIComponent(o.scope || "assignee")
      + "&openFilter=" + encodeURIComponent(o.openFilter || "all")
      + "&progFilter=" + encodeURIComponent(o.progFilter || "all")
      + "&doneFilter=" + encodeURIComponent(o.doneFilter || "1w"), onEvent, signal);
  },
  myTasksGroup: (syncId, key) => req("/api/mytasks/sync/" + encodeURIComponent(syncId)
    + "/group/" + encodeURIComponent(key)),
  myTasksEpics: (syncId) => req("/api/mytasks/sync/" + encodeURIComponent(syncId) + "/epics"),
  myTasksEpicMeta: (keys) => get("/api/mytasks/epics?keys="
    + encodeURIComponent((keys || []).join(","))),
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
  userBadge: (userId) => get("/api/mention/user/" + encodeURIComponent(userId)),
  ticketAncestors: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/ancestors"),
  ticketSiblings: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/siblings"),
  // 첫 조회는 서버가 저우선순위로 만들고 202를 즉시 돌려준다. pending 응답을 memo 하면 다음
  // poll도 영원히 같은 Promise를 보므로, 이 경로만 브라우저 memo 없이 서버 캐시를 직접 확인한다.
  ticketTimeline: (key, children) => req("/api/ticket/" + encodeURIComponent(key)
                               + "/timeline?deferred=1&children=" + (children ? "1" : "0"),
                               { timeoutMs: TIMELINE_TIMEOUT_MS }),
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
  recent: (limit, kind) => {                                             // memo 제외(자주 바뀜)
    const p = new URLSearchParams();
    if (limit) p.set("limit", limit);
    if (kind) p.set("kind", kind);
    return req("/api/recent" + (p.size ? "?" + p.toString() : ""));
  },
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
  // 기본 목록(빈 질의)은 **memo** — 같은 팝업을 다시 열 때 네트워크 없이 즉시 뜬다(라벨·컴포넌트
  // 기본 추천). 질의를 치는 순간부터는 fresh(req). 쓰기(evict)·refresh 로 무효화된다.
  options: (kind, q) => { const u = "/api/options/" + kind + (q ? "?q=" + encodeURIComponent(q) : ""); return q ? req(u) : get(u); },
  updateFields: (key, body) => jsonReq("/api/ticket/" + encodeURIComponent(key) + "/fields",
                                       "PUT", body).then((r) => { evict(key); evictLists(); return r; }),
  childTypes: (key) => req("/api/options/childtypes?q=" + encodeURIComponent(key)),
  taskTypes: () => req("/api/options/tasktypes"),                       // Epic 없이 만들 최상위 타입
  // Bulk 생성 — validate 는 dry-run(쓰기 없음), create 는 서버가 다시 검증한 뒤 차례로 만든다.
  // 생성은 여러 티켓을 한꺼번에 바꾸므로 목록 memo 를 통째로 비운다(어디에 떨어질지 모른다).
  bulkValidate: (body) => jsonReq("/api/bulk/validate", "POST", body),
  bulkCreate: (body) => jsonReq("/api/bulk/create", "POST", body).then((r) => { _memo.clear(); return r; }),
  createTask: (body) => jsonReq("/api/task", "POST", body).then((r) => { _memo.clear(); return r; }),
  createEpic: (body) => jsonReq("/api/epic", "POST", body).then((r) => { _memo.clear(); return r; }),
  // Epic 에 넣을 수 있는 **Task** 후보(Sub-Task 의 상위 피커). 진짜 Epic 목록은 options("epics").
  parentTaskCandidates: (q) => req("/api/parent-task-candidates?limit=25&q=" + encodeURIComponent(q || "")),
  createChild: (key, body) => jsonReq("/api/ticket/" + encodeURIComponent(key) + "/child",
                                      "POST", body)
    // 만든 직후 부모의 하위 목록을 다시 받아야 한다 — memo 를 안 비우면 **늘 만들기 전 목록**이
    // 돌아온다(프로미스 캐시라 서버가 최신을 줘도 소용없다).
    .then((r) => { evict(encodeURIComponent(key)); evictLists(); return r; }),
  // 상태 전이 메뉴 — **memo**(같은 상태에선 안정적, 유휴 워밍이 재사용). 전이하면 evict(key) 로 비워진다
  // (doTransition·ticket-changed 가 key memo 를 털어 낡은 전이목록으로 400 나는 걸 막는다).
  ticketMenu: (key) => get("/api/ticket/" + encodeURIComponent(key) + "/menu"),
  setAssignee: (key, assignee) => jsonReq("/api/ticket/" + encodeURIComponent(key) + "/assignee",
                                          "PUT", { assignee }).then((r) => { evict(key); evictLists(); return r; }),
  deleteTicket: (key) => req("/api/ticket/" + encodeURIComponent(key), { method: "DELETE" })
                           .then((r) => { evict(key); evictLists(); return r; }),
  transitions: (key) => req("/api/ticket/" + encodeURIComponent(key) + "/transitions"),
  doTransition: (key, body) => jsonReq("/api/ticket/" + encodeURIComponent(key) + "/transition",
                                       "POST", body).then((r) => { evict(key); evictLists(); return r; }),                                // 본인 댓글 판정
  // 빈 쿼리(팝업 첫 오픈)의 기본 추천 목록은 **memo** — 담당/보고 picker 를 다시 열 때 즉시.
  // (티켓 유관자 기본 목록은 issue+comment 조회라 prod 에서 특히 느렸다.) 질의 입력 시엔 fresh.
  mentionUsers: (q, key) => { const u = "/api/mention/users?q=" + encodeURIComponent(q || "")
    + (key ? "&key=" + encodeURIComponent(key) : ""); return q ? req(u) : get(u); },
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
  // 본문/코멘트 안 index 번째 체크박스 토글 → 서버가 원본 필드의 그 체크박스만 뒤집어 저장.
  toggleCheckbox: (key, body) =>
    jsonReq("/api/ticket/" + encodeURIComponent(key) + "/checkbox", "POST", body)
      .then((r) => { evict(encodeURIComponent(key)); return r; }),
  attachmentUpload: (key, file) => {                                   // multipart — Content-Type 자동
    const fd = new FormData();
    fd.append("file", file, file.name || "paste.png");
    return req("/api/ticket/" + encodeURIComponent(key) + "/attachment",
               { method: "POST", body: fd, timeoutMs: 16 * 60 * 1000 })
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
