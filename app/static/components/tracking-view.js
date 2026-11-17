import { api } from '../lib/api.js';
import { kstToday, dateRange, dateError, trackingRows, isTerminalTrackingRow, countLabel, groupEvents, eventText, readableTicketTitle, ticketLabel, EVENT_LABELS, RUN_LABELS } from './trackingModel.js';
import { buildTrackingReport, reportText } from './trackingReportModel.js';
import { projectTrackingVoc } from './trackingVocModel.js';
import { projectTrackingHierarchy } from './trackingHierarchyModel.js';
import { buildTrackingOverview, trackingRowSummary } from './trackingOverviewModel.js';
import { buildTrackingScope } from './trackingScopeModel.js';
import { buildTrackingCollectionAudit } from './trackingCollectionModel.js';
import TrackingSplitter from './tracking-splitter.js';

import TrackingBriefing from './tracking-briefing.js';
import TrackingReferenceNav, { TrackingReferenceIcon } from './tracking-reference-nav.js';
import TrackingReferenceDetail from './tracking-reference-detail.js';
import referenceTemplate from './tracking-reference-template.js';
const readPreference = (key, fallback) => {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (_) { return fallback; }
};
const referenceNavWidth = () => typeof window !== 'undefined' && window.innerWidth <= 1280 ? 236 : 252;

const running = run => ['queued','running'].includes(run?.status);
const basePath = '/api/tracking/runs';
export default {
  name: 'TrackingView',
  components: { TrackingBriefing, TrackingReferenceNav, TrackingReferenceDetail, TrackingReferenceIcon, TrackingSplitter },
  data() {
    return { ...dateRange(), options:{people:[],modules:[]}, optionsLoaded:false, optionsError:'', people:[],modules:[],personNames:{},
      run:null,recent:[],recentId:'',pending:false,error:'',pollError:'',query:'',highlightKind:'person',highlightValue:'',
      matchedOnly:false,mode:'report',closed:{},selectedId:'',selectedEventId:'',picker:'',pickerQuery:'',reportEvidenceOpen:false,
      ai:null,aiPending:false,aiError:'',active:true,scopeId:'',scopeClosed:{},evidenceSource:'row',hierarchyWidth:referenceNavWidth(),referenceNavWidth:referenceNavWidth(),reportScopeParentId:'',
      treeCollapsed:readPreference('ltm.tracking.treeCollapsed',false) === true,
      pins:(value => Array.isArray(value) ? value.filter(key => typeof key === 'string') : [])(readPreference('ltm.tracking.report.pins',[])),
      overlay:'',reportCopyText:'',copyState:'' };
  },
  computed: {
    allSelectedEvents() {
      return (this.run?.events || []).filter(event => !this.selectedId || this.selected?.keys.has(event.issueKey));
    },
    observationLabel() {
      const times = (this.run?.issues || []).map(issue => Date.parse(issue.fetchedAt)).filter(Number.isFinite);
      return times.length ? this.timestamp(new Date(Math.max(...times)).toISOString())+' 관측' : '관측 시각 미확인';
    },
    overlayTitle() { return {history:'이전 조회',copy:'보고서 복사',ai:'AI 업무 요약',audit:'수집 범위와 비용'}[this.overlay] || 'Tracking'; },
    today: () => kstToday(),
    busy() { return this.pending || running(this.run); },
    validation() { return dateError(this.startDate,this.endDate); },
    highlight() { return this.highlightValue ? {kind:this.highlightKind,value:this.highlightValue} : null; },
    displayRun() { return projectTrackingHierarchy(projectTrackingVoc(this.run,{vocComponent:this.options.vocComponent})); },
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
      if (!this.selectedId && this.reportEvidenceOpen) return {id:'',title:'전체 업무',kind:'wbs',path:[],children:this.displayRun?.tree || [],keys:new Set(this.targetIssues.map(issue => issue.key)),counts:this.coverage};
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
    feedEvents() {
      const rows = this.rowsInScope();
      const keys = new Set(rows.filter(r => !this.matchedOnly || r.matched || (!this.query && !this.highlight)).map(r => r.key).filter(Boolean));
      return (this.run?.events || []).filter(event => keys.has(event.issueKey));
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
        (this.highlightKind === 'epic' ? row.kind === 'epic' : row.kind !== 'epic')).map(row => {
          const context = row.path.map(node => node.name || node.summary || node.key).filter(Boolean).join(' / ');
          const name = row.epicUnassigned ? row.title+' · '+(context || '전체')
            : row.virtual ? 'VOC · '+row.title : ticketLabel(row.key,row.title);
          return {id:row.key || row.id,name};
        });
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
    collectionAudit() {
      return buildTrackingCollectionAudit(this.run,{displayRun:this.displayRun,scopedRun:this.scopedRun,
        vocComponent:this.options.vocComponent,query:this.query,highlight:this.highlight,matchedOnly:this.matchedOnly});
    },
    collectionFilterLabel() {
      const highlight = this.highlight && this.highlightOptions.find(option => option.id === this.highlight.value);
      return ['보고 범위: '+this.scopeLabel,'검색: '+(this.query || '없음'),
        '강조: '+(highlight?.name || this.highlight?.value || '없음'),this.matchedOnly ? '일치 항목만' : '전체 항목'].join(' · ');
    },
    rootWorkCount() { return this.targetIssues.filter(issue => !issue.isSubtask && !String(issue.type || '').toLowerCase().replace(/[-_ ]/g,'').startsWith('sub')).length; },
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
    this._referenceResize = () => {
      const width = referenceNavWidth();
      try { if (window.localStorage.getItem('ltm.tracking.hierarchyWidth') == null) this.hierarchyWidth = width; } catch (_) {}
      this.referenceNavWidth = width;
    };
    window.addEventListener('resize',this._referenceResize);
    this._authRecovered = () => {
      if (!this.active) { this._authDirty = true; return; }
      if (!this.optionsLoaded || this.optionsError) this.initialize();
      else if (this.pollError && this.run) this.openRun(this.run.id,false);
    };
    this._cacheUpdated = event => {
      const {path,value} = event.detail || {};
      if (!value || value.error || value.partial) return;
      if (path === '/api/tracking/options') this.applyOptions(value);
      else if (path === basePath) this.recent = value.runs || this.recent;
      else if (path === basePath+'/'+encodeURIComponent(this.run?.id || '') && !running(this.run)) this.adoptRun(value);
    };
    window.addEventListener('auth-ok',this._authRecovered);
    window.addEventListener('api-cache-updated',this._cacheUpdated);
    this.initialize();
  },
  activated() {
    this.active = true;
    if (this._authDirty) { this._authDirty = false; this._authRecovered(); }
    this.schedulePoll();
  },
  deactivated() { this.active = false; clearTimeout(this._pollTimer); this.closeOverlay(); },
  unmounted() {
    this.active = false; this._epoch++; clearTimeout(this._pollTimer);
    window.removeEventListener('auth-ok',this._authRecovered);
    window.removeEventListener('api-cache-updated',this._cacheUpdated);
    window.removeEventListener('resize',this._referenceResize);
  },
  methods: {
    revealReferencePath(id) {
      const row = this.allRows.find(row => row.id === id);
      if (row) this.scopeClosed = {...this.scopeClosed,...Object.fromEntries(row.path.map(node => [node.id,false]))};
    },
    setTreeCollapsed(value) {
      this.treeCollapsed = !!value;
      try { localStorage.setItem('ltm.tracking.treeCollapsed',JSON.stringify(this.treeCollapsed)); } catch (_) {}
    },
    togglePin(key) {
      if (!key) return;
      this.pins = this.pins.includes(key) ? this.pins.filter(value => value !== key) : [...this.pins,key];
      try { localStorage.setItem('ltm.tracking.report.pins',JSON.stringify(this.pins)); } catch (_) {}
    },
    setHighlight(value) { this.highlightKind = value?.kind || 'person'; this.highlightValue = value?.value || ''; },
    inspectReference(id) {
      const row = this.allRows.find(row => row.id === id);
      if (row) this.selectRow(row);
      else if (!id) { this.selectedId = ''; this.openEvidence(); }
      else {
        const item = [...this.scopedReport.items,...this.report.items].find(item => item.rowId === id);
        if (item) this.selectReport(item);
      }
    },
    closeReferenceDetail() {
      if (this.summaryDisabled) this.selectScope(this.selected?.path?.at(-1)?.id || '');
      else { this.reportEvidenceOpen = false; this.selectedId = this.scopeId; this.selectedEventId = ''; }
    },
    showOverlay(name) {
      this.picker = ''; this.overlay = name; this.copyState = '';
      this._overlayFocus = typeof document !== 'undefined' ? document.activeElement : null;
      if (name === 'copy') this.reportCopyText = this.reportEvidenceOpen && this.summaryDisabled
        ? '보고 범위: '+this.selected.title+'\n'+reportText(buildTrackingScope(this.displayRun,this.selectedId).report,{request:this.run.request})
        : this.$refs.briefing?.reportText() || '';
      this.$nextTick(() => this.$refs.overlayDialog?.showModal());
    },
    closeOverlay() { this.$refs.overlayDialog?.close(); },
    overlayClosed() { this.overlay = ''; this._overlayFocus?.focus?.({preventScroll:true}); },
    dialogBackdrop(event) {
      if (event.target !== this.$refs.overlayDialog) return;
      const box = event.target.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) this.closeOverlay();
    },
    openPrevious(id) { this.closeOverlay(); this.openRun(id); },
    openAiEvidence(id) { this.closeOverlay(); this.focusEvidence(id); },
    async copyReport() {
      this.copyState = '';
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(this.reportCopyText);
        else { this.$refs.reportText.select(); if (!document.execCommand('copy')) throw new Error('Clipboard unavailable'); }
        this.copyState = '보고서를 복사했습니다.';
      } catch (_) { this.copyState = '복사하지 못했습니다. 내용을 선택해 직접 복사해 주세요.'; }
    },
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
    kindLabel(kind) { return ({module:'모듈',wbs:'WBS Task',epic:'Epic',task:'Task',subtask:'Sub',sub:'Sub','sub-task':'Sub'})[String(kind).toLowerCase()] || kind || 'Task'; },
    dateLabel(value) { if (!value || value === '날짜 미확인') return value || '미확인'; return value.replace(/^(\d{4})-(\d{2})-(\d{2}).*$/, '$1.$2.$3'); },
    timeLabel(value) { const d = new Date(value); return Number.isFinite(d.getTime()) ? d.toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false}) : ''; },
    timestamp(value) { const d = new Date(value); return Number.isFinite(d.getTime()) ? d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) : '미확인'; },
    applyOptions(value) {
      const first = !this.optionsLoaded;
      this.options = value || {people:[],modules:[]}; this.optionsLoaded = true; this.optionsError = '';
      const defaults = this.options.defaults;
      if (first && !this.run && defaults) {
        this.startDate = defaults.startDate || this.startDate; this.endDate = defaults.endDate || this.endDate;
        this.people = defaults.people || []; this.modules = defaults.modules || [];
      }
    },
    async initialize() {
      const cached = api.cached('/api/tracking/options');
      if (cached) this.applyOptions(cached);
      const recent = api.cached(basePath);
      if (recent?.runs) this.recent = recent.runs;
      const results = await Promise.allSettled([api.readCached('/api/tracking/options'),api.readCached(basePath)]);
      if (results[0].status === 'fulfilled') {
        this.applyOptions(results[0].value);
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
      const path = basePath+'/'+encodeURIComponent(id), cached = api.cached(path);
      if (cached && !running(cached)) this.adoptRun(cached,restore);
      try {
        // Reopening a stored result never starts historical collection. Running/retry reads stay live.
        const run = await (restore && !running(cached) ? api.readCached(path) : api.raw(path));
        if (epoch === this._epoch) this.adoptRun(run,restore);
      }
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
      try {
        const run = await api.raw(basePath+'/'+encodeURIComponent(id)+'/'+action,{method:'POST'});
        this.cacheStoredRun(run);
        if (epoch === this._epoch) this.adoptRun(run);
      }
      catch (error) { if (epoch === this._epoch) { this.error = error.message || '실행 상태를 변경하지 못했습니다.'; this.schedulePoll(); } }
      finally { if (epoch === this._epoch) this.pending = false; }
    },
    fold(row) { this.closed = {...this.closed,[row.id]:!this.closed[row.id]}; },
    foldScope(row) { this.scopeClosed = {...this.scopeClosed,[row.id]:!this.scopeClosed[row.id]}; },
    selectScope(id) {
      this.revealReferencePath(id);
      if (!id?.startsWith('tracking-report:')) this.reportScopeParentId = '';
      this.selectedId = id || ''; this.selectedEventId = ''; this.evidenceSource = 'row';
      if (this.summaryDisabled) { this.openEvidence(); return; }
      this.scopeId = id || ''; this.reportEvidenceOpen = false;
      if (this.mode === 'tree') this.mode = 'report';
    },
    focusEvidencePanel() {
      if (typeof document === 'undefined') return;
      this.$nextTick(() => this.$refs.referenceDetail?.$el?.querySelector?.('.tp-close-btn,.tp-leaf-back')?.focus({preventScroll:true}));
    },
    openEvidence() { this.reportEvidenceOpen = true; this.focusEvidencePanel(); },
    selectRow(row) { this.revealReferencePath(row.id); this.selectedId = row.id; this.selectedEventId = ''; this.evidenceSource = 'row'; this.openEvidence(); },
    selectReport(item) {
      const row = trackingRows(this.scopedRun).find(row => row.key === item.key);
      if (!row && !this.scopeId.startsWith('tracking-report:')) this.reportScopeParentId = this.scopeId;
      this.selectedId = row?.id || item.rowId; this.selectedEventId = ''; this.evidenceSource = 'report'; this.openEvidence();
    },
    selectOverview(key) { const item = this.scopedReport.items.find(item => item.key === key); if (item) this.selectReport(item); },
    selectEvent(event) {
      this.evidenceSource = 'row';
      const row = trackingRows(this.scopedRun).find(row => row.key === event.issueKey) || this.allRows.find(row => row.key === event.issueKey);
      if (row) { this.revealReferencePath(row.id); this.selectedId = row.id; }
      this.selectedEventId = event.id;
      this.openEvidence();
      this.$nextTick(() => document.getElementById('tracking-event-'+event.id)?.scrollIntoView({block:'nearest'}));
    },
    openTicket(key) { if (key) window.dispatchEvent(new CustomEvent('lake-open-ticket',{detail:{key}})); },
    clearHighlight() { this.highlightValue = ''; },
    summaryLabel(value) { return value == null || (!value && this.provisional) ? '미확인' : String(value) + (this.provisional ? '+' : ''); },
    cacheStoredRun(run) {
      if (!run?.id) return;
      api.replaceCached(basePath+'/'+encodeURIComponent(run.id), run);
      const list = api.cached(basePath);
      if (list?.runs) api.replaceCached(basePath, { ...list,
        runs: [run, ...list.runs.filter(item => item.id !== run.id)].slice(0,20) });
    },
    async summarize() {
      if (!this.run || this.aiPending || !this.options.aiEnabled) return;
      const id = this.run.id, epoch = this._epoch; this.aiPending = true; this.aiError = '';
      const source = { ...this.run, metrics: { ...this.run.metrics } };
      try {
        const result = await api.raw(basePath+'/'+encodeURIComponent(id)+'/summary',{method:'POST',timeoutMs:120000});
        const stored = { ...source, aiSummary: result,
          metrics: { ...source.metrics, llmTokens: (source.metrics.llmTokens || 0)+(result.usage?.totalTokens || 0) } };
        this.cacheStoredRun(stored);
        if (this.run?.id === id && this._epoch === epoch) {
          this.ai = result;
          this.run.aiSummary = result;
          this.run.metrics = stored.metrics;
        }
      }
      catch (error) { if (this.run?.id === id && this._epoch === epoch) this.aiError = error.message || 'AI 요약을 만들지 못했습니다.'; }
      finally { if (this.run?.id === id && this._epoch === epoch) this.aiPending = false; }
    },
    focusEvidence(id) { const event = this.run?.events?.find(event => event.id === id); if (event) this.selectEvent(event); },
  },
  template: referenceTemplate,
};
