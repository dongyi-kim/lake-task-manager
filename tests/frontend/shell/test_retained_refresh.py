"""Refresh keeps visible evidence, fences old reads and coalesces hidden views."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    base = (REPO_ROOT / "app/static/lib").as_uri() + "/"
    script = """
      import assert from 'node:assert/strict';
      globalThis.window = new EventTarget();
      globalThis.localStorage = {getItem:()=>null,setItem(){},removeItem(){}};
      globalThis.sessionStorage = localStorage;
      const tick = () => new Promise(r=>setImmediate(r));
    """ + body.replace("BASE", json.dumps(base))
    subprocess.run([node, "--input-type=module", "-e", script],
                   check=True, capture_output=True, text=True, timeout=20)


def test_retained_memo_invalidation_fences_late_reads_and_failed_refresh():
    run_js("""
      const {CacheMemo} = await import(BASE+'cacheMemo.js');
      let now=1000, result={name:'old'}, pending, calls=0;
      const memo=new CacheMemo(async (path,opts)=>{
        calls++;
        if (pending) return await pending;
        opts.onCacheMetadata({fetchedAt:1000,freshUntil:1600,displayUntil:87400});
        return result;
      },()=>{},()=>now);
      await memo.get('/ticket');
      let finishOld;
      pending=new Promise(r=>finishOld=r); now=1601;
      await memo.get('/ticket');
      memo.invalidate('/ticket');
      assert.equal(memo.peek('/ticket').name,'old');
      assert.equal(memo.metadata('/ticket').fetchedAt,1000);
      finishOld({name:'outdated'}); await tick();
      assert.equal(memo.peek('/ticket').name,'old');
      pending=null; result={partial:true};
      await memo.get('/ticket'); await tick();
      assert.equal(memo.peek('/ticket').name,'old');
      result={name:'new'};
      await memo.get('/ticket'); await tick();
      assert.equal(memo.peek('/ticket').name,'new');
      memo.invalidate('/ticket');
      memo.fetcher=async()=>{throw Object.assign(new Error('denied'),{status:403});};
      await memo.get('/ticket'); await tick();
      assert.equal(memo.peek('/ticket'),undefined);
      assert.equal(calls,4);
    """)


def test_refresh_one_server_request_and_revision_baseline():
    run_js("""
      const calls=[], events=[]; let release, refreshStarted;
      const refreshEntered = new Promise(resolve => refreshStarted = resolve);
      globalThis.fetch=async(path)=>{
        calls.push(path);
        if(path==='/api/refresh') await new Promise(r=>{ release=r; refreshStarted(); });
        return new Response(JSON.stringify({name:'cached'}),{status:200});
      };
      const {api}=await import(BASE+'api.js');
      await api.readCached('/api/wbs');
      window.addEventListener('tickets-updated',e=>events.push(e.detail.revision));
      api.ticketRevisionUpdated('mock:1'); api.ticketRevisionUpdated('mock:1');
      api.ticketRevisionUpdated('mock:2'); api.ticketRevisionUpdated('mock:2');
      assert.deepEqual(events,['mock:2']);
      assert.equal(api.cached('/api/wbs').name,'cached');
      let reads=0;
      window.addEventListener('data-refresh',e=>e.detail.waitUntil(Promise.resolve().then(()=>reads++)));
      const a=api.refresh(), b=api.refresh();
      assert.equal(a,b);
      assert.equal(api.cached('/api/wbs').name,'cached');
      await refreshEntered; release(); await a;
      assert.equal(calls.filter(p=>p==='/api/refresh').length,1);
      assert.equal(reads,1);
      assert.equal(api.cached('/api/wbs').name,'cached');
    """)


def test_inactive_views_defer_and_inflight_updates_are_not_lost():
    run_js("""
      const {retainedViewRefresh}=await import(BASE+'viewRefresh.js');
      let calls=0, release;
      const mix=retainedViewRefresh(async function(){
        calls++;
        if(calls===1) await new Promise(r=>release=r);
      });
      const view={...mix.methods}; mix.mounted.call(view);
      mix.deactivated.call(view);
      window.dispatchEvent(new CustomEvent('tickets-updated'));
      window.dispatchEvent(new CustomEvent('data-refresh'));
      await tick(); assert.equal(calls,0);
      mix.activated.call(view); await tick(); assert.equal(calls,1);
      window.dispatchEvent(new CustomEvent('tickets-updated'));
      window.dispatchEvent(new CustomEvent('ticket-changed'));
      release(); await view._viewRefreshPending;
      assert.equal(calls,2);
      mix.beforeUnmount.call(view);
      window.dispatchEvent(new CustomEvent('data-refresh')); await tick();
      assert.equal(calls,2);
    """)


def test_confirmed_delete_forgets_exact_ticket_and_mutation_replaces_display():
    run_js("""
      const paths=[], removed=[];
      globalThis.fetch=async(path,opts)=>{
        paths.push(path);
        return new Response(JSON.stringify({name:path,ok:true}),{status:200});
      };
      const {api}=await import(BASE+'api.js');
      await api.readCached('/api/ticket/DL-1');
      await api.readCached('/api/ticket/DL-1/children');
      await api.readCached('/api/ticket/DL-10');
      window.addEventListener('api-cache-updated',e=>{if(e.detail.removed) removed.push(e.detail.path);});
      await api.deleteTicket('DL-1');
      assert.equal(api.cached('/api/ticket/DL-1'),undefined);
      assert.equal(api.cached('/api/ticket/DL-1/children'),undefined);
      assert.equal(api.cached('/api/ticket/DL-10').name,'/api/ticket/DL-10');
      assert.deepEqual(removed,['/api/ticket/DL-1','/api/ticket/DL-1/children']);
      api.replaceCached('/api/tracking/runs/1',{status:'running'});
      assert.equal(api.cached('/api/tracking/runs/1').status,'running');
      assert.equal(api.cacheMetadata('/api/tracking/runs/1').fetchedAt,null);
      assert.equal(api.cacheMetadata('/api/tracking/runs/1').freshUntil,0);
      await api.myTasksEpicMeta(['DL-3','DL-2','DL-3'],true);
      assert(paths.includes('/api/mytasks/epics?keys=DL-2%2CDL-3&revalidate=1'));
    """)
