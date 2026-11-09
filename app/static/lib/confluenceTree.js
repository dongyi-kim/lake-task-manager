import { api } from './api.js';
import { pageTree } from './confluenceTreeModel.js';
import { ConfluenceTreeQueue } from './confluenceTreeQueue.js';

export default {
  data: () => ({ query: '', treePages: [], treeLoading: false, treeError: '',
    expanded: {}, searchOverrides: {}, activeMatch: '', treePending: '' }),
  computed: {
    tree() { return pageTree(this.treePages, this.expanded, this.query, this.searchOverrides); },
    matchPosition() { return this.tree.matches.indexOf(this.activeMatch) + 1; },
  },
  watch: {
    query() { this.searchOverrides = {}; this.activeMatch = ''; this.selectTreeMatch();
      this._treeQueue?.search(this.query ? this.tree.matches.filter(id => this.tree.nodes.get(id)?.hasChildren !== false) : null); },
    'tree.matches'() { this.selectTreeMatch(); },
    favorites: { deep: true, handler() { this._treeQueue?.setFavorites(this.treeFavoriteIds()); } },
  },
  activated() { if (this.space && !this._treeQueue) this.loadTree(); },
  deactivated() { this._treeQueue?.stop(); this._treeQueue = null; this.treeLoading = false; },
  beforeUnmount() { this._treeQueue?.stop(); },
  methods: {
    treeFavoriteIds() {
      return (this.favorites || []).filter(row => String(row.space?.key || row.space).toLowerCase() === String(this.space).toLowerCase()).map(row => String(row.id));
    },
    async loadTree(reset = false) {
      if (!this.space) return;
      if (!reset && this._treeQueue?.errors.size) { this._treeQueue.retry(); return; }
      const space = this.space;
      this._treeQueue?.stop();
      if (this._treeSpace !== space) { this.treePages = []; this.expanded = {}; this.searchOverrides = {}; }
      this._treeSpace = space; this.treeError = ''; this.treeLoading = true;
      const queue = new ConfluenceTreeQueue({
        snapshot: () => api.confluenceTreeSnapshot(space),
        read: job => api.confluenceTreeBranch(space, job),
        bulkRead: (start, background) => api.confluenceTree(space, start, true, background),
        favoriteRead: (id, start) => api.confluenceTreeFavorite(space, id, start),
        favorites: this.treeFavoriteIds(),
        changed: rows => { if (this._treeQueue === queue) this.treePages = rows; },
        state: ({ loading, pending, errors }) => { if (this._treeQueue !== queue) return;
          this.treeLoading = loading; this.treePending = pending;
          this.treeError = errors.length ? `일부 하위 문서를 불러오지 못했습니다. (${errors.length}개 영역)` : ''; },
      });
      this._treeQueue = queue;
      await queue.start();
    },
    prioritizeTree(id) { if (this.tree.nodes.get(String(id))?.hasChildren !== false) this._treeQueue?.prioritize(id); },
    toggle(row) {
      if (this.tree.searching) this.searchOverrides[row.id] = !row.open;
      else this.expanded[row.id] = !row.open;
      if (!row.open) this.prioritizeTree(row.id);
    },
    clearTreeSearch() { this.query = ''; this.searchOverrides = {}; },
    selectTreeMatch() {
      if (this.tree.matches.includes(this.activeMatch)) return;
      this.activeMatch = this.tree.matches[0] || '';
      if (this.activeMatch) this.$nextTick(() => this.scrollTreeMatch());
    },
    moveTreeMatch(direction = 1) {
      const matches = this.tree.matches;
      if (!matches.length) return;
      this.activeMatch = matches[(matches.indexOf(this.activeMatch) + direction + matches.length) % matches.length];
      let parent = this.tree.nodes.get(this.activeMatch)?.parent;
      const visited = new Set();
      while (parent && !visited.has(parent)) {
        visited.add(parent); this.searchOverrides[parent] = true; parent = this.tree.nodes.get(parent)?.parent;
      }
      if (this.tree.nodes.get(this.activeMatch)?.hasChildren === true) this.prioritizeTree(this.activeMatch);
      this.$nextTick(() => this.scrollTreeMatch());
    },
    scrollTreeMatch() {
      const row = [...this.$el.querySelectorAll('.conf-tree-row[data-page-id]')].find(el => el.dataset.pageId === this.activeMatch);
      row?.scrollIntoView({ block: 'nearest' });
    },
  },
};
