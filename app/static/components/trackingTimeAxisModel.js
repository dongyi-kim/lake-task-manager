// Project only saved, scoped evidence; the existing model owns hierarchy and counts.
import { buildTicketTimeline } from './trackingTicketTimelineModel.js';
import { eventText, EVENT_LABELS } from './trackingModel.js';

const DAY=86400000, HOUR=3600000;
const midnight=date=>Date.parse(date+'T00:00:00+09:00');
export function axisRange(request={}, options={}) {
  if(options.zoom && options.date) {
    const start=midnight(options.date);
    return {start:start+(options.fullDay?0:9*HOUR),end:start+(options.fullDay?DAY:18*HOUR)};
  }
  return {start:midnight(request.startDate),end:midnight(request.endDate)+DAY};
}

export function buildTimeAxis(issue, events=[], request={}, context={}, options={}) {
  const timeline=buildTicketTimeline(issue,events,request,context);
  const range=axisRange(request,options), children=new Map(timeline.children.map(child=>[child.key,child]));
  const ownLabel=/^epic$/i.test(issue.type||'')?'이 Epic':issue.isSubtask||/sub.?task/i.test(issue.type||'')?'이 Sub-task':'이 Task';
  const convert=item=>{
    const issueKey=item.issueKey||issue.key, child=children.get(issueKey);
    const comment=['comment','commentEdited'].includes(item.kind);
    const detail=eventText(item.event).replace(/\s+/g,' ').trim();
    const field=String(item.field||item.event.field||'').toLowerCase();
    const label=item.kind==='fieldChanged'?({status:'상태',assignee:'담당자',duedate:'마감일',priority:'우선순위'}[field]||'변경'):EVENT_LABELS[item.kind]||'변경';
    const short=item.kind==='completed'?'완료':item.kind==='reopened'?'재오픈':item.kind==='created'?'생성':
      item.kind==='fieldChanged'&&field==='status'?(item.to||'상태 변경'):label;
    return {...item,uid:JSON.stringify([issueKey,item.id]),at:Date.parse(item.event.date||item.event.at),issueKey,own:!child,comment,
      source:child?child.title:ownLabel,title:child?child.title:issue.summary||issue.key,
      detail,short,label,tone:item.kind==='completed'?'done':item.kind==='reopened'?'reopen':item.kind==='created'?'new':comment?'comment':'progress'};
  };
  const all=timeline.days.flatMap(day=>[...day.transitions,...day.comments,...day.childChanges]).map(convert)
    .sort((a,b)=>a.at-b.at||String(a.id).localeCompare(String(b.id)));
  const visible=all.filter(item=>item.at>=range.start&&item.at<range.end && (options.comments!==false||!item.comment));
  const grouped=[], lastBySource=new Map();
  for(const item of visible) {
    if(!item.comment){grouped.push(item);continue;}
    const last=lastBySource.get(item.issueKey);
    // Limit the full span, rather than chaining an entire day of comments together.
    if(last&&item.at-last.at<Math.max(60000,(range.end-range.start)*.035))last.members.push(item);
    else {const group={...item,members:[item]};grouped.push(group);lastBySource.set(item.issueKey,group);}
  }
  const dayStart=midnight(options.date), dayEnd=dayStart+DAY;
  const outsideCount=options.zoom?all.filter(item=>item.at>=dayStart&&item.at<dayEnd&&
    (item.at<dayStart+9*HOUR||item.at>=dayStart+18*HOUR)&& (options.comments!==false||!item.comment)).length:0;
  const counts=new Map();for(const item of all)counts.set(item.date,(counts.get(item.date)||0)+1);
  return {timeline,range,items:grouped.sort((a,b)=>a.at-b.at||String(a.id).localeCompare(String(b.id))),outsideCount,
    days:timeline.days.map(day=>({date:day.date,label:day.label,count:counts.get(day.date)||0}))};
}
