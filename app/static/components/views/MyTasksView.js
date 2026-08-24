// MyTasksView.js — '내 Task'.
//
// 백엔드는 **사실 하나**(내 실행 원자 + 부모/동료/Epic 맥락)만 주고, 배치는 전부 여기서 한다.
// 상단 옵션 패널이 세 축을 정한다:
//
//  1) 상태 축   **폭이 정한다(옵션 아님)**. 3칼럼이 들어가면 가로축, 안 들어가면 세로축.
//               가로 = 할당됨/진행중/최근완료가 세로로 긴 칼럼(칸반). 티켓은 1차원 리스트.
//               세로 = 상태가 가로로 꽉 찬 패널로 쌓임. 티켓은 그리드.
//               ★ 그룹은 늘 상태의 **반대 축**에 놓인다 — 가로 모드면 그룹이 위아래로 쌓이고,
//                 세로 모드면 상태 패널 안에서 그룹이 좌우로 늘어선다.
//               ★ 두 모드 모두 상태를 **접을 수 있고**, 접힘 상태(bandClosed)를 공유한다 —
//                 창을 줄였다고 접어 둔 게 펴지면 그건 같은 화면이 아니다.
//  2) 그룹화    없음     = 모든 Task/Sub-Task가 개별 카드
//               Sub Task = 같은 부모 Task로 묶고 그 안에 Sub-Task. 하위가 없는 Task는
//                          묶을 게 없으므로 그룹이 아니라 그냥 카드다.
//  3) 유관 보기 그룹 안에서 담당이 '나'인 티켓을 강조해 먼저 보여주고,
//               확장 버튼을 누르면 내가 담당이 아닌 티켓(동료 몫)도 함께 보여준다.
//
// 카드 모양은 배치와 무관하게 하나다(.mt-card) — 리스트든 그리드든 같은 것을 읽는다.
import { api } from "../../lib/api.js";
import TypeBadge from "../ui/TypeBadge.js";
import Avatar from "../ui/Avatar.js";
import PriIcon from "../ui/PriIcon.js";
import TaskCard, { isHot, isUrgent } from "../ui/TaskCard.js";
import DueText from "../ui/DueText.js";
import FieldEdit from "../ui/FieldEdit.js";
import AdvancedSearchDialog from "../ui/AdvancedSearchDialog.js";
import TransitionDialog from "../ui/TransitionDialog.js";
import { categoryColor } from "../../lib/colors.js";
import { pushToast } from "../../lib/toast.js";
import { vocBadgeSegs, vocStripTitle } from "../../lib/voc.js";
import { confirmDoneDespiteOpenSubs } from "../../lib/doneGuard.js";

const NO_DUE = 1e6;


// 상태 축 — 순서가 곧 작업 흐름이다.
const STATES = [
  { k: "todo", label: "할당됨", drop: "작업 대기" },
  { k: "inprogress", label: "진행 중", drop: "진행 중" },
  { k: "done", label: "최근 완료", drop: "완료" },
];
const STATE_KEYS = new Set(STATES.map((st) => st.k));

/** 모든 Sub-Task 가 같은 상태 Category 면 그 상태를 돌려준다.
 *  빈 그룹이나 둘 이상의 상태가 섞인 그룹은 기존 3컬럼 UI 를 써야 하므로 null 이다. */
export function uniformStatusCategory(cards) {
  let only = null;
  for (const card of cards || []) {
    // byState 와 같은 폴백: 알 수 없는 Category 는 할당됨 칸에 놓인다.
    const status = STATE_KEYS.has(card.statusCategory) ? card.statusCategory : "todo";
    if (only !== null && only !== status) return null;
    only = status;
  }
  return only;
}
// Epic 시그니처 컬러 — 같은 Epic 은 어느 화면·어느 카드에서도 같은 색.
// 사용자 VoC 는 Epic 이 없어도 **전용 Epic 처럼** 자기 색을 갖는다(Epic 이 배정돼 있으면 그쪽 우선).
// Epic 도 VoC 도 아니면 색을 주지 않는다 — 없는 소속을 색으로 지어내지 않는다.
const VOC_SIG = "var(--ty-story)";
function epicSig(card) {
  if (card.epicKey) return categoryColor(card.epicKey);
  if (card.voc) return VOC_SIG;
  return null;
}

/** Task with SubTask 공용 하단바.
 *  3축 그룹 카드와 1축 단독 Task 흐름이 같은 접기 UI·진척 표시를 공유한다. */
const SubtaskFoldBar = {
  name: "SubtaskFoldBar",
  components: { Avatar },
  props: {
    panel: { type: Object, required: true },
    closed: { type: Boolean, default: false },
  },
  emits: ["toggle"],
  computed: {
    done() { return Math.max(0, Number(this.panel?.group?.kidsDone) || 0); },
    total() { return Math.max(0, Number(this.panel?.group?.kidsTotal) || 0); },
    pct() {
      const value = Number(this.panel?.group?.pct);
      if (Number.isFinite(value)) return Math.max(0, Math.min(100, value));
      return this.total ? Math.round(this.done * 100 / this.total) : 0;
    },
    assignees() { return this.panel?.assignees || []; },
    pending() { return !!this.panel?.group?.childrenPending; },
  },
  template: `
    <button type="button" class="mt-subfoot" :class="{ open: !closed, pending }"
            :aria-expanded="!closed" :title="closed ? 'SubTask 펼치기' : 'SubTask 접기'"
            @click.stop="$emit('toggle')">
      <span class="mt-subfoot-toggle" :class="{ open: !closed }" aria-hidden="true">▸</span>
      <span class="mt-subfoot-label"><strong>{{ total }}</strong> Subtasks</span>
      <span v-if="assignees.length" class="mt-subfoot-sep" aria-hidden="true"></span>
      <span v-if="assignees.length" class="mt-subfoot-owners">
        <span v-for="owner in assignees" :key="owner.id || owner.name" class="mt-subfoot-owner"
              :title="owner.name">
          <Avatar :user="owner.id" :name="owner.name" :size="16" />
          <span>{{ owner.name }}</span>
        </span>
      </span>
      <span class="mt-subfoot-sep mt-subfoot-progress-sep" aria-hidden="true"></span>
      <span v-if="pending" class="mt-subfoot-sync"><i aria-hidden="true"></i>동기화 중</span>
      <span v-else class="mt-subfoot-progress">
        <span class="mt-pbar" role="progressbar" :aria-valuenow="done" aria-valuemin="0"
              :aria-valuemax="total" :aria-label="done + ' / ' + total + ' SubTask 완료'"
              :title="done + ' / ' + total + ' 완료'">
          <i :style="{ width: pct + '%' }"></i>
        </span>
        <em>{{ done }} / {{ total }}</em>
      </span>
    </button>`,
};

// 옵션 정의 — 라벨/설명이 한곳에 있어야 콤보박스와 저장 키가 어긋나지 않는다.
// reload: true 면 서버 질의 조건이라 값이 바뀌면 다시 받아야 한다(클라이언트에서 거를 수 없다).
// 상태 축은 **옵션이 아니다**. 3칼럼이 들어가면 가로축, 안 들어가면 세로축 — 화면 폭이
// 답을 정해 놓았는데 사람에게 고르라고 하면, 좁은 화면에서 가로축을 골라 글자가 두 자씩
// 끊기는 칼럼을 보게 된다. 고를 수 있다고 더 나은 게 아니다.
// 한 상태 칸에 우선 보여 줄 하위 개수. **어느 한 칸이라도** 이 수를 넘으면 그 Task 는
// 접기 대상이 되고, 그때부터 모든 칸이 같은 규칙으로 잘린다.
const SUB_CAP = 5;
const NARROW = "(max-width: 900px)";      // 이 아래로는 3칼럼이 성립하지 않는다(CSS 도 같은 값)

const OPTIONS = [
  { key: "groupBy", label: "그룹화", opts: [
    { k: "none", label: "없음", hint: "모든 티켓을 개별 카드로" },
    { k: "sub", label: "Sub Task", hint: "부모 Task 로 묶고 그 안에 Sub-Task — 하위가 없는 Task 는 그냥 카드" }] },
  { key: "subView", label: "Sub Task 보기", opts: [
    { k: "collapsed", label: "모두 접기", hint: "하위를 모두 접는다 — 부모 Task 만 본다" },
    { k: "mine", label: "내 티켓만", hint: "하위 중 내가 담당인 것만 펼친다" },
    { k: "all", label: "모든 티켓", hint: "동료가 담당인 하위(유관)까지 모두 펼친다" }] },
  { key: "sort", label: "정렬", opts: [
    { k: "due", label: "마감", hint: "1차 마감 → 2차 우선순위" },
    { k: "pri", label: "우선순위", hint: "1차 우선순위 → 2차 마감" },
    { k: "epic", label: "소속 Epic", hint: "Epic 으로 모으고 그 안에서 우선순위 → 마감" }] },
];

// 컬럼(상태) 제목에 붙는 **표시 범위** — 그 칸에만 걸리는 조건이라 그 칸의 제목에 둔다.
// 아래 플로팅 바에 두면 '지금 무엇이 걸러진 목록인가' 를 화면 반대편에서 읽어야 했다.
const BAND_FILTERS = {
  todo: { key: "openFilter", opts: [
    { k: "all", label: "모두", hint: "담당된 모든 미착수 티켓" },
    { k: "2w", label: "2주 내 갱신", hint: "최근 2주 안에 갱신된 것만 — 오래 방치된 건 감춘다" }] },
  inprogress: { key: "progFilter", opts: [
    { k: "1m", label: "1달 내 갱신", hint: "최근 1달 안에 손댄 것만 — 오래 멈춘 진행 중은 감춘다" },
    { k: "all", label: "모두", hint: "진행 중인 모든 티켓" }] },
  done: { key: "doneFilter", opts: [
    { k: "1w", label: "1주", hint: "최근 1주 안에 완료" },
    { k: "1m", label: "1달", hint: "최근 1달 안에 완료" }] },
};
const PREF_KEY = "mytasks.opts";

export default {
  name: "MyTasksView",
  components: { TypeBadge, Avatar, TaskCard, PriIcon, DueText, FieldEdit, AdvancedSearchDialog, TransitionDialog,
                SubtaskFoldBar },
  data() {
    return {
      model: null, loading: false, err: "",
      streamAxes: {
        todo: { state: "loading", chunks: 0 },
        inprogress: { state: "loading", chunks: 0 },
        done: { state: "loading", chunks: 0 },
      },
      streamProgress: { done: 0, total: 0 },
      // 카드 드래그 상태변경 — 드래그 중이면 {key,title,cat,x,y,zone}. zone = 커서 아래 드랍영역(상태 k | null).
      drag: null,
      dragTrx: null,        // 드랍한 전이에 필수 입력이 있으면 TransitionDialog 로 채운다 {ticket, transition}
      // 폭이 정한다(사용자 선택 아님). 좁으면 v(세로) — 화면이 답을 알고 있다.
      axis: window.matchMedia(NARROW).matches ? "v" : "h",
      groupBy: "sub",       // none | sub (부모 Task 로 묶기)
      // 하위(Sub-Task) 보기 — **화면 전체에 하나**. collapsed | mine | all.
      // 전에는 그룹마다 버튼을 달아 개별로 바꿨는데, 그룹이 열 개면 열 번 눌러야 원하는 상태가 되고
      // 지금 무엇이 펼쳐져 있는지도 카드마다 달라 한눈에 안 잡혔다. 보기 방식은 화면의 성격이지
      // 그룹 하나하나의 속성이 아니다.
      subView: "mine",
      bandClosed: {},       // 세로축 모드에서 접어 둔 상태 밴드 { todo|inprogress|done: true }
      // 상단 퀵필터 — 세 세그먼트(담당자/보고자/모듈) + 고급 검색. 각 세그먼트는 [버튼][우측 선택]
      // 으로, 버튼이 그 스코프를 켜고 우측 선택(사람 picker / 모듈 콤보)이 대상을 정한다. Default = '나'.
      scope: "assignee",    // assignee | reporter | module | jql
      moduleSel: "",        // 선택한 모듈명("" = 내 모듈 전체) — 포커스 떠나도 유지
      assigneeSel: null,    // {id, name}  null = 나
      reporterSel: null,    // {id, name}  null = 나
      jqlText: "",          // 고급 검색 JQL(직접 입력 또는 빌더가 채움)
      advOpen: false,       // 고급 검색 다이얼로그 열림
      me: null,             // /api/me — modules(내 모듈)·allModules·manager
      gClosed: {},          // Task+SubTask 그룹 개별 접힘 { groupKey: true }
      openFilter: "all",    // 할당됨 축: all | 2w   (서버 질의 조건)
      progFilter: "all",    // 진행 중 축: all | 1m  (기본 모두 — 지금 하는 일을 숨기지 않는다)
      doneFilter: "1w",     // 완료 축 기간: 1w | 1m (서버 질의 조건)
      sort: "due",
      busy: false,
      // Epic 필터(하단 콤보) — **가릴 버킷**만 담는다(비어 있으면 전부 표시). 새로 나타난 Epic 은
      // 여기 없으니 기본 표시된다. 버킷 키 = Epic 키, 소속 없음은 "__none__".
      epicHidden: {},
      epicOpen: false,
      // Project 필터 — jira.yml search 에 등록된 프로젝트는 **기본 체크**, 그 외는 **기본 언체크**.
      // projPref[proj] = true(보임)|false(숨김) 은 사용자가 명시적으로 토글한 것만 담는다(없으면 기본 규칙).
      projPref: {},
      projOpen: false,
      // 수정으로 **현재 퀵필터에서 이탈**한 티켓 키 — 네트워크 재조회 없이 즉시 숨긴다(다음 실 로딩에 초기화).
      excluded: {},
      // Task+SubTask 그룹별 '완료 하위 더 보기' 펼침 { groupKey: true } — 일시 상태(저장 안 함).
      subOpen: {},          // Task 별 하위 펼침(일시 상태 — 화면을 떠나면 접힌다)
    };
  },
  mounted() {
    this.loadPrefs();
    // 모듈 필터 셀렉터용 — 내 모듈/전체 모듈. 태스크 로딩과 병렬(부팅 안 막음).
    api.me().then((me) => { this.me = me || null; }).catch(() => {});
    this.load();
    // 창 크기가 바뀌면 축도 따라간다(리사이즈·모니터 전환·창 분할).
    // 상태 전이 등으로 티켓이 바뀌면 **이 뷰만** 조용히 다시 받는다. 카드가 새 상태의 열로
    // 알아서 옮겨 간다(상태·담당·해결이 한꺼번에 바뀌므로 카드 하나만 손대는 것보다 안전하고,
    // 화면을 다시 그리는 것보다 가볍다 — 스크롤·펼침·옵션이 그대로 남는다).
    // 티켓 수정 알림. 담당/보고/모듈 퀵필터는 **네트워크 없이** 이 티켓이 필터에서 빠지는지
    // 판정해 즉시 숨기고(+우하단 토스트) 로컬 카드도 갱신한다. 고급검색(jql)만 서버 재조회로
    // 사라진 것을 찾아 토스트한다(그쪽은 클라가 조건을 알 수 없다).
    this._onChanged = (e) => {
      const view = (e && e.detail && e.detail.view) || null;
      // 바뀐 필드는 **즉시** 화면에 반영한다(상태·담당이 눈앞에서 바뀌게).
      if (view) this._applyEditLocally(view);
      // ★ 목록에서 빠질지는 **서버가 판정한다.** 예전엔 클라가 흉내 냈는데, 이 목록에 있던
      //   이유가 그 티켓의 필드만으로는 알 수 없는 경우가 있다 — 이를테면 부모 Task 는
      //   '내가 하위를 담당해서' 걸려 있는데, 부모 담당자를 같은 모듈 다른 사람으로 바꾸면
      //   그 필드만 보고 '이탈' 로 단정해 **부모와 하위가 통째로 사라졌다**(리포트된 버그).
      //   순서만 바뀌면 될 일에 티켓을 잃는 쪽이 훨씬 나쁘다 — 판정은 목록을 만든 쪽에 맡긴다.
      const before = new Set(this.rawCards.map((c) => c.key));
      this._dropModelCache();
      this.load({ quiet: true }).then(() => {
        const after = new Set(this.rawCards.map((c) => c.key));
        const gone = [...before].filter((k) => !after.has(k));
        if (gone.length) this._toastExcluded(gone);
      });
    };
    window.addEventListener("ticket-changed", this._onChanged);
    // 좌하단 플로팅 새로고침
    window.addEventListener("force-refresh", this._fr = async () => {
      try { await this.hardRefresh(); }
      finally { window.dispatchEvent(new CustomEvent("force-refresh-done")); }
    });
    // 재인증(auth-ok) 후 — 세션이 끊긴 채 실패했던 조회를 다시 받는다(그대로 두면 '목록 없음'
    // 으로 굳어 새로고침해야만 떴다). 서버 캐시는 안 비운다(가벼운 재조회).
    window.addEventListener("auth-ok", this._authok = () => { this.load(); });
    this._mq = window.matchMedia(NARROW);
    this._onMq = (e) => { this.axis = e.matches ? "v" : "h"; };
    this._mq.addEventListener ? this._mq.addEventListener("change", this._onMq)
                              : this._mq.addListener(this._onMq);
    this._bindDrag();
  },
  unmounted() {
    if (this._streamAbort) this._streamAbort.abort();
    if (this._epicMetaTimer) clearTimeout(this._epicMetaTimer);
    window.removeEventListener("ticket-changed", this._onChanged);
    window.removeEventListener("force-refresh", this._fr);
    window.removeEventListener("auth-ok", this._authok);
    this._unbindDrag && this._unbindDrag();
    if (!this._mq) return;
    this._mq.removeEventListener ? this._mq.removeEventListener("change", this._onMq)
                                 : this._mq.removeListener(this._onMq);
  },
  computed: {
    groups() { return (this.model && this.model.groups) || []; },
    epicMap() {
      const m = {};
      for (const e of (this.model && this.model.epics) || []) m[e.key] = e;
      return m;
    },
    states() { return STATES; },
    options() { return OPTIONS; },
    // ── 상단 퀵필터 ──
    myModules() { return (this.me && this.me.modules) || []; },
    otherModules() {
      const mine = new Set(this.myModules);
      return ((this.me && this.me.allModules) || []).filter((m) => !mine.has(m));
    },
    /** 세션 사용자 사번 — 담당자/보고자 picker 에서 '나' 판정용. */
    myId() { return (this.me && (this.me.id || this.me.name)) || ""; },
    /** 서버로 보낼 scope 문자열. 담당자/보고자는 대상이 '나'면 축약 스코프(assignee/reporter)로
     *  보내 currentUser 와 **같은 캐시 키**에 모이게 한다(특정 사람이면 assignee:<사번>). */
    apiScope() {
      if (this.scope === "module") return this.moduleSel ? "module:" + this.moduleSel : "mymodules";
      if (this.scope === "reporter") return this.reporterSel ? "reporter:" + this.reporterSel.id : "reporter";
      if (this.scope === "jql") return this.jqlText.trim() ? "jql:" + this.jqlText.trim() : "assignee";
      return this.assigneeSel ? "assignee:" + this.assigneeSel.id : "assignee";
    },
    /** 상태 열 폭 — 접힌 열은 좁은 레일만 남긴다(사라지면 되펼 자리가 없다).
     *  헤더 줄과 모든 그룹 본문이 같은 변수를 쓰므로 칼럼이 통째로 함께 움직인다. */
    gridCols() {
      return this.states.map((st) => (this.bandOpen(st.k) ? "minmax(0, 1fr)" : "34px")).join(" ");
    },
    doneDays() { return (this.model && this.model.doneWindowDays) || 7; },

    /** 모든 카드(내 것 + 유관) — Epic 필터 **적용 전** 평면 목록. 필터 옵션 목록은 이걸 본다. */
    rawCards() {
      const out = [];
      for (const g of this.groups) {
        for (const a of g.atoms) out.push(this.card(a, g, true));
        for (const o of g.others) out.push(this.card(o, g, false));
      }
      return out;
    },
    /** Epic·Project 필터를 적용한 카드 — 배치·집계는 모두 이걸 쓴다(가린 것은 개수에서도 빠진다). */
    allCards() { return this.rawCards.filter((c) => this.epicPass(c) && this.projPass(c) && !this.excluded[c.key]); },
    // ── Project 필터 ── (jira.yml search 등록=기본 보임 / 미등록=기본 숨김, 사용자 토글 가능)
    searchProjects() { return (this.me && this.me.searchProjects) || []; },
    /** 내 Task 에 실제로 있는 프로젝트(이슈키 접두사)들 — 등록 여부와 함께. */
    projectOptions() {
      const seen = new Set(), out = [], reg = new Set(this.searchProjects);
      for (const c of this.rawCards) {
        const p = this.projectOf(c);
        if (p && !seen.has(p)) { seen.add(p); out.push({ key: p, registered: reg.has(p) }); }
      }
      return out.sort((a, b) => a.key.localeCompare(b.key));
    },
    allProjectsShown() { return this.projectOptions.every((p) => this.projShown(p.key)); },
    anyProjectHidden() { return this.projectOptions.some((p) => !this.projShown(p.key)); },
    projFilterLabel() {
      if (this.allProjectsShown) return "전체";
      return this.projectOptions.filter((p) => this.projShown(p.key)).length + "/" + this.projectOptions.length;
    },
    /** 하단 Epic 콤보의 개별 항목 — 내 Task 에 실제로 있는 Epic 들(시그니처 컬러 포함). */
    epicOptions() {
      const seen = new Set(), out = [];
      for (const c of this.rawCards) {
        if (c.epicKey && !seen.has(c.epicKey)) {
          seen.add(c.epicKey);
          out.push({ key: c.epicKey, title: this.epicTitle(c.epicKey) || c.epicKey,
                     color: categoryColor(c.epicKey) });
        }
      }
      return out.sort((a, b) => a.title.localeCompare(b.title, "ko"));
    },
    // 사용자 VoC(=Epic 은 아니지만 논리적으로 Epic 처럼 쓰는) 버킷 — **Epic 에 안 속한 VoC** 만.
    // (Epic 이 배정된 VoC 는 그 Epic 버킷에 들어간다.)
    hasVocBucket() { return this.rawCards.some((c) => !c.epicKey && c.voc); },
    hasNoneBucket() { return this.rawCards.some((c) => !c.epicKey && !c.voc); },
    /** 필터 대상 전체 버킷(Epic 키들 + 사용자 VoC + 소속없음). '모든 Epic' 체크 상태 계산용. */
    allBuckets() {
      const ks = this.epicOptions.map((e) => e.key);
      if (this.hasVocBucket) ks.push("__voc__");
      if (this.hasNoneBucket) ks.push("__none__");
      return ks;
    },
    allEpicsShown() { return this.allBuckets.every((k) => !this.epicHidden[k]); },
    anyEpicHidden() { return this.allBuckets.some((k) => this.epicHidden[k]); },
    epicFilterLabel() {
      if (this.allEpicsShown) return "전체";
      const shown = this.allBuckets.filter((k) => !this.epicHidden[k]).length;
      return shown + "/" + this.allBuckets.length;
    },
    counts() {
      const c = { todo: 0, inprogress: 0, done: 0, related: 0 };
      for (const x of this.allCards) {
        if (!x.mine) { c.related++; continue; }
        c[x.statusCategory] = (c[x.statusCategory] || 0) + 1;
      }
      return c;
    },

    /** 화면에 그릴 목록.
     *  ★ 그룹은 **하위가 실제로 있을 때만** 만든다. Sub-Task 가 없는 Task 는 묶을 게 없으므로
     *    자기 자신만 담은 그룹 패널이 아니라 **그냥 티켓 카드**다(그게 사실에 맞는 표현이다). */
    panels() {
      if (this.groupBy === "none") {
        return [{ key: "__all__", kind: "none", cards: this.visible(this.allCards) }];
      }
      // 하위가 있는 Task 를 **위로**, 하위 없는 단독 Task 묶음을 아래로.
      // 단독 묶음은 서로 아무 관계 없는 티켓을 담는 자루라 그 '순위'(가장 급한 카드)는 사실상
      // 전체 최솟값이 된다 → 다른 그룹과 같이 정렬하면 언제나 맨 위를 차지한다. 순위 경쟁은
      // 실제 묶음(그룹)끼리만 시키고, 자루는 자리를 고정한다.
      // Epic 필터: 그룹은 부모·자식이 같은 Epic 을 공유하므로 그룹의 버킷으로 통째로 거른다.
      const grouped = this.groups
        .filter((g) => g.hasSubs && this.epicPass(g) && this.projPass(g) && !this.excluded[g.key])
        .map((g) => this.taskPanel(g));
      // 가로축에서 모든 Sub-Task 가 한 상태인 Task 는 그룹 패널이 아니다. 하위 없는 Task 와
      // 같은 마지막 카드 목록으로 보내 폭·위치·정렬을 완전히 공유하고, 하위 목록만 카드 아래에 붙인다.
      // 단, 어느 열에 놓일지는 Sub-Task 상태가 아니라 **부모 Task 자신의 상태**가 정한다.
      // 세로축이거나 부모 상태 열을 접었으면 기존 그룹 표현을 유지한다.
      const compact = this.axis === "h" ? grouped.filter((p) => this.compactStatus(p)) : [];
      const out = grouped.filter((p) => !compact.includes(p))
        .sort((a, b) => a.rank[0] - b.rank[0] || a.rank[1] - b.rank[1]);
      const solo = this.soloPanel(
        this.groups.filter((g) => !g.hasSubs && this.epicPass(g) && this.projPass(g) && !this.excluded[g.key]),
        compact,
      );
      if (solo) out.push(solo);
      return out;
    },
  },
  methods: {
    _emptyTaskModel() {
      return {
        scope: this.apiScope, openFilter: this.openFilter, progFilter: this.progFilter,
        doneFilter: this.doneFilter, doneWindowDays: this.doneFilter === "1m" ? 30 : 7,
        groups: [], epics: [], counts: this._groupCounts([]), streamComplete: false,
      };
    },
    _mergeRows(left, right) {
      const byKey = new Map();
      for (const row of (left || [])) if (row && row.key) byKey.set(row.key, row);
      for (const row of (right || [])) if (row && row.key) byKey.set(row.key, row);
      return Array.from(byKey.values());
    },
    _sortTaskRows(rows) {
      return (rows || []).slice().sort((a, b) => {
        const ad = a.dueDays === null || a.dueDays === undefined ? NO_DUE : a.dueDays;
        const bd = b.dueDays === null || b.dueDays === undefined ? NO_DUE : b.dueDays;
        return ad - bd || (a.priRank ?? 2) - (b.priRank ?? 2)
          || String(a.key).localeCompare(String(b.key));
      });
    },
    _refreshTaskGroup(group) {
      const merged = Object.assign({}, group);
      merged.atoms = this._sortTaskRows(merged.atoms);
      merged.others = this._sortTaskRows(merged.others)
        .filter((row) => !merged.atoms.some((atom) => atom.key === row.key));
      const children = this._mergeRows(merged.atoms, merged.others)
        .filter((row) => row.key !== merged.key);
      merged.kidsTotal = Math.max(merged.kidsTotal || 0, children.length);
      merged.kidsDone = children.filter((row) => row.statusCategory === "done").length;
      merged.othersDone = merged.others.filter((row) => row.statusCategory === "done").length;
      const due = merged.atoms.map((row) => row.dueDays)
        .filter((value) => value !== null && value !== undefined);
      merged.urgency = due.length ? Math.min(...due) : null;
      merged.priRank = merged.atoms.length
        ? Math.min(...merged.atoms.map((row) => row.priRank ?? 2)) : (merged.priRank ?? 2);
      return merged;
    },
    _mergeTaskGroup(previous, incoming) {
      if (!previous) return Object.assign({}, incoming, {
        atoms: (incoming.atoms || []).slice(), others: (incoming.others || []).slice(),
      });
      const oldComplete = previous.hasSubs && previous.childrenLoaded && !previous.childrenPending;
      const newComplete = incoming.hasSubs && incoming.childrenLoaded && !incoming.childrenPending;
      const complete = newComplete ? incoming : (oldComplete ? previous : null);
      const merged = Object.assign({}, previous, incoming);
      if (complete) {
        merged.atoms = (complete.atoms || []).slice();
        merged.others = (complete.others || []).slice();
        merged.childrenPending = false; merged.childrenLoaded = true;
      } else {
        merged.atoms = this._mergeRows(previous.atoms, incoming.atoms);
        merged.others = this._mergeRows(previous.others, incoming.others)
          .filter((row) => !merged.atoms.some((atom) => atom.key === row.key));
        merged.childrenPending = !!(previous.childrenPending || incoming.childrenPending);
        merged.childrenLoaded = !merged.childrenPending;
      }
      merged.hasSubs = !!(previous.hasSubs || incoming.hasSubs);
      merged.standalone = !merged.hasSubs;
      merged.kidsTotal = Math.max(previous.kidsTotal || 0, incoming.kidsTotal || 0,
                                  merged.atoms.length + merged.others.length);
      return this._refreshTaskGroup(merged);
    },
    _normalizeStreamGroups(groups) {
      const byGroup = new Map((groups || []).map((group) => [group.key, group]));
      const mineByKey = new Map();
      for (const group of (groups || [])) for (const atom of (group.atoms || [])) {
        if (atom && atom.key) mineByKey.set(atom.key, atom);
      }
      let rows = (groups || []).map((group) => Object.assign({}, group, {
        atoms: (group.atoms || []).filter((atom) =>
          !atom.parentKey || atom.parentKey === group.key || !byGroup.has(atom.parentKey)),
        others: (group.others || []).slice(),
      }));
      // A partial leaf may know that a ticket is mine before its parent metadata is complete.
      // Once a parent group also contains that key as related, promote the known mine row into the
      // parent rather than rendering a related child plus a standalone mine card.
      rows = rows.map((group) => {
        if (!group.hasSubs) return group;
        const promoted = (group.others || []).filter((row) => mineByKey.has(row.key));
        if (!promoted.length) return group;
        const promoteKeys = new Set(promoted.map((row) => row.key));
        return Object.assign({}, group, {
          atoms: this._mergeRows(group.atoms, promoted.map((row) => mineByKey.get(row.key))),
          others: group.others.filter((row) => !promoteKeys.has(row.key)),
        });
      });
      // Parent 그룹이 standalone보다 먼저 issue key를 소유한다. Jira가 SubTask flag를 누락해도
      // 같은 티켓이 두 카드로 늘어나지 않는다.
      const claimed = new Set();
      for (const group of rows.slice().sort((a, b) => Number(!!b.hasSubs) - Number(!!a.hasSubs))) {
        group.atoms = group.atoms.filter((atom) => {
          if (claimed.has(atom.key)) return false;
          claimed.add(atom.key); return true;
        });
        group.others = group.others.filter((atom) => {
          if (claimed.has(atom.key)) return false;
          claimed.add(atom.key); return true;
        });
      }
      rows = rows.filter((group) => group.hasSubs || group.atoms.length || group.others.length)
        .map((group) => this._refreshTaskGroup(group));
      return rows.sort((a, b) => {
        const ad = a.urgency === null || a.urgency === undefined ? NO_DUE : a.urgency;
        const bd = b.urgency === null || b.urgency === undefined ? NO_DUE : b.urgency;
        return ad - bd || (a.priRank ?? 2) - (b.priRank ?? 2)
          || String(a.key).localeCompare(String(b.key));
      });
    },
    /** A completed leaf is appended immediately; issue identity, order and statistics are rebuilt
     *  after every append so arrival order never leaks into the visible Task order. */
    _mergeStreamModel(previous, incoming) {
      const base = previous || this._emptyTaskModel();
      const byKey = new Map((base.groups || []).map((group) => [group.key, group]));
      for (const group of (incoming && incoming.groups) || []) {
        byKey.set(group.key, this._mergeTaskGroup(byKey.get(group.key), group));
      }
      const groups = this._normalizeStreamGroups(Array.from(byKey.values()));
      const epicMap = new Map((base.epics || []).map((epic) => [epic.key, epic]));
      for (const epic of (incoming && incoming.epics) || []) {
        const old = epicMap.get(epic.key);
        if (!old || old.pending || !epic.pending) epicMap.set(epic.key, epic);
      }
      return Object.assign({}, base, incoming || {}, {
        groups, epics: Array.from(epicMap.values()), counts: this._groupCounts(groups),
        streamComplete: false,
      });
    },
    _cacheModel(cache, key, model) {
      cache[key] = model;
      const keys = Object.keys(cache);
      if (keys.length > 12) delete cache[keys[0]];
    },
    axisLoading(key) { return (this.streamAxes[key] || {}).state !== "done"; },
    axisChunks(key) { return (this.streamAxes[key] || {}).chunks || 0; },
    _applyEpicMetadata(epics, cache) {
      const rows = (epics || []).filter((epic) => epic && epic.key && epic.title);
      if (!rows.length) return;
      const known = this._epicMetaKnown || (this._epicMetaKnown = new Map());
      for (const epic of rows) known.set(epic.key, Object.assign({}, epic, { pending: false }));
      const patch = (model) => {
        if (!model) return model;
        let changed = false;
        const nextEpics = (model.epics || []).map((epic) => {
          const meta = known.get(epic.key);
          if (!meta || (epic.title === meta.title && !epic.pending)) return epic;
          changed = true; return Object.assign({}, epic, meta, { pending: false });
        });
        if (!changed) return model;
        return Object.assign({}, model, {
          epics: nextEpics,
          epicsPending: nextEpics.some((epic) => epic.pending || !epic.title || epic.title === epic.key),
        });
      };
      for (const modelKey of Object.keys(cache || {})) cache[modelKey] = patch(cache[modelKey]);
      if (this._activeCacheKey && cache[this._activeCacheKey]) this.model = cache[this._activeCacheKey];
    },
    _queueEpicMetadata(model, cache) {
      const known = this._epicMetaKnown || (this._epicMetaKnown = new Map());
      const ready = [], pending = this._epicMetaPending || (this._epicMetaPending = new Set());
      const inflight = this._epicMetaInflight || (this._epicMetaInflight = new Set());
      for (const epic of (model && model.epics) || []) {
        if (!epic || !epic.key) continue;
        if (epic.title && epic.title !== epic.key && !epic.pending) {
          known.set(epic.key, epic); continue;
        }
        if (known.has(epic.key)) ready.push(known.get(epic.key));
        else if (!inflight.has(epic.key)) pending.add(epic.key);
      }
      this._applyEpicMetadata(ready, cache);
      if (!pending.size || this._epicMetaTimer || this._epicMetaBusy) return;
      this._epicMetaTimer = setTimeout(() => {
        this._epicMetaTimer = null; this._flushEpicMetadata(cache);
      }, 40);
    },
    async _flushEpicMetadata(cache) {
      const pending = this._epicMetaPending || new Set();
      if (this._epicMetaBusy || !pending.size) return;
      const keys = Array.from(pending).slice(0, 100);
      const inflight = this._epicMetaInflight || (this._epicMetaInflight = new Set());
      keys.forEach((key) => { pending.delete(key); inflight.add(key); });
      this._epicMetaBusy = true;
      try {
        const result = await api.myTasksEpicMeta(keys);
        this._applyEpicMetadata((result && result.epics) || [], cache);
      } catch (e) { /* 카드/JQL 스트림은 Epic 메타 조회 실패와 독립적으로 계속 동작한다 */ }
      finally {
        keys.forEach((key) => inflight.delete(key));
        this._epicMetaBusy = false;
        if (pending.size) this._queueEpicMetadata({ epics: Array.from(pending).map((key) => ({ key })) }, cache);
      }
    },
    /** The axes exist before the first upstream result.  Every leaf then appends independently. */
    async load(opts) {
      const key = this.apiScope + "|" + this.openFilter + "|" + this.progFilter + "|" + this.doneFilter;
      const cache = this._mcache || (this._mcache = {});
      if (this._streamAbort) this._streamAbort.abort();
      const controller = this._streamAbort = new AbortController();
      const seq = this._loadSeq = (this._loadSeq || 0) + 1;
      const cached = cache[key];
      // 새 필터는 이전 필터 카드를 남기지 않는다. 같은 필터의 완료/부분 캐시만 즉시 재사용한다.
      this.model = cached || this._emptyTaskModel();
      this._activeCacheKey = key;
      this._queueEpicMetadata(this.model, cache);
      this.streamAxes = {
        todo: { state: "loading", chunks: 0 },
        inprogress: { state: "loading", chunks: 0 },
        done: { state: "loading", chunks: 0 },
      };
      this.streamProgress = { done: 0, total: 0 };
      this.loading = false;
      this.err = "";
      if (Object.keys(this.excluded).length) this.excluded = {};   // 실 로딩이면 클라 이탈표시 초기화(목록이 새로 정확)
      try {
        await api.myTasksStream({ scope: this.apiScope, openFilter: this.openFilter,
          progFilter: this.progFilter, doneFilter: this.doneFilter }, (event) => {
          if (event.type === "planned") {
            if (seq === this._loadSeq) this.streamProgress = {
              done: event.leafDone || 0, total: event.leafTotal || 0,
            };
            return;
          }
          if (event.type === "chunk") {
            const next = this._mergeStreamModel(cache[key] || this._emptyTaskModel(), event.model);
            this._cacheModel(cache, key, next);       // 화면이 바뀐 뒤 도착해도 이 필터 캐시에 남긴다
            if (seq !== this._loadSeq) {
              this._queueEpicMetadata(next, cache);   // 지난 필터도 완료된 Epic 메타는 캐시에 남긴다
              return;
            }
            this.model = next;
            this._queueEpicMetadata(next, cache);     // 첫 참조 즉시 별도 저우선순위 배치로 이름을 채운다
            this.streamProgress = { done: event.leafDone || 0, total: event.leafTotal || 0 };
            const axes = Object.assign({}, this.streamAxes);
            if (event.axis && axes[event.axis]) axes[event.axis] = {
              state: "loading", chunks: axes[event.axis].chunks + 1,
            };
            this.streamAxes = axes;
            return;
          }
          if (event.type === "leaf-error") {
            if (seq !== this._loadSeq) return;
            this.streamProgress = { done: event.leafDone || 0, total: event.leafTotal || 0 };
            const kind = (event.error && event.error.kind) || "other";
            if (kind === "permission") return;       // 볼 권한 없는 leaf는 Jira의 정상적인 부분 결과
            const notices = this._streamErrorNotices || (this._streamErrorNotices = new Set());
            const noticeKey = seq + ":" + kind;
            if (notices.has(noticeKey)) return;
            notices.add(noticeKey);
            if (kind === "auth") window.dispatchEvent(new CustomEvent("need-login"));
            pushToast({
              kind: "error", key: "task-stream-" + noticeKey,
              title: kind === "auth" ? "일부 Task를 인증 문제로 불러오지 못했습니다"
                                     : "일부 Task를 불러오지 못했습니다",
              message: "불러온 티켓은 계속 표시합니다.", timeout: 7000,
            });
            return;
          }
          if (event.type === "complete") {
            const finalModel = Object.assign({}, event.model || this._emptyTaskModel(), {
              streamComplete: true,
            });
            this._cacheModel(cache, key, finalModel);
            if (seq !== this._loadSeq) {
              this._queueEpicMetadata(finalModel, cache);
              return;
            }
            this.model = finalModel;
            this._queueEpicMetadata(finalModel, cache);
            this.streamProgress = { done: event.leafDone || 0, total: event.leafTotal || 0 };
            this.streamAxes = {
              todo: { state: "done", chunks: this.axisChunks("todo") },
              inprogress: { state: "done", chunks: this.axisChunks("inprogress") },
              done: { state: "done", chunks: this.axisChunks("done") },
            };
            this._hydrateModel(finalModel, seq, key, cache);
          }
        }, controller.signal);
      }
      catch (e) {
        if (controller.signal.aborted || seq !== this._loadSeq) return;
        this.err = (e && e.message) || "불러오기 실패";
      }
      finally {
        if (seq === this._loadSeq && this._streamAbort === controller) this._streamAbort = null;
      }
    },
    /** 완료된 후속 결과는 필터가 이미 바뀌었어도 그 필터의 SWR 캐시에 합친다.
     *  단 현재 화면은 load sequence가 같은 경우에만 바꾼다. */
    _mergeHydration(cache, cacheKey, syncId, patch) {
      const previous = cache[cacheKey];
      if (!previous || previous.syncId !== syncId) return;
      let next = previous;
      if (patch.group) {
        const claimed = new Set(this._mergeRows(patch.group.atoms, patch.group.others)
          .map((row) => row.key));
        const groups = this._normalizeStreamGroups((previous.groups || []).map((group) => {
          if (group.key === patch.group.key) return patch.group;
          if (!claimed.size) return group;
          // Parent가 소유한다고 확인된 모든 child(atom/other)는 standalone/다른 임시 그룹에서
          // 제거한다. leaf 도착 시 parent 정보가 덜 온 row도 hydration 뒤에는 한 장만 남는다.
          return Object.assign({}, group, {
            atoms: (group.atoms || []).filter((atom) => !claimed.has(atom.key)),
            others: (group.others || []).filter((atom) => !claimed.has(atom.key)),
          });
        }));
        next = Object.assign({}, previous, {
          groups, counts: this._groupCounts(groups),
        });
      } else if (patch.epics) {
        next = Object.assign({}, previous, { epics: patch.epics, epicsPending: false });
      }
      cache[cacheKey] = next;
      if (this._loadSeq === patch.seq && this.model && this.model.syncId === syncId) this.model = next;
    },
    _groupCounts(groups) {
      const seen = new Set(), atoms = [];
      for (const group of groups || []) for (const atom of group.atoms || []) {
        if (!seen.has(atom.key)) { seen.add(atom.key); atoms.push(atom); }
      }
      return {
        total: atoms.length,
        overdue: atoms.filter((atom) => atom.statusCategory !== "done" && atom.dueDays !== null
          && atom.dueDays !== undefined && atom.dueDays < 0).length,
        today: atoms.filter((atom) => atom.statusCategory !== "done" && atom.dueDays === 0).length,
        week: atoms.filter((atom) => atom.statusCategory !== "done" && atom.dueDays > 0 && atom.dueDays <= 7).length,
        done: atoms.filter((atom) => atom.statusCategory === "done").length,
        noDue: atoms.filter((atom) => atom.dueDays === null || atom.dueDays === undefined).length,
      };
    },
    async _hydrateModel(model, seq, cacheKey, cache) {
      const syncId = model && model.syncId;
      if (!syncId) return;
      const jobs = (model.groups || []).filter((group) => group.childrenPending)
        .map((group) => ({ type: "group", key: group.key }));
      let cursor = 0;
      // 브라우저 요청은 둘만 병렬화한다. 서버에서는 background priority라 새 퀵필터 JQL이
      // 이 작업들을 앞지르고, 이미 시작된 옛 필터 결과만 해당 필터 캐시에 안전하게 남는다.
      const worker = async () => {
        while (cursor < jobs.length) {
          if (seq !== this._loadSeq) return;   // 아직 시작하지 않은 옛 필터 보강은 새 필터에 양보
          const job = jobs[cursor++];
          try {
            const result = job.type === "group"
              ? await api.myTasksGroup(syncId, job.key)
              : await api.myTasksEpics(syncId);
            // await 중 필터가 바뀌어도 완료된 값은 버리지 않는다. 현재 UI 반영만 seq가 막는다.
            this._mergeHydration(cache, cacheKey, syncId, {
              seq, group: result && result.group, epics: result && result.epics,
            });
          } catch (e) {
            // Parent 하나 실패가 다른 카드나 새 필터를 막지 않는다. 다음 실제 load에서 재시도한다.
          }
        }
      };
      await Promise.allSettled([worker(), worker()]);
    },
    /** 티켓이 바뀌면 클라이언트 모델 캐시는 낡는다 — 통째로 비운다(서버 mt: 캐시도 같은 이유로 무효화). */
    _dropModelCache() { this._mcache = {}; },
    /** 티켓 수정 알림을 화면에 **즉시** 반영 — 로컬 카드의 필드만 갱신한다.
     *  목록에서 빼는 판정은 하지 않는다(서버가 한다 — _onChanged 주석 참고). */
    _applyEditLocally(f) {
      const key = f.key;
      const upd = (n) => {
        if (!n || n.key !== key) return;
        if (f.statusCategory) n.statusCategory = f.statusCategory;
        if ("assigneeId" in f) n.assigneeId = f.assigneeId;
        if ("reporterId" in f) n.reporterId = f.reporterId;
        if (f.components) n.components = f.components;
      };
      for (const g of (this.model && this.model.groups) || []) {
        (g.atoms || []).forEach(upd); (g.others || []).forEach(upd); upd(g);
      }
    },
    _toastExcluded(keys) {
      const label = keys.length === 1 ? keys[0] : keys.length + "개 티켓";
      pushToast({ kind: "info", icon: "↪", title: "현재 필터에서 제외",
                  message: label + " — 수정으로 이 목록 조건에 더는 맞지 않아 숨겼습니다.", timeout: 5000 });
    },
    async hardRefresh() {
      if (this.busy) return;
      this.busy = true;
      this._dropModelCache();
      try { await api.refresh(); this.model = null; await this.load(); }
      catch (e) { this.err = (e && e.message) || "다시 받지 못했습니다."; }
      finally { this.busy = false; }
    },
    /** 옵션 하나 바꾸기. reload 옵션은 JQL 조건이라 바꾸면 다시 받는다
     *  (서버 질의 자체가 달라지므로 클라이언트에서 걸러낼 수 있는 게 아니다). */
    setOpt(o, v) {
      if (this[o.key] === v) return;
      this[o.key] = v;
      this.savePrefs();
      if (o.reload) this.load();
    },
    hintOf(o) { const cur = o.opts.find((x) => x.k === this[o.key]); return cur ? cur.hint : o.label; },
    /** 세그먼트 버튼 — 그 스코프를 켠다. 우측 선택(사람/모듈)은 **유지**(포커스 떠나도 안 바뀜). */
    setScope(s) {
      if (this.scope === s) return;
      this.scope = s;
      this.savePrefs(); this.load();
    },
    /** 담당자 picker(FieldEdit local)에서 고름 → 담당자 스코프. 빈 값 또는 **나**를 고르면
     *  '나'(null)로 둬 currentUser 와 같은 캐시 키로 모이게 한다. */
    pickAssignee(id, u) {
      this.assigneeSel = (!id || id === this.myId) ? null : { id, name: (u && (u.display || u.name)) || id };
      this.scope = "assignee"; this.savePrefs(); this.load();
    },
    pickReporter(id, u) {
      this.reporterSel = (!id || id === this.myId) ? null : { id, name: (u && (u.display || u.name)) || id };
      this.scope = "reporter"; this.savePrefs(); this.load();
    },
    /** 모듈 콤보 — '내 모듈 전체'(__all__)면 moduleSel 비움, 아니면 특정 모듈. **모듈 스코프로 전환**.
     *  선택은 moduleSel 에 남아 포커스가 떠나도 유지된다. */
    onModulePick(v) {
      this.moduleSel = (!v || v === "__all__") ? "" : v;
      this.scope = "module"; this.savePrefs(); this.load();
    },
    /** 고급 검색 — JQL 입력/빌더 결과로 검색 실행. 비어 있으면 '나(담당)'로 폴백. */
    runJql() {
      this.scope = this.jqlText.trim() ? "jql" : "assignee";
      this.savePrefs(); this.load();
    },
    onAdvApply(jql) {           // 빌더 다이얼로그가 만든 JQL 을 입력창에 채우고 바로 검색
      this.jqlText = jql || "";
      this.advOpen = false;
      this.runJql();
    },
    /** Task+SubTask 그룹 개별 접기/펴기. '모두 접기'도 하단바 한 번으로 다시 열 수 있다. */
    isGroupClosed(panel) {
      if (!panel) return true;
      if (Object.prototype.hasOwnProperty.call(this.gClosed, panel.key)) return !!this.gClosed[panel.key];
      return panel.mode === "collapsed";
    },
    toggleGroup(panel) {
      if (!panel) return;
      this.gClosed = Object.assign({}, this.gClosed, { [panel.key]: !this.isGroupClosed(panel) });
      this.savePrefs();
    },

    /** 이 카드/그룹의 필터 버킷 — Epic 키, 없으면 사용자 VoC("__voc__"), 그것도 아니면 "__none__". */
    bucketOf(x) {
      const ek = (x && (x.epicKey || x.epic)) || null;
      if (ek) return ek;
      return x && x.voc ? "__voc__" : "__none__";
    },
    /** Epic 필터 — 이 카드/그룹의 버킷이 가려지지 않았는가. */
    epicPass(x) { return !this.epicHidden[this.bucketOf(x)]; },
    toggleEpic(k) {
      const h = Object.assign({}, this.epicHidden);
      if (h[k]) delete h[k]; else h[k] = true;
      this.epicHidden = h; this.savePrefs();
    },
    /** '모든 Epic' — 다 보이면 전부 가리고, 아니면 전부 보인다(가림 초기화). */
    toggleAllEpics() {
      if (this.allEpicsShown) {
        const h = {}; for (const k of this.allBuckets) h[k] = true; this.epicHidden = h;
      } else { this.epicHidden = {}; }
      this.savePrefs();
    },

    /** 이슈키 접두사 = 프로젝트 키(DL-1234 → DL). */
    projectOf(x) { const k = (x && x.key) || ""; const i = k.indexOf("-"); return i > 0 ? k.slice(0, i) : k; },
    /** 이 프로젝트가 보이는가 — 사용자 토글이 있으면 그것, 없으면 기본(등록=보임/미등록=숨김). */
    projShown(p) {
      if (Object.prototype.hasOwnProperty.call(this.projPref, p)) return !!this.projPref[p];
      return this.searchProjects.includes(p);
    },
    /** Project 필터 — 이 카드/그룹의 프로젝트가 보이는가. */
    projPass(x) { return this.projShown(this.projectOf(x)); },
    toggleProj(p) {
      this.projPref = Object.assign({}, this.projPref, { [p]: !this.projShown(p) });
      this.savePrefs();
    },
    /** '모든 Project' — 다 보이면 전부 숨기고, 아니면 전부 보인다. */
    toggleAllProjects() {
      const want = !this.allProjectsShown;
      const pref = Object.assign({}, this.projPref);
      for (const p of this.projectOptions) pref[p.key] = want;
      this.projPref = pref; this.savePrefs();
    },

    /** 옵션은 브라우저에 남긴다 — 매번 같은 배치로 맞추는 건 화면이 할 일이지 사람이 할 일이 아니다.
     *  값 검증까지 하는 이유: 옵션 목록이 바뀌면 저장된 옛 값이 어디에도 없는 상태가 되고,
     *  그러면 select 가 빈 채로 뜨고 필터는 이상하게 걸린다. */
    loadPrefs() {
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(PREF_KEY) || "null"); } catch (e) { saved = null; }
      if (!saved) return;
      for (const o of OPTIONS) {
        if (o.opts.some((x) => x.k === saved[o.key])) this[o.key] = saved[o.key];
      }
      // 상태 칸 제목으로 옮긴 표시 범위도 같이 기억한다(플로팅 바에서 빠졌을 뿐 값은 그대로다)
      for (const f of Object.values(BAND_FILTERS)) {
        if (f.opts.some((x) => x.k === saved[f.key])) this[f.key] = saved[f.key];
      }
      // 상태 열 접힘은 옵션 목록에 없지만(콤보가 아니라 헤더 클릭) 같이 기억한다 —
      // 매번 다시 접게 하면 접기의 의미가 없다.
      if (saved.bandClosed && typeof saved.bandClosed === "object") {
        this.bandClosed = Object.assign({}, saved.bandClosed);
      }
      if (saved.epicHidden && typeof saved.epicHidden === "object") {
        this.epicHidden = Object.assign({}, saved.epicHidden);
      }
      // 상단 퀵필터(연관성/모듈)는 OPTIONS 밖이라 따로 복원한다.
      if (["assignee", "reporter", "module", "jql"].includes(saved.scope)) this.scope = saved.scope;
      if (typeof saved.moduleSel === "string") this.moduleSel = saved.moduleSel;
      if (saved.assigneeSel && saved.assigneeSel.id) this.assigneeSel = saved.assigneeSel;
      if (saved.reporterSel && saved.reporterSel.id) this.reporterSel = saved.reporterSel;
      if (typeof saved.jqlText === "string") this.jqlText = saved.jqlText;
      if (saved.gClosed && typeof saved.gClosed === "object") this.gClosed = Object.assign({}, saved.gClosed);
      if (saved.projPref && typeof saved.projPref === "object") this.projPref = Object.assign({}, saved.projPref);
    },
    savePrefs() {
      const out = { bandClosed: this.bandClosed, epicHidden: this.epicHidden, gClosed: this.gClosed,
                    scope: this.scope, moduleSel: this.moduleSel, projPref: this.projPref,
                    assigneeSel: this.assigneeSel, reporterSel: this.reporterSel, jqlText: this.jqlText };
      for (const o of OPTIONS) out[o.key] = this[o.key];
      for (const f of Object.values(BAND_FILTERS)) out[f.key] = this[f.key];
      try { localStorage.setItem(PREF_KEY, JSON.stringify(out)); } catch (e) { /* 사파리 프라이빗 등 */ }
    },

    /** 원자/동료 티켓 → 카드. mine 이면 강조 대상이다. */
    card(t, g, mine) {
      // Sub-Task 에 마감이 없으면 **부모 Task 의 마감**을 쓴다. 실무에서 마감은 보통 부모에만
      // 걸리고 하위는 비워 두는데, 그걸 '마감 없음' 으로 두면 실제로는 부모와 같은 날 끝나야 할
      // 일이 안 급한 것처럼 보인다. 다만 빌려 온 값이므로 dueInherited 로 표시해 구분한다.
      const own = t.dueDays !== null && t.dueDays !== undefined;
      const inherit = !own && t.key !== g.key && g.dueDays !== null && g.dueDays !== undefined;
      return Object.assign({}, t, {
        mine,
        parent: g,
        epicKey: t.epic || g.epic || null,
        due: inherit ? g.due : t.due,
        dueDays: inherit ? g.dueDays : t.dueDays,
        dueInherited: inherit,
        // 그룹 자체가 카드인 경우(하위 없는 단독 Task)와 하위 카드 구분
        isGroupSelf: t.key === g.key,
      });
    },
    /** 그룹이 아닌(=하위 없는) 카드 묶음용 — 기본은 내 담당만, '유관 기본 펼침' 이면 전부.
        '모두 접기' 는 하위에만 걸리는 말이라 여기선 '내 것만' 과 같게 둔다 — 접을 하위가 없다. */
    visible(cards) {
      return this.sorted(this.subView === "all" ? cards : cards.filter((c) => c.mine));
    },
    /** 이 상태 칸에 붙는 표시 범위 정의(없으면 null). 진행 중은 기간 개념이 없다 — 지금 하는 일이다. */
    bandFilter(k) { return BAND_FILTERS[k] || null; },
    /** 그 칸의 현재 값 */
    opt(k) { const f = BAND_FILTERS[k]; return f ? this[f.key] : null; },
    setBandFilter(k, v) {
      const f = BAND_FILTERS[k];
      if (!f || this[f.key] === v) return;
      this[f.key] = v;
      this.savePrefs();
      this.load({ quiet: true });          // 서버 질의 조건이라 다시 받아야 한다
    },
    dueOf(c) { return (c.dueDays === null || c.dueDays === undefined) ? NO_DUE : c.dueDays; },
    sorted(cards) {
      const by = this.sort;
      // Epic 기준은 **Epic 으로 모으고 그 안에서 우선순위 → 마감**이다. Epic 으로 묶어 보는
      // 이유가 '이 Epic 에서 뭐부터 하지' 라서, 묶기만 하고 안이 뒤죽박죽이면 볼 이유가 없다.
      // Epic 없음은 맨 뒤로 — 소속이 없는 건 소속들 사이에 끼워 넣을 자리가 없다.
      const ep = (c) => (c.epicKey ? (this.epicTitle(c.epicKey) || c.epicKey) : "￿");
      const byPri = (a, b) => a.priRank - b.priRank || this.dueOf(a) - this.dueOf(b);
      const byDue = (a, b) => this.dueOf(a) - this.dueOf(b) || a.priRank - b.priRank;
      const cmp = by === "epic" ? ((a, b) => ep(a).localeCompare(ep(b), "ko") || byPri(a, b))
                : by === "pri" ? byPri : byDue;
      return cards.slice().sort((a, b) => cmp(a, b) || (a.key < b.key ? -1 : 1));
    },
    /** 패널 정렬 키 — 그 안에서 가장 급한 내 카드가 순서를 정한다(급한 게 위/앞으로).
     *  ★ 카드와 **같은 1·2차 규칙**을 쓴다. 1차만 보면 마감이 같은 그룹들의 순서가 우선순위와
     *    무관하게 흔들려, 그룹 안 카드는 제대로 정렬됐는데 그룹끼리는 아닌 상태가 된다. */
    rankOf(cards) {
      const mine = cards.filter((c) => c.mine);
      const pool = mine.length ? mine : cards;
      if (!pool.length) return [NO_DUE, 9];
      const due = Math.min(...pool.map((c) => this.dueOf(c)));
      const pri = Math.min(...pool.map((c) => c.priRank));
      return this.sort === "pri" ? [pri, due] : [due, pri];
    },
    taskPanel(g) {
      const ex = (a) => !this.excluded[a.key];      // 수정으로 필터 이탈한 하위는 뺀다
      const mineCards = g.atoms.filter(ex).map((a) => this.card(a, g, true));
      const all = mineCards.concat(g.others.filter(ex).map((ot) => this.card(ot, g, false)));
      const mode = this.subView;
      // '내 티켓만' 이라도 **내 하위가 하나도 없으면** 그 그룹의 실제 하위(others)를 대신 보여 준다.
      // (내가 부모만 담당하고 하위는 전부 남의 것이거나, epic 스코프처럼 하위가 스코프에 안 잡힐 때 —
      //  안 그러면 그룹 본문이 비어 '하위가 없는 것처럼' 보인다.)
      // '모두 접기'는 초기 표시 상태일 뿐이다. 하단바를 눌러 개별 Task를 펼칠 수 있도록
      // 실제 하위 카드는 보존하고, 렌더 여부만 isGroupClosed()가 정한다.
      const shown = mode === "all" || mode === "collapsed" || !mineCards.length ? all : mineCards;
      // 하단바는 현재 펼쳐 둔 범위와 무관하게 실제 Sub-Task 담당자를 모두 보여 준다.
      // 부모 담당자는 부모 카드에 이미 있으므로 중복하지 않는다.
      const assignees = [];
      const seenAssignees = new Set();
      for (const card of all) {
        const id = card.assigneeId || "";
        const name = card.assignee || "";
        if (!id && !name) continue;
        const identity = id || name;
        if (seenAssignees.has(identity)) continue;
        seenAssignees.add(identity);
        assignees.push({ id, name: name || id });
      }
      return {
        key: g.key, kind: "task", group: g,
        title: g.title, epicKey: g.epic,
        mode, mineCount: mineCards.length, allCount: all.length, assignees,
        // 1컬럼 모드의 부모는 별도 축약 UI 가 아니라 단독 Task 와 같은 TaskCard 를 그대로 쓴다.
        parentCard: this.card(g, g, !!g.mine),
        cards: this.sorted(shown),
        // 화면에 일부만 펼쳐도 판정은 동료 몫까지 포함한 **모든 실제 Sub-Task** 기준이다.
        singleStatus: uniformStatusCategory(all),
        rank: this.rankOf(all),
      };
    },
    /** 하위 없는 Task 들과 1축 Task 들을 같은 카드 목록으로 모은 덩어리(그룹 패널이 아니다). */
    soloPanel(gs, compactPanels = []) {
      const cards = [];
      for (const g of gs) {
        for (const a of g.atoms) if (!this.excluded[a.key]) cards.push(this.card(a, g, true));
        for (const ot of g.others) if (!this.excluded[ot.key]) cards.push(this.card(ot, g, false));
      }
      const vis = this.visible(cards);
      // 단독 Task 처럼 취급하므로 열 배치도 부모 Task 자신의 상태를 그대로 쓴다. Sub-Task의
      // 공통 상태는 1컬럼 전환 여부와 아래 하위 목록에만 쓰며 부모 상태를 덮어쓰지 않는다.
      for (const p of compactPanels) {
        vis.push(Object.assign({}, p.parentCard, {
          compactPanel: p,
        }));
      }
      if (!vis.length) return null;
      return { key: "__solo__", kind: "solo", cards: this.sorted(vis), rank: this.rankOf(cards) };
    },
    /** 패널의 카드를 상태별로 나눈다 — 상태 축이 가로든 세로든 이 함수를 쓴다. */
    byState(cards) {
      const m = { todo: [], inprogress: [], done: [] };
      for (const c of cards) (m[c.statusCategory] || m.todo).push(c);
      return m;
    },
    /** 가로축의 1컬럼 버전. 부모 상태 열이 접혔으면 부모 정보가 찌그러지지 않게 기존 전폭으로 둔다. */
    compactStatus(p) {
      if (p?.group?.childrenPending) return null;
      const uniform = p && p.kind === "task" ? p.singleStatus : null;
      const rawParent = p?.parentCard?.statusCategory;
      const parentStatus = STATE_KEYS.has(rawParent) ? rawParent : "todo";
      return uniform && this.bandOpen(parentStatus) ? parentStatus : null;
    },
    parentState(p) {
      const value = p?.group?.statusCategory;
      return STATE_KEYS.has(value) ? value : "todo";
    },
    /**
     * 하위가 많은 Task 는 카드가 세로로 길어져 목록을 훑기 어렵다 — 접어 둔다.
     *
     * **어느 한 상태라도** SUB_CAP 을 넘으면 그 Task 가 접기 대상이 되고, 그때부터는
     * **모든 상태에 같은 규칙**이 걸린다(각 칸 SUB_CAP 개까지). 예전엔 완료 칸만, 그것도
     * '다른 칸 중 큰 쪽' 기준으로 잘랐다 — 진행중이 스무 개인 Task 는 손도 못 댔고, 자르는
     * 기준이 칸마다 달라 왜 여기만 잘렸는지 설명하기 어려웠다. 하나의 수로 통일한다.
     *
     * 펼침은 **Task 단위 하나**다(칸마다 따로 열지 않는다) — 하위를 펼쳐 볼 땐 대개 전부를
     * 보려는 것이고, 칸별로 열고 닫게 하면 지금 무엇이 접혀 있는지 사람이 추적해야 한다.
     */
    foldable(p) {
      const bs = this.byState(p.cards);
      return bs.todo.length > SUB_CAP || bs.inprogress.length > SUB_CAP || bs.done.length > SUB_CAP;
    },
    /** 접힘과 무관하게 이 칸이 **원래 넘쳤나** — 펼친 뒤 '접기' 를 그 칸에 두려고 쓴다. */
    overflowed(p, k) { return this.byState(p.cards)[k].length > SUB_CAP; },
    /** 이 칸에서 접혀 안 보이는 개수(0 이면 이 칸엔 더보기 버튼이 없다). */
    cellHidden(p, k) {
      if (!this.foldable(p)) return 0;
      return Math.max(0, this.byState(p.cards)[k].length - SUB_CAP);
    },
    toggleSub(key) { this.subOpen = Object.assign({}, this.subOpen, { [key]: !this.subOpen[key] }); },
    /**
     * 상태 칸에 그릴 카드 — **자르지 않고 전부 준다.**
     * 잘라내기는 CSS(.fold-peek)가 한다: 여섯 번째가 흐려지며 잘리고 그 뒤는 사라진다.
     * "뒤에 더 있다" 를 잘린 카드가 말해 주는데, JS 로 미리 잘라 버리면 보여 줄 카드가 없다
     * (첨부·관련문서 목록과 같은 방식이다).
     */
    cellCards(p, k) { return this.byState(p.cards)[k]; },
    /** 이 칸을 접어서 보여 줄 것인가 — 접기 대상이고 아직 안 폈을 때. */
    peeking(p) { return this.foldable(p) && !this.subOpen[p.key]; },
    /** 세로축 모드의 상태 밴드 접기 — 지금 안 보는 상태를 통째로 치우고 화면을 벌 수 있게. */
    bandOpen(k) { return !this.bandClosed[k]; },
    toggleBand(k) {
      this.bandClosed = Object.assign({}, this.bandClosed, { [k]: !this.bandClosed[k] });
      this.savePrefs();
    },
    bandCount(k) {
      return this.allCards.filter((c) => c.statusCategory === k && (this.subView === "all" || c.mine)).length;
    },
    /** 하위 보기 모드 — 그룹별 설정이 있으면 그것, 없으면 상단 옵션이 정한 기본값. */
    /** 펼치기 버튼 — 접기 → 내 것만 → 전체 → 접기 로 순환한다. */
    epicTitle(k) { return k ? ((this.epicMap[k] || {}).title || k) : null; },
    epicPending(k) {
      if (!k) return false;
      const epic = this.epicMap[k];
      return !epic || epic.pending || !epic.title || epic.title === k;
    },
    epicDisplayTitle(k) { return this.epicPending(k) ? "Epic 이름 확인 중" : this.epicTitle(k); },
    // VoC 티켓 제목 접두 [대분류 - 소분류] → 뱃지 세그먼트 / 제목에서 접두 제거(voc.js).
    vocSegs(title) { return vocBadgeSegs(title); },
    vocStrip(title) { return vocStripTitle(title); },
    /** 카드 좌측 띠 색 — 소속 Epic 의 시그니처 컬러. 없으면 null(중립). */
    sigOf(c) { return epicSig(c); },
    sigStyle(c) {
      // 그룹(부모 Task)은 epicKey 가 아니라 epic 필드를 쓴다 — 둘 다 받아 준다.
      const v = epicSig({ epicKey: c.epicKey || c.epic, voc: c.voc });
      return v ? { "--sig": v } : {};
    },
    // 급함 판정은 2줄 카드와 **같은 함수**를 쓴다. 그룹·Sub-Task 카드가 다른 기준으로 붉어지면
    // 같은 화면에서 '급함' 의 뜻이 두 개가 된다.
    isHotC(c) { return c.statusCategory !== "done" && isHot(c.dueDays); },
    isUrgentC(c) { return c.statusCategory !== "done" && isUrgent(c); },

    // ── 카드 드래그로 상태변경 ──────────────────────────────────────
    // 카드를 잡아 끌면 화면에 **오버레이 드랍가이드**가 뜬다: 가로축(칸반)에선 세로 3등분,
    // 세로축에선 가로 3등분. 영역 밖(가장자리 여백·영역 사이 틈)에 놓으면 **취소**다.
    // 클릭(티켓 열기)과의 충돌은 이동 임계값(8px)으로 가른다 — 그 미만이면 클릭으로 흘려보낸다.
    _bindDrag() {
      const root = this.$el;
      if (!root || this._dragBound) return;
      this._dragBound = true;
      let cand = null;   // pointerdown 후보 {key,title,cat,x0,y0,el}
      const findCard = (t) => t.closest && t.closest(".mt-card[data-key]");
      const onDown = (e) => {
        if (e.button !== 0 || this.drag) return;
        if (e.target.closest("button, a, input, select, textarea")) return;
        const el = findCard(e.target);
        if (!el) return;
        const key = el.getAttribute("data-key");
        // 일반 카드는 allCards, 그룹 부모 카드는 panels[].group 에 산다 — 둘 다 찾아야
        // '같은 상태에 놓기 = 취소' 판정(cat)이 그룹 카드에서도 동작한다.
        const c = this.allCards.find((x) => x.key === key)
          || (this.panels.find((p) => p.key === key) || {}).group;
        cand = { key, title: (c && (c.title || c.summary)) || key,
                 cat: (c && c.statusCategory) || null, x0: e.clientX, y0: e.clientY };
        this._dragEl = el;   // 원본 카드 — 드래그 중 흐리게(무엇이 들려 있는지 즉시 보이게)
      };
      const onMove = (e) => {
        if (!cand && !this.drag) return;
        if (!this.drag) {
          if (Math.hypot(e.clientX - cand.x0, e.clientY - cand.y0) < 8) return;   // 아직 클릭 범위
          this.drag = { ...cand, x: e.clientX, y: e.clientY, zone: null,
                        cols: this.axis === "h" ? this._measureCols() : null };
          document.body.classList.add("mtdnd-lock");
          this._dragEl && this._dragEl.classList.add("mtdnd-src");
        }
        this.drag.x = e.clientX; this.drag.y = e.clientY;
        const z = document.elementFromPoint(e.clientX, e.clientY);
        const zel = z && z.closest && z.closest(".mtdnd-z");
        this.drag.zone = (zel && !zel.classList.contains("cur")) ? zel.getAttribute("data-zone") : null;
      };
      const onUp = () => {
        if (this.drag) {
          const { key, zone, cat } = this.drag;
          this._endDrag();
          if (zone && zone !== cat) this._dropTo(key, zone);
          // 드래그였다면 이어지는 click(티켓 다이얼로그 열기)을 한 번 먹는다.
          const eat = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
          document.addEventListener("click", eat, { capture: true, once: true });
          setTimeout(() => document.removeEventListener("click", eat, { capture: true }), 0);
        }
        cand = null;
      };
      const onKey = (e) => {
        if (e.key !== "Escape") return;
        if (this.drag) { this._endDrag(); cand = null; return; }
        // 드랍으로 연 전이 다이얼로그의 ESC — TicketDialog 의 체인과 같은 규칙:
        // 에디터에 포커스가 있으면 먼저 에디터에서 빠져나가고, 한 번 더 누르면 닫는다.
        if (!this.dragTrx) return;
        const ae = document.activeElement;
        if (ae && ae.closest && ae.closest('.ProseMirror, .tiptap, [contenteditable="true"]')) {
          e.preventDefault();
          try { ae.blur(); } catch (_) { /* noop */ }
          return;
        }
        this.dragTrx = null;
      };
      root.addEventListener("pointerdown", onDown);
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("keydown", onKey);
      this._unbindDrag = () => {
        root.removeEventListener("pointerdown", onDown);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("keydown", onKey);
        this._dragBound = false;
      };
    },
    _endDrag() {
      this.drag = null;
      document.body.classList.remove("mtdnd-lock");
      this._dragEl && this._dragEl.classList.remove("mtdnd-src");
      this._dragEl = null;
    },
    /** 가로축 드랍존의 left/right 를 **실제 상태 칼럼**(.mt-colbg 자식)과 일치시키기 위한 측정.
     *  접힌 칼럼(34px)이 있으면 '모두 펼친' 기하(균등 3등분 + gap)로 재구성한다 — 접힘과 무관하게
     *  드랍존은 항상 같은 자리(사용자 요청: 폴딩 안 된 상태의 left/right 기준). 실패 시 null(폴백 flex). */
    _measureCols() {
      const bg = this.$el && this.$el.querySelector(".mt-colbg");
      if (!bg) return null;
      const kids = Array.from(bg.children);
      if (kids.length !== this.states.length) return null;
      const allOpen = this.states.every((st) => this.bandOpen(st.k));
      if (allOpen) {
        return kids.map((el) => { const r = el.getBoundingClientRect(); return { left: r.left, width: r.width }; });
      }
      const cr = bg.getBoundingClientRect();
      const cs = getComputedStyle(bg);
      const gap = parseFloat(cs.columnGap || cs.gap) || 0;
      const w = (cr.width - gap * (kids.length - 1)) / kids.length;
      return kids.map((_, i) => ({ left: cr.left + i * (w + gap), width: w }));
    },
    zoneStyle(i) {
      const c = this.drag && this.drag.cols && this.drag.cols[i];
      if (!c) return {};
      return { position: "absolute", left: c.left + "px", width: c.width + "px", top: "0", bottom: "0" };
    },
    /** 드랍 → 그 상태로 가는 전이를 찾아 실행. 필수 입력이 있으면 전이 다이얼로그로 넘긴다. */
    async _dropTo(key, zone) {
      // 완료로 보내는데 미완료 하위가 남아 있으면 먼저 확인(도네가드)
      if (zone === "done" && !(await confirmDoneDespiteOpenSubs(key))) return;
      let trs = [];
      try { trs = await api.transitions(key) || []; } catch (e) { trs = []; }
      const t = trs.find((x) => x.toCategory === zone);   // done 은 Resolved 우선(서버 정렬)
      if (!t) {
        pushToast({ kind: "error", title: key + " — 이동할 수 없습니다",
                    message: "현재 상태에서 그 상태로 가는 전이가 없습니다.", timeout: 5000 });
        return;
      }
      const fld = t.fields || {};
      if ((fld.fields || []).length || (fld.unsupported || []).length) {
        this.dragTrx = { ticket: key, transition: t };   // 필수 입력 → 다이얼로그
        return;
      }
      try {
        const r = await api.doTransition(key, { id: t.id });
        if (r && r.ok === false) throw new Error(r.error || "전이에 실패했습니다.");
        pushToast({ kind: "success", title: key + " → " + (t.to || "전이"), timeout: 3500 });
        window.dispatchEvent(new CustomEvent("ticket-changed", { detail: { key } }));
        if (r && r.cascade) window.dispatchEvent(new CustomEvent("cascade-prompt", { detail: r.cascade }));
      } catch (e) {
        pushToast({ kind: "error", title: key + " 전이 실패", message: (e && e.message) || "", timeout: 6000 });
      }
    },
    onDragTrxDone() {
      const k = this.dragTrx && this.dragTrx.ticket;
      this.dragTrx = null;
      if (k) window.dispatchEvent(new CustomEvent("ticket-changed", { detail: { key: k } }));
    },
  },
  template: `
  <div class="mytasks" :class="'ax-' + axis" :style="{ '--mt-cols': gridCols }">
    <!-- ══ 상단: 퀵필터(담당/보고/모듈) + 요약 타일 — 딱딱한 워크로드식 정형 ══ -->
    <div class="mt-top">
      <div class="mt-qf">
        <!-- 담당자 세그먼트: [담당자][picker: 나/이름] — 버튼이 스코프를 켜고 picker 가 대상을 정한다. -->
        <span class="mt-qf-seg">
          <button type="button" class="mt-qf-b seg-l" :class="{ on: scope === 'assignee' }"
                  @click="setScope('assignee')" title="담당자 기준으로 보기">담당자</button>
          <FieldEdit class="mt-qf-fe seg-r" :class="{ 'qf-on': scope === 'assignee' }" ticket="__filter__"
                     field="assignee" local :value="assigneeSel ? assigneeSel.id : ''"
                     :user-id="assigneeSel ? assigneeSel.id : ''" @pick="pickAssignee">
            <span class="qf-fe-v">{{ assigneeSel ? assigneeSel.name : '나' }}</span><span class="qf-fe-cav">▾</span>
          </FieldEdit>
        </span>
        <!-- 보고자 세그먼트 -->
        <span class="mt-qf-seg">
          <button type="button" class="mt-qf-b seg-l" :class="{ on: scope === 'reporter' }"
                  @click="setScope('reporter')" title="보고자 기준으로 보기">보고자</button>
          <FieldEdit class="mt-qf-fe seg-r" :class="{ 'qf-on': scope === 'reporter' }" ticket="__filter__"
                     field="reporter" local :value="reporterSel ? reporterSel.id : ''"
                     :user-id="reporterSel ? reporterSel.id : ''" @pick="pickReporter">
            <span class="qf-fe-v">{{ reporterSel ? reporterSel.name : '나' }}</span><span class="qf-fe-cav">▾</span>
          </FieldEdit>
        </span>
        <!-- 모듈 세그먼트: [모듈][콤보] — 콤보 선택은 포커스 떠나도 유지, 버튼이 모듈 스코프를 켠다. -->
        <span class="mt-qf-seg">
          <button type="button" class="mt-qf-b seg-l" :class="{ on: scope === 'module' }"
                  @click="setScope('module')" title="모듈 단위로 보기">모듈</button>
          <select class="mt-qf-sel seg-r" :class="{ on: scope === 'module' }"
                  :value="moduleSel || '__all__'"
                  @change="onModulePick($event.target.value)" title="모듈 선택(포커스 떠나도 유지)">
            <option value="__all__">{{ myModules.length ? '내 모듈 전체' : '모듈 전체' }}</option>
            <optgroup v-if="myModules.length" label="내 모듈">
              <option v-for="m in myModules" :key="'my-' + m" :value="m">{{ m }}</option>
            </optgroup>
            <optgroup v-if="otherModules.length" label="다른 모듈">
              <option v-for="m in otherModules" :key="'ot-' + m" :value="m">{{ m }}</option>
            </optgroup>
          </select>
        </span>

        <!-- 고급 검색: [고급 검색][JQL 입력][🔍] — 버튼은 빌더 다이얼로그, 우측 입력창은 JQL 직접 입력,
             돋보기(또는 Enter)로 그 JQL 을 Task 에 검색해 띄운다. -->
        <span class="mt-qf-seg mt-qf-adv" :class="{ 'qf-on': scope === 'jql' }">
          <button type="button" class="mt-qf-b seg-l" @click="advOpen = true"
                  title="조건을 조합해 JQL 만들기">고급 검색</button>
          <input class="mt-qf-jql" v-model="jqlText" @keydown.enter="runJql"
                 placeholder="JQL 직접 입력 또는 [고급 검색]" spellcheck="false" autocomplete="off" />
          <button type="button" class="mt-qf-jgo seg-r" @click="runJql" title="이 JQL 로 검색" aria-label="검색">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
          </button>
        </span>
      </div>
      <AdvancedSearchDialog v-if="advOpen" :projects="(me && me.searchProjects) || []" :my-id="myId"
                            :initial="jqlText" @apply="onAdvApply" @close="advOpen = false" />
      <div v-if="model && model.counts" class="mt-tiles">
        <div class="mt-tile over" :class="{ zero: !model.counts.overdue }">
          <b>{{ model.counts.overdue }}</b><span>지남</span></div>
        <div class="mt-tile today" :class="{ zero: !model.counts.today }">
          <b>{{ model.counts.today }}</b><span>오늘</span></div>
        <div class="mt-tile week" :class="{ zero: !model.counts.week }">
          <b>{{ model.counts.week }}</b><span>이번 주</span></div>
        <div class="mt-tile"><b>{{ model.counts.total }}</b><span>전체</span></div>
        <div class="mt-tile done"><b>{{ model.counts.done }}</b><span>최근 완료</span></div>
      </div>
    </div>

    <div v-if="err" class="mt-err">{{ err }}</div>

    <!-- ══ 상태 = 가로축 : 칼럼 헤더는 맨 위 한 줄, 그룹은 **각자 하나의 카드** 안에 3칼럼 ══
         (그룹마다 헤더를 반복하면 빈 헤더가 그룹 수만큼 늘어나고, 반대로 헤더만 위에 두고 카드를
          안 씌우면 어디부터 어디까지가 한 그룹인지 안 읽힌다 — 둘 다 피한 구조다.) -->
    <template v-if="axis === 'h'">
      <div class="mt-headrow">
        <div v-for="st in states" :key="'h-' + st.k" class="mt-colh"
             :class="['c-' + st.k, { closed: !bandOpen(st.k) }]">
          <button class="mt-colh-b" @click="toggleBand(st.k)"
                  :title="(bandOpen(st.k) ? '접기' : '펼치기') + ' — ' + st.label">
            <span class="chev" :class="{ open: bandOpen(st.k) }">▸</span>
            <template v-if="bandOpen(st.k)">{{ st.label }}</template>
            <b>{{ allCards.filter(c => c.statusCategory === st.k && (subView === 'all' || c.mine)).length }}</b>
          </button>
          <span v-if="bandOpen(st.k) && axisLoading(st.k)" class="mt-axis-load"
                :title="'완료된 JQL leaf ' + axisChunks(st.k) + '개 반영'">
            <i aria-hidden="true"></i>{{ axisChunks(st.k) ? axisChunks(st.k) + '차 반영' : '수집 중' }}
          </span>
          <!-- 표시 범위는 **그 칸에만** 걸리는 조건이라 그 칸의 제목에 둔다 -->
          <span v-if="bandOpen(st.k) && bandFilter(st.k)" class="mt-colf">
            <button v-for="o in bandFilter(st.k).opts" :key="o.k" type="button"
                    class="mt-colf-b" :class="{ on: opt(st.k) === o.k }"
                    :title="o.hint" @click.stop="setBandFilter(st.k, o.k)">{{ o.label }}</button>
          </span>
        </div>
      </div>

      <!-- 칼럼 배경 — 카드 뒤에 **한 겹으로** 깐다. 셀마다 칠하면 그룹 카드가 놓인 구간에서
           띠가 끊겨 '영역' 이 아니라 '카드 뒤 색칠' 로 보인다(실제로 그랬다). -->
      <div class="mt-hwrap">
        <div class="mt-colbg" aria-hidden="true">
          <div v-for="st in states" :key="'bg-' + st.k" :class="'c-' + st.k"></div>
        </div>

      <div v-if="!panels.length" class="mt-axis-shell">
        <div v-for="st in states" :key="'shell-' + st.k" :class="'c-' + st.k">
          <span v-if="axisLoading(st.k)"><i aria-hidden="true"></i>완료되는 티켓부터 표시합니다</span>
          <span v-else>해당 상태의 티켓 없음</span>
        </div>
      </div>

      <template v-for="p in panels" :key="p.key">
        <!-- 그룹화 없음 / 하위 없는 Task 묶음 — 묶을 게 없으니 카드 테두리도 없다 -->
        <div v-if="p.kind === 'none' || p.kind === 'solo'" class="mt-gbody plain">
          <div v-for="st in states" :key="'n-' + st.k" class="mt-cell"
               :class="['c-' + st.k, { empty: !byState(p.cards)[st.k].length,
                                                closed: !bandOpen(st.k) }]">
            <template v-for="c in byState(p.cards)[st.k]" :key="c.key">
              <TaskCard v-if="!c.compactPanel" :card="c"
                     :style="sigStyle(c)" :epic-title="epicDisplayTitle(c.epicKey)"
                     :epic-pending="epicPending(c.epicKey)" />
              <!-- 1축 Task 는 단독 Task 와 같은 셀·배열·TaskCard 를 그대로 쓴다.
                   공용 하단바와 폴더블 Sub-Task 목록만 부모 카드 바로 아래에 잇는다. -->
              <div v-else class="mt-compact-flow" :style="sigStyle(c)"
                   :class="{ folded: isGroupClosed(c.compactPanel), open: !isGroupClosed(c.compactPanel) }">
                <div class="mt-compact-head">
                  <TaskCard :card="c" :style="sigStyle(c)" :epic-title="epicDisplayTitle(c.epicKey)"
                            :epic-pending="epicPending(c.epicKey)" />
                </div>
                <SubtaskFoldBar :panel="c.compactPanel" :closed="isGroupClosed(c.compactPanel)"
                                @toggle="toggleGroup(c.compactPanel)" />
                <div v-if="!isGroupClosed(c.compactPanel)"
                     class="mt-gbody one mt-compact-children"
                     :class="{ foldwrap: foldable(c.compactPanel), folded: peeking(c.compactPanel),
                               'fold-peek': peeking(c.compactPanel) }">
                  <div v-for="sub in cellCards(c.compactPanel, c.compactPanel.singleStatus)" :key="sub.key" class="mt-card tkt"
                       :class="{ mine: sub.mine, rel: !sub.mine, done: sub.statusCategory === 'done',
                               urgent: isUrgentC(sub) }" :style="sigStyle(sub)" :data-key="sub.key">
                    <span v-if="isHotC(sub)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                    <PriIcon :rank="sub.priRank" :name="sub.pri" />
                    <span class="mt-key">{{ sub.key }}</span>
                    <span class="mt-title">{{ sub.title }}</span>
                    <span class="mt-subdue-sep" aria-hidden="true"></span>
                    <span class="mt-owner mt-sub-owner" :class="{ me: sub.mine }"
                          :title="(sub.assignee || '미할당') + ' 담당' + (sub.mine ? ' (나)' : '')">
                      <Avatar :user="sub.assigneeId" :name="sub.assignee" :size="15" />
                      <span class="mt-owner-name">{{ sub.assignee || '미할당' }}</span>
                    </span>
                    <DueText :card="sub" />
                  </div>
                  <button v-if="overflowed(c.compactPanel, c.compactPanel.singleStatus)" class="fold-b"
                          @click.stop="toggleSub(c.compactPanel.key)">{{
                          subOpen[c.compactPanel.key] ? '접기' : '+' + cellHidden(c.compactPanel, c.compactPanel.singleStatus) + '개 더' }}</button>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- Task 그룹 = 카드 하나 -->
        <div v-else-if="p.kind === 'task'" class="mt-gslot">
        <div class="mt-gcard2 k-task" :class="{ folded: isGroupClosed(p) }" :style="sigStyle(p.group)">
          <div class="mt-gh">
            <div class="mt-card parent tkt" :data-key="p.key" :style="sigStyle(p.group)"
                 :class="{ mine: p.group.mine, rel: !p.group.mine, done: p.group.statusCategory === 'done',
                        urgent: isUrgentC(p.group) }">
              <span v-if="isHotC(p.group)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
              <PriIcon :rank="p.group.priRank" :name="p.group.pri" />
              <TypeBadge :type="p.group.type" />
              <span class="mt-key">{{ p.key }}</span>
              <span class="mt-title">{{ p.group.voc ? vocStrip(p.title) : p.title }}</span>
              <span v-if="p.epicKey" class="mt-epic" :class="{ pending: epicPending(p.epicKey) }"
                    :title="'Epic: ' + epicDisplayTitle(p.epicKey)">{{ epicDisplayTitle(p.epicKey) }}</span>
              <span v-else-if="p.group.voc" class="mt-voc" :title="'사용자 VoC' + (vocSegs(p.title).length > 1 ? ' — ' + vocSegs(p.title).slice(1).join(' · ') : '')">
                <span v-for="(s, i) in vocSegs(p.title)" :key="i" class="mt-voc-seg" :class="{ head: i === 0 }">{{ s }}</span>
              </span>
              <span v-else class="mt-epic none">Epic 없음</span>
              <span class="mt-sep" aria-hidden="true"></span>
              <span class="mt-owner" :class="{ me: p.group.mine }"
                    :title="(p.group.assignee || '미할당') + ' 담당' + (p.group.mine ? ' (나)' : '')">
                <Avatar :user="p.group.assigneeId" :name="p.group.assignee" :size="16" />{{ p.group.assignee || '미할당' }}</span>
              <DueText :card="p.group" />

            </div>
          </div>
          <SubtaskFoldBar :panel="p" :closed="isGroupClosed(p)" @toggle="toggleGroup(p)" />
          <div v-if="!isGroupClosed(p)" class="mt-gbody">
            <div v-for="st in states" :key="p.key + st.k" class="mt-cell"
                 :class="['c-' + st.k, { empty: !byState(p.cards)[st.k].length,
                                                closed: !bandOpen(st.k),
                                                foldwrap: foldable(p),
                                                folded: peeking(p), 'fold-peek': peeking(p) }]">
                <div v-for="c in cellCards(p, st.k)" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done',
                             urgent: isUrgentC(c) }" :style="sigStyle(c)" :data-key="c.key">
                  <span v-if="isHotC(c)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                  <PriIcon :rank="c.priRank" :name="c.pri" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span class="mt-subdue-sep" aria-hidden="true"></span>
                  <span class="mt-owner mt-sub-owner" :class="{ me: c.mine }"
                        :title="(c.assignee || '미할당') + ' 담당' + (c.mine ? ' (나)' : '')">
                    <Avatar :user="c.assigneeId" :name="c.assignee" :size="15" />
                    <span class="mt-owner-name">{{ c.assignee || '미할당' }}</span>
                  </span>
                  <DueText :card="c" />
                </div>
                <!-- 넘친 칸에만 더보기가 붙지만, 누르면 **이 Task 의 모든 칸**이 함께 열린다.
                     접혀 있을 때는 흐려진 카드 **위에** 앉는다(.foldwrap.folded > .fold-b). -->
                <button v-if="overflowed(p, st.k)" class="fold-b" @click.stop="toggleSub(p.key)">{{
                        subOpen[p.key] ? '접기' : '+' + cellHidden(p, st.k) + '개 더' }}</button>
            </div>
          </div>
        </div>
        </div>

      </template>
      </div>
    </template>

    <!-- ══ 상태 = 세로축 : 상태 패널이 가로로 꽉 차서 쌓이고, 그 안에서 그룹이 좌우로 ══ -->
    <template v-else>
      <div v-for="st in states" :key="st.k" class="mt-band" :class="['c-' + st.k, { closed: !bandOpen(st.k) }]">
        <div class="mt-bandh-w">
          <button class="mt-bandh" @click="toggleBand(st.k)"
                  :title="bandOpen(st.k) ? '접기' : '펼치기'">
            <span class="chev" :class="{ open: bandOpen(st.k) }">▸</span>{{ st.label }}
            <b>{{ bandCount(st.k) }}</b>
          </button>
          <span v-if="axisLoading(st.k)" class="mt-axis-load"
                :title="'완료된 JQL leaf ' + axisChunks(st.k) + '개 반영'">
            <i aria-hidden="true"></i>{{ axisChunks(st.k) ? axisChunks(st.k) + '차 반영' : '수집 중' }}
          </span>
          <span v-if="bandFilter(st.k)" class="mt-colf">
            <button v-for="o in bandFilter(st.k).opts" :key="o.k" type="button"
                    class="mt-colf-b" :class="{ on: opt(st.k) === o.k }"
                    :title="o.hint" @click.stop="setBandFilter(st.k, o.k)">{{ o.label }}</button>
          </span>
        </div>
        <template v-if="bandOpen(st.k)">
        <!-- 그룹화 없음 → 카드 그리드 하나 -->
        <div v-if="groupBy === 'none'" class="mt-grid2">
          <TaskCard v-for="c in byState(panels[0].cards)[st.k]" :key="c.key" :card="c"
                   :style="sigStyle(c)" :epic-title="epicDisplayTitle(c.epicKey)"
                   :epic-pending="epicPending(c.epicKey)" />
          <div v-if="!byState(panels[0].cards)[st.k].length" class="mt-none">{{
            axisLoading(st.k) ? '완료되는 티켓부터 표시합니다' : '해당 상태의 티켓 없음' }}</div>
        </div>
        <!-- 그룹화 있음 → 그룹이 좌우로 늘어서고 각 그룹 안이 그리드 -->
        <div v-else class="mt-grouprow">
          <div v-if="!bandCount(st.k)" class="mt-none">{{
            axisLoading(st.k) ? '완료되는 티켓부터 표시합니다' : '해당 상태의 티켓 없음' }}</div>
          <template v-for="p in panels" :key="p.key">
            <!-- 하위 없는 Task 묶음 — 카드로만 -->
            <template v-if="p.kind === 'solo'">
              <TaskCard v-for="c in byState(p.cards)[st.k]" :key="'so-' + c.key" :card="c"
                   :style="sigStyle(c)" :epic-title="epicDisplayTitle(c.epicKey)"
                   :epic-pending="epicPending(c.epicKey)" />
            </template>
            <div v-else v-show="byState(p.cards)[st.k].length || (p.group.childrenPending && parentState(p) === st.k)" class="mt-gcard2 k-task"
                 :class="{ folded: isGroupClosed(p) }" :style="sigStyle(p.group)">
              <div class="mt-gh">
            <div class="mt-card parent tkt" :data-key="p.key" :style="sigStyle(p.group)"
                 :class="{ mine: p.group.mine, rel: !p.group.mine, done: p.group.statusCategory === 'done',
                        urgent: isUrgentC(p.group) }">
              <span v-if="isHotC(p.group)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
              <PriIcon :rank="p.group.priRank" :name="p.group.pri" />
              <TypeBadge :type="p.group.type" />
              <span class="mt-key">{{ p.key }}</span>
              <span class="mt-title">{{ p.group.voc ? vocStrip(p.title) : p.title }}</span>
              <span v-if="p.epicKey" class="mt-epic" :class="{ pending: epicPending(p.epicKey) }"
                    :title="'Epic: ' + epicDisplayTitle(p.epicKey)">{{ epicDisplayTitle(p.epicKey) }}</span>
              <span v-else-if="p.group.voc" class="mt-voc" :title="'사용자 VoC' + (vocSegs(p.title).length > 1 ? ' — ' + vocSegs(p.title).slice(1).join(' · ') : '')">
                <span v-for="(s, i) in vocSegs(p.title)" :key="i" class="mt-voc-seg" :class="{ head: i === 0 }">{{ s }}</span>
              </span>
              <span v-else class="mt-epic none">Epic 없음</span>
              <span class="mt-sep" aria-hidden="true"></span>
              <span class="mt-owner" :class="{ me: p.group.mine }"
                    :title="(p.group.assignee || '미할당') + ' 담당' + (p.group.mine ? ' (나)' : '')">
                <Avatar :user="p.group.assigneeId" :name="p.group.assignee" :size="16" />{{ p.group.assignee || '미할당' }}</span>
              <DueText :card="p.group" />

            </div>
              </div>
              <SubtaskFoldBar :panel="p" :closed="isGroupClosed(p)" @toggle="toggleGroup(p)" />
              <div v-if="!isGroupClosed(p)" class="mt-gbody one"
                   :class="{ foldwrap: foldable(p), folded: peeking(p), 'fold-peek': peeking(p) }">
                <div v-for="c in cellCards(p, st.k)" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done',
                             urgent: isUrgentC(c) }" :style="sigStyle(c)" :data-key="c.key">
                  <span v-if="isHotC(c)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                  <PriIcon :rank="c.priRank" :name="c.pri" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span class="mt-subdue-sep" aria-hidden="true"></span>
                  <span class="mt-owner mt-sub-owner" :class="{ me: c.mine }"
                        :title="(c.assignee || '미할당') + ' 담당' + (c.mine ? ' (나)' : '')">
                    <Avatar :user="c.assigneeId" :name="c.assignee" :size="15" />
                    <span class="mt-owner-name">{{ c.assignee || '미할당' }}</span>
                  </span>
                  <DueText :card="c" />
                </div>
                <!-- 넘친 칸에만 더보기가 붙지만, 누르면 **이 Task 의 모든 칸**이 함께 열린다.
                     접혀 있을 때는 흐려진 카드 **위에** 앉는다(.foldwrap.folded > .fold-b). -->
                <button v-if="overflowed(p, st.k)" class="fold-b" @click.stop="toggleSub(p.key)">{{
                        subOpen[p.key] ? '접기' : '+' + cellHidden(p, st.k) + '개 더' }}</button>
              </div>
            </div>
          </template>
        </div>
        </template>
      </div>
    </template>
    <!-- 옵션 패널 — 세 축을 여기서 정한다. **화면 하단에 띄운다**:
         이 화면의 본론은 목록이고 옵션은 가끔 건드리는 것이라, 위에 두면 스크롤 한 판을
         옵션이 먼저 먹는다. 마크업도 목록 뒤에 둬 탭 이동이 목록을 먼저 지나가게 한다
         (보이는 순서와 읽는 순서를 맞춘다). -->
    <div class="mt-bar float">
      <label v-for="o in options" :key="o.key" class="mt-opt">
        <span class="mt-opt-l" :class="o.cls">{{ o.label }}</span>
        <select class="mt-sel" :value="$data[o.key]" @change="setOpt(o, $event.target.value)"
                :title="hintOf(o)">
          <option v-for="v in o.opts" :key="v.k" :value="v.k" :title="v.hint">{{ v.label }}</option>
        </select>
      </label>

      <!-- Epic 필터 — 체크박스 다중선택 콤보. 내 Task 유관 Epic + 사용자 VoC + 소속 없음을 개별로. -->
      <div v-if="epicOptions.length || hasVocBucket || hasNoneBucket" class="mt-opt mt-epicf">
        <span class="mt-opt-l">Epic</span>
        <div class="mt-ef">
          <button class="mt-ef-btn" :class="{ on: !allEpicsShown }" @click.stop="epicOpen = !epicOpen"
                  :title="'Epic 필터 — ' + epicFilterLabel">
            {{ epicFilterLabel }}<i class="mt-ef-cav">▾</i>
          </button>
          <div v-if="epicOpen" class="mt-ef-back" @click="epicOpen = false"></div>
          <div v-if="epicOpen" class="mt-ef-pop" @click.stop>
            <button type="button" class="mt-ef-i master" @click="toggleAllEpics">
              <span class="mt-ef-ck" :class="{ on: allEpicsShown, ind: anyEpicHidden && !allEpicsShown }"></span>
              모든 Epic</button>
            <div class="mt-ef-sep"></div>
            <div class="mt-ef-list">
              <button v-for="e in epicOptions" :key="e.key" type="button" class="mt-ef-i" @click="toggleEpic(e.key)"
                      :title="e.title">
                <span class="mt-ef-ck" :class="{ on: !epicHidden[e.key] }"></span>
                <span class="mt-ef-dot" :style="{ background: e.color }"></span>
                <span class="mt-ef-t">{{ e.title }}</span></button>
              <!-- 사용자 VoC — Epic 은 아니지만 논리적으로 Epic 처럼. 끄면 Epic 에 안 속한 VoC 가 숨는다. -->
              <button v-if="hasVocBucket" type="button" class="mt-ef-i" @click="toggleEpic('__voc__')">
                <span class="mt-ef-ck" :class="{ on: !epicHidden['__voc__'] }"></span>
                <span class="mt-ef-dot" style="background: var(--ty-story)"></span>
                <span class="mt-ef-t">사용자 VoC</span></button>
              <button v-if="hasNoneBucket" type="button" class="mt-ef-i" @click="toggleEpic('__none__')">
                <span class="mt-ef-ck" :class="{ on: !epicHidden['__none__'] }"></span>
                <span class="mt-ef-dot none"></span><span class="mt-ef-t">Epic 없음</span></button>
            </div>
          </div>
        </div>
      </div>

      <!-- Project 필터 — Epic 필터와 같은 다중선택 콤보. jira.yml search 등록 프로젝트는 기본 체크,
           그 외(다른 프로젝트에 걸린 내 티켓 등)는 기본 언체크로 감춘다(콤보에서 켤 수 있다). -->
      <div v-if="projectOptions.length > 1 || anyProjectHidden" class="mt-opt mt-epicf">
        <span class="mt-opt-l">Project</span>
        <div class="mt-ef">
          <button class="mt-ef-btn" :class="{ on: !allProjectsShown }" @click.stop="projOpen = !projOpen"
                  :title="'Project 필터 — ' + projFilterLabel">
            {{ projFilterLabel }}<i class="mt-ef-cav">▾</i>
          </button>
          <div v-if="projOpen" class="mt-ef-back" @click="projOpen = false"></div>
          <div v-if="projOpen" class="mt-ef-pop" @click.stop>
            <button type="button" class="mt-ef-i master" @click="toggleAllProjects">
              <span class="mt-ef-ck" :class="{ on: allProjectsShown, ind: anyProjectHidden && !allProjectsShown }"></span>
              모든 Project</button>
            <div class="mt-ef-sep"></div>
            <div class="mt-ef-list">
              <button v-for="p in projectOptions" :key="p.key" type="button" class="mt-ef-i"
                      @click="toggleProj(p.key)" :title="p.registered ? p.key : p.key + ' — 검색 미등록'">
                <span class="mt-ef-ck" :class="{ on: projShown(p.key) }"></span>
                <span class="mt-ef-t">{{ p.key }}</span>
                <span v-if="!p.registered" class="mt-projf-x" title="jira.yml search 에 미등록">미등록</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ 카드 드래그 상태변경 — 오버레이 드랍가이드 ══
         가로축(칸반)이면 세로 3등분(위/중간/아래), 세로축이면 가로 3등분(좌/중/우).
         영역 밖 여백·틈에 놓으면 취소. 현재 상태 영역은 흐리게(놓아도 취소). -->
    <div v-if="drag" class="mtdnd-ov" :class="'ax-' + axis" aria-hidden="true">
      <div class="mtdnd-zones" :class="{ measured: !!drag.cols }">
        <div v-for="(st, i) in states" :key="'dz-' + st.k" class="mtdnd-z" :style="zoneStyle(i)"
             :class="['c-' + st.k, { hot: drag.zone === st.k, cur: drag.cat === st.k }]" :data-zone="st.k">
          <span class="mtdnd-zl"><em>To</em> {{ st.drop }}</span>
          <span v-if="drag.cat === st.k" class="mtdnd-zc">현재 상태</span>
        </div>
      </div>
      <div class="mtdnd-hint">영역 밖에 놓으면 취소 · ESC 취소</div>
      <div class="mtdnd-ghost" :style="{ left: drag.x + 'px', top: drag.y + 'px' }">{{ drag.key }} · {{ drag.title }}</div>
    </div>
    <!-- 드랍한 전이에 필수 입력(해결책 등)이 있으면 기존 전이 다이얼로그로 채운다 -->
    <TransitionDialog v-if="dragTrx" :ticket="dragTrx.ticket" :transition="dragTrx.transition"
                      @close="dragTrx = null" @done="onDragTrxDone()" />
  </div>`,
};
