"""WBS-style Tracking navigation preserves issue identity, branches and controls."""
import json
import mimetypes
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest

from support.browser import launch_chromium
from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    module = (REPO_ROOT / 'app/static/components/tracking-navigator.js').as_uri()
    source = 'import assert from "node:assert/strict";\nimport nav from ' + json.dumps(module) + ';\n'
    result = subprocess.run([node, '--input-type=module', '-e', source + script],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr


def test_issue_badges_keep_bug_unknown_and_virtual_voc_semantics():
    run_js('''
      const ctx = {...nav.methods};
      for (const [kind,label,color] of [
        ['epic','Epic','epic'],['task','업무','task'],['subtask','서브','sub'],
        ['bug','버그','bug'],['story','스토리','story'],['improvement','개선','feat'],
        ['newfeature','기능','feat'],
      ]) {
        const row={kind};
        assert.equal(ctx.kind(row),label);
        assert.equal(ctx.typeColor(row),'var(--ty-'+color+')');
      }
      assert.equal(ctx.kind({kind:'task',issue:{type:'Bug'}}),'버그');
      assert.equal(ctx.kind({kind:'custom',type:'고객 요청'}),'고객 요청');
      assert.equal(ctx.kind({kind:'epic',virtual:true}),'Epic'); // VOC categories remain Epic.
      const group={kind:'epic',virtual:true,epicUnassigned:true};
      assert.equal(ctx.kind(group),'미소속');
      assert.equal(ctx.typeColor(group),undefined);
      assert.equal(ctx.kind({kind:'wbs'}),'WBS Task');
      assert.equal(ctx.kind({kind:'module'}),'모듈');
    ''')


def test_branch_guides_continue_only_ancestors_with_later_siblings():
    run_js('''
      const rows=[['module',0],['w1',1],['e1',2],['task',3],['e2',2],['w2',1],['other',0]]
        .map(([id,depth])=>({id,depth}));
      const before=JSON.stringify(rows);
      const entries=nav.computed.entries.call({rows});
      assert.deepEqual(entries.map(e=>e.last),[false,false,false,true,true,true,true]);
      assert.deepEqual(entries.map(e=>e.guides),[[],[],[0],[0,1],[0],[],[]]);
      assert.equal(JSON.stringify(rows),before);
      const closed={w1:true}, activeId='task', events=[];
      nav.methods.activate.call({$emit:(...event)=>events.push(event),closed,activeId},rows[2]);
      assert.deepEqual(events,[['scope','e1']]);
      assert.deepEqual(closed,{w1:true});
    ''')


@pytest.fixture
def navigator_browser():
    playwright = pytest.importorskip('playwright.sync_api')
    static = REPO_ROOT / 'app/static'
    html = '''<!doctype html><html lang="ko"><head><meta charset="utf-8">
      <link rel="stylesheet" href="/styles/tokens.css"><link rel="stylesheet" href="/styles/base.css">
      <link rel="stylesheet" href="/styles/tracking.css"><link rel="stylesheet" href="/styles/tracking-scope.css">
      </head><body><main id="app" class="tracking" style="padding:24px"></main>
      <script type="module">
        import {createApp} from '/vendor/vue.esm-browser.prod.js';
        import Navigator from '/components/tracking-navigator.js';
        import {trackingRows} from '/components/trackingModel.js';
        const long='긴 제목 전체를 툴팁으로 확인하고 선택하는 업무 범위 '.repeat(5);
        const tree=[{id:'m',kind:'module',name:'모듈',children:[{id:'w',kind:'wbs',name:'WBS 업무',children:[
          {id:'e',kind:'Epic',name:'실제 Epic',children:[{id:'t',kind:'Task',key:'T-1',name:long,children:[{id:'s',kind:'Sub-Task',key:'S-1',name:'하위 업무'}]}]},
          {id:'u',kind:'Epic',name:'소속 Epic 없음',virtual:true,epicUnassigned:true,children:[{id:'b',kind:'Bug',key:'B-1',name:'버그 수정'}]},
          {id:'voc',kind:'Epic',name:'기존 VOC 분류',virtual:true,children:[]},
        ]}]}];
        window.navApp=createApp({components:{Navigator},data:()=>({width:300,closed:{},activeId:'',reveal:false,folds:0,inspections:0}),
          computed:{rows(){return trackingRows({tree,issues:[],events:[]},{closed:this.closed})}},
          template:`<Navigator :style="{width:width+'px'}" :width="width" :rows="rows" :closed="closed" :active-id="activeId" :reveal-matches="reveal" :total="3" mode="report"
            @scope="activeId=$event" @fold="closed[$event.id]=!closed[$event.id];folds++" @resize="width=$event" @inspect="inspections++"/>`}).mount('#app');
      </script></body></html>'''

    def respond(route):
        path = urlsplit(route.request.url).path
        if path == '/':
            route.fulfill(content_type='text/html; charset=utf-8', body=html)
        else:
            asset = (static / path.lstrip('/')).resolve()
            if asset.is_relative_to(static.resolve()) and asset.is_file():
                content_type = 'text/javascript' if asset.suffix == '.js' else mimetypes.guess_type(asset.name)[0] or 'application/octet-stream'
                route.fulfill(content_type=content_type, body=asset.read_bytes())
            else:
                route.fulfill(status=404)

    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/*', respond)
            page.goto('http://127.0.0.1:4457/#/tracking')
            playwright.expect(page.locator('.tr-scope-node')).to_have_count(8)
            yield page, errors
        finally:
            browser.close()


@pytest.mark.parametrize('theme,width', [('light', 1280), ('dark', 1280), ('light', 1440), ('dark', 1440)])
def test_compact_wbs_controls_keep_full_labels_branches_and_keyboard_actions(navigator_browser, theme, width):
    from playwright.sync_api import expect

    page, errors = navigator_browser
    page.set_viewport_size({'width': width, 'height': 1000})
    page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
    rows = page.locator('.tr-scope-node')
    heights = rows.evaluate_all('(rows)=>rows.slice(0,5).map(row=>row.getBoundingClientRect().height)')
    assert heights == [34, 34, 28, 26, 26]
    toggle = page.get_by_role('button', name='실제 Epic 접기', exact=True)
    assert toggle.bounding_box()['width'] == toggle.bounding_box()['height'] == 24
    assert toggle.locator('span').evaluate('el=>el.getBoundingClientRect().width') == 13
    expect(toggle).to_have_text('−')
    expect(rows.nth(4).locator('.tr-tree-guide')).to_have_count(1)
    expect(rows.nth(7).locator('.tr-tree-branch')).to_have_class('tr-tree-branch is-last')
    expect(page.locator('.tr-scope-type-unassigned')).to_have_text('미소속')
    expect(rows.nth(7).locator('.tr-scope-type')).to_have_text('Epic')
    expect(rows.nth(6).locator('.tr-scope-type')).to_have_text('버그')
    assert rows.nth(6).locator('.tr-scope-type').evaluate('el=>el.style.getPropertyValue("--type-color")') == 'var(--ty-bug)'
    label = rows.nth(3).locator('.tr-scope-node-label')
    title = label.get_attribute('title')
    assert title.endswith('(T-1)') and len(title) > 150
    assert label.locator('strong').evaluate('el=>el.scrollWidth>el.clientWidth && getComputedStyle(el).whiteSpace==="nowrap"')
    label.focus()
    label.press('Enter')
    expect(label).to_have_attribute('aria-current', 'true')
    toggle.focus()
    toggle.press('Space')
    expect(page.get_by_role('button', name='실제 Epic 펼치기', exact=True)).to_have_attribute('aria-expanded', 'false')
    assert page.evaluate('window.navApp.closed.e') is True
    page.get_by_role('button', name='전체 업무 3', exact=True).click()
    assert page.evaluate('window.navApp.closed.e') is True
    page.get_by_role('combobox', name='계층 패널 너비', exact=True).select_option('420')
    assert page.locator('.tr-scope-nav').bounding_box()['width'] == 420
    page.evaluate('window.navApp.reveal=true')
    expect(page.get_by_role('button', name='실제 Epic 접기', exact=True)).to_be_disabled()
    assert page.evaluate('window.navApp.closed.e') is True
    page.get_by_role('button', name='계층별 집계', exact=True).click()
    assert page.evaluate('window.navApp.inspections') == 1
    assert not errors
