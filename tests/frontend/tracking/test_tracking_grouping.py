"""Two-level report grouping uses current Jira state and preserves every root once."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/trackingGroupingModel.js").as_uri()
    source = (
        "import assert from 'node:assert/strict';\nimport {groupTrackingReport} from "
        + json.dumps(module) + ";\n"
        + """
        const item=(key,status='Active',statusCategory='indeterminate',extra={})=>({
          key,rowId:'tracking-report:'+key,issue:{key,status,statusCategory},...extra});
        const keys=items=>items.map(i=>i.key);
        const fixture=()=>({
          items:[item('B','Ready','new'),item('A'),item('C','Closed','done'),
            item('D'),item('E','Mystery',null),item('F','Closed','done')],
          groups:[{key:'one',title:'One',pathLabel:'WBS / One',scopeId:'epic-1',voc:false,keys:['A','B','D']},
            {key:'two',title:'Two',pathLabel:'VOC / Two',scopeId:'epic-2',voc:true,keys:['A','C','E']},
            {key:'empty',title:'Empty',keys:['absent']}]
        });
        """ + script
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source], capture_output=True,
        text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_hierarchy_first_orders_current_states_and_preserves_membership_and_input():
    run_js("""
      const {items,groups}=fixture(), before=JSON.stringify({items,groups});
      const result=groupTrackingReport(items,groups);
      assert.deepEqual(result.map(g=>g.title),['One','Two','연결되지 않은 업무']);
      assert.deepEqual(result.map(g=>g.kind),['hierarchy','hierarchy','hierarchy']);
      assert.deepEqual(keys(result[0].items),['B','A','D']);
      assert.deepEqual(result[0].children.map(g=>[g.title,keys(g.items)]),[
        ['Active',['A','D']],['Ready',['B']]]);
      assert.deepEqual(result[1].children.map(g=>[g.title,keys(g.items)]),[
        ['Closed',['C']],['Mystery',['E']]]);
      assert.equal(result[1].scopeId,'epic-2'); assert.equal(result[1].voc,true);
      assert.equal(result[1].pathLabel,'VOC / Two');
      assert.deepEqual(result.flatMap(g=>keys(g.items)),['B','A','D','C','E','F']);
      assert.ok(result.every(g=>g.children.every(child=>child.kind==='status')));
      assert.equal(JSON.stringify({items,groups}),before);
    """)


def test_status_first_retains_global_item_order_and_distinct_hierarchy_child_keys():
    run_js("""
      const {items,groups}=fixture(), result=groupTrackingReport(items,groups,'status');
      assert.deepEqual(result.map(g=>g.title),['Active','Ready','Closed','Mystery']);
      assert.deepEqual(result.map(g=>g.kind),['status','status','status','status']);
      assert.deepEqual(keys(result[0].items),['A','D']);
      assert.deepEqual(keys(result[2].items),['C','F']);
      assert.deepEqual(result[2].children.map(g=>[g.title,keys(g.items)]),[
        ['Two',['C']],['연결되지 않은 업무',['F']]]);
      const child=result[2].children[0];
      assert.equal(child.kind,'hierarchy'); assert.equal(child.scopeId,'epic-2');
      assert.equal(child.pathLabel,'VOC / Two'); assert.equal(child.voc,true);
      const allChildren=result.flatMap(g=>g.children.map(child=>child.key));
      assert.equal(new Set(allChildren).size,allChildren.length);
      assert.deepEqual(result.flatMap(g=>g.children.flatMap(c=>keys(c.items))).sort(),
        ['A','B','C','D','E','F']);
    """)


def test_period_outcomes_do_not_infer_status_and_raw_status_keys_cannot_collide():
    run_js("""
      const items=[item('unknown',null,null,{category:'completed',transition:{to:'Closed'}}),
        item('reopened','Active','indeterminate',{category:'review',reopenedKeys:['reopened']}),
        item('same-new','Same','new'),item('same-done','Same','done'),
        item('colon-one','b:c','a'),item('colon-two','c','a:b'),
        item('pending','  Pending  ','new'),item('alpha','Alpha','indeterminate')];
      const result=groupTrackingReport(items,null,'status');
      assert.deepEqual(result.slice(0,5).map(g=>g.title),['Active','Alpha','  Pending  ','Same','Same']);
      assert.equal(result.find(g=>g.items[0].key==='unknown').title,'상태 미확인');
      assert.equal(result.find(g=>g.items[0].key==='reopened').title,'Active');
      assert.equal(new Set(result.map(g=>g.key)).size,result.length);
      assert.equal(result.filter(g=>g.title==='Same').length,2);
    """)


def test_null_groups_keep_voc_categories_and_duplicates_never_repeat_roots():
    run_js("""
      const voc=item('V'); voc.issue.voc=true;voc.issue.vocCategory='Login';
      const missing=item('M'); missing.issue.voc=true;
      const items=[voc,item('T'),missing,voc];
      const result=groupTrackingReport(items,null,'hierarchy');
      assert.deepEqual(result.map(g=>[g.title,g.voc,keys(g.items)]),[
        ['일반 업무',false,['T']],['Login',true,['V']],['분류 미지정',true,['M']]]);
      assert.deepEqual(groupTrackingReport([],null,'status'),[]);
      assert.deepEqual(groupTrackingReport([],[],'hierarchy'),[]);
      assert.deepEqual(keys(groupTrackingReport([item('X')],[])[0].items),['X']);
    """)


def test_unknown_category_names_cannot_inherit_object_properties_as_sort_ranks():
    run_js("""
      const result=groupTrackingReport([
        item('unknown','A custom state','constructor'),item('done','Closed','done')],null,'status');
      assert.deepEqual(result.map(g=>g.title),['Closed','A custom state']);
      assert.deepEqual(groupTrackingReport([
        item('todo','Queue','todo'),item('active','Active','indeterminate')],null,'status')
        .map(g=>g.title),['Active','Queue']);
    """)
