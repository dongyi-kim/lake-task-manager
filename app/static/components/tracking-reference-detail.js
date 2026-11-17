import TrackingComment from './tracking-comment.js';
import TrackingTicketTimeline from './tracking-ticket-timeline.js';
import { groupActivityEvents } from './tracking-feed.js';
import { eventText, EVENT_LABELS, kstToday, trackingRows, readableTicketTitle } from './trackingModel.js';

const GROUPING_KEY='ltm.tracking.feed.grouping';
const groupingOf=value=>['ticket','date-ticket'].includes(value)?value:'none';
const comment=event=>['comment','commentEdited'].includes(event.kind);
const recentFirst=(a,b)=>(Date.parse(b.date)||0)-(Date.parse(a.date)||0)||String(a.id).localeCompare(String(b.id));
export function referenceEvidenceSummary(events) {
  const ordered=[...events].sort(recentFirst), changes=ordered.filter(event=>!comment(event));
  const counts=[['completed','완료 전환'],['reopened','재오픈'],['created','생성'],['fieldChanged','변경']]
    .map(([kind,label])=>({kind,label,count:ordered.filter(event=>event.kind===kind).length})).filter(item=>item.count);
  const comments=ordered.filter(comment),latest=comments[0];
  return {counts,latest,latestChange:changes[0],commentCount:comments.length,
    text:latest?eventText(latest):changes[0]?eventText(changes[0]):'선택 기간에 확인된 활동이 없습니다.'};
}
export default {
  name:'TrackingReferenceDetail',components:{TrackingComment,TrackingTicketTimeline},
  props:{row:Object,run:Object,events:{type:Array,default:()=>[]},matchKeys:{type:Array,default:()=>[]},hasMatch:Boolean,selectedEventId:String,leaf:Boolean},
  emits:['close','scope','ticket','focus'],
  data(){let grouping='none';try{grouping=groupingOf(localStorage.getItem(GROUPING_KEY));}catch(_){}return {grouping,onlyMatches:false};},
  computed:{
    issue(){return this.row?.issue||null;},
    timelineIssue(){const key=this.row?.key;return {...(this.issue||(this.run?.issues||[]).find(issue=>issue.key===key)||{}),key};},
    showTimeline(){return !!this.row?.key&&!this.row.virtual&&!['module','wbs','epic','group'].includes(String(this.row.kind||this.timelineIssue.type||'').toLowerCase().replace(/[-_\s]/g,''));},
    rows(){return trackingRows(this.run);},
    breadcrumbs(){return (this.row?.path||[]).map(node=>({id:node.id,title:node.title||node.name||node.summary||node.key}));},
    matchedEvents(){const keys=new Set(this.matchKeys);return this.hasMatch?this.events.filter(event=>keys.has(event.issueKey)):this.events;},
    shownEvents(){return this.onlyMatches&&this.hasMatch?this.matchedEvents:this.events;},
    sections(){return groupActivityEvents(this.shownEvents,this.grouping);},
    summary(){return referenceEvidenceSummary(this.events);},
    latestChangeTitle(){const event=this.summary.latestChange;return event&&event.issueKey!==this.row?.key?this.title(event.issueKey):'';},
    period(){const request=this.run?.request||{};return [request.startDate,request.endDate].filter(Boolean).join(' — ');},
    uncertain(){const keys=this.row?.keys||new Set(this.events.map(event=>event.issueKey));return (this.run?.issues||[]).some(issue=>keys.has(issue.key)&&!issue.contextOnly&&(issue.coverage?.historyComplete!==true||issue.coverage?.commentsComplete!==true));},
  },
  watch:{'row.id'(){this.onlyMatches=false;},selectedEventId(id){
    if(this.events.some(event=>event.id===id&&!this.matchKeys.includes(event.issueKey)))this.onlyMatches=false;
  }},
  methods:{
    eventText,
    isComment:comment,
    setGrouping(value){this.grouping=groupingOf(value);try{localStorage.setItem(GROUPING_KEY,this.grouping);}catch(_){}},
    title(key){return readableTicketTitle((this.run?.issues||[]).find(issue=>issue.key===key)?.summary||this.rows.find(row=>row.key===key)?.title,key);},
    eventLabel(event){return EVENT_LABELS[event.kind]||'활동';},
    dateLabel(value){return String(value||'').replaceAll('-','.');},
    timestamp(value,onlyTime=false){
      if(!Number.isFinite(Date.parse(value)))return '시각 미확인';
      const date=new Date(value),time=date.toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false});
      return (onlyTime?'':this.dateLabel(kstToday(date))+' ')+time;
    },
    statusTone(){return this.issue?.statusCategory==='done'?'is-done':this.issue?.statusCategory==='new'?'is-wait':'';},
    fieldLabel(field){return ({status:'상태',assignee:'담당자',duedate:'마감일',summary:'제목',description:'설명',priority:'우선순위',labels:'라벨',resolution:'해결'})[field]||field||'변경';},
  },
  template:`
    <aside v-if="row" class="tp-evidence" :class="{'tp-evidence-leaf':leaf}" aria-label="상세 근거">
      <header class="tp-evidence-top"><div class="tp-evidence-kicker"><span>{{ leaf?'현재 선택 · ':'' }}{{ row.key?'선택한 업무 근거 · '+row.key:'선택 범위의 활동 근거' }}</span><button type="button" class="tp-close-btn" :class="{'tp-leaf-back':leaf}" @click="$emit('close')" :aria-label="leaf?'상위 범위로 돌아가기':'상세 근거 닫기'">{{ leaf?'↑ 상위로':'×' }}</button></div>
        <nav class="tp-evidence-breadcrumb" aria-label="근거 상위 경로"><button type="button" @click="$emit('scope','')">전체 업무</button><template v-for="crumb in breadcrumbs" :key="crumb.id"><span aria-hidden="true">›</span><button type="button" @click="$emit('scope',crumb.id)">{{ crumb.title }}</button></template></nav>
        <h2>{{ row.title }}</h2><div class="tp-evidence-path">{{ leaf?'하위 작업이 없는 종단 업무 · 상세 근거를 바로 보고 있습니다.':row.key?'위 선택 경로에 속한 업무의 자체 활동과 하위 활동입니다.':'선택 범위에 포함된 업무의 활동입니다.' }}</div>
        <div class="tp-evidence-meta"><span v-if="issue" class="tp-status-badge" :class="statusTone()">{{ issue.status||'상태 미확인' }}</span><span v-if="issue">{{ issue.assigneeName||issue.assignee?.name||'담당자 미확인' }}</span><span v-if="issue">조회 당시 상태 · {{ issue.fetchedAt?timestamp(issue.fetchedAt):'수집 시각 미확인' }}</span><button v-if="row.key" type="button" class="tp-source-button" @click="$emit('ticket',row.key)">원본 티켓 ↗</button></div>
      </header>
      <div class="tp-evidence-scroll">
        <TrackingTicketTimeline v-if="showTimeline" :key="row.key" :issue="timelineIssue" :events="events" :request="run?.request||{}" :selected-event-id="selectedEventId" @focus="$emit('focus',$event)"/>
        <section class="tp-fact-block"><h3>Δ 선택 기간 {{ period }}</h3><div v-if="summary.counts.length" class="tp-evidence-deltas"><span v-for="fact in summary.counts" :key="fact.kind" :class="'tp-delta-'+fact.kind">{{ fact.label }} <b>{{ fact.count }}</b>건</span></div><p v-if="summary.latestChange" class="tp-delta-change"><span v-if="latestChangeTitle" class="tp-delta-child-title">{{ latestChangeTitle }} · </span>{{ eventText(summary.latestChange) }}</p><div v-if="summary.latest" class="tp-delta-comment"><span>최근 댓글 보고</span><p>{{ summary.text }}</p><button type="button" class="tp-source-button" @click="$emit('focus',summary.latest.id)">댓글 근거 보기 →</button></div><p v-else-if="!events.length">{{ summary.text }}</p><div class="tp-source-note">업무 전체 근거 {{ events.length }}건<span v-if="hasMatch"> · 현재 표시와 일치 {{ matchedEvents.length }}건</span><span v-if="summary.commentCount"> · 댓글·수정 {{ summary.commentCount }}건</span></div><p v-if="uncertain" class="tp-evidence-uncertain">이력·댓글 확인이 끝나지 않은 항목이 있습니다. 근거 수는 확보한 범위입니다.</p></section>
        <div class="tp-evidence-tabs"><h3>{{ onlyMatches&&hasMatch?'현재 표시와 일치':'업무 전체 근거' }} <small>{{ shownEvents.length }}건</small></h3><label><span class="tp-visually-hidden">활동 그룹핑</span><select aria-label="활동 그룹핑" :value="grouping" @change="setGrouping($event.target.value)"><option value="none">그룹 없이 시간순</option><option value="ticket">기간 전체 · 티켓별</option><option value="date-ticket">날짜 → 티켓별</option></select></label></div>
        <div v-if="hasMatch" class="tp-evidence-filter" role="group" aria-label="근거 표시 범위"><button type="button" :aria-pressed="!onlyMatches" @click="onlyMatches=false">업무 전체 {{ events.length }}</button><button type="button" :aria-pressed="onlyMatches" @click="onlyMatches=true">일치 근거 {{ matchedEvents.length }}</button></div>
        <section v-for="section in sections" :key="section.key" class="tp-event-section"><h4 v-if="section.date" class="tp-event-date">{{ dateLabel(section.date) }}</h4><section v-for="ticket in section.tickets" :key="ticket.key"><div v-if="ticket.issueKey" class="tp-event-divider"><button type="button" :title="ticket.issueKey" @click="$emit('ticket',ticket.issueKey)">{{ title(ticket.issueKey) }}</button><small>{{ ticket.issueKey }} · {{ ticket.events.length }}건</small></div>
          <article v-for="event in ticket.events" :key="event.id" :id="'tracking-event-'+event.id" class="tp-event" :class="{'is-selected':selectedEventId===event.id,'is-match':hasMatch&&matchKeys.includes(event.issueKey)}" :data-event-id="event.id">
            <div class="tp-event-head"><time :datetime="event.date">{{ timestamp(event.date,!!section.date) }}</time><b>{{ event.actorName||event.actorId||'작성자 미확인' }}</b><span class="tp-event-kind">{{ eventLabel(event) }}</span><span v-if="hasMatch&&matchKeys.includes(event.issueKey)" class="tp-event-matched">일치</span></div>
            <button type="button" class="tp-event-title" :title="event.issueKey" @click="$emit('ticket',event.issueKey)">{{ title(event.issueKey) }}</button>
            <TrackingComment v-if="isComment(event)" :event="event"/>
            <div v-else-if="event.from!=null||event.to!=null" class="tp-event-change"><span>{{ fieldLabel(event.field) }}</span> <del>{{ event.from??'미지정' }}</del> <span aria-label="에서">→</span> <strong>{{ event.to??'미지정' }}</strong></div><p v-else class="tp-event-text">{{ eventText(event) }}</p>
            <div class="tp-event-source"><button type="button" class="tp-source-button" @click="$emit('ticket',event.issueKey)">원본 {{ isComment(event)?'댓글 티켓':'티켓' }} ↗</button><button type="button" class="tp-source-button" @click="$emit('focus',event.id)">이 근거 선택</button><span v-if="event.kind==='commentEdited'">관측한 최신 수정 본문</span></div>
          </article>
        </section></section>
        <div v-if="!shownEvents.length" class="tp-evidence-empty"><strong>{{ onlyMatches?'현재 표시 조건에 일치하는 근거가 없습니다.':uncertain?'아직 확인하지 못한 기록이 있습니다.':'선택 기간에 확인된 활동이 없습니다.' }}</strong><p>활동 없음은 미작업이나 지연을 뜻하지 않습니다.</p></div>
      </div><footer class="tp-evidence-foot">시간 추적·worklog ID·Resolved → Closed는 제외합니다.<br>댓글은 관측한 최신 본문이며 과거 수정본과 다를 수 있습니다.</footer>
    </aside>`,
};
