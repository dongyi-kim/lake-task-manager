// app-root.js — SPA 루트: 해시 라우팅(#/wbs #/vit #/workload) + nav + 산식 callout + 로그인 오버레이.
// <keep-alive> 로 뷰 상태(데이터·detail/activity 캐시·펼침) 보존 → 탭 전환 재fetch 없음. updated: 2026-07-09
import WorkloadView from "./views/WorkloadView.js";
import VitView from "./views/VitView.js";
import WbsView from "./views/WbsView.js";
import FormulaCallout from "./ui/FormulaCallout.js";
import LoginOverlay from "./ui/LoginOverlay.js";

const ROUTES = { wbs: WbsView, vit: VitView, workload: WorkloadView };
function currentRoute() { return location.hash.replace("#/", "") || "wbs"; }

export default {
  name: "AppRoot",
  components: { FormulaCallout, LoginOverlay },
  data() { return { route: currentRoute() }; },
  computed: { view() { return ROUTES[this.route] || ROUTES.wbs; } },
  mounted() {
    window.addEventListener("hashchange", () => { this.route = currentRoute(); });
  },
  template: `
    <div class="wrap">
      <header class="top">
        <h1>Lake Task Manager <span class="sub">Data Lake · PMO</span></h1>
      </header>
      <nav class="tabs">
        <a :class="{ on: route === 'wbs' }" href="#/wbs">WBS Dashboard</a>
        <a :class="{ on: route === 'vit' }" href="#/vit">현안 (PMO_VIT)</a>
        <a :class="{ on: route === 'workload' }" href="#/workload">인력 워크로드</a>
      </nav>
      <FormulaCallout :route="route" />
      <keep-alive><component :is="view"></component></keep-alive>
      <LoginOverlay />
    </div>`,
};
