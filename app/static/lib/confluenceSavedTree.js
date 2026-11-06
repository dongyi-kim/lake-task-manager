import { api } from './api.js';
import { pageTree } from './confluenceTreeModel.js';

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
        const data = await api.confluenceTreeSnapshot(space); // local SQLite only
        this.savedTreePages[space] = data.batches.flatMap(batch => batch.results);
      }));
    },
    async expandSavedTree(row) {
      if (this.savedTreePending[row.id]) return;
      const space = row.space?.key || row.space || this.space;
      this.savedTreePending[row.id] = true; this.savedTreeErrors[row.id] = false;
      const rows = [];
      try {
        let start = 0;
        do {
          const batch = await api.confluenceTreeBranch(space, {parent:row.id, start, background:false});
          rows.push(...batch.results); start = batch.nextStart;
        } while (start !== null);
        const old = (this.savedTreePages[space] || []).filter(item => String(item.ancestors?.at(-1)?.id || '') !== row.id && String(item.id) !== row.id);
        this.savedTreePages[space] = [...old, {...row, hasChildren:!!rows.length, unknownChildren:false}, ...rows];
        // Current tree uses its own queue, backed by exactly the same branch cache.
        if (space === this.space) this.prioritizeTree(row.id);
      } catch (_) { this.savedTreeErrors[row.id] = true; }
      finally { this.savedTreePending[row.id] = false; }
    },
  },
};
