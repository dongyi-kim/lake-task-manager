import { api } from './api.js';
import { pageTree, replaceTreeBranch } from './confluenceTreeModel.js';

export default {
  data: () => ({ savedTreePages: {}, savedTreePending: {}, savedTreeErrors: {} }),
  computed: {
    favoriteIds() { return new Set(this.favorites.map(row => String(row.id))); },
    savedTreeNodes() {
      const pages = [...this.treePages, ...Object.values(this.savedTreePages).flat()];
      const known = new Set(pages.map(row => String(row.id)));
      // Unknown saved items retain their title without pretending to be known leaves.
      pages.push(...[...this.favorites, ...this.recentDocuments].filter(row => !known.has(String(row.id))));
      return pageTree(pages).nodes;
    },
  },
  methods: {
    async loadSavedTrees() {
      const spaces = [...new Set([...this.favorites, ...this.recentDocuments].map(row => row.space).filter(Boolean))];
      await Promise.allSettled(spaces.map(async space => {
        const revision = this._savedRevision?.[space] || 0;
        const data = await api.confluenceTreeSnapshot(space); // local SQLite only
        if (revision === (this._savedRevision?.[space] || 0)) this.savedTreePages[space] = data.batches.flatMap(batch => batch.results);
      }));
    },
    async expandSavedTree(row) {
      if (this.savedTreePending[row.id]) return;
      const space = row.space?.key || row.space || this.space;
      const id = String(row.id);
      this._savedBranches = this._savedBranches || {};
      const versions = this._savedBranches[space] ||= new Map();
      const known = (this.savedTreePages[space] || []).find(item => String(item.id) === id);
      const ancestry = row.ancestors || known?.ancestors || [];
      const observed = ancestry.map(parent => [String(parent.id), versions.get(String(parent.id))]);
      const current = () => observed.every(([parent, version]) => versions.get(parent) === version);
      this._savedRevision = this._savedRevision || {};
      this._savedRevision[space] = (this._savedRevision[space] || 0) + 1;
      this.savedTreePending[row.id] = true; this.savedTreeErrors[row.id] = false;
      const rows = [];
      try {
        let start = 0;
        do {
          const batch = await api.confluenceTreeBranch(space, {parent:row.id, start, background:false});
          if (!current()) return; // A newer ancestor replacement owns this subtree.
          rows.push(...batch.results); start = batch.nextStart;
        } while (start !== null);
        versions.set(id, (versions.get(id) || 0) + 1);
        this.savedTreePages[space] = replaceTreeBranch(this.savedTreePages[space] || [], row, rows);
        if (space === this.space) {
          if (this._treeQueue) this._treeQueue.acceptBranch(row, rows);
          else this.treePages = replaceTreeBranch(this.treePages, row, rows);
        }
      } catch (error) {
        if (!current()) return;
        this.savedTreeErrors[row.id] = true;
        if ([403,404].includes(error.status)) {
          versions.set(id, (versions.get(id) || 0) + 1);
          this.savedTreePages[space] = (this.savedTreePages[space] || []).filter(item => String(item.id) !== id &&
            !(item.ancestors || []).some(parent => String(parent.id) === id));
          if (space === this.space) this.loadTree(true);
        }
      }
      finally { this.savedTreePending[row.id] = false; }
    },
  },
};
