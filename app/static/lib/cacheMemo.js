// Bounded display cache. Source timestamps come from the server, never from a cache hit.
function retainedBytes(value, limit) {
  // Count conservatively without allocating a second full JSON string. Also
  // bound traversal work for tiny-object-heavy payloads.
  const stack = [value], seen = new WeakSet();
  let bytes = 0, nodes = 0;
  while (stack.length && bytes <= limit && ++nodes <= 100000) {
    const item = stack.pop();
    if (typeof item === 'string') bytes += 2 * item.length + 32;
    else if (item && typeof item === 'object' && !seen.has(item)) {
      seen.add(item); bytes += 64;
      for (const key in item) {
        if (++nodes > 100000) return limit + 1;
        bytes += 2 * key.length + 16; stack.push(item[key]);
      }
    } else bytes += 8;
  }
  return stack.length || nodes > 100000 ? limit + 1 : bytes;
}
export class CacheMemo {
  constructor(fetcher, publish = () => {}, now = () => Date.now() / 1000,
    limits = { entries: 300, bytes: 32 * 1024 * 1024, entryBytes: 4 * 1024 * 1024 }) {
    this.fetcher = fetcher; this.publish = publish; this.now = now; this.rows = new Map(); this.nextRevision = 0;
    this.limits = limits;
    this.policy = { displaySeconds: 86400, freshnessSeconds: 600,
      resolvedFreshnessSeconds: 86400, resolvedCommentsFreshnessSeconds: 3600,
      metadataDisplaySeconds: 2592000, metadataFreshnessSeconds: 86400 };
  }
  get(path, opts) {
    let row = this.rows.get(path);
    if (opts?.freshAfter && (!row || !row.sourceFetchedAt || row.sourceFetchedAt * 1000 < opts.freshAfter)) {
      if (!row) { row = { revision: ++this.nextRevision }; this.rows.set(path, row); }
      return this.fetch(path, row, opts, true);
    }
    if (row && row.value !== undefined && row.displayUntil >= this.now()) {
      this.rows.delete(path); this.rows.set(path, row);
      if (row.freshUntil < this.now()) this.fetch(path, row, opts, true).catch(() => {});
      return Promise.resolve(row.value);
    }
    if (!row) { row = { revision: ++this.nextRevision }; this.rows.set(path, row); }
    return this.fetch(path, row, opts, false);
  }
  fetch(path, row, opts, revalidate) {
    if (row.pending) return row.pending;
    let metadata, needsRefresh = false;
    const { freshAfter, ...requestOptions } = opts || {};
    const url = path + (revalidate ? (path.includes('?') ? '&' : '?') + 'revalidate=1' : '')
      + (freshAfter ? (path.includes('?') || revalidate ? '&' : '?') + 'freshAfter=' + encodeURIComponent(freshAfter) : '');
    const pending = this.fetcher(url, { ...requestOptions, onCacheMetadata: (m) => { metadata = m; } })
      .then((value) => {
        if (this.rows.get(path) !== row) return value; // evicted by a mutation/navigation
        if (value?.complete === false && Array.isArray(value.comments)) {
          if (row.value === undefined) this.rows.delete(path);
          return value; // a failed coverage refresh must not erase a prior complete display snapshot
        }
        if (value && (value.error || value.partial || value.pending)) {
          if (row.value === undefined) this.rows.delete(path);
          return value;
        }
        const now = this.now();
        row.value = value;
        row.fetchedAt = metadata?.fetchedAt ?? now;
        row.sourceFetchedAt = metadata?.fetchedAt ?? null; // browser fallback time is not source evidence
        row.commentsComplete = metadata?.commentsComplete;
        row.commentsTotal = metadata?.commentsTotal;
        row.invalidated = metadata?.freshUntil === 0;
        row.freshUntil = metadata?.freshUntil ?? (now + this.policy.freshnessSeconds);
        row.displayUntil = metadata?.displayUntil ?? (now + this.policy.displaySeconds);
        row.displayKind = metadata?.displayKind;
        if (revalidate) this.publish({ path, value });
        row.bytes = retainedBytes(value, this.limits.entryBytes);
        // A first request can itself be a server SWR hit; update this open view as well.
        needsRefresh = !revalidate && row.freshUntil < now;
        return value;
      }).catch((error) => {
        if (this.rows.get(path) === row && (row.value === undefined || [403, 404].includes(error.status))) {
          this.rows.delete(path);
          if ([403, 404].includes(error.status)) this.publish({ path, value: null, removed: true });
        }
        throw error;
      }).finally(() => {
        if (row.pending === pending) row.pending = null;
        this.trim();
        if (needsRefresh && this.rows.get(path) === row) queueMicrotask(() => this.fetch(path, row, opts, true).catch(() => {}));
      });
    row.pending = pending;
    return pending;
  }
  trim() {
    // This releases browser RAM only. Persistent server snapshots keep their
    // cold retention and can still render before upstream revalidation.
    let bytes = 0;
    for (const [key, row] of this.rows) {
      if (!row.pending && row.bytes > this.limits.entryBytes) this.rows.delete(key);
      else bytes += row.bytes || 0;
    }
    for (const [key, row] of this.rows) {
      if (bytes <= this.limits.bytes && this.rows.size <= this.limits.entries) break;
      if (row.pending) continue;
      this.rows.delete(key); bytes -= row.bytes || 0;
    }
  }
  configure(policy) {
    this.policy = policy;
    for (const row of this.rows.values()) {
      if (!row.fetchedAt) continue;
      // A response can combine fresh tickets and old-but-retained Epic metadata. Never derive
      // a new lifetime for that composition from a single age or extend the server's deadline.
      row.freshUntil = 0;
      if (row.displayKind) {
        const ttl = row.displayKind === 'metadata' ? policy.metadataDisplaySeconds : policy.displaySeconds;
        row.displayUntil = Math.min(row.displayUntil, row.fetchedAt + ttl);
      }
    }
  }
  peek(path) {
    const row = this.rows.get(path);
    return row && row.displayUntil >= this.now() ? row.value : undefined;
  }
  metadata(path) {
    const row = this.rows.get(path);
    return row && row.value !== undefined ? { fetchedAt: row.sourceFetchedAt,
      commentsComplete: row.commentsComplete, commentsTotal: row.commentsTotal,
      freshUntil: row.freshUntil, displayUntil: row.displayUntil } : undefined;
  }
  keys() { return this.rows.keys(); }
  revision(path) { return this.rows.get(path)?.revision || 0; }
  invalidate(path) {
    const row = this.rows.get(path);
    if (!row) return;
    // Replace the request identity, not its display value or source age. A response
    // started before the mutation must not overwrite the next successful refresh.
    this.rows.set(path, { ...row, freshUntil: 0, invalidated: true,
      pending: null, revision: ++this.nextRevision });
  }
  invalidateAll() { for (const path of this.rows.keys()) this.invalidate(path); }
  replace(path, value) {
    const now = this.now();
    // A confirmed mutation supplies new display data, not proof of the source age
    // required by freshness-sensitive reads. Fence any earlier pending request.
    this.rows.set(path, { value, fetchedAt: now, sourceFetchedAt: null,
      freshUntil: 0, displayUntil: now + this.policy.displaySeconds,
      invalidated: true, revision: ++this.nextRevision, pending: null,
      bytes: retainedBytes(value, this.limits.entryBytes) });
    this.publish({ path, value });
    this.trim();
  }
  delete(path) { this.rows.delete(path); }
  clear() { this.rows.clear(); }
}
