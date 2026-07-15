// TicketDialog.js — 인앱 티켓 상세 다이얼로그(모달). 티켓 링크(.tkt) 클릭 시 app-root 가 keyId 를 넘겨 연다.
// description 은 백엔드에서 **정화된 HTML**(app/htmlsafe.py) 이라 v-html 로 그대로 렌더(table/code/quote/panel/callout/img).
// 코멘트 텍스트는 Vue 기본 이스케이프({{ }})로 안전 표시. Esc/백드롭/X 로 닫기.
import { api } from "../../lib/api.js";
import { ymd, ymdhm } from "../../lib/fmt.js";
import TypeBadge from "./TypeBadge.js";

export default {
  name: "TicketDialog",
  components: { TypeBadge },
  props: { keyId: { type: String, required: true } },
  emits: ["close"],
  data() { return { v: null, comments: null, err: "", loading: true, expanded: false, zoom: null, zoomLoading: false }; },
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
  watch: {
    keyId() { this.load(); },
    v() { this.$nextTick(this.augmentZoomables); },            // 설명 렌더 후 확대버튼 주입
    comments() { this.$nextTick(this.augmentZoomables); },     // 코멘트 렌더 후
  },
  methods: {
    async load() {
      this.loading = true; this.err = ""; this.v = null; this.comments = null;
      try {
        this.v = await api.ticket(this.keyId);
        api.ticketComments(this.keyId).then((c) => { this.comments = c; }).catch(() => { this.comments = []; });
      } catch (e) {
        this.err = e && e.message === "HTTP 404" ? "티켓을 찾을 수 없습니다: " + this.keyId : (e.message || "불러오기 실패");
      } finally { this.loading = false; }
    },
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
          <div class="tkt-head">
            <TypeBadge :type="v.type" />
            <span class="tkt-key">{{ v.key }}</span>
            <span class="tkt-status" :class="statusClass(v.statusCategory)">{{ v.status }}</span>
            <a v-if="v.url" class="tkt-ext" :href="v.url" target="_blank" rel="noopener">Jira에서 열기 ↗</a>
          </div>

          <h2 class="tkt-summary">{{ v.summary }}</h2>

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
