// tiptap.js — TipTap 에디터를 **첫 사용 시** 로컬 미러에서 지연 로드.
//
// 예전엔 esm.sh(CDN)에서 직접 받았는데, 사내 프록시가 esm.sh 응답의 CORS(ACAO) 헤더를 URL 단위로
// 캐시하며 최초 요청 origin(포트)을 박아버려, 포트가 바뀌면(8000→4457) 캐시된 ACAO 와 안 맞아
// 에디터가 CORS 로 막혔다(사내망은 외부 CDN 자체도 불안정). → esm.sh 의존성 트리를 통째로
// 로컬(app/static/vendor/esm)에 vendoring 해 **우리 오리진에서** 서빙한다(무CDN·무CORS).
//   · 각 파일의 esm.sh 헤더가 패키지·버전을 기록하고 manifest.json 이 진입 이름→로컬 파일을 잇는다.
//   · 같은 concrete 모듈은 같은 해시 파일이라 @tiptap/pm 공유(단일 인스턴스)가 유지된다.
//   · 모든 TipTap 패키지는 v3.30.3으로 고정해 단일 core/ProseMirror 인스턴스를 공유한다.

const ORDER = [
  "core", "starter-kit", "extension-mention", "extension-table", "extension-image",
  "extension-placeholder", "pm-state", "extension-code-block-lowlight", "lowlight", "suggestion",
  "extension-text-align", "extension-text-style", "extension-font-family", "pm-transform",
  "extension-task-list", "extension-task-item",
];

let _p = null;

export function loadTiptap() {
  if (_p) return _p;
  _p = fetch("/vendor/esm/manifest.json")
    .then((r) => { if (!r.ok) throw new Error("에디터 미러 매니페스트 로드 실패"); return r.json(); })
    .then((man) => Promise.all(ORDER.map(async (key) => [key, await import(/* @vite-ignore */ man[key])])))
    .then((entries) => Object.fromEntries(entries))
    .then((m) => {
      const core = m.core;
      const table = m["extension-table"];
      const lowlightModule = m.lowlight;
      const lowlight = lowlightModule.createLowlight(lowlightModule.common);   // 공통 언어(약 37종) 등록
      return {
        version: "3.30.3",
        Editor: core.Editor,
        Extension: core.Extension,
        Node: core.Node,
        // 입력 규칙 — '=== 제목 ===' 같은 표기를 치는 즉시 노드로 바꾼다
        textblockTypeInputRule: core.textblockTypeInputRule,
        InputRule: core.InputRule,
        StarterKit: m["starter-kit"].default,
        Mention: m["extension-mention"].default,
        Table: table.Table,
        TableRow: table.TableRow,
        TableCell: table.TableCell,
        TableHeader: table.TableHeader,
        Image: m["extension-image"].default,
        Placeholder: m["extension-placeholder"].default,
        Plugin: m["pm-state"].Plugin,
        PluginKey: m["pm-state"].PluginKey,
        CodeBlockLowlight: m["extension-code-block-lowlight"].default,
        Suggestion: m.suggestion.default,
        TextAlign: m["extension-text-align"].default,
        TextStyle: m["extension-text-style"].TextStyle,
        FontFamily: m["extension-font-family"].default,
        findWrapping: m["pm-transform"].findWrapping,
        TaskList: m["extension-task-list"].default,
        TaskItem: m["extension-task-item"].default,
        lowlight,
        languages: lowlight.listLanguages(),
      };
    })
    .catch((e) => { _p = null; throw e; });
  return _p;
}
