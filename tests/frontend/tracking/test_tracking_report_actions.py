"""Reporting actions preserve selected evidence and stay entirely local."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_report_pins_filters_copy_and_plain_text_render():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    static = (REPO_ROOT / 'app/static').as_uri()
    script = r'''
    import assert from 'node:assert/strict';
    const memory=new Map();
    globalThis.localStorage={getItem:key=>memory.get(key),setItem:(key,value)=>memory.set(key,value)};
    let copied='';
    Object.defineProperty(globalThis,'navigator',{value:{clipboard:{writeText:async text=>{copied=text}}},configurable:true});
    const {default:view}=await import(STATIC+'/components/tracking-report.js');
    const {buildTrackingReport}=await import(STATIC+'/components/trackingReportModel.js');
    const coverage={historyComplete:true,commentsComplete:true,complete:true};
    const run={status:'complete',coverage:{complete:true},request:{startDate:'2026-11-01',endDate:'2026-11-15'},asOf:'2026-11-16T00:00:00+09:00',
      issues:[{key:'T-1',summary:'주요 적재 개발',coverage,type:'Task'},
        {key:'S-1',summary:'증분 적재 완료',parentKey:'T-1',isSubtask:true,coverage,type:'Sub-Task'},
        {key:'V-1',summary:'테이블 문의',voc:true,vocCategory:'질의 - 데이터',coverage,type:'Task'}],
      events:[{id:'done',issueKey:'S-1',kind:'completed',date:'2026-11-12T09:00:00Z'},
        {id:'comment',issueKey:'T-1',kind:'comment',date:'2026-11-13T09:00:00Z',body:'<script>untrusted</script>'},
        {id:'sub-comment',issueKey:'S-1',kind:'comment',date:'2026-11-13T10:00:00Z',body:'하위 작업 원문'}]};
    const report=buildTrackingReport(run);
    const vm={...view.data(),run,report,selectedKey:'T-1',matchKeys:['S-1'],hasMatch:true,filterMatches:false,$emit(){}};
    for(const [key,fn] of Object.entries(view.methods))vm[key]=fn.bind(vm);
    for(const [key,fn] of Object.entries(view.computed))Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
    assert.equal(vm.sections.length,2); assert.equal(vm.sections[1].title,'질의 - 데이터');
    vm.filterMatches=true;assert.deepEqual(vm.items.map(i=>i.key),['T-1']);
    const task=report.items.find(i=>i.key==='T-1');vm.pin(task);vm.filter='pinned';
    assert.deepEqual(view.data().pins,['T-1']);
    await vm.copy();assert.ok(copied.includes('증분 적재 완료'));assert.ok(copied.includes('주요 적재 개발'));
    assert.ok(!copied.includes('테이블 문의'));assert.equal(vm.copyError,false);
    const {compile}=await import(STATIC+'/vendor/vue.esm-browser.prod.js');
    const render=compile(view.template,{decodeEntities:raw=>raw,onError(error){throw error}});
    const nodes=[];function visit(node){if(!node||typeof node!=='object')return;nodes.push(node);if(Array.isArray(node.children))node.children.forEach(visit)}
    visit(render.call(vm,vm,[]));
    assert.ok(!nodes.some(n=>n.children==='<script>untrusted</script>')); // Comments stay folded in the scan view.
    assert.ok(nodes.some(n=>n.props?.['aria-expanded']===false));
    vm.toggle(task);nodes.length=0;visit(render.call(vm,vm,[]));
    assert.ok(nodes.some(n=>n.children==='<script>untrusted</script>'));
    assert.ok(nodes.some(n=>n.children==='증분 적재 완료 (S-1)'));
    assert.ok(!nodes.some(n=>n.props?.innerHTML));
    const {default:overviewView}=await import(STATIC+'/components/tracking-overview.js');
    const {buildTrackingOverview}=await import(STATIC+'/components/trackingOverviewModel.js');
    const overviewVm={overview:buildTrackingOverview(report),section:'highlights',scopeLabel:'전체 업무',$emit(){}};
    for(const [key,fn] of Object.entries(overviewView.methods))overviewVm[key]=fn.bind(overviewVm);
    const overviewRender=compile(overviewView.template,{decodeEntities:raw=>raw,onError(error){throw error}});
    nodes.length=0;visit(overviewRender.call(overviewVm,overviewVm,[]));
    assert.ok(nodes.some(n=>n.children==='주요 적재 개발'));
    assert.ok(!nodes.some(n=>n.props?.innerHTML));
    const changed=buildTrackingReport({...run,events:[...run.events,
      {id:'created',issueKey:'S-1',kind:'created',date:'2026-11-11T09:00:00Z'},
      {id:'field',issueKey:'T-1',kind:'fieldChanged',field:'assignee',from:'A',to:'B',date:'2026-11-11T09:00:00Z'}]});
    const changedOverview=buildTrackingOverview(changed);
    assert.equal(changedOverview.totals.newTickets,1);assert.equal(changedOverview.totals.newRoots,0);
    assert.equal(changedOverview.totals.fieldChanges,1);
    vm.pin(task);assert.equal(vm.items.length,0);
    '''.replace('STATIC', json.dumps(static))
    result = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True,
                            text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr


def test_report_hierarchy_groups_keep_scope_folding_and_copy_independent():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    static = (REPO_ROOT / 'app/static').as_uri()
    script = r'''
    import assert from 'node:assert/strict';
    globalThis.localStorage={getItem:()=>null,setItem(){}};
    let copied='';
    Object.defineProperty(globalThis,'navigator',{value:{clipboard:{writeText:async text=>{copied=text}}},configurable:true});
    const {default:view}=await import(STATIC+'/components/tracking-report.js');
    const {buildTrackingReport}=await import(STATIC+'/components/trackingReportModel.js');
    const {compile}=await import(STATIC+'/vendor/vue.esm-browser.prod.js');
    const coverage={historyComplete:true,commentsComplete:true,complete:true};
    const run={status:'complete',coverage:{complete:true},request:{startDate:'2026-11-01',endDate:'2026-11-15'},asOf:'2026-11-16T00:00:00+09:00',
      issues:[{key:'T-1',summary:'첫 번째 업무',coverage,type:'Task'},
        {key:'S-1',summary:'하위 업무',parentKey:'T-1',isSubtask:true,coverage,type:'Sub-Task'},
        {key:'T-2',summary:'두 번째 업무',coverage,type:'Task'}],events:[]};
    const report=buildTrackingReport(run);
    const groups=[{key:'epic-1',title:'<img src=x onerror=alert(1)>',pathLabel:'WBS A / Epic 1',scopeId:'epic:E-1',keys:['T-1']},
      {key:'epic-2',title:'Epic 2',pathLabel:'WBS B / Epic 2',scopeId:'epic:E-2',keys:['T-1','T-2']}];
    const emitted=[];
    const vm={...view.data(),run,report,groups,scopeId:'',scopeLabel:'전체 업무',selectedKey:'',matchKeys:[],hasMatch:false,filterMatches:false,$emit:(...args)=>emitted.push(args)};
    for(const [key,fn] of Object.entries(view.methods))vm[key]=fn.bind(vm);
    for(const [key,fn] of Object.entries(view.computed))Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
    assert.deepEqual(vm.sections.map(s=>[s.key,s.items.map(i=>i.key)]),[['epic-1',['T-1']],['epic-2',['T-2']]]);
    const render=compile(view.template,{decodeEntities:raw=>raw,onError(error){throw error}});
    function nodes(){const result=[];function visit(node){if(!node||typeof node!=='object')return;result.push(node);if(Array.isArray(node.children))node.children.forEach(visit)}visit(render.call(vm,vm,[]));return result}
    let rendered=nodes();
    assert.ok(rendered.some(n=>n.children===groups[0].title));
    assert.ok(rendered.some(n=>n.children==='WBS A / Epic 1'));
    assert.ok(!rendered.some(n=>n.props?.innerHTML));
    const fold=rendered.find(n=>n.props?.class==='tr-report-group-toggle');
    assert.equal(fold.props['aria-expanded'],true);
    fold.props.onClick();
    assert.equal(vm.collapsed['epic-1'],true);
    rendered=nodes();
    assert.ok(rendered.some(n=>n.props?.class==='tr-report-group-toggle' && n.props['aria-expanded']===false));
    assert.ok(!rendered.some(n=>n.children==='첫 번째 업무'));
    assert.equal(vm.items.length,2);
    await vm.copy();assert.ok(copied.includes('첫 번째 업무'));assert.ok(copied.includes('두 번째 업무'));
    vm.filter='quiet';void vm.sections;assert.equal(vm.collapsed['epic-1'],true);
    vm.filterMatches=true;vm.hasMatch=true;vm.matchKeys=['S-1'];
    assert.deepEqual(vm.sections.map(s=>s.key),['epic-1']);
    assert.equal(vm.collapsed['epic-1'],true);
    vm.filterMatches=false;
    const scope=nodes().find(n=>n.props?.class==='tr-report-group-scope');
    scope.props.onClick();assert.deepEqual(emitted,[['scope','epic:E-1']]);
    vm.scopeId='epic:E-1';view.watch.scopeId.call(vm,'epic:E-1','');
    vm.scopeLabel='Epic 1';await vm.copy();assert.ok(copied.startsWith('보고 범위: Epic 1\n'));
    assert.deepEqual(vm.collapsed,{});
    vm.groups=[groups[0]];vm.report={...report,items:[report.items.find(i=>i.key==='T-1')]};
    assert.deepEqual(vm.sections.map(s=>s.items.map(i=>i.key)),[['T-1']]);
    assert.ok(!nodes().some(n=>n.props?.class==='tr-report-group-scope'));
    '''.replace('STATIC', json.dumps(static))
    result = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True,
                            text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
