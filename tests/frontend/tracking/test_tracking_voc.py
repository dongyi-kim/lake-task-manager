"""VOC grouping is a display projection, never a source or event mutation."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/trackingVocModel.js").as_uri()
    source = "import assert from 'node:assert/strict';\nimport {projectTrackingVoc} from " + json.dumps(module) + ";\n" + script
    result = subprocess.run([node, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


def test_component_voc_moves_all_shared_paths_and_subtasks_without_mutating_saved_evidence():
    run_js("""
      const coverage={complete:true}, summary={completed:1};
      const events=[{id:'c',issueKey:'L-1',kind:'comment',body:'original'}];
      const run={coverage,summary,events,issues:[
        {key:'L-1',type:'Task',summary:'[ 적재 - 지연 ] 소비자 배치 복구',modules:['사용자 VoC'],epicKey:'E-1'},
        {key:'L-2',type:'Sub-Task',summary:'[일반 - 접두어] 재현',parentKey:'L-1',modules:['ETL']},
        {key:'L-3',type:'Task',summary:'[ 적재 - 지연 ] 일반 개발 Task',modules:['ETL']},
      ],tree:[{id:'w',kind:'wbs',name:'원래WBS',children:[{id:'e',kind:'Epic',issueKey:'E-1',name:'Epic',children:[
        {id:'t',kind:'Task',issueKey:'L-1',name:'원래 제목',children:[{id:'s',issueKey:'L-2',kind:'Sub-Task'}]},
        {id:'regular',issueKey:'L-3',kind:'Task'},
      ]}]},{id:'shared',kind:'wbs',children:[{id:'repeat',issueKey:'L-1',kind:'Task'}]}]};
      const original=JSON.stringify(run), output=projectTrackingVoc(run);
      assert.equal(JSON.stringify(run),original);
      assert.equal(output.events,events); assert.equal(output.coverage,coverage); assert.equal(output.summary,summary);
      const walk=n=>[n,...(n.children||[]).flatMap(walk)]; const nodes=output.tree.flatMap(walk);
      assert.equal(nodes.filter(n=>n.issueKey==='L-1').length,1);
      assert.equal(nodes.filter(n=>n.issueKey==='L-2').length,1);
      const wbs=output.tree.find(n=>n.virtual&&n.kind==='wbs'); assert.equal(wbs.name,'사용자 VOC');
      const epic=wbs.children[0]; assert.equal(epic.name,'적재 - 지연'); assert.equal(epic.virtual,true);
      assert.ok(!('issueKey' in epic)); assert.ok(!('key' in epic));
      assert.equal(epic.children[0].issueKey,'L-1'); assert.equal(epic.children[0].children[0].issueKey,'L-2');
      assert.equal(epic.children[0].name,'소비자 배치 복구');
      assert.equal(output.issues[0].summary,'소비자 배치 복구');
      assert.equal(output.issues[0].originalSummary,run.issues[0].summary);
      assert.equal(output.issues[0].voc,true); assert.equal(output.issues[0].vocCategory,'적재 - 지연');
      assert.equal(output.issues[1].vocCategory,'적재 - 지연');
      assert.equal(output.issues[1].summary,run.issues[1].summary);
      assert.equal(output.issues[2].summary,run.issues[2].summary);
      assert.equal(nodes.find(n=>n.issueKey==='L-3').id,'regular');
      assert.equal(projectTrackingVoc(run).tree.at(-1).children[0].id,epic.id);
      assert.deepEqual(projectTrackingVoc(output),output);
    """)


def test_custom_components_flags_missing_prefix_and_empty_title_keep_safe_fallbacks():
    run_js("""
      const run={issues:[
        {key:'L-1',summary:'[ 분류 - 상세 ]',components:[{name:'CUSTOM VOC'}]},
        {key:'L-2',summary:'접두어 없는 요청',voc:true},
        {key:'L-3',summary:'[ 일반 - 요청 ] 일반Task',components:['ETL']},
        {key:'L-4',summary:'[분류-상세] 실제 요청',components:['custom voc']},
        {key:'L-5',summary:'[분류-상세] 기본값 불일치',modules:['사용자 VoC']},
      ],events:[],tree:[]};
      const projected=projectTrackingVoc(run,{vocComponent:'Custom Voc'});
      const wbs=projected.tree[0]; assert.equal(wbs.children.length,2);
      const category=wbs.children.find(n=>n.name==='분류 - 상세');
      assert.deepEqual(category.children.map(n=>n.issueKey),['L-1','L-4']);
      assert.equal(projected.issues[0].summary,'[ 분류 - 상세 ]');
      assert.equal(projected.issues[3].summary,'실제 요청');
      assert.equal(projected.issues[2].summary,run.issues[2].summary);
      assert.equal(projected.issues[4].summary,run.issues[4].summary);
      assert.equal(wbs.children.find(n=>n.name==='분류 미지정').children[0].issueKey,'L-2');
      assert.equal(projectTrackingVoc({issues:[],tree:[],events:[]}).tree.length,0);
      assert.equal(projectTrackingVoc(null),null);
    """)


def test_voc_projection_is_idempotent_and_deduplicates_voc_subtask_own_membership():
    run_js("""
      const run={issues:[{key:'L-1',summary:'[분류-상세] 요청',voc:true},
        {key:'L-2',type:'Sub-Task',isSubtask:true,summary:'[하위-상세] 확인',voc:true,parentKey:'L-1'}],tree:[],events:[]};
      const once=projectTrackingVoc(run), twice=projectTrackingVoc(once);
      assert.deepEqual(twice,once);
      assert.equal(once.tree[0].children.length,1);
      assert.equal(once.tree[0].children[0].children[0].children[0].issueKey,'L-2');
    """)


def test_options_publish_configured_voc_component_with_existing_default():
    from types import SimpleNamespace
    from app.tracking.manager import TrackingManager
    manager = object.__new__(TrackingManager)
    manager.authorize = lambda: None
    manager.people_loader = lambda: {}
    manager.client = SimpleNamespace(s=SimpleNamespace(voc_component="고객 VOC"))
    assert manager.options()["vocComponent"] == "고객 VOC"
    manager.client.s = SimpleNamespace()
    assert manager.options()["vocComponent"] == "사용자 VoC"


def test_non_voc_task_below_a_real_voc_epic_is_preserved_in_original_wbs():
    run_js("""
      const run={issues:[{key:'E-1',type:'Epic',summary:'[ 요청 - 공통 ] 문의',voc:true},
        {key:'T-1',type:'Task',summary:'일반 작업',epicKey:'E-1',modules:['ETL']}],events:[],tree:[
        {id:'w',kind:'wbs',name:'ETL',children:[{id:'e',issueKey:'E-1',kind:'Epic',children:[
          {id:'t',issueKey:'T-1',kind:'Task',children:[]}
        ]}]}
      ]};
      const out=projectTrackingVoc(run);
      assert.equal(out.tree[0].children[0].issueKey,'T-1');
      assert.equal(out.tree.at(-1).children[0].children[0].issueKey,'E-1');
      assert.equal(out.issues[1].voc,undefined);
    """)
