// TicketDialog.js — 인앱 티켓 상세 다이얼로그(모달). 티켓 링크(.tkt) 클릭 시 app-root 가 keyId 를 넘겨 연다.
// description 은 백엔드에서 **정화된 HTML**(app/htmlsafe.py) 이라 v-html 로 그대로 렌더(table/code/quote/panel/callout/img).
// 코멘트 텍스트는 Vue 기본 이스케이프({{ }})로 안전 표시. Esc/백드롭/X 로 닫기.
import { api } from "../../lib/api.js";
import { ymd, ymdhm, esc } from "../../lib/fmt.js";
import { TYPE_BG } from "../../lib/colors.js";
import TypeBadge from "./TypeBadge.js";
import Avatar from "./Avatar.js";

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
  components: { TypeBadge, Avatar },
  props: { keyId: { type: String, required: true } },
  emits: ["close"],
  data() { return { v: null, comments: null, ancestors: [], siblings: [], timeline: [], children: [], related: [], sibOpen: true,
                    pdesc: null, pdescOpen: false, pdescErr: "",
                    err: "", expanded: false, zoom: null, zoomLoading: false }; },
  mounted() {
    // Esc: 확대(zoom)가 열려 있으면 그것부터 닫고, 아니면 다이얼로그 닫기
    this._onKey = (e) => {
      if (e.key !== "Escape") return;
      if (this.zoom) { this.zoom = null; } else { this.$emit("close"); }
    };
    window.addEventListener("keydown", this._onKey);
    this.load();
  },
  unmounted() { window.removeEventListener("keydown", this._onKey); },
  computed: {
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
    ownDescEmpty() { return descEmpty(this.v && this.v.descriptionHtml); },
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
      this.children = []; this.related = [];
      this.pdesc = null; this.pdescOpen = false; this.pdescErr = "";

      api.ticketComments(key).then((c) => { if (fresh()) this.comments = c; })
        .catch(() => { if (fresh()) this.comments = []; });
      api.ticketAncestors(key).then((a) => { if (fresh()) this.ancestors = a || []; }).catch(() => {});
      api.ticketSiblings(key).then((s) => { if (fresh()) this.siblings = s || []; }).catch(() => {});
      api.ticketTimeline(key).then((t) => { if (fresh()) this.timeline = t || []; }).catch(() => {});
      api.ticketChildren(key).then((c) => { if (fresh()) this.children = c || []; }).catch(() => {});
      api.ticketRelated(key).then((r) => { if (fresh()) this.related = r || []; }).catch(() => {});

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
    descEmpty(html) { return descEmpty(html); },
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
    tlText(e) {
      const f = e.from || "없음", t = e.to || "없음";
      const kind = (e.kind || "").replace(/^child-/, "");   // 자손 이벤트도 같은 문구 사용
      if (kind === "created") return "티켓 생성";
      if (kind === "comment") return "댓글 작성";
      if (kind === "status") return "상태 " + f + " → " + t;
      if (kind === "assignee") return "담당자 " + f + " → " + t;
      if (kind === "resolution") return e.to ? ("해결: " + e.to) : "해결 취소";
      if (kind === "duedate") return "마감일 " + f + " → " + t;
      if (kind === "priority") return "우선순위 " + f + " → " + t;
      return (e.field || "변경") + " " + f + " → " + t;
    },
    // 타 모듈 형제 = 흐리게(숨기지는 않는다 — 존재는 알리고 노이즈만 줄임)
    isOther(s) { return !!(this.myComp && s.component && s.component !== this.myComp); },
    fy(s) { return ymd(s); },
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
    augment() { this.augmentZoomables(); this.augmentLinks(); },
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
          const meta = [b.status, b.assignee].filter(Boolean).join(" · ");
          a.querySelector(".jb-meta").textContent = meta;
          a.title = key + " " + (b.summary || "") + (meta ? " (" + meta + ")" : "");
        }).catch(() => { /* 조회 실패 시 키만 표시 */ });
      });
    },
    // v-html 로 렌더된 이미지/표에 '확대' 버튼을 얹는다(우측 상단). 중복 주입 방지 마커 사용.
    augmentZoomables() {
      const root = this.$el;
      if (!root || !root.querySelectorAll) return;
      root.querySelectorAll(".tkt-desc img, .tkt-desc table").forEach((el) => {
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
    <div class="tkt-ov" :class="{ expanded }" @click.self="$emit('close')">
      <div class="tkt-dlg" :class="{ expanded }" role="dialog" aria-modal="true">
        <button class="tkt-max" @click="expanded = !expanded"
                :aria-label="expanded ? '축소' : '확장'" :title="expanded ? '축소' : '확장'">
          <svg v-if="!expanded" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3"/></svg>
          <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3a2 2 0 0 0 2-2V3M20 8h-3a2 2 0 0 1-2-2V3M4 16h3a2 2 0 0 1 2 2v3M20 16h-3a2 2 0 0 0-2 2v3"/></svg>
        </button>
        <button class="tkt-x" @click="$emit('close')" aria-label="닫기">✕</button>

        <!-- 섹션별 독립 렌더: 스파인(계보/형제/타임라인)은 본문(v) 응답을 기다리지 않는다.
             본문·코멘트도 각자 자기 상태가 채워지는 대로 그려진다. -->
        <div class="tkt-cols">
          <!-- 좌측 세로 스파인 — 계보(조상→현재, 레일+진척) + 형제 목록. 클릭 시 해당 티켓으로 이동 -->
          <aside v-if="spine.length > 1 || siblings.length || timeline.length" class="tkt-spine">
            <!-- 조상이 없으면(Epic 등) 자기 자신만 남으므로 계보 블록 자체를 생략 -->
            <template v-if="spine.length > 1">
            <div class="tkt-mlabel">계보</div>
            <div v-for="(n, i) in spine" :key="n.key" class="spn-item">
              <div class="spn-rail">
                <span class="spn-dot" :class="{ on: n.current }" :style="{ '--tc': typeColor(n.type) }"></span>
                <span v-if="i < spine.length - 1" class="spn-line"></span>
              </div>
              <div class="spn-body" :class="{ cur: n.current, tkt: !n.current }"
                   :data-key="n.current ? null : n.key"
                   :title="n.type + ' ' + n.key + ' · ' + n.summary">
                <div class="spn-top"><TypeBadge :type="n.type" /><span class="spn-key">{{ n.key }}</span></div>
                <div class="spn-title">{{ n.summary }}</div>
                <div v-if="n.pct !== null && n.pct !== undefined" class="spn-prog">
                  <span class="spn-bar"><i :style="{ width: n.pct + '%', background: typeColor(n.type) }"></i></span>
                  <span class="spn-pct">{{ n.pct }}%</span>
                </div>
              </div>
            </div>
            </template>

            <div v-if="siblings.length" class="spn-sib">
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

            <div v-if="children.length" class="spn-sib">
              <div class="tkt-mlabel">하위 Task {{ children.length }}</div>
              <div v-for="c in children" :key="'ch-' + c.key" class="spn-sibrow tkt"
                   :data-key="c.key" :title="c.type + ' ' + c.key + ' · ' + c.summary">
                <span class="spn-sdot" :class="'st-' + (c.statusCategory || 'todo')"></span>
                <span class="spn-stitle">{{ c.summary }}</span>
              </div>
            </div>

            <div v-if="related.length" class="spn-sib">
              <div class="tkt-mlabel">관련 Task {{ related.length }}</div>
              <div v-for="r in related" :key="'rel-' + r.key" class="spn-sibrow tkt"
                   :data-key="r.key" :title="r.rel + ' · ' + r.key + ' · ' + r.summary">
                <span class="spn-sdot" :class="'st-' + (r.statusCategory || 'todo')"></span>
                <span class="spn-stitle">{{ r.summary }}</span>
                <span class="spn-rel" :class="r.via">{{ r.via === 'link' ? r.rel : '언급' }}</span>
              </div>
            </div>

          </aside>

          <div class="tkt-main">
          <div v-if="err" class="tkt-err">{{ err }}</div>
          <div v-else-if="!v" class="tkt-load"><span class="spinner"></span> 불러오는 중…</div>

          <template v-else>
          <div class="tkt-head">
            <TypeBadge :type="v.type" />
            <span class="tkt-key">{{ v.key }}</span>
            <span class="tkt-status" :class="statusClass(v.statusCategory)">{{ v.status }}</span>
            <a v-if="v.url" class="tkt-ext" :href="v.url" target="_blank" rel="noopener">Jira에서 열기 ↗</a>
          </div>

          <h2 class="tkt-summary">{{ v.summary }}</h2>

          <div class="tkt-meta">
            <div><span class="k">담당자</span><span class="val val-user">
              <Avatar v-if="v.assigneeId" :user="v.assigneeId" :name="v.assignee" :size="18" />{{ v.assignee || '—' }}</span></div>
            <div><span class="k">보고자</span><span class="val val-user">
              <Avatar v-if="v.reporterId" :user="v.reporterId" :name="v.reporter" :size="18" />{{ v.reporter || '—' }}</span></div>
            <div><span class="k">우선순위</span><span class="val">{{ v.priority || '—' }}</span></div>
            <div><span class="k">생성</span><span class="val">{{ fdt(v.created) || '—' }}</span></div>
            <div><span class="k">수정</span><span class="val">{{ fdt(v.updated) || '—' }}</span></div>
            <div><span class="k">마감</span><span class="val">{{ fy(v.due) || '—' }}</span></div>
            <div v-if="v.resolved"><span class="k">완료</span><span class="val">{{ fdt(v.resolved) }}</span></div>
            <div v-if="v.components && v.components.length"><span class="k">컴포넌트</span><span class="val">{{ v.components.join(', ') }}</span></div>
          </div>

          <div v-if="v.labels && v.labels.length" class="tkt-labels">
            <span v-for="l in v.labels" :key="l" class="tkt-label">{{ l }}</span>
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

          <div class="tkt-sec-t">설명</div>
          <div v-if="ownDescEmpty" class="tkt-desc tkt-desc-box"><p class="muted">설명이 없습니다.</p></div>
          <div v-else class="tkt-desc tkt-desc-box" @click="onContentClick" v-html="v.descriptionHtml"></div>

          </template><!-- /본문(v) -->

          <!-- 코멘트 — 본문(v)과 무관하게 자기 상태로 렌더 -->
          <template v-if="!err">
            <div class="tkt-sec-t">코멘트<span v-if="comments"> ({{ comments.length }})</span></div>
            <div v-if="!comments" class="loading">코멘트 불러오는 중…</div>
            <div v-else-if="!comments.length" class="muted">코멘트가 없습니다.</div>
            <div v-else class="tkt-comments" @click="onContentClick">
              <div v-for="(c, i) in comments" :key="i" class="tkt-cmt">
                <div class="tkt-cmt-h"><Avatar :user="c.authorId" :name="c.author" :size="20" /><b>{{ c.author }}</b><span class="muted">{{ fdt(c.date) }}</span></div>
                <div class="tkt-cmt-b tkt-desc" v-html="c.html"></div>
              </div>
            </div>
          </template>
          </div><!-- /.tkt-main -->

          <!-- 우측: 타임라인 -->
          <aside v-if="timeline.length" class="tkt-tl">
            <div class="tkt-mlabel">타임라인</div>
              <div v-for="(e, i) in timeline" :key="i" class="tl-row"
                   :class="{ child: e.srcKey, tkt: !!e.srcKey }" :data-key="e.srcKey || null"
                   :title="(e.srcKey ? e.srcKey + ' · ' : '') + tlText(e)">
                <span class="tl-rail">
                  <span class="tl-dot" :class="'k-' + e.kind"></span>
                  <span v-if="i < timeline.length - 1" class="tl-line"></span>
                </span>
                <span class="tl-body">
                  <span class="tl-t"><span v-if="e.srcKey" class="tl-src">{{ e.srcKey }}</span>{{ tlText(e) }}</span>
                  <span class="tl-m">{{ e.author || '—' }} · {{ fdt(e.date) }}</span>
                </span>
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
