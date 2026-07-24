// LinkPicker.js — 티켓에 '관련 티켓' / '관련문서'를 붙이는 검색 팝오버.
//
//  mode="jira"       : 티켓 번호(DL-1234)나 제목으로 검색 → 대상 선택 + **관계 선택**(Jira 링크 타입)
//  mode="confluence" : 문서 제목으로 검색하거나 **URL 을 그대로 붙여넣어** 특정
//
// 둘 다 Jira 의 링크 기능이다(전자=issue link, 후자=remote link). 저장은 부모가 emit 을 받아
// api.linkAdd / api.documentAdd 로 처리한다 — 이 컴포넌트는 '무엇을 붙일지' 고르는 역할만 한다.
import { api } from "../../lib/api.js";
import { createTypeahead } from "../../lib/typeahead.js";
import TypeBadge from "./TypeBadge.js";

const _URL_RE = /^https?:\/\/\S+$/i;

export default {
  name: "LinkPicker",
  components: { TypeBadge },
  props: {
    mode: { type: String, default: "jira" },        // jira | confluence
    excludeKeys: { type: Array, default: () => [] },  // 이미 걸린 티켓(중복 방지)
    busy: Boolean,
    // 글 안에 **넣기만** 할 때(에디터 '/jira' '/confluence'). 링크 '관계'를 묻지 않는다 —
    // 관계는 티켓끼리의 issue link 개념이라 문장 속 참조에는 의미가 없다.
    insert: Boolean,
    err: { type: String, default: "" },
  },
  emits: ["close", "pick"],
  data() {
    return { q: "", items: [], loading: false, serr: "", active: -1,
             recent: [],                      // 검색어 없을 때의 후보(최근 조회)
             types: [], type: "Relates", direction: "outward", title: "" };
  },
  computed: {
    isJira() { return this.mode === "jira"; },
    // 검색어가 없을 때 보여줄 후보 = 최근 조회한 티켓/문서.
    // 검색 결과와 **같은 모양**으로 맞춰 둔다 — 그래야 ↑↓/Enter·선택 코드를 그대로 쓴다.
    recentItems() {
      const skip = new Set((this.excludeKeys || []).map((k) => String(k).toUpperCase()));
      const seen = new Set();          // 예전에 다른 URL 형태로 저장된 같은 항목 방어
      const out = [];
      for (const r of this.recent) {
        if (this.isJira) {
          const m = /\/browse\/([A-Za-z][A-Za-z0-9]*-\d+)/.exec(r.url || "");
          if (!m) continue;
          const key = m[1].toUpperCase();
          if (skip.has(key) || seen.has(key)) continue;
          seen.add(key);
          // 저장된 제목은 "KEY 요약" 형태 — 앞의 키를 떼어 목록의 다른 행과 모양을 맞춘다
          const title = String(r.title || "").replace(new RegExp("^" + key + "\s*"), "");
          out.push({ key, title: title || r.title || key, status: r.meta || "",
                     issuetype: r.type || "", _recent: true });
        } else {
          if (!r.url || r.kind === "jira") continue;      // 문서/웹 링크만
          const u = r.url.replace(/\/+$/, "").toLowerCase();
          if (seen.has(u)) continue;
          seen.add(u);
          out.push({ url: r.url, title: r.title || r.url, path: [], _recent: true });
        }
      }
      return out.slice(0, 12);
    },
    // 지금 목록에 그릴 것 — 검색어가 있으면 검색 결과, 없으면 최근 조회
    shown() { return this.q.trim() ? this.items : this.recentItems; },
    showingRecent() { return !this.q.trim() && !this.rawUrl && this.recentItems.length > 0; },
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
  created() {
    this._ta = createTypeahead(
      (q) => api.search(q, "all", this.isJira ? "jira" : "confluence")
               .catch((e) => ({ error: e.message || "검색 실패" })),
      { minLen: 2, cacheMs: 12000, emptyValue: { jira: { items: [] }, confluence: { items: [] } } });
  },
  mounted() {
    this.$nextTick(() => { const el = this.$refs.input; if (el) el.focus(); });
    document.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); this.$emit("close"); }
    }, true);
    // 최근 조회 목록(서버 저장 — 브라우저가 달라도 같다). 검색어가 없을 때의 후보로 쓴다.
    api.recent(20).then((r) => {
      this.recent = r || [];
      if (!this.q.trim() && this.recentItems.length) this.active = 0;
    }).catch(() => { /* 없으면 그냥 안내문만 */ });
    if (this.isJira) {
      api.linkTypes().then((t) => {
        this.types = t || [];
        if (this.typeOpts.length) { this.type = this.typeOpts[0].name; this.direction = this.typeOpts[0].direction; }
      }).catch(() => { /* 폴백은 서버가 준다 */ });
    }
  },
  unmounted() {
    document.removeEventListener("keydown", this._onEsc, true);
    this._ta.cancel();
  },
  watch: {
    q() { this.run(); },
  },
  methods: {
    setRel(e) {
      const o = this.typeOpts[+e.target.value];
      if (o) { this.type = o.name; this.direction = o.direction; }
    },
    run() {
      const q = this.q.trim();
      if (!q || this.rawUrl) {
        this._ta.cancel(); this.items = []; this.loading = false;
        this.active = this.recentItems.length ? 0 : -1;   // 최근 목록으로 돌아가면 첫 항목 선택
        return;
      }
      this.loading = true; this.serr = "";
      this._ta.run(q).then((r) => {
        if (r === null) return;                 // 낡은 응답 — 버린다
        this.loading = false;
        if (r && r.error) { this.serr = r.error; this.items = []; return; }
        const src = (this.isJira ? (r || {}).jira : (r || {}).confluence) || {};
        const skip = new Set((this.excludeKeys || []).map((k) => String(k).toUpperCase()));
        this.items = (src.items || []).filter((it) => !this.isJira || !skip.has((it.key || "").toUpperCase()));
        this.serr = src.error || "";
        this.active = this.items.length ? 0 : -1;
      });
    },
    onKey(e) {
      if (e.key === "ArrowDown") { e.preventDefault(); this.move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.move(-1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (this.rawUrl) { this.pickUrl(); return; }
        const it = this.shown[this.active];
        if (it) this.choose(it);
      }
    },
    move(d) { const n = this.shown.length; if (n) this.active = (this.active + d + n) % n; },
    choose(it) {
      if (this.insert) {
        // 넣는 쪽은 무엇을 넣을지만 알면 된다 — 키·제목·주소.
        this.$emit("pick", { key: it.key || "", url: it.url || "", title: this.plain(it.title) });
      } else if (this.isJira) {
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
  <!-- 오버레이 모달 — 인라인으로 열면 폭이 좁고 아래 내용을 밀어낸다.
       body 로 teleport: 티켓 다이얼로그 안에 두면 그 스택/스크롤에 갇힌다. -->
  <Teleport to="body">
  <div class="lp-ov" @click.self="$emit('close')">
  <div class="lp" @click.stop>
    <div class="lp-h">{{ insert ? (isJira ? '티켓 넣기' : '문서 넣기')
                                : (isJira ? '관련 티켓 추가' : '관련문서 추가') }}
      <span class="lp-h-s">{{ insert ? '고른 것이 글 안에 링크로 들어갑니다'
                                     : (isJira ? 'Jira 이슈 링크' : 'Confluence 문서 · 웹 링크') }}</span>
    </div>
    <div class="lp-top">
      <input ref="input" v-model="q" class="lp-input" @keydown="onKey" autocomplete="off"
             :placeholder="isJira ? '티켓 번호(DL-1234) 또는 제목으로 검색…' : '문서 제목으로 검색하거나 URL 붙여넣기…'" />
      <span v-if="loading || busy" class="spinner"></span>
      <button class="lp-x" @click="$emit('close')" aria-label="닫기">✕</button>
    </div>

    <!-- 관련 티켓: 관계(링크 타입 + 방향)를 먼저 정한다 -->
    <div v-if="isJira && !insert" class="lp-rel">
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
      <div v-if="showingRecent" class="lp-sec">최근 조회한 {{ isJira ? '티켓' : '문서' }}</div>
      <div v-else-if="!q.trim()" class="lp-hint">
        {{ isJira ? '번호나 제목 일부를 입력하세요.' : '제목 일부를 입력하거나 문서 URL 을 붙여넣으세요.' }}
      </div>
      <div v-else-if="!shown.length && !loading" class="lp-hint">결과 없음</div>
      <div v-for="(it, i) in shown" :key="isJira ? it.key : ('c'+i)" class="lp-item"
           :class="{ active: active === i }" @mousemove="active = i" @click="choose(it)">
        <!-- [타입] [번호] [제목] - [상태] — 타입은 뱃지로 -->
        <template v-if="isJira">
          <TypeBadge v-if="it.issuetype" :type="it.issuetype" />
          <span v-else class="sr-dot" :class="it._recent ? 'rc' : 'st-' + (it.statusCategory || 'todo')"></span>
          <b class="sr-key">{{ it.key }}</b>
          <span class="sr-title">{{ it.title }}</span>
          <span v-if="it.status" class="lp-st" :class="'st-' + (it.statusCategory || '')">- {{ it.status }}</span>
        </template>
        <template v-else>
          <span class="sr-pageic"></span>
          <span class="sr-title" v-html="it.title"></span>
          <span class="sr-meta">{{ (it.path || []).slice().reverse().join(' ‹ ') }}</span>
        </template>
      </div>
    </div>
  </div>
  </div>
  </Teleport>`,
};
