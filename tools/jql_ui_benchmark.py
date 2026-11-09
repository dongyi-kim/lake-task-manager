"""Actual-browser JQL harness; disposable OS scratch and exclusive external evidence.

The flow under test is: Task -> many quick-filter changes -> workload -> WBS -> Task,
with rendered progress, result identity, cache reuse, and upstream request accounting.
Browser plugin not available: use the already installed Python Playwright runtime.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == "__main__":
    # Edge helpers import this canonical name even when the CLI executes the file.
    sys.modules["tools.jql_ui_benchmark"] = sys.modules[__name__]

from tools.jql_ui_benchmark_fixture import ETL, LOGICAL_NOW
from tools.jql_cache_benchmark import dependency_identity, repository_identity, reserve_attempt

BASE = "http://127.0.0.1:4457"
UI_TIMEOUT = 60
EVIDENCE_DIR = None
RAW_STREAM = None
NETWORK_FAILURES = []
CONSOLE_WARNINGS = []


def request(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def prepare(repo, run, bundle=None):
    import yaml
    from tools.codex_mcp_eval.etl import build_etl_world
    bundle = bundle or build_etl_world()
    config = run / "config"
    config.mkdir(parents=True)
    for source in (repo / "config").glob("*.y*ml"):
        shutil.copy2(source, config / source.name)
    (config / "people.yaml").write_text(yaml.safe_dump(bundle.world.people), encoding="utf-8")
    plan = {"project_key": bundle.world.plan["project_key"], "wbs": []}
    for module in bundle.world.modules:
        tasks = [{"id": row["id"], "name": row["name"], "start": row["start"], "end": row["end"],
                  "epics": [{"ticket": epic["key"], "weight": epic["weight"]} for epic in row["epics"]]}
                 for row in bundle.world.plan["wbs"] if row.get("moduleId", row.get("module")) == module]
        plan["wbs"].append({"module": module, "tasks": tasks})
    (config / "wbs_config.yaml").write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
    cfg = yaml.safe_load((config / "jira.yml").read_text(encoding="utf-8"))
    cfg["env"] = "mock"
    cfg["managers"] = []
    cfg["search"]["jira"]["projects"] = ["DL"]
    cfg.setdefault("server", {}).update(host="127.0.0.1", port=4457)
    (config / "jira.yml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    bootstrap = run / "bootstrap"
    bootstrap.mkdir()
    (bootstrap / "sitecustomize.py").write_text(
        "from tools.jql_ui_benchmark_fixture import install\ninstall()\n", encoding="utf-8")
    return config, bootstrap


def server_executable(repo):
    if os.name != "nt":
        return sys.executable
    # run.py's Python launcher exits after spawning the named image; own that image directly.
    executable = Path(sys.executable).resolve().with_name("LakeTaskManagerDev.exe")
    if not executable.is_file():
        raise RuntimeError("Existing named development launcher is required; no launcher installation is performed")
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


def child_environment(repo, scratch, config, bootstrap, latency, source=None):
    env = dict(os.environ if source is None else source)
    paths = [str(bootstrap), str(repo)] + [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    env.update(JIRA_ENV="mock", APP_HOST="127.0.0.1", APP_PORT="4457", CONFIG_DIR=str(config),
               LAKE_CACHE_DIR=str(scratch / "runtime"), CACHE_DB_PATH=str(scratch / "runtime.sqlite3"),
               LAKE_MOCK_LATENCY_MS=str(latency), LAKE_NO_WINDOW="1", PYTHONUTF8="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join(dict.fromkeys(paths)))
    return env


def launch(repo, scratch, output, latency, bundle):
    try:
        health = request("/api/health")
    except OSError:
        health = None
    if health is not None:
        raise RuntimeError(f"Port 4457 is already occupied; inspect and stop verified LTM first: {health}")
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 4457))
    config, bootstrap = prepare(repo, scratch, bundle)
    env = child_environment(repo, scratch, config, bootstrap, latency)
    env["JQL_UI_WIRE_DIR"] = str(output / "wire")
    log = (output / "server.log").open("x", encoding="utf-8")
    process = None
    try:
        process = subprocess.Popen([server_executable(repo), str(repo / "run.py"), "--reload"], cwd=repo,
            env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Server exited during startup: {output / 'server.log'}")
            try:
                health = request("/api/health")
                metrics = request("/__jql_benchmark")
                return process, log, health, metrics
            except (OSError, ValueError):
                time.sleep(.25)
        raise RuntimeError(f"Server did not start: {output / 'server.log'}")
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


def wait_state(page, timeout=None):
    timeout = timeout or UI_TIMEOUT
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
    row = {"name": name, "previousKey": prior.get("key"), "previousToken": prior.get("requestToken")}
    try:
        action()
        click_ms = (time.perf_counter()-start)*1000
        state, first, _elapsed = wait_state(page)
        row.update(actionMs=round(click_ms, 2), firstContentMs=round(click_ms+(first or 0), 2), state=state)
        assert state.get('ready') and state.get('cards',0)>0, state
        assert page.locator('vite-error-overlay,webpack-dev-server-client-overlay').count()==0
        row["ok"] = True
    except BaseException as error:
        row.update(ok=False, error=repr(error), state=page.evaluate(STATE_JS))
        raise
    finally:
        row["completeMs"] = round((time.perf_counter() - start) * 1000, 2)
        row["settlement"] = request("/__jql_benchmark/settle", method="POST")
        after = request("/__jql_benchmark")
        records = after["requests"][len(before["requests"]):]
        row.update(requests=records, searchRequests=sum(r["path"] == "/rest/api/2/search" for r in records),
                   allRequests=len(records), longTasks=page.evaluate("window.__benchLong || []"))
        record_row(page, row, result)
    print(json.dumps({k:row[k] for k in ("name","firstContentMs","completeMs","searchRequests","allRequests")}), flush=True)


def record_row(page, row, rows):
    if EVIDENCE_DIR:
        screenshot = EVIDENCE_DIR / f"{len(rows):03d}-{row['name'].replace(':', '-')}.png"
        page.screenshot(path=str(screenshot), full_page=False)
        row["screenshot"] = str(screenshot)
    rows.append(row)
    if RAW_STREAM:
        RAW_STREAM.write(json.dumps(row, ensure_ascii=False) + "\n")
        RAW_STREAM.flush()


def journey(page, rows, repeats, smoke=False):
    measure(page,"task-journey-entry",lambda: page.goto(BASE+"/#/mytasks"),rows)
    for cycle in range(repeats):
        def band(status, value, legacy_label):
            select = page.locator(f'[data-status-control="{status}"] select')
            if select.count():
                select.select_option(value)
            else:
                page.locator(f'.mt-colh.c-{status}').get_by_role('button', name=legacy_label, exact=True).click()
        def person(index, user):
            page.locator('.mt-qf-fe .fe-v').nth(index).click()
            page.get_by_placeholder('이름 또는 사번').fill(user)
            page.locator('.fe-pop .fe-i').filter(has=page.locator('em',has_text=user)).click()

        def expand_wbs():
            page.get_by_role('button',name='전체 Epic 펼치기',exact=True).click()
            for key in ('DL-20000','DL-20002'):
                page.locator(f'.lrow.epic[data-key="{key}"]').first.locator('.tgl').last.click()

        def expand_subtasks():
            page.locator('.lrow.task.clk .tgl').last.click()
            page.wait_for_function("document.querySelectorAll('.g-labels .lrow.sub').length > 0")

        actions = [
            ("done-month", lambda: band('done', '1m', '1달')),
            ("allocated-2w", lambda: band('todo', '2w', '2주 내 갱신')),
            ("progress-month", lambda: band('inprogress', '1m', '1달 내 갱신')),
            ("assignee-scope", lambda: page.get_by_role('button',name='담당자',exact=True).click()),
            ("assignee-other", lambda: person(0, ETL[1])),
            ("reporter-scope", lambda: page.get_by_role('button',name='보고자',exact=True).click()),
            ("reporter-other", lambda: person(1, ETL[2])),
            ("module-catalog", lambda: page.locator('.mt-qf-sel').select_option('Catalog')),
            ("module-etl", lambda: page.locator('.mt-qf-sel').select_option('ETL')),
            ("allocated-all", lambda: band('todo', 'all', '모두')),
            ("progress-all", lambda: band('inprogress', 'all', '모두')),
            ("done-week", lambda: band('done', '1w', '1주')),
            ("workload-after-task", lambda: page.locator('a[href="#/workload"]').click()),
            ("workload-module-catalog", lambda: page.locator('.wl-modsel').select_option('Catalog')),
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
            if smoke and name not in {"done-month", "assignee-scope", "assignee-other", "module-catalog", "module-etl",
                                      "workload-after-task", "wbs-after-workload", "wbs-expand", "task-return"}:
                continue
            measure(page,f"{cycle+1}:{name}",action,rows)


def wait_idle():
    state = request('/__jql_benchmark/settle', method='POST')
    if not state['complete']:
        raise TimeoutError(f'Upstream background work did not drain: {state}')


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
    page.on('console', lambda message: errors.append({"type": "console", "text": message.text})
            if message.type == 'error' else CONSOLE_WARNINGS.append(message.text) if message.type == 'warning' else None)
    page.on('requestfailed', lambda item: NETWORK_FAILURES.append({"url": item.url, "failure": item.failure}))
    page.on('response', lambda response: NETWORK_FAILURES.append({"url": response.url, "status": response.status})
            if response.status >= 400 else None)
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


def wire_tail(directory):
    values = {}
    for file in sorted(directory.glob("wire-*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            values[(row["pid"], row["sequence"])] = row
    rows = sorted(values.values(), key=lambda row: (row["start"], row["pid"], row["sequence"]))
    return {"requests": rows, "dispatchAttempts": len(rows),
            "searchPageAttempts": sum(row["path"] == "/rest/api/2/search" for row in rows),
            "authProbeAttempts": sum(row["path"] == "/rest/api/2/myself" for row in rows),
            "complete": all(row["done"] for row in rows)}


def observation_complete(result):
    return (all(result.get(name) is True for name in
                ("revisionStable", "port4457Closed", "ownedProcessStopped", "scratchRemoved"))
            and result.get("settlementBeforeStop", {}).get("complete") is True
            and result.get("wireTail", {}).get("complete") is True
            and all(row.get("settlement", {}).get("complete") is True
                    for row in result.get("rows", []) if not row.get("skipped")))


def scenario_coverage_complete(result):
    return not any(row.get("skipped") for row in result.get("rows", []))


def run_ui(repo, output, *, latency=0, cold=1, warm=1, journeys=1, smoke=True, reverse=False,
           keep_server=False, rapid=False, mutation=False, generic=False, timeout=60):
    global EVIDENCE_DIR, RAW_STREAM, UI_TIMEOUT
    if keep_server:
        raise ValueError("--keep-server is incompatible with owned process/scratch cleanup")
    repo = Path(repo).resolve()
    if repo != Path(__file__).resolve().parents[1] or repo != Path.cwd().resolve():
        raise ValueError("Use the current application checkout; no alternate checkout or source copy")
    output = reserve_attempt(repo, output)
    EVIDENCE_DIR = output / "screenshots"
    EVIDENCE_DIR.mkdir()
    (output / "wire").mkdir()
    UI_TIMEOUT = timeout
    NETWORK_FAILURES.clear()
    CONSOLE_WARNINGS.clear()
    rows, errors = [], []
    identity = repository_identity(repo)
    result = {"protocol": "jql-ui-year-v2", "qualificationEligible": False,
        "revision": identity, "dependencies": dependency_identity(os.environ.get("JQL_DEPENDENCY_LABEL", "declared-environment-unspecified")),
        "browser": "Chromium via Python Playwright; Browser plugin not available",
        "clockControl": "cache/JQL/CacheMemo frozen; actual browser, latency, deadlines and performance clocks",
        "viewport": {"width": 1600, "height": 1000}, "rows": rows, "consoleErrors": errors,
        "consoleWarnings": CONSOLE_WARNINGS, "networkFailures": NETWORK_FAILURES,
        "latencyMs": latency, "samples": {"cold": cold, "warm": warm, "journeys": journeys, "smoke": smoke},
        "mcpCalls": 0, "modelUsage": None,
        "coverageGaps": ["Not ten repeated UI samples or a paired arm comparison",
            "Current WBS references two of five public Epic contexts; no invented WBS links",
            "Confluence report/source fidelity not exercised by these navigation flows",
            "Optional edge scenarios are measured only when explicitly requested"]}
    process, log, scratch_path = None, None, None
    RAW_STREAM = (output / "rows.jsonl").open("x", encoding="utf-8")
    try:
        with tempfile.TemporaryDirectory() as scratch:
            scratch_path = Path(scratch)
            result["scratchPath"] = str(scratch_path)
            try:
                from tools.codex_mcp_eval.etl import build_etl_world
                bundle = build_etl_world()
                result["world"] = bundle.manifest
                with (output / "manifest.json").open("x", encoding="utf-8") as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
                process, log, health, metrics = launch(repo, scratch_path, output, latency, bundle)
                result.update(health=health, fixture=metrics, ownedProcess=process.pid)
                assert health.get("rev") and identity["commit"].startswith(health["rev"]), health
                assert metrics["issueCount"] == 10000 and metrics["worldHash"] == bundle.manifest["data_sha256"]
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    installed = list((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
                    if not installed:
                        raise RuntimeError("No installed Chromium; this harness does not install browsers")
                    executable = max(installed, key=lambda path: int(path.parents[1].name.rsplit('-', 1)[1]))
                    browser = pw.chromium.launch(headless=True, executable_path=str(executable))
                    try:
                        page = entry_samples(browser, repo, rows, errors, cold, warm)
                        journey(page, rows, journeys, smoke=smoke)
                        if reverse:
                            page = reverse_flow(browser, repo, rows, errors)
                        if rapid or mutation or generic:
                            from tools import jql_ui_edge_scenarios as edge
                            if rapid:
                                page = edge.rapid_filter(browser, repo, rows, errors)
                            if mutation:
                                edge.mutate(page, rows)
                            if generic:
                                edge.generic_query(page, rows)
                        result.update(title=page.title(), url=page.url, bodyText=page.locator("body").inner_text()[:1500])
                        assert result["url"].startswith(BASE) and result["title"] and result["bodyText"].strip()
                        assert not errors, errors
                        result["uiPass"] = True
                    finally:
                        browser.close()
            finally:
                if process:
                    try:
                        result["settlementBeforeStop"] = request('/__jql_benchmark/settle', method='POST')
                        result["finalMetrics"] = request('/__jql_benchmark')
                        result["finalHealth"] = request('/api/health')
                    except Exception as error:
                        result["finalMetricsError"] = repr(error)
                    stop_owned_server(process)
                    result["ownedProcessStopped"] = process.poll() is not None
                if log:
                    log.close()
    except BaseException as error:
        result.update(uiPass=False, error=repr(error))
    finally:
        RAW_STREAM.close()
        RAW_STREAM = None
        result["scratchRemoved"] = scratch_path is None or not scratch_path.exists()
        result["wireTail"] = wire_tail(output / "wire")
        result["revisionAfter"] = repository_identity(repo)
        result["revisionStable"] = identity == result["revisionAfter"]
        try:
            request('/api/health')
            result["port4457Closed"] = False
        except OSError:
            result["port4457Closed"] = True
        result["observationComplete"] = observation_complete(result)
        result["scenarioCoverageComplete"] = scenario_coverage_complete(result)
        result["coverageGaps"].extend(f"Skipped scenario: {row['name']}: {row.get('reason', '')}"
                                      for row in rows if row.get("skipped"))
        result["uiPass"] = bool(result.get("uiPass") and result["observationComplete"]
                                and result["scenarioCoverageComplete"])
        with (output / "result.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo",type=Path,required=True)
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--latency",type=int,default=0)
    parser.add_argument("--journeys",type=int,default=1)
    parser.add_argument("--cold",type=int,default=1)
    parser.add_argument("--warm",type=int,default=1)
    parser.add_argument("--reverse",action='store_true')
    parser.add_argument("--keep-server",action="store_true")
    parser.add_argument("--full-flow", action="store_true")
    parser.add_argument("--rapid", action="store_true")
    parser.add_argument("--mutation", action="store_true")
    parser.add_argument("--generic", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--dependency-dir", type=Path)
    parser.add_argument("--dependency-label", required=True)
    args = parser.parse_args()
    if args.dependency_dir:
        overlay = str(args.dependency_dir.resolve())
        sys.path.insert(0, overlay)
        os.environ["PYTHONPATH"] = os.pathsep.join([overlay, os.environ.get("PYTHONPATH", "")])
    os.environ["JQL_DEPENDENCY_LABEL"] = args.dependency_label
    result = run_ui(args.repo, args.run, latency=args.latency, cold=args.cold, warm=args.warm,
        journeys=args.journeys, smoke=not args.full_flow, reverse=args.reverse, keep_server=args.keep_server,
        rapid=args.rapid, mutation=args.mutation, generic=args.generic, timeout=args.timeout)
    print(json.dumps({"output": str(args.run), "uiPass": result["uiPass"], "error": result.get("error"),
                      "scratchRemoved": result["scratchRemoved"], "qualificationEligible": False}), flush=True)
    return 0 if result["uiPass"] else 1



if __name__ == '__main__':
    raise SystemExit(main())
