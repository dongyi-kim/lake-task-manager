// MAXIS/Dify workflow configuration.  This is intentionally separate from the
// OpenAI/AOAI model-provider editor: a Dify app owns a workflow contract/version,
// while model selection remains a MAXIS governance concern.
import { agentApi } from "../../lib/agentApi.js";
import MaxisSetupWizard from "./MaxisSetupWizard.js";

const WORKFLOWS = [
  { key: "agent_core", label: "Agent Core", keyField: "agentCoreApiKey",
    hint: "요청 분해·조사·Task 계획·감사·응답 통합" },
  { key: "editor_author", label: "Editor Author", keyField: "editorAuthorApiKey",
    hint: "Description·Comment 의미 초안" },
  { key: "pmo_brief", label: "PMO Brief", keyField: "pmoBriefApiKey",
    hint: "LTM이 계산한 수치의 변화·위험·조치 설명" },
];
const ROUTE_MODES = [
  { key: "local", label: "Local", hint: "기존 LTM Agent" },
  { key: "shadow", label: "Shadow", hint: "사용자 결과는 Local, MAXIS는 평가만" },
  { key: "maxis", label: "MAXIS", hint: "MAXIS/Dify를 해당 기능의 생성 주체로 사용" },
];

function blankForm() {
  return { baseUrl: "", dataPolicy: "synthetic_only", workflowBundleVersion: "",
    knowledgeVersion: "", knowledgeDatasetId: "", knowledgeSentinelSha256: "",
    remoteLogPolicyId: "", remoteLogMode: "", remoteLogRetentionDays: null,
    workflows: Object.fromEntries(WORKFLOWS.map((w) =>
      [w.key, { binding: "", version: "" }])) };
}

export default {
  name: "MaxisSettingsPanel",
  components: { MaxisSetupWizard },
  emits: ["saved"],
  data() {
    return {
      workflowRows: WORKFLOWS, routeModes: ROUTE_MODES,
      st: null, selectedName: "", form: blankForm(), routes: {},
      view: "profiles", setupMode: "", addProfileName: "dev",
      profileEditorOpen: false, keysEditorOpen: false, formSnapshot: "",
      runtime: null, statusUnavailable: false,
      keys: { agentCoreApiKey: "", editorAuthorApiKey: "", pmoBriefApiKey: "" },
      clearKeys: { agentCoreApiKey: false, editorAuthorApiKey: false, pmoBriefApiKey: false },
      busy: "", err: "", note: "", probe: null, impact: null, impactDenied: false,
    };
  },
  computed: {
    profiles() { return (this.st && this.st.profiles) || []; },
    selected() { return this.profiles.find((p) => p.name === this.selectedName) || null; },
    canManage() { return !!this.st && this.st.canManage === true && !this.statusUnavailable; },
    profileDirty() {
      return !!this.selected && JSON.stringify(this.form) !== this.formSnapshot;
    },
    keysDirty() {
      return Object.values(this.keys).some((value) => String(value).trim()) ||
        Object.values(this.clearKeys).some(Boolean);
    },
    routesDirty() {
      return WORKFLOWS.some((w) => this.routes[w.key] !== ((this.st && this.st.routes) || {})[w.key]);
    },
    hasUnsavedChanges() { return this.profileDirty || this.keysDirty || this.routesDirty; },
    runtimeRows() {
      return [{ key: "conversation", label: "Agent 대화" }, { key: "editor", label: "에디터 AI" }]
        .map((feature) => {
          const row = this.runtime && this.runtime[feature.key];
          const usingSelected = !!row && row.engine === "maxis" && row.ready === true &&
            row.profile === this.selectedName && !!this.selected && this.selected.enabled;
          return { ...feature, usingSelected,
            labelText: !row ? "실행 상태 확인 불가" : usingSelected ? "현재 사용 중 · MAXIS" :
              `${row.engine === "maxis" ? "MAXIS" : row.engine === "mixed" ? "Local + MAXIS" : "Local"}${row.profile ? " · " + row.profile : ""} · ${row.ready ? "사용 가능" : "사용 불가"}`,
            reason: row && !row.ready ? row.reason || "실행 설정을 확인하세요." : "" };
        });
    },
    deploymentOverrideNames() {
      const env = (this.st && this.st.environmentOverrides) || {};
      return [env.profile, env.baseUrl, env.workflowBundleVersion, env.knowledgeVersion,
        env.knowledgeDatasetId, env.knowledgeSentinelSha256, env.remoteLogPolicyId,
        env.remoteLogMode, env.remoteLogRetentionDays, env.knowledgeEvalAttestation,
        ...Object.values(env.workflowVersions || {}), ...Object.values(env.credentials || {})]
        .filter(Boolean);
    },
    remoteLogReady() {
      if (!this.selected || this.selected.stage !== "prod") return true;
      return !!this.selected.effectiveRemoteLogPolicyId &&
        ["redacted", "approved_bounded_content"].includes(
          this.selected.effectiveRemoteLogMode) &&
        Number.isInteger(this.selected.effectiveRemoteLogRetentionDays) &&
        this.selected.effectiveRemoteLogRetentionDays >= 0 &&
        this.selected.effectiveRemoteLogRetentionDays <= 90;
    },
    knowledgeEval() {
      return (this.selected && this.selected.knowledgeEvalAttestation) || null;
    },
    knowledgeEvalReady() {
      if (!this.selected || this.selected.stage !== "prod") return true;
      return !!(this.selected.qualification && this.selected.qualification.valid);
    },
    activationDisabledReason() {
      if (!this.canManage) return "연결 설정 변경은 매니저만 할 수 있습니다.";
      if (!this.selected) return "프로필을 선택하세요.";
      if (this.hasUnsavedChanges) return "변경한 설정·API Key·실행 경로를 먼저 저장하세요.";
      if (this.selected.configError) return this.selected.configError;
      if (this.selected.disabledReason) return this.selected.disabledReason;
      if (!this.selected.verified) return "저장된 설정의 연결을 먼저 확인하세요.";
      if (!this.remoteLogReady) return "원격 로그 승인 정책을 먼저 저장하세요.";
      if (Object.values(this.routes || {}).includes("maxis") && !this.knowledgeEvalReady) {
        return "MAXIS 실행 경로를 사용하려면 서명된 PROD 평가 검증이 필요합니다.";
      }
      if (this.selected.dataPolicy === "synthetic_only" &&
          Object.values(this.routes || {}).includes("maxis")) {
        return "DEV는 합성 연결·Shadow 검증 전용입니다. MAXIS 최종 응답 경로를 사용할 수 없습니다.";
      }
      if ((this.st.environmentOverrides || {}).profile && !this.selected.active) {
        return "배포 환경변수로 연결 프로필이 고정되어 있습니다.";
      }
      const versionEnv = ((this.st.environmentOverrides || {}).workflowVersions) || {};
      const required = new Set(["agent_core"]);
      Object.entries(this.routes || {}).forEach(([key, mode]) => {
        if (mode === "shadow" || mode === "maxis") required.add(key);
      });
      const incomplete = [...required].find((key) => {
        const workflow = (this.selected.workflows && this.selected.workflows[key]) || {};
        return !workflow.binding || !(workflow.version || versionEnv[key]) ||
          !this.credential(key).configured;
      });
      return incomplete ? `${WORKFLOWS.find((w) => w.key === incomplete).label}의 게시 정보와 API Key를 저장하세요.` : "";
    },
    canActivate() { return !this.activationDisabledReason; },
    probeRows() {
      return (this.probe && this.probe.workflows) ||
        (this.selected && this.selected.lastProbe && this.selected.lastProbe.workflows) || {};
    },
    remoteObservationState() {
      const value = this.st && this.st.lastRemoteObservations;
      if (!value || !["observed", "none_recorded", "unavailable"].includes(value.state)) return "unavailable";
      return value.state === "observed" && !Array.isArray(value.observations) ? "unavailable" : value.state;
    },
    remoteObservations() {
      const value = this.st && this.st.lastRemoteObservations;
      if (this.remoteObservationState !== "observed" ||
          !["dev", "prod", "internal-pilot"].includes(this.selectedName)) return [];
      const rows = value.observations.slice(0, 9).filter((row) =>
        row && typeof row === "object" && row.profile === this.selectedName);
      const text = (value, pattern) => typeof value === "string" && pattern.test(value) ? value : "미확인";
      const label = (value, labels) => typeof value === "string" &&
        Object.prototype.hasOwnProperty.call(labels, value) ? labels[value] : "미확인";
      const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
      const instant = (value) => typeof value === "string" &&
        /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value) &&
        Number.isFinite(Date.parse(value)) ? value : "미확인";
      const rank = (row) => instant(row.observedAt) === "미확인" ? -Infinity : Date.parse(row.observedAt);
      return WORKFLOWS.map((workflow) => {
        const row = rows.filter((item) => item.workflowKey === workflow.key)
          .sort((a, b) => rank(b) - rank(a))[0];
        if (!row) return null;
        return {
          key: workflow.key, label: workflow.label, observedAt: instant(row.observedAt),
          route: label(row.routeMode, { shadow: "Shadow · 원격 평가", maxis: "MAXIS/Dify" }),
          outputOwner: row.routeMode === "shadow" && row.outputOwner === "local" ? "Local" : "미확인",
          audit: label(row.auditStatus, { started: "시작", succeeded: "수용", blocked: "차단", failed: "실패" }),
          transport: label(row.transportStatus, { not_attempted: "미시도", succeeded: "성공", failed: "실패" }),
          result: label(row.resultStatus, { not_evaluated: "평가 안 됨", completed: "계약 수용",
            needs_input: "입력 필요", blocked: "차단", failed: "실패" }),
          http: Number.isInteger(row.httpStatus) && row.httpStatus >= 100 && row.httpStatus <= 599 ? row.httpStatus : "미확인",
          workflowVersion: text(row.workflowVersion, uuid), workflowRunId: text(row.workflowRunId, uuid),
          knowledgeVersion: text(row.knowledgeVersion, /^(?:ltm-knowledge-\d{4}\.\d{2}(?:\.\d+)?|ltm-public-knowledge\.v[1-9]\d*)$/),
        };
      }).filter(Boolean);
    },
  },
  mounted() { this.load(); this.loadImpact(); },
  methods: {
    async load(prefer, preserve = {}) {
      const previous = { name: this.selectedName, form: this.form, keys: this.keys,
        clearKeys: this.clearKeys, routes: this.routes };
      try {
        this.st = await agentApi.maxisStatus();
        this.statusUnavailable = false;
        const configured = (p) => !!(p.effectiveHost || p.effectiveBaseUrl || p.baseUrl);
        const preferred = [prefer, this.selectedName,
          (this.profiles.find((p) => p.active && configured(p)) || {}).name,
          (this.profiles.find((p) => p.name === "dev" && configured(p)) || {}).name,
          (this.profiles.find(configured) || {}).name, this.st.activeProfile,
          (this.profiles[0] || {}).name];
        const selected = preferred.find((name) => this.profiles.some((p) => p.name === name));
        this.selectProfile(selected, true);
        this.routes = { ...(this.st.routes || {}) };
        if (previous.name === this.selectedName) {
          if (preserve.form) this.form = previous.form;
          if (preserve.keys) { this.keys = previous.keys; this.clearKeys = previous.clearKeys; }
          if (preserve.routes) this.routes = previous.routes;
        }
        await this.loadRuntime();
        return true;
      } catch (e) {
        this.statusUnavailable = true; this.runtime = null;
        this.err = (e && e.message) || "MAXIS 설정을 불러오지 못했습니다";
        return false;
      }
    },
    async loadRuntime() {
      this.runtime = null;
      try { this.runtime = await agentApi.runtimeStatus(); } catch (_) {}
    },
    async retryLoad() {
      if (this.busy) return;
      this.busy = "reload";
      try { await this.load(this.selectedName, { form: true, keys: true, routes: true }); }
      finally { this.busy = ""; }
    },
    async loadImpact() {
      try { this.impact = await agentApi.maxisImpact(30); }
      catch (e) { this.impactDenied = !!(e && String(e.message || "").includes("403")); }
    },
    selectProfile(name, discard = false) {
      if (!discard && (this.busy || !this.canLeaveDraft())) return false;
      const row = this.profiles.find((p) => p.name === name);
      if (!row) return false;
      if (this.selectedName !== name) {
        this.profileEditorOpen = false; this.keysEditorOpen = false;
      }
      this.selectedName = name;
      this.form = { baseUrl: row.baseUrl || "", dataPolicy: row.dataPolicy || "",
        workflowBundleVersion: row.workflowBundleVersion || "",
        knowledgeVersion: row.knowledgeVersion || "",
        knowledgeDatasetId: row.knowledgeDatasetId || "",
        knowledgeSentinelSha256: row.knowledgeSentinelSha256 || "", workflows: {} };
      this.form.remoteLogPolicyId = row.remoteLogPolicyId || "";
      this.form.remoteLogMode = row.remoteLogMode || "";
      this.form.remoteLogRetentionDays = row.remoteLogRetentionDays == null
        ? null : row.remoteLogRetentionDays;
      WORKFLOWS.forEach((w) => {
        const value = (row.workflows && row.workflows[w.key]) || {};
        this.form.workflows[w.key] = { binding: value.binding || "", version: value.version || "" };
      });
      this.formSnapshot = JSON.stringify(this.form);
      this.keys = { agentCoreApiKey: "", editorAuthorApiKey: "", pmoBriefApiKey: "" };
      this.clearKeys = { agentCoreApiKey: false, editorAuthorApiKey: false, pmoBriefApiKey: false };
      this.probe = null; this.err = ""; this.note = "";
      return true;
    },
    canLeaveDraft() {
      if (!this.hasUnsavedChanges) return true;
      this.err = "저장하지 않은 변경이 있습니다. 저장하거나 변경 취소 후 이동하세요.";
      return false;
    },
    changeView(view) {
      if (this.busy || !this.canLeaveDraft()) return;
      this.view = view; this.err = ""; this.note = "";
      this.addProfileName = this.selectedName || (this.profiles[0] || {}).name || "dev";
    },
    discardChanges() {
      if (this.busy) return;
      this.selectProfile(this.selectedName, true);
      this.routes = { ...((this.st && this.st.routes) || {}) };
    },
    openManualSetup() {
      if (!this.selectProfile(this.addProfileName)) return;
      this.view = "profiles"; this.profileEditorOpen = true; this.keysEditorOpen = true;
    },
    async setupConfigured() {
      if (await this.load("prod")) this.$emit("saved");
    },
    ensureOk(result) {
      if (result && result.ok === false) throw new Error(result.error || "설정을 확인하세요.");
      return result;
    },
    credential(workflowKey) {
      return (this.selected && this.selected.credentials && this.selected.credentials[workflowKey]) ||
        { configured: false, source: "none" };
    },
    credentialLabel(workflowKey) {
      const row = this.credential(workflowKey);
      if (!row.configured) return "미설정";
      return row.source === "environment" ? `환경변수 (${row.environmentVariable})` : "Windows DPAPI에 저장됨";
    },
    async saveProfile() {
      if (this.busy || !this.canManage || !this.selected) return;
      this.busy = "profile"; this.err = ""; this.note = "";
      try {
        this.ensureOk(await agentApi.maxisSaveProfile(this.selectedName, this.form));
        if (!await this.load(this.selectedName, { keys: true, routes: true })) return;
        this.$emit("saved");
        this.note = "프로필을 저장했습니다. 변경된 조합은 다시 확인해야 합니다.";
      } catch (e) { this.err = (e && e.message) || "프로필을 저장하지 못했습니다"; }
      finally { this.busy = ""; }
    },
    async saveCredentials() {
      if (this.busy || !this.canManage || !this.selected) return;
      const body = { clear: [] };
      WORKFLOWS.forEach((w) => {
        const value = String(this.keys[w.keyField] || "").trim();
        if (value) body[w.keyField] = value;
        if (this.clearKeys[w.keyField]) body.clear.push(w.keyField);
      });
      if (Object.keys(body).length === 1 && !body.clear.length) {
        this.err = "바꿀 API Key를 입력하거나 삭제할 항목을 선택하세요."; return;
      }
      this.busy = "credentials"; this.err = ""; this.note = "";
      try {
        this.ensureOk(await agentApi.maxisSaveCredentials(this.selectedName, body));
        if (!await this.load(this.selectedName, { form: true, routes: true })) return;
        this.$emit("saved");
        this.note = "API Key를 현재 Windows 사용자 DPAPI로 저장했습니다. 원문은 다시 표시되지 않습니다.";
      } catch (e) { this.err = (e && e.message) || "API Key를 저장하지 못했습니다"; }
      finally { this.busy = ""; }
    },
    async runProbe() {
      if (this.busy || !this.canManage || !this.selected || this.hasUnsavedChanges) return;
      this.busy = "probe"; this.err = ""; this.note = "";
      try {
        const result = await agentApi.maxisProbe(this.selectedName);
        if (!await this.load(this.selectedName)) return;
        this.probe = result;
        this.$emit("saved");
        this.note = result.ok ? "저장된 연결을 확인했습니다. 이 설정 사용 버튼과 기능별 실행 상태를 확인하세요."
          : (result.error || "연결 확인에 실패했습니다.");
      } catch (e) { this.err = (e && e.message) || "연결 확인에 실패했습니다"; }
      finally { this.busy = ""; }
    },
    async activate() {
      if (this.busy || !this.canActivate) return;
      this.busy = "activate"; this.err = ""; this.note = "";
      try {
        this.ensureOk(await agentApi.maxisActivate(this.selectedName));
        if (!await this.load(this.selectedName)) return;
        this.$emit("saved");
        this.note = `${this.selectedName} 프로필을 연결 설정으로 선택했습니다. 실제 사용 경로는 아래 실행 상태를 따릅니다.`;
      } catch (e) { this.err = (e && e.message) || "프로필을 적용하지 못했습니다"; }
      finally { this.busy = ""; }
    },
    async saveRoutes() {
      if (this.busy || !this.canManage || this.profileDirty || this.keysDirty) return;
      this.busy = "routes"; this.err = ""; this.note = "";
      try {
        this.ensureOk(await agentApi.maxisSaveRoutes(this.routes));
        if (!await this.load(this.selectedName)) return;
        this.$emit("saved");
        this.note = "기능별 실행 경로를 저장했습니다. 한 요청에서 두 Agent를 연쇄 실행하지 않습니다.";
      } catch (e) { this.err = (e && e.message) || "실행 경로를 저장하지 못했습니다"; }
      finally { this.busy = ""; }
    },
    async setKill(enabled) {
      if (this.busy || !this.canManage) return;
      this.busy = "kill"; this.err = ""; this.note = "";
      try {
        this.ensureOk(await agentApi.maxisSetKillSwitch(enabled));
        if (!await this.load(this.selectedName, { form: true, keys: true, routes: true })) return;
        this.$emit("saved");
        this.note = enabled ? "긴급 중단을 적용했습니다. MAXIS 경로는 Local로 자동 우회하지 않습니다."
          : "긴급 중단을 해제했습니다.";
      } catch (e) { this.err = (e && e.message) || "긴급 중단 상태를 바꾸지 못했습니다"; }
      finally { this.busy = ""; }
    },
    pct(value) { return value == null ? "—" : `${value}%`; },
    duration(value) {
      if (value == null) return "—";
      return value < 60 ? `${Math.round(value)}초` : `${(value / 60).toFixed(1)}분`;
    },
    impactSegments() {
      const rows = this.impact && this.impact.segments;
      return Array.isArray(rows) ? rows.slice(0, 12) : [];
    },
    featureLabel(value) {
      return ({ task_draft: "Task 초안", editor_author: "Editor 초안",
                pmo_brief: "PMO 브리프" })[value] || value || "기능";
    },
    engineLabel(row) {
      if (!row) return "—";
      const engine = row.engine === "maxis" ? "MAXIS/Dify" :
        (row.engine === "local" ? "LTM Local" : (row.engine || "미확인"));
      return row.profile ? `${engine} · ${row.profile}` : engine;
    },
    probeDetail(row) {
      if (row && Array.isArray(row.missingParameters) && row.missingParameters.length) {
        return `필수 변수 누락: ${row.missingParameters.join(", ")}`;
      }
      if (!row) return "";
      if (row.ok && row.knowledgeRetrievalVerified) {
        return `${row.ms == null ? "" : `${row.ms}ms · `}계약·버전·인용·Knowledge 조회 확인`;
      }
      return row.errorCode || (row.ms == null ? "" : `${row.ms}ms`);
    },
    promotion(workflowKey) {
      return (this.selected && this.selected.promotion &&
        this.selected.promotion[workflowKey]) || null;
    },
    promotionDetail(workflowKey) {
      const row = this.promotion(workflowKey);
      if (!row) return "";
      const samples = `${row.completed || 0}/${row.minimumSamples || 5} 표본`;
      const roles = Object.values(row.roles || {});
      const roleGate = roles.length
        ? ` · Role ${roles.filter((it) => it && it.ready).length}/${roles.length}` : "";
      return `${samples}${roleGate} · 성공 ${this.pct(row.successRatePct)} · 인용 ${this.pct(row.citationValidRatePct)} · 구조 ${this.pct(row.structureScorePct)}`;
    },
  },
  template: `
  <div class="mx-panel mx-profile-first">
    <nav class="mx-mode-nav" aria-label="MAXIS 설정">
      <button :class="{on:view==='profiles'}" :aria-pressed="view==='profiles'"
              :disabled="!!busy" @click="changeView('profiles')">저장된 MAXIS 설정</button>
      <button :class="{on:view==='add'}" :aria-pressed="view==='add'"
              :disabled="!!busy" @click="changeView('add')">MAXIS 설정 추가</button>
    </nav>
    <div v-if="note" class="mx-feedback mx-note" role="status">{{ note }}</div>
    <div v-if="err" class="mx-feedback ag-err" role="alert">{{ err }}</div>
    <div v-if="statusUnavailable" class="mx-feedback ag-warn">
      최신 연결 상태를 확인할 수 없어 설정 변경을 잠갔습니다. 입력한 값은 유지됩니다.
      <button class="ag-mini" :disabled="!!busy" @click="retryLoad">설정 상태 다시 불러오기</button>
    </div>
    <div v-if="hasUnsavedChanges" class="mx-draft-note">
      <span>저장하지 않은 변경이 있습니다.</span>
      <button class="ag-mini" :disabled="!!busy" @click="discardChanges">변경 취소</button>
    </div>
    <div v-if="!st" class="ag-body">MAXIS 설정을 불러오는 중…</div>
    <template v-else-if="view==='profiles'">
      <div class="mx-profile-bar">
        <div><b>MAXIS 연결 설정</b><span>저장된 프로필을 선택하고 연결 상태를 확인하세요.</span></div>
        <div class="mx-stage-pick" aria-label="MAXIS 프로필">
          <button v-for="p in profiles" :key="p.name" class="ag-mini"
                  :data-profile="p.name" :aria-pressed="selectedName === p.name" :disabled="!!busy"
                  :class="{ on: selectedName === p.name }" @click="selectProfile(p.name)">
            {{ p.name }}<em v-if="p.active">선택됨</em>
          </button>
        </div>
      </div>

      <div v-if="st.environmentError" class="ag-err">{{ st.environmentError }}</div>
      <div v-if="deploymentOverrideNames.length" class="ag-warn">
        배포 환경변수가 저장값보다 우선합니다: {{ deploymentOverrideNames.join(', ') }}
      </div>
      <div v-if="!canManage" class="ag-warn">
        읽기 전용입니다. MAXIS 프로필·Key·실행 경로 변경은 매니저만 할 수 있습니다.
      </div>
      <div v-if="selected && selected.configError" class="ag-err">{{ selected.configError }}</div>

      <div v-if="selected" class="mx-scroll">
        <section class="ag-sec mx-profile-summary" aria-label="선택한 MAXIS 설정">
          <div class="mx-summary-title">
            <b>{{ selected.name }} 프로필</b>
            <span :class="selected.enabled ? 'mx-selected' : ''">{{ selected.active ? '연결 설정 선택됨' : '연결 설정 후보' }}</span>
          </div>
          <p class="mx-profile-host">{{ selected.effectiveBaseUrl || selected.effectiveHost || selected.baseUrl || '연결 주소 미설정' }}</p>
          <div class="mx-meta">
            <span><b>연결</b>{{ selected.verified ? '확인 완료' : '확인 필요' }}</span>
            <span><b>API Key</b>{{ workflowRows.filter(w => credential(w.key).configured).length }}/{{ workflowRows.length }}개 설정</span>
          </div>
          <div class="mx-actions">
            <button class="ag-ok inline" :disabled="!!busy || !canActivate"
                    :title="activationDisabledReason" @click="activate">{{ busy === 'activate' ? '적용 중…' : '이 설정 사용' }}</button>
            <button class="ag-mini" :disabled="!!busy || !canManage || hasUnsavedChanges"
                    @click="runProbe">{{ busy === 'probe' ? '확인 중…' : '연결 확인' }}</button>
          </div>
          <p v-if="activationDisabledReason" class="mx-help mx-disabled-reason">{{ activationDisabledReason }}</p>
          <p v-if="selected.dataPolicy === 'synthetic_only'" class="mx-help">
            DEV는 합성 데이터 연결·Shadow 검증용입니다. 연결 설정을 선택해도 Agent 최종 응답이 MAXIS로 바뀌지는 않습니다.
          </p>
          <div class="mx-runtime-status" aria-label="현재 기능별 실행 상태">
            <div v-for="row in runtimeRows" :key="row.key" class="mx-runtime-row">
              <b>{{ row.label }}</b><span :class="row.usingSelected ? 'mx-selected' : ''">{{ row.labelText }}</span>
              <small v-if="row.reason">{{ row.reason }}</small>
            </div>
          </div>
          <details v-if="Object.keys(probeRows).length" class="mx-probe-details">
            <summary>마지막 연결 확인 결과</summary>
            <p class="mx-help">{{ selected.lastProbe && selected.lastProbe.at ? selected.lastProbe.at : '확인 시각 없음' }}</p>
            <div class="ag-probe">
              <div v-for="(row,key) in probeRows" :key="key" class="ag-row" :class="row.ok ? 'ok' : 'no'">
                <b>{{ key }}</b><span>{{ row.ok ? '정상' : '실패' }}</span>
                <em>{{ probeDetail(row) }}</em>
              </div>
            </div>
          </details>
        </section>

        <details class="ag-sec mx-details" :open="profileEditorOpen" @toggle="profileEditorOpen=$event.target.open">
          <summary>연결 설정 편집</summary>
          <p class="mx-help">저장 위치: {{ selected.name }} 프로필</p>
          <div class="mx-meta">
            <span><b>Stage</b>{{ selected.stage }}</span><span><b>Host</b>{{ selected.effectiveHost || '미설정' }}</span>
            <span><b>Data policy</b>{{ selected.dataPolicy }}</span><span><b>Fingerprint</b>{{ selected.fingerprint || '미확정' }}</span>
          </div>
          <label class="ag-f"><span>Base URL</span><input v-model="form.baseUrl" :readonly="!canManage || !!busy" placeholder="https://maxis.example/v1"></label>
          <label class="ag-f"><span>Data policy</span><input :value="form.dataPolicy" readonly></label>
          <label class="ag-f"><span>Workflow bundle</span><input v-model="form.workflowBundleVersion" :readonly="!canManage || !!busy" placeholder="예: ltm-agent-2026.09.1"></label>
          <label class="ag-f"><span>Knowledge version</span><input v-model="form.knowledgeVersion" :readonly="!canManage || !!busy" placeholder="예: ltm-knowledge-2026.09"></label>
          <label class="ag-f"><span>Dify Knowledge dataset UUID</span><input v-model="form.knowledgeDatasetId" :readonly="!canManage || !!busy" placeholder="예: 12345678-1234-4123-8123-123456789abc"></label>
          <label class="ag-f"><span>Knowledge release sentinel SHA-256</span><input v-model="form.knowledgeSentinelSha256" :readonly="!canManage || !!busy" placeholder="Dify에 저장한 릴리스 센티널의 SHA-256"></label>
          <small class="mx-help">센티널 원문은 Dify Knowledge에만 두고, LTM에는 SHA-256만 저장합니다.</small>
          <div v-if="selected.knowledgeManifestSha256" class="mx-meta">
            <span><b>Knowledge manifest</b>{{ selected.knowledgeManifestSha256.slice(0, 16) }}…</span>
            <span><b>Tracked docs</b>{{ selected.knowledgeDocumentCount }}</span>
          </div>
          <div v-if="selected.stage === 'prod' && knowledgeEval" class="mx-promotion-gate"
               :class="knowledgeEvalReady ? 'on' : 'off'">
            <b>Knowledge 원격 평가 {{ knowledgeEvalReady ? '권한 검증 완료' : '운영 권한 차단' }}</b>
            <span v-if="knowledgeEvalReady">
              현재 PROD 배포의 독립 검수와 서명 검증 완료
            </span>
            <span v-else>
              현재 PROD 배포에 결속된 서명 평가 결과물 검증이 필요합니다.
              <template v-if="knowledgeEval.bindingValid"> metadata binding 확인됨 · 권한 증명 아님</template>
              <template v-if="knowledgeEval.expectedBinding && knowledgeEval.expectedBinding.profileMaterialSha256">
                예상 binding {{ knowledgeEval.expectedBinding.profileMaterialSha256.slice(0, 16) }}…
              </template>
            </span>
          </div>
          <div v-if="selected.stage === 'prod'" class="ag-warn">
            Knowledge 평가 값은 외부 결과물의 ID·SHA-256을 현재 배포에 묶는 metadata입니다.
            실제 Dify 평가 실행을 자동 검증했다는 뜻은 아닙니다. LTM이 artifact 본문·서명을
            검증하지 않는 동안에는 운영 권한을 부여하지 않습니다.
          </div>
          <div v-if="selected.stage === 'prod'" class="mx-workflow">
            <div><b>Dify 원격 실행 로그 정책</b><span>운영 전 관리자 승인 증빙</span></div>
            <label class="ag-f"><span>승인 정책 ID</span><input v-model="form.remoteLogPolicyId" :readonly="!canManage || !!busy" placeholder="예: ISMS-AI-LOG-2026-014"></label>
            <label class="ag-f"><span>로그 내용 범위</span><select v-model="form.remoteLogMode" :disabled="!canManage || !!busy">
              <option value="">선택 필요</option>
              <option value="redacted">redacted — 민감 내용 제거</option>
              <option value="approved_bounded_content">approved_bounded_content — 승인된 최소 내용</option>
            </select></label>
            <label class="ag-f"><span>보존 기간(일)</span><input type="number" min="0" max="90" step="1"
              v-model.number="form.remoteLogRetentionDays" :readonly="!canManage || !!busy"></label>
            <div class="ag-warn">이 값은 관리자 승인 증빙이며 Dify 설정을 자동 검사하지 않습니다. Dify 실행 이력에는 입력·출력 원문이 남을 수 있으므로 실제 Workspace 로그 설정을 별도로 대조해야 합니다.</div>
          </div>
          <div v-else-if="selected.stage === 'dev'" class="mx-help">
            DEV 원격 로그는 synthetic-only · redacted · 0일 정책으로 고정됩니다.
          </div>
          <div v-else-if="selected.stage === 'internal-pilot'" class="ag-warn">
            내부 시범은 DEV와 별도 환경이며 아직 실행할 수 없습니다.
            신규 자산의 콘텐츠 보존 상한 24시간·메타데이터 7일, 사용자 접근권한과
            불변 릴리스의 실제 서버 검증이 필요합니다. 설정·증적 등록만으로 활성화되지 않습니다.
            정책 세부 설정은 관리자 API와 내부 시범 템플릿을 사용합니다.
          </div>
          <div v-for="w in workflowRows" :key="w.key" class="mx-workflow">
            <div><b>{{ w.label }}</b><span>{{ w.hint }}</span></div>
            <label class="ag-f"><span>Binding</span><input v-model="form.workflows[w.key].binding" readonly></label>
            <label class="ag-f"><span>게시 Workflow UUID</span><input v-model="form.workflows[w.key].version" placeholder="xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx" :readonly="!canManage || !!busy"></label>
          </div>
          <button class="ag-ok inline" :disabled="!!busy || !canManage" @click="saveProfile">
            {{ busy === 'profile' ? '저장 중…' : '후보 프로필 저장' }}
          </button>
        </details>

        <details class="ag-sec mx-details" :open="keysEditorOpen" @toggle="keysEditorOpen=$event.target.open">
          <summary>API Key 관리</summary>
          <p class="mx-help">Dify App API Key · 환경변수 &gt; DPAPI 저장값. 빈 입력은 저장된 Key를 유지합니다.</p>
          <div v-for="w in workflowRows" :key="'key-'+w.key" class="mx-key-row">
            <label class="ag-f"><span>{{ w.label }}</span><input type="password" autocomplete="new-password"
              v-model="keys[w.keyField]" :readonly="!canManage || !!busy" :placeholder="credentialLabel(w.key)"></label>
            <label v-if="credential(w.key).configured && credential(w.key).source !== 'environment'" class="mx-clear">
              <input type="checkbox" v-model="clearKeys[w.keyField]" :disabled="!canManage || !!busy"> 저장된 Key 삭제
            </label>
            <small>{{ credentialLabel(w.key) }}</small>
          </div>
          <button class="ag-mini" :disabled="!!busy || !canManage" @click="saveCredentials">
            {{ busy === 'credentials' ? '암호화 저장 중…' : 'API Key 저장' }}
          </button>
        </details>

        <details class="ag-sec mx-details mx-observations">
          <summary>과거 원격 요청 관측</summary>
          <p class="mx-help">현재 설정과 일치: 미확인 · 요청 기록은 현재 활성화나 응답 품질의 증명이 아닙니다.</p>
          <p v-if="remoteObservationState === 'unavailable'" class="ag-warn">관측 기록 조회 불가 · 실제 처리 경로 미확인</p>
          <p v-else-if="!remoteObservations.length" class="mx-help">선택한 프로필의 원격 요청 기록 없음 · 실제 처리 경로 미확인</p>
          <div v-for="row in remoteObservations" :key="row.key" class="mx-workflow">
            <b>{{ row.label }}</b>
            <p class="mx-help" style="margin:4px 0">관측 시각: {{ row.observedAt }}</p>
            <p class="mx-help" style="margin:4px 0">경로: {{ row.route }} · 사용자 결과 주체: {{ row.outputOwner }}</p>
            <p class="mx-help" style="margin:4px 0">요청 기록: {{ row.audit }} · 전송: {{ row.transport }} · HTTP: {{ row.http }} · 결과: {{ row.result }}</p>
            <p class="mx-help" style="margin:4px 0">게시 Workflow UUID: {{ row.workflowVersion }} · Knowledge 버전: {{ row.knowledgeVersion }}</p>
            <p class="mx-help" style="margin:4px 0">원격 실행 UUID: {{ row.workflowRunId }}</p>
          </div>
        </details>

        <details class="ag-sec mx-details">
          <summary>기능별 실행 경로와 긴급 중단</summary>
          <p class="mx-help">실행 경로는 현재 연결 설정({{ st.activeProfile || '미선택' }})에 적용됩니다.</p>
          <div v-for="w in workflowRows" :key="'route-'+w.key" class="ag-f">
            <span>{{ w.label }}</span><select v-model="routes[w.key]" :disabled="!!busy || !canManage">
              <option v-for="mode in routeModes" :key="mode.key" :value="mode.key"
                      :disabled="mode.key === 'maxis' && (selected.dataPolicy === 'synthetic_only' || !knowledgeEvalReady)">{{ mode.label }} — {{ mode.hint }}</option>
            </select>
          </div>
          <div v-if="selected.dataPolicy === 'synthetic_only'" class="ag-warn">
            DEV는 합성 데이터 canary·Shadow 검증 전용이며 MAXIS를 최종 응답 주체로 지정할 수 없습니다.
          </div>
          <div v-if="selected.stage === 'prod' && !knowledgeEvalReady" class="ag-warn">
            서명된 PROD 평가 검증 전에는 Shadow만 사용할 수 있으며 MAXIS 운영 경로는 저장할 수 없습니다.
          </div>
          <div v-for="w in workflowRows" :key="'promotion-'+w.key">
            <div v-if="selected.stage === 'prod' && promotion(w.key)" class="mx-promotion-gate"
                 :class="promotion(w.key).ready ? 'on' : 'off'">
              <b>{{ w.label }} 운영 승격 {{ promotion(w.key).ready ? '통과' : '대기' }}</b>
              <span>{{ promotionDetail(w.key) }}</span>
            </div>
          </div>
          <button class="ag-mini" :disabled="!!busy || !canManage || profileDirty || keysDirty" @click="saveRoutes">실행 경로 저장</button>
          <div class="mx-kill">
            <div><b>MAXIS 긴급 중단</b><span>활성화하면 MAXIS 요청을 차단하며 Local로 조용히 우회하지 않습니다.</span></div>
            <button class="sm-switch" :class="{ on: st.killSwitch }" role="switch" :aria-checked="st.killSwitch"
                    :disabled="!!busy || !canManage" @click="setKill(!st.killSwitch)"><span class="sm-knob"></span></button>
          </div>
        </details>

        <details v-if="impact" class="ag-sec mx-details">
          <summary>AI 효과 · 최근 {{ impact.windowDays }}일</summary>
          <p class="mx-help">{{ impact.privacy }}</p>
          <div class="mx-impact">
            <span><b>{{ impact.draftCount }}</b>초안</span>
            <span><b>{{ pct(impact.acceptanceRatePct) }}</b>승인율</span>
            <span><b>{{ pct(impact.noMajorRewriteRatePct) }}</b>무대폭수정률</span>
            <span><b>{{ duration(impact.averageReviewSeconds) }}</b>평균검토시간</span>
          </div>
          <p class="mx-impact-note">
            AI 초안의 승인·수정·검토시간 운영지표입니다. 기존 Jira Dashboard/수작업 기준선은
            별도로 수집해야 하며, 아래에서 LTM Local과 MAXIS/Dify를 같은 기능·Role 단위로 비교합니다.
          </p>
          <div v-if="impactSegments().length" class="mx-impact-segments">
            <div class="mx-impact-row head">
              <span>기능 · Role</span><span>실행 경로</span><span>검토 초안</span>
              <span>승인율</span><span>무대폭수정</span><span>평균 검토</span>
            </div>
            <div v-for="(row, i) in impactSegments()" :key="i" class="mx-impact-row">
              <span><b>{{ featureLabel(row.feature) }}</b><small>{{ row.role }}</small></span>
              <span><b>{{ engineLabel(row) }}</b><small v-if="row.workflowBundleVersion">{{ row.workflowBundleVersion }}</small></span>
              <span>{{ row.reviewedCount }} / {{ row.draftCount }}</span>
              <span>{{ pct(row.acceptanceRatePct) }}</span>
              <span>{{ pct(row.noMajorRewriteRatePct) }}</span>
              <span>{{ duration(row.averageReviewSeconds) }}</span>
            </div>
          </div>
        </details>
        <div v-else-if="impactDenied" class="ag-hint">AI 효과 집계는 매니저에게만 표시됩니다.</div>
      </div>
    </template>
    <div v-show="view==='add'" class="mx-add-content">
      <section class="ag-sec">
        <div class="ag-lab">MAXIS 설정 추가</div>
        <p class="mx-help">연결할 MAXIS의 주소와 앱 API Key를 저장할 프로필을 선택하세요. 기존 프로필에 설정을 입력하거나 수정합니다.</p>
        <label class="ag-f"><span>저장할 프로필</span><select v-model="addProfileName">
          <option v-for="p in profiles" :key="p.name" :value="p.name">{{ p.name }}{{ p.effectiveHost ? ' · ' + p.effectiveHost : ' · 주소 미설정' }}</option>
        </select></label>
        <button class="ag-ok inline" @click="openManualSetup">선택한 프로필 설정</button>
      </section>
      <section class="ag-sec mx-setup-choice">
        <div class="ag-lab">저장소 파일로 준비</div>
        <p class="mx-help">새 PROD 포털에 Knowledge와 Chatflow를 준비할 때 사용합니다. 저장 위치: prod 프로필.</p>
        <button v-if="setupMode!=='repository'" class="ag-mini" :disabled="!canManage"
                @click="setupMode='repository'">저장소 파일로 PROD 설정</button>
      </section>
      <MaxisSetupWizard v-if="setupMode==='repository'" :can-manage="canManage" @configured="setupConfigured" />
    </div>
  </div>`,
};
