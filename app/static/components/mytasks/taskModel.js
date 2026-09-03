// Pure My Tasks layout, filtering, and snapshot reconciliation policy.
import { categoryColor } from "../../lib/colors.js";

export const NO_DUE = 1e6;
export const TASK_RETRY_DELAYS = [800, 2400];
export const STATES = [
  { k: "todo", label: "할당됨", drop: "작업 대기" },
  { k: "inprogress", label: "진행 중", drop: "진행 중" },
  { k: "done", label: "최근 완료", drop: "완료" },
];
export const SUB_CAP = 5;
// Task with SubTask는 자식을 쪼개지 않고 한 항목으로 센다.
export const AXIS_PAGE_SIZE = 40;
export const NARROW = "(max-width: 900px)";
export const PREF_KEY = "mytasks.opts";

export const OPTIONS = [
  { key: "groupBy", label: "그룹화", opts: [
    { k: "none", label: "없음", hint: "모든 티켓을 개별 카드로" },
    { k: "sub", label: "Sub Task", hint: "부모 Task 로 묶고 그 안에 Sub-Task — 하위가 없는 Task 는 그냥 카드" }] },
  { key: "subView", label: "Sub Task 보기", opts: [
    { k: "collapsed", label: "모두 접기", hint: "하위를 모두 접는다 — 부모 Task 만 본다" },
    { k: "mine", label: "내 티켓만", hint: "하위 중 내가 담당인 것만 펼친다" },
    { k: "all", label: "모든 티켓", hint: "동료가 담당인 하위(유관)까지 모두 펼친다" }] },
  { key: "sort", label: "정렬", opts: [
    { k: "due", label: "마감", hint: "1차 마감 → 2차 우선순위" },
    { k: "pri", label: "우선순위", hint: "1차 우선순위 → 2차 마감" },
    { k: "epic", label: "소속 Epic", hint: "Epic 으로 모으고 그 안에서 우선순위 → 마감" }] },
];

export const BAND_FILTERS = {
  todo: { key: "openFilter", opts: [
    { k: "all", label: "모두", hint: "담당된 모든 미착수 티켓" },
    { k: "2w", label: "2주 내 갱신", hint: "최근 2주 안에 갱신된 것만 — 오래 방치된 건 감춘다" }] },
  inprogress: { key: "progFilter", opts: [
    { k: "1m", label: "1달 내 갱신", hint: "최근 1달 안에 손댄 것만 — 오래 멈춘 진행 중은 감춘다" },
    { k: "all", label: "모두", hint: "진행 중인 모든 티켓" }] },
  done: { key: "doneFilter", opts: [
    { k: "1w", label: "1주", hint: "최근 1주 안에 완료" },
    { k: "1m", label: "1달", hint: "최근 1달 안에 완료" }] },
};

export const STATE_KEYS = new Set(STATES.map((state) => state.k));
const VOC_SIG = "var(--ty-story)";

export function taskLoadErrorKind(error) {
  const message = String((error && error.message) || error || "").toLowerCase();
  if (/\b403\b|forbidden|permission|not permitted|권한/.test(message)) return "permission";
  if (/\b401\b|login|required|session|anonymous|인증|로그인/.test(message)) return "auth";
  return "other";
}

export function retryDelay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** MyTasks snapshots are plain JSON.  Hydration works on a request-owned copy so an obsolete
 *  same-filter response cannot mutate the object currently rendered (or its cache alias). */
export function cloneTaskModel(model) {
  return model == null ? model : JSON.parse(JSON.stringify(model));
}

export function uniformStatusCategory(cards) {
  let only = null;
  for (const card of cards || []) {
    const status = STATE_KEYS.has(card.statusCategory) ? card.statusCategory : "todo";
    if (only !== null && only !== status) return null;
    only = status;
  }
  return only;
}

export function epicSig(card) {
  if (card.epicKey) return categoryColor(card.epicKey);
  if (card.voc) return VOC_SIG;
  return null;
}

export function resolveDefaultModule(selected, explicit, mine, all) {
  const current = typeof selected === "string" ? selected : "";
  const mineList = Array.isArray(mine) ? mine.filter(Boolean) : [];
  const allList = Array.isArray(all) ? all.filter(Boolean) : [];
  const known = new Set(allList);
  if (explicit && (!current || !allList.length || known.has(current))) {
    return { selected: current, explicit: true, changed: false };
  }
  const next = mineList.find((module) => !allList.length || known.has(module)) || "";
  return { selected: next, explicit: false, changed: next !== current || !!explicit };
}

function sameTaskData(a, b) {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length
      && a.every((value, index) => sameTaskData(value, b[index]));
  }
  const aKeys = Object.keys(a), bKeys = Object.keys(b);
  return aKeys.length === bKeys.length && aKeys.every((key) => Object.prototype.hasOwnProperty.call(b, key)
    && sameTaskData(a[key], b[key]));
}

export function patchTaskData(current, incoming, skip) {
  const ignored = skip || new Set();
  for (const key of Object.keys(current)) {
    if (!ignored.has(key) && !Object.prototype.hasOwnProperty.call(incoming, key)) delete current[key];
  }
  for (const [key, value] of Object.entries(incoming)) {
    if (!ignored.has(key) && !sameTaskData(current[key], value)) current[key] = value;
  }
  return current;
}

function reconcileTaskRows(current, incoming, patch) {
  const rows = Array.isArray(current) ? current : [];
  const old = new Map(rows.filter((row) => row && row.key).map((row) => [row.key, row]));
  const next = (incoming || []).map((row) => {
    const existing = row && old.get(row.key);
    return existing ? patch(existing, row) : row;
  });
  if (rows.length !== next.length || rows.some((row, index) => row !== next[index])) {
    rows.splice(0, rows.length, ...next);
  }
  return rows;
}

function reconcileTaskGroup(current, incoming) {
  current.atoms = reconcileTaskRows(current.atoms, incoming.atoms, patchTaskData);
  current.others = reconcileTaskRows(current.others, incoming.others, patchTaskData);
  return patchTaskData(current, incoming, new Set(["atoms", "others"]));
}

export function reconcileTaskModel(current, incoming) {
  if (!current || !incoming || !Array.isArray(current.groups) || !Array.isArray(incoming.groups)) {
    return incoming;
  }
  current.groups = reconcileTaskRows(current.groups, incoming.groups, reconcileTaskGroup);
  current.epics = reconcileTaskRows(current.epics || [], incoming.epics || [], patchTaskData);
  return patchTaskData(current, incoming, new Set(["groups", "epics"]));
}
