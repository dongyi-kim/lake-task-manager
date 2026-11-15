// Report projections over a saved run. No collection, network, DOM, or current-status inference.
import { eventText, issueKeyOf, ticketLabel } from './trackingModel.js';

const rank = {review:0,completed:1,changed:2,quiet:3};
const kindOf = value => String(value || '').toLowerCase().replace(/[-_\s]/g,'');
const at = event => event.date || event.at || '';
const time = value => Number.isFinite(Date.parse(value)) ? Date.parse(value) : 0;
const recentFirst = (a,b) => time(at(b)) - time(at(a)) || String(a.id || '').localeCompare(String(b.id || ''));
const distinct = values => [...new Set(values)];
const eventId = event => String(event.id || JSON.stringify([event.issueKey,event.kind,at(event),event.commentId,
  event.field,event.from,event.to,event.actorId,event.body,event.source]));
const isComment = event => event.kind === 'comment' || event.kind === 'commentEdited';
const isBoundary = event => event.kind === 'completed' || event.kind === 'reopened';
const isStatus = event => isBoundary(event) || (event.kind === 'fieldChanged' && kindOf(event.field) === 'status');

function commentsFrom(events) {
  const groups = new Map();
  for (const event of events.filter(isComment)) {
    const parsed = String(event.id || '').match(/:comment:(.+):(created|edited)$/);
    const immutableId = event.commentId ?? event.source?.commentId ?? event.source?.id ?? parsed?.[1];
    // Without an immutable comment identifier, matching body/author would merge distinct comments.
    const id = event.issueKey + ':' + (immutableId == null ? 'event:' + eventId(event) : 'comment:' + immutableId);
    if (!groups.has(id)) groups.set(id, {commentId:immutableId == null ? null : String(immutableId),events:[]});
    groups.get(id).events.push(event);
  }
  return [...groups.values()].map(group => {
    const ordered = [...group.events].sort(recentFirst);
    const representative = ordered[0];
    const created = [...ordered].reverse().find(event => event.kind === 'comment');
    const edits = ordered.filter(event => event.kind === 'commentEdited');
    const observed = ordered.map(event => event.observedUpdatedAt).filter(Boolean).sort((a,b)=>time(b)-time(a))[0];
    const editedAt = [edits[0] && at(edits[0]),observed].filter(Boolean).sort((a,b)=>time(b)-time(a))[0] || null;
    const createdAt = created ? at(created) : null;
    return {...representative,commentId:group.commentId,
      isEdited:!!edits.length || !!(editedAt && createdAt && time(editedAt) > time(createdAt)),
      editedAt,createdAt,evidenceIds:ordered.map(eventId),
      createdEvent:created || null,editEvents:edits};
  }).sort((a,b)=>time(b.editedAt || at(b))-time(a.editedAt || at(a)) || recentFirst(a,b));
}

function headlineFor(item, lastBoundary) {
  const rootReopened = lastBoundary.get(item.key)?.kind === 'reopened';
  const reopenedChildren = item.children.filter(child => lastBoundary.get(child.key)?.kind === 'reopened').length;
  const childDone = item.completedKeys.filter(key=>key !== item.key).length;
  const parts = [];
  if (rootReopened) parts.push('업무 재오픈 이력');
  if (reopenedChildren) parts.push('하위 업무 '+reopenedChildren+'건 재오픈 이력');
  if (lastBoundary.get(item.key)?.kind === 'completed') parts.push(kindOf(item.issue?.type) === 'epic' ? 'Epic 완료 확인' : item.children.length ? '상위 업무 완료 확인' : '업무 완료 확인');
  if (childDone) parts.push('하위 업무 '+childDone+'건 완료 확인');
  if (item.uncertain) parts.push('수집 근거 일부 미확인');
  if (parts.length) return parts.join(' · ');
  const parentCreated = item.createdKeys.includes(item.key);
  const childCreated = item.createdKeys.filter(key=>key !== item.key).length;
  if (parentCreated) parts.push('업무 생성');
  if (childCreated) parts.push('하위 업무 '+childCreated+'건 생성');
  const changes = item.events.filter(event=>event.kind === 'fieldChanged').length;
  if (changes) parts.push('변경 '+changes+'건 확인');
  if (item.comments.length) parts.push('댓글 '+item.comments.length+'건 확인');
  return parts.join(' · ') || (item.events.length ? '기간 내 활동 '+item.events.length+'건 확인' : '기간 내 확인된 활동 없음');
}

export function buildTrackingReport(run) {
  const summary = {total:0,completed:0,changed:0,review:0,quiet:0,provisional:false};
  if (!run) return {items:[],summary};
  const originalIssues = Array.isArray(run.issues) ? run.issues : Object.values(run.issues || {});
  const issues = new Map(originalIssues.filter(issue=>issue?.key).map(issue=>[issue.key,issue]));
  const treeInfo = new Map(), paths = new Map(), treeParents = new Map();
  function walk(node, labels=[], parentKey='', seen=new Set()) {
    if (!node || seen.has(node)) return;
    const key = issueKeyOf(node), label = node.name || node.summary || key;
    const nextLabels = label ? [...labels,label] : labels;
    if (key) {
      if (!treeInfo.has(key)) treeInfo.set(key,node);
      if (!paths.has(key)) paths.set(key,new Set());
      paths.get(key).add(nextLabels.join(' › '));
      if (parentKey && !treeParents.has(key)) treeParents.set(key,parentKey);
    }
    for (const child of node.children || []) walk(child,nextLabels,key || parentKey,new Set([...seen,node]));
  }
  for (const node of run.tree || []) walk(node);
  for (const [key,node] of treeInfo) {
    if (!issues.has(key)) issues.set(key,{key,summary:node.name || node.summary || key,
      type:node.kind || node.type || 'Task',contextOnly:true});
  }
  const uniqueEvents = new Map();
  for (const event of [...(run.events || []),...originalIssues.flatMap(issue=>issue.events || [])]) {
    if (event?.issueKey && !uniqueEvents.has(eventId(event))) uniqueEvents.set(eventId(event),event);
  }
  const eventsByKey = new Map();
  for (const event of uniqueEvents.values()) {
    if (!eventsByKey.has(event.issueKey)) eventsByKey.set(event.issueKey,[]);
    eventsByKey.get(event.issueKey).push(event);
    if (!issues.has(event.issueKey)) issues.set(event.issueKey,{key:event.issueKey,summary:event.issueKey,type:'Task'});
  }
  for (const values of eventsByKey.values()) values.sort(recentFirst);
  const isSub = key => issues.get(key)?.isSubtask === true || kindOf(issues.get(key)?.type || treeInfo.get(key)?.kind).startsWith('sub');
  const parentOf = key => issues.get(key)?.parentKey || treeParents.get(key) || '';
  const uncertainRoots = new Set();
  function taskOf(key) {
    let current = key;
    const seen = new Set();
    while (isSub(current) && parentOf(current)) {
      if (seen.has(current)) {
        current = [...seen].sort()[0];
        uncertainRoots.add(current);
        break;
      }
      seen.add(current);
      const parent = parentOf(current);
      if (kindOf(issues.get(parent)?.type || treeInfo.get(parent)?.kind) === 'epic') {
        uncertainRoots.add(current);
        break;
      }
      current = parent;
    }
    if (!issues.has(current)) {
      issues.set(current,{key:current,summary:current,type:'Task',contextOnly:true});
      uncertainRoots.add(current);
    }
    return current;
  }
  const groups = new Map();
  const targets = [...issues.keys()].filter(key=>issues.get(key).contextOnly !== true || eventsByKey.has(key));
  for (const key of targets) {
    const root = taskOf(key);
    if (!groups.has(root)) groups.set(root,new Set([root]));
    groups.get(root).add(key);
  }
  const missing = run.coverage?.missing || [];
  const items = [...groups].map(([key,groupKeys]) => {
    const issue = issues.get(key);
    const keys = [key,...[...groupKeys].filter(value=>value !== key).sort()];
    const events = keys.flatMap(value=>eventsByKey.get(value) || []).sort(recentFirst);
    const lastBoundary = new Map();
    for (const event of events) if (isBoundary(event) && !lastBoundary.has(event.issueKey)) lastBoundary.set(event.issueKey,event);
    const targetIssues = keys.map(value=>issues.get(value)).filter(value=>value?.contextOnly !== true);
    const missingSource = targetIssues.some(value=>!value?.coverage || value.coverage.complete === false ||
      value.coverage.historyComplete !== true || value.coverage.commentsComplete !== true);
    const matchingGap = missing.some(gap=>groupKeys.has(gap.issueKey || gap.key) &&
      !(issues.get(gap.issueKey || gap.key)?.contextOnly === true && ['history','comments'].includes(gap.source)));
    const uncertain = missingSource || matchingGap || uncertainRoots.has(key);
    const unresolvedReopen = [...lastBoundary.values()].some(event=>event.kind === 'reopened');
    const category = uncertain || unresolvedReopen ? 'review' : lastBoundary.get(key)?.kind === 'completed' ? 'completed' : events.length ? 'changed' : 'quiet';
    const ownTransition = (eventsByKey.get(key) || []).find(isStatus);
    const children = keys.filter(value=>value !== key).map(value=>({key:value,title:issues.get(value)?.summary || value,
      issue:issues.get(value),events:eventsByKey.get(value) || []}));
    const item = {key,rowId:'tracking-report:'+key,title:issue?.summary || key,issue,keys,
      paths:[...new Set(paths.get(key) || keys.flatMap(value=>[...(paths.get(value) || [])]))].sort(),events,eventIds:events.map(eventId),
      completedKeys:distinct(events.filter(event=>event.kind === 'completed').map(event=>event.issueKey)),
      createdKeys:distinct(events.filter(event=>event.kind === 'created').map(event=>event.issueKey)),
      reopenedKeys:distinct(events.filter(event=>event.kind === 'reopened').map(event=>event.issueKey)),
      comments:commentsFrom(events),latestAt:events.length ? at(events[0]) : null,uncertain,category,
      transition:ownTransition && (ownTransition.from != null || ownTransition.to != null) ?
        {from:ownTransition.from ?? '',to:ownTransition.to ?? ''} : null,children};
    item.headline = headlineFor(item,lastBoundary);
    return item;
  }).sort((a,b)=>rank[a.category]-rank[b.category] || time(b.latestAt)-time(a.latestAt) || a.key.localeCompare(b.key));
  summary.total = items.length;
  for (const item of items) summary[item.category]++;
  summary.provisional = run.status !== 'complete' || run.coverage?.complete !== true ||
    !!run.summary?.provisional || !!missing.length || items.some(item=>item.uncertain);
  return {items,summary};
}

function excerpt(value, limit=240) {
  const text = String(value || '').replace(/\s+/g,' ').trim();
  return text.length > limit ? text.slice(0,limit)+'…' : text;
}

export function reportText(report, {request={},asOf='',keys} = {}) {
  const selected = keys == null ? null : new Set(typeof keys === 'string' ? [keys] : keys);
  const items = (report?.items || []).filter(item=>!selected || selected.has(item.key) || selected.has(item.rowId));
  const start = request.startDate || '미지정', end = request.endDate || start;
  const people = request.people?.length ? request.people.join(', ') : '전체 사람';
  const modules = request.modules?.length ? request.modules.join(', ') : '전체 모듈';
  const lines = ['업무 활동 보고','기간: '+start+(end !== start ? '~'+end : '')+' (한국 시간)',
    '대상: '+people+' · '+modules];
  if (asOf) lines.push('수집 기준: '+asOf);
  if (report?.summary?.provisional) lines.push('잠정 보고: 일부 근거 미확인');
  if (!items.length) lines.push('','선택한 업무 없음');
  for (const item of items) {
    const titles = new Map([[item.key,item.title],...item.children.map(child=>[child.key,child.title])]);
    const label = key => ticketLabel(key,titles.get(key));
    lines.push('',label(item.key),'- '+item.headline);
    if (item.paths.length) lines.push('- 업무 경로: '+item.paths.join(' | '));
    for (const child of item.children.filter(value=>item.completedKeys.includes(value.key))) {
      let note = '';
      if (item.reopenedKeys.includes(child.key)) {
        const latest = child.events.filter(isBoundary).sort(recentFirst)[0];
        note = latest?.kind === 'reopened' ? ' · 마지막 확인 이력: 재오픈' :
          latest?.kind === 'completed' ? ' · 재오픈 후 완료 확인' : ' · 재오픈 이력도 있음';
      }
      lines.push('- 하위 완료 이력: '+label(child.key)+note);
    }
    const sourceKeys = distinct(item.events.map(event=>event.issueKey));
    lines.push('- '+(sourceKeys.length ? '근거 티켓: '+sourceKeys.map(label).join('; ') : '확인 대상: '+item.keys.map(label).join('; ')));
    for (const comment of item.comments.slice(0,2)) {
      const author = comment.actorName || comment.author?.name || comment.actorId || '작성자 미확인';
      const when = comment.editedAt || at(comment) || '시점 미확인';
      const note = comment.isEdited ? '수정된 댓글 · 마지막 관측 본문' : '댓글 · 관측 본문';
      lines.push('  > '+label(comment.issueKey)+' '+author+' · '+when+' · '+note+': '+excerpt(eventText(comment)));
    }
  }
  return lines.join('\n');
}
