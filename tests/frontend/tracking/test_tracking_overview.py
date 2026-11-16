"""Overview statements retain the distinction between period evidence and saved current state."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    overview = (REPO_ROOT / "app/static/components/trackingOverviewModel.js").as_uri()
    report = (REPO_ROOT / "app/static/components/trackingReportModel.js").as_uri()
    source = (
        "import assert from 'node:assert/strict';\nimport * as m from " + json.dumps(overview)
        + ";\nimport {buildTrackingReport} from " + json.dumps(report) + ";\n"
        + """
        const complete={complete:true,historyComplete:true,commentsComplete:true};
        const issue=(key,extra={})=>({key,summary:key+' title',type:'Task',status:'Active',statusCategory:'indeterminate',coverage:complete,...extra});
        const child=(key,parentKey,extra={})=>issue(key,{type:'Sub-Task',isSubtask:true,parentKey,...extra});
        const event=(id,issueKey,kind,date,extra={})=>({id,issueKey,kind,date,actorId:'alice',actorName:'Alice',...extra});
        const report=(issues,events=[],extra={})=>buildTrackingReport({status:'complete',coverage:{complete:true,missing:[]},issues,events,tree:[],...extra});
        """ + script
    )
    result = subprocess.run([node, "--input-type=module", "-e", source], capture_output=True,
                            text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr


def test_completion_history_counts_do_not_claim_reopened_or_unknown_roots_are_done():
    run_js("""
      const input=report([issue('T-1',{status:'Closed',statusCategory:'done'}),issue('T-2'),
        issue('T-3',{status:'Closed',statusCategory:null}),issue('T-4',{statusCategory:'done'})],[
        event('one','T-1','completed','2026-11-15T01:00:00Z'),
        event('two','T-2','completed','2026-11-15T02:00:00Z'),
        event('again','T-2','completed','2026-11-15T03:00:00Z'),
        event('reopen','T-2','reopened','2026-11-15T04:00:00Z'),
        event('three','T-3','completed','2026-11-15T05:00:00Z')]);
      const before=JSON.stringify(input), result=m.buildTrackingOverview(input);
      assert.equal(result.totals.rootCompletionHistory,3);
      assert.equal(result.totals.currentDoneAfterCompletion,1);
      assert.equal(result.totals.currentNotDoneAfterCompletion,1);
      assert.equal(result.totals.currentUnknownAfterCompletion,1);
      assert.equal(result.totals.reopenedRoots,1);
      assert.ok(result.followups.some(i=>i.key==='T-2'));
      assert.ok(result.followups.some(i=>i.key==='T-3'));
      assert.equal(JSON.stringify(input),before);
      const row=m.trackingRowSummary(input.items.find(i=>i.key==='T-2'));
      assert.equal(row.currentDone,false);assert.equal(row.attention,true);
      assert.match(row.headline,/완료 이력/);assert.match(row.detail,/재오픈/);
      assert.equal(row.evidenceId,'reopen');
    """)


def test_saved_current_state_is_independent_of_last_period_boundary():
    run_js("""
      // These snapshots were fetched after the report's end date. No later events were queried.
      const input=report([issue('T-1',{status:'Active',updated:'2026-11-18T00:00:00Z'}),
        issue('T-2',{status:'Closed',statusCategory:'done',updated:'2026-11-18T00:00:00Z'})],[
        event('done','T-1','completed','2026-11-15T01:00:00Z'),
        event('reopen','T-2','reopened','2026-11-15T02:00:00Z')]);
      const overview=m.buildTrackingOverview(input);
      assert.equal(overview.totals.currentDoneAfterCompletion,0);
      assert.equal(overview.totals.currentNotDoneAfterCompletion,1);
      assert.equal(overview.totals.rootCompletionHistory,1);
      assert.ok(!overview.highlights.some(h=>h.kind==='completed'));
      const doneNow=m.trackingRowSummary(input.items.find(i=>i.key==='T-2'));
      assert.equal(doneNow.currentDone,true);assert.equal(doneNow.attention,false);
      assert.match(doneNow.headline,/재오픈 이력/);
      assert.ok(!overview.followups.some(i=>i.key==='T-2'));
    """)


def test_child_completion_is_distinct_history_and_never_a_full_tree_completion_ratio():
    run_js("""
      const input=report([issue('T-1'),child('S-1','T-1'),child('S-2','T-1',{statusCategory:'done'})],[
        event('child-done','S-1','completed','2026-11-15T01:00:00Z'),
        event('child-again','S-1','completed','2026-11-15T02:00:00Z'),
        event('child-open','S-1','reopened','2026-11-15T03:00:00Z'),
        event('other-done','S-2','completed','2026-11-15T04:00:00Z')]);
      const overview=m.buildTrackingOverview(input), row=m.trackingRowSummary(input.items[0]);
      assert.equal(overview.totals.childCompletionHistory,2);
      assert.equal(overview.totals.rootCompletionHistory,0);
      assert.match(row.headline,/하위 업무 2건 완료 이력/);
      assert.match(row.detail,/재오픈/);
      assert.equal(row.attention,true);
      assert.ok(!JSON.stringify(overview).includes('%'));
      assert.ok(!row.headline.includes('/'));assert.ok(!row.detail.includes('/'));
      assert.match(row.remainingLabel,/조회된 하위/);
    """)


def test_missing_sources_stay_provisional_and_do_not_imply_zero_or_estimated_counts():
    run_js("""
      const input=report([issue('T-1',{coverage:{complete:false,historyComplete:false,commentsComplete:true}})],[],
        {status:'partial',coverage:{complete:false,missing:[{issueKey:'T-1',source:'history'}]}});
      const overview=m.buildTrackingOverview(input), row=m.trackingRowSummary(input.items[0]);
      assert.equal(overview.totals.provisional,true);assert.equal(overview.totals.rootCompletionHistory,0);
      assert.equal(overview.totals.review,1);assert.equal(overview.followups[0].key,'T-1');
      assert.match(row.headline,/미확인/);assert.equal(row.attention,true);
      assert.ok(!JSON.stringify(overview).includes('+'));
      assert.equal(overview.highlights[0].kind,'review');
    """)


def test_comments_are_explicit_recent_comment_excerpts_and_edits_count_once():
    run_js("""
      const input=report([issue('T-1')],[
        event('created','T-1','comment','2026-11-15T01:00:00Z',{commentId:'7',body:'승인 대기'}),
        event('edited','T-1','commentEdited','2026-11-15T02:00:00Z',{commentId:'7',body:'검증 완료, 승인 대기'})]);
      const overview=m.buildTrackingOverview(input), row=m.trackingRowSummary(input.items[0]);
      assert.equal(overview.totals.comments,1);assert.equal(overview.totals.rootCompletionHistory,0);
      assert.equal(overview.highlights.length,0);
      assert.match(row.headline,/댓글 1건/);assert.match(row.detail,/최근 댓글/);
      assert.match(row.detail,/검증 완료, 승인 대기/);assert.equal(row.evidenceId,'edited');
      assert.ok(!row.headline.includes('완료'));assert.ok(!row.headline.includes('진척'));
    """)


def test_highlights_name_distinct_roots_and_choose_recent_completion_progress_and_review():
    run_js("""
      const input=report([issue('T-1',{statusCategory:'done'}),issue('T-2',{statusCategory:'done'}),
        issue('T-3'),child('S-1','T-3'),issue('T-4')],[
        event('old','T-1','completed','2026-11-15T01:00:00Z'),
        event('new','T-2','completed','2026-11-15T02:00:00Z'),
        event('sub','S-1','completed','2026-11-15T03:00:00Z'),
        event('review','T-4','reopened','2026-11-15T04:00:00Z')]);
      const result=m.buildTrackingOverview(input);
      assert.deepEqual(result.highlights.map(i=>[i.kind,i.key]),[['completed','T-2'],['progress','T-3'],['review','T-4']]);
      assert.equal(new Set(result.highlights.map(i=>i.key)).size,3);
      assert.ok(result.highlights.every(i=>i.title===i.key+' title' && i.headline && i.evidenceId));
      assert.equal(result.highlights[0].evidenceId,'new');
    """)


def test_no_activity_and_empty_runs_do_not_invent_delivery_or_followups():
    run_js("""
      let result=m.buildTrackingOverview(null);
      assert.equal(result.totals.roots,0);assert.deepEqual(result.highlights,[]);assert.deepEqual(result.followups,[]);
      const input=report([issue('T-1',{statusCategory:'done'})]);
      result=m.buildTrackingOverview(input);
      assert.equal(result.totals.roots,1);assert.equal(result.totals.rootCompletionHistory,0);
      assert.deepEqual(result.highlights,[]);assert.deepEqual(result.followups,[]);
      assert.match(m.trackingRowSummary(input.items[0]).headline,/확인된 활동 없음/);
    """)


def test_large_report_preserves_exact_counts_and_limits_named_followups_without_lower_bounds():
    run_js("""
      const issues=Array.from({length:100},(_,i)=>issue('T-'+i));
      const events=issues.flatMap((item,i)=>[
        event('new-'+i,item.key,'created','2026-11-15T01:00:00Z'),
        event('done-'+i,item.key,'completed','2026-11-15T02:00:00Z'),
        event('open-'+i,item.key,'reopened','2026-11-15T03:00:00Z')]);
      const result=m.buildTrackingOverview(report(issues,events));
      assert.equal(result.totals.roots,100);assert.equal(result.totals.rootCompletionHistory,100);
      assert.equal(result.totals.currentNotDoneAfterCompletion,100);assert.equal(result.totals.newRoots,100);
      assert.equal(result.totals.reopenedRoots,100);assert.equal(result.totals.review,100);
      assert.equal(result.followups.length,3);assert.ok(result.highlights.length<=3);
      assert.deepEqual(m.buildTrackingOverview(report([...issues].reverse(),[...events].reverse())),result);
    """)


def test_row_categories_follow_saved_current_state_without_changing_raw_report_categories():
    run_js("""
      const input=report([issue('T-1'),issue('T-2',{statusCategory:'done'}),
        issue('T-3',{statusCategory:'done'}),issue('T-4'),issue('T-5')],[
        event('done-one','T-1','completed','2026-11-15T01:00:00Z'),
        event('open-two','T-2','reopened','2026-11-15T02:00:00Z'),
        event('done-three','T-3','completed','2026-11-15T03:00:00Z'),
        event('new-five','T-5','created','2026-11-15T04:00:00Z')]);
      const row=key=>m.trackingRowSummary(input.items.find(item=>item.key===key));
      assert.equal(input.items.find(item=>item.key==='T-1').category,'completed');
      assert.equal(input.items.find(item=>item.key==='T-2').category,'review');
      assert.equal(row('T-1').category,'review');assert.match(row('T-1').remainingLabel,/현재 완료 미확인/);
      assert.equal(row('T-2').category,'changed');assert.equal(row('T-2').remainingLabel,'');
      assert.equal(row('T-3').category,'completed');assert.equal(row('T-3').remainingLabel,'');
      assert.equal(row('T-4').category,'quiet');assert.match(row('T-4').remainingLabel,/기간 내 실제 작업 여부 미확인/);
      assert.equal(row('T-5').category,'changed');assert.equal(row('T-5').remainingLabel,'');
      const partial=report([issue('T-6',{coverage:{complete:false}})]).items[0];
      assert.equal(m.trackingRowSummary(partial).category,'review');
      assert.match(m.trackingRowSummary(partial).remainingLabel,/수집 근거 일부 미확인/);
    """)


@pytest.mark.parametrize("current_done", [False, True], ids=["still-open", "done-after-period"])
def test_child_reopen_without_period_completion_is_activity_even_if_currently_done(current_done):
    run_js("""
      const currentDone=""" + json.dumps(current_done) + """;
      const input=report([issue('T-1'),child('S-1','T-1',{
        status:currentDone?'Closed':'Active',statusCategory:currentDone?'done':'indeterminate'})],[
        event('child-open','S-1','reopened','2026-11-15T03:00:00Z')]);
      const overview=m.buildTrackingOverview(input), row=m.trackingRowSummary(input.items[0]);
      assert.match(row.headline,/하위 업무 1건 재오픈 이력/);
      assert.ok(!row.headline.includes('활동 없음'));assert.equal(row.evidenceId,'child-open');
      assert.equal(row.category,currentDone?'changed':'review');assert.equal(row.attention,!currentDone);
      assert.equal(overview.totals.review,currentDone?0:1);
      assert.equal(overview.totals.childCompletionHistory,0);
    """)


def test_progress_highlight_uses_child_work_or_changes_instead_of_a_second_root_completion_story():
    run_js("""
      const issues=[issue('T-1',{statusCategory:'done'}),issue('T-2',{statusCategory:'done'}),
        child('S-1','T-1',{statusCategory:'done'}),issue('T-3')];
      const events=[event('done-one','T-1','completed','2026-11-15T01:00:00Z'),
        event('child-done','S-1','completed','2026-11-15T02:00:00Z'),
        event('done-two','T-2','completed','2026-11-15T03:00:00Z'),
        event('change','T-3','fieldChanged','2026-11-15T04:00:00Z',{field:'priority',from:'Low',to:'High'})];
      let result=m.buildTrackingOverview(report(issues,events));
      assert.deepEqual(result.highlights.map(item=>[item.kind,item.key]),[['completed','T-2'],['progress','T-3']]);
      result=m.buildTrackingOverview(report(issues.filter(item=>item.key!=='T-3'),events.filter(item=>item.issueKey!=='T-3')));
      assert.deepEqual(result.highlights.map(item=>[item.kind,item.key]),[['completed','T-2']]);
    """)


def test_root_completion_details_name_child_evidence_or_dated_comment_or_transition():
    run_js("""
      const input=report([issue('T-1',{statusCategory:'done'}),child('S-1','T-1',{summary:'한도 검증 추가',statusCategory:'done'}),
        issue('T-2',{statusCategory:'done'}),issue('T-3',{statusCategory:'done'})],[
        event('done-one','T-1','completed','2026-11-15T01:00:00Z'),
        event('child-done','S-1','completed','2026-11-15T02:00:00Z'),
        event('done-two','T-2','completed','2026-11-15T02:00:00Z'),
        event('comment','T-2','comment','2026-11-15T03:00:00Z',{commentId:'7',body:'운영 승인 대기'}),
        event('done-three','T-3','completed','2026-11-15T04:00:00Z',{field:'status',from:'Active',to:'Closed'})]);
      const row=key=>m.trackingRowSummary(input.items.find(item=>item.key===key));
      assert.match(row('T-1').detail,/하위 완료 이력.*한도 검증 추가.*S-1/);
      assert.match(row('T-2').detail,/11\\.15 최근 댓글.*운영 승인 대기/);
      assert.equal(row('T-2').evidenceId,'comment');
      assert.match(row('T-3').detail,/11\\.15.*완료 이력.*Active.*Closed/);
      assert.equal(row('T-3').evidenceId,'done-three');
      assert.ok(!row('T-2').detail.includes('진척'));
    """)
