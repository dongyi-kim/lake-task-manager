// fmt.js — 순수 포맷/이스케이프 헬퍼. DOM·상태 없음. 모든 화면 공유.
// updated: 2026-07-09
export function esc(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
// "YYYY-MM-DD..." → "M/D"
export function mdISO(s) { if (!s) return ""; var p = s.substring(0, 10).split("-"); return (+p[1]) + "/" + (+p[2]); }
// ISO → "yy.mm.dd"
export function ymd(iso) {
  if (!iso) return "";
  const p = iso.substring(0, 10).split("-");
  return p[0].slice(2) + "." + p[1] + "." + p[2];
}
// ISO(datetime) → "mm.dd hh:mm" (연도 없이). 시간 없으면 "mm.dd".
export function mdhm(iso) {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (m) return m[2] + "." + m[3] + " " + m[4] + ":" + m[5];
  const p = iso.substring(0, 10).split("-");
  return p.length === 3 ? p[1] + "." + p[2] : iso;
}
// ISO(datetime) → "yy.mm.dd hh:mm" (24h). 날짜만이면 ymd 로 폴백.
export function ymdhm(iso) {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  return m ? (m[1].slice(2) + "." + m[2] + "." + m[3] + " " + m[4] + ":" + m[5]) : ymd(iso);
}
// ISO → "yyyy.mm.dd HH:mm:ss" (일정 필드 공통 포맷).
// Jira 가 날짜만 가진 필드(duedate 등)는 없는 시:분:초를 지어내지 않고 "yyyy.mm.dd" 로 둔다.
export function ts(iso) {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(iso);
  if (!m) return iso;
  const d = m[1] + "." + m[2] + "." + m[3];
  return m[4] ? d + " " + m[4] + ":" + m[5] + ":" + (m[6] || "00") : d;
}

// 마감까지 남은 일정 → "D-Day" | "D-N"(남음) | "D+N"(초과)
export function dday(iso) {
  if (!iso) return "";
  const due = new Date(iso.substring(0, 10) + "T00:00:00");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((due - today) / 86400000);
  if (isNaN(diff)) return "";
  return diff === 0 ? "D-Day" : (diff > 0 ? "D-" + diff : "D+" + (-diff));
}
// Jira 티켓 링크 — 외부 브라우저 대신 **인앱 티켓 다이얼로그**를 여는 트리거.
// (href 없음 → run.py 외부링크 훅과 충돌 안 함. app-root 의 위임 클릭 핸들러가 data-key 로 다이얼로그 오픈.)
// base 는 하위호환용으로 받되 무시(다이얼로그가 자체적으로 Jira URL 확보).
export function tkt(key, base) {
  if (!key) return "";
  return "<a class='lnk tkt' role='button' tabindex='0' data-key='" + esc(key) + "'>" + esc(key) + "</a>";
}
