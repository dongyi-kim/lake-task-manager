"""Shared local history is immediate, bounded, and prioritizes only menu entries."""
import json
import shutil
import subprocess

from support.paths import REPO_ROOT


def test_recent_identity_mirror_order_and_priority_scope():
    script = r'''
      import assert from 'node:assert/strict';
      globalThis.window=new EventTarget();
      globalThis.CustomEvent=class extends Event {};
      globalThis.location={origin:'http://localhost:4457'};
      const storage=new Map(), calls=[];
      globalThis.localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)};
      globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response('{}',{headers:{'Content-Type':'application/json'}});};
      const {recordOpen,recentItems,hydrateRecent,forgetRecent}=await import(RECENT);
      const Tree=(await import(TREE)).default;
      const base='https://docs.test/wiki';
      const doc=(id)=>({url:base+'/spaces/ETL/pages/'+id+'/Title#heading',kind:'confluence',title:'Doc '+id,data:{pageId:String(id),space:'ETL'}});
      for(let i=1;i<=105;i++) await recordOpen(doc(i));
      for(let i=1;i<=105;i++) await recordOpen({url:'/browse/DL-'+i,kind:'jira',title:'Task'});
      assert.equal(recentItems(1000,'confluence').length,100);
      assert.equal(recentItems(1000,'jira').length,100);
      await recordOpen({...doc(6),url:base+'/display/ETL/Renamed'});
      assert.equal(recentItems(100,'confluence')[0].pageId,'6');
      await recordOpen(doc(7)); await recordOpen(doc(6)); // Revisiting within cooldown still moves above another page.
      assert.equal(recentItems(100,'confluence')[0].pageId,'6');
      assert.equal(recentItems(1000,'confluence').length,100);
      forgetRecent(base+'/spaces/ETL/pages/6/Title');
      assert.equal(recentItems(1000,'confluence').length,99);
      await recordOpen(doc(6)); // Explicit delete must not leave a cooldown that prevents reopening.
      assert.equal(recentItems(100,'confluence')[0].pageId,'6');
      hydrateRecent([doc(42)],'confluence');
      assert.equal(recentItems(1000,'confluence').length,1);
      assert.equal(recentItems(1000,'jira').length,100);
      const recentDocuments=Array.from({length:15},(_,i)=>({id:String(i+1),space:i===1?'OPS':'ETL'}));
      const ids=Tree.methods.treePriorityIds.call({space:'ETL',favorites:[{id:'1',space:'ETL'},{id:'99',space:'ETL'}],recentDocuments});
      assert.deepEqual(ids,['1','99','3','4','5','6','7','8','9','10']);
      assert(calls.every(c=>c.url==='/api/recent'),'no auth or upstream request is needed for recents');
      const {api}=await import(API);
      const Recents=(await import(DOCS)).default, pending=[];
      api.recent=()=>new Promise(resolve=>pending.push(resolve));
      const context={base,_recentSpaces:new Set(['etl']),...Recents.methods};
      const old=context.loadRecentDocuments(false), newer=context.loadRecentDocuments(false);
      pending[1]([doc(42)]); await newer;
      pending[0]([doc(43)]); await old;
      assert.equal(recentItems(100,'confluence')[0].pageId,'42','late history snapshot must not overwrite a newer one');
      let saved;
      api.recentAdd=()=>new Promise(resolve=>saved=resolve);
      const saving=recordOpen(doc(44));
      hydrateRecent([doc(42)],'confluence');
      assert.equal(recentItems(100,'confluence')[0].pageId,'44','a GET issued during a write must retain that local visit');
      saved({ok:true}); await saving;
    '''.replace('RECENT',json.dumps((REPO_ROOT/'app/static/lib/recent.js').as_uri())).replace(
        'TREE',json.dumps((REPO_ROOT/'app/static/lib/confluenceTree.js').as_uri())).replace(
        'API',json.dumps((REPO_ROOT/'app/static/lib/api.js').as_uri())).replace(
        'DOCS',json.dumps((REPO_ROOT/'app/static/lib/confluenceRecents.js').as_uri()))
    result=subprocess.run([shutil.which('node'),'--input-type=module','-e',script],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
