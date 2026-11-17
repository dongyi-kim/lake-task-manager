import { buildTrackingBriefing, briefingReportText, BRIEFING_STATES } from './trackingBriefingModel.js';
import { trackingRows, countLabel, eventText, kstToday, issueKeyOf } from './trackingModel.js';
import TrackingFeed from './tracking-feed.js';
import TrackingComment from './tracking-comment.js';
import TrackingTicketTimeline from './tracking-ticket-timeline.js';
import { TrackingReferenceIcon, trackingReferenceIconName } from './tracking-reference-nav.js';

const GROUPING_KEY='ltm.tracking.report.grouping';
function readGrouping(){try{return localStorage.getItem(GROUPING_KEY)==='status'?'status':'hierarchy';}catch(_){return 'hierarchy';}}
export default {
  name:'TrackingBriefing',components:{TrackingFeed,TrackingComment,TrackingReferenceIcon,TrackingTicketTimeline},
  props:{run:Object,scopeId:{type:String,default:''},query:{type:String,default:''},highlight:{type:Object,default:null},
    matchedOnly:Boolean,pins:{type:Array,default:()=>[]},evidenceOpen:Boolean},
  emits:['scope','inspect','evidence','ticket','query','highlight','matched-only','pin'],
  data(){return {category:'all',childStatus:'all',grouping:readGrouping(),mode:'report',collapsed:{},expanded:{},pages:{},beforeExpanded:false,activityEvents:null};},
  mounted(){this._searchShortcut=event=>this.searchShortcut(event);document.addEventListener('keydown',this._searchShortcut);},
  beforeUnmount(){document.removeEventListener('keydown',this._searchShortcut);},
  computed:{
    timelineIssue(){
      const node=this.view.scope.node;
      if(!node || node.virtual || ['module','wbs','group'].includes(String(node.kind||node.type||'').toLowerCase().replace(/[-_\s]/g,'')))return null;
      const key=issueKeyOf(node);
      if(!key)return null;
      return {type:node.type||node.kind,summary:node.summary||node.name||node.title||key,
        ...(this.run?.issues||[]).find(issue=>issue.key===key),key};
    },
    view(){return buildTrackingBriefing(this.run,{scopeId:this.scopeId,query:this.query,highlight:this.highlight,matchedOnly:this.matchedOnly,
      category:this.category,childStatus:this.childStatus,pins:this.pins,grouping:this.grouping});},
    sections(){return this.view.sections.map(section=>{
      const preview=section.completion==='before'&&!this.beforeExpanded;
      const limit=preview?5:10,pageCount=Math.max(1,Math.ceil(section.items.length/limit));
      const page=Math.max(0,Math.min(this.pages[section.key] || 0,pageCount-1));
      return {...section,page,pageCount,preview,shown:section.items.slice(page*limit,page*limit+limit)};
    });},
    hierarchy(){return trackingRows(this.view.hierarchyRun,{query:this.query,highlight:this.highlight,matchedOnly:this.matchedOnly});},
    activity(){
      if(!this.activityEvents)return this.view.events;
      const ids=new Set(this.activityEvents.map(event=>event.id || JSON.stringify([event.issueKey,event.kind,event.date])));
      return this.view.events.filter(event=>ids.has(event.id || JSON.stringify([event.issueKey,event.kind,event.date])));
    },
    sparkMax(){return Math.max(1,...this.view.spark.map(bucket=>bucket.events.length));},
    parentId(){return this.view.scope.breadcrumbs.at(-2)?.id || '';},
    currentStates(){return this.view.inventory.states.filter(state=>state.key!=='structure');},
    highlightValue(){return this.highlight ? this.highlight.kind+':'+this.highlight.value : '';},
    metrics(){return [
      {key:'completed',label:'업무 완료 전환',value:this.view.metrics.completed,note:'기간 중 완료 이력이 있는 업무',icon:'check'},
      {key:'subCompleted',label:'하위 완료 전환',value:this.view.metrics.subCompleted,note:'상위 전체 완료와 별도 집계',icon:'subtask'},
      {key:'new',label:'신규 생성',value:this.view.metrics.created,note:'선택 기간에 생성된 업무',icon:'task'},
      {key:'changed',label:'변경·논의',value:this.view.metrics.changed,note:'변경 또는 댓글이 있는 업무',icon:'chat'},
      {key:'review',label:'재확인',value:this.view.metrics.review,note:this.view.partial?'미확인 근거도 포함':'재오픈·근거 확인',icon:'alert'},
    ];},
  },
  watch:{scopeId(){this.resetLocal();const main=this.$el?.closest('.rf-main');if(main)main.scrollTop=0;},query(){this.pages={};},highlight(){this.pages={};},matchedOnly(){this.pages={};}},
  methods:{
    eventText,countLabel,icon:trackingReferenceIconName,
    searchShortcut(event){
      if(event.key!=='/' || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey ||
        event.target?.isContentEditable || ['INPUT','TEXTAREA','SELECT'].includes(event.target?.tagName) ||
        !this.$el?.getClientRects().length || (typeof document!=='undefined' && document.querySelector('dialog[open]')))return;
      event.preventDefault();this.$refs.searchInput?.focus();
    },
    reportText(){return briefingReportText(this.view);},
    openActivity(events){this.activityEvents=Array.isArray(events)?events:null;this.mode='feed';},
    resetLocal(){this.category='all';this.childStatus='all';this.collapsed={};this.expanded={};this.pages={};this.beforeExpanded=false;this.activityEvents=null;this.mode='report';},
    resetDisplay(){this.resetLocal();this.$emit('query','');this.$emit('highlight',null);this.$emit('matched-only',false);},
    setCategory(value){this.category=value;this.pages={};},
    setStatus(value){this.childStatus=value;this.pages={};},
    setGrouping(value){this.grouping=value==='status'?'status':'hierarchy';this.collapsed={};try{localStorage.setItem(GROUPING_KEY,this.grouping);}catch(_){}},
    setHighlight(value){const [kind,...parts]=value.split(':');this.$emit('highlight',value?{kind,value:parts.join(':')}:null);},
    date(value){return Number.isFinite(Date.parse(value))?kstToday(new Date(value)).replaceAll('-','.').slice(5):'날짜 미확인';},
    dateFull(value){return Number.isFinite(Date.parse(value))?new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}):'시각 미확인';},
    stateLabel(state){return BRIEFING_STATES[state]?.label || '미확인';},
    stateIcon(state){return ({progress:'again',todo:'clock',done:'check',structure:'layers',unknown:'alert'})[state] || 'task';},
    activate(item){this.$emit(item.children.length?'scope':'inspect',item.id);},
    toggle(key){this.expanded={...this.expanded,[key]:!this.expanded[key]};},
    fold(key){this.collapsed={...this.collapsed,[key]:!this.collapsed[key]};},
    page(key,value){this.pages={...this.pages,[key]:Math.max(0,value)};},
    foldAll(){this.collapsed=Object.fromEntries(this.sections.map(section=>[section.key,true]));},
    excerpt(event){return eventText(event).replace(/\s+/g,' ').slice(0,150);},
    evidence(event){if(event?.id)this.$emit('evidence',event.id);},
  },
  template:`
    <div class="tb-briefing" aria-label="선택 범위 업무 브리핑">
      <header class="tb-scope-header" aria-label="현재 선택한 항목">
        <nav class="tb-breadcrumbs" aria-label="선택 범위 경로"><button @click="$emit('scope','')">전체 업무</button><template v-for="crumb in view.scope.breadcrumbs" :key="crumb.id"><span aria-hidden="true">›</span><button @click="$emit('scope',crumb.id)">{{ crumb.title }}</button></template></nav>
        <div class="tb-scope-title-row"><span class="tb-scope-icon"><TrackingReferenceIcon :name="view.scope.node ? icon(view.scope.node) : 'layers'" :size="23"/></span><div class="tb-scope-title"><div class="tb-kicker"><span>현재 선택</span><span>{{ view.scope.node?.kind || '전체 업무' }}</span><span v-if="view.scope.node?.issueKey">{{ view.scope.node.issueKey }}</span></div><h1 :title="view.title">{{ view.title }}</h1></div><div class="tb-scope-actions"><button v-if="scopeId" class="tb-button" @click="$emit('scope',parentId)">↑ 상위로</button><button v-if="scopeId" class="tb-text" @click="$emit('scope','')">전체 범위</button><button v-if="scopeId" class="tb-button" @click="$emit('inspect',scopeId)">범위 활동 <TrackingReferenceIcon name="outbound" :size="12"/></button></div></div>
        <div class="tb-scope-context"><span>선택 범위의 표시 대상 <b>업무 {{ view.metrics.tasks }}</b> · <b>하위 작업 {{ view.metrics.subtasks }}</b><template v-if="view.partial"> · 확보분</template></span><span>현재 상태 관측 · {{ view.observedAt ? dateFull(view.observedAt)+' KST' : '기준 시각 미확인' }}</span></div>
      </header>

      <TrackingTicketTimeline v-if="timelineIssue&&!evidenceOpen" :key="timelineIssue.key" :issue="timelineIssue" :events="view.run?.events||[]" :issues="view.run?.issues||[]" :tree="view.run?.tree||[]" :coverage="run?.coverage||{}" :request="run?.request||{}" @focus="$emit('evidence',$event)"/>

      <section class="tb-inventory" aria-label="조회된 직속 하위 구성">
        <div class="tb-inventory-top"><strong><TrackingReferenceIcon name="layers" :size="16"/>조회된 직속 하위 {{ view.level }} <b>{{ view.inventory.total }}</b>개</strong><span>표시 조건 일치 {{ view.matchCount }}개<template v-if="view.partial"> · 전체 구성 미확인</template></span></div>
        <div v-if="currentStates.length" class="tb-composition" role="img" :aria-label="'조회 시점 상태: '+currentStates.map(s=>s.label+' '+s.count+'개').join(', ')"><span v-for="state in currentStates" :key="state.key" :class="'tb-tone-'+state.key" :style="{flex:state.count}" :title="state.label+' '+state.count+'개'"></span></div>
        <div class="tb-inventory-bottom"><div class="tb-status-filters" role="group" aria-label="직속 하위 현재 상태"><button :class="{on:childStatus==='all'}" :aria-pressed="childStatus==='all'" @click="setStatus('all')">전체 {{ view.inventory.total }}</button><button v-for="state in view.inventory.states" :key="state.key" :class="['tb-status-'+state.key,{on:childStatus===state.key}]" :aria-pressed="childStatus===state.key" @click="setStatus(state.key)"><i></i>{{ state.label }} <b>{{ state.count }}</b></button></div><span>조회 시점 상태 · 업무명과 변화는 아래 목록에서 확인</span></div>
      </section>

      <div class="tb-period-head"><div><h2>이 범위의 기간 내 변화 <span>Δ</span></h2><small>{{ run?.request?.startDate?.replaceAll('-','.') || '시작일 미확인' }} — {{ run?.request?.endDate?.replaceAll('-','.') || '종료일 미확인' }} · {{ view.title }}</small></div><div class="tb-spark" aria-label="기간 활동 분포"><div class="tb-spark-title"><span>기간 활동 분포</span><span>{{ view.metrics.events }}건</span></div><div class="tb-spark-bars"><button v-for="bucket in view.spark" :key="bucket.index" :disabled="!bucket.events.length" @click="openActivity(bucket.events)" :aria-label="bucket.start+' ~ '+bucket.end+' 활동 '+bucket.events.length+'건'"><i :style="{height:(bucket.events.length ? Math.max(3,bucket.events.length/sparkMax*23) : 2)+'px'}"></i><small>{{ bucket.empty ? '–' : bucket.start.slice(5).replace('-','/') }}</small></button></div></div></div>
      <div class="tb-section-label">기간 중 발생한 변화 <span>고유 업무 수 · 완료 전환과 현재 완료 상태를 구분</span></div>
      <div class="tb-metrics" role="group" aria-label="선택 범위 기간 내 변화"><button v-for="metric in metrics" :key="metric.key" class="tb-metric" :class="{on:category===metric.key}" @click="setCategory(metric.key)" :aria-pressed="category===metric.key" :aria-label="metric.label+' '+metric.value+'개'"><span class="tb-metric-label">{{ metric.label }}</span><span class="tb-metric-number" :class="{'tb-warning':metric.key==='review'&&metric.value}">{{ metric.value }}<small>개</small></span><span class="tb-metric-note">{{ metric.note }}</span><TrackingReferenceIcon :name="metric.icon" :size="15"/></button></div>

      <section v-if="view.level!=='Subtask'" class="tb-current" aria-label="조회 시점 현재 상태 분포"><div><strong>조회 시점 현재 상태</strong><span>조회된 직속 하위 {{ view.inventory.total }}개 기준<template v-if="view.partial"> · 확보분</template></span></div><div class="tb-current-bar"><span v-for="state in view.inventory.states" :key="state.key" :class="'tb-tone-'+state.key" :style="{flex:state.count}" :title="state.label+' '+state.count+'개'"></span></div><div class="tb-current-legend"><span v-for="state in view.inventory.states" :key="state.key"><i :class="'tb-tone-'+state.key"></i>{{ state.label }} <b>{{ state.count }}</b></span><small>기간 내 변화와 별도 기준</small></div></section>

      <div class="tb-work-header"><h2>{{ scopeId ? '직속 '+view.level+' 목록' : '업무별 변화' }} <span>{{ view.items.length }}개 표시</span></h2><div class="tb-view-modes" role="group" aria-label="활동 보기"><button :class="{on:mode==='report'}" :aria-pressed="mode==='report'" @click="mode='report'">업무 요약</button><button :class="{on:mode==='feed'}" :aria-pressed="mode==='feed'" @click="openActivity()">날짜별 활동</button><button :class="{on:mode==='tree'}" :aria-pressed="mode==='tree'" @click="mode='tree'">계층별 집계</button></div></div>
      <div class="tb-controls"><div class="tb-tabs" role="group" aria-label="업무 분류"><button v-for="tab in view.categories" :key="tab.key" :class="{on:category===tab.key}" :aria-pressed="category===tab.key" @click="setCategory(tab.key)">{{ tab.label }}<small>{{ tab.count }}</small></button></div><div class="tb-search-tools"><label class="tb-search"><span aria-hidden="true">⌕</span><input ref="searchInput" :value="query" @input="$emit('query',$event.target.value)" placeholder="업무명, 번호, 상위 경로 검색" aria-label="업무 검색"/><kbd>/</kbd></label><select :value="highlightValue" @change="setHighlight($event.target.value)" aria-label="사람 또는 업무 강조"><option value="">사람·업무 강조</option><optgroup label="사람"><option v-for="person in view.people" :key="person.id" :value="'person:'+person.id">{{ person.name }}</option></optgroup><optgroup label="업무"><option v-for="item in view.highlights" :key="item.id" :value="item.kind+':'+item.id">{{ item.title }}</option></optgroup></select><select :value="matchedOnly?'matches':'context'" @change="$emit('matched-only',$event.target.value==='matches')" aria-label="검색 표시 방식"><option value="context">맥락 유지</option><option value="matches">일치만 보기</option></select><select :value="grouping" @change="setGrouping($event.target.value)" aria-label="업무 그룹핑"><option value="hierarchy">계층 → 현재 상태</option><option value="status">현재 상태 → 계층</option></select></div><div class="tb-filter-line"><div><span>표시 조건</span><span class="tb-chip">{{ view.categories.find(c=>c.key===category)?.label || metrics.find(m=>m.key===category)?.label || '전체' }}</span><span v-if="query" class="tb-chip">검색 {{ query }} <button @click="$emit('query','')" aria-label="검색 지우기">×</button></span><span v-if="highlight" class="tb-chip">강조 {{ highlight.value }} <button @click="$emit('highlight',null)" aria-label="강조 지우기">×</button></span><span v-if="childStatus!=='all'" class="tb-chip">현재 {{ stateLabel(childStatus) }} <button @click="setStatus('all')" aria-label="상태 조건 지우기">×</button></span><button class="tb-text" @click="resetDisplay">초기화</button></div><span>집계: 업무 {{ view.metrics.tasks }} · 하위 {{ view.metrics.subtasks }} · 활동 {{ view.metrics.events }}건</span></div></div>

      <div v-if="mode==='report'" class="tb-work-list"><div class="tb-list-tools"><span>{{ view.partial ? '확보한 ' : '' }}표시 대상 {{ view.items.length }}개<template v-if="query || highlight"> · 일치 {{ view.matchCount }}개</template></span><span><button @click="collapsed={}">모두 펼치기</button><button @click="foldAll">모두 접기</button></span></div>
        <section v-for="section in sections" :key="section.key" :class="['tb-group','tb-group-'+section.state,{'tb-past':section.completion==='before'}]" :data-completion-kind="section.completion || null" :data-group-title="section.title"><h3 class="tb-group-heading"><button @click="fold(section.key)" :aria-expanded="!collapsed[section.key]"><TrackingReferenceIcon :name="section.completion==='unknown'?'alert':stateIcon(section.state)" :size="18"/><i class="tb-title-divider"></i><strong>{{ section.title }}</strong><b>· {{ section.items.length }}</b><small>{{ section.completion==='before'?'참고 · 선택 기간 이전의 확인된 완료':section.completion==='unknown'?'현재 완료 상태 · 전환 시점 근거 미확보':view.partial?'확보한 범위 · 조회 시점 상태':'조회 시점 상태' }}</small><span class="tb-fold">{{ collapsed[section.key]?'펼치기 ＋':'접기 −' }}</span></button></h3>
          <div v-if="!collapsed[section.key]"><div class="tb-columns" aria-hidden="true"><span>하위 업무</span><span>{{ section.completion?'완료 시점 · 확인 근거':'Δ 이번 기간의 변화' }}</span><span>조회 시점 상태</span><span>확인 사항 · 근거</span></div>
            <article v-for="item in section.shown" :key="item.id" class="tb-row" :class="{'is-context':item.context,'is-match':item.matched}" :data-row-id="item.id" :data-key="item.key"><div class="tb-row-grid"><div class="tb-identity"><span class="tb-item-icon"><TrackingReferenceIcon :name="icon(item)" :size="16"/></span><div><button class="tb-title-button" @click="activate(item)" :title="item.title">{{ item.title }}</button><small><span>{{ item.issue?.type || item.node.kind || '그룹' }}</span><button v-if="item.key" @click="$emit('ticket',item.key)" :aria-label="item.key+' 원본 티켓'">{{ item.key }}</button><span v-if="item.context">검색 맥락</span></small><span v-if="item.path && !scopeId" class="tb-row-path">{{ item.path }}</span><div v-if="item.children.length" class="tb-child-links"><button v-for="child in item.children.slice(0,2)" :key="child.id" @click="$emit('scope',child.id)">{{ child.title }}</button><button v-if="item.children.length>2" @click="activate(item)">외 {{ item.children.length-2 }}개</button></div></div><button class="tb-pin" :class="{on:item.pinned}" :aria-pressed="item.pinned" :aria-label="item.title+(item.pinned?' 주요 업무 고정 해제':' 주요 업무로 고정')" @click="$emit('pin',item.key || item.id)">☆</button></div>
              <div class="tb-delta"><strong v-if="section.completion" :class="'tb-provenance-'+item.completion.kind"><TrackingReferenceIcon :name="section.completion==='unknown'?'alert':'check'" :size="13"/>{{ item.completion.label }}</strong><strong v-else><TrackingReferenceIcon :name="item.changes.length?'again':item.comment?'chat':item.events.length?'check':'clock'" :size="13"/>{{ item.headline }}</strong><small v-if="section.completion">{{ item.completion.at ? date(item.completion.at)+' · 확인된 완료 전환 이력' : '현재 완료 상태 확인 · 완료 전환 이력 미확보' }}</small><div v-if="item.changes.length" class="tb-delta-flow" role="group" aria-label="선택 기간의 실제 상태 전환"><template v-for="(change,index) in item.changes.slice(0,2)" :key="change.id"><span v-if="index && item.changes[index-1].to!==change.from" class="tb-flow-break" aria-hidden="true">·</span><button v-if="!index || item.changes[index-1].to!==change.from" class="tb-delta-node tb-delta-from" @click="evidence(change)"><b>{{ change.from ?? '미확인' }}</b><small>전환 직전</small></button><span class="tb-delta-arrow" aria-hidden="true">→</span><button class="tb-delta-node tb-delta-to" :class="{'tb-delta-complete':change.kind==='completed','tb-delta-reopened':change.kind==='reopened'}" @click="evidence(change)"><b>{{ change.to ?? '미확인' }}</b><time :datetime="change.date">{{ date(change.date) }}</time></button></template></div><div v-if="item.completedChildren.length" class="tb-child-completions"><button v-for="child in item.completedChildren.slice(0,2)" :key="child.key" @click="evidence(child.event)">✓ {{ child.title }}</button><small v-if="item.completedChildren.length>2">외 {{ item.completedChildren.length-2 }}개 하위 완료 이력</small></div><button v-if="item.comment" class="tb-comment-preview" @click="evidence(item.comment)" :title="eventText(item.comment)">{{ date(item.comment.date) }} · {{ item.comment.actorName || item.comment.actorId || '작성자 미확인' }} · {{ excerpt(item.comment) }}</button><small v-if="!item.events.length">{{ item.uncertain?'추가 근거 확인 필요':'미작업·지연을 뜻하지 않습니다.' }}</small></div>
              <div class="tb-state"><span class="tb-badge" :class="'tb-badge-'+item.state">{{ item.currentLabel }}</span><small>{{ item.owner }}</small><small v-if="item.children.length">조회된 직속 하위 {{ item.children.length }}개</small></div>
              <div class="tb-evidence"><span>{{ item.uncertain?'수집 근거 일부 미확인':item.events.some(e=>e.kind==='reopened')?'재오픈 이력 확인':item.events.length?'확인된 활동 '+item.events.length+'건':'확인된 활동 없음' }}</span><button class="tb-text" @click="$emit('inspect',item.id)">근거 보기 <TrackingReferenceIcon name="outbound" :size="12"/></button><button v-if="item.comment || item.completedChildren.length || item.events.length>2" class="tb-more" @click="toggle(item.id)" :aria-expanded="!!expanded[item.id]">{{ expanded[item.id]?'내용 접기':'내용 펼치기' }}</button></div></div>
              <div v-if="expanded[item.id]" class="tb-row-expanded"><div v-for="comment in item.comments" :key="comment.id" class="tb-expanded-comment"><span>최근 보고·논의 · {{ comment.actorName || comment.actorId || '작성자 미확인' }} · {{ dateFull(comment.date) }}</span><TrackingComment :event="comment"/><button class="tb-text" @click="evidence(comment)">댓글 근거 보기</button></div><div v-if="item.children.length" class="tb-expanded-children"><span>조회된 직속 하위</span><button v-for="child in item.children" :key="child.id" @click="$emit('scope',child.id)"><TrackingReferenceIcon :name="icon(child)" :size="13"/>{{ child.title }}</button></div><button class="tb-text" @click="openActivity(item.events)">이 업무의 날짜별 활동 {{ item.events.length }}건</button></div>
            </article>
            <button v-if="section.preview && section.items.length>5" class="tb-before-more" @click="beforeExpanded=true">기간 이전 완료 {{ section.items.length }}개 보기 · 10개씩⌄</button><div v-else-if="section.pageCount>1" class="tb-pager"><span>{{ section.page*10+1 }}–{{ Math.min(section.items.length,section.page*10+10) }} / {{ section.items.length }}개</span><button :disabled="!section.page" @click="page(section.key,section.page-1)" :aria-label="section.title+' 이전 페이지'">이전</button><b>{{ section.page+1 }} / {{ section.pageCount }}</b><button :disabled="section.page>=section.pageCount-1" @click="page(section.key,section.page+1)" :aria-label="section.title+' 다음 페이지'">다음</button></div><button v-if="section.completion==='before' && beforeExpanded && section.items.length>5" class="tb-before-less" @click="beforeExpanded=false">참고 업무 5개만 보기</button>
          </div>
        </section>
        <div v-if="!view.items.length" class="tb-empty"><strong>조건에 맞는 {{ view.level }}이 없습니다.</strong><p>{{ view.partial?'아직 전체 조회가 끝나지 않았습니다. 확인한 범위에 일치 항목이 없습니다.':'검색 또는 표시 조건을 바꿔 보세요. 기존 조회 범위는 유지됩니다.' }}</p><button class="tb-button" @click="resetDisplay">표시 조건 초기화</button></div>
      </div>
      <div v-else-if="mode==='feed'" class="tb-activity"><div v-if="activityEvents" class="tb-list-tools"><span>선택한 날짜 구간 · 활동 {{ activity.length }}건</span><button @click="activityEvents=null">기간 전체 보기</button></div><TrackingFeed :events="activity" :ticket-names="view.ticketNames" :match-keys="query || highlight ? view.items.flatMap(i=>i.matchedKeys) : []" @select="evidence"/><p v-if="!activity.length" class="tb-activity-empty">이 범위에서 선택 기간에 확인된 활동이 없습니다.</p></div>
      <div v-else class="tb-hierarchy" aria-label="계층별 집계"><div class="tb-hierarchy-head"><span>업무 · 계층</span><span>생성</span><span>완료</span><span>재오픈</span><span>댓글</span><span>변경</span></div><div v-for="row in hierarchy" :key="row.id" class="tb-hierarchy-row"><button :style="{paddingLeft:(row.depth*15)+'px'}" @click="$emit(row.children.length?'scope':'inspect',row.id)"><TrackingReferenceIcon :name="icon(row)" :size="14"/>{{ row.title }}</button><span>{{ countLabel(row.counts.created,row.counts.historyComplete) }}</span><span>{{ countLabel(row.counts.completed,row.counts.historyComplete) }}</span><span>{{ countLabel(row.counts.reopened,row.counts.historyComplete) }}</span><span>{{ countLabel(row.counts.comments,row.counts.commentsComplete) }}</span><span>{{ countLabel(row.counts.fieldChanges,row.counts.historyComplete) }}</span></div></div>
      <footer class="tb-footnote">변화는 선택 기간에 확인된 사건을 뜻합니다. 과거 기준값이 확인되지 않은 수치에는 증가량이나 증감률을 표시하지 않습니다.<br>현재 상태 기준 {{ view.observedAt ? dateFull(view.observedAt)+' KST' : '미확인' }} · {{ view.partial?'미확인은 0건이나 활동 없음으로 처리하지 않습니다.':'활동 없음은 미작업·지연을 뜻하지 않습니다.' }}</footer>
    </div>`,
};
