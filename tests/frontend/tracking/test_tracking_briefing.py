"""Reference briefing keeps direct hierarchy and observed period facts separate."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/trackingBriefingModel.js").as_uri()
    component = (REPO_ROOT / "app/static/components/tracking-briefing.js").as_uri()
    fixture = """
      const coverage={complete:true,historyComplete:true,commentsComplete:true};
      const issue=(key,extra={})=>({key,summary:key+' title',type:'Task',statusCategory:'indeterminate',status:'진행 중',coverage,...extra});
      const event=(id,issueKey,kind,extra={})=>({id,issueKey,kind,date:'2026-11-12T01:00:00Z',...extra});
      const leaf=(id,key,children=[])=>({id,issueKey:key,name:key+' title',kind:'Task',children});
      const run=(issues,events,tree)=>({issues,events,tree,status:'complete',coverage:{complete:true},request:{startDate:'2026-11-09',endDate:'2026-11-15'},asOf:'2026-11-16T00:00:00Z'});
    """
    source = "import assert from 'node:assert/strict';\nimport * as m from " + json.dumps(module) + ";\nimport component from " + json.dumps(component) + ";\n" + fixture + script
    result = subprocess.run([node, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr


def test_direct_levels_shared_paths_and_unlinked_are_collected_only():
    run_js("""
      const task=leaf('task-path-A','T-1',[leaf('sub-A','S-1')]);
      const epic={id:'epic-A',kind:'Epic',issueKey:'E-1',name:'Epic A',children:[task]};
      const wbs={id:'wbs-A',kind:'wbs',name:'WBS A',children:[epic,{id:'empty',kind:'Epic',issueKey:'E-2',contextOnly:true,children:[]}]};
      const tree=[{id:'mod',kind:'module',name:'Module',children:[wbs]}];
      const input=run([issue('E-1',{type:'Epic'}),issue('E-2',{type:'Epic',contextOnly:true}),issue('T-1'),issue('S-1',{type:'Sub-Task',parentKey:'T-1'}),issue('O-1')],[],tree);
      const before=JSON.stringify(input);
      assert.deepEqual(m.buildTrackingBriefing(input).items.map(i=>i.id),['wbs-A','tracking-report:O-1']);
      assert.deepEqual(m.buildTrackingBriefing(input,{scopeId:'wbs-A'}).items.map(i=>i.id),['epic-A']);
      assert.deepEqual(m.buildTrackingBriefing(input,{scopeId:'epic-A'}).items.map(i=>i.id),['task-path-A']);
      assert.deepEqual(m.buildTrackingBriefing(input,{scopeId:'task-path-A'}).items.map(i=>i.id),['sub-A']);
      assert.equal(JSON.stringify(input),before);
    """)


def test_observed_events_are_deduplicated_and_context_never_counts():
    run_js("""
      const done=event('done','S-1','completed',{field:'status',from:'진행 중',to:'완료'});
      const input=run([issue('T-1'),issue('S-1',{type:'Sub-Task',parentKey:'T-1',statusCategory:'done',status:'완료'}),issue('C-1',{contextOnly:true})],
        [done,done,event('fake','C-1','completed'),event('outside','T-1','created',{date:'2026-11-01T01:00:00Z'})],
        [leaf('T','T-1',[leaf('S','S-1')]),leaf('C','C-1')]);
      const view=m.buildTrackingBriefing(input);
      assert.equal(view.metrics.completed,0);assert.equal(view.metrics.subCompleted,1);
      assert.equal(view.metrics.created,0);assert.equal(view.events.length,1);
      assert.match(view.items[0].headline,/하위.*1.*완료/);
      assert.equal(view.items[0].state,'progress');
      assert.equal(view.items[0].completedChildren[0].key,'S-1');
      assert.equal(view.spark.length,7);assert.equal(view.spark.reduce((n,b)=>n+b.events.length,0),1);
    """)


def test_completion_provenance_never_guesses_from_current_done():
    run_js("""
      const values=['period','after','before','unknown'];
      const dates=['2026-11-12T00:00:00Z','2026-11-16T00:00:00Z','2026-11-01T00:00:00Z',null];
      const issues=values.map((v,i)=>issue('T-'+(i+1),{statusCategory:'done',status:'완료',...(dates[i]?{currentCompletion:{at:dates[i],source:'completeStatusHistory'}}:{})}));
      const input=run(issues,[event('observed','T-4','completed')],issues.map(i=>leaf(i.key,i.key)));
      const view=m.buildTrackingBriefing(input);
      assert.deepEqual(view.sections.map(s=>s.completion),['period','after','unknown','before']);
      assert.equal(view.items.find(i=>i.key==='T-4').completion.kind,'unknown');
      assert.equal(view.metrics.completed,1);
    """)


def test_filters_copy_and_status_counts_use_visible_scope_without_losing_context():
    run_js("""
      const input=run([issue('T-1',{summary:'검색 대상',assigneeName:'가람'}),issue('T-2',{summary:'다른 업무',statusCategory:'done',status:'완료'})],
        [event('note','T-1','comment',{bodyText:'확인 의견',actorName:'나래'})],[leaf('one','T-1'),leaf('two','T-2')]);
      let view=m.buildTrackingBriefing(input,{query:'검색',matchedOnly:false});
      assert.equal(view.items.length,2);assert.equal(view.matchCount,1);assert.equal(view.items[1].context,true);
      view=m.buildTrackingBriefing(input,{query:'검색',matchedOnly:true});
      assert.equal(view.items.length,1);assert.equal(view.inventory.total,1);assert.equal(view.inventory.states.reduce((n,s)=>n+s.count,0),1);
      const text=m.briefingReportText(view);
      assert.match(text,/검색 대상/);assert.match(text,/가람/);assert.match(text,/note/);assert.match(text,/2026-11-09/);assert.ok(!text.includes('다른 업무'));
      assert.equal(m.buildTrackingBriefing(input,{category:'pinned',pins:['T-2']}).items[0].key,'T-2');
      assert.equal(m.buildTrackingBriefing(input,{childStatus:'done'}).items[0].key,'T-2');
    """)


def test_selected_parent_own_activity_stays_in_scope_feed_and_copy():
    run_js("""
      const input=run([issue('T-1',{summary:'상위 업무'}),issue('S-1',{type:'Sub-Task',parentKey:'T-1'})],
        [event('parent-note','T-1','comment',{bodyText:'상위 결정 사항'}),event('child-done','S-1','completed')],
        [leaf('T','T-1',[leaf('S','S-1')])]);
      const view=m.buildTrackingBriefing(input,{scopeId:'T'});
      assert.equal(view.items.length,1);assert.deepEqual(view.events.map(e=>e.id).sort(),['child-done','parent-note']);
      assert.equal(view.metrics.tasks,1);assert.equal(view.metrics.subtasks,1);
      assert.match(m.briefingReportText(view),/parent-note/);
    """)


def test_selected_parent_qualifying_activity_survives_category_and_status_filters():
    run_js("""
      const input=run([issue('E-1',{type:'Epic'}),issue('T-1')],
        [event('own-note','E-1','comment',{actorId:'bob',actorName:'Bob'}),event('own-done','E-1','completed')],
        [{id:'e',kind:'Epic',issueKey:'E-1',children:[leaf('t','T-1')]}]);
      for(const category of ['changed','completed']){
        const view=m.buildTrackingBriefing(input,{scopeId:'e',category,childStatus:'progress'});
        assert.deepEqual(view.events.map(e=>e.id).sort(),['own-done','own-note']);
      }
      assert.equal(m.buildTrackingBriefing(input,{scopeId:'e',category:'quiet'}).events.length,0);
    """)


def test_activity_snapshot_intersects_current_view_and_uses_latest_records():
    run_js("""
      const old=event('old','T-1','comment',{bodyText:'old'}),current=event('new','T-2','comment',{bodyText:'before'});
      const updated={...current,bodyText:'updated'};
      const activity=component.computed.activity.call({activityEvents:[old,current],view:{events:[updated]}});
      assert.deepEqual(activity,[updated]);
    """)


def test_actor_only_people_and_pinned_descendant_context_remain_available():
    run_js("""
      const input=run([issue('T-1',{assigneeId:'alice',assigneeName:'Alice'}),issue('S-1',{type:'Sub-Task',parentKey:'T-1'})],
        [event('note','S-1','comment',{actorId:'bob',actorName:'Bob'})],[leaf('t','T-1',[leaf('s','S-1')])]);
      const view=m.buildTrackingBriefing(input,{pins:['S-1'],category:'pinned'});
      assert.deepEqual(view.people.map(p=>p.id).sort(),['alice','bob']);
      assert.equal(view.items.length,1);assert.equal(view.items[0].pinned,false);assert.equal(view.items[0].containsPinned,true);
    """)


def test_hierarchy_matches_category_and_current_status_selection():
    run_js("""
      const input=run([issue('T-1'),issue('T-2',{statusCategory:'done',status:'완료'})],
        [event('done','T-2','completed')],[leaf('one','T-1'),leaf('two','T-2')]);
      const view=m.buildTrackingBriefing(input,{category:'completed',childStatus:'done'});
      const rows=component.computed.hierarchy.call({view,query:'',highlight:null,matchedOnly:false});
      assert.deepEqual(rows.map(row=>row.key),['T-2']);assert.equal(rows[0].counts.completed,1);
    """)


def test_section_grouping_order_is_actually_inverted():
    run_js("""
      const sections=[{path:'A',state:'progress',key:'ap'},{path:'A',state:'todo',key:'at'},{path:'B',state:'progress',key:'bp'}];
      assert.deepEqual(m.sortBriefingSections(sections,'status').map(s=>s.key),['ap','bp','at']);
      assert.deepEqual(m.sortBriefingSections(sections,'hierarchy').map(s=>s.key),['ap','at','bp']);
    """)


def test_completion_uses_frozen_instant_upper_bound_and_virtual_highlights_have_paths():
    run_js("""
      const done=issue('D-1',{statusCategory:'done',currentCompletion:{at:'2026-11-15T11:00:00+09:00',source:'completeStatusHistory'}});
      const input=run([done,issue('T-1'),issue('T-2')],[],[
        {id:'A',kind:'wbs',name:'WBS A',children:[{id:'va',kind:'Epic',name:'소속 Epic 없음',virtual:true,epicUnassigned:true,children:[leaf('t1','T-1')]}]},
        {id:'B',kind:'wbs',name:'WBS B',children:[{id:'vb',kind:'Epic',name:'소속 Epic 없음',virtual:true,epicUnassigned:true,children:[leaf('t2','T-2')]}]},leaf('done','D-1')]);
      input.asOf='2026-11-15T10:00:00+09:00';
      const view=m.buildTrackingBriefing(input);
      assert.equal(view.items.find(i=>i.key==='D-1').completion.kind,'after');
      assert.match(view.highlights.find(h=>h.id==='va').title,/WBS A/);
      assert.match(view.highlights.find(h=>h.id==='vb').title,/WBS B/);
    """)


def test_search_shortcut_does_not_steal_typing_or_hidden_page_keys():
    run_js("""
      let focused=0,prevented=0;
      const ctx={$el:{getClientRects:()=>[{}]},$refs:{searchInput:{focus:()=>focused++}}};
      const slashEvent={key:'/',target:{tagName:'BUTTON'},preventDefault:()=>prevented++};
      component.methods.searchShortcut.call(ctx,slashEvent);
      assert.equal(focused,1);assert.equal(prevented,1);
      for(const extra of [{target:{tagName:'INPUT'}},{target:{isContentEditable:true}},{ctrlKey:true}]) component.methods.searchShortcut.call(ctx,{...slashEvent,...extra});
      component.methods.searchShortcut.call({...ctx,$el:{getClientRects:()=>[]}},slashEvent);
      assert.equal(focused,1);
    """)
