"""Node contracts for metadata tabs. Browser focus/overflow is verified by the UI owner."""
import json
import os
import shutil
import subprocess

from support.paths import REPO_ROOT


def run_node(body):
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    assert node, "Node is required; an unavailable runtime is not a passed tab contract"
    header = "import assert from 'node:assert/strict';\n"
    header += "const lib=await import(" + json.dumps((REPO_ROOT / "app/static/lib/openItemTabs.js").as_uri()) + ");\n"
    header += "const UI=(await import(" + json.dumps((REPO_ROOT / "app/static/components/ui/OpenItemTabs.js").as_uri()) + ")).default;\n"
    header += "const context={env:'mock',jiraBase:'https://jira.test',projectKey:'DL',confluenceBase:'https://docs.test/wiki'};\n"
    header += "let requests=0; globalThis.fetch=()=>{requests++;throw Error('Tabs must not fetch');};\n"
    result = subprocess.run([node, "--input-type=module", "-e", header + body + "\nassert.equal(requests,0);"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_metadata_order_dedup_selection_scroll_and_no_eviction():
    run_node(r'''
      const saved=new Map(), events=[];
      const storage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)};
      let reactiveCalls=0;
      const store=lib.createOpenItemTabs({kind:'task',context,storage,reactive:s=>{reactiveCalls++;return s;},now:()=>42});
      assert.equal(reactiveCalls,1);
      const unsubscribe=store.subscribe((state,event)=>events.push(event.type));
      for(let i=1;i<=205;i++)store.open({id:'DL-'+i,title:'긴 업무 제목 '+i,body:'not stored',draft:'not stored'});
      assert.equal(store.state.items.length,205,'tabs must not evict dirty inactive work');
      store.setScroll('DL-2',{x:4,y:720});
      store.open({id:' dl-2 ',title:'새 제목',attachments:['secret'],receipts:['secret']});
      assert.equal(store.state.items.length,205);
      assert.equal(store.state.items[1].id,'DL-2','revisits preserve established tab order');
      assert.deepEqual(store.state.items[1].scroll,{x:4,y:720});
      assert.equal(store.state.activeId,'DL-2');
      store.close('DL-1');
      assert.equal(store.state.activeId,'DL-2','closing inactive/middle tabs must preserve active identity');
      store.close('DL-2');assert.equal(store.state.activeId,'DL-3');
      store.activate('DL-205');store.close('DL-205');assert.equal(store.state.activeId,'DL-204');
      assert.equal(store.activate('missing'),false);assert.equal(store.open({id:undefined}),null);
      assert.equal(store.open({id:'<script>-1'}),null);assert.equal(store.open({id:'DL-0'}),null);
      assert.equal(lib.normalizeItemId('task',' dl-0002 '),'DL-2');
      assert.equal(lib.normalizeItemId('confluence','/pages/12'), '');
      for(const raw of saved.values()){
        const value=JSON.parse(raw);
        assert.deepEqual(Object.keys(value).sort(),['activeId','items','version']);
        for(const item of value.items)assert.deepEqual(Object.keys(item).sort(),['id','lastOpenedAt','scroll','title']);
        assert(!raw.includes('secret')&&!raw.includes('not stored'));
      }
      assert(events.includes('update'));unsubscribe();const count=events.length;store.clear();assert.equal(events.length,count);
      assert.equal(store.state.activeId,null);assert.equal(store.state.items.length,0);
    ''')


def test_scope_partition_canonical_urls_and_incomplete_context_is_memory_only():
    run_node(r'''
      const {openItemTabsScope:scope,createOpenItemTabs:create}=lib;
      assert.equal(scope('task',context),scope('task',{...context,env:'MOCK',projectKey:'dl',jiraBase:'https://JIRA.test/',confluenceBase:'https://docs.test/wiki/'}));
      assert(!scope('task',{...context,jiraBase:'https://user:password@jira.test/?token=secret#x'}).includes('secret'));
      assert(!scope('task',{...context,jiraBase:'https://user:password@jira.test'}).includes('password'));
      const names=[scope('task',context),scope('confluence',context),...['env','jiraBase','projectKey','confluenceBase'].map(key=>scope('task',{...context,[key]:key.endsWith('Base')?'https://other.test':'OTHER'}))];
      assert.equal(new Set(names).size,names.length);
      const saved=new Map(),storage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)};
      const a=create({kind:'task',context,storage});a.open({id:'DL-1',title:'A'});a.setScroll('DL-1',{y:9});
      a.setContext({...context,env:'prod'});assert.equal(a.state.items.length,0);a.open({id:'DL-2'});
      a.setContext(context);assert.equal(a.state.activeId,'DL-1');assert.equal(a.state.items[0].scroll.y,9);
      const docs=create({kind:'confluence',context,storage});docs.open({id:'00012',title:'문서'});docs.open({id:12,title:'이름 변경'});
      assert.equal(docs.state.items.length,1);assert.equal(docs.state.activeId,'12');
      const before=saved.size;
      for(const invalid of [undefined,null,'mock',[],{env:'mock'},{...context,jiraBase:'javascript:alert(1)'}]){
        const pending=create({kind:'task',context:invalid,storage});pending.open({id:'DL-9'});
        const other=create({kind:'task',context:invalid,storage});assert.equal(other.state.items.length,0);
      }
      assert.equal(saved.size,before,'unknown scope must not persist or recover another environment');
      assert.throws(()=>scope('unknown',context),TypeError);
    ''')


def test_storage_failures_keep_latest_memory_and_forged_payload_is_allowlisted():
    run_node(r'''
      const {createOpenItemTabs:create,openItemTabsScope:scope}=lib;
      const fakeContext={...context,projectKey:'FORGED'};
      let raw=JSON.stringify({version:1,scope:'another project',activeId:' dl-7 ',body:'private',items:[
        {id:' dl-7 ',title:'제목',scroll:{x:5,y:-4},lastOpenedAt:2,draft:'private',attachments:['private']},
        {id:'DL-7',title:'중복'},{id:{toString:'DL-8'},title:'invalid'},null,
        {id:'DL-8',title:{html:'bad'},scroll:{x:'9',y:null},lastOpenedAt:'bad'}]});
      const storage={getItem:()=>raw,setItem:(key,value)=>{raw=value;}};
      const s=create({kind:'task',context:fakeContext,storage});
      assert.deepEqual(s.state.items.map(x=>x.id),['DL-7','DL-8']);
      assert.equal(s.state.activeId,'DL-7');assert.deepEqual(s.state.items[0].scroll,{x:5,y:0});
      assert.equal(s.state.items[1].title,'');assert.equal(s.state.items[1].lastOpenedAt,0);
      s.activate('DL-8');assert(!raw.includes('private')&&!raw.includes('scope'));
      const failContext={...context,projectKey:'FAIL'};
      let old=JSON.stringify({version:1,items:[],activeId:null});
      const fail=create({kind:'task',context:failContext,storage:{getItem:()=>old,setItem:()=>{throw Error('quota');}}});
      fail.open({id:'DL-1'});fail.setContext({...failContext,env:'other'});fail.setContext(failContext);
      assert.equal(fail.state.activeId,'DL-1','stale storage must not override the newer quota-fallback memory');
      Object.defineProperty(globalThis,'localStorage',{configurable:true,get(){throw Error('blocked');}});
      const privateStore=create({kind:'task',context:{...context,projectKey:'PRIVATE'}});privateStore.open({id:'DL-3'});assert.equal(privateStore.state.activeId,'DL-3');
      for(const payload of ['not json',JSON.stringify({version:99,items:[{id:'DL-4'}]}),JSON.stringify({version:1,items:'wrong'})]){
        const empty=create({kind:'task',context:{...context,projectKey:'BAD-'+payload.length},storage:{getItem:()=>payload,setItem(){}}});
        assert.equal(empty.state.items.length,0);
      }
    ''')


def test_keyboard_manual_activation_disabled_items_close_and_focus_contract():
    run_node(r'''
      const items=Array.from({length:35},(_,i)=>({id:'DL-'+i,title:'긴 제목 '+i,disabled:i===1}));
      assert.equal(lib.tabFocusTarget(items,'DL-0','ArrowRight'),'DL-2');
      assert.equal(lib.tabFocusTarget(items,'DL-0','ArrowLeft'),'DL-34');
      assert.equal(lib.tabFocusTarget(items,'DL-20','Home'),'DL-0');
      assert.equal(lib.tabFocusTarget(items,'DL-20','End'),'DL-34');
      assert.equal(lib.tabFocusTarget(items,'DL-0','Enter'),null);
      const events=[],focus=[];
      const ctx={...UI.methods,items,disabled:false,focusId:'DL-0',closingFocusId:null,$emit:(...args)=>events.push(args),focusTab:id=>focus.push(id)};
      const key=value=>({key:value,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}});
      ctx.onKey(key('ArrowRight'),items[0]);assert.deepEqual(focus,['DL-2']);assert.equal(events.length,0);
      ctx.onKey(key('Enter'),items[2]);ctx.onKey(key(' '),items[2]);
      assert.deepEqual(events,[['activate','DL-2'],['activate','DL-2']]);
      ctx.onKey(key('Delete'),items[2]);assert.deepEqual(events.at(-1),['close','DL-2']);
      assert.equal(items.length,35,'UI close requests cannot discard a guarded draft');
      const before=events.length;ctx.disabled=true;ctx.onKey(key('Enter'),items[0]);ctx.close(items[0]);assert.equal(events.length,before);
      assert(UI.template.includes('role="tablist"')&&UI.template.includes('role="tab"'));
      assert(UI.template.includes(':data-item-id=')&&UI.template.includes(':aria-selected='));
      assert(UI.template.includes(':title=')&&UI.template.includes(':aria-label='));
      const restore={items:[{id:'A'},{id:'C'}],knownIds:['A','B','C'],closingFocusId:'B',activeId:'A',focusId:'B',
        $nextTick:fn=>fn(),focusTab:id=>focus.push(id),updateOverflow(){},reveal(){}};
      Object.defineProperty(restore,'rovingId',{get(){return this.focusId;}});
      UI.watch.itemKeys.handler.call(restore);assert.equal(focus.at(-1),'C');
      const disabledNext={...restore,items:[{id:'A'},{id:'C',disabled:true},{id:'D'}],knownIds:['A','B','C','D'],closingFocusId:'B',focusId:'B'};
      Object.defineProperty(disabledNext,'rovingId',{get(){return this.focusId;}});
      UI.watch.itemKeys.handler.call(disabledNext);assert.equal(focus.at(-1),'D');
      const cancelled={...restore,items:[{id:'A'},{id:'B'},{id:'C'}],knownIds:['A','B','C'],closingFocusId:'B',focusId:'B'};
      const count=focus.length;UI.watch.itemKeys.handler.call(cancelled);assert.equal(focus.length,count,'a cancelled close must retain focus');
    ''')
