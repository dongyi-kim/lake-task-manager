import { buildTicketTimeline } from './trackingTicketTimelineModel.js';
import { eventText } from './trackingModel.js';

export default {
  name:'TrackingTicketTimeline',
  props:{issue:{type:Object,required:true},events:{type:Array,default:()=>[]},request:{type:Object,default:()=>({})},selectedEventId:String},
  emits:['focus'],
  data(){return {expandedDays:{}};},
  computed:{
    timeline(){return buildTicketTimeline(this.issue,this.events,this.request);},
    heading(){return this.issue.isSubtask || /subtask/i.test(String(this.issue.type||'').replace(/[-_\s]/g,''))?'이 Sub-task의 상태 변화':'이 Task의 상태 변화';},
  },
  watch:{'issue.key'(){this.expandedDays={};}},
  methods:{
    transitionLabel(item){return item.kind==='completed'?'완료':item.kind==='reopened'?'재오픈':'상태';},
    transitionText(item){return item.from!=null || item.to!=null ? (item.from ?? '이전 상태 미확인')+' → '+(item.to ?? '이후 상태 미확인') : '전환 상태 미확인';},
    commentLabel(item){return item.kind==='commentEdited'?'댓글 수정':'댓글';},
    commentTitle(item){return eventText(item.event).replace(/\s+/g,' ').trim();},
    toggleDay(date){this.expandedDays={...this.expandedDays,[date]:!this.expandedDays[date]};},
  },
  template:`
    <section class="tt-timeline" :data-issue-key="issue.key" aria-label="업무 자체 상태·댓글 타임라인">
      <div class="tt-heading"><h3>{{ heading }}</h3><span>{{ issue.key }} 자체 기록 · 한국 시간</span></div>
      <div class="tt-summary"><span><i class="tt-legend-status" aria-hidden="true"></i>상태 변화 <b>{{ timeline.transitionCount }}</b>건</span><span><i class="tt-legend-comment" aria-hidden="true"></i>댓글·수정 <b>{{ timeline.commentCount }}</b>건</span><span><i class="tt-legend-empty" aria-hidden="true"></i>빈 날짜</span></div>
      <p v-if="timeline.partial" class="tt-partial">이력·댓글 수집이 완료되지 않았습니다. 확보한 기록만 표시합니다.</p>
      <p v-if="!timeline.rangeKnown" class="tt-empty">선택 기간을 확인할 수 없습니다.</p>
      <template v-else>
        <p v-if="!timeline.transitionCount" class="tt-empty">{{ timeline.commentCount?'확인된 상태 변화가 없습니다.':'확인된 상태 변화·댓글이 없습니다.' }}</p>
        <div class="tt-scroll" tabindex="0" role="region" aria-label="날짜별 상태·댓글 시간축 · 가로 스크롤">
          <div class="tt-track" :style="{gridTemplateColumns:'repeat('+timeline.days.length+', minmax(144px, 1fr))',minWidth:timeline.days.length*144+'px'}">
            <div v-for="day in timeline.days" :key="day.date" class="tt-day" :class="{'has-status':day.transitions.length,'has-comments':day.comments.length}" :data-date="day.date">
              <time class="tt-date" :datetime="day.date">{{ day.label }}</time>
              <div v-if="day.transitions.length" class="tt-transitions" :tabindex="day.transitions.length>3?0:null" :aria-label="day.label+' 상태 변화 '+day.transitions.length+'건'">
                <button v-for="item in day.transitions" :key="item.id" type="button" class="tt-event tt-transition" :class="['tt-'+item.kind,{'is-selected':selectedEventId===item.id}]" :data-event-id="item.id" :aria-current="selectedEventId===item.id?'true':null" :aria-label="day.label+' '+item.time+' '+transitionLabel(item)+' '+transitionText(item)+' 근거 보기'" @click="$emit('focus',item.id)"><span class="tt-event-meta"><time>{{ item.time }}</time><span>{{ transitionLabel(item) }}</span></span><strong>{{ transitionText(item) }}</strong></button>
              </div>
              <small v-if="day.transitions.length>3" class="tt-overflow-note">상태 변화 {{ day.transitions.length }}건 · 세로 스크롤</small>
              <div v-if="day.comments.length" class="tt-comments">
                <button v-if="day.comments.length>2" type="button" class="tt-comment-group" :aria-expanded="!!expandedDays[day.date]" :aria-label="day.label+' 댓글·수정 '+day.comments.length+'건 '+(expandedDays[day.date]?'접기':'펼치기')" @click="toggleDay(day.date)"><span aria-hidden="true">◇</span> 댓글·수정 {{ day.comments.length }}건 <span aria-hidden="true">{{ expandedDays[day.date]?'−':'+' }}</span></button>
                <div v-if="day.comments.length<=2 || expandedDays[day.date]" class="tt-comment-list" :aria-label="day.label+' 댓글 근거'">
                  <button v-for="item in day.comments" :key="item.id" type="button" class="tt-event tt-comment" :class="{'is-selected':selectedEventId===item.id}" :data-event-id="item.id" :aria-current="selectedEventId===item.id?'true':null" :title="commentTitle(item)" :aria-label="day.label+' '+item.time+' '+commentLabel(item)+' 근거 보기'" @click="$emit('focus',item.id)"><span class="tt-event-meta"><time>{{ item.time }}</time><span>{{ commentLabel(item) }}</span></span><span class="tt-comment-excerpt">{{ commentTitle(item) }}</span></button>
                </div>
              </div>
            </div>
          </div>
        </div>
        <p class="tt-footnote">{{ timeline.start }} — {{ timeline.end }} · 날짜 간격은 1일입니다. 사건을 선택하면 아래 근거로 이동합니다.</p>
      </template>
      <p v-if="timeline.unplacedCount" class="tt-partial">시각 미확인 기록 {{ timeline.unplacedCount }}건은 시간축에 배치하지 않았습니다.</p>
    </section>`,
};
