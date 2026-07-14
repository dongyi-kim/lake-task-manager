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
  data() { return { v: null, comments: null, err: "", loading: true, expanded: false }; },
  mounted() {
    this._onKey = (e) => { if (e.key === "Escape") this.$emit("close"); };
    window.addEventListener("keydown", this._onKey);
    this.load();
  },
  unmounted() { window.removeEventListener("keydown", this._onKey); },
  watch: { keyId() { this.load(); } },
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
          <div class="tkt-desc tkt-desc-box" v-html="v.descriptionHtml || '<p class=&quot;muted&quot;>설명이 없습니다.</p>'"></div>

          <div class="tkt-sec-t">코멘트<span v-if="comments"> ({{ comments.length }})</span></div>
          <div v-if="!comments" class="loading">코멘트 불러오는 중…</div>
          <div v-else-if="!comments.length" class="muted">코멘트가 없습니다.</div>
          <div v-else class="tkt-comments">
            <div v-for="(c, i) in comments" :key="i" class="tkt-cmt">
              <div class="tkt-cmt-h"><b>{{ c.author }}</b><span class="muted">{{ fdt(c.date) }}</span></div>
              <div class="tkt-cmt-b tkt-desc" v-html="c.html"></div>
            </div>
          </div>
        </template>
      </div>
    </div>`,
};
