"""Hierarchy selection projects saved evidence without leaking adjacent branches."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/trackingScopeModel.js").as_uri()
    voc_module = (REPO_ROOT / "app/static/components/trackingVocModel.js").as_uri()
    source = (
        "import assert from 'node:assert/strict';\nimport {buildTrackingScope} from "
        + json.dumps(module)
        + ";\nimport {projectTrackingVoc} from "
        + json.dumps(voc_module)
        + ";\n"
        + """
        const complete={complete:true,historyComplete:true,commentsComplete:true};
        const issue=(key,extra={})=>({key,summary:key+' title',type:'Task',coverage:complete,...extra});
        const event=(key,id=key)=>({id,issueKey:key,kind:'created',date:'2026-11-15T00:00:00Z'});
        const ticket=(id,key,children=[],kind='Task')=>({id,issueKey:key,name:key+' title',kind,children});
        const epic=(id,children)=>({id,kind:'Epic',name:'Epic '+id,children});
        const wbs=(id,children)=>({id,kind:'wbs',name:'WBS '+id,children});
        const run=(issues,tree=[],events=issues.map(i=>event(i.key)),extra={})=>({
          status:'complete',coverage:{complete:true,missing:[]},issues,tree,events,...extra});
        """
        + script
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source], capture_output=True,
        text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_whole_groups_by_identity_once_and_keeps_all_unlinked_report_roots():
    run_js("""
      const input=run([issue('T-1'),issue('T-2'),issue('O-1'),
        issue('S-1',{type:'Sub-Task',parentKey:'M-1'})],[
        wbs('w2',[epic('e2',[ticket('shared2','T-1'),ticket('two','T-2')])]),
        wbs('w1',[epic('e1',[ticket('shared1','T-1')])])]);
      const before=JSON.stringify(input), scope=buildTrackingScope(input);
      assert.equal(scope.valid,true); assert.equal(scope.node,null);
      assert.equal(scope.run,input); assert.equal(scope.report.items.length,4);
      const assigned=scope.groups.flatMap(g=>g.keys);
      assert.deepEqual([...assigned].sort(),scope.report.items.map(i=>i.key).sort());
      assert.equal(new Set(assigned).size,assigned.length);
      assert.equal(scope.groups.find(g=>g.keys.includes('T-1')).scopeId,'e1');
      assert.deepEqual(scope.groups.find(g=>g.scopeId==='e2').keys,['T-2']);
      const fallback=scope.groups.find(g=>g.keys.includes('O-1'));
      assert.ok(fallback.keys.includes('M-1')); assert.equal(fallback.scopeId,'');
      assert.equal(JSON.stringify(input),before);
      const reversed=buildTrackingScope({...input,tree:[...input.tree].reverse()});
      assert.deepEqual(reversed.groups,scope.groups);
    """)


def test_selected_branch_filters_sibling_events_and_embedded_evidence_without_mutation():
    run_js("""
      const input=run([issue('T-1'),issue('S-1',{type:'Sub-Task',parentKey:'T-1',events:[event('S-1','embedded'),event('T-2','leak')]}),issue('T-2')],[
        wbs('a',[epic('a-e',[ticket('a-t','T-1',[ticket('a-s','S-1',[],'Sub-Task')])])]),
        wbs('b',[epic('b-e',[ticket('b-t','T-1',[ticket('b-other','T-2')])])])]);
      const before=JSON.stringify(input), scope=buildTrackingScope(input,'a');
      assert.deepEqual(scope.run.issues.map(i=>i.key),['T-1','S-1']);
      assert.deepEqual(scope.run.events.map(e=>e.issueKey),['T-1','S-1']);
      assert.deepEqual(scope.report.items[0].keys,['T-1','S-1']);
      assert.ok(scope.report.items.every(i=>i.events.every(e=>e.issueKey!=='T-2')));
      assert.ok(scope.report.items[0].paths.every(p=>!p.includes('WBS b')));
      assert.deepEqual(scope.breadcrumbs.map(b=>b.id),['a']);
      assert.equal(scope.groups[0].pathLabel,'Epic a-e');
      assert.equal(JSON.stringify(input),before);
    """)


def test_task_and_subtask_selection_promotes_only_selected_root_and_preserves_issue_type():
    run_js("""
      const input=run([issue('T-1'),issue('S-1',{type:'Sub-Task',isSubtask:true,parentKey:'T-1'})],[
        wbs('w',[epic('e',[ticket('t','T-1',[ticket('s','S-1',[],'Sub-Task')])])])]);
      const task=buildTrackingScope(input,'t');
      assert.deepEqual(task.report.items[0].keys,['T-1','S-1']);
      assert.equal(task.groups[0].pathLabel,'');
      const sub=buildTrackingScope(input,'s');
      assert.equal(sub.report.items.length,1);assert.equal(sub.report.items[0].key,'S-1');
      assert.deepEqual(sub.report.items[0].keys,['S-1']);
      assert.equal(sub.run.issues[0].parentKey,'');
      assert.equal(sub.run.issues[0].type,'Sub-Task');assert.equal(sub.run.issues[0].isSubtask,true);
      assert.equal(input.issues[1].parentKey,'T-1');
      assert.deepEqual(sub.breadcrumbs.map(b=>b.id),['w','e','t','s']);
      assert.equal(buildTrackingScope(input,'e').groups[0].pathLabel,'');
    """)


def test_voc_virtual_wbs_is_real_group_context_and_scope_has_no_original_branch():
    run_js("""
      const input=projectTrackingVoc(run([issue('V-1',{voc:true,summary:'[Account - Login] Repair'}),issue('T-1')],[
        wbs('w',[epic('e',[ticket('v','V-1'),ticket('t','T-1')])])]));
      const whole=buildTrackingScope(input), voc=whole.groups.find(g=>g.voc);
      assert.ok(voc); assert.ok(voc.pathLabel.includes('사용자 VOC'));
      assert.deepEqual(voc.keys,['V-1']);
      const selected=buildTrackingScope(input,'tracking-voc:wbs');
      assert.deepEqual(selected.report.items.map(i=>i.key),['V-1']);
      assert.equal(selected.groups[0].pathLabel,'Account - Login');
      assert.equal(selected.groups[0].voc,true);
      assert.ok(selected.report.items[0].paths.every(p=>!p.includes('WBS w')));
    """)


def test_context_only_and_global_partial_caveat_survive_scope_projection():
    run_js("""
      const input=run([issue('T-1',{contextOnly:true,coverage:{}}),issue('S-1',{type:'Sub-Task',parentKey:'T-1'})],[
        wbs('w',[ticket('t','T-1',[ticket('s','S-1',[],'Sub-Task')])])], [event('S-1')],
        {status:'partial',coverage:{complete:false,missing:[{issueKey:'OTHER-1',source:'issue'}]}});
      const scope=buildTrackingScope(input,'t');
      assert.equal(scope.report.items[0].uncertain,false);
      assert.equal(scope.report.summary.provisional,true);
      assert.deepEqual(scope.run.coverage.missing,input.coverage.missing);
      assert.equal(scope.run.issues[0].contextOnly,true);
      const gap=buildTrackingScope({...input,coverage:{complete:false,missing:[{issueKey:'S-1',source:'comments'}]}},'t');
      assert.equal(gap.report.items[0].uncertain,true);
    """)


def test_equal_labels_and_path_separators_do_not_merge_distinct_epics():
    run_js("""
      const first=epic('e1',[ticket('one','T-1')]);first.name='Same › Epic';
      const second=epic('e2',[ticket('two','T-2')]);second.name='Same › Epic';
      const input=run([issue('T-1'),issue('T-2')],[wbs('w',[first,second])]);
      const scope=buildTrackingScope(input,'w');
      assert.equal(scope.groups.length,2);
      assert.notEqual(scope.groups[0].key,scope.groups[1].key);
      assert.deepEqual(scope.groups.map(g=>g.scopeId),['e1','e2']);
      assert.deepEqual(scope.groups.map(g=>g.pathLabel),['Same › Epic','Same › Epic']);
      assert.deepEqual(scope.groups.map(g=>g.keys),[['T-1'],['T-2']]);
      const selected=buildTrackingScope(input,'e2');
      assert.deepEqual(selected.report.items.map(i=>i.key),['T-2']);
      assert.equal(selected.groups[0].pathLabel,'');
    """)


def test_empty_and_invalid_selections_fail_closed_and_cycle_graphs_terminate():
    run_js("""
      const a=wbs('a',[]), t=ticket('t','T-1');a.children=[t];t.children=[a];
      const input=run([issue('T-1')],[a]);
      const whole=buildTrackingScope(input);
      assert.equal(whole.report.items.length,1);
      const selected=buildTrackingScope(input,'a');
      assert.equal(selected.report.items.length,1);
      const invalid=buildTrackingScope(input,'not-a-real-id');
      assert.equal(invalid.valid,false);assert.equal(invalid.node,null);
      assert.deepEqual(invalid.run.issues,[]);assert.deepEqual(invalid.run.events,[]);
      assert.equal(invalid.report.items.length,0);assert.deepEqual(invalid.groups,[]);
      assert.equal(buildTrackingScope(input).report.items.length,1);
      assert.deepEqual(buildTrackingScope(null).groups,[]);
      assert.equal(buildTrackingScope(run([], [wbs('empty',[])]),'empty').report.items.length,0);
    """)
