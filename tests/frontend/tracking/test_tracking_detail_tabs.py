"""Selected hierarchy nodes share summary/detail navigation without new queries."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_summary_detail_tabs_cover_ancestors_leaves_and_shared_paths():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    static = (REPO_ROOT / 'app/static').as_uri()
    script = r'''
    import assert from 'node:assert/strict';
    globalThis.window={addEventListener(){},removeEventListener(){}};
    const {default:view}=await import(STATIC+'/components/tracking-view.js');
    const {api}=await import(STATIC+'/lib/api.js');
    api.raw=()=>{throw new Error('Navigation must use saved evidence')};
    const coverage={historyComplete:true,commentsComplete:true};
    const ticket=(id,key,children=[])=>({id,issueKey:key,name:key,kind:'Task',children});
    const run={id:'tabs',status:'complete',coverage:{complete:true},
      issues:['T-1','S-1','S-2'].map(key=>({key,summary:key,type:'Task',coverage})),
      events:['T-1','S-1','S-2'].map(key=>({id:key,issueKey:key,kind:'comment',body:key})),
      tree:[{id:'w',kind:'wbs',name:'WBS',children:[{id:'e',kind:'epic',name:'Epic',children:[
        ticket('t','T-1',[ticket('s','S-1')]),ticket('leaf','S-2')]}]}]};
    const vm={...view.data(),run,options:{},$nextTick:()=>{}};
    for(const [key,fn] of Object.entries(view.methods))vm[key]=fn.bind(vm);
    for(const [key,fn] of Object.entries(view.computed))Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
    vm.selectScope('e');
    assert.equal(vm.selectedId,'e');
    assert.equal(vm.summaryDisabled,false);
    vm.selectTab('detail');
    assert.equal(vm.reportEvidenceOpen,true);
    assert.deepEqual(vm.selectedEvents.map(e=>e.issueKey),['T-1','S-1','S-2']);
    vm.query='S-1';vm.matchedOnly=true;
    assert.deepEqual(vm.selectedEvents.map(e=>e.issueKey),['S-1']);
    vm.query='';vm.matchedOnly=false;
    vm.selectTab('summary');
    assert.equal(vm.reportEvidenceOpen,false);assert.equal(vm.scopeId,'e');
    vm.selectRow(vm.allRows.find(row=>row.id==='t'));
    vm.selectTab('summary');
    assert.equal(vm.scopeId,'t');
    assert.deepEqual(vm.scopedReport.items.flatMap(item=>item.keys).sort(),['S-1','T-1']);
    vm.selectScope('s');
    assert.equal(vm.summaryDisabled,true);assert.equal(vm.reportEvidenceOpen,true);
    vm.selectTab('summary');
    assert.equal(vm.reportEvidenceOpen,true);
    assert.deepEqual(vm.selectedEvents.map(e=>e.issueKey),['S-1']);
    vm.selectScope('');vm.selectTab('detail');
    assert.equal(vm.summaryDisabled,false);assert.equal(vm.selectedId,'');
    assert.equal(vm.selectedEvents.length,3);
    vm.selectTab('summary');assert.equal(vm.reportEvidenceOpen,false);
    // A missing parent may appear under two scopes; its summary must retain the chosen one.
    vm.run={...run,events:run.events.filter(e=>e.issueKey!=='T-1'),issues:[
      {...run.issues[1],type:'Sub-Task',parentKey:'T-1'},
      {...run.issues[2],type:'Sub-Task',parentKey:'T-1'}],tree:[
        {id:'a',kind:'wbs',name:'A',children:[ticket('a-s','S-1')]},
        {id:'b',kind:'wbs',name:'B',children:[ticket('b-s','S-2')]}]};
    vm.selectScope('a');vm.selectReport(vm.scopedReport.items[0]);
    assert.equal(vm.selected.id,'tracking-report:T-1');
    vm.selectTab('summary');
    assert.equal(vm.scope.valid,true);
    assert.deepEqual(vm.scopedReport.items[0].keys.slice().sort(),['S-1','T-1']);
    vm.selectTab('detail');assert.deepEqual(vm.selectedEvents.map(e=>e.issueKey),['S-1']);

    '''.replace('STATIC', json.dumps(static))
    result = subprocess.run([node, '--input-type=module', '-e', script],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
