// app-root.js — SPA 루트: 해시 라우팅(#/wbs #/vit #/workload) + nav + 산식 callout + 로그인 오버레이.
// <keep-alive> 로 뷰 상태(데이터·detail/activity 캐시·펼침) 보존 → 탭 전환 재fetch 없음. updated: 2026-07-09
import MyTasksView from "./views/MyTasksView.js";
import WorkloadView from "./views/WorkloadView.js";
import VitView from "./views/VitView.js";
import WbsView from "./views/WbsView.js";
import DevToolsView from "./views/DevToolsView.js";
import FormulaCallout from "./ui/FormulaCallout.js";
import LoginOverlay from "./ui/LoginOverlay.js";
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
  components: { FormulaCallout, LoginOverlay, TicketDialog, SearchOverlay, SettingsMenu },
  // ready=health 판정 전. prod 첫 실행: 부팅로더 → (여기) 로딩 스피너 → 로그인 오버레이/대시보드.
  //   → 흰 화면 없음 + 로그인 필요 시 뷰를 먼저 안 띄워 401 에러 깜빡임 방지.
  data() { return { route: currentRoute(), theme: document.documentElement.getAttribute("data-theme") || "light",
                    ready: false, needLogin: false, ticketKey: null, searchOpen: false,
                    // null = 아직 모름. 판정 전에는 매니저 전용 탭을 감춰 둔다 —
                    // 보였다가 사라지면 눌러 놓고 튕기는 꼴이 된다.
                    manager: null,
                    ticketKeyFromPath: ticketOf() }; },
  computed: {
    tabs() { return TABS.filter((t) => !t.manager || this.manager); },
    /** 이 사용자가 볼 수 있는 화면인가. 매니저 전용인데 아니면 접근 자체를 막는다. */
    allowed() { return !MANAGER_ONLY.has(this.route) || this.manager === true; },
    view() {
      if (!this.allowed) return ROUTES.mytasks;   // 권한 없는 주소는 '내 Task' 로
      return ROUTES[this.route] || ROUTES.wbs;
    },
    pageTicket() { return this.ticketKeyFromPath; },
  },
  watch: {
    // 볼 수 없는 주소면 주소 자체를 바로잡는다. 화면만 바꿔치면 해시가 #/wbs 로 남아
    // 탭 강조가 아무 데도 안 걸리고, 새로고침할 때마다 같은 상황이 되풀이된다.
    route() { if (this.ready && !this.allowed) location.hash = "#/mytasks"; },
  },
  mounted() {
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
    api.health().then((h) => {
      this.needLogin = !!(h && h.needLogin);
      if (this.needLogin) return null;
      // 매니저 판정은 세션 사용자로 하므로 로그인 뒤에야 알 수 있다.
      return api.me().then((me) => { this.manager = !!(me && me.manager); }).catch(() => {
        // 판정을 못 하면 **막지 않는다**. 서버가 어차피 403 으로 막으므로 여기서 감추면
        // 매니저인데도 화면이 사라지는 쪽이 더 큰 사고다.
        this.manager = true;
      });
    }).catch(() => {}).finally(() => {
      this.ready = true;
      // 기본 진입(#없음)은 wbs 인데, 매니저가 아니면 볼 수 없다 → 내 Task 로 보낸다.
      if (!this.allowed) location.hash = "#/mytasks";
    });
  },
  methods: {
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
      <div v-if="!ready" class="loading page">불러오는 중…</div>
      <!-- 티켓 단독 페이지: 대시보드 뷰 대신 티켓 내용만 -->
      <TicketDialog v-else-if="!needLogin && pageTicket" :key="pageTicket"
                    :key-id="pageTicket" mode="page" :theme="theme"
                    @search="searchOpen = true" @toggle-theme="toggleTheme" />
      <template v-else-if="!needLogin">
        <FormulaCallout :route="route" />
        <keep-alive><component :is="view"></component></keep-alive>
      </template>
      <LoginOverlay />
      <TicketDialog v-if="ticketKey" :key-id="ticketKey" @close="ticketKey = null" />
      <!-- keep-alive: 같은 창에서 다시 열면 마지막 검색어·결과가 그대로 남는다 -->
      <keep-alive>
        <SearchOverlay v-if="searchOpen" @close="searchOpen = false"
                       @open-ticket="(k) => { ticketKey = k; searchOpen = false; }" />
      </keep-alive>
    </div>`,
};
