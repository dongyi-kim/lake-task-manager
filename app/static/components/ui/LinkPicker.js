// LinkPicker.js — 티켓에 '관련 티켓' / '관련문서'를 붙이는 검색 팝오버.
//
//  mode="jira"       : 티켓 번호(DL-1234)나 제목으로 검색 → 대상 선택 + **관계 선택**(Jira 링크 타입)
//  mode="confluence" : 문서 제목으로 검색하거나 **URL 을 그대로 붙여넣어** 특정
//
// 둘 다 Jira 의 링크 기능이다(전자=issue link, 후자=remote link). 저장은 부모가 emit 을 받아
// api.linkAdd / api.documentAdd 로 처리한다 — 이 컴포넌트는 '무엇을 붙일지' 고르는 역할만 한다.
import { api } from "../../lib/api.js";

const _URL_RE = /^https?:\/\/\S+$/i;

export default {
  name: "LinkPicker",
  props: {
    mode: { type: String, default: "jira" },        // jira | confluence
    excludeKeys: { type: Array, default: () => [] },  // 이미 걸린 티켓(중복 방지)
    busy: Boolean,
    err: { type: String, default: "" },
  },
  emits: ["close", "pick"],
  data() {
    return { q: "", items: [], loading: false, serr: "", active: -1,
             types: [], type: "Relates", direction: "outward", title: "" };
  },
  computed: {
    isJira() { return this.mode === "jira"; },
    // 붙여넣은 URL 은 검색 없이 그대로 첨부할 수 있다(문서 제목은 서버가 og:title 로 채운다)
    rawUrl() { return !this.isJira && _URL_RE.test(this.q.trim()) ? this.q.trim() : ""; },
    typeOpts() {
      // 관계는 방향까지 골라야 의미가 정해진다(예: blocks ↔ is blocked by).
      const out = [];
      for (const t of this.types) {
        out.push({ label: t.outward, name: t.name, direction: "outward" });
        if (t.inward && t.inward !== t.outward) {
          out.push({ label: t.inward, name: t.name, direction: "inward" });
        }
      }
      return out;
    },
    relKey() { return this.type + "|" + this.direction; },
  },
  mounted() {
    this.$nextTick(() => { const el = this.$refs.input; if (el) el.focus(); });
    this._onDoc = (e) => { if (this.$el && !this.$el.contains(e.target)) this.$emit("close"); };
    // capture 로 달면 내부 클릭도 먼저 잡혀 닫힌다 → bubble 단계에서 문서 클릭만 본다
    setTimeout(() => document.addEventListener("click", this._onDoc), 0);
    document.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); this.$emit("close"); }
    }, true);
    if (this.isJira) {
      api.linkTypes().then((t) => {
        this.types = t || [];
        if (this.typeOpts.length) { this.type = this.typeOpts[0].name; this.direction = this.typeOpts[0].direction; }
      }).catch(() => { /* 폴백은 서버가 준다 */ });
    }
  },
  unmounted() {
    document.removeEventListener("click", this._onDoc);
    document.removeEventListener("keydown", this._onEsc, true);
    clearTimeout(this._t);
  },
  watch: {
    q() { clearTimeout(this._t); this._t = setTimeout(() => this.run(), 280); },
  },
  methods: {
    setRel(e) {
      const o = this.typeOpts[+e.target.value];
      if (o) { this.type = o.name; this.direction = o.direction; }
    },
    async run() {
      const q = this.q.trim();
      if (!q || this.rawUrl) { this.items = []; this.active = -1; return; }
      this.loading = true; this.serr = "";
      try {
        const r = await api.search(q, "all", this.isJira ? "jira" : "confluence");
        const src = this.isJira ? r.jira : r.confluence;
        const skip = new Set((this.excludeKeys || []).map((k) => String(k).toUpperCase()));
        this.items = (src.items || []).filter((it) => !this.isJira || !skip.has((it.key || "").toUpperCase()));
        this.serr = src.error || "";
        this.active = this.items.length ? 0 : -1;
      } catch (e) { this.serr = e.message || "검색 실패"; this.items = []; }
      finally { this.loading = false; }
    },
    onKey(e) {
      if (e.key === "ArrowDown") { e.preventDefault(); this.move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.move(-1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (this.rawUrl) { this.pickUrl(); return; }
        const it = this.items[this.active];
        if (it) this.choose(it);
      }
    },
    move(d) { const n = this.items.length; if (n) this.active = (this.active + d + n) % n; },
    choose(it) {
      if (this.isJira) {
        this.$emit("pick", { key: it.key, type: this.type, direction: this.direction });
      } else {
        this.$emit("pick", { url: it.url, title: this.plain(it.title) });
      }
    },
    pickUrl() { this.$emit("pick", { url: this.rawUrl, title: this.title.trim() }); },
    // 검색 하이라이트(<mark>)가 섞인 제목 → 평문
    plain(s) { const d = document.createElement("div"); d.innerHTML = s || ""; return (d.textContent || "").trim(); },
  },
  template: `
  <div class="lp" @click.stop>
    <div class="lp-top">
      <input ref="input" v-model="q" class="lp-input" @keydown="onKey" autocomplete="off"
             :placeholder="isJira ? '티켓 번호(DL-1234) 또는 제목으로 검색…' : '문서 제목으로 검색하거나 URL 붙여넣기…'" />
      <span v-if="loading || busy" class="spinner"></span>
      <button class="lp-x" @click="$emit('close')" aria-label="닫기">✕</button>
    </div>

    <!-- 관련 티켓: 관계(링크 타입 + 방향)를 먼저 정한다 -->
    <div v-if="isJira" class="lp-rel">
      <span class="lp-rel-l">이 티켓이</span>
      <select class="lp-sel" @change="setRel">
        <option v-for="(o, i) in typeOpts" :key="i" :value="i"
                :selected="o.name === type && o.direction === direction">{{ o.label }}</option>
      </select>
    </div>

    <!-- 관련문서: URL 을 그대로 붙여넣은 경우 제목만 받고 바로 첨부 -->
    <div v-if="rawUrl" class="lp-url">
      <div class="lp-url-u" :title="rawUrl">{{ rawUrl }}</div>
      <input v-model="title" class="lp-input sm" placeholder="표시할 제목(비우면 문서 제목을 가져옵니다)"
             @keydown.enter.prevent="pickUrl" />
      <button class="lp-add" :disabled="busy" @click="pickUrl">이 URL 첨부</button>
    </div>

    <div v-if="err" class="lp-err">{{ err }}</div>
    <div v-else-if="serr" class="lp-err">{{ serr }}</div>

    <div v-if="!rawUrl" class="lp-list">
      <div v-if="!q.trim()" class="lp-hint">
        {{ isJira ? '번호나 제목 일부를 입력하세요.' : '제목 일부를 입력하거나 문서 URL 을 붙여넣으세요.' }}
      </div>
      <div v-else-if="!items.length && !loading" class="lp-hint">결과 없음</div>
      <div v-for="(it, i) in items" :key="isJira ? it.key : ('c'+i)" class="lp-item"
           :class="{ active: active === i }" @mousemove="active = i" @click="choose(it)">
        <template v-if="isJira">
          <span class="sr-dot" :class="'st-' + (it.statusCategory || 'todo')"></span>
          <b class="sr-key">{{ it.key }}</b>
          <span class="sr-title">{{ it.title }}</span>
          <span class="sr-meta">{{ it.status }}</span>
        </template>
        <template v-else>
          <span class="sr-pageic"></span>
          <span class="sr-title" v-html="it.title"></span>
          <span class="sr-meta">{{ (it.path || []).slice().reverse().join(' ‹ ') }}</span>
        </template>
      </div>
    </div>
  </div>`,
};
