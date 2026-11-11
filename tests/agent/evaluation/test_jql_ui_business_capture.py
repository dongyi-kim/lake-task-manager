"""Post-settled observer contracts; tiny Vue/DOM literals, no browser or world."""
from copy import deepcopy
import hashlib
import json
import shutil
import subprocess

import pytest

from tools import jql_ui_benchmark as runner


def javascript_capture(kind, view, *, mutate=False, dom_mode="production", with_state=False):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the actual observer JavaScript")
    payload = {"source": runner.BUSINESS_JS, "kind": kind, "view": view, "mutate": mutate,
               "domMode": dom_mode, "stateSource": runner.STATE_JS, "withState": with_state}
    script = r"""
const x=JSON.parse(require('fs').readFileSync(0,'utf8'));
const v=x.view;
const component={type:{name:{task:'MyTasksView',workload:'WorkloadView',wbs:'WbsView'}[x.kind]},proxy:v};
// Production Vue has vnode/component links, but no element.__vueParentComponent.
const cards=(v.domCardFixtures||v.allCards||[]).map(card=>({dataset:{key:card.key},isConnected:true}));
const cardVnodes=cards.map((el,i)=>({component:{type:{name:'TaskCard'},
  props:{card:(v.domCardFixtures||v.allCards)[i]},subTree:{el}}}));
const dueVnodes=cards.map((el,i)=>({component:{type:{name:'DueText'},
  props:{card:(v.domCardFixtures||v.allCards)[i]},subTree:{el:{isConnected:true,
    closest:selector=>selector==='.mt-card[data-key]'?el:null}}}}));
if(x.domMode==='taskcard-due')cardVnodes.forEach((n,i)=>n.component.subTree.children=[dueVnodes[i]]);
if(x.domMode.startsWith('inline'))cardVnodes.splice(0,cardVnodes.length,
  ...cards.map((el,i)=>({el,children:[dueVnodes[i]]})));
if(x.domMode==='inline-wrong-key')cards[0].dataset.key='DL-wrong';
if(x.domMode==='inline-detached-root')cards[0].isConnected=false;
if(x.domMode==='inline-detached-due')dueVnodes[0].component.subTree.el.isConnected=false;
if(x.domMode==='inline-cross-card')dueVnodes[0].component.props.card=(v.domCardFixtures||v.allCards)[1];
if(x.domMode==='inline-conflict')cardVnodes[0].children.push({component:{type:{name:'TaskCard'},
  props:{card:{...(v.domCardFixtures||v.allCards)[0],status:'stale'}},subTree:{el:cards[0]}}});
component.subTree={children:[{children:cardVnodes}]};
if(x.domMode==='wrong-key')cards[0].dataset.key='DL-wrong';
if(x.domMode==='detached')cards[0].isConnected=false;
if(x.domMode==='other-element')cardVnodes[0].component.subTree.el={...cards[0]};
if(x.domMode==='duplicate')cardVnodes.push({component:{type:{name:'TaskCard'},
  props:{card:{...(v.domCardFixtures||v.allCards)[0],status:'stale'}},subTree:{el:cards[0]}}});
global.location={hash:{task:'#/mytasks',workload:'#/workload',wbs:'#/wbs'}[x.kind]};
global.document={querySelector:()=>({_vnode:{component}}),querySelectorAll:s=>
  s.includes('mt-card')?cards:(v.curMod?.people||[]).map(p=>({getAttribute:()=>p.id}))};
if(x.mutate){const a=v.allCards;let calls=0;Object.defineProperty(v,'allCards',
  {get(){if(++calls===2)v._activeRequestToken='replacement';return a;}});}
const business=eval('('+x.source+')')();
console.log(JSON.stringify(x.withState?{business,state:eval('('+x.stateSource+')')()}:business));
"""
    result = subprocess.run([node, "-e", script], input=json.dumps(payload),
                            text=True, encoding="utf-8", capture_output=True, timeout=10, check=True)
    return json.loads(result.stdout)


def task_view():
    child = {"key": "DL-2", "parentKey": "DL-1", "status": "In Progress", "statusCategory": "inprogress",
             "type": "Sub-Task", "isSub": True, "assigneeId": "u1", "reporterId": "u2", "due": None,
             "dueDays": None, "updated": "2026-11-03T15:00:00Z", "resolved": None,
             "components": ["Catalog"], "epic": None}
    context = {**child, "key": "DL-3", "assigneeId": "u2"}
    group = {"key": "DL-1", "status": "Open", "statusCategory": "todo", "type": "Task",
             "assigneeId": "u1", "reporterId": "u1", "mine": True, "queryMatched": True,
             "due": "2026-11-10", "dueDays": 0, "updated": child["updated"], "hasSubs": True, "childrenLoaded": True,
             "childrenPending": False, "atoms": [child], "others": [context]}
    return {"apiScope": "assignee:u1", "openFilter": "all", "progFilter": "all", "doneFilter": "1w",
            "_activeCacheKey": "assignee:u1|all|all|1w", "_activeRequestToken": "req1",
            "model": {"snapshotSequence": 8, "streamComplete": True, "today": "2026-11-10",
                      "counts": {"total": 1, "done": 0, "overdue": 0, "today": 0, "week": 0, "noDue": 1},
                      "groups": [group]}, "groups": [group], "streamAxes": {"done": {"state": "done"}},
            "allCards": [{**child, "parent": group, "mine": True, "due": "2026-11-10", "dueDays": 0,
                          "dueInherited": True, "epicKey": None, "isGroupSelf": False},
                         {**context, "parent": group, "mine": False, "due": "2026-11-10", "dueDays": 0,
                          "dueInherited": True, "epicKey": None, "isGroupSelf": False}],
            "summaryCounts": {"total": 1, "done": 0, "overdue": 0, "today": 1, "week": 0},
            "priorityFilter": "", "dueFilter": "", "updateFilter": "all", "epicHidden": {},
            "excluded": {}, "projPref": {}, "searchProjects": ["DL"], "groupBy": "sub", "subView": "mine",
            "me": {"id": "u1"}, "err": ""}


def test_actual_projection_separates_raw_atoms_context_headers_and_inherited_dates():
    snapshot = javascript_capture("task", task_view())
    assert snapshot["stable"] is True
    assert snapshot["kind"] == "task"
    assert [r["key"] for r in snapshot["owned"]] == ["DL-2"]
    assert [r["key"] for r in snapshot["related"]] == ["DL-3"]
    assert [r["key"] for r in snapshot["groups"]] == ["DL-1"]
    assert snapshot["owned"][0]["due"] is None
    assert snapshot["cards"][0]["due"] == "2026-11-10"
    assert snapshot["cards"][0]["dueInherited"] is True
    assert snapshot["counts"]["today"] == 0 and snapshot["summaryCounts"]["today"] == 1
    assert snapshot["domCards"][0]["status"] == "In Progress"
    assert snapshot["domCards"][0]["groupKey"] == "DL-1"
    assert snapshot["filters"]["searchProjects"] == ["DL"]


def test_actual_projection_detects_generation_change_during_computed_read():
    snapshot = javascript_capture("task", task_view(), mutate=True)
    assert snapshot["stable"] is False
    assert snapshot["identityBefore"]["requestToken"] == "req1"
    assert snapshot["identityAfter"]["requestToken"] == "replacement"


@pytest.mark.parametrize("dom_mode", ["wrong-key", "detached", "other-element", "duplicate"])
def test_dom_capture_rejects_unassociated_stale_or_ambiguous_component_roots(dom_mode):
    snapshot = javascript_capture("task", task_view(), dom_mode=dom_mode)
    assert snapshot["domCards"][0]["unverified"] is True
    assert "status" not in snapshot["domCards"][0]
    assert snapshot["domCards"][1]["status"] == "In Progress"


@pytest.mark.parametrize("dom_mode", ["inline", "taskcard-due"])
def test_dom_capture_reads_inline_parent_and_child_actual_due_props(dom_mode):
    view = task_view()
    view["domCardFixtures"] = [view["model"]["groups"][0], *view["allCards"]]
    snapshot = javascript_capture("task", view, dom_mode=dom_mode)
    assert [row["key"] for row in snapshot["domCards"]] == ["DL-1", "DL-2", "DL-3"]
    assert snapshot["domCards"][0]["status"] == "Open"
    assert snapshot["domCards"][1]["dueInherited"] is True
    assert snapshot["domCards"][2]["mine"] is False


@pytest.mark.parametrize("dom_mode", ["inline-wrong-key", "inline-detached-root",
                                     "inline-detached-due", "inline-cross-card", "inline-conflict"])
def test_inline_due_association_does_not_launder_stale_or_conflicting_props(dom_mode):
    snapshot = javascript_capture("task", task_view(), dom_mode=dom_mode)
    assert snapshot["domCards"][0]["unverified"] is True
    assert "status" not in snapshot["domCards"][0]
    assert snapshot["domCards"][1]["status"] == "In Progress"


def generic_capture(**sync_fields):
    view = task_view()
    view["apiScope"] = 'jql:project = DL ORDER BY key ASC'
    view["_activeCacheKey"] = view["apiScope"] + "|all|all|1w"
    del view["model"]["snapshotSequence"]
    view["model"].update(sync_fields)
    return javascript_capture("task", view, with_state=True)


@pytest.mark.parametrize("sync_pending,epics_pending", [(None, None), (None, False), (0, False)])
def test_completed_generic_without_optional_sync_sequence_uses_exact_request_identity(sync_pending, epics_pending):
    projection = generic_capture(syncPending=sync_pending, epicsPending=epics_pending)
    for identity in (projection["state"], projection["business"]["identityBefore"]):
        assert {field: identity[field] for field in ("sequence", "syncId", "syncPending", "epicsPending")} == {
            "sequence": None, "syncId": None, "syncPending": sync_pending, "epicsPending": epics_pending}
    page = type("Page", (), {"evaluate": lambda self, _: deepcopy(projection["business"])})()
    assert runner.capture_business(page, projection["state"])["verified"] is True


@pytest.mark.parametrize("bad", ["non-generic", "empty-query", "sequence", "token", "empty-token", "keys",
                               "active-key", "scope", "sync-id", "sync-pending", "epics-pending", "missing-sync-field",
                               "missing-keys"])
def test_absent_generic_sequence_cannot_waive_scope_generation_or_sync_fences(bad):
    projection = generic_capture()
    state, snapshot = projection["state"], projection["business"]
    identity = snapshot["identityBefore"]
    if bad in {"non-generic", "empty-query"}:
        snapshot["scope"] = "assignee:u1" if bad == "non-generic" else "jql:  "
        for item in (state, identity):
            item["key"] = item["activeKey"] = snapshot["scope"] + "|all|all|1w"
    elif bad == "sequence": state["sequence"] = 1
    elif bad == "token": state["requestToken"] = "different"
    elif bad == "empty-token": state["requestToken"] = identity["requestToken"] = ""
    elif bad == "keys": state["keys"] = list(reversed(state["keys"]))
    elif bad == "active-key": state["activeKey"] = identity["activeKey"] = "old"
    elif bad == "scope": snapshot["scope"] = "jql:different query"
    elif bad == "sync-id": state["syncId"] = identity["syncId"] = "pending-session"
    elif bad == "sync-pending": state["syncPending"] = identity["syncPending"] = 1
    elif bad == "epics-pending": state["epicsPending"] = identity["epicsPending"] = True
    elif bad == "missing-keys":
        state.pop("keys", None)
        identity.pop("keys", None)
    else:
        state.pop("syncId", None)
        identity.pop("syncId", None)
    page = type("Page", (), {"evaluate": lambda self, _: snapshot})()
    assert runner.capture_business(page, state)["verified"] is False


def test_workload_captures_only_visible_roster_not_retained_other_module_person():
    bucket = {"count": {"task": 2, "subtask": 1, "voc": 1}, "epics": {}}
    v = {"mod": "Catalog", "curMod": {"module": "Catalog", "peopleCount": 1, "people": [{"id": "u2"}]},
         "assignedWindow": "all", "doneDays": 7, "metric": "count", "excludeVoc": False,
         "peopleLoadEpoch": 4, "statsComplete": True, "err": "", "totals": {"p": 1, "op": 4, "ip": 4, "dn": 4},
         "pstat": {"u1": {"id": "u1", "error": "old failed module"},
                   "u2": {"id": "u2", "open": bucket, "inProgress": bucket, "done7d": bucket,
                          "partial": False, "loading": False, "retrying": False}}}
    snapshot = javascript_capture("workload", v)
    assert snapshot["stable"] is True
    assert snapshot["roster"] == ["u2"] == snapshot["domRoster"]
    assert [p["id"] for p in snapshot["people"]] == ["u2"]
    assert snapshot["retainedOtherPersonIds"] == ["u1"]
    assert snapshot["people"][0]["open"]["count"] == {"task": 2, "subtask": 1, "voc": 1}


def test_python_capture_rejects_new_snapshot_even_if_its_own_projection_is_stable():
    view = task_view()
    snapshot = javascript_capture("task", view)
    settled = {"key": "assignee:u1|all|all|1w", "activeKey": "assignee:u1|all|all|1w",
               "requestToken": "req1", "sequence": 7, "keys": ["DL-2", "DL-3"], "complete": True}
    page = type("Page", (), {"evaluate": lambda self, _source: snapshot})()
    observed = runner.capture_business(page, settled)
    assert observed["verified"] is False
    assert "settled_identity_mismatch" in observed["failures"]
    settled["sequence"] = 8
    assert runner.capture_business(page, settled)["verified"] is True
    settled["keys"] = ["DL-2", "DL-4"]  # Equal totals do not certify identity.
    assert runner.capture_business(page, settled)["verified"] is False


def test_capture_is_once_after_complete_time_and_settlement(monkeypatch):
    clock = [0.0]
    state = {"ready": True, "complete": True, "cards": 2, "key": "task", "activeKey": "task", "requestToken": 1}
    calls = []
    class Page:
        _jql_evidence = {}
        def evaluate(self, source):
            return state if source == runner.STATE_JS else []
        def locator(self, _selector):
            return type("Locator", (), {"count": lambda self: 0})()
    def api(path, method="GET", **_kwargs):
        calls.append(path)
        return {"complete": True} if "settle" in path else {"requests": []}
    def capture(page, settled):
        assert [path.split("?", 1)[0] for path in calls[-2:]] == ["/__jql_benchmark/settle", "/__jql_benchmark"]
        calls.append("capture")
        clock[0] += 5
        return {"verified": True}
    monkeypatch.setattr(runner, "request", api)
    monkeypatch.setattr(runner.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(runner, "wait_state", lambda page, **_kwargs: (deepcopy(state), 10, 50))
    monkeypatch.setattr(runner, "capture_business", capture)
    monkeypatch.setattr(runner, "record_row", lambda page, row, rows: rows.append(row))
    rows = []
    runner.measure(Page(), "test", lambda: clock.__setitem__(0, 0.05), rows)
    assert rows[0]["completeMs"] == 50
    assert rows[0]["businessObserverMs"] == 5000
    assert rows[0]["business"]["verified"] is True
    assert calls.count("capture") == 1


def test_browser_identity_hash_pin_rejects_replaced_binary(tmp_path, monkeypatch):
    binary = tmp_path / "chrome.exe"
    binary.write_bytes(b"tiny browser fixture")
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda name: "1.51.0")
    identity = runner.browser_identity(binary, "Chromium/134.0")
    assert identity["executableSha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert identity["playwrightVersion"] == "1.51.0"
    assert identity["browserVersion"] == "Chromium/134.0"
    runner.require_browser_identity(identity, identity)
    binary.write_bytes(b"replacement same path")
    with pytest.raises(ValueError, match="Browser identity"):
        runner.require_browser_identity(identity, runner.browser_identity(binary, "Chromium/134.0"))


def test_wbs_projects_real_status_names_and_nested_child_relationships():
    view = {"D": {}, "partial": {}, "epicTree": {"DL-10": [{"key": "DL-1", "type": "Task",
            "statusName": "Open", "statusCat": "todo", "updated": "2026-11-01T00:00:00Z", "component": "ETL",
            "children": [{"key": "DL-2", "type": "Sub-Task", "statusName": "Resolved", "statusCat": "done",
                          "updated": "2026-11-02T00:00:00Z", "component": "ETL"}]}]}}
    snapshot = javascript_capture("wbs", view)
    assert snapshot["trees"]["DL-10"][0]["status"] == "Open"
    child = snapshot["trees"]["DL-10"][0]["children"][0]
    assert child["status"] == "Resolved" and child["parentKey"] == "DL-1"
