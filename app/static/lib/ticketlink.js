// ticketlink.js — 티켓의 Jira 절대 URL 계산 + 클립보드 복사(공통 유틸).
//
// jiraBase 는 /api/health 가 알려준다(환경마다 다름: mock/local/사내). 한 번 받아 캐시한다.
// 복사는 navigator.clipboard 우선, 실패(비보안 컨텍스트 등) 시 textarea+execCommand 폴백.
import { api } from "./api.js";

let _base = null;                 // 확정된 jiraBase("" 포함)
let _baseReq = null;              // in-flight health 요청(중복 방지)

/** jiraBase 를 반환(끝 슬래시 제거). 실패하면 "" — 그래도 /browse 상대경로는 만든다. */
export function jiraBase() {
  if (_base !== null) return Promise.resolve(_base);
  if (!_baseReq) {
    _baseReq = api.health()
      .then((h) => { _base = String((h && h.jiraBase) || "").replace(/\/+$/, ""); return _base; })
      .catch(() => { _base = ""; return ""; });
  }
  return _baseReq;
}

/** 티켓 키 → 열람 가능한 절대 URL(가능하면 사내 Jira, 아니면 상대 /browse). */
export async function ticketUrl(key) {
  const b = await jiraBase();
  return (b || "") + "/browse/" + encodeURIComponent(key);
}

/** 텍스트를 클립보드로. 성공 여부(boolean) 반환. */
export async function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* 폴백으로 */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch (e) { return false; }
}

/** 티켓 링크를 클립보드로. { ok, url } 반환. */
export async function copyTicketLink(key) {
  const url = await ticketUrl(key);
  const ok = await copyText(url);
  return { ok, url };
}
