"""Small public-evidence regressions; no year world, model, server or old regrade."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from tools.codex_mcp_eval.etl import checks
from tools.codex_mcp_eval.etl.oracle import EventOracle
from tools.codex_mcp_eval.etl.projection import project
from tools.codex_mcp_eval.etl.world import YearWorldSpec, _Author, _development_reports


def envelope(data):
    return {"ok": True, "partial": False, "missing": 0, "data": data, "cache": {
        "available": True, "state": "fresh", "invalidated": False, "compliant": True,
        "upstreamValidated": True, "fetchedAt": datetime.fromisoformat("2026-11-10T12:00:00+09:00").timestamp()}}


def status_fact(**changes):
    return {"key": "DL-1", "field": "status", "from": "In Progress", "to": "Resolved",
        "occurredAt": "2026-10-31T16:00:00+09:00", "recordedAt": "2026-10-31T16:00:00+09:00",
        "commentSources": [], "viewAt": "2026-11-01T00:00:00+09:00",
        "knowledgeAt": "2026-11-10T23:59:59+09:00", "valueAtView": "Resolved",
        "relation": "after-transition", **changes}


def status_call(**changes):
    fields = {"status": {"name": "Resolved"}, "created": "2026-10-01T00:00:00+09:00",
              "updated": "2026-10-31T16:00:00+09:00", "resolutiondate": "2026-10-31T16:00:00+09:00", **changes}
    return {"name": "get_tickets", "arguments": {"keys": ["DL-1"]},
            "result": envelope({"complete": True, "issues": [{"key": "DL-1", "fields": fields}]})}


def test_fresh_complete_status_unchanged_since_before_cutoff_is_equivalent_proof():
    assert checks.field_facts_observed([status_call()], [status_fact()], {})
    assert checks.field_facts_observed([status_call(updated="2026-10-31T07:00:00+00:00")], [status_fact()], {})
    unchanged = status_fact(unchanged=True)
    assert checks.field_facts_observed([status_call()], [unchanged], {})


@pytest.mark.parametrize("changes", [
    {"updated": None}, {"updated": "2026-10-31"}, {"updated": "2026-10-31T16:00:00"},
    {"updated": "2026-11-01T00:00:01+09:00"}, {"updated": "2026-10-31T16:00:00-09:00"},
    {"created": "2026-11-02T00:00:00+09:00"}, {"created": None},
    {"status": {"name": "Reopened"}, "updated": "2026-11-03T00:00:00+09:00"},
    {"status": {"name": "Closed"}},
])
def test_current_status_equivalence_never_uses_resolution_date_alone_or_future_fields(changes):
    assert not checks.field_facts_observed([status_call(**changes)], [status_fact()], {})


def test_status_equivalence_respects_exact_cutoff_and_observation_boundary():
    assert checks.field_facts_observed([status_call(updated="2026-11-01T00:00:00+09:00")], [status_fact()], {})
    call = status_call()
    call["result"]["cache"]["fetchedAt"] = datetime.fromisoformat("2026-11-01T00:00:00+09:00").timestamp()
    assert not checks.field_facts_observed([call], [status_fact()], {})


@pytest.mark.parametrize("damage", ["partial", "incomplete", "stale", "unvalidated", "noncompliant",
                                    "missingObservation", "futureObservation", "beforeCutoff", "wrongKey", "quoted"])
def test_status_equivalence_requires_actual_complete_fresh_typed_observation(damage):
    call = status_call()
    result, data = call["result"], call["result"]["data"]
    if damage == "partial": result["partial"] = True
    elif damage == "incomplete": data["complete"] = False
    elif damage == "stale": result["cache"]["state"] = "stale"
    elif damage == "unvalidated": result["cache"]["upstreamValidated"] = False
    elif damage == "noncompliant": result["cache"]["compliant"] = False
    elif damage == "missingObservation": result["cache"].pop("fetchedAt")
    elif damage == "futureObservation": result["cache"]["fetchedAt"] += 86400
    elif damage == "beforeCutoff": result["cache"]["fetchedAt"] -= 12 * 86400
    elif damage == "wrongKey": data["issues"][0]["key"] = "DL-2"
    else: data["quoted"] = data.pop("issues")
    assert not checks.field_facts_observed([call], [status_fact()], {})


def test_status_equivalence_does_not_prove_known_then_or_an_unrequested_transition():
    assert not checks.field_facts_observed([status_call()], [status_fact(knowledgeAt="2026-11-01T00:00:00+09:00")], {})
    transition = status_fact()
    for field in ("viewAt", "knowledgeAt", "valueAtView", "relation"):
        transition.pop(field)
    assert not checks.field_facts_observed([status_call()], [transition], {})


def minimal_expected():
    return {"keys": ["DL-100"], "measurements": [], "denominators": [{"scope": "WBS-1@current",
        "metric": "SP-weighted epic progress", "numerator": 200, "denominator": 3, "unit": "weighted percent"}],
        "requiredSources": [], "requiredTicketInputs": {}, "requiredFieldFacts": [],
        "reportFindings": [], "reportProgress": [], "progress": None, "reportedTotal": None}


def evaluate(expected, calls, *, modify=None, public=None):
    answer = {name: deepcopy(expected[name]) for name in (
        "measurements", "denominators", "reportFindings", "reportProgress", "progress", "reportedTotal")}
    answer.update(resultKeys=expected["keys"], sources=[])
    if modify: modify(answer)
    return checks.check({"id": "public"}, {"ok": True, "reply": "근거와 기간을 구분합니다.",
        "structuredAnswer": answer}, calls, {"cases": {"public": expected}, "ticketFieldIds": {}, "publicSources": public or {}})


def test_declared_exact_sp_metric_labels_are_aliases_but_arithmetic_and_units_remain_strict():
    expected = minimal_expected()
    observed = [{"name": "get_ticket", "result": envelope({"key": "DL-100"})}]
    def metric(answer): answer["denominators"][0]["metric"] = "SP progress"
    assert evaluate(expected, observed, modify=metric)["pass"]
    for change in ({"numerator": 201}, {"denominator": 4}, {"unit": "SP"}, {"metric": "sp_progress"},
                   {"numerator": True}, {"numerator": float("nan")}, {"denominator": None}):
        result = evaluate(expected, observed, modify=lambda answer: answer["denominators"][0].update(change))
        assert "denominator_mismatch" in result["failures"]


def test_aliases_do_not_merge_duplicate_metrics_or_change_unknown_sp_totals():
    expected = minimal_expected()
    observed = [{"name": "get_ticket", "result": envelope({"key": "DL-100"})}]
    def duplicate(answer):
        answer["denominators"].append({**answer["denominators"][0], "metric": "SP progress"})
    assert "denominator_mismatch" in evaluate(expected, observed, modify=duplicate)["failures"]
    expected["denominators"][0].update(metric="SP progress", unit="SP", numerator=None)
    assert evaluate(expected, observed, modify=lambda answer:
        answer["denominators"][0].update(metric="SP-weighted epic progress"))["pass"]
    assert "denominator_mismatch" in evaluate(expected, observed, modify=lambda answer:
        answer["denominators"][0].update(numerator=0))["failures"]


def mapping_case():
    expected = minimal_expected()
    expected["denominators"] = []
    body = "ETL-1: DL-100 가중치 3, DL-101 가중치 1. SP 가중 진척률이며 Jira 부모 계층이 아니다."
    expected["mappingIdentityEvidence"] = [{"source": "confluence:123:version:1", "documentId": "123",
        "version": 1, "wbs": "ETL-1", "epicKeys": ["DL-100", "DL-101"]}]
    source = {"kind": "confluence_revision", "id": "123", "version": 1, "text": body}
    call = {"name": "read_confluence_document", "result": envelope({"id": "123", "version": {"number": 1},
            "content": body, "offset": 0, "totalCharacters": len(body)})}
    return expected, [call], {"confluence:123:version:1": source}


def test_designated_complete_mapping_revision_proves_only_its_literal_epic_identity():
    expected, calls, public = mapping_case()
    result = evaluate(expected, calls, public=public, modify=lambda answer: answer.update(resultKeys=["DL-100", "DL-101"]))
    assert result["pass"], result
    expected["requiredTicketInputs"] = {"DL-101": {"status": "Resolved"}}
    assert "required_typed_inputs_not_observed" in evaluate(expected, calls, public=public,
        modify=lambda answer: answer.update(resultKeys=["DL-100", "DL-101"]))["failures"]


@pytest.mark.parametrize("damage", ["wrongVersion", "wrongDocument", "partial", "truncated", "otherWbs", "missingEpic", "arbitraryBody"])
def test_mapping_identity_does_not_scan_unrelated_or_incomplete_documents(damage):
    expected, calls, public = mapping_case()
    data = calls[0]["result"]["data"]
    if damage == "wrongVersion": data["version"]["number"] = 2
    elif damage == "wrongDocument": data["id"] = "124"
    elif damage == "partial": calls[0]["result"]["partial"] = True
    elif damage == "truncated": data["content"] = data["content"][:20]
    elif damage == "otherWbs": data["content"] = data["content"].replace("ETL-1", "OTHER-1")
    elif damage == "missingEpic": data["content"] = data["content"].replace("DL-101", "DL-999")
    else: expected.pop("mappingIdentityEvidence")
    assert "result_scope_mismatch" in evaluate(expected, calls, public=public,
        modify=lambda answer: answer.update(resultKeys=["DL-100", "DL-101"]))["failures"]


def test_mapping_complete_valid_read_does_not_crash_on_an_additional_malformed_record():
    expected, calls, public = mapping_case()
    damaged = deepcopy(calls[0])
    damaged["result"]["data"].pop("offset")
    result = evaluate(expected, [*calls, damaged], public=public,
        modify=lambda answer: answer.update(resultKeys=["DL-100", "DL-101"]))
    assert result["pass"], result


def test_mapping_contiguous_complete_chunks_prove_identity_without_fields():
    expected, calls, public = mapping_case()
    first, second = calls[0], deepcopy(calls[0])
    body = first["result"]["data"]["content"]
    first["result"]["data"]["content"] = body[:20]
    second["result"]["data"].update(offset=20, content=body[20:])
    assert evaluate(expected, [first, second], public=public,
        modify=lambda answer: answer.update(resultKeys=["DL-100", "DL-101"]))["pass"]


def test_monthly_inventory_does_not_require_excluded_base_but_keeps_activity_and_report_claims():
    spec, author = YearWorldSpec(), _Author(YearWorldSpec())
    for n in range(5):
        author.issue(f"DL-{20000+n}", "development_epics", spec.start, "Epic", "Public", kind="Epic")
    keys = [21000, 21001, 21002, 21010, 21740, 21742, 21743, 21744, 21745, 21747, 21748, 21749, 50003]
    for n in keys:
        author.issue(f"DL-{n}", "development", spec.start, str(n), "Public", epic="DL-20000",
                     module="Storage" if n == 50003 else "ETL", parent="DL-21000" if n == 21001 else None)
    author.field("DL-21000", "development", checks._at("2025-11-30"), "status", "In Progress", "Resolved")
    author.field("DL-21000", "development", checks._at("2026-11-03"), "status", "Resolved", "Reopened")
    for version, when in ((1, spec.start), (2, checks._at("2026-05-01"))):
        author.document("mapping", when, "ETL-1: DL-20000 가중치 1", kind="mapping", version=version,
            extra={"wbs": "ETL-1", "epics": {"DL-20000": 1}})
    author.document("other-mapping", checks._at("2026-06-01"), "OTHER-1: DL-20004 가중치 1", kind="mapping",
                    extra={"wbs": "OTHER-1", "epics": {"DL-20004": 1}})
    for version in (1, 2):
        author.document("etl-monthly-2025-11", checks._at("2025-12-01"), "Public", version=version)
    _development_reports(author)
    world, sources, _ = project(tuple(author.events), spec)
    bundle = SimpleNamespace(spec=spec, world=world, public_sources=sources,
        oracle=EventOracle(tuple(author.events), spec.observed_until),
        manifest={"development_lifecycle_coverage": {"long_wait": ["DL-21010"]}})
    expectations = checks.build_expectations(bundle)
    monthly = expectations["ETL-F03-boundary"]
    assert "DL-21000" not in monthly["keys"]
    assert "DL-21000" not in monthly["requiredTicketInputs"]
    assert not any(fact["key"] == "DL-21000" for fact in monthly["requiredFieldFacts"])
    assert "DL-21010" in monthly["requiredTicketInputs"]
    assert monthly["reportProgress"] and monthly["lifecycleExpectations"]
    comparator = next(row for row in monthly["reportProgress"] if row["key"] == "DL-21743")
    assert comparator["planned"] == []  # Real Nov5 correction cannot admit its undated new plan into October.
    answer = {"reportFindings": deepcopy(monthly["reportFindings"]), "reportProgress": deepcopy(monthly["reportProgress"])}
    next(row for row in answer["reportProgress"] if row["key"] == "DL-21743")["planned"] = ["추가 합계 검증"]
    assert not checks.report_contract(answer, monthly, [], sources)[1]
    assert "DL-21000" in expectations["ETL-F03-normal"]["keys"]  # Actually active in that month.
    mapping = expectations["ETL-F04-boundary"]["mappingIdentityEvidence"]
    assert {item["version"] for item in mapping} == {1, 2}
    assert all(item["wbs"] == "ETL-1" and item["epicKeys"] == ["DL-20000"] for item in mapping)


def test_public_in_scope_contract_is_independent_of_supersession_and_period():
    from tools.codex_mcp_eval.etl.cases import OUTPUT_SCHEMA
    description = OUTPUT_SCHEMA["properties"]["reportFindings"]["items"]["properties"]["inScope"].get("description", "")
    assert "module/environment" in description and "period" in description and "superseded" in description


@pytest.mark.parametrize("title,module,environment,wanted", [
    ("ETL 개발 월간", "Storage", "test", False), ("Unrelated title", "ETL", "prod", True),
    ("ETL 개발 일간", "ETL", "test", False)])
def test_finding_scope_uses_public_module_environment_not_title_or_late_occurrence(title, module, environment, wanted):
    claim = {"deliverable": "합계 검증", "stage": "plan", "occurred_at": None, "artifact": None, "outcome": None}
    payload = {"document": "report", "version": 2, "report_context": {"module": module, "environment": environment},
               "items": [{"item_id": "item", "context": {}, "claims": [claim]}]}
    event = SimpleNamespace(kind="development_report", payload=payload, sources=("doc",))
    bundle = SimpleNamespace(oracle=SimpleNamespace(events=[event]), public_sources={"doc": {
        "id": "123", "title": title, "text": "<h2>1. 개발 경과</h2><p>합계 검증을 계획했다.</p>"}})
    match = {"document": "report", "version": 2, "item_id": "item", "claim": claim,
             "candidate_keys": ["DL-1"], "status": "verified"}
    assert checks._public_report_findings(bundle, [match])[0]["inScope"] is wanted
