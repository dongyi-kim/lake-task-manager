"""Actual-browser JQL latency comparison; all run artifacts live under .cache.

The flow under test is: Task -> many quick-filter changes -> workload -> WBS -> Task,
with rendered progress, result identity, cache reuse, and upstream request accounting.
Browser plugin not available: use the already installed Python Playwright runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from jql_ui_benchmark_fixture import ETL, WORKBENCH, LOGICAL_NOW

BASE = "http://127.0.0.1:4457"


def request(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def prepare(repo, run):
    import yaml
    config = run / "config"
    config.mkdir(parents=True)
    for source in (repo / "config").glob("*.y*ml"):
        shutil.copy2(source, config / source.name)
    (config / "people.yaml").write_text(yaml.safe_dump({"ETL": ETL, "Workbench": WORKBENCH,
        "TEST": ["test.ui01", "test.ui02"]}), encoding="utf-8")
    plan = {"project_key": "DL", "wbs": [
        {"module": "ETL", "tasks": [{"name": "ETL 성능 검증", "start": "2026-01-01", "end": "2026-12-31",
            "epics": [{"ticket": "DL-101", "weight": 1}, {"ticket": "DL-102", "weight": 1}]}]},
        {"module": "Workbench", "tasks": [{"name": "중첩 모듈 검증", "start": "2026-01-01", "end": "2026-12-31",
            "epics": [{"ticket": "DL-110", "weight": 1}, {"ticket": "DL-101", "weight": 1}]}]},
        {"module": "TEST", "tasks": []}]}
    (config / "wbs_config.yaml").write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
    cfg = yaml.safe_load((config / "jira.yml").read_text(encoding="utf-8"))
    cfg["env"] = "mock"
    cfg["managers"] = []
    cfg["search"]["jira"]["projects"] = ["DL"]
    (config / "jira.yml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    bootstrap = run / "bootstrap"
    bootstrap.mkdir()
    (bootstrap / "sitecustomize.py").write_text(
        "from jql_ui_benchmark_fixture import install\ninstall()\n", encoding="utf-8")
    return config, bootstrap


def server_executable(repo):
    if os.name != "nt":
        return sys.executable
    # run.py's Python launcher exits after spawning the named image; own that image directly.
    spec = importlib.util.spec_from_file_location("benchmark_process_identity", repo / "app/infra/process_identity.py")
    identity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(identity)
    executable = identity.named_launcher_path(sys.executable, "mock")
    identity._sync_launcher(Path(getattr(sys, "_base_executable", sys.executable)), executable, "mock")
    return str(executable)


def stop_owned_server(process):
    if process is None or process.poll() is not None:
        return
    # Never target a port, image name, or an externally attached instance.
    if os.name == "nt":
        result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode and process.poll() is None:
            raise RuntimeError(f"Could not stop owned benchmark process {process.pid}: {result.stderr!r}")
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)


def launch(repo, run, latency):
    try:
        health = request("/api/health")
    except OSError:
        health = None
    if health is not None:
        raise RuntimeError(f"Port 4457 is already occupied; inspect and stop verified LTM first: {health}")
    config, bootstrap = prepare(repo, run)
    env = os.environ.copy()
    env.update(JIRA_ENV="mock", APP_PORT="4457", CONFIG_DIR=str(config), LAKE_CACHE_DIR=str(run / "runtime"),
               LAKE_MOCK_LATENCY_MS=str(latency), LAKE_NO_WINDOW="1", PYTHONUTF8="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join([str(bootstrap), str(Path(__file__).parent), str(repo)]))
    log = (run / "server.log").open("w", encoding="utf-8")
    process = None
    try:
        process = subprocess.Popen([server_executable(repo), str(repo / "run.py"), "--reload"], cwd=repo,
            env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Server exited during startup: {run / 'server.log'}")
            try:
                health = request("/api/health")
                metrics = request("/__jql_benchmark")
                return process, log, health, metrics
            except (OSError, ValueError):
                time.sleep(.25)
        raise RuntimeError(f"Server did not start: {run / 'server.log'}")
    except BaseException:
        try:
            stop_owned_server(process)
        finally:
            log.close()
        raise


STATE_JS = """() => {
 const selector = location.hash.includes('workload') ? '.wl-view' : location.hash.includes('wbs') ? '.wbs-view' : '.mytasks';
 const name=selector==='.wl-view'?'WorkloadView':selector==='.wbs-view'?'WbsView':'MyTasksView';
 function find(n){if(!n)return null;if(Array.isArray(n)){for(const x of n){const v=find(x);if(v)return v;}return null;}
  if(n.component){if(n.component.type.name===name)return n.component.proxy;const v=find(n.component.subTree);if(v)return v;}
  return Array.isArray(n.children)?find(n.children):null;}
 const v=find(document.querySelector('#app')?._vnode);
 if (!v) return {ready:false, selector};
 if (selector === '.mytasks') return {ready:!!v.model,
   complete:!!v.model?.streamComplete && !v._streamAbort && Object.values(v.streamAxes).every(x=>x.state==='done') && !(v.model.groups||[]).some(x=>x.childrenPending),
   key:v.apiScope+'|'+v.openFilter+'|'+v.progFilter+'|'+v.doneFilter,
   activeKey:v._activeCacheKey, requestToken:v._activeRequestToken, sequence:v.model?.snapshotSequence,
   keys:(v.allCards || []).map(x=>x.key), counts:v.model && v.model.counts,
   axes:v.streamAxes, progress:v.streamProgress, cards:document.querySelectorAll('.mt-card').length, err:v.err};
 if (selector === '.wl-view') return {ready:!!v.d, complete:!!v.statsComplete,
   key:v.mod+'|'+v.assignedWindow+'|'+v.doneDays, requestToken:v.peopleLoadEpoch, counts:v.totals,
   people:Object.values(v.pstat || {}).map(x=>({id:x.id,open:x.open,inProgress:x.inProgress,done7d:x.done7d,error:x.error})),
   cards:document.querySelectorAll('.prow .wbars:not(.wl-pending)').length, err:v.err};
 return {ready:!!v.D,complete:!!v.D && Object.values(v.epicTree).every(Array.isArray),key:'wbs',counts:v.pmo,
   treeKeys:Object.fromEntries(Object.entries(v.epicTree).filter(([k,x])=>Array.isArray(x)).map(([k,x])=>[k,x.map(t=>t.key)])),
   cards:document.querySelectorAll('.g-labels .lrow').length,err:v.err,partial:v.partial};
}"""


def wait_state(page, timeout=240):
    start, first = time.perf_counter(), None
    while time.perf_counter() - start < timeout:
        state = page.evaluate(STATE_JS)
        if first is None and state.get("cards", 0) and state.get('activeKey',state.get('key')) == state.get('key'):
            first = (time.perf_counter()-start)*1000
        if state.get("complete") and state.get('activeKey',state.get('key')) == state.get('key'):
            return state, first, (time.perf_counter()-start)*1000
        if state.get("err"):
            raise RuntimeError(state)
        page.wait_for_timeout(40)
    raise TimeoutError(state)


def measure(page, name, action, result):
    before = request("/__jql_benchmark")
    prior = page.evaluate(STATE_JS)
    start = time.perf_counter()
    page.evaluate("window.__benchLong=[]")
    action()
    click_ms = (time.perf_counter()-start)*1000
    state, first, elapsed = wait_state(page)
    completed_ms = (time.perf_counter()-start)*1000
    assert state.get('ready') and state.get('cards',0)>0, state
    assert page.locator('vite-error-overlay,webpack-dev-server-client-overlay').count()==0
    after = request("/__jql_benchmark")
    records = after["requests"][len(before["requests"]):]
    row = {"name":name,"actionMs":round(click_ms,2), "firstContentMs":round(click_ms+(first or 0),2),
        "completeMs":round(completed_ms,2), "state":state,
        "requests":records, "searchRequests":sum(r["path"] == "/rest/api/2/search" for r in records),
        "allRequests":len(records), "cacheDelta":{k:v-before["cache"].get(k,0) for k,v in after["cache"].items()},
        "longTasks":page.evaluate("window.__benchLong || []")}
    row['previousKey'] = prior.get('key')
    row['previousToken'] = prior.get('requestToken')
    result.append(row)
    print(json.dumps({k:row[k] for k in ("name","firstContentMs","completeMs","searchRequests","allRequests")}), flush=True)


def journey(page, rows, repeats):
    measure(page,"task-module-etl-cold",lambda: page.goto(BASE+"/#/mytasks"),rows)
    for cycle in range(repeats):
        def person(index, user):
            page.locator('.mt-qf-fe .fe-v').nth(index).click()
            page.get_by_placeholder('이름 또는 사번').fill(user)
            page.locator('.fe-pop .fe-i').filter(has=page.locator('em',has_text=user)).click()

        def expand_wbs():
            page.get_by_role('button',name='전체 Epic 펼치기',exact=True).click()
            for key in ('DL-101','DL-102','DL-110'):
                page.locator('.lrow.epic').filter(has_text=key).first.locator('.tgl').last.click()

        def expand_subtasks():
            page.locator('.lrow.task.clk .tgl').last.click()
            page.wait_for_function("document.querySelectorAll('.g-labels .lrow.sub').length > 0")

        actions = [
            ("done-month", lambda: page.locator('.mt-colh.c-done').get_by_role('button',name='1달',exact=True).click()),
            ("allocated-2w", lambda: page.locator('.mt-colh.c-todo').get_by_role('button',name='2주 내 갱신',exact=True).click()),
            ("progress-month", lambda: page.locator('.mt-colh.c-inprogress').get_by_role('button',name='1달 내 갱신',exact=True).click()),
            ("assignee-scope", lambda: page.get_by_role('button',name='담당자',exact=True).click()),
            ("assignee-other", lambda: person(0, ETL[1+(cycle+5)%14])),
            ("reporter-scope", lambda: page.get_by_role('button',name='보고자',exact=True).click()),
            ("reporter-other", lambda: person(1, ETL[1+(cycle+10)%14])),
            ("module-workbench", lambda: page.locator('.mt-qf-sel').select_option('Workbench')),
            ("module-etl", lambda: page.locator('.mt-qf-sel').select_option('ETL')),
            ("allocated-all", lambda: page.locator('.mt-colh.c-todo').get_by_role('button',name='모두',exact=True).click()),
            ("progress-all", lambda: page.locator('.mt-colh.c-inprogress').get_by_role('button',name='모두',exact=True).click()),
            ("done-week", lambda: page.locator('.mt-colh.c-done').get_by_role('button',name='1주',exact=True).click()),
            ("workload-after-task", lambda: page.locator('a[href="#/workload"]').click()),
            ("workload-module-workbench", lambda: page.locator('.wl-modsel').select_option('Workbench')),
            ("workload-module-etl", lambda: page.locator('.wl-modsel').select_option('ETL')),
            ("workload-month", lambda: page.locator('.wl-opt').filter(has=page.locator('.wl-opt-l',has_text='할당 갱신')).get_by_role('button',name='1달',exact=True).click()),
            ("workload-all", lambda: page.locator('.wl-opt').filter(has=page.locator('.wl-opt-l',has_text='할당 갱신')).get_by_role('button',name='전체',exact=True).click()),
            ("workload-done-week", lambda: page.locator('.wl-opt').filter(has=page.locator('.wl-opt-l',has_text='완료 기간')).get_by_role('button',name='1주',exact=True).click()),
            ("workload-done-month", lambda: page.locator('.wl-opt').filter(has=page.locator('.wl-opt-l',has_text='완료 기간')).get_by_role('button',name='4주',exact=True).click()),
            ("wbs-after-workload", lambda: page.locator('a[href="#/wbs"]').click()),
            ("wbs-expand", expand_wbs),
            ("wbs-subtasks", expand_subtasks),
            ("wbs-collapse", lambda: page.get_by_role('button',name='전체 접기',exact=True).click()),
            ("task-return", lambda: page.locator('a[href="#/mytasks"]').click()),
        ]
        for name, action in actions:
            measure(page,f"{cycle+1}:{name}",action,rows)


def wait_idle():
    for _ in range(480):
        metrics=request('/__jql_benchmark')
        if all(r['done'] for r in metrics['requests']):
            time.sleep(.15)
            if all(r['done'] for r in request('/__jql_benchmark')['requests']):
                return
        time.sleep(.25)
    raise TimeoutError('Upstream background work did not drain')


def new_page(browser, repo, errors):
    context = browser.new_context(viewport={'width':1600,'height':1000})
    guide_ids = re.findall(r'id:\s*"([^"]+)"', (repo/'app/static/lib/guides.js').read_text(encoding='utf-8'))
    context.add_init_script("localStorage.setItem('lake.guides.seen',"+json.dumps(json.dumps(guide_ids))+");")
    # Use CacheMemo's existing injected clock only. Keep browser clocks, throttles,
    # activity checks, crypto IDs and network timeout scheduling untouched.
    memo_source=(repo/'app/static/lib/cacheMemo.js').read_text(encoding='utf-8')
    clock_expr='now = () => Date.now() / 1000'
    assert memo_source.count(clock_expr)==1
    memo_source=memo_source.replace(clock_expr, f'now = () => {LOGICAL_NOW.timestamp()}')
    context.route('**/lib/cacheMemo.js',lambda route:route.fulfill(body=memo_source,content_type='application/javascript'))
    context.add_init_script("""localStorage.setItem('mytasks.opts',JSON.stringify({scope:'module',moduleSel:'ETL',moduleSelExplicit:true,openFilter:'all',progFilter:'all',doneFilter:'1w',groupBy:'sub',subView:'collapsed'}));
      localStorage.setItem('workload.opts',JSON.stringify({mod:'ETL',assignedWindow:'all',doneDays:7}));
      window.__benchLong=[];new PerformanceObserver(l=>window.__benchLong.push(...l.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask',buffered:true});""")
    page=context.new_page()
    page.on('pageerror',lambda e:errors.append(str(e)))
    return page


def entry_samples(browser, repo, rows, errors, cold, warm):
    page=None
    for i in range(cold):
        if page:
            page.context.close()
        wait_idle()
        request('/__jql_benchmark/reset',method='POST')
        page=new_page(browser,repo,errors)
        measure(page,f'entry-cold-{i+1}',lambda:page.goto(BASE+'/#/mytasks'),rows)
    if page is None:
        page=new_page(browser,repo,errors)
        measure(page,'entry-initial',lambda:page.goto(BASE+'/#/mytasks'),rows)
    for i in range(warm):
        measure(page,f'entry-warm-{i+1}',page.reload,rows)
    return page


def reverse_flow(browser, repo, rows, errors):
    for context in list(browser.contexts):
        context.close()
    wait_idle()
    request('/__jql_benchmark/reset',method='POST')
    page=new_page(browser,repo,errors)
    measure(page,'reverse-workload-cold',lambda:page.goto(BASE+'/#/workload'),rows)
    measure(page,'reverse-task-after-workload',lambda:page.locator('a[href="#/mytasks"]').click(),rows)
    measure(page,'reverse-wbs-after-task',lambda:page.locator('a[href="#/wbs"]').click(),rows)
    measure(page,'reverse-workload-return',lambda:page.locator('a[href="#/workload"]').click(),rows)
    return page


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo",type=Path,required=True)
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--latency",type=int,default=0)
    parser.add_argument("--journeys",type=int,default=1)
    parser.add_argument("--cold",type=int,default=0)
    parser.add_argument("--warm",type=int,default=0)
    parser.add_argument("--reverse",action='store_true')
    parser.add_argument("--keep-server",action="store_true")
    parser.add_argument("--attach",action="store_true",help="Debug controls against an existing benchmark instance; not a cold sample")
    args = parser.parse_args()
    args.repo, args.run = args.repo.resolve(), args.run.resolve()
    args.run.mkdir(parents=True,exist_ok=False)
    process, log = None, None
    try:
        if args.attach:
            health, metrics = request('/api/health'), request('/__jql_benchmark')
        else:
            process, log, health, metrics = launch(args.repo,args.run,args.latency)
        from playwright.sync_api import sync_playwright
        rows, errors = [], []
        result = {"baseline":"2fdb8feb8d18f3d8852ae3d1bcda54f058a332e8","repo":str(args.repo),
            "sourceCommit":subprocess.check_output(['git','-c',f'safe.directory={args.repo.as_posix()}','-C',str(args.repo),'rev-parse','HEAD'],text=True).strip(),
            "sourceDiffSha256":hashlib.sha256(subprocess.check_output(['git','-c',f'safe.directory={args.repo.as_posix()}','-C',str(args.repo),'diff','HEAD'])).hexdigest(),
            "harnessSha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('jql_ui*.py')},
            "clockControl":"server Cache/JiraClient.time.time and compile_jql now fixed; browser CacheMemo injected now fixed; real Date.now, scheduling and perf_counter",
            "health":health,"fixture":{k:metrics[k] for k in ("worldHash","issueCount","etlPeople","logicalClock")},
            "latencyMs":args.latency,"browser":"Chromium via Playwright; Browser plugin not available",
            "requestedSamples":{"cold":args.cold,"warm":args.warm,"journeys":args.journeys,"reverse":args.reverse},
            "viewport":{"width":1600,"height":1000},"rows":rows,"consoleErrors":errors}
        try:
            with sync_playwright() as pw:
                # Reuse the installed browser, even when Python Playwright was upgraded separately.
                installed = list((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
                executable = max(installed, key=lambda path: int(path.parents[1].name.rsplit('-',1)[1]))
                browser = pw.chromium.launch(headless=True, executable_path=str(executable))
                page = entry_samples(browser,args.repo,rows,errors,args.cold,args.warm) if (args.cold or args.warm) else new_page(browser,args.repo,errors)
                journey(page,rows,args.journeys)
                if args.reverse:
                    page=reverse_flow(browser,args.repo,rows,errors)
                result["title"], result["url"] = page.title(),page.url
                result["bodyText"] = page.locator('body').inner_text()[:1500]
                assert result['url'].startswith(BASE) and result['title'] and result['bodyText'].strip()
                assert not errors,errors
                browser.close()
        except BaseException as exc:
            result["error"] = repr(exc)
            raise
        finally:
            try:
                result["finalMetrics"] = request('/__jql_benchmark')
            except Exception as exc:
                result["finalMetricsError"] = repr(exc)
            (args.run/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        try:
            if not args.keep_server:
                stop_owned_server(process)
        finally:
            if log:
                log.close()



if __name__ == '__main__':
    main()
