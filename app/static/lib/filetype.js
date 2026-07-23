// filetype.js — 파일 확장자 → 표시용 라벨·색.
//
// 아이콘 세트를 외부에서 받아오지 않는다: 이 앱은 사내망·오프라인에서도 돌아야 하고(캐시로
// 버티는 화면이 있다), 아이콘 폰트 하나를 위해 CDN 의존을 늘릴 이유가 없다. 확장자 글자
// 자체가 가장 정확한 아이콘이다 — 'PDF' 보다 PDF 를 잘 나타내는 그림은 없다.
// 대신 **색으로 종류를 가른다**. 코드 파일은 언어 색(널리 쓰이는 브랜드 색)을 그대로 써서
// 목록에서 언어가 먼저 잡히게 한다.
//
// 서버가 렌더한 코멘트 HTML 에도 같은 규칙이 필요해, 색은 CSS 의 [data-ext] 선택자로 준다
// (여기 KIND 는 라벨 계산과 그룹 판정에만 쓴다). 양쪽이 같은 표를 보도록 확장자 목록을
// 이 파일 하나에 둔다.

// 확장자 → 화면에 찍을 라벨. 없으면 확장자를 그대로 대문자로.
const LABEL = {
  jpeg: "JPG", htm: "HTML", markdown: "MD", yml: "YAML",
  cpp: "C++", cc: "C++", cxx: "C++", hpp: "C++", kt: "KOTLIN", rs: "RUST",
  py: "PY", js: "JS", mjs: "JS", cjs: "JS", ts: "TS", tsx: "TSX", jsx: "JSX",
  rb: "RUBY", sh: "SH", ps1: "PS", psm1: "PS", "7z": "7Z",
};

// 종류 — CSS 가 색을 정하고, 여기서는 무엇으로 분류되는지만 판단한다.
export const GROUPS = {
  code: ["js", "mjs", "cjs", "ts", "tsx", "jsx", "py", "java", "kt", "go", "rs", "rb", "php",
         "c", "h", "cpp", "cc", "cxx", "hpp", "cs", "swift", "scala", "sh", "bat", "ps1", "psm1",
         "sql", "r", "m", "pl", "lua", "dart", "vue", "svelte"],
  markup: ["html", "htm", "css", "scss", "less", "xml", "json", "yaml", "yml", "toml", "ini",
           "md", "markdown", "rst", "csv", "tsv"],
  doc: ["pdf", "doc", "docx", "hwp", "hwpx", "rtf", "odt", "txt", "log"],
  sheet: ["xls", "xlsx", "xlsm", "ods", "numbers"],
  slide: ["ppt", "pptx", "odp", "key"],
  image: ["png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "heic", "tif", "tiff"],
  archive: ["zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "jar", "war"],
  av: ["mp4", "mov", "avi", "mkv", "webm", "mp3", "wav", "flac", "m4a", "ogg"],
};

const OF = {};
for (const [g, exts] of Object.entries(GROUPS)) for (const e of exts) OF[e] = g;

export function extOf(name) {
  const n = (name || "").trim();
  const i = n.lastIndexOf(".");
  return i > 0 ? n.slice(i + 1).toLowerCase() : "";
}

/** 뱃지에 찍을 짧은 라벨. 확장자가 없거나 너무 길면 'FILE'. */
export function extLabel(name) {
  const e = extOf(name);
  if (!e) return "FILE";
  return LABEL[e] || (e.length <= 5 ? e.toUpperCase() : e.slice(0, 4).toUpperCase());
}

/** 종류(css 색 그룹). 모르는 확장자는 'etc'. */
export function extGroup(name) { return OF[extOf(name)] || "etc"; }
