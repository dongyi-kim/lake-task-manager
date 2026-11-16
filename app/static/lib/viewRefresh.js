// Keep-alive views retain their display while inactive. One refresh is queued for
// activation; events arriving during a read queue one follow-up, never a lost update.
export function retainedViewRefresh(refresh, { localChanges = true } = {}) {
  return {
    mounted() {
      this._refreshActive = true;
      this._viewRefreshListener = event => {
        const pending = this.requestViewRefresh();
        event.detail?.waitUntil?.(pending);
      };
      this._viewRefreshEvents = ['data-refresh', 'tickets-updated'];
      if (localChanges) this._viewRefreshEvents.push('ticket-changed');
      for (const type of this._viewRefreshEvents) window.addEventListener(type, this._viewRefreshListener);
    },
    activated() {
      this._refreshActive = true;
      if (this._refreshDirty) this.requestViewRefresh();
    },
    deactivated() { this._refreshActive = false; },
    beforeUnmount() {
      this._refreshActive = false;
      for (const type of this._viewRefreshEvents || []) window.removeEventListener(type, this._viewRefreshListener);
    },
    methods: {
      requestViewRefresh() {
        this._refreshDirty = true;
        if (!this._refreshActive) return Promise.resolve();
        if (this._viewRefreshPending) return this._viewRefreshPending;
        const pending = Promise.resolve().then(async () => {
          while (this._refreshDirty && this._refreshActive) {
            this._refreshDirty = false;
            await refresh.call(this);
          }
        }).finally(() => { if (this._viewRefreshPending === pending) this._viewRefreshPending = null; });
        this._viewRefreshPending = pending;
        return pending;
      },
    },
  };
}
