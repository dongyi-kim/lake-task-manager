"""Explicit person selections sync without delaying local suggestions or fetching identity."""
import json
import shutil
import subprocess

from support.paths import REPO_ROOT


def test_explicit_selection_sync_is_best_effort_and_never_records_passive_reads():
    script = r'''
      import assert from 'node:assert/strict';
      globalThis.window = new EventTarget();
      globalThis.CustomEvent = class extends Event { constructor(type, init) { super(type); this.detail = init?.detail; } };
      const stored = new Map(), calls = [];
      globalThis.localStorage = {getItem: k => stored.get(k) ?? null, setItem: (k,v) => stored.set(k,v)};
      let offline = false, actor = 'actor-a';
      globalThis.fetch = async (url, options = {}) => {
        calls.push({url, options});
        if (offline) throw new Error('offline');
        return new Response(JSON.stringify(url === '/api/me' ? {id:actor} : {recorded:true}),
          {status:200, headers:{'Content-Type':'application/json'}});
      };
      const {api} = await import(API);
      const {rememberUser, recentUsers, mentionInitialUsers} = await import(SUGGESTIONS);
      const settle = () => new Promise(resolve => setTimeout(resolve, 0));
      rememberUser({id:'u01', name:'첫 후보'});
      recentUsers(); mentionInitialUsers([]);
      await settle(); assert.equal(calls.length, 0); // No identity fetch solely for history.
      await api.me();
      const beforePassive = calls.length;
      recentUsers(); mentionInitialUsers([{id:'passive', name:'아직 고르지 않은 사람'}]);
      await settle(); assert.equal(calls.length, beforePassive);
      rememberUser({id:'u02', name:'선택한 사람'});
      rememberUser({id:'u03', name:'멘션한 사람'}, 'mention');
      assert.equal(recentUsers()[0].id, 'u03'); // Local selection is synchronous.
      await settle();
      const posts = calls.filter(c => c.url === '/api/people/recent');
      assert.deepEqual(posts.map(c => JSON.parse(c.options.body)), [
        {userId:'u02', event:'selection', actorHint:'actor-a'},
        {userId:'u03', event:'mention', actorHint:'actor-a'}]);
      assert.equal(posts[0].options.headers['x-ltm-local'], '1');
      await api.refresh(); // Generic GET eviction must not erase the verified actor hint.
      rememberUser({id:'after-refresh', name:'새로고침 후 선택'});
      await settle(); assert.equal(JSON.parse(calls.at(-1).options.body).actorHint, 'actor-a');
      offline = true;
      rememberUser({id:'u04', name:'오프라인 선택'});
      await settle(); assert.equal(recentUsers()[0].id, 'u04');
      const count = calls.length;
      offline = false; actor = 'actor-b';
      window.dispatchEvent(new Event('auth-ok'));
      rememberUser({id:'u05', name:'새 로그인 대기'});
      await settle(); assert.equal(calls.length, count + 1); // Only the auth boundary /me lookup.
      assert.equal(calls.at(-1).url, '/api/me');
      rememberUser({id:'u06', name:'새 계정 선택'});
      await settle(); assert.equal(JSON.parse(calls.at(-1).options.body).actorHint, 'actor-b');
    '''.replace('API', json.dumps((REPO_ROOT / 'app/static/lib/api.js').as_uri())).replace(
        'SUGGESTIONS', json.dumps((REPO_ROOT / 'app/static/lib/userSuggestions.js').as_uri()))
    subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
                   check=True, capture_output=True, text=True, timeout=20)
