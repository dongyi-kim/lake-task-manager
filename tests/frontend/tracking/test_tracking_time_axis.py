"""Actual timestamps, source-safe comment clusters and dense axis placement."""
import json
import shutil
import subprocess

import pytest
from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    base = (REPO_ROOT / 'app/static/components').as_uri()
    source = ('import assert from "node:assert/strict";\n'
              f'import {{buildTimeAxis, axisRange}} from {json.dumps(base + "/trackingTimeAxisModel.js")};\n'
              f'import {{layoutTimeAxis}} from {json.dumps(base + "/trackingTimeAxisLayout.js")};\n' + script)
    result = subprocess.run([node, '--input-type=module', '-e', source], capture_output=True,
                            text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr


def test_projection_preserves_real_elapsed_time_and_kst_nights_without_current_state_inference():
    run_js("""
      const issue={key:'T-1',type:'Task',status:'Done',coverage:{historyComplete:false,commentsComplete:true}};
      const request={startDate:'2026-11-10',endDate:'2026-11-12'};
      const events=[['a','2026-11-10T00:00:00Z'],['b','2026-11-10T01:00:00Z'],['c','2026-11-11T01:00:00Z']]
        .map(([id,date])=>({id,date,kind:'completed',issueKey:'T-1'}));
      events.push({id:'night',date:'2026-11-12T14:10:00Z',kind:'comment',issueKey:'T-1'});
      const axis=buildTimeAxis(issue,events,request);
      assert.equal(axis.items.length,4); assert.equal(axis.timeline.partial,true);
      assert.equal(axis.items[2].at-axis.items[1].at,86400000);
      assert.equal(axis.items[1].at-axis.items[0].at,3600000);
      assert.equal(axis.items.at(-1).date,'2026-11-12');
      const zoom=buildTimeAxis(issue,events,request,{}, {date:'2026-11-12',zoom:true});
      assert.equal(zoom.outsideCount,1); assert.equal(zoom.items.length,0);
      const full=buildTimeAxis(issue,events,request,{}, {date:'2026-11-12',zoom:true,fullDay:true});
      assert.equal(full.items[0].id,'night');
      assert.equal(buildTimeAxis(issue,[],request).items.length,0);
      assert.equal(axisRange(request).end-axisRange(request).start,3*86400000);
    """)


def test_comments_group_only_same_source_and_preserve_all_original_evidence_ids():
    run_js("""
      const coverage={historyComplete:true,commentsComplete:true};
      const issue={key:'T',type:'Task',coverage};
      const children=[{key:'S',type:'Sub-task',parentKey:'T',statusCategory:'done',coverage}];
      const events=Array.from({length:74},(_,i)=>({id:'comment-'+i,issueKey:'T',kind:'comment',date:new Date(Date.parse('2026-11-12T01:00:00Z')+i*1000).toISOString()}));
      events.push({id:'child',issueKey:'S',kind:'comment',date:'2026-11-12T01:00:00Z'});
      const axis=buildTimeAxis(issue,events,{startDate:'2026-11-12',endDate:'2026-11-12'},{issues:children});
      assert.equal(axis.items.length,2);
      const own=axis.items.find(item=>item.issueKey==='T');
      assert.equal(own.members.length,74); assert.equal(new Set(own.members.map(item=>item.id)).size,74);
      assert.equal(axis.timeline.commentCount,74); assert.equal(axis.timeline.childEventCount,1);
      const hidden=buildTimeAxis(issue,events,{startDate:'2026-11-12',endDate:'2026-11-12'},{issues:children},{comments:false});
      assert.equal(hidden.items.length,0); assert.equal(hidden.timeline.commentCount,74);
    """)


def test_dense_simultaneous_markers_preserve_members_with_bounded_height():
    run_js("""
      for(const width of [280,380,900]) {
        const items=Array.from({length:16},(_,i)=>({id:String(i),at:500,comment:false}));
        const result=layoutTimeAxis(items,{start:0,end:1000},width,Object.fromEntries(items.map(item=>[item.id,Math.min(175,width-8)])));
        assert.equal(result.totalCount,16);
        assert.equal(new Set(result.items.flatMap(item=>item.members.map(member=>member.id))).size,16);
        assert.ok(new Set(result.items.map(item=>item.y)).size<=3);
        assert.ok(result.items.every(item=>item.x===width/2));
        for(let i=0;i<result.items.length;i++)for(let j=i+1;j<result.items.length;j++) {
          const a=result.items[i],b=result.items[j];
          assert.ok(Math.abs(a.x-b.x)>=48||Math.abs(a.y-b.y)>=28);
          if(a.showCallout&&b.showCallout)assert.ok(a.labelY!==b.labelY||a.left+a.width+8<=b.left||b.left+b.width+8<=a.left);
        }
        assert.ok(result.items.filter(item=>item.showCallout).every(item=>item.left>=0&&item.left+item.width<=width));
        assert.ok(result.items.every(item=>item.y+14<=result.height));
        assert.ok(result.height<=240);
      }
    """)


def test_guides_balance_above_and_below_before_adding_more_tracks():
    run_js("""
      const items=Array.from({length:6},(_,i)=>({id:String(i),at:100+i*150,comment:false}));
      const result=layoutTimeAxis(items,{start:0,end:1000},900,Object.fromEntries(items.map(item=>[item.id,100])));
      assert.equal(result.items.filter(item=>item.showCallout&&item.upper).length,3);
      assert.equal(result.items.filter(item=>item.showCallout&&!item.upper).length,3);
      assert.ok(result.height<=240);
      assert.ok(layoutTimeAxis([],{start:0,end:1000},900).height<100);
    """)


def test_zoom_is_half_open_and_unknown_records_never_receive_invented_coordinates():
    run_js("""
      const issue={key:'T',type:'Task',coverage:{historyComplete:false,commentsComplete:false}};
      const request={startDate:'2026-11-12',endDate:'2026-11-13'};
      const events=[['before','2026-11-11T23:59:59+09:00'],['start','2026-11-12T00:00:00+09:00'],
        ['nine','2026-11-12T09:00:00+09:00'],['eighteen','2026-11-12T18:00:00+09:00'],
        ['next','2026-11-13T00:00:00+09:00'],['after','2026-11-14T00:00:00+09:00'],['unknown','invalid']]
        .map(([id,date])=>({id,date,issueKey:'T',kind:'completed'}));
      const all=buildTimeAxis(issue,events,request);
      assert.deepEqual(all.items.map(item=>item.id),['start','nine','eighteen','next']);
      assert.equal(all.timeline.unplacedCount,1);
      const work=buildTimeAxis(issue,events,request,{}, {zoom:true,date:'2026-11-12'});
      assert.deepEqual(work.items.map(item=>item.id),['nine']);assert.equal(work.outsideCount,2);
      const day=buildTimeAxis(issue,events,request,{}, {zoom:true,date:'2026-11-12',fullDay:true});
      assert.deepEqual(day.items.map(item=>item.id),['start','nine','eighteen']);
    """)


def test_duplicate_event_ids_across_sources_keep_distinct_internal_identity_and_measured_widths():
    run_js("""
      const coverage={historyComplete:true,commentsComplete:true},issue={key:'T',type:'Task',coverage};
      const events=['T','S'].map(issueKey=>({id:'same',issueKey,kind:'completed',date:'2026-11-12T01:00:00Z'}));
      const axis=buildTimeAxis(issue,events,{startDate:'2026-11-12',endDate:'2026-11-12'},
        {issues:[{key:'S',parentKey:'T',type:'Sub-task',coverage}]});
      assert.equal(new Set(axis.items.map(item=>item.uid)).size,2);
      const widths=Object.fromEntries(axis.items.map((item,i)=>[item.uid,100+i*50]));
      const layout=layoutTimeAxis(axis.items,axis.range,380,widths);
      assert.deepEqual(layout.items.map(item=>item.width),[100,150]);
      assert.ok(layout.items.every(item=>item.id==='same'));
    """)


def test_epic_changes_and_two_hundred_events_have_bounded_density_without_lost_evidence():
    run_js("""
      for(const count of [31,200])for(const width of [380,1000]) {
        const start=Date.parse('2026-11-12T09:00:00+09:00');
        const events=Array.from({length:count},(_,i)=>({id:'event-'+i,uid:'source-'+i,
          issueKey:'DL-'+(10331+i%11),title:'작업 '+i%11,source:'하위 작업',
          kind:i%7===0?'completed':i%5===0?'reopened':i%3===0?'comment':'fieldChanged',
          field:i%2?'status':'assignee',at:start+(i%20)*30000,comment:i%3===0,
          detail:'원본 '+i,event:{id:'event-'+i}}));
        const result=layoutTimeAxis(events,{start,end:start+9*3600000},width);
        assert.equal(result.totalCount,count);
        const members=result.items.flatMap(item=>item.members);
        assert.deepEqual(members.map(item=>item.id).sort(),events.map(item=>item.id).sort());
        assert.ok(members.every(member=>member.at===events.find(item=>item.id===member.id).at));
        assert.ok(result.items.length<count);
        assert.ok(result.height<=(width<500?260:240));
        assert.ok(result.shownCalloutCount<=Math.min(6,Math.max(2,2*Math.floor(width/280))));
        for(const item of result.items) {
          assert.ok([0,-28,28].includes(item.offset));
          assert.equal(item.at,(item.startAt+item.endAt)/2);
          if(item.sourceCount>1){assert.equal(item.issueKey,'');assert.equal(item.title,item.sourceCount+'개 작업');}
        }
        for(let i=0;i<result.items.length;i++)for(let j=i+1;j<result.items.length;j++) {
          const a=result.items[i],b=result.items[j];
          assert.ok(a.level!==b.level||Math.abs(a.x-b.x)>=48);
          if(a.showCallout&&b.showCallout&&a.labelY===b.labelY)
            assert.ok(a.left+a.width+8<=b.left||b.left+b.width+8<=a.left);
        }
      }
    """)


def test_sparse_coordinates_source_identity_and_non_status_semantics_survive():
    run_js("""
      const items=[{uid:'a',id:'a',issueKey:'T',at:100,kind:'fieldChanged',field:'assignee',tone:'progress',short:'담당자'},
        {uid:'b',id:'b',issueKey:'S',at:100,kind:'comment',comment:true},
        {uid:'c',id:'c',issueKey:'T',at:900,kind:'completed'}];
      const result=layoutTimeAxis(items,{start:0,end:1000},1000);
      assert.equal(result.items.length,3);
      assert.deepEqual(result.items.map(item=>item.x),[100,100,900]);
      assert.equal(result.items[0].tone,'change');
      assert.equal(result.items[0].short,'담당자');
      assert.ok(result.items.every(item=>item.members.length===1&&!item.isCluster));
    """)


def test_existing_comment_groups_flatten_deduplicate_and_keep_cross_source_members_explicit():
    run_js("""
      const start=Date.parse('2026-11-12T09:00:00+09:00');
      const members=Array.from({length:60},(_,i)=>({uid:'c'+i,id:'c'+i,issueKey:i%2?'S':'T',
        source:'댓글 원본',title:'제목',kind:'comment',comment:true,own:i%2===0,at:start+i*1000}));
      const result=layoutTimeAxis([{...members[0],members:members.slice(0,30)},
        {...members[30],members:members.slice(30)},members[0]],{start,end:start+9*3600000},380);
      assert.equal(result.totalCount,60);
      assert.equal(result.items.flatMap(item=>item.members).length,60);
      assert.ok(result.items.some(item=>item.sourceCount===2));
      const mixed=result.items.find(item=>item.sourceCount===2);
      assert.equal(mixed.issueKey,'');assert.equal(mixed.source,'2개 작업');
      assert.equal(mixed.own,false);assert.equal(mixed.hasOwn,true);assert.equal(mixed.hasChildren,true);
      assert.equal(mixed.at,(mixed.startAt+mixed.endAt)/2);
    """)
