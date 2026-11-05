// Local display filters use the same effective card values as the board renderer.
// The caller supplies inherited SubTask deadlines and includes parent cards in options.
import { priRankOf } from "../ui/PriIcon.js";

export const UPDATE_FILTER_OPTIONS = [
  { k: "yes", label: "있음" }, { k: "no", label: "없음" }, { k: "all", label: "모두" },
];
export const DUE_FILTER_OPTIONS = [
  { k: "", label: "전체" },
  { k: "calm", label: "여유", hint: "마감까지 8일 이상" },
  { k: "soon", label: "임박", hint: "오늘부터 7일 이내" },
  { k: "overdue", label: "지남", hint: "마감일이 지난 티켓" },
  { k: "none", label: "마감 없음" },
];

export function deadlineBucket(card) {
  const days = card?.dueDays;
  if (!Number.isFinite(days)) return "none";
  if (days < 0) return "overdue";
  return days <= 7 ? "soon" : "calm";
}

// Preserve Jira's actual names: e.g. Unclassified and Medium may share a sorting rank.
export function priorityKey(card) {
  return typeof card?.pri === "string" ? card.pri.trim() : "";
}

function priorityValue(card) {
  const name = priorityKey(card);
  return name ? "name:" + name : "__none__";
}

export function priorityOptions(cards, selected = "") {
  const options = new Map();
  for (const card of cards || []) {
    if (!card) continue;
    const k = priorityValue(card);
    const rank = Number.isFinite(card.priRank) ? card.priRank : 999;
    const existing = options.get(k);
    if (existing) existing.rank = Math.min(existing.rank, rank);
    else options.set(k, { k, label: priorityKey(card) || "미지정", rank });
  }
  // A filter persists across scope changes; keep it selectable even with no matches.
  if (selected && !options.has(selected)) {
    if (selected === "__none__") options.set(selected, { k: selected, label: "미지정", rank: 999 });
    else if (selected.startsWith("name:") && selected.slice(5).trim()) {
      options.set(selected, { k: selected, label: selected.slice(5), rank: 999 });
    }
  }
  return [...options.values()].sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label, "ko"));
}

export function isUnrankedPriority(name) {
  return /^(?:unclassified|none|not set|undefined|-)?$/i.test(String(name || "").trim());
}

export function matchTaskDisplayFilters(card, priorityFilter = "", dueFilter = "", threshold) {
  const selected = priorityFilter.startsWith("name:") ? priorityFilter.slice(5) : "";
  const rank = Number.isFinite(threshold) && threshold !== 999 ? threshold : priRankOf(selected);
  // Unclassified/empty values are not a low priority. Keep those choices exact.
  const priorityMatch = !priorityFilter || (isUnrankedPriority(selected)
    ? priorityValue(card) === priorityFilter
    : !isUnrankedPriority(priorityKey(card))
      && (Number.isFinite(card?.priRank) ? card.priRank : priRankOf(priorityKey(card))) <= rank);
  return priorityMatch
    && (!dueFilter || deadlineBucket(card) === dueFilter);
}
