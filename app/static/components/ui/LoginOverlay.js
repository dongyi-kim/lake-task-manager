// LoginOverlay.js — 사내 SSO 세션 없음/만료 시 오버레이(auth.js 대체). api.js 의 need-login 이벤트 수신
// + 마운트 시 /api/health 확인. "SSO 로그인" → /api/login(설치 Chrome 로그인 폴링) → 성공 시 새로고침.
// mock/local 은 needLogin=false 라 표시 안 됨. updated: 2026-07-09
import { api } from "../../lib/api.js";
export default {
  name: "LoginOverlay",
  data() { return { show: false, busy: false, msg: "" }; },
  mounted() {
    window.addEventListener("need-login", () => { this.show = true; });
    api.health().then((h) => { if (h && h.needLogin) this.show = true; }).catch(() => {});
  },
  methods: {
    async doLogin() {
      this.busy = true;
      this.msg = "브라우저 창에서 사내 SSO/인증서 로그인을 끝까지 완료하세요. 완료를 감지하면 자동으로 새로고침합니다…";
      try {
        const r = await api.login();
        if (r && r.ok) { location.reload(); return; }
        this.msg = "로그인이 완료되지 않았습니다(시간 초과/취소). 다시 시도하세요.";
      } catch (e) { this.msg = "로그인 실패: " + e.message; }
      this.busy = false;
    },
  },
  template: `
    <div v-if="show" class="login-ov">
      <div class="login-card">
        <div class="login-ic">🔒</div>
        <div class="login-h">사내 Jira SSO 로그인 필요</div>
        <div class="login-msg">{{ msg || '세션이 없거나 만료되었습니다. 아래 버튼을 누르면 브라우저 창이 열립니다. 사내 SSO/인증서 로그인을 완료하면 자동으로 이어집니다.' }}</div>
        <button class="login-btn" :disabled="busy" @click="doLogin">{{ busy ? '로그인 진행 중…' : 'SSO 로그인' }}</button>
      </div>
    </div>`,
};
