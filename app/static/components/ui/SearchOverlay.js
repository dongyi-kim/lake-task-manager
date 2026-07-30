// SearchOverlay.js — 스포트라이트형 통합 검색(우상단). Jira+Confluence+Bitbucket(mock) 병렬.
// 입력 디바운스 → /api/search. Jira 결과는 인앱 티켓 다이얼로그로, Confluence 는 시스템 브라우저로.
// 스코프 토글(소속 한정 ↔ 전체), 키보드 ↑↓/Enter/Esc. updated: 2026-07-15
import { api } from "../../lib/api.js";
import { ymdhm } from "../../lib/fmt.js";
import { recordOpen, stripTags } from "../../lib/recent.js";
import Avatar from "./Avatar.js";
import { createTypeahead } from "../../lib/typeahead.js";
import { fromBackdrop } from "../../lib/backdrop.js";
import { categoryColor } from "../../lib/colors.js";

export default {
  name: "SearchOverlay",
  components: { Avatar },
  emits: ["close", "open-ticket"],
  data() {
    // 소스별로 **따로** 채운다 — 셋을 다 기다리지 않고 오는 대로 보인다.
    // res 는 늘 세 칸을 갖고, loadingSrc 가 각 칸이 아직 오는 중인지 말한다.
    return { q: "", scope: "scoped", res: null, loading: false, err: "",
             loadingSrc: { jira: false, confluence: false, bitbucket: false },
             active: -1, optsOpen: false, recent: [] };
  },
  created() {
    // 소스마다 러너를 따로 둔다 — Jira 가 빨리 와도 Confluence 를 안 기다리고 먼저 그린다.
    // 빈/에러 결과는 캐시하지 않는다(순간 실패가 같은 검색어에 굳지 않게).
    const mk = (only) => createTypeahead(
      (q) => api.search(q, this.scope, only).then((r) => (r && r[only]) || { items: [] })
                .catch((e) => ({ error: e.message || "검색 실패" })),
      { minLen: 2, cacheMs: 12000, emptyValue: { items: [] },
        shouldCache: (r) => !!(r && !r.error && ((r.items || []).length > 0)) });
    this._src = { jira: mk("jira"), confluence: mk("confluence"), bitbucket: mk("bitbucket") };
  },
  mounted() {
    this._onKey = (e) => {
      if (e.key === "Escape") { this.$emit("close"); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); this.move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.move(-1); }
      else if (e.key === "Enter") {
        // 직접 고른 항목이 있으면 그걸 연다.
        if (this.active >= 0) { const f = this.flat[this.active]; if (f) this.pick(f); return; }
        // 고른 게 없으면 **검색어가 티켓 번호와 정확히 같을 때만** 바로 넘어간다
        // (DL-1234 처럼). 그 외에는 아무 일도 안 한다 — 무심코 누른 Enter 로 넘어가지 않게.
        const m = /^\s*([A-Za-z][A-Za-z0-9]*-\d+)\s*$/.exec(this.q || "");
        if (m) { e.preventDefault(); this.$emit("open-ticket", m[1].toUpperCase()); this.$emit("close"); }
      }
    };
  },
  // keep-alive 로 감싸 마지막 검색어·결과를 유지한다(창을 닫았다 열어도 그대로).
  // ★ 전역 키 리스너는 반드시 activated/deactivated 에서 붙이고 뗀다.
  //   mounted/unmounted 에 두면 **닫혀 있는 동안에도** 살아 있어서
  //   ArrowDown/Enter 가 보이지도 않는 목록을 조작하고 티켓을 열어버린다.
  activated() {
    window.addEventListener("keydown", this._onKey);
    this.loadRecent();                  // 열 때마다 갱신(다른 브라우저에서 연 것도 반영된다)
    // 이전 검색어를 선택 상태로 둔다 — 바로 새로 타이핑하면 덮어써지고, 그대로 두면 결과 유지
    this.$nextTick(() => { const el = this.$refs.input; if (el) { el.focus(); el.select(); } });
  },
  deactivated() { window.removeEventListener("keydown", this._onKey); Object.values(this._src).forEach((t) => t.cancel()); },
  unmounted() { window.removeEventListener("keydown", this._onKey); Object.values(this._src).forEach((t) => t.cancel()); },
  watch: {
    q() { this.schedule(); },
    scope() { if (this.q.trim()) this.run(); },
  },
  computed: {
    // 검색어가 없을 땐 '최근 열어본 항목'이 목록이다(키보드 이동도 여기에 걸린다).
    showRecent() { return !this.q.trim() && !this.res; },
    // 소스별 **표시용 뷰** — 결과에 새겨진 q 가 지금 입력한 검색어와 다르면 stale.
    // stale 이면 화면엔 흐리게(이전 결과) + 스피너로 두고, 키보드 선택 대상에선 뺀다
    // (오래된 결과를 새 검색어 결과인 양 고르거나 열지 않게). 새 결과가 오면 stale 이 풀린다.
    view() {
      const q = this.q.trim();
      const pick = (src) => {
        const r = this.res && this.res[src];
        if (!r) return { items: [], error: null, stale: false };
        return { items: r.items || [], error: r.error || null, stale: r.q !== q };
      };
      return { jira: pick("jira"), confluence: pick("confluence"), bitbucket: pick("bitbucket") };
    },
    // 키보드 네비게이션용 평면 리스트 (jira → confluence; bitbucket 은 mock 이라 선택 제외).
    // stale 소스는 제외 — 흐려진 이전 결과를 Enter 로 열어 버리지 않게.
    flat() {
      if (this.showRecent) return this.recent.map((x) => ({ src: "recent", it: x }));
      if (!this.res) return [];
      const j = this.view.jira.stale ? [] : (this.view.jira.items || []).map((x) => ({ src: "jira", it: x }));
      const c = this.view.confluence.stale ? [] : (this.view.confluence.items || []).map((x) => ({ src: "confluence", it: x }));
      return j.concat(c);
    },
    scopeLabel() { return this.scope === "scoped" ? "소속 프로젝트/스페이스" : "전체"; },
  },
  methods: {
    // 드래그가 창 밖에서 끝났을 뿐인데 닫히지 않게 — lib/backdrop.js 참고
    fromBackdrop,
    // 디바운스·응답 역전 방어·같은 질의 캐시는 typeahead 가 맡는다(대기 시간은 설정값).
    schedule() {
      const q = this.q.trim();
      if (!q) {
        Object.values(this._src).forEach((t) => t.cancel());
        this.res = null; this.err = ""; this.loading = false; this.active = -1;
        this.loadingSrc = { jira: false, confluence: false, bitbucket: false };
        return;
      }
      this.err = "";
      // 세 칸을 미리 만들어 두고(빈 목록), 오는 대로 갈아 끼운다 — 셋을 다 기다리지 않는다.
      if (!this.res) this.res = { jira: { items: [] }, confluence: { items: [] }, bitbucket: { items: [] } };
      this.active = -1;                          // 새 검색 — 자동 선택 없음(무심코 Enter 방지)
      this.loading = true;
      ["jira", "confluence", "bitbucket"].forEach((src) => {
        this.loadingSrc[src] = true;
        this._src[src].run(q).then((r) => {
          if (r === null) return;                // 낡은 응답 — 버린다
          this.loadingSrc[src] = false;
          this.loading = Object.values(this.loadingSrc).some(Boolean);
          // 한 소스가 실패해도 그 칸만 에러로 두고 나머지는 그대로 보인다.
          // ★ 결과에 **그 결과가 어느 검색어의 것인지**(q)를 새긴다 — 검색어가 이미 바뀌었으면
          //   view 가 이걸 stale 로 판정해 새 검색어 결과인 양 보여 주지 않는다(오래된 결과 방지).
          this.res[src] = r && r.error ? { items: [], error: r.error, q }
                                       : { items: (r && r.items) || [], error: null, q };
        });
      });
    },
    run() { this.schedule(); },                 // scope 변경 등 즉시 재조회도 같은 경로로
    move(d) {
      const n = this.flat.length; if (!n) return;
      // 아직 아무것도 안 골랐으면 첫 ↓ 는 맨 위, 첫 ↑ 는 맨 아래로.
      this.active = this.active < 0 ? (d > 0 ? 0 : n - 1) : (this.active + d + n) % n;
      this.scrollActive();
    },
    scrollActive() { this.$nextTick(() => { const el = this.$el.querySelector(".sr-item.active"); if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" }); }); },
    idx(src, i) { return this.flat.findIndex((f) => f.src === src && f.it === (this.view[src].items[i])); },
    async loadRecent() {
      let r = [];
      try { r = (await api.recent(30)) || []; } catch (e) { r = []; }
      // 예전에 다른 URL 형태(호스트 다름)로 저장된 같은 티켓/문서를 한 줄로 합친다
      const seen = new Set();
      this.recent = r.filter((x) => {
        const m = /\/browse\/([A-Za-z][A-Za-z0-9]*-\d+)/.exec(x.url || "");
        const k = m ? m[1].toUpperCase() : String(x.url || "").replace(/\/+$/, "").toLowerCase();
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      }).slice(0, 20);
      if (this.showRecent) this.active = -1;
    },
    // 최근 목록에서 지우기(잘못 열었던 항목 등). 서버 목록이라 다른 브라우저에서도 사라진다.
    async forget(it) {
      this.recent = this.recent.filter((x) => x.url !== it.url);
      try { await api.recentClear(it.url); } catch (e) { /* noop */ }
    },
    pick(f) {
      if (f.src === "recent") { this.openRecent(f.it); return; }
      if (f.src === "jira") {
        recordOpen({ url: f.it.url || ("/browse/" + f.it.key), kind: "jira",
                     title: f.it.key + " " + (f.it.title || ""), type: f.it.issuetype || "",
                     meta: f.it.status || "", data: this.jiraData(f.it) });
        this.$emit("open-ticket", f.it.key); this.$emit("close"); return;
      }
      if (f.src === "confluence" && f.it.url) {
        // title 은 검색 하이라이트(<mark>)가 섞인 HTML — 저장은 평문으로.
        recordOpen({ url: f.it.url, kind: "confluence", title: stripTags(f.it.title),
                     meta: (f.it.path || []).slice().reverse().join(" ‹ ") });
        this.openExternal(f.it.url);
      }
    },
    // 최근 항목 열기 — Jira 티켓이면 인앱 다이얼로그, 그 외는 시스템 브라우저.
    openRecent(it) {
      const m = /\/browse\/([A-Za-z][A-Za-z0-9]*-\d+)/.exec(it.url || "");
      // 다시 열었으니 맨 위로 — 저장돼 있던 표시필드(key·epicKey/Name·assignee·…)도 함께 재기록.
      recordOpen({ url: it.url, kind: it.kind, title: it.title, meta: it.meta, type: it.type,
                   data: it.kind === "jira" ? this.jiraData(it) : (it.data || {}) });
      if (m) { this.$emit("open-ticket", m[1].toUpperCase()); this.$emit("close"); return; }
      this.openExternal(it.url);
    },
    // 검색결과/최근항목 공통 — Jira 티켓의 표시용 부가필드만 추린다.
    jiraData(it) {
      return {
        key: it.key || null, summary: it.summary || it.title || "",
        epicKey: it.epicKey || null, epicName: it.epicName || null,
        assignee: it.assignee || null, assigneeId: it.assigneeId || null,
        status: it.status || null, statusCategory: it.statusCategory || null,
        project: it.project || null, issuetype: it.issuetype || it.type || null,
      };
    },
    epicColor(key) { return categoryColor(key); },
    recentIc(kind) { return kind === "jira" ? "sr-dot st-inprogress"
      : kind === "confluence" ? "sr-pageic" : "sr-webic"; },
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
    cnt(src) { return this.view && this.view[src] ? this.view[src].items.length : 0; },
  },
  template: `
  <div class="sr-ov" @click.self="fromBackdrop($event) && $emit('close')">
    <div class="sr-box" role="dialog" aria-modal="true">
      <div class="sr-top">
        <svg class="sr-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
        <!-- ★ v-model 대신 uncontrolled + @input. v-model 은 한글 IME **조합 중엔 값을 안 올려서**
             (조합 끝나야 반영) 타이핑을 마쳐도 검색이 안 걸리고 Enter(조합 확정)를 눌러야 했다.
             @input 은 조합 중에도 매 입력마다 e.target.value(조합 중 글자 포함)를 q 로 올린다.
             :value 를 안 걸어(uncontrolled) 조합 중 재바인딩이 없어 IME 커서도 안 깨진다. -->
        <input ref="input" @input="q = $event.target.value" @compositionupdate="q = $event.target.value"
               class="sr-input" placeholder="Jira · Confluence · Bitbucket 통합 검색…" autocomplete="off" />
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
        <!-- 검색어가 없을 때 — 최근 열어본 항목(서버 저장이라 브라우저가 달라도 같다) -->
        <template v-else-if="showRecent && !loading">
          <div v-if="!recent.length" class="sr-empty">검색어를 입력하세요.</div>
          <div v-else class="sr-sec">
            <div class="sr-sec-h"><span class="sr-src recent">최근 열어본 항목</span> <b>{{ recent.length }}</b></div>
            <div v-for="(it, i) in recent" :key="it.url" class="sr-item sr-recent"
                 :class="{ active: active === i }" @click="openRecent(it)" @mousemove="active = i">
              <!-- Jira 티켓 — 검색 결과와 동일 포맷(상태점·키·제목·Epic뱃지·프로젝트·상태·담당자) -->
              <template v-if="it.key">
                <span class="sr-dot" :class="stCls(it.statusCategory)"></span>
                <b class="sr-key">{{ it.key }}</b>
                <span class="sr-title">{{ it.summary || it.title }}</span>
                <span v-if="it.epicKey" class="sr-epic" :style="{ '--ec': epicColor(it.epicKey) }"
                      :title="'소속 Epic: ' + (it.epicName || it.epicKey)">{{ it.epicName || it.epicKey }}</span>
                <span class="sr-meta"><template v-if="it.project || it.status">{{ it.project }}<template v-if="it.project && it.status"> · </template>{{ it.status }}</template><template v-if="it.assignee">
                  <span class="sr-who"><Avatar :user="it.assigneeId" :name="it.assignee" :size="14" />{{ it.assignee }}</span>
                </template></span>
              </template>
              <!-- Confluence · 웹 등 그 외 — 기존 단순 표기(아이콘·제목·부제) -->
              <template v-else>
                <span :class="recentIc(it.kind)"></span>
                <span class="sr-title">{{ it.title }}</span>
                <span class="sr-meta">{{ it.meta }}</span>
              </template>
              <button class="sr-forget" title="목록에서 지우기"
                      @click.stop="forget(it)" aria-label="목록에서 지우기">✕</button>
            </div>
          </div>
        </template>
        <template v-else-if="res">
          <!-- Jira -->
          <div class="sr-sec" :class="{ stale: view.jira.stale }">
            <div class="sr-sec-h"><span class="sr-src jira">Jira</span> <b>{{ cnt('jira') }}</b><span v-if="view.jira.error" class="sr-serr">· {{ view.jira.error }}</span></div>
            <div v-for="(it, i) in view.jira.items" :key="it.key" class="sr-item" :class="{ active: flat[active] && flat[active].it === it }"
                 @click="!view.jira.stale && pick({ src: 'jira', it })" @mousemove="active = idx('jira', i)">
              <span class="sr-dot" :class="stCls(it.statusCategory)"></span>
              <b class="sr-key">{{ it.key }}</b>
              <span class="sr-title">{{ it.title }}</span>
              <span v-if="it.epicKey" class="sr-epic" :style="{ '--ec': epicColor(it.epicKey) }"
                    :title="'소속 Epic: ' + (it.epicName || it.epicKey)">{{ it.epicName || it.epicKey }}</span>
              <span class="sr-meta">{{ it.project }} · {{ it.status }}<template v-if="it.assignee">
                <span class="sr-who"><Avatar :user="it.assigneeId" :name="it.assignee" :size="14" />{{ it.assignee }}</span>
              </template></span>
            </div>
            <div v-if="loadingSrc.jira || view.jira.stale" class="sr-none"><span class="spinner"></span> 불러오는 중…</div>
            <div v-else-if="!cnt('jira') && !view.jira.error" class="sr-none">결과 없음</div>
          </div>
          <!-- Confluence -->
          <div class="sr-sec" :class="{ stale: view.confluence.stale }">
            <div class="sr-sec-h"><span class="sr-src conf">Confluence</span> <b>{{ cnt('confluence') }}</b><span v-if="view.confluence.error" class="sr-serr">· {{ view.confluence.error }}</span></div>
            <div v-for="(it, i) in view.confluence.items" :key="'c'+i" class="sr-item sr-item2 conf" :class="{ active: flat[active] && flat[active].it === it }"
                 @click="!view.confluence.stale && pick({ src: 'confluence', it })" @mousemove="active = idx('confluence', i)">
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
            <div v-if="loadingSrc.confluence || view.confluence.stale" class="sr-none"><span class="spinner"></span> 불러오는 중…</div>
            <div v-else-if="!cnt('confluence') && !view.confluence.error" class="sr-none">결과 없음</div>
          </div>
          <!-- Bitbucket (mock) -->
          <div class="sr-sec" :class="{ stale: view.bitbucket.stale }">
            <div class="sr-sec-h"><span class="sr-src bb">Bitbucket</span> <b>{{ cnt('bitbucket') }}</b> <span class="sr-mock">연동 예정(mock)</span></div>
            <div v-for="(it, i) in view.bitbucket.items" :key="'b'+i" class="sr-item mock">
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
