// TicketDialog.js — 인앱 티켓 상세 다이얼로그(모달). 티켓 링크(.tkt) 클릭 시 app-root 가 keyId 를 넘겨 연다.
// description 은 백엔드에서 **정화된 HTML**(app/htmlsafe.py) 이라 v-html 로 그대로 렌더(table/code/quote/panel/callout/img).
// 코멘트 텍스트는 Vue 기본 이스케이프({{ }})로 안전 표시. Esc/백드롭/X 로 닫기.
import { api } from "../../lib/api.js";
import { ymd, ymdhm, esc } from "../../lib/fmt.js";
import { TYPE_BG } from "../../lib/colors.js";
import TypeBadge from "./TypeBadge.js";

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

export default {
  name: "TicketDialog",
  components: { TypeBadge },
  props: { keyId: { type: String, required: true } },
  emits: ["close"],
  data() { return { v: null, comments: null, ancestors: [], descendants: null,
                    err: "", loading: true, expanded: false, zoom: null, zoomLoading: false }; },
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
    // 자손 총 개수(직계 + 하위 sub-task) — "하위 (N)" 헤더용
    descCount() {
      return (this.descendants || []).reduce(
        (n, x) => n + 1 + ((x.children && x.children.length) || 0), 0);
    },
    // 계보 카드 스택 = [조상…, 현재]. dist = 현재로부터 거리(0=현재=맨 앞/메인, 클수록 위·뒤로 밀림).
    lineage() {
      if (!this.v) return [];
      const cur = { key: this.v.key, summary: this.v.summary, type: this.v.type, main: true };
      const arr = (this.ancestors || []).map((a) => ({ ...a, main: false })).concat(cur);
      const L = arr.length;
      return arr.map((n, i) => ({ ...n, dist: L - 1 - i }));
    },
  },
  watch: {
    keyId() { this.load(); },
    v() { this.$nextTick(this.augment); },            // 설명 렌더 후 확대버튼·뱃지 주입
    comments() { this.$nextTick(this.augment); },     // 코멘트 렌더 후
  },
  methods: {
    async load() {
      this.loading = true; this.err = ""; this.v = null; this.comments = null;
      this.ancestors = []; this.descendants = null;
      try {
        this.v = await api.ticket(this.keyId);
        // 조상(상위 epic/parent)·자손(하위 sub-task 등)·코멘트는 병렬 lazy 로드 (전부 티켓단위 캐시)
        api.ticketComments(this.keyId).then((c) => { this.comments = c; }).catch(() => { this.comments = []; });
        api.ticketAncestors(this.keyId).then((a) => { this.ancestors = a || []; }).catch(() => { this.ancestors = []; });
        api.ticketDescendants(this.keyId).then((d) => { this.descendants = d || []; }).catch(() => { this.descendants = []; });
      } catch (e) {
        this.err = e && e.message === "HTTP 404" ? "티켓을 찾을 수 없습니다: " + this.keyId : (e.message || "불러오기 실패");
      } finally { this.loading = false; }
    },
    fy(s) { return ymd(s); },
    fdt(s) { return ymdhm(s); },
    statusClass(cat) { return "st-" + (cat || "todo"); },
    // 계보 카드 색/타입 클래스 — Epic=회색사선, Sub-Task=기본(플레인), 그 외=타입색.
    linClass(n) {
      return { "tkt-lin-main": !!n.main, "tkt-lin-epic": n.type === "Epic",
               "tkt-lin-sub": n.type === "Sub-Task" };
    },
    // 역피라미드 — 위(조상)가 넓고 아래(현재)로 갈수록 가운데로 좁아진다. z: 현재(dist=0)가 앞.
    //   Epic 은 여백 없이 다이얼로그 상단 풀블리드(머리띠) → 인라인 마진 없이 CSS(.tkt-lin-epic)가 처리.
    linStyle(n) {
      const s = { zIndex: 100 - n.dist };
      if (n.type === "Epic") return s;
      const step = 20, top = this.lineage.length - 1;
      const inset = (top - n.dist) * step;            // 위로 갈수록 넓게, 아래로 갈수록 좁게(가운데)
      s.marginLeft = inset + "px"; s.marginRight = inset + "px";
      s["--acc"] = n.type === "Sub-Task" ? "var(--border)" : (TYPE_BG[n.type] || "#3568c4");
      return s;
    },
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

        <div v-if="loading" class="tkt-load"><span class="spinner"></span> 불러오는 중…</div>
        <div v-else-if="err" class="tkt-err">{{ err }}</div>

        <template v-else-if="v">
          <!-- 계보 카드 스택 — 위에서부터 Epic → Task → …, 맨 아래(앞)가 현재 티켓(메인).
               Epic=시그니처색(없으면 회색 사선) / Task류=타입색 / Sub-Task=기본. 조상 카드 클릭 시 이동. -->
          <div class="tkt-lineage">
            <template v-for="n in lineage" :key="n.key">
              <a v-if="!n.main" class="tkt-lin tkt" :class="linClass(n)" :style="linStyle(n)"
                 :data-key="n.key" :title="n.type + ' ' + n.key + ' · ' + n.summary">
                <TypeBadge :type="n.type" />
                <span class="tkt-lin-key">{{ n.key }}</span>
                <span class="tkt-lin-sum">{{ n.summary }}</span>
              </a>
              <div v-else class="tkt-lin tkt-lin-main" :class="linClass(n)" :style="linStyle(n)">
                <div class="tkt-lin-top">
                  <TypeBadge :type="v.type" />
                  <span class="tkt-lin-key">{{ v.key }}</span>
                  <span class="tkt-status" :class="statusClass(v.statusCategory)">{{ v.status }}</span>
                  <a v-if="v.url" class="tkt-ext" :href="v.url" target="_blank" rel="noopener">Jira에서 열기 ↗</a>
                </div>
                <h2 class="tkt-summary">{{ v.summary }}</h2>
              </div>
            </template>
          </div>

          <div class="tkt-meta">
            <div><span class="k">담당자</span><span class="val">{{ v.assignee || '—' }}</span></div>
            <div><span class="k">보고자</span><span class="val">{{ v.reporter || '—' }}</span></div>
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

          <div class="tkt-sec-t">설명</div>
          <div class="tkt-desc tkt-desc-box" @click="onContentClick" v-html="v.descriptionHtml || '<p class=&quot;muted&quot;>설명이 없습니다.</p>'"></div>

          <!-- 하위 자손(Epic→자식+Sub-Task 2단계 / Task→Sub-Task). 클릭 시 해당 티켓으로 이동 -->
          <template v-if="descendants && descendants.length">
            <div class="tkt-sec-t">하위 ({{ descCount }})</div>
            <div class="tkt-children">
              <div v-for="n in descendants" :key="n.key" class="tkt-child-group">
                <a class="tkt-child tkt" :data-key="n.key" :title="n.type + ' ' + n.key + ' · ' + n.summary">
                  <span class="tkt-child-dot" :class="'st-' + (n.statusCat || 'todo')"></span>
                  <span class="tkt-child-type">{{ n.type }}</span>
                  <b class="tkt-child-key">{{ n.key }}</b>
                  <span class="tkt-child-sum">{{ n.summary }}</span>
                </a>
                <div v-if="n.children && n.children.length" class="tkt-child-subs">
                  <a v-for="s in n.children" :key="s.key" class="tkt-child sub tkt" :data-key="s.key"
                     :title="s.type + ' ' + s.key + ' · ' + s.summary">
                    <span class="tkt-child-dot" :class="'st-' + (s.statusCat || 'todo')"></span>
                    <span class="tkt-child-type">{{ s.type }}</span>
                    <b class="tkt-child-key">{{ s.key }}</b>
                    <span class="tkt-child-sum">{{ s.summary }}</span>
                  </a>
                </div>
              </div>
            </div>
          </template>

          <div class="tkt-sec-t">코멘트<span v-if="comments"> ({{ comments.length }})</span></div>
          <div v-if="!comments" class="loading">코멘트 불러오는 중…</div>
          <div v-else-if="!comments.length" class="muted">코멘트가 없습니다.</div>
          <div v-else class="tkt-comments" @click="onContentClick">
            <div v-for="(c, i) in comments" :key="i" class="tkt-cmt">
              <div class="tkt-cmt-h"><b>{{ c.author }}</b><span class="muted">{{ fdt(c.date) }}</span></div>
              <div class="tkt-cmt-b tkt-desc" v-html="c.html"></div>
            </div>
          </div>
        </template>
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
