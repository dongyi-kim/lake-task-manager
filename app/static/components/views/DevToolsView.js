// DevToolsView.js — 개발용 API 모음 페이지(#/devtools). 설정 메뉴의 'Dev Tools' 가 여기로 온다.
// 제공 중인 /api/dev/* 를 카드로 나열: 새 탭 열기 + 인라인 실행(결과 미리보기).
import { api } from "../../lib/api.js";

export default {
  name: "DevToolsView",
  data() { return { rev: "", tools: null, results: {}, running: {} }; },
  async mounted() {
    const [h, t] = await Promise.all([
      api.health().catch(() => null),
      api.raw("/api/dev/tools").catch(() => null),
    ]);
    this.rev = (h && h.rev) || "";
    this.tools = t;
  },
  methods: {
    async run(ep) {
      this.running[ep.path] = true;
      try {
        this.results[ep.path] = await api.raw(ep.path);
      } catch (e) {
        this.results[ep.path] = { error: String(e) };
      } finally {
        this.running[ep.path] = false;
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

    <div v-for="ep in (tools && tools.endpoints) || []" :key="ep.path" class="dt-card">
      <div class="dt-card-h">
        <div class="dt-card-t">
          <span class="dt-label">{{ ep.label }}</span>
          <span class="dt-path"><span class="dt-method">{{ ep.method }}</span> {{ ep.path }}</span>
        </div>
        <div class="dt-actions">
          <button class="dt-run" :disabled="running[ep.path]" @click="run(ep)">
            {{ running[ep.path] ? '실행 중…' : '실행' }}</button>
          <a class="dt-open" :href="ep.path" target="_blank" rel="noopener">새 탭 ↗</a>
        </div>
      </div>
      <pre v-if="results[ep.path]" class="dt-result">{{ pretty(results[ep.path]) }}</pre>
    </div>

    <div v-if="tools && tools.bitbucket_base" class="dt-note">
      Bitbucket base: <code>{{ tools.bitbucket_base }}</code>
    </div>
  </div>`,
};
