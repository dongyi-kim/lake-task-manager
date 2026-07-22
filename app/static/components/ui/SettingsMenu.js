// SettingsMenu.js — 헤더 우상단 고정 설정 버튼(기존 테마 버튼 자리). 클릭 시 아래로 드롭다운:
//   · SSO 상태(Jira/Confluence/Bitbucket) + 미인증 시 '인증하기'
//   · 테마(다크/라이트) 토글
//   · Dev Tools — 제공 중인 개발용 API 링크
//   · rev(빌드 커밋)
// 백엔드: /api/health(rev·needLogin) · /api/dev/sso · /api/dev/tools · /api/login
import { api } from "../../lib/api.js";

export default {
  name: "SettingsMenu",
  props: { theme: { type: String, default: "light" } },
  emits: ["toggle-theme"],
  data() {
    return { open: false, rev: "", sso: null, tools: null, loading: false, loggingIn: false };
  },
  mounted() {
    this._onDoc = (e) => {
      if (this.open && this.$el && !this.$el.contains(e.target)) this.open = false;
    };
    document.addEventListener("click", this._onDoc, true);
    document.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape" && this.open) this.open = false;
    });
  },
  unmounted() {
    document.removeEventListener("click", this._onDoc, true);
    document.removeEventListener("keydown", this._onEsc);
  },
  methods: {
    toggle() { this.open = !this.open; if (this.open) this.refresh(); },
    async refresh() {
      this.loading = true;
      try {
        const [h, sso, tools] = await Promise.all([
          api.health().catch(() => null),
          api.raw("/api/dev/sso").catch(() => null),
          api.raw("/api/dev/tools").catch(() => null),
        ]);
        this.rev = (h && h.rev) || "";
        this.sso = sso;
        this.tools = tools;
      } finally { this.loading = false; }
    },
    async authenticate() {
      // 순회 SSO 로그인 창(모든 서비스). 완료 후 상태 갱신.
      this.loggingIn = true;
      try { await api.login(); await this.refresh(); }
      catch (e) { /* 취소 등 — 상태 유지 */ }
      finally { this.loggingIn = false; }
    },
  },
  template: `
  <div class="setmenu" :class="{ open }">
    <button class="theme-btn setmenu-trig" @click.stop="toggle"
            :title="open ? '설정 닫기' : '설정'" :aria-expanded="open">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      <span>설정</span>
    </button>

    <div v-if="open" class="setmenu-panel" @click.stop>
      <!-- SSO 상태 -->
      <div class="sm-sec">
        <div class="sm-h">SSO 인증</div>
        <div v-if="!sso || !sso.targets" class="sm-note">{{ loading ? '확인 중…' : '상태를 불러오지 못했습니다.' }}</div>
        <template v-else>
          <div v-for="t in sso.targets" :key="t.service" class="sm-row" :title="t.detail">
            <span class="sm-dot" :class="t.authenticated ? 'ok' : 'no'"></span>
            <span class="sm-svc">{{ t.service }}</span>
            <span class="sm-state" :class="t.authenticated ? 'ok' : 'no'">
              {{ t.authenticated ? '인증됨' : '미인증' }}</span>
          </div>
          <button v-if="sso.targets.some(t => !t.authenticated)" class="sm-btn primary"
                  :disabled="loggingIn" @click="authenticate">
            {{ loggingIn ? '인증 창 진행 중…' : '인증하기 (SSO 로그인)' }}
          </button>
        </template>
      </div>

      <!-- 테마 -->
      <div class="sm-sec">
        <div class="sm-h">화면</div>
        <button class="sm-btn" @click="$emit('toggle-theme')">
          <span v-if="theme === 'dark'">☀ 라이트 모드로</span><span v-else>🌙 다크 모드로</span>
        </button>
      </div>

      <!-- Dev Tools -->
      <div class="sm-sec">
        <div class="sm-h">Dev Tools</div>
        <div v-if="!tools || !tools.endpoints" class="sm-note">개발용 API 없음</div>
        <a v-for="e in (tools && tools.endpoints) || []" :key="e.path" class="sm-devlink"
           :href="e.path" target="_blank" rel="noopener" :title="e.method + ' ' + e.path">
          <span class="sm-devlabel">{{ e.label }}</span>
          <span class="sm-devpath">{{ e.path }}</span>
        </a>
      </div>

      <!-- 버전 -->
      <div class="sm-foot">
        <span class="sm-rev" title="빌드 커밋">rev {{ rev || '…' }}</span>
        <button class="sm-refresh" @click="refresh" :disabled="loading" title="상태 새로고침">↻</button>
      </div>
    </div>
  </div>`,
};
