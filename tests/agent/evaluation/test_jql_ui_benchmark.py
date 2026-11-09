"""UI harness world/config fidelity and low-level request accounting contracts."""
from pathlib import Path
from types import SimpleNamespace
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import subprocess
import sys
import textwrap

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
