import { eventText, readableTicketTitle, ticketLabel } from './trackingModel.js';
import { reportText } from './trackingReportModel.js';

const PIN_KEY = 'ltm.tracking.report.pins';
function readPins() {
  try { const value = JSON.parse(localStorage.getItem(PIN_KEY) || '[]'); return Array.isArray(value) ? value.filter(k => typeof k === 'string') : []; }
  catch (error) { return []; }
}
const CATEGORIES = [
  {key:'all',label:'전체 업무'}, {key:'pinned',label:'주요 업무'},
  {key:'completed',label:'업무 완료'}, {key:'changed',label:'변경·논의'},
  {key:'review',label:'재확인'}, {key:'quiet',label:'활동 없음'},
];

export default {
  name:'TrackingReport',
  props:{report:Object,run:Object,selectedKey:String,matchKeys:Array,filterMatches:Boolean,hasMatch:Boolean},
  emits:['select','evidence'],
  data() { return {pins:readPins(),filter:'all',expanded:{},copyState:'',copyError:false}; },
  computed:{
    categories() { return CATEGORIES.map(c => ({...c,count:c.key === 'all' ? this.report.items.length : c.key === 'pinned' ? this.report.items.filter(i => this.pins.includes(i.key)).length : this.report.items.filter(i => i.category === c.key).length})); },
    items() {
      return this.report.items.filter(item => (this.filter === 'all' || (this.filter === 'pinned' ? this.pins.includes(item.key) : item.category === this.filter)) &&
        (!this.filterMatches || !this.hasMatch || this.matches(item)))
        .sort((a,b) => Number(this.pins.includes(b.key))-Number(this.pins.includes(a.key)));
    },
    sections() {
      const groups = new Map();
      for (const item of this.items) {
        const category = item.issue?.vocCategory || '분류 미지정';
        const key = item.issue?.voc ? 'voc:'+category : 'work';
        if (!groups.has(key)) groups.set(key,{key,title:item.issue?.voc ? category : '일반 업무',voc:!!item.issue?.voc,items:[]});
        groups.get(key).items.push(item);
      }
      return [...groups.values()].sort((a,b) => Number(a.voc)-Number(b.voc));
    },
    overview() { const s = this.report.summary; return s.total ? `${s.total}개 업무로 모은 기간 내 변화` : '아직 요약할 업무가 없습니다'; },
  },
  methods:{
    eventText,
    matches(item) { return item.keys.some(key => (this.matchKeys || []).includes(key)); },
    categoryLabel(key) { return CATEGORIES.find(c => c.key === key)?.label || '활동'; },
    isSelected(item) { return item.keys.includes(this.selectedKey); },
    pin(item) {
      this.pins = this.pins.includes(item.key) ? this.pins.filter(key => key !== item.key) : [...this.pins,item.key];
      try { localStorage.setItem(PIN_KEY,JSON.stringify(this.pins)); } catch (error) { /* Session pinning remains usable. */ }
    },
    date(value) { const d = new Date(value); return Number.isFinite(d.getTime()) ? d.toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric'}) : '날짜 미확인'; },
    issueTitle(item,key) { return readableTicketTitle(key === item.key ? item.title : item.children.find(c => c.key === key)?.title,key); },
    issueLabel(item,key) { return ticketLabel(key,this.issueTitle(item,key)); },
    completedChildren(item) { return item.completedKeys.filter(key => key !== item.key); },
    comments(item) { return this.expanded[item.key] ? item.comments : item.comments.slice(0,2); },
    changeEvents(item) { return item.events.filter(e => ['created','completed','reopened','fieldChanged'].includes(e.kind)).slice(0,3); },
    toggle(item) { this.expanded = {...this.expanded,[item.key]:!this.expanded[item.key]}; },
    async copy() {
      this.copyState = ''; this.copyError = false;
      const text = reportText(this.report,{request:this.run.request,asOf:this.run.asOf,keys:this.items.map(i => i.key)});
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
        else {
          const input = document.createElement('textarea'); input.value = text;
          input.style.cssText = 'position:fixed;left:-9999px'; document.body.appendChild(input);
          try { input.select(); if (!document.execCommand('copy')) throw new Error('Clipboard unavailable'); }
          finally { input.remove(); }
        }
        this.copyState = `${this.items.length}개 업무의 보고 내용을 복사했습니다.`;
      } catch (error) { this.copyError = true; this.copyState = '복사하지 못했습니다. 브라우저의 클립보드 권한을 확인해 주세요.'; }
    },
  },
  template:`
    <section class="tr-report" aria-label="기간 업무 요약">
      <header class="tr-report-head"><div><h2>{{ overview }}</h2><p>Task와 하위 작업을 함께 읽고, 주요 업무를 고정해 보고에 활용하세요.</p></div><button class="tr-button" @click="copy" :disabled="!items.length">보고 내용 복사</button></header>
      <div class="tr-report-filters" role="group" aria-label="업무 요약 분류"><button v-for="category in categories" :key="category.key" :class="['tr-report-filter','tr-report-filter-'+category.key,{on:filter === category.key}]" :aria-pressed="filter === category.key" @click="filter = category.key">{{ category.label }}<b>{{ category.count }}</b></button></div>
      <p v-if="copyState" class="tr-report-copy" :class="{'tr-error':copyError}" role="status">{{ copyState }}</p>
      <p v-if="report.summary.provisional" class="tr-report-caveat">일부 근거만 확인됨 · 업무 요약과 완료 이력은 추가 수집 시 달라질 수 있습니다.</p>
      <div class="tr-report-list">
        <section v-for="section in sections" :key="section.key" class="tr-report-section"><header v-if="section.voc || sections.length > 1" class="tr-report-section-head"><span v-if="section.voc" class="tr-report-voc">사용자 VOC</span><h3>{{ section.title }}</h3><span>{{ section.items.length }}개 업무</span><small v-if="section.voc">분류별 묶음</small></header><div class="tr-report-briefs">
        <article v-for="item in section.items" :key="item.key" :class="['tr-brief','tr-brief-'+item.category,{'tr-brief-selected':isSelected(item),'tr-brief-match':hasMatch && matches(item)}]">
          <div class="tr-brief-context"><span :title="item.paths.join(' / ')">{{ item.paths[0] || '연결된 상위 업무 없음' }}</span><span v-if="item.paths.length > 1">외 {{ item.paths.length-1 }}개 경로</span><button class="tr-brief-pin" @click="pin(item)" :aria-pressed="pins.includes(item.key)" :aria-label="item.title + (pins.includes(item.key) ? ' 주요 업무 고정 해제' : ' 주요 업무로 고정')" :title="pins.includes(item.key) ? '주요 업무 고정 해제' : '주요 업무로 고정'"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m7 2 6 0-1 5 3 3v2h-4v6l-1-2-1 2v-6H5v-2l3-3z"/></svg><span>{{ pins.includes(item.key) ? '주요 업무' : '고정' }}</span></button></div>
          <div class="tr-brief-title"><button @click="$emit('select',item)"><h3>{{ issueTitle(item,item.key) }}</h3><span>{{ item.key }}</span></button><span class="tr-brief-current" title="조회 시점의 현재 상태이며 기간 말 상태와 다를 수 있습니다">현재 {{ item.issue?.status || '미확인' }}</span></div>
          <div class="tr-brief-outcome"><span class="tr-brief-label">{{ categoryLabel(item.category) }}</span><strong>{{ item.headline }}</strong><span v-if="item.transition" class="tr-brief-transition">{{ item.transition.from }} → {{ item.transition.to }}</span></div>
          <ul v-if="completedChildren(item).length" class="tr-brief-deliveries"><li v-for="key in completedChildren(item).slice(0,expanded[item.key] ? undefined : 2)" :key="key"><span aria-hidden="true">✓</span><button @click="$emit('evidence',item.events.find(e => e.issueKey === key && e.kind === 'completed')?.id)">{{ issueTitle(item,key) }}</button><small>{{ key }}</small><small v-if="item.reopenedKeys.includes(key)">재오픈 이력도 있음</small></li><li v-if="!expanded[item.key] && completedChildren(item).length > 2" class="tr-muted">외 {{ completedChildren(item).length-2 }}건의 하위 완료 이력</li></ul>
          <div v-if="item.comments.length" class="tr-brief-comments"><span class="tr-brief-section-label">최근 보고·논의</span><button v-for="comment in comments(item)" :key="comment.id" class="tr-brief-comment" @click="$emit('evidence',comment.id)"><p>{{ eventText(comment) }}</p><span>{{ comment.actorName || comment.actorId || '작성자 미확인' }}<time>{{ date(comment.date) }}</time><span v-if="comment.isEdited || comment.latestObservedEdit || comment.kind === 'commentEdited'">관측된 수정본</span><span v-if="comment.issueKey !== item.key" class="tr-comment-source">{{ issueLabel(item,comment.issueKey) }}</span></span></button></div>
          <ul v-else-if="changeEvents(item).length" class="tr-brief-changes"><li v-for="event in changeEvents(item)" :key="event.id"><button @click="$emit('evidence',event.id)"><span class="tr-change-ticket">{{ issueLabel(item,event.issueKey) }}</span><span>{{ eventText(event) }}</span></button><time>{{ date(event.date) }}</time></li></ul>
          <p v-else class="tr-brief-no-evidence">{{ item.uncertain ? '수집되지 않은 근거가 있어 기간 내 진행 내용을 확정할 수 없습니다.' : '이 기간에 수집된 활동 없음. 실제 작업 여부는 이 정보만으로 판단할 수 없습니다.' }}</p>
          <footer class="tr-brief-footer"><span>{{ item.issue?.assigneeName || item.issue?.assignee?.name || '담당 미확인' }}</span><span v-if="item.children.length">하위 {{ item.children.length }}개 업무 묶음</span><span v-if="item.uncertain" class="tr-brief-uncertain">근거 일부 미확인</span><button v-if="item.comments.length > 2 || completedChildren(item).length > 2" @click="toggle(item)" :aria-expanded="!!expanded[item.key]">{{ expanded[item.key] ? '간략히' : '내용 더 보기' }}</button><button class="tr-brief-evidence" @click="$emit('select',item)">활동 근거 {{ item.events.length }}건 보기</button></footer>
        </article></div></section>
        <div v-if="!items.length" class="tr-empty"><template v-if="filter === 'pinned'">보고에서 계속 볼 업무의 ‘고정’을 누르세요. 기간을 바꿔도 주요 업무 선택을 유지합니다.</template><template v-else>이 분류에 표시할 업무가 없습니다. 분류나 검색 조건을 바꿔 보세요.</template></div>
      </div>
      <footer class="tr-report-note">완료 이력과 최근 댓글을 함께 표시 · 하위 완료는 상위 Task 전체 완료를 뜻하지 않음</footer>
    </section>`,
};
