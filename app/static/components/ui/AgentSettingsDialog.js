// AgentSettingsDialog.js — AI 에이전트 설정 + **연결 테스트**.
//
// 이 화면의 존재 이유는 "안 된다"를 사용자가 스스로 진단하게 하는 것이다. LLM 연동은 실패할
// 자리가 많다(키 오타 · 배포명과 모델명 혼동 · api-version · 사내망 차단). 로그를 볼 수 없는
// 사용자에게 "실패했습니다" 한 줄만 주면 아무것도 할 수 없다. 그래서 chat 과 embeddings 를
// **각각** 실제로 한 번씩 불러 보고, 걸린 자리와 원문 오류를 그대로 보여 준다.
//
// 비밀값은 **한 방향으로만** 흐른다 — 저장은 여기서 보내고, 조회는 마스킹된 것만 받는다.
// 이미 저장된 키는 자리표시자로만 보이고, 비워 두면 기존 값이 유지된다(다시 칠 필요 없다).
import { agentApi } from "../../lib/agentApi.js";

// provider 마다 물어볼 것이 다르다. 여기 한 곳에 모아 두면 화면과 서버가 갈라지지 않는다.
const PROVIDERS = [
  { k: "aoai", label: "Azure OpenAI", hint: "사내/채점 환경. 환경변수(AOAI_*)가 있으면 그것이 우선합니다.",
    fields: [["aoaiEndpoint", "엔드포인트", "https://xxx.openai.azure.com", false],
             ["aoaiApiKey", "API 키", "", true]],
    models: ["배포명 (모델명 아님)", "임베딩 배포명"] },
  { k: "openai", label: "OpenAI", hint: "개발 PC 에서 쓰는 개인 키. 사내 AOAI 는 사내망에서만 열립니다.",
    fields: [["openaiApiKey", "API 키", "sk-...", true]],
    models: ["채팅 모델", "임베딩 모델"] },
  { k: "openai_compat", label: "OpenAI 호환", hint: "자체 LLM 등 OpenAI 규격을 따르는 엔드포인트.",
    fields: [["compatBaseUrl", "Base URL", "https://llm.example/v1", false],
             ["compatApiKey", "API 키", "", true],
             ["compatHeaders", "추가 헤더 (JSON)", '{"X-Auth":"..."}', true]],
    models: ["채팅 모델", "임베딩 모델"] },
  { k: "fake", label: "테스트(가짜)", hint: "키 없이 도는 결정적 모델. 화면·흐름 확인용입니다.",
    fields: [], models: [] },
];

export default {
  name: "AgentSettingsDialog",
  emits: ["close"],
  data() {
    return {
      st: null, err: "", busy: false, saving: false,
      provider: "aoai", chatModel: "", embedModel: "", apiVersion: "",
      secrets: {},              // 사용자가 **이번에 새로 친 것만** 담긴다
      probe: null,              // 연결 테스트 결과
      index: null,              // 색인 현황
      // 모델 콤보박스 재료 — 서버가 실 API 에서 조회한다. 실패하면 자유 입력으로 폴백
      // (목록은 참고이지 제약이 아니다).
      models: { chat: [], embed: [], error: "" },
      modelsBusy: false,
      comboOpen: "",            // "chat" | "embed" | "" — 열려 있는 모델 드롭다운
    };
  },
  watch: {
    // provider 를 바꾸면 목록도 그 provider 것으로 — 단, 저장 전이라 서버는 아직 이전
    // provider 다. 안내만 하고, 목록은 저장 후 자동 갱신된다.
    provider() { this.models = { chat: [], embed: [], error: "" }; },
  },
  computed: {
    providers() { return PROVIDERS; },
    cur() { return PROVIDERS.find((p) => p.k === this.provider) || PROVIDERS[0]; },
    masked() { return (this.st && this.st.secrets) || {}; },
  },
  mounted() {
    this.load();
    this._closeCombo = (e) => {
      if (this.comboOpen && !e.target.closest(".ag-combo")) this.comboOpen = "";
    };
    document.addEventListener("mousedown", this._closeCombo, true);
    document.addEventListener("keydown", this._esc = (e) => { if (e.key === "Escape") this.$emit("close"); });
  },
  unmounted() {
    document.removeEventListener("keydown", this._esc);
    document.removeEventListener("mousedown", this._closeCombo, true);
  },
  methods: {
    async load() {
      try {
        this.st = await agentApi.status();
        this.provider = this.st.provider || "aoai";
        this.chatModel = this.st.chatModel || "";
        this.embedModel = this.st.embedModel || "";
        this.apiVersion = this.st.apiVersion || "";
      } catch (e) { this.err = (e && e.message) || "설정을 불러오지 못했습니다"; }
      agentApi.indexStats().then((r) => { this.index = r; }).catch(() => {});
      this.loadModels();
    },

    /** 보이는 콤보박스 — datalist 는 화살표가 없어 목록이 있는지조차 안 보인다(사용자 지적).
     *  ▾ 를 누르면 전체 목록, 타이핑하면 걸러진 목록. 직접 입력도 그대로 유효하다. */
    toggleCombo(kind) {
      this.comboOpen = this.comboOpen === kind ? "" : kind;
      if (this.comboOpen && !this.models.chat.length && !this.modelsBusy) this.loadModels();
    },
    comboOpts(kind) {
      const list = kind === "chat" ? this.models.chat : this.models.embed;
      const cur = (kind === "chat" ? this.chatModel : this.embedModel).trim().toLowerCase();
      if (!cur || list.some((m) => m.toLowerCase() === cur)) return list;
      return list.filter((m) => m.toLowerCase().includes(cur));
    },
    pickModel(kind, m) {
      if (kind === "chat") this.chatModel = m; else this.embedModel = m;
      this.comboOpen = "";
    },

    async loadModels() {
      if (this.modelsBusy) return;
      this.modelsBusy = true;
      try { this.models = await agentApi.models(); }
      catch (e) { this.models = { chat: [], embed: [], error: (e && e.message) || "조회 실패" }; }
      finally { this.modelsBusy = false; }
    },

    /** 이미 저장된 키는 자리표시자로만 보인다 — 다시 칠 필요가 없어야 한다. */
    ph(field, fallback) {
      const m = this.masked[field];
      return m ? m + " (비워 두면 유지)" : (fallback || "");
    },

    async save() {
      if (this.saving) return;
      this.saving = true; this.err = ""; this.probe = null;
      try {
        const body = { provider: this.provider, chatModel: this.chatModel,
                       embedModel: this.embedModel };
        if (this.provider === "aoai") body.apiVersion = this.apiVersion;
        // 빈 칸은 보내지 않는다 — 빈 문자열을 보내면 저장된 키를 지우게 된다.
        const s = {};
        for (const [k, v] of Object.entries(this.secrets)) if ((v || "").trim()) s[k] = v.trim();
        if (Object.keys(s).length) body.secrets = s;
        this.st = await agentApi.saveSettings(body);
        this.secrets = {};
        this.loadModels();                 // provider·키가 바뀌었으니 목록도 새 것으로
        await this.test();                 // 저장했으면 되는지까지 확인해 주는 게 맞다
      } catch (e) { this.err = (e && e.message) || "저장에 실패했습니다"; }
      finally { this.saving = false; }
    },

    async test() {
      if (this.busy) return;
      this.busy = true; this.probe = null; this.err = "";
      try { this.probe = await agentApi.probe(); }
      catch (e) { this.err = (e && e.message) || "연결 테스트를 실행하지 못했습니다"; }
      finally { this.busy = false; }
    },

    async resetIndex() {
      await agentApi.resetIndex().catch(() => {});
      this.index = await agentApi.indexStats().catch(() => null);
    },
  },

  template: `
  <div class="ag-back" @click.self="$emit('close')">
    <div class="ag-dlg">
      <div class="ag-h">
        <h3>AI 에이전트 설정</h3>
        <button class="ag-x" @click="$emit('close')" aria-label="닫기">✕</button>
      </div>

      <div v-if="!st" class="ag-body"><p>불러오는 중…</p></div>

      <div v-else class="ag-body">
        <div v-if="!st.available" class="ag-warn">{{ st.reason }}</div>

        <!-- provider -->
        <div class="ag-sec">
          <div class="ag-lab">연결 방식</div>
          <div class="ag-tabs">
            <button v-for="p in providers" :key="p.k" :class="{ on: provider === p.k }"
                    @click="provider = p.k">{{ p.label }}</button>
          </div>
          <div class="ag-hint">{{ cur.hint }}</div>
        </div>

        <!-- 키 -->
        <div v-if="cur.fields.length" class="ag-sec">
          <div class="ag-lab">인증</div>
          <label v-for="f in cur.fields" :key="f[0]" class="ag-f">
            <span>{{ f[1] }}</span>
            <input :type="f[3] ? 'password' : 'text'" :placeholder="ph(f[0], f[2])"
                   v-model="secrets[f[0]]" autocomplete="off" spellcheck="false">
          </label>
        </div>

        <!-- 모델 — 콤보박스(datalist): 목록에서 고르거나 직접 친다.
             목록 조회가 막힌 환경(권한 없는 키·자체 LLM)에서도 입력은 계속 돼야 한다. -->
        <div v-if="cur.models.length" class="ag-sec">
          <div class="ag-lab">모델
            <button class="ag-mini" :disabled="modelsBusy" @click="loadModels"
                    title="현재 저장된 provider 기준으로 사용 가능한 목록을 다시 조회">
              {{ modelsBusy ? '조회 중…' : '목록 새로고침 ↻' }}</button>
            <em v-if="models.chat.length" class="ag-mini-hint">{{ models.chat.length + models.embed.length }}개 확인됨</em>
          </div>
          <div class="ag-f"><span>{{ cur.models[0] }}</span>
            <div class="ag-combo">
              <input v-model="chatModel" spellcheck="false" autocomplete="off"
                     @focus="comboOpen = 'chat'" @input="comboOpen = 'chat'">
              <button class="ag-combo-btn" @click="toggleCombo('chat')" title="목록 열기">▾</button>
              <div v-if="comboOpen === 'chat' && comboOpts('chat').length" class="ag-combo-drop">
                <button v-for="m in comboOpts('chat')" :key="m"
                        :class="{ on: m === chatModel }"
                        @mousedown.prevent="pickModel('chat', m)">{{ m }}</button>
              </div>
            </div>
          </div>
          <div class="ag-f"><span>{{ cur.models[1] }}</span>
            <div class="ag-combo">
              <input v-model="embedModel" spellcheck="false" autocomplete="off"
                     @focus="comboOpen = 'embed'" @input="comboOpen = 'embed'">
              <button class="ag-combo-btn" @click="toggleCombo('embed')" title="목록 열기">▾</button>
              <div v-if="comboOpen === 'embed' && comboOpts('embed').length" class="ag-combo-drop">
                <button v-for="m in comboOpts('embed')" :key="m"
                        :class="{ on: m === embedModel }"
                        @mousedown.prevent="pickModel('embed', m)">{{ m }}</button>
              </div>
            </div>
          </div>
          <div v-if="models.error" class="ag-hint">목록 조회 실패 — 직접 입력하세요. ({{ models.error }})</div>
          <label v-if="provider === 'aoai'" class="ag-f"><span>api-version</span>
            <input v-model="apiVersion" placeholder="2024-10-21" spellcheck="false"></label>
          <div v-if="provider === 'aoai'" class="ag-hint">
            Azure 는 <b>모델명이 아니라 배포명</b>을 넣습니다. 가장 흔한 실수입니다.
          </div>
        </div>

        <!-- 관측 -->
        <div class="ag-sec">
          <div class="ag-lab">관측 (선택)</div>
          <label class="ag-f"><span>Langfuse Public Key</span>
            <input v-model="secrets.langfusePublicKey" :placeholder="ph('langfusePublicKey')" autocomplete="off"></label>
          <label class="ag-f"><span>Langfuse Secret Key</span>
            <input type="password" v-model="secrets.langfuseSecretKey" :placeholder="ph('langfuseSecretKey')" autocomplete="off"></label>
          <label class="ag-f"><span>Host</span>
            <input v-model="secrets.langfuseHost" :placeholder="ph('langfuseHost', 'https://cloud.langfuse.com')"></label>
          <div class="ag-hint">설정하지 않아도 동작합니다. 질의·응답은 어차피 파일 로그로 남습니다.</div>
        </div>

        <!-- 연결 테스트 -->
        <div class="ag-sec">
          <div class="ag-lab">연결 테스트</div>
          <button class="ag-cancel" :disabled="busy" @click="test">
            {{ busy ? '확인 중…' : '지금 확인' }}
          </button>
          <div v-if="probe" class="ag-probe">
            <div class="ag-row" :class="probe.chat && probe.chat.ok ? 'ok' : 'no'">
              <b>채팅</b>
              <template v-if="probe.chat && probe.chat.ok">
                <span>정상 · {{ probe.chat.ms }}ms</span><em>{{ probe.chat.sample }}</em>
              </template>
              <template v-else><span>실패</span><em>{{ probe.chat && probe.chat.error }}</em></template>
            </div>
            <div class="ag-row" :class="probe.embeddings && probe.embeddings.ok ? 'ok' : 'no'">
              <b>임베딩</b>
              <template v-if="probe.embeddings && probe.embeddings.ok">
                <span>정상 · {{ probe.embeddings.ms }}ms · {{ probe.embeddings.dim }}차원</span>
              </template>
              <template v-else><span>실패</span><em>{{ probe.embeddings && probe.embeddings.error }}</em></template>
            </div>
            <div v-if="probe.error" class="ag-row no"><b>설정</b><em>{{ probe.error }}</em></div>
          </div>
        </div>

        <!-- 색인 -->
        <div v-if="index" class="ag-sec">
          <div class="ag-lab">RAG 색인</div>
          <div class="ag-idx">
            <div>규칙 문서 {{ (index.static && index.static.documents || []).length }}개 ·
                 {{ (index.static && index.static.indexed) || 0 }}조각
                 <em v-if="index.static && index.static.fresh === false">(문서가 바뀜 — 다음 질의에서 다시 만듭니다)</em></div>
            <div>수집한 티켓·문서 {{ (index.dynamic && index.dynamic.documents) || 0 }}건 ·
                 벡터 {{ (index.dynamic && index.dynamic.vectors) || 0 }}개</div>
          </div>
          <button class="ag-cancel" @click="resetIndex">색인 비우기</button>
          <div class="ag-hint">임베딩 모델을 바꾸면 자동으로 다시 만듭니다. 결과가 이상할 때의 탈출구입니다.</div>
        </div>

        <div v-if="err" class="ag-err">{{ err }}</div>
      </div>

      <div class="ag-act">
        <button class="ag-ok" :disabled="saving" @click="save">
          {{ saving ? '저장 중…' : '저장하고 확인' }}
        </button>
        <button class="ag-cancel" @click="$emit('close')">닫기</button>
      </div>
    </div>
  </div>`,
};
