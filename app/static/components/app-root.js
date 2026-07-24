// app-root.js — SPA 루트: 해시 라우팅(#/wbs #/vit #/workload) + nav + 산식 callout + 로그인 오버레이.
// <keep-alive> 로 뷰 상태(데이터·detail/activity 캐시·펼침) 보존 → 탭 전환 재fetch 없음. updated: 2026-07-09
import MyTasksView from "./views/MyTasksView.js";
import WorkloadView from "./views/WorkloadView.js";
import VitView from "./views/VitView.js";
import WbsView from "./views/WbsView.js";
import DevToolsView from "./views/DevToolsView.js";
import FormulaCallout from "./ui/FormulaCallout.js";
import LoginOverlay from "./ui/LoginOverlay.js";
import StatusBanner from "./ui/StatusBanner.js";
import TicketMenu from "./ui/TicketMenu.js";
import TicketDialog from "./ui/TicketDialog.js";
import SearchOverlay from "./ui/SearchOverlay.js";
import SettingsMenu from "./ui/SettingsMenu.js";
import { api } from "../lib/api.js";

const ROUTES = { wbs: WbsView, vit: VitView, workload: WorkloadView,
                 mytasks: MyTasksView, devtools: DevToolsView };
// 탭 정의 한곳 — 라벨과 접근 권한이 갈라지지 않게. manager: true 면 매니저에게만 보인다.
// (티켓 뷰/다이얼로그·검색은 역할과 무관하다 — 여기 없는 건 다 누구나 쓴다.)
const TABS = [
  { k: "wbs", label: "WBS Dashboard", manager: true },
  { k: "vit", label: "현안 (PMO_VIT)" },
  { k: "workload", label: "인력 워크로드", manager: true },
  { k: "mytasks", label: "내 Task" },
];
// 탭에 없지만 주소로는 갈 수 있는 매니저 전용 화면(설정 메뉴에서 진입).
const MANAGER_ONLY = new Set(TABS.filter((t) => t.manager).map((t) => t.k).concat(["devtools"]));
function currentRoute() { return location.hash.replace("#/", "") || "wbs"; }
// /browse/DL-1234 — 그 티켓만의 단독 페이지("새 창에서 열기" 대상).
// Jira 와 같은 URL 형태라 주소만 보고도 어느 티켓인지 안다.
function ticketOf() {
  const m = /^\/browse\/([^/?#]+)/.exec(location.pathname);
  return m ? decodeURIComponent(m[1]) : null;
}

export default {
  name: "AppRoot",
  components: { FormulaCallout, LoginOverlay, StatusBanner, TicketMenu, TicketDialog, SearchOverlay, SettingsMenu },
  // ready=health 판정 전. prod 첫 실행: 부팅로더 → (여기) 로딩 스피너 → 로그인 오버레이/대시보드.
  //   → 흰 화면 없음 + 로그인 필요 시 뷰를 먼저 안 띄워 401 에러 깜빡임 방지.
  data() { return { route: currentRoute(), theme: document.documentElement.getAttribute("data-theme") || "light",
                    ready: false, needLogin: false, ticketKey: null, searchOpen: false,
                    // null = 아직 모름 · true = 매니저 · false = 아님.
                    // ★ 모를 때는 **막지 않는다**(false 일 때만 제한). 판정이 늦거나 실패했다고
                    //   매니저의 탭이 사라지거나 첫 화면에서 튕기는 쪽이, 워커에게 잠깐 보였다가
                    //   사라지는 쪽보다 훨씬 나쁘다. 데이터는 어차피 서버가 403 으로 막는다.
                    manager: null,
                    // 캐시로 버틸 수 있는가. 미인증이어도 캐시가 살아 있으면 화면은 띄운다.
                    hasCache: false,
                    ticketKeyFromPath: ticketOf() }; },
  computed: {
    tabs() { return TABS.filter((t) => !t.manager || this.manager !== false); },
    /** 이 사용자가 볼 수 있는 화면인가. 매니저 전용인데 아니면 접근 자체를 막는다. */
    allowed() { return !MANAGER_ONLY.has(this.route) || this.manager !== false; },
    view() {
      if (!this.allowed) return ROUTES.mytasks;   // 권한 없는 주소는 '내 Task' 로
      return ROUTES[this.route] || ROUTES.wbs;
    },
    pageTicket() { return this.ticketKeyFromPath; },
  },
  watch: {
    // 볼 수 없는 주소면 주소 자체를 바로잡는다. 화면만 바꿔치면 해시가 #/wbs 로 남아
    // 탭 강조가 아무 데도 안 걸리고, 새로고침할 때마다 같은 상황이 되풀이된다.
    route() { this.guard(); },
    // 판정이 늦게 도착해 '매니저 아님' 으로 밝혀지면 그때 자리를 옮긴다.
    // (기본 진입 #없음 = wbs 도 여기서 걸린다 — 워커의 기본 화면은 내 Task 다.)
    manager() { this.guard(); },
  },
  mounted() {
    // 로그인 왕복(앱 창이 Jira 로 갔다 돌아옴) 뒤 **보던 자리로** 되돌린다.
    // 없으면 늘 홈에서 다시 시작하게 되는데, 그건 로그인이 아니라 사고처럼 느껴진다.
    try {
      const back = sessionStorage.getItem("lake.route");
      if (back) {
        sessionStorage.removeItem("lake.route");
        if (back !== (location.hash || "")) { location.hash = back; this.route = currentRoute(); }
      }
    } catch (e) { /* noop */ }
    // ★ 파일을 **빗맞게 떨어뜨리면 브라우저가 그 파일로 이동한다** — 앱 창이 파일을 열고,
    //   돌아오면 앱이 처음부터 다시 뜬 것처럼 보인다('홈으로 돌아가고 새로 뜨는' 증상이 이것이다).
    //   드롭 영역들은 각자 preventDefault 를 하지만, 그 **바깥**(여백·헤더·다이얼로그 틈)은
    //   아무도 안 막는다. 여기서 한 겹 더 막는다 — 파일 드래그일 때만.
    const hasFiles = (e) => {
      const t = e.dataTransfer && e.dataTransfer.types;
      return !!t && Array.prototype.indexOf.call(t, "Files") >= 0;
    };
    window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
    window.addEventListener("drop", (e) => { if (hasFiles(e)) e.preventDefault(); });
    window.addEventListener("hashchange", () => { this.route = currentRoute(); });
    window.addEventListener("need-login", () => { this.needLogin = true; this.ready = true; });
    // 티켓 링크(.tkt[data-key]) 위임 처리 — 어느 화면/코멘트에서 눌러도 인앱 다이얼로그로 연다.
    document.addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest(".tkt[data-key]");
      if (!a) return;
      e.preventDefault();
      this.ticketKey = a.getAttribute("data-key");
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const a = e.target.closest && e.target.closest(".tkt[data-key]");
      if (!a) return;
      e.preventDefault();
      this.ticketKey = a.getAttribute("data-key");
    });
    // 통합 검색 단축키: "/" (입력 중 아닐 때) 또는 Ctrl/Cmd+K
    document.addEventListener("keydown", (e) => {
      const t = e.target, tag = (t && t.tagName) || "";
      const typing = tag === "INPUT" || tag === "TEXTAREA" || (t && t.isContentEditable);
      if (((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) || (e.key === "/" && !typing)) {
        e.preventDefault(); this.searchOpen = true;
      }
    });
    // ★ 부팅은 health 하나만 기다린다. /api/me 는 prod 에서 Jira(/myself)를 타고, 그건
    //   Playwright 전용 스레드 한 줄로 직렬화돼 있어 세션이 굳으면 최대 JOB_TIMEOUT(180s)까지
    //   멎는다. 여기에 부팅을 묶으면 창이 '불러오는 중…' 에서 몇 분씩 갇힌다(실제로 겪었다).
    api.health().then((h) => {
      this.needLogin = !!(h && h.needLogin);
      this.hasCache = !!(h && h.hasCache);
    }).catch(() => {}).finally(() => { this.ready = true; });
    // 매니저 판정은 **부팅과 무관하게** 따로 흐른다. 결과가 오면 watch 가 정리한다.
    api.me().then((me) => { this.manager = !!(me && me.manager); })
      .catch(() => { this.manager = null; });   // 모르면 막지 않는다(아래 주석 참고)
  },
  methods: {
    /** 볼 수 없는 주소면 워커 기본 화면(내 Task)으로. 화면만 바꿔치지 않고 **주소까지** 고친다
     *  — 안 그러면 탭 강조가 아무 데도 안 걸리고 새로고침마다 같은 상황이 되풀이된다. */
    guard() { if (!this.allowed) location.hash = "#/mytasks"; },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", this.theme);
      try { localStorage.setItem("theme", this.theme); } catch (e) {}
    },
  },
  template: `
    <div class="wrap" :class="{ 'wrap-bare': pageTicket }">
      <header v-if="!pageTicket" class="top">
        <img class="nav-logo" src="icon.png" alt="Lake Task Manager" title="Lake Task Manager" />
        <nav class="tabs">
          <a v-for="t in tabs" :key="t.k" :class="{ on: route === t.k }"
             :href="'#/' + t.k">{{ t.label }}</a>
        </nav>
        <div class="top-actions">
          <button class="search-trig" @click="searchOpen = true" title="통합 검색 ( / )">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
            <span>검색</span><kbd>/</kbd>
          </button>
          <SettingsMenu :theme="theme" @toggle-theme="toggleTheme" />
        </div>
      </header>
      <div v-if="!ready" class="loading page">인증 확인 중…</div>
      <!-- 티켓 단독 페이지: 대시보드 뷰 대신 티켓 내용만 -->
      <TicketDialog v-else-if="!needLogin && pageTicket" :key="pageTicket"
                    :key-id="pageTicket" mode="page" :theme="theme"
                    @search="searchOpen = true" @toggle-theme="toggleTheme" />
      <!-- 인증이 안 됐어도 **캐시가 살아 있으면** 화면을 준다. 오프라인에서 아무것도 못 보는
           것보다, 낡았다는 사실을 알리면서 어제 것이라도 보여 주는 편이 쓸모 있다.
           캐시가 죽었으면(dead_ttl 초과) 보여 줄 게 없으므로 인증 전에는 못 들어간다. -->
      <template v-else-if="!needLogin || hasCache">
        <StatusBanner />
        <FormulaCallout :route="route" />
        <keep-alive><component :is="view"></component></keep-alive>
      </template>
      <TicketMenu />
      <LoginOverlay />
      <TicketDialog v-if="ticketKey" :key-id="ticketKey" @close="ticketKey = null" />
      <!-- keep-alive: 같은 창에서 다시 열면 마지막 검색어·결과가 그대로 남는다 -->
      <keep-alive>
        <SearchOverlay v-if="searchOpen" @close="searchOpen = false"
                       @open-ticket="(k) => { ticketKey = k; searchOpen = false; }" />
      </keep-alive>
    </div>`,
};
