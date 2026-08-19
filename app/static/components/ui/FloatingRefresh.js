// FloatingRefresh.js — 좌하단 플로팅 '강제 새로고침' 버튼.
//
// 새로고침 버튼이 탭마다 제각각(어디는 ↻ 둘, 어디는 인라인 '강제 새로고침')이라 무엇이
// 캐시를 비우는 것인지 헷갈렸다. **하나로 통일**한다 — 화면 어디에 있든 좌하단 같은 자리,
// 같은 아이콘. 누르면 지금 보고 있는 화면이 캐시를 비우고 처음부터 다시 받는다.
//
// 각 뷰를 직접 알지 않는다(뷰는 keep-alive 로 갈아 끼워진다). 전역 이벤트로 부탁하고,
// 지금 화면의 뷰가 자기 hardRefresh 를 돌린 뒤 끝났다고 알린다. 아무도 못 받으면(그런 화면이면)
// 8초 뒤 스스로 멈춘다 — 영영 도는 아이콘은 '멈춘 것' 으로 읽힌다.
//
// ★ 다시 받기 **전에 인증부터 확인**한다. 사용자가 이 버튼을 누르는 상황의 절반은 "화면이 안
//   뜬다" 인데 그 원인이 대개 끊긴 세션이다. 그대로 다시 받으면 같은 실패를 반복할 뿐이라
//   여기서 재인증까지 끝내고 받는다. 안 되면 **무엇 때문인지** 말한다 — 오프라인인지, 인증이
//   안 끝난 것인지, 앱 서버가 죽은 것인지에 따라 사용자가 할 일이 완전히 다르다.
import { api } from "../../lib/api.js";
import { pushToast } from "../../lib/toast.js";

export default {
  name: "FloatingRefresh",
  data() { return { busy: false, phase: "" }; },   // phase: '' | 'auth'(인증 중 — 말풍선 문구가 다르다)
  mounted() {
    window.addEventListener("force-refresh-done", this._done = () => { this.busy = false; this.phase = ""; });
  },
  unmounted() {
    window.removeEventListener("force-refresh-done", this._done);
    clearTimeout(this._t);
  },
  methods: {
    async go() {
      if (this.busy) return;
      this.busy = true;
      let ok = false;
      try { ok = await this.ensureAuth(); }
      catch (e) { this._toast("error", "새로고침에 실패했습니다", (e && e.message) || String(e)); }
      this.phase = "";
      if (!ok) { this.busy = false; return; }
      window.dispatchEvent(new CustomEvent("force-refresh"));
      clearTimeout(this._t);
      this._t = setTimeout(() => { this.busy = false; }, 8000);   // 아무도 안 받으면 스스로 멈춘다
    },

    /** 지금 받아도 되는 상태인가. 아니면 고칠 수 있는 만큼 고치고, 안 되면 이유를 말한다. */
    async ensureAuth() {
      // /api/status 는 **Jira 를 타지 않는** 경량 상태다(mode: ok | offline | authenticating).
      let st = null;
      try { st = await api.raw("/api/status"); } catch (e) { st = null; }
      if (!st) {
        // 우리 앱 서버조차 안 닿는다 — Jira 문제와 구분해서 말해야 사용자가 헛짚지 않는다.
        this._toast("error", "앱 서버에 연결할 수 없습니다",
                    "앱이 종료되었거나 재시작 중일 수 있습니다. 창을 닫고 다시 실행해 주세요.");
        return false;
      }
      if (st.mode === "offline") {
        // 망이 안 닿으면 재인증도 무의미하다 — 기다리는 것 말곤 할 수 있는 게 없다.
        this._toast("error", "사내망에 연결되지 않았습니다",
                    "VPN·사내망 연결을 확인해 주세요. 연결되면 자동으로 최신 데이터를 받아옵니다.");
        return false;
      }
      if (st.mode === "degraded") {
        this._toast("error", "Jira 응답이 지연되고 있습니다",
                    "앱은 정상 실행 중입니다. 잠시 후 다시 누르면 자동으로 새 연결을 시도합니다.");
        return false;
      }
      if (!st.needLogin) return true;                        // 정상 — 바로 받는다

      this.phase = "auth";
      this._toast("info", "인증이 만료되어 다시 로그인합니다", "잠시만 기다려 주세요…", 5000);
      let r = null;
      try { r = await api.login(); }
      catch (e) {
        this._toast("error", "로그인에 실패했습니다", (e && e.message) || "잠시 후 다시 시도해 주세요.");
        return false;
      }
      if (r && r.pending) {
        // 앱 창 모드: 이 창이 Jira 로 이동해 사람이 직접 로그인한다. 지금 데이터를 받을 수는 없다.
        this._toast("info", "로그인 창에서 사내 로그인을 완료해 주세요",
                    "로그인이 끝나면 화면이 자동으로 최신 데이터를 받아옵니다.");
        return false;
      }
      if (!(r && r.ok)) {
        this._toast("error", "로그인이 완료되지 않았습니다",
                    "시간이 초과되었거나 취소되었습니다. 잠시 후 다시 눌러 주세요.");
        return false;
      }
      // 인증이 방금 섰다 — api.js 가 이 신호로 memo(죽은 요청)를 비우고 각 화면을 깨운다.
      // 성공 알림은 여기서 내지 않는다: ToastStack 이 auth-ok 를 받아 이미 띄운다(두 번 말하게 된다).
      window.dispatchEvent(new CustomEvent("auth-ok"));
      return true;
    },

    /** key 를 주어 연타해도 같은 알림이 쌓이지 않게 한다. */
    _toast(kind, title, message, timeout) {
      pushToast({ kind, title, message, timeout: timeout || 9000, key: "refresh-" + kind });
    },
  },
  template: `
  <button class="fab-refresh" :class="{ busy, auth: phase === 'auth' }" @click="go"
          :title="phase === 'auth' ? '인증하는 중…'
                  : (busy ? '다시 받는 중…' : '강제 새로고침 — 인증을 확인하고 캐시를 비운 뒤 처음부터 다시 받습니다')"
          aria-label="강제 새로고침">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 4v5h-5"/>
    </svg>
  </button>`,
};
