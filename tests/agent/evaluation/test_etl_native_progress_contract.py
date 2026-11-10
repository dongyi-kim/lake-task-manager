"""Independent small native-aggregate contracts; no full world/server/model."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tools.codex_mcp_eval.etl import checks


NATIVE = {"name": "Public Epic", "doneSp": 2.0, "totalSp": 3.0, "progressPct": 66.7,
          "mockSp": 1.0, "countedIssues": 2, "excludedIssues": 1}
INPUTS = {"DL-1": {"sp": 2, "type": "Task", "epic": "DL-100", "status": "Closed", "component": "ETL"},
          "DL-2": {"sp": None, "type": "Task", "epic": "DL-100", "status": "In Progress", "component": "ETL"},
          "DL-3": {"sp": 8, "type": "Bug", "epic": "DL-100", "status": "Closed", "component": "ETL"}}


def envelope(data):
    return {"ok": True, "partial": False, "missing": 0, "data": data,
            "cache": {"available": True, "state": "fresh", "invalidated": False}}


def calls():
    return [{"name": "get_epic_progress", "arguments": {"key": "DL-100"}, "result": envelope(deepcopy(NATIVE))},
            {"name": "get_tickets", "arguments": {"keys": ["DL-100"]}, "result": envelope({
                "complete": True, "issues": [{"key": "DL-100", "url": "http://jira/browse/DL-100",
                    "fields": {"summary": "Public Epic", "issuetype": {"name": "Epic"}}}], "missingKeys": []})}]


def expected():
    return {"keys": ["DL-1", "DL-2", "DL-3", "DL-100"], "measurements": [{"stream": "development",
        "scope": "DL-100@current", "metric": "percent", "unit": "percent", "value": 66.7}],
        "denominators": [{"scope": "DL-100@current", "metric": "SP progress", "numerator": 2,
                          "denominator": 3, "unit": "SP"}],
        "requiredTicketInputs": deepcopy(INPUTS), "requiredFieldFacts": [], "requiredSources": [],
        "reportFindings": [], "reportProgress": [], "reportedTotal": None, "progress": None,
        "nativeProgressEvidence": {"view": "current", "epics": {"DL-100": deepcopy(NATIVE)},
            "arithmeticKeys": list(INPUTS), "arithmeticTicketInputs": deepcopy(INPUTS)}}


def evaluate(*, observations=None, wanted=None, answer_change=None, public_sources=None):
    wanted = deepcopy(wanted or expected())
    answer = {name: deepcopy(wanted[name]) for name in (
        "measurements", "denominators", "reportFindings", "reportProgress", "reportedTotal", "progress")}
    answer.update(resultKeys=["DL-100"], sources=[{"kind": "ticket", "id": "DL-100", "url": "http://jira/browse/DL-100"}])
    if answer_change:
        answer_change(answer)
    return checks.check({"id": "test"}, {"ok": True, "reply": "SP 2/3, 66.7%입니다.", "structuredAnswer": answer},
        calls() if observations is None else observations, {"cases": {"test": wanted}, "ticketFieldIds": {},
                                                          "publicSources": public_sources or {}})


def test_fresh_complete_native_progress_with_typed_epic_identity_discharges_only_arithmetic_roster():
    result = evaluate()
    assert result["pass"], result


def test_native_progress_reuses_complete_fresh_search_epic_metadata():
    observed = calls()
    observed[1]["name"] = "search_issues"
    observed[1]["arguments"] = {"jql": "type=Epic"}
    observed[1]["result"]["data"].update(hasMore=False, total=1, returned=1)
    assert evaluate(observations=observed)["pass"]


def dashboard():
    entry = {name: value for name, value in NATIVE.items() if name not in {"countedIssues", "excludedIssues"}}
    return {"name": "get_dashboard", "arguments": {"kind": "wbs", "module": "ETL"},
        "result": envelope({"epics": {"DL-100": {**entry, "key": "DL-100"}},
            "wbs": [{"id": "WBS-1", "progressPct": 66.7, "epics": [
                {"epicKey": "DL-100", "weight": 1, "epicPct": 66.7}]}]})}


def wbs_expected():
    wanted = expected()
    wanted["nativeProgressEvidence"]["wbs"] = {"id": "WBS-1", "weights": {"DL-100": 1}, "progressPct": 66.7}
    wanted["measurements"][0]["scope"] = "WBS-1@current"
    wanted["denominators"] = [{"scope": "WBS-1@current", "metric": "SP-weighted epic progress",
        "numerator": 66.7, "denominator": 1, "unit": "weighted percent"}]
    return wanted


def test_bound_dashboard_fields_and_mapping_reads_prove_wbs_without_extra_epic_calls():
    wanted = wbs_expected()
    sources, reads = {}, []
    for version in (1, 2):
        source = "confluence:123:version:" + str(version)
        sources[source] = {"kind": "confluence_revision", "id": "123", "version": version, "text": "Mapping"}
        reads.append({"name": "read_confluence_document", "result": envelope({"id": "123", "version": {
            "number": version}, "url": "http://conf/123", "content": "Mapping", "offset": 0, "totalCharacters": 7})})
    wanted["requiredSources"] = list(sources)
    result = evaluate(wanted=wanted, observations=[dashboard(), *reads], public_sources=sources,
        answer_change=lambda answer: answer.update(sources=[{"kind": "document", "id": "123", "url": "http://conf/123"}]))
    assert result["pass"], result
    assert "required_evidence_not_observed" in evaluate(wanted=wanted, observations=[dashboard()],
        public_sources=sources, answer_change=lambda answer: answer.update(sources=[]))["failures"]


def test_bound_dashboard_epic_proof_is_current_only_and_retains_requested_denominator():
    assert evaluate(observations=[dashboard()], answer_change=lambda answer: answer.update(sources=[]))["pass"]
    historical = expected()
    historical["nativeProgressEvidence"]["view"] = "2026-01-15"
    assert not evaluate(wanted=historical, observations=[dashboard()],
        answer_change=lambda answer: answer.update(sources=[]))["pass"]
    result = evaluate(observations=[dashboard()], answer_change=lambda answer:
        (answer.update(sources=[]), answer["denominators"][0].update(numerator=3)))
    assert "denominator_mismatch" in result["failures"]


@pytest.mark.parametrize("damage", ["missingField", "wrongMapKey", "wrongKey", "wrongName", "wrongValue",
    "nan", "boolValue", "otherKind", "missingKind", "noMappedEpic", "wrongWbsId", "wrongWeight",
    "wrongWbsPct", "partial", "stale", "scalarOnly", "referenceOnly"])
def test_dashboard_route_requires_bound_requested_fields_current_scope_and_complete_wbs(damage):
    observed = dashboard()
    data = observed["result"]["data"]
    epic = data["epics"]["DL-100"]
    if damage == "missingField": del epic["mockSp"]
    elif damage == "wrongMapKey": data["epics"] = {"DL-999": epic}
    elif damage == "wrongKey": epic["key"] = "DL-999"
    elif damage == "wrongName": epic["name"] = "Other Epic"
    elif damage == "wrongValue": epic["doneSp"] = 3
    elif damage == "nan": epic["totalSp"] = float("nan")
    elif damage == "boolValue": epic["mockSp"] = False
    elif damage == "otherKind": observed["arguments"]["kind"] = "vit"
    elif damage == "missingKind": observed["arguments"].pop("kind")
    elif damage == "noMappedEpic": data["epics"] = {}
    elif damage == "wrongWbsId": data["wbs"][0]["id"] = "Other"
    elif damage == "wrongWeight": data["wbs"][0]["epics"][0]["weight"] = 3
    elif damage == "wrongWbsPct": data["wbs"][0]["progressPct"] = 0
    elif damage == "partial": observed["result"]["partial"] = True
    elif damage == "stale": observed["result"]["cache"]["state"] = "stale"
    elif damage == "scalarOnly": data.clear(); data["progressPct"] = 66.7
    elif damage == "referenceOnly": data["epics"]["DL-100"] = {"epicKey": "DL-100", "name": "Public Epic"}
    assert not evaluate(wanted=wbs_expected(), observations=[observed],
        answer_change=lambda answer: answer.update(sources=[]))["pass"]


@pytest.mark.parametrize("damage", ["partial", "missing", "stale", "invalidated", "unavailable", "failed",
    "badData", "wrongOwner", "wrongName", "wrongSp", "wrongPct", "wrongCounts", "missingField", "nan",
    "boolCount", "incomplete", "noMetadata", "wrongMetadata", "staleMetadata", "metadataOnly"])
def test_native_route_does_not_accept_incomplete_unowned_or_nonmatching_evidence(damage):
    observed = calls()
    result, data = observed[0]["result"], observed[0]["result"]["data"]
    if damage == "partial": result["partial"] = True
    elif damage == "missing": result["missing"] = 1
    elif damage == "stale": result["cache"]["state"] = "stale"
    elif damage == "invalidated": result["cache"]["invalidated"] = True
    elif damage == "unavailable": result["cache"]["available"] = False
    elif damage == "failed": result["ok"] = False
    elif damage == "badData": result["data"] = []
    elif damage == "wrongOwner": observed[0]["arguments"]["key"] = "DL-999"
    elif damage == "wrongName": data["name"] = "Other Epic"
    elif damage == "wrongSp": data["totalSp"] = 11
    elif damage == "wrongPct": data["progressPct"] = 100
    elif damage == "wrongCounts": data["excludedIssues"] = 0
    elif damage == "missingField": del data["mockSp"]
    elif damage == "nan": data["doneSp"] = float("nan")
    elif damage == "boolCount": data["countedIssues"] = True
    elif damage == "incomplete": data["complete"] = False
    elif damage == "noMetadata": observed.pop()
    elif damage == "wrongMetadata": observed[1]["result"]["data"]["issues"][0]["fields"]["issuetype"]["name"] = "Task"
    elif damage == "staleMetadata": observed[1]["result"]["cache"]["state"] = "stale"
    elif damage == "metadataOnly": observed.pop(0)
    assert not evaluate(observations=observed)["pass"]


def test_historical_contract_and_numeric_denominators_never_get_native_waiver():
    historical = expected()
    historical.pop("nativeProgressEvidence")
    assert not evaluate(wanted=historical)["pass"]
    historical = expected()
    historical["nativeProgressEvidence"]["view"] = "2026-01-15"
    assert not evaluate(wanted=historical)["pass"]
    assert "denominator_mismatch" in evaluate(answer_change=lambda answer:
        answer["denominators"][0].update(denominator=4))["failures"]


def test_mapping_versions_and_unrelated_report_status_proofs_stay_required():
    wanted = expected()
    wanted["requiredSources"] = ["confluence:123:version:1", "confluence:123:version:2"]
    assert "required_evidence_not_observed" in evaluate(wanted=wanted)["failures"]
    wanted = expected()
    wanted["requiredTicketInputs"]["DL-1"]["summary"] = "Report task"
    assert "required_typed_inputs_not_observed" in evaluate(wanted=wanted)["failures"]
    wanted = expected()
    wanted["reportProgress"] = [{"key": "DL-1", "view": "current", "jiraStatus": "Closed",
        "implementation": [], "testAccepted": [], "finalAccepted": [], "planned": [], "acceptedEvents": 0}]
    assert "result_scope_mismatch" in evaluate(wanted=wanted)["failures"]


def test_report_status_overlapping_an_arithmetic_child_is_still_observed():
    wanted = expected()
    wanted["reportProgress"] = [{"key": "DL-1", "view": "current", "jiraStatus": "Closed",
        "implementation": [], "testAccepted": [], "finalAccepted": [], "planned": [], "acceptedEvents": 0}]
    observed = [*calls(), {"name": "get_ticket", "result": envelope({"key": "DL-1"})}]
    result = evaluate(wanted=wanted, observations=observed, answer_change=lambda answer:
        answer.update(resultKeys=["DL-1", "DL-100"]))
    assert "required_typed_inputs_not_observed" in result["failures"]


def test_every_mapped_epic_requires_its_own_aggregate_and_unknown_native_totals_never_qualify():
    wanted = expected()
    wanted["nativeProgressEvidence"]["epics"]["DL-101"] = {**NATIVE, "name": "Another Epic"}
    observed = calls()
    observed[1]["result"]["data"]["issues"].append({"key": "DL-101", "fields": {
        "summary": "Another Epic", "issuetype": {"name": "Epic"}}})
    assert not evaluate(wanted=wanted, observations=observed)["pass"]
    wanted = expected()
    wanted["nativeProgressEvidence"]["epics"]["DL-100"]["totalSp"] = None
    observed = calls()
    observed[0]["result"]["data"]["totalSp"] = None
    assert not evaluate(wanted=wanted, observations=observed)["pass"]


def test_independent_native_expectations_preserve_missing_zero_exclusions_and_mock():
    rows = {key: {"type": values["type"], "epic": values["epic"], "sp": values["sp"], "status": values["status"]}
            for key, values in INPUTS.items()}
    rows["DL-100"] = {"type": "Epic", "epic": None, "sp": 0, "status": "In Progress"}
    public = {key: {"summary": "Public Epic" if key == "DL-100" else key,
                    "component": "ETL", "labels": ["mock"] if key == "DL-2" else []} for key in rows}
    bundle = SimpleNamespace(world=SimpleNamespace(issues=public))
    contract = checks._native_progress_expectations(bundle, {"issues": rows,
        "epics": {"DL-100": {"sp_done": 2, "sp_total": 3, "percent": 66.7}}}, ["DL-100"], INPUTS)
    assert contract["epics"] == {"DL-100": NATIVE}
    assert set(contract["arithmeticKeys"]) == set(INPUTS)
    rows["DL-4"] = {"type": "Task", "epic": "DL-100", "sp": 0, "status": "Closed"}
    rows["DL-5"] = {"type": "Task", "epic": "DL-100", "sp": 50, "status": "Closed"}
    public.update({"DL-4": {"component": "ETL", "labels": []},
                   "DL-5": {"component": "사용자 VoC", "labels": ["mock"]}})
    with_zero_and_voc = checks._native_progress_expectations(bundle, {"issues": rows,
        "epics": {"DL-100": {"sp_done": 2, "sp_total": 3, "percent": 66.7}}}, ["DL-100"], INPUTS)
    assert with_zero_and_voc["epics"]["DL-100"] == {**NATIVE, "countedIssues": 3, "excludedIssues": 2}


def test_lifecycle_evidence_and_review_expectations_do_not_add_unrequested_scalar_measurements():
    row = {"keys": [], "requiredSources": [], "measurements": [{"metric": name, "value": value}
        for name, value in (("active_tickets", 108), ("request_tickets", 72), ("request_tickets", 48))]}
    lifecycle = {"DL-1": {"completed_deliverables": ["Reviewed item"], "sources": ["jira:DL-1:comment:1"]}}
    checks._attach_lifecycle_expectations(row, lifecycle)
    assert [item["value"] for item in row["measurements"]] == [108, 72, 48]
    assert row["lifecycleExpectations"] == lifecycle
    assert row["requiredSources"] == ["jira:DL-1:comment:1"] and row["keys"] == ["DL-1"]
