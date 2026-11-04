"""Execute the real child scheduler against controlled responses, without browser/Jira delays."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_child_scheduler_settles_activity_and_preserves_partial_data():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/mytasks/taskHydration.js").as_uri()
    script = r'''
import assert from 'node:assert/strict';
import {hydrateTaskGroups, childSyncBusy} from MODULE;
const group = key => ({key, atoms:[{key:key+'-child'}], others:[],
  childrenPending:true, childrenLoaded:false});
const initial = () => ({syncId:'s', snapshotSequence:0, groups:[group('A'),group('B')]});
const response = (seq, groups, retryable=false, kind='transport') => ({
  contract:'task-snapshot.v1',type:'snapshot',syncId:'s',sequence:seq,
  model:{syncId:'s',snapshotSequence:seq,groups},retryable,
  missing:retryable ? [{kind}] : []});
const ready = key => ({...group(key),childrenPending:false,childrenLoaded:true});
async function run(load, options={}) {
  const original=initial(), calls=[], notices=[], publications=[], waits=[];
  let states={};
  const result=await hydrateTaskGroups({model:original,
    loadGroup:async (id,key)=>{calls.push(key); return load(key,calls.length);},
    isCurrent:()=>true, canStart:()=>true,
    publish:m=>publications.push(JSON.parse(JSON.stringify(m))),
    onStates:s=>{states=s;}, onFailure:(key,kind)=>notices.push([key,kind]),
    wait:async ms=>{waits.push(ms);}, ...options});
  assert.equal(original.groups[0].childrenPending,true,'input/cache aliases must not mutate');
  assert.ok(Object.values(result.states).every(s=>!childSyncBusy(s)),'no idle spinner');
  return {...result,calls,notices,publications,waits};
}
for (const [name,load,expected,count] of [
  ['network',()=>{throw new Error('network');},'failed',6],
  ['partial',(_key,n)=>response(n,[group('A'),group('B')],true),'failed',6],
  ['auth',()=>{throw new Error('401 login required');},'waiting-auth',2],
  ['permission',()=>{throw new Error('403 forbidden');},'unavailable',2],
  ['invalid',()=>({}),'failed',6],
  ['expired',()=>{throw new Error('410 expired');},'failed',6],
]) {
  const r=await run(load);
  assert.equal(r.calls.length,count,name);
  assert.equal(r.states.A,expected,name);
  assert.equal(r.model.groups[0].atoms.length,1,name+' keeps visible card');
  assert.equal(r.notices.length,name==='permission'?0:2,name+' notice');
}
let seq=0;
const success=await run(key=>response(++seq,[ready('A'),ready('B')]));
assert.equal(success.states.A,'complete');
assert.equal(success.notices.length,0);

// B's newer cumulative snapshot arrives before A's older retryable result.
let release, attempts=0;
const reversed=await run(async key=>{
  if(key==='A' && ++attempts===1) return new Promise(resolve=>{release=resolve;});
  if(key==='B') {
    queueMicrotask(()=>release(response(1,[group('A'),group('B')],true)));
    return response(2,[group('A'),ready('B')]);
  }
  return response(3,[ready('A'),ready('B')]);
});
assert.deepEqual(reversed.calls,['A','B','A']);
assert.equal(reversed.states.A,'complete');
assert.equal(reversed.model.snapshotSequence,3);

// A terminal HTTP failure must not revive when B publishes A's old pending flag.
const terminal=await run(key=>{
  if(key==='A') throw new Error('403 forbidden');
  return response(1,[group('A'),ready('B')]);
});
assert.equal(terminal.states.A,'unavailable');
assert.equal(terminal.model.groups[0].childrenPending,true,'incompleteness is not success');

// A newly selected filter stops queued work; in-flight successes still reach old-filter cache.
let active=true;
const switched=await run(key=>{active=false;return response(1,[ready('A'),group('B')]);},
  {canStart:()=>active});
assert.deepEqual(switched.calls,['A']);
assert.equal(switched.publications.length,1);
assert.equal(switched.states.B,'paused');
let generation=true;
const obsolete=await run(()=>{generation=false;return response(1,[ready('A'),ready('B')]);},
  {isCurrent:()=>generation});
assert.equal(obsolete.publications.length,0,'same-filter obsolete generation cannot cache');

// Auth resumption selects only the failed parent, keeping successful groups cached.
const resume=await run(key=>response(1,[ready('A'),ready('B')]),{keys:['A']});
assert.deepEqual(resume.calls,['A']);
console.log('scheduler cases passed');
'''.replace("MODULE", json.dumps(module))
    result = subprocess.run([node, "--input-type=module", "-e", script],
                            check=True, capture_output=True, text=True)
    assert "scheduler cases passed" in result.stdout


def test_stream_without_final_snapshot_is_not_success():
    # Keep the public loader integration checked alongside the executable scheduler cases.
    source = (REPO_ROOT / "app/static/components/views/MyTasksView.js").read_text(encoding="utf-8")
    assert "if (!receivedFinal) throw new Error" in source
    assert 'if (event.done && event.model) receivedFinal = true;' in source
