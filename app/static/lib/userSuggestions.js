// userSuggestions.js — 담당자/보고자 FieldEdit와 @멘션이 공유하는 사용자 추천.
// 같은 티켓·같은 빈 검색이면 두 UI가 같은 최근 사용자와 서버 기본 추천을 같은 순서로 보여야 한다.
import { api } from "./api.js";
import { createTypeahead } from "./typeahead.js";

const RECENT_KEY = "userSuggestions.recent";
const LEGACY_KEYS = ["fe.recent.assignee", "fe.recent.reporter"];
const RECENT_MAX = 6;
const RESULT_MAX = 8;

function stored(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (e) { return []; }
}

function clean(user) {
  if (!user || !user.id) return null;
  return { id: String(user.id), name: user.name || user.displayName || String(user.id),
           display: user.display || user.displayName || user.name || String(user.id), avatar: user.avatar || "" };
}

/** 여러 출처의 사용자를 앞 출처 우선으로 합친다. */
export function mergeUserSuggestions(...groups) {
  const out = [], seen = new Set();
  for (const group of groups) for (const raw of (group || [])) {
    const user = clean(raw);
    if (!user || seen.has(user.id)) continue;
    seen.add(user.id); out.push(user);
  }
  return out;
}

/** 예전 담당자/보고자별 최근값도 한 번에 읽어 두 UI의 추천이 곧바로 같아지게 한다. */
export function recentUsers() {
  return mergeUserSuggestions(stored(RECENT_KEY), ...LEGACY_KEYS.map(stored)).slice(0, RECENT_MAX);
}

export function rememberUser(user) {
  const item = clean(user);
  if (!item) return;
  const next = mergeUserSuggestions([item], recentUsers()).slice(0, RECENT_MAX);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch (e) { /* 사파리 프라이빗 등 */ }
}

/** 빈 검색의 공통 목록: 최근 선택 → 로컬 선택지 → 티켓 기반 서버 추천. */
export function defaultUserSuggestions(serverItems, localItems) {
  return mergeUserSuggestions(recentUsers(), localItems, serverItems).slice(0, RESULT_MAX);
}

function finalUsers(query, serverItems, localItems) {
  return query ? mergeUserSuggestions(serverItems).slice(0, RESULT_MAX)
               : defaultUserSuggestions(serverItems, localItems);
}

/** FieldEdit용. 서버 캐시는 재사용하되 최근값 병합은 매번 다시 해 방금 고른 사람도 즉시 반영한다. */
export function createUserTypeahead(ticketKey, localItems) {
  const runner = createTypeahead((q) => api.mentionUsers(q, ticketKey),
                                 { minLen: 1, allowEmpty: true });
  return {
    cancel: runner.cancel,
    run(query) {
      const q = String(query || "").trim();
      return runner.run(q).then((items) => items == null ? null : finalUsers(q, items, localItems));
    },
  };
}

/** TipTap v3 @멘션용 초기값. 서버를 기다리지 않고 FieldEdit와 같은 최근 목록부터 보여 준다. */
export function mentionInitialUsers() {
  return defaultUserSuggestions([], []);
}

/** TipTap v3 @멘션용. 디바운스·응답 역전·취소는 Suggestion이 관리한다. */
export function createManagedMentionItems(ticketKey) {
  return ({ query, signal }) => {
    const q = String(query || "").trim();
    return api.mentionUsers(q, ticketKey, { signal }).then((items) => finalUsers(q, items));
  };
}
