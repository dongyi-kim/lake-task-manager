// hljs.js — 코드블럭 구문강조 공유 유틸.
// · 테마 CSS(hljs-* 색): 에디터(lowlight 가 만든 span)와 렌더된 댓글(highlightElement) 둘 다 이 CSS 로 색이 입혀진다.
// · 렌더 하이라이팅: 댓글/설명의 <pre><code class="language-X"> 를 highlight.js 로 강조(제출본은 서버 렌더라 span 이 없음).
// 지연 로드 — **로컬 미러**(사내망 CDN 차단·CORS 캐시오염 회피). 실패해도 코드블럭은 무-강조로 보인다.
const THEME = {
  light: "/vendor/hljs/github.css",
  dark: "/vendor/hljs/github-dark.css",
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
  // highlight.js 도 로컬 미러(vendor/esm)에서. manifest 가 진입 파일을 가리킨다.
  _hp = fetch("/vendor/esm/manifest.json").then((r) => r.json())
    .then((man) => import(/* @vite-ignore */ man.hljs))
    .then((m) => m.default || m)
    .catch((e) => { _hp = null; throw e; });
  return _hp;
}

// 줄번호 거터 — 하이라이팅 스팬을 쪼개지 않고 <pre> 안에 별도 열로 붙인다(멀티라인 span 안전).
// 폰트/line-height 가 code 와 같아 줄이 정렬된다.
function ensureLineNumbers(pre) {
  const code = pre.querySelector("code");
  if (!code) return;
  const n = code.textContent.replace(/\n$/, "").split("\n").length;
  let g = pre.querySelector(".ln-gutter");
  if (!g) {
    g = document.createElement("span");
    g.className = "ln-gutter";
    g.setAttribute("aria-hidden", "true");
    pre.insertBefore(g, code);
  }
  const want = Array.from({ length: n }, (_, i) => i + 1).join("\n");
  if (g.textContent !== want) g.textContent = want;
  pre.classList.add("has-ln");
}

// root 안의 코드블럭을 강조 + 줄번호(중복 처리 방지 data-hl). 실패는 조용히 무시.
export async function highlightIn(root) {
  if (!root) return;
  ensureHljsTheme(document.documentElement.getAttribute("data-theme") === "dark");
  const codes = root.querySelectorAll("pre code");
  if (!codes.length) return;
  try {
    const hljs = await loadHljs();
    codes.forEach((el) => {
      if (!el.dataset.hl) {
        try { hljs.highlightElement(el); } catch (_) { /* noop */ }
        el.dataset.hl = "1";
      }
      try { ensureLineNumbers(el.parentElement); } catch (_) { /* noop */ }
    });
  } catch (_) {
    // CDN 차단 등 — 강조는 못 해도 줄번호는 붙인다.
    codes.forEach((el) => { try { ensureLineNumbers(el.parentElement); } catch (_) { /* noop */ } });
  }
}
