/* Reviewable design prototype. All records below are illustrative, with no API writes. */
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const at=(d,h,m=0)=>Date.parse(`2026-11-${d}T${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:00+09:00`);
const fmt=t=>new Date(t).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false});
const full=t=>new Date(t).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'long',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon={start:'<path d="m4 2 6 4-6 4z"/>',done:'<path d="m2 6 3 3 5-6"/>',reopen:'<path d="M2 5a4 4 0 1 1 1 4M2 2v3h3"/>',note:'<path d="M2 2h8v6H5l-3 2z"/>',new:'<path d="M6 2v8M2 6h8"/>'};
const svg=k=>`<svg viewBox="0 0 12 12" aria-hidden="true">${icon[k]||icon.start}</svg>`;
function ev(day,h,m,kind,title,body,state){return {at:at(day,h,m),kind,title,body,state};}
const parent={key:'DL-10000',title:'주문 이벤트 중복 제거',person:'최민서',current:'진행 중',events:[
 ev(11,9,10,'new','요청 접수','주문 이벤트 중복 처리 요구사항 등록.','new'),ev(13,10,0,'start','개발 착수','업무 키와 이벤트 순서로 중복을 판별하는 로직 개발.','progress'),
 ev(16,12,10,'done','검증 완료','정상 경로와 재시도 경로 검증 완료. 운영 인수 전 예외 경로 확인 예정.','done'),
 ev(16,14,20,'reopen','예외 발견·재오픈','운영 인수 중 순서가 역전된 이벤트에서 중복 발생. 해당 경로 보완을 위해 재오픈.','reopen'),
 ev(16,16,40,'start','수정 착수','역순 이벤트 재현 완료. 체크포인트 비교 로직 수정 착수.','progress')],comments:[
 ev(12,11,0,'note','업무 키 기준 합의','중복 판정은 주문 키와 이벤트 버전으로 구분.'),ev(15,15,20,'note','정상 경로 대사 완료','정상 데이터 12개 파티션 건수 일치.'),
 ev(16,9,30,'note','검증 범위 공유','정상·재시도·역순 이벤트 경로를 순차 점검.'),
 ...[[10,32,'재시도 검증 시작'],[10,36,'누락 없음 확인'],[10,41,'지연 이벤트 재현'],[10,48,'검증 로그 첨부']].map(([h,m,t])=>ev(16,h,m,'note',t,'체크포인트와 처리 결과 비교. 확인한 로그를 검증 기록에 첨부.')),
 ev(16,15,10,'note','원인·보완 방향 공유','역순 이벤트를 처리할 때 이전 버전 기록이 먼저 반영되는 경로 확인.'),ev(16,23,10,'note','야간 처리 확인','야간 배치에서 수정 경로 확인. 처리 로그를 다음 인수 점검에 공유.') ]};
const children=[
 {key:'DL-10001',title:'원천·목적지 대사',person:'이준서',current:'완료',events:[ev(11,10,0,'start','대사 착수','업무 키별 건수·금액 비교 시작.','progress'),ev(13,16,0,'done','대사 완료','원천·목적지 건수와 금액 일치 확인.','done')],comments:[]},
 {key:'DL-10002',title:'재처리 검증',person:'이다은',current:'완료',events:[ev(14,9,30,'start','재처리 점검','실패 파티션 복구 검증 시작.','progress'),ev(16,11,40,'done','재처리 검증 완료','정상·재시도 경로 검증을 완료하고 결과 전달.','done')],comments:[ev(16,10,40,'note','재시도 3회 정상','재시도 이후 누락·중복 없음.')]},
 {key:'DL-10003',title:'모니터링 지표',person:'최민서',current:'완료',events:[ev(15,10,0,'start','지표 추가','처리 지연과 중복 제거 건수 지표 추가.','progress'),ev(16,15,30,'done','지표 반영 완료','운영 대시보드 지연·중복 지표 확인.','done')],comments:[]},
 {key:'DL-10004',title:'운영 인수 점검',person:'이준서',current:'진행 중',events:[ev(16,10,0,'start','인수 점검 시작','운영 담당자와 정상·예외 경로 인수 점검.','progress')],comments:[ev(16,14,10,'note','역순 이벤트 이상','역순 이벤트에서 중복 발생. 부모 Task에 확인 요청.')]},
 {key:'DL-10005',title:'복구 가이드',person:'이다은',current:'대기',events:[],comments:[]}
];
const epicChildren=[parent,
 {key:'DL-10012',title:'스키마 변경 감지',person:'이다은',current:'완료',events:[ev(11,11,0,'start','감지 규칙 개발','컬럼 추가·타입 변경 감지 구현.','progress'),ev(14,15,0,'done','감지 검증 완료','스키마 변경 유형 8개 검증 완료.','done')],comments:[ev(16,10,40,'note','운영 인수 완료','알림 수신 대상과 가이드 인계.')]},
 {key:'DL-10024',title:'실패 파티션 재처리',person:'이준서',current:'완료',events:[ev(12,10,0,'start','복구 구현','선택 파티션 재처리 흐름 구현.','progress'),ev(16,11,40,'done','복구 검증 완료','중단 시점부터 복구해 누락 없음 확인.','done')],comments:[]},
 {key:'DL-10036',title:'수집 지연 알림 개선',person:'최민서',current:'완료',events:[ev(11,9,20,'start','알림 튜닝','지연 구간과 수신 규칙 보완.','progress'),ev(16,15,30,'done','알림 적용 완료','중복 알림 억제와 임계값 반영.','done')],comments:[]},
 {key:'DL-10048',title:'체크포인트 자동 복구',person:'이다은',current:'진행 중',events:[ev(15,11,0,'start','복구 개발 착수','체크포인트 복구 재현 시작.','progress')],comments:[ev(16,13,20,'note','복구 시나리오 추가','프로세스 중단 이후 복구 경로 확인 중.')]},
 {key:'DL-10060',title:'운영 가이드 정리',person:'이준서',current:'대기',events:[],comments:[]}
];
const epic={key:'DL-101',title:'실시간 처리 안정화',rowLabel:'이 Epic',person:'최민서',current:'진행 중',events:[ev(12,9,30,'start','Epic 진행 시작','수집 안정화 작업의 개발·검증 착수.','progress')],comments:[ev(16,17,20,'note','인수 범위 재확인','중복 제거의 재오픈 경로를 포함해 운영 인수 범위 재확인.')]};
let mode='task',scale='day',day=16,comments=true,fullDay=false,expandedChildren=false,registry=[],activeTrigger=null;
const color=s=>({progress:'blue',done:'green',reopen:'amber',new:'idle'}[s]||'unknown');
const label=s=>({progress:'진행 중',done:'완료',reopen:'재오픈',new:'대기'}[s]||'근거 미확인');
function range(){return scale==='day'?(fullDay?[at(day,0),at(day+1,0)]:[at(day,9),at(day,18)]):[at(11,0),at(18,0)];}
function x(t){const [a,b]=range();return Math.max(0,Math.min(100,(t-a)/(b-a)*100));}
function remember(e,row){const id=registry.length;registry.push({...e,row});return id;}
function dot(e,row,extra='',offset=0,base=27){
 const id=remember(e,row),type=e.kind==='note'?'note':e.kind;
 return (offset?`<i class="marker-stem" data-stem="${id}" style="left:${x(e.at)}%;top:${Math.min(base,base+offset)}px;height:${Math.abs(offset)}px"></i>`:'')+`<button class="event-dot ${type} ${extra}" style="--x:${x(e.at)}%;${offset?'top:'+(base+offset)+'px':''}" data-event="${id}" data-offset="${offset}" aria-expanded="false" aria-label="${esc(full(e.at)+' '+row.title+' '+e.title)}">${svg(e.kind)}${e.members?e.members.length:''}</button>`;
}
function groups(items){
 const [a,b]=range(),threshold=(b-a)*.035,out=[];
 for(const e of items.filter(e=>e.at>=a&&e.at<=b).sort((a,b)=>a.at-b.at)){
  const last=out.at(-1);if(last&&e.at-last.at<threshold)last.members.push(e);else out.push({...e,members:[e]});
 }
 return out.map(e=>e.members.length>1?{...e,title:`댓글 ${e.members.length}건`}:e.members[0]);
}
function combinedAxis(owner,rows){
 const [a,b]=range(),sources=[owner,...rows];
 const changes=sources.flatMap(row=>row.events.filter(e=>e.at>=a&&e.at<=b).map(event=>({event,row})));
 const notes=comments?sources.flatMap(row=>groups(row.comments).map(event=>({event,row}))):[];
 const items=[...changes,...notes].sort((a,b)=>a.event.at-b.event.at||a.row.key.localeCompare(b.row.key));
 const occupied=[],width=$('#ticks').clientWidth||900;
 const marks=items.map(item=>{const {event,row}=item,pos=x(event.at)/100*width;
  let level=occupied.findIndex(last=>pos-last>=34);if(level<0)level=occupied.length;occupied[level]=pos;
  const offset=level?(level%2?-1:1)*Math.ceil(level/2)*22:0;item.markerId=registry.length;
  return dot(event,row,(event.members?'cluster ':'')+(row===owner?'own-marker':'child-marker'),offset,122);
 }).join('');
 let callouts='';
 for(const {event,row,markerId} of changes){
  const id=remember(event,row),source=row===owner?(mode==='task'?'이 Task':'이 Epic'):row.title;
  const short=row===owner?event.title:({done:'완료',reopen:'재오픈',start:'착수',new:'생성'}[event.kind]||event.title);
  callouts+=`<button class="bubble summary-callout ${event.kind==='done'?'done':event.kind==='reopen'?'reopen':'blue-top'}" data-event="${id}" data-anchor="${markerId}" title="${esc(row.key+' · '+source+' · '+short)}" aria-label="${esc(full(event.at)+' '+row.title+' '+event.title+' 근거')}"><span class="summary-source">${esc(source)}</span><span class="callout-key" hidden>${row.key}</span><span class="callout-separator" aria-hidden="true">·</span><strong>${esc(short)}</strong></button>`;
 }
 const noteCount=notes.reduce((sum,{event})=>sum+(event.members?.length||1),0);
 return `<div class="lane combined" data-change-count="${changes.length}" data-comment-count="${noteCount}"><div class="lane-label"><div class="lane-title">자체 + 하위 ${rows.length}개</div><div class="lane-meta">주요 변화 ${changes.length} · 댓글 ${noteCount}</div><p class="summary-label-note">같은 시간축에서<br>주요 변화 함께 보기</p></div><div class="lane-body" style="--rail-y:122px;height:220px"><div class="rail"></div><svg class="callout-leaders" aria-hidden="true"></svg>${marks}${callouts}${items.length?'':'<span class="summary-empty">이 구간에 확인된 활동 없음</span>'}</div></div>`;
}
function layoutCallouts(){
 const body=$('.combined .lane-body');if(!body)return;
 const width=body.clientWidth,padding=4,gap=10,rowHeight=24,rowGap=10;
 const occupied=[];
 const markers=[...body.querySelectorAll('.event-dot')].sort((a,b)=>registry[Number(a.dataset.event)].at-registry[Number(b.dataset.event)].at);
 body.querySelectorAll('.marker-stem').forEach(stem=>stem.remove());
 for(const marker of markers){const pos=x(registry[Number(marker.dataset.event)].at)/100*width;let level=occupied.findIndex(last=>pos-last>=34);if(level<0)level=occupied.length;occupied[level]=pos;
  const offset=level?(level%2?-1:1)*Math.ceil(level/2)*22:0;marker.dataset.offset=String(offset);
  if(offset)body.insertAdjacentHTML('beforeend',`<i class="marker-stem" data-stem="${marker.dataset.event}" style="left:${x(registry[Number(marker.dataset.event)].at)}%;height:${Math.abs(offset)}px"></i>`);
 }
 const buttons=[...body.querySelectorAll('.summary-callout')];
 const sourceCounts=new Map();for(const b of buttons){const title=registry[Number(b.dataset.event)].row.title;sourceCounts.set(title,(sourceCounts.get(title)||new Set()).add(registry[Number(b.dataset.event)].row.key));}
 for(const b of buttons){const source=b.querySelector('.summary-source'),e=registry[Number(b.dataset.event)];b.querySelector('.callout-key').hidden=source.scrollWidth<=source.clientWidth&&sourceCounts.get(e.row.title).size<2;}
 const tracks=[[],[]],used=[0,0];
 const labels=buttons.map(button=>({button,width:button.getBoundingClientRect().width,anchor:x(registry[Number(button.dataset.event)].at)/100*width})).sort((a,b)=>a.anchor-b.anchor);
 for(const item of labels){let index=-1;for(let i=0;i<tracks.length;i++){if(used[i]+(tracks[i].length?gap:0)+item.width<=width-padding*2&&(index<0||used[i]<used[index]))index=i;}if(index<0){index=tracks.length;tracks.push([]);used.push(0);}used[index]+=(tracks[index].length?gap:0)+item.width;tracks[index].push(item);}
 const offsets=[...body.querySelectorAll('.event-dot')].map(b=>Number(b.dataset.offset)||0),above=Math.max(0,...offsets.map(n=>-n)),below=Math.max(0,...offsets);
 const upperCount=Math.ceil(tracks.length/2),lowerCount=Math.floor(tracks.length/2),baseline=12+upperCount*(rowHeight+rowGap)+above+22;
 const lowerStart=baseline+below+24,height=lowerStart+lowerCount*(rowHeight+rowGap)+10;
 body.style.setProperty('--rail-y',baseline+'px');body.style.height=height+'px';
 body.querySelectorAll('.event-dot').forEach(b=>b.style.top=baseline+Number(b.dataset.offset)+'px');
 body.querySelectorAll('[data-stem]').forEach(stem=>{const offset=Number(body.querySelector(`[data-event="${stem.dataset.stem}"]`).dataset.offset);stem.style.top=baseline+Math.min(0,offset)+'px';});
 let paths='';
 tracks.forEach((track,index)=>{let end=padding-gap;for(const item of track){item.left=Math.max(padding,item.anchor-item.width/2,end+gap);end=item.left+item.width;}let limit=width-padding;for(const item of [...track].reverse()){item.left=Math.min(item.left,limit-item.width);limit=item.left-gap;}
  const upper=index%2===0,y=upper?12+Math.floor(index/2)*(rowHeight+rowGap):lowerStart+Math.floor(index/2)*(rowHeight+rowGap);
  for(const {button,left,width:labelWidth,anchor} of track){button.style.left=left+'px';button.style.top=y+'px';button.dataset.track=String(index);
   const marker=body.querySelector(`.event-dot[data-event="${button.dataset.anchor}"]`),my=baseline+Number(marker.dataset.offset),tipY=upper?y+rowHeight:y,bendY=upper?baseline-above-14:baseline+below+14;
   paths+=`<path data-anchor="${button.dataset.anchor}" d="M ${anchor} ${my+(upper?-8:8)} L ${anchor} ${bendY} L ${left+labelWidth/2} ${tipY}"/>`;
  }
 });
 const leaders=body.querySelector('.callout-leaders');leaders.setAttribute('viewBox',`0 0 ${width} ${height}`);leaders.innerHTML=paths;
}
function lane(row,primary=false,commentOnly=false){
 const [a,b]=range(),events=row.events.filter(e=>e.at>=a&&e.at<=b),all=row.events.filter(e=>e.at<=b).sort((a,b)=>a.at-b.at);
 const crowded=!primary&&!commentOnly&&events.some((e,i)=>i>0&&x(e.at)-x(events[i-1].at)<3);
 const baseline=primary?77:commentOnly?22:crowded?86:40;
 let state=all.filter(e=>e.at<a).at(-1)?.state||null,previous=a,segments='';
 if(!commentOnly){
  for(const e of [...events,{at:b,state:null}]){
   if(e.at>previous){const left=x(previous),right=x(e.at);segments+=`<i class="state-segment ${color(state)}" style="left:${left}%;width:${right-left}%"></i>`;
    if(primary&&right-left>13)segments+=`<span class="state-label" style="left:${(left+right)/2}%">${label(state)}</span>`;}
   previous=e.at;state=e.state;
  }
 }
 const notes=comments?groups(row.comments):[];
 let lastX=-100,stack=0;
 let marks=commentOnly?notes.map(e=>dot(e,row,e.members?'cluster':'')).join(''):events.map(e=>{const pos=x(e.at);stack=pos-lastX<3?stack+1:0;lastX=pos;return dot(e,row,'',stack?-24*(stack%3):0,baseline);}).join('');
 if(!primary&&!commentOnly)marks+=notes.map(e=>dot(e,row,e.members?'cluster':'',12-baseline,baseline)).join('');
 let bubbles='';
 if(primary&&!commentOnly){
  const selected=scale==='day'?events.slice(-3):events.filter(e=>e.kind!=='new').slice(-3);
  let last=-100;for(const e of selected){const pos=x(e.at);if(pos-last<19||pos<10||pos>90||scale==='period'&&events.some(other=>other!==e&&Math.abs(x(other.at)-pos)<3))continue;last=pos;
   const id=remember(e,row);bubbles+=`<button class="bubble ${e.kind==='done'?'done':e.kind==='reopen'?'reopen':'blue-top'}" style="--x:${pos}%" data-event="${id}" aria-label="${esc(full(e.at)+' '+e.title+' 근거')}"><span class="time">${scale==='period'?full(e.at):fmt(e.at)}</span>${esc(e.title)}</button>`;
  }
 }
 const empty=!commentOnly&&!primary&&!events.length&&!notes.length?`<span class="end-caption">${row.events.length?'기간 내 변화 없음':'기간 내 기록 없음'}</span>`:'';
 return `<div class="lane ${primary?'primary':''} ${commentOnly?'comment':''}"><div class="lane-label"><div class="lane-title">${commentOnly?'댓글·보고':primary?'이 Task':esc(row.rowLabel||row.title)}</div><div class="lane-meta">${commentOnly?'짧은 보고와 논의':`<span>${row.key}</span><span class="lane-state ${row.current==='완료'?'done':row.current==='대기'?'wait':''}">${row.current}</span>`}</div></div><div class="lane-body" style="--rail-y:${baseline}px;height:${primary?115:commentOnly?46:crowded?112:64}px"><div class="rail"></div>${segments}${marks}${bubbles}${empty}</div></div>`;
}
function render(){
 closePopover(false);registry=[];$('#evidence').hidden=true;
 const rows=mode==='task'?children:epicChildren,done=rows.filter(r=>r.current==='완료').length;
 $('#ticketType').textContent=mode==='task'?'TASK':'EPIC';$('#ticketKey').textContent=mode==='task'?'DL-10000':'DL-101';$('#ticketTitle').textContent=mode==='task'?parent.title:'실시간 처리 안정화';
 $('#breadcrumb').textContent=mode==='task'?'실시간 처리 안정화':'Epic';$('#byline').textContent=mode==='task'?'최민서 담당 · 선택 기간 중 재오픈 1회':'Task 6개 · 재오픈 업무 1개 · 상태 변화와 보고를 함께 확인';
 $('#progressLabel').textContent=mode==='task'?'Sub-task 완료':'Task 완료';$('#progressValue').innerHTML=`${done}<span>/ ${rows.length}</span>`;$('#progressFill').style.width=done/rows.length*100+'%';$('#progressNote').textContent=`현재 조회 상태 기준 · ${Math.round(done/rows.length*100)}%`;
 $$('[data-mode]').forEach(e=>{e.classList.toggle('selected',e.dataset.mode===mode);e.setAttribute('aria-pressed',String(e.dataset.mode===mode));});
 $$('[data-scale]').forEach(e=>{e.classList.toggle('selected',e.dataset.scale===scale);e.setAttribute('aria-pressed',String(e.dataset.scale===scale));});
 $('#commentsToggle').classList.toggle('selected',comments);$('#commentsToggle').setAttribute('aria-pressed',String(comments));
 $('#overview').innerHTML=Array.from({length:7},(_,i)=>{const d=i+11;const all=(mode==='task'?[parent,...rows]:[epic,...rows]).flatMap(r=>[...r.events,...r.comments]).filter(e=>e.at>=at(d,0)&&e.at<at(d+1,0));return `<button data-day="${d}" class="${scale==='day'&&day===d?'selected':''}" aria-pressed="${scale==='day'&&day===d}" aria-label="11월 ${d}일 시간 확대"><span>11.${d} ${['수','목','금','토','일','월','화'][i]}</span><div class="density" aria-hidden="true">${Array.from({length:14},(_,n)=>`<i style="height:${Math.min(9,2+all.filter(e=>Math.floor((e.at-at(d,0))/3600000/24*14)===n).length*3)}px"></i>`).join('')}</div></button>`;}).join('');
 $('#windowLabel').textContent=scale==='day'?`11월 ${day}일 ${['수','목','금','토','일','월','화'][day-11]}요일`:'11월 11일 — 17일';$('#windowTime').textContent=scale==='day'?(fullDay?'00:00 — 24:00 · KST':'09:00 — 18:00 · KST'):'전체 7일 · 실제 시간 간격';$('#prevDay').disabled=scale==='period'||day===11;$('#nextDay').disabled=scale==='period'||day===17;
 const outside=(mode==='task'?[parent,...rows]:[epic,...rows]).flatMap(r=>[...r.events,...r.comments]).filter(e=>e.at>=at(day,0)&&e.at<at(day+1,0)&&(e.at<at(day,9)||e.at>at(day,18))).length;
 $('#fullDay').hidden=scale==='period';$('#fullDay').textContent=fullDay?'09–18시 보기':outside?`24시간 · 시간 밖 ${outside}건`:'24시간 보기';$('#fullDay').setAttribute('aria-pressed',String(fullDay));
 $('#ticks').innerHTML=scale==='day'?(fullDay?Array.from({length:9},(_,i)=>`<time class="tick" style="left:${i/8*100}%">${i*3}:00</time>`).join(''):Array.from({length:10},(_,i)=>`<time class="tick" style="left:${i/9*100}%">${9+i}:00</time>`).join('')):Array.from({length:8},(_,i)=>`<time class="tick" style="left:${i/7*100}%">11.${11+i} 00시</time>`).join('');
 $('#lanes').innerHTML=combinedAxis(mode==='task'?parent:epic,rows);
 $('#childrenToggle').innerHTML=`<span class="fold-symbol">${expandedChildren?'−':'+'}</span>하위 작업별 ${expandedChildren?'접기':'펼치기'} <small>${rows.length}</small>`;$('#childrenToggle').setAttribute('aria-expanded',String(expandedChildren));
 $('#childLanes').hidden=!expandedChildren;$('#childLanes').innerHTML=expandedChildren?`<div class="group-heading"><span>${mode==='task'?'Sub-task':'하위 Task'}별 상태 흐름</span><span>위 통합 축과 같은 사건 · 선은 각 업무의 상태</span></div>`+rows.map(r=>lane(r)).join(''):'';
 $$('.lane-body').forEach(e=>e.style.backgroundSize=scale==='day'?(fullDay?'112.5% 100%':'100% 100%'):'128.5714% 100%');
 layoutCallouts();
 $('#chartHint').textContent=scale==='day'?'점·말풍선을 눌러 시각과 근거 확인':'날짜를 눌러 시간 확대 · 간격은 실제 경과 시간';
 $('#resultItems').innerHTML=mode==='task'?'<div class="result-item"><span class="result-icon">✓</span><div><b>하위 작업 3개 완료</b><p>대사 · 재처리 검증 · 모니터링 지표</p></div></div><div class="result-item"><span class="result-icon" style="color:var(--amber)">↶</span><div><b>검증 후 재오픈</b><p>11.16 14:20 · 역순 이벤트 예외 발견</p></div></div><div class="result-item"><span class="result-icon" style="color:var(--blue)">◷</span><div><b>예외 경로 수정 착수</b><p>11.16 16:40 · 운영 인수 점검 진행</p></div></div>':'<div class="result-item"><span class="result-icon">✓</span><div><b>Task 3개 완료</b><p>스키마 감지 · 재처리 · 지연 알림</p></div></div><div class="result-item"><span class="result-icon" style="color:var(--amber)">↶</span><div><b>중복 제거 Task 재확인</b><p>검증 이후 예외 발견 · 수정 착수</p></div></div><div class="result-item"><span class="result-icon" style="color:var(--blue)">◷</span><div><b>현재 진행 2개 · 대기 1개</b><p>활동량과 완료 비율을 구분해 표시</p></div></div>';
}
function closePopover(focus=true){$('#popover').hidden=true;if(activeTrigger){activeTrigger.setAttribute('aria-expanded','false');if(focus&&activeTrigger.isConnected)activeTrigger.focus();}activeTrigger=null;}
function openEvent(id,trigger){
 const e=registry[id];if(!e)return;closePopover(false);activeTrigger=trigger;trigger.setAttribute('aria-expanded','true');const p=$('#popover');
 p.innerHTML=`<div class="popover-top"><span>${esc(e.members?full(e.at)+' — '+fmt(e.members.at(-1).at):full(e.at))}</span><button id="closePopover" aria-label="말풍선 닫기">×</button></div><h3 id="popoverTitle">${esc(e.title)}</h3>${e.members?e.members.map(item=>`<div class="cluster-entry"><time>${fmt(item.at)}</time>${esc(item.title)}</div>`).join(''):`<p>${esc(e.body)}</p>`}<div class="popover-source">${esc(e.row.key+' · '+e.row.title)}<br>${esc(e.row.person)} · 예시 근거</div><button class="evidence-link" id="showEvidence">근거 펼치기 ↗</button>`;
 p.hidden=false;const rect=trigger.getBoundingClientRect();p.style.left=Math.max(12,Math.min(innerWidth-332,rect.left+rect.width/2-160))+'px';p.style.top=Math.max(12,Math.min(innerHeight-p.offsetHeight-12,rect.bottom+12))+'px';$('#closePopover').onclick=()=>closePopover();$('#closePopover').focus({preventScroll:true});
 $('#showEvidence').onclick=()=>{const evidence=$('#evidence');evidence.innerHTML=`<button id="closeEvidence">접기</button><h3>${esc(e.row.title)} · ${esc(e.title)}</h3><p>${esc(e.row.person)} · ${esc(e.row.key)} · 시안용 예시 근거</p>${(e.members||[e]).map(item=>`<blockquote><b>${esc(full(item.at)+' · '+item.title)}</b><br>${esc(item.body)}</blockquote>`).join('')}`;evidence.hidden=false;closePopover(false);$('#closeEvidence').onclick=()=>{evidence.hidden=true;};evidence.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth',block:'nearest'});};
}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(b?.dataset.mode){mode=b.dataset.mode;expandedChildren=false;render();}else if(b?.dataset.scale){scale=b.dataset.scale;render();}else if(b?.dataset.day){day=Number(b.dataset.day);scale='day';render();}else if(b?.dataset.event!==undefined)openEvent(Number(b.dataset.event),b);else if(!e.target.closest('#popover'))closePopover(false);});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closePopover();});
$('#prevDay').onclick=()=>{day--;render();};$('#nextDay').onclick=()=>{day++;render();};$('#commentsToggle').onclick=()=>{comments=!comments;render();};
$('#fullDay').onclick=()=>{fullDay=!fullDay;render();};
$('#childrenToggle').onclick=()=>{expandedChildren=!expandedChildren;render();};
$('#theme').onclick=()=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'light';$('#theme').setAttribute('aria-label',dark?'밝은 테마로 보기':'어두운 테마로 보기');};
function highlightConnection(target){
 $$('.is-related').forEach(e=>e.classList.remove('is-related'));
 const button=target?.closest?.('.combined .event-dot,.summary-callout');if(!button)return;
 const anchor=button.dataset.anchor||button.dataset.event;
 $$('.combined [data-anchor="'+anchor+'"],.combined .event-dot[data-event="'+anchor+'"]').forEach(e=>e.classList.add('is-related'));
}
document.addEventListener('pointerover',e=>highlightConnection(e.target));
document.addEventListener('pointerout',e=>highlightConnection(e.relatedTarget));
document.addEventListener('focusin',e=>highlightConnection(e.target));
document.addEventListener('focusout',e=>highlightConnection(e.relatedTarget));
window.addEventListener('resize',()=>{closePopover(false);layoutCallouts();});render();
