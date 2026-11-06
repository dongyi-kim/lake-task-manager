"""Deep favorite paths get early coverage without starving a 3,000+ page index."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    uri = (REPO_ROOT / 'app/static/lib/confluenceTreeQueue.js').as_uri()
    result = subprocess.run([node, '--input-type=module', '-e',
        "import {ConfluenceTreeQueue} from " + json.dumps(uri) + ";\nimport assert from 'node:assert/strict';\n" + script],
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_deep_favorites_progress_during_continuous_search_and_finish_full_index():
    run_node('''
      const row=(id,ancestors=[],hasChildren=false)=>({id:String(id),title:'Page '+id,ancestors,hasChildren,space:{key:'DL'}});
      const ancestors=[1,11,111].map(id=>({id:String(id),title:'Page '+id}));
      const roots=Array.from({length:10},(_,i)=>row(i+1,[],i<3));
      const favorite=row(1111,ancestors,true), child=row(11111,[...ancestors,favorite]);
      const all=[...roots,favorite,child,...Array.from({length:3400},(_,i)=>row(20000+i,[roots[1]]))];
      const calls=[],states=[],frames=[]; let queue;
      queue=new ConfluenceTreeQueue({favorites:['1111','11111',...Array.from({length:30},(_,i)=>String(20000+i))],
        snapshot:async()=>({batches:[],jobs:[{parent:'',start:0,depth:0}]}),
        read:async job=>{calls.push('root'); return {parent:'',start:0,nextStart:null,results:roots};},
        favoriteRead:async(ids,start)=>{
          calls.push('favorite:'+ids.join(',')); await new Promise(r=>setImmediate(r));
          return {start,results:ids.includes('1111')?[favorite,child]:[],nextStart:start<1500?start+100:null};
        },
        bulkRead:async start=>{
          calls.push('index:'+start);
          for(let n=0;n<25;n++) queue.search(n%2?['1','1111']:[]);
          await new Promise(r=>setImmediate(r));
          return {start,results:all.slice(start,start+500),nextStart:start+500<all.length?start+500:null};
        },changed:rows=>frames.push(rows),state:s=>states.push(s)});
      await queue.start();
      assert.equal(calls[0],'root'); assert(calls[1].startsWith('favorite:1111,11111,')); assert.equal(calls[2],'index:0');
      assert(!calls.includes('favorite:11111'),'nested favorites belong to the same batched query');
      assert(frames.some(rows=>rows.some(r=>r.id==='11111')&&rows.length<100),'deep path must appear before the whole index');
      const indexes=calls.flatMap((v,i)=>v.startsWith('index:')?[i]:[]);
      assert.equal(indexes.length,7);
      assert(indexes.slice(1).every((i,n)=>i-indexes[n]<=3),'whole index must make progress under foreground load');
      assert.equal(queue.searching,true);
      assert.equal(states.at(-1).loading,false);
      assert.equal(frames.at(-1).length,all.length+2); // ancestor stubs 11 and 111
      assert.equal(new Set(frames.at(-1).map(r=>r.id)).size,frames.at(-1).length);
      assert.equal(queue.queue.size,0);
      assert(calls.filter(c=>c.startsWith('favorite:')).length<32,'complete index cancels redundant prefetch');
    ''')


def test_warm_cache_and_removed_favorite_do_not_fetch_redundant_subtrees():
    run_node('''
      const batch={parent:'',start:0,nextStart:null,results:[{id:'1',title:'Root',ancestors:[],hasChildren:false}]};
      let calls=0;
      const warm=new ConfluenceTreeQueue({favorites:['1'],snapshot:async()=>({batches:[batch],jobs:[]}),
        read:()=>assert.fail('warm branch request'),favoriteRead:()=>assert.fail('warm favorite request'),
        changed:()=>{},state:()=>{}});
      await warm.start(); warm.setFavorites(['1']); assert.equal(warm.queue.size,0);
      let release;
      const queue=new ConfluenceTreeQueue({favorites:['99'],snapshot:async()=>({batches:[],jobs:[{parent:'',start:0,depth:0}]}),
        read:async()=>batch,
        favoriteRead:async()=>{calls++; return new Promise(r=>release=r);},changed:()=>{},state:()=>{}});
      const loading=queue.start(); while(!release) await new Promise(r=>setImmediate(r));
      queue.setFavorites([]);
      release({results:[{id:'99',title:'Favorite',ancestors:[]}],nextStart:100});
      await loading; assert.equal(calls,1);
    ''')


@pytest.mark.parametrize('status', [401, 403, 503], ids=['auth-resume', 'search-denied', 'retry-exhausted'])
def test_favorite_failure_does_not_discard_tree_or_leave_spinner(status):
    run_node('const status=' + str(status) + ';' + '''
      const root={id:'1',title:'Root',ancestors:[],hasChildren:true};
      const child={id:'2',title:'Child',ancestors:[root],hasChildren:false};
      let calls=0, restored=false, rows, state;
      const queue=new ConfluenceTreeQueue({favorites:['2'],
        snapshot:async()=>({batches:[],jobs:[{parent:'',start:0,depth:0}]}),
        read:async()=>({results:[root],nextStart:null}),
        favoriteRead:async()=>{ calls++; if(!restored) throw Object.assign(new Error('fixture'),{status});
          return {results:[child],nextStart:null}; },
        bulkRead:async()=>({results:[root,child],nextStart:null}),
        changed:value=>rows=value, state:value=>state=value});
      await queue.start();
      assert.equal(state.loading,false);
      if(status===401) {
        assert.equal(queue.paused,true); assert(rows.some(r=>r.id==='1'));
        restored=true; queue.retry();
        while(queue.running) await new Promise(r=>setImmediate(r));
      }
      assert.deepEqual(rows.map(r=>r.id),['1','2']);
      assert.equal(state.loading,false); assert.equal(queue.complete,true);
      assert(calls<=2,'optional prefetch failure must not loop');
    ''')
