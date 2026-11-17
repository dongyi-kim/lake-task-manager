// Display-only hierarchy over saved evidence. Virtual groups never represent Jira issues.
import { issueKeyOf } from './trackingModel.js';

const kindOf = node => String(node?.kind || node?.type || '').toLowerCase().replace(/[-_\s]/g, '');
const groupId = path => 'tracking-hierarchy:epic-unassigned:' + encodeURIComponent(JSON.stringify(path));
const sameNodes = (left, right) => left.length === right.length && left.every((node, index) => node === right[index]);

// Module membership is a query condition, not a visible hierarchy level. Retain
// source paths only for stable IDs when legacy branches reuse a node ID.
function wbsRoots(tree) {
  const counts = new Map();
  function count(nodes, seen = new Set()) {
    for (const node of nodes || []) {
      if (!node || seen.has(node)) continue;
      if (node.id) counts.set(node.id, (counts.get(node.id) || 0) + 1);
      count(node.children, new Set([...seen, node]));
    }
  }
  count(tree);
  const displayId = (prefix, path) => 'tracking-hierarchy:' + prefix + ':' + encodeURIComponent(JSON.stringify(path));
  const unlinked = (children, id, name = 'WBS Task 미연결') => ({id, kind:'wbs', name, virtual:true, wbsUnassigned:true, children});
  function lift(nodes, path = [], seen = new Set()) {
    const result = [], occurrences = new Map();
    for (const node of nodes || []) {
      if (!node || seen.has(node)) continue;
      const identity = String(node.id || issueKeyOf(node) || kindOf(node) + ':' + (node.name || node.summary || ''));
      const occurrence = occurrences.get(identity) || 0;
      occurrences.set(identity, occurrence + 1);
      const nextPath = [...path, [identity, occurrence]];
      const id = counts.get(node.id) > 1 ? displayId('node', nextPath) : node.id;
      const children = lift(node.children, nextPath, new Set([...seen, node]));
      if (kindOf(node) === 'module') {
        result.push(...children.filter(child => kindOf(child) === 'wbs'));
        const loose = children.filter(child => kindOf(child) !== 'wbs');
        if (loose.length) result.push(node.id === 'unmapped'
          ? {...node, id, kind:'wbs', type:'wbs', name:'WBS Task 미연결', summary:'WBS Task 미연결', virtual:true, wbsUnassigned:true, children:loose}
          : unlinked(loose, displayId('wbs-unassigned', nextPath),
            'WBS Task 미연결' + (node.name || node.summary ? ' · ' + (node.name || node.summary) : '')));
        continue;
      }
      result.push(id === node.id && sameNodes(children, node.children || []) ? node : {...node, id, children});
    }
    return result;
  }
  const lifted = lift(tree), loose = lifted.filter(node => kindOf(node) !== 'wbs');
  return loose.length ? [...lifted.filter(node => kindOf(node) === 'wbs'),
    unlinked(loose, displayId('wbs-unassigned', []))] : lifted;
}

export function projectTrackingHierarchy(run) {
  if (!run) return run;
  const issues = new Map((Array.isArray(run.issues) ? run.issues : Object.values(run.issues || {}))
    .filter(issue => issue?.key).map(issue => [issue.key, issue]));
  const nodes = new Map(), epicMembers = new Set();
  const kind = node => kindOf(node) || kindOf(issues.get(issueKeyOf(node)));
  function index(node, inEpic = false, seen = new Set()) {
    if (!node || seen.has(node)) return;
    const key = issueKeyOf(node);
    if (key && !nodes.has(key)) nodes.set(key, node);
    const nextInEpic = inEpic || (kind(node) === 'epic' && node.epicUnassigned !== true);
    if (key && nextInEpic) epicMembers.add(key);
    const nextSeen = new Set([...seen, node]);
    for (const child of node.children || []) index(child, nextInEpic, nextSeen);
  }
  for (const node of run.tree || []) index(node);
  const isTicket = node => !!issueKeyOf(node) && !['module', 'wbs', 'epic'].includes(kind(node));

  function hasKnownEpic(node) {
    let current = node;
    const seen = new Set();
    while (current) {
      const key = issueKeyOf(current) || current.key;
      if (seen.has(key)) break;
      seen.add(key);
      if (epicMembers.has(key)) return true;
      const issue = issues.get(key);
      if (String(current.epicKey || issue?.epicKey || '').trim()) return true;
      const parentKey = current.parentKey || issue?.parentKey;
      if (!parentKey) break;
      const parent = issues.get(parentKey) || nodes.get(parentKey);
      if (!parent) break;
      if (kindOf(parent) === 'epic') return true;
      current = parent;
    }
    return false;
  }

  function project(children, path, protectedBranch = false, seen = new Set()) {
    const occurrences = new Map(), result = [];
    let unassigned = null;
    for (const node of children || []) {
      if (!node || seen.has(node)) continue;
      // Include the entire actual parent path, not a label or shared issue key.
      // Occurrences also distinguish malformed/legacy siblings with identical IDs.
      const identity = String(node.id || issueKeyOf(node) || kind(node) + ':' + (node.name || node.summary || ''));
      const occurrence = occurrences.get(identity) || 0;
      occurrences.set(identity, occurrence + 1);
      const nextPath = [...path, [identity, occurrence]];
      const ticket = isTicket(node);
      const projectedChildren = project(node.children, nextPath,
        protectedBranch || ticket || kind(node) === 'epic', new Set([...seen, node]));
      const projected = sameNodes(projectedChildren, node.children || []) ? node : {...node, children:projectedChildren};
      // A missing tree boundary is not evidence of missing Epic membership.
      // Keep tickets with known membership in their existing saved-run location.
      if (!protectedBranch && ticket && !hasKnownEpic(node)) {
        if (!unassigned) {
          unassigned = {id:groupId(path), kind:'Epic', name:'소속 Epic 없음',
            virtual:true, epicUnassigned:true, children:[]};
          result.push(unassigned);
        }
        unassigned.children.push(projected);
      } else result.push(projected);
    }
    return result;
  }
  // Group with the saved ancestry first: removing module paths must not merge
  // independent unassigned-Epic scopes or change their existing identities.
  const tree = wbsRoots(project(run.tree, []));
  return sameNodes(tree, run.tree || []) ? run : {...run, tree};
}
