// MAXIS/Dify workflow configuration.  This is intentionally separate from the
// OpenAI/AOAI model-provider editor: a Dify app owns a workflow contract/version,
// while model selection remains a MAXIS governance concern.
import { agentApi } from "../../lib/agentApi.js";

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
    workflows: Object.fromEntries(WORKFLOWS.map((w) =>
      [w.key, { binding: "", version: "" }])) };
}

export default {
  name: "MaxisSettingsPanel",
  data() {
    return {
      workflowRows: WORKFLOWS, routeModes: ROUTE_MODES,
      st: null, selectedName: "", form: blankForm(), routes: {},
      keys: { agentCoreApiKey: "", editorAuthorApiKey: "", pmoBriefApiKey: "" },
      clearKeys: { agentCoreApiKey: false, editorAuthorApiKey: false, pmoBriefApiKey: false },
      busy: "", err: "", note: "", probe: null, impact: null, impactDenied: false,
    };
  },
  computed: {
    profiles() { return (this.st && this.st.profiles) || []; },
    selected() { return this.profiles.find((p) => p.name === this.selectedName) || null; },
    canManage() { return !!this.st && this.st.canManage !== false; },
    deploymentOverrideNames() {
      const env = (this.st && this.st.environmentOverrides) || {};
      return [env.profile, env.baseUrl, env.workflowBundleVersion, env.knowledgeVersion,
        env.knowledgeDatasetId, env.knowledgeSentinelSha256,
        ...Object.values(env.workflowVersions || {}), ...Object.values(env.credentials || {})]
        .filter(Boolean);
    },
    canActivate() {
      if (!this.canManage) return false;
      if (!this.selected || !this.selected.verified || this.selected.configError) return false;
      if (this.selected.dataPolicy === "synthetic_only" &&
          Object.values(this.routes || {}).includes("maxis")) return false;
      if (this.st.environmentOverrides.profile && !this.selected.active) return false;
      const versionEnv = ((this.st.environmentOverrides || {}).workflowVersions) || {};
      const required = new Set(["agent_core"]);
      Object.entries(this.routes || {}).forEach(([key, mode]) => {
        if (mode === "shadow" || mode === "maxis") required.add(key);
      });
      return [...required].every((key) => {
        const workflow = (this.selected.workflows && this.selected.workflows[key]) || {};
        return workflow.binding && (workflow.version || versionEnv[key]) &&
          this.credential(key).configured;
      });
    },
    probeRows() {
      return (this.probe && this.probe.workflows) ||
        (this.selected && this.selected.lastProbe && this.selected.lastProbe.workflows) || {};
    },
  },
  mounted() { this.load(); this.loadImpact(); },
  methods: {
    async load(prefer) {
      try {
        this.st = await agentApi.maxisStatus();
        const selected = prefer || this.selectedName || this.st.activeProfile || "dev";
        this.selectProfile(this.profiles.some((p) => p.name === selected) ? selected : "dev");
        this.routes = { ...(this.st.routes || {}) };
      } catch (e) { this.err = (e && e.message) || "MAXIS 설정을 불러오지 못했습니다"; }
    },
    async loadImpact() {
      try { this.impact = await agentApi.maxisImpact(30); }
      catch (e) { this.impactDenied = !!(e && String(e.message || "").includes("403")); }
    },
    selectProfile(name) {
      const row = this.profiles.find((p) => p.name === name);
      if (!row) return;
      this.selectedName = name;
      this.form = { baseUrl: row.baseUrl || "", dataPolicy: row.dataPolicy || "",
        workflowBundleVersion: row.workflowBundleVersion || "",
        knowledgeVersion: row.knowledgeVersion || "",
        knowledgeDatasetId: row.knowledgeDatasetId || "",
        knowledgeSentinelSha256: row.knowledgeSentinelSha256 || "", workflows: {} };
      WORKFLOWS.forEach((w) => {
        const value = (row.workflows && row.workflows[w.key]) || {};
        this.form.workflows[w.key] = { binding: value.binding || "", version: value.version || "" };
      });
      this.keys = { agentCoreApiKey: "", editorAuthorApiKey: "", pmoBriefApiKey: "" };
      this.clearKeys = { agentCoreApiKey: false, editorAuthorApiKey: false, pmoBriefApiKey: false };
      this.probe = null; this.err = ""; this.note = "";
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
      if (this.busy) return;
      this.busy = "profile"; this.err = ""; this.note = "";
      try {
        const result = await agentApi.maxisSaveProfile(this.selectedName, this.form);
        this.st = result;
        await this.load(this.selectedName);
        this.note = "프로필을 저장했습니다. 변경된 조합은 다시 확인해야 합니다.";
      } catch (e) { this.err = (e && e.message) || "프로필을 저장하지 못했습니다"; }
      finally { this.busy = ""; }
    },
    async saveCredentials() {
      if (this.busy) return;
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
        await agentApi.maxisSaveCredentials(this.selectedName, body);
        await this.load(this.selectedName);
        this.note = "API Key를 현재 Windows 사용자 DPAPI로 저장했습니다. 원문은 다시 표시되지 않습니다.";
      } catch (e) { this.err = (e && e.message) || "API Key를 저장하지 못했습니다"; }
      finally { this.busy = ""; }
    },
    async runProbe() {
      if (this.busy) return;
      this.busy = "probe"; this.err = ""; this.note = "";
      try {
        const result = await agentApi.maxisProbe(this.selectedName);
        await this.load(this.selectedName);
        this.probe = result;
        this.note = result.ok ? "연결 확인에 성공했습니다. 이제 이 프로필을 적용할 수 있습니다."
          : (result.error || "연결 확인에 실패했습니다.");
      } catch (e) { this.err = (e && e.message) || "연결 확인에 실패했습니다"; }
      finally { this.busy = ""; }
    },
    async activate() {
      if (this.busy) return;
      this.busy = "activate"; this.err = ""; this.note = "";
      try {
        this.st = await agentApi.maxisActivate(this.selectedName);
        await this.load(this.selectedName);
        this.note = `${this.selectedName} 프로필을 적용했습니다.`;
      } catch (e) { this.err = (e && e.message) || "프로필을 적용하지 못했습니다"; }
      finally { this.busy = ""; }
    },
    async saveRoutes() {
      if (this.busy) return;
      this.busy = "routes"; this.err = ""; this.note = "";
      try {
        this.st = await agentApi.maxisSaveRoutes(this.routes);
        this.routes = { ...(this.st.routes || {}) };
        this.note = "기능별 실행 경로를 저장했습니다. 한 요청에서 두 Agent를 연쇄 실행하지 않습니다.";
      } catch (e) { this.err = (e && e.message) || "실행 경로를 저장하지 못했습니다"; }
      finally { this.busy = ""; }
    },
    async setKill(enabled) {
      if (this.busy) return;
      this.busy = "kill"; this.err = ""; this.note = "";
      try {
        this.st = await agentApi.maxisSetKillSwitch(enabled);
        this.routes = { ...(this.st.routes || {}) };
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
  <div class="mx-panel">
    <div v-if="!st" class="ag-body">MAXIS 설정을 불러오는 중…</div>
    <template v-else>
      <div class="mx-profile-bar">
        <div><b>MAXIS / Dify Workflow</b><span>모델 Provider와 분리된 실행·거버넌스 프로필</span></div>
        <div class="mx-stage-pick">
          <button v-for="p in profiles" :key="p.name" class="ag-mini"
                  :class="{ on: selectedName === p.name }" @click="selectProfile(p.name)">
            {{ p.stage.toUpperCase() }}<em v-if="p.active">{{ p.enabled ? '사용 중' : '재확인' }}</em>
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
        <section class="ag-sec">
          <div class="ag-lab">프로필</div>
          <div class="mx-meta">
            <span><b>Stage</b>{{ selected.stage }}</span><span><b>Host</b>{{ selected.effectiveHost || '미설정' }}</span>
            <span><b>Data policy</b>{{ selected.dataPolicy }}</span><span><b>Fingerprint</b>{{ selected.fingerprint || '미확정' }}</span>
          </div>
          <label class="ag-f"><span>Base URL</span><input v-model="form.baseUrl" :readonly="!canManage" placeholder="https://maxis.example/v1"></label>
          <label class="ag-f"><span>Data policy</span><input :value="form.dataPolicy" readonly></label>
          <label class="ag-f"><span>Workflow bundle</span><input v-model="form.workflowBundleVersion" :readonly="!canManage" placeholder="예: ltm-agent-2026.09.1"></label>
          <label class="ag-f"><span>Knowledge version</span><input v-model="form.knowledgeVersion" :readonly="!canManage" placeholder="예: ltm-knowledge-2026.09"></label>
          <label class="ag-f"><span>Dify Knowledge dataset UUID</span><input v-model="form.knowledgeDatasetId" :readonly="!canManage" placeholder="예: 12345678-1234-4123-8123-123456789abc"></label>
          <label class="ag-f"><span>Knowledge release sentinel SHA-256</span><input v-model="form.knowledgeSentinelSha256" :readonly="!canManage" placeholder="Dify에 저장한 릴리스 센티널의 SHA-256"></label>
          <small class="mx-help">센티널 원문은 Dify Knowledge에만 두고, LTM에는 SHA-256만 저장합니다.</small>
          <div v-if="selected.knowledgeManifestSha256" class="mx-meta">
            <span><b>Knowledge manifest</b>{{ selected.knowledgeManifestSha256.slice(0, 16) }}…</span>
            <span><b>Tracked docs</b>{{ selected.knowledgeDocumentCount }}</span>
          </div>
          <div v-for="w in workflowRows" :key="w.key" class="mx-workflow">
            <div><b>{{ w.label }}</b><span>{{ w.hint }}</span></div>
            <label class="ag-f"><span>Binding</span><input v-model="form.workflows[w.key].binding" readonly></label>
            <label class="ag-f"><span>Workflow version</span><input v-model="form.workflows[w.key].version" :readonly="!canManage"></label>
          </div>
          <button class="ag-ok inline" :disabled="!!busy || !canManage" @click="saveProfile">
            {{ busy === 'profile' ? '저장 중…' : '후보 프로필 저장' }}
          </button>
        </section>

        <section class="ag-sec">
          <div class="ag-lab">Dify App API Key <em>환경변수 &gt; DPAPI 저장값</em></div>
          <div v-for="w in workflowRows" :key="'key-'+w.key" class="mx-key-row">
            <label class="ag-f"><span>{{ w.label }}</span><input type="password" autocomplete="new-password"
              v-model="keys[w.keyField]" :readonly="!canManage" :placeholder="credentialLabel(w.key)"></label>
            <label v-if="credential(w.key).configured && credential(w.key).source !== 'environment'" class="mx-clear">
              <input type="checkbox" v-model="clearKeys[w.keyField]" :disabled="!canManage"> 저장된 Key 삭제
            </label>
            <small>{{ credentialLabel(w.key) }}</small>
          </div>
          <button class="ag-mini" :disabled="!!busy || !canManage" @click="saveCredentials">
            {{ busy === 'credentials' ? '암호화 저장 중…' : 'API Key 저장' }}
          </button>
        </section>

        <section class="ag-sec">
          <div class="ag-lab">연결 확인과 적용</div>
          <div class="ag-gate" :class="selected.enabled ? 'on' : 'off'">
            <b>{{ selected.enabled ? 'MAXIS 사용 가능' : (selected.verified ? '확인됨 · 적용 전' : '연결 미확인') }}</b>
            <span>{{ selected.active ? '활성 프로필' : '후보 프로필' }} · {{ selected.lastProbe && selected.lastProbe.at ? selected.lastProbe.at : '확인 기록 없음' }}</span>
          </div>
          <div v-if="Object.keys(probeRows).length" class="ag-probe">
            <div v-for="(row,key) in probeRows" :key="key" class="ag-row" :class="row.ok ? 'ok' : 'no'">
              <b>{{ key }}</b><span>{{ row.ok ? '정상' : '실패' }}</span>
              <em>{{ probeDetail(row) }}</em>
            </div>
          </div>
          <div class="mx-actions">
            <button class="ag-mini" :disabled="!!busy || !canManage" @click="runProbe">{{ busy === 'probe' ? '확인 중…' : '저장된 설정 연결 확인' }}</button>
            <button class="ag-ok inline" :disabled="!!busy || !canActivate" @click="activate">{{ busy === 'activate' ? '적용 중…' : '이 프로필 사용' }}</button>
          </div>
        </section>

        <section class="ag-sec">
          <div class="ag-lab">기능별 실행 경로</div>
          <div v-for="w in workflowRows" :key="'route-'+w.key" class="ag-f">
            <span>{{ w.label }}</span><select v-model="routes[w.key]" :disabled="!!busy || !canManage">
              <option v-for="mode in routeModes" :key="mode.key" :value="mode.key"
                      :disabled="selected.dataPolicy === 'synthetic_only' && mode.key === 'maxis'">{{ mode.label }} — {{ mode.hint }}</option>
            </select>
          </div>
          <div v-if="selected.dataPolicy === 'synthetic_only'" class="ag-warn">
            DEV는 합성 데이터 canary·Shadow 검증 전용이며 MAXIS를 최종 응답 주체로 지정할 수 없습니다.
          </div>
          <div v-for="w in workflowRows" :key="'promotion-'+w.key">
            <div v-if="selected.stage === 'prod' && promotion(w.key)" class="mx-promotion-gate"
                 :class="promotion(w.key).ready ? 'on' : 'off'">
              <b>{{ w.label }} 운영 승격 {{ promotion(w.key).ready ? '통과' : '대기' }}</b>
              <span>{{ promotionDetail(w.key) }}</span>
            </div>
          </div>
          <button class="ag-mini" :disabled="!!busy || !canManage" @click="saveRoutes">실행 경로 저장</button>
          <div class="mx-kill">
            <div><b>MAXIS 긴급 중단</b><span>활성화하면 MAXIS 요청을 차단하며 Local로 조용히 우회하지 않습니다.</span></div>
            <button class="sm-switch" :class="{ on: st.killSwitch }" role="switch" :aria-checked="st.killSwitch"
                    :disabled="!!busy || !canManage" @click="setKill(!st.killSwitch)"><span class="sm-knob"></span></button>
          </div>
        </section>

        <section v-if="impact" class="ag-sec">
          <div class="ag-lab">AI 효과 · 최근 {{ impact.windowDays }}일 <em>{{ impact.privacy }}</em></div>
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
        </section>
        <div v-else-if="impactDenied" class="ag-hint">AI 효과 집계는 매니저에게만 표시됩니다.</div>

        <div v-if="note" class="mx-note">{{ note }}</div>
        <div v-if="err" class="ag-err">{{ err }}</div>
      </div>
    </template>
  </div>`,
};
