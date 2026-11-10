"""Small public reports exercise host gates without a model/server/year fixture."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tools.codex_mcp_eval.etl import checks
from tools.codex_mcp_eval.etl.oracle import EventOracle
from tools.codex_mcp_eval.etl.projection import project
from tools.codex_mcp_eval.etl.world import YearWorldSpec, _Author


@pytest.fixture
def report_case():
    spec = YearWorldSpec()
    author = _Author(spec)
    when = checks._at("2026-10-01")
    for key in ("DL-21743", "DL-21744"):
        author.issue(key, "development", when, key, "Public task evidence", module="ETL")
    context = {"module": "ETL", "environment": "prod", "release": "R17",
               "participants": [f"etl.user{i:02d}" for i in range(8)]}
    claims = [("계약 지문 비교기", "원천 계약 지문"), ("역순 재시도 시험", "시간창 역전 처리")]
    items = [{"item_id": str(index), "context": {"scopes": [scope]}, "claims": [{
        "deliverable": name, "stage": "test_acceptance", "occurred_at": checks._at("2026-10-23"),
        "artifact": None, "outcome": "accepted"}]} for index, (name, scope) in enumerate(claims, 1)]
    body = ("<h1>ETL 개발 주간보고</h1><h2>1. 개발 경과</h2><p>계약 지문 비교기 회귀 시험을 수용했다.</p>"
            "<h2>2. 개발 경과</h2><p>역순 재시도 시험을 수용했다. 계약 지문 비교기 외부 검토는 별도 안건이다.</p>")
    author.document("public-report", checks._at("2026-10-27"), body, kind="development_report", extra={
        "title": "ETL 개발 주간보고", "period_start": checks._at("2026-10-19"),
        "period_end": checks._at("2026-10-26"), "report_context": context, "items": items})
    world, sources, _ = project(tuple(author.events), spec)
    bundle = SimpleNamespace(spec=spec, world=world, public_sources=sources,
                             oracle=EventOracle(tuple(author.events), spec.observed_until))
    expected = {"keys": [], "measurements": [], "denominators": [], "requiredSources": [],
        "requiredTicketInputs": {}, "requiredFieldFacts": [], "reportFindings": [], "reportProgress": [],
        "mustDiscloseUncertainty": False, "distinctions": [], "progress": None, "reportedTotal": None}
    checks._attach_reports(expected, bundle, spec.observed_until, keys=["DL-21743"], view="current")
    expected["keys"] = sorted(set(expected["keys"]))
    source = next(row for row in sources.values() if row["kind"] == "confluence_revision")
    read = {"name": "read_confluence_document", "result": {"ok": True, "data": {
        "id": source["id"], "version": {"number": 1}, "content": body,
        "offset": 0, "totalCharacters": len(body)}}}
    tickets = [{"name": "get_ticket", "result": {"ok": True, "data": {
        "key": key, "url": "http://jira/browse/" + key,
        "description": world.issues[key]["description"], "status": "In Progress"}}}
        for key in ("DL-21743", "DL-21744")]
    search = {"name": "search_confluence_documents", "result": {"ok": True, "data": {
        "canonicalCql": "space=DL", "startAt": 0, "returned": 1, "total": 1,
        "documents": [{"id": source["id"]}], "hasMore": False, "nextStartAt": None}}}
    answer = {name: deepcopy(expected[name]) for name in (
        "measurements", "denominators", "reportFindings", "reportProgress", "progress", "reportedTotal")}
    answer.update(resultKeys=expected["keys"], sources=[{
        "kind": "ticket", "id": "DL-21743", "url": "http://jira/browse/DL-21743"}])
    return bundle, expected, answer, [read, *tickets, search]


def evaluate(case, *, change=None, calls=None):
    bundle, expected, answer, original = case
    answer = deepcopy(answer)
    if change:
        change(answer)
    return checks.check({"id": "public"}, {"ok": True, "reply": "근거와 남은 일을 구분합니다.",
        "structuredAnswer": answer}, original if calls is None else calls,
        {"cases": {"public": expected}, "publicSources": bundle.public_sources, "ticketFieldIds": {}})


def test_same_section_literal_long_name_resolves_in_finding_and_progress(report_case):
    def change(answer):
        answer["reportFindings"][0]["deliverable"] = "계약 지문 비교기 회귀 시험"
        answer["reportProgress"][0]["testAccepted"] = ["계약 지문 비교기 회귀 시험"]
    result = evaluate(report_case, change=change)
    assert result["pass"], result


@pytest.mark.parametrize("name", ["계약 지문 비교기 외부 검토", "새 계약 지문 비교기", "원천 계약 검사기"])
def test_alias_from_other_section_unobserved_or_synonymous_does_not_pass(report_case, name):
    assert not evaluate(report_case, change=lambda answer: answer["reportFindings"][0].update(deliverable=name))["pass"]


def test_alias_needs_actual_complete_same_version_read_and_valid_finding(report_case):
    calls = deepcopy(report_case[3])
    calls[0]["result"]["data"]["version"]["number"] = 2
    assert not evaluate(report_case, calls=calls, change=lambda answer:
        answer["reportFindings"][0].update(deliverable="계약 지문 비교기 회귀 시험"))["pass"]
    assert not evaluate(report_case, change=lambda answer:
        answer["reportProgress"][0].update(testAccepted=["계약 지문 비교기 회귀 시험"]))["pass"]


@pytest.mark.parametrize("damage", ["failed", "truncated", "wrongDocument", "otherSection"])
def test_alias_requires_literal_source_not_just_read_metadata(report_case, damage):
    calls = deepcopy(report_case[3])
    read = calls[0]
    data = read["result"]["data"]
    if damage == "failed":
        read["result"]["ok"] = False
    elif damage == "truncated":
        data["content"] = data["content"][:40]
    elif damage == "wrongDocument":
        data["id"] = "other"
    else:
        data["content"] = "<h2>2. 개발 경과</h2><p>계약 지문 비교기 회귀 시험을 수용했다.</p>"
        data["totalCharacters"] = len(data["content"])
    assert not evaluate(report_case, calls=calls, change=lambda answer:
        answer["reportFindings"][0].update(deliverable="계약 지문 비교기 회귀 시험"))["pass"]


def test_actual_contiguous_chunked_read_can_ground_literal(report_case):
    calls = deepcopy(report_case[3])
    data = calls[0]["result"]["data"]
    text = data["content"]
    data["content"] = text[:53]
    second = deepcopy(calls[0])
    second["result"]["data"].update(offset=53, content=text[53:])
    calls.append(second)
    result = evaluate(report_case, calls=calls, change=lambda answer:
        answer["reportFindings"][0].update(deliverable="계약 지문 비교기 회귀 시험"))
    assert result["pass"], result


def test_text_read_and_partial_storage_read_do_not_mix_revision_chunks(report_case):
    from tools.codex_mcp_eval.etl.report_checks import public_text
    calls = deepcopy(report_case[3])
    data = calls[0]["result"]["data"]
    storage = deepcopy(calls[0])
    storage["result"]["data"].update(format="storage", content=data["content"][:20])
    text = public_text(data["content"])
    data.update(format="text", content=text, totalCharacters=len(text))
    calls.append(storage)
    assert evaluate(report_case, calls=calls, change=lambda answer:
        answer["reportFindings"][0].update(deliverable="계약 지문 비교기 회귀 시험"))["pass"]


def test_short_and_long_name_cannot_duplicate_one_claim_or_stage(report_case):
    def duplicate_claim(answer):
        answer["reportFindings"].append({**answer["reportFindings"][0], "deliverable": "계약 지문 비교기 회귀 시험"})
    def duplicate_stage(answer):
        answer["reportFindings"][0]["deliverable"] = "계약 지문 비교기 회귀 시험"
        answer["reportProgress"][0]["testAccepted"] += ["계약 지문 비교기 회귀 시험"]
    assert not evaluate(report_case, change=duplicate_claim)["pass"]
    assert not evaluate(report_case, change=duplicate_stage)["pass"]


@pytest.mark.parametrize("field,value", [("stage", "final_acceptance"), ("outcome", "pending"),
    ("occurredAt", None), ("artifact", "invented"), ("candidateKeys", ["DL-21744"]), ("inScope", False)])
def test_alias_never_relaxes_typed_claim_gates(report_case, field, value):
    def change(answer):
        answer["reportFindings"][0].update(deliverable="계약 지문 비교기 회귀 시험", **{field: value})
    assert not evaluate(report_case, change=change)["pass"]


def add_context(case, *, progress=True):
    def change(answer):
        catalog = case[1]["reportCatalog"]
        answer["reportFindings"].append(deepcopy(next(item["row"] for item in catalog["findings"]
            if item["row"]["candidateKeys"] == ["DL-21744"])))
        if progress:
            answer["reportProgress"].append(deepcopy(next(item["row"] for item in catalog["progress"]
                if item["row"]["key"] == "DL-21744")))
    return change


def test_extra_independently_known_context_rows_are_validated_and_require_own_evidence(report_case):
    result = evaluate(report_case, change=add_context(report_case))
    assert result["pass"], result
    unread = [call for call in report_case[3] if (call["result"].get("data") or {}).get("key") != "DL-21744"]
    result = evaluate(report_case, change=add_context(report_case), calls=unread)
    assert "required_evidence_not_observed" in result["failures"]
    assert "required_typed_inputs_not_observed" in result["failures"]


def test_optional_finding_alone_also_requires_its_candidate_status(report_case):
    calls = deepcopy(report_case[3])
    del calls[2]["result"]["data"]["status"]
    assert "required_typed_inputs_not_observed" in evaluate(
        report_case, change=add_context(report_case, progress=False), calls=calls)["failures"]


def test_unknown_extra_or_missing_required_claim_is_not_ignored(report_case):
    assert not evaluate(report_case, change=lambda answer: answer["reportFindings"].clear())["pass"]
    def fabricated(answer):
        add_context(report_case)(answer)
        answer["reportFindings"][-1]["occurredAt"] = "2026-10-24"
    assert not evaluate(report_case, change=fabricated)["pass"]


def test_extra_progress_requires_allowed_view_and_exact_reported_count(report_case):
    def change(answer):
        add_context(report_case)(answer)
        answer["reportProgress"][-1]["view"] = "2026-10-end"
    assert not evaluate(report_case, change=change)["pass"]


def test_catalog_is_derived_from_requested_views_and_revision_policy(report_case):
    bundle, expected, _, _ = report_case
    author = _Author(bundle.spec)
    source = next(event for event in bundle.oracle.events if event.kind == "development_report")
    future_payload = deepcopy(source.payload)
    future_payload.update(version=2, text="<h2>1. 개발 경과</h2><p>계약 지문 비교기 승인 철회.</p>")
    future_payload["items"] = [deepcopy(source.payload["items"][0])]
    future_payload["items"][0]["claims"][0].update(outcome="pending")
    author.document("public-report", checks._at("2026-11-05"), future_payload.pop("text"),
                    kind="development_report", version=2, extra=future_payload)
    events = (*bundle.oracle.events, *author.events)
    world, sources, _ = project(events, bundle.spec)
    revised = SimpleNamespace(**{**vars(bundle), "world": world, "public_sources": sources,
                                "oracle": EventOracle(events, bundle.spec.observed_until)})
    def wanted():
        return {**deepcopy(expected), "reportCatalog": {"findings": [], "progress": []}}
    then = wanted()
    cutoff = checks._at("2026-11-01")
    checks._attach_reports(then, revised, cutoff, knowledge_at=cutoff, keys=["DL-21743"], view="known-2026-11-01")
    assert {item["row"]["version"] for item in then["reportCatalog"]["findings"]} == {1}
    current = wanted()
    checks._attach_reports(current, revised, revised.spec.observed_until, keys=["DL-21743"], view="current")
    assert {item["row"]["version"] for item in current["reportCatalog"]["findings"]} == {2}
    revisions = wanted()
    checks._attach_reports(revisions, revised, revised.spec.observed_until, keys=["DL-21743"],
                          view="current", revisions=True)
    assert {item["row"]["version"] for item in revisions["reportCatalog"]["findings"]} == {1, 2}


def test_optional_historical_context_carries_sourced_status_transition_proof(report_case):
    bundle, expected, _, _ = report_case
    author = _Author(bundle.spec)
    author.field("DL-21744", "development", checks._at("2026-10-28"), "status", "In Progress", "Resolved")
    events = (*bundle.oracle.events, *author.events)
    world, sources, _ = project(events, bundle.spec)
    revised = SimpleNamespace(**{**vars(bundle), "world": world, "public_sources": sources,
                                "oracle": EventOracle(events, bundle.spec.observed_until)})
    wanted = {**deepcopy(expected), "reportCatalog": {"findings": [], "progress": []}}
    checks._attach_reports(wanted, revised, checks._at("2026-11-01"), keys=["DL-21743"], view="2026-10-end")
    entry = next(item for item in wanted["reportCatalog"]["findings"]
                 if item["row"]["candidateKeys"] == ["DL-21744"])
    fact, = entry["requiredFieldFacts"]
    assert fact["key"] == "DL-21744" and fact["valueAtView"] == "Resolved"
    assert fact["occurredAt"] == checks._at("2026-10-28").isoformat()
    assert fact["commentSources"] == list(author.events[0].sources)
    assert not checks.field_facts_observed(report_case[3], entry["requiredFieldFacts"], sources)
    comment = world.issues["DL-21744"]["comments"][0]
    read = {"name": "get_comments", "arguments": {"key": "DL-21744"}, "result": {
        "ok": True, "data": {"key": "DL-21744", "comments": [comment]}}}
    assert checks.field_facts_observed([*report_case[3], read], entry["requiredFieldFacts"], sources)


@pytest.mark.parametrize("field,value", [("jiraStatus", "Closed"), ("view", "known-2026-11-01"),
    ("acceptedEvents", 0), ("testAccepted", []), ("finalAccepted", ["계약 지문 비교기"])])
def test_progress_retains_exact_status_view_stages_and_reported_event_count(report_case, field, value):
    assert not evaluate(report_case, change=lambda answer: answer["reportProgress"][0].update(**{field: value}))["pass"]


def test_unknown_multiplicity_is_not_zero_and_zero_reports_is_not_actual_completion():
    unknown = {"key": "DL-1", "view": "current", "acceptedEvents": None}
    assert not checks._report_rows_match([{**unknown, "acceptedEvents": 0}], [unknown])
    zero = {**unknown, "acceptedEvents": 0}
    assert checks._report_rows_match([zero], [zero])
