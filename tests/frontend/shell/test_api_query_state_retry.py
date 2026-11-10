"""Real API/memo transport retries only a typed invalidated read, within one deadline."""
import json
import shutil
import subprocess

import pytest

from support.paths import STATIC_ROOT


SCENARIOS = {
    "typed_read_recovers_latest_and_memoizes_only_success": r"""
      let calls=0;
      globalThis.fetch=async()=>++calls===1 ? changed() : json([{key:'DL-1',summary:'latest'}]);
      assert.deepEqual(await api.ticketSiblings('DL-1'),[{key:'DL-1',summary:'latest'}]);
      assert.deepEqual(await api.ticketSiblings('DL-1'),[{key:'DL-1',summary:'latest'}]);
      assert.equal(calls,2); assert.deepEqual(events,[]);
    """,
    "head_is_a_safe_read": r"""
      const methods=[];
      globalThis.fetch=async(_path,opts)=>{methods.push(opts.method);return methods.length===1?changed():json({ok:true});};
      assert.deepEqual(await api.raw('/head',{method:'HEAD'}),{ok:true});
      assert.deepEqual(methods,['HEAD','HEAD']);
    """,
    "second_rejection_preserves_type_and_manual_retry_can_recover": r"""
      let calls=0;
      globalThis.fetch=async()=>++calls<=2 ? changed() : json([{summary:'after manual retry'}]);
      await assert.rejects(api.ticketSiblings('DL-1'),e=>e.status===503 &&
        e.code==='query_state_changed' && e.retryable===true && e.needLogin===false);
      assert.equal(calls,2); assert.equal(api.cached('/api/ticket/DL-1/siblings'),undefined);
      assert.deepEqual(await api.ticketSiblings('DL-1'),[{summary:'after manual retry'}]);
      assert.equal(calls,3); assert.deepEqual(events,[]);
    """,
    "wrong_contract_is_not_retried": r"""
      for(const [status,extra] of [[503,{code:'upstream_busy'}],[502,{}],
          [503,{retryable:false}],[503,{retryable:'true'}],[503,{needLogin:true}],
          [503,{needLogin:undefined}],[503,{uncertain:true}]]) {
        let calls=0;
        globalThis.fetch=async()=>{calls++;return changed(extra,status);};
        await assert.rejects(api.raw('/wrong'),e=>e.status===status);
        assert.equal(calls,1);
      }
      assert.deepEqual(events,[]);
    """,
    "write_and_mutation_marked_get_are_never_replayed": r"""
      for(const opts of [{method:'POST'},{method:'PUT'},{method:'DELETE'}, {mutation:true}]) {
        let calls=0;
        globalThis.fetch=async()=>{calls++;return changed();};
        await assert.rejects(api.raw('/write',opts),e=>e.status===503 && e.code==='query_state_changed');
        assert.equal(calls,1);
      }
    """,
    "network_error_is_not_retried": r"""
      let calls=0;
      globalThis.fetch=async()=>{calls++;throw new TypeError('offline');};
      await assert.rejects(api.raw('/offline'),e=>e instanceof TypeError);
      assert.equal(calls,1); assert.deepEqual(events,[]);
    """,
    "caller_abort_during_retry_after_prevents_second_fetch": r"""
      let calls=0; const controller=new AbortController();
      globalThis.fetch=async()=>{calls++;return changed({},503,'1');};
      const pending=api.raw('/cancel',{signal:controller.signal});
      setTimeout(()=>controller.abort(),20);
      await assert.rejects(pending,e=>e.name==='AbortError');
      await sleep(30); assert.equal(calls,1); assert.deepEqual(events,[]);
    """,
    "timeout_in_retry_delay_does_not_restart_budget": r"""
      let calls=0;
      globalThis.fetch=async()=>{calls++;return changed({},503,'1');};
      await assert.rejects(api.raw('/timeout',{timeoutMs:30}),e=>e.reason==='request_timeout' && e.retryable);
      assert.equal(calls,1);
    """,
    "missing_invalid_and_excessive_retry_after_stay_bounded": r"""
      for(const header of [null,'invalid-date','999999999999999']) {
        let calls=0;
        globalThis.fetch=async()=>{calls++;return header===null
          ? json({code:'query_state_changed',retryable:true,needLogin:false},503)
          : changed({},503,header);};
        await assert.rejects(api.raw('/bounded',{timeoutMs:30}),e=>e.reason==='request_timeout');
        assert.equal(calls,1);
      }
    """,
    "retry_after_seconds_and_http_date_are_respected": r"""
      for(const [kind,minWait] of [['seconds',850],['date',600]]) {
        const header=kind==='seconds' ? '1' : new Date(Date.now()+2000).toUTCString();
        let calls=0; const started=performance.now();
        globalThis.fetch=async()=>++calls===1 ? changed({},503,header) : json({ok:true});
        assert.deepEqual(await api.raw('/delay'),{ok:true});
        assert.ok(performance.now()-started>=minWait); assert.equal(calls,2);
      }
    """,
    "body_parsing_remains_inside_timeout": r"""
      for(const ok of [false,true]) {
        globalThis.fetch=async()=>({ok,status:ok?200:503,headers:new Headers(),
          clone(){return this;},json(){return new Promise(()=>{});}});
        await assert.rejects(api.raw('/body',{timeoutMs:25}),e=>e.reason==='request_timeout');
      }
    """,
    "second_fetch_and_body_share_original_timeout": r"""
      let calls=0;
      globalThis.fetch=async()=> {
        calls++;
        if(calls===1){await sleep(100);return changed();}
        return {ok:true,status:200,headers:new Headers(),json:()=>sleep(200).then(()=>({ok:true}))};
      };
      await assert.rejects(api.raw('/total',{timeoutMs:250}),e=>e.reason==='request_timeout');
      assert.equal(calls,2);
    """,
    "second_fetch_uses_original_abort_signal_and_budget": r"""
      let calls=0;
      globalThis.fetch=async(_path,opts)=> {
        if(++calls===1){await sleep(80);return changed();}
        return new Promise((_resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(opts.signal.reason),{once:true}));
      };
      await assert.rejects(api.raw('/second-fetch',{timeoutMs:200}),e=>e.reason==='request_timeout');
      assert.equal(calls,2);
    """,
    "caller_abort_during_error_body_prevents_replay": r"""
      let calls=0; const controller=new AbortController();
      globalThis.fetch=async()=>{calls++;return {ok:false,status:503,headers:new Headers(),
        clone(){return this;},json:()=>sleep(80).then(()=>({code:'query_state_changed',retryable:true,needLogin:false}))};};
      const pending=api.raw('/body-cancel',{signal:controller.signal});
      setTimeout(()=>controller.abort(),15);
      await assert.rejects(pending,e=>e.name==='AbortError'); assert.equal(calls,1);
    """,
    "dialog_guard_ignores_recovered_old_ticket_and_keeps_failed_siblings": r"""
      const fs=await import('node:fs'); const source=fs.readFileSync(DIALOG,'utf8');
      const start=source.indexOf('    _loadDialogPanel('), end=source.indexOf('    retryPanel(',start);
      assert.ok(start>=0 && end>start);
      const load=new Function('api','requestErrorText','return ({'+source.slice(start,end)+'})._loadDialogPanel;')(
        api,e=>e.message);
      const states=[]; const state={keyId:'DL-1',_req:1,siblings:[{summary:'keep'}],
        _setPanelState:(...args)=>states.push(args)};
      let calls=0,release; const response=new Promise(resolve=>release=resolve);
      globalThis.fetch=async()=>++calls===1 ? changed() : response;
      const pending=load.call(state,'siblings');
      while(calls<2) await sleep(1);
      state._req=2; state.keyId='DL-2'; release(json([{summary:'obsolete'}]));
      await pending; assert.deepEqual(state.siblings,[{summary:'keep'}]);
      assert.deepEqual(states,[['siblings','loading']]);
      api.evict('DL-1'); state.keyId='DL-1'; calls=0;
      globalThis.fetch=async()=>{calls++;return changed();};
      await load.call(state,'siblings');
      assert.equal(calls,2); assert.deepEqual(state.siblings,[{summary:'keep'}]);
      assert.equal(states.at(-1)[1],'error');
      globalThis.fetch=async()=>json([{summary:'newest'}]);
      await load.call(state,'siblings');
      assert.deepEqual(state.siblings,[{summary:'newest'}]); assert.equal(states.at(-1)[1],'ready');
    """,
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIOS)
def test_typed_query_state_retry(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend transport tests")
    script = r"""
      import assert from 'node:assert/strict';
      globalThis.window=new EventTarget();
      globalThis.CustomEvent=class extends Event {constructor(name,opts){super(name);this.detail=opts?.detail;}};
      const storage=new Map();
      globalThis.localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
      globalThis.sessionStorage=localStorage;
      const events=[];
      for(const kind of ['auth-ok','need-login']) window.addEventListener(kind,()=>events.push(kind));
      const json=(value,status=200,headers={})=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json',...headers}});
      const changed=(extra={},status=503,delay='0')=>json({error:'query changed',code:'query_state_changed',
        retryable:true,needLogin:false,...extra},status,{'Retry-After':delay});
      const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      const {api}=await import(API);
    """
    script = script.replace("API", json.dumps((STATIC_ROOT / "lib/api.js").as_uri()))
    script += SCENARIOS[scenario].replace("DIALOG", json.dumps(str(STATIC_ROOT / "components/ui/TicketDialog.js")))
    result = subprocess.run([node, "--input-type=module"], input=script,
                            capture_output=True, text=True, encoding="utf-8", timeout=8)
    assert result.returncode == 0, result.stderr
