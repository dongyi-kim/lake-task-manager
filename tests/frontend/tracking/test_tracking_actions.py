"""Exercise the saved-run UI controller with the real auth API boundary substituted."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_collection_reopening_and_ai_use_saved_evidence_without_display_fetches():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    static = (REPO_ROOT / 'app/static').as_uri()
    script = r'''
    import assert from 'node:assert/strict';
    globalThis.window={addEventListener(){},removeEventListener(){},dispatchEvent(){}};
    const {default: view}=await import(STATIC+'/components/tracking-view.js');
    const {api}=await import(STATIC+'/lib/api.js');
    api.readCached=(path,opts)=>api.raw(path,opts);
    const vm=view.data();
    for(const [key,fn] of Object.entries(view.methods)) vm[key]=fn.bind(vm);
    for(const [key,fn] of Object.entries(view.computed)) Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
    vm.$nextTick=fn=>{};vm._epoch=0;vm.active=false;
    const source={id:'r1',status:'partial',request:{startDate:'2026-11-01',endDate:'2026-11-02',people:['a'],modules:['M']},
      summary:{uniqueIssues:1,provisional:true},coverage:{complete:false,historyComplete:false,issuesTotal:1},
      issues:[{key:'L-1',summary:'Work',assignee:{id:'a',name:'Same name'},coverage:{historyComplete:false,commentsComplete:true}},
        {key:'E-1',contextOnly:true,coverage:{historyComplete:false,commentsComplete:false}}],
      events:[{id:'ev1',issueKey:'L-1',kind:'comment',actorId:'b',actorName:'Same name',date:'2026-11-01T00:00:00Z',body:'<script>unsafe</script>'}],
      tree:[{id:'w',kind:'wbs',name:'WBS',children:[{id:'t',issueKey:'L-1',kind:'Task',name:'Work'}]}]};
    let calls=[];
    api.raw=async(path,opts)=>{calls.push([path,opts]);return structuredClone(source)};
    Object.assign(vm,{optionsLoaded:true,options:{people:[{id:'a',name:'a'},{id:'b',name:'b'}],modules:[],aiEnabled:true},startDate:'2026-11-01',endDate:'2026-11-02',people:['a'],modules:['M']});
    await vm.collect();
    assert.equal(calls.length,1);
    assert.equal(calls[0][0],'/api/tracking/runs');
    assert.deepEqual(JSON.parse(calls[0][1].body),source.request);
    assert.equal(vm.provisional,true); assert.equal(vm.summaryLabel(0),'미확인');
    assert.equal(vm.canResume,true);assert.equal(vm.selectedId,'');
    assert.equal(vm.targetIssues.length,1);assert.equal(vm.historyConfirmed,0);
    assert.deepEqual(vm.peopleOptions.map(p=>p.name),['Same name (a)','Same name (b)']);
    assert.equal(vm.selectionLabel('people'),'Same name (a)');
    assert.equal(vm.summaryLabel(0),'미확인');
    assert.equal(vm.ticketLabel('L-1'),'Work (L-1)');
    assert.equal(vm.evidenceLabel('ev1'),'Work (L-1)');
    vm.run.status='cancelled';assert.equal(vm.canResume,false);vm.run.status='partial';
    vm.closed={w:true}; vm.query='work'; vm.highlightKind='task';vm.highlightValue='L-1';
    assert.equal(vm.rows.length,1); // display folding stays as chosen
    assert.equal(vm.feedGroups[0].events.length,1); // date feed must be independent of tree folding
    vm.matchedOnly=true;assert.equal(vm.rows.length,2);
    assert.equal(calls.length,1); // all display actions are local
    vm.selectEvent(source.events[0]);assert.equal(vm.selectedId,'t');assert.equal(vm.selectedEventId,'ev1');
    let firstResolve;
    api.raw=(path)=>path.endsWith('/first') ? new Promise(resolve=>firstResolve=resolve) : Promise.resolve({...source,id:'second'});
    const first=vm.openRun('first'); await vm.openRun('second'); firstResolve({...source,id:'first'});await first;
    assert.equal(vm.run.id,'second');assert.equal(vm.pending,false);
    api.raw=async(path)=>{calls.push([path]);return {items:[{text:'Evidence',evidenceIds:['ev1']}],usage:{totalTokens:18}}};
    await vm.summarize();assert.equal(vm.ai.items[0].evidenceIds[0],'ev1');
    assert.equal(vm.metrics.llmTokens,18);
    assert.ok(calls.at(-1)[0].endsWith('/second/summary'));
    api.raw=async()=>{throw new Error('connection lost')};
    await vm.runAction('resume');assert.equal(vm.run.id,'second');assert.equal(vm.error,'connection lost');assert.equal(vm.pending,false);
    // The production Vue compiler catches expression/HTML errors before browser QA.
    const {compile}=await import(STATIC+'/vendor/vue.esm-browser.prod.js');
    const render=compile(view.template,{decodeEntities:raw=>raw,onError(error){throw error}});
    assert.equal(typeof render,'function');
    vm.picker='people';vm.options.people=[{id:'a',name:'UserA'},{id:'b',name:'UserB'}];
    assert.equal(vm.reportEvidenceOpen,false); // A different saved run clears stale evidence.
    vm.selectEvent(source.events[0]);
    const nodes=[];
    function visit(node){if(!node || typeof node !== 'object')return;nodes.push(node);if(Array.isArray(node.children))node.children.forEach(visit)}
    visit(render.call(vm,vm,[]));
    assert.ok(nodes.some(n=>n.props?.event?.body === '<script>unsafe</script>')); // rich component receives raw input; it escapes unsanitized fallback
    assert.ok(!nodes.some(n=>n.props?.innerHTML));
    const choices=nodes.filter(n=>n.type==='input' && n.props?.type==='checkbox' && n.props?.checked !== undefined);
    assert.equal(choices.length,2);assert.equal(choices[0].props.checked,true);
    choices[1].props.onChange();assert.deepEqual(vm.people,['a','b']);
    vm.selectScope('');vm.mode='feed';nodes.length=0;visit(render.call(vm,vm,[]));
    const feedTicket=nodes.find(n=>n.props?.class==='tr-feed-ticket');
    assert.ok(feedTicket.children.some(n=>n.type==='strong' && n.children==='Work'));
    assert.ok(feedTicket.children.some(n=>n.type==='small' && n.children==='L-1'));
    // Missing parents still expose all collected child evidence from the report.
    vm.run={...source,id:'orphan',issues:[{key:'S-1',summary:'Recovered evidence',type:'Sub-Task',isSubtask:true,parentKey:'T-404',coverage:{historyComplete:true,commentsComplete:true}}],
      events:[{id:'child-comment',issueKey:'S-1',kind:'comment',date:'2026-11-01T00:00:00Z',body:'Completed migration'}],
      tree:[{id:'orphan-sub',issueKey:'S-1',name:'Recovered evidence',kind:'Sub-Task'}]};
    vm.query='';vm.highlightValue='';vm.matchedOnly=false;
    vm.mode='report';vm.selectReport(vm.report.items[0]);
    assert.equal(vm.reportEvidenceOpen,true);assert.equal(vm.selected.key,'T-404');
    assert.equal(vm.selectedEvents[0].id,'child-comment');
    '''.replace('STATIC',json.dumps(static))
    result = subprocess.run([node,'--input-type=module','-e',script],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode == 0, result.stderr


def test_scoped_event_evidence_keeps_the_selected_shared_ticket_branch():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    static = (REPO_ROOT / 'app/static').as_uri()
    script = r'''
    import assert from 'node:assert/strict';
    globalThis.window={addEventListener(){},removeEventListener(){},dispatchEvent(){}};
    const {default:view}=await import(STATIC+'/components/tracking-view.js');
    const {api}=await import(STATIC+'/lib/api.js');
    let calls=0;api.raw=()=>{calls++;throw new Error('Display actions must not fetch')};
    const coverage={complete:true,historyComplete:true,commentsComplete:true};
    const issue=(key,extra={})=>({key,summary:key,type:'Task',coverage,...extra});
    const ticket=(id,key,children=[])=>({id,issueKey:key,kind:'Task',name:key,children});
    const event=key=>({id:'event:'+key,issueKey:key,kind:'comment',date:'2026-11-15T00:00:00Z',body:key});
    const run={id:'shared',status:'complete',coverage:{complete:true},
      issues:[issue('T-1'),issue('S-1',{type:'Sub-Task',parentKey:'T-1'}),issue('S-2',{type:'Sub-Task',parentKey:'T-1'})],
      events:['T-1','S-1','S-2'].map(event),tree:[
        {id:'a',kind:'wbs',name:'Branch A',children:[ticket('a-t','T-1',[ticket('a-s','S-1')])]},
        {id:'b',kind:'wbs',name:'Branch B',children:[ticket('b-t','T-1',[ticket('b-s','S-2')])]}]};
    const vm={...view.data(),run,scopeId:'b',options:{},$nextTick:()=>{}};
    for(const [key,fn] of Object.entries(view.methods))vm[key]=fn.bind(vm);
    for(const [key,fn] of Object.entries(view.computed))Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
    vm.selectReport(vm.scopedReport.items.find(item=>item.key==='T-1'));
    assert.equal(vm.selectedId,'b-t');
    assert.deepEqual(vm.selectedEvents.map(e=>e.issueKey),['T-1','S-2']);
    vm.focusEvidence('event:T-1');
    assert.equal(vm.selectedId,'b-t');assert.equal(vm.selectedEventId,'event:T-1');
    assert.equal(vm.scopeId,'b');assert.equal(vm.reportEvidenceOpen,true);
    assert.deepEqual(vm.selectedEvents.map(e=>e.issueKey),['T-1','S-2']);
    assert.deepEqual(vm.selectedBrief.keys,['T-1','S-2']);
    vm.mode='feed';vm.selectEvent(vm.feedGroups[0].events.find(e=>e.issueKey==='T-1'));
    assert.equal(vm.selectedId,'b-t');
    assert.deepEqual(vm.selectedEvents.map(e=>e.issueKey),['T-1','S-2']);
    // A deliberate navigator evidence action remains independent of report scope.
    vm.selectRow(vm.allRows.find(row=>row.id==='a-t'));
    assert.equal(vm.scopeId,'b');assert.equal(vm.selectedId,'a-t');
    assert.deepEqual(vm.selectedEvents.map(e=>e.issueKey),['T-1','S-1']);
    assert.equal(calls,0);
    '''.replace('STATIC',json.dumps(static))
    result = subprocess.run([node,'--input-type=module','-e',script],capture_output=True,
                            text=True,encoding='utf-8',timeout=20)
    assert result.returncode == 0, result.stderr


def test_scoped_views_keep_ancestor_search_and_epic_highlight_matches():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    static = (REPO_ROOT / 'app/static').as_uri()
    script = r'''
    import assert from 'node:assert/strict';
    globalThis.window={addEventListener(){},removeEventListener(){},dispatchEvent(){}};
    const {default:view}=await import(STATIC+'/components/tracking-view.js');
    const {api}=await import(STATIC+'/lib/api.js');
    let calls=0;api.raw=()=>{calls++;throw new Error('Display actions must not fetch')};
    const coverage={complete:true,historyComplete:true,commentsComplete:true};
    const run={id:'ancestor-matches',status:'complete',coverage:{complete:true},issues:[
      {key:'T-1',summary:'Task',type:'Task',coverage},
      {key:'S-1',summary:'Child',type:'Sub-Task',parentKey:'T-1',coverage},
      {key:'T-2',summary:'Outside scope',type:'Task',coverage}],
      events:['T-1','S-1','T-2'].map(key=>({id:key,issueKey:key,kind:'comment',date:'2026-11-15T00:00:00Z',body:key})),
      tree:[{id:'w',kind:'wbs',name:'Ancestor WBS',children:[
        {id:'e',issueKey:'E-1',kind:'Epic',name:'Ancestor Epic',children:[
          {id:'t',issueKey:'T-1',kind:'Task',name:'Task',children:[
            {id:'s',issueKey:'S-1',kind:'Sub-Task',name:'Child',children:[]}]},
          {id:'outside',issueKey:'T-2',kind:'Task',name:'Outside scope',children:[]}]}]}]};
    const vm={...view.data(),run,scopeId:'t',options:{},matchedOnly:true,$nextTick:()=>{}};
    for(const [key,fn] of Object.entries(view.methods))vm[key]=fn.bind(vm);
    for(const [key,fn] of Object.entries(view.computed))Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
    function verify(){
      assert.deepEqual(vm.scopedReport.items.map(item=>item.key),['T-1']);
      assert.ok(vm.scopedReport.items.every(item=>item.keys.some(key=>vm.matchKeys.has(key))));
      assert.deepEqual(vm.rows.map(row=>row.key),['T-1','S-1']);
      assert.deepEqual(vm.rows.map(row=>row.depth),[0,1]);
      assert.deepEqual(vm.feedGroups.flatMap(group=>group.events).map(event=>event.issueKey),['T-1','S-1']);
      assert.equal(vm.scopeActivity.uniqueIssues,2);assert.equal(vm.scopeActivity.comments,2);
      assert.equal(vm.overview.totals.comments,2);
    }
    vm.query='Ancestor WBS';verify();
    vm.query='';vm.highlightKind='epic';vm.highlightValue='E-1';verify();
    // Closing an ancestor in the full navigator cannot hide this isolated scope.
    vm.closed={w:true,e:true};verify();
    vm.query='No matching ancestor';
    assert.deepEqual(vm.rows,[]);assert.deepEqual(vm.feedGroups,[]);
    assert.ok(vm.scopedReport.items.every(item=>item.keys.every(key=>!vm.matchKeys.has(key))));
    // Search filters the list; saved-scope aggregates retain their independent totals.
    assert.equal(vm.scopeActivity.comments,2);assert.equal(vm.overview.totals.comments,2);
    assert.equal(calls,0);
    '''.replace('STATIC',json.dumps(static))
    result = subprocess.run([node,'--input-type=module','-e',script],capture_output=True,
                            text=True,encoding='utf-8',timeout=20)
    assert result.returncode == 0, result.stderr
