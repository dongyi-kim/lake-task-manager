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
  data() { return { mode: "ok", lastSyncAt: null, hidden: false,
                    // 앱 창은 다운로드 표시줄이 없다 — 저장됐다는 사실을 우리가 알려야 한다.
                    dl: null }; },
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
    window.addEventListener("lake-download", this._onDl = (e) => {
      this.dl = (e && e.detail) || null;
      clearTimeout(this._dlT);
      this._dlT = setTimeout(() => { this.dl = null; }, 6000);
    });
  },
  unmounted() { clearInterval(this._t); clearTimeout(this._dlT);
                window.removeEventListener("lake-download", this._onDl); },
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
  <!-- 다운로드 알림 — 앱 창(Chromium 앱 모드)에는 다운로드 표시줄이 없어서, 저장이 됐는지
       안 됐는지 알 길이 없다. 파일명과 저장 위치를 잠깐 띄운다. -->
  <div v-if="dl" class="dltoast" :class="{ bad: !dl.ok }">
    <span class="dl-ic">{{ dl.ok ? '⬇' : '⚠' }}</span>
    <template v-if="dl.ok"><b>{{ dl.name }}</b><span class="dl-p">{{ dl.path }}</span></template>
    <template v-else><b>다운로드 실패</b><span class="dl-p">{{ dl.error }}</span></template>
    <button class="dl-x" @click="dl = null" title="닫기">×</button>
  </div>
  <div v-if="show" class="stbanner" :class="mode" role="status">
    <span class="stb-dot"></span>
    <b>{{ label }}</b>
    <span class="stb-stale">지금 보는 데이터는 최신이 아닙니다 · {{ since }}</span>
    <span class="stb-hint">{{ detail }}</span>
    <button class="stb-x" @click="hidden = true" title="이 알림 닫기">×</button>
  </div>
  </div>`,
};
