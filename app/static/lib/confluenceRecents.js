// Shared SQLite history + immediate browser mirror. No Confluence/auth reads.
import { api } from './api.js';
import { hydrateRecent, recentItems, recentRevision, recordOpen } from './recent.js';
import { confluencePageId, readRecentDocuments } from './confluenceNavigation.js';

export default {
  computed: {
    recentMenu() { return this.recentDocuments.slice(0, 10); },
    recentDocumentIds() { return new Set(this.recentDocuments.map(row => String(row.id))); },
  },
  mounted() {
    this._recentChanged = () => this.syncRecentDocuments();
    this._recentStorage = event => { if (event.key === 'recent.items') this.syncRecentDocuments(); };
    this._recentFocus = () => { if (this.base && !document.hidden) this.loadRecentDocuments(false); };
    window.addEventListener('recent-changed', this._recentChanged);
    window.addEventListener('storage', this._recentStorage);
    window.addEventListener('focus', this._recentFocus);
  },
  activated() { if (this.base) this.loadRecentDocuments(false); },
  beforeUnmount() {
    window.removeEventListener('recent-changed', this._recentChanged);
    window.removeEventListener('storage', this._recentStorage);
    window.removeEventListener('focus', this._recentFocus);
  },
  methods: {
    syncRecentDocuments() {
      if (!this.base) return;
      this.recentDocuments = recentItems(100, 'confluence')
        .filter(row => row.url.startsWith(this.base.replace(/\/+$/, '') + '/'))
        .map(row => ({id:confluencePageId(row.pageId || row.id || row.url), title:row.title,
          space:String(row.space?.key || row.space || ''), openedAt:row.openedAt}))
        .filter(row => row.id && (!row.space || this._recentSpaces?.has(row.space.toLowerCase())));
    },
    async loadRecentDocuments(migrate = true) {
      const generation = this._recentLoadGeneration = (this._recentLoadGeneration || 0) + 1;
      this.syncRecentDocuments();
      const base = this.base;
      const legacy = migrate ? readRecentDocuments(base) : [];
      if (legacy.length) {
        const items = legacy.map((row, index) => ({url:base + '/pages/viewpage.action?pageId=' + row.id,
          kind:'confluence', title:row.title, openedAt:(Number(row.openedAt) || Date.now() - index) / 1000,
          data:{pageId:String(row.id), space:row.space, confluenceBase:base}}));
        hydrateRecent(items); // Display existing history before writing its migration.
        const saved = await Promise.allSettled(items.map(item => api.recentAdd(item)));
        if (saved.every(row => row.status === 'fulfilled')) {
          try { localStorage.removeItem('ltm.confluence.recent:' + base); } catch (_) { /* idempotent next migration */ }
        } else return; // Keep the legacy source if local persistence failed.
      }
      const started = recentRevision();
      try {
        const rows = await api.recent(100, 'confluence');
        if (generation === this._recentLoadGeneration && base === this.base && started === recentRevision()) hydrateRecent(rows, 'confluence');
      } catch (_) { /* already known titles remain visible offline */ }
    },
    rememberDocument(doc) {
      recordOpen({url:this.base + '/pages/viewpage.action?pageId=' + doc.id, kind:'confluence', title:doc.title,
        meta:doc.space?.key || '', data:{pageId:String(doc.id), space:doc.space?.key || '', confluenceBase:this.base}})
        ?.then(saved => { if (saved) this.loadRecentDocuments(false); });
    },
  },
};
