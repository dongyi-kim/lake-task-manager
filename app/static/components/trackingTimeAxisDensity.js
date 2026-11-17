// Every summary retains the saved evidence and its exact timestamps in members.
const DAY=86400000, HOUR=3600000, KST=9*HOUR;
const order=(a,b)=>a.at-b.at||String(a.id).localeCompare(String(b.id));
const identity=item=>item.uid||JSON.stringify([item.issueKey||'',item.id]);

function semantic(item) {
  if(item.kind==='completed')return 'done';
  if(item.kind==='reopened')return 'reopen';
  if(item.kind==='created')return 'new';
  if(item.comment||['comment','commentEdited'].includes(item.kind))return 'comment';
  const field=String(item.field||item.event?.field||'').toLowerCase();
  return field==='status'?'progress':'change';
}

function display(members) {
  members=[...members].sort(order);
  const first=members[0], startAt=first.at, endAt=members.at(-1).at;
  const sources=new Set(members.map(item=>item.issueKey||item.source||''));
  const tones=new Set(members.map(semantic)), tone=tones.size===1?[...tones][0]:'mixed';
  const labels={done:'완료',reopen:'재오픈',new:'생성',comment:'댓글',progress:'상태',change:'변화',mixed:'변화'};
  const isCluster=members.length>1, multiple=sources.size>1;
  const short=isCluster?`${labels[tone]} ${members.length}건`:first.short||labels[tone];
  return {...first,uid:isCluster?'cluster:'+JSON.stringify(members.map(identity).sort()):identity(first),
    members,startAt,endAt,at:isCluster?(startAt+endAt)/2:first.at,isCluster,
    sourceCount:sources.size,issueKey:multiple?'':first.issueKey,
    own:members.every(item=>item.own),hasOwn:members.some(item=>item.own),
    hasChildren:members.some(item=>!item.own),
    title:multiple?`${sources.size}개 작업`:first.title,
    source:multiple?`${sources.size}개 작업`:first.source,
    tone,short,comment:tone==='comment',kind:isCluster&&tone==='mixed'?'mixed':first.kind};
}

function overflow(items,range,width) {
  const ends=[-Infinity,-Infinity,-Infinity];
  for(let i=0;i<items.length;i++) {
    const x=(items[i].at-range.start)/(range.end-range.start)*width;
    const level=ends.findIndex(end=>x-end>=48);
    if(level<0)return i;
    ends[level]=x;
  }
  return -1;
}

function bucketSize(range,width) {
  const span=range.end-range.start, slots=Math.max(1,Math.floor(width/96));
  if(span>=2*DAY)return Math.max(1,Math.ceil(span/DAY/slots))*DAY;
  return ([.5,1,2,3,6,12,24,48].find(hours=>hours*HOUR>=span/slots)||48)*HOUR;
}

export function compressTimeAxis(items,range,width) {
  width=Math.max(1,width);
  const seen=new Set(), originals=[], initial=[];
  function take(item) {
    if(item.members?.length)return item.members.flatMap(take);
    const uid=identity(item);
    if(seen.has(uid)||!Number.isFinite(item.at)||item.at<range.start||item.at>=range.end)return [];
    seen.add(uid);
    const original={...item,uid};
    originals.push(original);
    return [original];
  }
  for(const item of items) {
    const members=take(item);
    if(members.length)initial.push(display(members));
  }
  let result=initial.sort(order);
  // Sparse own/child events stay separate. Only density triggers semantic buckets.
  if(overflow(result,range,width)>=0) {
    const size=bucketSize(range,width), buckets=new Map();
    for(const item of originals) {
      const key=semantic(item)+':'+Math.floor((item.at+KST)/size);
      if(!buckets.has(key))buckets.set(key,[]);
      buckets.get(key).push(item);
    }
    result=[...buckets.values()].map(display).sort(order);
  }
  // More than three coincident semantic groups become an explicit mixed summary.
  let conflict;
  while((conflict=overflow(result,range,width))>=0) {
    const a=result[conflict-1], b=result[conflict];
    result.splice(conflict-1,2,display([...a.members,...b.members]));
    result.sort(order);
  }
  return {items:result,totalCount:originals.length,
    groupedCount:result.reduce((count,item)=>count+(item.isCluster?item.members.length:0),0)};
}
