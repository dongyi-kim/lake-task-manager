// Display projection only: saved Jira sources, event evidence and totals stay intact.
import { issueKeyOf } from './trackingModel.js';

const fold = value => String(value ?? '').normalize('NFKC').toLocaleLowerCase().trim();
const ordered = values => [...values].sort((a, b) => a < b ? -1 : a > b ? 1 : 0);
const nodeId = (kind, value) => `tracking-voc:${kind}:${encodeURIComponent(value)}`;
const isSubtask = issue => issue?.isSubtask === true || /subtask/i.test(String(issue?.type || issue?.kind || '').replace(/[-_ ]/g, ''));

function classification(summary) {
  const original = String(summary ?? '');
  const prefix = original.match(/^\s*\[\s*([^\]]+?)\s*\]\s*([\s\S]*)$/);
  const category = prefix?.[1].match(/^\s*(.+?)\s*-\s*(.+?)\s*$/);
  if (!category) return { category: '분류 미지정', title: original };
  return { category: `${category[1].trim()} - ${category[2].trim()}`, title: prefix[2].trim() || original };
}

export function projectTrackingVoc(run, { vocComponent = '사용자 VoC' } = {}) {
  if (!run) return run;
  const issues = new Map((run.issues || []).map(issue => [issue.key, issue]));
  const originals = new Map(), parents = new Map();
  function index(node, parentKey = '') {
    const key = issueKeyOf(node);
    if (key && !originals.has(key)) originals.set(key, node);
    if (key && parentKey && (isSubtask(issues.get(key)) || isSubtask(node))) parents.set(key, parentKey);
    for (const child of node.children || []) index(child, key || parentKey);
  }
  for (const node of run.tree || []) index(node);
  for (const issue of issues.values()) if (issue.parentKey) parents.set(issue.key, issue.parentKey);
  const wantedComponent = fold(vocComponent);
  const genuine = issue => (issue.voc === true && issue.vocInherited !== true) || (wantedComponent && [...(issue.modules || []), ...(issue.components || [])]
    .some(component => fold(typeof component === 'object' ? component?.name : component) === wantedComponent));
  const moved = new Set([...issues.values()].filter(genuine).map(issue => issue.key));
  if (!moved.size) return run;
  let grew = true;
  while (grew) {
    grew = false;
    for (const [key, parent] of parents) if (moved.has(parent) && !moved.has(key)) { moved.add(key); grew = true; }
  }
  const rootOf = key => {
    const visited = new Set([key]);
    while (moved.has(parents.get(key)) && !visited.has(parents.get(key))) {
      key = parents.get(key); visited.add(key);
    }
    return key;
  };
  const info = new Map();
  for (const key of moved) {
    const issue = issues.get(key), original = issue?.originalSummary ?? issue?.summary ?? originals.get(key)?.name ?? key;
    const parsed = classification(original);
    info.set(key, { original, title: genuine(issue || {}) ? parsed.title : original,
      category: classification(issues.get(rootOf(key))?.originalSummary ?? issues.get(rootOf(key))?.summary ?? original).category });
  }
  const projectedIssues = (run.issues || []).map(issue => {
    if (!moved.has(issue.key)) return issue;
    const display = info.get(issue.key);
    return { ...issue, summary: display.title, originalSummary: display.original, voc: true,
      vocCategory: display.category, vocInherited: !genuine(issue) };
  });
  function removeMoved(node) {
    if (node.vocGrouping === true) return [];
    const retained = (node.children || []).flatMap(removeMoved);
    if (moved.has(issueKeyOf(node))) return retained;
    return [{ ...node, children: retained }];
  }
  const tree = (run.tree || []).flatMap(removeMoved);
  const children = new Map();
  for (const key of moved) {
    const parent = parents.get(key);
    if (moved.has(parent)) {
      if (!children.has(parent)) children.set(parent, new Set());
      children.get(parent).add(key);
    }
  }
  const visited = new Set();
  function ticket(key) {
    if (visited.has(key)) return null;
    visited.add(key);
    const source = originals.get(key) || {}, issue = issues.get(key) || {}, display = info.get(key);
    return { ...source, id: nodeId('ticket', key), issueKey: key, kind: issue.type || source.kind || 'Task',
      name: display.title, children: ordered(children.get(key) || []).map(ticket).filter(Boolean) };
  }
  const categories = new Map();
  const roots = ordered([...moved].filter(key => !moved.has(parents.get(key))));
  // A malformed parent cycle must not discard collected tickets.
  const rootKeys = new Set(roots);
  roots.push(...ordered([...moved].filter(key => !rootKeys.has(key))));
  for (const key of roots) {
    if (visited.has(key)) continue;
    const category = info.get(key).category;
    if (!categories.has(category)) categories.set(category, []);
    categories.get(category).push(ticket(key));
  }
  tree.push({ id: 'tracking-voc:wbs', kind: 'wbs', name: '사용자 VOC', virtual: true, vocGrouping: true,
    children: ordered(categories.keys()).map(category => ({ id: nodeId('epic', category), kind: 'Epic',
      name: category, virtual: true, children: categories.get(category) })) });
  return { ...run, issues: projectedIssues, tree };
}
