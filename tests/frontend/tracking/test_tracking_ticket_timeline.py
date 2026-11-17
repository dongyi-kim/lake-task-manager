"""Ticket timelines separate saved own evidence from direct-child progress reports."""
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


def test_epic_timeline_separates_direct_task_changes_from_own_status_and_subtask_history():
    run_js("""
      const coverage={historyComplete:true,commentsComplete:true};
      const issue={key:'E-1',type:'Epic',coverage};
      const issues=[issue,
        {key:'T-1',type:'Task',summary:'Closed task',epicKey:'E-1',status:'Closed',statusCategory:'done',coverage},
        {key:'T-2',type:'Bug',summary:'Resolved task',epicKey:'E-1',status:'Resolved',statusCategory:'done',coverage},
        {key:'S-1',type:'Sub-Task',isSubtask:true,parentKey:'T-1',epicKey:'E-1',statusCategory:'done',coverage},
        {key:'OTHER',type:'Task',epicKey:'E-2',statusCategory:'new',coverage}];
      const task={key:'T-1',kind:'task',children:[{key:'S-1',kind:'subtask'}]};
      const tree=[{key:'E-1',kind:'epic',children:[task,{id:'virtual',kind:'group',virtual:true,
        children:[{key:'T-2',kind:'task'}]}]},{key:'E-1',kind:'epic',children:[task]}];
      const event=(id,key,kind,extra={})=>({id,issueKey:key,kind,date:'2026-11-10T01:00:00Z',...extra});
      const events=[event('own','E-1','completed'),event('task','T-1','completed'),
        event('comment','T-2','comment',{bodyText:'Progress report'}),
        event('edit','T-2','commentEdited',{bodyText:'Updated report'}),
        event('due','T-2','fieldChanged',{field:'duedate',from:'2026-11-11',to:'2026-11-12'}),
        event('noise','T-1','fieldChanged',{field:'description'}),event('sub','S-1','completed'),
        event('other','OTHER','completed'),event('outside','T-1','completed',{date:'2026-11-11T01:00:00Z'})];
      events.push({...events[1]});
      const before=JSON.stringify({issue,events,issues,tree});
      const model=buildTicketTimeline(issue,events,{startDate:'2026-11-10',endDate:'2026-11-10'},{issues,tree});
      assert.deepEqual(model.days[0].transitions.map(item=>item.id),['own']);
      assert.deepEqual(model.days[0].childChanges.map(item=>item.id),['comment','due','edit','task']);
      assert.equal(model.childEventCount,4); assert.equal(model.transitionCount,1); assert.equal(model.commentCount,0);
      assert.deepEqual(model.children.map(child=>child.key).sort(),['T-1','T-2']);
      assert.equal(model.days[0].childChanges[0].title,'Resolved task');
      assert.equal(model.days[0].childChanges[0].issueKey,'T-2');
      assert.equal(model.days[0].childChanges[0].time,'10:00');
      assert.equal(model.days[0].childChanges[0].event,events[2]);
      assert.deepEqual(model.childProgress,{total:2,done:2,unknown:0,percent:100,partial:false,label:'조회된 하위 작업 기준'});
      assert.equal(model.childLabel,'Task');
      assert.equal(JSON.stringify({issue,events,issues,tree}),before);
    """)


def test_task_progress_uses_observed_direct_subtasks_and_discloses_unknown_or_partial_children():
    run_js("""
      const coverage={historyComplete:true,commentsComplete:true};
      const issue={key:'T-1',type:'Task',coverage};
      const issues=[issue,
        {key:'S-1',type:'Sub-task',parentKey:'T-1',status:'Closed',statusCategory:'done',coverage},
        {key:'S-2',type:'Sub-task',parentKey:'T-1',status:'Resolved',statusCategory:'done',coverage},
        {key:'S-3',type:'Sub-task',parentKey:'T-1',statusCategory:'new',coverage},
        {key:'GRAND',type:'Sub-task',parentKey:'S-1',statusCategory:'done',coverage},
        {key:'OTHER',type:'Sub-task',parentKey:'T-2',statusCategory:'done',coverage}];
      const request={startDate:'2026-11-10',endDate:'2026-11-12'};
      const events=[{id:'child',issueKey:'S-1',kind:'reopened',date:'2026-11-09T15:00:00Z'},
        {id:'unknown-date',issueKey:'S-2',kind:'comment',date:'invalid'},
        {id:'grand',issueKey:'GRAND',kind:'completed',date:'2026-11-10T01:00:00Z'}];
      const model=buildTicketTimeline(issue,events,request,{issues});
      assert.equal(model.childProgress.total,3); assert.equal(model.childProgress.done,2);
      assert.equal(model.childProgress.percent,2/3*100); assert.equal(model.childProgress.partial,false);
      assert.equal(model.childLabel,'Sub-task'); assert.equal(model.childEventCount,1);
      assert.equal(model.childUnplacedCount,1); assert.equal(model.unplacedCount,0);
      assert.equal(model.days[0].childChanges[0].time,'00:00');
      assert.ok(model.days.slice(1).every(day=>day.childChanges.length===0));
      const unknown=buildTicketTimeline(issue,[],request,{issues:[...issues,{key:'S-4',parentKey:'T-1',type:'Sub-task',status:'Done',coverage}]});
      assert.equal(unknown.childProgress.unknown,1); assert.equal(unknown.childProgress.percent,null);
      const incomplete=buildTicketTimeline(issue,[],request,{issues:issues.map(child=>child.key==='S-3'?{...child,coverage:{historyComplete:false,commentsComplete:true}}:child)});
      assert.equal(incomplete.childProgress.total,3); assert.equal(incomplete.childProgress.done,2);
      assert.equal(incomplete.childProgress.partial,true); assert.equal(incomplete.childProgress.percent,null);
      const missing=buildTicketTimeline(issue,[],request,{issues,tree:[{key:'T-1',children:[{key:'MISSING',kind:'subtask'}]}]});
      assert.equal(missing.childProgress.total,4); assert.equal(missing.childProgress.unknown,1);
      assert.equal(missing.childProgress.partial,true); assert.equal(missing.childProgress.percent,null);
    """)


def test_leaf_or_empty_hierarchy_never_invents_progress_or_historical_child_counts():
    run_js("""
      const coverage={historyComplete:true,commentsComplete:true};
      const request={startDate:'2026-11-10',endDate:'2026-11-10'};
      const empty=buildTicketTimeline({key:'E-1',type:'Epic',coverage},[],request,{issues:[]});
      assert.equal(empty.childProgress.total,0); assert.equal(empty.childProgress.percent,null);
      assert.equal(empty.childProgress.unknown,0); assert.equal(empty.childEventCount,0);
      assert.deepEqual(empty.days[0].childChanges,[]);
      const leaf=buildTicketTimeline({key:'S-1',type:'Sub-Task',isSubtask:true,coverage},[],request,
        {issues:[{key:'S-2',type:'Sub-task',parentKey:'S-1',statusCategory:'done',coverage}]});
      assert.equal(leaf.childProgress.total,0); assert.equal(leaf.childLabel,'');
      const uncertain=buildTicketTimeline({key:'T-1',type:'Task',coverage},[],request,
        {coverage:{complete:false},issues:[{key:'S-1',type:'Sub-task',parentKey:'T-1',statusCategory:'done',coverage}]});
      assert.equal(uncertain.childProgress.done,1); assert.equal(uncertain.childProgress.partial,true);
      assert.equal(uncertain.childProgress.percent,null);
    """)
