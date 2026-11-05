import { migrateDraft } from "./draft.js";

// Drafts belong to this local installation, not the current Jira session. Health supplies only
// local server configuration; resolving a draft must never start/wait for Jira or authentication.
const CONTEXT_KEY = "ltm-draft-context.v3";
let context;
try { context = JSON.parse(localStorage.getItem(CONTEXT_KEY) || "null"); } catch (_) { /* offline/private storage */ }

export function rememberDraftContext(health) {
  if (!health || typeof health.env !== "string" || typeof health.jiraBase !== "string") return;
  context = [health.env, health.jiraBase.replace(/\/+$/, ""), String(health.projectKey || "")];
  try { localStorage.setItem(CONTEXT_KEY, JSON.stringify(context)); } catch (_) { /* memory still works */ }
}

export async function resolveDraftKey(scope) {
  const known = Array.isArray(context) && context.length === 3 && context.every(v => typeof v === "string");
  const parts = known ? context.map(encodeURIComponent) : null;
  // IndexedDB already isolates browser origins. Even a first offline launch can edit/save;
  // once local metadata is available that fallback record migrates into its server/project scope.
  const fallback = "v3:local:" + scope;
  const key = parts ? "v3:" + parts.join(":") + ":" + scope : fallback;
  const legacyPrefix = parts && "v2:" + parts.slice(0, 2).join(":") + ":";
  await migrateDraft(key, old => {
    if (old === scope || (parts && old === fallback)) return true;
    if (!legacyPrefix || !old.startsWith(legacyPrefix)) return false;
    const userAndScope = old.slice(legacyPrefix.length);
    return userAndScope.slice(userAndScope.indexOf(":") + 1) === scope;
  });
  return key;
}
