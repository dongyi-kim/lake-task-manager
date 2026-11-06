import { normalizeSearchText } from "./searchText.js";

// Space metadata only: searching never changes the tree into a results list.
export function pageTree(pages, expanded = {}, query = "", overrides = {}) {
  const nodes = new Map(), needle = normalizeSearchText(query);
  const add = (page, parent = "") => {
    const id = String(page.id || "");
    if (id) nodes.set(id, { ...nodes.get(id), ...page, id, parent });
  };
  for (const page of pages) {
    let parent = "";
    for (const ancestor of page.ancestors || []) { add(ancestor, parent); parent = String(ancestor.id); }
    add(page, parent);
  }
  const groups = new Map(), paths = new Set();
  for (const node of nodes.values()) {
    node.match = !!needle && normalizeSearchText(node.title).includes(needle);
    const siblings = groups.get(node.parent) || [];
    siblings.push(node); groups.set(node.parent, siblings);
    if (node.match) {
      let parent = node.parent;
      const visited = new Set([node.id]);
      while (parent && !visited.has(parent)) {
        visited.add(parent); paths.add(parent); parent = nodes.get(parent)?.parent;
      }
    }
  }
  const compare = (a, b) => String(a.title).localeCompare(String(b.title), undefined, { numeric: true }) || a.id.localeCompare(b.id);
  for (const siblings of groups.values()) siblings.sort(compare);
  const rows = [], orderedMatches = [], visited = new Set();
  function visit(parent, level, visible) {
    for (const node of groups.get(parent) || []) {
      if (visited.has(node.id)) continue;
      visited.add(node.id);
      const hasChildren = !!groups.get(node.id)?.length;
      const open = hasChildren && (needle && overrides[node.id] !== undefined
        ? overrides[node.id] : !!expanded[node.id] || !!needle && paths.has(node.id));
      if (node.match) orderedMatches.push(node.id);
      if (visible) rows.push({ ...node, level, hasChildren, open, containsMatch: paths.has(node.id) });
      visit(node.id, level + 1, visible && open);
    }
  }
  visit("", 0, true);
  return { rows, matches: orderedMatches, nodes, count: nodes.size, searching: !!needle };
}
