// TicketDialog.js — 인앱 티켓 상세 다이얼로그(모달). 티켓 링크(.tkt) 클릭 시 app-root 가 keyId 를 넘겨 연다.
// description 은 백엔드에서 **정화된 HTML**(app/htmlsafe.py) 이라 v-html 로 그대로 렌더(table/code/quote/panel/callout/img).
// 코멘트 텍스트는 Vue 기본 이스케이프({{ }})로 안전 표시. Esc/백드롭/X 로 닫기.
import { api } from "../../lib/api.js";
import { extOf } from "../../lib/filetype.js";
import FieldEdit from "./FieldEdit.js";
import PriIcon, { priRankOf } from "./PriIcon.js";
import { ymd, ymdhm, ts, esc } from "../../lib/fmt.js";
import { TYPE_BG, typeLabel, sigColor, categoryColor } from "../../lib/colors.js";
import TypeBadge from "./TypeBadge.js";
import Avatar from "./Avatar.js";
import CommentEditor from "./CommentEditor.js";
import SettingsMenu from "./SettingsMenu.js";
import LinkPicker from "./LinkPicker.js";
import TransitionDialog from "./TransitionDialog.js";
import DueText from "./DueText.js";
import NewChildDialog from "./NewChildDialog.js";
import { fromBackdrop } from "../../lib/backdrop.js";

// 목록이 이보다 길면 기본으로 접는다. 첨부가 스무 개인 티켓에서 본문·코멘트가 화면 밖으로
// 밀려나는 걸 막는다 — 몇 개인지는 제목 옆 숫자로 이미 알 수 있다.
const FOLD_AT = 5;

// 하위 Task 정렬 기준 — '내 Task' 와 같은 축(마감·우선순위) + 사람별 보기.
const KID_SORTS = [
  { k: "due", label: "마감", hint: "마감일 → 우선순위" },
  { k: "pri", label: "우선순위", hint: "우선순위 → 마감일" },
  { k: "who", label: "담당자", hint: "담당자 이름 → 마감일" },
];
const KID_SORT_KEY = "tkt.kidSort";

function loadKidSort() {
  try {
    const v = localStorage.getItem(KID_SORT_KEY);
    return KID_SORTS.some((o) => o.k === v) ? v : "due";
  } catch (e) { return "due"; }
}

/** 오늘부터 마감까지 남은 날. 없으면 null(= '미정'). */
function daysTo(iso) {
  if (!iso) return null;
  const due = new Date(String(iso).substring(0, 10) + "T00:00:00");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const d = Math.round((due - today) / 86400000);
  return isNaN(d) ? null : d;
}
import { recordOpen } from "../../lib/recent.js";
import { highlightIn as hljsHighlight, ensureHljsTheme } from "../../lib/hljs.js";
import { loadTiptap } from "../../lib/tiptap.js";
import { confirmBox } from "../../lib/confirm.js";
import { pushToast } from "../../lib/toast.js";
import { copyTicketLink } from "../../lib/ticketlink.js";


// Confluence URL 에서 문서 제목 추출(내부 <a> 텍스트 무시) — /pages/{id}/{slug} 또는 /display/{space}/{slug}.
function confTitleFromUrl(u) {
  try {
    const path = new URL(u, location.href).pathname;
    const m = path.match(/\/pages\/\d+\/([^/]+)\/?$/) || path.match(/\/display\/[^/]+\/([^/]+)\/?$/);
    if (m && m[1]) return decodeURIComponent(m[1].replace(/\+/g, " ")).trim();
  } catch (e) { /* noop */ }
  return null;
}
const SPINE_W_KEY = "tkt.spineW";
const SPINE_HIDE_KEY = "tkt.spineHidden";
function loadSpineW() {
  try { const v = parseInt(localStorage.getItem(SPINE_W_KEY), 10); if (v >= 180 && v <= 460) return v; } catch (e) { /* noop */ }
  return 264;
}
function loadSpineHidden() {
  try { return localStorage.getItem(SPINE_HIDE_KEY) === "1"; } catch (e) { return false; }
}
// 우측 타임라인 패널도 좌측 스파인과 같은 규칙으로 폭 조절·접기(각자 저장).
const TL_W_KEY = "tkt.tlW";
const TL_HIDE_KEY = "tkt.tlHidden";
function loadTlW() {
  try { const v = parseInt(localStorage.getItem(TL_W_KEY), 10); if (v >= 170 && v <= 440) return v; } catch (e) { /* noop */ }
  return 220;
}
function loadTlHidden() {
  try { return localStorage.getItem(TL_HIDE_KEY) === "1"; } catch (e) { return false; }
}

const _BROWSE_RE = /\/browse\/([A-Z][A-Z0-9]+-\d+)/;

// 설명이 "실제로" 비었는지 — HTML 문자열이 아니라 **렌더 텍스트**를 trim 해서 본다.
// (<p></p>, <p class="blank">, &nbsp; 처럼 태그는 있어도 화면엔 아무것도 안 보이는 경우가 흔함)
// 글자가 없어도 이미지·표·코드·목록이 있으면 내용이 있는 것으로 본다.
// 입력 HTML 은 서버에서 이미 정화(app/htmlsafe.py)됐고 여기선 읽기만 한다.
function descEmpty(html) {
  if (!html) return true;
  const d = document.createElement("div");
  d.innerHTML = html;
  if (d.querySelector("img, table, pre, code, li, blockquote")) return false;
  return !(d.textContent || "").replace(/\u00a0/g, " ").trim();
}

export default {
  name: "TicketDialog",
  components: { TypeBadge, Avatar, CommentEditor, SettingsMenu, LinkPicker, FieldEdit, PriIcon,
                TransitionDialog, DueText, NewChildDialog },
  // mode: dialog(모달, 기본) | page(새 창 전용 단독 페이지 — 오버레이·닫기 없음)
  props: { keyId: { type: String, required: true },
           mode: { type: String, default: "dialog" },
           theme: { type: String, default: "light" } },   // 페이지 모드 테마 버튼 표시용
  emits: ["close", "search", "toggle-theme"],
  data() { return { v: null, comments: null, ancestors: [], siblings: [], timeline: [], children: [], related: [], atts: [], docs: [],
                    pdesc: null, pdescOpen: false, pdescErr: "",
                    me: null, composing: false, editingId: null, editInitial: "", editErr: "",
                    cmtSort: "new",              // new=최신순(기본) | old=오래된순. 초 단위까지 비교.
                    // 링크 추가(관련 티켓/관련문서) · 파일 첨부(＋ 버튼 · 드래그앤드롭)
                    relPick: false, linkBusy: false, linkErr: "",
                    docPick: false, docBusy: false, docErr: "",
                    uploading: false, upErr: "", dragOver: false, dragInEditor: false,
                    // 편집 가능 필드 — Jira 가 답한 것만 편집 UI 를 연다(추측 금지).
                    emeta: null, descEdit: false, descBusy: false, descErr: "",
                    // 본문 편집을 시작한 시점의 본문 + 그 뒤 남이 고쳤는가
                    descBase: "", descConflict: false,
                    // 상태 전이 팝업
                    stOpen: false, stInfo: null, stErr: "", stPick: null,
                    err: "", expanded: false, zoom: null, zoomLoading: false,
                    refreshing: false,               // 좌하단 강제 새로고침 진행 표시
                    sumEdit: false, sumDraft: "", sumBusy: false, sumErr: "",   // 제목(summary) 인라인 수정
                    // 좌/우 부가정보 패널 — 폭 조절·접기(저장). 넓은 화면(사이드바 모드)에서만 의미.
                    spineW: loadSpineW(), spineHidden: loadSpineHidden(),
                    tlW: loadTlW(), tlHidden: loadTlHidden(),
                    // 목록이 길면 기본으로 접는다(FOLD_AT 초과). 몇 개인지는 제목 옆 숫자로 안다.
                    attOpen: true, docOpen: true,
                    kidSort: loadKidSort(),
                    // 하위 티켓 만들기 — 만들 수 있는 타입은 부모가 정한다(서버가 다시 검사한다)
                    kidTypes: [], adding: false }; },
  mounted() {
    // Esc: 위에 뜬 것부터 하나씩 닫는다(확대 → 상태 팝업 → 다이얼로그).
    // 안쪽 것을 두고 다이얼로그가 먼저 닫히면, 열려 있던 팝업의 투명 배경(fe-back)이 남아
    // 화면 전체가 안 눌리는 상태가 된다 — 실제로 그렇게 막혔다.
    this._onKey = (e) => {
      if (e.key !== "Escape") return;
      if (this.zoom) { this.zoom = null; }
      else if (this.stOpen) { this.stOpen = false; }
      else { this.$emit("close"); }
    };
    window.addEventListener("keydown", this._onKey);
    // 창 크기가 바뀌면 보이는 중앙도 바뀐다 → 접기버튼 재배치.
    window.addEventListener("resize", this._onResize = () => this.posCollapse());
    // 관련 티켓(내 하위/형제)이 **다른 곳에서** 바뀌면 그 패널만 조용히 다시 받는다(UI 안 멈춤).
    // 내 변경(전이·필드·하위생성)은 각 핸들러가 이미 처리하므로 changed===내키 는 건너뛴다.
    window.addEventListener("ticket-changed", this._onExtChanged = (e) => {
      const changed = e && e.detail && e.detail.key;
      if (!changed || changed === this.keyId) return;
      const inChildren = (this.children || []).some((c) => c.key === changed);
      const inSiblings = (this.siblings || []).some((s) => s.key === changed);
      if (!inChildren && !inSiblings) return;
      api.evict(this.keyId);        // 내 관련 GET memo 를 비워 아래 재조회가 최신을 읽게(memo 는 키별)
      if (inChildren) { this.reloadChildren(); this.reloadLineage(); this.softReloadView(); }
      else this.reloadLineage();    // 형제 하나가 바뀜 → 형제 목록만
    });
    this.$nextTick(() => this.posCollapse());
    // 재인증(auth-ok) 후 — 티켓 로딩 중 세션이 끊겨 본문이 안 뜬 채 굳은 경우 다시 받는다.
    // (정상 로드된 창은 건드리지 않는다 — 쓰던 글·스크롤 보존.)
    window.addEventListener("auth-ok", this._authok = () => { if (this.err || !this.v) this.load(); });
    this.load();
    // 에디터·구문강조 CDN 프리로드 — 티켓 다이얼로그/풀뷰가 열리는 시점에 미리 받아둔다.
    // (버전 고정 URL 이라 브라우저가 장기 캐시 → 이후엔 네트워크 없이 즉시.) '댓글 달기' 지연 제거.
    ensureHljsTheme(document.documentElement.getAttribute("data-theme") === "dark");
    loadTiptap().catch(() => { /* CDN 차단 등 — 실제 사용 시 에러 표시 */ });
  },
  unmounted() {
    window.removeEventListener("keydown", this._onKey);
    window.removeEventListener("resize", this._onResize);
    window.removeEventListener("ticket-changed", this._onExtChanged);
    window.removeEventListener("auth-ok", this._authok);
  },
  computed: {
    FOLD_AT: () => FOLD_AT,
    /** 하위가 무엇인지는 **내 타입**이 정한다 — Epic 밑은 Task, Task 밑은 Sub-Task.
     *  Sub-Task 밑은 없다(Jira 가 3단까지만 둔다) → 그때만 칸을 안 그린다.
     *  판정은 issuetype.subtask 로 한다 — 타입 **이름**은 인스턴스·로케일마다 다르다. */
    isEpic() { return !!this.v && this.v.type === "Epic"; },
    canHaveKids() { return !!this.v && !this.v.subtask; },
    kidsLabel() { return this.isEpic ? "소속 Task" : "하위 Sub-Task"; },
    KID_SORTS: () => KID_SORTS,
    /** 정렬된 하위 목록. 기준은 '내 Task' 와 같다:
     *    마감 → (마감일, 우선순위) / 우선순위 → (우선순위, 마감일)
     *  담당자 기준은 이름으로 먼저 모으고 **그 안에서 마감 순**이다 — 사람별로 보는 이유가
     *  '이 사람이 다음에 뭘 해야 하나' 라서, 이름만 맞추고 안이 뒤죽박죽이면 쓸모가 없다.
     *  완료된 하위는 어느 기준에서도 맨 뒤로 간다(담당자 기준에서는 사람별로).
     *  마감 없음도 맨 뒤다(언제까지인지 모르는 일이 급한 일보다 앞에 설 이유가 없다). */
    kidsSorted() {
      const NO_DUE = 99999;
      const dd = (c) => {
        const v = this.kidCard(c).dueDays;
        return v === null || v === undefined ? NO_DUE : v;
      };
      const pr = (c) => (c.priRank === null || c.priRank === undefined ? 2 : c.priRank);
      // 미할당은 이름이 없다 — 사람 뒤에 모은다(빈 문자열이 앞에 서면 목록이 '아무도' 로 시작한다)
      const who = (c) => (c.assignee || "\uffff");
      // ★ **완료는 늘 맨 뒤.** 끝난 일은 마감이 아무리 지났어도 지금 할 일이 아니다 — 위에 두면
      //   목록의 첫 줄들이 이미 끝난 일로 채워져 '다음에 뭘 하지' 를 못 읽는다.
      //   담당자 기준에서는 **사람별로** 뒤로 보낸다(사람 묶음을 깨면 사람별로 보는 뜻이 없다).
      const fin = (c) => (c.statusCategory === "done" ? 1 : 0);
      const rest = (a, b) => dd(a) - dd(b) || pr(a) - pr(b) || a.key.localeCompare(b.key);
      const byDue = (a, b) => fin(a) - fin(b) || rest(a, b);
      const byPri = (a, b) => fin(a) - fin(b) || pr(a) - pr(b) || dd(a) - dd(b)
                              || a.key.localeCompare(b.key);
      const cmp = this.kidSort === "pri" ? byPri
                : this.kidSort === "who"
                  ? ((a, b) => who(a).localeCompare(who(b), "ko") || fin(a) - fin(b) || rest(a, b))
                  : byDue;
      return (this.children || []).slice().sort(cmp);
    },
    /** 만들 수 있는 타입이 있고 이 티켓을 손댈 수 있을 때만 추가 UI 를 연다.
     *  못 만드는데 버튼만 있으면 다 적은 뒤 거절당한다 — 그건 기능이 아니라 함정이다. */
    kidCreate() { return this.kidTypes.length > 0; },
    canCreate() {
      return !!(this.nc.type && this.nc.priority && (this.nc.summary || "").trim());
    },
    /** 관련문서 = **사람이 붙인** 문서만. 멘션 링크는 아래 mentionDocs 로 간다. */
    refDocs() { return (this.docs || []).filter((d) => !d.mention); },
    /** 이 티켓을 언급해서 자동으로 생긴 링크 — 좌측 패널에 따로 모은다. */
    mentionDocs() { return (this.docs || []).filter((d) => d.mention); },
    /** 소속 Epic 의 제목 — 계보 패널이 이미 받아 둔 것을 쓴다(따로 조회하지 않는다).
     *  아직 안 왔거나 없으면 키를 그대로 — 빈 뱃지를 보이느니 번호라도 보이는 게 낫다. */
    epicTitle() {
      const k = this.v && this.v.epicKey;
      if (!k) return "";
      const a = (this.ancestors || []).find((x) => x.key === k);
      // 에픽 뱃지 라벨 규칙(전 화면 공통): Epic Name(단축어) → Summary → 티켓 키.
      return (a && (a.epicName || a.summary)) || k;
    },
    /** 전이 목록·권한 — 우클릭 메뉴와 같은 응답에서 꺼낸다(판정이 갈리면 안 된다). */
    stList() { return (this.stInfo && this.stInfo.transitions) || []; },
    stMayEdit() { return !!(this.stInfo && this.stInfo.mayEdit); },
    /** 이 티켓을 손댈 수 있는가 — editmeta 에 고칠 수 있는 필드가 하나라도 있으면 그렇다.
     *  (서버가 판정한 결과라 우리가 추측하지 않는다. 삭제는 서버가 한 번 더 막는다.) */
    mayEdit() { return !!(this.emeta && Object.keys(this.emeta).length); },
    /** Epic Link 필드 id — 인스턴스마다 다른 커스텀필드라 이름으로 찾는다(하드코딩 금지). */
    epicFieldId() {
      const m = this.emeta || {};
      for (const k of Object.keys(m)) {
        if (/epic\s*link/i.test(m[k].name || "")) return k;
      }
      return "__no_epic__";
    },
    today() { return ymd(new Date().toISOString()); },   // 기한 초과 판정용
    tk() { return (this.v && this.v.key) || this.keyId; },   // 쓰기 대상 티켓 키
    // 코멘트 정렬 — created 를 ms 로 파싱해 **초 단위까지** 비교(같은 분에 여러 개 달려도 안정).
    sortedComments() {
      const list = (this.comments || []).slice();
      const t = (c) => { const n = Date.parse(c && c.date); return isNaN(n) ? 0 : n; };
      list.sort((a, b) => (this.cmtSort === "old" ? t(a) - t(b) : t(b) - t(a)));
      return list;
    },
    /** 좌측 부가정보 패널을 그릴 거리가 있는가(계보/형제/타임라인 중 하나라도). */
    hasSpine() { return this.spine.length > 1 || this.siblings.length > 0 || this.timeline.length > 0; },
    /** 우측 타임라인 패널을 그릴 거리가 있는가(일정 또는 이력). */
    hasTl() { return !!(this.v || this.timeline.length); },
    // 스파인 계보 = [조상…, 현재]. 조상만 도착해도 그릴 수 있어야 하므로 **v 를 기다리지 않는다**
    // (현재 노드는 keyId 로 먼저 그리고, v 가 오면 제목·타입이 채워진다).
    spine() {
      const anc = this.ancestors || [];
      if (!anc.length) return [];              // 조상 없으면 계보 블록 자체를 생략
      const v = this.v;
      return anc.concat([{ key: this.keyId, summary: v ? v.summary : "", type: v ? v.type : "",
                           statusCategory: v ? v.statusCategory : "todo", pct: null, current: true }]);
    },
    // Sub-Task 의 직계 상위(조상 체인의 마지막) — '상위 설명 보기' 대상
    parentOf() {
      if (!this.v || !this.v.subtask) return null;
      const a = this.ancestors || [];
      return a.length ? a[a.length - 1] : null;
    },
    // 이 티켓 자체에 설명이 비었는지 (실무상 Sub-Task 설명은 대충 쓰는 경우가 많다)
    isPage() { return this.mode === "page"; },
    // 새 창 링크 — Jira 와 같은 /browse/{키} 형태
    pageHref() { return "/browse/" + encodeURIComponent(this.keyId); },
    ownDescEmpty() { return descEmpty(this.v && this.v.descriptionHtml); },
    // 타이틀바 툴팁 — 요청 포맷 그대로 "[타입] [번호] [제목] - 상태"
    barTitle() {
      const v = this.v;
      if (!v) return this.keyId;
      return `${v.type} ${v.key} ${v.summary}` + (v.status ? ` - ${v.status}` : "");
    },
    // '=== 제목 ===' 구분선으로 나뉜 영역들. 백엔드가 항상 1개 이상 주지만
    // (구버전 캐시 등) 없으면 통짜 descriptionHtml 하나로 폴백한다.
    descSections() {
      const v = this.v; if (!v) return [];
      const secs = v.descriptionSections;
      return (secs && secs.length) ? secs : [{ title: null, html: v.descriptionHtml || "" }];
    },
    // 형제 중 현재 위치 (1-based, 없으면 0)
    sibPos() { return (this.siblings || []).findIndex((s) => s.current) + 1; },
    // 현재 티켓의 대표 컴포넌트(모듈) — 형제 중 타 모듈을 흐리게 하는 기준
    myComp() { return (this.v && this.v.components && this.v.components[0]) || null; },
  },
  watch: {
    keyId() { this.load(); },
    v() { this.$nextTick(() => { this.augment(); this.posCollapse(); }); },   // 설명 렌더 후 확대버튼·뱃지 + 접기버튼 위치
    comments() { this.$nextTick(() => { this.augment(); this.posCollapse(); }); },  // 코멘트 렌더 후
    pdesc() { this.$nextTick(this.augment); },        // 상위 설명 렌더 후(확대버튼·뱃지 주입)
    // 이 Sub-Task 에 설명이 없으면 상위 설명을 자동으로 펼친다(가장 흔한 케이스)
    parentOf(p) { if (p && this.ownDescEmpty && !this.pdescOpen) this.toggleParentDesc(); },
  },
  methods: {
    /** 이 티켓의 Jira 링크를 클립보드로 복사. */
    async copyLink() {
      const { ok, url } = await copyTicketLink(this.keyId);
      pushToast(ok
        ? { kind: "success", icon: "📋", title: "링크 복사됨", message: url, timeout: 4000 }
        : { kind: "error", icon: "⚠", title: "복사 실패", message: url, timeout: 6000 });
    },
    /** 좌우 패널 접기/펴기 버튼을 **지금 보이는 스크롤 영역의 세로 중앙**에 둔다.
     *  본문(.tkt-body)이 스크롤 주체라 그 scrollTop+높이의 절반이 곧 화면 중앙(콘텐츠 좌표계). */
    posCollapse() {
      const b = this.$refs.body;
      if (!b) return;
      b.style.setProperty("--cb-top", Math.round(b.scrollTop + b.clientHeight / 2) + "px");
    },
    setSpineHidden(v) {
      this.spineHidden = v;
      try { localStorage.setItem(SPINE_HIDE_KEY, v ? "1" : "0"); } catch (e) { /* noop */ }
    },
    startSpineDrag(e) {
      const x0 = e.clientX, w0 = this.spineW;
      const onMove = (ev) => {
        // 왼쪽 패널이라 오른쪽으로 끌면 넓어진다. 너무 좁으면 목록이 잘리고, 너무 넓으면
        // 본문이 좁아진다 — 180~460 으로 묶는다.
        this.spineW = Math.max(180, Math.min(460, w0 + (ev.clientX - x0)));
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        document.body.style.userSelect = "";
        try { localStorage.setItem(SPINE_W_KEY, String(this.spineW)); } catch (e) { /* noop */ }
      };
      document.body.style.userSelect = "none";   // 드래그 중 글자 선택 방지
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    setTlHidden(v) {
      this.tlHidden = v;
      try { localStorage.setItem(TL_HIDE_KEY, v ? "1" : "0"); } catch (e) { /* noop */ }
    },
    startTlDrag(e) {
      const x0 = e.clientX, w0 = this.tlW;
      const onMove = (ev) => {
        // 오른쪽 패널이라 **왼쪽으로** 끌면 넓어진다(델타 부호가 스파인과 반대).
        this.tlW = Math.max(170, Math.min(440, w0 - (ev.clientX - x0)));
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        document.body.style.userSelect = "";
        try { localStorage.setItem(TL_W_KEY, String(this.tlW)); } catch (e) { /* noop */ }
      };
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    // 드래그가 창 밖에서 끝났을 뿐인데 닫히지 않게 — lib/backdrop.js 참고
    fromBackdrop,
    extOf,
    // 본문(Description)·코멘트·계보(조상/형제)·타임라인을 **동시에 출발**시키고 각자 도착하는 대로
    // 개별 렌더한다(서로 막지 않음). 느린 타임라인이 본문을 기다리지 않게 하는 게 핵심.
    // 다이얼로그는 계보/형제/타임라인 클릭으로 티켓을 갈아타므로, 늦게 온 이전 티켓 응답이
    // 새 티켓 화면을 덮지 않도록 요청 토큰(_req)으로 가드한다.
    setKidSort(k) {
      this.kidSort = k;
      // 고른 기준은 기억한다 — 매번 고르게 하면 그건 기능이 아니라 숙제다.
      try { localStorage.setItem(KID_SORT_KEY, k); } catch (e) { /* 사파리 프라이빗 등 */ }
    },
    /** 하위가 생기면 하위목록뿐 아니라 **내 진척(계보 캡슐)·형제**(내가 subtask면)도 바뀐다 —
     *  관련 패널을 백그라운드로 함께 갱신하고, 다른 화면(내 Task 등)·부모에도 알린다. */
    async onKidCreated() {
      this.adding = false;
      await this.reloadChildren();
      this.reloadLineage();
      window.dispatchEvent(new CustomEvent("ticket-changed", { detail: { key: this.keyId } }));
    },
    reloadChildren() {
      const key = this.keyId;
      return api.ticketChildren(key)
        .then((c) => { if (this.keyId === key) this.children = c || []; }).catch(() => {});
    },
    /** 형제·조상(진척 캡슐) 재조회 — 하위/형제 변화가 이 둘에 반영된다. 형제 목록은 서버에서
     *  부모별 공유 캐시라, 형제 하나가 바뀌면 부모 그룹 무효화로 여기서 최신을 받는다. */
    reloadLineage() {
      const key = this.keyId;
      api.ticketSiblings(key).then((s) => { if (this.keyId === key) this.siblings = s || []; }).catch(() => {});
      api.ticketAncestors(key).then((a) => { if (this.keyId === key) this.ancestors = a || []; }).catch(() => {});
    },
    /** 상태 전이 완료 — 내 뷰를 다시 받고(load), 다른 화면·부모에도 알린다(형제·부모 진척 갱신). */
    onTransitioned() { this.stPick = null; this.onFieldSaved(); },
    /** 본문 깜빡임 없이 요약 뷰(상태·진척 등)만 갱신 — 하위 상태변경이 내 진척 %를 바꿀 때. */
    softReloadView() {
      const key = this.keyId;
      api.ticket(key, true).then((v) => { if (this.keyId === key && v) this.v = v; }).catch(() => {});
    },
    /** 본문 편집 열기 — **열기 직전에 본문을 다시 받는다.**
     *  화면에 떠 있던 본문은 이 창을 연 시점의 것이라, 그 사이 남이 고쳤으면 낡은 글 위에
     *  저장하게 되고 남의 수정이 조용히 사라진다. 못 받으면 지금 화면의 본문으로 연다
     *  (편집 자체를 막을 이유는 없다 — 저장할 때 다시 실패하면 그때 알린다). */
    async startDescEdit() {
      this.descErr = ""; this.descConflict = false;
      try {
        const v = await api.ticket(this.keyId, true);      // 캐시 건너뛰기 — 기준이 낡으면 안 된다
        if (v && this.v && v.descriptionHtml !== undefined) {
          this.v.descriptionHtml = v.descriptionHtml;
          this.v.descriptionSections = v.descriptionSections;
        }
      } catch (e) { /* 지금 화면의 본문으로 연다 */ }
      // 이 순간의 본문이 **내가 고치기 시작한 기준**이다. 뒤에 이게 달라지면 남이 손댄 것.
      this.descBase = (this.v && this.v.descriptionHtml) || "";
      this.descEdit = true;
      this.watchDesc();
    },
    /** 편집 중 본문이 남에 의해 바뀌었는지 지켜본다. 서버가 알려 줄 방법이 없어 주기적으로 묻되,
     *  간격을 넓게 둔다 — prod 는 상류가 한 줄(SSO 세션)이라 잦은 조회가 다른 요청을 밀어낸다.
     *  한 번 알리면 멈춘다(같은 말을 반복할 이유가 없다). */
    watchDesc() {
      this.stopWatchDesc();
      this._descT = setInterval(async () => {
        if (!this.descEdit) { this.stopWatchDesc(); return; }
        try {
          const v = await api.ticket(this.keyId, true);
          if (!v || v.descriptionHtml === undefined) return;
          if ((v.descriptionHtml || "") !== this.descBase) {
            this.descConflict = true;
            this.stopWatchDesc();
          }
        } catch (e) { /* 못 물어봤을 뿐 — 조용히 다음 차례에 다시 */ }
      }, 30000);
    },
    stopWatchDesc() { if (this._descT) { clearInterval(this._descT); this._descT = null; } },
    /** 상태 전이 목록 — 카드 우클릭 메뉴와 **같은 응답**을 쓴다(권한 판정도 같아야 한다). */
    openStatus() {
      if (this.stOpen) { this.stOpen = false; return; }
      this.stOpen = true; this.stErr = ""; this.stInfo = null;
      api.ticketMenu(this.keyId)
        .then((r) => { this.stInfo = r || {}; })
        .catch((e) => { this.stErr = (e && e.message) || "불러오지 못했습니다."; this.stInfo = {}; });
    },
    hardRefresh() {
      // 좌하단 강제 새로고침 — 서버측 파생 캐시(children/siblings/… SWR 옛 결과)까지 비운 **뒤**
      // 다시 받는다. 순서가 중요: 먼저 서버 캐시를 털고 나서 load 해야 최신이 잡힌다.
      if (this.refreshing) return;
      this.refreshing = true;
      const key = this.keyId;
      api.evict(key);
      Promise.resolve(api.ticketRefresh(key))
        .then(() => this.load(true))
        .finally(() => { setTimeout(() => { this.refreshing = false; }, 500); });
    },
    async load(force) {
      const key = this.keyId;
      const my = this._req = (this._req || 0) + 1;
      const fresh = () => my === this._req && this.keyId === key;
      this.err = ""; this.v = null; this.comments = null;
      this.ancestors = []; this.siblings = []; this.timeline = [];
      this.children = []; this.related = []; this.atts = []; this.docs = [];
      this.pdesc = null; this.pdescOpen = false; this.pdescErr = "";
      this.composing = false; this.editingId = null; this.editInitial = ""; this.editErr = "";

      this.kidTypes = []; this.adding = false; this.ncErr = ""; this.emeta = null;
      if (!this.me) api.me().then((m) => { this.me = m || {}; }).catch(() => { this.me = {}; });

      // ★ 본문(description)을 **가장 먼저** 큐에 넣는다 — 상류가 직렬(prod SSO)일 땐 먼저 보낸 게 먼저
      //   처리된다. 전엔 본문을 맨 마지막에 await 해, 사용자가 제일 먼저 읽는 본문이 다른 패널 8개 뒤로
      //   밀려 가장 늦게 떴다(지연 주입 측정: 본문 1023ms/가시 1506ms). 먼저 큐잉하면 체감이 확 준다.
      const vp = api.ticket(key, force).then((v) => {          // force=강제면 서버 캐시도 건너뜀
        if (!fresh()) return;
        this.v = v;
        // 검색창을 빈 상태로 열었을 때 보여줄 '최근 열어본 항목'에 남긴다.
        if (v) {
          recordOpen({ url: v.url || ("/browse/" + key), kind: "jira",
                       title: key + " " + (v.summary || ""), type: v.type || "",
                       meta: v.status || "",
                       data: {
                         key, summary: v.summary || "",
                         epicKey: v.epicKey || null, epicName: v.epicName || null,
                         assignee: v.assignee || null, assigneeId: v.assigneeId || null,
                         status: v.status || null, statusCategory: v.statusCategory || null,
                         project: (String(key).split("-")[0] || null), issuetype: v.type || null,
                       } });
        }
      }).catch((e) => {
        if (fresh()) this.err = e && e.message === "HTTP 404" ? "티켓을 찾을 수 없습니다: " + key : (e.message || "불러오기 실패");
      });

      // ★ 우선순위를 **틈(await)으로 강제**한다. 브라우저 병렬 + 상류 직렬(prod SSO) 이면 그냥 다
      //   쏴 두면 도착 순서가 뒤섞인다(계보가 첨부보다 늦게 오는 걸 지연 테스트로 확인). 상류가
      //   직렬이라 tier 사이 await 는 **추가 지연이 없다**(어차피 한 줄로 처리된다). 로컬(basic,
      //   parallel)만 미세 손해지만 dev·localhost 라 무시 가능.
      await vp;                                          // 1순위: 티켓정보·설명·일정
      if (!fresh()) return;
      // 2순위: 계보(조상) · 댓글(설명 바로 아래, 먼저 읽는다)
      await Promise.all([
        api.ticketAncestors(key).then((a) => { if (fresh()) this.ancestors = a || []; }).catch(() => {}),
        api.ticketComments(key).then((c) => { if (fresh()) this.comments = c; }).catch(() => { if (fresh()) this.comments = []; }),
      ]);
      if (!fresh()) return;
      // 3순위: 형제·타임라인·첨부·관련문서·하위·관련티켓·지원(편집메타·하위타입) — 서로 동급이라 함께.
      api.ticketSiblings(key).then((s) => { if (fresh()) this.siblings = s || []; }).catch(() => {});
      api.ticketTimeline(key).then((t) => { if (fresh()) this.timeline = t || []; }).catch(() => {});
      api.ticketAttachments(key).then((a) => {
        if (!fresh()) return;
        this.atts = a || []; this.attOpen = this.atts.length <= FOLD_AT;
      }).catch(() => {});
      api.ticketDocuments(key).then((d) => {
        if (!fresh()) return;
        this.docs = d || []; this.docOpen = this.docs.length <= FOLD_AT;
      }).catch(() => {});
      api.ticketChildren(key).then((c) => { if (fresh()) this.children = c || []; }).catch(() => {});
      api.ticketRelated(key).then((r) => { if (fresh()) this.related = r || []; }).catch(() => {});
      api.editmeta(key).then((m) => { if (fresh()) this.emeta = m || {}; }).catch(() => { if (fresh()) this.emeta = {}; });
      api.childTypes(key).then((t) => { if (fresh()) this.kidTypes = t || []; }).catch(() => {});

      // 유휴 시 이 티켓의 편집 팝업(담당/보고 기본·상태 전이)·전역 기본목록을 미리 데운다(로그인 상태).
      if (fresh()) { api.warmTicket(key); api.warmGlobals(); }
    },
    typeColor(t) { return TYPE_BG[t] || "var(--ty-task)"; },
    typeLabel(t) { return typeLabel(t); },
    descEmpty(html) { return descEmpty(html); },
    fsize(n) {
      n = +n || 0;
      if (n < 1024) return n + " B";
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
      return (n / 1024 / 1024).toFixed(1) + " MB";
    },
    // ── 코멘트 작성/수정/삭제 (첫 쓰기 기능) ──
    // 작성자 시그니처 컬러 — 사번 해시 → 고정 색. 같은 사람은 늘 같은 색(좌측 선으로 구분).
    // ── 파일 첨부: ＋ 버튼 · 드래그앤드롭 ──
    // 드래그는 '파일'일 때만 반응한다(에디터 안 텍스트/이미지 드래그까지 받으면 오작동).
    hasFiles(e) {
      const t = e.dataTransfer && e.dataTransfer.types;
      return !!t && Array.prototype.indexOf.call(t, "Files") >= 0;
    },
    /** 지금 포인터가 **에디터 위**인가. 같은 파일을 놓아도 결과가 다르다:
     *  에디터 위 → 댓글 본문에 들어가고 등록할 때 첨부된다.
     *  그 밖 → 티켓 첨부로 **즉시** 올라간다(댓글과 무관).
     *  구분을 안 하면 댓글을 쓰다 파일을 떨어뜨렸는데 본문엔 안 들어가고 첨부만 늘어난다. */
    inEditor(e) { return !!(e.target && e.target.closest && e.target.closest(".cmt-editor")); },
    onDragEnter(e) {
      if (!this.hasFiles(e)) return;
      this._dragDepth = (this._dragDepth || 0) + 1;
      this.dragOver = true;
      this.dragInEditor = this.inEditor(e);
    },
    onDragOver(e) {
      if (!this.hasFiles(e)) return;
      e.dataTransfer.dropEffect = "copy";
      // dragenter 만으로는 에디터 안팎을 오갈 때 늦게 바뀐다 — 움직일 때마다 다시 본다.
      this.dragInEditor = this.inEditor(e);
    },
    // dragleave 는 자식으로 넘어갈 때도 뜬다 → 깊이를 세서 진짜 벗어날 때만 끈다
    onDragLeave() {
      this._dragDepth = Math.max(0, (this._dragDepth || 0) - 1);
      if (!this._dragDepth) { this.dragOver = false; this.dragInEditor = false; }
    },
    onDrop(e) {
      this._dragDepth = 0; this.dragOver = false;
      const wasEditor = this.dragInEditor;
      this.dragInEditor = false;
      // ★ 에디터 위에 놓았으면 여기서 아무것도 하지 않는다 — 에디터가 이미 받았다.
      //   둘 다 처리하면 파일이 본문에도 들어가고 첨부로도 올라가 **두 번** 붙는다.
      if (wasEditor || this.inEditor(e)) return;
      const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      if (files.length) this.uploadFiles(files);
    },
    onFilePick(e) {
      const files = Array.from(e.target.files || []);
      e.target.value = "";                        // 같은 파일을 연속으로 고를 수 있게 초기화
      if (files.length) this.uploadFiles(files);
    },
    async uploadFiles(files) {
      if (this.uploading || !this.tk) return;
      this.uploading = true; this.upErr = "";
      const failed = [];
      for (const f of files) {
        try { await api.attachmentUpload(this.tk, f); }
        catch (e) { failed.push(f.name + " (" + ((e && e.message) || e) + ")"); }
      }
      this.uploading = false;
      this.upErr = failed.length ? "첨부 실패: " + failed.join(", ") : "";
      await this.reloadAttachments();
    },
    /** 편집 가능 필드를 받아 둔다. 실패하면 아무것도 안 열린다 — 조용히 열어 주는 것보다
     *  조용히 닫는 편이 안전하다(열어 두면 다 입력한 뒤 거절당한다). */
    loadEditmeta() {
      const key = this.keyId;
      return api.editmeta(key)
        .then((m) => { if (this.keyId === key) this.emeta = m || {}; })
        .catch(() => { if (this.keyId === key) this.emeta = {}; });
    },
    fmeta(id) { return (this.emeta && this.emeta[id]) || null; },
    /** 제목(summary) 인라인 수정 — editmeta 에 summary 가 있을 때만(권한). Enter 저장 / Esc 취소. */
    startSumEdit() {
      if (!this.v || !this.fmeta("summary") || this.sumBusy) return;
      this.sumDraft = this.v.summary || "";
      this.sumErr = ""; this.sumEdit = true;
      this.$nextTick(() => { const el = this.$refs.sumInput; if (el) { el.focus(); el.select(); } });
    },
    cancelSumEdit() { this.sumEdit = false; this.sumErr = ""; },
    async saveSum() {
      const s = (this.sumDraft || "").trim();
      if (!s) { this.sumErr = "제목을 입력하세요."; return; }
      if (this.v && s === this.v.summary) { this.sumEdit = false; return; }   // 변경 없음
      this.sumBusy = true; this.sumErr = "";
      try {
        const r = await api.updateFields(this.tk, { summary: s });
        if (r && r.ok === false) throw new Error(r.error || "저장 실패");
        if (this.v) this.v.summary = s;         // 낙관적 즉시 반영(그 뒤 onFieldSaved 가 재조회)
        this.sumEdit = false;
        this.onFieldSaved();
      } catch (e) {
        this.sumErr = (e && e.message) || "저장 실패";
      } finally { this.sumBusy = false; }
    },
    /** 본문 저장 — 에디터가 이미지 업로드까지 끝낸 HTML 을 준다. 실패는 **던져야** 에디터가
     *  올린 이미지를 되돌린다(조용히 삼키면 첨부만 남는다). */
    async saveDesc(html) {
      this.descBusy = true; this.descErr = "";
      try {
        const r = await api.updateFields(this.tk, { descriptionHtml: html });
        if (r && r.ok === false) throw new Error(r.error || "저장 실패");
      } catch (e) {
        this.descBusy = false;
        this.descErr = (e && e.message) || "저장 실패";
        throw e;
      }
      this.descBusy = false; this.descEdit = false; this.stopWatchDesc();
      this.onFieldSaved();
    },
    /** 필드가 바뀌면 티켓을 다시 받는다 — 한 필드만 손대도 상태·이력이 같이 움직인다.
     *  ★ 재조회가 끝난 뒤(this.v 최신) **바뀐 값까지 실어** 알린다 — Task 화면이 네트워크 없이
     *  이 티켓이 현재 퀵필터에서 빠지는지 판정해 즉시 반영할 수 있게. */
    async onFieldSaved() {
      await this.load();     // 끝나면 this.v 가 최신(담당/상태/컴포넌트)
      this._fireChanged();
    },
    _fireChanged() {
      const v = this.v || {};
      window.dispatchEvent(new CustomEvent("ticket-changed", { detail: {
        key: this.keyId,
        view: { key: this.keyId, assigneeId: v.assigneeId || null, reporterId: v.reporterId || null,
                statusCategory: v.statusCategory || null, components: v.components || [],
                resolved: v.resolved || null } } }));
    },
    /** 첨부·문서를 함께 다시 받는다 — 댓글/본문을 고치면 그 안의 첨부·문서도 같이 달라진다.
     *  예전엔 코멘트 목록만 다시 받아, 파일을 붙였는데 첨부 목록은 그대로였다. */
    reloadSide() { this.reloadAttachments(); this.reloadDocs(); },
    async delAttachment(a) {
      if (!await confirmBox(a.filename + " 을(를) 삭제할까요?", { okLabel: "삭제", danger: true })) return;
      this.upErr = "";
      try { await api.attachmentDelete(this.tk, a.id); }
      catch (e) { this.upErr = "삭제 실패: " + ((e && e.message) || e); }
      this.reloadSide();
    },
    async delDoc(d) {
      if (!await confirmBox((d.title || d.url) + " 을(를) 이 티켓에서 뗄까요?",
                            { okLabel: "떼기", danger: true })) return;
      this.docErr = "";
      try { await api.documentDelete(this.tk, d.linkId); }
      catch (e) { this.docErr = "떼지 못했습니다: " + ((e && e.message) || e); }
      this.reloadDocs();
    },
    reloadAttachments() {
      const key = this.keyId;
      return api.ticketAttachments(key)
        .then((a) => { if (this.keyId === key) this.atts = a || []; }).catch(() => {});
    },

    // ── 관련 티켓 / 관련문서 링크 걸기 ──
    async addLink(sel) {
      if (this.linkBusy) return;
      this.linkBusy = true; this.linkErr = "";
      try {
        await api.linkAdd(this.tk, sel);
        this.relPick = false;
        await this.reloadRelated();
      } catch (e) { this.linkErr = "링크 실패: " + ((e && e.message) || e); }
      finally { this.linkBusy = false; }
    },
    reloadRelated() {
      const key = this.keyId;
      return api.ticketRelated(key)
        .then((r) => { if (this.keyId === key) this.related = r || []; }).catch(() => {});
    },
    async addDoc(sel) {
      if (this.docBusy) return;
      this.docBusy = true; this.docErr = "";
      try {
        let title = sel.title || "";
        if (!title) {                             // URL 만 붙여넣은 경우 — 문서 제목을 가져온다
          try { title = (await api.linkTitle(sel.url)).title || ""; } catch (e) { /* 없으면 URL 로 */ }
        }
        await api.documentAdd(this.tk, { url: sel.url, title });
        this.docPick = false;
        await this.reloadDocs();
      } catch (e) { this.docErr = "첨부 실패: " + ((e && e.message) || e); }
      finally { this.docBusy = false; }
    },
    reloadDocs() {
      const key = this.keyId;
      return api.ticketDocuments(key)
        .then((d) => { if (this.keyId === key) this.docs = d || []; }).catch(() => {});
    },

    // 글쓴이 시그니처 컬러 — 기본 아바타(프사 없는 사람)와 같은 색이어야 하므로 colors.js 단일 소스.
    sigColor,
    categoryColor,
    /** DueText 가 먹는 모양으로 — 마감이 비면 **상위(이 티켓)의 마감을 물려받는다.**
     *  Sub-Task 에 마감을 따로 안 적는 게 흔한데, 그때 '미정' 이라고 하면 실제로는 부모
     *  마감에 묶여 있는 일이 자유로워 보인다. */
    kidCard(c) {
      const inh = !c.due && this.v && this.v.due ? this.v.due : null;
      const due = c.due || inh;
      return { statusCategory: c.statusCategory, resolved: c.resolved, due,
               dueInherited: !!inh, dueDays: daysTo(due) };
    },
    // 본인 댓글 판정은 **서버가 매긴 c.mine 을 우선**한다(세션 사용자로 서버가 대조 — id 형식/
    // 로딩 타이밍에 안 흔들린다). 옛 캐시로 mine 이 없을 때만 클라이언트 비교로 폴백.
    canEdit(c) {
      if (!c) return false;
      if (c.mine === true) return true;
      if (c.mine === false) return false;
      return !!(this.me && this.me.id && c.authorId === this.me.id);
    },
    reloadComments() {
      const key = this.keyId;
      return api.ticketComments(key)
        .then((c) => { if (this.keyId === key) this.comments = c || []; })
        .catch(() => {});
    },
    startCompose() { this.editingId = null; this.editErr = ""; this.composing = true; },
    cancelCompose() { this.composing = false; },
    submitNew(md) { return api.commentCreate(this.tk, md); },     // CommentEditor 가 await
    onComposed() { this.composing = false; this.reloadComments(); this.reloadSide(); },
    async startEdit(c) {
      // 원본(markdown)을 먼저 받아온 뒤 editingId 를 켠다 — 에디터는 마운트 시 initialValue 만 읽으므로.
      this.composing = false; this.editErr = "";
      try {
        const src = await api.commentSource(this.tk, c.id);
        this.editInitial = (src && src.html) || "";
        this.editingId = c.id;
      } catch (e) { this.editErr = "댓글 원본을 불러오지 못했습니다."; }
    },
    cancelEdit() { this.editingId = null; this.editInitial = ""; },
    submitEdit(c, md) { return api.commentUpdate(this.tk, c.id, md); },
    onEdited() { this.editingId = null; this.editInitial = ""; this.reloadComments(); this.reloadSide(); },
    async delComment(c) {
      // window.confirm 을 쓰면 **앱 창에서는 아무 일도 일어나지 않는다**(Playwright 가 대화상자를
      // 자동 거절한다). 크롬에서만 되던 이유가 이것이었다.
      if (!await confirmBox("이 댓글을 삭제할까요?", { okLabel: "삭제", danger: true })) return;
      try { await api.commentDelete(this.tk, c.id); await this.reloadComments(); }
      catch (e) { window.alert("삭제 실패: " + ((e && e.message) || e)); }
    },
    // 상위 티켓 설명 — 기존 /api/ticket 응답을 그대로 재사용(정화된 HTML + 프론트 memo 캐시)
    async toggleParentDesc() {
      this.pdescOpen = !this.pdescOpen;
      if (!this.pdescOpen || this.pdesc || !this.parentOf) return;
      const pk = this.parentOf.key;
      try {
        const d = await api.ticket(pk);
        if (this.parentOf && this.parentOf.key === pk) this.pdesc = d;
      } catch (e) { this.pdescErr = (e && e.message) || "불러오기 실패"; }
    },
    // 타임라인 한 줄 문구 — 중요 이벤트만 오므로 종류별로 짧게 표현
    // 우선순위 등급 — 사내 체계는 P0-Blocker … P4-Trivial, 그리고 Unclassified.
    // **접두사 숫자**로 등급을 뽑는다(영문 이름 하드코딩 회피 — 이름이 바뀌어도 견딘다).
    // 숫자가 작을수록 중요. 못 읽으면 중립 칩.
    prioCls(name) {
      if (!name) return "unset";
      const m = /^\s*P(\d+)/i.exec(name);
      return m ? "pr-" + Math.min(+m[1], 4) : "";
    },
    // 상태/우선순위 변경은 값 부분을 뱃지로 — 텍스트보다 눈에 빨리 들어온다
    tlKind(e) { return (e.kind || "").replace(/^child-/, ""); },
    tlBadged(e) { return ["status", "priority", "duedate"].includes(this.tlKind(e)); },
    tlLabel(e) { return { status: "상태", priority: "우선순위", duedate: "마감일" }[this.tlKind(e)]; },
    // 뱃지 색: 상태는 statusCategory(인스턴스 조회), 우선순위는 P 등급
    tlBCls(e, v) {
      const k = this.tlKind(e);
      if (k === "priority") return this.prioCls(v);
      if (k === "duedate") return "";                 // 마감일은 의미색 없음 — 중립 칩
      const cat = (v === e.from) ? e.fromCat : e.toCat;
      return cat ? "st-" + cat : "";
    },
    tlVal(e, v) {
      const k = this.tlKind(e);
      if (v) return k === "duedate" ? (this.fy(v) || v) : v;
      // 우선순위 미설정은 백엔드가 null 로 정규화(사내 Jira 의 'Unclassified')
      return k === "priority" ? "미지정" : "없음";
    },
    tlText(e) {
      const f = e.from || "없음", t = e.to || "없음";
      const kind = (e.kind || "").replace(/^child-/, "");   // 자손 이벤트도 같은 문구 사용
      if (kind === "created") return "티켓 생성";
      if (kind === "comment") return "댓글 작성";
      if (kind === "status") return "상태 " + f + " → " + t;
      if (kind === "assignee") return "담당자 " + f + " → " + t;
      if (kind === "resolution") return e.to ? ("해결: " + e.to) : "해결 취소";
      if (kind === "duedate") return "마감일 " + f + " → " + t;
      if (kind === "priority") return "우선순위 " + (e.from || "미지정") + " → " + (e.to || "미지정");
      return (e.field || "변경") + " " + f + " → " + t;
    },
    // 타 모듈 형제 = 흐리게(숨기지는 않는다 — 존재는 알리고 노이즈만 줄임)
    isOther(s) { return !!(this.myComp && s.component && s.component !== this.myComp); },
    fy(s) { return ymd(s); },
    fts(s) { return ts(s); },   // 일정 공통 포맷 yyyy.mm.dd HH:mm
    fdt(s) { return ymdhm(s); },
    /** 첨부 칩의 시각 — 날짜·시각·분을 따로 준다. 좁아질 때 **뒤에서부터** 버리기 위해서다. */
    fdate(s) { return (ymdhm(s) || "").split(" ")[0] || ""; },
    fhour(s) { return ((ymdhm(s) || "").split(" ")[1] || "").split(":")[0] || ""; },
    fmin(s) { return ((ymdhm(s) || "").split(" ")[1] || "").split(":")[1] || ""; },
    statusClass(cat) { return "st-" + (cat || "todo"); },
    // 확대 버튼(.zoom-btn)만 반응 — 표는 드래그 복사가 가능해야 하므로 내용 클릭으로는 확대 안 함.
    onContentClick(e, comment) {
      // ── 체크박스 토글 ── 본문/코멘트의 렌더된 체크박스를 누르면 원본의 그 체크박스를 뒤집어 저장.
      const cb = e.target.closest && e.target.closest("input.tkt-cb");
      if (cb) {
        // 본인 코멘트이거나 이 이슈를 **뭐라도 고칠 수 있으면**(editmeta 에 편집 필드 존재) 토글 시도.
        // 최종 판정은 서버 — 권한 없으면 저장이 실패해 원복된다. (mayEdit 은 computed=값)
        const editable = (comment && this.canEdit(comment)) || this.mayEdit || !this.emeta;
        if (!editable || cb.dataset.cbBusy) { e.preventDefault(); return; }   // 못 고치면 상태 고정
        // ★ preventDefault 를 걸지 않는다 — 체크박스는 클릭의 '활성화' 로 checked 가 바뀌는데,
        //   preventDefault 를 걸면 브라우저가 그 활성화를 **취소해 우리 코드 뒤에 다시 되돌린다**
        //   (그래서 아무리 눌러도 체크가 안 됐다). 네이티브 토글을 그대로 살려 즉시 반영하고,
        //   그 값을 저장한다. 실패하면 원복.
        const want = cb.checked;                             // 활성화가 이미 뒤집은 값 = 원하는 상태
        const index = parseInt(cb.getAttribute("data-cb-index"), 10);
        const id = cb.getAttribute("data-cb-id") || null;    // id 우선(서버가 index 폴백)
        if (!(index >= 0) && !id) { cb.checked = !want; return; }
        cb.dataset.cbBusy = "1";
        const body = comment
          ? { target: "comment", commentId: String(comment.id), id, index, checked: want }
          : { target: "description", id, index, checked: want };
        api.toggleCheckbox(this.tk, body).then((r) => {
          if (r && r.ok) { cb.defaultChecked = want; if (want) cb.setAttribute("checked", ""); else cb.removeAttribute("checked"); }
          else { cb.checked = !want; this.editErr = (r && r.error) || "체크박스 저장 실패"; }
        }).catch((err) => { cb.checked = !want; this.editErr = "체크박스 저장 실패: " + ((err && err.message) || err); })
          .finally(() => { delete cb.dataset.cbBusy; });
        return;
      }
      // 본문의 문서/웹 링크를 열면 '최근 열어본 항목'에 남긴다(Jira 뱃지는 다이얼로그가 기록).
      const a = e.target.closest && e.target.closest("a[href]");
      if (a) {
        const href = a.getAttribute("href") || "";
        if (/^https?:/i.test(href)) {
          let host = "";
          try { host = new URL(href).host; } catch (_) { /* noop */ }
          recordOpen({ url: href,
                       kind: a.classList.contains("conf-link") ? "confluence" : "web",
                       title: (a.getAttribute("title") || a.textContent || href).trim(),
                       meta: host });
        }
      }
      const btn = e.target.closest && e.target.closest(".zoom-btn");
      if (!btn) return;
      e.preventDefault();
      const wrap = btn.closest(".zoomable");
      const img = wrap && wrap.querySelector("img");
      const table = wrap && wrap.querySelector("table");
      if (img) {
        // 이미 완전히 로드된 이미지면 스피너 생략(즉시 표시), 아니면 로딩 표시
        this.zoomLoading = !(img.complete && img.naturalWidth > 0);
        this.zoom = { type: "img", src: img.currentSrc || img.src, alt: img.alt || "" };
      } else if (table) {
        this.zoom = { type: "table", html: table.outerHTML };
      }
    },
    augment() { this.augmentZoomables(); this.augmentLinks(); this.highlightCode(); },
    highlightCode() { try { hljsHighlight(this.$el); } catch (e) { /* noop */ } },
    // 설명/코멘트 내 링크를 뱃지로: Confluence(문서 제목=URL 슬러그), Jira 티켓(이름/상태/담당자).
    augmentLinks() {
      const root = this.$el;
      if (!root || !root.querySelectorAll) return;
      // 1) Confluence 뱃지 — 내부 텍스트 무시하고 URL 에서 문서 제목 유도. 없으면 기존 텍스트.
      root.querySelectorAll(".tkt-desc a.conf-link").forEach((a) => {
        if (a.dataset.conftitled) return;
        a.dataset.conftitled = "1";
        const href = a.getAttribute("href") || "";
        // ① URL 슬러그에 제목이 있으면 그걸로(요청 없음). 옛 링크(viewpage.action?pageId=)엔 없다.
        const fromUrl = confTitleFromUrl(href);
        const setLabel = (t) => {
          const label = t || "Confluence 문서";
          a.innerHTML = '<span class="conf-title">' + esc(label) + "</span>";
          a.title = label;
        };
        if (fromUrl) { setLabel(fromUrl); return; }
        // ② 슬러그가 없으면 **서버에서 문서 제목을 받아 온다**(Confluence 링크는 무조건 제목으로).
        //    받아오는 동안은 링크 텍스트로 임시 표시(빈 뱃지보단 낫다).
        setLabel((a.textContent || "").trim() || "Confluence 문서");
        api.linkTitle(href).then((r) => {
          if (r && r.title && a.isConnected) setLabel(r.title);
        }).catch(() => { /* 못 받으면 임시 라벨 유지 */ });
      });
      // 2) Jira 티켓 링크(/browse/KEY) → 뱃지. href 제거하고 인앱 다이얼로그로 열기.
      root.querySelectorAll(".tkt-desc a[href]").forEach((a) => {
        if (a.dataset.jira) return;
        const m = _BROWSE_RE.exec(a.getAttribute("href") || "");
        if (!m) return;
        a.dataset.jira = "1";
        const key = m[1];
        a.classList.add("jira-badge", "tkt");
        a.setAttribute("data-key", key);
        a.removeAttribute("href");
        // [타입][번호][제목] - [상태]. 예전엔 맨 앞이 상태색 점이었는데, 상태는 오른쪽에 글자로도
        // 나오므로 중복인 데다 무슨 뜻인지 읽히지 않았다 → 그 자리를 **타입 뱃지**로 바꾼다.
        a.innerHTML = '<span class="tbadge v-solid jb-type"></span><b class="jb-key">' + esc(key) + "</b>"
          + '<span class="jb-name"></span><span class="jb-meta"></span>';
        api.ticketBadge(key).then((b) => {
          if (!b) return;
          const tb = a.querySelector(".jb-type");
          tb.textContent = typeLabel(b.type || "");
          tb.style.setProperty("--tc", TYPE_BG[b.type] || "var(--ty-task)");
          a.querySelector(".jb-name").textContent = b.summary || "";
          // 상태만(담당자 제외) — 구분선 '|' 는 CSS, 색은 상태 카테고리로.
          const meta = a.querySelector(".jb-meta");
          meta.textContent = b.status || "";
          meta.className = "jb-meta st-" + (b.statusCategory || "todo");
          a.title = key + " " + (b.summary || "") + (b.status ? " (" + b.status + ")" : "");
        }).catch(() => { /* 조회 실패 시 키만 표시 */ });
      });
      // 3) 일반 웹 링크 → favicon 뱃지(에디터와 동일한 모양). Confluence/Jira 뱃지는 위에서 처리됨.
      root.querySelectorAll(".tkt-desc a[href]").forEach((a) => {
        if (a.dataset.web || a.dataset.jira) return;
        if (a.classList.contains("conf-link") || a.classList.contains("jira-badge")) return;
        const href = a.getAttribute("href") || "";
        // 맨션은 **사람**이지 웹 링크가 아니다. prod 는 프로필 주소를 절대 URL 로 주는데,
        // 그것까지 favicon 뱃지로 바꾸면 사람 이름이 남의 사이트 링크처럼 보인다.
        if (a.classList.contains("user-hover") || /\/secure\/ViewProfile\.jspa/i.test(href)) return;
        if (!/^https?:\/\//i.test(href)) return;
        a.dataset.web = "1";
        a.classList.add("web-badge");
        a.style.setProperty("--fav", "url('/api/favicon?u=" + encodeURIComponent(href) + "')");
      });
    },
    // v-html 로 렌더된 이미지/표에 '확대' 버튼을 얹는다(우측 상단). 중복 주입 방지 마커 사용.
    // kv 표(.kv-table)는 제외 — 본문 표와 달리 '라벨|값' 2단이라 넓힐 이유가 없고,
    // 영역마다 버튼이 붙으면 오히려 시끄럽다.
    augmentZoomables() {
      const root = this.$el;
      if (!root || !root.querySelectorAll) return;
      root.querySelectorAll(".tkt-desc img, .tkt-desc table:not(.kv-table)").forEach((el) => {
        if (el.dataset.zoomified) return;
        // 첨부 **파일 뱃지(칩)/링크** 안의 아이콘·썸네일 이미지는 확대 대상이 아니다 — 콘텐츠 이미지가
        // 아니라 링크 칩이다. 여기에 '확대' 를 얹으면 칩 뒤에 엉뚱한 '확대' 버튼이 붙는다(prod 리포트).
        // ★ prod 는 뱃지 class 가 우리(.file-badge)와 달라(Jira 자체 렌더) 못 걸릴 수 있어, **앵커(a)
        //   안**과 **data-ext 를 가진 요소 안**도 함께 제외한다(첨부 이미지는 늘 링크/뱃지 안이다).
        if (el.tagName === "IMG" && el.closest("a, .file-badge, .fchip, .attachment, [data-ext]")) return;
        // Jira 이모티콘/이모지(예: (*)→별)는 인라인 아이콘이라 확대 대상이 아니다.
        //  1) 서버가 붙인 .emoticon 표식,  2) 폴백으로 아주 작은 이미지(≤32px)도 제외.
        if (el.tagName === "IMG") {
          if (el.classList.contains("emoticon")) return;
          const w = parseInt(el.getAttribute("width") || "0", 10);
          const h = parseInt(el.getAttribute("height") || "0", 10);
          if ((w && w <= 32) || (h && h <= 32)) return;
          const rw = el.naturalWidth || el.width || 0, rh = el.naturalHeight || el.height || 0;
          if (rw && rh && Math.max(rw, rh) <= 32) return;
        }
        el.dataset.zoomified = "1";
        const wrap = document.createElement(el.tagName === "IMG" ? "span" : "div");
        wrap.className = "zoomable";
        el.parentNode.insertBefore(wrap, el);
        wrap.appendChild(el);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "zoom-btn";
        btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3M11 8v6M8 11h6"/></svg>확대';
        wrap.appendChild(btn);
      });
    },
  },
  template: `
    <div :class="[isPage ? 'tkt-page' : 'tkt-ov', { expanded, 'drag-over': dragOver }]"
         @click.self="!isPage && fromBackdrop($event) && $emit('close')"
         @dragenter.prevent="onDragEnter" @dragover.prevent="onDragOver"
         @dragleave="onDragLeave" @drop.prevent="onDrop">
    <!-- 드래그 중 안내 — 같은 파일이라도 **어디에 놓느냐로 결과가 달라지므로** 그 차이를
         놓기 전에 말해 준다. 놓고 나서 "왜 본문에 안 들어갔지" 를 겪게 하면 안 된다. -->
    <div v-if="dragOver" class="tkt-dz" :class="{ ed: dragInEditor }">
      <div class="tkt-dz-c">
        <div class="tkt-dz-ic">{{ dragInEditor ? '✍' : '📎' }}</div>
        <b>{{ dragInEditor ? '댓글 본문에 넣기' : '티켓에 첨부' }}</b>
        <span>{{ dragInEditor
          ? '이미지는 본문에 보이고, 그 밖의 파일은 칩으로 들어갑니다 · 등록할 때 첨부됩니다'
          : '놓는 즉시 첨부됩니다 · 댓글 본문에 넣으려면 작성 중인 에디터 위에 놓으세요' }}</span>
      </div>
    </div>
      <div class="tkt-dlg" :class="{ expanded, page: isPage }"
           :role="isPage ? null : 'dialog'" :aria-modal="isPage ? null : 'true'">
        <!-- 최상단 타이틀바 — 배경은 티켓 타입 색을 따른다.
             좌: "[타입] [번호] [제목] - 상태" / 우: Jira에서 열기 · 최대화 · 닫기 -->
        <div class="tkt-bar" :style="{ '--tc': typeColor(v && v.type) }">
          <span class="tb-name" :title="barTitle">
            <span class="tb-type">{{ v ? v.type : '' }}</span>
            <span class="tb-key">{{ (v && v.key) || keyId }}</span>
            <!-- 제목 — 수정권한(editmeta.summary) 있으면 연필로 인라인 편집 -->
            <template v-if="!sumEdit">
              <span class="tb-sum" :class="{ editable: v && fmeta('summary') }"
                    @click="v && fmeta('summary') && startSumEdit()"
                    :title="v && fmeta('summary') ? '클릭해 제목 수정' : ''">{{ v ? v.summary : '불러오는 중…' }}</span>
              <button v-if="v && fmeta('summary')" class="tb-sumedit" @click.stop="startSumEdit" title="제목 수정" aria-label="제목 수정">✎</button>
              <span v-if="v && v.status" class="tb-st" :class="statusClass(v.statusCategory)">- {{ v.status }}</span>
            </template>
            <template v-else>
              <input ref="sumInput" class="tb-sum-input" v-model="sumDraft" :disabled="sumBusy" maxlength="255"
                     @keydown.enter.prevent="saveSum" @keydown.esc.prevent="cancelSumEdit" @click.stop />
              <button class="tb-sumedit ok" @click.stop="saveSum" :disabled="sumBusy" :title="sumBusy ? '저장 중' : '저장 (Enter)'">{{ sumBusy ? '…' : '✓' }}</button>
              <button class="tb-sumedit" @click.stop="cancelSumEdit" :disabled="sumBusy" title="취소 (Esc)">✕</button>
              <span v-if="sumErr" class="tb-sum-err">{{ sumErr }}</span>
            </template>
          </span>
          <span class="tb-actions">
            <!-- 단독 페이지는 nav 가 없다 → Home·검색·테마를 타이틀바에서 제공.
                 순서: [Home] [검색] [Jira에서 열기] [Dark]
                 검색·테마는 헤더와 **같은 클래스/마크업**을 써서 모양을 일치시킨다.
                 ('/' 단축키는 app-root 가 document 에 걸어둬 여기서도 그대로 동작) -->
            <a v-if="isPage" class="tb-btn" href="/" title="Home으로 돌아가기">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 10 9-7 9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/></svg>
              Home
            </a>
            <button v-if="isPage" class="search-trig" @click="$emit('search')" title="통합 검색 ( / )">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
              <span>검색</span><kbd>/</kbd>
            </button>
            <!-- 링크 복사 — 이 티켓의 Jira URL 을 클립보드로 -->
            <button v-if="v" class="tb-btn ico" @click="copyLink" aria-label="Jira 링크 복사" title="Jira 링크 복사">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
            </button>
            <a v-if="v && v.url" class="tb-btn" :href="v.url" target="_blank" rel="noopener"
               title="Jira에서 열기">Jira에서 열기 ↗</a>
            <!-- 티켓 단독 페이지도 Home 과 같은 우상단 구성 — 테마 토글은 설정 메뉴 안에 있다
                 (SSO 상태·Dev Tools·rev 도 여기서 함께 본다). -->
            <SettingsMenu v-if="isPage" :theme="theme" @toggle-theme="$emit('toggle-theme')" />
            <!-- data-external: 같은 호스트지만 앱 창(Chromium)이 아니라 시스템 기본 브라우저로.
                 run.py 의 외부링크 훅이 이 속성을 보고 넘긴다. -->
            <a v-if="!isPage" class="tb-btn ico" :href="pageHref" target="_blank" rel="noopener"
               data-external aria-label="새 창에서 열기" title="새 창에서 열기">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>
            </a>
            <button v-if="!isPage" class="tb-btn ico" @click="expanded = !expanded"
                    :aria-label="expanded ? '축소' : '최대화'" :title="expanded ? '축소' : '최대화'">
              <svg v-if="!expanded" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3"/></svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3a2 2 0 0 0 2-2V3M20 8h-3a2 2 0 0 1-2-2V3M4 16h3a2 2 0 0 1 2 2v3M20 16h-3a2 2 0 0 0-2 2v3"/></svg>
            </button>
            <button v-if="!isPage" class="tb-btn ico close" @click="$emit('close')" aria-label="닫기" title="닫기">✕</button>
          </span>
        </div>

        <!-- 스크롤 주체는 **본문**이다. 다이얼로그 전체를 스크롤시키면 바가 타이틀바
             옆까지 올라와 모서리 밖으로 삐져나온 것처럼 보인다. -->
        <div class="tkt-body" ref="body" @scroll="posCollapse">

        <!-- 섹션별 독립 렌더: 스파인(계보/형제/타임라인)은 본문(v) 응답을 기다리지 않는다.
             본문·코멘트도 각자 자기 상태가 채워지는 대로 그려진다. -->
        <div class="tkt-cols" :class="{ 'spine-hidden': spineHidden, 'tl-hidden': tlHidden }"
             :style="{ '--spine-w': spineW + 'px', '--tl-w': tlW + 'px' }">
          <!-- 접힌 상태에서 다시 펴는 손잡이(얇은 레일) -->
          <button v-if="spineHidden && hasSpine" class="spine-show" title="부가정보 패널 펼치기"
                  @click="setSpineHidden(false)">›</button>
          <!-- 좌측 세로 스파인 — 계보(조상→현재, 레일+진척) + 형제 목록. 클릭 시 해당 티켓으로 이동 -->
          <aside v-if="hasSpine && !spineHidden" class="tkt-spine">
            <button class="spine-hide" title="부가정보 패널 접기" @click="setSpineHidden(true)">‹</button>
            <!-- 오른쪽 가장자리를 끌어 폭 조절. 넓은 화면에서만 보인다(좁으면 grid 라 무의미). -->
            <div class="spine-grip" title="너비 조절 — 드래그" @mousedown.prevent="startSpineDrag"></div>
            <!-- 조상이 없으면(Epic 등) 자기 자신만 남으므로 계보 블록 자체를 생략 -->
            <!-- 좁은 화면에서 '열 묶음' 단위로 배치된다(.grp). 넓은 화면에선 display:contents 라
                 구조상 없는 것과 같고, 순서는 CSS order 로 기존과 동일하게 유지한다. -->
            <div class="grp grp-lineage">
            <div v-if="spine.length > 1" class="sec sec-lineage">
            <div class="tkt-mlabel">계보</div>
            <div v-for="(n, i) in spine" :key="n.key || 'virt-' + i" class="spn-item">
              <div class="spn-rail">
                <span class="spn-dot" :class="{ on: n.current, virt: n.virtual }"
                      :style="{ '--tc': typeColor(n.type) }"></span>
                <span v-if="i < spine.length - 1" class="spn-line"></span>
              </div>
              <!-- 가상 노드(실 티켓 아님, 예: '사용자 VoC')는 클릭 대상이 아니다 -->
              <div class="spn-body" :class="{ cur: n.current, tkt: !n.current && !n.virtual }"
                   :data-key="(n.current || n.virtual) ? null : n.key"
                   :title="n.virtual ? n.summary : (n.type + ' ' + n.key + ' · ' + n.summary)">
                <div v-if="!n.virtual" class="spn-top"><TypeBadge :type="n.type" /><span class="spn-key">{{ n.key }}</span></div>
                <div class="spn-title" :class="{ virt: n.virtual }">{{ n.summary }}</div>
                <div v-if="n.pct !== null && n.pct !== undefined" class="spn-prog">
                  <span class="spn-bar"><i :style="{ width: n.pct + '%', background: typeColor(n.type) }"></i></span>
                  <span class="spn-pct">{{ n.pct }}%</span>
                </div>
              </div>
            </div>
            </div>
            <!-- 하위 Task 는 **본문 칸**이 맡는다(상태·담당자·마감까지 보이는 쪽). 같은 목록을
                 여기에도 두면 어느 쪽이 진짜인지 매번 눈이 헷갈리고, 좁은 화면에선 계보 열이
                 그만큼 길어져 정작 계보가 안 보인다. -->
            </div>

            <div class="grp grp-rel">
            <div class="sec sec-related spn-sib">
              <div class="tkt-mlabel has-add">
                <span>관련 Task {{ related.length }}</span>
                <button class="add-b" title="관련 티켓 추가 (Jira 이슈 링크)"
                        @click.stop="relPick = !relPick">＋</button>
              </div>
              <LinkPicker v-if="relPick" mode="jira" :exclude-keys="related.map(r => r.key).concat([tk])"
                          :busy="linkBusy" :err="linkErr"
                          @close="relPick = false" @pick="addLink" />
              <!-- 좌측 패널은 접지 않는다 — 여기는 '지금 어디에 있고 무엇과 엮여 있나'를 한눈에
                   보는 자리라, 몇 개를 감추면 그 한눈이 성립하지 않는다(길면 패널이 스크롤된다). -->
              <div v-for="r in related" :key="'rel-' + r.key" class="spn-sibrow tkt"
                   :data-key="r.key" :title="r.rel + ' · ' + r.key + ' · ' + r.summary">
                <span class="spn-sdot" :class="'st-' + (r.statusCategory || 'todo')"></span>
                <span class="spn-stitle">{{ r.summary }}</span>
                <span class="spn-rel" :class="r.via">{{ r.via === 'link' ? r.rel : '언급' }}</span>
              </div>
              <div v-if="!related.length" class="muted mini">관련 티켓 없음</div>
            </div>
            <!-- 이 티켓을 **저쪽에서 언급해** 자동으로 생긴 링크(Confluence 의 Jira 이슈 매크로 등).
                 참고하라고 사람이 붙인 관련문서와 성질이 달라 자리를 나눈다 — 관련문서에 섞으면
                 '내가 붙인 것' 과 '남이 나를 부른 것' 이 한 줄로 보인다. -->
            <div v-if="mentionDocs.length" class="sec sec-mention spn-sib">
              <div class="tkt-mlabel">이 Ticket을 멘션함 <span class="spn-pos">{{ mentionDocs.length }}</span></div>
              <a v-for="(d, i) in mentionDocs" :key="'mn-' + i" class="spn-sibrow mn-doc"
                 :href="d.url" target="_blank" rel="noopener" :title="d.url">
                <span class="sr-pageic"></span>
                <span class="spn-stitle">{{ d.title }}</span>
              </a>
            </div>

            <div v-if="siblings.length" class="sec sec-sib spn-sib">
              <!-- 숫자는 **한 번만**. 예전엔 '형제 15' 옆에 '12/15' 가 또 붙어, 전체 개수가 두 번
                   나오고 15 와 12 가 나란히 서서 무엇이 무엇인지 읽히지 않았다.
                   위치를 알 수 있으면 '몇 번째/전체', 아니면 전체만. -->
              <div class="tkt-mlabel">
                <span>형제</span>
                <span class="spn-pos">{{ sibPos ? sibPos + '/' + siblings.length : siblings.length }}</span>
              </div>
              <div v-for="s in siblings" :key="s.key" class="spn-sibrow"
                   :class="{ cur: s.current, other: isOther(s), tkt: !s.current }"
                   :data-key="s.current ? null : s.key"
                   :title="s.key + ' · ' + s.summary + (s.component ? ' (' + s.component + ')' : '')">
                <span class="spn-sdot" :class="'st-' + (s.statusCategory || 'todo')"></span>
                <span class="spn-stitle">{{ s.summary }}</span>
                <span v-if="isOther(s)" class="spn-scomp">{{ s.component }}</span>
              </div>
            </div>
            </div>

          </aside>

          <div class="tkt-main">
          <div v-if="err" class="tkt-err">{{ err }}</div>
          <!-- ★ 응답 전에도 **레이아웃(구조)을 먼저** 그린다 — 값 자리만 스켈레톤(깜빡이는 회색 줄).
               본문(v) 이 오면 아래 실제 내용으로 교체된다. 전엔 스피너 하나가 전체를 대신해 화면이
               '텅 빈 채 기다렸다 통째로 뜨는' 느낌이었다. -->
          <template v-else-if="!v">
            <div class="tkt-sec-t first">티켓 정보</div>
            <div class="tkt-meta">
              <div v-for="(kk, n) in ['상태','우선순위','담당자','보고자','작업 기한','컴포넌트','소속 Epic','라벨']"
                   :key="'skm'+n" :class="{ wide: n === 7 }">
                <span class="k">{{ kk }}</span><span class="val"><span class="sk-ln"></span></span>
              </div>
            </div>
            <div class="tkt-sec-t">설명</div>
            <div class="tkt-desc tkt-desc-box sk-box">
              <span class="sk-ln" v-for="w in [96,90,93,72,45]" :key="'skd'+w" :style="{ width: w + '%' }"></span>
            </div>
            <div class="tkt-sec-t">코멘트</div>
            <div class="sk-box">
              <span class="sk-ln" v-for="w in [82,64,90]" :key="'skc'+w" :style="{ width: w + '%' }"></span>
            </div>
          </template>

          <template v-else>
          <!-- 티켓 제목은 타이틀바가 담당한다. 이 자리는 아래 메타 영역의 헤딩 -->
          <div class="tkt-sec-t first">티켓 정보</div>

          <div class="tkt-meta">
            <!-- 상태는 '고쳐 넣는 값'이 아니라 워크플로가 허용한 **전이를 실행**하는 것이라
                 editmeta 에 안 온다. 그래서 다른 필드처럼 FieldEdit 을 쓰지 못하지만, 쓰는 사람
                 입장에선 똑같이 '눌러서 바꾸는 것' 이어야 한다 — 카드 우클릭 메뉴와 같은 목록을 연다. -->
            <div><span class="k">상태</span><span class="val fe">
              <button class="fe-v" :class="{ on: stOpen }" @click.stop="openStatus" title="상태 변경"
                ><span class="val-st" :class="statusClass(v.statusCategory)">{{ v.status || '—' }}</span></button>
              <span v-if="stOpen" class="fe-pop" @click.stop>
                <div v-if="!stInfo" class="fe-none">불러오는 중…</div>
                <template v-else>
                  <div v-if="!stMayEdit" class="fe-none">담당자·보고자 또는 매니저만 바꿀 수 있습니다.</div>
                  <template v-else>
                    <button v-for="t in stList" :key="t.id" class="fe-i" @click="stPick = t; stOpen = false">
                      <span class="sr-dot" :class="'st-' + (t.toCategory || 'todo')"></span>{{ t.to }}
                      <em v-if="t.hasScreen" title="추가 입력이 필요합니다">…</em>
                    </button>
                    <div v-if="!stList.length" class="fe-none">가능한 전이가 없습니다.</div>
                  </template>
                </template>
                <div v-if="stErr" class="fe-err">{{ stErr }}</div>
              </span>
              <span v-if="stOpen" class="fe-back" @click.stop="stOpen = false"></span>
            </span></div>
            <div><span class="k">우선순위</span><span class="val">
              <FieldEdit :ticket="tk" field="priority" :meta="fmeta('priority')"
                         :value="v.priority" @saved="onFieldSaved">
                <PriIcon :rank="v.priRank" :name="v.priority" /><span class="prio-n">{{ v.priority || '미지정' }}</span>
              </FieldEdit></span></div>
            <div><span class="k">담당자</span><span class="val val-user">
              <FieldEdit :ticket="tk" field="assignee" :meta="fmeta('assignee')"
                         :value="v.assigneeId" :user-id="v.assigneeId" @saved="onFieldSaved">
                <Avatar v-if="v.assigneeId" :user="v.assigneeId" :name="v.assignee" :size="18" />{{ v.assignee || '—' }}
              </FieldEdit></span></div>
            <div><span class="k">보고자</span><span class="val val-user">
              <FieldEdit :ticket="tk" field="reporter" :meta="fmeta('reporter')"
                         :value="v.reporterId" :user-id="v.reporterId" @saved="onFieldSaved">
                <Avatar v-if="v.reporterId" :user="v.reporterId" :name="v.reporter" :size="18" />{{ v.reporter || '—' }}
              </FieldEdit></span></div>
            <div><span class="k">작업 기한</span><span class="val">
              <FieldEdit :ticket="tk" field="duedate" :meta="fmeta('duedate')"
                         :value="v.due" @saved="onFieldSaved">{{ v.due || '—' }}</FieldEdit></span></div>
            <div><span class="k">컴포넌트</span><span class="val">
              <FieldEdit :ticket="tk" field="components" :meta="fmeta('components')"
                         :value="v.components || []" @saved="onFieldSaved">
                <span v-if="v.components && v.components.length" class="tkt-labels">
                  <span v-for="c in v.components" :key="c" class="tkt-label comp">{{ c }}</span>
                </span><span v-else>—</span>
              </FieldEdit></span></div>
            <div><span class="k">소속 Epic</span><span class="val">
              <FieldEdit :ticket="tk" field="epic" :meta="fmeta(epicFieldId)"
                         :value="v.epicKey" @saved="onFieldSaved">
                <!-- '내 Task' 카드와 **같은 뱃지·같은 시그니처 컬러**. 같은 Epic 이 화면마다
                     다른 모습이면 색으로 소속을 알아보는 것 자체가 안 된다.
                     번호는 뱃지 밖에 따로 적지 않는다 — 계보 패널에 이미 있고, 여기서 알고 싶은
                     것은 '어느 Epic 소속인가' 다(번호가 필요하면 툴팁에 있다). -->
                <span v-if="v.epicKey" class="tkt-epic">
                  <span class="mt-epic" :style="{ '--sig': categoryColor(v.epicKey) }"
                        :title="v.epicKey + ' · ' + epicTitle">{{ epicTitle }}</span>
                </span><span v-else>—</span>
              </FieldEdit></span></div>
            <div class="wide"><span class="k">라벨</span><span class="val">
              <FieldEdit :ticket="tk" field="labels" :meta="fmeta('labels')"
                         :value="v.labels || []" @saved="onFieldSaved">
                <span v-if="v.labels && v.labels.length" class="tkt-labels">
                  <span v-for="l in v.labels" :key="l" class="tkt-label">{{ l }}</span>
                </span><span v-else>—</span>
              </FieldEdit></span></div>
          </div>

          <!-- Sub-Task 는 설명을 대충 쓰는 경우가 많아 상위(부모) 설명을 여기서 바로 볼 수 있게.
               자기 설명이 비어 있으면 자동으로 펼친다. -->
          <!-- 제목·폴딩버튼·영역을 **하나의 접이식 헤더 + 영역**으로 통합(중복 라벨 제거).
               헤더 자체가 토글이라 누르면 바로 아래 상위 설명이 펼쳐진다. -->
          <div v-if="parentOf" class="pdesc">
            <button class="pdesc-t" :class="{ open: pdescOpen }" @click="toggleParentDesc"
                    :title="parentOf.key + ' · ' + parentOf.summary">
              <span class="chev">&#9656;</span>
              <span class="pdesc-lbl">상위 티켓 설명</span>
              <span class="pdesc-k">{{ parentOf.key }}</span>
            </button>
            <div v-if="pdescOpen" class="pdesc-body">
              <div v-if="pdescErr" class="muted">상위 설명을 불러오지 못했습니다: {{ pdescErr }}</div>
              <div v-else-if="!pdesc" class="loading">불러오는 중…</div>
              <div v-else-if="descEmpty(pdesc.descriptionHtml)" class="tkt-desc tkt-desc-box pdesc-box">
                <p class="muted">상위 티켓에도 설명이 없습니다.</p></div>
              <div v-else class="tkt-desc tkt-desc-box pdesc-box" @click="onContentClick"
                   v-html="pdesc.descriptionHtml"></div>
            </div>
          </div>

          <!-- 본문 편집 — 설명이 비었든 영역이 여럿이든 **한 자리**에서 연다.
               분기마다 버튼을 달면 어떤 티켓에선 보이고 어떤 티켓에선 안 보인다(실제로 그랬다). -->
          <div v-if="descEdit" class="tkt-desc-edit">
            <div class="tkt-sec-t">설명 편집</div>
            <!-- 편집을 시작한 뒤 **남이 본문을 바꿨다.** 자동으로 합치지 않는다 — 두 글을 기계가
                 섞으면 어느 쪽도 아닌 글이 남는다. 사실만 알리고 판단은 사람이 한다.
                 한 번만 뜬다(고칠 때마다 뜨면 경고가 아니라 소음이다). -->
            <div v-if="descConflict" class="tkt-desc-conflict">
              ⚠ 누군가 이 본문을 수정했습니다. 작성 중이던 내용을 백업한 뒤 새로고침을 권장합니다.
            </div>
            <!-- 댓글과 **같은 에디터** — 여기만 다른 편집기를 쓰면 표·코드·이미지 붙여넣기가
                 되는 곳과 안 되는 곳이 생긴다. 저장은 이 화면이 소유한다(버튼이 둘이면 안 된다). -->
            <CommentEditor ref="ded" :ticket-key="tk" :initial="v.descriptionHtml" hide-footer
                           sections kind="description"
                           :submit-fn="saveDesc" @cancel="descEdit = false" />
            <div class="tkt-desc-edit-f">
              <span v-if="descErr" class="tkt-cmt-err">{{ descErr }}</span>
              <button class="cmt-ed-btn ghost" @click="descEdit = false; stopWatchDesc()">취소</button>
              <button class="cmt-ed-btn primary" :disabled="descBusy"
                      @click="$refs.ded && $refs.ded.submit()">{{ descBusy ? '저장 중…' : '저장' }}</button>
            </div>
          </div>

          <div v-else-if="ownDescEmpty">
            <div class="tkt-sec-t">설명
              <button v-if="fmeta('description')" class="sec-edit" @click="startDescEdit">수정</button>
            </div>
            <div class="tkt-desc tkt-desc-box"><p class="muted">설명이 없습니다.</p></div>
          </div>
          <!-- 구분선(=== 제목 ===)으로 나뉜 영역을 각각 제목 달린 카드로. 구분선이 없으면 1개뿐 -->
          <template v-else v-for="(sec, i) in descSections" :key="i">
            <div class="tkt-sec-t">{{ sec.title || '설명' }}
              <!-- 첫 영역에만 — 영역마다 버튼이 있으면 '이 영역만 고치는 건가' 로 읽힌다.
                   실제로는 본문 전체를 한 번에 편집한다(구분선도 본문의 일부다). -->
              <button v-if="i === 0 && fmeta('description')" class="sec-edit"
                      @click="startDescEdit">수정</button>
            </div>
            <!-- {N} 시스템정보 + {N} 테이블정보 는 항상 짝 → 한 행에 2단으로 -->
            <div v-if="sec.columns" class="tkt-two secpair">
              <div v-for="(c, j) in sec.columns" :key="j" class="tkt-two-col">
                <div class="secpair-t">{{ c.title }}</div>
                <div class="tkt-desc tkt-desc-box">
                  <table v-if="c.kv" class="kv-table">
                    <tr v-for="(r, k) in c.kv" :key="k">
                      <th>{{ r.k }}</th>
                      <td @click="onContentClick" v-html="r.html || '&mdash;'"></td>
                    </tr>
                  </table>
                  <div v-else @click="onContentClick" v-html="c.html"></div>
                </div>
              </div>
            </div>
            <!-- 영역 전체가 'key : value' 면 표로 (VoC 시스템 주입 블록) -->
            <div v-else-if="sec.kv" class="tkt-desc tkt-desc-box">
              <table class="kv-table">
                <tr v-for="(r, j) in sec.kv" :key="j">
                  <th>{{ r.k }}</th>
                  <td @click="onContentClick" v-html="r.html || '&mdash;'"></td>
                </tr>
              </table>
            </div>
            <div v-else class="tkt-desc tkt-desc-box" @click="onContentClick" v-html="sec.html"></div>
          </template>

          <!-- 하위 Task — Epic 이면 소속 Task, Task 면 Sub-Task. Sub-Task 는 아래가 없어
               칸 자체가 안 뜬다(빈 칸을 남기면 '있는데 못 불러온 것' 처럼 읽힌다).
               좌측 계보 패널에도 같은 목록이 있지만 그건 **이동용**이고, 여기는 상태·담당자·
               마감까지 한눈에 보는 **현황**이다. -->
          <div v-if="canHaveKids" class="tkt-kids">
            <div class="tkt-sec-t">{{ kidsLabel }}<span v-if="children.length"> ({{ children.length }})</span>
              <!-- 정렬 기준 — '내 Task' 와 **같은 규칙**을 쓴다(마감↔우선순위가 서로의 2차 기준).
                   화면마다 순서가 다르면 같은 목록을 두 번 익혀야 한다. -->
              <span v-if="children.length > 1" class="kid-sortwrap">
                <span class="kid-sortl">정렬기준</span>
                <span class="cmt-sort">
                  <button v-for="o in KID_SORTS" :key="o.k" type="button" class="cmt-sort-b"
                          :class="{ on: kidSort === o.k }" @click="setKidSort(o.k)"
                          :title="o.hint">{{ o.label }}</button>
                </span>
              </span>
            </div>
            <!-- 접어도 **아무것도 안 보이게 하지 않는다.** 접힌 목록이 통째로 사라지면 '없는 것'
                 처럼 읽힌다. 앞 5개는 그대로 두고 6번째부터 흐려지며 잘려, 뒤에 더 있다는 것이
                 목록 자체로 보이게 한다(개수는 버튼에 적는다). -->
            <!-- 비어 있어도 칸은 남는다 — 목록이 통째로 없으면 이 티켓에 하위를 둘 수 있다는
                 것 자체를 모른다. 다만 '없음' 을 글로 적지는 않는다: 바로 아래 추가 카드가
                 무엇을 할 수 있는지 이미 말하고 있어, 안내문은 자리만 먹는다. -->
            <!-- 하위는 접지 않는다 — 이 칸은 '이 티켓이 무엇으로 이뤄져 있나' 자체라,
                 몇 개를 감추면 그 답이 반쪽이 된다. 길면 스크롤하면 된다. -->
            <div v-if="children.length" class="kidlist">
              <!-- 한 줄의 뼈대는 '내 Task' 카드와 같다: [타입][번호][제목] | 상태 | 담당자 | 마감.
                   같은 정보가 화면마다 다른 순서로 놓이면 눈이 매번 다시 찾아야 한다. -->
              <div v-for="c in kidsSorted" :key="'k-' + c.key" class="kidrow tkt" :data-key="c.key"
                   :title="c.key + ' ' + c.summary + (c.priority ? ' · ' + c.priority : '')">
                <PriIcon :rank="c.priRank" :name="c.priority" />
                <!-- 타입 뱃지는 **갈릴 때만** 쓴다. Sub-Task 목록은 전부 Sub-Task 라 같은 그림을
                     줄마다 반복하며 자리만 먹는다. Epic 밑은 Task·Story·Bug 가 섞여 구별이 필요하다. -->
                <!-- 칸 자체는 비어도 남긴다 — 줄마다 칸 수가 다르면 칼럼을 맞출 수 없다. -->
                <span class="kid-ty"><TypeBadge v-if="isEpic" :type="c.type" /></span>
                <b class="kid-k">{{ c.key }}</b>
                <span class="kid-s">{{ c.summary }}</span>
                <DueText :card="kidCard(c)" no-date />
                <span class="kid-st" :class="statusClass(c.statusCategory)">{{ c.status }}</span>
                <!-- 담당자 칸 폭은 **내용이 정한다**(subgrid — 아래 CSS). 고정 폭이면 이름이
                     짧은 목록엔 줄마다 빈 띠가 남는다. 미할당도 같은 자리·같은 크기를 지킨다 —
                     아바타가 빠지면 그 줄만 글자가 왼쪽으로 밀려 세로로 훑을 수가 없다. -->
                <span class="kid-a">
                  <Avatar v-if="c.assigneeId" :user="c.assigneeId" :name="c.assignee" :size="18" />
                  <span v-else class="kid-noav" aria-hidden="true"></span>
                  <span class="kid-an" :class="{ none: !c.assigneeId }">{{ c.assignee || '미할당' }}</span>
                </span>
              </div>
            </div>

            <!-- 추가 카드 — 목록의 마지막 줄과 **같은 크기**로 앉아, 새 줄이 여기에 생긴다는 것을
                 자리로 말한다. 다이얼로그를 새로 띄우지 않는 이유: 지금 보고 있는 맥락(부모가
                 무엇이고 형제가 어떤지)이 화면에서 사라지면, 그걸 보려고 연 창에서 그걸 잃는다. -->
            <button v-if="kidCreate" class="kidadd" @click="adding = true">
              ＋ {{ isEpic ? '하위 Task 추가' : 'Sub Task 추가' }}
            </button>

            <NewChildDialog v-if="adding" :parent="tk" :is-epic="isEpic" :types="kidTypes"
                            :parent-due="(v && v.due) || ''"
                            :parent-components="(v && v.components) || []"
                            @close="adding = false" @created="onKidCreated" />
          </div>

          <!-- 설명 아래 2분할: 첨부파일 | 관련문서(언급된 Confluence 문서) -->
          <div class="tkt-two">
            <div class="tkt-two-col">
              <div class="tkt-sec-t has-add">첨부파일<span v-if="atts.length"> ({{ atts.length }})</span>
                <button class="add-b" title="파일 첨부" @click="$refs.file.click()">＋</button>
                <input ref="file" type="file" multiple hidden @change="onFilePick">
              </div>
              <div v-if="upErr" class="tkt-cmt-err">{{ upErr }}</div>
              <div v-if="uploading" class="muted mini">첨부 올리는 중…</div>
              <div v-if="!atts.length" class="muted mini">첨부파일 없음 — 파일을 이 창에 끌어다 놓아도 됩니다</div>
              <!-- 목록이 길면 기본으로 접는다 — 첨부가 스무 개인 티켓에서 본문·코멘트가 화면
                   밖으로 밀려난다. 앞 5개는 남기고 6번째가 흐려지며, 그 위에 펼침 버튼이 앉는다. -->
              <div v-else class="foldwrap" :class="{ folded: !attOpen }">
              <div class="chipwrap" :class="{ 'fold-peek': !attOpen }">
                <!-- 첨부 목록 칩과 본문 속 파일 뱃지는 **같은 것**이다 — 모양이 갈라지면
                     "이건 첨부고 저건 뭐지" 가 된다. data-ext 로 아이콘·색 규칙을 공유한다. -->
                <span v-for="a in atts" :key="a.id" class="fchip-w">
                  <a class="fchip" :class="{ img: a.isImage }"
                     :data-ext="extOf(a.filename)" :href="a.url" :download="a.filename" rel="noopener"
                     :title="a.filename + ' · ' + fsize(a.size) + (a.author ? ' · ' + a.author : '')">
                    <span class="fchip-ic"></span>
                    <span class="fchip-n">{{ a.filename }}</span>
                    <!-- 메타는 **조각으로** 둔다: 폭이 모자라면 분 → 시각 → 용량 순으로 사라진다.
                         한 덩어리 문자열이면 '…' 로 잘려 아무 뜻도 안 남는다. -->
                    <span class="fchip-m">
                      <i class="m-d">{{ fdate(a.created) }}</i><i class="m-t">{{ fhour(a.created) }}<b class="m-min">:{{ fmin(a.created) }}</b></i><i class="m-s">{{ fsize(a.size) }}</i>
                    </span>
                  </a>
                  <!-- ✕ 는 **바꿀 수 있는 사람에게만**. 없는데 보이면 눌러 보고서야 안 되는 걸 안다. -->
                  <button v-if="mayEdit" class="chip-x" title="첨부 삭제"
                          @click.stop.prevent="delAttachment(a)">✕</button>
                </span>
              </div>
              <button v-if="atts.length > FOLD_AT" class="fold-b" @click="attOpen = !attOpen">
                {{ attOpen ? '접기' : '+' + (atts.length - FOLD_AT) + '개 더' }}</button>
              </div>
            </div>
            <div class="tkt-two-col">
              <div class="tkt-sec-t has-add">관련문서<span v-if="refDocs.length"> ({{ refDocs.length }})</span>
                <button class="add-b" title="관련문서 추가 (Confluence 문서·웹 링크)"
                        @click.stop="docPick = !docPick">＋</button>
              </div>
              <LinkPicker v-if="docPick" mode="confluence" :busy="docBusy" :err="docErr"
                          @close="docPick = false" @pick="addDoc" />
              <div v-if="!refDocs.length" class="muted mini">관련문서 없음</div>
              <div v-else class="foldwrap" :class="{ folded: !docOpen }">
              <div class="chipwrap" :class="{ 'fold-peek': !docOpen }">
                <span v-for="(d, i) in refDocs" :key="i" class="fchip-w">
                  <a class="fchip doc" :href="d.url" target="_blank" rel="noopener" :title="d.url">
                    <span class="fchip-ic conf"></span>
                    <span class="fchip-n">{{ d.title }}</span>
                  </a>
                  <!-- 본문에 **언급**된 문서는 링크가 아니라 글이라 뗄 수 없다(linkId 가 없다) -->
                  <button v-if="mayEdit && d.linkId" class="chip-x" title="관련문서 떼기"
                          @click.stop.prevent="delDoc(d)">✕</button>
                </span>
              </div>
              <button v-if="docs.length > FOLD_AT" class="fold-b" @click="docOpen = !docOpen">
                {{ docOpen ? '접기' : '+' + (docs.length - FOLD_AT) + '개 더' }}</button>
              </div>
            </div>
          </div>

          </template><!-- /본문(v) -->

          <!-- 코멘트 — 본문(v)과 무관하게 자기 상태로 렌더 -->
          <template v-if="!err">
            <div class="tkt-sec-t">코멘트<span v-if="comments"> ({{ comments.length }})</span>
              <span v-if="comments && comments.length > 1" class="cmt-sort">
                <button type="button" class="cmt-sort-b" :class="{ on: cmtSort === 'new' }"
                        @click="cmtSort = 'new'" title="최신 댓글이 위로">최신순</button>
                <button type="button" class="cmt-sort-b" :class="{ on: cmtSort === 'old' }"
                        @click="cmtSort = 'old'" title="오래된 댓글이 위로">오래된순</button>
              </span>
            </div>
            <div v-if="!comments" class="loading">코멘트 불러오는 중…</div>
            <template v-else>
              <div v-if="!comments.length" class="muted">코멘트가 없습니다.</div>
              <div v-else class="tkt-comments">
                <div v-for="(c, i) in sortedComments" :key="c.id || i" class="tkt-cmt"
                     :style="{ '--sig': sigColor(c.authorId) }">
                  <div class="tkt-cmt-h">
                    <Avatar :user="c.authorId" :name="c.author" :size="20" /><b>{{ c.author }}</b>
                    <span class="muted">{{ fdt(c.date) }}</span>
                    <span v-if="c.updated && c.updated !== c.date" class="muted tkt-cmt-edited">· 수정됨</span>
                    <span v-if="canEdit(c) && editingId !== c.id" class="tkt-cmt-acts">
                      <button class="tkt-cmt-act" @click="startEdit(c)">수정</button>
                      <button class="tkt-cmt-act" @click="delComment(c)">삭제</button>
                    </span>
                  </div>
                  <CommentEditor v-if="editingId === c.id" :ticket-key="tk" :initial="editInitial"
                    submit-label="저장" :submit-fn="(md) => submitEdit(c, md)"
                    @submitted="onEdited" @cancel="cancelEdit" />
                  <div v-else class="tkt-cmt-b tkt-desc" @click="(e) => onContentClick(e, c)" v-html="c.html"></div>
                </div>
              </div>
              <!-- 작성 — 항상 노출(me 조회 실패에도 사라지지 않게). 미인증이면 제출 때 로그인 오버레이 -->
              <div class="tkt-cmt-compose">
                <button v-if="!composing" class="tkt-cmt-addbtn" @click="startCompose">＋ 댓글 달기</button>
                <CommentEditor v-else :ticket-key="tk" submit-label="등록" :submit-fn="submitNew"
                  @submitted="onComposed" @cancel="cancelCompose" />
              </div>
              <div v-if="editErr" class="tkt-cmt-err">{{ editErr }}</div>
            </template>
          </template>
          </div><!-- /.tkt-main -->

          <!-- 접힌 상태에서 다시 펴는 손잡이(우측 가장자리) -->
          <button v-if="tlHidden && hasTl" class="tl-show" title="일정·타임라인 패널 펼치기"
                  @click="setTlHidden(false)">‹</button>
          <!-- 우측: 일정 + 타임라인 (폭 조절·접기 — 좌측 스파인과 대칭) -->
          <aside v-if="hasTl && !tlHidden" class="tkt-tl">
            <button class="tl-hide" title="일정·타임라인 패널 접기" @click="setTlHidden(true)">›</button>
            <!-- 왼쪽 가장자리를 끌어 폭 조절 -->
            <div class="tl-grip" title="너비 조절 — 드래그" @mousedown.prevent="startTlDrag"></div>
            <template v-if="v">
              <div class="grp grp-who">
              <div class="sec sec-dates">
              <div class="tkt-mlabel sf-gap">일정</div>
              <div class="sfield"><span class="sf-k">생성일</span><span class="sf-v">{{ fts(v.created) || '—' }}</span></div>
              <div class="sfield"><span class="sf-k">시작일</span><span class="sf-v">{{ fts(v.started) || '—' }}</span></div>
              <div class="sfield"><span class="sf-k">작업 기한</span>
                <span class="sf-v" :class="{ overdue: v.due && !v.resolved && fy(v.due) < today }">{{ fts(v.due) || '—' }}</span></div>
              <div class="sfield"><span class="sf-k">완료일</span><span class="sf-v">{{ fts(v.resolved) || '—' }}</span></div>
              <div class="sfield"><span class="sf-k">최종 수정일</span><span class="sf-v">{{ fts(v.updated) || '—' }}</span></div>
              </div>
              </div>
            </template>

            <div v-if="timeline.length" class="sec sec-history">
            <div class="tkt-mlabel sf-gap">타임라인</div>
              <div v-for="(e, i) in timeline" :key="i" class="tl-row"
                   :class="{ child: e.srcKey, tkt: !!e.srcKey }" :data-key="e.srcKey || null"
                   :title="(e.srcKey ? e.srcKey + ' · ' : '') + tlText(e)">
                <span class="tl-rail">
                  <span class="tl-dot" :class="'k-' + e.kind"></span>
                  <span v-if="i < timeline.length - 1" class="tl-line"></span>
                </span>
                <span class="tl-body">
                  <span class="tl-t"><span v-if="e.srcKey" class="tl-src">{{ e.srcKey }}</span
                    ><template v-if="tlKind(e) === 'comment'"
                      ><span class="tl-b on">{{ e.author || '—' }}</span> 댓글 작성</template
                    ><template v-else-if="tlBadged(e)">{{ tlLabel(e) }}
                      <span class="tl-b" :class="tlBCls(e, e.from)">{{ tlVal(e, e.from) }}</span
                      ><span class="tl-arw">→</span
                      ><span class="tl-b on" :class="tlBCls(e, e.to)">{{ tlVal(e, e.to) }}</span>
                    </template><template v-else>{{ tlText(e) }}</template></span>
                  <span class="tl-m">{{ e.author || '—' }} · {{ fdt(e.date) }}</span>
                </span>
              </div>
            </div>
          </aside>
        </div><!-- /.tkt-cols -->
        </div><!-- /.tkt-body -->

        <!-- 좌하단 강제 새로고침 — 이 티켓을 캐시 비우고 서버에서 다시 받는다(체크박스 등 최신 반영) -->
        <button class="tkt-refresh" :class="{ busy: refreshing }" @click="hardRefresh"
                :title="refreshing ? '새로 받는 중…' : '강제 새로고침 (캐시 비우고 다시 받기)'"
                aria-label="강제 새로고침">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 4v5h-5"/>
          </svg>
        </button>
      </div>

      <div v-if="zoom" class="tkt-zoom" @click="zoom = null">
        <template v-if="zoom.type === 'img'">
          <div v-if="zoomLoading" class="tkt-zoom-spin"><span class="spinner"></span></div>
          <img class="tkt-zoom-img" :src="zoom.src" :alt="zoom.alt"
               :style="{ visibility: zoomLoading ? 'hidden' : 'visible' }"
               @load="zoomLoading = false" @error="zoomLoading = false">
        </template>
        <div v-else class="tkt-zoom-table" @click.stop v-html="zoom.html"></div>
        <button class="tkt-zoom-x" @click.stop="zoom = null" aria-label="닫기">✕</button>
      </div>

      <!-- 상태 전이 — 카드 우클릭과 **같은 창**. 코멘트·담당자·해결책 같은 전이 화면 입력을
           여기서만 다르게 받으면 같은 일을 두 벌로 관리하게 된다. -->
      <TransitionDialog v-if="stPick" :ticket="tk" :transition="stPick"
                        @close="stPick = null" @done="onTransitioned()" />
    </div>`,
};
