// LoginOverlay.js — 사내 SSO 세션 없음/만료 시 오버레이(auth.js 대체). api.js 의 need-login 이벤트 수신
// + 마운트 시 /api/health 확인. "SSO 로그인" → /api/login(설치 Chrome 로그인 폴링) → 성공 시 새로고침.
// mock/local 은 needLogin=false 라 표시 안 됨. updated: 2026-07-09
import { api } from "../../lib/api.js";
export default {
  name: "LoginOverlay",
  data() { return { show: false, busy: false, msg: "", tried: false }; },
  watch: {
    // 미인증이 확인되면 **곧바로** 로그인을 시작한다. 버튼을 기다릴 이유가 없다 —
    // 이 상태에서 사용자가 할 수 있는 일이 그것 하나뿐이고, 세션이 없으면 화면의 모든 조회가
    // 어차피 실패한다. 버튼은 실패했을 때의 재시도용으로 남는다.
    // ★ 페이지당 한 번만 자동 시도한다. 로그인이 실패하는 상황에서 무한히 다시 걸면
    //   앱 창이 Jira 로 계속 튕겨 사용자가 아무것도 못 한다.
    show(v) { if (v && !this.tried) { this.tried = true; this.doLogin(); } },
  },
  mounted() {
    window.addEventListener("need-login", () => { this.show = true; });
    api.health().then((h) => { if (h && h.needLogin) this.show = true; }).catch(() => {});
  },
  methods: {
    async doLogin() {
      this.busy = true;
      this.msg = "인증이 필요해 자동으로 SSO 로그인을 시작합니다. 잠시 후 이 창이 사내 로그인 페이지로 이동하며, 로그인을 끝까지 완료하면 자동으로 앱으로 돌아옵니다…";
      try {
        const r = await api.login();
        if (r && r.pending) return;                    // 앱 창 모드: 이 창이 Jira 로 이동됨(대기)
        if (r && r.ok) { location.reload(); return; }  // 폴백(별도 창): 성공 시 새로고침
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
        <div class="login-msg">{{ msg || '세션이 없거나 만료되었습니다. 인증을 시작합니다…' }}</div>
        <button class="login-btn" :disabled="busy" @click="doLogin">{{ busy ? '로그인 진행 중…' : '다시 시도' }}</button>
      </div>
    </div>`,
};
