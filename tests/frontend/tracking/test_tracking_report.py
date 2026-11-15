"""Saved Tracking evidence becomes task reports without recollection or inference."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/trackingReportModel.js").as_uri()
    fixture = """
      const complete={complete:true,historyComplete:true,commentsComplete:true};
      const issue=(key,extra={})=>({key,summary:key+' title',type:'Task',coverage:complete,...extra});
      const event=(id,key,kind,date,extra={})=>({id,issueKey:key,kind,date,actorId:'alice',actorName:'Alice',...extra});
      const run=(issues,events,extra={})=>({status:'complete',coverage:{complete:true,missing:[]},issues,events,tree:[],...extra});
    """
    source = "import assert from 'node:assert/strict';\nimport * as m from " + json.dumps(module) + ";\n" + fixture + script
    result = subprocess.run([node, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr


def test_subtasks_roll_up_once_with_shared_paths_while_epic_own_activity_stays_separate():
    run_js("""
      const issues=[issue('E-1',{type:'Epic'}),issue('T-1',{parentKey:'',epicKey:'E-1'}),
        issue('S-1',{type:'Sub-Task',isSubtask:true,parentKey:'T-1'})];
      const own=event('epic','E-1','comment','2026-11-15T00:00:00Z',{body:'Epic discussion'});
      const done=event('done','S-1','completed','2026-11-15T01:00:00Z',{field:'status',from:'Active',to:'Closed'});
      const branch=name=>({id:name,kind:'module',name,children:[{kind:'wbs',name:'WBS',children:[
        {issueKey:'E-1',name:'Epic',kind:'Epic',children:[{issueKey:'T-1',name:'Task',kind:'Task',children:[
          {issueKey:'S-1',name:'Sub',kind:'Sub-Task'}]}]}]}]});
      const input=run(issues,[own,done,done],{tree:[branch('A'),branch('B')]});
      const before=JSON.stringify(input), report=m.buildTrackingReport(input);
      assert.equal(report.items.length,2);
      const task=report.items.find(i=>i.key==='T-1');
      assert.deepEqual(task.keys,['T-1','S-1']);
      assert.equal(task.events.length,1);assert.deepEqual(task.completedKeys,['S-1']);
      assert.equal(task.category,'changed');assert.match(task.headline,/하위.*1건.*완료/);
      assert.ok(!task.headline.includes('상위 업무 완료'));assert.equal(task.transition,null);
      assert.equal(task.paths.length,2);assert.ok(task.paths.some(p=>p.includes('A')));
      assert.equal(task.children[0].key,'S-1');assert.equal(task.children[0].events.length,1);
      assert.equal(report.items.find(i=>i.key==='E-1').events[0].id,'epic');
      assert.equal(JSON.stringify(input),before);
    """)


def test_current_done_never_implies_period_completion_and_parent_completion_is_explicit():
    run_js("""
      const issues=[issue('T-1',{statusCategory:'done'}),issue('T-2',{statusCategory:'done'})];
      let report=m.buildTrackingReport(run(issues,[event('child','T-2','comment','2026-11-15T01:00:00Z',{body:'Discuss'})]));
      assert.equal(report.items.find(i=>i.key==='T-1').category,'quiet');
      assert.equal(report.items.find(i=>i.key==='T-2').category,'changed');
      assert.equal(report.summary.completed,0);
      report=m.buildTrackingReport(run(issues,[event('done','T-1','completed','2026-11-15T01:00:00Z',{field:'status',from:'Active',to:'Closed'})]));
      const task=report.items.find(i=>i.key==='T-1');assert.equal(task.category,'completed');
      assert.deepEqual(task.transition,{from:'Active',to:'Closed'});assert.match(task.headline,/업무 완료/);
    """)


def test_latest_reopen_requires_review_but_recompletion_clears_that_category():
    run_js("""
      const issues=[issue('T-1'),issue('S-1',{type:'Sub-Task',isSubtask:true,parentKey:'T-1'})];
      const events=[event('done','S-1','completed','2026-11-15T01:00:00Z'),
        event('reopen','S-1','reopened','2026-11-15T02:00:00Z')];
      let item=m.buildTrackingReport(run(issues,events)).items[0];
      assert.equal(item.category,'review');assert.deepEqual(item.completedKeys,['S-1']);
      assert.deepEqual(item.reopenedKeys,['S-1']);assert.match(item.headline,/재오픈/);
      events.push(event('again','S-1','completed','2026-11-15T03:00:00Z'));
      item=m.buildTrackingReport(run(issues,events)).items[0];
      assert.equal(item.category,'changed'); // Child completion still is not parent completion.
      events.push(event('parentdone','T-1','completed','2026-11-15T04:00:00Z'));
      assert.equal(m.buildTrackingReport(run(issues,events)).items[0].category,'completed');
    """)


def test_context_only_parent_is_not_missing_but_unrelated_global_gaps_stay_provisional():
    run_js("""
      const issues=[issue('T-1',{contextOnly:true,coverage:{historyComplete:false,commentsComplete:false}}),
        issue('S-1',{type:'Sub-Task',isSubtask:true,parentKey:'T-1'})];
      let report=m.buildTrackingReport(run(issues,[event('done','S-1','completed','2026-11-15T01:00:00Z')]));
      assert.equal(report.items[0].uncertain,false);assert.equal(report.summary.provisional,false);
      report=m.buildTrackingReport(run(issues,[],{status:'partial',coverage:{complete:false,missing:[{issueKey:'OTHER-1',source:'issue'}]}}));
      assert.equal(report.items[0].uncertain,false);assert.equal(report.items[0].category,'quiet');
      assert.equal(report.summary.provisional,true);
      issues[1].coverage={historyComplete:false,commentsComplete:true};
      report=m.buildTrackingReport(run(issues,[]));
      assert.equal(report.items[0].uncertain,true);assert.equal(report.items[0].category,'review');
      assert.equal(report.summary.provisional,true);
    """)


def test_comment_creation_and_edit_share_one_comment_with_original_evidence_preserved():
    run_js("""
      const created=event('c-created','T-1','comment','2026-11-15T01:00:00Z',
        {commentId:'77',body:'Last observed body',observedUpdatedAt:'2026-11-15T03:00:00Z'});
      const edited=event('c-edited','T-1','commentEdited','2026-11-15T03:00:00Z',
        {commentId:'77',body:'Last observed body',actorId:'bob',actorName:'Bob'});
      const sourceEdit=event('c-source','T-1','commentEdited','2026-11-15T04:00:00Z',
        {source:{id:'88'},body:'Another comment edit'});
      const input=run([issue('T-1')],[created,edited,sourceEdit]);
      const item=m.buildTrackingReport(input).items[0];
      assert.equal(item.comments.length,2);
      const comment=item.comments.find(c=>c.commentId==='77');
      assert.equal(comment.body,'Last observed body');assert.equal(comment.actorId,'bob');
      assert.equal(comment.date,edited.date);assert.equal(comment.isEdited,true);
      assert.equal(comment.editedAt,edited.date);assert.equal(comment.createdAt,created.date);
      assert.deepEqual(new Set(comment.evidenceIds),new Set(['c-created','c-edited']));
      assert.equal(item.events.length,3);assert.equal(input.events[0].body,'Last observed body');
    """)


def test_copy_report_contains_only_selected_work_and_marks_partial_and_comment_edits():
    run_js(r"""
      const source=run([issue('T-1'),issue('T-2')],[
        event('one','T-1','commentEdited','2026-11-15T03:00:00Z',{commentId:'1',body:'<script>plain text</script> Updated note'}),
        event('two','T-2','completed','2026-11-15T04:00:00Z')],
        {status:'partial',coverage:{complete:false,missing:[{source:'query'}]}});
      const report=m.buildTrackingReport(source);
      const text=m.reportText(report,{request:{startDate:'2026-11-10',endDate:'2026-11-16',people:['alice'],modules:['M']},
        asOf:'2026-11-16T12:00:00+09:00',keys:['T-1']});
      assert.match(text,/2026-11-10/);assert.match(text,/2026-11-16/);assert.match(text,/alice/);assert.match(text,/M/);
      assert.match(text,/12:00:00/);assert.match(text,/잠정|일부.*미확인/);assert.match(text,/T-1/);
      assert.ok(!text.includes('T-2'));assert.match(text,/수정/);assert.match(text,/<script>plain text<\/script>/);
      assert.ok(!m.reportText(report,{keys:[]}).includes('T-1'));
    """)


def test_categories_are_exclusive_and_stably_sorted_by_priority_then_latest_activity():
    run_js("""
      const report=m.buildTrackingReport(run([issue('Q-1'),issue('C-1'),issue('R-1'),issue('D-1'),issue('D-2')],[
        event('c','C-1','completed','2026-11-15T01:00:00Z'),
        event('r','R-1','reopened','2026-11-15T02:00:00Z'),
        event('d1','D-1','created','2026-11-15T03:00:00Z'),
        event('d2','D-2','created','2026-11-15T04:00:00Z')]));
      assert.deepEqual(report.items.map(i=>i.key),['R-1','C-1','D-2','D-1','Q-1']);
      assert.deepEqual(report.summary,{total:5,completed:1,changed:2,review:1,quiet:1,provisional:false});
      assert.equal(new Set(report.items.flatMap(i=>i.keys)).size,5);
      assert.deepEqual(m.buildTrackingReport(null),{items:[],summary:{total:0,completed:0,changed:0,review:0,quiet:0,provisional:false}});
    """)


def test_other_child_completion_does_not_hide_unresolved_reopen_and_distinct_comments_stay_distinct():
    run_js("""
      const issues=[issue('T-1'),issue('S-1',{type:'Sub-Task',parentKey:'T-1'}),issue('S-2',{type:'Sub-Task',parentKey:'T-1'})];
      const events=[event('reopen','S-1','reopened','2026-11-15T01:00:00Z'),
        event('done','S-2','completed','2026-11-15T02:00:00Z'),
        event('comment-a','S-1','comment','2026-11-15T03:00:00Z',{commentId:'1',body:'Same text'}),
        event('comment-b','S-1','comment','2026-11-15T03:00:00Z',{commentId:'2',body:'Same text'})];
      const item=m.buildTrackingReport(run(issues,events)).items[0];
      assert.equal(item.category,'review');assert.deepEqual(item.completedKeys,['S-2']);
      assert.equal(item.comments.length,2);assert.equal(item.comments[0].body,'Same text');
    """)


def test_missing_parent_and_hierarchy_cycle_remain_review_without_duplicate_task_groups():
    run_js("""
      let report=m.buildTrackingReport(run([issue('S-1',{type:'Sub-Task',parentKey:'T-missing'})],
        [event('c','S-1','comment','2026-11-15T01:00:00Z',{body:'Observed'})]));
      assert.equal(report.items[0].key,'T-missing');assert.equal(report.items[0].uncertain,true);
      assert.equal(report.items[0].category,'review');
      report=m.buildTrackingReport(run([issue('S-1',{type:'Sub-Task',parentKey:'S-2'}),
        issue('S-2',{type:'Sub-Task',parentKey:'S-1'})],[event('c','S-1','created','2026-11-15T01:00:00Z')]));
      assert.equal(report.items.length,1);assert.equal(report.items[0].category,'review');
      assert.equal(report.items[0].events.length,1);
    """)


def test_copy_names_completed_children_and_retains_virtual_voc_group_path():
    run_js(r"""
      const input=run([issue('T-1',{summary:'Parent delivery'}),
        issue('S-1',{summary:'Fix account validation',type:'Sub-Task',parentKey:'T-1'})],
        [event('done','S-1','completed','2026-11-15T01:00:00Z')],{tree:[
          {name:'VOC',kind:'module',children:[{name:'[Payments] Requests',kind:'wbs',children:[
            {name:'[Payments] Login',kind:'epic',children:[{issueKey:'T-1',name:'Parent delivery',kind:'Task'}]}]}]}
        ]});
      const text=m.reportText(m.buildTrackingReport(input),{keys:['T-1']});
      assert.match(text,/S-1/);assert.match(text,/Fix account validation/);
      assert.match(text,/\[Payments\] Requests/);assert.match(text,/\[Payments\] Login/);
    """)


def test_copy_child_completion_history_distinguishes_reopen_and_recompletion():
    run_js(r"""
      const issues=[issue('T-1'),...['S-1','S-2','S-3'].map(key=>issue(key,{type:'Sub-Task',parentKey:'T-1'}))];
      const source=run(issues,[
        event('done1','S-1','completed','2026-11-15T01:00:00Z'),
        event('reopen1','S-1','reopened','2026-11-15T02:00:00Z'),
        event('done2','S-2','completed','2026-11-15T01:00:00Z'),
        event('reopen2','S-2','reopened','2026-11-15T02:00:00Z'),
        event('again2','S-2','completed','2026-11-15T03:00:00Z'),
        event('done3','S-3','completed','2026-11-15T01:00:00Z')]);
      const text=m.reportText(m.buildTrackingReport(source));
      const lines=text.split('\n');
      const first=lines.find(line=>line.startsWith('- 하위 완료 이력: [S-1]'));
      const second=lines.find(line=>line.startsWith('- 하위 완료 이력: [S-2]'));
      const third=lines.find(line=>line.startsWith('- 하위 완료 이력: [S-3]'));
      assert.match(first,/마지막 확인 이력: 재오픈/);
      assert.match(second,/재오픈 후 완료 확인/);
      assert.ok(third);assert.ok(!third.includes('재오픈'));
    """)
