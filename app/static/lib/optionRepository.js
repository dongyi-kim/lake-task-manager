// Synchronous option snapshots shared by FieldEdit and ticket-creation dialogs.
// Network hydration is owned by callers; this repository never hides local/default choices while it runs.
import { recentItems } from "./recent.js";

const CACHE_KEY = "optionRepository.v1";
const LEGACY_CREATE_CACHE = "newTicket.optionCache.v1";
const FIELD_PREFIX = "fe.recent.";

function readObject(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch (error) {
    return {};
  }
}

function readArray(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (error) {
    return [];
  }
}

export function mergeOptions(groups, identity) {
  const output = [];
  const seen = new Set();
  for (const group of groups || []) for (const item of (group || [])) {
    const rawId = identity ? identity(item) : item;
    const id = String(rawId == null ? "" : rawId);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    output.push(item);
  }
  return output;
}

export function cachedOptions(kind) {
  const current = readObject(CACHE_KEY)[kind];
  if (Array.isArray(current)) return current.filter(Boolean);
  const legacy = readObject(LEGACY_CREATE_CACHE)[kind];
  return Array.isArray(legacy) ? legacy.filter(Boolean) : [];
}

export function rememberOptions(kind, values) {
  const list = mergeOptions([values || []], (value) => String(value || "").trim());
  if (!list.length) return list;
  try {
    const cache = readObject(CACHE_KEY);
    cache[kind] = list;
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
  } catch (error) {
    // The live dialog keeps the resolved values even when persistent storage is unavailable.
  }
  return list;
}

export function recentFieldOptions(field) {
  return readArray(FIELD_PREFIX + field);
}

export function rememberFieldOption(field, item, max = 6) {
  if (!item || !item.id) return;
  const next = [item].concat(
    recentFieldOptions(field).filter((candidate) => candidate && candidate.id !== item.id)
  ).slice(0, max);
  try {
    localStorage.setItem(FIELD_PREFIX + field, JSON.stringify(next));
  } catch (error) {
    // Recent options are an enhancement; selection itself must still succeed.
  }
}

export function fieldObjectSnapshot(field, values, identity) {
  return mergeOptions([recentFieldOptions(field), values || []], (item) => (
    item && (item.id || identity(item))
  ));
}

export function fieldStringSnapshot(field, values) {
  const recent = recentFieldOptions(field).map((item) => item && item.id);
  return mergeOptions([recent, values || []], (value) => String(value || ""));
}

export function recentEpicOptions(limit = 50) {
  const output = [];
  const indexes = new Map();
  for (const item of recentItems(limit, "jira")) {
    const isEpic = String(item.type || item.issuetype || "").toLowerCase() === "epic";
    const key = String(isEpic ? (item.key || "") : (item.epicKey || "")).toUpperCase();
    if (!key) continue;
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const summary = String(item.summary || item.title || "")
      .replace(new RegExp("^" + escaped + "\\s*"), "");
    const name = String(item.epicName || (isEpic ? summary : "") || key);
    const candidate = { id: key, key, name, summary: isEpic ? (summary || name) : name };
    const score = (candidate.name !== key ? 2 : 0) + (candidate.summary !== key ? 1 : 0);
    const index = indexes.get(key);
    if (index == null) {
      indexes.set(key, output.length);
      output.push(candidate);
    } else {
      const current = output[index];
      const currentScore = (current.name !== key ? 2 : 0) + (current.summary !== key ? 1 : 0);
      // 더 최근의 Task 기록에는 Epic key만 있고, 조금 전 연 Epic 기록에는 실제 이름이 있을 수 있다.
      // 순서는 최신 항목 기준으로 유지하되 같은 key의 더 풍부한 로컬 정보로 보강한다.
      if (score > currentScore) output[index] = candidate;
    }
  }
  return output;
}
