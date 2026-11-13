import { api } from "../../lib/api.js";
import { confluencePageId, openConfluenceDocument, readFavorites, writeFavorites, readNavigationFolds } from "../../lib/confluenceNavigation.js";
import { documentHeadings, visibleHeading } from "../../lib/confluenceToc.js";
import { mountConfluenceMacros } from "../../lib/confluenceMacroView.js";
import confluenceLayout from "../../lib/confluenceLayout.js";
import ConfluencePanel from "../ui/ConfluencePanel.js";
import ConfluenceDocumentList from "../ui/ConfluenceDocumentList.js";
import ConfluenceComposer from "../ui/ConfluenceComposer.js";
import ConfluencePageMenu from "../ui/ConfluencePageMenu.js";
import confluenceTree from "../../lib/confluenceTree.js";
import confluenceSavedTree from '../../lib/confluenceSavedTree.js';
import confluenceRecents from '../../lib/confluenceRecents.js';
import ConfluenceTreeRow from '../ui/ConfluenceTreeRow.js';
import { readConfluence } from '../../lib/confluenceRead.js';
import ConfluenceReadNotice from '../ui/ConfluenceReadNotice.js';
import { reactive } from '../../vendor/vue.esm-browser.prod.js';
import { createOpenItemTabs, normalizeItemId } from '../../lib/openItemTabs.js';
import OpenItemTabs from '../ui/OpenItemTabs.js';

const pageId = () => normalizeItemId('confluence', location.hash.match(/^#\/confluence\/(\d+)/)?.[1] || "");
const nextStart = data => data?._links?.next ? Number(data.start || 0) + Number(data.size ?? data.results?.length ?? 0) : null;
const merge = (old, rows) => [...new Map([...old, ...rows].map(row => [String(row.id ?? row.key), row])).values()];

export default {
  name: "ConfluenceView",
  components: { ConfluencePanel, ConfluenceDocumentList, ConfluenceComposer, ConfluencePageMenu, ConfluenceTreeRow, ConfluenceReadNotice, OpenItemTabs },
  mixins: [confluenceLayout, confluenceTree, confluenceSavedTree, confluenceRecents],
  data() { return { documentTabs: createOpenItemTabs({ kind: 'confluence', reactive }), tabNavigationPending: false,
    base: "", favorites: [], recentDocuments: [], navigationFolds: readNavigationFolds(), spaces: [], space: "", spacesNext: null,
    loading: false, error: "", doc: null, selectedId: "", reading: false, readError: "", readFailure: null, readSlow: false, readRetrying: false, requestedVersion: null,
    checking: false, revision: null, revisionError: "",
    headings: [], activeHeading: "", composing: false, editingPage: null, openingEditor: false, composeParent: null, contextPage: null }; },
  computed: {
    isFavorite() { return !!this.doc && this.favorites.some(row => String(row.id) === String(this.doc.id)); },
    newer() { return this.revision && this.doc && Number(this.revision.latestPublishedVersion) > Number(this.doc.version?.number); },
    version() { return this.doc?.version?.number || ""; },
    checkedAt() { return this.revision?.checkedAt || this.doc?.cacheInfo?.headCheckedAt; },
    headingDepth() { return Math.min(...this.headings.map(row => row.level), 6); },
  },
  mounted() {
    this._viewActive = true;
    this._readGeneration = 0;
    this._tabNavigationGeneration = 0;
    this._hash = () => {
      const id = pageId();
      if (this._viewActive && this._navigationReady && id && id !== this.selectedId) this.navigateDocument(id, { fromHash: true });
    };
    window.addEventListener("hashchange", this._hash);
    this.initialize();
  },
  activated() {
    this._viewActive = true;
    if (this._hash) this._hash();
    if (this._navigationReady && !pageId() && !this.selectedId && this.documentTabs.state.activeId && !this.composing)
      this.navigateDocument(this.documentTabs.state.activeId);
    this.$nextTick(() => this.buildToc());
  },
  deactivated() { this._viewActive = false; ++this._tabNavigationGeneration; this.tabNavigationPending = false;
    this.captureTabScroll(); this.contextPage=null; this.stopToc(); },
  beforeUnmount() { this.captureTabScroll(); ++this._tabNavigationGeneration; ++this._readGeneration;
    this._readAbort?.abort(); window.removeEventListener("hashchange", this._hash); this.stopToc(); },
  methods: {
    async closeComposerForNavigation() {
      if (!this.composing) return true;
      // The composer owns body drafts and mutation receipts. Metadata tabs never discard either.
      if (!this._composerClosePromise) {
        const composer = this.$refs.composer;
        if (!composer) return false;
        this._composerClosePromise = Promise.resolve(composer.close({ requireDurable: true, canClose: () => this._viewActive }))
          .finally(() => { this._composerClosePromise = null; });
      }
      return await this._composerClosePromise === true;
    },
    captureTabScroll() {
      clearTimeout(this._tabScrollTimer);
      const reader = this.$refs.reader;
      if (reader && this.doc && String(this.doc.id) === this.selectedId)
        this.documentTabs.setScroll(this.selectedId, { x: reader.scrollLeft, y: reader.scrollTop });
    },
    rememberTabScroll() {
      clearTimeout(this._tabScrollTimer);
      this._tabScrollTimer = setTimeout(() => this.captureTabScroll(), 150);
    },
    restoreNavigationFocus(previous) {
      if (!previous?.closest?.('.open-item-tabs')) return;
      this.$nextTick(() => {
        const current = globalThis.document?.activeElement;
        if (current && current !== globalThis.document.body && current !== previous) return;
        const target = previous.isConnected && !previous.disabled ? previous :
          this.$el.querySelector('[data-open-tab="' + this.documentTabs.state.activeId + '"]');
        target?.focus({ preventScroll: true });
      });
    },
    async navigateDocument(value, { fromHash = false, anchor = '' } = {}) {
      const id = normalizeItemId('confluence', confluencePageId(String(value)));
      if (!id || !this._viewActive) return false;
      if (id === this.selectedId && !anchor && this.composing) return true;
      const previousFocus = globalThis.document?.activeElement;
      const navigation = ++this._tabNavigationGeneration;
      const previousHash = this.selectedId ? '#/confluence/' + this.selectedId : '#/confluence';
      this.tabNavigationPending = true;
      try {
        if (!await this.closeComposerForNavigation()) {
          if (fromHash && navigation === this._tabNavigationGeneration)
            history.replaceState(null, '', previousHash);
          return false;
        }
        if (navigation !== this._tabNavigationGeneration || !this._viewActive) return false;
        this.captureTabScroll(); this.closeDrawer();
        this._pendingAnchor = anchor;
        if (this.tree.nodes.get(id)?.hasChildren === true) this.prioritizeTree(id);
        if (this.tree.matches.includes(id)) this.activeMatch = id;
        this.documentTabs.open({ id, title: this.documentTabs.state.items.find(row => row.id === id)?.title || id });
        if (!fromHash) openConfluenceDocument(id);
        if (id !== this.selectedId || !this.doc) this.read(id);
        else if (anchor) this.$nextTick(() => this.jumpToAnchor(anchor));
        return true;
      } finally { if (navigation === this._tabNavigationGeneration) {
        this.tabNavigationPending = false; this.restoreNavigationFocus(previousFocus);
      } }
    },
    async closeDocumentTab(id) {
      if (!this._viewActive) return;
      if (id !== this.documentTabs.state.activeId) { this.documentTabs.close(id); return; }
      const previousFocus = globalThis.document?.activeElement;
      const navigation = ++this._tabNavigationGeneration;
      this.tabNavigationPending = true;
      try {
        if (!await this.closeComposerForNavigation() || navigation !== this._tabNavigationGeneration || !this._viewActive) return;
        this.captureTabScroll(); this.documentTabs.close(id);
        this._pendingAnchor = '';
        const next = this.documentTabs.state.activeId;
        if (next) { openConfluenceDocument(next); this.read(next); }
        else {
          ++this._readGeneration; this._readAbort?.abort(); this.stopToc();
          this.selectedId = ''; this.doc = null; this.headings = []; this.activeHeading = '';
          this.reading = false; this.readError = ''; this.readFailure = null;
          this.readSlow = false; this.readRetrying = false; this.revision = null; this.revisionError = ''; this.checking = false;
          history.replaceState(null, '', '#/confluence');
          this.$nextTick(() => this.$el.querySelector('[aria-label="문서 트리 검색"]')?.focus());
        }
      } finally { if (navigation === this._tabNavigationGeneration) {
        this.tabNavigationPending = false; this.restoreNavigationFocus(previousFocus);
      } }
    },
    startCompose(parent = null) { if (this.tabNavigationPending) return;
      this.captureTabScroll(); this.editingPage = null; this.composeParent = parent; this.contextPage = null;
      this.closeDrawer(); this.stopToc(); this.headings = []; this.composing = true; },
    showPageMenu(row,event) {
      if (row.more) return;
      const box=event.currentTarget.getBoundingClientRect();
      this.contextPage={row:{...row,space:row.space?.key || row.space || this.space},
        x:event.clientX || box.left, y:event.clientY || box.bottom};
    },
    editorOutline(value) { this.headings=value.headings; this.activeHeading=value.active; },
    async editDocument() {
      if (this.openingEditor || !this.doc) return;
      const id = this.doc.id, generation = this._readGeneration;
      this.openingEditor = true; this.readError = "";
      try {
        const source = await api.confluenceSource(id);
        if (generation !== this._readGeneration) return;
        this.editingPage = source; this.composeParent = null; this.closeDrawer(); this.stopToc(); this.headings = []; this.composing = true;
      } catch (error) { if (generation === this._readGeneration) this.readError = error.message; }
      finally { this.openingEditor = false; }
    },
    closeCompose() { this.composing = false; this.$nextTick(() => this.buildToc()); },
    createdDocument(id) { this.closeCompose(); this.loadTree(true);
      if (String(id) === this.selectedId) this.read(id, null, true); else this.open(id); },
    async initialize() {
      // Local configuration is independent of SSO/network so saved titles appear immediately.
      const healthPromise = api.health().catch(() => null);
      try { const config = await api.confluenceConfig(); this.base = config.base;
        const spaces = new Set((config.spaces || []).map(value => value.toLowerCase()));
        this._recentSpaces = spaces;
        this.favorites = readFavorites(this.base).filter(row => spaces.has(String(row.space || "").toLowerCase()));
        this.loadRecentDocuments();
        this.loadSavedTrees(); }
      catch (error) { this.error = error.message; }
      const health = await healthPromise;
      this.documentTabs.setContext({ env: health?.env, jiraBase: health?.jiraBase, projectKey: health?.projectKey, confluenceBase: this.base });
      this._navigationReady = true;
      this.loadSpaces();
      const initialId = pageId() || this.documentTabs.state.activeId;
      if (initialId && this._viewActive) this.navigateDocument(initialId, { fromHash: !!pageId() });
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
      this.contextPage = null;
      await this.loadTree(true);
    },
    open(id) {
      let anchor = '';
      try { const url = new URL(String(id)); anchor = url.hash ? decodeURIComponent(url.hash.slice(1)) : ''; } catch (_) { /* numeric page id */ }
      return this.navigateDocument(id, { anchor });
    },
    async read(id, version = null, refresh = false) {
      const gen = ++this._readGeneration;
      this.requestedVersion = version;
      this._readAbort?.abort();
      const controller = this._readAbort = new AbortController();
      this.readSlow = false; this.readRetrying = false; this.readFailure = null;
      const slowTimer = setTimeout(() => { if (gen === this._readGeneration) this.readSlow = true; }, 8000);
      const replacing = String(this.doc?.id) === String(id);
      const savedScroll = this.documentTabs.state.items.find(row => row.id === String(id))?.scroll;
      const scrollTop = replacing ? this.$refs.reader?.scrollTop || 0 : savedScroll?.y || 0;
      const scrollLeft = replacing ? this.$refs.reader?.scrollLeft || 0 : savedScroll?.x || 0;
      this.documentTabs.open({ id: String(id), title: this.documentTabs.state.items.find(row => row.id === String(id))?.title || String(id) });
      this.selectedId = String(id); this.reading = true; this.readError = "";
      this.revision = null; this.revisionError = ""; this.checking = false;
      if (!replacing) { this.stopToc(); this.doc = null; this.headings = []; this.activeHeading = ""; }
      try {
        const data = await readConfluence(id, version, refresh, controller.signal,
          () => { if (gen === this._readGeneration) this.readRetrying = true; });
        if (gen !== this._readGeneration) return;
        this.doc = data;
        this.documentTabs.update(String(id), { title: data.title });
        this.rememberDocument(data);
        const favorite = this.favorites.find(row => String(row.id) === String(data.id));
        if (favorite && favorite.title !== data.title) { favorite.title = data.title; this.saveFavorites(); }
        this.$nextTick(() => { if (gen !== this._readGeneration) return;
          this.buildToc(); if (this.$refs.reader) { this.$refs.reader.scrollTop = scrollTop; this.$refs.reader.scrollLeft = scrollLeft; }
          if (this._pendingAnchor) { this.jumpToAnchor(this._pendingAnchor); this._pendingAnchor=''; } this.updateToc(); });
      } catch (error) { if (gen === this._readGeneration && error.name !== 'AbortError') { this.readError = error.message; this.readFailure = error;
        if ([403, 404].includes(error.status)) { this.doc = null; this.headings = []; this.stopToc(); } } }
      finally { clearTimeout(slowTimer); if (gen === this._readGeneration) this.reading = false; }
    },
    async checkLatest() {
      if (!this.doc || this.checking) return;
      const id = this.doc.id, gen = this._readGeneration;
      this.checking = true; this.revisionError = "";
      try { const result = await api.confluenceRevision(id, this.version); if (gen === this._readGeneration) {
        if (result.isLatest) await this.read(id, null, true);
        if (String(this.selectedId) === String(id)) this.revision = result;
      } }
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
    toggleNavigationList(name) {
      this.navigationFolds[name] = !this.navigationFolds[name];
      try { localStorage.setItem("ltm.confluence.navigation-folds", JSON.stringify(this.navigationFolds)); } catch (_) { /* session state remains */ }
    },
    stopToc() {
      this._unmountMacros?.(); this._unmountMacros = null;
      if (this._tocReader && this._tocScroll) this._tocReader.removeEventListener("scroll", this._tocScroll);
      if (this._tocFrame) cancelAnimationFrame(this._tocFrame);
      this._tocFrame = null; this._tocReader = null; this._tocClicked = false;
    },
    buildToc() {
      if (this.composing) return;
      this.stopToc();
      const body = this.$refs.documentBody, reader = this.$refs.reader;
      if (!body || !reader) { this.headings = []; return; }
      this._tocHeadings = documentHeadings(body);
      this._unmountMacros = mountConfluenceMacros(body, this._tocHeadings);
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
      if (this.composing) { this.closeDrawer(); this.$nextTick(() => this.$refs.composer?.jumpToHeading(id)); return; }
      const target = this._tocHeadings?.find(row => row.id === id), reader = this._tocReader;
      if (!target || !reader) return;
      for (let parent = target.node.parentElement; parent && parent !== reader; parent = parent.parentElement)
        if (parent.tagName === "DETAILS") parent.open = true;
      this._tocClicked = true; this.activeHeading = id;
      const top = target.node.getBoundingClientRect().top - reader.getBoundingClientRect().top + reader.scrollTop - 12;
      reader.scrollTo({ top, behavior: "auto" });
      if (this.compact) {
        this.closeDrawer();
        this.$nextTick(() => requestAnimationFrame(() => target.node.focus({ preventScroll: true })));
      } else target.node.focus({ preventScroll: true });
    },
    date(value) { if (!value) return ""; const date = new Date(typeof value === "number" ? value * 1000 : value); return Number.isNaN(+date) ? "" : date.toLocaleString(); },
    author() { const who = this.doc?.version?.by; return who?.displayName || who?.username || ""; },
    jumpToAnchor(id) {
      const target = [...(this.$refs.documentBody?.querySelectorAll('[id]') || [])].find(node => node.id === id || node.id === 'conf-anchor-'+id);
      if (!target) return;
      for (let parent=target.parentElement; parent && parent!==this.$refs.reader; parent=parent.parentElement) if (parent.tagName==='DETAILS') parent.open=true;
      target.scrollIntoView({block:'start'});
    },
    linkClick(event) {
      const link = event.target.closest("a[href]"); if (!link) return;
      if (link.getAttribute("href").startsWith("#")) {
        if (link.getAttribute("href").startsWith("#/confluence/")) {
          event.preventDefault(); this.open(link.getAttribute('href').match(/^#\/confluence\/(\d+)/)?.[1]); return;
        }
        event.preventDefault();
        let id; try { id = decodeURIComponent(link.getAttribute("href").slice(1)); } catch (_) { return; }
        this.jumpToAnchor(id); return;
      }
      try { const url = new URL(link.href); if (url.origin === new URL(this.base).origin && confluencePageId(url.href)) { event.preventDefault(); this.open(url.href); } }
      catch (_) { /* sanitizer already handles non-web links */ }
    },
  },
  template: `
    <section class="conf-view">
    <OpenItemTabs v-if="documentTabs.state.items.length" :items="documentTabs.state.items" :active-id="documentTabs.state.activeId" kind="confluence" label="최근 열린 문서" panel-id="confluence-workspace" :disabled="tabNavigationPending" @activate="navigateDocument" @close="closeDocumentTab" />
    <section id="confluence-workspace" class="conf-workspace" role="tabpanel" :class="{ 'conf-compact': compact, 'conf-resizing': draggingPanel }" :style="layoutStyle" aria-label="Confluence 문서">
      <ConfluencePanel v-if="!composing" :visible="showSidebar" :compact="compact" label="문서 탐색" @close="toggleSidebar" class="conf-panel conf-sidebar">
        <div class="conf-panel-title"><h1>Confluence</h1><button class="conf-fold" @click="toggleSidebar" aria-label="문서 탐색 접기" title="문서 탐색 접기"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12 5l-5 5 5 5"/></svg></button></div>
        <div class="conf-navigation-controls">
          <label class="conf-space-label">Space<select v-model="space" @change="changeSpace" aria-label="Space"><option v-for="item in spaces" :key="item.key" :value="item.key">{{ item.name }} ({{ item.key }})</option></select></label>
          <button v-if="spacesNext !== null" class="conf-more" @click="loadSpaces(true)">Space 더 보기</button>
          <div class="conf-search">
            <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8" cy="8" r="5"/><path d="m12 12 5 5"/></svg>
            <input v-model="query" aria-label="문서 트리 검색" placeholder="트리에서 제목 찾기" @keydown.enter.prevent="moveTreeMatch($event.shiftKey ? -1 : 1)" @keydown.esc.prevent="clearTreeSearch" />
            <button v-if="query" type="button" class="conf-find-control" @click="clearTreeSearch" aria-label="트리 검색 지우기">×</button>
          </div>
          <div v-if="tree.searching" class="conf-find-status" role="status">
            <span>{{ tree.matches.length ? matchPosition + ' / ' + tree.matches.length + '개 일치' : (treeLoading ? '일치 문서 찾는 중…' : '일치하는 제목 없음') }}</span>
            <button class="conf-find-control" @click="moveTreeMatch(-1)" :disabled="!tree.matches.length" aria-label="이전 일치 문서">↑</button><button class="conf-find-control" @click="moveTreeMatch(1)" :disabled="!tree.matches.length" aria-label="다음 일치 문서">↓</button>
          </div>
        </div>
        <div class="conf-sidebar-scroll">
          <ConfluenceDocumentList title="즐겨찾기" :rows="favorites" :selected-id="selectedId" :expanded="!navigationFolds.favorites" @toggle="toggleNavigationList('favorites')" @open="open" empty-text="문서의 ☆ 버튼으로 여기에 고정하세요."
            :nodes="savedTreeNodes" :favorites="favoriteIds" :recents="recentDocumentIds" :pending="savedTreePending" :errors="savedTreeErrors" @expand="expandSavedTree" @menu="showPageMenu" />
          <ConfluenceDocumentList title="최근 열어본 문서" :rows="recentMenu" :selected-id="selectedId" :expanded="!navigationFolds.recent" @toggle="toggleNavigationList('recent')" @open="open" empty-text="열어본 문서가 여기에 표시됩니다."
            :nodes="savedTreeNodes" :favorites="favoriteIds" :recents="recentDocumentIds" :pending="savedTreePending" :errors="savedTreeErrors" @expand="expandSavedTree" @menu="showPageMenu" />
          <p v-if="error" class="conf-error" role="alert">{{ error }} <button class="conf-more" @click="loadSpaces()">다시 시도</button></p>
          <div class="conf-tree-caption"><span>페이지 트리</span><span>{{ tree.count }}</span></div>
          <div class="conf-tree" :class="{'is-finding': tree.searching}" role="navigation" aria-label="문서 트리" :aria-busy="treeLoading">
            <ConfluenceTreeRow v-for="row in tree.rows" :key="row.id" :row="row" :selected-id="selectedId" :favorite="favoriteIds.has(row.id)" :recent="recentDocumentIds.has(row.id)"
              :current-match="activeMatch === row.id" :pending="treeLoading && treePending === row.id" @toggle="toggle" @open="open" @menu="showPageMenu" />
            <p v-if="treeLoading" class="conf-muted" role="status">문서 목록을 불러오는 중…</p>
            <p v-if="treeError" class="conf-error" role="alert">{{ treeError }} <button class="conf-more" @click="loadTree()">문서 목록 다시 시도</button></p>
            <p v-if="!treeLoading && !tree.count && !treeError" class="conf-muted">이 Space에서 볼 수 있는 문서가 없습니다.</p>
          </div>
        </div>
        <ConfluencePageMenu v-if="contextPage" :key="contextPage.row.id" :target="contextPage" @close="contextPage=null" @create="startCompose" />
      </ConfluencePanel>
      <div v-if="!composing && showSidebar && !compact" class="conf-resizer" role="separator" tabindex="0" aria-orientation="vertical" aria-label="문서 탐색 너비 조절" :aria-valuenow="sidebarWidth" aria-valuemin="224" :aria-valuemax="panelMax('sidebar')" @pointerdown="startPanelResize('sidebar', $event)" @keydown="resizePanelKey('sidebar', $event)"></div>
      <ConfluenceComposer v-if="composing" ref="composer" :base="base" :spaces="spaces" :space="space" :editing-page="editingPage" :parent="composeParent || (doc ? { id: doc.id, title: doc.title, space: doc.space?.key } : null)" :parent-draft="!!composeParent" :toc-visible="showToc" @toggle-toc="toggleToc" @outline="editorOutline" @close="closeCompose" @created="createdDocument" />
      <main v-else class="conf-main">
        <header class="conf-toolbar">
          <button class="btn conf-create" :disabled="!base || !spaces.length" @click="startCompose()">＋ 새 문서</button>
          <button v-if="doc?.canEdit" class="btn conf-edit" :disabled="reading || openingEditor" @click="editDocument">{{ openingEditor ? '원문 확인 중…' : '수정' }}</button>
          <div class="conf-toolbar-location"><button v-if="!showSidebar || compact" class="btn conf-nav-toggle" @click="toggleSidebar" :aria-expanded="showSidebar"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 4h14v12H3zM8 4v12"/></svg>문서 탐색</button><span>{{ doc?.space?.name || '문서' }}</span></div>
          <div v-if="doc" class="conf-actions"><button class="btn conf-favorite-toggle" @click="toggleFavorite" :aria-pressed="isFavorite" :aria-label="isFavorite ? '즐겨찾기 해제' : '즐겨찾기 추가'">{{ isFavorite ? '★' : '☆' }}</button><button class="btn conf-check" @click="checkLatest" :disabled="checking || reading" aria-label="최신 여부 확인"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16 8a6 6 0 10-1 6M16 3v5h-5"/></svg><span>{{ checking ? '확인 중…' : '최신 여부 확인' }}</span></button><a :href="doc.url" target="_blank" rel="noopener noreferrer" class="btn conf-external" aria-label="Confluence에서 열기" title="Confluence에서 열기"><span>원문</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M11 3h6v6M17 3l-8 8M8 4H4v12h12v-4"/></svg></a><button v-if="headings.length" class="btn conf-toc-toggle" @click="toggleToc" :aria-expanded="showToc" aria-controls="conf-document-toc"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5h12M4 10h12M4 15h8"/></svg>목차</button></div>
        </header>
        <ConfluenceReadNotice :error="readError ? (readFailure || readError) : null" :loading="reading" :slow="readSlow" :retrying="readRetrying" :cached="!!doc"
          :url="doc?.url || (selectedId ? base + '/pages/viewpage.action?pageId=' + selectedId : '')" @retry="read(selectedId,requestedVersion)" />
        <p v-if="revisionError" class="conf-error" role="alert">{{ revisionError }}</p>
        <div v-if="newer" class="conf-new-version" role="status">새 발행 버전 v{{ revision.latestPublishedVersion }}이 있습니다.<button class="btn" @click="read(doc.id, revision.latestPublishedVersion)">새 버전 보기</button></div>
        <p v-else-if="revision?.isLatest" class="conf-current" role="status">확인 시점의 최신 발행 버전입니다. {{ date(revision.checkedAt) }}</p>
        <div ref="reader" class="conf-reader" tabindex="0" aria-label="문서 본문" :aria-busy="reading" @scroll.passive="rememberTabScroll">
          <p v-if="doc?.macroWarnings?.length" class="conf-muted">일부 매크로는 원문 확인이 필요합니다. <button class="conf-more" @click="read(doc.id,null,true)" :disabled="reading">본문 다시 불러오기</button></p>
          <p v-if="reading" class="conf-muted" role="status">문서를 불러오는 중…</p>
          <article v-if="doc" class="conf-document"><nav v-if="doc.ancestors?.length" class="conf-breadcrumb" aria-label="문서 경로"><button v-for="item in doc.ancestors" :key="item.id" @click="open(item.id)">{{ item.title }}</button></nav><h1>{{ doc.title }}</h1><p class="conf-document-meta">v{{ version }} <span v-if="doc.version?.when">{{ date(doc.version.when) }} 수정</span><span>{{ author() }}</span><span v-if="checkedAt">최신 확인 {{ date(checkedAt) }}</span></p><div ref="documentBody" class="conf-document-body" v-html="doc.html" @click="linkClick"></div></article>
          <div v-else-if="!reading && !readError" class="conf-empty"><h2>프로젝트 문서를 한곳에서</h2><p>왼쪽 페이지 트리에서 문서를 선택하세요.<br>자주 보는 문서는 즐겨찾기에 고정할 수 있습니다.</p></div>
        </div>
      </main>
      <div v-if="(composing || headings.length) && showToc && !compact" class="conf-resizer" role="separator" tabindex="0" aria-orientation="vertical" aria-label="목차 너비 조절" :aria-valuenow="tocWidth" aria-valuemin="180" :aria-valuemax="panelMax('toc')" @pointerdown="startPanelResize('toc', $event)" @keydown="resizePanelKey('toc', $event)"></div>
      <ConfluencePanel v-if="composing || headings.length" :visible="showToc" :compact="compact" label="이 문서의 목차" @close="toggleToc" id="conf-document-toc" class="conf-panel conf-toc"><div class="conf-panel-title"><h2>이 문서의 목차</h2><button class="conf-fold" @click="toggleToc" aria-label="목차 접기" title="목차 접기"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5l5 5-5 5"/></svg></button></div><nav aria-label="본문 목차"><p v-if="!headings.length" class="conf-muted">본문에 헤딩을 추가하면 여기에 표시됩니다.</p><button v-for="heading in headings" :key="heading.id" :style="{'--level': heading.level - headingDepth}" :aria-current="activeHeading === heading.id ? 'location' : null" @click="jumpToHeading(heading.id)">{{ heading.title }}</button></nav></ConfluencePanel>
    </section></section>`,
};
