"""Ticket timelines use only saved own-issue events within the requested KST dates."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    module = (REPO_ROOT / 'app/static/components/trackingTicketTimelineModel.js').as_uri()
    source = 'import assert from "node:assert/strict";\nimport {buildTicketTimeline} from ' + json.dumps(module) + ';\n' + script
    result = subprocess.run([node, '--input-type=module', '-e', source], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr


def test_timeline_preserves_same_day_steps_comments_kst_boundaries_and_empty_day_spacing():
    run_js("""
      const issue={key:'T-1',status:'Currently Done',coverage:{historyComplete:true,commentsComplete:true}};
      const request={startDate:'2026-11-10',endDate:'2026-11-13'};
      const event=(id,kind,date,extra={})=>({id,kind,issueKey:'T-1',date,...extra});
      const events=[
        event('reopen','reopened','2026-11-12T02:00:00Z',{from:'Done',to:'Doing'}),
        event('comment-b','comment','2026-11-12T00:00:00Z',{bodyText:'Report B'}),
        event('done','completed','2026-11-12T01:00:00Z',{from:'Doing',to:'Done'}),
        event('comment-a','comment','2026-11-12T00:00:00Z',{bodyText:'Report A'}),
        event('generic','fieldChanged','2026-11-12T03:00:00Z',{field:'Status',from:'Doing',to:'Review'}),
        event('edit','commentEdited','2026-11-12T04:00:00Z',{bodyText:'Updated report'}),
        event('before','comment','2026-11-09T14:59:59Z'),
        event('first','comment','2026-11-09T15:00:00Z'),
        event('last','comment','2026-11-13T14:59:59Z'),
        event('after','comment','2026-11-13T15:00:00Z'),
        event('child','completed','2026-11-12T01:00:00Z',{issueKey:'S-1'}),
        event('title','fieldChanged','2026-11-12T01:00:00Z',{field:'summary'}),
      ];
      events.push({...events[0]});
      const before=JSON.stringify({issue,events,request});
      const model=buildTicketTimeline(issue,events,request);
      assert.equal(model.rangeKnown,true); assert.equal(model.partial,false);
      assert.deepEqual(model.days.map(day=>day.date),['2026-11-10','2026-11-11','2026-11-12','2026-11-13']);
      assert.equal(model.days[1].transitions.length+model.days[1].comments.length,0);
      assert.deepEqual(model.days[2].transitions.map(item=>item.id),['done','reopen','generic']);
      assert.deepEqual(model.days[2].comments.map(item=>item.id),['comment-a','comment-b','edit']);
      assert.deepEqual(model.days[2].transitions.map(item=>[item.from,item.to]),[['Doing','Done'],['Done','Doing'],['Doing','Review']]);
      assert.equal(model.transitionCount,3); assert.equal(model.commentCount,5);
      assert.equal(model.days[0].comments[0].time,'00:00'); assert.equal(model.days[3].comments[0].time,'23:59');
      assert.equal(model.days[2].comments[0].event,events.find(item=>item.id==='comment-a'));
      assert.equal(JSON.stringify({issue,events,request}),before);
    """)


def test_zero_one_and_partial_timelines_never_infer_the_period_start_from_current_status():
    run_js("""
      const request={startDate:'2026-11-10',endDate:'2026-11-10'};
      const complete={key:'T-1',status:'Done',coverage:{historyComplete:true,commentsComplete:true}};
      const empty=buildTicketTimeline(complete,[],request);
      assert.equal(empty.transitionCount,0); assert.equal(empty.commentCount,0); assert.equal(empty.partial,false);
      assert.deepEqual(empty.days[0].transitions,[]);
      const one=buildTicketTimeline(complete,[{id:'one',issueKey:'T-1',kind:'completed',date:'2026-11-10T01:00:00Z'}],request);
      assert.equal(one.transitionCount,1); assert.equal(one.days[0].transitions[0].from,null);
      assert.equal(one.days[0].transitions[0].to,null);
      assert.equal(buildTicketTimeline({...complete,coverage:{historyComplete:true}},[],request).partial,true);
      assert.equal(buildTicketTimeline({...complete,coverage:undefined},[],request).partial,true);
      const comments=buildTicketTimeline(complete,[{id:'comment',issueKey:'T-1',kind:'comment',date:'2026-11-10T01:00:00Z'}],request);
      assert.equal(comments.transitionCount,0); assert.equal(comments.commentCount,1);
      assert.equal(buildTicketTimeline(complete,[],{}).rangeKnown,false);
      assert.equal(buildTicketTimeline(complete,[],{startDate:'2026-11-31',endDate:'2026-12-01'}).rangeKnown,false);
    """)


def test_unplaced_own_records_are_disclosed_without_fabricating_a_time():
    run_js("""
      const issue={key:'T-1',coverage:{historyComplete:true,commentsComplete:true}};
      const events=[{id:'unknown',issueKey:'T-1',kind:'comment',date:'invalid'},
        {id:'child',issueKey:'S-1',kind:'comment',date:'invalid'}];
      const model=buildTicketTimeline(issue,events,{startDate:'2026-11-10',endDate:'2026-11-12'});
      assert.equal(model.unplacedCount,1); assert.equal(model.commentCount,0);
      assert.ok(model.days.every(day=>day.comments.length===0));
    """)
