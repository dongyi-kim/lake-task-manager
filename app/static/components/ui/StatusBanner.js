// StatusBanner.js — 화면 상단 플로팅 알림.
//
// 지금 보고 있는 것이 **언제 기준의 데이터인지** 말한다. 오프라인이거나 세션이 끊긴 동안에도
// 앱은 캐시로 계속 돌아가는데, 그 사실을 안 알리면 사용자는 낡은 숫자를 최신으로 믿는다.
// 화면을 막는 대신 알린다 — 막으면 아무것도 못 하고, 조용하면 잘못 믿는다.
//
//   offline        망이 안 닿는다. 사용자가 할 수 있는 건 기다리는 것뿐.
//   authenticating 망은 닿는데 세션이 없다. 로그인이 진행 중이다.
// 둘을 같은 말로 뭉뚱그리면 무엇을 기다려야 하는지 알 수 없다.
import { api } from "../../lib/api.js";

const POLL_MS = 8000;

export default {
  name: "StatusBanner",
  data() { return { mode: "ok", lastSyncAt: null, hidden: false }; },
  computed: {
    show() { return !this.hidden && (this.mode === "offline" || this.mode === "authenticating"); },
    label() { return this.mode === "offline" ? "오프라인" : "인증 중"; },
    detail() {
      return this.mode === "offline"
        ? "사내망에 연결되지 않았습니다. 연결되면 자동으로 최신 데이터를 받아옵니다."
        : "SSO 로그인을 진행하고 있습니다. 완료되면 자동으로 최신 데이터를 받아옵니다.";
    },
    since() {
      if (!this.lastSyncAt) return "저장된 이전 데이터";
      const d = new Date(this.lastSyncAt * 1000);
      const p = (n) => String(n).padStart(2, "0");
      const today = new Date();
      const sameDay = d.toDateString() === today.toDateString();
      const t = p(d.getHours()) + ":" + p(d.getMinutes());
      return (sameDay ? "오늘 " + t : (d.getMonth() + 1) + "/" + d.getDate() + " " + t) + " 기준";
    },
  },
  mounted() {
    this.tick();
    this._t = setInterval(this.tick, POLL_MS);
    // 다운로드 알림은 우하단 ToastStack 이 맡는다(여기선 상단 오프라인/인증중 배너만).
  },
  unmounted() { clearInterval(this._t); },
  methods: {
    tick() {
      api.raw("/api/status").then((s) => {
        if (!s) return;
        // 정상으로 돌아오면 닫아 둔 것도 다시 살린다 — 다음에 또 끊기면 알려야 하므로.
        if (s.mode === "ok") this.hidden = false;
        this.mode = s.mode || "ok";
        this.lastSyncAt = s.lastSyncAt || null;
      }).catch(() => { this.mode = "offline"; });   // 우리 서버조차 안 닿으면 오프라인이다
    },
  },
  template: `
  <div>
  <div v-if="show" class="stbanner" :class="mode" role="status">
    <span class="stb-dot"></span>
    <b>{{ label }}</b>
    <span class="stb-stale">지금 보는 데이터는 최신이 아닙니다 · {{ since }}</span>
    <span class="stb-hint">{{ detail }}</span>
    <button class="stb-x" @click="hidden = true" title="이 알림 닫기">×</button>
  </div>
  </div>`,
};
