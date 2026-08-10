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
import { api } from "../../lib/api.js";

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
  emits: ["close", "saved"],
  data() {
    return {
      st: null, err: "", busy: false, saving: false,
      provider: "aoai", chatModel: "", chatModelSimple: "", embedModel: "", apiVersion: "",
      userPrompt: "",           // 사용자별 시스템 프롬프트(로컬 저장, 커밋 안 됨)
      showProjPrompt: false,    // 프로젝트 공용 프롬프트(읽기 전용) 펼침
      secrets: {},              // 사용자가 **이번에 새로 친 것만** 담긴다
      probe: null,              // 연결 테스트 결과
      index: null,              // 색인 현황
      // 모델 콤보박스 재료 — 서버가 실 API 에서 조회한다. 실패하면 자유 입력으로 폴백
      // (목록은 참고이지 제약이 아니다).
      models: { chat: [], embed: [], error: "" },
      modelsBusy: false,
      // 권한 확인 결과 — { ok:[...], denied:{name:사유} }. 누르기 전엔 null.
      verify: null, verifyBusy: false,
      comboOpen: "",            // "chat" | "embed" | "" — 열려 있는 모델 드롭다운
      comboAll: false,          // ▾ 로 열었나(전체) vs 타이핑 중인가(걸러 보기)
      // 실행 환경(mock/local/prod) — provider 목록을 가리는 데 쓴다. 아래 providers() 참고.
      env: "",
      // ── 인증 변경 팝업 ──────────────────────────────────────────────
      // 왜 팝업인가: 키는 **한 번 정하고 오래 안 건드리는 값**인데, 입력칸으로 늘 열어 두면
      // ①설정됐는지가 안 보이고(빈 칸은 '없음'처럼 보인다) ②실수로 지울 수 있다.
      // 평소에는 마스킹된 값을 **읽기 전용으로 보여 주고**, 바꿀 때만 팝업을 연다.
      //
      // ★ 항목 **하나가 아니라 provider 의 인증 항목 전부**를 한 팝업에서 받는다(사용자 지적).
      //   예전엔 칸마다 팝업이 따로였고 각각이 저장·연결확인을 했다 — AOAI(엔드포인트+키)나
      //   OpenAI 호환(Base URL+키+헤더)에서는 **첫 칸만 넣은 상태로 연결 테스트가 돌아**
      //   당연히 실패했다. 사용자에게는 "방금 넣은 게 틀렸다"로 보인다. 한 벌이어야 의미가
      //   생기는 값들이므로 입력도 검증도 한 번에 한다.
      authEdit: null,           // { fields: [...], values: {}, busy, result, err }
    };
  },
  watch: {
    // provider 를 바꾸면 목록도 그 provider 것으로 — 단, 저장 전이라 서버는 아직 이전
    // provider 다. 안내만 하고, 목록은 저장 후 자동 갱신된다.
    provider() { this.models = { chat: [], embed: [], error: "" }; },
  },
  computed: {
    /** prod 에서는 **테스트(가짜)를 뺀다**(사용자 지적).
     *  실 Jira 를 보는 화면에서 가짜 모델로 답을 만들면, 그 답이 진짜처럼 보인다 —
     *  고를 수 있게 두는 것 자체가 사고의 씨앗이다. mock/local 에서는 그대로 둔다
     *  (거기서는 키 없이 흐름을 보는 것이 정당한 용도다). */
    providers() {
      return this.env === "prod" ? PROVIDERS.filter((p) => p.k !== "fake") : PROVIDERS;
    },
    cur() { return PROVIDERS.find((p) => p.k === this.provider) || PROVIDERS[0]; },
    masked() { return (this.st && this.st.secrets) || {}; },
    /** 아직 아무 키도 안 잡힌 상태 — 첫 사용 안내를 띄울지의 기준.
     *  'fake' 는 키가 필요 없는 provider 라 안내가 필요 없다. */
    needsSetup() {
      if (this.provider === "fake") return false;
      const need = this.cur.fields.filter((f) => f[3]).map((f) => f[0]);
      return need.length > 0 && !need.some((k) => this.masked[k]);
    },
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
        this.chatModelSimple = this.st.chatModelSimple || "";
        this.embedModel = this.st.embedModel || "";
        this.apiVersion = this.st.apiVersion || "";
        this.userPrompt = this.st.userPrompt || "";
      } catch (e) { this.err = (e && e.message) || "설정을 불러오지 못했습니다"; }
      // 실행 환경 — prod 면 '테스트(가짜)' provider 를 목록에서 뺀다(providers() 참고).
      // 실패하면 빈 문자열로 남고, 그때는 아무것도 가리지 않는다(모르면 막지 않는다).
      api.health().then((h) => { this.env = (h && h.env) || ""; }).catch(() => {});
      agentApi.indexStats().then((r) => { this.index = r; }).catch(() => {});
      this.loadModels();
    },

    /** ▾ 버튼 = **전체 목록 열기**. 값이 들어 있어도 걸러내지 않는다(사용자 지적).
     *  예전엔 comboOpts 가 늘 입력값으로 걸러서, 목록에 없는 이름을 직접 쳐 넣었거나
     *  우리가 걸러 낸 모델을 쓰고 있으면 후보가 0개가 되고 **드롭다운이 아예 안 떴다** —
     *  버튼이 고장 난 것처럼 보인다. 거르기는 '타이핑 중'의 기능이지 '열기'의 기능이 아니다. */
    toggleCombo(kind) {
      const open = this.comboOpen !== kind;
      this.comboOpen = open ? kind : "";
      this.comboAll = open;                 // 버튼으로 열면 전체
      if (open && !this.models.chat.length && !this.modelsBusy) this.loadModels();
    },
    comboVal(kind) {
      return kind === "chat" ? this.chatModel
           : kind === "simple" ? this.chatModelSimple : this.embedModel;
    },
    comboOpts(kind) {
      // simple 도 채팅 모델 목록에서 고른다 — 같은 provider 의 같은 종류다.
      let list = kind === "embed" ? this.models.embed : this.models.chat;
      // 권한 확인을 했으면 **못 쓰는 것은 후보에서 뺀다** — 고르고 나서 403 을 보는 것보다
      // 애초에 안 보이는 편이 낫다. 확인 전에는 서버가 준 그대로 둔다(짐작으로 안 지운다).
      if (kind !== "embed" && this.verify && (this.verify.ok || []).length) {
        list = list.filter((m) => this.verify.ok.includes(m));
      }
      const cur = this.comboVal(kind).trim().toLowerCase();
      if (this.comboAll || !cur || list.some((m) => m.toLowerCase() === cur)) return list;
      return list.filter((m) => m.toLowerCase().includes(cur));
    },
    pickModel(kind, m) {
      if (kind === "chat") this.chatModel = m;
      else if (kind === "simple") this.chatModelSimple = m;
      else this.embedModel = m;
      this.comboOpen = "";
    },

    async loadModels() {
      if (this.modelsBusy) return;
      this.modelsBusy = true;
      this.verify = null;                  // 목록이 바뀌면 예전 권한 결과는 무효다
      try { this.models = await agentApi.models(); }
      catch (e) { this.models = { chat: [], embed: [], error: (e && e.message) || "조회 실패" }; }
      finally { this.modelsBusy = false; }
    },

    /** 후보 모델을 **하나씩 실제로 불러 본다** — 게이트웨이가 권한을 안 알려 줄 때.
     *  자동으로 안 돈다: 모델 수만큼 호출이 나가고 그건 돈과 시간이다. */
    async verifyModels() {
      if (this.verifyBusy) return;
      this.verifyBusy = true;
      try { this.verify = await agentApi.verifyModels(this.models.chat || []); }
      catch (e) { this.verify = { ok: [], denied: {}, error: (e && e.message) || "확인 실패" }; }
      finally { this.verifyBusy = false; }
    },

    /** 이미 저장된 키는 자리표시자로만 보인다 — 다시 칠 필요가 없어야 한다. */
    ph(field, fallback) {
      const m = this.masked[field];
      return m ? m + " (비워 두면 유지)" : (fallback || "");
    },

    /** 저장된 키를 **입력칸 안에** 마스킹해서 보여 준다 — 빈 칸은 '설정 안 됨'으로 읽힌다.
     *  서버는 원문을 절대 안 내려 준다(단방향). 여기 보이는 것은 서버가 만든 마스킹 문자열. */
    keyShown(field) {
      const m = this.masked[field] || "";
      return m ? m.replace("설정됨 ", "") : "";
    },
    hasKey(field) { return !!this.masked[field]; },

    /** provider 의 인증 항목 **전부**를 한 팝업으로 연다.
     *  칸마다 따로 열면 한 벌이어야 의미가 생기는 값(엔드포인트+키)이 반쪽인 채로
     *  연결 테스트를 타고, 사용자에게는 "방금 넣은 게 틀렸다"로 보인다. */
    openAuthEdit() {
      const values = {};
      for (const f of this.cur.fields) values[f[0]] = "";
      this.authEdit = { fields: this.cur.fields, values, busy: false, result: null, err: "" };
      this.$nextTick(() => { const el = this.$refs.authin; (el && el[0] ? el[0] : el)?.focus(); });
    },
    closeAuthEdit() { this.authEdit = null; },
    /** 이 provider 의 인증이 하나라도 잡혀 있나 — 버튼 문구('변경' vs '입력')를 정한다. */
    anyKey() { return this.cur.fields.some((f) => this.hasKey(f[0])); },

    /** 입력을 **한 번에** 저장하고, 그 자리에서 연결을 확인하고 모델 목록을 갱신한다.
     *  예전엔 저장과 '지금 확인' 버튼이 따로였는데, 키를 바꾼 사람이 알고 싶은 것은 정확히
     *  "이게 되느냐"다 — 그 답을 받으러 버튼을 한 번 더 찾아 누르게 할 이유가 없다. */
    async applyAuth() {
      const a = this.authEdit;
      if (!a || a.busy) return;
      // 빈 칸은 **보내지 않는다** — 빈 문자열을 보내면 저장된 값을 지우게 된다.
      // (그래서 "바꿀 것만 치고 나머지는 비워 두기"가 그대로 성립한다.)
      const secrets = {};
      for (const f of a.fields) {
        const v = (a.values[f[0]] || "").trim();
        if (v) secrets[f[0]] = v;
      }
      if (!Object.keys(secrets).length) { a.err = "바꿀 값을 하나 이상 입력하세요"; return; }
      a.busy = true; a.err = ""; a.result = null;
      try {
        const body = { provider: this.provider, secrets };
        if (this.provider === "aoai" && this.apiVersion) body.apiVersion = this.apiVersion;
        this.st = await agentApi.saveSettings(body);
        this.$emit("saved", this.st);
        a.result = await agentApi.probe();          // ① 되는지
        await this.loadModels();                    // ② 무엇을 쓸 수 있는지
        this.probe = a.result;
        if (this.probeOk(a.result)) {
          // 성공이면 닫는다 — 확인이 목적이었고, 결과는 아래 '연결 상태'에 남는다.
          this.authEdit = null;
        }
      } catch (e) { a.err = (e && e.message) || "저장에 실패했습니다"; }
      finally { if (this.authEdit) this.authEdit.busy = false; }
    },

    probeOk(p) {
      return !!(p && p.chat && p.chat.ok);   // 임베딩은 없어도 대화는 된다(색인만 못 만든다)
    },

    async save() {
      if (this.saving) return;
      this.saving = true; this.err = ""; this.probe = null;
      try {
        const body = { provider: this.provider, chatModel: this.chatModel,
                       chatModelSimple: this.chatModelSimple,
                       embedModel: this.embedModel, userPrompt: this.userPrompt };
        if (this.provider === "aoai") body.apiVersion = this.apiVersion;
        // 빈 칸은 보내지 않는다 — 빈 문자열을 보내면 저장된 키를 지우게 된다.
        const s = {};
        for (const [k, v] of Object.entries(this.secrets)) if ((v || "").trim()) s[k] = v.trim();
        if (Object.keys(s).length) body.secrets = s;
        this.st = await agentApi.saveSettings(body);
        // 저장 즉시 부모에게 알린다 — 좌상단 모델 표시가 옛 값으로 남아 있으면
        // 무엇으로 도는지 화면이 거짓말을 한다(사용자 지적).
        this.$emit("saved", this.st);
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

        <!-- 처음 여는 사람에게 **순서**를 준다. 탭·입력칸·모델 콤보가 한꺼번에 보이면
             어디가 시작인지 알 수 없다. 키가 하나라도 잡히면 이 안내는 사라진다. -->
        <div v-if="needsSetup" class="ag-start">
          <b>처음이신가요? 세 단계면 됩니다.</b>
          <ol>
            <li>아래에서 <b>연결 방식</b>을 고릅니다 <em>— 사내는 Azure OpenAI, 개인 키는 OpenAI</em></li>
            <li><b>API 키</b> 옆 <b>입력</b>을 눌러 키를 넣습니다 <em>— 넣는 즉시 연결을 확인하고
              쓸 수 있는 모델 목록을 불러옵니다</em></li>
            <li>불러온 목록에서 <b>모델</b>을 고르고 <b>저장</b>합니다
              <em>— 키 없이 둘러보려면 '테스트(가짜)'를 고르세요</em></li>
          </ol>
        </div>

        <!-- provider -->
        <div class="ag-sec">
          <div class="ag-lab">연결 방식</div>
          <div class="ag-tabs">
            <button v-for="p in providers" :key="p.k" :class="{ on: provider === p.k }"
                    @click="provider = p.k">{{ p.label }}</button>
          </div>
          <div class="ag-hint">{{ cur.hint }}</div>
        </div>

        <!-- 키 — 설정돼 있으면 **마스킹된 값이 칸 안에** 보인다.
             빈 칸은 '설정 안 됨'으로 읽히므로, 있는 것을 있다고 보여 주는 것이 먼저다. -->
        <div v-if="cur.fields.length" class="ag-sec">
          <div class="ag-lab">인증</div>
          <!-- 읽기 전용 현황 — 무엇이 잡혀 있는지 한눈에. 고치는 것은 아래 버튼 하나로
               연다(칸마다 팝업이 따로면 반쪽 입력으로 연결 테스트가 돈다 — 사용자 지적). -->
          <div v-for="f in cur.fields" :key="f[0]" class="ag-f">
            <span>{{ f[1] }}</span>
            <input class="ag-keyin" :class="{ set: hasKey(f[0]) }" readonly
                   :value="hasKey(f[0]) ? keyShown(f[0]) : ''"
                   :placeholder="hasKey(f[0]) ? '' : (f[2] || '아직 설정되지 않았습니다')">
          </div>
          <!-- ★ 환경변수가 이기고 있으면 **그 사실을 말한다**(사용자 지적: "저장/반영되는 거
               맞니? 수상해"). 저장은 됐는데 가려진 상태를 안 알려 주면, 화면에 보이는 값과
               실제로 쓰이는 값이 다른 채로 사용자가 원인을 찾아 헤맨다. -->
          <div v-for="f in cur.fields" :key="'env-' + f[0]">
            <div v-if="st.envOverrides && st.envOverrides[f[0]]" class="ag-warn">
              <b>{{ f[1] }}</b> 는 환경변수 <code>{{ st.envOverrides[f[0]] }}</code> 가 쓰이고 있습니다 —
              여기 저장한 값은 <b>가려집니다</b>. 저장값을 쓰려면 그 환경변수를 지우고 앱을 다시 시작하세요.
            </div>
          </div>
          <div class="ag-keyrow one">
            <button class="ag-mini" @click="openAuthEdit()">
              {{ anyKey() ? '인증 정보 변경' : '인증 정보 입력' }}</button>
          </div>
          <div class="ag-hint">키는 이 PC 에만 저장되고 <b>원문은 화면으로 다시 내려오지 않습니다</b>.
            {{ cur.fields.length > 1 ? '항목을 모두 넣은 뒤' : '입력하면' }} 그 자리에서 연결을
            확인하고 모델 목록을 갱신합니다.</div>
        </div>

        <!-- 모델 — 콤보박스(datalist): 목록에서 고르거나 직접 친다.
             목록 조회가 막힌 환경(권한 없는 키·자체 LLM)에서도 입력은 계속 돼야 한다. -->
        <div v-if="cur.models.length" class="ag-sec">
          <div class="ag-lab">모델
            <button class="ag-mini" :disabled="modelsBusy" @click="loadModels"
                    title="현재 저장된 provider 기준으로 사용 가능한 목록을 다시 조회">
              {{ modelsBusy ? '조회 중…' : '목록 새로고침 ↻' }}</button>
            <!-- ★ 권한 확인은 **버튼을 눌러야** 돈다(사용자 요청: 권한 없는 모델 거르기).
                 게이트웨이는 자기가 아는 모델을 다 늘어놓고, 그중 내 키로 못 부르는 것이
                 섞여 있다 — 골라 놓고 나서야 403 을 본다. 다만 확인은 후보 수만큼 **실제
                 호출**이라 비용이 든다. 목록을 보는 일이 조용히 과금되면 안 된다. -->
            <button v-if="models.chat.length" class="ag-mini" :disabled="verifyBusy"
                    @click="verifyModels"
                    :title="'후보 ' + models.chat.length + '개를 하나씩 실제로 불러 봅니다 (호출 발생)'">
              {{ verifyBusy ? '확인 중…' : '권한 확인' }}</button>
            <!-- ★ **서버가 준 개수까지** 보인다(사용자 지적: "직접 /v1/models 날려본 것과
                 목록이 다르다"). 거르는 것 자체는 필요하지만 — 음성·이미지 모델을 다 보이면
                 목록이 소음이 된다 — **몇 개를 걸렀는지는 사용자가 알아야 할 사실**이다.
                 걸러진 것도 직접 입력하면 그대로 쓸 수 있다(목록은 참고이지 제약이 아니다). -->
            <em v-if="models.total" class="ag-mini-hint">서버 {{ models.total }}개 ·
              채팅 {{ models.chat.length }} · 임베딩 {{ models.embed.length }}
              <template v-if="models.total > models.chat.length + models.embed.length">(나머지는
                음성·이미지 등으로 걸러 냄 — 필요하면 직접 입력)</template></em>
          </div>
          <div class="ag-f"><span>{{ cur.models[0] }}</span>
            <div class="ag-combo">
              <input v-model="chatModel" spellcheck="false" autocomplete="off"
                     @focus="comboOpen = 'chat'; comboAll = false"
                     @input="comboOpen = 'chat'; comboAll = false">
              <button class="ag-combo-btn" @click="toggleCombo('chat')" title="목록 열기">▾</button>
              <!-- 열려 있으면 **비어 있어도 뜬다** — 버튼을 눌렀는데 아무 일도 안 일어나면
                   고장으로 읽힌다. 왜 비었는지(조회 실패·미조회)를 그 자리에서 말해 준다. -->
              <div v-if="comboOpen === 'chat'" class="ag-combo-drop">
                <button v-for="m in comboOpts('chat')" :key="m"
                        :class="{ on: m === chatModel }"
                        @mousedown.prevent="pickModel('chat', m)">{{ m }}</button>
                <div v-if="!comboOpts('chat').length" class="ag-combo-empty">
                  {{ modelsBusy ? '조회 중…' : (models.error ? '목록을 못 불러왔습니다 — 직접 입력하세요'
                     : '목록이 비어 있습니다 — 직접 입력하세요') }}
                </div>
              </div>
            </div>
          </div>
          <div class="ag-f"><span>{{ cur.models[1] }}</span>
            <div class="ag-combo">
              <input v-model="embedModel" spellcheck="false" autocomplete="off"
                     @focus="comboOpen = 'embed'; comboAll = false"
                     @input="comboOpen = 'embed'; comboAll = false">
              <button class="ag-combo-btn" @click="toggleCombo('embed')" title="목록 열기">▾</button>
              <!-- 열려 있으면 **비어 있어도 뜬다** — 버튼을 눌렀는데 아무 일도 안 일어나면
                   고장으로 읽힌다. 왜 비었는지(조회 실패·미조회)를 그 자리에서 말해 준다. -->
              <div v-if="comboOpen === 'embed'" class="ag-combo-drop">
                <button v-for="m in comboOpts('embed')" :key="m"
                        :class="{ on: m === embedModel }"
                        @mousedown.prevent="pickModel('embed', m)">{{ m }}</button>
                <div v-if="!comboOpts('embed').length" class="ag-combo-empty">
                  {{ modelsBusy ? '조회 중…' : (models.error ? '목록을 못 불러왔습니다 — 직접 입력하세요'
                     : '목록이 비어 있습니다 — 직접 입력하세요') }}
                </div>
              </div>
            </div>
          </div>
          <!-- 역할별 모델 분리(선택) — 간단한 역할(의도 분류·티켓 실행)은 저렴한 모델로.
               비우면 위의 기본 모델 하나로 전부 돈다. -->
          <div class="ag-f"><span>간단한 역할 모델 (선택)</span>
            <div class="ag-combo">
              <input v-model="chatModelSimple" spellcheck="false" autocomplete="off"
                     placeholder="비우면 기본 모델 사용"
                     @focus="comboOpen = 'simple'; comboAll = false"
                     @input="comboOpen = 'simple'; comboAll = false">
              <button class="ag-combo-btn" @click="toggleCombo('simple')" title="목록 열기">▾</button>
              <!-- 열려 있으면 **비어 있어도 뜬다** — 버튼을 눌렀는데 아무 일도 안 일어나면
                   고장으로 읽힌다. 왜 비었는지(조회 실패·미조회)를 그 자리에서 말해 준다. -->
              <div v-if="comboOpen === 'simple'" class="ag-combo-drop">
                <button v-for="m in comboOpts('simple')" :key="m"
                        :class="{ on: m === chatModelSimple }"
                        @mousedown.prevent="pickModel('simple', m)">{{ m }}</button>
                <div v-if="!comboOpts('simple').length" class="ag-combo-empty">
                  {{ modelsBusy ? '조회 중…' : (models.error ? '목록을 못 불러왔습니다 — 직접 입력하세요'
                     : '목록이 비어 있습니다 — 직접 입력하세요') }}
                </div>
              </div>
            </div>
          </div>
          <div class="ag-hint">간단한 역할 = 의도 분류(Planner)·티켓 실행(Operator).
            조사·초안·검토·답변은 기본 모델을 씁니다. 예) 기본 gpt-4o + 간단 gpt-4o-mini</div>
          <div v-if="models.error" class="ag-hint">목록 조회 실패 — 직접 입력하세요. ({{ models.error }})</div>
          <div v-if="verify" class="ag-hint">
            <template v-if="verify.error">권한 확인 실패 — {{ verify.error }}</template>
            <template v-else>권한 확인: 사용 가능 <b>{{ (verify.ok || []).length }}</b>개<template
              v-if="Object.keys(verify.denied || {}).length"> · 제외 {{ Object.keys(verify.denied).length }}개
              <span class="ag-denied">({{ Object.keys(verify.denied).slice(0, 4).join(', ') }}{{
                Object.keys(verify.denied).length > 4 ? ' …' : '' }})</span></template>
            </template>
          </div>
          <label v-if="provider === 'aoai'" class="ag-f"><span>api-version</span>
            <input v-model="apiVersion" placeholder="2024-10-21" spellcheck="false"></label>
          <div v-if="provider === 'aoai'" class="ag-hint">
            Azure 는 <b>모델명이 아니라 배포명</b>을 넣습니다. 가장 흔한 실수입니다.
          </div>
        </div>

        <!-- 프롬프트 레이어 — 사용자별(여기서 편집) + 프로젝트 공용(config 파일, 읽기 전용) -->
        <div class="ag-sec">
          <div class="ag-lab">내 프롬프트 (선택)</div>
          <textarea v-model="userPrompt" class="ag-ta" rows="4" spellcheck="false"
                    placeholder="모든 답변에 적용할 나만의 지시. 예) 답변은 두괄식으로. 마감 제안은 항상 금요일을 피해서."></textarea>
          <div class="ag-hint">이 PC에만 저장됩니다(커밋 안 됨). 날조 금지·승인 없는 쓰기 금지
            같은 절대 규칙은 이 프롬프트로도 바꿀 수 없습니다.</div>
          <div v-if="st.projectPrompt" class="ag-hint">
            <button class="ag-mini" @click="showProjPrompt = !showProjPrompt">
              {{ showProjPrompt ? '▾' : '▸' }} 프로젝트 공용 프롬프트 (config/agent-prompt.md, 읽기 전용)</button>
            <pre v-if="showProjPrompt" class="ag-proj-prompt">{{ st.projectPrompt }}</pre>
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

        <!-- 연결 상태 — **버튼이 없다.** 확인은 키를 넣을 때와 저장할 때 자동으로 한다.
             사용자가 알고 싶은 건 "지금 되느냐"이지 '확인'을 누르는 일이 아니다. -->
        <div class="ag-sec">
          <div class="ag-lab">연결 상태</div>
          <div v-if="!probe" class="ag-hint">아직 확인하지 않았습니다 — 키를 입력하거나 저장하면
            바로 확인합니다.</div>
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

      <!-- 인증 변경 팝업 — 이 provider 가 요구하는 것을 **한 번에** 받고, 저장한 뒤
           그 자리에서 연결까지 확인한다. 확인이 목적이므로 성공하면 스스로 닫힌다. -->
      <div v-if="authEdit" class="ag-back inner" @click.self="closeAuthEdit">
        <div class="ag-dlg small">
          <div class="ag-h">
            <h3>{{ cur.label }} 인증 {{ anyKey() ? '변경' : '입력' }}</h3>
            <button class="ag-x" @click="closeAuthEdit" aria-label="닫기">✕</button>
          </div>
          <div class="ag-body">
            <div v-for="(f, fi) in authEdit.fields" :key="f[0]" class="ag-f">
              <span>{{ f[1] }}</span>
              <input :type="f[3] ? 'password' : 'text'" v-model="authEdit.values[f[0]]"
                     :placeholder="hasKey(f[0]) ? keyShown(f[0]) + ' (비워 두면 유지)' : (f[2] || '')"
                     autocomplete="off" spellcheck="false" ref="authin"
                     @keydown.enter.prevent="applyAuth">
            </div>
            <div class="ag-hint">비워 둔 칸은 <b>지금 값을 그대로 둡니다</b> — 바꿀 것만 치세요.
              저장하면 곧바로 연결을 확인하고 모델 목록을 다시 불러옵니다.</div>
            <div v-if="authEdit.result" class="ag-probe">
              <div class="ag-row" :class="authEdit.result.chat && authEdit.result.chat.ok ? 'ok' : 'no'">
                <b>채팅</b>
                <template v-if="authEdit.result.chat && authEdit.result.chat.ok">
                  <span>정상 · {{ authEdit.result.chat.ms }}ms</span>
                </template>
                <template v-else>
                  <span>실패</span><em>{{ authEdit.result.chat && authEdit.result.chat.error }}</em>
                </template>
              </div>
              <div class="ag-row" :class="authEdit.result.embeddings && authEdit.result.embeddings.ok ? 'ok' : 'no'">
                <b>임베딩</b>
                <template v-if="authEdit.result.embeddings && authEdit.result.embeddings.ok">
                  <span>정상 · {{ authEdit.result.embeddings.dim }}차원</span>
                </template>
                <template v-else>
                  <span>실패</span><em>{{ authEdit.result.embeddings && authEdit.result.embeddings.error }}</em>
                </template>
              </div>
            </div>
            <div v-if="authEdit.err" class="ag-err">{{ authEdit.err }}</div>
          </div>
          <div class="ag-act">
            <button class="ag-ok" :disabled="authEdit.busy" @click="applyAuth">
              {{ authEdit.busy ? '확인 중…' : '저장하고 연결 확인' }}</button>
            <button class="ag-cancel" @click="closeAuthEdit">취소</button>
          </div>
        </div>
      </div>
    </div>
  </div>`,
};
