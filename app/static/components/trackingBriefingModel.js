// Saved-run briefing. Hierarchy identity is always a node ID; facts require collected issues.
import { buildTrackingScope } from './trackingScopeModel.js';
import { eventText, issueKeyOf, normalize, readableTicketTitle, kstToday } from './trackingModel.js';

const DAY = 86400000;
const kindOf = node => String(node?.kind || node?.type || 'task').toLowerCase().replace(/[-_\s]/g,'');
const issueList = run => Array.isArray(run?.issues) ? run.issues : Object.values(run?.issues || {});
const at = event => event.date || event.at || '';
const stamp = value => Number.isFinite(Date.parse(value)) ? Date.parse(value) : 0;
const recent = (a,b) => stamp(at(b))-stamp(at(a)) || String(a.id || '').localeCompare(String(b.id || ''));
const unique = values => [...new Set(values)];
export function sortBriefingSections(sections,grouping='hierarchy') {
  const ranks={progress:0,todo:1,unknown:2,structure:3,done:4};
  const state=(a,b)=>(ranks[a.state]??9)-(ranks[b.state]??9);
  const path=(a,b)=>String(a.path || '').localeCompare(String(b.path || ''),'ko');
  return [...sections].sort((a,b)=>grouping==='status' ? state(a,b)||path(a,b) : path(a,b)||state(a,b));
}
function categoryMatches(item,category,issues) {
  return category==='all' || (category==='pinned' ? item.containsPinned : category==='changed' ? item.events.some(e=>['comment','commentEdited','fieldChanged'].includes(e.kind)) : category==='completed' ? item.events.some(e=>e.kind==='completed') : category==='new' ? item.events.some(e=>e.kind==='created') : category==='subCompleted' ? item.events.some(e=>e.kind==='completed' && isSubtask(issues.get(e.issueKey))) : item.category===category);
}
const isSubtask=issue=>issue?.isSubtask===true || kindOf(issue).startsWith('sub');
function issueUncertain(issue) { return !issue?.coverage || issue.coverage.complete===false || issue.coverage.historyComplete!==true || issue.coverage.commentsComplete!==true; }
export const BRIEFING_CATEGORIES = [{key:'all',label:'전체'},{key:'pinned',label:'주요 업무'},
  {key:'completed',label:'완료 전환'},{key:'changed',label:'변경·논의'},
  {key:'review',label:'재확인'},{key:'quiet',label:'활동 없음'}];
export const BRIEFING_STATES = {
  progress:{label:'진행 중',icon:'progress'},todo:{label:'대기',icon:'clock'},done:{label:'완료',icon:'check'},
  unknown:{label:'상태 미확인',icon:'help'},structure:{label:'계층별 업무',icon:'layers'},
};
export const COMPLETION_LABELS = {period:'이번 기간 완료',after:'기간 이후 완료',unknown:'완료 시점 미확인',before:'기간 이전 완료 · 참고'};
export function briefingState(issue) {
  const category = String(issue?.statusCategory?.key || issue?.statusCategory || '').toLowerCase();
  return category === 'done' ? 'done' : category === 'indeterminate' ? 'progress' : category === 'new' ? 'todo' : 'unknown';
}
export function completionProvenance(issue, request={}, asOf='') {
  const fact = issue?.currentCompletion;
  if (briefingState(issue) !== 'done' || fact?.source !== 'completeStatusHistory' || !stamp(fact.at))
    return {kind:'unknown',label:COMPLETION_LABELS.unknown,at:null};
  const lower=stamp((request.startDate || '')+'T00:00:00+09:00');
  const end=stamp((request.endDate || '')+'T00:00:00+09:00');
  const upper=Math.min(end ? end+DAY : Infinity,stamp(asOf) || Infinity),when=stamp(fact.at);
  const kind=lower && when<lower ? 'before' : when>=upper ? 'after' : 'period';
  return {kind,label:COMPLETION_LABELS[kind],at:fact.at};
}
function eventsFor(run, issues) {
  const request = run?.request || {}, seen = new Set();
  return [...(run?.events || []),...issueList(run).flatMap(issue=>issue.events || [])].filter(event=>{
    if (!event?.issueKey || !issues.has(event.issueKey) || issues.get(event.issueKey).contextOnly === true) return false;
    const date = stamp(at(event)) ? kstToday(new Date(at(event))) : '';
    if (date && ((request.startDate && date < request.startDate) || (request.endDate && date > request.endDate))) return false;
    const id = event.id || JSON.stringify([event.issueKey,event.kind,at(event),event.field,event.from,event.to,event.bodyText || event.body]);
    if (seen.has(id)) return false;
    seen.add(id);return true;
  }).sort(recent);
}
function flatten(tree) {
  const rows=[];
  const walk=(node,path=[])=>{
    if (!node || path.includes(node)) return;
    rows.push({node,path});
    for (const child of node.children || []) walk(child,[...path,node]);
  };
  for (const node of tree || []) walk(node);
  return rows;
}
function keysIn(node, issues) {
  return unique(flatten([node]).map(row=>issueKeyOf(row.node)).filter(key=>issues.has(key) && issues.get(key).contextOnly !== true));
}
const isSub = issue => issue?.isSubtask === true || kindOf(issue).startsWith('sub');
const uncertain = issue => !issue?.coverage || issue.coverage.complete === false ||
  issue.coverage.historyComplete !== true || issue.coverage.commentsComplete !== true;
function categoryFor(events, keys, issues) {
  const boundaries=new Map();
  for(const event of [...events].reverse()) if(['completed','reopened'].includes(event.kind)) boundaries.set(event.issueKey,event.kind);
  if(keys.some(key=>uncertain(issues.get(key))) || [...boundaries.values()].includes('reopened')) return 'review';
  if(events.some(event=>event.kind === 'completed')) return 'completed';
  return events.length ? 'changed' : 'quiet';
}
function sparkBuckets(events, request) {
  const end=stamp((request.endDate || request.startDate || '')+'T00:00:00Z');
  const start=stamp((request.startDate || request.endDate || '')+'T00:00:00Z');
  const days=Math.max(1,Math.round((end-start)/DAY)+1);
  const buckets=Array.from({length:7},(_,index)=>{
    const low=Math.floor(index*days/7), high=Math.floor((index+1)*days/7)-1;
    const date=value=>new Date(start+value*DAY).toISOString().slice(0,10);
    return {index,start:date(low),end:date(Math.max(low,high)),empty:high<low,events:[]};
  });
  for(const event of events){
    if(!stamp(at(event))) continue;
    const offset=Math.round((stamp(kstToday(new Date(at(event)))+'T00:00:00Z')-start)/DAY);
    if(offset>=0 && offset<days) buckets[Math.min(6,Math.ceil((offset+1)*7/days)-1)].events.push(event);
  }
  return buckets;
}
function metricsFor(keys, events, issues) {
  const count = predicate => new Set(events.filter(predicate).map(event=>event.issueKey)).size;
  const completed=count(event=>event.kind==='completed' && !isSub(issues.get(event.issueKey)));
  const subCompleted=count(event=>event.kind==='completed' && isSub(issues.get(event.issueKey)));
  const latest=new Map();
  for(const event of [...events].reverse()) if(['completed','reopened'].includes(event.kind)) latest.set(event.issueKey,event.kind);
  const review=keys.filter(key=>uncertain(issues.get(key)) || latest.get(key)==='reopened').length;
  return {completed,subCompleted,created:count(event=>event.kind==='created'),
    changed:count(event=>['fieldChanged','comment','commentEdited'].includes(event.kind)),review,
    tasks:keys.filter(key=>!isSub(issues.get(key))).length,subtasks:keys.filter(key=>isSub(issues.get(key))).length,events:events.length};
}
export function buildTrackingBriefing(run, options={}) {
  const {scopeId='',query='',highlight=null,matchedOnly=false,category='all',childStatus='all',pins=[],grouping='hierarchy'}=options;
  const scope=buildTrackingScope(run,scopeId), scoped=scope.run || run || {};
  const issues=new Map(issueList(scoped).map(issue=>[issue.key,issue]));
  const events=eventsFor(scoped,issues), allRows=flatten(scoped.tree), q=normalize(query), active=!!q || !!highlight?.value;
  let roots=scope.node ? scope.node.children || [] : scoped.tree || [];
  if(!scopeId && roots.length===1 && kindOf(roots[0])==='module') roots=roots[0].children || [];
  const treeKeys=new Set(allRows.map(row=>issueKeyOf(row.node)));
  const unlinked=issueList(scoped).filter(issue=>issue.contextOnly!==true && !treeKeys.has(issue.key));
  if(!scopeId && unlinked.length) roots=[...roots,...unlinked.map(issue=>({id:'tracking-report:'+issue.key,issueKey:issue.key,name:issue.summary,kind:issue.type,children:[]}))];
  const searchKeys=new Set();
  for(const {node,path} of [...allRows,...flatten(roots)]){
    const key=issueKeyOf(node),issue=issues.get(key);
    if(!issue || issue.contextOnly===true) continue;
    const text=[key,node.name,node.summary,issue.summary,issue.assigneeName,issue.assignee?.name,...path.map(n=>n.name || n.summary)].join(' ');
    const highlightMatch=!highlight?.value || (highlight.kind==='person' ? issue.assigneeId===highlight.value || events.some(e=>e.issueKey===key && e.actorId===highlight.value) :
      key===highlight.value || node.id===highlight.value || path.some(n=>issueKeyOf(n)===highlight.value || n.id===highlight.value));
    if((!q || normalize(text).includes(q)) && highlightMatch) searchKeys.add(key);
  }
  const decorate=node=>{
    const keys=keysIn(node,issues),key=issueKeyOf(node),issue=issues.get(key), ownKnown=issue && issue.contextOnly!==true;
    const matchedKeys=active ? keys.filter(k=>searchKeys.has(k)) : keys;
    const selectedKeys=active && matchedKeys.length ? matchedKeys : keys;
    const records=events.filter(e=>selectedKeys.includes(e.issueKey));
    const ownEvents=records.filter(e=>e.issueKey===key);
    const changes=ownEvents.filter(e=>['completed','reopened'].includes(e.kind) || (e.kind==='fieldChanged' && e.field==='status')).sort((a,b)=>-recent(a,b));
    const completedKeys=unique(records.filter(e=>e.kind==='completed' && e.issueKey!==key).map(e=>e.issueKey));
    const completedChildren=completedKeys.map(k=>({key:k,title:readableTicketTitle(issues.get(k)?.summary,k),event:records.find(e=>e.issueKey===k && e.kind==='completed')}));
    const commentIds=new Set();
    const comments=records.filter(e=>['comment','commentEdited'].includes(e.kind)).filter(e=>{
      const identity=e.issueKey+':'+(e.commentId || e.source?.commentId || e.id);
      if(commentIds.has(identity))return false;commentIds.add(identity);return true;
    });
    const comment=comments[0];
    const itemCategory=categoryFor(records,selectedKeys,issues);
    const completion=completionProvenance(issue,scoped.request,scoped.asOf);
    let headline=changes.length ? changes.map(e=>(e.from || '미확인')+' → '+(e.to || '미확인')).join(' · ') : '';
    if(!headline && completedChildren.length) headline='하위 업무 '+completedChildren.length+'건 완료 전환';
    if(!headline && records.some(e=>e.kind==='created')) headline='기간 내 '+new Set(records.filter(e=>e.kind==='created').map(e=>e.issueKey)).size+'개 업무 생성';
    if(!headline && comment) headline='댓글로 진척 보고';
    if(!headline) headline=itemCategory==='review' ? '근거 일부 미확인' : records.length ? '기간 내 활동 '+records.length+'건 확인' : '기간 내 확인된 활동 없음';
    const path=allRows.find(row=>row.node.id===node.id)?.path || [];
    return {id:node.id || 'tracking-report:'+key,key,kind:kindOf(node),node,issue,keys,matchedKeys,events:records,ownEvents,
      title:readableTicketTitle(issue?.summary || node.name || node.summary,key),path:path.map(n=>n.name || n.summary).filter(Boolean).join(' › '),
      state:ownKnown ? briefingState(issue) : ['module','wbs','group','epic'].includes(kindOf(node)) ? 'structure' : 'unknown',
      currentLabel:ownKnown ? issue.status || BRIEFING_STATES[briefingState(issue)].label : '조회된 하위 기준',
      category:itemCategory,uncertain:selectedKeys.some(k=>uncertain(issues.get(k))),completion,headline,changes,comment,comments,completedChildren,
      context:active && !matchedKeys.length,matched:active && !!matchedKeys.length,
      pinned:pins.includes(key || node.id),containsPinned:keys.some(k=>pins.includes(k)) || pins.includes(key || node.id),
      children:(node.children || []).filter(n=>keysIn(n,issues).length).map(n=>({id:n.id,key:issueKeyOf(n),title:issues.get(issueKeyOf(n))?.summary || n.name || n.summary,kind:kindOf(n)})),
      owner:issue?.assigneeName || issue?.assignee?.name || (ownKnown ? '담당 미확인' : '하위 업무 집계')};
  };
  const candidates=roots.filter(node=>keysIn(node,issues).length).map(decorate).filter(item=>!matchedOnly || !active || item.matched);
  const categories=BRIEFING_CATEGORIES.map(entry=>({...entry,count:candidates.filter(item=>categoryMatches(item,entry.key,issues)).length}));
  const categoryItems=candidates.filter(item=>categoryMatches(item,category,issues));
  const inventory={total:categoryItems.length,states:Object.entries(BRIEFING_STATES).map(([key,value])=>({key,...value,count:categoryItems.filter(item=>item.state===key).length})).filter(state=>state.count)};
  const items=categoryItems.filter(item=>childStatus==='all' || item.state===childStatus);
  const selectedKey=scope.node ? issueKeyOf(scope.node) : '';
  const selectedIssue=issues.get(selectedKey),selectedRecords=events.filter(event=>event.issueKey===selectedKey);
  const selectedBoundary=selectedRecords.find(event=>['completed','reopened'].includes(event.kind));
  const selectedCategory=issueUncertain(selectedIssue) || selectedBoundary?.kind==='reopened' ? 'review' : selectedRecords.some(event=>event.kind==='completed') ? 'completed' : selectedRecords.length ? 'changed' : 'quiet';
  const includeSelected=selectedKey && selectedIssue?.contextOnly!==true && issues.has(selectedKey) &&
    categoryMatches({events:selectedRecords,category:selectedCategory,containsPinned:pins.includes(selectedKey)},category,issues) &&
    (childStatus==='all' || briefingState(selectedIssue)===childStatus) && (!active || searchKeys.has(selectedKey));
  const visibleKeys=unique([...items.flatMap(item=>active ? item.matchedKeys : item.keys),...(includeSelected?[selectedKey]:[])]);
  const visibleEvents=events.filter(event=>visibleKeys.includes(event.issueKey));
  const level=scope.node ? kindOf(scope.node)==='wbs' ? 'Epic·그룹' : ['epic','group'].includes(kindOf(scope.node)) ? 'Task' : kindOf(scope.node)==='module' ? 'WBS Task' : 'Subtask' : roots.some(n=>kindOf(n)==='module') ? '모듈' : roots.some(n=>kindOf(n)==='wbs') ? 'WBS Task' : '업무';
  const heading=state=>state==='progress' ? '진행 중인 '+level : state==='todo' ? '대기 중인 '+level : BRIEFING_STATES[state].label+' '+level;
  const open=items.filter(item=>item.state!=='done'), sections=[];
  const groups=new Map();
  for(const item of open){
    const path=scopeId ? '' : item.path;
    const groupKey=grouping==='status' ? item.state+'|'+path : path+'|'+item.state;
    const title=path ? grouping==='status' ? heading(item.state)+' · '+path : path+' › '+heading(item.state) : heading(item.state);
    if(!groups.has(groupKey)) groups.set(groupKey,{key:'open:'+groupKey,title,path,state:item.state,completion:'',items:[]});
    groups.get(groupKey).items.push(item);
  }
  sections.push(...sortBriefingSections([...groups.values()],grouping));
  for(const completion of ['period','after','unknown','before']){
    const groupItems=items.filter(item=>item.state==='done' && item.completion.kind===completion).sort((a,b)=>stamp(b.completion.at)-stamp(a.completion.at));
    if(groupItems.length) sections.push({key:'completed:'+completion,title:COMPLETION_LABELS[completion],state:'done',completion,items:groupItems});
  }
  const partial=scoped.status!=='complete' || scoped.coverage?.complete!==true || !!scoped.coverage?.missing?.length || visibleKeys.some(key=>uncertain(issues.get(key)));
  const title=issues.get(selectedKey)?.summary || scope.node?.name || scope.node?.summary || (run?.tree?.length===1 && kindOf(run.tree[0])==='module' ? run.tree[0].name : '전체 업무');
  const displayedKeys=new Set([...items.flatMap(item=>item.keys),...(includeSelected?[selectedKey]:[])]),countedKeys=new Set(visibleKeys);
  function hierarchyNode(node){
    const children=(node.children || []).map(hierarchyNode).filter(Boolean),key=issueKeyOf(node);
    return displayedKeys.has(key) || children.length ? {...node,children} : null;
  }
  const hierarchyRun={...scoped,tree:[...(scoped.tree || []),...roots.filter(node=>node.id?.startsWith('tracking-report:') && !treeKeys.has(issueKeyOf(node)))].map(hierarchyNode).filter(Boolean),
    issues:issueList(scoped).filter(issue=>displayedKeys.has(issue.key)).map(issue=>countedKeys.has(issue.key)?issue:{...issue,contextOnly:true}),events:visibleEvents};
  const people=new Map();
  for(const issue of issueList(scoped)) if(issue.assigneeId) people.set(issue.assigneeId,{id:issue.assigneeId,name:issue.assigneeName || issue.assigneeId});
  for(const event of events) if(event.actorId && !people.has(event.actorId)) people.set(event.actorId,{id:event.actorId,name:event.actorName || event.actorId});
  return {run:scoped,scope,options,title,level,items,sections,categories,inventory,events:visibleEvents,allEvents:events,
    hierarchyRun,
    selectedIssue:includeSelected ? issues.get(selectedKey) : null,selectedEvents:includeSelected ? visibleEvents.filter(event=>event.issueKey===selectedKey) : [],
    metrics:metricsFor(visibleKeys,visibleEvents,issues),spark:sparkBuckets(visibleEvents,scoped.request || {}),partial,
    matchCount:items.filter(item=>!item.context).length,ticketNames:new Map(issueList(scoped).map(issue=>[issue.key,issue.summary])),
    people:[...people.values()],
    observedAt:issueList(scoped).map(issue=>issue.fetchedAt).filter(Boolean).sort().at(-1) || '',
    highlights:[...new Map(allRows.filter(row=>issueKeyOf(row.node) || row.node.virtual).map(({node,path})=>{
      const title=issues.get(issueKeyOf(node))?.summary || node.name || node.summary;
      const context=path.map(ancestor=>ancestor.name || ancestor.summary || issueKeyOf(ancestor)).filter(Boolean).join(' › ');
      return [issueKeyOf(node) || node.id,{id:issueKeyOf(node) || node.id,title:node.virtual || node.epicUnassigned ? title+' · '+(context || '전체') : title,kind:kindOf(node)==='epic' ? 'epic' : 'task'}];
    })).values()]};
}
export function briefingReportText(view) {
  const {run,options={},metrics}=view,request=run.request || {};
  const lines=['업무 활동 보고','기간: '+(request.startDate || '미지정')+' ~ '+(request.endDate || '미지정')+' (한국 시간)',
    '선택 범위: '+view.title,'기간 내 담당·활동 대상: '+(request.people?.join(', ') || '전체'),
    '표시 조건: '+(BRIEFING_CATEGORIES.find(c=>c.key===options.category)?.label || '전체')+' / 현재 상태 '+(BRIEFING_STATES[options.childStatus]?.label || '전체')+' / 검색 '+(options.query || '없음')+' / '+(options.matchedOnly ? '일치만 보기' : '맥락 유지'),
    '조회 기간 상한: '+(run.asOf || '미확인')+' / 현재 상태 관측: '+(view.observedAt || '시각 미확인')+' / '+(view.partial ? '일부 확인 결과 · 전체 합계 미확정' : '조회 완료'),
    '집계: 업무 '+metrics.tasks+'개 · 하위 작업 '+metrics.subtasks+'개 · 활동 '+metrics.events+'건',''];
  if(view.selectedIssue){
    lines.push('선택 항목 자체 활동: '+view.title+' ('+view.selectedIssue.key+')',
      '담당: '+(view.selectedIssue.assigneeName || '담당 미확인'),
      '근거: '+(view.selectedEvents.map(event=>at(event)+' '+eventText(event)+' '+event.issueKey+'#'+event.id).join(' | ') || '확인된 활동 없음'),'');
  }
  for(const item of view.items){
    lines.push('■ '+item.title+(item.key ? ' ('+item.key+')' : '')+(item.context ? ' [상위 맥락 · 집계 제외]' : ''),
      '경로: '+(item.path || view.title),'담당: '+item.owner,'기간 변화: '+item.headline,'조회 당시 상태: '+item.currentLabel);
    if(item.state==='done') lines.push('완료 시점: '+item.completion.label+(item.completion.at ? ' '+item.completion.at : ''));
    for(const child of item.completedChildren) lines.push('하위 완료: '+child.title+' ('+child.key+')');
    if(item.comment) lines.push('최근 논의: '+eventText(item.comment));
    lines.push('확인 사항: '+(item.uncertain ? '수집 근거 일부 미확인' : '수집된 근거 기준'),
      '근거: '+(item.events.map(e=>at(e)+' '+eventText(e)+' '+e.issueKey+'#'+e.id).join(' | ') || '확인된 활동 없음'),'');
  }
  lines.push('활동 없음은 미작업·지연을 뜻하지 않습니다. 하위 완료는 상위 전체 완료와 별도로 집계합니다.');
  return lines.join('\n');
}
