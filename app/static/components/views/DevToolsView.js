// DevToolsView.js — 개발용 API 모음 페이지(#/devtools). 설정 메뉴의 'Dev Tools' 가 여기로 온다.
// 제공 중인 /api/dev/* 를 카드로 나열: 새 탭 열기 + 인라인 실행(결과 미리보기).
import { api } from "../../lib/api.js";

export default {
  name: "DevToolsView",
  data() { return { rev: "", tools: null, results: {}, running: {}, params: {} }; },
  async mounted() {
    const [h, t] = await Promise.all([
      api.health().catch(() => null),
      api.raw("/api/dev/tools").catch(() => null),
    ]);
    this.rev = (h && h.rev) || "";
    this.tools = t;
  },
  methods: {
    // 카드 식별자 — **경로만으로는 안 된다.** 같은 경로에 GET/POST 가 나란히 있는 게 있어
    // (PAT: GET=확인만 / POST=실제 발급), 경로로 키를 잡으면 입력칸·결과·실행중 표시가
    // 두 카드에서 겹치고 v-for :key 도 중복된다.
    idOf(ep) { return ep.method + " " + ep.path; },
    // 실제 호출 경로 — param 이 있으면 입력값을 {param} 자리에 넣는다(없으면 원본).
    pathOf(ep) {
      if (!ep.param) return ep.path;
      const v = (this.params[this.idOf(ep)] || "").trim();
      return v ? ep.path.replace("{" + ep.param + "}", encodeURIComponent(v)) : ep.path;
    },
    async run(ep) {
      const id = this.idOf(ep);
      if (ep.param && !(this.params[id] || "").trim()) {
        this.results[id] = { error: ep.param + " 를 입력하세요" };
        return;
      }
      this.running[id] = true;
      try {
        const path = this.pathOf(ep);
        this.results[id] = ep.method === "POST"
          ? await api.raw(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify(ep.body || {}) })
          : await api.raw(path);
      } catch (e) {
        this.results[id] = { error: String(e) };
      } finally {
        this.running[id] = false;
      }
    },
    pretty(v) { try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); } },
  },
  template: `
  <div class="devtools-page">
    <div class="dt-head">
      <h1>Dev Tools</h1>
      <span class="dt-rev" title="빌드 커밋">rev {{ rev || '…' }}</span>
    </div>
    <p class="dt-intro">사내 API 실제 응답을 확인·진단하는 개발용 엔드포인트입니다. 값은 마스킹됩니다.
      <br>노출 제어는 나중에 유저 역할이 생기면 걸립니다(지금은 전부 열림).</p>

    <div v-if="!tools || !tools.endpoints" class="dt-empty">개발용 API 목록을 불러오지 못했습니다.</div>

    <div v-for="ep in (tools && tools.endpoints) || []" :key="idOf(ep)" class="dt-card"
         :class="{ danger: ep.danger }">
      <div class="dt-card-h">
        <div class="dt-card-t">
          <span class="dt-label">{{ ep.label }}</span>
          <span class="dt-path"><span class="dt-method">{{ ep.method }}</span> {{ ep.path }}</span>
          <span v-if="ep.note" class="dt-note-i">{{ ep.note }}</span>
        </div>
        <div class="dt-actions">
          <!-- param 이 필요한 엔드포인트(예: 티켓 키)는 입력칸을 먼저 -->
          <input v-if="ep.param" class="dt-param" :placeholder="ep.placeholder || ep.param"
                 v-model="params[idOf(ep)]" @keyup.enter="run(ep)" spellcheck="false">
          <button class="dt-run" :disabled="running[idOf(ep)]" @click="run(ep)">
            {{ running[idOf(ep)] ? '실행 중…' : '실행' }}</button>
          <!-- GET 만 새 탭으로 열 수 있다(POST 는 실행 버튼으로). 입력한 param 을 넣은 실제 경로로. -->
          <a v-if="ep.method === 'GET'" class="dt-open" :href="pathOf(ep)" target="_blank" rel="noopener">새 탭 ↗</a>
        </div>
      </div>
      <pre v-if="results[idOf(ep)]" class="dt-result">{{ pretty(results[idOf(ep)]) }}</pre>
    </div>

    <div v-if="tools && tools.bitbucket_base" class="dt-note">
      Bitbucket base: <code>{{ tools.bitbucket_base }}</code>
    </div>
  </div>`,
};
