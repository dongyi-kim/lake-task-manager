import { api } from '../lib/api.js';
import { kstToday, dateRange, dateError, trackingRows, isTerminalTrackingRow, countLabel, groupEvents, eventText, readableTicketTitle, ticketLabel, EVENT_LABELS, RUN_LABELS } from './trackingModel.js';
import TrackingReport from './tracking-report.js';
import { buildTrackingReport } from './trackingReportModel.js';
import { projectTrackingVoc } from './trackingVocModel.js';
import TrackingOverview from './tracking-overview.js';
import { buildTrackingOverview, trackingRowSummary } from './trackingOverviewModel.js';
import { buildTrackingScope } from './trackingScopeModel.js';
import TrackingNavigator from './tracking-navigator.js';
import TrackingSplitter from './tracking-splitter.js';
import TrackingComment from './tracking-comment.js';

const AI_SUMMARY_TEMPLATE = `
  <div class="tr-ai-result" :class="{'tr-ai-placeholder':!ai && !aiPending}" aria-live="polite">
    <div class="tr-ai-heading"><strong>AI 업무 요약</strong><button class="tr-button" @click="summarize" :disabled="!options.aiEnabled || aiPending || busy" :title="options.aiReason || '수집된 근거만 요약합니다'">{{ aiPending ? '요약 중…' : options.aiEnabled ? 'AI 요약 생성' : '사용 불가' }}</button></div>
    <p v-if="scopeId" class="tr-ai-description">AI 요약은 선택 계층이 아닌 전체 조회 범위 기준입니다.</p>
    <p v-if="!ai && !aiPending && options.aiEnabled" class="tr-ai-description">수집된 성과·진행·확인 사항을 보고 문장으로 정리합니다.</p>
    <details v-if="!ai && !aiPending && !options.aiEnabled" class="tr-ai-reason"><summary>연결 설정 확인</summary><p>{{ options.aiReason || 'AI 연결 설정이 필요합니다.' }}</p></details>
    <span v-if="aiPending">확보한 근거를 읽고 있습니다…</span><p v-if="aiError" class="tr-error">{{ aiError }}</p>
    <ul v-if="ai"><li v-for="(item,i) in ai.items || []" :key="i">{{ item.text }} <button v-for="id in item.evidenceIds || []" :key="id" class="tr-inline" @click="focusEvidence(id)">{{ evidenceLabel(id) }} · 근거</button></li></ul>
    <small v-if="ai">{{ provisional ? '부분 수집 근거 기준 · ' : '' }}추가 Jira 조회 없음 · 사용 토큰 {{ ai.usage?.totalTokens ?? '미확인' }}</small>
  </div>`;

const running = run => ['queued','running'].includes(run?.status);
const basePath = '/api/tracking/runs';
export default {
  name: 'TrackingView',
  components: { TrackingReport, TrackingOverview, TrackingNavigator, TrackingSplitter, TrackingComment },
  data() {
    return { ...dateRange(), options:{people:[],modules:[]}, optionsLoaded:false, optionsError:'', people:[],modules:[],personNames:{},
      run:null,recent:[],recentId:'',pending:false,error:'',pollError:'',query:'',highlightKind:'person',highlightValue:'',
      matchedOnly:false,mode:'report',closed:{},selectedId:'',selectedEventId:'',picker:'',pickerQuery:'',reportEvidenceOpen:false,
      ai:null,aiPending:false,aiError:'',active:true,scopeId:'',scopeClosed:{},evidenceSource:'row',hierarchyWidth:300,reportScopeParentId:'' };
  },
  computed: {
    today: () => kstToday(),
    busy() { return this.pending || running(this.run); },
    validation() { return dateError(this.startDate,this.endDate); },
    highlight() { return this.highlightValue ? {kind:this.highlightKind,value:this.highlightValue} : null; },
    displayRun() { return projectTrackingVoc(this.run,{vocComponent:this.options.vocComponent}); },
    scope() {
      const base = this.scopeId.startsWith('tracking-report:') && this.reportScopeParentId
        ? buildTrackingScope(this.displayRun,this.reportScopeParentId).run : this.displayRun;
      return buildTrackingScope(base,this.scopeId);
    },
    scopedRun() { return this.scope.run; },
    scopedReport() { return this.scope.report; },
    scopeLabel() { return this.scope.node?.name || this.scope.node?.summary || (this.scopeId ? '선택 범위 미확인' : '전체 업무'); },
    navigationRows() { return trackingRows(this.displayRun,{query:this.query,highlight:this.highlight,matchedOnly:this.matchedOnly,closed:this.scopeClosed}); },
    rows() { return this.rowsInScope(this.closed); },
    allRows() { return trackingRows(this.displayRun); },
    ticketNames() {
      const names = new Map(this.allRows.filter(row => row.key).map(row => [row.key,row.title]));
      for (const issue of this.displayRun?.issues || []) if (issue.summary && issue.summary !== issue.key) names.set(issue.key,issue.summary);
      return names;
    },
    selected() {
      const row = this.allRows.find(row => row.id === this.selectedId);
      if (row) return row;
      const item = [...this.scopedReport.items,...this.report.items].find(item => item.rowId === this.selectedId);
      return item ? {...item,id:item.rowId,path:[],keys:new Set(item.keys),contextOnly:true,counts:{historyComplete:!item.uncertain}} : null;
    },
    selectedIssue() { return this.selected?.issue || null; },
    summaryDisabled() { return isTerminalTrackingRow(this.selected); },
    evidenceBreadcrumbs() {
      return [...(this.selected?.path || []),this.selected].filter(Boolean).map(node => ({id:node.id,title:node.title || node.name || node.summary || node.key}));
    },
    report() { return buildTrackingReport(this.displayRun); },
    overview() { return buildTrackingOverview(this.scopedReport); },
    selectedBrief() {
      const source = this.evidenceSource === 'report' ? this.scopedReport : buildTrackingScope(this.displayRun,this.selectedId).report;
      return source.items.find(item => item.key === this.selected?.key);
    },
    selectedBriefSummary() { return this.selectedBrief && !['module','wbs','epic'].includes(this.selected?.kind) ? trackingRowSummary(this.selectedBrief) : null; },
    selectedEvents() {
      const item = this.evidenceSource === 'report' && this.scopedReport.items.find(item => item.key === this.selected?.key);
      return (this.run?.events || []).filter(e =>
        (item ? item.keys.includes(e.issueKey) : !this.selectedId || this.selected?.keys.has(e.issueKey)) &&
        (!this.matchedOnly || (!this.query && !this.highlight) || this.matchKeys.has(e.issueKey)));
    },
    evidenceGroups() { return groupEvents(this.selectedEvents); },
    feedGroups() {
      const rows = this.rowsInScope();
      const keys = new Set(rows.filter(r => !this.matchedOnly || r.matched || (!this.query && !this.highlight)).map(r => r.key).filter(Boolean));
      return groupEvents((this.run?.events || []).filter(event => keys.has(event.issueKey)));
    },
    matchKeys() { return new Set(trackingRows(this.displayRun,{query:this.query,highlight:this.highlight}).filter(r => r.matched).map(r => r.key)); },
    peopleOptions() {
      const people = (this.options.people || []).map(option => {
        const id = String(option.id), name = String(option.name || id);
        return {...option,name:name.toLocaleLowerCase() === id.toLocaleLowerCase() ? this.personNames[id.toLocaleLowerCase()] || name : name};
      });
      const counts = new Map();
      for (const person of people) counts.set(person.name,(counts.get(person.name) || 0)+1);
      return people.map(person => ({...person,name:counts.get(person.name) > 1 ? person.name+' ('+person.id+')' : person.name}));
    },
    highlightOptions() {
      if (this.highlightKind === 'person') return this.peopleOptions;
      const seen = new Set();
      return this.allRows.filter(row => (row.key || (row.virtual && row.kind === 'epic')) && !seen.has(row.key || row.id) && seen.add(row.key || row.id) &&
        (this.highlightKind === 'epic' ? row.kind === 'epic' : row.kind !== 'epic')).map(row => ({id:row.key || row.id,name:row.virtual ? 'VOC · '+row.title : ticketLabel(row.key,row.title)}));
    },
    pickerOptions() {
      const values = this.picker === 'people' ? this.peopleOptions : this.options[this.picker] || [];
      return values.filter(value => (value.name+' '+value.id).toLocaleLowerCase().includes(this.pickerQuery.toLocaleLowerCase()));
    },
    provisional() { return this.run?.status !== 'complete' || !!this.run?.summary?.provisional || this.run?.coverage?.complete !== true; },
    summary() { return this.run?.summary || {}; },
    scopeActivity() {
      const events = this.scopedRun?.events || [];
      const distinct = kind => new Set(events.filter(event => event.kind === kind).map(event => event.issueKey)).size;
      return {uniqueIssues:new Set(events.map(event => event.issueKey)).size,created:distinct('created'),completed:distinct('completed'),
        comments:events.filter(event => event.kind === 'comment').length,fieldChanges:events.filter(event => event.kind === 'fieldChanged').length};
    },
    coverage() { return this.run?.coverage || {}; },
    targetIssues() { return (this.run?.issues || []).filter(issue => issue.contextOnly !== true); },
    historyConfirmed() { return this.targetIssues.filter(issue => issue.coverage?.historyComplete === true).length; },
    metrics() { return this.run?.metrics || {}; },
    runLabel() { return RUN_LABELS[this.run?.status] || '수집 전'; },
    canResume() { return ['partial','paused'].includes(this.run?.status); },
    changedRequest() {
      if (!this.run?.request) return false;
      const r = this.run.request, same = (a,b) => JSON.stringify([...(a || [])].sort()) === JSON.stringify([...(b || [])].sort());
      return r.startDate !== this.startDate || r.endDate !== this.endDate || !same(r.people,this.people) || !same(r.modules,this.modules);
    },
  },
  watch: {
    mode() { if (!this.summaryDisabled) this.reportEvidenceOpen = false; },
  },
  mounted() {
    this._epoch = 0;
    this._authRecovered = () => {
      if (!this.active) return;
      if (!this.optionsLoaded || this.optionsError) this.initialize();
      else if (this.pollError && this.run) this.openRun(this.run.id,false);
    };
    window.addEventListener('auth-ok',this._authRecovered);
    this.initialize();
  },
  activated() { this.active = true; this.schedulePoll(); },
  deactivated() { this.active = false; clearTimeout(this._pollTimer); },
  unmounted() { this.active = false; this._epoch++; clearTimeout(this._pollTimer); window.removeEventListener('auth-ok',this._authRecovered); },
  methods: {
    countLabel,eventText,
    rowsInScope(closed = {}) {
      const options = {query:this.query,highlight:this.highlight,matchedOnly:this.matchedOnly,closed};
      if (!this.scopeId) return trackingRows(this.displayRun,options);
      const permitted = new Map(trackingRows(this.scopedRun).map(row => [row.id,row.depth]));
      options.closed = Object.fromEntries(Object.entries(closed).filter(([id]) => permitted.has(id)));
      return trackingRows(this.displayRun,options).filter(row => permitted.has(row.id)).map(row => ({...row,depth:permitted.get(row.id)}));
    },
    ticketTitle(key) { return readableTicketTitle(this.ticketNames.get(key),key); },
    ticketLabel(key) { return ticketLabel(key,this.ticketNames.get(key)); },
    evidenceLabel(id) {
      const event = this.run?.events?.find(event => event.id === id);
      return event ? this.ticketLabel(event.issueKey) : '근거 미확인';
    },
    eventLabel(event) { return EVENT_LABELS[event.kind] || '활동'; },
    rowTitle(row) { return (row.key ? row.key+' ' : '')+row.title+(row.contextOnly ? ' · 계층 연결용 · 하위 대상의 활동 근거만 집계' : ''); },
    statusLabel(status) { return RUN_LABELS[status] || status; },
    selectAll(kind) { this[kind] = []; },
    isSelected(kind,id) { return this[kind].includes(id); },
    kindLabel(kind) { return ({module:'모듈',wbs:'WBS',epic:'Epic',task:'Task',subtask:'Sub',sub:'Sub','sub-task':'Sub'})[String(kind).toLowerCase()] || kind || 'Task'; },
    dateLabel(value) { if (!value || value === '날짜 미확인') return value || '미확인'; return value.replace(/^(\d{4})-(\d{2})-(\d{2}).*$/, '$1.$2.$3'); },
    timeLabel(value) { const d = new Date(value); return Number.isFinite(d.getTime()) ? d.toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false}) : ''; },
    timestamp(value) { const d = new Date(value); return Number.isFinite(d.getTime()) ? d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) : '미확인'; },
    async initialize() {
      const results = await Promise.allSettled([api.raw('/api/tracking/options'),api.raw(basePath)]);
      if (results[0].status === 'fulfilled') {
        this.options = results[0].value || {people:[],modules:[]}; this.optionsLoaded = true; this.optionsError = '';
        const defaults = this.options.defaults;
        if (!this.run && defaults) { this.startDate = defaults.startDate || this.startDate; this.endDate = defaults.endDate || this.endDate; this.people = defaults.people || []; this.modules = defaults.modules || []; }
      } else this.optionsError = results[0].reason?.message || '대상 목록을 불러오지 못했습니다.';
      if (results[1].status === 'fulfilled') {
        this.recent = results[1].value?.runs || [];
        if (!this.run && !this.pending && this.recent.length) this.openRun(this.recent[0].id);
      } else this.error = results[1].reason?.message || '저장된 실행을 불러오지 못했습니다.';
    },
    preset(name) { Object.assign(this,dateRange(name)); },
    togglePicker(kind) { this.picker = this.picker === kind ? '' : kind; this.pickerQuery = ''; },
    toggleValue(kind,id) { this[kind] = this[kind].includes(id) ? this[kind].filter(value => value !== id) : [...this[kind],id]; },
    selectionLabel(kind) {
      const ids = this[kind]; if (!ids.length) return kind === 'people' ? '전체 사람' : '전체 모듈';
      const first = (kind === 'people' ? this.peopleOptions : this.options[kind] || []).find(option => option.id === ids[0]);
      return (first?.name || ids[0]) + (ids.length > 1 ? ' 외 '+(ids.length-1)+(kind === 'people' ? '명' : '개') : '');
    },
    adoptRun(run, restore = false) {
      if (!run?.id) throw new Error('수집 실행 응답을 확인할 수 없습니다. 최근 실행에서 다시 여세요.');
      const names = {...this.personNames};
      const remember = (id,name,overwrite = false) => {
        if (typeof id !== 'string' || typeof name !== 'string' || !id.trim() || !name.trim()) return;
        const key = id.toLocaleLowerCase();
        if (key !== name.trim().toLocaleLowerCase() && (overwrite || !names[key])) names[key] = name.trim();
      };
      for (const issue of run.issues || []) remember(issue.assignee?.id || issue.assigneeId,issue.assignee?.name || issue.assigneeName,true);
      for (const event of run.events || []) remember(event.actorId,event.actorName);
      this.personNames = names;
      const changed = this.run?.id !== run.id; this.run = run; this.recentId = run.id; this.pollError = '';
      this.recent = [run,...this.recent.filter(item => item.id !== run.id)].slice(0,20);
      if (changed) {
        this.closed = {}; this.scopeId = ''; this.scopeClosed = {}; this.reportScopeParentId = ''; this.reportEvidenceOpen = false;
        this.selectedId = ''; this.selectedEventId = ''; this.ai = run.aiSummary || null; this.aiError = '';
        for (const row of this.allRows) if (row.kind === 'epic' && row.children.length) this.scopeClosed[row.id] = true;
      }
      if (restore && run.request) { this.startDate = run.request.startDate; this.endDate = run.request.endDate; this.people = [...(run.request.people || [])]; this.modules = [...(run.request.modules || [])]; }
      this.schedulePoll();
    },
    schedulePoll() {
      clearTimeout(this._pollTimer);
      if (!this.active || !running(this.run) || this.pollError) return;
      const id = this.run.id, epoch = this._epoch;
      this._pollTimer = setTimeout(async () => {
        try { const run = await api.raw(basePath+'/'+encodeURIComponent(id)); if (this._epoch === epoch && this.run?.id === id) this.adoptRun(run); }
        catch (error) { if (this._epoch === epoch) this.pollError = error.message || '수집 상태를 확인하지 못했습니다.'; }
      },1200);
    },
    async openRun(id,restore = true) {
      if (!id) return;
      const epoch = ++this._epoch; clearTimeout(this._pollTimer); this.pending = true; this.error = ''; this.aiPending = false;
      try { const run = await api.raw(basePath+'/'+encodeURIComponent(id)); if (epoch === this._epoch) this.adoptRun(run,restore); }
      catch (error) { if (epoch === this._epoch) this.error = error.message || '실행을 열지 못했습니다.'; }
      finally { if (epoch === this._epoch) this.pending = false; }
    },
    async collect() {
      if (this.validation || this.busy || !this.optionsLoaded) return;
      const epoch = ++this._epoch; this.pending = true; this.error = ''; this.picker = ''; this.aiPending = false; clearTimeout(this._pollTimer);
      try {
        const run = await api.raw(basePath,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({startDate:this.startDate,endDate:this.endDate,people:this.people,modules:this.modules})});
        if (epoch === this._epoch) this.adoptRun(run);
      } catch (error) { if (epoch === this._epoch) this.error = (error.message || '수집을 시작하지 못했습니다.') + (error.uncertain ? ' 최근 실행을 확인한 뒤 다시 시도하세요.' : ''); }
      finally { if (epoch === this._epoch) this.pending = false; }
    },
    async runAction(action) {
      if (this.pending || !this.run) return;
      const id = this.run.id, epoch = ++this._epoch; this.pending = true; this.error = ''; clearTimeout(this._pollTimer);
      try { const run = await api.raw(basePath+'/'+encodeURIComponent(id)+'/'+action,{method:'POST'}); if (epoch === this._epoch) this.adoptRun(run); }
      catch (error) { if (epoch === this._epoch) { this.error = error.message || '실행 상태를 변경하지 못했습니다.'; this.schedulePoll(); } }
      finally { if (epoch === this._epoch) this.pending = false; }
    },
    fold(row) { this.closed = {...this.closed,[row.id]:!this.closed[row.id]}; },
    foldScope(row) { this.scopeClosed = {...this.scopeClosed,[row.id]:!this.scopeClosed[row.id]}; },
    selectScope(id) {
      if (!id?.startsWith('tracking-report:')) this.reportScopeParentId = '';
      this.selectedId = id || ''; this.selectedEventId = ''; this.evidenceSource = 'row';
      if (this.summaryDisabled) { this.openEvidence(); return; }
      this.scopeId = id || ''; this.reportEvidenceOpen = false;
      if (this.mode === 'tree') this.mode = 'report';
    },
    selectTab(tab) {
      if (tab === 'summary') {
        if (this.summaryDisabled) return;
        this.scopeId = this.selectedId || ''; this.reportEvidenceOpen = false;
      } else { this.evidenceSource = 'row'; this.openEvidence(); }
    },
    tabKey(event) {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const detail = this.summaryDisabled || event.key === 'End' || (event.key !== 'Home' && !this.reportEvidenceOpen);
      this.selectTab(detail ? 'detail' : 'summary');
      this.$nextTick(() => this.$refs[detail ? 'detailTab' : 'summaryTab']?.focus({preventScroll:true}));
    },
    inspectHierarchy() {
      const mode = this.mode === 'tree' ? 'report' : 'tree';
      if (this.summaryDisabled) this.selectScope(this.scopeId);
      else this.selectTab('summary');
      this.mode = mode;
    },
    focusEvidencePanel() {
      if (typeof document === 'undefined') return;
      if (this.$refs.scopeInsights) this.$refs.scopeInsights.open = false;
      this.$nextTick(() => { this.$refs.detailTab?.focus({preventScroll:true}); this.$refs.detailTabs?.scrollIntoView({block:'nearest'}); });
    },
    openEvidence() { this.reportEvidenceOpen = true; this.focusEvidencePanel(); },
    selectRow(row) { this.selectedId = row.id; this.selectedEventId = ''; this.evidenceSource = 'row'; this.openEvidence(); },
    selectReport(item) {
      const row = trackingRows(this.scopedRun).find(row => row.key === item.key);
      if (!row && !this.scopeId.startsWith('tracking-report:')) this.reportScopeParentId = this.scopeId;
      this.selectedId = row?.id || item.rowId; this.selectedEventId = ''; this.evidenceSource = 'report'; this.openEvidence();
    },
    selectOverview(key) { const item = this.scopedReport.items.find(item => item.key === key); if (item) this.selectReport(item); },
    selectEvent(event) {
      this.evidenceSource = 'row';
      const row = trackingRows(this.scopedRun).find(row => row.key === event.issueKey) || this.allRows.find(row => row.key === event.issueKey);
      if (row) this.selectedId = row.id;
      this.selectedEventId = event.id;
      this.openEvidence();
      this.$nextTick(() => document.getElementById('tracking-event-'+event.id)?.scrollIntoView({block:'nearest'}));
    },
    openTicket(key) { if (key) window.dispatchEvent(new CustomEvent('lake-open-ticket',{detail:{key}})); },
    clearHighlight() { this.highlightValue = ''; },
    summaryLabel(value) { return value == null || (!value && this.provisional) ? '미확인' : String(value) + (this.provisional ? '+' : ''); },
    async summarize() {
      if (!this.run || this.aiPending || !this.options.aiEnabled) return;
      const id = this.run.id, epoch = this._epoch; this.aiPending = true; this.aiError = '';
      try {
        const result = await api.raw(basePath+'/'+encodeURIComponent(id)+'/summary',{method:'POST',timeoutMs:120000});
        if (this.run?.id === id && this._epoch === epoch) {
          this.ai = result;
          this.run.aiSummary = result;
          this.run.metrics = {...this.run.metrics,llmTokens:(this.run.metrics?.llmTokens || 0)+(result.usage?.totalTokens || 0)};
        }
      }
      catch (error) { if (this.run?.id === id && this._epoch === epoch) this.aiError = error.message || 'AI 요약을 만들지 못했습니다.'; }
      finally { if (this.run?.id === id && this._epoch === epoch) this.aiPending = false; }
    },
    focusEvidence(id) { const event = this.run?.events?.find(event => event.id === id); if (event) this.selectEvent(event); },
  },
  template: `
  <section class="tracking tracking-overview" aria-labelledby="tracking-title" @keydown.esc="picker = ''">
    <header class="tr-heading">
      <div><h1 id="tracking-title">Tracking</h1><p>기간의 성과, 지금의 진척</p></div>
      <div class="tr-heading-actions">
        <details class="tr-scope-note"><summary>조회 기준</summary><p>시작일·종료일 포함 · 한국 시간 · 기간 중 담당 업무와 본인 활동 · 조직과 WBS는 현재 설정 기준</p></details>
        <label v-if="recent.length" class="tr-recent"><span>최근 실행</span><select :value="recentId" @change="openRun($event.target.value)" :disabled="pending" aria-label="최근 수집 실행 열기">
          <option v-for="item in recent" :key="item.id" :value="item.id">{{ dateLabel(item.request?.startDate) }} – {{ dateLabel(item.request?.endDate) }} · {{ statusLabel(item.status) }}</option>
        </select></label>
      </div>
    </header>
    <form class="tr-scope" @submit.prevent="collect">
      <fieldset class="tr-dates"><legend class="tr-sr">조회 기간 · 한국 시간, 양끝 날짜 포함</legend><strong>기간</strong>
        <input type="date" v-model="startDate" :max="endDate || today" aria-label="시작일" required><span>–</span><input type="date" v-model="endDate" :min="startDate" :max="today" aria-label="종료일" required>
        <div class="tr-presets"><button type="button" @click="preset('today')">오늘</button><button type="button" @click="preset('week')">최근 7일</button><button type="button" @click="preset('lastWeek')">지난주</button><button type="button" @click="preset('month')">30일</button></div>
      </fieldset>
      <div class="tr-picker" v-for="kind in ['modules','people']" :key="kind">
        <span>{{ kind === 'modules' ? '모듈' : '사람' }}</span><button type="button" class="tr-picker-button" @click="togglePicker(kind)" :aria-expanded="picker === kind" :disabled="!optionsLoaded">{{ selectionLabel(kind) }}<span aria-hidden="true">▾</span></button>
        <div v-if="picker === kind" class="tr-picker-menu"><input v-model="pickerQuery" :aria-label="kind === 'people' ? '사람 찾기' : '모듈 찾기'" placeholder="찾기"><button type="button" class="tr-picker-all" @click="selectAll(kind)">선택 해제 · 전체 조회</button>
          <label v-for="option in pickerOptions" :key="option.id"><input type="checkbox" :checked="isSelected(kind,option.id)" @change="toggleValue(kind,option.id)"><span>{{ option.name }}</span></label>
          <p v-if="!pickerOptions.length" class="tr-menu-empty">일치하는 대상이 없습니다.</p><button type="button" class="tr-picker-done" @click="picker = ''">선택 완료</button>
        </div>
      </div>
      <button type="submit" class="tr-button tr-primary" :disabled="!!validation || busy || !optionsLoaded">{{ busy ? '수집 중…' : '조회' }}</button>
    </form>
    <div v-if="picker" class="tr-picker-backdrop" @click="picker = ''"></div>
    <p v-if="validation" class="tr-error" role="alert">{{ validation }}</p>
    <p v-if="optionsError" class="tr-error" role="alert">{{ optionsError }} <button class="tr-inline" @click="initialize">다시 연결</button></p>
    <p v-if="error" class="tr-error" role="alert">{{ error }} <button class="tr-inline" @click="initialize">최근 실행 확인</button></p>
    <template v-if="run">
      <div class="tr-run-status" :class="{ 'tr-incomplete':provisional }" aria-live="polite"><strong>{{ runLabel }}</strong><span>{{ dateLabel(run.request?.startDate) }} – {{ dateLabel(run.request?.endDate) }}</span>
        <span v-if="provisional">확인된 활동만 표시 · 최종 합계가 아닙니다</span><span v-else>{{ timestamp(run.asOf) }} 기준</span>
        <span v-if="changedRequest" class="tr-changed">조건이 바뀌었습니다. 조회하면 새 실행으로 수집합니다.</span>
        <button v-if="canResume" class="tr-inline" @click="runAction('resume')" :disabled="pending">이어서 수집</button><button v-if="['queued','running'].includes(run.status)" class="tr-inline" @click="runAction('cancel')" :disabled="pending">수집 취소</button>
      </div>
      <div v-if="pollError" class="tr-error" role="alert">{{ pollError }} 저장된 결과는 유지됩니다. <button class="tr-inline" @click="openRun(run.id,false)">상태 다시 확인</button></div>
      <div class="tr-workspace tr-workspace-report tr-workspace-scoped" :style="{'--tracking-nav-width':hierarchyWidth+'px'}">
        <TrackingNavigator :width="hierarchyWidth" @resize="hierarchyWidth = $event" id="tracking-hierarchy" :rows="navigationRows" :active-id="selectedId || scopeId" :closed="scopeClosed" :total="report.items.length" :mode="mode" :reveal-matches="matchedOnly && !!(query || highlight)" @scope="selectScope" @fold="foldScope" @inspect="inspectHierarchy" />
        <TrackingSplitter :width="hierarchyWidth" @resize="hierarchyWidth = $event" />
        <section class="tr-content">
      <div class="tr-scope-context"><nav class="tr-scope-breadcrumb" :aria-label="reportEvidenceOpen ? '현재 업무 위치' : '현재 보고 범위'"><button @click="selectScope('')" :aria-current="!scopeId && !reportEvidenceOpen ? 'location' : undefined">전체 업무</button><template v-for="part in reportEvidenceOpen ? evidenceBreadcrumbs : scope.breadcrumbs" :key="part.id"><span aria-hidden="true">/</span><span v-if="reportEvidenceOpen && part.id === selectedId" aria-current="page" :title="part.title">{{ part.title }}</span><button v-else :title="part.title" :aria-current="!reportEvidenceOpen && part.id === scopeId ? 'location' : undefined" @click="selectScope(part.id)">{{ part.title }}</button></template><span v-if="!scope.valid">선택 범위 미확인</span></nav>
        <div v-show="!reportEvidenceOpen" class="tr-scope-metrics"><span>완료 이력 <b>{{ overview.totals.rootCompletionHistory }}</b></span><span>그중 현재 완료 <b>{{ overview.totals.currentDoneAfterCompletion }}</b></span><span>댓글 <b>{{ overview.totals.comments }}</b></span><span v-if="overview.totals.provisional">잠정</span></div>
        <details v-show="!reportEvidenceOpen" ref="scopeInsights" class="tr-scope-insights"><summary>집계·AI</summary><div class="tr-scope-insights-popover"><TrackingOverview section="rail" :overview="overview" :scope-label="scopeLabel" @select="selectOverview">${AI_SUMMARY_TEMPLATE}</TrackingOverview></div></details>
      </div>
      <div ref="detailTabs" class="tr-detail-tabs" role="tablist" aria-label="선택 업무 보기" @keydown="tabKey">
        <button id="tracking-summary-tab" ref="summaryTab" role="tab" :aria-selected="!reportEvidenceOpen" aria-controls="tracking-summary" :tabindex="!reportEvidenceOpen ? 0 : -1" :disabled="summaryDisabled" :title="summaryDisabled ? '하위 업무가 없는 항목은 상세에서 확인합니다' : '선택 업무와 하위 업무 요약'" @click="selectTab('summary')">요약</button>
        <button id="tracking-detail-tab" ref="detailTab" role="tab" :aria-selected="reportEvidenceOpen" aria-controls="tracking-detail" :tabindex="reportEvidenceOpen ? 0 : -1" @click="selectTab('detail')">상세 <span>변경 · 댓글</span></button>
        <small v-if="summaryDisabled">하위 업무 없음</small>
      </div>
      <div v-show="!reportEvidenceOpen"><TrackingOverview v-if="mode === 'report'" :overview="overview" :scope-label="scopeLabel" @select="selectOverview" /></div>
      <div v-if="mode !== 'report' && !reportEvidenceOpen" class="tr-summary" aria-label="선택 범위의 기간 내 활동 집계"><span><b>{{ provisional ? '확인된 활동 티켓' : '활동 티켓' }}</b><strong>{{ summaryLabel(scopeActivity.uniqueIssues) }}</strong></span><span>생성 <strong>{{ summaryLabel(scopeActivity.created) }}</strong></span><span class="tr-completed">완료 <strong>{{ summaryLabel(scopeActivity.completed) }}</strong></span><span>댓글 <strong>{{ summaryLabel(scopeActivity.comments) }}</strong></span><span title="필요한 이력에서 확보한 변경만 표시합니다">확인된 변경 <strong>{{ summaryLabel(scopeActivity.fieldChanges) }}</strong></span></div>
      <div class="tr-toolbar"><div v-show="!reportEvidenceOpen" class="tr-segment" role="group" aria-label="활동 보기"><button :class="{on:mode !== 'feed'}" :aria-pressed="mode !== 'feed'" @click="mode = 'report'">업무 요약</button><button :class="{on:mode === 'feed'}" :aria-pressed="mode === 'feed'" @click="mode = 'feed'">날짜별 활동</button><span v-if="mode === 'tree'" class="tr-muted">계층별 집계</span></div>
        <input type="search" class="tr-search" v-model="query" placeholder="티켓 · Epic · 사람 검색" aria-label="수집 결과에서 검색">
        <div class="tr-highlight-controls"><span>강조</span><select v-model="highlightKind" @change="clearHighlight" aria-label="강조 대상 종류"><option value="person">사람</option><option value="task">Task</option><option value="epic">Epic</option></select><select v-model="highlightValue" aria-label="강조 대상"><option value="">선택 안 함</option><option v-for="option in highlightOptions" :key="option.id" :value="option.id">{{ option.name }}</option></select><button v-if="highlightValue" class="tr-clear" @click="clearHighlight" aria-label="강조 해제">×</button></div>
        <label class="tr-matched-only"><input type="checkbox" v-model="matchedOnly">일치 항목만</label>
      </div>
        <div v-show="!reportEvidenceOpen" id="tracking-summary" class="tr-results" role="tabpanel" aria-labelledby="tracking-summary-tab">
          <TrackingReport v-if="mode === 'report'" :key="run.id" :report="scopedReport" :run="run" :groups="scope.groups" :scope-id="scopeId" :scope-label="scopeLabel" :selected-key="reportEvidenceOpen ? selected?.key : ''" :match-keys="[...matchKeys]" :has-match="!!(query || highlight)" :filter-matches="matchedOnly" @select="selectReport" @evidence="focusEvidence" @scope="selectScope" />
          <div v-else-if="mode === 'tree'" class="tr-table-scroll"><table class="tr-table"><colgroup><col class="tr-title-col"><col class="tr-assignee-col"><col v-for="i in 4" :key="i" class="tr-count-col"></colgroup><thead><tr><th scope="col">업무</th><th scope="col">현재 담당</th><th scope="col">생성</th><th scope="col">완료</th><th scope="col">댓글</th><th scope="col" title="확보한 이력 기준">변경</th></tr></thead><tbody>
            <tr v-for="row in rows" :key="row.id" :class="['tr-row','tr-kind-'+row.kind,{'tr-selected':selectedId === row.id,'tr-highlighted':row.highlighted,'tr-search-match':row.matched && query}]" @click="selectRow(row)">
              <td><div class="tr-tree-label" :style="{'--tr-depth':row.depth}"><button v-if="row.children.length" class="tr-fold" @click.stop="fold(row)" :aria-expanded="!closed[row.id] || (matchedOnly && !!(query || highlight))" :aria-label="row.title + (closed[row.id] ? ' 펼치기' : ' 접기')"><svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="1.5" y="1.5" width="13" height="13" rx="1"/><path d="M4.5 8h7"/><path v-if="closed[row.id] && !(matchedOnly && (query || highlight))" d="M8 4.5v7"/></svg></button><span v-else class="tr-fold-space"></span><span class="tr-type" :class="'tr-type-'+row.kind">{{ row.virtual ? (row.kind === 'wbs' ? '가상 WBS' : '가상 Epic') : kindLabel(row.kind) }}</span><button class="tr-row-title" @click.stop="selectRow(row)" :title="rowTitle(row)">{{ row.key ? ticketTitle(row.key) : row.title }}<span v-if="row.key" class="tr-row-key">{{ row.key }}</span></button><span v-if="closed[row.id] && row.children.length" class="tr-child-count">하위 {{ row.keys.size }}</span></div></td>
              <td><button v-if="row.issue?.assigneeId" class="tr-person" :class="{'tr-person-on':highlightKind === 'person' && highlightValue === row.issue.assigneeId}" @click.stop="highlightKind = 'person'; highlightValue = row.issue.assigneeId">{{ row.issue.assigneeName || row.issue.assigneeId }}</button><span v-else class="tr-muted">–</span></td>
              <td>{{ countLabel(row.counts.created,!!row.issue || row.counts.uniqueIssues > 0) }}</td><td class="tr-completed">{{ countLabel(row.counts.completed,row.counts.historyComplete) }}</td><td :class="{'tr-unknown':!row.counts.commentsComplete}">{{ countLabel(row.counts.comments,row.counts.commentsComplete) }}</td><td :class="{'tr-unknown':!row.counts.historyComplete}">{{ countLabel(row.counts.fieldChanges,row.counts.historyComplete) }}</td>
            </tr>
          </tbody></table><div v-if="!rows.length" class="tr-empty">{{ query || highlight ? '일치하는 활동이 없습니다. 검색어나 강조 대상을 바꿔 보세요.' : (provisional ? '아직 확인된 업무가 없습니다. 수집 상태와 확인 범위를 확인하세요.' : '선택한 기간과 대상의 활동이 없습니다.') }}</div></div>
          <div v-else class="tr-feed"><section v-for="group in feedGroups" :key="group.date"><h3>{{ dateLabel(group.date) }} <small>한국 시간</small></h3><button v-for="event in group.events" :key="event.id" :class="['tr-feed-event',{'tr-feed-match':matchKeys.has(event.issueKey),'tr-feed-selected':selectedEventId === event.id}]" @click="selectEvent(event)"><time>{{ timeLabel(event.date) }}</time><span class="tr-event-kind" :class="'tr-event-'+event.kind">{{ eventLabel(event) }}</span><span class="tr-feed-content"><span class="tr-feed-ticket"><strong>{{ ticketTitle(event.issueKey) }}</strong><small>{{ event.issueKey }}</small></span><span class="tr-feed-body">{{ eventText(event) }}</span></span><span class="tr-muted">{{ event.actorName || event.actorId || '작성자 미확인' }}</span></button></section><div v-if="!feedGroups.length" class="tr-empty">표시할 기간 내 활동이 없습니다.</div></div>
        </div>
        <section v-if="reportEvidenceOpen" id="tracking-detail" ref="evidencePane" class="tr-evidence tr-evidence-inline" role="tabpanel" aria-labelledby="tracking-detail-tab">
          <p class="tr-evidence-scope-label">{{ dateLabel(run.request?.startDate) }} – {{ dateLabel(run.request?.endDate) }} · {{ !selectedId ? '전체 업무의 근거' : evidenceSource === 'report' ? '요약 업무의 근거' : selected?.children?.length ? '선택 계층과 하위 업무의 근거' : '선택 업무의 근거' }}</p>
          <div class="tr-evidence-head"><h2><button v-if="selected?.key" class="tr-ticket-title" @click="openTicket(selected.key)">{{ ticketTitle(selected.key) }}</button><template v-else>{{ selected?.title || '전체 업무' }}</template></h2><span v-if="selected?.key" class="tr-ticket-key">{{ selected.key }}</span><div v-if="selectedIssue" class="tr-current"><span :class="['tr-current-status',{'tr-current-done':selectedIssue.statusCategory === 'done'}]">현재 {{ selectedIssue.status || '상태 미확인' }}</span><span class="tr-muted">{{ selectedIssue.assigneeName || '미할당' }}</span></div></div>
            <section v-if="selectedBriefSummary" class="tr-evidence-brief"><h3>기간 내 확인된 변화</h3><strong class="tr-evidence-outcome">{{ selectedBriefSummary.headline }}</strong><p>{{ selectedBriefSummary.remainingLabel }}</p><ul v-if="selectedBrief.completedKeys.some(key => key !== selectedBrief.key)" class="tr-evidence-deliveries"><li v-for="child in selectedBrief.children.filter(child => selectedBrief.completedKeys.includes(child.key))" :key="child.key"><button class="tr-inline" @click="openTicket(child.key)">{{ ticketLabel(child.key) }}</button><span>완료 이력<span v-if="selectedBrief.reopenedKeys.includes(child.key)"> · 재오픈 이력도 있음</span></span></li></ul></section>
            <div class="tr-evidence-summary"><strong>기간 내 활동 근거</strong><span>{{ selectedEvents.length }}건{{ provisional ? ' 확인' : '' }}</span></div>
            <div class="tr-evidence-events"><section class="tr-timeline-day" v-for="group in evidenceGroups" :key="group.date"><h3>{{ dateLabel(group.date) }}</h3><article v-for="event in group.events" :key="event.id" :id="'tracking-event-'+event.id" :class="['tr-evidence-event',{'tr-event-focused':selectedEventId === event.id,'tr-evidence-match':!!(query || highlight) && matchKeys.has(event.issueKey)}]"><div class="tr-event-top"><span class="tr-event-kind" :class="'tr-event-'+event.kind">{{ eventLabel(event) }}</span><span>{{ event.actorName || event.actorId || '작성자 미확인' }}</span><time>{{ timeLabel(event.date) }}</time></div><button v-if="!selectedIssue || event.issueKey !== selected?.key" class="tr-evidence-ticket tr-inline" @click="openTicket(event.issueKey)">{{ ticketLabel(event.issueKey) }}</button><TrackingComment v-if="['comment','commentEdited'].includes(event.kind)" :event="event" /><p v-else>{{ eventText(event) }}</p><small v-if="event.kind === 'comment' || event.kind === 'commentEdited'">{{ event.kind === 'commentEdited' ? '관측된 마지막 수정 · 과거 편집본 복원 제외' : '확인된 댓글 원문' }}</small></article></section><p v-if="!selectedEvents.length" class="tr-empty">{{ matchedOnly && (query || highlight) ? '일치하는 활동이 없습니다. 검색어나 강조 대상을 바꿔 보세요.' : provisional ? '확인된 기간 내 활동이 없습니다. 미수집 이력이 남아 있을 수 있습니다.' : '이 업무의 기간 내 활동이 없습니다.' }}</p></div>
            <footer class="tr-evidence-footer"><span>{{ selected?.contextOnly ? '계층 연결용 · 하위 근거 기준' : ((selected ? selected.counts.historyComplete : coverage.historyComplete) ? '확보한 이력 기준' : '일부 이력 미확인') }}</span><button v-if="selected?.key" class="tr-button" @click="openTicket(selected.key)">티켓 열기 ↗</button></footer>
        </section>
        </section>
      </div>
      <footer v-show="!reportEvidenceOpen" class="tr-footnote"><span>선택 범위 활동 티켓 {{ summaryLabel(scopeActivity.uniqueIssues) }} · 공유 티켓 중복 제외</span><span>변경은 확보한 이력만 표시 · 미확인을 0으로 집계하지 않음</span></footer>
      <details class="tr-audit"><summary>전체 조회 수집 범위와 비용 · {{ runLabel }}</summary><div class="tr-audit-body"><p>생성·완료는 고유 티켓 수입니다. 재오픈·재완료 사건은 상세 근거에 남습니다. 댓글은 작성일·작성자 기준이며 삭제된 댓글과 과거 편집본은 복원하지 않습니다.</p><p>대상 티켓 이력 확인 {{ historyConfirmed }}/{{ targetIssues.length }} · 전체 조회 기준</p><p>후보 검색 {{ coverage.queriesComplete ? '완료' : '미완료' }} · 전체 확인 {{ coverage.issuesComplete ?? '미확인' }}/{{ coverage.issuesTotal ?? '미확인' }} · 댓글 확인 {{ coverage.commentsComplete ? '완료' : '일부 미확인' }} · 변경 이력 확인 {{ coverage.historyComplete ? '완료' : '일부 미확인' }}</p><p>수집 REST {{ metrics.restRequests ?? '미확인' }}회 (인증 요청 제외) · 응답 {{ metrics.responseBytes == null ? '미확인' : (metrics.responseBytes / 1024).toFixed(1) + ' KB' }} · 재시도 {{ metrics.retries ?? '미확인' }}회 · 캐시 재사용 {{ metrics.cacheHits ?? '미확인' }}회 · {{ metrics.elapsedMs == null ? '시간 미확인' : (metrics.elapsedMs / 1000).toFixed(1) + '초' }} · MCP {{ metrics.mcpCalls ?? 0 }}회 · LLM 토큰 {{ metrics.llmTokens ?? 0 }}</p><ul v-if="coverage.missing?.length"><li v-for="(item,i) in coverage.missing" :key="i">{{ item.issueKey ? ticketLabel(item.issueKey) : '검색' }} · {{ item.source }} · {{ item.reason }}</li></ul><ul v-if="run.errors?.length"><li v-for="(item,i) in run.errors" :key="i">{{ typeof item === 'string' ? item : item.message || item.reason || item.error }}</li></ul></div></details>
    </template>
    <div v-else class="tr-start"><h2>{{ pending ? '저장된 실행을 여는 중…' : '기간과 대상을 선택해 활동을 확인하세요' }}</h2><p>생성·완료·댓글과 확보한 변경 이력을 업무 계층으로 모읍니다.</p><p>조회 버튼을 누르면 수집을 시작합니다. 검색·강조·보기 전환은 추가 Jira 조회 없이 동작합니다.</p></div>
  </section>`,
};
