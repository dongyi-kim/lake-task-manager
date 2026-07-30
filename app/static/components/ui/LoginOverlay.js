// LoginOverlay.js — 사내 SSO 세션 없음/만료 시 오버레이(auth.js 대체). api.js 의 need-login 이벤트 수신
// + 마운트 시 /api/health 확인. "SSO 로그인" → /api/login(설치 Chrome 로그인 폴링) → 성공 시 새로고침.
// mock/local 은 needLogin=false 라 표시 안 됨. updated: 2026-07-09
import { api } from "../../lib/api.js";
export default {
  name: "LoginOverlay",
  data() { return { show: false, busy: false, msg: "", tried: false, hasCache: false,
                    prog: 0, phase: "auto" }; },   // prog 0~100(자동로그인 타임아웃 진행바), phase: auto|manual
  watch: {
    // 미인증이 확인되면 **곧바로** 로그인을 시작한다. 버튼을 기다릴 이유가 없다 —
    // 이 상태에서 사용자가 할 수 있는 일이 그것 하나뿐이고, 세션이 없으면 화면의 모든 조회가
    // 어차피 실패한다. 버튼은 실패했을 때의 재시도용으로 남는다.
    // ★ 페이지당 한 번만 자동 시도한다. 로그인이 실패하는 상황에서 무한히 다시 걸면
    //   앱 창이 Jira 로 계속 튕겨 사용자가 아무것도 못 한다.
    show(v) { if (v && !this.tried) { this.tried = true; this.doLogin(); } },
  },
  mounted() {
    // 도중에 세션이 끊긴 경우: 캐시로 버틸 수 있으면 막지 않고 인증만 다시 건다.
    window.addEventListener("need-login", () => {
      // 화면을 막는 건 **보여 줄 캐시조차 없을 때** 뿐이다. 그 외에는 뒤에서 인증만 다시 건다.
      if (!this.hasCache) this.show = true;
      this.kick();
    });
    // 서버가 로그인 성공을 알리면(창 없이 갱신 포함) 오버레이를 바로 걷고 다음 시도를 풀어 준다.
    window.addEventListener("auth-ok", () => {
      this.show = false; this.msg = ""; this.busy = false;
      this._stopProg();                              // 진행바 정지·리셋
      this.tried = false; this._lastTry = 0;         // 쿨다운 리셋 — 다음에 정말 필요하면 즉시 재시도
    });
    // ★ 캐시가 살아 있으면 화면을 막지 않는다 — 오프라인에서도 최소한의 이용성을 준다.
    //   그때도 로그인 시도는 백그라운드로 계속되고(아래 kick), 상태는 상단 알림이 말한다.
    api.health().then((h) => {
      if (!h) return;
      this.hasCache = !!h.hasCache;
      if (!h.needLogin) return;
      if (this.hasCache) { this.kick(); return; }   // 막지 않고 조용히 인증만 시도
      this.show = true;                          // 보여 줄 캐시조차 없다 → 인증 전엔 진입 불가
    }).catch(() => {});
  },
  methods: {
    /** 화면을 막지 않고 로그인만 시작한다.
     *  ★ **자주 부르지 않는다.** prod 는 401 이 주기적으로 스쳐 지나가는데, 그때마다 로그인을
     *    걸면 인증 흐름이 끊임없이 돌며 화면이 요동친다. 한 번 시도했으면 한동안 쉰다. */
    kick() {
      const now = Date.now();
      if (this.tried && now - (this._lastTry || 0) < 120000) return;   // 2분 쿨다운
      this.tried = true; this._lastTry = now;
      this.doLogin();
    },
    async doLogin() {
      // 앱 창 모드의 로그인은 **이 창을 Jira 로 보냈다가** 앱으로 되돌린다. 그때 주소가 초기값이라
      // 보던 화면(내 Task·티켓)이 홈으로 리셋됐다 — 어디였는지 적어 두고 돌아와서 되돌린다.
      try { sessionStorage.setItem("lake.route", location.hash || ""); } catch (e) { /* noop */ }
      this.busy = true;
      this._startProg();          // 자동 로그인 타임아웃 진행바 시작(끝나면 '수동 로그인' 안내)
      this.msg = "SSO 로그인을 진행하고 있습니다.";
      try {
        const r = await api.login();
        if (r && r.pending) return;                    // 앱 창 모드: 이 창이 Jira 로 이동됨(대기)
        if (r && r.ok) {
          // ★ **새로고침하지 않는다.** 보던 티켓 창·쓰던 글·스크롤이 전부 날아간다. 인증은
          //   서버 쪽에서 이미 끝났으므로, 오버레이만 걷고 하던 일을 계속하게 둔다
          //   (다음 조회부터 정상 응답이 오고, 상단 알림도 스스로 사라진다).
          this.show = false; this.msg = "";
          window.dispatchEvent(new CustomEvent("auth-ok"));
          return;
        }
        this.msg = "로그인이 완료되지 않았습니다(시간 초과/취소). 다시 시도하세요.";
      } catch (e) { this.msg = "로그인 실패: " + e.message; }
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
  unmounted() { this._stopProg(); },
  template: `
    <div v-if="show" class="login-ov">
      <div class="login-card">
        <div class="login-ic">🔒</div>
        <div class="login-h">사내 Jira SSO 로그인 필요</div>
        <div class="login-msg">{{ msg || '세션이 없거나 만료되었습니다. 인증을 시작합니다…' }}</div>
        <template v-if="busy">
          <div class="login-prog" :class="{ manual: phase === 'manual' }"><i :style="{ width: prog + '%' }"></i></div>
          <div class="login-sub">{{ phase === 'manual'
              ? '자동 로그인이 되지 않아 수동 로그인 창을 띄웁니다 — 사내 로그인을 끝까지 완료해 주세요.'
              : '자동 로그인을 시도하는 중입니다… 실패하면 수동 로그인 창이 뜹니다.' }}</div>
        </template>
        <button class="login-btn" :disabled="busy" @click="doLogin">{{ busy ? '로그인 진행 중…' : '다시 시도' }}</button>
      </div>
    </div>`,
};
