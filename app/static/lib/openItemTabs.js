// Open tabs are scoped UI metadata, independent of recent.js visit history and drafts.
// This module never reads a body, performs a request, or owns a close/write guard.
const memory = new Map();
const VERSION = 1;
const PREFIX = "ltm.open-item-tabs.v1:";
let unscopedSequence = 0;

export function normalizeItemId(kind, value) {
  if (typeof value !== "string" && !(kind === "confluence" && Number.isSafeInteger(value) && value >= 0)) return "";
  const id = String(value).trim();
  if (!id) return "";
  if (kind === "task") {
    const match = /^([A-Z][A-Z0-9_]*)-(\d+)$/.exec(id.toUpperCase());
    if (!match) return "";
    const number = match[2].replace(/^0+(?=\d)/, "");
    return number === "0" ? "" : match[1] + "-" + number;
  }
  if (!/^\d+$/.test(id)) return "";
  const number = id.replace(/^0+(?=\d)/, "");
  return number === "0" ? "" : number;
}

function base(value) {
  if (!value) return "";
  try {
    const url = new URL(String(value));
    if (!/^https?:$/.test(url.protocol)) return "";
    return url.origin + url.pathname.replace(/\/+$/, "");
  } catch (_) { return ""; }
}

export function openItemTabsScope(kind, context = {}) {
  if (kind !== "task" && kind !== "confluence") throw new TypeError("Unknown tab kind");
  if (!context || typeof context !== "object" || Array.isArray(context)) context = {};
  return JSON.stringify({
    kind,
    env: typeof context.env === "string" ? context.env.trim().toLowerCase() : "",
    jiraBase: base(context.jiraBase),
    projectKey: typeof context.projectKey === "string" ? context.projectKey.trim().toUpperCase() : "",
    confluenceBase: base(context.confluenceBase),
  });
}

function point(value) {
  const coordinate = n => Number.isFinite(n) ? Math.max(0, n) : 0;
  return { x: coordinate(value?.x), y: coordinate(value?.y) };
}

function metadata(kind, value) {
  if (!value || typeof value !== "object") return null;
  const id = normalizeItemId(kind, value.id);
  if (!id) return null;
  // Explicit allowlist: body, attachments, receipts, drafts and arbitrary data never persist.
  return {
    id, title: typeof value.title === "string" ? value.title : "",
    scroll: point(value.scroll),
    lastOpenedAt: Number.isFinite(value.lastOpenedAt) ? value.lastOpenedAt : 0,
  };
}

function restore(kind, value) {
  const items = [], seen = new Set();
  if (value?.version === VERSION && Array.isArray(value.items)) {
    for (const source of value.items) {
      const item = metadata(kind, source);
      if (item && !seen.has(item.id)) { items.push(item); seen.add(item.id); }
    }
  }
  const activeId = normalizeItemId(kind, value?.activeId);
  return { items, activeId: seen.has(activeId) ? activeId : items[0]?.id || null };
}

// Pure keyboard model shared by the UI and Node tests. Moving focus never activates a body.
export function tabFocusTarget(items, currentId, key) {
  const ids = items.filter(item => !item.disabled).map(item => String(item.id));
  if (!ids.length) return null;
  if (key === "Home") return ids[0];
  if (key === "End") return ids[ids.length - 1];
  if (key !== "ArrowLeft" && key !== "ArrowRight") return null;
  const current = ids.indexOf(String(currentId));
  if (current < 0) return key === "ArrowLeft" ? ids[ids.length - 1] : ids[0];
  return ids[(current + (key === "ArrowLeft" ? -1 : 1) + ids.length) % ids.length];
}

export function createOpenItemTabs(options = {}) {
  const kind = options.kind;
  const unscopedId = ++unscopedSequence;
  function namespace(context) {
    const value = openItemTabsScope(kind, context), parts = JSON.parse(value);
    return { key: value + (parts.env && parts.jiraBase && parts.projectKey && (kind === "task" || parts.confluenceBase) ? "" : ":unscoped:" + unscopedId),
      ready: !!(parts.env && parts.jiraBase && parts.projectKey && (kind === "task" || parts.confluenceBase)) };
  }
  let identity = namespace(options.context), scope = identity.key;
  let storage = null;
  try { storage = options.storage === undefined ? globalThis.localStorage : options.storage; }
  catch (_) { /* Restricted storage still permits in-memory tabs. */ }
  const listeners = new Set();
  const clock = options.now || Date.now;
  function load() {
    if (memory.has(scope)) return restore(kind, memory.get(scope));
    let value;
    try {
      const raw = identity.ready ? storage?.getItem(PREFIX + scope) : null;
      if (raw) value = JSON.parse(raw);
    } catch (_) { /* Malformed/unavailable storage cannot break navigation. */ }
    return restore(kind, value);
  }
  const initial = load();
  const state = options.reactive ? options.reactive(initial) : initial;
  function persist(type, id = null) {
    const saved = { version: VERSION, items: state.items.map(item => metadata(kind, item)), activeId: state.activeId };
    memory.set(scope, saved);
    try { if (identity.ready) storage?.setItem(PREFIX + scope, JSON.stringify(saved)); } catch (_) { /* Quota/privacy fallback. */ }
    for (const listener of listeners) listener(state, { type, id });
  }
  function find(id) { return state.items.find(item => item.id === normalizeItemId(kind, id)); }
  function update(id, patch = {}) {
    const item = find(id);
    if (!item) return false;
    if (typeof patch.title === "string") item.title = patch.title;
    if (Object.prototype.hasOwnProperty.call(patch, "scroll")) item.scroll = point(patch.scroll);
    persist("update", item.id);
    return true;
  }
  return {
    state,
    get scope() { return scope; },
    setContext(context) {
      const next = namespace(context);
      if (next.key === scope) return state;
      identity = next; scope = next.key;
      const saved = load();
      state.items.splice(0, state.items.length, ...saved.items);
      state.activeId = saved.activeId;
      for (const listener of listeners) listener(state, { type: "context", id: state.activeId });
      return state;
    },
    open(value) {
      const id = normalizeItemId(kind, value?.id);
      if (!id) return null;
      let item = find(id);
      if (item) {
        if (typeof value.title === "string" && value.title) item.title = value.title;
        item.lastOpenedAt = clock();
      } else {
        item = metadata(kind, { id, title: value.title, lastOpenedAt: clock() });
        state.items.push(item);
      }
      state.activeId = id;
      persist("open", id);
      return find(id);
    },
    activate(id) {
      const item = find(id);
      if (!item) return false;
      state.activeId = item.id;
      persist("activate", item.id);
      return true;
    },
    close(id) {
      id = normalizeItemId(kind, id);
      const index = state.items.findIndex(item => item.id === id);
      if (index < 0) return false;
      const next = state.items[index + 1]?.id || state.items[index - 1]?.id || null;
      state.items.splice(index, 1);
      if (state.activeId === id) state.activeId = next;
      persist("close", id);
      return true;
    },
    update,
    setScroll(id, scroll) { return update(id, { scroll }); },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    clear() {
      state.items.splice(0); state.activeId = null;
      persist("clear");
    },
  };
}
