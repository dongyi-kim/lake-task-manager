// backdrop.js — "바깥을 눌러서 닫기" 를 **누른 곳과 뗀 곳이 같을 때만** 인정한다.
//
// click 이벤트의 target 은 누른 곳이 아니라 **뗀 곳**이다. 그래서 창 안에서 글자를 드래그하다가
// 손이 창 밖에서 떨어지면, 브라우저는 그것도 '바깥 클릭' 으로 준다 — 고르던 텍스트와 함께 창이
// 닫혀 버린다(검색창·티켓 창에서 겪은 그것이다).
//
// 그래서 mousedown 의 target 을 기억해 두고, click 때 **둘 다 배경일 때만** 닫는다.
// 각 창이 알아서 처리하게 두면 어떤 창은 고쳐지고 어떤 창은 안 고쳐진다 — 한 곳에 둔다.
import { isBusy } from "./uibusy.js";

let downTarget = null;

// capture 로 받는다 — 창 안쪽에서 stopPropagation 하는 핸들러가 있어도 기록은 남아야 한다.
window.addEventListener("mousedown", (e) => { downTarget = e.target; }, true);
// 드래그가 창 밖(문서 밖)에서 끝나 click 이 아예 안 오는 경우를 대비해 다음 누름 전까지만 유효.
window.addEventListener("dragstart", () => { downTarget = null; }, true);

/** 이 클릭이 **배경을 눌렀다 뗀 것**인가. 오버레이의 @click.self 에서 쓴다. */
export function fromBackdrop(e) {
  // 끝내면 안 되는 일(AI 생성 등)이 도는 중에는 배경 클릭으로 닫지 않는다 — 받아 놓은
  // 글이 통째로 사라진다. 판정을 여기 두는 이유는 위와 같다: 각 창이 알아서 처리하면
  // 어떤 창은 막히고 어떤 창은 안 막힌다.
  if (isBusy()) return false;
  return !!e && e.target === e.currentTarget && downTarget === e.currentTarget;
}
