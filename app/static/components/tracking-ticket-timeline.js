import { buildTimeAxis } from './trackingTimeAxisModel.js';
import TrackingTimeAxis from './tracking-time-axis.js';

export default {
  name:'TrackingTicketTimeline',
  components:{TrackingTimeAxis},
  props:{issue:{type:Object,required:true},events:{type:Array,default:()=>[]},request:{type:Object,default:()=>({})},issues:{type:Array,default:()=>[]},tree:{type:Array,default:()=>[]},coverage:{type:Object,default:()=>({})},selectedEventId:String},
  emits:['focus'],
  data(){return {expandedChildren:false,zoom:false,selectedDate:this.request.endDate,fullDay:false,showComments:true};},
  computed:{
    axis(){return buildTimeAxis(this.issue,this.events,this.request,{issues:this.issues,tree:this.tree,coverage:this.coverage},{zoom:this.zoom,date:this.selectedDate,fullDay:this.fullDay,comments:this.showComments});},
    timeline(){return this.axis.timeline;},
    scopeKey(){return JSON.stringify([this.issue.key,this.request.startDate,this.request.endDate]);},
    heading(){return /^epic$/i.test(this.issue.type||'')?'이 Epic의 상태 변화':this.issue.isSubtask || /subtask/i.test(String(this.issue.type||'').replace(/[-_\s]/g,''))?'이 Sub-task의 상태 변화':'이 Task의 상태 변화';},
    childLabel(){return this.timeline.children?.length ? this.timeline.childLabel || '' : '';},
    childProgress(){return this.timeline.childProgress;},
    progressPercent(){return this.childProgress?.percent == null ? null : Math.round(this.childProgress.percent);},
    maxDayCount(){return Math.max(1,...this.axis.days.map(day=>day.count));},
    ticks(){
      const {start,end}=this.axis.range;
      if(!Number.isFinite(start)||!Number.isFinite(end))return [];
      const instants=[];
      if(this.zoom){
        const n=this.fullDay?4:3;
        for(let i=0;i<=n;i++)instants.push(start+(end-start)*i/n);
      }else{
        const step=86400000;
        for(let at=start;at<end;at+=step)instants.push(at);
        instants.push(end);
      }
      return instants.map(at=>{
        const shifted=new Date(at+9*3600000).toISOString();
        return {at,x:(at-start)/(end-start)*100,end:at===end,label:this.zoom?(at===end&&this.fullDay?'24:00':shifted.slice(11,16)):shifted.slice(5,10).replace('-','.')+(at===end?' 00시':' '+['일','월','화','수','목','금','토'][new Date(at+9*3600000).getUTCDay()])};
      });
    },
  },
  watch:{scopeKey(){this.expandedChildren=false;this.zoom=false;this.selectedDate=this.request.endDate;this.fullDay=false;}},
  template:`
    <section class="tt-timeline" :data-issue-key="issue.key" aria-label="업무 상태·댓글과 하위 주요 변화 타임라인">
      <div class="tt-heading"><h3>{{ heading }}</h3><span>{{ issue.key }} 자체 기록 · 한국 시간</span></div>
      <div class="tt-summary"><span><i class="tt-legend-status" aria-hidden="true"></i>자체 상태 변화 <b>{{ timeline.transitionCount }}</b>건</span><span><i class="tt-legend-comment" aria-hidden="true"></i>자체 댓글·수정 <b>{{ timeline.commentCount }}</b>건</span><span v-if="childLabel"><i class="tt-legend-child" aria-hidden="true"></i>하위 {{ childLabel }} 주요 변화 <b>{{ timeline.childEventCount }}</b>건</span></div>
      <div v-if="childLabel && childProgress" class="tt-child-progress" :class="{'is-provisional':childProgress.partial || childProgress.unknown}">
        <div class="tt-progress-heading"><span>조회된 하위 {{ childLabel }} 완료</span><b>{{ childProgress.done }}/{{ childProgress.total }}</b><span v-if="childProgress.partial || childProgress.unknown" class="tt-progress-uncertain">{{ childProgress.unknown?'상태 미확인 '+childProgress.unknown+'건':'잠정' }}</span><span v-else-if="progressPercent!==null">{{ progressPercent }}%</span></div>
        <div v-if="progressPercent!==null" class="tt-progress-track" role="progressbar" :aria-label="'조회된 하위 '+childLabel+' 완료'" :aria-valuenow="childProgress.done" :aria-valuemin="0" :aria-valuemax="childProgress.total" :aria-valuetext="childProgress.done+' / '+childProgress.total+'건 완료'"><span :style="{width:progressPercent+'%'}"></span></div>
        <p>{{ childProgress.total?'현재 조회된 하위 작업의 상태 기준입니다.':'조회된 하위 작업이 없습니다.' }}<template v-if="childProgress.partial"> 수집이 완료되지 않아 잠정 집계입니다.</template><template v-if="childProgress.unknown"> 미확인 상태가 있어 완료율을 확정할 수 없습니다.</template></p>
      </div>
      <p v-if="timeline.partial" class="tt-partial">이력·댓글 수집이 완료되지 않았습니다. 확보한 기록만 표시합니다.</p>
      <p v-if="!timeline.rangeKnown" class="tt-empty">선택 기간을 확인할 수 없습니다.</p>
      <template v-else>
        <p v-if="!timeline.transitionCount" class="tt-empty">{{ timeline.childEventCount?(timeline.commentCount?'확인된 자체 상태 변화가 없습니다.':'확인된 자체 상태 변화·댓글이 없습니다.'):(timeline.commentCount?'확인된 상태 변화가 없습니다.':'확인된 상태 변화·댓글이 없습니다.') }}</p>
        <div class="tt-axis-controls" aria-label="시간축 표시 설정">
          <button type="button" :aria-pressed="!zoom" @click="zoom=false">전체 기간</button>
          <button type="button" :aria-pressed="zoom" @click="zoom=true;selectedDate=selectedDate||timeline.end">시간 확대</button>
          <button v-if="zoom" type="button" :aria-pressed="fullDay" @click="fullDay=!fullDay">{{ fullDay?'09–18시 보기':'24시간' }}<template v-if="!fullDay && axis.outsideCount"> · 시간 밖 {{ axis.outsideCount }}건</template></button>
          <button type="button" :aria-pressed="showComments" @click="showComments=!showComments">댓글 표시</button>
        </div>
        <div class="tt-overview" tabindex="0" role="region" aria-label="날짜 선택 · 시간 확대">
          <button v-for="day in axis.days" :key="day.date" type="button" :data-date="day.date" :aria-label="day.date+' 시간 확대 · 기록 '+day.count+'건'" :aria-pressed="zoom&&selectedDate===day.date" @click="selectedDate=day.date;zoom=true"><time :datetime="day.date">{{ day.label }}</time><i :style="{height:day.count?2+12*day.count/maxDayCount+'px':'0px'}" aria-hidden="true"></i><span class="tt-sr-only">기록 {{ day.count }}건</span></button>
        </div>
        <p class="tt-window"><strong>{{ zoom?selectedDate:timeline.start+' — '+timeline.end }}</strong><span>{{ zoom?(fullDay?'00:00 — 24:00':'09:00 — 18:00')+' · KST':'전체 기간 · 실제 경과 시간' }}</span></p>
        <div class="tt-scroll" role="region" aria-label="상태·댓글과 하위 주요 변화 시간축">
          <div class="tt-axis-track">
            <tracking-time-axis :items="axis.items" :range="axis.range" :ticks="ticks" source-key="combined" :selected-event-id="selectedEventId" @focus="$emit('focus',$event)" />
            <div v-if="expandedChildren" class="tt-child-lanes">
              <p>하위 {{ childLabel }}별 기록 · 사건 사이의 과거 상태는 추정하지 않습니다.</p>
              <div v-for="child in timeline.children" :key="child.key" class="tt-child-lane">
                <div class="tt-child-lane-heading"><strong>{{ child.title }}</strong><span>{{ child.key }} · 현재 {{ child.status || '상태 미확인' }}</span></div>
                <tracking-time-axis :items="axis.items.filter(item=>item.issueKey===child.key)" :range="axis.range" :ticks="ticks" :source-key="child.key" :selected-event-id="selectedEventId" @focus="$emit('focus',$event)" />
              </div>
            </div>
          </div>
        </div>
        <button v-if="childLabel" class="tt-children-toggle" type="button" :aria-label="expandedChildren?'하위 작업별 접기':'하위 작업별 펼치기'" :aria-expanded="expandedChildren" @click="expandedChildren=!expandedChildren"><span aria-hidden="true">{{ expandedChildren?'−':'+' }}</span>하위 작업별 {{ expandedChildren?'접기':'펼치기' }}<small>{{ timeline.children.length }}</small></button>
        <p class="tt-footnote">{{ timeline.start }} — {{ timeline.end }} · 간격은 실제 경과 시간입니다. 사건을 선택하면 아래 근거로 이동합니다.</p>
      </template>
      <p v-if="timeline.unplacedCount" class="tt-partial">시각 미확인 기록 {{ timeline.unplacedCount }}건은 시간축에 배치하지 않았습니다.</p>
      <p v-if="timeline.childUnplacedCount" class="tt-partial">시각 미확인 하위 기록 {{ timeline.childUnplacedCount }}건은 시간축에 배치하지 않았습니다.</p>
    </section>`,
};
