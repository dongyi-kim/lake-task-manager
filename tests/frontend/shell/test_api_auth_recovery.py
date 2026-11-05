"""Real frontend transport with controlled fetch responses; no Jira delay or browser required."""
import json
import shutil
import subprocess

import pytest

from support.paths import STATIC_ROOT


SCENARIOS = {
    "concurrent_and_late_401_share_one_silent_probe": r"""
      const probe=deferred(), late=deferred(); let probes=0, reads=0;
      globalThis.fetch=async path => {
        if(path.includes('/auth/probe')) {probes++; return probe.promise;}
        reads++;
        if(path==='/late' && reads===2) return late.promise;
        return reads<=3 ? rejected() : json({key:path});
      };
      const first=api.raw('/first'), delayed=api.raw('/late'), third=api.raw('/third');
      await until(()=>probes===1); assert.equal(logins(),0);
      probe.resolve(json({ok:true,recovered:true})); await Promise.all([first,third]);
      late.resolve(rejected()); assert.deepEqual(await delayed,{key:'/late'});
      assert.equal(probes,1); assert.equal(reads,6); assert.equal(logins(),0);
      assert.equal(events.filter(e=>e==='auth-ok').length,1);
    """,
    "failed_silent_probe_prompts_once_without_recursion": r"""
      const probe=deferred(); let probes=0;
      globalThis.fetch=async path => {
        if(path.includes('/auth/probe')) {probes++; return probe.promise;}
        return rejected();
      };
      const attempts=Promise.allSettled([api.raw('/a'),api.raw('/b')]);
      await until(()=>probes===1); assert.equal(logins(),0);
      probe.resolve(json({ok:false,needLogin:true}));
      const results=await attempts;
      assert.ok(results.every(r=>r.reason.status===401 && r.reason.needLogin));
      await assert.rejects(api.raw('/c'),e=>e.needLogin);
      assert.equal(probes,1); assert.equal(logins(),1);
    """,
    "safe_read_retries_at_most_once": r"""
      let reads=0, probes=0;
      globalThis.fetch=async path => {
        if(path.includes('/auth/probe')) {probes++; return json({ok:true});}
        reads++; return rejected();
      };
      await assert.rejects(api.raw('/always-expired'),e=>e.status===401 && e.needLogin);
      assert.equal(reads,2); assert.equal(probes,1); assert.equal(logins(),1);
    """,
    "probe_auth_rejection_prompts_once_without_recursive_probes": r"""
      const calls=[];
      globalThis.fetch=async path => {calls.push(path);return rejected();};
      await assert.rejects(api.raw('/a'),e=>e.needLogin);
      assert.deepEqual(calls,['/a','/api/auth/probe?recover=1']);
      assert.equal(logins(),1);
    """,
    "permission_network_and_auth_endpoint_do_not_start_probe": r"""
      const calls=[];
      globalThis.fetch=async path => {
        calls.push(path);
        if(path==='/network') throw new TypeError('Failed to fetch');
        return path==='/forbidden' ? json({error:'권한 없음'},403) : rejected();
      };
      await assert.rejects(api.raw('/forbidden'),e=>e.status===403);
      await assert.rejects(api.raw('/network'),e=>e instanceof TypeError);
      await assert.rejects(api.authProbe(),e=>e.status===401);
      await assert.rejects(api.login(),e=>e.status===401);
      assert.deepEqual(calls,['/forbidden','/network','/api/auth/probe','/api/login']);
      assert.equal(logins(),0);
    """,
    "unavailable_or_pending_probe_is_not_interactive_login": r"""
      globalThis.fetch=async path => path.includes('/auth/probe')
        ? json({ok:false,pending:true,needLogin:false}) : rejected();
      const error=await api.raw('/a').catch(e=>e);
      assert.equal(error.status,401); assert.equal(error.needLogin,false);
      assert.equal(error.authRecoveryPending,true); assert.equal(logins(),0);
      assert.equal(taskLoadErrorKind(error),'other'); assert.equal(workloadErrorKind(error),'other');
    """,
    "probe_transport_failure_does_not_claim_login_required": r"""
      globalThis.fetch=async path => {
        if(path.includes('/auth/probe')) throw new TypeError('offline');
        return rejected();
      };
      await assert.rejects(api.raw('/a'),e=>e.authRecoveryPending && !e.needLogin);
      assert.equal(logins(),0);
    """,
    "recovered_write_is_not_replayed_and_retains_receipt": r"""
      let writes=0, probes=0; const bodies=[];
      globalThis.fetch=async (path,opts) => {
        if(path.includes('/auth/probe')) {probes++; return json({ok:true});}
        writes++; bodies.push(JSON.parse(opts.body));
        return writes===1 ? rejected() : json({ok:true});
      };
      const error=await api.doTransition('DL-1',{id:'31'}).catch(e=>e);
      assert.equal(error.authRecovered,true); assert.equal(error.uncertain,false);
      assert.equal(error.needLogin,false); assert.equal(logins(),0); assert.equal(writes,1);
      assert.match(error.message,/복구/); assert.doesNotMatch(error.message,/로그인 후/);
      assert.equal(taskLoadErrorKind(error),'other');
      assert.equal([...storage.keys()].filter(k=>k.startsWith('pending-mutation')).length,1);
      await api.doTransition('DL-1',{id:'31'}); // explicit receipt-aware retry
      assert.equal(writes,2); assert.equal(probes,1);
      assert.equal(bodies[0].clientMutationId,bodies[1].clientMutationId);
      assert.equal([...storage.keys()].filter(k=>k.startsWith('pending-mutation')).length,0);
    """,
    "uncertain_write_never_replayed_or_downgraded": r"""
      let writes=0, probes=0;
      globalThis.fetch=async path => {
        if(path.includes('/auth/probe')) {probes++; return json({ok:true});}
        writes++; return rejected({uncertain:true});
      };
      await assert.rejects(api.createTask({summary:'keep draft',clientMutationId:'one'}),
        e=>e.uncertain && e.authRecovered && !e.needLogin);
      assert.equal(writes,1); assert.equal(probes,1);
      globalThis.fetch=async()=>{writes++; throw new TypeError('response lost');};
      await assert.rejects(api.createTask({summary:'keep draft',clientMutationId:'two'}),e=>e.uncertain);
      assert.equal(writes,2); assert.equal(probes,1); assert.equal(logins(),0);
    """,
    "caller_abort_does_not_cancel_other_read_or_replay_aborted_request": r"""
      const probe=deferred(), ctl=new AbortController(); let probes=0;
      const reads=new Map();
      globalThis.fetch=async path => {
        if(path.includes('/auth/probe')) {probes++; return probe.promise;}
        const n=(reads.get(path)||0)+1; reads.set(path,n);
        return n===1 ? rejected() : json({ok:true});
      };
      const cancelled=api.raw('/cancel',{signal:ctl.signal}).catch(e=>e);
      const active=api.raw('/active'); await until(()=>probes===1);
      ctl.abort(); assert.equal((await cancelled).name,'AbortError');
      probe.resolve(json({ok:true})); await active;
      assert.equal(reads.get('/cancel'),1); assert.equal(reads.get('/active'),2);
      assert.equal(probes,1); assert.equal(logins(),0);
    """,
    "rejected_write_timeout_while_renewing_is_not_uncertain": r"""
      const probe=deferred(); let probes=0, writes=0;
      globalThis.fetch=async path => {
        if(path.includes('/auth/probe')) {probes++; return probe.promise;}
        writes++; return rejected();
      };
      await assert.rejects(api.raw('/write',{method:'POST',mutation:true,timeoutMs:20}),
        e=>e.authRecoveryPending && !e.uncertain);
      assert.equal(probes,1); assert.equal(writes,1);
      probe.resolve(json({ok:true})); await until(()=>events.includes('auth-ok'));
    """,
    "stream_header_retries_but_partial_stream_does_not_replay": r"""
      let reads=0, probes=0; const received=[];
      globalThis.fetch=async path => {
        if(path.includes('/auth/probe')) {probes++; return json({ok:true});}
        reads++; return reads===1 ? rejected()
          : new Response('{"type":"snapshot","sequence":1}\n{"type":"done"}\n');
      };
      await api.myTasksStream({},event=>received.push(event));
      assert.deepEqual(received.map(e=>e.type),['snapshot','done']);
      assert.equal(reads,2); assert.equal(probes,1);
      globalThis.fetch=async path => {
        if(path.includes('/auth/probe')) {probes++; return json({ok:true});}
        reads++; return new Response('{"type":"snapshot","sequence":2}\n'
          +'{"type":"error","needLogin":true,"error":"session expired"}\n');
      };
      await assert.rejects(api.myTasksStream({},event=>received.push(event)),e=>e.authRecovered && !e.needLogin);
      assert.equal(reads,3); assert.equal(probes,2); assert.equal(logins(),0);
      assert.deepEqual(received.map(e=>e.type),['snapshot','done','snapshot']);
    """,
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIOS)
def test_api_silent_auth_recovery(scenario):
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
      const interval=globalThis.setInterval;
      globalThis.setInterval=(...args)=>{const timer=interval(...args);timer.unref();return timer;};
      const events=[];
      for(const kind of ['auth-ok','need-login']) window.addEventListener(kind,()=>events.push(kind));
      const logins=()=>events.filter(e=>e==='need-login').length;
      const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
      const rejected=(extra={})=>json({needLogin:true,detail:'session expired',...extra},401);
      const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
      const until=async predicate=>{for(let n=0;n<100;n++){if(predicate())return;await new Promise(r=>setImmediate(r));}throw Error('condition did not settle');};
      const {api}=await import(API);
      const {taskLoadErrorKind}=await import(TASK_MODEL);
      const {workloadErrorKind}=await import(WORKLOAD);
    """
    for token, relative in [("API", "lib/api.js"), ("TASK_MODEL", "components/mytasks/taskModel.js"),
                            ("WORKLOAD", "components/workload/recovery.js")]:
        script = script.replace(token, json.dumps((STATIC_ROOT / relative).as_uri()))
    result = subprocess.run([node, "--input-type=module"], input=script + SCENARIOS[scenario],
                            capture_output=True, text=True, encoding="utf-8", timeout=12)
    assert result.returncode == 0, result.stderr
