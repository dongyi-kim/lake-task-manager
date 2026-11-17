"""Unassigned Epic groups stay local to their saved hierarchy branch."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    components = REPO_ROOT / "app/static/components"
    imports = {
        "projectTrackingHierarchy": "trackingHierarchyModel.js",
        "buildTrackingScope": "trackingScopeModel.js",
        "buildTrackingReport": "trackingReportModel.js",
        "trackingRows": "trackingModel.js",
        "projectTrackingVoc": "trackingVocModel.js",
    }
    source = "import assert from 'node:assert/strict';\n" + "\n".join(
        f"import {{{name}}} from {json.dumps((components / filename).as_uri())};"
        for name, filename in imports.items()
    ) + "\n" + """
      const complete={complete:true,historyComplete:true,commentsComplete:true};
      const issue=(key,extra={})=>({key,type:'Task',summary:key+' title',coverage:complete,...extra});
      const event=(key)=>({id:key,issueKey:key,kind:'created',date:'2026-11-15T00:00:00Z'});
      const ticket=(id,key,children=[],kind='Task')=>({id,issueKey:key,kind,name:key+' title',children});
      const container=(id,children,kind='wbs')=>({id,kind,name:'Same label',children});
      const run=(issues,tree)=>({status:'complete',coverage:{complete:true,missing:[]},
        summary:{created:issues.length},issues,tree,events:issues.map(i=>event(i.key))});
      const walk=node=>[node,...(node.children||[]).flatMap(walk)];
      const groups=run=>run.tree.flatMap(walk).filter(node=>node.epicUnassigned);
    """ + script
    result = subprocess.run(
        [node, "--input-type=module", "-e", source], capture_output=True,
        text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_unmapped_tasks_and_root_subtasks_gain_scopable_groups_without_changing_evidence():
    run_js("""
      const input=run([issue('T-1'),issue('S-1',{type:'Sub-Task',parentKey:'T-1'}),
        issue('T-2'),issue('S-2',{type:'Sub-Task'})],[
        container('unmapped',[ticket('task','T-1',[ticket('sub','S-1',[],'Sub-Task')]),
          ticket('other','T-2')],'module'),ticket('root','S-2',[],'Sub-Task')]);
      const before=JSON.stringify(input), output=projectTrackingHierarchy(input);
      const [local,root]=groups(output);
      assert.equal(local.name,'소속 Epic 없음'); assert.equal(local.kind,'Epic');
      assert.equal(local.virtual,true); assert.equal(local.epicUnassigned,true);
      assert.ok(!local.issueKey && !local.key);
      assert.deepEqual(local.children.map(n=>n.issueKey),['T-1','T-2']);
      assert.deepEqual(local.children[0].children.map(n=>n.issueKey),['S-1']);
      assert.deepEqual(root.children.map(n=>n.issueKey),['S-2']);
      assert.notEqual(root.id,local.id);
      assert.equal(output.events,input.events); assert.equal(output.issues,input.issues);
      assert.equal(output.summary,input.summary); assert.equal(output.coverage,input.coverage);
      assert.equal(JSON.stringify(input),before);
      const selected=buildTrackingScope(output,local.id);
      assert.deepEqual(selected.run.issues.map(i=>i.key),['T-1','S-1','T-2']);
      assert.deepEqual(selected.run.events.map(e=>e.issueKey),['T-1','S-1','T-2']);
      assert.deepEqual(selected.report.items.map(i=>i.keys),[['T-1','S-1'],['T-2']]);
      assert.equal(selected.breadcrumbs.at(-1).title,'소속 Epic 없음');
      assert.equal(buildTrackingScope(output).groups.find(g=>g.keys.includes('T-1')).scopeId,local.id);
      const report=buildTrackingReport(output);
      assert.equal(report.summary.total,3);
      assert.deepEqual(report.items.flatMap(i=>i.eventIds).sort(),['S-1','S-2','T-1','T-2']);
      assert.ok(report.items.find(i=>i.key==='T-1').ancestorPaths[0].includes('소속 Epic 없음'));
      const row=trackingRows(output).find(row=>row.id===local.id);
      assert.equal(row.counts.uniqueIssues,3); assert.equal(row.counts.created,3);
      assert.equal(trackingRows(output,{query:'소속 Epic 없음',matchedOnly:true}).filter(r=>r.key).length,4);
      assert.deepEqual(projectTrackingHierarchy(output),output);
    """)


def test_equal_labels_and_shared_tickets_have_distinct_stable_branch_groups():
    run_js("""
      const input=run([issue('T-1'),issue('T-2'),issue('T-3')],[
        container('module-a',[container('same-wbs',[ticket('shared','T-1'),ticket('two','T-2')])],'module'),
        container('module-b',[container('same-wbs',[ticket('shared','T-1'),ticket('three','T-3')])],'module')]);
      const output=projectTrackingHierarchy(input), [a,b]=groups(output);
      assert.ok(output.tree.every(node=>node.kind==='wbs'));
      assert.notEqual(output.tree[0].id,output.tree[1].id);
      assert.deepEqual(buildTrackingScope(output,output.tree[0].id).run.issues.map(i=>i.key),['T-1','T-2']);
      assert.deepEqual(buildTrackingScope(output,output.tree[1].id).run.issues.map(i=>i.key),['T-1','T-3']);
      const displayNodes=output.tree.flatMap(walk);
      assert.equal(new Set(displayNodes.map(n=>n.id)).size,displayNodes.length);
      assert.notEqual(a.id,b.id);
      const selected=buildTrackingScope(output,a.id);
      assert.deepEqual(selected.run.issues.map(i=>i.key),['T-1','T-2']);
      assert.deepEqual(selected.report.items.flatMap(i=>i.eventIds).sort(),['T-1','T-2']);
      assert.equal(selected.report.items.some(i=>i.key==='T-3'),false);
      const reversed=projectTrackingHierarchy({...input,tree:[...input.tree].reverse()});
      assert.deepEqual(groups(reversed).map(g=>g.id),[b.id,a.id]);
      const report=buildTrackingScope(output);
      assert.deepEqual(report.groups.flatMap(g=>g.keys).sort(),['T-1','T-2','T-3']);
      assert.equal(new Set(report.groups.flatMap(g=>g.keys)).size,3);
      const duplicates=projectTrackingHierarchy(run([issue('T-1')],[container('dup',[
        container('same',[ticket('shared','T-1')]),container('same',[ticket('shared','T-1')])])]));
      assert.equal(new Set(groups(duplicates).map(g=>g.id)).size,2);
      const unmapped=projectTrackingHierarchy(run([issue('T-1'),issue('T-2')],[
        container('unmapped',[ticket('one','T-1')],'module'),
        container('unmapped',[ticket('two','T-2')],'module')]));
      assert.notEqual(unmapped.tree[0].id,unmapped.tree[1].id);
      assert.deepEqual(buildTrackingScope(unmapped,unmapped.tree[0].id).run.issues.map(i=>i.key),['T-1']);
      assert.deepEqual(buildTrackingScope(unmapped,unmapped.tree[1].id).run.issues.map(i=>i.key),['T-2']);
      assert.deepEqual(projectTrackingHierarchy(unmapped),unmapped);
    """)


def test_modules_are_query_context_not_hierarchy_even_with_unmapped_and_voc_roots():
    run_js("""
      const input=run([issue('T-1'),issue('T-2'),issue('T-3'),
        issue('V-1',{voc:true,summary:'[계정 - 권한] 접근 요청'})],[
        {id:'module:ETL',kind:'module',name:'ETL',children:[container('wbs:a',[ticket('one','T-1')])]},
        {id:'unmapped',kind:'module',name:'WBS 미연결',children:[ticket('two','T-2')]},
        ticket('three','T-3'),ticket('voc','V-1')]);
      const original=JSON.stringify(input), voc=projectTrackingVoc(input), output=projectTrackingHierarchy(voc);
      assert.ok(output.tree.every(node=>node.kind.toLowerCase()==='wbs'));
      assert.ok(output.tree.flatMap(walk).every(node=>node.kind!=='module'));
      assert.equal(output.tree.find(node=>node.id==='wbs:a').name,'Same label');
      assert.equal(output.tree.find(node=>node.id==='unmapped').name,'WBS Task 미연결');
      assert.equal(output.tree.find(node=>node.vocGrouping).name,'사용자 VOC');
      assert.deepEqual(buildTrackingScope(output,'wbs:a').breadcrumbs.map(n=>n.title),['Same label']);
      const rows=trackingRows(output);
      assert.ok(rows.every(row=>row.path.every(node=>node.kind!=='module')));
      assert.deepEqual([...new Set(rows.filter(row=>row.key).map(row=>row.key))].sort(),['T-1','T-2','T-3','V-1']);
      assert.equal(output.events,voc.events); assert.equal(output.issues,voc.issues);
      assert.equal(output.summary,voc.summary); assert.equal(output.coverage,voc.coverage);
      assert.equal(JSON.stringify(input),original);
      assert.deepEqual(projectTrackingHierarchy(output),output);
    """)


def test_real_and_voc_epics_and_known_missing_boundaries_are_never_marked_unassigned():
    run_js("""
      const input=run([issue('E-1',{type:'Epic'}),issue('T-1'),
        issue('T-2',{epicKey:'E-absent'}),issue('S-1',{type:'Sub-Task',parentKey:'T-2'}),
        issue('T-3'),issue('V-1',{voc:true,summary:'[Account - Login] Fix'})],[
        container('w',[{id:'epic',kind:'Epic',issueKey:'E-1',name:'Real',children:[
          ticket('task','T-1',[ticket('nested','T-3')])]},ticket('known','T-2'),
          ticket('known-child','S-1',[],'Sub-Task'),ticket('voc','V-1')]),container('empty',[])]);
      const voc=projectTrackingVoc(input), output=projectTrackingHierarchy(voc);
      assert.deepEqual(groups(output),[]);
      assert.deepEqual(output.tree,voc.tree);
      const row=trackingRows(output).find(r=>r.key==='V-1');
      assert.ok(row.path.some(n=>n.vocGrouping));
      assert.equal(projectTrackingHierarchy(null),null);
      assert.deepEqual(projectTrackingHierarchy({issues:[],events:[],tree:[]}).tree,[]);
    """)


def test_other_epic_paths_and_parent_links_prevent_false_unassigned_membership():
    run_js("""
      const input=run([issue('E-1',{type:'Epic'}),issue('T-1'),
        issue('S-1',{type:'Sub-Task',parentKey:'T-1'}),
        issue('T-2',{parentKey:'E-1'}),issue('T-3')],[
        container('w',[{id:'real',issueKey:'E-1',kind:'Epic',children:[ticket('in-epic','T-1')]}]),
        container('unmapped',[ticket('duplicate','T-1'),ticket('child','S-1',[],'Sub-Task'),
          ticket('parent-link','T-2'),ticket('orphan','T-3')],'module')]);
      const output=projectTrackingHierarchy(input), [group]=groups(output);
      assert.equal(groups(output).length,1);
      assert.deepEqual(group.children.map(n=>n.issueKey),['T-3']);
      assert.deepEqual(output.tree[1].children.slice(0,3).map(n=>n.issueKey),['T-1','S-1','T-2']);
      assert.deepEqual(buildTrackingScope(output,group.id).run.events.map(e=>e.issueKey),['T-3']);
    """)


def test_existing_epic_subtrees_are_preserved_even_when_leaves_omit_children():
    run_js("""
      const originalEpic={id:'real',kind:'Epic',issueKey:'E-1',children:[
        {id:'leaf',kind:'Task',issueKey:'T-1',name:'Task without children'}]};
      const input=run([issue('E-1',{type:'Epic'}),issue('T-1'),issue('T-2')],[
        container('w',[originalEpic,ticket('loose','T-2')])]);
      const output=projectTrackingHierarchy(input);
      assert.deepEqual(output.tree[0].children[0],originalEpic);
      assert.equal(groups(output).length,1);
      assert.deepEqual(groups(output)[0].children.map(n=>n.issueKey),['T-2']);
    """)
