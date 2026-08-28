// Pure presentation policies and persisted layout preferences for TicketDialog.
import { ymd } from "../../lib/fmt.js";

export const FOLD_AT = 5;
export const TIMELINE_POLL_MS = 800;
export const TIMELINE_WAIT_MS = 15 * 1000;

export const KID_SORTS = [
  { k: "due", label: "마감", hint: "마감일 → 우선순위" },
  { k: "pri", label: "우선순위", hint: "우선순위 → 마감일" },
  { k: "who", label: "담당자", hint: "담당자 이름 → 마감일" },
];

const KID_SORT_KEY = "tkt.kidSort";
const SPINE_W_KEY = "tkt.spineW";
const SPINE_HIDE_KEY = "tkt.spineHidden";
const TL_W_KEY = "tkt.tlW";
const TL_HIDE_KEY = "tkt.tlHidden";

function read(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}

function write(key, value) {
  try { localStorage.setItem(key, value); } catch (_) { /* private storage or disabled */ }
}

export function loadKidSort() {
  const value = read(KID_SORT_KEY);
  return KID_SORTS.some((option) => option.k === value) ? value : "due";
}

export function saveKidSort(value) { write(KID_SORT_KEY, value); }

export function loadSpineW() {
  const value = parseInt(read(SPINE_W_KEY), 10);
  return value >= 180 && value <= 460 ? value : 264;
}

export function saveSpineW(value) { write(SPINE_W_KEY, String(value)); }
export function loadSpineHidden() { return read(SPINE_HIDE_KEY) === "1"; }
export function saveSpineHidden(value) { write(SPINE_HIDE_KEY, value ? "1" : "0"); }

export function loadTlW() {
  const value = parseInt(read(TL_W_KEY), 10);
  return value >= 170 && value <= 440 ? value : 220;
}

export function saveTlW(value) { write(TL_W_KEY, String(value)); }
export function loadTlHidden() { return read(TL_HIDE_KEY) === "1"; }
export function saveTlHidden(value) { write(TL_HIDE_KEY, value ? "1" : "0"); }

export function formatBytes(value) {
  if (!value) return "";
  if (value < 1024) return value + "B";
  if (value < 1024 * 1024) return Math.round(value / 1024) + "KB";
  return (value / (1024 * 1024)).toFixed(1) + "MB";
}

export function descriptionEmpty(html) {
  if (!html) return true;
  const root = document.createElement("div");
  root.innerHTML = html;
  if (root.querySelector("img, table, pre, code, li, blockquote")) return false;
  return !(root.textContent || "").replace(/\u00a0/g, " ").trim();
}

function daysTo(iso) {
  if (!iso) return null;
  const due = new Date(String(iso).substring(0, 10) + "T00:00:00");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((due - today) / 86400000);
  return isNaN(days) ? null : days;
}

export function childCard(child, parentDue) {
  const inherited = !child.due && parentDue ? parentDue : null;
  const due = child.due || inherited;
  return {
    statusCategory: child.statusCategory,
    resolved: child.resolved,
    due,
    dueInherited: !!inherited,
    dueDays: daysTo(due),
  };
}

export function sortChildren(children, sortKey, parentDue) {
  const noDue = 99999;
  const due = (child) => {
    const value = childCard(child, parentDue).dueDays;
    return value === null || value === undefined ? noDue : value;
  };
  const priority = (child) => (
    child.priRank === null || child.priRank === undefined ? 2 : child.priRank
  );
  const assignee = (child) => child.assignee || "\uffff";
  const finished = (child) => child.statusCategory === "done" ? 1 : 0;
  const rest = (left, right) => due(left) - due(right)
    || priority(left) - priority(right)
    || left.key.localeCompare(right.key);
  const byDue = (left, right) => finished(left) - finished(right) || rest(left, right);
  const byPriority = (left, right) => finished(left) - finished(right)
    || priority(left) - priority(right)
    || due(left) - due(right)
    || left.key.localeCompare(right.key);
  const compare = sortKey === "pri" ? byPriority
    : sortKey === "who"
      ? ((left, right) => assignee(left).localeCompare(assignee(right), "ko")
        || finished(left) - finished(right) || rest(left, right))
      : byDue;
  return (children || []).slice().sort(compare);
}

export function sortComments(comments, direction) {
  const result = (comments || []).slice();
  const time = (comment) => {
    const value = Date.parse(comment && comment.date);
    return isNaN(value) ? 0 : value;
  };
  result.sort((left, right) => (
    direction === "old" ? time(left) - time(right) : time(right) - time(left)
  ));
  return result;
}

export function priorityClass(name) {
  if (!name) return "unset";
  const match = /^\s*P(\d+)/i.exec(name);
  return match ? "pr-" + Math.min(+match[1], 4) : "";
}

export function timelineKind(event) {
  return (event.kind || "").replace(/^child-/, "");
}

export function timelineBadged(event) {
  return ["status", "priority", "duedate"].includes(timelineKind(event));
}

export function timelineLabel(event) {
  return { status: "상태", priority: "우선순위", duedate: "마감일" }[timelineKind(event)];
}

export function timelineBadgeClass(event, value) {
  const kind = timelineKind(event);
  if (kind === "priority") return priorityClass(value);
  if (kind === "duedate") return "";
  const category = value === event.from ? event.fromCat : event.toCat;
  return category ? "st-" + category : "";
}

export function timelineValue(event, value) {
  const kind = timelineKind(event);
  if (value) return kind === "duedate" ? (ymd(value) || value) : value;
  return kind === "priority" ? "미지정" : "없음";
}

export function timelineText(event) {
  const from = event.from || "없음";
  const to = event.to || "없음";
  const kind = timelineKind(event);
  if (kind === "created") return "티켓 생성";
  if (kind === "comment") return "댓글 작성";
  if (kind === "status") return "상태 " + from + " → " + to;
  if (kind === "assignee") return "담당자 " + from + " → " + to;
  if (kind === "resolution") return event.to ? "해결: " + event.to : "해결 취소";
  if (kind === "duedate") return "마감일 " + from + " → " + to;
  if (kind === "priority") {
    return "우선순위 " + (event.from || "미지정") + " → " + (event.to || "미지정");
  }
  return (event.field || "변경") + " " + from + " → " + to;
}
