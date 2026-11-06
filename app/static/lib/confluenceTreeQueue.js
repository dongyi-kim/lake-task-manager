// One metadata request at a time. Interactions promote queued branches without flooding SSO.
export class ConfluenceTreeQueue {
  constructor({ snapshot, read, bulkRead, favoriteRead, favorites = [], changed, state }) {
    Object.assign(this, { snapshot, read, bulkRead, favoriteRead, favorites, changed, state });
    this.branches = new Map(); this.queue = new Map(); this.done = new Set();
    this.working = new Map(); this.errors = new Map(); this.sequence = 0; this.stopped = false;
    this.indexRows = new Map(); this.bulkStarted = false; this.denied = new Set();
    this.focusRows = new Map(); this.focusDone = new Set(); this.urgentStreak = 0;
  }
  stop() { this.stopped = true; }
  enqueue(job) {
    const key = job.parent + ':' + job.start;
    if (!this.queue.has(key) && (!this.done.has(key) || job.refresh)) {
      this.queue.set(key, { rank: job.parent ? 1 : -2, order: this.sequence++, ...job });
    }
    return this.queue.get(key);
  }
  publish() {
    if (this.stopped) return;
    const rows = new Map([...this.focusRows].filter(([id, row]) => !this.denied.has(id)
      && !(row.ancestors || []).some(a => this.denied.has(String(a.id)))));
    const parents = ['', ...rows.keys()], seen = new Set();
    for (const parent of parents) {
      if (seen.has(parent) || this.denied.has(parent)) continue;
      seen.add(parent);
      for (const batch of this.branches.get(parent)?.values() || []) for (const row of batch.results) {
        const id = String(row.id);
        if (!this.denied.has(id)) { rows.set(id, row); parents.push(id); }
      }
    }
    for (const [id, row] of this.indexRows) if (!this.denied.has(id) &&
      !(row.ancestors || []).some(a => this.denied.has(String(a.id)))) rows.set(id, row);
    this.changed([...rows.values()].map(row => {
      const pages = this.branches.get(String(row.id));
      const complete = pages && [...pages.values()].some(batch => batch.nextStart === null);
      return complete ? { ...row, hasChildren: [...pages.values()].some(batch => batch.results.length > 0) } : row;
    }));
    this.state({ loading: !this.paused && (!!this.running || !!this.queue.size), pending: this.active?.parent || '', errors: [...this.errors.values()] });
  }
  async start() {
    this.running = true;
    try {
      const data = await this.snapshot();
      if (this.stopped) return;
      this.cold = !data.batches.length;
      for (const batch of data.batches) {
        const pages = this.branches.get(batch.parent) || new Map();
        pages.set(batch.start, batch); this.branches.set(batch.parent, pages);
        this.done.add(batch.parent + ':' + batch.start);
      }
      for (const job of data.jobs) this.enqueue(job);
      this.complete = !!data.batches.length && !data.jobs.length;
      this.ready = true; this.setFavorites(this.favorites);
      this.publish();
    } catch (error) {
      this.errors.set('snapshot', error); this.enqueue({ parent: '', start: 0, depth: 0 });
      this.ready = true; this.cold = true; this.setFavorites(this.favorites);
    } finally { this.running = false; }
    await this.run();
  }
  prioritize(id) {
    if (String(id) === this.active?.parent) return;
    if ([...this.indexRows.values()].some(row => String(row.ancestors?.at(-1)?.id) === String(id))) return;
    const parent = String(id), job = this.enqueue({ parent, start: 0, depth: 0 });
    if (job) job.rank = -2;
    for (const item of this.queue.values()) if (item.parent === parent) item.rank = -2;
    this.run();
  }
  search(ids) {
    // Typing changes local matches only. Do not restart/cancel the index or turn
    // every matching folder into another upstream request. Selected rows can promote work.
    this.searching = ids !== null;
    this.run();
  }
  subtreeReady(id, seen = new Set()) {
    if (seen.has(id)) return false;
    seen.add(id);
    const pages = this.branches.get(id);
    if (!pages) {
      return [...this.branches.values()].some(batches => [...batches.values()].some(batch =>
        batch.cacheInfo?.state !== 'stale' && batch.results.some(row => String(row.id) === id && row.hasChildren === false)));
    }
    let start = 0;
    do {
      const batch = pages.get(start);
      if (!batch || batch.cacheInfo?.state === 'stale') return false;
      for (const row of batch.results) if (row.hasChildren !== false && !this.subtreeReady(String(row.id), seen)) return false;
      if (batch.nextStart !== null && batch.nextStart <= start) return false;
      start = batch.nextStart;
    } while (start !== null);
    return true;
  }
  setFavorites(favorites) {
    this.favorites = [...new Set(favorites.map(String))];
    for (const [key, job] of this.queue) if (job.favoriteIds && !job.favoriteIds.some(id => this.favorites.includes(id))) this.queue.delete(key);
    if (!this.ready || this.complete || !this.favoriteRead) return;
    const pending = new Set([...(this.active?.favoriteIds || []), ...[...this.queue.values()].flatMap(job => job.favoriteIds || [])]);
    const missing = this.favorites.filter(id => !this.focusDone.has(id)
      && !this.focusRows.get(id)?.ancestors?.some(a => this.focusDone.has(String(a.id)))
      && !this.subtreeReady(id) && !pending.has(id));
    for (let offset = 0; offset < missing.length; offset += 20) {
      const ids = missing.slice(offset, offset + 20).sort();
      this.enqueue({parent:'@favorite:' + ids.join(','), favoriteIds:ids, start:0, depth:0, rank:-1});
    }
    this.run();
  }
  applyFavorite(job, batch) {
    for (const row of batch.results) this.focusRows.set(String(row.id), row);
    if (batch.nextStart !== null) {
      if (job.favoriteIds.some(id => this.favorites.includes(id))) this.enqueue({...job, start:batch.nextStart, attempts:0});
    }
    else {
      for (const id of job.favoriteIds) this.focusDone.add(id);
      // A completed ancestor query already covers nested favorites. Do not fetch
      // the same subtree again; CQL rows still never assert authoritative leafness.
      for (const id of this.favorites) {
        if (this.focusRows.get(id)?.ancestors?.some(a => job.favoriteIds.includes(String(a.id)))) {
          this.focusDone.add(id);
          for (const [key, item] of this.queue) if (item.favoriteIds?.every(root => this.focusDone.has(root))) this.queue.delete(key);
        }
      }
    }
  }
  finishIndex() {
    for (const row of [...this.indexRows.values()]) for (const [index, ancestor] of (row.ancestors || []).entries()) {
      const id = String(ancestor.id);
      if (!this.indexRows.has(id)) this.indexRows.set(id, {...ancestor, id, space:row.space, ancestors:row.ancestors.slice(0,index)});
    }
    const groups = new Map([['', []]]);
    for (const row of this.indexRows.values()) groups.set(String(row.id), []);
    for (const row of this.indexRows.values()) {
      const parent = String(row.ancestors?.at(-1)?.id || '');
      if (!groups.has(parent)) groups.set(parent, []);
      groups.get(parent).push(row);
    }
    this.branches.clear(); this.queue.clear(); this.errors.clear(); this.focusRows.clear(); this.complete = true;
    for (const [parent, rows] of groups) {
      const results = rows.map(row => ({ ...row, hasChildren: !!groups.get(String(row.id))?.length }));
      this.branches.set(parent, new Map([[0, { parent, start: 0, results, nextStart: null }]]));
      this.done.add(parent + ':0');
    }
    this.indexRows.clear();
  }
  retry() {
    this.paused = false;
    for (const error of this.errors.values()) if (error.job) this.enqueue({ ...error.job, attempts: 0 });
    this.errors.clear(); this.run();
  }
  async run() {
    if (this.running || this.stopped || this.paused) return;
    this.running = true;
    while (this.queue.size && !this.stopped && !this.paused) {
      // Broad trees/search favour one bulk metadata pass over one round-trip per parent.
      // Explicit user branches still outrank the pass; small trees stay breadth-first.
      const coldDescendants = this.cold && this.done.has(':0') && ![...this.queue.values()].some(job => !job.parent);
      if (this.bulkRead && !this.bulkStarted && (coldDescendants || this.queue.size >= 8 || this.searching && this.queue.size > 1)) {
        this.bulkStarted = true;
        this.enqueue({ parent: '@index', start: 0, depth: 0, rank: 0 });
      }
      const jobs = [...this.queue.entries()].sort(([, a], [, b]) => a.rank - b.rank || a.depth - b.depth || a.order - b.order);
      // At most two foreground/favorite batches before one whole-index batch.
      // Continuous clicks/search must never starve eventual Space completeness.
      const background = jobs.find(([, job]) => job.parent === '@index') || jobs.find(([, job]) => job.rank >= 0);
      const [key, job] = this.urgentStreak >= 2 && background ? background : jobs[0];
      this.urgentStreak = job.rank < 0 ? this.urgentStreak + 1 : 0;
      this.queue.delete(key); this.active = job; this.publish();
      try {
        const index = job.parent === '@index';
        const batch = job.favoriteIds ? await this.favoriteRead(job.favoriteIds, job.start)
          : index ? await this.bulkRead(job.start, true)
          : await this.read({ ...job, background: job.rank > 0 });
        if (this.stopped) return;
        if (!Array.isArray(batch.results) || batch.nextStart !== null && (!Number.isInteger(batch.nextStart) || batch.nextStart <= job.start)) {
          throw new Error('문서 목록의 다음 위치를 확인하지 못했습니다.');
        }
        this.done.add(key); this.errors.delete(key); this.errors.delete('snapshot');
        if (job.favoriteIds) { this.applyFavorite(job, batch); this.publish(); continue; }
        if (index) {
          for (const row of batch.results) this.indexRows.set(String(row.id), row);
          // The in-flight bulk pass already revealed these children. Let that
          // pass finish coverage instead of issuing a duplicate user-branch read.
          const covered = new Set([...this.indexRows.values()].map(row => String(row.ancestors?.at(-1)?.id || '')));
          for (const [queuedKey, queued] of this.queue) if (queued.rank < 0 && covered.has(queued.parent)) this.queue.delete(queuedKey);
          if (batch.nextStart === null) this.finishIndex();
          else this.enqueue({ ...job, start: batch.nextStart, attempts: 0 });
          this.publish(); continue;
        }
        let pages = this.working.get(job.parent);
        if (!pages || job.start === 0) pages = new Map(job.start > 0 ? this.branches.get(job.parent) : []);
        pages.set(job.start, batch); this.working.set(job.parent, pages);
        // Keep an old complete branch until every page of its replacement arrives.
        if (!job.refresh || batch.nextStart === null) this.branches.set(job.parent, new Map(pages));
        if (batch.nextStart !== null) this.enqueue({ ...job, start: batch.nextStart, attempts: 0 });
        else this.working.delete(job.parent);
        for (const row of batch.results) if (row.hasChildren !== false) {
          this.enqueue({ parent: String(row.id), start: 0, depth: job.depth + 1 });
        }
      } catch (error) {
        if (this.stopped) return;
        if (![401, 403, 404].includes(error.status) && (job.attempts || 0) < 1) {
          this.enqueue({ ...job, attempts: (job.attempts || 0) + 1, rank: 2 });
        } else if ([403, 404].includes(error.status)) {
          if (job.favoriteIds) continue; // Search permission may differ from page read access; full tree still proceeds.
          if (!job.parent || job.parent === '@index') { this.branches.clear(); this.indexRows.clear(); this.focusRows.clear(); this.queue.clear(); }
          else {
            this.denied.add(job.parent);
            for (const [queuedKey, queued] of this.queue) if (queued.parent === job.parent) this.queue.delete(queuedKey);
          }
        } else { error.job = job; this.errors.set(key, error); if (error.status === 401) this.paused = true; }
      }
      this.publish();
    }
    this.active = null; this.running = false; this.publish();
  }
}
