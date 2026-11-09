import { normalizeSearchText } from "./searchText.js";

// An explicit complete branch read supersedes old rows beneath that parent.
export function replaceTreeBranch(pages, parent, children) {
  const id = String(parent.id), known = new Map(children.map(row => [String(row.id), row]));
  const kept = pages.filter(row => {
    if (String(row.id) === id || known.has(String(row.id))) return false;
    const path = row.ancestors || [], at = path.findIndex(row => String(row.id) === id);
    if (at < 0) return true;
    const child = known.get(String(path[at + 1]?.id || row.id));
    return child && child.hasChildren !== false;
  });
  return [{...parent, hasChildren:!!children.length, unknownChildren:false}, ...children, ...kept];
}

// Space metadata only: searching never changes the tree into a results list.
export function pageTree(pages, expanded = {}, query = "", overrides = {}) {
  const nodes = new Map(), needle = normalizeSearchText(query);
  const add = (page, parent = "") => {
    const id = String(page.id || "");
    if (id && !nodes.has(id)) nodes.set(id, { ...page, id, parent });
  };
  // A descendant's ancestor stub must not overwrite a page's own title/parent.
  // Rows arrive across multiple requests and can straddle a page rename or move.
  for (const page of pages) add(page, String(page.ancestors?.at(-1)?.id || ""));
  for (const page of pages) {
    let parent = "";
    for (const ancestor of page.ancestors || []) { add(ancestor, parent); parent = String(ancestor.id); }
  }
  // Keep fetched pages reachable even if concurrent moves produce a cyclic path.
  const checked = new Set();
  for (const node of nodes.values()) {
    const path = new Set();
    let current = node;
    while (current && !checked.has(current.id)) {
      if (path.has(current.id)) { current.parent = ""; break; }
      path.add(current.id); current = nodes.get(current.parent);
    }
    for (const id of path) checked.add(id);
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
      const hasChildren = !!groups.get(node.id)?.length || node.hasChildren === true;
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
