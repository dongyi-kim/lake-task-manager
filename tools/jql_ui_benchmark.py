"""Actual-browser JQL harness; disposable OS scratch and exclusive external evidence.

The flow under test is: Task -> many quick-filter changes -> workload -> WBS -> Task,
with rendered progress, result identity, cache reuse, and upstream request accounting.
Browser plugin not available: use the already installed Python Playwright runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import secrets
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
HTTP_RESPONSES = []
HTTP_REQUEST_STARTS = []
HTTP_STREAM = None
PAGE_SEQUENCE = 0
ACTION_SEQUENCE = 0
_OWNERSHIP_TOKEN = None


def _ownership_token(value=None):
    token = _OWNERSHIP_TOKEN if value is None else value
    if not isinstance(token, str) or not token or not token.isascii():
        raise ValueError("An explicit benchmark owner token is required")
    return token


def _redact_owner(text):
    return text.replace(_OWNERSHIP_TOKEN, "[redacted]") if _OWNERSHIP_TOKEN else text


def request(path, method="GET", *, timeout=15):
    req = urllib.request.Request(BASE + path, method=method,
                                 headers={"X-LTM-Benchmark-Owner": _ownership_token()})
    with urllib.request.urlopen(req, timeout=timeout) as response:
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


def child_environment(repo, scratch, config, bootstrap, latency, source=None, *, ownership_token=None):
    env = dict(os.environ if source is None else source)
    paths = [str(bootstrap), str(repo)] + [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    env.update(JIRA_ENV="mock", APP_HOST="127.0.0.1", APP_PORT="4457", CONFIG_DIR=str(config),
               LAKE_CACHE_DIR=str(scratch / "runtime"), CACHE_DB_PATH=str(scratch / "runtime.sqlite3"),
               LAKE_MOCK_LATENCY_MS=str(latency), LAKE_NO_WINDOW="1", PYTHONUTF8="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join(dict.fromkeys(paths)),
               JQL_UI_OWNER_TOKEN=_ownership_token(ownership_token))
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
   activeKey:v._activeCacheKey, requestToken:v._activeRequestToken, sequence:v.model?.snapshotSequence??null,
   syncId:v.model?.syncId??null,syncPending:v.model?.syncPending??null,epicsPending:v.model?.epicsPending??null,
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


BUSINESS_JS = """() => {
 const kind=location.hash.includes('workload')?'workload':location.hash.includes('wbs')?'wbs':'task';
 const name={task:'MyTasksView',workload:'WorkloadView',wbs:'WbsView'}[kind];
 function find(n){if(!n)return null;if(Array.isArray(n)){for(const x of n){const v=find(x);if(v)return v;}return null;}
  if(n.component){if(n.component.type.name===name)return n.component.proxy;const v=find(n.component.subTree);if(v)return v;}
  return Array.isArray(n.children)?find(n.children):null;}
 const v=find(document.querySelector('#app')?._vnode);
 if(!v)return {version:'public-ui-business-v1',kind,stable:false,error:'view_not_found'};
 const copy=x=>x===undefined?null:JSON.parse(JSON.stringify(x));
 const list=x=>{if(!Array.isArray(x)||x.length>20000)throw Error('unbounded_or_missing_business_rows');return x;};
 const fields=['key','parentKey','type','isSub','status','statusCategory','assigneeId','reporterId',
  'due','dueDays','updated','resolved','components','epic','epicKey','mine','dueInherited','isGroupSelf','hasSubs','queryMatched'];
 function fact(x,group){const r=Object.fromEntries(fields.map(k=>[k,copy(x[k])]));
  r.groupKey=group?.key||x.parent?.key||null;return r;}
 function identity(){if(kind==='task')return {key:v.apiScope+'|'+v.openFilter+'|'+v.progFilter+'|'+v.doneFilter,
  activeKey:copy(v._activeCacheKey),requestToken:copy(v._activeRequestToken),sequence:copy(v.model?.snapshotSequence),
  syncId:copy(v.model?.syncId),syncPending:copy(v.model?.syncPending),epicsPending:copy(v.model?.epicsPending),
  filters:[v.groupBy,v.subView,v.priorityFilter,v.dueFilter,v.updateFilter,copy(v.projPref),copy(v.epicHidden),copy(v.excluded)],
  keys:list(v.allCards||[]).map(x=>x.key),groups:list(v.model?.groups||[]).map(g=>[g.key,
   list(g.atoms||[]).map(x=>x.key),list(g.others||[]).map(x=>x.key)])};
 if(kind==='workload')return {key:v.mod+'|'+v.assignedWindow+'|'+v.doneDays,requestToken:copy(v.peopleLoadEpoch),
  module:v.curMod?.module,roster:list(v.curMod?.people||[]).map(p=>p.id),metric:v.metric,excludeVoc:v.excludeVoc};
 return {key:'wbs',epoch:copy(v._treeEpoch),requests:copy(v._treeReq),keys:Object.fromEntries(Object.entries(v.epicTree||{}).filter(([k,a])=>Array.isArray(a)).map(([k,a])=>[k,a.map(x=>x.key)]))};}
 const before=identity();
 const out={version:'public-ui-business-v1',kind,identityBefore:before,error:copy(v.err)};
 if(kind==='task'){
  const groups=list(v.model?.groups||[]);
  out.complete=!!v.model?.streamComplete&&!v._streamAbort&&Object.values(v.streamAxes||{}).every(x=>x.state==='done');
  out.scope=v.apiScope;out.actor=v.me?.id||v.me?.name||null;out.today=copy(v.model?.today);
  out.bands={open:v.openFilter,progress:v.progFilter,done:v.doneFilter};
  out.filters=Object.fromEntries(['groupBy','subView','priorityFilter','dueFilter','updateFilter','projPref',
    'epicHidden','excluded','searchProjects','bandClosed','axisPage','gClosed','subOpen'].map(k=>[k,copy(v[k])]));
  out.counts=copy(v.model?.counts);out.summaryCounts=copy(v.summaryCounts);out.loadErrors=copy(v.model?.loadErrors);
  out.groups=groups.map(g=>({...fact(g),hasSubs:!!g.hasSubs,queryMatched:copy(g.queryMatched),
    childrenPending:!!g.childrenPending,childrenLoaded:copy(g.childrenLoaded),childrenUnavailable:!!g.childrenUnavailable,
    childrenMissing:copy(g.childrenMissing),childrenLoadedCount:copy(g.childrenLoadedCount),kidsTotal:copy(g.kidsTotal)}));
  out.owned=groups.flatMap(g=>list(g.atoms||[]).map(x=>fact(x,g)));
  out.related=groups.flatMap(g=>list(g.others||[]).map(x=>fact(x,g)));
  out.cards=list(v.allCards||[]).map(x=>fact(x));
  // Production Vue omits the DOM __vueParentComponent backlink. Native inline
  // cards bind their actual row to DueText; other cards bind it to TaskCard.
  const cardRoots=new Map(),visited=new Set();
  function associate(c,el){if(!el)return;const rows=cardRoots.get(el)||[],card=c.props?.card;
   rows.push({kind:c.type.name,card:!c.isUnmounted&&c.subTree?.el?.isConnected===true&&
    el.isConnected===true&&card?.key===el.dataset?.key?card:null});cardRoots.set(el,rows);}
  function renderedCards(n){if(!n||typeof n!=='object'||visited.has(n))return;visited.add(n);
   if(Array.isArray(n)){n.forEach(renderedCards);return;}
   const c=n.component;
   if(c){if(c.type?.name==='TaskCard')associate(c,c.subTree?.el);
    if(c.type?.name==='DueText')associate(c,c.subTree?.el?.closest?.('.mt-card[data-key]'));
    renderedCards(c.subTree);}
   if(Array.isArray(n.children))n.children.forEach(renderedCards);}
  renderedCards(document.querySelector('#app')?._vnode);
  out.domCards=Array.from(document.querySelectorAll('.mytasks .mt-card[data-key]')).map(el=>{
   const rows=cardRoots.get(el);
   const unique=rows&&rows.every(r=>r.card)&&new Set(rows.map(r=>r.card));
   return unique?.size===1&&['TaskCard','DueText'].every(k=>rows.filter(r=>r.kind===k).length<=1)?
    {...fact(rows[0].card),domKey:el.dataset.key}:
    {domKey:el.dataset.key,unverified:true};});
 }else if(kind==='workload'){
  out.complete=!!v.statsComplete;out.module=v.curMod?.module;out.requestedModule=v.mod;
  out.roster=list(v.curMod?.people||[]).map(p=>p.id);out.peopleCount=copy(v.curMod?.peopleCount);
  out.assignedWindow=v.assignedWindow;out.doneDays=v.doneDays;out.metric=v.metric;out.excludeVoc=v.excludeVoc;
  out.totals=copy(v.totals);out.retainedOtherPersonIds=Object.keys(v.pstat||{}).filter(k=>!out.roster.includes(k));
  out.people=out.roster.map(id=>{const p=v.pstat?.[id];if(!p)return {id,missing:true};
   const r=Object.fromEntries(['id','error','partial','loading','retrying','missingParents'].map(k=>[k,copy(p[k])]));
   for(const k of ['open','inProgress','done7d']){const b=p[k];r[k]=b?{count:copy(b.count),
    partial:copy(b.partial),retryable:copy(b.retryable),missingParents:copy(b.missingParents)}:null;}return r;});
  out.domRoster=Array.from(document.querySelectorAll('.wl-people .prow .pname[title]')).map(e=>e.getAttribute('title'));
 }else{
  out.complete=!!v.D&&Object.values(v.epicTree||{}).every(Array.isArray);out.partial=copy(v.partial);
  function treeFact(x,parent=null){return {key:x.key,type:copy(x.type),status:copy(x.statusName),
   statusCategory:copy(x.statusCat),updated:copy(x.updated),component:copy(x.component),parentKey:parent,
   children:parent===null?list(x.children||[]).map(c=>treeFact(c,x.key)):[]};}
  out.trees=Object.fromEntries(Object.entries(v.epicTree||{}).filter(([k,a])=>Array.isArray(a)).map(([k,a])=>[k,list(a).map(x=>treeFact(x))]));
 }
 out.identityAfter=identity();out.stable=JSON.stringify(before)===JSON.stringify(out.identityAfter);
 return out;
}"""


def capture_business(page, settled_state):
    """One passive projection, never a readiness poll or a refresh/retry."""
    try:
        observed = page.evaluate(BUSINESS_JS)
        failures = []
        identity = observed.get("identityBefore") or {}
        if not observed.get("stable"):
            failures.append("generation_changed_during_capture")
        if not settled_state.get("complete") or not observed.get("complete") or observed.get("error"):
            failures.append("incomplete_or_failed_view")
        required = ["key", "requestToken"] if observed.get("kind") != "wbs" else ["key"]
        if observed.get("kind") == "task":
            required += ["activeKey"]
            scope, bands = observed.get("scope"), observed.get("bands") or {}
            sync_fields = ("syncId", "syncPending", "epicsPending")
            # snapshotSequence is a model child/epic-sync ordinal, distinct from
            # the stream event sequence already fenced by the production view.
            generic_without_sync = (
                isinstance(scope, str) and scope.startswith("jql:") and bool(scope[4:].strip())
                and identity.get("key") == scope + "|" + "|".join(str(bands.get(k)) for k in ("open", "progress", "done"))
                and all("sequence" in row and row["sequence"] is None
                        and all(k in row for k in sync_fields) and row["syncId"] is None
                        and (row["syncPending"] is None or type(row["syncPending"]) is int and row["syncPending"] == 0)
                        and (row["epicsPending"] is None or row["epicsPending"] is False)
                        and isinstance(row.get("requestToken"), str) and bool(row["requestToken"].strip())
                        for row in (settled_state, identity))
                and settled_state.get("complete") is True and observed.get("complete") is True
                and isinstance(identity.get("keys"), list) and isinstance(settled_state.get("keys"), list)
                and identity.get("keys") == settled_state.get("keys"))
            if not generic_without_sync:
                required.append("sequence")
        if any(settled_state.get(k) is None or identity.get(k) != settled_state.get(k) for k in required):
            failures.append("settled_identity_mismatch")
        if observed.get("kind") == "task":
            if identity.get("activeKey") != identity.get("key") or identity.get("keys") != settled_state.get("keys"):
                failures.append("settled_card_keys_mismatch")
            if observed.get("loadErrors") or any(g.get("childrenPending") or g.get("childrenUnavailable")
                                                     or g.get("childrenMissing") for g in observed.get("groups", [])):
                failures.append("incomplete_task_children")
        if observed.get("kind") == "workload":
            if any(p.get("missing") or p.get("error") or p.get("loading") or p.get("retrying") or p.get("partial")
                   or p.get("missingParents") or any(not p.get(k) or p[k].get("partial") or p[k].get("missingParents")
                                                     for k in ("open", "inProgress", "done7d"))
                   for p in observed.get("people", [])):
                failures.append("incomplete_workload_people")
        if observed.get("kind") == "wbs" and (observed.get("partial") or identity.get("keys") != settled_state.get("treeKeys")):
            failures.append("partial_or_replaced_wbs_tree")
        observed.update(verified=not failures, failures=failures)
        return observed
    except Exception as error:
        return {"version": "public-ui-business-v1", "verified": False,
                "failures": ["business_observer_error"], "error": repr(error)}


def browser_identity(executable, browser_version):
    path = Path(executable).resolve(strict=True)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"playwrightVersion": importlib.metadata.version("playwright"), "executablePath": str(path),
            "executableSha256": digest.hexdigest(), "browserVersion": browser_version}


def require_browser_identity(expected, actual):
    if expected != actual:
        raise ValueError("Browser identity does not match the pinned package/binary/version")


def wait_state(page, timeout=None):
    timeout = UI_TIMEOUT if timeout is None else timeout
    start, first = time.perf_counter(), None
    state = {}
    while time.perf_counter() - start < timeout:
        state = page.evaluate(STATE_JS)
        if time.perf_counter() - start >= timeout:
            break
        if first is None and state.get("cards", 0) and state.get('activeKey',state.get('key')) == state.get('key'):
            first = (time.perf_counter()-start)*1000
        if state.get("complete") and state.get('activeKey',state.get('key')) == state.get('key'):
            return state, first, (time.perf_counter()-start)*1000
        if state.get("err"):
            raise RuntimeError(state)
        page.wait_for_timeout(40)
    raise TimeoutError(state)


def measure(page, name, action, result):
    global ACTION_SEQUENCE
    before = request("/__jql_benchmark")
    prior = page.evaluate(STATE_JS)
    start = time.perf_counter()
    ACTION_SEQUENCE += 1
    context = getattr(page, "_jql_evidence", {})
    context["actionId"] = f"a{ACTION_SEQUENCE}"
    context["deadlineAt"] = start + UI_TIMEOUT
    context.pop("dialogProof", None)
    page.evaluate("window.__benchLong=[]")
    row = {"name": name, "previousKey": prior.get("key"), "previousToken": prior.get("requestToken"),
           "pageId": context.get("pageId"), "actionId": context["actionId"],
           "startedAt": start, "deadlineAt": context["deadlineAt"]}
    try:
        action()
        click_ms = (time.perf_counter()-start)*1000
        state, first, _elapsed = wait_state(page, timeout=max(0, context["deadlineAt"] - time.perf_counter()))
        row.update(actionMs=round(click_ms, 2), firstContentMs=round(click_ms+(first or 0), 2), state=state)
        assert state.get('ready') and state.get('cards',0)>0, state
        assert page.locator('vite-error-overlay,webpack-dev-server-client-overlay').count()==0
        row["ok"] = True
    except BaseException as error:
        row.update(ok=False, error=repr(error), state=page.evaluate(STATE_JS))
        raise
    finally:
        row["completeMs"] = round((time.perf_counter() - start) * 1000, 2)
        remaining = max(0, context["deadlineAt"] - time.perf_counter())
        row["settlementBudgetSeconds"] = remaining
        try:
            row["settlement"] = request(f"/__jql_benchmark/settle?timeoutSeconds={remaining}",
                                        method="POST", timeout=remaining + 5)
        except Exception as error:
            row["settlement"] = {"complete": False, "error": repr(error), "active": None, "admitted": None}
        row["settledMs"] = round((time.perf_counter() - start) * 1000, 2)
        try:
            after = request("/__jql_benchmark")
            records = after["requests"][len(before["requests"]):]
            row.update(requests=records, searchRequests=sum(r["path"] == "/rest/api/2/search" for r in records),
                       allRequests=len(records), requestAccountingComplete=True)
        except Exception as error:
            row.update(requests=None, searchRequests=None, allRequests=None, requestAccountingComplete=False,
                       requestAccountingError=repr(error))
        row["longTasks"] = page.evaluate("window.__benchLong || []")
        observer_started = time.perf_counter()
        row["business"] = capture_business(page, row["state"])
        row["businessObserverMs"] = round((time.perf_counter() - observer_started) * 1000, 2)
        if context.get("dialogProof"):
            row['dialogProof'] = context['dialogProof']
        record_row(page, row, result)
        context["actionId"] = None
    if not row["settlement"].get("complete") or not row["requestAccountingComplete"]:
        raise TimeoutError(f"Upstream background work did not drain within the action budget: {row['settlement']}")
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


def record_http_response(response, trace, failures, stream=None, request_evidence=None):
    """Retain typed local failures and later retry statuses without consuming successful bodies."""
    if not response.url.startswith(BASE + "/api/"):
        return
    row = {"sequence": len(trace), "observedAt": time.perf_counter(), "url": response.url,
           "method": response.request.method, "status": response.status}
    if request_evidence:
        row.update(request_evidence)
    trace.append(row)
    if response.status >= 400:
        row["retryAfter"] = response.headers.get("retry-after")
        try:
            row["body"] = response.json()
        except Exception as error:
            row["bodyReadError"] = repr(error)
        failures.append(row)
    if stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.flush()
    return row


def accounted_dialog_reads(failed, http, failures, request_starts):
    """Account for every same-action read, including starts with no response yet."""
    if not isinstance(request_starts, list):
        return None
    fields = ('pageId', 'actionId', 'method', 'url')
    same = lambda row: all(row.get(key) == failed.get(key) for key in fields)
    starts = [row for row in request_starts if same(row)]
    responses = [row for row in http if same(row)]
    if not starts or len(starts) != len(responses):
        return None
    successes, ids = [], []
    for start in sorted(starts, key=lambda row: row.get('startedAt', 0)):
        rid = start.get('requestId')
        if (not isinstance(rid, str) or not rid
                or sum(row.get('requestId') == rid for row in request_starts) != 1):
            return None
        matched = [row for row in http if row.get('requestId') == rid]
        if len(matched) != 1:
            return None
        response = matched[0]
        if not all(response.get(key) == start.get(key) for key in (*fields, 'startedAt')):
            return None
        times = [response.get(key) for key in ('startedAt', 'observedAt', 'finishedAt')]
        if (not all(type(value) in (int, float) for value in times)
                or not times[0] <= times[1] <= times[2]
                or times[2] > min(times[0] + 35, failed['startedAt'] + 35)
                or type(response.get('sequence')) is not int
                or sum(row.get('sequence') == response['sequence'] for row in http) != 1):
            return None
        ids.append(rid)
        if rid == failed['requestId']:
            if response.get('status') != 503:
                return None
        elif response.get('status') != 200:
            return None
        elif times[2] > failed['observedAt']:
            successes.append(response)
    if (failed['requestId'] not in ids or not any(row['startedAt'] > failed['observedAt'] for row in successes)
            or any(row.get('status') is None and (row.get('requestId') in ids or
                (row.get('url') == failed['url'] and row.get('pageId') == failed['pageId']
                 and row.get('actionId') == failed['actionId'])) for row in failures)):
        return None
    return successes, ids


def classify_recovered_reads(errors, http, failures, rows, world_hash, request_starts=None):
    """Prospective observable recovery, with automatic-retry attribution unobserved."""
    recovered, console_used, http_used = [], set(), set()
    for failed in http:
        body = failed.get("body")
        if (failed.get("status") != 503 or failed.get("method") not in {"GET", "HEAD"}
                or not isinstance(body, dict) or body.get("code") != "query_state_changed"
                or body.get("retryable") is not True or body.get("needLogin") is not False
                or body.get("uncertain") or not all(failed.get(k) for k in ("requestId", "pageId", "actionId"))
                or not isinstance(failed.get("startedAt"), (int, float))):
            continue
        accounted = accounted_dialog_reads(failed, http, failures, request_starts)
        if accounted is None:
            continue
        successes, accounted_ids = accounted
        last_completion = max(failed['finishedAt'], max(item['finishedAt'] for item in successes))
        matches = [row for row in rows if row.get("name") == "mutation-summary"
            and row.get("pageId") == failed["pageId"] and row.get("actionId") == failed["actionId"]]
        if len(matches) != 1:
            continue
        row, proof = matches[0], matches[0].get("dialogProof", {})
        expected, rendered, model = (proof.get(k) for k in ("expectedKeys", "renderedKeys", "modelKeys"))
        target = proof.get("targetKey")
        if (not isinstance(expected, list) or not isinstance(rendered, list) or not isinstance(model, list)
                or len(set(expected)) != len(expected) or sorted(rendered) != sorted(expected)
                or sorted(model) != sorted(expected) or proof.get("renderedCount") != len(expected)
                or proof.get("modelCount") != len(expected) or proof.get("worldHash") != world_hash
                or not world_hash or row.get("ok") is not True or row.get("settlement", {}).get("complete") is not True
                or proof.get("settled") is not True or proof.get("panelState") != "ready"
                or not proof.get("expectedTitle") or proof.get("title") != proof["expectedTitle"]
                or proof.get("modelTitle") != proof["expectedTitle"] or proof.get("dialogKey") != target
                or proof.get("currentKeys") != ([target] if target in expected else [])
                or (target in expected and proof.get("siblingCurrentTitle") != proof["expectedTitle"])
                or type(proof.get("requestId")) is not int or proof["requestId"] != proof.get("currentRequestId")
                or proof["requestId"] <= proof.get("beforeRequestId", proof["requestId"])
                or not proof.get("taskToken") or proof["taskToken"] != row.get("state", {}).get("requestToken")
                or not last_completion <= proof.get("verifiedAt", 0) <= row.get("deadlineAt", 0)
                or failed["url"] != BASE + "/api/ticket/" + str(target) + "/siblings"):
            continue
        native = []
        for index, error in enumerate(errors):
            if (isinstance(error, dict) and error.get("type") == "console"
                    and re.fullmatch(r"Failed to load resource: the server responded with a status of 503 \([^)]*\)", error.get("text", ""))
                    and error.get("location", {}).get("url") == failed["url"]
                    and error.get("pageId") == failed["pageId"] and error.get("actionId") == failed["actionId"]
                    and failed["startedAt"] <= error.get("observedAt", 0) <= proof["verifiedAt"]):
                native.append(index)
        if len(native) > 1:
            continue
        console_used.update(native)
        http_used.add(failed["sequence"])
        recovered.append({"failureSequence": failed["sequence"],
            "successSequences": [item['sequence'] for item in successes],
            "failureRequestId": failed["requestId"], "successRequestIds": [item['requestId'] for item in successes],
            "accountedRequestIds": accounted_ids, "outcome": "observable-dialog-recovery",
            "retryAttribution": "not-observed",
            "consoleIndices": native, "pageId": failed["pageId"], "actionId": failed["actionId"],
            "oracleRow": row["name"], "verifiedAt": proof["verifiedAt"]})
    unexpected_console = [error for index, error in enumerate(errors) if index not in console_used]
    unexpected_http = [failure for failure in http if failure.get('status', 0) >= 400
                       and failure.get('sequence') not in http_used]
    # Preserve the original rapid-filter cancellation contract. This classifier adjudicates
    # HTTP error responses; requestfailed events remain raw and explicitly unclassified.
    unclassified_requests = [failure for failure in failures if failure.get('status') is None]
    return {"complete": isinstance(request_starts, list) and not unexpected_console and not unexpected_http,
        "contract": "observable-dialog-recovery-v1", "retryAttribution": "not-observed",
        "requestLedgerAvailable": isinstance(request_starts, list), "recovered": recovered,
        "unexpectedConsoleErrors": unexpected_console, "unexpectedNetworkFailures": unexpected_http,
        "unclassifiedRequestFailures": unclassified_requests,
        "requestFailureScope": "HTTP responses only; rapid scenario owns cancellation correctness",
        "rawConsoleErrorCount": len(errors), "rawHttpErrorCount": sum(row.get("status", 0) >= 400 for row in http)}


def new_page(browser, repo, errors):
    global PAGE_SEQUENCE
    context = browser.new_context(viewport={'width':1600,'height':1000},
                                  extra_http_headers={"X-LTM-Benchmark-Owner": _ownership_token()})
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
    PAGE_SEQUENCE += 1
    evidence = page._jql_evidence = {"pageId": f"p{PAGE_SEQUENCE}", "actionId": None, "requests": {}, "responses": {}}
    def persist(event):
        if HTTP_STREAM:
            HTTP_STREAM.write(json.dumps(event, ensure_ascii=False) + "\n"); HTTP_STREAM.flush()
    def began(item):
        if not item.url.startswith(BASE + '/api/'):
            return
        row = {"requestId": f"{evidence['pageId']}:r{len(evidence['requests'])}",
               "pageId": evidence['pageId'], "actionId": evidence['actionId'], "startedAt": time.perf_counter(),
               "url": item.url, "method": item.method}
        evidence['requests'][item] = row
        HTTP_REQUEST_STARTS.append(dict(row))
        persist({"event": "request-start", **row, "url": item.url, "method": item.method})
    def responded(response):
        row = record_http_response(response, HTTP_RESPONSES, NETWORK_FAILURES, HTTP_STREAM,
                                   evidence['requests'].get(response.request))
        if row is not None:
            row.update(evidence['requests'].get(response.request, {}))
            evidence['responses'][response.request] = row
    def finished(item):
        metadata = evidence['requests'].get(item)
        if metadata is not None:
            metadata['finishedAt'] = time.perf_counter()
            if item in evidence['responses']:
                evidence['responses'][item]['finishedAt'] = metadata['finishedAt']
            persist({"event": "request-finished", **metadata})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('console', lambda message: errors.append({"type": "console", "text": message.text,
                                                     "location": message.location, "observedAt": time.perf_counter(),
                                                     "pageId": evidence['pageId'], "actionId": evidence['actionId']})
            if message.type == 'error' else CONSOLE_WARNINGS.append(message.text) if message.type == 'warning' else None)
    page.on('request', began)
    page.on('requestfinished', finished)
    page.on('requestfailed', lambda item: NETWORK_FAILURES.append({"url": item.url, "failure": item.failure,
                                                                 **evidence['requests'].get(item, {})}))
    page.on('response', responded)
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
           keep_server=False, rapid=False, mutation=False, generic=False, timeout=60, browser_pin=None):
    global EVIDENCE_DIR, RAW_STREAM, HTTP_STREAM, UI_TIMEOUT, PAGE_SEQUENCE, ACTION_SEQUENCE, _OWNERSHIP_TOKEN
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
    HTTP_RESPONSES.clear()
    HTTP_REQUEST_STARTS.clear()
    PAGE_SEQUENCE = ACTION_SEQUENCE = 0
    rows, errors = [], []
    identity = repository_identity(repo)
    result = {"protocol": "jql-ui-year-v3", "qualificationEligible": False,
        "revision": identity, "dependencies": dependency_identity(os.environ.get("JQL_DEPENDENCY_LABEL", "declared-environment-unspecified")),
        "browser": "Chromium via Python Playwright; Browser plugin not available",
        "clockControl": "cache/JQL/CacheMemo frozen; actual browser, latency, deadlines and performance clocks",
        "viewport": {"width": 1600, "height": 1000}, "rows": rows, "consoleErrors": errors,
        "consoleWarnings": CONSOLE_WARNINGS, "networkFailures": NETWORK_FAILURES, "httpResponses": HTTP_RESPONSES,
        "httpRequestStarts": HTTP_REQUEST_STARTS,
        "latencyMs": latency, "samples": {"cold": cold, "warm": warm, "journeys": journeys, "smoke": smoke},
        "mcpCalls": 0, "modelUsage": None,
        "coverageGaps": ["Not ten repeated UI samples or a paired arm comparison",
            "Current WBS references two of five public Epic contexts; no invented WBS links",
            "Confluence report/source fidelity not exercised by these navigation flows",
            "Optional edge scenarios are measured only when explicitly requested"]}
    process, log, scratch_path = None, None, None
    RAW_STREAM = (output / "rows.jsonl").open("x", encoding="utf-8")
    HTTP_STREAM = (output / "http.jsonl").open("x", encoding="utf-8")
    previous_owner, _OWNERSHIP_TOKEN = _OWNERSHIP_TOKEN, secrets.token_urlsafe(32)
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
                import yaml
                cache_config = yaml.safe_load((scratch_path / "config" / "jira.yml").read_text(encoding="utf-8")).get("cache", {})
                result["clockContract"] = {"logicalNow": metrics["logicalClock"], "timezone": bundle.world.users[bundle.world.ME]["timeZone"],
                    "ttlSeconds": (cache_config.get("policy") or {}).get("freshnessSeconds", cache_config.get("ttl_seconds", 600)),
                    "searchProjects": ["DL"], "actor": bundle.world.ME}
                assert health.get("rev") and identity["commit"].startswith(health["rev"]), health
                assert metrics["issueCount"] == 10000 and metrics["worldHash"] == bundle.manifest["data_sha256"]
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    pinned_browser = json.loads(Path(browser_pin).read_text(encoding="utf-8")) if browser_pin else None
                    installed = ([Path(pinned_browser["executablePath"])] if pinned_browser else
                        list((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe")))
                    if not installed:
                        raise RuntimeError("No installed Chromium; this harness does not install browsers")
                    executable = installed[0] if pinned_browser else max(installed, key=lambda path: int(path.parents[1].name.rsplit('-', 1)[1]))
                    browser = pw.chromium.launch(headless=True, executable_path=str(executable))
                    try:
                        result["browserIdentity"] = browser_identity(executable, browser.version)
                        if pinned_browser:
                            require_browser_identity(pinned_browser, result["browserIdentity"])
                        page = entry_samples(browser, repo, rows, errors, cold, warm)
                        journey(page, rows, journeys, smoke=smoke)
                        if reverse:
                            page = reverse_flow(browser, repo, rows, errors)
                        if rapid or mutation or generic:
                            from tools import jql_ui_edge_scenarios as edge
                            if rapid:
                                page = edge.rapid_filter(browser, repo, rows, errors)
                            if mutation:
                                edge.mutate(page, rows, bundle)
                            if generic:
                                edge.generic_query(page, rows)
                        result.update(title=page.title(), url=page.url, bodyText=page.locator("body").inner_text()[:1500])
                        assert result["url"].startswith(BASE) and result["title"] and result["bodyText"].strip()
                        result['recoveryClassification'] = classify_recovered_reads(errors, HTTP_RESPONSES,
                            NETWORK_FAILURES, rows, bundle.manifest['data_sha256'], HTTP_REQUEST_STARTS)
                        assert result['recoveryClassification']['complete'], result['recoveryClassification']
                        result["uiPass"] = True
                    finally:
                        try:
                            result["browserIdentityAfter"] = browser_identity(executable, browser.version)
                            result["browserIdentityStable"] = result.get("browserIdentity") == result["browserIdentityAfter"]
                        finally:
                            browser.close()
            finally:
                if process:
                    try:
                        result["settlementBeforeStop"] = request('/__jql_benchmark/settle', method='POST')
                        result["finalMetrics"] = request('/__jql_benchmark')
                        result["finalHealth"] = request('/api/health')
                    except Exception as error:
                        result["finalMetricsError"] = _redact_owner(repr(error))
                    stop_owned_server(process)
                    result["ownedProcessStopped"] = process.poll() is not None
                if log:
                    log.close()
    except BaseException as error:
        result.update(uiPass=False, error=_redact_owner(repr(error)))
    finally:
        RAW_STREAM.close()
        RAW_STREAM = None
        HTTP_STREAM.close()
        HTTP_STREAM = None
        result["scratchRemoved"] = scratch_path is None or not scratch_path.exists()
        result["wireTail"] = wire_tail(output / "wire")
        result["revisionAfter"] = repository_identity(repo)
        result["revisionStable"] = identity == result["revisionAfter"]
        try:
            request('/api/health')
            result["port4457Closed"] = False
        except OSError:
            result["port4457Closed"] = True
        finally:
            _OWNERSHIP_TOKEN = previous_owner
        result["observationComplete"] = observation_complete(result)
        business_rows = [row for row in rows if "completeMs" in row]
        result["businessCaptureComplete"] = bool(business_rows) and all(
            row.get("business", {}).get("verified") is True for row in business_rows)
        result["scenarioCoverageComplete"] = scenario_coverage_complete(result)
        result['recoveryClassification'] = classify_recovered_reads(errors, HTTP_RESPONSES,
            NETWORK_FAILURES, rows, result.get('world', {}).get('data_sha256'), HTTP_REQUEST_STARTS)
        result["coverageGaps"].extend(f"Skipped scenario: {row['name']}: {row.get('reason', '')}"
                                      for row in rows if row.get("skipped"))
        result["uiPass"] = bool(result.get("uiPass") and result["observationComplete"]
                                and result["scenarioCoverageComplete"] and result['recoveryClassification']['complete']
                                and result["businessCaptureComplete"] and result.get("browserIdentityStable"))
        if result['recoveryClassification']['unclassifiedRequestFailures']:
            result['coverageGaps'].append('requestfailed events retained without HTTP recovery classification; rapid scenario separately checks cancellation tokens and result keys')
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
    parser.add_argument("--browser-pin", type=Path, help="Exact saved browserIdentity JSON; no binary fallback")
    args = parser.parse_args()
    if args.dependency_dir:
        overlay = str(args.dependency_dir.resolve())
        sys.path.insert(0, overlay)
        os.environ["PYTHONPATH"] = os.pathsep.join([overlay, os.environ.get("PYTHONPATH", "")])
    os.environ["JQL_DEPENDENCY_LABEL"] = args.dependency_label
    result = run_ui(args.repo, args.run, latency=args.latency, cold=args.cold, warm=args.warm,
        journeys=args.journeys, smoke=not args.full_flow, reverse=args.reverse, keep_server=args.keep_server,
        rapid=args.rapid, mutation=args.mutation, generic=args.generic, timeout=args.timeout, browser_pin=args.browser_pin)
    print(json.dumps({"output": str(args.run), "uiPass": result["uiPass"], "error": result.get("error"),
                      "scratchRemoved": result["scratchRemoved"], "qualificationEligible": False}), flush=True)
    return 0 if result["uiPass"] else 1



if __name__ == '__main__':
    raise SystemExit(main())
