import { migrateDraft } from "./draft.js";

// Drafts belong to this local installation, not the current Jira session. Health supplies only
// local server configuration; resolving a draft must never start/wait for Jira or authentication.
const CONTEXT_KEY = "ltm-draft-context.v3";
const EXECUTION_KEY = "ltm-draft-execution-context.v1";
let context, executionContext;
try { context = JSON.parse(localStorage.getItem(CONTEXT_KEY) || "null");
  executionContext = JSON.parse(localStorage.getItem(EXECUTION_KEY) || "null"); } catch (_) { /* offline/private storage */ }

export function rememberDraftContext(health) {
  if (!health || typeof health.env !== "string" || typeof health.jiraBase !== "string") return;
  context = [health.env, health.jiraBase.replace(/\/+$/, ""), String(health.projectKey || "")];
  executionContext = health.executionScope || null;
  try { localStorage.setItem(CONTEXT_KEY, JSON.stringify(context));
    localStorage.setItem(EXECUTION_KEY, JSON.stringify(executionContext)); } catch (_) { /* memory still works */ }
}

export function localDraftContext() {
  if (!Array.isArray(context) || context.length !== 3) return null;
  if (executionContext?.jiraBase === context[1] && executionContext?.projectKey === context[2]
      && typeof executionContext.environment === "string") return { ...executionContext };
  // Older mock health responses omit the dated world ID. Resolve it only at submission.
  if (context[0] !== "prod") return null;
  return { environment: context[0], jiraBase: context[1], projectKey: context[2] };
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
    if (parts && /^(?:form:)?bulk-create:/.test(scope)) {
      const execution = localDraftContext();
      const variants = [parts, execution && [execution.environment, execution.jiraBase, execution.projectKey].map(encodeURIComponent)];
      for (const variant of variants.filter(Boolean)) {
        const prefix = "v3:bulk:" + variant.join(":") + ":";
        if (old.startsWith(prefix)) {
          const tail = old.slice(prefix.length);
          if (tail === scope || tail.slice(tail.indexOf(":") + 1) === scope) return true;
        }
      }
    }
    if (!legacyPrefix || !old.startsWith(legacyPrefix)) return false;
    const userAndScope = old.slice(legacyPrefix.length);
    return userAndScope.slice(userAndScope.indexOf(":") + 1) === scope;
  });
  return key;
}
