import { api } from '../lib/api.js';
import { kstToday, dateRange, dateError, trackingRows, countLabel, groupEvents, eventText, EVENT_LABELS, RUN_LABELS } from './trackingModel.js';

const running = run => ['queued','running'].includes(run?.status);
const basePath = '/api/tracking/runs';
export default {
  name: 'TrackingView',
  data() {
    return { ...dateRange(), options:{people:[],modules:[]}, optionsLoaded:false, optionsError:'', people:[],modules:[],personNames:{},
      run:null,recent:[],recentId:'',pending:false,error:'',pollError:'',query:'',highlightKind:'person',highlightValue:'',
      matchedOnly:false,mode:'tree',closed:{},selectedId:'',selectedEventId:'',picker:'',pickerQuery:'',
      ai:null,aiPending:false,aiError:'',active:true };
  },
  computed: {
    today: () => kstToday(),
    busy() { return this.pending || running(this.run); },
    validation() { return dateError(this.startDate,this.endDate); },
    highlight() { return this.highlightValue ? {kind:this.highlightKind,value:this.highlightValue} : null; },
    rows() { return trackingRows(this.run,{query:this.query,highlight:this.highlight,matchedOnly:this.matchedOnly,closed:this.closed}); },
    allRows() { return trackingRows(this.run); },
    selected() { return this.allRows.find(row => row.id === this.selectedId) || null; },
    selectedIssue() { return this.selected?.issue || null; },
    selectedEvents() { return (this.run?.events || []).filter(e => this.selected?.keys.has(e.issueKey)); },
    evidenceGroups() { return groupEvents(this.selectedEvents); },
    feedGroups() {
      const rows = trackingRows(this.run,{query:this.query,highlight:this.highlight,matchedOnly:this.matchedOnly});
      const keys = new Set(rows.filter(r => !this.matchedOnly || r.matched || (!this.query && !this.highlight)).map(r => r.key).filter(Boolean));
      return groupEvents((this.run?.events || []).filter(event => keys.has(event.issueKey)));
    },
    matchKeys() { return new Set(this.rows.filter(r => r.matched).map(r => r.key)); },
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
      return this.allRows.filter(row => row.key && !seen.has(row.key) && seen.add(row.key) &&
        (this.highlightKind === 'epic' ? row.kind === 'epic' : row.kind !== 'epic')).map(row => ({id:row.key,name:row.key+' '+row.title}));
    },
    pickerOptions() {
      const values = this.picker === 'people' ? this.peopleOptions : this.options[this.picker] || [];
      return values.filter(value => (value.name+' '+value.id).toLocaleLowerCase().includes(this.pickerQuery.toLocaleLowerCase()));
    },
    provisional() { return this.run?.status !== 'complete' || !!this.run?.summary?.provisional || this.run?.coverage?.complete !== true; },
    summary() { return this.run?.summary || {}; },
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
      if (changed) { this.closed = {}; this.selectedId = ''; this.selectedEventId = ''; this.ai = run.aiSummary || null; this.aiError = ''; }
      if (restore && run.request) { this.startDate = run.request.startDate; this.endDate = run.request.endDate; this.people = [...(run.request.people || [])]; this.modules = [...(run.request.modules || [])]; }
      if (!this.selectedId) this.selectedId = this.allRows.find(row => row.issue && row.kind !== 'epic')?.id || this.allRows[0]?.id || '';
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
    selectRow(row) { this.selectedId = row.id; this.selectedEventId = ''; },
    selectEvent(event) {
      const row = this.allRows.find(row => row.key === event.issueKey);
      if (row) this.selectedId = row.id;
      this.selectedEventId = event.id;
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
  <section class="tracking" aria-labelledby="tracking-title" @keydown.esc="picker = ''">
    <header class="tr-heading">
      <div><h1 id="tracking-title">Tracking</h1><p>기간별 업무 활동</p></div>
      <div class="tr-heading-actions">
        <label v-if="recent.length" class="tr-recent"><span>최근 실행</span><select :value="recentId" @change="openRun($event.target.value)" :disabled="pending" aria-label="최근 수집 실행 열기">
          <option v-for="item in recent" :key="item.id" :value="item.id">{{ dateLabel(item.request?.startDate) }} – {{ dateLabel(item.request?.endDate) }} · {{ statusLabel(item.status) }}</option>
        </select></label>
        <button v-if="run && optionsLoaded" class="tr-button tr-ai-button" @click="summarize" :disabled="aiPending || busy || !options.aiEnabled" :title="options.aiEnabled ? '확보한 활동 근거만 AI에 전달합니다' : (options.aiReason || 'AI 연결 설정이 필요합니다')">{{ aiPending ? '요약 중…' : 'AI 요약' }}</button>
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
    <div class="tr-scope-note">시작일·종료일 포함 · 한국 시간 · 기간 중 담당 업무와 본인 활동 · 조직과 WBS는 현재 설정 기준</div>
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
      <div class="tr-summary" aria-label="기간 내 활동 집계"><span><b>{{ provisional ? '확인된 활동 티켓' : '활동 티켓' }}</b><strong>{{ summaryLabel(summary.uniqueIssues) }}</strong></span><span>생성 <strong>{{ summaryLabel(summary.created) }}</strong></span><span class="tr-completed">완료 <strong>{{ summaryLabel(summary.completed) }}</strong></span><span>댓글 <strong>{{ summaryLabel(summary.comments) }}</strong></span><span title="필요한 이력에서 확보한 변경만 표시합니다">확인된 변경 <strong>{{ summaryLabel(summary.fieldChanges) }}</strong></span><span class="tr-coverage">대상 이력 확인 <b>{{ historyConfirmed }}/{{ targetIssues.length }}</b></span></div>
      <div class="tr-toolbar"><div class="tr-segment" role="group" aria-label="활동 보기"><button :class="{on:mode === 'tree'}" :aria-pressed="mode === 'tree'" @click="mode = 'tree'">계층</button><button :class="{on:mode === 'feed'}" :aria-pressed="mode === 'feed'" @click="mode = 'feed'">날짜별 활동</button></div>
        <input type="search" class="tr-search" v-model="query" placeholder="티켓 · Epic · 사람 검색" aria-label="수집 결과에서 검색">
        <div class="tr-highlight-controls"><span>강조</span><select v-model="highlightKind" @change="clearHighlight" aria-label="강조 대상 종류"><option value="person">사람</option><option value="task">Task</option><option value="epic">Epic</option></select><select v-model="highlightValue" aria-label="강조 대상"><option value="">선택 안 함</option><option v-for="option in highlightOptions" :key="option.id" :value="option.id">{{ option.name }}</option></select><button v-if="highlightValue" class="tr-clear" @click="clearHighlight" aria-label="강조 해제">×</button></div>
        <label class="tr-matched-only"><input type="checkbox" v-model="matchedOnly">일치 항목만</label>
      </div>
      <div v-if="aiPending || ai || aiError" class="tr-ai-result" aria-live="polite"><strong>AI 요약</strong><span v-if="aiPending">확보한 근거를 읽고 있습니다…</span><p v-if="aiError" class="tr-error">{{ aiError }}</p><ul v-if="ai"><li v-for="(item,i) in ai.items || []" :key="i">{{ item.text }} <button v-for="id in item.evidenceIds || []" :key="id" class="tr-inline" @click="focusEvidence(id)">근거 보기</button></li></ul><small v-if="ai">{{ provisional ? '부분 수집 근거 기준 · ' : '' }}추가 Jira 조회 없음 · 사용 토큰 {{ ai.usage?.totalTokens ?? '미확인' }}</small></div>
      <div class="tr-workspace">
        <div class="tr-results">
          <div v-if="mode === 'tree'" class="tr-table-scroll"><table class="tr-table"><colgroup><col class="tr-title-col"><col class="tr-assignee-col"><col v-for="i in 4" :key="i" class="tr-count-col"></colgroup><thead><tr><th scope="col">업무</th><th scope="col">현재 담당</th><th scope="col">생성</th><th scope="col">완료</th><th scope="col">댓글</th><th scope="col" title="확보한 이력 기준">변경</th></tr></thead><tbody>
            <tr v-for="row in rows" :key="row.id" :class="['tr-row','tr-kind-'+row.kind,{'tr-selected':selectedId === row.id,'tr-highlighted':row.highlighted,'tr-search-match':row.matched && query}]" @click="selectRow(row)">
              <td><div class="tr-tree-label" :style="{'--tr-depth':row.depth}"><button v-if="row.children.length" class="tr-fold" @click.stop="fold(row)" :aria-expanded="!closed[row.id] || (matchedOnly && !!(query || highlight))" :aria-label="row.title + (closed[row.id] ? ' 펼치기' : ' 접기')"><svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="1.5" y="1.5" width="13" height="13" rx="1"/><path d="M4.5 8h7"/><path v-if="closed[row.id] && !(matchedOnly && (query || highlight))" d="M8 4.5v7"/></svg></button><span v-else class="tr-fold-space"></span><span class="tr-type" :class="'tr-type-'+row.kind">{{ kindLabel(row.kind) }}</span><button class="tr-row-title" @click.stop="selectRow(row)" :title="rowTitle(row)"><span v-if="row.key" class="tr-row-key">{{ row.key }}</span>{{ row.title }}</button><span v-if="closed[row.id] && row.children.length" class="tr-child-count">하위 {{ row.keys.size }}</span></div></td>
              <td><button v-if="row.issue?.assigneeId" class="tr-person" :class="{'tr-person-on':highlightKind === 'person' && highlightValue === row.issue.assigneeId}" @click.stop="highlightKind = 'person'; highlightValue = row.issue.assigneeId">{{ row.issue.assigneeName || row.issue.assigneeId }}</button><span v-else class="tr-muted">–</span></td>
              <td>{{ countLabel(row.counts.created,!!row.issue || row.counts.uniqueIssues > 0) }}</td><td class="tr-completed">{{ countLabel(row.counts.completed,row.counts.historyComplete) }}</td><td :class="{'tr-unknown':!row.counts.commentsComplete}">{{ countLabel(row.counts.comments,row.counts.commentsComplete) }}</td><td :class="{'tr-unknown':!row.counts.historyComplete}">{{ countLabel(row.counts.fieldChanges,row.counts.historyComplete) }}</td>
            </tr>
          </tbody></table><div v-if="!rows.length" class="tr-empty">{{ query || highlight ? '일치하는 활동이 없습니다. 검색어나 강조 대상을 바꿔 보세요.' : (provisional ? '아직 확인된 업무가 없습니다. 수집 상태와 확인 범위를 확인하세요.' : '선택한 기간과 대상의 활동이 없습니다.') }}</div></div>
          <div v-else class="tr-feed"><section v-for="group in feedGroups" :key="group.date"><h3>{{ dateLabel(group.date) }} <small>한국 시간</small></h3><button v-for="event in group.events" :key="event.id" :class="['tr-feed-event',{'tr-feed-match':matchKeys.has(event.issueKey),'tr-feed-selected':selectedEventId === event.id}]" @click="selectEvent(event)"><time>{{ timeLabel(event.date) }}</time><span class="tr-event-kind" :class="'tr-event-'+event.kind">{{ eventLabel(event) }}</span><b>{{ event.issueKey }}</b><span class="tr-feed-body">{{ eventText(event) }}</span><span class="tr-muted">{{ event.actorName || event.actorId || '작성자 미확인' }}</span></button></section><div v-if="!feedGroups.length" class="tr-empty">표시할 기간 내 활동이 없습니다.</div></div>
        </div>
        <aside class="tr-evidence" aria-label="선택 업무의 활동 근거">
          <template v-if="selected"><div class="tr-evidence-head"><div class="tr-breadcrumb">{{ selected.path.map(p => p.name || p.summary || p.key).join(' / ') }}</div><button v-if="selected.key" class="tr-ticket-link" @click="openTicket(selected.key)">{{ selected.key }}</button><h2>{{ selected.title }}</h2><div v-if="selectedIssue" class="tr-current"><span :class="['tr-current-status',{'tr-current-done':selectedIssue.statusCategory === 'done'}]">현재 {{ selectedIssue.status || '상태 미확인' }}</span><span class="tr-muted">{{ selectedIssue.assigneeName || '미할당' }}</span></div></div>
            <div class="tr-evidence-summary"><strong>기간 내 활동</strong><span>{{ selectedEvents.length }}건{{ provisional ? ' 확인' : '' }}</span></div>
            <div class="tr-evidence-events"><section v-for="group in evidenceGroups" :key="group.date"><h3>{{ dateLabel(group.date) }}</h3><article v-for="event in group.events" :key="event.id" :id="'tracking-event-'+event.id" :class="['tr-evidence-event',{'tr-event-focused':selectedEventId === event.id}]"><div class="tr-event-top"><span class="tr-event-kind" :class="'tr-event-'+event.kind">{{ eventLabel(event) }}</span><span>{{ event.actorName || event.actorId || '작성자 미확인' }}</span><time>{{ timeLabel(event.date) }}</time></div><button v-if="!selectedIssue || event.issueKey !== selected.key" class="tr-inline" @click="openTicket(event.issueKey)">{{ event.issueKey }}</button><p :class="{'tr-comment-excerpt':['comment','commentEdited'].includes(event.kind)}">{{ eventText(event) }}</p><small v-if="event.kind === 'comment' || event.kind === 'commentEdited'">{{ event.kind === 'commentEdited' ? '관측된 마지막 수정 · 과거 편집본 복원 제외' : '확인된 원문 발췌' }}</small></article></section><p v-if="!selectedEvents.length" class="tr-empty">{{ provisional ? '확인된 기간 내 활동이 없습니다. 미수집 이력이 남아 있을 수 있습니다.' : '이 업무의 기간 내 활동이 없습니다.' }}</p></div>
            <footer class="tr-evidence-footer"><span>{{ selected.contextOnly ? '계층 연결용 · 하위 근거 기준' : (selected.counts.historyComplete ? '확보한 이력 기준' : '일부 이력 미확인') }}</span><button v-if="selected.key" class="tr-button" @click="openTicket(selected.key)">티켓 열기 ↗</button></footer>
          </template><div v-else class="tr-empty">업무를 선택하면 기간 내 활동 근거를 확인할 수 있습니다.</div>
        </aside>
      </div>
      <footer class="tr-footnote"><span v-if="summary.uniqueIssues == null || (!summary.uniqueIssues && provisional)">활동 티켓 수 미확인</span><span v-else>{{ summary.uniqueIssues }}개 활동 티켓{{ provisional ? ' 확인' : '' }} · 공유 티켓 중복 제외</span><span>변경은 확보한 이력만 표시 · 미확인을 0으로 집계하지 않음</span></footer>
      <details class="tr-audit"><summary>수집 범위와 비용 · {{ runLabel }}</summary><div class="tr-audit-body"><p>생성·완료는 고유 티켓 수입니다. 재오픈·재완료 사건은 상세 근거에 남습니다. 댓글은 작성일·작성자 기준이며 삭제된 댓글과 과거 편집본은 복원하지 않습니다.</p><p>후보 검색 {{ coverage.queriesComplete ? '완료' : '미완료' }} · 전체 확인 {{ coverage.issuesComplete ?? '미확인' }}/{{ coverage.issuesTotal ?? '미확인' }} · 댓글 확인 {{ coverage.commentsComplete ? '완료' : '일부 미확인' }} · 변경 이력 확인 {{ coverage.historyComplete ? '완료' : '일부 미확인' }}</p><p>수집 REST {{ metrics.restRequests ?? '미확인' }}회 (인증 요청 제외) · 응답 {{ metrics.responseBytes == null ? '미확인' : (metrics.responseBytes / 1024).toFixed(1) + ' KB' }} · 재시도 {{ metrics.retries ?? '미확인' }}회 · 캐시 재사용 {{ metrics.cacheHits ?? '미확인' }}회 · {{ metrics.elapsedMs == null ? '시간 미확인' : (metrics.elapsedMs / 1000).toFixed(1) + '초' }} · MCP {{ metrics.mcpCalls ?? 0 }}회 · LLM 토큰 {{ metrics.llmTokens ?? 0 }}</p><ul v-if="coverage.missing?.length"><li v-for="(item,i) in coverage.missing" :key="i">{{ item.issueKey || '검색' }} · {{ item.source }} · {{ item.reason }}</li></ul><ul v-if="run.errors?.length"><li v-for="(item,i) in run.errors" :key="i">{{ typeof item === 'string' ? item : item.message || item.reason || item.error }}</li></ul></div></details>
    </template>
    <div v-else class="tr-start"><h2>{{ pending ? '저장된 실행을 여는 중…' : '기간과 대상을 선택해 활동을 확인하세요' }}</h2><p>생성·완료·댓글과 확보한 변경 이력을 업무 계층으로 모읍니다.</p><p>조회 버튼을 누르면 수집을 시작합니다. 검색·강조·보기 전환은 추가 Jira 조회 없이 동작합니다.</p></div>
  </section>`,
};
