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
    assert.ok(nodes.some(n=>n.children==='<script>untrusted</script>'));
    assert.ok(nodes.some(n=>n.children==='증분 적재 완료 (S-1)'));
    assert.ok(!nodes.some(n=>n.props?.innerHTML));
    vm.pin(task);assert.equal(vm.items.length,0);
    '''.replace('STATIC', json.dumps(static))
    result = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True,
                            text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
