// app-root.js — SPA 루트: 해시 라우팅(#/wbs #/vit #/workload) + nav + 산식 callout + 연결 안내.
// <keep-alive> 로 뷰 상태(데이터·detail/activity 캐시·펼침) 보존 → 탭 전환 재fetch 없음. updated: 2026-07-09
import MyTasksView from "./views/MyTasksView.js";
import WorkloadView from "./views/WorkloadView.js";
import VitView from "./views/VitView.js";
import WbsView from "./views/WbsView.js";
import HomeView from "./views/HomeView.js";
import AgentView from "./views/AgentView.js";
import DevToolsView from "./views/DevToolsView.js";
import ConfluenceView from "./views/ConfluenceView.js";
import FloatingRefresh from "./ui/FloatingRefresh.js";
import AddTicketFab from "./ui/AddTicketFab.js";
import FormulaCallout from "./ui/FormulaCallout.js";
import LoginOverlay from "./ui/LoginOverlay.js";
import StatusBanner from "./ui/StatusBanner.js";
import ToastStack from "./ui/ToastStack.js";
import ClipboardTicketPrompt from "./ui/ClipboardTicketPrompt.js";
import TicketMenu from "./ui/TicketMenu.js";
import TicketDialog from "./ui/TicketDialog.js";
import TransitionDialog from "./ui/TransitionDialog.js";
import SearchOverlay from "./ui/SearchOverlay.js";
import SettingsMenu from "./ui/SettingsMenu.js";
import GuideSpot from "./ui/GuideSpot.js";
import OpenItemTabs from "./ui/OpenItemTabs.js";
import { reactive } from "../vendor/vue.esm-browser.prod.js";
import { createOpenItemTabs, normalizeItemId } from "../lib/openItemTabs.js";
import { api, watchAuth } from "../lib/api.js";
import { confirmBox } from "../lib/confirm.js";
import { pushToast } from "../lib/toast.js";
import { installReferenceHover } from "../lib/referenceHover.js";
import { installAuthActivityProbe } from "../lib/authActivity.js";

// 메인 페이지는 **정보성 랜딩**(서비스 안내·릴리스 노트)이다 — 사용자 결정으로 복원.
// 에이전트는 #/ai 탭. 다만 홈에 심플한 입력창을 두어, 입력하면 AI 탭으로 넘어가
// 새 대화를 시작한다(입력 전달은 HomeView → sessionStorage → AgentView).
// #/guide 는 예전 주소를 살리는 별칭이다(북마크를 깨지 않는다).
const ROUTES = { home: HomeView, guide: HomeView, ai: AgentView, wbs: WbsView, vit: VitView,
                 workload: WorkloadView, mytasks: MyTasksView, devtools: DevToolsView, confluence: ConfluenceView };
// 로컬 앱의 모든 화면은 설치 사용자에게 열려 있다. Jira 권한은 실제 조회/쓰기에서 확인한다.
const TABS = [
  // ✨ 는 **AI 탭에만** 붙는다(사용자 요청) — 새로 생긴 기능이라 눈이 먼저 가야 한다.
  //    라벨 문자열에 이모지를 섞지 않고 플래그로 두는 이유: 라벨은 라우팅·검색·
  //    접근성 텍스트에도 쓰여서, 거기까지 이모지가 따라가면 안 된다.
  { k: "ai", label: "AI", spark: true },
  { k: "wbs", label: "WBS Dashboard" },
  { k: "vit", label: "현안 (PMO_VIT)" },
  { k: "workload", label: "인력 워크로드" },
  { k: "mytasks", label: "Task" },
  { k: "confluence", label: "Confluence" },
];
function currentRoute() { return location.hash.replace("#/", "").split(/[/?]/)[0] || "home"; }
// /browse/DL-1234 — 그 티켓만의 단독 페이지("새 창에서 열기" 대상).
// Jira 와 같은 URL 형태라 주소만 보고도 어느 티켓인지 안다.
function ticketOf() {
  const m = /^\/browse\/([^/?#]+)/.exec(location.pathname);
  return m ? decodeURIComponent(m[1]) : null;
}

export default {
  name: "AppRoot",
  components: { FormulaCallout, LoginOverlay, StatusBanner, ToastStack, ClipboardTicketPrompt, TicketMenu, TicketDialog, TransitionDialog, SearchOverlay, SettingsMenu, FloatingRefresh, AddTicketFab, GuideSpot, OpenItemTabs },
  // Health reads local configuration; Jira authentication never gates local navigation/drafts.
  data() { return { route: currentRoute(), theme: document.documentElement.getAttribute("data-theme") || "light",
                    ready: false, needLogin: false, ticketKey: null, searchOpen: false, clipboardJiraBase: "",
                    ticketTabs: createOpenItemTabs({ kind: "task", reactive }), ticketNavigationBusy: false,
                    // 배포 repo 업데이트 가능 여부(null=아직 모름). updating=재시작 트리거 후.
                    update: null, updating: false,
                    // 하위 상태변경 후처리: 부모 전이에 필수 입력이 있으면 이 다이얼로그로 채운다.
                    cascadeTrx: null,
                    ticketKeyFromPath: ticketOf() }; },
  computed: {
    tabs() { return TABS; },
    view() { return ROUTES[this.route] || ROUTES.home; },
    pageTicket() { return this.ticketKeyFromPath; },
    ticketTabScroll() { return this.ticketTabs.state.items.find(item => item.id === this.ticketKey)?.scroll || { x: 0, y: 0 }; },
  },
  mounted() {
    // 티켓 링크·뱃지와 사람 멘션은 화면별 구현 대신 앱 전체가 같은 상세 호버를 사용한다.
    installReferenceHover();
    // 5분 이상 유휴 후 포커스/가시성/첫 입력이 돌아오면, 사용자의 첫 쓰기보다 먼저 Jira
    // 세션을 뒤에서 확인한다. 정상 화면은 기다리지 않으며 중복 이벤트는 모듈이 합친다.
    this._stopAuthActivity = installAuthActivityProbe(api);
    window.addEventListener("pagehide", this._ticketPageHide = () => this.persistTicketScroll());
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
    // 티켓 추가(fab) 등에서 만든 새 티켓을 바로 연다.
    window.addEventListener("lake-open-ticket", (e) => {
      const k = e && e.detail && e.detail.key; if (k) this.requestTicket(k);
    });
    // 하위 티켓 상태변경/생성의 **후처리** — 부모 상태 규칙을 촉발하면 '상위도 바꿀까?' 물어본다.
    window.addEventListener("cascade-prompt", (e) => { if (e && e.detail) this.onCascade(e.detail); });
    window.addEventListener("need-login", () => {
      this.needLogin = true; // connection notice only; never replace the active view
    });
    // 인증 복구는 현재 화면과 초안을 유지한 채 데이터 갱신만 재개한다.
    window.addEventListener("auth-ok", () => {
      this.needLogin = false; this.ready = true;
      api.warmGlobals();       // 어느 탭의 본 데이터도 기다리지 않고 FieldEdit 기본목록 준비
    });
    // 티켓 링크(.tkt[data-key]) 위임 처리 — 어느 화면/코멘트에서 눌러도 인앱 다이얼로그로 연다.
    document.addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest(".tkt[data-key]");
      if (!a) return;
      e.preventDefault();
      this.requestTicket(a.getAttribute("data-key"));
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const a = e.target.closest && e.target.closest(".tkt[data-key]");
      if (!a) return;
      e.preventDefault();
      this.requestTicket(a.getAttribute("data-key"));
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
      this.clipboardJiraBase = String((h && h.jiraBase) || "");
      this.ticketTabs.setContext({ env: h?.env, jiraBase: h?.jiraBase, projectKey: h?.projectKey });
      this.needLogin = !!(h && h.needLogin);
      // The nonblocking connection notice performs silent recovery; observe its completion.
      if (this.needLogin) watchAuth();
      else api.warmGlobals();  // Task/티켓 로딩이 느려도 FieldEdit 추천은 독립적으로 데운다.
    }).catch(() => {}).finally(() => { this.ready = true; });
    // 업데이트(배포 repo 최신 여부) — 시작 시 + 주기적으로 확인. 실패는 조용히(표시만 하는 기능).
    this.checkUpdate();
    setInterval(() => this.checkUpdate(), 30 * 60 * 1000);
  },
  unmounted() {
    if (this._stopAuthActivity) this._stopAuthActivity();
    clearTimeout(this._ticketScrollTimer);
    window.removeEventListener("pagehide", this._ticketPageHide);
  },
  methods: {
    persistTicketScroll() {
      clearTimeout(this._ticketScrollTimer);
      const dialog = this.$refs.activeTicketDialog;
      if (this.ticketKey && dialog) this.ticketTabs.setScroll(this.ticketKey, dialog.viewScroll());
    },
    onTicketScroll(value) {
      if (!value?.key || value.key !== this.ticketKey) return;
      clearTimeout(this._ticketScrollTimer);
      this._ticketScrollTimer = setTimeout(() => {
        if (value.key === this.ticketKey) this.ticketTabs.setScroll(value.key, value.scroll);
      }, 150);
    },
    focusTicketReturn() {
      if (this.ticketKey) {
        if (this.$refs.modalTicketTabs) this.$refs.modalTicketTabs.focusTab(this.ticketKey);
        else this.$refs.activeTicketDialog?.$el.querySelector('.tkt-bar button, .tkt-bar a')?.focus();
      } else if (this.$refs.taskTicketTabs && this.ticketTabs.state.activeId)
        this.$refs.taskTicketTabs.focusTab(this.ticketTabs.state.activeId);
      else if (this._ticketReturnFocus?.isConnected) this._ticketReturnFocus.focus();
      else this.$el.querySelector('.mytasks .mt-card[tabindex="0"], .top .tabs a')?.focus();
    },
    async requestTicket(value, options = {}) {
      const key = value == null ? null : normalizeItemId("task", value);
      if (value != null && !key) return false;
      if (this.ticketNavigationBusy) return false;
      if (key === this.ticketKey && !options.remove) {
        if (options.fromSearch) {
          this.searchOpen = false;
          this.$nextTick(() => this.focusTicketReturn());
        }
        return true;
      }
      if (this.cascadeTrx) {
        pushToast("열려 있는 전이 창에서 작업을 마친 뒤 다시 선택하세요.", "warn"); return false;
      }
      const focused = document.activeElement;
      let completed = false;
      this.ticketNavigationBusy = true;
      const previous = this.ticketKey, dialog = this.$refs.activeTicketDialog;
      if (!previous && key) this._ticketReturnFocus = document.activeElement;
      try {
        if (dialog && !(await dialog.prepareToLeave())) return false;
        if (this.ticketKey !== previous) return false;
        this.persistTicketScroll();
        if (options.remove) {
          this.ticketTabs.close(options.remove);
          this.ticketKey = previous ? this.ticketTabs.state.activeId : null;
        } else if (key) {
          this.ticketTabs.open({ id: key });
          this.ticketKey = key;
        } else this.ticketKey = null; // Closing the modal never removes metadata or a scoped draft.
        if (options.fromSearch) this.searchOpen = false;
        await this.$nextTick();
        this.ticketNavigationBusy = false;
        await this.$nextTick(); // The shared tab buttons must be enabled before focus().
        this.focusTicketReturn();
        completed = true;
        return true;
      } catch (error) {
        pushToast({ kind: "error", title: "티켓 전환 실패", message: error?.message || "현재 작성 내용을 확인하세요." });
        return false;
      } finally {
        this.ticketNavigationBusy = false;
        if (!completed) {
          await this.$nextTick();
          if (focused?.isConnected && focused !== document.body) focused.focus();
          else this.focusTicketReturn();
        }
      }
    },
    closeTicketTab(value) {
      const key = normalizeItemId("task", value);
      if (!key || this.ticketNavigationBusy) return false;
      if (key === this.ticketKey) return this.requestTicket(null, { remove: key });
      // Inactive metadata has no mounted body to flush. Closing it does not delete its drafts.
      const result = this.ticketTabs.close(key);
      this.$nextTick(() => this.focusTicketReturn());
      return result;
    },
    onTicketMetadata(value) {
      if (value?.key === this.ticketKey && typeof value.title === "string")
        this.ticketTabs.update(value.key, { title: value.title });
    },
    /** 하위 상태변경 후처리 — 부모도 바꿀지 확인하고, [예]면 기존 전이 경로로 상위를 전이한다.
     *  필수 입력이 있는 전이면(needsScreen) 부모 전이 다이얼로그를 열어 사용자가 채우게 한다.
     *  상위 전이가 또 조상 규칙을 촉발하면 응답의 cascade 로 **연쇄**된다. */
    async onCascade(c) {
      if (!c || !c.parentKey || !c.transition) return;
      const t = c.transition;
      const ok = await confirmBox(this.cascadeMsg(c),
        { okLabel: (t.to || "변경") + " 로 변경", cancelLabel: "아니오",
          ticket: { key: c.parentKey, summary: c.parentSummary, type: c.parentType, assignee: c.parentAssignee } });
      if (!ok) return;
      if (t.needsScreen || t.toCategory === "done") {
        this.cascadeTrx = { ticket: c.parentKey, transition: t }; return;
      }
      try {
        const r = await api.doTransition(c.parentKey, {
          id: t.id, targetStatusId: t.toId || "", targetStatusName: t.to || "",
          targetStatusCategory: t.toCategory || "",
        });
        if (r && r.ok === false) throw new Error(r.error || "전이에 실패했습니다.");
        pushToast({ kind: "success", title: c.parentKey + " → " + (t.to || "전이"), timeout: 4000 });
        window.dispatchEvent(new CustomEvent("ticket-changed", { detail: { key: c.parentKey } }));
        if (r && r.cascade) this.onCascade(r.cascade);   // 조부모까지 연쇄
      } catch (e) {
        pushToast({ kind: "error", title: "상위 전이 실패", message: (e && e.message) || "", timeout: 6000 });
      }
    },
    cascadeMsg(c) {
      // 대상 티켓은 본문에 텍스트로 언급하지 않는다 — 아래 카드 행(confirmBox opts.ticket)이 보여준다.
      const to = c.transition.to || "";
      if (c.rule === "done") return "모든 하위 작업이 완료됐습니다.\n아래 상위 Task 도 " + to + " (으)로 완료 처리할까요?";
      if (c.rule === "inprogress") return "하위 작업이 진행 중으로 바뀌었습니다.\n아래 상위 Task 도 진행중(" + to + ")으로 바꿀까요?";
      return "완료된 상위 Task 에 미완료/새 하위가 생겼습니다.\n아래 상위 Task 를 " + to + " (으)로 되돌릴까요?";
    },
    onCascadeDone() {
      const k = this.cascadeTrx && this.cascadeTrx.ticket;
      this.cascadeTrx = null;
      if (k) window.dispatchEvent(new CustomEvent("ticket-changed", { detail: { key: k } }));
    },
    checkUpdate() {
      api.updateInfo().then((u) => { this.update = u || null; }).catch(() => {});
    },
    async doUpdate() {
      if (this.updating || !(this.update && this.update.available)) return;
      // 배포는 릴리즈 태그 단위 — '몇 커밋 뒤처짐' 이 아니라 **어느 버전으로 가는지**를 보여 준다.
      const to = this.update.latest || "";
      const ok = await confirmBox(
        "새 버전이 있습니다" + (to ? " (" + to + ")" : "") + ".\n"
        + "지금 받아서 앱을 재시작할까요? 잠시 창이 닫혔다가 다시 열립니다.",
        { okLabel: "업데이트 후 재시작", cancelLabel: "나중에" });
      if (!ok) return;
      this.updating = true;
      try {
        const r = await api.updateRestart();
        if (r && r.action === "restart") {
          pushToast({ kind: "info", icon: "⬆", title: "업데이트를 받아 재시작합니다",
                      message: "잠시 후 앱이 다시 열립니다.", timeout: 0, key: "update" });
        } else {
          // 트레이 모드가 아니면 스스로 재시작 못 함 — 수동 안내.
          this.updating = false;
          pushToast({ kind: "info", title: "수동 업데이트가 필요합니다",
                      message: "앱을 닫고 run.bat 을 다시 실행하세요.", timeout: 8000, key: "update" });
        }
      } catch (e) {
        this.updating = false;
        pushToast({ kind: "error", title: "업데이트 시작 실패",
                    message: (e && e.message) || "", timeout: 6000, key: "update" });
      }
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", this.theme);
      try { localStorage.setItem("theme", this.theme); } catch (e) {}
    },
  },
  template: `
    <div class="wrap" :class="{ 'wrap-bare': pageTicket, 'wrap-confluence': route === 'confluence' && !pageTicket }">
      <header v-if="!pageTicket" class="top">
        <a href="#/home" class="nav-home" title="홈으로">
          <img class="nav-logo" src="icon.png" alt="Lake Task Manager" />
        </a>
        <nav class="tabs">
          <a v-for="t in tabs" :key="t.k" :class="{ on: route === t.k }"
             :href="'#/' + t.k" @focus="$event.currentTarget.scrollIntoView({ block: 'nearest', inline: 'nearest' })">{{ t.label }}<span v-if="t.spark" class="tab-spark"
             aria-hidden="true">✨</span></a>
        </nav>
        <div class="top-actions">
          <button v-if="update && update.available" class="update-trig" :class="{ busy: updating }"
                  @click="doUpdate" :disabled="updating"
                  :title="'새 버전 ' + (update.latest || '') + ' — 클릭해 업데이트 후 재시작 (현재 ' + (update.current || '?') + ')'">
            <span class="update-dot"></span>
            <span>{{ updating ? '업데이트 중…' : '업데이트' }}</span>
          </button>
          <button class="search-trig" @click="searchOpen = true" title="통합 검색 ( / )">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
            <span>검색</span><kbd>/</kbd>
          </button>
          <SettingsMenu :theme="theme" @toggle-theme="toggleTheme" />
        </div>
      </header>
      <div v-if="!ready" class="loading page">인증 확인 중…</div>
      <!-- Jira 세션은 개별 데이터 요청에서 처리한다. 로컬 화면과 초안은 항상 열린다. -->
      <TicketDialog v-else-if="pageTicket" :key="pageTicket"
                    :key-id="pageTicket" mode="page" :theme="theme"
                    @search="searchOpen = true" @toggle-theme="toggleTheme" />
      <template v-else>
        <StatusBanner />
        <FormulaCallout :route="route" />
        <OpenItemTabs v-if="route === 'mytasks' && !ticketKey && ticketTabs.state.items.length"
                      ref="taskTicketTabs" kind="task" label="최근 열린 티켓"
                      :items="ticketTabs.state.items" :active-id="ticketTabs.state.activeId"
                      :disabled="ticketNavigationBusy" @activate="requestTicket" @close="closeTicketTab" />
        <keep-alive><component :is="view"></component></keep-alive>
        <!-- 좌하단 플로팅 강제 새로고침 — 데이터 화면에서만(개발 도구·티켓 단독 페이지 제외) -->
        <FloatingRefresh v-if="route !== 'devtools' && route !== 'confluence'" />
      </template>
      <TicketMenu />
      <LoginOverlay />
      <!-- 우하단 알림 스택 — 인증·다운로드 등 주요 알림이 쌓였다 사라진다(항상 존재) -->
      <ToastStack />
      <ClipboardTicketPrompt v-if="ready && !needLogin" :jira-base="clipboardJiraBase"
                             :open-keys="[ticketKey, pageTicket]" />
      <!-- 좌하단 '+' 티켓 추가(공통) — 로그인·devtools 제외하고 어디서든 -->
      <AddTicketFab v-if="ready && route !== 'devtools' && route !== 'confluence'" />
      <!-- 기능 안내(한 번만) — 목록은 lib/guides.js. 다이얼로그가 떠 있으면 띄우지 않는다:
           가리킬 버튼이 그 뒤에 가려져 무엇을 말하는지 알 수 없게 된다. -->
      <GuideSpot v-if="ready && !needLogin && !ticketKey && !pageTicket" :route="route" />
      <TicketDialog v-if="ticketKey" :key="ticketKey" ref="activeTicketDialog" :key-id="ticketKey"
                    :initial-scroll="ticketTabScroll" @metadata="onTicketMetadata" @view-scroll="onTicketScroll"
                    @close="requestTicket(null)">
        <template #tabs>
          <OpenItemTabs ref="modalTicketTabs" kind="task" label="최근 열린 티켓"
                        :items="ticketTabs.state.items" :active-id="ticketKey" :disabled="ticketNavigationBusy"
                        @activate="requestTicket" @close="closeTicketTab" />
        </template>
      </TicketDialog>
      <!-- 하위 상태변경 후처리: 상위 전이에 필수 입력이 있을 때만 뜨는 부모 전이 다이얼로그 -->
      <TransitionDialog v-if="cascadeTrx" :ticket="cascadeTrx.ticket" :transition="cascadeTrx.transition"
                        @close="cascadeTrx = null" @done="onCascadeDone()" />
      <!-- keep-alive: 같은 창에서 다시 열면 마지막 검색어·결과가 그대로 남는다 -->
      <keep-alive>
        <SearchOverlay v-if="searchOpen" @close="searchOpen = false"
                       @open-ticket="(k) => requestTicket(k, { fromSearch: true })" />
      </keep-alive>
    </div>`,
};
