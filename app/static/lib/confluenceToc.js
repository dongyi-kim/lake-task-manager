// Headings belong to the rendered document, not the SPA hash route.
const nonHeadingCharacters = new RegExp("[^\\p{L}\\p{N}]+", "gu");
export function documentHeadings(element) {
  const used = new Set();
  return [...element.querySelectorAll("h1,h2,h3,h4,h5,h6")].map(node => {
    const title = (node.textContent || "").replace(/\s+/g, " ").trim();
    const stem = node.id || "conf-heading-" + (title.normalize("NFKC").toLowerCase()
      .replace(nonHeadingCharacters, "-").replace(/^-|-$/g, "").slice(0, 80) || "section");
    let id = stem, index = 2;
    while (used.has(id)) id = stem + "-" + index++;
    used.add(id); node.id = id; node.tabIndex = -1;
    return { id, title: title || "제목 없음", level: Number(node.tagName.slice(1)), node };
  });
}

export function visibleHeading(headings, reader) {
  if (!headings.length) return "";
  const top = reader.getBoundingClientRect().top + 40;
  let current = headings[0].id;
  for (const heading of headings) {
    if (heading.node.getBoundingClientRect().top > top) break;
    current = heading.id;
  }
  return current;
}
