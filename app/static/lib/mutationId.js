// Stable identifier for a logical create action.  Keep it across timeout/auth retries and replace
// it only after a confirmed success or when the user starts a distinct creation dialog.
export function newMutationId(kind = "write") {
  const cryptoApi = globalThis.crypto;
  const random = cryptoApi && typeof cryptoApi.randomUUID === "function"
    ? cryptoApi.randomUUID()
    : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2)
      + "-" + Math.random().toString(36).slice(2);
  return "ltm-" + String(kind || "write").replace(/[^A-Za-z0-9._:-]/g, "-") + ":" + random;
}

const PENDING_PREFIX = "pending-mutation.v1:";
const PENDING_TTL_MS = 8 * 24 * 60 * 60 * 1000;

/** Persist a create request before fetch so a renderer/app restart can resume its receipt. */
export function savePendingMutation(scope, id, payload, context = {}) {
  if (!scope || !id || !payload) return;
  try {
    localStorage.setItem(PENDING_PREFIX + scope, JSON.stringify({
      id, payload, context, savedAt: Date.now(),
    }));
  } catch (_) { /* component memory still protects an ordinary retry */ }
}

export function loadPendingMutation(scope) {
  if (!scope) return null;
  try {
    const key = PENDING_PREFIX + scope;
    const value = JSON.parse(localStorage.getItem(key) || "null");
    if (!value || !value.id || !value.payload
        || Date.now() - Number(value.savedAt || 0) > PENDING_TTL_MS) {
      localStorage.removeItem(key); return null;
    }
    return value;
  } catch (_) { return null; }
}

export function clearPendingMutation(scope) {
  try { localStorage.removeItem(PENDING_PREFIX + scope); } catch (_) { /* noop */ }
}
