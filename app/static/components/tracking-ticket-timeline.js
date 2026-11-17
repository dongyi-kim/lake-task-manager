import { buildTicketTimeline } from './trackingTicketTimelineModel.js';
import { eventText, EVENT_LABELS } from './trackingModel.js';

export default {
  name:'TrackingTicketTimeline',
  props:{issue:{type:Object,required:true},events:{type:Array,default:()=>[]},request:{type:Object,default:()=>({})},issues:{type:Array,default:()=>[]},tree:{type:Array,default:()=>[]},coverage:{type:Object,default:()=>({})},selectedEventId:String},
  emits:['focus'],
  data(){return {expandedDays:{},expandedChildDays:{}};},
  computed:{
    timeline(){return buildTicketTimeline(this.issue,this.events,this.request,{issues:this.issues,tree:this.tree,coverage:this.coverage});},
    heading(){return /^epic$/i.test(this.issue.type||'')?'이 Epic의 상태 변화':this.issue.isSubtask || /subtask/i.test(String(this.issue.type||'').replace(/[-_\s]/g,''))?'이 Sub-task의 상태 변화':'이 Task의 상태 변화';},
    childLabel(){return this.timeline.children?.length ? this.timeline.childLabel || '' : '';},
    childProgress(){return this.timeline.childProgress;},
    progressPercent(){return this.childProgress?.percent == null ? null : Math.round(this.childProgress.percent);},
  },
  watch:{'issue.key'(){this.expandedDays={};this.expandedChildDays={};}},
  methods:{
    transitionLabel(item){return item.kind==='completed'?'완료':item.kind==='reopened'?'재오픈':'상태';},
    transitionText(item){return item.from!=null || item.to!=null ? (item.from ?? '이전 상태 미확인')+' → '+(item.to ?? '이후 상태 미확인') : '전환 상태 미확인';},
    commentLabel(item){return item.kind==='commentEdited'?'댓글 수정':'댓글';},
    commentTitle(item){return eventText(item.event).replace(/\s+/g,' ').trim();},
    toggleDay(date){this.expandedDays={...this.expandedDays,[date]:!this.expandedDays[date]};},
    toggleChildDay(date){this.expandedChildDays={...this.expandedChildDays,[date]:!this.expandedChildDays[date]};},
    childChanges(day){return day.childChanges || [];},
    visibleChildChanges(day){const items=this.childChanges(day);return this.expandedChildDays[day.date]?items:items.slice(0,2);},
    childChangeLabel(item){return item.kind==='fieldChanged'?({status:'상태',assignee:'담당자',duedate:'마감일',priority:'우선순위'})[String(item.field||'').toLowerCase()] || '변경':EVENT_LABELS[item.kind] || '변경';},
    childChangeText(item){return eventText(item.event).replace(/\s+/g,' ').trim();},
  },
  template:`
    <section class="tt-timeline" :data-issue-key="issue.key" aria-label="업무 상태·댓글과 하위 주요 변화 타임라인">
      <div class="tt-heading"><h3>{{ heading }}</h3><span>{{ issue.key }} 자체 기록 · 한국 시간</span></div>
      <div class="tt-summary"><span><i class="tt-legend-status" aria-hidden="true"></i>자체 상태 변화 <b>{{ timeline.transitionCount }}</b>건</span><span><i class="tt-legend-comment" aria-hidden="true"></i>자체 댓글·수정 <b>{{ timeline.commentCount }}</b>건</span><span v-if="childLabel"><i class="tt-legend-child" aria-hidden="true"></i>하위 {{ childLabel }} 주요 변화 <b>{{ timeline.childEventCount }}</b>건</span><span><i class="tt-legend-empty" aria-hidden="true"></i>빈 날짜</span></div>
      <div v-if="childLabel && childProgress" class="tt-child-progress" :class="{'is-provisional':childProgress.partial || childProgress.unknown}">
        <div class="tt-progress-heading"><span>조회된 하위 {{ childLabel }} 완료</span><b>{{ childProgress.done }}/{{ childProgress.total }}</b><span v-if="childProgress.partial || childProgress.unknown" class="tt-progress-uncertain">{{ childProgress.unknown?'상태 미확인 '+childProgress.unknown+'건':'잠정' }}</span><span v-else-if="progressPercent!==null">{{ progressPercent }}%</span></div>
        <div v-if="progressPercent!==null" class="tt-progress-track" role="progressbar" :aria-label="'조회된 하위 '+childLabel+' 완료'" :aria-valuenow="childProgress.done" :aria-valuemin="0" :aria-valuemax="childProgress.total" :aria-valuetext="childProgress.done+' / '+childProgress.total+'건 완료'"><span :style="{width:progressPercent+'%'}"></span></div>
        <p>{{ childProgress.total?'현재 조회된 하위 작업의 상태 기준입니다.':'조회된 하위 작업이 없습니다.' }}<template v-if="childProgress.partial"> 수집이 완료되지 않아 잠정 집계입니다.</template><template v-if="childProgress.unknown"> 미확인 상태가 있어 완료율을 확정할 수 없습니다.</template></p>
      </div>
      <p v-if="timeline.partial" class="tt-partial">이력·댓글 수집이 완료되지 않았습니다. 확보한 기록만 표시합니다.</p>
      <p v-if="!timeline.rangeKnown" class="tt-empty">선택 기간을 확인할 수 없습니다.</p>
      <template v-else>
        <p v-if="!timeline.transitionCount" class="tt-empty">{{ timeline.commentCount?'확인된 상태 변화가 없습니다.':'확인된 상태 변화·댓글이 없습니다.' }}</p>
        <div class="tt-scroll" tabindex="0" role="region" aria-label="날짜별 상태·댓글과 하위 주요 변화 시간축 · 가로 스크롤">
          <div class="tt-track" :class="{'has-children':childLabel}" :style="{gridTemplateColumns:'repeat('+timeline.days.length+', minmax(144px, 1fr))',minWidth:timeline.days.length*144+'px'}">
            <div v-for="day in timeline.days" :key="day.date" class="tt-day" :class="{'has-status':day.transitions.length,'has-comments':day.comments.length,'has-child-changes':childChanges(day).length}" :data-date="day.date">
              <time class="tt-date" :datetime="day.date">{{ day.label }}</time>
              <div class="tt-own-events">
              <div v-if="childLabel" class="tt-lane-label">자체 상태·댓글</div>
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
              <div v-if="childLabel" class="tt-child-events">
                <div class="tt-lane-label">하위 {{ childLabel }} 주요 변화</div>
                <div v-if="childChanges(day).length" class="tt-child-list" :aria-label="day.label+' 하위 '+childLabel+' 주요 변화'">
                  <button v-for="item in visibleChildChanges(day)" :key="item.id" type="button" class="tt-event tt-child-event" :class="['tt-'+item.kind,{'is-selected':selectedEventId===item.id}]" :data-event-id="item.id" :aria-current="selectedEventId===item.id?'true':null" :title="item.title+' · '+childChangeText(item)" :aria-label="day.label+' '+item.time+' 하위 '+childLabel+' '+item.title+' '+item.issueKey+' '+childChangeLabel(item)+' '+childChangeText(item)+' 근거 보기'" @click="$emit('focus',item.id)"><span class="tt-event-meta"><time>{{ item.time }}</time><span>{{ childChangeLabel(item) }}</span></span><span class="tt-child-title">{{ item.title }}</span><span class="tt-child-key">{{ childLabel }} · {{ item.issueKey }}</span><strong class="tt-child-change">{{ childChangeText(item) }}</strong></button>
                </div>
                <button v-if="childChanges(day).length>2" type="button" class="tt-child-group" :aria-expanded="!!expandedChildDays[day.date]" :aria-label="day.label+' 하위 '+childLabel+' 주요 변화 '+childChanges(day).length+'건 '+(expandedChildDays[day.date]?'접기':'모두 펼치기')" @click="toggleChildDay(day.date)">{{ expandedChildDays[day.date]?'접기':'+'+(childChanges(day).length-2)+'건 더 보기' }}<span>{{ childChanges(day).length }}건</span></button>
              </div>
            </div>
          </div>
        </div>
        <p class="tt-footnote">{{ timeline.start }} — {{ timeline.end }} · 날짜 간격은 1일입니다. 사건을 선택하면 아래 근거로 이동합니다.</p>
      </template>
      <p v-if="timeline.unplacedCount" class="tt-partial">시각 미확인 기록 {{ timeline.unplacedCount }}건은 시간축에 배치하지 않았습니다.</p>
      <p v-if="timeline.childUnplacedCount" class="tt-partial">시각 미확인 하위 기록 {{ timeline.childUnplacedCount }}건은 시간축에 배치하지 않았습니다.</p>
    </section>`,
};
