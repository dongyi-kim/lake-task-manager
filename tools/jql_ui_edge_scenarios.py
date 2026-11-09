"""Focused actual-UI cancellation and mutation checks, against an isolated benchmark server."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.jql_ui_benchmark import BASE, STATE_JS, measure, new_page, request, wait_idle, wait_state


def rapid_filter(browser, repo, rows, errors):
    page = new_page(browser, repo, errors)
    measure(page, 'rapid-prime-etl', lambda: page.goto(BASE+'/#/mytasks'), rows)
    measure(page, 'rapid-catalog-reference', lambda: page.locator('.mt-qf-sel').select_option('Catalog'), rows)
    expected = sorted(rows[-1]['state']['keys'])
    page.context.close()
    wait_idle()
    request('/__jql_benchmark/reset', method='POST')
    page = new_page(browser, repo, errors)
    before = request('/__jql_benchmark')
    page.goto(BASE+'/#/mytasks')
    for _ in range(500):
        old = page.evaluate(STATE_JS)
        if old.get('cards',0) and not old.get('complete'):
            break
        page.wait_for_timeout(20)
    else:
        rows.append({"name": "rapid-partial-frame", "skipped": True,
                     "reason": "No partial frame observed in bounded zero-latency window; not cancellation qualification"})
        wait_state(page)
        return page
    middle = request('/__jql_benchmark')
    measure(page, 'rapid-switch-to-catalog', lambda:page.locator('.mt-qf-sel').select_option('Catalog'), rows)
    state=rows[-1]['state']
    assert state['requestToken'] != old['requestToken']
    assert state['key'].startswith('module:Catalog|')
    assert sorted(state['keys']) == expected
    complete_before_switch=[x for x in middle['requests'][len(before['requests']):]
                            if x['done'] and x['path']=='/rest/api/2/search']
    measure(page,'rapid-return-to-etl',lambda:page.locator('.mt-qf-sel').select_option('ETL'),rows)
    repeated={x['params'].get('jql') for x in complete_before_switch} & {
        x['params'].get('jql') for x in rows[-1]['requests'] if x['path']=='/rest/api/2/search'}
    rows[-1]['completedSearchesBeforeSwitch']=len(complete_before_switch)
    rows[-1]['sameSearchRepeated']=sorted(repeated)
    # Completed physical queries are reusable even though the old UI request was aborted.
    return page


def mutate(page, rows):
    candidates = page.locator('.mt-card[data-key]')
    key = None
    for index in range(min(30, candidates.count())):
        candidate = candidates.nth(index).get_attribute('data-key')
        if request(f'/api/ticket/{candidate}')['statusCategory'] != 'done':
            key = candidate
            break
    if key is None:
        raise RuntimeError('No rendered non-Done public ticket available for a permitted summary mutation')
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
    rows[-1].update(targetKey=key, latestValueVerified=True, mutationCoverage='summary only')


def generic_query(page, rows):
    query='project = DL AND (component = ETL OR component = Catalog) ORDER BY key ASC'
    reordered='(component = Catalog OR component = ETL) AND project = DL ORDER BY key ASC'
    def submit(value):
        page.locator('.mt-qf-jql').fill(value)
        page.locator('.mt-qf-jql').press('Enter')
    measure(page,'generic-jql-cold',lambda:submit(query),rows)
    keys=rows[-1]['state']['keys']
    measure(page,'generic-jql-equivalent',lambda:submit(reordered),rows)
    assert rows[-1]['state']['keys']==keys
    rows[-1]['equivalentSearchRequests'] = rows[-1]['searchRequests']


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
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)


if __name__=='__main__':
    main()
