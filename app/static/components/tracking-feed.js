import TrackingComment from './tracking-comment.js';
import { eventText, EVENT_LABELS, kstToday, readableTicketTitle } from './trackingModel.js';

const GROUPING_KEY = 'ltm.tracking.feed.grouping';
const modeOf = value => ['ticket','date-ticket'].includes(value) ? value : 'none';
const eventTime = event => Number.isFinite(Date.parse(event.date)) ? Date.parse(event.date) : -Infinity;
function buckets(events, keyOf) {
  const groups = new Map();
  for (const event of events) {
    const key = keyOf(event);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(event);
  }
  return [...groups];
}
export function groupActivityEvents(events, mode) {
  const sorted = [...events].sort((a,b) => eventTime(b)-eventTime(a) || String(a.id).localeCompare(String(b.id)));
  if (!sorted.length) return [];
  const byTicket = rows => buckets(rows,e=>e.issueKey || '').map(([issueKey,events])=>({key:issueKey,issueKey,events}));
  if (modeOf(mode) === 'none') return [{key:'all',date:'',tickets:[{key:'all',issueKey:'',events:sorted}]}];
  if (mode === 'ticket') return [{key:'all',date:'',tickets:byTicket(sorted)}];
  return buckets(sorted,e=>Number.isFinite(eventTime(e)) ? kstToday(new Date(e.date)) : '날짜 미확인')
    .map(([date,events])=>({key:date,date,tickets:byTicket(events)}));
}
export default {
  name:'TrackingFeed',
  components:{TrackingComment},
  props:{events:{type:Array,default:()=>[]},ticketNames:{type:Map,required:true},selectedId:String,
    matchKeys:{type:Array,default:()=>[]}},
  emits:['select'],
  data() {
    let grouping = 'none';
    try { grouping = modeOf(localStorage.getItem(GROUPING_KEY)); } catch (_) { /* Session preference remains usable. */ }
    return {grouping};
  },
  computed:{sections() { return groupActivityEvents(this.events,this.grouping); }},
  methods:{
    eventText,
    setGrouping(value) {
      this.grouping = modeOf(value);
      try { localStorage.setItem(GROUPING_KEY,this.grouping); } catch (_) { /* Session preference remains usable. */ }
    },
    title(key) { return readableTicketTitle(this.ticketNames.get(key),key); },
    eventLabel(event) { return EVENT_LABELS[event.kind] || '활동'; },
    dateLabel(date) { return date.replaceAll('-','.'); },
    timestamp(event,dateGrouped) {
      if (!Number.isFinite(eventTime(event))) return '시각 미확인';
      const date = new Date(event.date);
      const time = date.toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false});
      return (dateGrouped ? '' : this.dateLabel(kstToday(date))+' ') + time;
    },
  },
  template:`
    <div class="tr-feed">
      <div class="tr-feed-toolbar"><label>그룹핑 <select :value="grouping" @change="setGrouping($event.target.value)" aria-label="날짜별 활동 그룹핑"><option value="none">그룹핑 안함</option><option value="ticket">티켓별로 보기</option><option value="date-ticket">날짜 내 티켓 묶기</option></select></label><span>최신 활동 순 · 한국 시간</span></div>
      <section v-for="section in sections" :key="section.key" class="tr-feed-section">
        <h3 v-if="section.date" class="tr-feed-date">{{ dateLabel(section.date) }}</h3>
        <section v-for="ticket in section.tickets" :key="ticket.key" class="tr-feed-ticket-group" :class="{'is-grouped':!!ticket.issueKey}">
          <h4 v-if="ticket.issueKey" class="tr-feed-ticket-heading"><button @click="$emit('select',ticket.events[0])"><strong>{{ title(ticket.issueKey) }}</strong><small>{{ ticket.issueKey }}</small></button><span>{{ ticket.events.length }}건</span></h4>
          <article v-for="event in ticket.events" :key="event.id" :data-event-id="event.id" :class="['tr-feed-event',{'tr-feed-match':matchKeys.includes(event.issueKey),'tr-feed-selected':selectedId === event.id}]">
            <time :datetime="event.date">{{ timestamp(event,!!section.date) }}</time><span class="tr-event-kind" :class="'tr-event-'+event.kind">{{ eventLabel(event) }}</span>
            <div class="tr-feed-content"><button class="tr-feed-ticket" @click="$emit('select',event)" :aria-label="title(event.issueKey)+' ('+event.issueKey+') · 활동 상세'"><template v-if="!ticket.issueKey"><strong>{{ title(event.issueKey) }}</strong><small>{{ event.issueKey }}</small></template><span v-else class="tr-muted">활동 상세</span></button>
              <TrackingComment v-if="['comment','commentEdited'].includes(event.kind)" :event="event" /><span v-else class="tr-feed-body">{{ eventText(event) }}</span>
            </div><span class="tr-muted">{{ event.actorName || event.actorId || '작성자 미확인' }}</span>
          </article>
        </section>
      </section><div v-if="!sections.length" class="tr-empty">표시할 기간 내 활동이 없습니다.</div>
    </div>`,
};
