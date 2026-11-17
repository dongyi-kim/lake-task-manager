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


def test_dense_simultaneous_markers_and_measured_callouts_never_overlap_or_move_time_x():
    run_js("""
      for(const width of [280,380,900]) {
        const items=Array.from({length:16},(_,i)=>({id:String(i),at:500,comment:false}));
        const result=layoutTimeAxis(items,{start:0,end:1000},width,Object.fromEntries(items.map(item=>[item.id,Math.min(175,width-8)])));
        assert.equal(new Set(result.items.map(item=>item.y)).size,16);
        assert.ok(result.items.every(item=>item.x===width/2));
        for(let i=0;i<result.items.length;i++)for(let j=i+1;j<result.items.length;j++) {
          const a=result.items[i],b=result.items[j];
          assert.ok(Math.abs(a.x-b.x)>=28||Math.abs(a.y-b.y)>=28);
          assert.ok(Math.abs(a.labelY-b.labelY)>=34||a.left+a.width+8<=b.left||b.left+b.width+8<=a.left);
        }
        assert.ok(result.items.every(item=>item.left>=0&&item.left+item.width<=width));
        assert.ok(result.items.every(item=>item.y+14<=result.height));
      }
    """)


def test_guides_balance_above_and_below_before_adding_more_tracks():
    run_js("""
      const items=Array.from({length:6},(_,i)=>({id:String(i),at:100+i*150,comment:false}));
      const result=layoutTimeAxis(items,{start:0,end:1000},900,Object.fromEntries(items.map(item=>[item.id,100])));
      assert.equal(result.items.filter(item=>item.upper).length,3);
      assert.equal(result.items.filter(item=>!item.upper).length,3);
      assert.ok(result.height<180);
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
