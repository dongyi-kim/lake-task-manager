// tiptap.js — TipTap 에디터를 **첫 사용 시** 로컬 미러에서 지연 로드.
//
// 예전엔 esm.sh(CDN)에서 직접 받았는데, 사내 프록시가 esm.sh 응답의 CORS(ACAO) 헤더를 URL 단위로
// 캐시하며 최초 요청 origin(포트)을 박아버려, 포트가 바뀌면(8000→4457) 캐시된 ACAO 와 안 맞아
// 에디터가 CORS 로 막혔다(사내망은 외부 CDN 자체도 불안정). → esm.sh 의존성 트리를 통째로
// 로컬(app/static/vendor/esm)에 vendoring 해 **우리 오리진에서** 서빙한다(무CDN·무CORS).
//   · 생성: scratchpad/vendor_esm.py (개발 PC에서 1회). manifest.json 이 진입 이름→로컬 파일.
//   · 같은 concrete 모듈은 같은 해시 파일이라 @tiptap/pm 공유(단일 인스턴스)가 유지된다.
//   · 진입 순서를 그대로 두어 CommentEditor 의 m[N] 인덱싱이 유효하다.

// m[N] 순서 = 아래 배열 순서. (manifest 키와 일치)
const ORDER = [
  "core", "starter-kit", "extension-mention", "extension-table", "extension-table-row",
  "extension-table-cell", "extension-table-header", "extension-image", "extension-link",
  "extension-placeholder", "pm-state", "extension-code-block-lowlight", "lowlight",
  "suggestion", "extension-text-align", "extension-text-style", "extension-font-family",
  "pm-transform", "extension-task-list", "extension-task-item",
];

let _p = null;

export function loadTiptap() {
  if (_p) return _p;
  _p = fetch("/vendor/esm/manifest.json")
    .then((r) => { if (!r.ok) throw new Error("에디터 미러 매니페스트 로드 실패"); return r.json(); })
    .then((man) => Promise.all(ORDER.map((k) => import(/* @vite-ignore */ man[k]))))
    .then((m) => {
      const lowlight = m[12].createLowlight(m[12].common);   // 공통 언어(약 37종) 등록
      return {
        Editor: m[0].Editor,
        Extension: m[0].Extension,
        Node: m[0].Node,
        // 입력 규칙 — '=== 제목 ===' 같은 표기를 치는 즉시 노드로 바꾼다
        textblockTypeInputRule: m[0].textblockTypeInputRule,
        InputRule: m[0].InputRule,
        StarterKit: m[1].default,
        Mention: m[2].default,
        Table: m[3].default,
        TableRow: m[4].default,
        TableCell: m[5].default,
        TableHeader: m[6].default,
        Image: m[7].default,
        Link: m[8].default,
        Placeholder: m[9].default,
        Plugin: m[10].Plugin,
        PluginKey: m[10].PluginKey,
        CodeBlockLowlight: m[11].default,
        Suggestion: m[13].default,
        TextAlign: m[14].default,
        TextStyle: m[15].default,
        FontFamily: m[16].default,
        findWrapping: m[17].findWrapping,
        TaskList: m[18].default,
        TaskItem: m[19].default,
        lowlight,
        languages: lowlight.listLanguages(),
      };
    })
    .catch((e) => { _p = null; throw e; });
  return _p;
}
