"""Reference panels preserve saved evidence and independent navigation controls."""
import json
import mimetypes
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest

from support.browser import launch_chromium
from support.paths import REPO_ROOT


def test_period_summary_keeps_current_state_separate_from_actual_events():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/tracking-reference-detail.js").as_uri()
    result = subprocess.run([node, "--input-type=module", "-e", """
      import assert from 'node:assert/strict';
      const {referenceEvidenceSummary: summary}=await import(MODULE);
      const events=[
        {id:'done',kind:'completed',issueKey:'T-1',date:'2026-11-10T01:00:00Z',from:'진행',to:'완료'},
        {id:'reopen',kind:'reopened',issueKey:'T-1',date:'2026-11-11T01:00:00Z',from:'완료',to:'진행'},
        {id:'comment',kind:'comment',issueKey:'T-1',date:'2026-11-12T01:00:00Z',bodyText:'재현 조건을 확인 중입니다.'},
      ];
      const before=JSON.stringify(events), result=summary(events);
      assert.deepEqual(result.counts.map(x=>[x.kind,x.count]),[['completed',1],['reopened',1]]);
      assert.equal(result.latestChange.id,'reopen');
      assert.equal(result.text,'재현 조건을 확인 중입니다.');
      assert.equal(result.commentCount,1);
      assert.equal(JSON.stringify(events),before);
      assert.equal(summary([]).latest,undefined);
      assert.equal(summary([]).text,'선택 기간에 확인된 활동이 없습니다.');
    """.replace("MODULE", json.dumps(module))], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def panels_browser():
    playwright = pytest.importorskip("playwright.sync_api")
    static = REPO_ROOT / "app/static"
    html = '''<!doctype html><html lang="ko"><head><meta charset="utf-8">
      <link rel="stylesheet" href="/styles/tokens.css"><link rel="stylesheet" href="/styles/base.css">
      <link rel="stylesheet" href="/styles/ticket.css"><link rel="stylesheet" href="/styles/tracking-comment.css">
      <link rel="stylesheet" href="/styles/tracking-reference-panels.css">
      <style>body{margin:0}.tracking-reference{height:100vh;display:flex;--tr-ink:#1b2840;--tr-muted:#68758b;--tr-line:#e5e9f1;--tr-bg:#f5f7fb;--tr-blue:#3956c8;--tr-blue-light:#eef1ff;--tr-green:#21745d;--tr-amber:#956013;--tr-panel:#fff;font-family:Arial,sans-serif}button{border:0;background:none;cursor:pointer;color:inherit;font:inherit}button:focus-visible{outline:2px solid #3956c8}#spacer{flex:1}</style>
      </head><body><main id="app" class="tracking-reference"></main><script type="module">
      import {createApp} from '/vendor/vue.esm-browser.prod.js';
      import Navigator from '/components/tracking-reference-nav.js';
      import Evidence from '/components/tracking-reference-detail.js';
      import {trackingRows} from '/components/trackingModel.js';
      const coverage={historyComplete:true,commentsComplete:true};
      const run={request:{startDate:'2026-11-09',endDate:'2026-11-15'},asOf:'2026-11-16T23:59:00+09:00',issues:[
        {key:'T-1',summary:'긴 업무 제목을 줄임 없이 읽고 선택할 수 있는 데이터 적재 검증과 안정화 작업',type:'Task',status:'진행 중',statusCategory:'indeterminate',assigneeName:'김담당',fetchedAt:'2026-11-16T01:10:00Z',coverage},
        {key:'S-1',summary:'하위 검증',type:'Sub-Task',status:'완료',statusCategory:'done',coverage}],
        tree:[{id:'m',kind:'module',name:'데이터 수집',children:[{id:'w',kind:'wbs',name:'수집 신뢰성',children:[{id:'t',key:'T-1',kind:'task',children:[{id:'s',key:'S-1',kind:'subtask'}]},{id:'v',kind:'epic',virtual:true,name:'VOC 분류'}]}]}],
        events:[{id:'rich',issueKey:'T-1',kind:'comment',date:'2026-11-13T01:00:00Z',actorName:'김담당',bodyText:'첫 번째 보고. 두 번째 보고. 세 번째 보고.',bodyHtml:'<p>첫 번째 보고 문장입니다.</p><p>두 번째 보고 문장입니다.</p><p>세 번째 보고 문장입니다.</p><table><tr><th>항목</th><th>결과</th></tr><tr><td>적재</td><td>완료</td></tr></table><pre>status = verified</pre><p><a href="#reference">참고 링크</a></p>'},
          {id:'done',issueKey:'S-1',kind:'completed',date:'2026-11-12T01:00:00Z',actorName:'이담당',field:'status',from:'진행 중',to:'완료'}]};
      window.vm=createApp({components:{Navigator,Evidence},data:()=>({run,closed:{},activeId:'t',collapsed:false,pins:['T-1'],ticket:'',focused:'',leaf:false,detailOpen:true}),
        computed:{allRows(){return trackingRows(this.run)},rows(){return trackingRows(this.run,{closed:this.closed})},row(){return this.allRows.find(r=>r.id==='t')}},
        template:`<Navigator :rows="rows" :all-rows="allRows" :active-id="activeId" detail-id="t" :closed="closed" :pins="pins" :total="1" :collapsed="collapsed" @scope="activeId=$event" @fold="closed[$event.id]=!closed[$event.id]" @collapse="collapsed=$event" @pin="pins=pins.filter(k=>k!==$event)"/><div id="spacer"></div><Evidence v-if="detailOpen" :row="row" :run="run" :events="run.events" :match-keys="['T-1']" :has-match="true" :selected-event-id="focused" :leaf="leaf" @ticket="ticket=$event" @focus="focused=$event" @close="detailOpen=false" @scope="activeId=$event"/>`}).mount('#app');
      </script></body></html>'''
    errors = []

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
                route.fulfill(status=404)

    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 950})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", respond)
            page.goto("http://tracking-reference.test/")
            page.locator(".tp-evidence").wait_for()
            yield page, errors
        finally:
            browser.close()


def test_navigation_wraps_full_titles_and_keeps_disclosure_selection_and_panel_separate(panels_browser):
    from playwright.sync_api import expect
    page, errors = panels_browser
    selected = page.locator('.tp-tree-node.is-selected .tp-tree-label')
    assert selected.evaluate('el=>el.scrollHeight<=el.clientHeight+1')
    assert selected.bounding_box()['height'] > 30
    expect(selected).to_have_attribute('title', '긴 업무 제목을 줄임 없이 읽고 선택할 수 있는 데이터 적재 검증과 안정화 작업 (T-1)')
    expect(page.locator('.tp-tree-node.is-ancestor')).to_have_count(1)
    fold = page.get_by_role('button', name='수집 신뢰성 접기', exact=True)
    fold.focus()
    page.keyboard.press('Space')
    expect(page.locator('.tp-tree-node')).to_have_count(1)
    assert page.evaluate('vm.activeId') == 't'
    page.get_by_role('button', name='수집 신뢰성 펼치기', exact=True).click()
    page.get_by_role('button', name='업무 탐색 패널 접기', exact=True).click()
    expect(page.locator('.tp-nav-rail')).to_have_count(1)
    assert page.locator('.tp-nav-rail').bounding_box()['width'] == 44
    page.get_by_role('button', name='업무 탐색 패널 펼치기', exact=True).focus()
    page.keyboard.press('Enter')
    expect(page.locator('.tp-navigation')).to_have_count(1)
    page.get_by_role('button', name='긴 업무 제목을 줄임 없이 읽고 선택할 수 있는 데이터 적재 검증과 안정화 작업 고정 해제', exact=True).click()
    expect(page.locator('.tp-pinned-row')).to_have_count(0)
    page.get_by_role('button', name='트리의 모든 항목 접기', exact=True).click()
    expect(page.locator('.tp-tree-node')).to_have_count(1)
    assert page.evaluate('vm.activeId') == 't'
    assert not errors


def test_direct_scope_and_virtual_group_pins_keep_identity_and_can_be_removed(panels_browser):
    from playwright.sync_api import expect

    page, errors = panels_browser
    page.evaluate("vm.pins=['w','v','T-1']")
    expect(page.locator('.tp-pinned-row')).to_have_count(3)
    page.locator('.tp-side-pin').filter(has_text='VOC 분류').click()
    assert page.evaluate('vm.activeId') == 'v'
    page.get_by_role('button', name='VOC 분류 고정 해제', exact=True).click()
    assert page.evaluate('vm.pins') == ['w', 'T-1']
    expect(page.locator('.tp-pinned-row')).to_have_count(2)
    page.get_by_role('button', name='수집 신뢰성 고정 해제', exact=True).click()
    assert page.evaluate('vm.pins') == ['T-1']
    expect(page.locator('.tp-pinned-row')).to_have_count(1)
    assert not errors


def test_evidence_uses_snapshot_time_full_history_and_sanitized_rich_comment_path(panels_browser):
    from playwright.sync_api import expect
    page, errors = panels_browser
    expect(page.locator('.tp-evidence-meta')).to_contain_text('2026.11.16 10:10')
    expect(page.locator('.tp-evidence-meta')).not_to_contain_text('23:59')
    expect(page.locator('.tp-event')).to_have_count(2)
    expect(page.locator('.tp-event-change')).to_contain_text('진행 중 → 완료')
    expect(page.locator('.tp-evidence-meta')).to_contain_text('진행 중')
    expect(page.locator('.tp-delta-child-title')).to_have_text('하위 검증 · ')
    toggle = page.locator('.tp-event .tr-comment-toggle')
    expect(toggle).to_have_attribute('aria-expanded', 'false')
    toggle.click()
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    expect(page.locator('.tp-event table')).to_contain_text('적재')
    expect(page.locator('.tp-event pre')).to_have_text('status = verified')
    page.get_by_role('button', name='일치 근거 1', exact=True).click()
    expect(page.locator('.tp-event')).to_have_count(1)
    page.get_by_role('button', name='업무 전체 2', exact=True).click()
    expect(page.locator('.tp-event')).to_have_count(2)
    page.get_by_role('combobox', name='활동 그룹핑').select_option('date-ticket')
    assert page.evaluate("localStorage.getItem('ltm.tracking.feed.grouping')") == 'date-ticket'
    expect(page.locator('.tp-event-date')).to_have_count(2)
    page.get_by_role('button', name='원본 티켓 ↗', exact=True).first.click()
    assert page.evaluate('vm.ticket') == 'T-1'
    page.get_by_role('button', name='댓글 근거 보기 →', exact=True).click()
    expect(page.locator('.tp-event.is-selected')).to_have_attribute('data-event-id', 'rich')
    page.evaluate('vm.leaf=true')
    expect(page.locator('.tp-evidence')).to_have_class('tp-evidence tp-evidence-leaf')
    page.get_by_role('button', name='상위 범위로 돌아가기', exact=True).click()
    expect(page.locator('.tp-evidence')).to_have_count(0)
    assert not errors
