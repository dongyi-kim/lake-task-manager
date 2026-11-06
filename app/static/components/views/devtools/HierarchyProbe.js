import { api } from "../../../lib/api.js";
import { newMutationId } from "../../../lib/mutationId.js";

const BASE = "/api/dev/hierarchy-test";
const STATUS = { running: "실행 중", passed: "통과", blocked: "추가 확인 필요", failed: "실패",
  pending: "대기", skipped: "건너뜀", completed: "완료", succeeded: "통과" };
const NAMES = { create_parent_a: "부모 Task A 생성", create_parent_b: "부모 Task B 생성",
  create_subject: "테스트 Task 생성", convert_to_subtask: "Task → SubTask",
  change_parent: "부모 A → 부모 B", convert_to_task: "SubTask → Task" };
const ROLES = { create_parent_a: "부모 A", create_parent_b: "부모 B", create_subject: "변환 대상" };

export default {
  name: "HierarchyProbe",
  data() { return { options: null, optionError: "", taskType: "", subtaskType: "", pending: null,
    report: null, busy: false, checking: false, uncertain: false, message: "", copied: "", copyText: "",
    pollTimer: null, pollFailures: 0, requestVersion: 0, disposed: false }; },
  computed: {
    storageKey() { return this.options ? "ltm:hierarchy-probe:" + location.origin + ":" + this.options.scope : ""; },
    active() { return this.busy || this.report?.status === "running"; },
    canResume() { return this.pending && !this.busy && (!this.active || this.uncertain); },
    canNew() { return this.report && ["passed", "blocked", "failed"].includes(this.report.status)
      && !this.uncertain && !this.busy && !this.report.error?.outcomeUnknown
      && !(this.report.steps || []).some(s => s.error?.outcomeUnknown); },
  },
  mounted() { this.loadOptions(); },
  beforeUnmount() { this.disposed = true; clearTimeout(this.pollTimer); },
  methods: {
    async loadOptions() {
      this.optionError = "";
      try {
        const options = await api.raw(BASE + "/options");
        if (this.disposed) return;
        if (!options.scope) throw new Error("실행 범위를 확인하지 못했습니다. 앱을 최신 버전으로 다시 시작하세요.");
        this.options = options;
        this.taskType = options.taskType || ""; this.subtaskType = options.subtaskType || "";
        try { this.pending = JSON.parse(localStorage.getItem(this.storageKey) || "null"); }
        catch (_) { this.pending = null; }
        if (this.pending?.runId && this.pending.taskType && this.pending.subtaskType) {
          this.taskType = this.pending.taskType; this.subtaskType = this.pending.subtaskType;
          this.uncertain = true;
          await this.refresh();
        } else this.pending = null;
      } catch (error) { if (!this.disposed) this.optionError = error.message || String(error); }
    },
    schedule() {
      clearTimeout(this.pollTimer);
      if (!this.disposed && this.report?.status === "running")
        this.pollTimer = setTimeout(() => this.refresh(), this.pollFailures ? 3000 : 1000);
    },
    accept(report) {
      if (this.disposed) return;
      if (report.runId !== this.pending?.runId || report.scope !== this.options.scope)
        throw new Error("실행 범위가 달라 결과를 표시할 수 없습니다. 같은 계정과 Jira 연결인지 확인하세요.");
      this.report = report; this.uncertain = false; this.message = ""; this.pollFailures = 0;
      this.schedule();
    },
    async refresh() {
      if (!this.pending || this.checking || this.disposed) return;
      const version = this.requestVersion;
      this.checking = true;
      try {
        const report = await api.raw(BASE + "/" + encodeURIComponent(this.pending.runId));
        if (version === this.requestVersion) this.accept(report);
      }
      catch (error) {
        if (this.disposed || version !== this.requestVersion) return;
        this.uncertain = true;
        this.message = "실행 상태를 확인하지 못했습니다. 새 테스트를 만들지 않고 같은 실행을 확인합니다. " + (error.message || error);
        if (++this.pollFailures <= 3) this.schedule();
      } finally { this.checking = false; }
    },
    async submit() {
      if (this.busy || !this.options || !this.taskType || !this.subtaskType) return;
      if (!this.pending) {
        let payload;
        try {
          const stored = JSON.parse(localStorage.getItem(this.storageKey) || "null");
          payload = stored?.runId && stored.taskType && stored.subtaskType ? stored :
            { runId: newMutationId("hierarchy-probe"), expectedScope: this.options.scope,
              taskType: this.taskType, subtaskType: this.subtaskType };
          localStorage.setItem(this.storageKey, JSON.stringify(payload));
        }
        catch (_) { this.message = "실행 ID를 저장하지 못했습니다. 브라우저 저장 공간을 확인한 뒤 다시 실행하세요."; return; }
        this.pending = payload; this.taskType = payload.taskType; this.subtaskType = payload.subtaskType;
      }
      this.busy = true; this.uncertain = true; this.message = ""; this.copied = ""; this.copyText = "";
      this.requestVersion++;
      clearTimeout(this.pollTimer);
      try { this.accept(await api.raw(BASE, { method: "POST", mutation: true,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(this.pending) })); }
      catch (error) {
        if (!this.disposed) {
          this.message = "요청 결과를 확인하지 못했습니다. 같은 실행 ID를 유지합니다. " + (error.message || error);
          await this.refresh();
        }
      } finally { this.busy = false; }
    },
    newRun() {
      if (!this.canNew) return;
      try { localStorage.removeItem(this.storageKey); }
      catch (_) { this.message = "이전 실행 정보를 지우지 못했습니다. 브라우저 저장 공간을 확인하세요."; return; }
      clearTimeout(this.pollTimer);
      this.requestVersion++;
      this.pending = null; this.report = null; this.message = ""; this.copyText = ""; this.copied = "";
    },
    label(status) { return STATUS[status] || status; },
    stepName(name) { return NAMES[name] || name; },
    roleName(name) { return ROLES[name] || name; },
    pretty(value) { return JSON.stringify(value, null, 2); },
    formDiagnostic(error) {
      const d = error?.formDiagnostic;
      if (!d) return "";
      const reason = { http_response: "변환 화면 응답 오류", form_not_identified: "응답에서 지원하는 변환 폼을 찾지 못함",
        transport_timeout: "변환 화면 요청 시간 초과", transport_failure: "변환 화면 전송 실패" }[d.reason];
      if (!reason) return "";
      return [reason, d.httpStatus ? "HTTP " + d.httpStatus : "",
        Number.isInteger(d.formCount) ? "폼 " + d.formCount + "개 / 지원 폼 " + d.supportedFormCount + "개" : ""].filter(Boolean).join(" · ");
    },
    ticketUrl(value) {
      try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : ""; }
      catch (_) { return ""; }
    },
    async copyReport() {
      const text = this.pretty(this.report);
      try { await navigator.clipboard.writeText(text); this.copied = "결과를 복사했습니다."; }
      catch (_) {
        this.copyText = text; this.copied = "아래 결과를 선택해 복사하세요.";
        this.$nextTick(() => this.$refs.copyFallback?.select());
      }
    },
  },
  template: `
  <section class="dt-card hp-card" aria-label="Jira 계층 변경 테스트">
    <div class="dt-card-h">
      <div class="dt-card-t"><span class="dt-label">Jira 계층 변경 원클릭 테스트</span>
        <span class="dt-path"><span class="dt-method">POST</span> /api/dev/hierarchy-test</span></div>
      <span v-if="options" class="hp-context">{{ options.environment }} · {{ options.projectKey }}</span>
    </div>
    <div class="hp-body">
      <p class="hp-intro">현재 연결된 Jira에 테스트 전용 Task 3개를 생성한 뒤 변환과 부모 변경을 검증합니다.
        <strong>테스트 티켓은 결과 확인을 위해 남습니다.</strong></p>
      <p class="hp-flow">Task → SubTask → 다른 부모로 이동 → Task</p>
      <p v-if="options?.environment === 'mock'" class="hp-hint">Mock 환경에서는 실제 Jira 웹 폼이 지원되지 않아 해당 단계에서 중단될 수 있습니다.</p>
      <div v-if="optionError" class="hp-error" role="alert">{{ optionError }}
        <button class="dt-open" @click="loadOptions">설정 다시 조회</button></div>
      <p v-else-if="!options" class="hp-hint">연결 환경과 티켓 타입을 확인하는 중…</p>
      <div v-else class="hp-controls">
        <label>Task 타입<select v-model="taskType" :disabled="!!pending || busy">
          <option value="" disabled>타입 선택</option><option v-for="type in options.taskTypes" :key="type" :value="type">{{ type }}</option>
        </select></label>
        <label>SubTask 타입<select v-model="subtaskType" :disabled="!!pending || busy">
          <option value="" disabled>타입 선택</option><option v-for="type in options.subtaskTypes" :key="type" :value="type">{{ type }}</option>
        </select></label>
        <button v-if="!pending" class="dt-run" :disabled="busy || !taskType || !subtaskType" @click="submit">계층 변경 원클릭 테스트</button>
        <button v-else-if="canResume && report?.status !== 'passed'" class="dt-run" @click="submit">같은 실행 재개</button>
        <span v-if="active" class="hp-hint" role="status">테스트 실행 중… 다른 화면을 사용해도 계속 진행됩니다.</span>
        <button v-if="canNew" class="dt-open" @click="newRun">새 테스트 준비</button>
      </div>
      <p v-if="pending" class="hp-run-id">실행 ID: {{ pending.runId }}</p>
      <div v-if="message" class="hp-error" role="status">{{ message }}
        <button class="dt-open" :disabled="checking || busy" @click="refresh">상태 다시 확인</button></div>
      <div v-if="report" class="hp-report" aria-live="polite">
        <div class="hp-report-head"><strong :class="'hp-' + report.status">{{ label(report.status) }}</strong>
          <button class="dt-open" @click="copyReport">결과 복사</button></div>
        <p v-if="report.error" class="hp-error">{{ report.error.message }}</p>
        <p v-if="report.detailsOmitted" class="hp-hint">{{ report.detailMessage }}</p>
        <ol class="hp-steps"><li v-for="(step, index) in report.steps" :key="index">
          <span>{{ stepName(step.name) }}</span><span :class="'hp-' + step.status">{{ label(step.status) }}</span>
          <small v-if="step.key">{{ step.key }}</small><p v-if="step.error" class="hp-error">{{ step.error.message }}</p>
          <p v-if="formDiagnostic(step.error)" class="hp-hint">{{ formDiagnostic(step.error) }}
            <span v-if="report.environment === 'mock' && step.error.formDiagnostic.httpStatus === 404"> · Mock에 변환 웹 화면이 구현되어 있지 않습니다.</span></p>
          <details v-if="step.inputs?.length || step.changes?.length"><summary>필요한 입력·변경 확인</summary>
            <pre>{{ pretty({ inputs: step.inputs, changes: step.changes }) }}</pre></details>
        </li></ol>
        <div v-if="report.createdTickets?.length" class="hp-tickets"><strong>생성된 테스트 티켓</strong>
          <a v-for="ticket in report.createdTickets" :key="ticket.key" :href="ticketUrl(ticket.url) || undefined"
             target="_blank" rel="noopener noreferrer">{{ ticket.key }} <span>{{ roleName(ticket.role) }}</span> ↗</a></div>
        <p v-if="copied" class="hp-hint">{{ copied }}</p>
        <textarea v-if="copyText" ref="copyFallback" class="hp-copy" :value="copyText" readonly aria-label="복사용 테스트 결과"></textarea>
      </div>
    </div>
  </section>`,
};
