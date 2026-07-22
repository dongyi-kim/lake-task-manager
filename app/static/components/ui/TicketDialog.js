// TicketDialog.js — 인앱 티켓 상세 다이얼로그(모달). 티켓 링크(.tkt) 클릭 시 app-root 가 keyId 를 넘겨 연다.
// description 은 백엔드에서 **정화된 HTML**(app/htmlsafe.py) 이라 v-html 로 그대로 렌더(table/code/quote/panel/callout/img).
// 코멘트 텍스트는 Vue 기본 이스케이프({{ }})로 안전 표시. Esc/백드롭/X 로 닫기.
import { api } from "../../lib/api.js";
import { ymd, ymdhm, ts, esc } from "../../lib/fmt.js";
import { TYPE_BG, typeLabel, sigColor } from "../../lib/colors.js";
import TypeBadge from "./TypeBadge.js";
import Avatar from "./Avatar.js";
import CommentEditor from "./CommentEditor.js";
import { highlightIn as hljsHighlight, ensureHljsTheme } from "../../lib/hljs.js";
import { loadTiptap } from "../../lib/tiptap.js";

// Confluence URL 에서 문서 제목 추출(내부 <a> 텍스트 무시) — /pages/{id}/{slug} 또는 /display/{space}/{slug}.
function confTitleFromUrl(u) {
  try {
    const path = new URL(u, location.href).pathname;
    const m = path.match(/\/pages\/\d+\/([^/]+)\/?$/) || path.match(/\/display\/[^/]+\/([^/]+)\/?$/);
    if (m && m[1]) return decodeURIComponent(m[1].replace(/\+/g, " ")).trim();
  } catch (e) { /* noop */ }
  return null;
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
  components: { TypeBadge, Avatar, CommentEditor },
  // mode: dialog(모달, 기본) | page(새 창 전용 단독 페이지 — 오버레이·닫기 없음)
  props: { keyId: { type: String, required: true },
           mode: { type: String, default: "dialog" },
           theme: { type: String, default: "light" } },   // 페이지 모드 테마 버튼 표시용
  emits: ["close", "search", "toggle-theme"],
  data() { return { v: null, comments: null, ancestors: [], siblings: [], timeline: [], children: [], related: [], atts: [], docs: [], sibOpen: true,
                    pdesc: null, pdescOpen: false, pdescErr: "",
                    me: null, composing: false, editingId: null, editInitial: "", editErr: "",
                    cmtSort: "new",              // new=최신순(기본) | old=오래된순. 초 단위까지 비교.
                    err: "", expanded: false, zoom: null, zoomLoading: false }; },
  mounted() {
    // Esc: 확대(zoom)가 열려 있으면 그것부터 닫고, 아니면 다이얼로그 닫기
    this._onKey = (e) => {
      if (e.key !== "Escape") return;
      if (this.zoom) { this.zoom = null; } else { this.$emit("close"); }
    };
    window.addEventListener("keydown", this._onKey);
    this.load();
    // 에디터·구문강조 CDN 프리로드 — 티켓 다이얼로그/풀뷰가 열리는 시점에 미리 받아둔다.
    // (버전 고정 URL 이라 브라우저가 장기 캐시 → 이후엔 네트워크 없이 즉시.) '댓글 달기' 지연 제거.
    ensureHljsTheme(document.documentElement.getAttribute("data-theme") === "dark");
    loadTiptap().catch(() => { /* CDN 차단 등 — 실제 사용 시 에러 표시 */ });
  },
  unmounted() { window.removeEventListener("keydown", this._onKey); },
  computed: {
    today() { return ymd(new Date().toISOString()); },   // 기한 초과 판정용
    tk() { return (this.v && this.v.key) || this.keyId; },   // 쓰기 대상 티켓 키
    // 코멘트 정렬 — created 를 ms 로 파싱해 **초 단위까지** 비교(같은 분에 여러 개 달려도 안정).
    sortedComments() {
      const list = (this.comments || []).slice();
      const t = (c) => { const n = Date.parse(c && c.date); return isNaN(n) ? 0 : n; };
      list.sort((a, b) => (this.cmtSort === "old" ? t(a) - t(b) : t(b) - t(a)));
      return list;
    },
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
    v() { this.$nextTick(this.augment); },            // 설명 렌더 후 확대버튼·뱃지 주입
    comments() { this.$nextTick(this.augment); },     // 코멘트 렌더 후
    pdesc() { this.$nextTick(this.augment); },        // 상위 설명 렌더 후(확대버튼·뱃지 주입)
    // 이 Sub-Task 에 설명이 없으면 상위 설명을 자동으로 펼친다(가장 흔한 케이스)
    parentOf(p) { if (p && this.ownDescEmpty && !this.pdescOpen) this.toggleParentDesc(); },
  },
  methods: {
    // 본문(Description)·코멘트·계보(조상/형제)·타임라인을 **동시에 출발**시키고 각자 도착하는 대로
    // 개별 렌더한다(서로 막지 않음). 느린 타임라인이 본문을 기다리지 않게 하는 게 핵심.
    // 다이얼로그는 계보/형제/타임라인 클릭으로 티켓을 갈아타므로, 늦게 온 이전 티켓 응답이
    // 새 티켓 화면을 덮지 않도록 요청 토큰(_req)으로 가드한다.
    async load() {
      const key = this.keyId;
      const my = this._req = (this._req || 0) + 1;
      const fresh = () => my === this._req && this.keyId === key;
      this.err = ""; this.v = null; this.comments = null;
      this.ancestors = []; this.siblings = []; this.timeline = [];
      this.children = []; this.related = []; this.atts = []; this.docs = [];
      this.pdesc = null; this.pdescOpen = false; this.pdescErr = "";
      this.composing = false; this.editingId = null; this.editInitial = ""; this.editErr = "";

      if (!this.me) api.me().then((m) => { this.me = m || {}; }).catch(() => { this.me = {}; });
      api.ticketComments(key).then((c) => { if (fresh()) this.comments = c; })
        .catch(() => { if (fresh()) this.comments = []; });
      api.ticketAncestors(key).then((a) => { if (fresh()) this.ancestors = a || []; }).catch(() => {});
      api.ticketSiblings(key).then((s) => { if (fresh()) this.siblings = s || []; }).catch(() => {});
      api.ticketTimeline(key).then((t) => { if (fresh()) this.timeline = t || []; }).catch(() => {});
      api.ticketChildren(key).then((c) => { if (fresh()) this.children = c || []; }).catch(() => {});
      api.ticketRelated(key).then((r) => { if (fresh()) this.related = r || []; }).catch(() => {});
      api.ticketAttachments(key).then((a) => { if (fresh()) this.atts = a || []; }).catch(() => {});
      api.ticketDocuments(key).then((d) => { if (fresh()) this.docs = d || []; }).catch(() => {});

      try {
        const v = await api.ticket(key);
        if (fresh()) this.v = v;
      } catch (e) {
        if (fresh()) {
          this.err = e && e.message === "HTTP 404" ? "티켓을 찾을 수 없습니다: " + key : (e.message || "불러오기 실패");
        }
      }
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
    // 글쓴이 시그니처 컬러 — 기본 아바타(프사 없는 사람)와 같은 색이어야 하므로 colors.js 단일 소스.
    sigColor,
    canEdit(c) { return !!(this.me && this.me.id && c && c.authorId === this.me.id); },
    reloadComments() {
      const key = this.keyId;
      return api.ticketComments(key)
        .then((c) => { if (this.keyId === key) this.comments = c || []; })
        .catch(() => {});
    },
    startCompose() { this.editingId = null; this.editErr = ""; this.composing = true; },
    cancelCompose() { this.composing = false; },
    submitNew(md) { return api.commentCreate(this.tk, md); },     // CommentEditor 가 await
    onComposed() { this.composing = false; this.reloadComments(); },
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
    onEdited() { this.editingId = null; this.editInitial = ""; this.reloadComments(); },
    async delComment(c) {
      if (!window.confirm("이 댓글을 삭제할까요?")) return;
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
    statusClass(cat) { return "st-" + (cat || "todo"); },
    // 확대 버튼(.zoom-btn)만 반응 — 표는 드래그 복사가 가능해야 하므로 내용 클릭으로는 확대 안 함.
    onContentClick(e) {
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
        const label = confTitleFromUrl(a.getAttribute("href") || "") || (a.textContent || "").trim() || "Confluence 문서";
        a.innerHTML = '<span class="conf-title">' + esc(label) + "</span>";
        a.title = label;
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
        a.innerHTML = '<span class="jb-dot"></span><b class="jb-key">' + esc(key) + "</b>"
          + '<span class="jb-name"></span><span class="jb-meta"></span>';
        api.ticketBadge(key).then((b) => {
          if (!b) return;
          a.querySelector(".jb-dot").className = "jb-dot st-" + (b.statusCategory || "todo");
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
    <div :class="[isPage ? 'tkt-page' : 'tkt-ov', { expanded }]" @click.self="!isPage && $emit('close')">
      <div class="tkt-dlg" :class="{ expanded, page: isPage }"
           :role="isPage ? null : 'dialog'" :aria-modal="isPage ? null : 'true'">
        <!-- 최상단 타이틀바 — 배경은 티켓 타입 색을 따른다.
             좌: "[타입] [번호] [제목] - 상태" / 우: Jira에서 열기 · 최대화 · 닫기 -->
        <div class="tkt-bar" :style="{ '--tc': typeColor(v && v.type) }">
          <span class="tb-name" :title="barTitle">
            <span class="tb-type">{{ v ? v.type : '' }}</span>
            <span class="tb-key">{{ (v && v.key) || keyId }}</span>
            <span class="tb-sum">{{ v ? v.summary : '불러오는 중…' }}</span>
            <span v-if="v && v.status" class="tb-st" :class="statusClass(v.statusCategory)">- {{ v.status }}</span>
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
            <a v-if="v && v.url" class="tb-btn" :href="v.url" target="_blank" rel="noopener"
               title="Jira에서 열기">Jira에서 열기 ↗</a>
            <button v-if="isPage" class="theme-btn" @click="$emit('toggle-theme')"
                    :title="theme === 'dark' ? '라이트 모드로' : '다크 모드로'">
              <span v-if="theme === 'dark'">☀ Light</span><span v-else>🌙 Dark</span>
            </button>
            <!-- data-ext: 같은 호스트지만 앱 창(Chromium)이 아니라 시스템 기본 브라우저로.
                 run.py 의 외부링크 훅이 이 속성을 보고 넘긴다. -->
            <a v-if="!isPage" class="tb-btn ico" :href="pageHref" target="_blank" rel="noopener"
               data-ext aria-label="새 창에서 열기" title="새 창에서 열기">
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

        <!-- 섹션별 독립 렌더: 스파인(계보/형제/타임라인)은 본문(v) 응답을 기다리지 않는다.
             본문·코멘트도 각자 자기 상태가 채워지는 대로 그려진다. -->
        <div class="tkt-cols">
          <!-- 좌측 세로 스파인 — 계보(조상→현재, 레일+진척) + 형제 목록. 클릭 시 해당 티켓으로 이동 -->
          <aside v-if="spine.length > 1 || siblings.length || timeline.length" class="tkt-spine">
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
            <div v-if="children.length" class="sec sec-children spn-sib">
              <div class="tkt-mlabel">하위 Task {{ children.length }}</div>
              <div v-for="c in children" :key="'ch-' + c.key" class="spn-sibrow tkt"
                   :data-key="c.key" :title="c.type + ' ' + c.key + ' · ' + c.summary">
                <span class="spn-sdot" :class="'st-' + (c.statusCategory || 'todo')"></span>
                <span class="spn-stitle">{{ c.summary }}</span>
              </div>
            </div>
            </div>

            <div class="grp grp-rel">
            <div v-if="related.length" class="sec sec-related spn-sib">
              <div class="tkt-mlabel">관련 Task {{ related.length }}</div>
              <div v-for="r in related" :key="'rel-' + r.key" class="spn-sibrow tkt"
                   :data-key="r.key" :title="r.rel + ' · ' + r.key + ' · ' + r.summary">
                <span class="spn-sdot" :class="'st-' + (r.statusCategory || 'todo')"></span>
                <span class="spn-stitle">{{ r.summary }}</span>
                <span class="spn-rel" :class="r.via">{{ r.via === 'link' ? r.rel : '언급' }}</span>
              </div>
            </div>
            <div v-if="siblings.length" class="sec sec-sib spn-sib">
              <div class="tkt-mlabel spn-sib-h" @click="sibOpen = !sibOpen">
                <span class="chev" :class="{ open: sibOpen }">▸</span>
                <span>형제 {{ siblings.length }}</span>
                <span v-if="sibPos" class="spn-pos">{{ sibPos }}/{{ siblings.length }}</span>
              </div>
              <template v-if="sibOpen">
                <div v-for="s in siblings" :key="s.key" class="spn-sibrow"
                     :class="{ cur: s.current, other: isOther(s), tkt: !s.current }"
                     :data-key="s.current ? null : s.key"
                     :title="s.key + ' · ' + s.summary + (s.component ? ' (' + s.component + ')' : '')">
                  <span class="spn-sdot" :class="'st-' + (s.statusCategory || 'todo')"></span>
                  <span class="spn-stitle">{{ s.summary }}</span>
                  <span v-if="isOther(s)" class="spn-scomp">{{ s.component }}</span>
                </div>
              </template>
            </div>
            </div>

          </aside>

          <div class="tkt-main">
          <div v-if="err" class="tkt-err">{{ err }}</div>
          <div v-else-if="!v" class="tkt-load"><span class="spinner"></span> 불러오는 중…</div>

          <template v-else>
          <!-- 티켓 제목은 타이틀바가 담당한다. 이 자리는 아래 메타 영역의 헤딩 -->
          <div class="tkt-sec-t first">티켓 정보</div>

          <div class="tkt-meta">
            <div><span class="k">상태</span><span class="val"
              ><span class="val-st" :class="statusClass(v.statusCategory)">{{ v.status || '—' }}</span></span></div>
            <div><span class="k">우선순위</span><span class="val"
              ><span class="prio-b" :class="prioCls(v.priority)">{{ v.priority || '미지정' }}</span></span></div>
            <div><span class="k">담당자</span><span class="val val-user"><Avatar v-if="v.assigneeId"
              :user="v.assigneeId" :name="v.assignee" :size="18" />{{ v.assignee || '—' }}</span></div>
            <div><span class="k">보고자</span><span class="val val-user"><Avatar v-if="v.reporterId"
              :user="v.reporterId" :name="v.reporter" :size="18" />{{ v.reporter || '—' }}</span></div>
            <div><span class="k">컴포넌트</span><span class="val">{{ (v.components && v.components.length) ? v.components.join(', ') : '—' }}</span></div>
            <div class="wide"><span class="k">라벨</span><span class="val">
              <span v-if="v.labels && v.labels.length" class="tkt-labels">
                <span v-for="l in v.labels" :key="l" class="tkt-label">{{ l }}</span>
              </span>
              <span v-else>—</span>
            </span></div>
          </div>

          <!-- Sub-Task 는 설명을 대충 쓰는 경우가 많아 상위(부모) 설명을 여기서 바로 볼 수 있게.
               자기 설명이 비어 있으면 자동으로 펼친다. -->
          <div v-if="parentOf" class="pdesc">
            <div class="tkt-sec-t">상위 티켓 설명</div>
            <button class="pdesc-t" :class="{ open: pdescOpen }" @click="toggleParentDesc"
                    :title="parentOf.key + ' · ' + parentOf.summary">
              <span class="chev">&#9656;</span>
              <span>{{ pdescOpen ? '접기' : '상위 티켓 설명 펼쳐 보기' }}</span>
              <span class="pdesc-k">{{ parentOf.key }}</span>
            </button>
            <div v-if="pdescOpen">
              <div v-if="pdescErr" class="muted">상위 설명을 불러오지 못했습니다: {{ pdescErr }}</div>
              <div v-else-if="!pdesc" class="loading">불러오는 중…</div>
              <div v-else-if="descEmpty(pdesc.descriptionHtml)" class="tkt-desc tkt-desc-box pdesc-box">
                <p class="muted">상위 티켓에도 설명이 없습니다.</p></div>
              <div v-else class="tkt-desc tkt-desc-box pdesc-box" @click="onContentClick"
                   v-html="pdesc.descriptionHtml"></div>
            </div>
          </div>

          <div v-if="ownDescEmpty">
            <div class="tkt-sec-t">설명</div>
            <div class="tkt-desc tkt-desc-box"><p class="muted">설명이 없습니다.</p></div>
          </div>
          <!-- 구분선(=== 제목 ===)으로 나뉜 영역을 각각 제목 달린 카드로. 구분선이 없으면 1개뿐 -->
          <template v-else v-for="(sec, i) in descSections" :key="i">
            <div class="tkt-sec-t">{{ sec.title || '설명' }}</div>
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

          <!-- 설명 아래 2분할: 첨부파일 | 관련문서(언급된 Confluence 문서) -->
          <div class="tkt-two">
            <div class="tkt-two-col">
              <div class="tkt-sec-t">첨부파일<span v-if="atts.length"> ({{ atts.length }})</span></div>
              <div v-if="!atts.length" class="muted mini">첨부파일 없음</div>
              <div v-else class="chipwrap">
                <a v-for="a in atts" :key="a.id" class="fchip" :href="a.url" target="_blank" rel="noopener"
                   :title="a.filename + ' · ' + fsize(a.size) + (a.author ? ' · ' + a.author : '')">
                  <span class="fchip-ic" :class="{ img: a.isImage }"></span>
                  <span class="fchip-n">{{ a.filename }}</span>
                  <span class="fchip-m">{{ fdt(a.created) }} · {{ fsize(a.size) }}</span>
                </a>
              </div>
            </div>
            <div class="tkt-two-col">
              <div class="tkt-sec-t">관련문서<span v-if="docs.length"> ({{ docs.length }})</span></div>
              <div v-if="!docs.length" class="muted mini">언급된 문서 없음</div>
              <div v-else class="chipwrap">
                <a v-for="(d, i) in docs" :key="i" class="fchip doc" :href="d.url" target="_blank"
                   rel="noopener" :title="d.url">
                  <span class="fchip-ic conf"></span>
                  <span class="fchip-n">{{ d.title }}</span>
                </a>
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
                  <div v-else class="tkt-cmt-b tkt-desc" @click="onContentClick" v-html="c.html"></div>
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

          <!-- 우측: 타임라인 -->
          <aside v-if="v || timeline.length" class="tkt-tl">
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
    </div>`,
};
