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
import { categoryColor } from "../../lib/colors.js";
import { pushToast } from "../../lib/toast.js";

const NO_DUE = 1e6;


// 상태 축 — 순서가 곧 작업 흐름이다.
const STATES = [
  { k: "todo", label: "할당됨" },
  { k: "inprogress", label: "진행 중" },
  { k: "done", label: "최근 완료" },
];
// Epic 시그니처 컬러 — 같은 Epic 은 어느 화면·어느 카드에서도 같은 색.
// 사용자 VoC 는 Epic 이 없어도 **전용 Epic 처럼** 자기 색을 갖는다(Epic 이 배정돼 있으면 그쪽 우선).
// Epic 도 VoC 도 아니면 색을 주지 않는다 — 없는 소속을 색으로 지어내지 않는다.
const VOC_SIG = "var(--ty-story)";
function epicSig(card) {
  if (card.epicKey) return categoryColor(card.epicKey);
  if (card.voc) return VOC_SIG;
  return null;
}

// 옵션 정의 — 라벨/설명이 한곳에 있어야 콤보박스와 저장 키가 어긋나지 않는다.
// reload: true 면 서버 질의 조건이라 값이 바뀌면 다시 받아야 한다(클라이언트에서 거를 수 없다).
// 상태 축은 **옵션이 아니다**. 3칼럼이 들어가면 가로축, 안 들어가면 세로축 — 화면 폭이
// 답을 정해 놓았는데 사람에게 고르라고 하면, 좁은 화면에서 가로축을 골라 글자가 두 자씩
// 끊기는 칼럼을 보게 된다. 고를 수 있다고 더 나은 게 아니다.
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
  done: { key: "doneFilter", opts: [
    { k: "1w", label: "1주", hint: "최근 1주 안에 완료" },
    { k: "1m", label: "1달", hint: "최근 1달 안에 완료" }] },
};
const PREF_KEY = "mytasks.opts";

export default {
  name: "MyTasksView",
  components: { TypeBadge, Avatar, TaskCard, PriIcon, DueText, FieldEdit, AdvancedSearchDialog },
  data() {
    return {
      model: null, loading: true, err: "",
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
      const clientEval = ["assignee", "reporter", "mymodules", "module"].includes(this.scope);
      if (view && clientEval) { this._applyEditLocally(view); return; }   // 네트워크 없음
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
    this._mq = window.matchMedia(NARROW);
    this._onMq = (e) => { this.axis = e.matches ? "v" : "h"; };
    this._mq.addEventListener ? this._mq.addEventListener("change", this._onMq)
                              : this._mq.addListener(this._onMq);
  },
  unmounted() {
    window.removeEventListener("ticket-changed", this._onChanged);
    window.removeEventListener("force-refresh", this._fr);
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
      const out = this.groups.filter((g) => g.hasSubs && this.epicPass(g) && this.projPass(g) && !this.excluded[g.key]).map((g) => this.taskPanel(g))
        .sort((a, b) => a.rank[0] - b.rank[0] || a.rank[1] - b.rank[1]);
      const solo = this.soloPanel(this.groups.filter((g) => !g.hasSubs && this.epicPass(g) && this.projPass(g) && !this.excluded[g.key]));
      if (solo) out.push(solo);
      return out;
    },
  },
  methods: {
    /** quiet=true 면 스피너를 띄우지 않는다 — 전이 후처럼 **이미 보고 있는 화면**을 갱신할 때
     *  로딩 화면을 한 번 끼워 넣으면 목록이 통째로 사라졌다 나타나 '전체 새로고침' 으로 보인다.
     *  바뀐 건 티켓 하나인데 화면 전체가 깜빡일 이유가 없다. */
    async load(opts) {
      // 이 요청이 무엇을 받는지의 서명(스코프+상태필터). 캐시 키 겸 SWR 판정에 쓴다.
      const key = this.apiScope + "|" + this.openFilter + "|" + this.doneFilter;
      const cache = this._mcache || (this._mcache = {});
      // 예전에 받아 둔 같은 필터의 응답이 있으면 **즉시** 보여 준다(SWR) — 스피너 없이 바로 뜨고,
      // 아래에서 최신으로 조용히 갱신한다. 퀵필터를 오갈 때 체감이 즉각적이 된다.
      if (!(opts && opts.quiet) && cache[key]) { this.model = cache[key]; this.loading = false; }
      else if (!(opts && opts.quiet)) this.loading = true;
      this.err = "";
      if (Object.keys(this.excluded).length) this.excluded = {};   // 실 로딩이면 클라 이탈표시 초기화(목록이 새로 정확)
      // ★ 요청 순번 가드 — 퀵필터를 빠르게 바꾸면 요청이 여러 개 날아가는데, prod 지연 때문에
      //   먼저 보낸(지나간) 요청이 **나중에** 도착해 최신 화면을 옛 결과로 덮어쓴다(리포트된 버그:
      //   이미 다른 필터를 골랐는데 지나간 필터 내용이 렌더링됨). 순번이 어긋난 응답은 UI 에선 버린다.
      const seq = this._loadSeq = (this._loadSeq || 0) + 1;
      try {
        const model = await api.myTasks({ scope: this.apiScope, openFilter: this.openFilter,
                                          doneFilter: this.doneFilter });
        // ★ 성공 응답은 **순번과 무관하게** 캐시에 보관한다 — UI 가 버릴 응답이라도 성공했으면
        //   그 필터로 다시 왔을 때 즉시 쓸 수 있게 재활용한다(요청 헛수고 방지). 캐시는 12개로 제한.
        cache[key] = model;
        const ks = Object.keys(cache);
        if (ks.length > 12) delete cache[ks[0]];
        if (seq !== this._loadSeq) return;     // 더 최신 요청이 이미 떴다 — 화면엔 안 쓴다
        this.model = model;
      }
      catch (e) {
        if (seq !== this._loadSeq) return;     // 낡은 요청의 에러도 최신 화면에 씌우지 않는다
        this.err = (e && e.message) || "불러오기 실패";
      }
      finally { if (seq === this._loadSeq) this.loading = false; }
    },
    /** 티켓이 바뀌면 클라이언트 모델 캐시는 낡는다 — 통째로 비운다(서버 mt: 캐시도 같은 이유로 무효화). */
    _dropModelCache() { this._mcache = {}; },
    /** 이 티켓(수정 후 필드)이 **현재 퀵필터 스코프**에 여전히 맞는가 — 네트워크 없이 판정.
     *  담당/보고는 사번 일치, 모듈은 (모듈 인력 담당/보고) 또는 (모듈 컴포넌트). */
    _matchesScope(f) {
      const me = ((this.me && this.me.id) || (this.me && this.me.name) || "").toLowerCase();
      const a = (f.assigneeId || "").toLowerCase(), r = (f.reporterId || "").toLowerCase();
      if (this.scope === "assignee") return a === me;
      if (this.scope === "reporter") return r === me;
      const mods = this.scope === "mymodules" ? this.myModules : (this.moduleSel ? [this.moduleSel] : this.myModules);
      const mu = (this.me && this.me.moduleUsers) || {};
      const people = new Set();
      for (const m of mods) for (const u of (mu[m] || [])) people.add(String(u).toLowerCase());
      if (people.has(a) || people.has(r)) return true;
      const comps = new Set(mods);
      return (f.components || []).some((c) => comps.has(c));
    },
    /** 티켓 수정 알림을 **네트워크 없이** 반영 — 로컬 카드 필드 갱신(상태/담당/컴포넌트) 후
     *  현재 필터에서 이탈했으면 숨기고 우하단 토스트. */
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
      if (!this._matchesScope(f)) {
        this.excluded = Object.assign({}, this.excluded, { [key]: true });
        this._toastExcluded([key]);
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
    /** Task+SubTask 그룹 개별 접기/펴기. */
    toggleGroup(key) {
      this.gClosed = Object.assign({}, this.gClosed, { [key]: !this.gClosed[key] });
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
      const shown = mode === "collapsed" ? [] : (mode === "all" || !mineCards.length ? all : mineCards);
      return {
        key: g.key, kind: "task", group: g,
        title: g.title, epicKey: g.epic,
        mode, mineCount: mineCards.length, allCount: all.length,
        cards: this.sorted(shown),
        rank: this.rankOf(all),
      };
    },
    /** 하위 없는 Task 들을 묶음 없이 카드로만 모은 덩어리(그룹 패널이 아니다). */
    soloPanel(gs) {
      const cards = [];
      for (const g of gs) {
        for (const a of g.atoms) if (!this.excluded[a.key]) cards.push(this.card(a, g, true));
        for (const ot of g.others) if (!this.excluded[ot.key]) cards.push(this.card(ot, g, false));
      }
      const vis = this.visible(cards);
      if (!vis.length) return null;
      return { key: "__solo__", kind: "solo", cards: vis, rank: this.rankOf(cards) };
    },
    /** 패널의 카드를 상태별로 나눈다 — 상태 축이 가로든 세로든 이 함수를 쓴다. */
    byState(cards) {
      const m = { todo: [], inprogress: [], done: [] };
      for (const c of cards) (m[c.statusCategory] || m.todo).push(c);
      return m;
    },
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

    <div v-if="loading" class="loading">불러오는 중…</div>
    <div v-else-if="err" class="mt-err">{{ err }}</div>
    <div v-else-if="!panels.length" class="mt-empty">표시할 일감이 없습니다.</div>

    <!-- ══ 상태 = 가로축 : 칼럼 헤더는 맨 위 한 줄, 그룹은 **각자 하나의 카드** 안에 3칼럼 ══
         (그룹마다 헤더를 반복하면 빈 헤더가 그룹 수만큼 늘어나고, 반대로 헤더만 위에 두고 카드를
          안 씌우면 어디부터 어디까지가 한 그룹인지 안 읽힌다 — 둘 다 피한 구조다.) -->
    <template v-else-if="axis === 'h'">
      <div class="mt-headrow">
        <div v-for="st in states" :key="'h-' + st.k" class="mt-colh"
             :class="['c-' + st.k, { closed: !bandOpen(st.k) }]">
          <button class="mt-colh-b" @click="toggleBand(st.k)"
                  :title="(bandOpen(st.k) ? '접기' : '펼치기') + ' — ' + st.label">
            <span class="chev" :class="{ open: bandOpen(st.k) }">▸</span>
            <template v-if="bandOpen(st.k)">{{ st.label }}</template>
            <b>{{ allCards.filter(c => c.statusCategory === st.k && (subView === 'all' || c.mine)).length }}</b>
          </button>
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

      <template v-for="p in panels" :key="p.key">
        <!-- 그룹화 없음 / 하위 없는 Task 묶음 — 묶을 게 없으니 카드 테두리도 없다 -->
        <div v-if="p.kind === 'none' || p.kind === 'solo'" class="mt-gbody plain">
          <div v-for="st in states" :key="'n-' + st.k" class="mt-cell"
               :class="['c-' + st.k, { empty: !byState(p.cards)[st.k].length,
                                                closed: !bandOpen(st.k) }]">
            <TaskCard v-for="c in byState(p.cards)[st.k]" :key="c.key" :card="c"
                   :style="sigStyle(c)" :epic-title="epicTitle(c.epicKey)" />
          </div>
        </div>

        <!-- Task 그룹 = 카드 하나 -->
        <div v-else-if="p.kind === 'task'" class="mt-gcard2 k-task" :class="{ folded: gClosed[p.key] }" :style="sigStyle(p.group)">
          <div class="mt-gh">
            <button type="button" class="mt-fold" @click.stop="toggleGroup(p.key)"
                    :title="gClosed[p.key] ? '하위 펼치기' : '하위 접기'">
              <span class="chev" :class="{ open: !gClosed[p.key] }">▸</span></button>
            <div class="mt-card parent tkt" :data-key="p.key" :style="sigStyle(p.group)"
                 :class="{ mine: p.group.mine, rel: !p.group.mine, done: p.group.statusCategory === 'done',
                        urgent: isUrgentC(p.group) }">
              <span v-if="isHotC(p.group)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
              <PriIcon :rank="p.group.priRank" :name="p.group.pri" />
              <TypeBadge :type="p.group.type" />
              <span class="mt-key">{{ p.key }}</span>
              <span class="mt-title">{{ p.title }}</span>
              <!-- 진척은 **제목 바로 뒤**에 둔다(사용자 요청) — 이 묶음이 얼마나 됐나를 제목 옆에서
                   바로 읽는다. **몇 개 중 몇 개**로 적는다(퍼센트는 SP 가중이라 손으로 못 센다). -->
              <span v-if="p.group.pct !== null" class="mt-roll"
                    :title="'하위 ' + p.group.kidsDone + '/' + p.group.kidsTotal + ' 완료 (진척 ' + p.group.pct + '%)'">
                <span class="mt-pbar"><i :style="{ width: p.group.pct + '%' }"></i></span>
                <em>{{ p.group.kidsDone }}/{{ p.group.kidsTotal }}</em>
              </span>
              <span v-if="p.epicKey" class="mt-epic" :title="'Epic: ' + epicTitle(p.epicKey)">{{ epicTitle(p.epicKey) }}</span>
              <span v-else-if="p.group.voc" class="mt-epic">사용자 VoC</span>
              <span v-else class="mt-epic none">Epic 없음</span>
              <span class="mt-sep" aria-hidden="true"></span>
              <span class="mt-owner" :class="{ me: p.group.mine }"
                    :title="(p.group.assignee || '미할당') + ' 담당' + (p.group.mine ? ' (나)' : '')">
                <Avatar :user="p.group.assigneeId" :name="p.group.assignee" :size="16" />{{ p.group.assignee || '미할당' }}</span>
              <DueText :card="p.group" />

            </div>
          </div>
          <div v-if="p.mode !== 'collapsed' && !gClosed[p.key]" class="mt-gbody">
            <div v-for="st in states" :key="p.key + st.k" class="mt-cell"
                 :class="['c-' + st.k, { empty: !byState(p.cards)[st.k].length,
                                                closed: !bandOpen(st.k) }]">
                <div v-for="c in byState(p.cards)[st.k]" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done',
                             urgent: isUrgentC(c) }" :style="sigStyle(c)" :data-key="c.key">
                  <span v-if="isHotC(c)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                  <PriIcon :rank="c.priRank" :name="c.pri" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span v-if="!c.mine || subView === 'all'" class="mt-owner" :class="{ me: c.mine }"
                        :title="(c.assignee || '미할당') + ' 담당' + (c.mine ? ' (나)' : '')">
                <Avatar :user="c.assigneeId" :name="c.assignee" :size="15" />{{ c.assignee || '미할당' }}</span>
                  <DueText :card="c" />
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
                   :style="sigStyle(c)" :epic-title="epicTitle(c.epicKey)" />
          <div v-if="!byState(panels[0].cards)[st.k].length" class="mt-none">해당 상태의 티켓 없음</div>
        </div>
        <!-- 그룹화 있음 → 그룹이 좌우로 늘어서고 각 그룹 안이 그리드 -->
        <div v-else class="mt-grouprow">
          <template v-for="p in panels" :key="p.key">
            <!-- 하위 없는 Task 묶음 — 카드로만 -->
            <template v-if="p.kind === 'solo'">
              <TaskCard v-for="c in byState(p.cards)[st.k]" :key="'so-' + c.key" :card="c"
                   :style="sigStyle(c)" :epic-title="epicTitle(c.epicKey)" />
            </template>
            <div v-else v-show="byState(p.cards)[st.k].length" class="mt-gcard2 k-task"
                 :class="{ folded: gClosed[p.key] }" :style="sigStyle(p.group)">
              <div class="mt-gh">
            <button type="button" class="mt-fold" @click.stop="toggleGroup(p.key)"
                    :title="gClosed[p.key] ? '하위 펼치기' : '하위 접기'">
              <span class="chev" :class="{ open: !gClosed[p.key] }">▸</span></button>
            <div class="mt-card parent tkt" :data-key="p.key" :style="sigStyle(p.group)"
                 :class="{ mine: p.group.mine, rel: !p.group.mine, done: p.group.statusCategory === 'done',
                        urgent: isUrgentC(p.group) }">
              <span v-if="isHotC(p.group)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
              <PriIcon :rank="p.group.priRank" :name="p.group.pri" />
              <TypeBadge :type="p.group.type" />
              <span class="mt-key">{{ p.key }}</span>
              <span class="mt-title">{{ p.title }}</span>
              <!-- 진척을 제목 바로 뒤로(사용자 요청) -->
              <span v-if="p.group.pct !== null" class="mt-roll"
                    :title="'하위 ' + p.group.kidsDone + '/' + p.group.kidsTotal + ' 완료 (진척 ' + p.group.pct + '%)'">
                <span class="mt-pbar"><i :style="{ width: p.group.pct + '%' }"></i></span>
                <em>{{ p.group.kidsDone }}/{{ p.group.kidsTotal }}</em>
              </span>
              <span class="mt-sep" aria-hidden="true"></span>
              <span class="mt-owner" :class="{ me: p.group.mine }"
                    :title="(p.group.assignee || '미할당') + ' 담당' + (p.group.mine ? ' (나)' : '')">
                <Avatar :user="p.group.assigneeId" :name="p.group.assignee" :size="16" />{{ p.group.assignee || '미할당' }}</span>
              <DueText :card="p.group" />

            </div>
              </div>
              <div v-if="!gClosed[p.key]" class="mt-gbody one">
                <div v-for="c in byState(p.cards)[st.k]" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done',
                             urgent: isUrgentC(c) }" :style="sigStyle(c)" :data-key="c.key">
                  <span v-if="isHotC(c)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                  <PriIcon :rank="c.priRank" :name="c.pri" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span v-if="!c.mine || subView === 'all'" class="mt-owner" :class="{ me: c.mine }"
                        :title="(c.assignee || '미할당') + ' 담당' + (c.mine ? ' (나)' : '')">
                <Avatar :user="c.assigneeId" :name="c.assignee" :size="15" />{{ c.assignee || '미할당' }}</span>
                  <DueText :card="c" />
                </div>
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
  </div>`,
};
