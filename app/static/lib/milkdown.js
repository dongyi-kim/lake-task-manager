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
// CSS 는 **로컬 벤더**(자체완결). Crepe 배포 CSS 는 @import '@milkdown/kit/...' 등 node_modules
// 경로를 담아 raw <link> 로 못 쓴다(ORB 차단 → 렌더 깨짐). tools/build_crepe_css.py 로 @import 를
// 전부 해소해 인라인한 파일을 쓴다. 테마별 완결 파일(common+frame 포함).
const CSS = { light: "vendor/crepe-light.css", dark: "vendor/crepe-dark.css" };

let _mod = null;

export async function loadCrepe(dark) {
  _ensureLink("crepe-css", dark ? CSS.dark : CSS.light);   // 테마 전환 시 href 교체
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
