"""Focused actual-UI cancellation and mutation checks, against an isolated benchmark server."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from jql_ui_benchmark import BASE, STATE_JS, measure, new_page, request, wait_idle, wait_state


def rapid_filter(browser, repo, rows, errors):
    page = new_page(browser, repo, errors)
    measure(page, 'rapid-prime-etl', lambda: page.goto(BASE+'/#/mytasks'), rows)
    measure(page, 'rapid-workbench-reference', lambda: page.locator('.mt-qf-sel').select_option('Workbench'), rows)
    expected = sorted(rows[-1]['state']['keys'])
    page.context.close()
    wait_idle()
    request('/__jql_benchmark/reset', method='POST')
    page = new_page(browser, repo, errors)
    before = request('/__jql_benchmark')
    page.goto(BASE+'/#/mytasks')
    for _ in range(6000):
        old = page.evaluate(STATE_JS)
        if old.get('cards',0) and not old.get('complete'):
            break
        page.wait_for_timeout(20)
    else:
        raise AssertionError('No partial Task frame observed before completion')
    middle = request('/__jql_benchmark')
    measure(page, 'rapid-switch-to-workbench', lambda:page.locator('.mt-qf-sel').select_option('Workbench'), rows)
    state=rows[-1]['state']
    assert state['requestToken'] != old['requestToken']
    assert state['key'].startswith('module:Workbench|')
    assert sorted(state['keys']) == expected
    complete_before_switch=[x for x in middle['requests'][len(before['requests']):]
                            if x['done'] and x['path']=='/rest/api/2/search']
    measure(page,'rapid-return-to-etl',lambda:page.locator('.mt-qf-sel').select_option('ETL'),rows)
    repeated={x['params'].get('jql') for x in complete_before_switch} & {
        x['params'].get('jql') for x in rows[-1]['requests'] if x['path']=='/rest/api/2/search'}
    rows[-1]['completedSearchesBeforeSwitch']=len(complete_before_switch)
    rows[-1]['sameSearchRepeated']=sorted(repeated)
    # Completed physical queries are reusable even though the old UI request was aborted.
    assert not repeated, repeated
    return page


def mutate(page, rows):
    key='DL-20001'
    title='[ETL] JQL UI mutation verification'
    if request(f'/api/ticket/{key}')['summary']==title:
        title+=' repeated'

    def open_ticket():
        page.locator(f'.mt-card[data-key="{key}"]').first.click()
        page.locator('.tkt-ov .tb-sumedit').first.wait_for()

    def close_ticket():
        page.locator('.tkt-ov .tb-actions .close').click()
        page.locator('.tkt-ov').wait_for(state='detached')

    def rename():
        open_ticket()
        page.locator('.tkt-ov .tb-sumedit').first.click()
        page.locator('.tb-sum-input').fill(title)
        with page.expect_response(lambda r:r.url.endswith(f'/api/ticket/{key}/fields') and r.request.method=='PUT') as response:
            page.locator('.tb-sum-input').press('Enter')
        assert response.value.status == 200 and response.value.json().get('ok') is not False
        page.locator('.tb-sum-input').wait_for(state='detached')
        close_ticket()
        page.locator(f'.mt-card[data-key="{key}"]').first.get_by_text(title,exact=True).wait_for()

    measure(page,'mutation-summary',rename,rows)
    assert request(f'/api/ticket/{key}')['summary']==title

    def description():
        open_ticket()
        page.locator('.tkt-ov .sec-edit').first.click()
        editor=page.locator('.tkt-desc-edit [contenteditable=true]').first
        editor.fill('JQL UI regression: updated body remains visible after mutation.')
        with page.expect_response(lambda r:'/api/ticket/'+key in r.url and r.request.method=='PUT') as response:
            page.locator('.tkt-desc-edit-f').get_by_role('button',name='저장',exact=True).click()
        assert response.value.status==200 and response.value.json().get('ok') is not False
        page.locator('.tkt-desc-edit').wait_for(state='detached')
        page.locator('.tkt-ov .tkt-desc').filter(has_text='JQL UI regression:').first.wait_for()
        close_ticket()

    measure(page,'mutation-description',description,rows)
    assert 'JQL UI regression:' in request(f'/api/ticket/{key}')['descriptionHtml']

    def transition():
        open_ticket()
        page.locator('.tkt-ov').get_by_title('상태 변경',exact=True).click()
        page.locator('.tkt-ov .fe-pop .fe-i').filter(has_text='In Progress').click()
        with page.expect_response(lambda r:r.url.endswith(f'/api/ticket/{key}/transition') and r.request.method=='POST') as response:
            page.locator('.trx-ok').click()
        assert response.value.status==200 and response.value.json().get('ok') is not False
        close_ticket()

    measure(page,'mutation-transition',transition,rows)
    assert request(f'/api/ticket/{key}')['statusCategory']=='inprogress'
    assert key in rows[-1]['state']['keys']
    rows[-1]['latestValueVerified']=True


def generic_query(page, rows):
    query='project = DL AND (priority = "P2-Major" OR labels = "perf-fixture") ORDER BY key ASC'
    reordered='(labels = "perf-fixture" OR priority = "P2-Major") AND project = DL ORDER BY key ASC'
    def submit(value):
        page.locator('.mt-qf-jql').fill(value)
        page.locator('.mt-qf-jql').press('Enter')
    measure(page,'generic-jql-cold',lambda:submit(query),rows)
    keys=rows[-1]['state']['keys']
    measure(page,'generic-jql-equivalent',lambda:submit(reordered),rows)
    assert rows[-1]['state']['keys']==keys
    assert rows[-1]['searchRequests']==0


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--rapid',action='store_true')
    p.add_argument('--mutation',action='store_true')
    p.add_argument('--generic',action='store_true')
    args=p.parse_args()
    from playwright.sync_api import sync_playwright
    rows, errors=[],[]
    result={'rows':rows,'consoleErrors':errors,'fixture':request('/__jql_benchmark')['worldHash']}
    try:
        with sync_playwright() as pw:
            installed=list((Path(os.environ['LOCALAPPDATA'])/'ms-playwright').glob('chromium-*/chrome-win64/chrome.exe'))
            executable=max(installed,key=lambda x:int(x.parents[1].name.rsplit('-',1)[1]))
            browser=pw.chromium.launch(headless=True,executable_path=str(executable))
            if args.rapid:
                page=rapid_filter(browser,args.repo,rows,errors)
            else:
                page=new_page(browser,args.repo,errors)
                measure(page,'mutation-prime',lambda:page.goto(BASE+'/#/mytasks'),rows)
            if args.mutation:
                mutate(page,rows)
            if args.generic:
                generic_query(page,rows)
            browser.close()
        assert not errors,errors
    except BaseException as exc:
        result['error']=repr(exc)
        raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
