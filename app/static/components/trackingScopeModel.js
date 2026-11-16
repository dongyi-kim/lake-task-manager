// Pure saved-run projection. scopeId is a hierarchy node ID, never a parsed label/path.
// Returns {run, report, node, breadcrumbs, groups, valid}; group.keys are report root keys.
import { issueKeyOf } from './trackingModel.js';
import { buildTrackingReport } from './trackingReportModel.js';

const kindOf = node => String(node.kind || node.type || '').toLowerCase().replace(/[-_\s]/g, '');
const titleOf = node => node.name || node.summary || issueKeyOf(node) || '';
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;

// Entries retain actual node ancestry, including virtual VOC nodes and shared branches.
// An ancestor set rather than a global visited set keeps every distinct display path.
function entriesFor(tree, ancestors = []) {
  const entries = [];
  function walk(node, path, positions, seen) {
    if (!node || seen.has(node)) return;
    const identity = String(node.id || issueKeyOf(node) || 'node:' + positions.join('.'));
    const entry = {node, identity, path};
    const nextPath = [...path, entry];
    entries.push(entry);
    const nextSeen = new Set([...seen, node]);
    for (const [index, child] of (node.children || []).entries()) {
      walk(child, nextPath, [...positions, index], nextSeen);
    }
  }
  for (const [index, node] of (tree || []).entries()) walk(node, ancestors, [index], new Set());
  return entries;
}

function clippedTree(node, seen = new Set()) {
  if (!node || seen.has(node)) return null;
  const nextSeen = new Set([...seen, node]);
  return {...node, children:(node.children || []).map(child => clippedTree(child, nextSeen)).filter(Boolean)};
}

// Some report roots exist only in issue.parentKey, outside the observed hierarchy.
// Give their summary tab a local hierarchy bounded by the report's exact keys.
function reportSelection(displayRun, scopeId, entries) {
  const item = buildTrackingReport(displayRun).items.find(value => value.rowId === scopeId);
  if (!item) return null;
  const keys = new Set(item.keys), used = new Set([item.key]);
  function clip(node, seen = new Set()) {
    if (!node || seen.has(node)) return [];
    const key = issueKeyOf(node), retain = keys.has(key) && !used.has(key);
    if (retain) used.add(key);
    const nextSeen = new Set([...seen, node]);
    const children = (node.children || []).flatMap(child => clip(child, nextSeen));
    return retain ? [{...node, children}] : children;
  }
  const original = entries.find(entry => issueKeyOf(entry.node) === item.key)?.node;
  const children = (original?.children || []).flatMap(child => clip(child));
  for (const child of item.children) {
    if (used.has(child.key)) continue;
    const entry = entries.find(value => issueKeyOf(value.node) === child.key);
    if (entry) children.push(...clip(entry.node));
    else {
      used.add(child.key);
      children.push({id:'tracking-report:'+child.key, issueKey:child.key,
        name:child.title, kind:child.issue?.type || 'Sub-Task', children:[]});
    }
  }
  const node = {...original, id:scopeId, issueKey:item.key, name:item.title,
    kind:item.issue?.type || 'Task', contextOnly:item.issue?.contextOnly === true, children};
  return {node, identity:scopeId, path:[]};
}

function reportGroups(report, entries, selection) {
  const byKey = new Map();
  for (const entry of entries) {
    const key = issueKeyOf(entry.node);
    if (!key) continue;
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(entry);
  }
  const groups = new Map();
  const priority = new Map();
  for (const item of report.items) {
    // A missing parent root can still inherit the observed child's hierarchy context.
    const direct = byKey.get(item.key) || [];
    const candidates = direct.length ? direct : item.keys.flatMap(key => byKey.get(key) || []);
    const ordered = [...candidates].sort((a, b) => compare(
      JSON.stringify([...a.path, a].map(entry => entry.identity)),
      JSON.stringify([...b.path, b].map(entry => entry.identity))));
    const entry = ordered[0], path = entry ? [...entry.path, entry] : [];
    const wbs = [...path].reverse().find(value => kindOf(value.node) === 'wbs');
    const epic = [...path].reverse().find(value => kindOf(value.node) === 'epic');
    const context = [wbs, epic].filter(Boolean);
    const key = context.length ? 'tracking-scope:group:' + JSON.stringify(context.map(value => value.identity)) : 'tracking-scope:unlinked';
    if (!groups.has(key)) {
      const selectedIndex = selection ? path.findIndex(value => value.identity === selection.identity) : -1;
      const visible = context.filter(value => !selection || path.indexOf(value) > selectedIndex);
      const deepest = epic || wbs;
      const voc = path.some(value => value.node.vocGrouping === true);
      priority.set(key, voc ? 2 : wbs ? 0 : epic ? 1 : 3);
      groups.set(key, {key, title:deepest ? titleOf(deepest.node) : '연결되지 않은 업무',
        pathLabel:context.length ? visible.map(value => titleOf(value.node)).filter(Boolean).join(' › ') : '연결되지 않은 업무',
        voc,
        scopeId:deepest?.node.id || '', keys:[]});
    }
    groups.get(key).keys.push(item.key);
  }
  return [...groups.values()].sort((a, b) => priority.get(a.key)-priority.get(b.key) || compare(a.key, b.key));
}

export function buildTrackingScope(displayRun, scopeId = '') {
  const entries = entriesFor(displayRun?.tree);
  const selection = scopeId ? entries.find(entry => entry.node.id === scopeId) ||
    (scopeId.startsWith('tracking-report:') ? reportSelection(displayRun, scopeId, entries) : null) : null;
  const valid = !scopeId || !!selection;
  let run = displayRun;
  let scopedEntries = entries;
  if (scopeId) {
    const tree = selection ? [clippedTree(selection.node)].filter(Boolean) : [];
    // Index the isolated tree: a shared ticket's other branch cannot widen selection.
    scopedEntries = entriesFor(tree, selection?.path || []);
    const keys = new Set(scopedEntries.map(entry => issueKeyOf(entry.node)).filter(Boolean));
    const originalIssues = Array.isArray(displayRun?.issues) ? displayRun.issues : Object.values(displayRun?.issues || {});
    const rootKey = selection ? issueKeyOf(selection.node) : '';
    const issues = originalIssues.filter(issue => keys.has(issue.key)).map(issue => {
      const projected = {...issue};
      if (Array.isArray(issue.events)) projected.events = issue.events.filter(event => keys.has(event?.issueKey));
      // Promoting a selected Subtask changes only this display projection, not its Jira type.
      if (issue.key === rootKey && issue.parentKey && !keys.has(issue.parentKey)) projected.parentKey = '';
      return projected;
    });
    // Keep global collection gaps/status: selecting a branch cannot certify a partial run.
    run = {...displayRun, issues, tree, events:(displayRun?.events || []).filter(event => keys.has(event?.issueKey))};
  }
  const report = buildTrackingReport(run);
  const breadcrumbs = selection ? [...selection.path, selection].map(entry => ({
    id:entry.node.id || '', title:titleOf(entry.node), kind:kindOf(entry.node),
  })) : [];
  return {run, report, node:selection?.node || null, breadcrumbs,
    groups:reportGroups(report, scopedEntries, selection), valid};
}
