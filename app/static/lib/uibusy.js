// uibusy.js — "지금 끝내면 안 되는 일" 이 도는 동안 창이 닫히지 않게 막는다.
//
// 왜 한 곳에 두나 — AI 자동완성은 몇 초 걸리고, 그 사이 사용자가 ESC 를 누르거나 배경을
// 클릭하면 다이얼로그가 닫히면서 **받아 놓은 글이 통째로 사라진다**. 각 창이 알아서
// 처리하게 두면 어떤 창은 막히고 어떤 창은 안 막힌다(backdrop.js 와 같은 이유로 공용).
//
// 규칙 하나: **잠근 쪽이 반드시 푼다.** begin() 이 돌려주는 end() 를 finally 에서 부르면
// 실패·예외·취소 어느 쪽으로 끝나도 잠금이 남지 않는다. 잠금이 남으면 창을 못 닫는
// 상태가 되는데, 그건 데이터를 잃는 것보다 더 나쁘다(사용자가 앱을 새로고침해야 한다).
let depth = 0;
let label = "";
const subs = new Set();

function notify() {
  subs.forEach((fn) => { try { fn(depth > 0, label); } catch (_) { /* noop */ } });
}

/**
 * 잠금 시작. **반드시 반환된 end() 를 finally 에서 부를 것.**
 * @param {string} why 사용자에게 보일 이유("AI가 작성 중")
 * @returns {() => void} 한 번만 먹는 해제 함수(두 번 불러도 안전하다)
 */
export function beginBusy(why = "") {
  depth += 1;
  label = why || label;
  notify();
  let done = false;
  return () => {
    if (done) return;
    done = true;
    depth = Math.max(0, depth - 1);
    if (!depth) label = "";
    notify();
  };
}

/** 지금 닫으면 안 되는 일이 도는 중인가. 창의 닫기 경로(ESC·배경·✕)에서 확인한다. */
export function isBusy() {
  return depth > 0;
}

/** 왜 막혔는지 — 안내 문구에 쓴다. */
export function busyLabel() {
  return label;
}

/** 상태 변화 구독(해제 함수 반환). 창이 자기 UI 를 바꿔야 할 때. */
export function onBusy(fn) {
  subs.add(fn);
  return () => subs.delete(fn);
}
