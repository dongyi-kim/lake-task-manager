import { api } from "../../lib/api.js";
import { confluencePageId, openConfluenceDocument, readFavorites, writeFavorites } from "../../lib/confluenceNavigation.js";
import { documentHeadings, visibleHeading } from "../../lib/confluenceToc.js";

const pageId = () => location.hash.match(/^#\/confluence\/(\d+)/)?.[1] || "";
const nextStart = data => data?._links?.next ? Number(data.start || 0) + Number(data.size ?? data.results?.length ?? 0) : null;
const merge = (old, rows) => [...new Map([...old, ...rows].map(row => [String(row.id ?? row.key), row])).values()];

export default {
  name: "ConfluenceView",
  data() { return { base: "", favorites: [], spaces: [], space: "", spacesNext: null,
    roots: [], rootsNext: null, children: {}, expanded: {}, loadingBranches: {}, branchErrors: {},
    query: "", results: [], searchNext: null, searching: false, searched: false,
    loading: false, error: "", doc: null, selectedId: "", reading: false, readError: "",
    checking: false, revision: null, revisionError: "", sidebarOpen: true,
    headings: [], activeHeading: "", tocOpen: true, tocMobileOpen: false, compact: false }; },
  computed: {
    rows() {
      const result = [];
      const visit = (items, level, visited = new Set()) => items.forEach(item => {
        const id = String(item.id);
        if (visited.has(id)) return;
        const next = new Set(visited); next.add(id);
        result.push({ ...item, id, level });
        if (this.expanded[id]) {
          visit(this.children[id]?.results || [], level + 1, next);
          if (this.loadingBranches[id] || this.branchErrors[id] || this.children[id]?.next !== null)
            result.push({ id: "more-" + id, parent: id, level: level + 1, more: true });
        }
      });
      visit(this.roots, 0);
      return result;
    },
    isFavorite() { return !!this.doc && this.favorites.some(row => String(row.id) === String(this.doc.id)); },
    newer() { return this.revision && this.doc && Number(this.revision.latestPublishedVersion) > Number(this.doc.version?.number); },
    version() { return this.doc?.version?.number || ""; },
    checkedAt() { return this.revision?.checkedAt || this.doc?.cacheInfo?.headCheckedAt; },
    showToc() { return this.compact ? this.tocMobileOpen : this.tocOpen; },
    headingDepth() { return Math.min(...this.headings.map(row => row.level), 6); },
  },
  mounted() {
    this._generation = 0; this._readGeneration = 0; this._searchGeneration = 0;
    this._compactQuery = window.matchMedia("(max-width:1100px)");
    this._resize = () => { this.compact = this._compactQuery.matches; this.tocMobileOpen = false; };
    this._resize(); this._compactQuery.addEventListener("change", this._resize);
    this._hash = () => { const id = pageId(); if (id && id !== this.selectedId) this.read(id); };
    window.addEventListener("hashchange", this._hash);
    this.initialize();
  },
  activated() { if (this._hash) this._hash(); this.$nextTick(() => this.buildToc()); },
  deactivated() { this.stopToc(); },
  beforeUnmount() { window.removeEventListener("hashchange", this._hash); this._compactQuery?.removeEventListener("change", this._resize); this.stopToc(); },
  methods: {
    async initialize() {
      // Local configuration is independent of SSO/network so saved titles appear immediately.
      try { const config = await api.confluenceConfig(); this.base = config.base;
        const spaces = new Set((config.spaces || []).map(value => value.toLowerCase()));
        this.favorites = readFavorites(this.base).filter(row => spaces.has(String(row.space || "").toLowerCase())); }
      catch (error) { this.error = error.message; }
      this.loadSpaces(); this._hash();
    },
    async loadSpaces(more = false) {
      this.loading = true; this.error = "";
      try {
        const data = await api.confluenceSpaces(more ? this.spacesNext : 0);
        this.spaces = merge(more ? this.spaces : [], data.results || []);
        this.spacesNext = nextStart(data);
        if (!this.space && this.spaces.length) { this.space = this.spaces[0].key; await this.changeSpace(); }
      } catch (error) { this.error = error.message; }
      finally { this.loading = false; }
    },
    async changeSpace() {
      this._generation++; this._searchGeneration++;
      this.roots = []; this.rootsNext = null; this.children = {}; this.expanded = {};
      this.loadingBranches = {}; this.branchErrors = {}; this.results = []; this.searched = false;
      await Promise.all([this.loadRoots(), this.query.trim() ? this.search() : Promise.resolve()]);
    },
    async loadRoots(more = false) {
      const gen = this._generation; this.loading = true; this.error = "";
      try {
        const data = await api.confluencePages(this.space, "", more ? this.rootsNext : 0);
        if (gen !== this._generation) return;
        this.roots = merge(more ? this.roots : [], data.results || []); this.rootsNext = nextStart(data);
      } catch (error) { if (gen === this._generation) this.error = error.message; }
      finally { if (gen === this._generation) this.loading = false; }
    },
    async toggle(row) {
      this.expanded[row.id] = !this.expanded[row.id];
      if (this.expanded[row.id] && !this.children[row.id]) await this.loadChildren(row.id);
    },
    async loadChildren(id, more = false) {
      if (this.loadingBranches[id]) return;
      const gen = this._generation; this.loadingBranches[id] = true; this.branchErrors[id] = "";
      try {
        const data = await api.confluencePages(this.space, id, more ? this.children[id].next : 0);
        if (gen !== this._generation) return;
        this.children[id] = { results: merge(more ? this.children[id].results : [], data.results || []), next: nextStart(data) };
      } catch (error) { if (gen === this._generation) this.branchErrors[id] = error.message; }
      finally { if (gen === this._generation) this.loadingBranches[id] = false; }
    },
    async search(more = false) {
      if (!this.query.trim()) { this._searchGeneration++; this.results = []; this.searched = false; this.searching = false; return; }
      const gen = ++this._searchGeneration; this.searching = true; this.error = "";
      try {
        const data = await api.confluenceSearch(this.query, this.space, more ? this.searchNext : 0);
        if (gen !== this._searchGeneration) return;
        const rows = (data.results || []).map(item => item.content || item);
        this.results = merge(more ? this.results : [], rows); this.searchNext = nextStart(data); this.searched = true;
      } catch (error) { if (gen === this._searchGeneration) this.error = error.message; }
      finally { if (gen === this._searchGeneration) this.searching = false; }
    },
    open(id) { openConfluenceDocument(id); },
    async read(id, version = null) {
      const gen = ++this._readGeneration;
      const replacing = String(this.doc?.id) === String(id);
      const scrollTop = replacing ? this.$refs.reader?.scrollTop || 0 : 0;
      this.selectedId = String(id); this.reading = true; this.readError = "";
      this.revision = null; this.revisionError = ""; this.checking = false;
      if (!replacing) { this.stopToc(); this.doc = null; this.headings = []; this.activeHeading = ""; }
      try {
        const data = await api.confluenceDocument(id, version);
        if (gen !== this._readGeneration) return;
        this.doc = data;
        const favorite = this.favorites.find(row => String(row.id) === String(data.id));
        if (favorite && favorite.title !== data.title) { favorite.title = data.title; this.saveFavorites(); }
        this.$nextTick(() => { this.buildToc(); if (this.$refs.reader) this.$refs.reader.scrollTop = scrollTop; this.updateToc(); });
      } catch (error) { if (gen === this._readGeneration) { this.readError = error.message;
        if ([403, 404].includes(error.status)) { this.doc = null; this.headings = []; this.stopToc(); } } }
      finally { if (gen === this._readGeneration) this.reading = false; }
    },
    async checkLatest() {
      if (!this.doc || this.checking) return;
      const id = this.doc.id, gen = this._readGeneration;
      this.checking = true; this.revisionError = "";
      try { const result = await api.confluenceRevision(id, this.version); if (gen === this._readGeneration) this.revision = result; }
      catch (error) { if (gen === this._readGeneration) { this.revision = null; this.revisionError = error.message;
        if ([403, 404].includes(error.status)) { this.doc = null; this.headings = []; this.stopToc(); } } }
      finally { if (gen === this._readGeneration) this.checking = false; }
    },
    toggleFavorite() {
      if (!this.doc) return;
      this.favorites = this.isFavorite ? this.favorites.filter(row => String(row.id) !== String(this.doc.id))
        : [...this.favorites, { id: String(this.doc.id), title: this.doc.title, space: this.doc.space?.key || "" }];
      this.saveFavorites();
    },
    saveFavorites() { try { writeFavorites(this.base, this.favorites); } catch (_) { this.readError = "즐겨찾기를 저장하지 못했습니다. 브라우저 저장 공간을 확인해 주세요."; } },
    stopToc() {
      if (this._tocReader && this._tocScroll) this._tocReader.removeEventListener("scroll", this._tocScroll);
      if (this._tocFrame) cancelAnimationFrame(this._tocFrame);
      this._tocFrame = null; this._tocReader = null; this._tocClicked = false;
    },
    buildToc() {
      this.stopToc();
      const body = this.$refs.documentBody, reader = this.$refs.reader;
      if (!body || !reader) { this.headings = []; return; }
      this._tocHeadings = documentHeadings(body);
      this.headings = this._tocHeadings.map(({ id, title, level }) => ({ id, title, level }));
      this._tocReader = reader;
      this._tocScroll = () => {
        if (this._tocClicked) { this._tocClicked = false; return; }
        if (this._tocFrame) return;
        this._tocFrame = requestAnimationFrame(() => { this._tocFrame = null; this.updateToc(); });
      };
      reader.addEventListener("scroll", this._tocScroll, { passive: true });
      this.updateToc();
    },
    updateToc() { if (this._tocReader) this.activeHeading = visibleHeading(this._tocHeadings || [], this._tocReader); },
    jumpToHeading(id) {
      const target = this._tocHeadings?.find(row => row.id === id), reader = this._tocReader;
      if (!target || !reader) return;
      this._tocClicked = true; this.activeHeading = id;
      const top = target.node.getBoundingClientRect().top - reader.getBoundingClientRect().top + reader.scrollTop - 12;
      reader.scrollTo({ top, behavior: "auto" }); target.node.focus({ preventScroll: true });
      if (this.compact) this.tocMobileOpen = false;
    },
    toggleToc() { if (this.compact) this.tocMobileOpen = !this.tocMobileOpen; else this.tocOpen = !this.tocOpen; },
    path(item) { return (item.ancestors || []).map(row => row.title).join(" / ") || item.space?.name || item.space?.key || ""; },
    date(value) { if (!value) return ""; const date = new Date(typeof value === "number" ? value * 1000 : value); return Number.isNaN(+date) ? "" : date.toLocaleString(); },
    author() { const who = this.doc?.version?.by; return who?.displayName || who?.username || ""; },
    linkClick(event) {
      const link = event.target.closest("a[href]"); if (!link) return;
      try { const url = new URL(link.href); if (url.origin === new URL(this.base).origin && confluencePageId(url.href)) { event.preventDefault(); this.open(url.href); } }
      catch (_) { /* sanitizer already handles non-web links */ }
    },
  },
  template: `
    <section class="conf-workspace" aria-label="Confluence 문서">
      <aside v-show="sidebarOpen" class="conf-sidebar" aria-label="문서 탐색">
        <div class="conf-sidebar-title"><h1>Confluence</h1><button class="btn" @click="sidebarOpen = false" aria-label="문서 탐색 접기">접기</button></div>
        <section class="conf-favorites" aria-label="즐겨찾기">
          <h2>즐겨찾기 <span>{{ favorites.length }}</span></h2>
          <p v-if="!favorites.length" class="conf-muted">문서의 ☆ 버튼으로 여기에 고정하세요.</p>
          <button v-for="item in favorites" :key="item.id" class="conf-tree-title" :class="{selected: selectedId === item.id}" @click="open(item.id)" :title="item.title">★ {{ item.title }}</button>
        </section>
        <label class="conf-space-label">Space<select v-model="space" @change="changeSpace" aria-label="Space"><option v-for="item in spaces" :key="item.key" :value="item.key">{{ item.name }} ({{ item.key }})</option></select></label>
        <button v-if="spacesNext !== null" class="conf-more" @click="loadSpaces(true)">Space 더 보기</button>
        <form class="conf-search" @submit.prevent="search()"><input v-model="query" @input="!query.trim() && search()" aria-label="문서 트리 검색" placeholder="이 Space의 문서 검색"><button class="btn" :disabled="searching">검색</button></form>
        <p v-if="error" class="conf-error" role="alert">{{ error }} <button class="conf-more" @click="query.trim() ? search() : (space ? loadRoots() : loadSpaces())">다시 시도</button></p>
        <div class="conf-tree" role="navigation" aria-label="문서 트리">
          <p v-if="searching" role="status" class="conf-muted">검색 중…</p>
          <template v-if="searched && query.trim()">
            <p class="conf-muted">{{ results.length }}개 표시{{ searchNext !== null ? ' · 더 불러올 결과가 있습니다' : '' }}</p>
            <button v-for="item in results" :key="item.id" class="conf-search-result" :class="{selected: selectedId === String(item.id)}" @click="open(item.id)"><strong>{{ item.title }}</strong><small>{{ path(item) }}</small></button>
            <p v-if="!results.length && !error" class="conf-muted">검색에서 반환된 문서가 없습니다. 다른 검색어로 찾아보세요.</p>
            <button v-if="searchNext !== null" class="conf-more" @click="search(true)" :disabled="searching">검색 결과 더 보기</button>
          </template>
          <template v-else>
            <div v-for="row in rows" :key="row.id" class="conf-tree-row" :style="{'--depth': row.level}">
              <template v-if="row.more"><span v-if="loadingBranches[row.parent]" class="conf-muted" role="status">불러오는 중…</span><button v-else class="conf-more" @click="loadChildren(row.parent, !branchErrors[row.parent])">{{ branchErrors[row.parent] ? '하위 문서 다시 시도' : '하위 문서 더 보기' }}</button></template>
              <template v-else><button class="conf-expand" @click="toggle(row)" :aria-expanded="!!expanded[row.id]" :aria-label="row.title + ' 하위 문서'">{{ expanded[row.id] ? '⌄' : '›' }}</button><button class="conf-tree-title" :class="{selected: selectedId === row.id}" @click="open(row.id)" :title="row.title">{{ row.title }}</button></template>
            </div>
            <p v-if="loading" class="conf-muted" role="status">문서 목록을 불러오는 중…</p>
            <button v-if="rootsNext !== null" class="conf-more" @click="loadRoots(true)" :disabled="loading">루트 문서 더 보기</button>
            <p v-if="!loading && !roots.length && !error" class="conf-muted">이 Space에서 볼 수 있는 루트 문서가 없습니다.</p>
          </template>
        </div>
      </aside>
      <main class="conf-main">
        <header class="conf-toolbar"><button v-if="!sidebarOpen" class="btn" @click="sidebarOpen = true">문서 탐색</button><span>{{ doc?.space?.name || '문서' }}</span><div v-if="doc" class="conf-actions"><button v-if="headings.length" class="btn" @click="toggleToc" :aria-expanded="showToc" aria-controls="conf-document-toc">목차</button><button class="btn conf-favorite-toggle" @click="toggleFavorite" :aria-pressed="isFavorite" :aria-label="isFavorite ? '즐겨찾기 해제' : '즐겨찾기 추가'">{{ isFavorite ? '★' : '☆' }}</button><button class="btn" @click="checkLatest" :disabled="checking || reading">{{ checking ? '확인 중…' : '최신 여부 확인' }}</button><a :href="doc.url" target="_blank" rel="noopener noreferrer" class="btn">Confluence에서 열기</a></div></header>
        <p v-if="readError || revisionError" class="conf-error" role="alert">{{ readError || revisionError }} <button v-if="readError" class="conf-more" @click="read(selectedId)">다시 시도</button></p>
        <div v-if="newer" class="conf-new-version" role="status">새 발행 버전 v{{ revision.latestPublishedVersion }}이 있습니다.<button class="btn" @click="read(doc.id, revision.latestPublishedVersion)">새 버전 보기</button></div>
        <p v-else-if="revision?.isLatest" class="conf-current" role="status">확인 시점의 최신 발행 버전입니다. {{ date(revision.checkedAt) }}</p>
        <div class="conf-reading-layout"><div ref="reader" class="conf-reader" :aria-busy="reading">
          <p v-if="reading" class="conf-muted" role="status">문서를 불러오는 중…</p>
          <article v-if="doc" class="conf-document"><nav v-if="doc.ancestors?.length" class="conf-breadcrumb" aria-label="문서 경로"><button v-for="item in doc.ancestors" :key="item.id" @click="open(item.id)">{{ item.title }}</button></nav><h1>{{ doc.title }}</h1><p class="conf-document-meta">v{{ version }} <span v-if="doc.version?.when">{{ date(doc.version.when) }} 수정</span><span>{{ author() }}</span><span v-if="checkedAt">최신 확인 {{ date(checkedAt) }}</span></p><div ref="documentBody" class="conf-document-body" v-html="doc.html" @click="linkClick"></div></article>
          <div v-else-if="!reading && !readError" class="conf-empty"><h2>프로젝트 문서를 한곳에서</h2><p>왼쪽 트리나 검색에서 문서를 선택하세요.<br>자주 보는 문서는 즐겨찾기에 고정할 수 있습니다.</p></div>
        </div><aside v-if="headings.length" v-show="showToc" id="conf-document-toc" class="conf-toc" aria-label="이 문서의 목차"><h2>이 문서의 목차</h2><nav aria-label="본문 목차"><button v-for="heading in headings" :key="heading.id" :style="{'--level': heading.level - headingDepth}" :aria-current="activeHeading === heading.id ? 'location' : null" @click="jumpToHeading(heading.id)">{{ heading.title }}</button></nav></aside></div>
      </main>
    </section>`,
};
