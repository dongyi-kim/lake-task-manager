// toast.js — 우하단 알림 스택에 알림 하나를 띄운다. 어디서든 pushToast(...) 로 호출.
//
// 실제 렌더는 ToastStack 컴포넌트가 'lake-toast' 이벤트를 받아 한다(전역 이벤트라 어느 위치의
// 코드든 컴포넌트 참조 없이 알림을 낼 수 있다). 인증·다운로드 알림이 여기로 모인다.
//
// toast: { kind:'info'|'success'|'error', icon?:'⬇', title, message?, timeout?(ms, 0=수동만), key? }
//   key 를 주면 같은 종류 알림이 중복으로 쌓이지 않는다(예: 반복되는 인증 완료).
export function pushToast(toast) {
  try {
    window.dispatchEvent(new CustomEvent("lake-toast", { detail: toast || {} }));
  } catch (e) { /* noop */ }
}
