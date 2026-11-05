// Confluence DC 9.2 native macro names/parameters. Jira editors do not install these nodes.
const text = (key, label, value = "") => ({ key, label, value, type: "text" });
const flag = (key, label, value = false) => ({ key, label, value: String(value), type: "boolean" });
const number = (key, label, value, min, max) => ({ key, label, value: String(value), type: "number", min, max });
const select = (key, label, value, options) => ({ key, label, value, type: "select", options });
const title = text("title", "제목");
const levels = [number("minLevel", "시작 헤딩 단계", 1, 1, 6), number("maxLevel", "마지막 헤딩 단계", 6, 1, 6), flag("outline", "목차 항목 번호")];
export const CONFLUENCE_MACROS = [
  { name: "expand", label: "펼치기", body: "rich", fields: [text("title", "제목", "자세히 보기")] },
  { name: "panel", label: "패널", body: "rich", fields: [title] },
  ...[["info", "정보"], ["note", "노트"], ["tip", "팁"], ["warning", "경고"]].map(([name, label]) => ({ name, label, body: "rich", fields: [title, flag("icon", "아이콘 표시", true)] })),
  { name: "excerpt", label: "발췌 영역", body: "rich", fields: [flag("hidden", "원문에서 숨기기")] },
  { name: "include", label: "문서 포함", page: "", fields: [] },
  { name: "excerpt-include", label: "발췌 포함", page: "", fields: [flag("nopanel", "제목과 테두리 숨기기")] },
  { name: "pagetree", label: "페이지 트리", page: "root", optionalPage: true, fields: [
    select("root", "시작 위치", "@home", ["@self", "@home", "@parent", "@none"]),
    select("sort", "정렬", "position", ["position", "natural", "bitwise", "creation", "modified"]),
    number("startDepth", "처음 펼칠 깊이", 1, 1, 5), flag("reverse", "역순"), flag("searchBox", "문서 검색"), flag("expandCollapseAll", "전체 접기·펼치기") ] },
  { name: "children", label: "하위 문서 목록", page: "page", optionalPage: true, fields: [number("depth", "표시 깊이", 1, 1, 5),
    select("sort", "정렬", "title", ["title", "creation", "modified"]), flag("reverse", "역순")] },
  { name: "toc", label: "본문 목차", fields: [select("type", "배치", "list", ["list", "flat"]), ...levels] },
  { name: "toc-zone", label: "영역 목차", body: "rich", fields: [select("location", "목차 위치", "top", ["top", "bottom", "both"]), ...levels] },
  { name: "attachments", label: "첨부 파일 목록", page: "page", optionalPage: true, fields: [select("sortBy", "정렬", "name", ["name", "date", "size"])] },
  { name: "status", label: "상태 표시", inline: true, fields: [text("title", "상태 문구", "진행 중"), select("colour", "색상", "Blue", ["Grey", "Red", "Yellow", "Green", "Blue"]), flag("subtle", "연한 배경")] },
  { name: "anchor", label: "이동 지점", inline: true, fields: [text("", "이동 지점 이름", "section")] },
  { name: "noformat", label: "서식 없는 텍스트", body: "plain", fields: [flag("nopanel", "테두리 숨기기")] },
  { name: "code", label: "코드 블록", body: "plain", fields: [text("language", "언어", "sql"), title, flag("collapse", "처음에 접기"), flag("linenumbers", "줄 번호", true), number("firstline", "첫 줄 번호", 1, 1, 100000)] },
];
export const macroDefinition = name => CONFLUENCE_MACROS.find(item => item.name === name);
export const macroDefaults = name => Object.fromEntries((macroDefinition(name)?.fields || []).map(item => [item.key, item.value]));
export const macroNodeType = name => {
  if (name.startsWith("resource:")) return "confluenceInline";
  const item = macroDefinition(name);
  return item?.inline ? "confluenceInline" : item?.body === "rich" ? "confluenceBlock" : item?.body === "plain" ? "confluencePlain" : "confluenceAtom";
};
export function confluenceSlashCommands() {
  return [...CONFLUENCE_MACROS.map(item => ({ g: "Confluence", id: item.name, ic: item.inline ? "◈" : "▤", t: item.label,
    h: item.name, k: item.name + " " + item.label,
    run: (editor, range, host) => { editor.chain().focus().deleteRange(range).run(); host.openConfluenceMacro(item.name); } })),
    { g: "Confluence", id: "hr", ic: "―", t: "가로 구분선", h: "horizontal rule", k: "hr horizontal rule divider 가로 구분선",
      run: (editor, range) => editor.chain().focus().deleteRange(range).setHorizontalRule().run() }];
}
