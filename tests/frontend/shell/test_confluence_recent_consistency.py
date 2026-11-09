"""History preserves visit order and browser changes across delayed persistence."""
import json
import shutil
import subprocess

import pytest
from support.paths import REPO_ROOT


@pytest.mark.parametrize("scenario", ["web_delete", "visit_order", "cross_tab_visit", "cross_tab_delete"])
def test_recent_consistency_across_delayed_requests(scenario):
    script = r'''
      import assert from 'node:assert/strict';
      globalThis.window=new EventTarget();
      globalThis.CustomEvent=class extends Event {};
      globalThis.location={origin:'http://localhost:4457'};
      globalThis.document={hidden:false};
      const storage=new Map();
      globalThis.localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)};
      const {api}=await import(API);
      const {recordOpen,recentItems,forgetRecent,hydrateRecent}=await import(RECENT);
      const Recents=(await import(DOCS)).default;
      const base='https://docs.test/wiki';
      const doc=id=>({url:base+'/pages/viewpage.action?pageId='+id,kind:'confluence',title:'Doc '+id,
        openedAt:1700000000+id,data:{pageId:String(id),space:'ETL',confluenceBase:base}});
      if(SCENARIO==='web_delete'){
        api.recentAdd=async()=>({ok:true});
        for(const url of ['https://site.test/pages/123/title','https://site.test/view?pageId=456']){
          await recordOpen({url,kind:'web',title:'External page'});
          forgetRecent(url);
          assert.equal(recentItems().length,0,'ordinary web URLs must be removable without Confluence rewriting');
        }
      }else if(SCENARIO==='visit_order'){
        let now=1700000000000;
        Date.now=()=>now;
        const writes=[],database=[];
        api.recentAdd=payload=>new Promise(resolve=>writes.push(()=>{
          database.push({...payload,openedAt:payload.openedAt||now/1000});resolve({ok:true});
        }));
        const a=recordOpen(doc(1)); now+=1000;
        const b=recordOpen(doc(2));
        writes[1](); await b; now+=1000; writes[0](); await a;
        hydrateRecent(database,'confluence');
        assert.deepEqual(recentItems(100,'confluence').map(row=>row.pageId),['2','1'],
          'server writes completing out of order must retain the actual visit order');
        await recordOpen(doc(2));
        assert.equal(writes.length,2,'recording the timestamp must preserve duplicate-write suppression');
      }else{
        storage.set('recent.items',JSON.stringify([doc(1)]));
        let finish;
        api.recent=()=>new Promise(resolve=>finish=resolve);
        const context={...Recents.methods,base,_recentSpaces:new Set(['etl'])};
        Recents.mounted.call(context);
        const loading=context.loadRecentDocuments(false);
        const external=SCENARIO==='cross_tab_visit'?[doc(2)]:[];
        storage.set('recent.items',JSON.stringify(external));
        const event=new Event('storage');event.key='recent.items';event.storageArea=localStorage;
        window.dispatchEvent(event);
        finish([doc(1)]);await loading;
        assert.deepEqual(recentItems(100,'confluence').map(row=>row.pageId),external.map(row=>row.data.pageId),
          'a stale GET must not erase a visit or restore a deletion received from another tab');
        Recents.beforeUnmount.call(context);
      }
    '''.replace('SCENARIO', json.dumps(scenario))
    for token, path in (("API", "api.js"), ("RECENT", "recent.js"), ("DOCS", "confluenceRecents.js")):
        script = script.replace(token, json.dumps((REPO_ROOT / "app/static/lib" / path).as_uri()))
    result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
