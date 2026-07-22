// hljs.js — 코드블럭 구문강조 공유 유틸.
// · 테마 CSS(hljs-* 색): 에디터(lowlight 가 만든 span)와 렌더된 댓글(highlightElement) 둘 다 이 CSS 로 색이 입혀진다.
// · 렌더 하이라이팅: 댓글/설명의 <pre><code class="language-X"> 를 highlight.js 로 강조(제출본은 서버 렌더라 span 이 없음).
// 지연 로드(CDN). 실패해도 코드블럭은 무-강조로 그대로 보인다.
const HLJS = "https://esm.sh/highlight.js@11";
const THEME = {
  light: "https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github.min.css",
  dark: "https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github-dark.min.css",
};

let _hp = null;

export function ensureHljsTheme(dark) {
  let l = document.getElementById("hljs-theme");
  if (!l) {
    l = document.createElement("link");
    l.id = "hljs-theme"; l.rel = "stylesheet";
    document.head.appendChild(l);
  }
  const href = dark ? THEME.dark : THEME.light;
  if (l.getAttribute("href") !== href) l.setAttribute("href", href);
}

export function loadHljs() {
  if (_hp) return _hp;
  _hp = import(/* @vite-ignore */ HLJS).then((m) => m.default || m).catch((e) => { _hp = null; throw e; });
  return _hp;
}

// root 안의 코드블럭을 강조(중복 강조 방지 data-hl). 실패는 조용히 무시.
export async function highlightIn(root) {
  if (!root) return;
  ensureHljsTheme(document.documentElement.getAttribute("data-theme") === "dark");
  const codes = root.querySelectorAll("pre code");
  if (!codes.length) return;
  try {
    const hljs = await loadHljs();
    codes.forEach((el) => {
      if (el.dataset.hl) return;
      try { hljs.highlightElement(el); } catch (_) { /* noop */ }
      el.dataset.hl = "1";
    });
  } catch (_) { /* CDN 차단 등 — 무강조로 둔다 */ }
}
