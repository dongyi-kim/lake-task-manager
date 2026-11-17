// Saved evidence only. Empty dates are retained so horizontal distance means days.
import { kstToday } from './trackingModel.js';

const DAY = 86400000;
const dateOnly = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') &&
  Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;
const kindOf = event => event.kind || event.type;
const isComment = event => ['comment','commentEdited'].includes(kindOf(event));
const isTransition = event => ['completed','reopened'].includes(kindOf(event)) ||
  kindOf(event) === 'fieldChanged' && String(event.field || '').trim().toLowerCase() === 'status';

export function buildTicketTimeline(issue, events = [], request = {}) {
  const start = request.startDate, end = request.endDate;
  const rangeKnown = dateOnly(start) && dateOnly(end) && start <= end;
  const days = [], byDate = new Map();
  if (rangeKnown) {
    for (let at = Date.parse(start); at <= Date.parse(end); at += DAY) {
      const date = new Date(at).toISOString().slice(0,10);
      const day = {date, label:date.slice(5).replace('-','.'), transitions:[], comments:[]};
      days.push(day); byDate.set(date,day);
    }
  }
  const seen = new Set();
  let unplacedCount = 0, transitionCount = 0, commentCount = 0;
  const own = events.filter(event => issue?.key && event.issueKey === issue.key && (isComment(event) || isTransition(event)))
    .filter(event => {
      const id = event.id || JSON.stringify([event.issueKey,kindOf(event),event.date || event.at,event.commentId,event.from,event.to]);
      if (seen.has(id)) return false;
      seen.add(id); return true;
    }).sort((a,b) => (Date.parse(a.date || a.at) || 0) - (Date.parse(b.date || b.at) || 0) ||
      String(a.id || '').localeCompare(String(b.id || '')));
  for (const event of own) {
    const instant = Date.parse(event.date || event.at);
    if (!Number.isFinite(instant)) { unplacedCount++; continue; }
    const date = kstToday(new Date(instant)), day = byDate.get(date);
    if (!day) continue;
    const kind = kindOf(event);
    const time = new Date(instant + 9*3600000).toISOString().slice(11,16);
    const item = {id:event.id, event, kind, date, time, from:event.from ?? null, to:event.to ?? null};
    if (isComment(event)) { day.comments.push(item); commentCount++; }
    else { day.transitions.push(item); transitionCount++; }
  }
  return {days, rangeKnown, start, end, transitionCount, commentCount, unplacedCount,
    partial:issue?.coverage?.historyComplete !== true || issue?.coverage?.commentsComplete !== true};
}
