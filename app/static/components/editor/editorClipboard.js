// Clipboard representations are ordered by semantic value. Spreadsheet apps commonly publish
// an editable HTML/TSV table and a PNG preview together; choosing files first flattens cells into
// an image. Keep the structured representation and use files only when no table is available.

export function hasClipboardTableHtml(html) {
  return /<(?:table|tr|td|th)(?:\s|>)/i.test(String(html || ""));
}

export function parseTsv(text) {
  const source = String(text || "");
  if (!source.includes("\t")) return null;
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
