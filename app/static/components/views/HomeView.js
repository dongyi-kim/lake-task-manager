// HomeView.js — 메인 랜딩(#/home). 로고·제목 + 서비스 인증 상황 + 주요 페이지 소개.
//
// 소개 카드는 **본인에게 허락된 페이지만** 보여준다(매니저 전용은 manager===true 일 때만).
// 인증 상황은 서비스별(Jira/Confluence/Bitbucket)로 표시 — SettingsMenu 와 같은 소스.
import { api } from "../../lib/api.js";
import { RELEASES } from "../../lib/releaseNotes.js";

const SERVICES = ["Jira", "Confluence", "Bitbucket"];

// 주요 페이지 소개. manager:true 는 매니저에게만. k 는 라우트(#/k).
const PAGES = [
  { k: "ai", label: "AI 어시스턴트", icon: "🤖", manager: false,
    desc: "업무를 한 문장으로 말하면 과거 이력을 조사해 티켓 초안까지 만듭니다. 지식·현황 질문도 답합니다." },
  { k: "wbs", label: "WBS Dashboard", icon: "📊", manager: true,
    desc: "모듈 → WBS Task → Epic 진척률을 Story Point 기준으로 롤업해 프로젝트 전체를 간트로 조망합니다." },
  { k: "vit", label: "현안 (PMO_VIT)", icon: "🎯", manager: false,
    desc: "PMO_VIT 루트 현안의 자손(Epic·Task·Sub-Task) 진행 상황을 데일리로 트래킹합니다." },
  { k: "workload", label: "인력 워크로드", icon: "👥", manager: true,
    desc: "인력별 작업량(진행 중 · 최근 7일 완료)과 활동을 모듈 단위로 봅니다." },
  { k: "mytasks", label: "Task", icon: "✅", manager: false,
    desc: "담당·보고·모듈 단위로 일감을 마감 · 우선순위로 정렬해 한눈에 봅니다." },
];

export default {
  name: "HomeView",
  // manager 는 스스로 확인한다(api.me 는 memo 라 앱루트와 중복 요청이 아니다). null=아직 모름.
  data() { return { auth: null, env: "", manager: null, releases: RELEASES.slice(0, 10),
                    ask: "", askBusy: false, askNote: "" }; },
  computed: {
    // 허락된 페이지만 — 매니저 전용은 manager 가 확정(true)일 때만 노출(먼저 판정, 그 뒤 표시).
    pages() { return PAGES.filter((p) => !p.manager || this.manager === true); },
  },
  mounted() {
    api.me().then((me) => { this.manager = !!(me && me.manager); }).catch(() => { this.manager = null; });
    api.health().then((h) => { this.env = (h && h.env) || ""; }).catch(() => {});
    // 서비스별 인증 상황(Jira/Confluence/Bitbucket) — 하나로 모아 받는다.
    api.raw("/api/dev/sso").then((r) => {
      const by = {};
      for (const t of (r && r.targets) || []) by[(t.service || "").toLowerCase()] = t;
      this.auth = SERVICES.map((name) => {
        const t = by[name.toLowerCase()] || {};
        const st = t.configured === false ? "off" : (t.authenticated ? "ok" : "no");
        return { name, status: st, detail: t.detail || "" };
      });
    }).catch(() => { this.auth = null; });
  },
  methods: {
    stateLabel(s) { return { ok: "인증됨", no: "미인증", off: "미설정" }[s] || "확인 중"; },
    // 홈의 입력 → AI 탭으로 넘어가 **새 대화**를 시작한다. 넘어가기 전에 인증을 확인하고,
    // 안 되어 있으면 시도하며, 그래도 안 되면 "캐시 기준" 안내와 함께 진행한다(막지 않는다).
    async goAsk() {
      const text = (this.ask || "").trim();
      if (!text || this.askBusy) return;
      this.askBusy = true;
      this.askNote = "인증 확인 중…";
      try {
        let h = await api.health().catch(() => null);
        if (h && h.needLogin) {
          this.askNote = "세션이 만료되어 인증을 시도합니다…";
          try { await api.login(); h = await api.health().catch(() => h); }
          catch (e) { /* 로그인 실패 — 아래 캐시 판단으로 */ }
        }
        if (h && h.needLogin) {
          this.askNote = h.hasCache
            ? "인증하지 못했습니다 — 저장된 데이터(캐시) 기준으로 진행합니다. 실시간 조회가 필요한 요청은 제한될 수 있습니다."
            : "인증하지 못했고 캐시도 없습니다 — 조회가 필요한 요청은 실패할 수 있습니다.";
        } else {
          this.askNote = "";
        }
      } finally {
        // 판단 결과와 무관하게 **막지 않는다** — AI 탭이 같은 정보를 다시 보여 준다.
        try { sessionStorage.setItem("agent:seed", text); } catch (e) { /* noop */ }
        try { if (this.askNote) sessionStorage.setItem("agent:authNote", this.askNote); } catch (e) { /* noop */ }
        this.askBusy = false;
        location.hash = "#/ai";
      }
    },
  },
  template: `
  <div class="home">
    <div class="home-hero">
      <img class="home-logo" src="icon.png" alt="Lake Task Manager" />
      <div class="home-titles">
        <h1 class="home-title">Lake Task Manager</h1>
        <p class="home-sub">Jira DC 위에 얹는 PMO 리포팅 레이어</p>
      </div>
    </div>

    <!-- 한 문장으로 시작 — 입력하면 AI 탭으로 넘어가 새 대화를 시작한다 -->
    <form class="home-ask" @submit.prevent="goAsk">
      <input class="home-ask-in" v-model="ask" :disabled="askBusy"
             placeholder="무엇을 해야 하나요? — 예: 적재 지연 알림 임계값을 조정해야 해" />
      <button class="home-ask-go" type="submit" :disabled="askBusy || !ask.trim()">
        {{ askBusy ? '확인 중…' : '시작' }}
      </button>
    </form>
    <p v-if="askNote" class="home-ask-note">{{ askNote }}</p>

    <!-- 서비스 인증 상황 -->
    <div v-if="auth" class="home-auth">
      <span v-for="a in auth" :key="a.name" class="home-authchip" :class="a.status"
            :title="a.detail || (a.name + ' ' + stateLabel(a.status))">
        <span class="ha-dot" :class="a.status"></span>
        <b>{{ a.name }}</b><span class="ha-st">{{ stateLabel(a.status) }}</span>
      </span>
      <span v-if="env && env !== 'prod'" class="home-envnote">개발 환경 ({{ env }}) — 실제 SSO 아님</span>
    </div>

    <!-- 주요 페이지 소개 (허락된 것만) -->
    <div class="home-cards">
      <a v-for="p in pages" :key="p.k" class="home-card" :href="'#/' + p.k">
        <span class="hc-ic">{{ p.icon }}</span>
        <span class="hc-body">
          <span class="hc-t">{{ p.label }}</span>
          <span class="hc-d">{{ p.desc }}</span>
        </span>
        <span class="hc-go" aria-hidden="true">→</span>
      </a>
    </div>
    <p v-if="manager === null" class="home-hint">권한 확인 중… 매니저 전용 페이지는 확인 후 표시됩니다.</p>

    <!-- 최근 업데이트(릴리즈 노트) — 유저가 느낄 변화만 짧게. 내용은 lib/releaseNotes.js -->
    <section v-if="releases.length" class="home-notes">
      <h2 class="hn-h">최근 업데이트</h2>
      <div v-for="r in releases" :key="r.version" class="hn-rel">
        <div class="hn-head"><span class="hn-version">{{ r.version }}</span>
          <b class="hn-title">{{ r.title }}</b></div>
        <!-- 유형별 소제목 — 한 배포에 열몇 줄이 평평하게 늘어서면 무엇이 어디 얘기인지 안 읽힌다. -->
        <div v-for="(g, gi) in r.groups" :key="gi" class="hn-grp">
          <div class="hn-gh">{{ g.h }}</div>
          <ul class="hn-items"><li v-for="(it, i) in g.items" :key="i">{{ it.text || it }}</li></ul>
        </div>
      </div>
    </section>
  </div>`,
};
