import { agentApi } from "../../lib/agentApi.js";

const APPS = [
  { key: "agent_core", label: "Agent Core", field: "agentCoreApiKey" },
  { key: "editor_author", label: "Editor Author", field: "editorAuthorApiKey" },
  { key: "pmo_brief", label: "PMO Brief", field: "pmoBriefApiKey" },
];

export default {
  name: "MaxisSetupWizard",
  props: { canManage: { type: Boolean, default: false } },
  emits: ["configured"],
  data() {
    return {
      step: 1, apps: APPS, setup: null, prod: null, qualification: null, loaded: false,
      busy: "", error: "", notice: "", portalUrl: "", baseUrl: "", hostConfirmed: false,
      assets: { datasetId: "", sentinelDatasetId: "", roleMetadataFieldId: "", sentinelDocumentId: "",
        documentIds: {}, model: { plugin_unique_identifier: "", provider: "", name: "" } },
      versions: { agent_core: "", editor_author: "", pmo_brief: "" },
      keys: { agentCoreApiKey: "", editorAuthorApiKey: "", pmoBriefApiKey: "" },
      logPolicy: "", logMode: "redacted", logDays: 0, includePmo: false,
      reviewerPublicKey: "", reviewerFingerprint: "", reviewerConfirmed: false,
      pollTimer: null,
    };
  },
  computed: {
    source() { return (this.setup && this.setup.source) || { documents: [] }; },
    run() { return this.setup && this.setup.currentRun; },
    sourceCurrent() { return !!this.run && this.run.sourceCurrent !== false; },
    qualificationReady() {
      if (!this.qualification || !this.qualification.valid) return false;
      const approved = this.qualification.workflows || [];
      return ["agent_core", "editor_author", ...(this.includePmo ? ["pmo_brief"] : [])].every((key) => approved.includes(key));
    },
    qualificationRun() { return this.qualification && this.qualification.currentRun; },
    evaluationRunning() {
      const state = this.qualificationRun && this.qualificationRun.state;
      return ["queued", "starting", "running", "review_required"].includes(state) && state !== "review_required";
    },
    portalLink() {
      try {
        const url = new URL(this.portalUrl);
        return url.protocol === "https:" && !url.username && !url.password ? url.href : "";
      } catch (_) { return ""; }
    },
  },
  mounted() { this.load(); },
  beforeUnmount() { if (this.pollTimer) clearTimeout(this.pollTimer); },
  methods: {
    async load() {
      try {
        const results = await Promise.all([agentApi.maxisSetup(), agentApi.maxisStatus()]);
        this.setup = results[0];
        this.prod = (results[1].profiles || []).find((row) => row.name === "prod") || null;
        const run = this.run;
        if (run) {
          this.baseUrl = run.baseUrl || (this.prod && this.prod.baseUrl) || this.baseUrl;
          if (run.assetInputs) {
            const input = run.assetInputs;
            this.assets = { datasetId: input.datasetId, sentinelDatasetId: input.sentinelDatasetId,
              roleMetadataFieldId: input.roleMetadataFieldId, sentinelDocumentId: input.sentinelDocumentId,
              documentIds: { ...input.documentIds }, model: { ...input.model } };
          }
        }
        this.source.documents.forEach((row) => {
          if (!(row.id in this.assets.documentIds)) this.assets.documentIds[row.id] = "";
        });
        if (this.prod) {
          if (!this.loaded) this.includePmo = !!(((this.prod.workflows || {}).pmo_brief || {}).version);
          APPS.forEach((app) => { this.versions[app.key] = ((this.prod.workflows || {})[app.key] || {}).version || this.versions[app.key]; });
          this.logPolicy = this.prod.remoteLogPolicyId || this.logPolicy;
          this.logMode = this.prod.remoteLogMode || this.logMode;
          if (this.prod.remoteLogRetentionDays != null) this.logDays = this.prod.remoteLogRetentionDays;
        }
        this.loaded = true;
        await this.loadQualification();
      } catch (error) { this.error = error.message || "PROD 준비 정보를 불러오지 못했습니다."; }
    },
    async loadQualification() {
      try {
        this.qualification = await agentApi.maxisQualification();
        if (this.pollTimer) clearTimeout(this.pollTimer);
        if (this.evaluationRunning) this.pollTimer = setTimeout(() => this.loadQualification(), 2000);
      } catch (_) { this.qualification = null; }
    },
    async action(name, execute) {
      if (this.busy || !this.canManage) return;
      this.busy = name; this.error = ""; this.notice = "";
      try { await execute(); }
      catch (error) { this.error = error.message || "PROD 설정 작업에 실패했습니다."; }
      finally { this.busy = ""; }
    },
    ensureOk(result) {
      if (result && result.ok === false) throw new Error(result.error || "설정을 확인하세요.");
      return result;
    },
    download(kind) {
      if (!this.run) return;
      const link = document.createElement("a");
      link.href = "/api/agent/maxis/setup/download/" + encodeURIComponent(this.run.runId) + "/" + kind;
      link.download = "ltm-prod-" + kind + ".zip";
      document.body.appendChild(link); link.click(); link.remove();
    },
    prepareKnowledge() {
      this.action("knowledge", async () => {
        this.ensureOk(await agentApi.maxisPrepareKnowledge());
        await this.load(); this.download("knowledge");
        this.notice = "정본 Knowledge와 이 PC에서 새로 만든 PROD 릴리스 표식을 내려받았습니다. 포털 업로드 후 다음 단계로 진행하세요.";
      });
    },
    registerHost() {
      this.action("host", async () => {
        const result = this.ensureOk(await agentApi.maxisRegisterHost(this.baseUrl, this.hostConfirmed));
        this.baseUrl = result.baseUrl;
        this.notice = "이 PC의 PROD Service API 호스트를 등록했습니다. 아직 API token을 전송하지 않았습니다.";
      });
    },
    prepareBundle() {
      this.action("bundle", async () => {
        this.ensureOk(await agentApi.maxisPrepareBundle({ ...this.assets, runId: this.run.runId, baseUrl: this.baseUrl }));
        await this.load(); this.download("workflows");
        this.notice = "실제 PROD 자산에 연결한 세 Chatflow 파일을 내려받았습니다. 포털에서 import한 뒤 게시하세요.";
      });
    },
    async importModel(event) {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      this.action("model", async () => {
        if (file.size > 2 * 1024 * 1024) throw new Error("모델 설정 YAML은 2 MiB 이하여야 합니다.");
        const result = this.ensureOk(await agentApi.maxisModelFromExport(await file.text()));
        this.assets.model = { ...result.model };
        this.notice = "모델 식별자만 가져왔습니다. 원본 파일·환경변수·credential은 저장하지 않습니다.";
      });
      event.target.value = "";
    },
    saveCandidate() {
      this.action("candidate", async () => {
        this.ensureOk(await agentApi.maxisSetupCandidate({ runId: this.run.runId,
          workflowVersions: Object.fromEntries(Object.entries(this.versions).filter(([key]) => key !== "pmo_brief" || this.includePmo)), remoteLogPolicyId: this.logPolicy,
          remoteLogMode: this.logMode, remoteLogRetentionDays: Number(this.logDays) }));
        await this.load(); this.$emit("configured");
        this.notice = "PROD 후보를 저장했습니다. 연결 확인과 평가 후 별도로 적용합니다.";
      });
    },
    saveTokens() {
      this.action("tokens", async () => {
        const body = {};
        APPS.forEach((app) => {
          if ((app.key !== "pmo_brief" || this.includePmo) && this.keys[app.field].trim()) body[app.field] = this.keys[app.field].trim();
        });
        if (!Object.keys(body).length) throw new Error("새로 저장할 앱 API token을 입력하세요.");
        this.ensureOk(await agentApi.maxisSaveCredentials("prod", body));
        Object.keys(this.keys).forEach((key) => { this.keys[key] = ""; });
        await this.load(); this.$emit("configured");
        this.notice = "현재 Windows 사용자의 DPAPI에 token을 저장했습니다. 원문은 다시 표시하지 않습니다.";
      });
    },
    tokenStatus(app) {
      const row = this.prod && this.prod.credentials && this.prod.credentials[app.key];
      return row && row.configured ? (row.source === "environment" ? "환경변수에서 사용" : "이 PC에 암호화 저장됨") : "미설정";
    },
    probe() {
      this.action("probe", async () => {
        this.ensureOk(await agentApi.maxisProbe("prod"));
        await this.load(); this.$emit("configured");
        this.notice = "앱 계약과 Knowledge 연결을 확인했습니다. 운영 사용에는 PROD 평가 검증이 필요합니다.";
      });
    },
    startQualification() {
      this.action("qualification", async () => {
        this.ensureOk(await agentApi.maxisStartQualification(this.includePmo ? APPS.map((app) => app.key) : ["agent_core", "editor_author"]));
        await this.loadQualification();
        this.notice = "이 PROD 환경에서 저장소 합성 fixture 평가를 시작했습니다. 완료 후 실제 출력 검토와 서명이 필요합니다.";
      });
    },
    registerReviewer() {
      this.action("reviewer", async () => {
        this.ensureOk(await agentApi.maxisRegisterReviewer({ publicKey: this.reviewerPublicKey.trim(),
          fingerprint: this.reviewerFingerprint.trim(), confirmed: this.reviewerConfirmed }));
        await this.loadQualification(); this.notice = "확인한 검토자 공개키를 이 PC에 등록했습니다.";
      });
    },
    async importArtifact(event) {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      this.action("artifact", async () => {
        if (file.size > 4 * 1024 * 1024) throw new Error("평가 결과 JSON은 4 MiB 이하여야 합니다.");
        const artifact = JSON.parse(await file.text());
        this.ensureOk(await agentApi.maxisImportQualification(artifact));
        await this.loadQualification(); this.$emit("configured");
        this.notice = "서명과 현재 PROD 배포 결속을 검증했습니다. 운영 적용은 마지막 버튼에서 수행합니다.";
      });
      event.target.value = "";
    },
    applyProduction() {
      this.action("activate", async () => {
        this.ensureOk(await agentApi.maxisApplyProduction(this.includePmo ? APPS.map((app) => app.key) : ["agent_core", "editor_author"]));
        await this.load(); this.$emit("configured");
        this.notice = "Agent와 에디터의 PROD MAXIS 경로를 적용했습니다. 새 대화와 에디터 AI에서 실제 경로를 확인하세요.";
      });
    },
  },
  template: `
  <section class="ag-sec mx-setup" style="overflow-wrap:anywhere;min-width:0;padding:16px 18px">
    <h3 style="margin:0 0 8px">PROD MAXIS 시작하기</h3>
    <p class="mx-help">새 PC에서 저장소 소스만으로 준비합니다. 포털 로그인은 해당 포털에서 진행하고, LTM에는 세 앱의 Service API token을 저장합니다.</p>
    <p class="mx-help"><a href="/api/agent/maxis/setup/guide" target="_blank" rel="noopener noreferrer">PROD 설정·검수·데모 가이드 열기</a> · 이 체크아웃의 저장소 정본입니다.</p>
    <div class="mx-stage-pick" role="tablist" aria-label="PROD 설정 단계" style="flex-wrap:wrap;gap:4px">
      <button v-for="n in 4" :key="n" class="ag-mini" :class="{on:step===n}" @click="step=n">{{ ['1 · 준비 파일','2 · 자산 연결','3 · 게시와 token','4 · 검증과 적용'][n-1] }}</button>
    </div>
    <p v-if="!canManage" class="ag-warn">준비 파일 다운로드와 설정 변경은 Jira 매니저만 할 수 있습니다.</p>
    <p v-if="run && !sourceCurrent" class="ag-warn">저장소 소스가 바뀌었습니다. 준비 파일부터 새로 생성하고 현재 PROD 자산을 다시 검증하세요.</p>
    <template v-if="step===1">
      <p class="mx-help">Knowledge {{ source.knowledgeVersion || '확인 중' }} · 정본 문서 {{ source.documents.length }}개. DEV 서버·DEV cache·다른 PC의 내보내기는 필요하지 않습니다.</p>
      <label class="ag-f"><span>PROD 포털 URL</span><input v-model="portalUrl" placeholder="https://portal.example" autocomplete="url"></label>
      <a v-if="portalLink" :href="portalLink" target="_blank" rel="noopener noreferrer">PROD 포털에서 로그인</a>
      <p class="mx-help">포털 ID/PW는 이 화면에서 수집하거나 저장하지 않습니다. 포털에 표시된 Service API 주소와 앱 token을 이후 단계에 사용하세요.</p>
      <button class="ag-ok inline" :disabled="!!busy || !canManage" @click="prepareKnowledge">{{ busy==='knowledge' ? '생성 중…' : '정본 Knowledge 준비 ZIP 생성·다운로드' }}</button>
      <button v-if="run" class="ag-mini" :disabled="!canManage" @click="download('knowledge')">현재 준비 ZIP 다시 받기</button>
      <ol class="mx-help"><li>처음에는 PROD Knowledge와 별도 릴리스 표식 dataset을 만들고, 갱신 시에는 기존 dataset·파일을 수정합니다. 날짜별 사본을 만들지 않습니다.</li><li>knowledge 폴더의 문서와 sentinel 폴더의 표식 파일을 각각 업로드·갱신합니다.</li><li>document-metadata.csv의 문서별 설명과 ltm_allowed_roles_v1 문자열 메타데이터를 설정합니다.</li><li>실제 dataset·문서·메타데이터 필드 UUID를 다음 단계에 입력합니다.</li></ol>
    </template>
    <template v-if="step===2">
      <p v-if="!run" class="ag-warn">1단계에서 준비 파일을 먼저 생성하세요.</p>
      <template v-else>
        <label class="ag-f"><span>PROD Service API Base URL</span><input v-model="baseUrl" placeholder="https://service.example/v1" autocomplete="url"></label>
        <label class="mx-help"><input type="checkbox" v-model="hostConfirmed" :disabled="!canManage"> 이 주소가 token을 전송할 실제 PROD 호스트임을 확인했습니다.</label>
        <button class="ag-mini" :disabled="!!busy || !canManage || !hostConfirmed" @click="registerHost">이 PC에 정확한 PROD 호스트 등록</button>
        <label class="ag-f"><span>Knowledge dataset UUID</span><input v-model="assets.datasetId" :readonly="!canManage"></label>
        <label class="ag-f"><span>별도 sentinel dataset UUID</span><input v-model="assets.sentinelDatasetId" :readonly="!canManage"></label>
        <label class="ag-f"><span>sentinel 문서 UUID</span><input v-model="assets.sentinelDocumentId" :readonly="!canManage"></label>
        <label class="ag-f"><span>ltm_allowed_roles_v1 메타데이터 필드 UUID</span><input v-model="assets.roleMetadataFieldId" :readonly="!canManage"></label>
        <details><summary>업로드한 정본 문서 UUID {{ source.documents.length }}개</summary><label v-for="doc in source.documents" :key="doc.id" class="ag-f"><span>{{ doc.remoteName }}</span><input v-model="assets.documentIds[doc.id]" :readonly="!canManage" placeholder="포털의 실제 문서 UUID"></label></details>
        <p class="mx-help">이 PROD 포털에서 승인된 모델 하나를 넣은 간단한 Chatflow를 YAML로 내보내 선택하면 아래 식별자를 자동 추출합니다. 원본을 저장하거나 그 안의 코드를 실행하지 않습니다.</p>
        <label class="ag-f"><span>승인된 모델 설정 YAML 가져오기</span><input type="file" accept=".yml,.yaml" :disabled="!!busy || !canManage" @change="importModel"></label>
        <label class="ag-f"><span>모델 plugin unique identifier</span><input v-model="assets.model.plugin_unique_identifier" :readonly="!canManage" placeholder="포털의 설치된 plugin 식별자"></label>
        <label class="ag-f"><span>모델 provider</span><input v-model="assets.model.provider" :readonly="!canManage" placeholder="포털의 provider 식별자"></label>
        <label class="ag-f"><span>승인된 모델 이름</span><input v-model="assets.model.name" :readonly="!canManage"></label>
        <button class="ag-ok inline" :disabled="!!busy || !canManage || !sourceCurrent" @click="prepareBundle">PROD Chatflow 3개 생성·다운로드</button>
        <button v-if="run.bundleReady" class="ag-mini" :disabled="!canManage" @click="download('workflows')">현재 Chatflow ZIP 다시 받기</button>
      </template>
    </template>
    <template v-if="step===3">
      <p class="mx-help">포털에서 Agent Core와 Editor Author를 import하고 승인된 모델·Knowledge를 확인한 후 Publish합니다. 각 앱의 실제 게시 Workflow UUID와 App API token을 사용합니다. PMO Brief는 선택한 경우에만 게시합니다.</p>
      <label class="mx-help"><input type="checkbox" v-model="includePmo" :disabled="!!busy"> PMO Brief도 게시·평가·적용</label>
      <template v-for="app in apps" :key="app.key"><label v-if="app.key!=='pmo_brief' || includePmo" class="ag-f"><span>{{ app.label }} 게시 Workflow UUID</span><input v-model="versions[app.key]" :readonly="!canManage"></label></template>
      <label class="ag-f"><span>원격 로그 승인 정책 ID</span><input v-model="logPolicy" :readonly="!canManage"></label>
      <label class="ag-f"><span>승인된 로그 내용</span><select v-model="logMode" :disabled="!canManage"><option value="redacted">민감 내용 제거</option><option value="approved_bounded_content">승인된 최소 내용</option></select></label>
      <label class="ag-f"><span>로그 보존 기간(일)</span><input v-model.number="logDays" type="number" min="0" max="90" step="1" :readonly="!canManage"></label>
      <p class="mx-help">포털의 실제 로그 정책을 대조하세요. 이 입력만으로 서버 보존 정책을 검사하지 않습니다.</p>
      <button class="ag-mini" :disabled="!!busy || !canManage || !run || !run.bundleReady || !sourceCurrent" @click="saveCandidate">게시 정보로 PROD 후보 저장</button>
      <template v-for="app in apps" :key="'token-'+app.key"><label v-if="app.key!=='pmo_brief' || includePmo" class="ag-f"><span>{{ app.label }} App API token</span><input v-model="keys[app.field]" type="password" autocomplete="new-password" :readonly="!canManage" :placeholder="tokenStatus(app)"></label></template>
      <button class="ag-mini" :disabled="!!busy || !canManage" @click="saveTokens">token을 Windows DPAPI에 저장</button>
      <p class="mx-help">다른 PC의 암호화 파일을 복사하지 마세요. 빈 token 입력은 기존 저장값을 유지합니다. 토큰은 다운로드 파일·Git·로그에 포함하지 않습니다.</p>
    </template>
    <template v-if="step===4">
      <button class="ag-mini" :disabled="!!busy || !canManage || !run || !run.candidateSaved || !sourceCurrent" @click="probe">저장된 PROD 연결과 Knowledge 확인</button>
      <p class="mx-help">연결 성공은 운영 품질 통과와 구별합니다. 실제 업무 자료를 쓰기 전에 이 PROD 환경에서 합성 평가를 실행합니다.<span v-if="qualification && qualification.requirements"> Core·Editor 기본 Knowledge {{ qualification.requirements.knowledgeCases }}건과 선택한 기능의 역할 검증이 필요합니다. PMO를 포함한 실제 실행 건수는 진행 상태를 따릅니다.</span></p>
      <p class="mx-help">평가 대상: Agent Core · Editor Author<span v-if="includePmo"> · PMO Brief</span>. PMO 선택은 3단계에서 변경합니다.</p>
      <button class="ag-mini" :disabled="!!busy || !canManage || evaluationRunning || !prod || !prod.verified" @click="startQualification">PROD 합성 평가 시작</button>
      <p v-if="qualificationRun" class="mx-help">평가 {{ qualificationRun.state }} · {{ qualificationRun.completed || 0 }}/{{ qualificationRun.total || 0 }}<span v-if="qualificationRun.errorCode"> · {{ qualificationRun.errorCode }}</span></p>
      <a v-if="qualificationRun && qualificationRun.runId && !evaluationRunning" :href="'/api/agent/maxis/qualification/runs/'+encodeURIComponent(qualificationRun.runId)+'/review'" download="ltm-prod-qualification-review.json">이 PROD 실행의 검토 원본 내려받기</a>
      <details><summary>독립 검토·서명과 공개키 등록</summary><p class="mx-help">검토자는 이 PROD PC에서 내려받은 원본의 report와 reviewTemplate을 직접 검토합니다. reviewTemplate을 review-completed.json으로 저장하고 판정·근거를 작성한 뒤 아래 오프라인 도구로 서명합니다. 개인 서명키는 이 화면에 넣지 마세요.</p><pre class="mx-help" style="white-space:pre-wrap">python -B -X utf8 tools/maxis_qualification.py reviewer-key --output .cache/maxis/reviewer-key.json
python -B -X utf8 tools/maxis_qualification.py sign --report .cache/maxis/downloaded-report.json --review .cache/maxis/review-completed.json --key .cache/maxis/reviewer-key.json --output .cache/maxis/qualification-signed.json</pre><p class="mx-help">검토 파일은 위 .cache/maxis 경로에 저장합니다. 첫 명령의 공개키·지문만 아래에 등록합니다. 서명키는 DPAPI로 보호됩니다. 실제 원본 검토와 서명 후 qualification-signed.json을 가져오세요.</p><label class="ag-f"><span>검토자 공개키</span><textarea v-model="reviewerPublicKey" rows="3" :readonly="!canManage"></textarea></label><label class="ag-f"><span>별도로 확인한 공개키 지문</span><input v-model="reviewerFingerprint" :readonly="!canManage"></label><label class="mx-help"><input type="checkbox" v-model="reviewerConfirmed" :disabled="!canManage"> 검토자와 공개키 지문을 별도로 확인했습니다.</label><button class="ag-mini" :disabled="!!busy || !canManage || !reviewerConfirmed" @click="registerReviewer">검토자 공개키 등록</button></details>
      <label class="ag-f"><span>서명된 평가 결과 JSON 가져오기</span><input type="file" accept="application/json,.json" :disabled="!!busy || !canManage" @change="importArtifact"></label>
      <p class="ag-gate" :class="qualificationReady ? 'on' : 'off'">{{ qualificationReady ? '현재 PROD 배포의 평가 서명 검증 완료' : ((qualification && qualification.reason) || '서명된 PROD 평가 검증 대기') }}</p>
      <button class="ag-ok inline" :disabled="!!busy || !canManage || !qualificationReady" @click="applyProduction">Agent·에디터에 PROD MAXIS 적용</button>
      <p class="mx-help">적용 후 새 Agent 대화와 본문·댓글 AI 초안을 확인하세요. Jira 변경은 기존 사용자 승인을 거칩니다.</p>
    </template>
    <details v-if="run"><summary>자동 계산된 릴리스 정보</summary><p class="mx-help">{{ run.workflowBundleVersion }} · {{ run.knowledgeVersion }}</p><p class="mx-help">source {{ run.sourceDigest }}</p><p class="mx-help">sentinel SHA-256 {{ run.knowledgeSentinelSha256 }}</p></details>
    <p v-if="busy" class="mx-help" role="status">처리 중…</p>
    <p v-if="notice" class="mx-note" role="status">{{ notice }}</p>
    <p v-if="error" class="ag-err" role="alert">{{ error }}</p>
  </section>`,
};
