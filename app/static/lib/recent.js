// recent.js — '최근 열어본 항목'(Jira 티켓·Confluence 문서·웹 링크) 기록.
//
// 서버(SQLite)가 여러 브라우저가 공유하는 정본이다. 다만 팝업을 열 때마다 서버 왕복을 기다리면
// Jira 지연과 무관한 '최근 항목'까지 늦게 보인다. 그래서 이 브라우저가 이미 본 항목은
// localStorage에도 작은 미러로 같이 적고 **즉시** 그린 뒤, 서버 정본을 비동기로 합친다.
import { api } from "./api.js";

const _last = new Map();            // url -> ts. 같은 항목이 연속으로 여러 번 기록되는 것 방지
const DEDUP_MS = 3000;
const LOCAL_KEY = "recent.items";
const LOCAL_KEEP = 100;

const _BROWSE = /\/browse\/([A-Za-z][A-Za-z0-9]*-\d+)/;

/** 저장 키 정규화 — **티켓의 정체는 키**다. URL(호스트 포함)을 정체로 삼으면 같은 티켓이
 *  base 표기마다(localhost / 127.0.0.1 / 사내주소, 혹은 주소 변경 후) 다른 항목으로 쌓인다. */
function canonicalUrl(url) {
  const m = _BROWSE.exec(url || "");
  return m ? "/browse/" + m[1].toUpperCase() : (url || "");
}

function stored() {
  try {
    const value = JSON.parse(localStorage.getItem(LOCAL_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (e) { return []; }
}

function normalize(item, fallbackOpenedAt) {
  if (!item || !item.url) return null;
  const data = item.data && typeof item.data === "object" ? item.data : {};
  const out = {
    url: canonicalUrl(item.url), kind: item.kind || "web",
    title: String(item.title || item.url).slice(0, 300),
    meta: String(item.meta || "").slice(0, 300), type: String(item.type || "").slice(0, 40),
    openedAt: Number(item.openedAt) || fallbackOpenedAt || 0,
  };
  // 서버 recent_items와 같은 평평한 모양으로 보관한다. 그래야 티켓/Epic 피커가 네트워크 전후에
  // 서로 다른 필드 경로를 해석하지 않는다.
  for (const [key, value] of Object.entries(data)) if (!(key in out)) out[key] = value;
  for (const [key, value] of Object.entries(item)) {
    if (key !== "data" && !(key in out)) out[key] = value;
  }
  return out;
}

function save(items) {
  try { localStorage.setItem(LOCAL_KEY, JSON.stringify(items.slice(0, LOCAL_KEEP))); }
  catch (e) { /* 사파리 프라이빗 모드 등 — 서버 목록은 계속 동작한다 */ }
}

function merge(...groups) {
  const byUrl = new Map();
  let order = 0;
  for (const group of groups) for (const raw of (group || [])) {
    const item = normalize(raw, -(order++));
    if (!item) continue;
    const prev = byUrl.get(item.url);
    if (!prev || item.openedAt > prev.openedAt) byUrl.set(item.url, item);
  }
  return Array.from(byUrl.values()).sort((a, b) => b.openedAt - a.openedAt).slice(0, LOCAL_KEEP);
}

/** 서버를 기다리지 않고 선택기에 바로 그릴 최근 항목. 서버 응답이 오면 hydrateRecent가 보강한다. */
export function recentItems(limit = 20, kind = "") {
  const items = merge(stored());
  return items.filter((item) => !kind || item.kind === kind).slice(0, limit);
}

/** 서버 정본과 로컬 최신 항목을 합쳐 미러를 갱신한다. 방금 연 항목이 늦은 GET에 지워지지 않는다. */
export function hydrateRecent(items) {
  const next = merge(items || [], stored());
  save(next);
  return next;
}

/** 지우기 UI도 서버 응답 전에 로컬 목록에서 즉시 빠지게 한다. */
export function forgetRecent(url) {
  const target = canonicalUrl(url || "");
  save(stored().filter((item) => canonicalUrl(item && item.url) !== target));
}

export function recordOpen(item) {
  if (!item || !item.url) return;
  const url = canonicalUrl(item.url);
  const now = Date.now();
  if (now - (_last.get(url) || 0) < DEDUP_MS) return;
  _last.set(url, now);
  const payload = {
    url,
    kind: item.kind || "web",
    title: (item.title || item.url).slice(0, 300),
    meta: (item.meta || "").slice(0, 300),
    type: (item.type || "").slice(0, 40),
    // 검색 결과와 동일 포맷으로 다시 그리기 위한 부가필드(key·epicKey/Name·assignee·status·…)
    data: item.data || {},
  };
  // 로컬 미러가 먼저다. POST가 지연/실패해도 이 브라우저의 다음 피커에는 즉시 나타난다.
  save(merge([normalize(payload, now / 1000)], stored()));
  api.recentAdd(payload).catch(() => { /* 기록 실패는 사용자 흐름을 막지 않는다 */ });
}

/** 검색 하이라이트(<mark>)가 섞인 제목을 평문으로 — 저장·표시용. */
export function stripTags(s) {
  const d = document.createElement("div");
  d.innerHTML = String(s == null ? "" : s);
  return (d.textContent || "").trim();
}
