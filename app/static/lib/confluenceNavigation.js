// Document identity survives title/parent changes; local favorites belong to this installation.
export function confluencePageId(value) {
  const text = String(value || "");
  if (/^\d+$/.test(text)) return text;
  try {
    const url = new URL(text, location.origin);
    return url.searchParams.get("pageId")?.match(/^\d+$/)?.[0]
      || url.pathname.match(/\/pages\/(\d+)(?:\/|$)/)?.[1] || "";
  } catch (_) { return ""; }
}

export function openConfluenceDocument(value) {
  const id = confluencePageId(value);
  if (!id) return false;
  location.hash = "/confluence/" + id;
  return true;
}

export function readFavorites(base) {
  try {
    const rows = JSON.parse(localStorage.getItem("ltm.confluence.favorites:" + base) || "[]");
    return Array.isArray(rows) ? rows.filter(row => /^\d+$/.test(String(row.id)) && typeof row.title === "string") : [];
  } catch (_) { return []; }
}

export function writeFavorites(base, rows) {
  localStorage.setItem("ltm.confluence.favorites:" + base, JSON.stringify(rows));
}
