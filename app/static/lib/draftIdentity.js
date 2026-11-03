import { api } from "./api.js";

let identity;
window.addEventListener("auth-ok", () => { identity = null; });

// Legacy unscoped drafts remain until TTL cleanup; never assign them to an assumed user.
export function resolveDraftKey(scope) {
  if (!identity) identity = Promise.all([api.health(), api.me()]).then(([health, me]) => {
    if (!me || !me.id) throw new Error("초안을 저장할 사용자를 확인하지 못했습니다. 로그인 후 다시 시도해 주세요.");
    return [health.env, health.jiraBase, me.id].map(value => encodeURIComponent(String(value || ""))).join(":");
  }).catch(error => { identity = null; throw error; });
  return identity.then(owner => "v2:" + owner + ":" + scope);
}
