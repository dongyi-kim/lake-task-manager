// SearchOverlay.js — 스포트라이트형 통합 검색(우상단). Jira+Confluence+Bitbucket(mock) 병렬.
// 입력 디바운스 → /api/search. Jira 결과는 인앱 티켓 다이얼로그로, Confluence 는 시스템 브라우저로.
// 스코프 토글(소속 한정 ↔ 전체), 키보드 ↑↓/Enter/Esc. updated: 2026-07-15
import { api } from "../../lib/api.js";
import { ymdhm } from "../../lib/fmt.js";

export default {
  name: "SearchOverlay",
  emits: ["close", "open-ticket"],
  data() {
    return { q: "", scope: "scoped", res: null, loading: false, err: "",
             active: -1, optsOpen: false };
  },
  mounted() {
    this._onKey = (e) => {
      if (e.key === "Escape") { this.$emit("close"); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); this.move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.move(-1); }
      else if (e.key === "Enter") { const f = this.flat[this.active]; if (f) this.pick(f); }
    };
  },
  // keep-alive 로 감싸 마지막 검색어·결과를 유지한다(창을 닫았다 열어도 그대로).
  // ★ 전역 키 리스너는 반드시 activated/deactivated 에서 붙이고 뗀다.
  //   mounted/unmounted 에 두면 **닫혀 있는 동안에도** 살아 있어서
  //   ArrowDown/Enter 가 보이지도 않는 목록을 조작하고 티켓을 열어버린다.
  activated() {
    window.addEventListener("keydown", this._onKey);
    // 이전 검색어를 선택 상태로 둔다 — 바로 새로 타이핑하면 덮어써지고, 그대로 두면 결과 유지
    this.$nextTick(() => { const el = this.$refs.input; if (el) { el.focus(); el.select(); } });
  },
  deactivated() { window.removeEventListener("keydown", this._onKey); clearTimeout(this._t); },
  unmounted() { window.removeEventListener("keydown", this._onKey); clearTimeout(this._t); },
  watch: {
    q() { this.schedule(); },
    scope() { if (this.q.trim()) this.run(); },
  },
  computed: {
    // 키보드 네비게이션용 평면 리스트 (jira → confluence; bitbucket 은 mock 이라 선택 제외)
    flat() {
      if (!this.res) return [];
      const j = (this.res.jira.items || []).map((x) => ({ src: "jira", it: x }));
      const c = (this.res.confluence.items || []).map((x) => ({ src: "confluence", it: x }));
      return j.concat(c);
    },
    scopeLabel() { return this.scope === "scoped" ? "소속 프로젝트/스페이스" : "전체"; },
  },
  methods: {
    schedule() { clearTimeout(this._t); this._t = setTimeout(() => this.run(), 280); },
    async run() {
      const q = this.q.trim();
      if (!q) { this.res = null; this.err = ""; this.active = -1; return; }
      this.loading = true; this.err = "";
      try { this.res = await api.search(q, this.scope); this.active = this.flat.length ? 0 : -1; }
      catch (e) { this.err = e.message || "검색 실패"; this.res = null; }
      finally { this.loading = false; }
    },
    move(d) { const n = this.flat.length; if (!n) return; this.active = (this.active + d + n) % n; this.scrollActive(); },
    scrollActive() { this.$nextTick(() => { const el = this.$el.querySelector(".sr-item.active"); if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" }); }); },
    idx(src, i) { return this.flat.findIndex((f) => f.src === src && f.it === (this.res[src].items[i])); },
    pick(f) {
      if (f.src === "jira") { this.$emit("open-ticket", f.it.key); this.$emit("close"); return; }
      if (f.src === "confluence" && f.it.url) this.openExternal(f.it.url);
    },
    openExternal(url) {
      const a = document.createElement("a");
      a.href = url; a.target = "_blank"; a.rel = "noopener";
      document.body.appendChild(a); a.click(); a.remove();   // run.py 훅이 시스템 브라우저로
    },
    stCls(cat) { return "st-" + (cat || "todo"); },
    // 문서 경로를 breadcrumb 로. 데이터는 [스페이스 … 직계부모] 순 → **역순**으로 뒤집어
    // '직계부모 ‹ … ‹ 스페이스' 표기. 4단 초과면 양끝만 남기고 가운데를 '…' 로 접는다
    // (양 끝 = 가장 가까운 폴더와 스페이스, 둘 다 맥락상 제일 중요).
    confPath(path) {
      const rev = (path || []).slice().reverse();
      if (rev.length <= 4) return rev;
      return [rev[0], rev[1], "…", rev[rev.length - 1]];
    },
    fdt(s) { return ymdhm(s); },
    cnt(src) { return this.res && this.res[src] && this.res[src].items ? this.res[src].items.length : 0; },
  },
  template: `
  <div class="sr-ov" @click.self="$emit('close')">
    <div class="sr-box" role="dialog" aria-modal="true">
      <div class="sr-top">
        <svg class="sr-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
        <input ref="input" v-model="q" class="sr-input" placeholder="Jira · Confluence · Bitbucket 통합 검색…" autocomplete="off" />
        <span v-if="loading" class="spinner"></span>
        <button class="sr-x" @click="$emit('close')" aria-label="닫기">✕</button>
      </div>

      <div class="sr-opts">
        <button class="sr-optbtn" :class="{ open: optsOpen }" @click="optsOpen = !optsOpen">
          <span class="chev">▸</span> 검색 옵션 · <b>{{ scopeLabel }}</b>
        </button>
        <div v-if="optsOpen" class="sr-optbody">
          <div class="sr-seg">
            <button :class="{ on: scope === 'scoped' }" @click="scope = 'scoped'">소속 한정</button>
            <button :class="{ on: scope === 'all' }" @click="scope = 'all'">전체</button>
          </div>
          <span class="muted sr-hint">소속 한정: config 의 project/space 로 제한 · 전체: 인스턴스 전역</span>
        </div>
      </div>

      <div class="sr-results">
        <div v-if="err" class="sr-err">{{ err }}</div>
        <div v-else-if="!res && !loading" class="sr-empty">검색어를 입력하세요.</div>
        <template v-else-if="res">
          <!-- Jira -->
          <div class="sr-sec">
            <div class="sr-sec-h"><span class="sr-src jira">Jira</span> <b>{{ cnt('jira') }}</b><span v-if="res.jira.error" class="sr-serr">· {{ res.jira.error }}</span></div>
            <div v-for="(it, i) in res.jira.items" :key="it.key" class="sr-item" :class="{ active: flat[active] && flat[active].it === it }"
                 @click="pick({ src: 'jira', it })" @mousemove="active = idx('jira', i)">
              <span class="sr-dot" :class="stCls(it.statusCategory)"></span>
              <b class="sr-key">{{ it.key }}</b>
              <span class="sr-title">{{ it.title }}</span>
              <span class="sr-meta">{{ it.project }} · {{ it.status }}<template v-if="it.assignee"> · {{ it.assignee }}</template></span>
            </div>
            <div v-if="!cnt('jira') && !res.jira.error" class="sr-none">결과 없음</div>
          </div>
          <!-- Confluence -->
          <div class="sr-sec">
            <div class="sr-sec-h"><span class="sr-src conf">Confluence</span> <b>{{ cnt('confluence') }}</b><span v-if="res.confluence.error" class="sr-serr">· {{ res.confluence.error }}</span></div>
            <div v-for="(it, i) in res.confluence.items" :key="'c'+i" class="sr-item sr-item2 conf" :class="{ active: flat[active] && flat[active].it === it }"
                 @click="pick({ src: 'confluence', it })" @mousemove="active = idx('confluence', i)">
              <div class="sr-body">
                <div class="sr-r1">
                  <span class="sr-pageic"></span>
                  <span class="sr-title" v-html="it.title"></span>
                  <span class="sr-path" :title="(it.path || []).slice().reverse().join(' ‹ ')">
                    <template v-for="(seg, j) in confPath(it.path)" :key="j"
                      ><span v-if="j" class="sr-psep">‹</span><span class="sr-pseg" :class="{ ell: seg === '…' }">{{ seg }}</span
                    ></template>
                  </span>
                </div>
                <div v-if="it.excerpt" class="sr-r2" v-html="it.excerpt"></div>
              </div>
            </div>
            <div v-if="!cnt('confluence') && !res.confluence.error" class="sr-none">결과 없음</div>
          </div>
          <!-- Bitbucket (mock) -->
          <div class="sr-sec">
            <div class="sr-sec-h"><span class="sr-src bb">Bitbucket</span> <b>{{ cnt('bitbucket') }}</b> <span class="sr-mock">연동 예정(mock)</span></div>
            <div v-for="(it, i) in res.bitbucket.items" :key="'b'+i" class="sr-item mock">
              <span class="sr-repoic"></span>
              <span class="sr-title">{{ it.title }}</span>
              <span class="sr-excerpt">{{ it.repo }}<template v-if="it.path"> · {{ it.path }}</template></span>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>`,
};
