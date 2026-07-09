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
// ISO(datetime) → "yy.mm.dd hh:mm" (24h). 날짜만이면 ymd 로 폴백.
export function ymdhm(iso) {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  return m ? (m[1].slice(2) + "." + m[2] + "." + m[3] + " " + m[4] + ":" + m[5]) : ymd(iso);
}
// Jira 티켓 링크 (jiraBase 있으면 <a>, 없으면 키 텍스트)
export function tkt(key, base) {
  if (!key) return "";
  return base
    ? "<a class='lnk' target='_blank' rel='noopener' href='" + base + "/browse/" + key + "'>" + key + "</a>"
    : key;
}
