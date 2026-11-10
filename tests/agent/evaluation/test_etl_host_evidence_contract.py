"""Host-only regressions from the preserved original pilot; no model or server."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tools.codex_mcp_eval.etl import checks


def epic_call():
    return {"name": "get_epic", "arguments": {"key": "DL-20000"}, "result": {
        "ok": True, "partial": False, "missing": 0,
        "cache": {"available": True, "state": "fresh", "invalidated": False},
        "data": {"epic": {"key": "DL-20000"}, "tree": [
            {"key": "DL-1", "type": "Task", "sp": 2, "statusName": "Resolved", "statusCat": "done",
             "component": "ETL", "children": [{"key": "DL-3", "type": "Sub-Task", "sp": 99}]},
            {"key": "DL-2", "type": "Bug", "sp": 8, "statusName": "In Progress", "statusCat": "inprogress",
             "component": "ETL", "children": []}]}}}


INPUTS = {
    "DL-1": {"type": "Task", "sp": 2, "status": "Resolved", "epic": "DL-20000", "component": "ETL"},
    "DL-2": {"type": "Bug", "sp": 8, "status": "In Progress", "epic": "DL-20000", "component": "ETL"}}


def test_complete_current_epic_tree_proves_typed_child_inputs_without_body_reads():
    assert checks.ticket_inputs_match([epic_call()], INPUTS, {})
    observed = checks.observed_ticket_inputs([epic_call()], {})
    assert "DL-3" not in observed  # Nested SubTask SP is not another Epic work item.
    assert not checks.ticket_inputs_match([epic_call()], {"DL-1": {"created_date": "2025-11-01"}}, {})


def test_epic_request_identity_is_canonical_but_proof_still_comes_from_response():
    call = epic_call()
    call["arguments"]["key"] = "dl-20000"
    assert checks.ticket_inputs_match([call], INPUTS, {})


@pytest.mark.parametrize("field,value", [("cache", []), ("data", {"epic": ["DL-20000"], "tree": []})])
def test_malformed_successful_epic_payload_is_not_evidence_or_a_checker_crash(field, value):
    call = epic_call()
    call["result"][field] = value
    assert not checks.ticket_inputs_match([call], INPUTS, {})


@pytest.mark.parametrize("broken", ["partial", "missing", "stale", "invalidated", "unavailable", "wrongOwner",
                                  "wrongArgument", "duplicate", "truncated", "missingSp", "missingStatus",
                                  "missingComponent", "morePages"])
def test_epic_evidence_requires_complete_fresh_matching_roster_and_actual_typed_fields(broken):
    call = epic_call()
    result, data = call["result"], call["result"]["data"]
    if broken == "partial": result["partial"] = True
    elif broken == "missing": result["missing"] = 1
    elif broken == "stale": result["cache"]["state"] = "stale"
    elif broken == "invalidated": result["cache"]["invalidated"] = True
    elif broken == "unavailable": result["cache"]["available"] = False
    elif broken == "wrongOwner": data["epic"]["key"] = "DL-20001"
    elif broken == "wrongArgument": call["arguments"]["key"] = "DL-20001"
    elif broken == "duplicate": data["tree"].append(deepcopy(data["tree"][0]))
    elif broken == "truncated": data["tree"].pop()
    elif broken == "missingSp": del data["tree"][0]["sp"]
    elif broken == "missingStatus": del data["tree"][0]["statusName"]
    elif broken == "missingComponent": del data["tree"][0]["component"]
    elif broken == "morePages": data["hasMore"] = True
    assert not checks.ticket_inputs_match([call], INPUTS, {})


def test_scalar_epic_aggregate_without_typed_roster_cannot_prove_child_inputs():
    call = {"name": "get_epic_progress", "arguments": {"key": "DL-20000"},
            "result": {"ok": True, "partial": False, "missing": 0,
                       "data": {"doneSp": 2, "totalSp": 2, "progressPct": 100}}}
    assert not checks.ticket_inputs_match([call], INPUTS, {})


def checked(answer_changes=None, *, extra_calls=()):
    expected = {"keys": ["DL-1"], "measurements": [{"stream": "operations", "scope": "modify@2025-11",
        "metric": "work_events", "value": None, "unit": "events"}], "denominators": [],
        "reportFindings": [], "reportProgress": [], "requiredTicketInputs": {}, "requiredFieldFacts": [],
        "progress": None, "reportedTotal": None, "requiredSources": []}
    answer = {"resultKeys": ["DL-1"], "measurements": deepcopy(expected["measurements"]), "denominators": [],
        "reportFindings": [], "reportProgress": [], "progress": None, "reportedTotal": None, "sources": []}
    answer.update(answer_changes or {})
    calls = [{"name": "get_ticket", "result": {"ok": True, "data": {"key": "DL-1", "parentKey": "DL-2"}}}, *extra_calls]
    return checks.check({"id": "test"}, {"ok": True, "reply": "확인된 소계와 미확정 총계를 구분합니다.",
                        "structuredAnswer": answer}, calls, {"cases": {"test": expected}, "ticketFieldIds": {}})


def test_result_scope_accepts_observed_context_and_exclusions_but_not_invented_keys():
    assert checked({"resultKeys": ["DL-1", "DL-2"]})["pass"]
    literal = {"name": "get_ticket", "result": {"ok": True, "data": {"key": "DL-3", "type": "Bug"}}}
    assert checked({"resultKeys": ["DL-1", "DL-3"]}, extra_calls=[literal])["pass"]
    quoted = {"name": "get_ticket", "arguments": {"key": "DL-99"},
              "result": {"ok": True, "data": {"key": "DL-1", "description": "DL-99 excluded"}}}
    assert "result_scope_mismatch" in checked({"resultKeys": ["DL-1", "DL-99"]}, extra_calls=[quoted])["failures"]
    failed = {"name": "get_ticket", "result": {"ok": False, "data": {"key": "DL-3"}}}
    assert "result_scope_mismatch" in checked({"resultKeys": ["DL-1", "DL-3"]}, extra_calls=[failed])["failures"]


@pytest.mark.parametrize("keys", [[], ["DL-2"], ["DL-1", "DL-1"], ["DL-2", "DL-1"], ["DL-1", "DL-99"]])
def test_result_scope_still_rejects_missing_duplicate_unsorted_and_unobserved_keys(keys):
    assert "result_scope_mismatch" in checked({"resultKeys": keys})["failures"]


def test_scope_extras_cannot_relax_unknown_canonical_totals():
    fabricated_total = [{"stream": "operations", "scope": "modify@2025-11", "metric": "work_events", "value": 10, "unit": "events"}]
    assert "measurement_mismatch" in checked({"resultKeys": ["DL-1", "DL-2"], "measurements": fabricated_total})["failures"]


def test_unknown_undated_acceptance_is_not_converted_to_zero_or_an_invented_date():
    unknown_events = {"key": "DL-1", "view": "current", "acceptedEvents": None}
    assert not checks._report_rows_match([{**unknown_events, "acceptedEvents": 0}], [unknown_events])
    undated = {"documentId": "report", "version": 1, "section": 2, "occurredAt": None}
    assert not checks._report_rows_match([{**undated, "occurredAt": "2026-10-24"}], [undated])


@pytest.fixture(scope="module")
def world_expectations():
    from tools.codex_mcp_eval.etl import build_etl_world
    bundle = build_etl_world()
    class ObservedOracle:
        def __init__(self): self.calls = []
        def __getattr__(self, name): return getattr(bundle.oracle, name)
        def operations(self, start, end, **options):
            self.calls.append((start, end, options))
            return bundle.oracle.operations(start, end, **options)
    oracle = ObservedOracle()
    expected = checks.build_expectations(SimpleNamespace(**{**vars(bundle), "oracle": oracle}))
    return bundle, oracle.calls, expected


def test_november_boundary_excludes_december_execution_and_closure_without_changing_unknowns(world_expectations):
    _bundle, _calls, expected = world_expectations
    rows = {row["metric"]: row["value"] for row in expected["ETL-F09-boundary"]["measurements"] if row["scope"] == "modify@2025-11"}
    assert rows["completed_tickets"] == 10  # DL-31012 closure Dec1 00:10 is not a November closure.
    assert rows["completed_unique_tables"] is None and rows["work_events"] is None


@pytest.mark.parametrize("operation", ["modify", "suspend", "delete"])
def test_normal_request_cohort_uses_current_as_of_completion_not_just_request_window(world_expectations, operation):
    bundle, calls, _expected = world_expectations
    keys = {event.issue_key for event in bundle.oracle.events if event.kind == "issue" and event.stream == "operations"
            and event.payload["operation"] == operation and checks._at("2025-11-14") <= event.occurred_at < checks._at("2025-11-17")}
    # The authored delete request cohort is empty; it must stay an empty cohort.
    intervals = [(start, end) for start, end, options in calls if set(options.get("issue_keys") or []) == keys]
    assert intervals and all(end == bundle.spec.observed_until for _start, end in intervals)


def test_ordinary_reports_do_not_demand_whole_task_percent_but_explicit_progress_remains(world_expectations):
    _bundle, _calls, expected = world_expectations
    for case_id in ("ETL-F01-normal", "ETL-F02-boundary", "ETL-F03-boundary", "ETL-F06-normal", "ETL-F14-boundary"):
        assert all("percent" not in row for row in expected[case_id]["reportProgress"])
        assert not any(row["metric"] == "percent" for row in expected[case_id]["measurements"])
    for case_id in ("ETL-F04-boundary", "ETL-F05-boundary", "ETL-F06-boundary"):
        assert any(row["metric"] == "percent" for row in expected[case_id]["measurements"])
    monthly = expected["ETL-F03-boundary"]
    assert any(row["metric"] == "verified_deliverables" for row in monthly["measurements"])
    assert monthly["reportExpectations"]["taskProgress"]["DL-21749"]["reported_acceptance_events"] is None
    assert all(row["acceptedEvents"] is None for row in monthly["reportProgress"] if row["key"] == "DL-21749")


def test_multiunit_questions_have_no_ambiguous_representative_total(world_expectations):
    _bundle, _calls, expected = world_expectations
    assert all(expected[key]["reportedTotal"] is None for key in
               ("ETL-F08-normal", "ETL-F12-normal", "ETL-F12-boundary", "ETL-F13-normal", "ETL-F13-boundary"))
    assert expected["ETL-F07-normal"]["reportedTotal"] == expected["ETL-F07-boundary"]["reportedTotal"] == 1
