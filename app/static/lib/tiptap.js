// tiptap.js — TipTap 에디터를 첫 사용 시 로컬 단일 번들에서 지연 로드.
// npm lock + esbuild로 생성한 bundle 하나가 TipTap/core/ProseMirror 인스턴스를 공유한다. 이전
// esm.sh 재귀 미러처럼 수백 개의 해시 파일과 요청을 앱/PR에 노출하지 않는다(무CDN·무CORS).

let _p = null;

export function loadTiptap() {
  if (_p) return _p;
  _p = import("/vendor/tiptap.bundle.mjs")
    .catch((e) => { _p = null; throw e; });
  return _p;
}
