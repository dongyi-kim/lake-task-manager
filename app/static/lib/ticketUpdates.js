// Unread activity is local installation state. Batches inspect existing server caches, never
// issue one Jira request per card. Dialogs retain their own baseline while shared read state moves.
import { api } from "./api.js";

export const RECENT_UPDATE_MS = 7 * 24 * 60 * 60 * 1000;
export function updateTimestamp(value) {
  const parsed = typeof value === "number" ? value : Date.parse(value || "");
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}
export function isRecentUnread(at, lastViewedAt, now = Date.now()) {
  const time = updateTimestamp(at), baseline = updateTimestamp(lastViewedAt);
  return time > baseline && time >= now - RECENT_UPDATE_MS && time <= now;
}
export function commentUpdateAt(comment, now = Date.now()) {
  return Math.max(0, ...[comment?.updated, comment?.date || comment?.created]
    .map(updateTimestamp).filter(time => time <= now));
}
const MAJOR_KINDS = new Set(["created", "status", "assignee", "reporter", "resolution", "duedate",
  "priority", "summary", "description", "components", "component", "labels", "parent", "epic", "epic-link", "epic link", "sprint"]);
export function isMajorHistoryUpdate(event) {
  const kind = String(event?.kind || "").replace(/^child-/, "").toLowerCase();
  return MAJOR_KINDS.has(kind) || MAJOR_KINDS.has(String(event?.field || "").toLowerCase());
}

export function createTicketUpdatesStore(transport, publish = () => {}, now = Date.now) {
  const rows = new Map(), checked = new Map(), waiting = new Map();
  let queued = new Set(), scheduled = false, scope = null;
  const state = { revision: 0 };
  const keyOf = value => String(typeof value === "string" ? value : value?.key || "").toUpperCase();
  const metadata = key => rows.get(keyOf(key));
  const occurredAt = value => { const time = updateTimestamp(value); return time <= now() ? time : 0; };
  const ingest = payload => {
    const changed = [];
    if (payload?.scope && payload.scope !== scope) {
      changed.push(...rows.keys()); rows.clear(); checked.clear(); scope = payload.scope;
    }
    for (const [key, value] of Object.entries(payload?.tickets || {})) {
      const canonical = keyOf(key), previous = rows.get(canonical) || {};
      const next = {
        lastViewedAt: Math.max(updateTimestamp(previous.lastViewedAt), updateTimestamp(value?.lastViewedAt)) || null,
        latestRelevantAt: Math.max(occurredAt(previous.latestRelevantAt), occurredAt(value?.latestRelevantAt)) || null,
        latestCommentAt: Math.max(occurredAt(previous.latestCommentAt), occurredAt(value?.latestCommentAt)) || null,
      };
      if (JSON.stringify(next) !== JSON.stringify(previous)) changed.push(canonical);
      rows.delete(canonical); rows.set(canonical, next);
    }
    while (rows.size > 3000) { const key = rows.keys().next().value; rows.delete(key); checked.delete(key); }
    if (changed.length) { state.revision++; publish({ keys: changed, revision: state.revision }); }
    return payload;
  };
  const flush = async () => {
    scheduled = false;
    const keys = [...queued]; queued = new Set();
    for (let offset = 0; offset < keys.length; offset += 500) {
      const batch = keys.slice(offset, offset + 500);
      let ok = false;
      try {
        const result = await transport.ticketActivity(batch);
        if (result?.tickets && typeof result.tickets === "object") {
          ingest(result); ok = true;
          for (const key of batch) checked.set(key, now());
        }
      } catch (_) { /* Optional highlights must not interrupt ordinary ticket rendering. */ }
      for (const key of batch) { waiting.get(key)?.resolve(ok); waiting.delete(key); }
    }
  };
  const observe = (cards, options = {}) => {
    const keys = [...new Set((cards || []).map(keyOf).filter(Boolean))];
    const promises = keys.map(key => {
      if (waiting.has(key)) return waiting.get(key).promise;
      if (!options.force && checked.has(key) && now() - checked.get(key) < 30000) return Promise.resolve(true);
      let resolve;
      const promise = new Promise(done => { resolve = done; });
      waiting.set(key, { promise, resolve }); queued.add(key);
      return promise;
    });
    if (queued.size && !scheduled) { scheduled = true; queueMicrotask(flush); }
    return Promise.all(promises).then(results => results.every(Boolean));
  };
  const acknowledge = async (key, through) => {
    const canonical = keyOf(key), time = Math.floor(updateTimestamp(through));
    if (!canonical || !time || time > now()) return false;
    if (updateTimestamp(metadata(canonical)?.lastViewedAt) >= time) return true;
    try {
      const result = await transport.ticketViewed(canonical, time);
      if (result?.key !== canonical || !updateTimestamp(result.lastViewedAt)) return false;
      ingest({ scope: result.scope, tickets: { [canonical]: { lastViewedAt: result.lastViewedAt } } });
      return true;
    } catch (_) { return false; }
  };
  return { state, metadata, ingest, observe, acknowledge,
    isUnread: card => !!metadata(card) && isRecentUnread(Math.max(occurredAt(metadata(card)?.latestRelevantAt),
      occurredAt(card?.updated), occurredAt(card?.latestRelevantAt)), metadata(card)?.lastViewedAt, now()) };
}

const store = createTicketUpdatesStore(api, detail => {
  window.dispatchEvent(new CustomEvent("ticket-updates-changed", { detail }));
});
export const ticketUpdatesState = store.state;
export const observeTicketUpdates = store.observe;
export const ingestTicketUpdates = store.ingest;
export const ticketUpdateMetadata = store.metadata;
export const isTicketUnread = store.isUnread;
export const acknowledgeTicketUpdates = store.acknowledge;
