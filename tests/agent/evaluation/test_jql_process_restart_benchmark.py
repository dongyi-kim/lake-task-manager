"""No-server contracts for the common process-restart recovery measurement."""
from copy import deepcopy
from types import SimpleNamespace
import asyncio
import json

import pytest


def page(keys, start, size=100, *, total=None, snapshot="frozen"):
    rows = keys[start:start + size]
    end = start + len(rows)
    return {"issues": [{"key": key} for key in rows], "returned": len(rows),
            "total": len(keys) if total is None else total, "startAt": start, "maxResults": size,
            "hasMore": end < len(keys), "nextStartAt": end if end < len(keys) else None,
            "snapshotId": snapshot, "snapshotFrozen": start > 0,
            "canonicalJql": "component = ETL ORDER BY key ASC", "snapshotCreatedAt": 100.0}


def test_full_2245_membership_requires_all_23_frozen_pages():
    from tools.jql_process_restart_benchmark import FrozenPages
    keys = [f"DL-{value}" for value in range(20000, 22245)]
    verifier = FrozenPages(keys)
    verifier.add(page(keys, 0))
    assert verifier.complete is False
    for offset in range(100, 2245, 100):
        verifier.add(page(keys, offset))
    assert verifier.result() == {"complete": True, "expectedCount": 2245, "returnedCount": 2245,
                                 "pageCount": 23, "continuationPages": 22, "snapshotId": "frozen",
                                 "optionalSnapshotDiagnostics": {"present": ["snapshotCreatedAt", "snapshotFrozen"],
                                                                 "missing": [], "advertised": []}}
    with pytest.raises(ValueError, match="complete"):
        verifier.add(page(keys, 2245))


@pytest.mark.parametrize("change", [
    {"startAt": 0}, {"total": 102}, {"total": None}, {"snapshotId": "changed"},
    {"snapshotFrozen": False}, {"canonicalJql": "other"}, {"snapshotCreatedAt": 101.0},
    {"returned": 0}, {"nextStartAt": 100}, {"hasMore": True}, {"maxResults": 7},
    {"returned": True}, {"snapshotFrozen": 1},
    {"issues": [{"key": "DL-0"}]},
])
def test_continuation_rejects_changed_identity_bad_progress_or_inexact_membership(change):
    from tools.jql_process_restart_benchmark import FrozenPages
    keys = [f"DL-{value}" for value in range(101)]
    verifier = FrozenPages(keys)
    verifier.add(page(keys, 0))
    broken = page(keys, 100)
    broken.update(change)
    with pytest.raises(ValueError):
        verifier.add(broken)
    assert verifier.complete is False


@pytest.mark.parametrize("keys", [["DL-1", "DL-1"], ["DL-2", "DL-1"], ["DL-1"]])
def test_first_page_rejects_duplicate_reordered_or_missing_membership(keys):
    from tools.jql_process_restart_benchmark import FrozenPages
    verifier = FrozenPages(["DL-1", "DL-2"])
    with pytest.raises(ValueError, match="membership"):
        verifier.add(page(keys, 0, total=2))


def test_exact_h_public_page_shape_supports_common_frozen_continuation():
    from tools.jql_process_restart_benchmark import FrozenPages
    # H's public SearchPage has these fields, without candidate-added diagnostics.
    first = {"issues": [{"key": "DL-21000", "fields": {}}], "startAt": 0, "maxResults": 1,
             "total": 2, "returned": 1, "hasMore": True, "nextStartAt": 1,
             "snapshotId": "h-snapshot", "canonicalJql": "project = DL ORDER BY key ASC"}
    second = {"issues": [{"key": "DL-21001", "fields": {}}], "startAt": 1, "maxResults": 1,
              "total": 2, "returned": 1, "hasMore": False, "nextStartAt": None,
              "snapshotId": "h-snapshot", "canonicalJql": "project = DL ORDER BY key ASC"}
    verifier = FrozenPages(["DL-21000", "DL-21001"], page_size=1)
    verifier.add(first)
    verifier.add(second)
    assert verifier.complete is True
    assert verifier.result()["optionalSnapshotDiagnostics"] == {
        "present": [], "missing": ["snapshotCreatedAt", "snapshotFrozen"], "advertised": []}


@pytest.mark.parametrize("field", ["snapshotCreatedAt", "snapshotFrozen"])
def test_candidate_optional_diagnostics_cannot_disappear_after_first_page(field):
    from tools.jql_process_restart_benchmark import FrozenPages
    keys = [f"DL-{value}" for value in range(101)]
    verifier = FrozenPages(keys)
    verifier.add(page(keys, 0))
    missing = page(keys, 100)
    del missing[field]
    with pytest.raises(ValueError, match="diagnostic"):
        verifier.add(missing)


def test_advertised_optional_diagnostic_is_required_without_fabricating_it():
    from tools.jql_process_restart_benchmark import FrozenPages, advertised_diagnostics
    tool = {"outputSchema": {"$defs": {"SearchPage": {"properties": {"snapshotFrozen": {"type": "boolean"}}}}}}
    fields = advertised_diagnostics(tool)
    assert fields == {"snapshotFrozen"}
    verifier = FrozenPages(["DL-1"], advertised=fields)
    missing = page(["DL-1"], 0)
    del missing["snapshotFrozen"]
    with pytest.raises(ValueError, match="diagnostic"):
        verifier.add(missing)


def test_expected_membership_comes_from_public_fields_and_numeric_key_order():
    from tools.jql_process_restart_benchmark import expected_etl_keys
    bundle = SimpleNamespace(world=SimpleNamespace(issues={
        "DL-10": {"component": "ETL", "project": "DL"},
        "DL-2": {"component": "ETL", "project": "DL"},
        "DL-1": {"component": "Catalog", "project": "DL"},
        "OTHER-3": {"component": "ETL", "project": "OTHER"},
    }))
    assert expected_etl_keys(bundle) == ["DL-2", "DL-10"]


def test_typed_tool_failure_is_not_empty_success_or_protocol_failure():
    from tools.jql_process_restart_benchmark import tool_payload, typed_failure
    failure = {"ok": False, "error": {"code": "upstream_unavailable", "message": "injected"}}
    assert typed_failure(tool_payload({"structuredContent": failure, "isError": True})) == failure["error"]
    for broken in ({"ok": True, "data": {"issues": []}}, {"ok": False, "error": "503"}, {}):
        with pytest.raises(ValueError):
            typed_failure(broken)
    with pytest.raises(ValueError, match="structured"):
        tool_payload({"content": [{"type": "text", "text": "failure"}]})


def complete_result():
    return {"membership": {"complete": True, "expectedCount": 2245, "returnedCount": 2245,
                            "pageCount": 23, "continuationPages": 22},
            "phases": [{"name": name, "backendPid": pid, "cachePath": "same.sqlite",
                        "cacheBefore": {"sha": "before" if index == 0 else f"after-{index - 1}"},
                        "cacheAfter": {"sha": f"after-{index}"}, "stopped": True, "portClosed": True,
                        "settlement": {"complete": True, "active": 0, "admitted": 0}}
                       for index, (name, pid) in enumerate(zip(("interrupted", "recovery", "continuation"), (10, 20, 30)))],
            "injectedFailure": {"code": "upstream_unavailable"}, "injectedFaultCount": 2, "injectedWire503Count": 2,
            "faultEvidence": {"matched": True, "injectedCount": 2, "wire503Count": 2},
            "frozenContinuationRestAttempts": 0, "revisionStable": True, "scratchRemoved": True,
            "port4457Closed": True, "withinOverallDeadline": True, "wireTail": {"complete": True}}


@pytest.mark.parametrize("bad", ["pid", "cache", "lineage", "settlement", "admission", "stopped", "port",
                                 "membership", "revision", "scratch", "wire", "failure", "faultCount", "faultMatch", "rest", "deadline"])
def test_completion_gate_rejects_missing_restart_recovery_or_cleanup_evidence(bad):
    from tools.jql_process_restart_benchmark import recovery_complete
    result = complete_result()
    assert recovery_complete(result) is True
    if bad == "pid": result["phases"][2]["backendPid"] = 20
    elif bad == "cache": result["phases"][1]["cachePath"] = "replacement.sqlite"
    elif bad == "lineage": result["phases"][1]["cacheBefore"] = {"sha": "fresh"}
    elif bad == "settlement": result["phases"][0]["settlement"]["complete"] = False
    elif bad == "admission": result["phases"][0]["settlement"]["admitted"] = 1
    elif bad == "stopped": result["phases"][1]["stopped"] = False
    elif bad == "port": result["port4457Closed"] = False
    elif bad == "membership": result["membership"]["complete"] = False
    elif bad == "revision": result["revisionStable"] = False
    elif bad == "scratch": result["scratchRemoved"] = False
    elif bad == "wire": result["wireTail"]["complete"] = False
    elif bad == "failure": result["injectedFailure"] = None
    elif bad == "faultCount": result["injectedWire503Count"] = 0
    elif bad == "faultMatch": result["faultEvidence"]["matched"] = False
    elif bad == "rest": result["frozenContinuationRestAttempts"] = 1
    elif bad == "deadline": result["withinOverallDeadline"] = False
    assert recovery_complete(result) is False


def test_protocol_counts_separate_business_discovery_notifications_and_failures():
    from tools.jql_process_restart_benchmark import protocol_counts
    rows = [{"kind": "mcp", "method": method, "httpStatus": status, "durationMs": duration}
            for method, status, duration in (("initialize", 200, 1), ("notifications/initialized", 202, 2),
                ("tools/list", 200, 3), ("tools/call", 200, 4), ("tools/call", 503, 5))]
    assert protocol_counts(rows) == {"protocolHttpAttempts": 5, "businessCalls": 2, "discoveryCalls": 2,
                                    "notificationCalls": 1, "httpFailures": 1, "durationMs": 15}


def test_dispatch_cost_counts_pages_auth_retries_and_failure_without_logical_query_inflation():
    from tools.jql_process_restart_benchmark import dispatch_metrics
    calls = [{"path": path, "params": params, "status": status, "elapsedMs": duration}
             for path, params, status, duration in (("/rest/api/2/myself", {}, 200, 1),
                ("/rest/api/2/search", {"jql": "same", "startAt": 0}, 200, 2),
                ("/rest/api/2/search", {"jql": "same", "startAt": 100}, 503, 3),
                ("/rest/api/2/search", {"jql": "same", "startAt": 100}, 200, 4))]
    assert dispatch_metrics(calls) == {"dispatchAttempts": 4, "searchPageAttempts": 3,
                                       "authProbeAttempts": 1, "uniquePhysicalJql": 1,
                                       "failedDispatches": 1, "dispatchDurationMs": 10}


def test_continuation_counts_full_wire_tail_after_metrics_snapshot_without_earlier_priming():
    from tools.jql_process_restart_benchmark import continuation_dispatches
    tail = [{"pid": pid, "sequence": sequence} for pid, sequence in ((10, 0), (30, 0), (30, 1), (30, 2))]
    assert continuation_dispatches(tail, pid=30, start_sequence=1) == 2


def test_deadline_caps_each_call_and_never_implicitly_restarts_the_clock(monkeypatch):
    from tools import jql_process_restart_benchmark as runner
    clock = [0.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    deadline = runner.Deadline(130, 120)
    assert deadline.remaining() == 120
    clock[0] = 125
    assert deadline.remaining() == 5
    clock[0] = 130
    with pytest.raises(TimeoutError, match="overall"):
        deadline.remaining()


def test_schema_preflight_rejects_missing_common_snapshot_api_without_substitution():
    from tools.jql_process_restart_benchmark import require_common_tool
    tool = {"name": "search_issues", "inputSchema": {"properties": {
        name: {} for name in ("jql", "start_at", "max_results", "snapshot_id", "detail", "include_description")}}}
    assert require_common_tool({"tools": [tool]}) == tool
    broken = deepcopy(tool)
    del broken["inputSchema"]["properties"]["snapshot_id"]
    with pytest.raises(ValueError, match="snapshot_id"):
        require_common_tool({"tools": [broken]})


@pytest.mark.parametrize("state", [{"complete": False, "active": 0, "admitted": 0},
                                   {"complete": True, "active": 1, "admitted": 0},
                                   {"complete": True, "active": 0, "admitted": 1}])
def test_any_pending_admission_or_dispatch_invalidates_a_phase_boundary(state):
    from tools.jql_process_restart_benchmark import require_settled
    with pytest.raises(ValueError, match="settlement"):
        require_settled(state)


def test_protocol_call_has_one_wall_deadline_and_preserves_timeout_without_retry(monkeypatch):
    from tools import jql_process_restart_benchmark as runner
    evidence, dispatched = [], []
    async def slow_http(*args, **kwargs):
        dispatched.append(args)
        await asyncio.sleep(.1)
    monkeypatch.setattr(runner, "send_http", slow_http)
    transport = runner.Transport(SimpleNamespace(record=evidence.append), runner.Deadline(1, .01))
    with pytest.raises(TimeoutError):
        transport.rpc("tools/list")
    assert len(dispatched) == 1
    assert len(transport.rows) == 1 and transport.rows[0]["done"] is True
    assert evidence[-1]["error"]["type"] == "TimeoutError"


def test_persistent_later_page_fault_blocks_bounded_read_recovery_and_records_every_injection(tmp_path):
    from tools.jql_process_restart_benchmark import LaterPageFault, typed_failure
    armed, log = tmp_path / "armed", tmp_path / "faults.jsonl"
    log.touch()
    scope = {"path": "/rest/api/2/search", "query_string": b"jql=component%3DETL&startAt=100&maxResults=100"}
    fault = LaterPageFault(armed, log, enabled=True)
    assert fault.inject(scope) is False
    armed.touch()
    first_page = {**scope, "query_string": b"jql=component%3DETL&startAt=0&maxResults=100"}
    assert fault.inject(first_page) is False
    # The original one-shot actual run returned this successful public response.
    recovered = {"ok": True, "data": {"total": 2245, "returned": 100, "hasMore": True}}
    with pytest.raises(ValueError, match="typed tool failure"):
        typed_failure(recovered)
    # Both original and retried later-page reads remain unavailable while armed.
    assert fault.inject(scope) is True
    assert fault.inject(first_page) is False
    assert fault.inject(scope) is True
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2 and all(row["status"] == 503 and row["injected"] for row in records)
    assert [row["params"]["startAt"] for row in records] == [["100"], ["100"]]
    assert LaterPageFault(armed, log, enabled=False).inject(scope) is False
    armed.unlink()
    assert fault.inject(scope) is False
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.parametrize("mismatch", [None, "missingRecord", "extraRecord", "otherPid", "wrongQuery", "wrongOffset"])
def test_fault_records_match_every_phase_a_wire_503_including_repeated_retry_pages(mismatch):
    from tools.jql_process_restart_benchmark import fault_evidence
    record = {"pid": 10, "path": "/rest/api/2/search", "status": 503, "injected": True,
              "params": {"jql": ["same"], "startAt": ["100"], "maxResults": ["100"]}}
    records = [deepcopy(record), deepcopy(record)]
    wire = [{"pid": pid, "path": "/rest/api/2/search", "status": status,
             "params": {"jql": "same", "startAt": 100, "maxResults": 100}}
            for pid, status in ((10, 200), (10, 503), (10, 503), (20, 503))]
    if mismatch == "missingRecord": records.pop()
    elif mismatch == "extraRecord": records.append(deepcopy(record))
    elif mismatch == "otherPid": records[0]["pid"] = 20
    elif mismatch == "wrongQuery": records[0]["params"]["jql"] = ["other"]
    elif mismatch == "wrongOffset": records[0]["params"]["startAt"] = ["200"]
    evidence = fault_evidence(records, wire, phase_a_pid=10)
    assert evidence["wire503Count"] == 2
    assert evidence["injectedCount"] == len(records)
    assert evidence["matched"] is (mismatch is None)


def test_no_injection_cannot_qualify_via_vacuous_wire_matching():
    from tools.jql_process_restart_benchmark import fault_evidence
    assert fault_evidence([], [], phase_a_pid=10) == {"injectedCount": 0, "wire503Count": 0, "matched": False}
