"""Browser admission and display-cache ownership with Node and deferred local I/O."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


STATIC_ROOT = REPO_ROOT / "app/static"

SCENARIOS = {
    "read_flood_and_cancellation": r"""
      const pool = new RequestPool();
      const gates = Array.from({length: 38}, deferred);
      const controllers = Array.from({length: 38}, () => new AbortController());
      const fetched = [], finished = [];
      async function request(index) {
        const release = await pool.acquire(controllers[index].signal);
        try {
          fetched.push(index);
          return await gates[index].promise;
        } finally {
          finished.push(index);
          release(); release(); // Cleanup can be called twice without transferring two permits.
        }
      }
      const pending = Array.from({length: 38}, (_, index) => request(index));
      const outcomes = Promise.allSettled(pending); // Observe queued aborts immediately.
      await turn();
      assert.equal(pool.active, 6);
      assert.equal(pool.queue.length, 32);
      assert.deepEqual(fetched, [0, 1, 2, 3, 4, 5]);
      await assert.rejects(pool.acquire(), error => error.status === 429
        && error.code === 'browser_capacity' && error.executed === false && error.retryable === true);
      controllers[6].abort(); controllers[7].abort();
      await turn();
      assert.equal(pool.queue.length, 30);
      assert.equal(pool.active, 6);
      assert(!fetched.includes(6) && !fetched.includes(7));
      const alreadyAborted = new AbortController(); alreadyAborted.abort();
      await assert.rejects(pool.acquire(alreadyAborted.signal), {name: 'AbortError'});
      assert.equal(pool.queue.length, 30);

      // An active caller's abort does not complete a non-cooperative owner.
      // Capacity stays owned until its real I/O finally reaches cleanup.
      controllers[0].abort();
      await turn();
      assert.equal(pool.active, 6);
      assert.equal(pool.queue.length, 30);
      assert.equal(fetched.length, 6);
      assert(!finished.includes(0));
      gates[0].resolve('finished after abort');
      await turn();
      assert.equal(pool.active, 6);
      assert.equal(pool.queue.length, 29);
      assert.equal(fetched.at(-1), 8);
      for (const gate of gates) gate.resolve('done');
      const settled = await outcomes;
      assert.equal(settled.filter(row => row.status === 'rejected').length, 2);
      assert.equal(fetched.length, 36);
      assert(!fetched.includes(6) && !fetched.includes(7));
      assert.equal(pool.active, 0);
      assert.equal(pool.queue.length, 0);
      const release = await pool.acquire();
      assert.equal(pool.active, 1);
      release(); release();
      assert.equal(pool.active, 0);
    """,
    "write_lane_is_independent_and_never_queued": r"""
      const reads = new RequestPool(6, 32), writes = new RequestPool(2, 0);
      const readOwners = await Promise.all(Array.from({length: 6}, () => reads.acquire()));
      const writeOwners = await Promise.all([writes.acquire(), writes.acquire()]);
      const controller = new AbortController();
      await assert.rejects(writes.acquire(controller.signal), error => error.status === 429
        && error.executed === false && error.code === 'browser_capacity');
      assert.equal(writes.active, 2);
      assert.equal(writes.queue.length, 0);
      assert.equal(reads.active, 6);
      writeOwners[0]();
      const nextWrite = await writes.acquire();
      assert.equal(writes.active, 2);
      assert.equal(reads.active, 6);
      assert.equal(reads.queue.length, 0);
      writeOwners[0](); // A stale cleanup cannot free the new writer's permit.
      assert.equal(writes.active, 2);
      nextWrite(); writeOwners[1]();
      for (const release of readOwners) release();
      assert.equal(writes.active, 0);
      assert.equal(reads.active, 0);
    """,
    "memo_row_and_byte_bounds_do_not_erase_persistent_sources": r"""
      const persistent = new Map();
      const calls = [];
      let now = 1000;
      for (let index = 0; index < 20; index++) {
        persistent.set('/ticket/' + index, {key: index, body: 'small body ' + index});
      }
      const oversized = {key: 'large', body: '한글 description/comment '.repeat(1024)};
      persistent.set('/ticket/large', oversized);
      const originalPersistent = [...persistent];
      const memo = new CacheMemo(async (url, options) => {
        calls.push(url);
        options.onCacheMetadata({fetchedAt: 100, freshUntil: 1500, displayUntil: 5000});
        return persistent.get(url.split('?')[0]);
      }, () => {}, () => now, {entries: 4, bytes: 4096, entryBytes: 300});
      const bounded = () => {
        assert(memo.rows.size <= 4);
        assert([...memo.rows.values()].reduce((sum, row) => sum + (row.bytes || 0), 0) <= memo.limits.bytes);
      };
      for (let index = 0; index < 20; index++) {
        assert.equal((await memo.get('/ticket/' + index)).key, index);
        bounded();
      }
      assert.equal(memo.rows.size, 4, 'entry count independently bounds a small-row flood');
      memo.limits.bytes = 500;
      memo.trim();
      bounded();
      assert(memo.rows.size < 4, 'the byte budget also evicts below the entry-count cap');
      assert.equal(memo.peek('/ticket/0'), undefined);
      assert.strictEqual(await memo.get('/ticket/large'), oversized);
      assert.equal(memo.peek('/ticket/large'), undefined, 'large display values are delivered but not retained');
      bounded();
      assert.deepEqual([...persistent], originalPersistent, 'browser eviction cannot delete persistent server snapshots');
      // The evicted key can render again from its old, still-valid source snapshot.
      now = 1100;
      assert.equal((await memo.get('/ticket/0')).key, 0);
      assert.equal(memo.metadata('/ticket/0').fetchedAt, 100, 'reloading does not invent a new source age');
      assert.equal(memo.metadata('/ticket/0').displayUntil, 5000);
      assert.equal(calls.filter(url => url === '/ticket/0').length, 2);
      bounded();
      // Replacement uses the same memory budget and never silently truncates caller data.
      memo.replace('/ticket/replacement', oversized);
      assert.equal(memo.peek('/ticket/replacement'), undefined);
      bounded();
      memo.clear();
      assert.deepEqual([...persistent], originalPersistent);
    """,
    "memo_swr_keeps_source_age_and_mutation_fences": r"""
      let now = 1000;
      const calls = [], published = [], refresh = deferred();
      const stale = {body: 'retained source body'}, current = {body: 'new source body'};
      const memo = new CacheMemo((url, options) => {
        calls.push(url);
        if (url.includes('revalidate=1')) return refresh.promise.then(value => {
          options.onCacheMetadata({fetchedAt: 1100, freshUntil: 1700, displayUntil: 2500});
          return value;
        });
        options.onCacheMetadata({fetchedAt: 100, freshUntil: 900, displayUntil: 2000});
        return Promise.resolve(stale);
      }, event => published.push(event), () => now,
      {entries: 4, bytes: 2048, entryBytes: 1024});
      assert.strictEqual(await memo.get('/retained'), stale);
      await turn();
      assert.deepEqual(calls, ['/retained', '/retained?revalidate=1']);
      assert.strictEqual(memo.peek('/retained'), stale);
      assert.equal(memo.metadata('/retained').fetchedAt, 100);
      assert.equal(memo.metadata('/retained').displayUntil, 2000);
      now = 1100;
      assert.strictEqual(await memo.get('/retained'), stale);
      assert.equal(calls.length, 2, 'concurrent stale hits share the existing refresh');
      refresh.resolve(current);
      await turn();
      assert.strictEqual(memo.peek('/retained'), current);
      assert.equal(memo.metadata('/retained').fetchedAt, 1100);
      assert.equal(published.length, 1);

      const oldRead = deferred(), freshRead = deferred(), raceCalls = [], racePublished = [];
      const race = new CacheMemo((url, options) => {
        raceCalls.push(url);
        return (raceCalls.length === 1 ? oldRead : freshRead).promise.then(value => {
          options.onCacheMetadata({fetchedAt: 1200, freshUntil: 1800, displayUntil: 2500});
          return value;
        });
      }, event => racePublished.push(event), () => now,
      {entries: 2, bytes: 2048, entryBytes: 1024});
      const first = race.get('/race');
      assert.strictEqual(race.get('/race'), first, 'pending duplicate requests share one source read');
      race.replace('/race', {body: 'confirmed mutation'});
      const mutationRevision = race.revision('/race');
      oldRead.resolve({body: 'obsolete before mutation'});
      await first;
      assert.equal(race.peek('/race').body, 'confirmed mutation');
      assert.equal(race.revision('/race'), mutationRevision);
      assert.equal(race.metadata('/race').fetchedAt, null);
      race.invalidate('/race');
      const next = race.get('/race', {freshAfter: 1100000});
      assert.equal(raceCalls.length, 2);
      freshRead.resolve({body: 'authoritative after mutation'});
      await next;
      assert.equal(race.peek('/race').body, 'authoritative after mutation');
      assert.equal(race.metadata('/race').fetchedAt, 1200);
      assert.deepEqual(racePublished.map(event => event.value.body),
        ['confirmed mutation', 'authoritative after mutation']);
      assert(race.rows.size <= 2);
    """,
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIOS)
def test_browser_overload_without_browser_or_network(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser admission/cache tests")
    script = r"""
      import assert from 'node:assert/strict';
      const {RequestPool} = await import(POOL_PATH);
      const {CacheMemo} = await import(MEMO_PATH);
      const deferred = () => {
        let resolve;
        const promise = new Promise(done => { resolve = done; });
        return {promise, resolve};
      };
      const turn = () => new Promise(resolve => setImmediate(resolve));
    """
    script = script.replace("POOL_PATH", json.dumps((STATIC_ROOT / "lib/requestPool.js").as_uri()))
    script = script.replace("MEMO_PATH", json.dumps((STATIC_ROOT / "lib/cacheMemo.js").as_uri()))
    result = subprocess.run([node, "--input-type=module"], input=script + SCENARIOS[scenario],
                            capture_output=True, text=True, encoding="utf-8", timeout=12)
    assert result.returncode == 0, result.stderr
