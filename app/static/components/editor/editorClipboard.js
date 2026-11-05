// Clipboard representations are ordered by semantic value. Spreadsheet apps commonly publish
// an editable HTML/TSV table and a PNG preview together; choosing files first flattens cells into
// an image. Keep the structured representation and use files only when no table is available.

export function hasClipboardTableHtml(html) {
  return /<(?:table|tr|td|th)(?:\s|>)/i.test(String(html || ""));
}

// Rich code copied from Jira, an IDE or a web page already has a schema. Let
// ProseMirror parse it before the tab-delimited plain-text fallback sees its tabs.
export function hasClipboardCodeHtml(html) {
  return /<(?:pre|code)(?:\s|>)/i.test(String(html || ""));
}

// Paste rules do not run Markdown input rules. Recognize complete fenced blocks,
// preserving surrounding prose and literal code (including tabs and HTML). This
// deliberately is not a general Markdown renderer; unmatched fences stay text.
export function fencedCodeNodes(text) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const nodes = [], prose = [];
  let found = false;
  const flush = () => {
    if (!prose.length) return;
    const content = [];
    for (const line of prose.splice(0)) {
      if (content.length) content.push({ type: "hardBreak" });
      if (line) content.push({ type: "text", text: line });
    }
    nodes.push({ type: "paragraph", ...(content.length ? { content } : {}) });
  };
  for (let i = 0; i < lines.length; i++) {
    const opening = lines[i].match(/^ {0,3}(`{3,}|~{3,})([A-Za-z0-9_+#.\-]*)[ \t]*$/);
    if (!opening) {
      if (lines[i] === "") flush(); else prose.push(lines[i]);
      continue;
    }
    // Even an unfinished fence must stay prose rather than becoming a TSV table.
    found = true;
    let end = i + 1;
    for (; end < lines.length; end++) {
      const closing = lines[end].match(/^ {0,3}(`{3,}|~{3,})[ \t]*$/);
      if (closing && closing[1][0] === opening[1][0]
          && closing[1].length >= opening[1].length) break;
    }
    if (end === lines.length) {
      for (; i < lines.length; i++) prose.push(lines[i]);
      break;
    }
    flush();
    const code = lines.slice(i + 1, end).join("\n");
    nodes.push({ type: "codeBlock", attrs: { language: opening[2] || null },
      ...(code ? { content: [{ type: "text", text: code }] } : {}) });
    i = end;
  }
  flush();
  return found ? nodes : null;
}

export function parseTsv(text) {
  const source = String(text || "");
  if (!source.trim() || !source.includes("\t")) return null;
  const rows = [];
  let row = [], cell = "", quoted = false;
  const pushCell = () => { row.push(cell); cell = ""; };
  const pushRow = () => { pushCell(); rows.push(row); row = []; };
  for (let i = 0; i < source.length; i++) {
    const ch = source[i];
    if (ch === '"') {
      if (quoted && source[i + 1] === '"') { cell += '"'; i++; }
      else if (quoted) quoted = false;
      else if (!cell) quoted = true;
      else cell += ch;
    } else if (!quoted && ch === "\t") {
      pushCell();
    } else if (!quoted && (ch === "\n" || ch === "\r")) {
      if (ch === "\r" && source[i + 1] === "\n") i++;
      pushRow();
    } else {
      cell += ch;
    }
  }
  if (cell || row.length || !rows.length) pushRow();
  if (rows.length > 1 && rows[rows.length - 1].every((value) => value === "")) rows.pop();
  return rows.length && rows.some((values) => values.length > 1) ? rows : null;
}

export function tsvTableNode(text) {
  const rows = parseTsv(text);
  if (!rows) return null;
  const width = Math.max(...rows.map((row) => row.length));
  const paragraph = (value) => ({
    type: "paragraph",
    content: value ? [{ type: "text", text: value }] : undefined,
  });
  return {
    type: "table",
    content: rows.map((row) => ({
      type: "tableRow",
      content: Array.from({ length: width }, (_, index) => ({
        type: "tableCell", content: [paragraph(row[index] || "")],
      })),
    })),
  };
}
