import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_priority_bfs_cache_retry_and_late_space_response():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    uri = (REPO_ROOT / 'app/static/lib/confluenceTreeQueue.js').as_uri()
    script = '''
      import { ConfluenceTreeQueue } from MODULE;
      import assert from 'node:assert/strict';
      const batch = (parent, results, start=0, nextStart=null) => ({parent,start,results,nextStart});
      const row = (id, children=false) => ({id,title:id,hasChildren:children});
      const calls=[], views=[], states=[];
      let release;
      const queue = new ConfluenceTreeQueue({
        snapshot:async()=>({batches:[], jobs:[{parent:'',start:0,depth:0}]}),
        read:async job=>{
          calls.push(job.parent);
          if(!job.parent) return batch('',[row('1',true),row('2',true),row('3',true)]);
          if(job.parent==='1') { await new Promise(resolve=>release=resolve); return batch('1',[row('11',true)]); }
          return batch(job.parent,[]);
        }, changed:rows=>views.push(rows.map(r=>r.id)),state:value=>states.push(value)
      });
      const loading=queue.start();
      while(!release) await new Promise(resolve=>setImmediate(resolve));
      assert.deepEqual(views.at(-1),['1','2','3']); // roots visible while a child is held
      queue.prioritize('3'); queue.prioritize('1'); // active work must not be duplicated
      release(); await loading;
      assert.deepEqual(calls,['','1','3','2','11']); // priority, then breadth, then deeper work
      assert.equal(states.at(-1).loading,false);
      const warm = new ConfluenceTreeQueue({snapshot:async()=>({batches:[batch('',[row('1')])],jobs:[]}),
        read:()=>assert.fail('warm snapshot should not query upstream'),changed:()=>{},state:()=>{}});
      await warm.start();
      let current=[], attempts=0;
      const refresh = new ConfluenceTreeQueue({
        snapshot:async()=>({batches:[batch('',[row('old')])],jobs:[{parent:'',start:0,depth:0,refresh:true}]}),
        read:async job=>{
          if(job.start===0) return batch('',[row('new')],0,100);
          assert.deepEqual(current,['old']);
          if(attempts++===0) throw Object.assign(new Error('temporary'),{status:503});
          return batch('',[row('last')],100);
        },changed:rows=>current=rows.map(r=>r.id),state:()=>{}});
      await refresh.start(); assert.deepEqual(current,['new','last']); assert.equal(attempts,2);
      let late, applied=0;
      const stopped = new ConfluenceTreeQueue({snapshot:async()=>({batches:[],jobs:[{parent:'',start:0,depth:0}]}),
        read:()=>new Promise(resolve=>late=resolve),changed:()=>applied++,state:()=>{}});
      const old=stopped.start(); while(!late) await new Promise(resolve=>setImmediate(resolve));
      stopped.stop(); const before=applied; late(batch('',[row('wrong-space')])); await old;
      assert.equal(applied,before);
      const broadCalls=[], broadRows=Array.from({length:10},(_,i)=>row(String(i+1),true));
      const broad = new ConfluenceTreeQueue({snapshot:async()=>({batches:[],jobs:[{parent:'',start:0,depth:0}]}),
        read:async job=>{broadCalls.push('branch:'+job.parent);return batch('',broadRows);},
        bulkRead:async start=>{broadCalls.push('index:'+start);return batch('',broadRows.map(r=>({...r,ancestors:[]})));},
        changed:()=>{},state:()=>{}});
      await broad.start();
      assert.deepEqual(broadCalls,['branch:','index:0']);
      assert.equal(broad.queue.size,0);
    '''.replace('MODULE', json.dumps(uri))
    subprocess.run([node, '--input-type=module', '-e', script], check=True, capture_output=True, text=True)
