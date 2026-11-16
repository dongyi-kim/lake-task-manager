// FloatingRefresh.js — 좌하단 플로팅 '강제 새로고침' 버튼.
//
// 새로고침 버튼이 탭마다 제각각(어디는 ↻ 둘, 어디는 인라인 '강제 새로고침')이라 무엇이
// 최신 정보를 받는 동작을 하나로 통일한다. 기존 화면과 캐시는 유지한다.
//
// 각 뷰를 직접 알지 않는다(뷰는 keep-alive 로 갈아 끼워진다). 전역 이벤트로 부탁하고,
// 공통 API가 한 번 갱신을 요청하고 현재 화면의 조회가 끝나면 완료를 알린다.
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
    window.addEventListener("force-refresh-done", this._done = e => { this.busy = false; this.phase = ""; if (e.detail?.error) this._toast("error", "새로고침에 실패했습니다", e.detail.error.message); });
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
      this._toast("info", "Jira 연결을 복구합니다", "백그라운드에서 연결을 확인합니다…", 5000);
      let r = null;
      try { r = await api.recoverAuth(); }
      catch (e) {
        this._toast("error", "로그인에 실패했습니다", (e && e.message) || "잠시 후 다시 시도해 주세요.");
        return false;
      }
      if (!(r && r.ok)) {
        this._toast("info", r?.needLogin ? "Jira 로그인이 필요합니다" : "Jira 연결을 확인하고 있습니다",
                    r?.needLogin ? "연결 안내 또는 설정의 로그인 버튼을 눌러 주세요." : "잠시 후 다시 시도해 주세요.");
        return false;
      }
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
                  : (busy ? '다시 받는 중…' : '새로고침 — 기존 화면을 유지하면서 최신 정보를 받습니다')"
          aria-label="강제 새로고침">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 4v5h-5"/>
    </svg>
  </button>`,
};
