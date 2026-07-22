// recent.js — '최근 열어본 항목'(Jira 티켓·Confluence 문서·웹 링크) 기록.
//
// 왜 서버에 두나: 브라우저 히스토리·캐시는 **웹페이지가 읽을 수 없다**(보안). 그래서 우리가 연
// 것은 우리가 적어야 한다. 그리고 이 백엔드는 상주하며 사용자는 앱 창·크롬·엣지 등 여러
// 브라우저로 같은 백엔드를 연다 → localStorage 에 두면 브라우저마다 목록이 갈린다. 서버(SQLite)에
// 두면 어디서 열든 한 목록이다.
import { api } from "./api.js";

const _last = new Map();            // url -> ts. 같은 항목이 연속으로 여러 번 기록되는 것 방지
const DEDUP_MS = 3000;

export function recordOpen(item) {
  if (!item || !item.url) return;
  const now = Date.now();
  if (now - (_last.get(item.url) || 0) < DEDUP_MS) return;
  _last.set(item.url, now);
  api.recentAdd({
    url: item.url,
    kind: item.kind || "web",
    title: (item.title || item.url).slice(0, 300),
    meta: (item.meta || "").slice(0, 300),
  }).catch(() => { /* 기록 실패는 사용자 흐름을 막지 않는다 */ });
}

/** 검색 하이라이트(<mark>)가 섞인 제목을 평문으로 — 저장·표시용. */
export function stripTags(s) {
  const d = document.createElement("div");
  d.innerHTML = String(s == null ? "" : s);
  return (d.textContent || "").trim();
}
