export function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"]/g, (character) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[character]
  ));
}

// Empty paragraphs/whitespace are not a comment. Structural content still is,
// even when getText() has nothing (image-only, table, mention or checkbox).
export function hasEditorContent(html) {
  const body = new DOMParser().parseFromString(String(html || ""), "text/html").body;
  return !!((body.textContent || "").replace(/[\u200b\ufeff]/g, "").trim()
    || body.querySelector("img, a[href], input, table, hr, [data-type='mention']"));
}
