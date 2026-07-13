// app-root.js — SPA 루트: 해시 라우팅(#/wbs #/vit #/workload) + nav + 산식 callout + 로그인 오버레이.
// <keep-alive> 로 뷰 상태(데이터·detail/activity 캐시·펼침) 보존 → 탭 전환 재fetch 없음. updated: 2026-07-09
import WorkloadView from "./views/WorkloadView.js";
import VitView from "./views/VitView.js";
import WbsView from "./views/WbsView.js";
import FormulaCallout from "./ui/FormulaCallout.js";
import LoginOverlay from "./ui/LoginOverlay.js";
import { api } from "../lib/api.js";

const ROUTES = { wbs: WbsView, vit: VitView, workload: WorkloadView };
function currentRoute() { return location.hash.replace("#/", "") || "wbs"; }

export default {
  name: "AppRoot",
  components: { FormulaCallout, LoginOverlay },
  // ready=health 판정 전. prod 첫 실행: 부팅로더 → (여기) 로딩 스피너 → 로그인 오버레이/대시보드.
  //   → 흰 화면 없음 + 로그인 필요 시 뷰를 먼저 안 띄워 401 에러 깜빡임 방지.
  data() { return { route: currentRoute(), theme: document.documentElement.getAttribute("data-theme") || "light",
                    ready: false, needLogin: false }; },
  computed: { view() { return ROUTES[this.route] || ROUTES.wbs; } },
  mounted() {
    window.addEventListener("hashchange", () => { this.route = currentRoute(); });
    window.addEventListener("need-login", () => { this.needLogin = true; this.ready = true; });
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
    <div class="wrap">
      <header class="top">
        <h1><img src="icon.png" class="app-logo" alt=""> Lake Task Manager <span class="sub">PMO Dashboard</span></h1>
        <button class="theme-btn" @click="toggleTheme" :title="theme === 'dark' ? '라이트 모드로' : '다크 모드로'">
          <span v-if="theme === 'dark'">☀ Light</span><span v-else>🌙 Dark</span>
        </button>
      </header>
      <nav class="tabs">
        <a :class="{ on: route === 'wbs' }" href="#/wbs">WBS Dashboard</a>
        <a :class="{ on: route === 'vit' }" href="#/vit">현안 (PMO_VIT)</a>
        <a :class="{ on: route === 'workload' }" href="#/workload">인력 워크로드</a>
      </nav>
      <div v-if="!ready" class="loading page">불러오는 중…</div>
      <template v-else-if="!needLogin">
        <FormulaCallout :route="route" />
        <keep-alive><component :is="view"></component></keep-alive>
      </template>
      <LoginOverlay />
    </div>`,
};
