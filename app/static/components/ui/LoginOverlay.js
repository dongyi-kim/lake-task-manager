// Nonblocking Jira connection notice. Local settings/drafts remain usable without a session;
// health and partial-result failures silently renew first, and only an explicit click opens SSO.
import { api } from "../../lib/api.js";
export default {
  name: "LoginOverlay",
  data() { return { show: false, busy: false, msg: "",
                    manualRetryRequired: false,
                    prog: 0, phase: "auto" }; },   // prog 0~100(자동로그인 타임아웃 진행바), phase: auto|manual
  mounted() {
    this._onNeedLogin = event => {
      if (event.detail?.silentFailed) { this.show = true; return; }
      this.kick(); // legacy partial-result notices join the same silent coordinator
    };
    window.addEventListener("need-login", this._onNeedLogin);
    // 서버가 로그인 성공을 알리면(창 없이 갱신 포함) 안내를 바로 걷고 다음 시도를 풀어 준다.
    this._onAuthOk = () => {
      this.show = false; this.msg = ""; this.busy = false;
      this._loginSequence = (this._loginSequence || 0) + 1;
      this._attemptId = null; this.manualRetryRequired = false;
      this._stopProg();                              // 진행바 정지·리셋
    };
    window.addEventListener("auth-ok", this._onAuthOk);
    this._onLoginState = event => {
      const state = event.detail;
      if (!this.busy || !this._attemptId || state?.attemptId !== this._attemptId) return;
      if (state.state === "completed" && !state.succeeded) this._loginFailed();
    };
    window.addEventListener("auth-login-state", this._onLoginState);
    api.health().then((h) => {
      if (h?.needLogin) this.kick();
    }).catch(() => {});
  },
  methods: {
    async kick() {
      if (this._silentChecking || this.manualRetryRequired) return;
      this._silentChecking = true;
      try { await api.recoverAuth(); }
      catch (_) { /* offline does not require an interactive login */ }
      finally { this._silentChecking = false; }
    },
    async doLogin() {
      if (this.busy) return;
      this.show = true;
      this.manualRetryRequired = false; this._attemptId = null;
      const sequence = this._loginSequence = (this._loginSequence || 0) + 1;
      // 앱 창 모드의 로그인은 **이 창을 Jira 로 보냈다가** 앱으로 되돌린다. 그때 주소가 초기값이라
      // 보던 화면(내 Task·티켓)이 홈으로 리셋됐다 — 어디였는지 적어 두고 돌아와서 되돌린다.
      try { sessionStorage.setItem("lake.route", location.hash || ""); } catch (e) { /* noop */ }
      this.busy = true;
      this._startProg();          // 자동 로그인 타임아웃 진행바 시작(끝나면 '수동 로그인' 안내)
      this.msg = "SSO 로그인을 진행하고 있습니다.";
      try {
        const r = await api.login();
        if (sequence !== this._loginSequence) return;
        this._attemptId = r?.attemptId || null;
        if (r && r.pending) return;                    // status watcher follows this exact attempt
        if (r?.state === "completed") {
          if (!r.succeeded) this._loginFailed();
          return;                                     // successful attempt still needs auth confirmation
        }
        if (r && r.ok) {
          // ★ **새로고침하지 않는다.** 보던 티켓 창·쓰던 글·스크롤이 전부 날아간다. 인증은
          //   서버 쪽에서 이미 끝났으므로, 안내만 걷고 하던 일을 계속하게 둔다
          //   (다음 조회부터 정상 응답이 오고, 상단 알림도 스스로 사라진다).
          this.show = false; this.msg = "";
          window.dispatchEvent(new CustomEvent("auth-ok"));
          return;
        }
        this._loginFailed();
      } catch (e) {
        if (sequence !== this._loginSequence) return;
        this._loginFailed("로그인 실패: " + e.message);
      }
    },
    _loginFailed(message = "로그인이 완료되지 않았습니다(시간 초과/취소). 다시 시도하세요.") {
      this.msg = message;
      this.manualRetryRequired = true;
      this.busy = false;
      this._stopProg();
    },
    /** 자동 로그인 대기시간을 진행바로 보여 준다 — 백엔드가 '창 없이(cert 자동)' 인증을 시도하는
     *  동안(대략 ~15초)을 채우고, 다 차면 '수동 로그인 창이 뜬다'고 안내한다(phase=manual).
     *  성공(auth-ok)하면 _stopProg 로 즉시 걷힌다. */
    _startProg() {
      this._stopProg();
      this.phase = "auto"; this.prog = 0;
      const total = 15000, t0 = Date.now();
      this._ptimer = setInterval(() => {
        const el = Date.now() - t0;
        this.prog = Math.min(100, Math.round((el / total) * 100));
        if (el >= total && this.phase !== "manual") this.phase = "manual";
      }, 200);
    },
    _stopProg() {
      if (this._ptimer) { clearInterval(this._ptimer); this._ptimer = null; }
      this.prog = 0; this.phase = "auto";
    },
  },
  unmounted() {
    this._stopProg();
    this._loginSequence = (this._loginSequence || 0) + 1;
    window.removeEventListener("need-login", this._onNeedLogin);
    window.removeEventListener("auth-ok", this._onAuthOk);
    window.removeEventListener("auth-login-state", this._onLoginState);
  },
  template: `
    <div v-if="show" class="stbanner authenticating login-notice" role="status">
        <span class="stb-dot"></span><b>Jira 연결</b>
        <span class="stb-hint">{{ msg || '무음 연결을 복구하지 못했습니다. Jira 작업을 계속하려면 로그인하세요. 로컬 설정과 초안은 계속 사용할 수 있습니다.' }}</span>
        <template v-if="busy">
          <div class="login-prog" :class="{ manual: phase === 'manual' }"><i :style="{ width: prog + '%' }"></i></div>
          <div class="login-sub">{{ phase === 'manual'
              ? '자동 로그인이 되지 않아 수동 로그인 창을 띄웁니다 — 사내 로그인을 끝까지 완료해 주세요.'
              : '자동 로그인을 시도하는 중입니다… 실패하면 수동 로그인 창이 뜹니다.' }}</div>
        </template>
        <button class="login-btn" :disabled="busy" @click="doLogin">{{ busy ? '로그인 진행 중…' : '다시 시도' }}</button>
        <button class="stb-x" @click="show = false" title="이 알림 닫기">×</button>
    </div>`,
};
