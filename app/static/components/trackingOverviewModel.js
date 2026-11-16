// Factual overview of an already-built, saved report. No collection or status inference.
import { eventText, kstToday, readableTicketTitle, ticketLabel } from './trackingModel.js';

const at = event => event?.date || event?.at || '';
const time = value => Number.isFinite(Date.parse(value)) ? Date.parse(value) : 0;
const recentFirst = (a,b) => time(at(b))-time(at(a)) || String(a.id || '').localeCompare(String(b.id || ''));
const itemsRecentFirst = (a,b) => time(b.latestAt)-time(a.latestAt) || a.key.localeCompare(b.key);
const excerpt = value => {
  const text = String(value || '').replace(/\s+/g,' ').trim();
  return text.length > 150 ? text.slice(0,150)+'…' : text;
};
const eventsOf = item => [...(item.events || [])].sort(recentFirst);
const completedChildren = item => [...new Set(item.completedKeys || [])].filter(key=>key !== item.key);
const recentComment = item => [...(item.comments || [])]
  .sort((a,b)=>time(b.editedAt || at(b))-time(a.editedAt || at(a)) || recentFirst(a,b))[0];
const dateLabel = value => {
  if (!Number.isFinite(Date.parse(value))) return '시점 미확인';
  const date = kstToday(new Date(value));
  return Number(date.slice(5,7))+'.'+Number(date.slice(8,10));
};
const commentDetail = comment => dateLabel(comment.editedAt || at(comment))+' 최근 댓글'+
  (comment.isEdited ? ' (수정됨)' : '')+': '+excerpt(eventText(comment));

function currentState(issue) {
  const category = String(issue?.statusCategory || '').toLowerCase();
  const known = ['done','new','indeterminate','todo','inprogress'].includes(category);
  const done = known && category === 'done';
  return {known,done,label:issue?.status ? String(issue.status) : '상태 미확인'};
}

function boundaries(item) {
  const last = new Map();
  for (const event of eventsOf(item)) {
    if (['completed','reopened'].includes(event.kind) && !last.has(event.issueKey)) last.set(event.issueKey,event);
  }
  return last;
}

function unresolvedReopens(item) {
  const issues = new Map([[item.key,item.issue],...(item.children || []).map(child=>[child.key,child.issue])]);
  return [...boundaries(item).values()].filter(event=>event.kind === 'reopened' && !currentState(issues.get(event.issueKey)).done);
}

function evidenceId(item,event) {
  if (!event) return null;
  return event.id || item.eventIds?.[(item.events || []).indexOf(event)] || null;
}

/** Compact report row. Current state is the saved snapshot, never the period's final transition. */
export function trackingRowSummary(item) {
  const events = eventsOf(item), current = currentState(item.issue);
  const rootCompletion = (item.completedKeys || []).includes(item.key);
  const childCompletion = completedChildren(item);
  const childReopens = events.filter(event=>event.kind === 'reopened' && event.issueKey !== item.key);
  const reopens = unresolvedReopens(item);
  const rootBoundary = boundaries(item).get(item.key);
  const attention = !!item.uncertain || reopens.length > 0 || (rootCompletion && !current.done);
  const category = attention ? 'review' : rootCompletion ? 'completed' : events.length ? 'changed' : 'quiet';
  let headline, detail = [], evidence;
  if (rootCompletion) {
    headline = '기간 내 완료 이력 · '+(current.done ? '현재 완료' : current.known ? '현재 미완료' : '현재 상태 미확인');
    evidence = rootBoundary || events.find(event=>event.issueKey === item.key && event.kind === 'completed');
    if (rootBoundary?.kind === 'reopened') detail.push('기간 내 마지막 상태 이력: 재오픈');
    if (current.known && !current.done) detail.push('수집 시점 상태: '+current.label);
    if (childCompletion.length) {
      const titles = new Map((item.children || []).map(child=>[child.key,child.title]));
      detail.push('하위 완료 이력: '+childCompletion.slice(0,2).map(key=>ticketLabel(key,titles.get(key))).join(' · '));
    } else if ((item.comments || []).length) {
      const comment = recentComment(item);
      detail.push(commentDetail(comment));
      if (!reopens.length && rootBoundary?.kind !== 'reopened') evidence = comment;
    } else {
      const completion = events.find(event=>event.issueKey === item.key && event.kind === 'completed');
      if (completion) detail.push(dateLabel(at(completion))+' 완료 이력'+
        (completion.from != null || completion.to != null ? ' · '+eventText(completion) : ''));
    }
  } else if (childCompletion.length) {
    headline = '하위 업무 '+childCompletion.length+'건 완료 이력';
    evidence = events.find(event=>event.kind === 'completed' && event.issueKey !== item.key);
    const titles = new Map((item.children || []).map(child=>[child.key,child.title]));
    detail.push(childCompletion.slice(0,2).map(key=>ticketLabel(key,titles.get(key))).join(' · '));
  } else if (rootBoundary?.kind === 'reopened') {
    headline = '기간 내 재오픈 이력';
    evidence = rootBoundary;
    if (current.done) detail.push('수집 시점에는 완료 상태');
  } else if (childReopens.length) {
    const keys = [...new Set(childReopens.map(event=>event.issueKey))];
    const titles = new Map((item.children || []).map(child=>[child.key,child.title]));
    headline = '하위 업무 '+keys.length+'건 재오픈 이력';
    evidence = childReopens[0];
    detail.push(keys.slice(0,2).map(key=>ticketLabel(key,titles.get(key))).join(' · '));
  } else {
    const changes = events.filter(event=>event.kind === 'fieldChanged');
    const created = (item.createdKeys || []).includes(item.key);
    const childCreated = (item.createdKeys || []).filter(key=>key !== item.key).length;
    if (changes.length) {
      headline = '기간 내 변경 '+changes.length+'건 확인';
      evidence = changes[0];detail.push(eventText(evidence));
    } else if (created || childCreated) {
      headline = created ? '기간 내 업무 생성' : '하위 업무 '+childCreated+'건 생성';
      evidence = events.find(event=>event.kind === 'created');
    } else if ((item.comments || []).length) {
      headline = '기간 내 댓글 '+item.comments.length+'건 확인';
      const comment = recentComment(item);
      evidence = comment;
      detail.push(commentDetail(comment));
    } else {
      headline = events.length ? '기간 내 활동 '+events.length+'건 확인' :
        item.uncertain ? '수집 근거 일부 미확인' : '기간 내 확인된 활동 없음';
      evidence = events[0];
    }
  }
  if (reopens.length) {
    if (!detail.some(text=>text.includes('재오픈'))) detail.unshift('기간 내 마지막 이력이 재오픈인 업무 '+reopens.length+'건 · 현재 완료 미확인');
    evidence = reopens[0];
  }
  if (item.uncertain && !headline.includes('미확인')) detail.unshift('수집 근거 일부 미확인');
  const childStates = (item.children || []).map(child=>currentState(child.issue));
  const notDone = childStates.filter(state=>state.known && !state.done).length;
  const unknown = childStates.filter(state=>!state.known).length;
  const remaining = [];
  if (notDone) remaining.push('현재 미완료 '+notDone+'건');
  if (unknown) remaining.push('현재 상태 미확인 '+unknown+'건');
  const fallback = item.uncertain ? '수집 근거 일부 미확인' :
    attention ? '현재 완료 미확인' : !events.length ? '기간 내 실제 작업 여부 미확인' : '';
  return {headline,detail:detail.join(' · '),evidenceId:evidenceId(item,evidence),
    currentLabel:current.label,currentDone:current.done,currentKnown:current.known,attention,category,
    remainingLabel:remaining.length ? '조회된 하위 업무: '+remaining.join(' · ') : fallback};
}

/** Overview totals count observed evidence; provisional reports do not estimate missing activity. */
export function buildTrackingOverview(report) {
  const items = [...(report?.items || [])].sort(itemsRecentFirst);
  const rows = new Map(items.map(item=>[item.key,trackingRowSummary(item)]));
  const rootsCompleted = items.filter(item=>(item.completedKeys || []).includes(item.key));
  const currentDone = rootsCompleted.filter(item=>rows.get(item.key).currentDone).length;
  const currentNotDone = rootsCompleted.filter(item=>rows.get(item.key).currentKnown && !rows.get(item.key).currentDone).length;
  const totals = {roots:items.length,rootCompletionHistory:rootsCompleted.length,
    currentDoneAfterCompletion:currentDone,currentNotDoneAfterCompletion:currentNotDone,
    currentUnknownAfterCompletion:rootsCompleted.length-currentDone-currentNotDone,
    childCompletionHistory:new Set(items.flatMap(completedChildren)).size,
    newRoots:items.filter(item=>(item.createdKeys || []).includes(item.key)).length,
    newTickets:new Set(items.flatMap(item=>item.createdKeys || [])).size,
    fieldChanges:items.reduce((count,item)=>count+(item.events || []).filter(event=>event.kind === 'fieldChanged').length,0),
    comments:items.reduce((count,item)=>count+(item.comments || []).length,0),
    reopenedRoots:items.filter(item=>(item.reopenedKeys || []).includes(item.key)).length,
    review:items.filter(item=>rows.get(item.key).attention).length,
    provisional:!!report?.summary?.provisional || items.some(item=>item.uncertain)};
  const named = item => ({key:item.key,title:readableTicketTitle(item.title,item.key),
    headline:rows.get(item.key).headline,detail:rows.get(item.key).detail,evidenceId:rows.get(item.key).evidenceId});
  const highlights = [], selected = new Set();
  function add(kind,candidates) {
    const item = candidates.find(candidate=>!selected.has(candidate.key));
    if (!item) return;
    selected.add(item.key);highlights.push({kind,...named(item)});
  }
  const completionTime = item => Math.max(0,...eventsOf(item).filter(event=>event.issueKey === item.key && event.kind === 'completed').map(event=>time(at(event))));
  add('completed',rootsCompleted.filter(item=>rows.get(item.key).currentDone && !rows.get(item.key).attention)
    .sort((a,b)=>completionTime(b)-completionTime(a) || a.key.localeCompare(b.key)));
  add('progress',items.filter(item=>!(item.completedKeys || []).includes(item.key) && !rows.get(item.key).attention && (completedChildren(item).length ||
    (item.events || []).some(event=>event.kind === 'fieldChanged')))
    .sort((a,b)=>Number(!!completedChildren(b).length)-Number(!!completedChildren(a).length) || itemsRecentFirst(a,b)));
  const followups = items.filter(item=>rows.get(item.key).attention).slice(0,3).map(named);
  add('review',items.filter(item=>rows.get(item.key).attention));
  return {highlights,totals,followups};
}
