"""Collection diagnostics distinguish saved pipeline evidence from local display filters."""
import json
import mimetypes
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest

from support.browser import launch_chromium
from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    static = (REPO_ROOT / "app/static").as_uri()
    source = "import assert from 'node:assert/strict';\nconst STATIC=" + json.dumps(static) + ";\n" + """
      const {buildTrackingCollectionAudit: audit}=await import(STATIC+'/components/trackingCollectionModel.js');
      const {projectTrackingVoc}=await import(STATIC+'/components/trackingVocModel.js');
      const {projectTrackingHierarchy}=await import(STATIC+'/components/trackingHierarchyModel.js');
    """ + script
    result = subprocess.run([node, "--input-type=module", "-e", source], capture_output=True,
                            text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr


def test_funnel_missing_zero_and_pending_are_different_and_old_runs_remain_immutable():
    run_js("""
      const old={issues:[],tree:[],events:[],coverage:{}};
      const before=JSON.stringify(old), legacy=audit(old);
      assert.equal(legacy.recorded,false);
      assert.deepEqual(legacy.stages.map(s=>s.state),Array(5).fill('미기록'));
      assert.ok(legacy.stages.every(s=>s.total===null && s.voc===null));
      const run={...old,coverage:{funnel:{version:1,vocComponent:'저장된 VOC',stages:[
        {id:'discovery',total:0,voc:0,complete:true},
        {id:'module',total:0,voc:0,complete:false},
        {id:'hydrated',total:null,voc:null,complete:false},
      ]}}};
      const out=audit(run,{vocComponent:'현재 VOC'});
      assert.deepEqual(out.stages.map(s=>s.id),['discovery','module','hydrated','analyzed','published']);
      assert.equal(out.stages[0].state,'처리 완료');
      assert.equal(out.stages[0].total,0); assert.equal(out.stages[0].voc,0);
      assert.equal(out.stages[1].state,'처리 미완료'); assert.equal(out.stages[1].total,0);
      assert.equal(out.stages[2].state,'처리 미완료'); assert.equal(out.stages[2].total,null);
      assert.equal(out.frozenVocComponent,'저장된 VOC'); assert.equal(out.currentVocComponent,'현재 VOC');
      assert.equal(out.vocComponentChanged,true);
      assert.equal(JSON.stringify(old),before);
    """)


def test_backend_408_retained_voc_and_zero_period_activity_do_not_disappear_locally():
    run_js("""
      const issues=Array.from({length:408},(_,i)=>({key:'V-'+(i+1),type:'Task',
        summary:'[문의 - 일반] 이전에 완료된 요청 '+i,components:['사용자 VoC'],
        created:'2026-09-01',resolved:'2026-09-02',updated:'2026-11-17'}));
      issues.push({key:'S-1',type:'Sub-Task',summary:'상속 하위',parentKey:'V-1'});
      issues.push({key:'C-1',type:'Task',summary:'문맥',voc:true,contextOnly:true});
      const run={issues,tree:[],events:[],coverage:{funnel:{version:1,vocComponent:'사용자 VoC',
        stages:['discovery','module','hydrated','analyzed','published'].map(id=>({id,total:409,voc:408,complete:true}))}}};
      const before=JSON.stringify(run), displayRun=projectTrackingHierarchy(projectTrackingVoc(run));
      const out=audit(run,{displayRun});
      assert.equal(out.stages.at(-1).voc,408);
      assert.deepEqual(out.display,{total:409,voc:408});
      assert.deepEqual(out.navigation,{total:409,voc:408});
      assert.deepEqual(out.activity,{issues:0,events:0});
      assert.equal(JSON.stringify(run),before);
    """)


def test_local_navigation_filters_exclude_retained_ancestors_and_shared_paths_and_ignore_folds():
    run_js("""
      const run={issues:[
        {key:'E-1',type:'Epic',summary:'원래 Epic'},
        {key:'T-1',type:'Task',summary:'needle',assigneeId:'p'},
        {key:'T-2',type:'Task',summary:'다른 업무',assigneeId:'q'},
        {key:'C-1',type:'Task',summary:'needle',contextOnly:true},
      ],tree:[{id:'e',issueKey:'E-1',kind:'Epic',children:[
        {id:'t',issueKey:'T-1',kind:'Task'}, {id:'other',issueKey:'T-2',kind:'Task'},
        {id:'context',issueKey:'C-1',kind:'Task'}
      ]},{id:'shared',issueKey:'T-1',kind:'Task'}],events:[
        {id:'a',issueKey:'T-1',kind:'comment'}, {id:'b',issueKey:'T-1',kind:'fieldChanged'},
        {id:'c',issueKey:'T-2',kind:'completed'}, {id:'context',issueKey:'C-1',kind:'comment'},
      ]};
      const all=audit(run,{query:'needle',matchedOnly:false});
      assert.equal(all.navigation.total,3); assert.deepEqual(all.activity,{issues:2,events:3});
      const out=audit(run,{query:'needle',highlight:{kind:'person',value:'p'},matchedOnly:true,closed:{e:true}});
      assert.equal(out.display.total,3); assert.equal(out.navigation.total,1);
      assert.deepEqual(out.activity,{issues:1,events:2});
      const scoped=audit(run,{scopedRun:{...run,issues:[run.issues[2]],tree:[run.tree[0].children[1]],events:[run.events[2]]},query:'needle',matchedOnly:true});
      assert.equal(scoped.navigation.total,1); // navigation remains the full existing navigator
      assert.deepEqual(scoped.activity,{issues:0,events:0});
      const noMatch=audit(run,{query:'missing',matchedOnly:true});
      assert.equal(noMatch.navigation.total,0); assert.deepEqual(noMatch.activity,{issues:0,events:0});
    """)


def test_audit_dialog_renders_frozen_counts_local_filters_and_legacy_state_without_requests():
    """Browser plugin not available; route a local saved fixture through real Vue UI."""
    playwright = pytest.importorskip("playwright.sync_api")
    static = REPO_ROOT / "app/static"
    html = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>Tracking 수집 진단</title>
      <link rel="stylesheet" href="/styles/tokens.css"><link rel="stylesheet" href="/styles/base.css">
      <link rel="stylesheet" href="/styles/tracking-reference.css">
      </head><body><main id="app"></main><script type="module">
      import {createApp} from '/vendor/vue.esm-browser.prod.js';
      import View from '/components/tracking-view.js';
      const issues=[{key:'V-1',type:'Task',summary:'[문의 - 일반] needle',components:['사용자 VoC']},
        {key:'T-1',type:'Task',summary:'다른 업무'}];
      const run={id:'saved',status:'complete',request:{startDate:'2026-11-10',endDate:'2026-11-17'},issues,
        tree:[{id:'t',issueKey:'T-1',kind:'Task'}],events:[],coverage:{complete:true,
          funnel:{version:1,vocComponent:'사용자 VoC',stages:[
            {id:'discovery',total:1200,voc:408,complete:true},
            {id:'module',total:409,voc:408,complete:true},
            {id:'hydrated',total:409,voc:408,complete:true},
            {id:'analyzed',total:409,voc:408,complete:true},
            {id:'published',total:409,voc:408,complete:true}]}}};
      window.vm=createApp({...View,mounted(){},data(){return {...View.data.call(this),run,
        optionsLoaded:true,options:{people:[],modules:[],vocComponent:'현재 VOC'}}}}).mount('#app');
      </script></body></html>'''
    errors, requests = [], []

    def respond(route):
        path = urlsplit(route.request.url).path
        if path == "/":
            route.fulfill(content_type="text/html; charset=utf-8", body=html)
        else:
            asset = (static / path.lstrip("/")).resolve()
            if asset.is_relative_to(static.resolve()) and asset.is_file():
                content_type = "text/javascript" if asset.suffix == ".js" else mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
                route.fulfill(content_type=content_type, body=asset.read_bytes())
            else:
                requests.append(path)
                route.fulfill(status=404)

    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", respond)
            page.goto("http://tracking-collection.test/")
            assert page.title() == "Tracking 수집 진단"
            page.get_by_role("button", name="수집 범위와 비용", exact=True).click()
            dialog = page.get_by_role("dialog", name="수집 범위와 비용")
            from playwright.sync_api import expect
            expect(dialog).to_be_visible()
            expect(dialog.locator('[data-collection-stage="discovery"]')).to_have_text('전체 1200 · VOC 408 · 처리 완료')
            expect(dialog).to_contain_text('수집 당시 VOC: 사용자 VoC')
            expect(dialog).to_contain_text('현재 분류 설정: 현재 VOC')
            expect(dialog.locator('[data-collection-local="display"]')).to_have_text('전체 2 · VOC 0')
            expect(dialog.locator('[data-collection-local="activity"]')).to_have_text('활동 티켓 0 · 이벤트 0')
            page.evaluate("vm.query='needle';vm.matchedOnly=true")
            expect(dialog.locator('[data-collection-local="navigation"]')).to_have_text('전체 0 · VOC 0')
            expect(dialog).to_contain_text('검색: needle')
            expect(dialog).to_contain_text('일치 항목만')
            page.evaluate("vm.run.coverage={complete:true}")
            expect(dialog.locator('[data-collection-stage="discovery"]')).to_have_text('미기록')
            page.evaluate("vm.run.coverage={funnel:{version:1,vocComponent:'사용자 VoC',stages:[{id:'discovery',total:0,voc:0,complete:true},{id:'module',total:null,voc:null,complete:false}]}}")
            expect(dialog.locator('[data-collection-stage="discovery"]')).to_have_text('전체 0 · VOC 0 · 처리 완료')
            expect(dialog.locator('[data-collection-stage="module"]')).to_have_text('전체 미확인 · VOC 미확인 · 처리 미완료')
            assert dialog.evaluate('el=>el.scrollWidth<=el.clientWidth')
            assert not errors
            assert not requests
        finally:
            browser.close()
