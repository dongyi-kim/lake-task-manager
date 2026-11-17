// Saved evidence only. Empty dates are retained so horizontal distance means days.
import { kstToday } from './trackingModel.js';

const DAY = 86400000;
const dateOnly = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') &&
  Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;
const kindOf = event => event.kind || event.type;
const isComment = event => ['comment','commentEdited'].includes(kindOf(event));
const isTransition = event => ['completed','reopened'].includes(kindOf(event)) ||
  kindOf(event) === 'fieldChanged' && String(event.field || '').trim().toLowerCase() === 'status';
const fieldOf = event => String(event.field || '').trim().toLowerCase();
const isMajorChange = event => isComment(event) || ['created','completed','reopened'].includes(kindOf(event)) ||
  kindOf(event) === 'fieldChanged' && ['status','assignee','duedate','priority'].includes(fieldOf(event));
const issueKind = issue => String(issue?.type || issue?.kind || '').toLowerCase().replace(/[-_\s]/g,'');
const isSubtask = issue => issue?.isSubtask === true || ['subtask','sub'].includes(issueKind(issue));
const isStructure = issue => ['module','wbs','group'].includes(issueKind(issue));
const keyOf = node => node?.issueKey || node?.key;
const completeEvidence = issue => issue?.coverage?.historyComplete === true && issue?.coverage?.commentsComplete === true;
const statusCategory = issue => String(issue?.statusCategory?.key || issue?.statusCategory || '').toLowerCase();

function directChildren(issue, context) {
  if (!issue?.key || isSubtask(issue) || isStructure(issue) || issue.virtual) return [];
  const epic = issueKind(issue) === 'epic';
  const records = Array.isArray(context.issues) ? context.issues : Object.values(context.issues || {});
  const issues = new Map(records.filter(child=>child?.key).map(child=>[child.key,child]));
  const children = new Map();
  const eligible = child => !child.virtual && !isStructure(child) && issueKind(child) !== 'epic' && (!epic || !isSubtask(child));
  const add = node => {
    const key = keyOf(node);
    if (!key || key === issue.key || node.virtual) return;
    const child = {...node,...issues.get(key),key};
    if (eligible(child)) children.set(key,{...child,title:child.summary || node.name || node.title || key});
  };
  for (const child of records) {
    if (epic ? child.epicKey === issue.key || child.parentKey === issue.key : child.parentKey === issue.key) add(child);
  }
  // Virtual wrappers carry layout, not another Jira hierarchy level.
  function addChildNodes(nodes, seen = new Set()) {
    for (const node of nodes || []) {
      if (!node || seen.has(node)) continue;
      seen.add(node);
      if (node.virtual || isStructure(node) || !keyOf(node)) addChildNodes(node.children,seen);
      else add(node);
    }
  }
  const visited = new Set();
  function walk(nodes) {
    for (const node of nodes || []) {
      if (!node || visited.has(node)) continue;
      visited.add(node);
      if (keyOf(node) === issue.key && !node.virtual) addChildNodes(node.children);
      walk(node.children);
    }
  }
  walk(context.tree);
  return [...children.values()];
}

export function buildTicketTimeline(issue, events = [], request = {}, context = {}) {
  const start = request.startDate, end = request.endDate;
  const rangeKnown = dateOnly(start) && dateOnly(end) && start <= end;
  const children = directChildren(issue,context), childByKey = new Map(children.map(child=>[child.key,child]));
  const total = children.length, done = children.filter(child=>statusCategory(child)==='done').length;
  const unknown = children.filter(child=>!['done','new','indeterminate'].includes(statusCategory(child))).length;
  const childPartial = !completeEvidence(issue) || context.coverage?.complete === false || children.some(child=>!completeEvidence(child));
  const childProgress = {total,done,unknown,percent:total && !unknown && !childPartial ? done/total*100 : null,
    partial:childPartial,label:'조회된 하위 작업 기준'};
  const childLabel = isSubtask(issue) || isStructure(issue) ? '' : issueKind(issue)==='epic' ? 'Task' : 'Sub-task';
  const days = [], byDate = new Map();
  if (rangeKnown) {
    for (let at = Date.parse(start); at <= Date.parse(end); at += DAY) {
      const date = new Date(at).toISOString().slice(0,10);
      const day = {date, label:date.slice(5).replace('-','.'), transitions:[], comments:[], childChanges:[]};
      days.push(day); byDate.set(date,day);
    }
  }
  const seen = new Set();
  let unplacedCount = 0, transitionCount = 0, commentCount = 0, childEventCount = 0, childUnplacedCount = 0;
  const selected = events.filter(event => issue?.key && event.issueKey === issue.key && (isComment(event) || isTransition(event)) ||
    childByKey.has(event.issueKey) && isMajorChange(event))
    .filter(event => {
      const id = JSON.stringify([event.issueKey,event.id || [kindOf(event),fieldOf(event),event.date || event.at,event.commentId,event.from,event.to]]);
      if (seen.has(id)) return false;
      seen.add(id); return true;
    }).sort((a,b) => (Date.parse(a.date || a.at) || 0) - (Date.parse(b.date || b.at) || 0) ||
      String(a.id || '').localeCompare(String(b.id || '')));
  for (const event of selected) {
    const child = childByKey.get(event.issueKey);
    const instant = Date.parse(event.date || event.at);
    if (!Number.isFinite(instant)) { if(child)childUnplacedCount++;else unplacedCount++; continue; }
    const date = kstToday(new Date(instant)), day = byDate.get(date);
    if (!day) continue;
    const kind = kindOf(event);
    const time = new Date(instant + 9*3600000).toISOString().slice(11,16);
    const item = {id:event.id, event, kind, date, time, from:event.from ?? null, to:event.to ?? null};
    if (child) { day.childChanges.push({...item,issueKey:child.key,title:child.title,field:event.field || null}); childEventCount++; }
    else if (isComment(event)) { day.comments.push(item); commentCount++; }
    else { day.transitions.push(item); transitionCount++; }
  }
  return {days, rangeKnown, start, end, transitionCount, commentCount, unplacedCount,
    children,childLabel,childProgress,childEventCount,childUnplacedCount,
    partial:issue?.coverage?.historyComplete !== true || issue?.coverage?.commentsComplete !== true};
}
