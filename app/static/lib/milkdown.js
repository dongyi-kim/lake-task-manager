// milkdown.js — Milkdown Crepe(WYSIWYG markdown 에디터)를 **첫 사용 시** CDN 지연 로드.
// 신버전 위키/노션처럼 '# '·'- '·'1. '·백틱3개 등 마크다운 입력이 실시간 변환(input rules),
// 슬래시(/) 블록 메뉴 · 플로팅 툴바 · 표 행/열 편집 · 코드블록 · 이미지 내장.
//
// ※ esm.sh 는 **?bundle** 로 받는다 — Crepe 는 prosemirror/codemirror 의존이 많아 개별 해소 시
//   'Adding different instances of a keyed plugin' / codemirror export 오류가 난다. ?bundle 은
//   모든 의존을 한 모듈로 묶어 그 문제를 없앤다(검증됨). 앱 시작은 블로킹하지 않고, CDN 차단
//   환경이면 reject → 호출측이 '에디터 로드 실패'만 표시하고 나머지 앱은 정상.
const VER = "7.21.2";
const ESM = "https://esm.sh/@milkdown/crepe@" + VER + "?bundle";
const CSS = "https://cdn.jsdelivr.net/npm/@milkdown/crepe@" + VER + "/lib/theme";
const COMMON = CSS + "/common/style.css";                 // 기능 CSS(하위 파일을 @import)
const THEME = { light: CSS + "/frame/style.css", dark: CSS + "/frame-dark/style.css" };

let _mod = null;

export async function loadCrepe(dark) {
  _ensureLink("crepe-common-css", COMMON);
  _ensureLink("crepe-theme-css", dark ? THEME.dark : THEME.light);   // 테마 전환 시 href 교체
  if (!_mod) _mod = import(/* @vite-ignore */ ESM);
  return await _mod;                                       // { Crepe, CrepeFeature, ... }
}

function _ensureLink(id, href) {
  let l = document.getElementById(id);
  if (!l) {
    l = document.createElement("link");
    l.id = id; l.rel = "stylesheet";
    document.head.appendChild(l);
  }
  if (l.getAttribute("href") !== href) l.setAttribute("href", href);
}
