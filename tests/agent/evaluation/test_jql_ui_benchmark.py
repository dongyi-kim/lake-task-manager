"""UI harness world/config fidelity and low-level request accounting contracts."""
from pathlib import Path
from types import SimpleNamespace
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import subprocess
import sys
import textwrap
from copy import deepcopy

import pytest

from tools import jql_ui_benchmark_fixture as fixture


@pytest.fixture(scope="module")
def bundle():
    from tools.codex_mcp_eval.etl import build_etl_world
    return build_etl_world()


def test_ui_fixture_uses_exact_approved_world_and_adapts_namespace_to_world(bundle):
    world = fixture.fixture_world(bundle)
    assert len(world.issues) == 10000
    assert sum(row["component"] == "ETL" for row in world.issues.values()) == 2245
    assert {key for key, row in world.issues.items() if row["type"] == "Epic" and row["component"] == "ETL"} == {
        "DL-20000", "DL-20001", "DL-20002", "DL-20003", "DL-20004"}
    assert world.issues == bundle.world.issues
    assert callable(world._index) and callable(world._fx)


def test_generated_yaml_round_trips_public_plan_without_touching_source_config(tmp_path, bundle):
    from tools import jql_ui_benchmark as runner
    from app.infra.settings import load_plan, load_people
    repo = Path(__file__).resolve().parents[3]
    before = (repo / "config/wbs_config.yaml").read_bytes()
    config, bootstrap = runner.prepare(repo, tmp_path, bundle)
    plan = load_plan(config / "wbs_config.yaml")
    assert plan["modules"] == bundle.world.modules
    assert plan["wbs"][0]["id"] == "ETL-1"
    assert plan["wbs"][0]["epics"] == [{"key": "DL-20000", "weight": 2}, {"key": "DL-20002", "weight": 1}]
    assert load_people(config / "people.yaml") == bundle.world.people
    assert (bootstrap / "sitecustomize.py").exists()
    assert (repo / "config/wbs_config.yaml").read_bytes() == before


def test_actual_ui_bootstrap_initializes_full_public_provider_status_scheme(tmp_path, bundle):
    """Run sitecustomize's real install hook in a child, without a listener/browser."""
    from tools import jql_ui_benchmark as runner
    repo = Path(__file__).resolve().parents[3]
    config, bootstrap = runner.prepare(repo, tmp_path, bundle)
    wire = tmp_path / "wire"
    wire.mkdir()
    source = dict(runner.os.environ, PYTHONPATH=runner.os.pathsep.join(sys.path))
    env = runner.child_environment(repo, tmp_path, config, bootstrap, 0, source)
    env["JQL_UI_WIRE_DIR"] = str(wire)
    reopened = sorted((key for key, row in bundle.world.issues.items() if row["statusName"] == "Reopened"),
                      key=lambda key: int(key.split("-")[1]))
    assert reopened and all(bundle.world.issues[key]["statusCategory"] == "inprogress" for key in reopened)
    expected = {"hash": bundle.manifest["data_sha256"], "issueCount": len(bundle.world.issues),
                "reopenedCount": len(reopened), "firstKeys": reopened[:10]}
    script = textwrap.dedent('''
        import json, sys
        from fastapi.testclient import TestClient
        # Python loaded bootstrap/sitecustomize.py before this script. Do not call
        # install or the status adapter manually: this exercises the actual entry.
        assert 'sitecustomize' in sys.modules and 'app.main' in sys.modules
        from app import main
        expected = json.load(sys.stdin)
        provider = main._client.provider
        app_client = TestClient(main.app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000))
        try:
            raw = provider._client
            store = raw.app.state.store
            assert len(store.issues) == expected['issueCount'] == 10000
            assert store.config.current_user == 'etl.user00'
            response = app_client.get('/__jql_benchmark')
            assert response.status_code == 200, response.text
            proof = response.json()
            assert proof['worldHash'] == expected['hash']
            assert proof['issueCount'] == 10000 and proof['providerIssueCounts'] == [10000]
            scheme = getattr(raw.app.state, 'etl_status_history_search').contract['publicStatusScheme']
            assert scheme['version'] == 'public-status-category-v1'
            page = raw.get('/rest/api/2/search', params={
                'jql': 'status = Reopened AND statusCategory = indeterminate ORDER BY key ASC',
                'maxResults': 10, 'fields': 'status'})
            assert page.status_code == 200, page.text
            page = page.json()
            assert page['total'] == expected['reopenedCount']
            assert [row['key'] for row in page['issues']] == expected['firstKeys']
            catalog = raw.get('/rest/api/2/status').json()
            status = next(row for row in catalog if row['name'] == 'Reopened')
            assert status['id'] == '4' and status['statusCategory']['key'] == 'indeterminate'
            assert next(row for row in store.config.statuses if row[0] == 'Reopened') == ['Reopened', 'inprogress', '4']
            for row in page['issues']:
                issue = raw.get('/rest/api/2/issue/' + row['key'], params={'fields': 'status'})
                assert issue.status_code == 200, issue.text
                assert issue.json()['fields']['status'] == row['fields']['status'] == status
            negative = raw.get('/rest/api/2/search', params={
                'jql': 'status = Reopened AND statusCategory = new', 'maxResults': 10})
            assert negative.status_code == 200 and negative.json()['total'] == 0
            assert app_client.post('/__jql_benchmark/settle').json()['complete'] is True
            print(json.dumps({'worldHash': proof['worldHash'], 'issues': len(store.issues),
                              'reopenedCount': page['total'], 'statusId': status['id'], 'settled': True}))
        finally:
            app_client.close()
            main._client.close()
            main._cache.close()
    ''')
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script], cwd=repo, env=env,
                            input=json.dumps(expected), text=True, encoding="utf-8", capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout.splitlines()[-1])
    assert observed == {"worldHash": expected["hash"], "issues": 10000,
                        "reopenedCount": len(reopened), "statusId": "4", "settled": True}
    assert list(wire.glob("wire-*.jsonl"))


def test_wire_observer_counts_auth_pages_retry_and_failure_once_each():
    ledger = fixture.WireLedger()
    def dispatch(method, url, **kwargs):
        if url.endswith("fail"):
            raise ConnectionError("injected before response")
        return SimpleNamespace(status_code=200, content=b"{}")
    observed = ledger.observe(dispatch)
    observed("GET", "/rest/api/2/myself")
    observed("GET", "/rest/api/2/search", params={"jql": "key = DL-1", "startAt": 0})
    observed("GET", "/rest/api/2/search", params={"jql": "key = DL-1", "startAt": 0})
    with pytest.raises(ConnectionError):
        observed("GET", "/rest/api/2/fail")
    rows = ledger.snapshot()
    assert len(rows) == 4 and all(row["done"] for row in rows)
    assert rows[-1]["serverReceived"] is None and rows[-1]["error"] == "ConnectionError"
    assert ledger.settle(timeout=0.1, quiet=0)["complete"] is True


def test_child_environment_preserves_dependency_overlay_and_scratch_cache(tmp_path):
    from tools import jql_ui_benchmark as runner
    repo = Path(__file__).resolve().parents[3]
    environment = runner.child_environment(repo, tmp_path, tmp_path / "config", tmp_path / "bootstrap", 0,
                                          {"PYTHONPATH": "pinned-overlay"})
    assert "pinned-overlay" in environment["PYTHONPATH"].split(runner.os.pathsep)
    assert Path(environment["LAKE_CACHE_DIR"]).is_relative_to(tmp_path)
    assert Path(environment["CACHE_DB_PATH"]).is_relative_to(tmp_path)
    assert environment["APP_PORT"] == "4457"


def test_keep_server_is_rejected_before_creating_artifacts(tmp_path):
    from tools import jql_ui_benchmark as runner
    with pytest.raises(ValueError, match="keep-server"):
        runner.run_ui(Path(__file__).resolve().parents[3], tmp_path / "run", keep_server=True)
    assert not (tmp_path / "run").exists()


def test_admitted_delayed_work_prevents_false_settlement_without_counting_rest_twice():
    ledger = fixture.WireLedger()
    entered, release = threading.Event(), threading.Event()
    wire = ledger.observe(lambda *_args: SimpleNamespace(status_code=200, content=b"{}"))
    def delayed():
        entered.set()
        assert release.wait(2)
        return wire("GET", "/rest/api/2/search")
    admitted = ledger.observe_admission(delayed)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(admitted)
        assert entered.wait(2)
        try:
            assert ledger.snapshot() == []
            assert ledger.settle(timeout=.02, quiet=0)["complete"] is False
        finally:
            release.set()
        future.result()
    assert len(ledger.snapshot()) == 1
    assert ledger.settle(timeout=.1, quiet=0)["complete"] is True


@pytest.mark.parametrize("failed", ["revisionStable", "port4457Closed", "ownedProcessStopped", "scratchRemoved",
                                  "rowSettlement", "stopSettlement", "wireComplete"])
def test_observation_gate_rejects_any_unsettled_or_wrong_identity_artifact(failed):
    from tools import jql_ui_benchmark as runner
    result = {"revisionStable": True, "port4457Closed": True, "ownedProcessStopped": True, "scratchRemoved": True,
              "rows": [{"settlement": {"complete": True}}], "settlementBeforeStop": {"complete": True},
              "wireTail": {"complete": True}}
    if failed == "rowSettlement":
        result["rows"][0]["settlement"]["complete"] = False
    elif failed == "stopSettlement":
        result["settlementBeforeStop"]["complete"] = False
    elif failed == "wireComplete":
        result["wireTail"]["complete"] = False
    else:
        result[failed] = False
    assert runner.observation_complete(result) is False


def test_direct_cli_edges_share_configured_runtime_without_starting_server(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    script = textwrap.dedent('''
        import io, json, runpy, sys
        from pathlib import Path
        entry = Path(sys.argv[1]).resolve()
        sys.argv = [str(entry), '--repo', str(entry.parent.parent), '--run', sys.argv[2],
                    '--dependency-label', 'test-only', '--timeout', '120', '--rapid']
        class EntryObserved(Exception): pass
        def observe(frame, event, arg):
            if event != 'call' or frame.f_code.co_name != 'run_ui' or Path(frame.f_code.co_filename) != entry:
                return
            sys.setprofile(None)
            runtime = frame.f_globals
            runtime.update(UI_TIMEOUT=frame.f_locals['timeout'], EVIDENCE_DIR=Path(sys.argv[4]),
                           RAW_STREAM=io.StringIO())
            runtime['EVIDENCE_DIR'].mkdir()
            from tools import jql_ui_edge_scenarios as edge
            edge_runtime = edge.measure.__globals__
            class Page:
                def screenshot(self, *, path, full_page):
                    Path(path).write_bytes(b'captured')
            rows = []
            edge_runtime['record_row'](Page(), {'name': 'entry-edge'}, rows)
            print(json.dumps({'sameRuntime': edge_runtime is runtime,
                              'timeout': edge_runtime['UI_TIMEOUT'],
                              'evidence': str(edge_runtime['EVIDENCE_DIR']),
                              'rawShared': edge_runtime['RAW_STREAM'] is runtime['RAW_STREAM'],
                              'rawWritten': 'entry-edge' in runtime['RAW_STREAM'].getvalue(),
                              'screenshotWritten': (runtime['EVIDENCE_DIR'] / '000-entry-edge.png').is_file(),
                              'networkShared': edge.new_page.__globals__['NETWORK_FAILURES'] is runtime['NETWORK_FAILURES']}))
            raise EntryObserved
        sys.setprofile(observe)
        try:
            runpy.run_path(str(entry), run_name='__main__')
        except EntryObserved:
            pass
    ''')
    completed = subprocess.run([sys.executable, "-B", "-c", script,
                                str(repo / "tools/jql_ui_benchmark.py"), str(tmp_path / "evidence")],
                               cwd=repo, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    state = json.loads(completed.stdout)
    assert state == {"sameRuntime": True, "timeout": 120, "evidence": str(tmp_path / "evidence"),
                     "rawShared": True, "rawWritten": True, "screenshotWritten": True, "networkShared": True}


def run_rapid_without_browser(monkeypatch, partial_at, timeout):
    from tools import jql_ui_benchmark as runner
    from tools import jql_ui_edge_scenarios as edge
    clock = [0.0]
    monkeypatch.setattr(runner, "UI_TIMEOUT", timeout)
    monkeypatch.setattr(runner.time, "perf_counter", lambda: clock[0])
    class Page:
        context = SimpleNamespace(close=lambda: None)
        def evaluate(self, _script):
            return {"cards": int(clock[0] >= partial_at), "complete": False, "requestToken": "partial"}
        def wait_for_timeout(self, milliseconds):
            clock[0] += milliseconds / 1000
        def goto(self, _url): pass
        def locator(self, _selector):
            return SimpleNamespace(select_option=lambda _value: None)
    page = Page()
    def measure(_page, name, action, rows):
        action()
        rows.append({"name": name, "ok": True, "requests": [],
                     "state": {"keys": ["DL-1"], "requestToken": "new", "key": "module:Catalog|all"}})
    monkeypatch.setattr(edge, "new_page", lambda *_: page)
    monkeypatch.setattr(edge, "measure", measure)
    monkeypatch.setattr(edge, "wait_idle", lambda: None)
    monkeypatch.setattr(edge, "wait_state", lambda _page: None)
    monkeypatch.setattr(edge, "request", lambda *_args, **_kwargs: {"requests": []})
    rows = []
    edge.rapid_filter(None, None, rows, [])
    return rows, clock[0]


def test_rapid_partial_wait_uses_configured_deadline_beyond_ten_seconds(monkeypatch):
    rows, elapsed = run_rapid_without_browser(monkeypatch, partial_at=12, timeout=15)
    assert any(row["name"] == "rapid-switch-to-catalog" for row in rows), rows
    assert 12 <= elapsed < 15


def test_rapid_partial_deadline_returns_explicit_coverage_gap(monkeypatch):
    from tools import jql_ui_benchmark as runner
    rows, elapsed = run_rapid_without_browser(monkeypatch, partial_at=100, timeout=.035)
    assert elapsed == pytest.approx(.035)
    assert rows[-1]["name"] == "rapid-partial-frame" and rows[-1]["skipped"] is True
    assert runner.scenario_coverage_complete({"rows": rows}) is False
    assert runner.scenario_coverage_complete({"rows": [{"name": "rapid-switch-to-catalog", "ok": True}]}) is True


def test_http_trace_preserves_typed_failure_and_success_without_reading_success_body():
    from io import StringIO
    from tools import jql_ui_benchmark as runner
    trace, failures = [], []
    stream = StringIO()
    body = {"code": "query_state_changed", "retryable": True, "needLogin": False}
    response = SimpleNamespace(url=runner.BASE + "/api/ticket/DL-1/siblings", status=503,
        request=SimpleNamespace(method="GET"), headers={"retry-after": "1"}, json=lambda: body)
    runner.record_http_response(response, trace, failures, stream)
    response.status = 200
    response.json = lambda: pytest.fail("successful response bodies must not be consumed by observation")
    runner.record_http_response(response, trace, failures, stream)
    assert [row["status"] for row in trace] == [503, 200]
    assert trace[0]["body"] == body and trace[0]["retryAfter"] == "1"
    assert trace[0]["method"] == "GET" and trace[1]["sequence"] == 1
    assert trace[0]["observedAt"] <= trace[1]["observedAt"]
    assert failures == trace[:1] and "body" not in trace[1]
    assert [json.loads(line) for line in stream.getvalue().splitlines()] == trace


def test_http_trace_keeps_unreadable_error_and_does_not_observe_external_resources():
    from tools import jql_ui_benchmark as runner
    trace, failures = [], []
    def unreadable():
        raise ValueError("not JSON")
    response = SimpleNamespace(url=runner.BASE + "/api/test", status=502,
        request=SimpleNamespace(method="GET"), headers={}, json=unreadable)
    runner.record_http_response(response, trace, failures)
    assert trace[0]["bodyReadError"] == "ValueError('not JSON')" and failures == trace
    response.url = "https://example.invalid/api/test"
    runner.record_http_response(response, trace, failures)
    assert len(trace) == 1


def recovered_read_evidence():
    url = "http://127.0.0.1:4457/api/ticket/DL-1/siblings"
    shared = {"url": url, "method": "GET", "pageId": "p1", "actionId": "a1"}
    failed = {**shared, "sequence": 0, "requestId": "p1:r1", "startedAt": 10.,
              "observedAt": 11., "finishedAt": 11.1, "status": 503,
              "body": {"code": "query_state_changed", "retryable": True, "needLogin": False}}
    success = {**shared, "sequence": 1, "requestId": "p1:r2", "startedAt": 12.,
               "observedAt": 13., "finishedAt": 13.1, "status": 200}
    console = {"type": "console", "text": "Failed to load resource: the server responded with a status of 503 (Service Unavailable)",
               "location": {"url": url}, "pageId": "p1", "actionId": "a1", "observedAt": 11.2}
    proof = {"targetKey": "DL-1", "expectedTitle": "latest", "title": "latest", "modelTitle": "latest",
             "expectedKeys": ["DL-1", "DL-2"], "renderedKeys": ["DL-2", "DL-1"],
             "modelKeys": ["DL-1", "DL-2"], "renderedCount": 2, "modelCount": 2,
             "currentKeys": ["DL-1"], "siblingCurrentTitle": "latest", "panelState": "ready",
             "requestId": 2, "currentRequestId": 2, "beforeRequestId": 1, "dialogKey": "DL-1",
             "taskToken": "current-task", "verifiedAt": 14., "worldHash": "public-world", "settled": True}
    row = {"name": "mutation-summary", "pageId": "p1", "actionId": "a1", "ok": True,
           "startedAt": 9., "deadlineAt": 50., "settlement": {"complete": True},
           "state": {"requestToken": "current-task"}, "dialogProof": proof}
    starts = [{**shared, 'requestId': 'p1:r1', 'startedAt': 10.},
              {**shared, 'requestId': 'p1:r2', 'startedAt': 12.}]
    return [console], [failed, success], [failed], [row], starts


def test_exact_recovered_typed_read_keeps_raw_errors_and_requires_independent_ui_proof():
    from tools.jql_ui_benchmark import classify_recovered_reads
    errors, http, failures, rows, starts = recovered_read_evidence()
    original = deepcopy((errors, http, failures, rows, starts))
    result = classify_recovered_reads(errors, http, failures, rows, "public-world", starts)
    assert result["complete"] is True
    assert result["recovered"][0]["failureSequence"] == 0
    assert result["recovered"][0]["successSequences"] == [1]
    assert result["rawConsoleErrorCount"] == 1 and result["rawHttpErrorCount"] == 1
    assert (errors, http, failures, rows, starts) == original


def test_two_completed_reads_establish_observable_recovery_without_retry_attribution():
    from tools.jql_ui_benchmark import classify_recovered_reads
    errors, http, failures, rows, starts = recovered_read_evidence()
    http.append({**http[1], 'sequence': 2, 'requestId': 'p1:r3', 'startedAt': 12.5,
                 'observedAt': 13.3, 'finishedAt': 13.5})
    starts.append({**starts[1], 'requestId': 'p1:r3', 'startedAt': 12.5})
    original = deepcopy((errors, http, failures, rows, starts))
    result = classify_recovered_reads(errors, http, failures, rows, 'public-world', starts)
    assert result['complete'] is True
    assert result['recovered'][0]['successSequences'] == [1, 2]
    assert result['recovered'][0]['retryAttribution'] == 'not-observed'
    assert result['recovered'][0]['accountedRequestIds'] == ['p1:r1', 'p1:r2', 'p1:r3']
    assert (errors, http, failures, rows, starts) == original


@pytest.mark.parametrize("bad", ["old-inflight", "repeated", "generic", "unreadable", "wrong-ui",
    "missing-member", "duplicate-member", "wrong-token", "wrong-request", "no-settlement", "late",
    "ambiguous", "pageerror", "write", "different-page", "missing-start", "wrong-world", "unrecovered",
    "uncertain", "not-retryable", "login", "missing-failure-copy"])
def test_recovery_classification_fails_closed_on_wrong_or_ambiguous_evidence(bad):
    from tools.jql_ui_benchmark import classify_recovered_reads
    errors, http, failures, rows, starts = recovered_read_evidence()
    if bad == "old-inflight": http[1]["startedAt"] = starts[1]['startedAt'] = 10.5
    elif bad == "repeated":
        another = {**deepcopy(http[0]), "sequence": 2, "requestId": "p1:r3", "observedAt": 13.2}
        http.append(another); failures.append(another)
        starts.append({**starts[0], 'requestId': 'p1:r3'})
    elif bad == "generic": http[0]["body"]["code"] = "upstream_unavailable"
    elif bad == "unreadable": http[0].pop("body"); http[0]["bodyReadError"] = "not JSON"
    elif bad == "wrong-ui": rows[0]["dialogProof"]["title"] = "old"
    elif bad == "missing-member": rows[0]["dialogProof"]["renderedKeys"] = ["DL-1"]
    elif bad == "duplicate-member": rows[0]["dialogProof"]["renderedKeys"] = ["DL-1", "DL-1"]
    elif bad == "wrong-token": rows[0]["state"]["requestToken"] = "new-request"
    elif bad == "wrong-request": rows[0]["dialogProof"]["currentRequestId"] = 3
    elif bad == "no-settlement": rows[0]["settlement"]["complete"] = False
    elif bad == "late": http[1]["finishedAt"] = 46.
    elif bad == "ambiguous": http.append({**http[1], "sequence": 2, "requestId": "p1:r3"})
    elif bad == "pageerror": errors.append("Uncaught TypeError")
    elif bad == "write": http[0]["method"] = "POST"
    elif bad == "different-page": http[1]["pageId"] = "p2"
    elif bad == "missing-start": http[1].pop("startedAt")
    elif bad == "wrong-world": rows[0]["dialogProof"]["worldHash"] = "other"
    elif bad == "unrecovered": http.pop()
    elif bad == "uncertain": http[0]["body"]["uncertain"] = True
    elif bad == "not-retryable": http[0]["body"]["retryable"] = False
    elif bad == "login": http[0]["body"]["needLogin"] = True
    elif bad == "missing-failure-copy": failures.clear(); http[0]["body"]["code"] = "other"
    assert classify_recovered_reads(errors, http, failures, rows, "public-world", starts)["complete"] is False


@pytest.mark.parametrize('bad', ['no-ledger', 'empty-ledger', 'pending', 'abort', 'missing-finish',
    'duplicate-start-id', 'duplicate-response-id', 'wrong-start-time', 'after-proof', 'later-failure',
    'outside-budget', 'unmatched-start-before-error', 'unmatched-response', 'missing-failed-finish',
    'late-failed-finish'])
def test_every_relevant_start_must_have_one_finished_in_budget_response_before_proof(bad):
    from tools.jql_ui_benchmark import classify_recovered_reads
    errors, http, failures, rows, starts = recovered_read_evidence()
    if bad == 'no-ledger': starts = None
    elif bad == 'empty-ledger': starts = []
    elif bad in {'pending', 'unmatched-start-before-error'}:
        starts.append({**starts[1], 'requestId': 'p1:r3',
                       'startedAt': 12.5 if bad == 'pending' else 9.5})
    elif bad == 'abort': failures.append({**starts[1], 'failure': 'net::ERR_ABORTED'})
    elif bad == 'missing-finish': http[1].pop('finishedAt')
    elif bad == 'missing-failed-finish': http[0].pop('finishedAt')
    elif bad == 'late-failed-finish': http[0]['finishedAt'] = 14.5
    elif bad == 'duplicate-start-id': starts.append(deepcopy(starts[1]))
    elif bad == 'duplicate-response-id': http.append({**http[1], 'sequence': 2})
    elif bad == 'wrong-start-time': starts[1]['startedAt'] = 12.1
    elif bad == 'after-proof': http[1]['finishedAt'] = 14.5
    elif bad == 'later-failure':
        starts.append({**starts[1], 'requestId': 'p1:r3', 'startedAt': 14.5})
        http.append({**http[1], 'requestId': 'p1:r3', 'sequence': 2, 'startedAt': 14.5,
                     'observedAt': 15., 'finishedAt': 15.1, 'status': 502})
    elif bad == 'outside-budget':
        http[1]['finishedAt'] = 46.; rows[0]['dialogProof']['verifiedAt'] = 47.
    elif bad == 'unmatched-response':
        http.append({**http[1], 'sequence': 2, 'requestId': 'unobserved'})
    assert classify_recovered_reads(errors, http, failures, rows, 'public-world', starts)['complete'] is False


def test_independent_sibling_membership_uses_public_parent_or_epic_fields_only():
    from tools.jql_ui_edge_scenarios import expected_public_siblings
    world = SimpleNamespace(world=SimpleNamespace(issues={
        "DL-1": {"type": "Task", "parentKey": None, "epicKey": "DL-9"},
        "DL-2": {"type": "Task", "parentKey": None, "epicKey": "DL-9"},
        "DL-3": {"type": "Sub-Task", "parentKey": "DL-1", "epicKey": None},
        "DL-4": {"type": "Sub-Task", "parentKey": "DL-1", "epicKey": None},
        "DL-8": {"type": "Task", "parentKey": None, "epicKey": "DL-10"},
        "DL-9": {"type": "Epic", "parentKey": None, "epicKey": None},
    }))
    assert expected_public_siblings(world, "DL-1") == ["DL-1", "DL-2"]
    assert expected_public_siblings(world, "DL-3") == ["DL-3", "DL-4"]
    assert expected_public_siblings(world, "DL-9") == []


def test_requestfailed_cancellation_is_retained_unclassified_without_recovered_label():
    from tools.jql_ui_benchmark import classify_recovered_reads
    aborted = {"url": "/api/mytasks/stream", "failure": "net::ERR_ABORTED"}
    result = classify_recovered_reads([], [], [aborted], [], "public-world")
    assert result['unclassifiedRequestFailures'] == [aborted]
    assert result['recovered'] == []
    assert 'rapid scenario' in result['requestFailureScope']


def test_observer_preserves_request_start_page_action_and_finish_through_response_reordering(monkeypatch):
    from tools import jql_ui_benchmark as runner
    clock = [10.]
    handlers = {}
    class Page:
        def on(self, name, handler): handlers[name] = handler
    page = Page()
    context = SimpleNamespace(add_init_script=lambda *_: None, route=lambda *_: None, new_page=lambda: page)
    browser = SimpleNamespace(new_context=lambda **_: context)
    monkeypatch.setattr(runner.time, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(runner, 'HTTP_RESPONSES', [])
    monkeypatch.setattr(runner, 'HTTP_REQUEST_STARTS', [])
    monkeypatch.setattr(runner, 'NETWORK_FAILURES', [])
    monkeypatch.setattr(runner, 'HTTP_STREAM', None)
    runner.new_page(browser, Path(__file__).resolve().parents[3], [])
    class Request:
        method = 'GET'
        url = runner.BASE + '/api/ticket/DL-1/siblings'
    first, second = Request(), Request()
    page._jql_evidence['actionId'] = 'old-action'
    handlers['request'](first)
    clock[0] = 12.
    page._jql_evidence['actionId'] = 'new-action'
    handlers['request'](second)
    clock[0] = 13.
    handlers['response'](SimpleNamespace(url=first.url, request=first, status=200))
    clock[0] = 14.
    handlers['requestfinished'](first)
    row = runner.HTTP_RESPONSES[0]
    assert row['startedAt'] == 10. and row['actionId'] == 'old-action' and row['finishedAt'] == 14.
    assert page._jql_evidence['requests'][first]['requestId'] != page._jql_evidence['requests'][second]['requestId']
    assert row['pageId'] == page._jql_evidence['pageId']
    assert len(runner.HTTP_REQUEST_STARTS) == 2
    assert runner.HTTP_REQUEST_STARTS[0]['startedAt'] == 10.
    assert runner.HTTP_REQUEST_STARTS[0]['url'] == first.url
