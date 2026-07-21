// app-root.js — SPA 루트: 해시 라우팅(#/wbs #/vit #/workload) + nav + 산식 callout + 로그인 오버레이.
// <keep-alive> 로 뷰 상태(데이터·detail/activity 캐시·펼침) 보존 → 탭 전환 재fetch 없음. updated: 2026-07-09
import WorkloadView from "./views/WorkloadView.js";
import VitView from "./views/VitView.js";
import WbsView from "./views/WbsView.js";
import FormulaCallout from "./ui/FormulaCallout.js";
import LoginOverlay from "./ui/LoginOverlay.js";
import TicketDialog from "./ui/TicketDialog.js";
import SearchOverlay from "./ui/SearchOverlay.js";
import { api } from "../lib/api.js";

const ROUTES = { wbs: WbsView, vit: VitView, workload: WorkloadView };
function currentRoute() { return location.hash.replace("#/", "") || "wbs"; }
// /browse/DL-1234 — 그 티켓만의 단독 페이지("새 창에서 열기" 대상).
// Jira 와 같은 URL 형태라 주소만 보고도 어느 티켓인지 안다.
function ticketOf() {
  const m = /^\/browse\/([^/?#]+)/.exec(location.pathname);
  return m ? decodeURIComponent(m[1]) : null;
}

export default {
  name: "AppRoot",
  components: { FormulaCallout, LoginOverlay, TicketDialog, SearchOverlay },
  // ready=health 판정 전. prod 첫 실행: 부팅로더 → (여기) 로딩 스피너 → 로그인 오버레이/대시보드.
  //   → 흰 화면 없음 + 로그인 필요 시 뷰를 먼저 안 띄워 401 에러 깜빡임 방지.
  data() { return { route: currentRoute(), theme: document.documentElement.getAttribute("data-theme") || "light",
                    ready: false, needLogin: false, ticketKey: null, searchOpen: false,
                    ticketKeyFromPath: ticketOf() }; },
  computed: {
    view() { return ROUTES[this.route] || ROUTES.wbs; },
    pageTicket() { return this.ticketKeyFromPath; },
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
    api.health().then((h) => { this.needLogin = !!(h && h.needLogin); })
      .catch(() => {}).finally(() => { this.ready = true; });
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
          <a :class="{ on: route === 'wbs' }" href="#/wbs">WBS Dashboard</a>
          <a :class="{ on: route === 'vit' }" href="#/vit">현안 (PMO_VIT)</a>
          <a :class="{ on: route === 'workload' }" href="#/workload">인력 워크로드</a>
        </nav>
        <div class="top-actions">
          <button class="search-trig" @click="searchOpen = true" title="통합 검색 ( / )">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
            <span>검색</span><kbd>/</kbd>
          </button>
          <button class="theme-btn" @click="toggleTheme" :title="theme === 'dark' ? '라이트 모드로' : '다크 모드로'">
            <span v-if="theme === 'dark'">☀ Light</span><span v-else>🌙 Dark</span>
          </button>
        </div>
      </header>
      <div v-if="!ready" class="loading page">불러오는 중…</div>
      <!-- 티켓 단독 페이지: 대시보드 뷰 대신 티켓 내용만 -->
      <TicketDialog v-else-if="!needLogin && pageTicket" :key="pageTicket"
                    :key-id="pageTicket" mode="page" />
      <template v-else-if="!needLogin">
        <FormulaCallout :route="route" />
        <keep-alive><component :is="view"></component></keep-alive>
      </template>
      <LoginOverlay />
      <TicketDialog v-if="ticketKey" :key-id="ticketKey" @close="ticketKey = null" />
      <SearchOverlay v-if="searchOpen" @close="searchOpen = false"
                     @open-ticket="(k) => { ticketKey = k; searchOpen = false; }" />
    </div>`,
};
