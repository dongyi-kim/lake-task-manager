"""Real API timeout plumbing with virtual timers; no browser or long waits."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


SCENARIOS = {
    "larger_defaults_and_caller_overrides": r"""
      globalThis.fetch = async () => json({ok: true});
      await api.raw('/ordinary');
      assert.equal(timers.at(-1).milliseconds, 240000);
      assert(timers.at(-1).cleared);
      await api.raw('/explicit', {timeoutMs: 17});
      assert.equal(timers.at(-1).milliseconds, 17, 'a caller deadline is not enlarged by the default');
      await api.ticketTimeline('DL-1', true);
      assert.equal(timers.at(-1).milliseconds, 240000);
      const model = await import(MODEL_PATH);
      assert.equal(model.TIMELINE_WAIT_MS, 240000, 'polling cannot give up before the API request deadline');
      const file = new Blob(['fixture'], {type: 'text/plain'});
      Object.defineProperty(file, 'name', {value: 'fixture.txt'});
      await api.attachmentUpload('DL-1', file, 'attachment:deadline:0001');
      assert.equal(timers.at(-1).milliseconds, 1080000);
      assert(timers.at(-1).milliseconds > (60 + 930) * 1000,
        'upload UI covers maximum backend queue + running budget with cleanup margin');
    """,
    "queue_uses_original_deadline_and_started_write_is_not_replayed": r"""
      const gates = [], fetched = [];
      globalThis.fetch = (url, options) => {
        fetched.push(url);
        const gate = deferred(); gates.push(gate);
        options.signal.addEventListener('abort', () => gate.reject(options.signal.reason), {once: true});
        return gate.promise;
      };
      const active = Array.from({length: 6}, (_, index) => api.raw('/held/' + index));
      await turn();
      assert.equal(fetched.length, 6);
      const queued = api.raw('/queued');
      const queuedTimeout = timers.at(-1);
      const refused = assert.rejects(queued, error => error.reason === 'request_timeout'
        && error.executed === false && error.retryable === true && !error.uncertain);
      await turn();
      assert.equal(fetched.length, 6, 'queued requests have a deadline before network dispatch');
      assert.equal(queuedTimeout.milliseconds, 240000);
      queuedTimeout.callback();
      await refused;
      assert(!fetched.includes('/queued'));
      gates.forEach(gate => gate.resolve(json({ok: true})));
      await Promise.all(active);
      await turn();

      const write = api.raw('/write', {method: 'POST', mutation: true, timeoutMs: 19});
      const writeTimeout = timers.at(-1);
      const uncertain = assert.rejects(write, error => error.reason === 'request_timeout'
        && error.uncertain === true && error.retryable === false);
      await turn();
      assert.equal(writeTimeout.milliseconds, 19);
      assert.equal(fetched.filter(url => url === '/write').length, 1);
      writeTimeout.callback();
      await uncertain;
      await turn();
      assert.equal(fetched.filter(url => url === '/write').length, 1, 'a timed-out started write is never replayed');
    """,
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIOS)
def test_browser_timeout_budgets_without_waiting_minutes(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser timeout tests")
    script = r"""
      import assert from 'node:assert/strict';
      globalThis.window = new EventTarget();
      globalThis.CustomEvent = class extends Event {constructor(type, options) {super(type); this.detail = options?.detail;}};
      const storage = new Map();
      globalThis.localStorage = {getItem: key => storage.get(key) || null,
        setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)};
      globalThis.sessionStorage = localStorage;
      const timers = [];
      globalThis.setTimeout = (callback, milliseconds) => {
        const timer = {callback, milliseconds, cleared: false}; timers.push(timer); return timer;
      };
      globalThis.clearTimeout = timer => {if (timer) timer.cleared = true;};
      globalThis.setInterval = () => ({unref() {}});
      globalThis.clearInterval = () => {};
      const json = value => new Response(JSON.stringify(value), {status: 200,
        headers: {'Content-Type': 'application/json'}});
      const deferred = () => {
        let resolve, reject;
        const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
        return {promise, resolve, reject};
      };
      const turn = () => new Promise(resolve => setImmediate(resolve));
      const {api} = await import(API_PATH);
    """
    paths = {"API_PATH": "lib/api.js", "MODEL_PATH": "components/ticket-dialog/model.js"}
    script += SCENARIOS[scenario]
    for token, relative in paths.items():
        script = script.replace(token, json.dumps((REPO_ROOT / "app/static" / relative).as_uri()))
    result = subprocess.run([node, "--input-type=module"], input=script,
                            capture_output=True, text=True, encoding="utf-8", timeout=12)
    assert result.returncode == 0, result.stderr
