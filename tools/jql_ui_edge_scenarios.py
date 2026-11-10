"""Focused actual-UI cancellation and mutation checks, against an isolated benchmark server."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.jql_ui_benchmark import BASE, STATE_JS, measure, new_page, request, wait_idle, wait_state
from tools import jql_ui_benchmark as harness


def wait_partial_frame(page):
    deadline = time.perf_counter() + harness.UI_TIMEOUT
    while (remaining := deadline - time.perf_counter()) > 0:
        state = page.evaluate(STATE_JS)
        if state.get('cards', 0) and not state.get('complete'):
            return state
        page.wait_for_timeout(min(20, remaining * 1000))
    return None


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
    old = wait_partial_frame(page)
    if old is None:
        harness.record_row(page, {"name": "rapid-partial-frame", "skipped": True, "ok": False,
                           "partialDeadlineSeconds": harness.UI_TIMEOUT,
                           "reason": "No partial frame observed before configured deadline; cancellation coverage failed"}, rows)
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


def expected_public_siblings(bundle, key):
    """Independent public relationship membership; no product aggregation or private mapping."""
    issues = bundle.world.issues
    row = issues[key]
    if row.get('type') == 'Epic':
        return []
    parent, epic = row.get('parentKey'), row.get('epicKey')
    if parent:
        return sorted(k for k, value in issues.items() if value.get('parentKey') == parent)
    return sorted(k for k, value in issues.items() if epic and value.get('epicKey') == epic)


DIALOG_JS = """() => {
 function find(n){if(!n)return null;if(Array.isArray(n)){for(const x of n){const v=find(x);if(v)return v;}return null;}
  if(n.component){if(n.component.type.name==='TicketDialog')return n.component.proxy;const v=find(n.component.subTree);if(v)return v;}
  return Array.isArray(n.children)?find(n.children):null;}
 const v=find(document.querySelector('#app')?._vnode); if(!v)return {};
 const nodes=[...document.querySelectorAll('.tkt-ov .sec-sib .spn-sibrow')];
 const current=(v.siblings||[]).filter(s=>s.current);
 return {dialogKey:v.keyId, requestId:v._req, modelTitle:v.v?.summary,
  title:document.querySelector('.tkt-ov .tb-sum')?.textContent.trim(), panelState:v.panelState?.siblings?.state,
  modelKeys:(v.siblings||[]).map(s=>s.key), modelCount:(v.siblings||[]).length,
  renderedKeys:nodes.map(n=>n.title.split(' · ')[0]), renderedCount:nodes.length,
  currentKeys:current.map(s=>s.key), siblingCurrentTitle:current[0]?.summary,
  error:v.err, sumError:v.sumErr, sumBusy:v.sumBusy};
}"""


def mutate(page, rows, bundle):
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
    expected = expected_public_siblings(bundle, key)
    dialog_proof = {}

    def open_ticket():
        page.locator(f'.mt-card[data-key="{key}"]').first.click()
        page.locator('.tkt-ov .tb-sumedit').first.wait_for()

    def close_ticket():
        page.locator('.tkt-ov .tb-actions .close').click()
        page.locator('.tkt-ov').wait_for(state='detached')

    def rename():
        open_ticket()
        before_request = page.evaluate(DIALOG_JS)['requestId']
        page.locator('.tkt-ov .tb-sumedit').first.click()
        page.locator('.tb-sum-input').fill(title)
        with page.expect_response(lambda r:r.url.endswith(f'/api/ticket/{key}/fields') and r.request.method=='PUT') as response:
            page.locator('.tb-sum-input').press('Enter')
        assert response.value.status == 200 and response.value.json().get('ok') is not False
        page.locator('.tb-sum-input').wait_for(state='detached')
        # Deliberately allow the write while siblings are loading. Prove recovery before closing,
        # rather than hiding the failed auxiliary panel by removing the dialog.
        deadline = page._jql_evidence['deadlineAt']
        while time.perf_counter() < deadline:
            proof = page.evaluate(DIALOG_JS)
            task = page.evaluate(STATE_JS)
            if (proof.get('dialogKey') == key and proof.get('requestId', 0) > before_request
                    and proof.get('panelState') == 'ready' and proof.get('title') == title
                    and proof.get('modelTitle') == title and not proof.get('error') and not proof.get('sumError')
                    and not proof.get('sumBusy') and sorted(proof.get('modelKeys', [])) == expected
                    and sorted(proof.get('renderedKeys', [])) == expected
                    and proof.get('modelCount') == len(expected) and proof.get('renderedCount') == len(expected)
                    and proof.get('currentKeys') == ([key] if key in expected else [])
                    and (key not in expected or proof.get('siblingCurrentTitle') == title)
                    and task.get('complete') and task.get('activeKey') == task.get('key') and task.get('requestToken')):
                settlement = request('/__jql_benchmark/settle', method='POST')
                current = page.evaluate(DIALOG_JS)
                current_task = page.evaluate(STATE_JS)
                if (settlement.get('complete') is True and settlement.get('active') == 0
                        and settlement.get('admitted') == 0 and current == proof
                        and current_task.get('requestToken') == task['requestToken']):
                    dialog_proof.update(proof, targetKey=key, expectedTitle=title, expectedKeys=expected,
                        beforeRequestId=before_request, currentRequestId=current['requestId'],
                        taskToken=task['requestToken'], verifiedAt=time.perf_counter(),
                        worldHash=bundle.manifest['data_sha256'], settled=True, settlement=settlement)
                    if harness.EVIDENCE_DIR:
                        screenshot = harness.EVIDENCE_DIR / f"{len(rows):03d}-mutation-dialog-proof.png"
                        page.screenshot(path=str(screenshot), full_page=False)
                        dialog_proof['screenshot'] = str(screenshot)
                    # Check the current dialog request once more after the screenshot.
                    assert page.evaluate(DIALOG_JS)['requestId'] == current['requestId']
                    page._jql_evidence['dialogProof'] = dialog_proof
                    if harness.EVIDENCE_DIR:
                        proof_path = harness.EVIDENCE_DIR / f"{len(rows):03d}-mutation-dialog-proof.json"
                        dialog_proof['artifact'] = str(proof_path)
                        with proof_path.open('x', encoding='utf-8') as stream:
                            json.dump(dialog_proof, stream, ensure_ascii=False, indent=2)
                    break
            page.wait_for_timeout(min(40, max(0, deadline-time.perf_counter())*1000))
        else:
            raise TimeoutError({'dialog': page.evaluate(DIALOG_JS), 'expectedKeys': expected, 'expectedTitle': title})
        close_ticket()
        page.locator(f'.mt-card[data-key="{key}"]').first.get_by_text(title,exact=True).wait_for()

    measure(page,'mutation-summary',rename,rows)
    assert request(f'/api/ticket/{key}')['summary']==title
    rows[-1].update(targetKey=key, latestValueVerified=True, mutationCoverage='summary only', dialogProof=dialog_proof)


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
    from tools.codex_mcp_eval.etl import build_etl_world
    bundle = build_etl_world()
    assert request('/__jql_benchmark')['worldHash'] == bundle.manifest['data_sha256']
    from playwright.sync_api import sync_playwright
    if args.output.exists():
        raise FileExistsError(args.output)
    harness.EVIDENCE_DIR = args.output.parent / (args.output.stem + '-evidence')
    harness.EVIDENCE_DIR.mkdir(parents=True, exist_ok=False)
    harness.HTTP_STREAM = (harness.EVIDENCE_DIR / 'http.jsonl').open('x', encoding='utf-8')
    harness.RAW_STREAM = (harness.EVIDENCE_DIR / 'rows.jsonl').open('x', encoding='utf-8')
    rows, errors=[],[]
    result={'rows':rows,'consoleErrors':errors,'fixture':request('/__jql_benchmark')['worldHash'],
            'httpResponses':harness.HTTP_RESPONSES,'httpRequestStarts':harness.HTTP_REQUEST_STARTS,
            'networkFailures':harness.NETWORK_FAILURES,
            'scope':'requestfailed events remain unclassified; rapid scenario owns cancellation correctness'}
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
                mutate(page,rows,bundle)
            if args.generic:
                generic_query(page,rows)
            browser.close()
        result['recoveryClassification'] = harness.classify_recovered_reads(errors, harness.HTTP_RESPONSES,
            harness.NETWORK_FAILURES, rows, bundle.manifest['data_sha256'], harness.HTTP_REQUEST_STARTS)
        assert result['recoveryClassification']['complete'], result['recoveryClassification']
    except BaseException as exc:
        result['error']=repr(exc)
        raise
    finally:
        harness.HTTP_STREAM.close(); harness.HTTP_STREAM = None
        harness.RAW_STREAM.close(); harness.RAW_STREAM = None
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)


if __name__=='__main__':
    main()
