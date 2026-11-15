"""Tracking keeps shared tickets, historical evidence and display filters distinct."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    model = (REPO_ROOT / "app/static/components/trackingModel.js").as_uri()
    script = "import assert from 'node:assert/strict';\nimport * as m from " + json.dumps(model) + ";\n" + script
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr


def test_dates_are_inclusive_kst_and_previous_week_is_calendar_week():
    run_js("""
      const now = new Date('2026-11-15T16:05:00Z'); // Monday in Korea
      assert.equal(m.kstToday(now), '2026-11-16');
      assert.deepEqual(m.dateRange('week', now), {startDate:'2026-11-10', endDate:'2026-11-16'});
      assert.deepEqual(m.dateRange('lastWeek', now), {startDate:'2026-11-09', endDate:'2026-11-15'});
      assert.deepEqual(m.dateRange('today', now), {startDate:'2026-11-16', endDate:'2026-11-16'});
      assert.ok(m.dateError('2026-11-17', '2026-11-16', now));
      assert.ok(m.dateError('2026-02-30', '2026-11-16', now));
      assert.equal(m.dateError('2026-11-16', '2026-11-16', now), '');
    """)


def test_local_match_preserves_ancestors_highlight_and_selection_do_not_hide_rows():
    run_js("""
      const issues = [{key:'L-1',summary:'복구',assigneeId:'a',assigneeName:'민준',coverage:{historyComplete:true,commentsComplete:true}},
        {key:'L-2',summary:'동료',assigneeId:'b',coverage:{historyComplete:false,commentsComplete:true}}];
      const tree = [{id:'w',name:'WBS',kind:'wbs',children:[{id:'e',issueKey:'EP-1',name:'Epic',kind:'Epic',children:[
        {id:'t1',issueKey:'L-1',name:'복구',kind:'Task',children:[]},
        {id:'t2',issueKey:'L-2',name:'동료',kind:'task',children:[]}]}]}];
      const run = {tree,issues,events:[]};
      assert.equal(m.trackingRows(run,{highlight:{kind:'person',value:'a'}}).length,4);
      const matches = m.trackingRows(run,{query:'복 구',matchedOnly:true,closed:{w:true,e:true}});
      assert.deepEqual(matches.map(r=>r.id),['w','e','t1']);
      assert.equal(matches[0].matched,false);
      assert.equal(matches[2].matched,true);
      assert.deepEqual(m.trackingRows(run,{highlight:{kind:'person',value:'a'},matchedOnly:true}).map(r=>r.id),['w','e','t1']);
      assert.equal(m.trackingRows(run,{closed:{e:true}}).length,2);
      const epic = m.trackingRows(run,{highlight:{kind:'epic',value:'EP-1'},matchedOnly:true});
      assert.equal(epic[1].kind,'epic');
      assert.equal(epic.length,4);
    """)


def test_rollups_deduplicate_shared_tickets_and_never_turn_unknown_history_into_zero():
    run_js("""
      const issue={key:'L-1',summary:'Shared',coverage:{historyComplete:false,commentsComplete:true}};
      const run={issues:[issue],events:[
        {id:'c1',issueKey:'L-1',kind:'completed',date:'2026-11-15T16:00:00Z'},
        {id:'r',issueKey:'L-1',kind:'reopened',date:'2026-11-16T02:00:00Z'},
        {id:'c2',issueKey:'L-1',kind:'completed',date:'2026-11-16T03:00:00Z'},
        {id:'f',issueKey:'L-1',kind:'fieldChanged',field:'duedate',from:'a',to:'b',date:'2026-11-16T04:00:00Z'},
      ]};
      const node={children:[{issueKey:'L-1'},{issueKey:'L-1'}]};
      const counts=m.nodeCounts(node,run);
      assert.equal(counts.uniqueIssues,1);
      assert.equal(counts.completed,1); // unique ticket; two completion events stay in evidence
      assert.equal(counts.fieldChanges,1);
      assert.equal(counts.historyComplete,false);
      assert.equal(m.countLabel(0,false),'미확인');
      assert.equal(m.countLabel(2,false),'2+');
      assert.equal(m.countLabel(0,true),'–');
      const groups=m.groupEvents(run.events);
      assert.equal(groups.length,1);
      assert.equal(groups[0].date,'2026-11-16');
      assert.equal(groups[0].events.length,4);
    """)


def test_context_ancestors_do_not_make_complete_target_rollups_unknown():
    run_js("""
      const run={issues:[
        {key:'E-1',contextOnly:true,coverage:{historyComplete:false,commentsComplete:false}},
        {key:'T-1',coverage:{historyComplete:true,commentsComplete:true}},
      ],events:[{id:'done',issueKey:'T-1',kind:'completed',date:'2026-11-01T00:00:00Z'}],tree:[
        {id:'epic',issueKey:'E-1',kind:'Epic',contextOnly:true,children:[
          {id:'task',issueKey:'T-1',kind:'Task',contextOnly:true},
        ]},
      ]};
      const rows=m.trackingRows(run);
      assert.equal(rows[0].counts.historyComplete,true);
      assert.equal(rows[0].counts.commentsComplete,true);
      assert.equal(rows[0].counts.uniqueIssues,1);
      assert.equal(m.countLabel(rows[0].counts.completed,rows[0].counts.historyComplete),'1');
      assert.equal(rows[0].contextOnly,true);
      // A tree node with no own events is still a real investigation target.
      assert.equal(rows[1].contextOnly,false);
      run.issues[1].coverage.historyComplete=false;
      assert.equal(m.trackingRows(run)[0].counts.historyComplete,false);
    """)


def test_virtual_voc_epic_highlight_keeps_category_and_matching_tickets():
    run_js("""
      const run={issues:[{key:'V-1'},{key:'V-2'}],events:[],tree:[{id:'voc',kind:'wbs',virtual:true,name:'사용자 VOC',children:[
        {id:'voc:category:a',kind:'Epic',virtual:true,name:'적재 - 신규',children:[{id:'v1',issueKey:'V-1',kind:'Task'}]},
        {id:'voc:category:b',kind:'Epic',virtual:true,name:'질의 - 확인',children:[{id:'v2',issueKey:'V-2',kind:'Task'}]},
      ]}]};
      const rows=m.trackingRows(run,{highlight:{kind:'epic',value:'voc:category:a'},matchedOnly:true});
      assert.deepEqual(rows.map(row=>row.id),['voc','voc:category:a','v1']);
    """)
