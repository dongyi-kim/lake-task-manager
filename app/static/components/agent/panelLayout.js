// Persistent width and collapsed-state policy shared by AgentView's side panels.
// ── 좌/우 패널 폭 조절·접기 ────────────────────────────────────────────
// 규칙은 **티켓 다이얼로그(TicketDialog 의 spine/timeline)와 같다** — 이 앱에서 이미
// 쓰는 몸짓이라 여기서 다르게 굴면 배우는 것이 하나 더 늘어난다:
//   · 경계선을 끌어 폭 조절(그립), 각 패널이 자기 폭을 localStorage 에 기억한다
//   · 경계선 가운데 버튼으로 접기, 접히면 얇은 레일(stub)이 남아 다시 편다
//   · 폭은 클램프한다 — 너무 좁으면 목록이 잘리고 너무 넓으면 대화가 눌린다
// 0 은 "아직 안 건드림"이다 → CSS 기본값(반응형 min(760px,48vw) 등)이 그대로 산다.
export const NAV_W_KEY = "agent.navW", NAV_HIDE_KEY = "agent.navHidden";
export const SIDE_W_KEY = "agent.sideW", SIDE_HIDE_KEY = "agent.sideHidden";
export const NAV_MIN = 170, NAV_MAX = 420, SIDE_MIN = 340, SIDE_MAX = 1200;

export function loadW(key, min, max) {
  try { const v = parseInt(localStorage.getItem(key), 10); if (v >= min && v <= max) return v; }
  catch (e) { /* noop */ }
  return 0;                      // 0 = 지정 없음(CSS 기본값 사용)
}
export function loadHidden(key) {
  try { return localStorage.getItem(key) === "1"; } catch (e) { return false; }
}
export function saveLS(key, val) {
  try { localStorage.setItem(key, String(val)); } catch (e) { /* noop */ }
}
