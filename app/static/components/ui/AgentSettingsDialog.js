// AI 연결 설정. provider 고정 탭이 아니라 사용자가 이름 붙인 config 목록을 관리한다.
// 후보를 편집·검증하는 동안 현재 활성 config는 바뀌지 않고, 마지막 '이 설정 사용'에서만 바뀐다.
import { agentApi } from "../../lib/agentApi.js";
import { api } from "../../lib/api.js";

const PROVIDERS = [
  { k: "aoai", label: "Azure OpenAI", hint: "Azure 배포명과 api-version을 사용합니다.",
    fields: [["aoaiEndpoint", "엔드포인트", "https://xxx.openai.azure.com", false],
             ["aoaiApiKey", "API 키", "", true]],
    models: ["채팅 배포명", "임베딩 배포명"] },
  { k: "openai", label: "OpenAI", hint: "OpenAI API 키와 모델을 사용합니다.",
    fields: [["openaiApiKey", "API 키", "sk-...", true]],
    models: ["채팅 모델", "임베딩 모델"] },
  { k: "openai_compat", label: "OpenAI 호환", hint: "자체 LLM 등 OpenAI 규격 엔드포인트입니다.",
    fields: [["compatBaseUrl", "Base URL", "https://llm.example/v1", false],
             ["compatApiKey", "API 키", "", true],
             ["compatHeaders", "추가 헤더 (JSON)", '{"X-Auth":"..."}', true]],
    models: ["채팅 모델", "임베딩 모델"] },
  { k: "fake", label: "테스트(가짜)", hint: "키 없이 UI와 흐름을 확인하는 로컬 테스트용입니다.",
    fields: [], models: ["채팅 모델", "임베딩 모델"] },
];

export default {
  name: "AgentSettingsDialog",
  emits: ["close", "saved"],
  data() {
    return {
      st: null, env: "", selectedId: "", err: "", busy: false, saving: false,
      form: { name: "", provider: "", chatModel: "", chatModelSimple: "", embedModel: "", apiVersion: "" },
      models: { chat: [], embed: [], total: 0, error: "" },
      comboOpen: "", comboAll: false, authEdit: null, authProbe: null, probe: null,
      verify: null, verifyBusy: false, addOpen: false, addName: "", addProvider: "openai",
      userPrompt: "", showProjPrompt: false, index: null,
      extras: { langfusePublicKey: "", langfuseSecretKey: "", langfuseHost: "" },
    };
  },
  computed: {
    configs() { return (this.st && this.st.configs) || []; },
    selected() { return this.configs.find((x) => x.id === this.selectedId) || null; },
    active() { return this.configs.find((x) => x.active) || null; },
    cur() { return PROVIDERS.find((x) => x.k === this.form.provider) || PROVIDERS[0]; },
    addProviders() { return this.env === "prod" ? PROVIDERS.filter((x) => x.k !== "fake") : PROVIDERS; },
    masked() { return (this.selected && this.selected.secrets) || {}; },
    canActivate() { return !!(this.selected && !this.st.envSupplied && this.selected.authOk && this.selected.modelsOk); },
  },
  mounted() {
    this.load();
    api.health().then((h) => { this.env = (h && h.env) || ""; }).catch(() => {});
    agentApi.indexStats().then((r) => { this.index = r; }).catch(() => {});
    this._outside = (e) => { if (this.comboOpen && !e.target.closest(".ag-combo")) this.comboOpen = ""; };
    this._esc = (e) => { if (e.key === "Escape") this.$emit("close"); };
    document.addEventListener("mousedown", this._outside, true);
    document.addEventListener("keydown", this._esc);
  },
  unmounted() {
    document.removeEventListener("mousedown", this._outside, true);
    document.removeEventListener("keydown", this._esc);
  },
  methods: {
    providerLabel(k) { return (PROVIDERS.find((x) => x.k === k) || {}).label || k; },
    async load(preferId) {
      try {
        this.st = await agentApi.status();
        this.userPrompt = this.st.userPrompt || "";
        const id = preferId || this.selectedId || this.st.activeConfigId ||
                   ((this.st.configs || [])[0] || {}).id || "";
        if (id) this.selectConfig(id); else this.selectedId = "";
      } catch (e) { this.err = (e && e.message) || "설정을 불러오지 못했습니다"; }
    },
    selectConfig(id) {
      const row = this.configs.find((x) => x.id === id);
      if (!row) return;
      this.selectedId = id;
      this.form = { name: row.name || "", provider: row.provider || "",
        chatModel: row.chatModel || "", chatModelSimple: row.chatModelSimple || "",
        embedModel: row.embedModel || "", apiVersion: row.apiVersion || "" };
      this.models = { chat: [], embed: [], total: 0, error: "" };
      this.authProbe = null; this.probe = null; this.verify = null; this.err = "";
      if (row.authOk || row.provider === "fake") this.loadModels();
    },
    async createConfig() {
      if (this.saving) return;
      if (!this.addName.trim()) { this.err = "설정 이름을 입력하세요."; return; }
      this.saving = true; this.err = "";
      try {
        const r = await agentApi.createConfig(this.addName.trim(), this.addProvider);
        this.addOpen = false; this.addName = "";
        await this.load(r.config.id);
      } catch (e) { this.err = (e && e.message) || "설정을 추가하지 못했습니다"; }
      finally { this.saving = false; }
    },
    async importLegacy(old) {
      if (!old || this.saving) return;
      this.saving = true; this.err = "";
      try {
        const r = await agentApi.importLegacyConfig(this.providerLabel(old.provider) + " 이전 설정", old.provider);
        await this.load(r.config.id);
      } catch (e) { this.err = (e && e.message) || "이전 설정을 가져오지 못했습니다"; }
      finally { this.saving = false; }
    },
    async deleteSelected() {
      if (!this.selected || this.selected.active || this.busy) return;
      if (!window.confirm(`'${this.selected.name}' 설정을 삭제할까요?`)) return;
      this.busy = true; this.err = "";
      try { await agentApi.deleteConfig(this.selected.id); this.selectedId = ""; await this.load(); }
      catch (e) { this.err = (e && e.message) || "삭제하지 못했습니다"; }
      finally { this.busy = false; }
    },
    hasKey(k) { return !!this.masked[k]; },
    keyShown(k) { return String(this.masked[k] || "").replace("설정됨 ", ""); },
    anyKey() { return this.cur.fields.some((f) => this.hasKey(f[0])); },
    openAuthEdit() {
      const values = {};
      this.cur.fields.forEach((f) => { values[f[0]] = ""; });
      this.authEdit = { values, busy: false, err: "" };
    },
    async applyAuth() {
      if (!this.authEdit || this.authEdit.busy || !this.selected) return;
      const secrets = {};
      this.cur.fields.forEach((f) => { const v = (this.authEdit.values[f[0]] || "").trim(); if (v) secrets[f[0]] = v; });
      if (!Object.keys(secrets).length) { this.authEdit.err = "바꿀 값을 하나 이상 입력하세요."; return; }
      this.authEdit.busy = true; this.authEdit.err = "";
      try {
        await agentApi.updateConfig(this.selected.id, { apiVersion: this.form.apiVersion, secrets });
        const result = await agentApi.probeConfigAuth(this.selected.id);
        await this.load(this.selected.id);
        this.authProbe = result;
        if (result && result.ok) this.authEdit = null;
      } catch (e) { this.authEdit.err = (e && e.message) || "저장하지 못했습니다"; }
      finally { if (this.authEdit) this.authEdit.busy = false; }
    },
    async loadModels() {
      if (!this.selected || this.busy) return;
      this.busy = true; this.verify = null;
      try { this.models = await agentApi.configModels(this.selected.id); }
      catch (e) { this.models = { chat: [], embed: [], total: 0, error: (e && e.message) || "조회 실패" }; }
      finally { this.busy = false; }
    },
    comboVal(kind) { return kind === "chat" ? this.form.chatModel : kind === "simple" ? this.form.chatModelSimple : this.form.embedModel; },
    comboOpts(kind) {
      let rows = kind === "embed" ? this.models.embed : this.models.chat;
      if (kind !== "embed" && this.verify && (this.verify.ok || []).length)
        rows = rows.filter((x) => this.verify.ok.includes(x));
      const q = this.comboVal(kind).trim().toLowerCase();
      return this.comboAll || !q || rows.some((x) => x.toLowerCase() === q)
        ? rows : rows.filter((x) => x.toLowerCase().includes(q));
    },
    toggleCombo(kind) { this.comboOpen = this.comboOpen === kind ? "" : kind; this.comboAll = !!this.comboOpen; },
    pickModel(kind, value) {
      if (kind === "chat") this.form.chatModel = value;
      else if (kind === "simple") this.form.chatModelSimple = value;
      else this.form.embedModel = value;
      this.comboOpen = "";
    },
    async verifyModels() {
      if (!this.selected || this.verifyBusy) return;
      this.verifyBusy = true;
      try { this.verify = await agentApi.verifyConfigModels(this.selected.id, this.models.chat || []); }
      catch (e) { this.verify = { error: (e && e.message) || "확인 실패" }; }
      finally { this.verifyBusy = false; }
    },
    async saveModels() {
      if (!this.selected || this.saving) return;
      this.saving = true; this.err = ""; this.probe = null;
      try {
        await agentApi.updateConfig(this.selected.id, { name: this.form.name,
          chatModel: this.form.chatModel, chatModelSimple: this.form.chatModelSimple,
          embedModel: this.form.embedModel, apiVersion: this.form.apiVersion });
        const result = await agentApi.probeConfig(this.selected.id);
        await this.load(this.selected.id);
        this.probe = result;
      } catch (e) { this.err = (e && e.message) || "모델 설정을 저장하지 못했습니다"; }
      finally { this.saving = false; }
    },
    async activateSelected() {
      if (!this.selected || this.busy) return;
      this.busy = true; this.err = "";
      try {
        const r = await agentApi.activateConfig(this.selected.id);
        if (r.ok === false) throw new Error(r.error || "활성화하지 못했습니다");
        this.st = r; this.$emit("saved", r); await this.load(this.selected.id);
      } catch (e) { this.err = (e && e.message) || "활성화하지 못했습니다"; }
      finally { this.busy = false; }
    },
    async saveExtras() {
      this.saving = true; this.err = "";
      try {
        const secrets = {};
        Object.entries(this.extras).forEach(([k, v]) => { if ((v || "").trim()) secrets[k] = v.trim(); });
        const body = { userPrompt: this.userPrompt };
        if (Object.keys(secrets).length) body.secrets = secrets;
        this.st = await agentApi.saveSettings(body); this.extras = { langfusePublicKey: "", langfuseSecretKey: "", langfuseHost: "" };
        this.$emit("saved", this.st);
      } catch (e) { this.err = (e && e.message) || "추가 설정을 저장하지 못했습니다"; }
      finally { this.saving = false; }
    },
    async resetIndex() { await agentApi.resetIndex().catch(() => {}); this.index = await agentApi.indexStats().catch(() => null); },
  },
  template: `
  <div class="ag-back" @click.self="$emit('close')">
    <div class="ag-dlg ag-config-dlg">
      <div class="ag-h"><h3>AI 에이전트 연결 설정</h3><button class="ag-x" @click="$emit('close')">✕</button></div>
      <div v-if="!st" class="ag-body">불러오는 중…</div>
      <div v-else class="ag-config-layout">
        <aside class="ag-config-list">
          <div class="ag-config-list-head"><b>내 설정</b><button class="ag-mini" @click="addOpen = true">+ 추가</button></div>
          <button v-for="c in configs" :key="c.id" class="ag-config-item" :class="{ on: c.id === selectedId }" @click="selectConfig(c.id)">
            <span><b>{{ c.name }}</b><em>{{ providerLabel(c.provider) }}</em></span>
            <small v-if="c.verified" class="active">사용 중</small><small v-else-if="c.active">재확인</small><small v-else-if="c.authOk && c.modelsOk">확인됨</small>
          </button>
          <div v-if="!configs.length" class="ag-config-empty">등록한 연결 없음<br><button class="ag-mini" @click="addOpen = true">첫 설정 추가</button></div>
          <div v-for="old in (st.legacyCandidates || [])" :key="'legacy-' + old.provider" class="ag-legacy">
            <b>이전 설정 발견</b><span>{{ providerLabel(old.provider) }} · {{ old.chatModel || '모델 미설정' }}</span>
            <button class="ag-mini" :disabled="saving" @click="importLegacy(old)">가져오기</button>
            <em>자동으로 사용하지 않음. 가져온 뒤 확인·적용 필요</em>
          </div>
        </aside>

        <main class="ag-config-main">
          <div v-if="st.envSupplied" class="ag-warn">환경변수로 주입된 연결을 사용 중. named config는 저장할 수 있지만 환경변수를 제거하고 앱을 다시 시작하기 전에는 적용되지 않음</div>
          <div v-if="!selected" class="ag-config-empty big">왼쪽에서 설정을 고르거나 새로 추가하세요.</div>
          <template v-else>
            <section class="ag-sec ag-config-title">
              <label class="ag-f"><span>설정 이름</span><input v-model="form.name" maxlength="60"></label>
              <div class="ag-type-line"><span>연결 방식</span><b>{{ cur.label }}</b><em>{{ cur.hint }}</em></div>
              <div class="ag-gate" :class="selected.verified ? 'on' : 'off'">
                <b>{{ selected.verified ? '사용 중' : (selected.active ? '재확인 필요' : '미적용') }}</b>
                <span>① 인증 {{ selected.authOk ? '확인됨' : '미확인' }} · ② 모델 {{ selected.modelsOk ? '확인됨' : '미확인' }} · ③ {{ selected.verified ? '적용됨' : '적용 전' }}</span>
              </div>
            </section>

            <section v-if="cur.fields.length" class="ag-sec">
              <div class="ag-lab">인증</div>
              <div v-for="f in cur.fields" :key="f[0]" class="ag-f"><span>{{ f[1] }}</span>
                <input class="ag-keyin" readonly :value="hasKey(f[0]) ? keyShown(f[0]) : ''" :placeholder="hasKey(f[0]) ? '' : '아직 설정되지 않음'">
              </div>
              <button class="ag-mini" @click="openAuthEdit">{{ anyKey() ? '인증 정보 변경' : '인증 정보 입력' }}</button>
              <div v-if="authProbe" class="ag-probe"><div class="ag-row" :class="authProbe.ok ? 'ok' : 'no'"><b>인증</b><span>{{ authProbe.ok ? '정상' : '실패' }}</span><em>{{ authProbe.error || (authProbe.ms + 'ms') }}</em></div></div>
            </section>

            <section class="ag-sec">
              <div class="ag-lab">모델
                <button class="ag-mini" :disabled="busy" @click="loadModels">{{ busy ? '조회 중…' : '목록 새로고침' }}</button>
                <button v-if="models.chat.length" class="ag-mini" :disabled="verifyBusy" @click="verifyModels">{{ verifyBusy ? '확인 중…' : '후보 권한 확인' }}</button>
              </div>
              <div v-for="kind in ['chat','embed','simple']" :key="kind" class="ag-f">
                <span>{{ kind === 'chat' ? cur.models[0] : kind === 'embed' ? cur.models[1] : '간단한 역할 모델 (선택)' }}</span>
                <div class="ag-combo"><input :value="comboVal(kind)" @input="kind === 'chat' ? form.chatModel = $event.target.value : kind === 'embed' ? form.embedModel = $event.target.value : form.chatModelSimple = $event.target.value" @focus="comboOpen = kind; comboAll = false" :placeholder="kind === 'simple' ? '비우면 기본 모델 사용' : ''">
                  <button class="ag-combo-btn" @click="toggleCombo(kind)">▾</button>
                  <div v-if="comboOpen === kind" class="ag-combo-drop"><button v-for="m in comboOpts(kind)" :key="m" @mousedown.prevent="pickModel(kind,m)">{{ m }}</button><div v-if="!comboOpts(kind).length" class="ag-combo-empty">{{ models.error ? '목록 조회 실패 — 직접 입력' : '목록이 비어 있음 — 직접 입력' }}</div></div>
                </div>
              </div>
              <label v-if="form.provider === 'aoai'" class="ag-f"><span>api-version</span><input v-model="form.apiVersion"></label>
              <div v-if="models.total" class="ag-hint">서버 {{ models.total }}개 · 채팅 {{ models.chat.length }} · 임베딩 {{ models.embed.length }}</div>
              <div v-if="models.error" class="ag-hint">목록 조회 실패 — {{ models.error }}</div>
              <div v-if="verify" class="ag-hint">{{ verify.error || ('사용 가능 ' + (verify.ok || []).length + '개 · 제외 ' + Object.keys(verify.denied || {}).length + '개') }}</div>
              <div v-if="probe" class="ag-probe"><div class="ag-row" :class="probe.chat && probe.chat.ok ? 'ok' : 'no'"><b>채팅</b><span>{{ probe.chat && probe.chat.ok ? '정상' : '실패' }}</span><em>{{ probe.chat && (probe.chat.error || probe.chat.ms + 'ms') }}</em></div><div class="ag-row" :class="probe.embeddings && probe.embeddings.ok ? 'ok' : 'no'"><b>임베딩</b><span>{{ probe.embeddings && probe.embeddings.ok ? '정상' : '실패' }}</span><em>{{ probe.embeddings && (probe.embeddings.error || probe.embeddings.ms + 'ms') }}</em></div></div>
              <button class="ag-ok inline" :disabled="saving" @click="saveModels">{{ saving ? '확인 중…' : '저장하고 모델 확인' }}</button>
            </section>

            <section class="ag-sec ag-config-actions">
              <button class="ag-ok" :disabled="busy || !canActivate || selected.verified" @click="activateSelected">{{ selected.verified ? '현재 사용 중' : (selected.active ? '다시 적용' : '이 설정 사용') }}</button>
              <button class="ag-cancel" :disabled="selected.active || busy" @click="deleteSelected">삭제</button>
              <span v-if="!canActivate">인증과 모델 확인을 마쳐야 적용 가능</span>
            </section>
          </template>

          <details class="ag-sec ag-extra"><summary>프롬프트·관측·색인</summary>
            <div class="ag-lab">내 프롬프트</div><textarea v-model="userPrompt" class="ag-ta" rows="3"></textarea>
            <button class="ag-mini" @click="showProjPrompt = !showProjPrompt">{{ showProjPrompt ? '▾' : '▸' }} 프로젝트 공용 프롬프트</button><pre v-if="showProjPrompt" class="ag-proj-prompt">{{ st.projectPrompt }}</pre>
            <div class="ag-lab">Langfuse (선택)</div>
            <label class="ag-f"><span>Public Key</span><input v-model="extras.langfusePublicKey"></label><label class="ag-f"><span>Secret Key</span><input type="password" v-model="extras.langfuseSecretKey"></label><label class="ag-f"><span>Host</span><input v-model="extras.langfuseHost"></label>
            <button class="ag-mini" :disabled="saving" @click="saveExtras">추가 설정 저장</button>
            <button v-if="index" class="ag-mini" @click="resetIndex">RAG 색인 비우기</button>
          </details>
          <div v-if="err" class="ag-err">{{ err }}</div>
        </main>
      </div>
      <div class="ag-act"><button class="ag-cancel" @click="$emit('close')">닫기</button></div>

      <div v-if="addOpen" class="ag-back inner" @click.self="addOpen = false"><div class="ag-dlg small"><div class="ag-h"><h3>연결 설정 추가</h3><button class="ag-x" @click="addOpen = false">✕</button></div><div class="ag-body"><label class="ag-f"><span>설정 이름</span><input v-model="addName" autofocus placeholder="예: 개인 OpenAI, 사내 AOAI"></label><label class="ag-f"><span>연결 방식</span><select v-model="addProvider"><option v-for="p in addProviders" :key="p.k" :value="p.k">{{ p.label }}</option></select></label><div class="ag-hint">같은 연결 방식도 이름을 달리해 여러 개 등록 가능</div></div><div class="ag-act"><button class="ag-ok" :disabled="saving" @click="createConfig">추가</button><button class="ag-cancel" @click="addOpen = false">취소</button></div></div></div>

      <div v-if="authEdit" class="ag-back inner" @click.self="authEdit = null"><div class="ag-dlg small"><div class="ag-h"><h3>{{ form.name }} 인증 정보</h3><button class="ag-x" @click="authEdit = null">✕</button></div><div class="ag-body"><label v-for="f in cur.fields" :key="f[0]" class="ag-f"><span>{{ f[1] }}</span><input :type="f[3] ? 'password' : 'text'" v-model="authEdit.values[f[0]]" :placeholder="hasKey(f[0]) ? keyShown(f[0]) + ' (비우면 유지)' : f[2]"></label><div v-if="authEdit.err" class="ag-err">{{ authEdit.err }}</div></div><div class="ag-act"><button class="ag-ok" :disabled="authEdit.busy" @click="applyAuth">{{ authEdit.busy ? '확인 중…' : '저장하고 연결 확인' }}</button><button class="ag-cancel" @click="authEdit = null">취소</button></div></div></div>
    </div>
  </div>`,
};
