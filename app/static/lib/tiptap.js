// tiptap.js — TipTap 에디터를 **첫 사용 시** CDN(esm.sh) 지연 로드.
// 모던 Confluence/Jira 에디터처럼: 마크다운 input rule(# ` ``` `- 등) + @사람 멘션 + 표 + 이미지.
// ※ plain esm.sh 임포트로 @tiptap/pm 이 공유돼 'different instances of keyed plugin' 이 안 난다
//   (?deps 로 고정하면 오히려 esm.sh 내부 해소가 깨짐 — 검증 결과 plain 이 정답). 버전 고정 필수.
const V = "2.11.7";
const E = (n) => "https://esm.sh/@tiptap/" + n + "@" + V;

let _p = null;

export function loadTiptap() {
  if (_p) return _p;
  _p = Promise.all([
    import(/* @vite-ignore */ E("core")),
    import(/* @vite-ignore */ E("starter-kit")),
    import(/* @vite-ignore */ E("extension-mention")),
    import(/* @vite-ignore */ E("extension-table")),
    import(/* @vite-ignore */ E("extension-table-row")),
    import(/* @vite-ignore */ E("extension-table-cell")),
    import(/* @vite-ignore */ E("extension-table-header")),
    import(/* @vite-ignore */ E("extension-image")),
    import(/* @vite-ignore */ E("extension-link")),
    import(/* @vite-ignore */ E("extension-placeholder")),
    import(/* @vite-ignore */ "https://esm.sh/@tiptap/pm@" + V + "/state"),
    import(/* @vite-ignore */ E("extension-code-block-lowlight")),
    import(/* @vite-ignore */ "https://esm.sh/lowlight@3"),
  ]).then((m) => {
    const lowlight = m[12].createLowlight(m[12].common);   // 공통 언어(약 37종) 등록
    return {
      Editor: m[0].Editor,
      Extension: m[0].Extension,
      Node: m[0].Node,
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
      lowlight,
      languages: lowlight.listLanguages(),
    };
  }).catch((e) => { _p = null; throw e; });
  return _p;
}
