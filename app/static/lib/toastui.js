// toastui.js — Toast UI Editor 를 **에디터 첫 사용 시** CDN 에서 지연 로드.
// 앱 시작을 블로킹하지 않고, CDN 이 차단된 환경이면 reject → 호출측이 "에디터 로드 실패"만
// 표시하고 나머지 앱은 정상 동작한다. (사내망이 CDN 을 막으면 여기만 실패)
// 로드 성공 시 window.toastui.Editor 를 resolve. 결과 프로미스는 캐시(중복 로드 방지).
const VER = "3.2.2";
const BASE = "https://uicdn.toast.com/editor/" + VER;
const CSS = [BASE + "/toastui-editor.min.css", BASE + "/toastui-editor-dark.min.css"];
const JS = BASE + "/toastui-editor-all.min.js";

let _p = null;

export function loadToastUI() {
  if (window.toastui && window.toastui.Editor) return Promise.resolve(window.toastui.Editor);
  if (_p) return _p;
  _p = new Promise((resolve, reject) => {
    for (const href of CSS) {
      if (!document.querySelector('link[data-tui="1"][href="' + href + '"]')) {
        const l = document.createElement("link");
        l.rel = "stylesheet"; l.href = href; l.setAttribute("data-tui", "1");
        document.head.appendChild(l);
      }
    }
    const s = document.createElement("script");
    s.src = JS; s.async = true;
    s.onload = () => (window.toastui && window.toastui.Editor)
      ? resolve(window.toastui.Editor)
      : reject(new Error("toastui: Editor 전역 없음"));
    s.onerror = () => { _p = null; reject(new Error("toastui CDN 로드 실패")); };
    document.head.appendChild(s);
  });
  return _p;
}
