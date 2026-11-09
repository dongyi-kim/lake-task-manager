"""UI harness world/config fidelity and low-level request accounting contracts."""
from pathlib import Path
from types import SimpleNamespace
import json
from concurrent.futures import ThreadPoolExecutor
import threading

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
