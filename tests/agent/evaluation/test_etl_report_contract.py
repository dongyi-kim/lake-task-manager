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


def test_only_optional_progress_requires_its_candidate_status(report_case):
    calls = deepcopy(report_case[3])
    del calls[2]["result"]["data"]["status"]
    assert evaluate(report_case, change=add_context(report_case, progress=False), calls=calls)["pass"]
    assert "required_typed_inputs_not_observed" in evaluate(
        report_case, change=add_context(report_case), calls=calls)["failures"]


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
    entry = next(item for item in wanted["reportCatalog"]["progress"]
                 if item["row"]["key"] == "DL-21744")
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


@pytest.fixture
def current_context_case(report_case):
    bundle, original, _, calls = report_case
    author = _Author(bundle.spec)
    author.field("DL-21743", "development", checks._at("2026-10-28"), "status", "In Progress", "Closed")
    report = next(event for event in bundle.oracle.events if event.kind == "development_report")
    payload = deepcopy(report.payload)
    body = "<h1>ETL 개발 주간보고</h1><h2>1. 개발 경과</h2><p>계약 지문 비교기 후속 검토를 계획했다.</p>"
    payload.update(version=2, text=body, items=[deepcopy(payload["items"][0])])
    payload["items"][0]["claims"][0].update(stage="plan", outcome=None, occurred_at=None, period_bound=False)
    author.document("public-report", checks._at("2026-11-05"), body,
                    kind="development_report", version=2, extra=payload)
    events = (*bundle.oracle.events, *author.events)
    world, sources, _ = project(events, bundle.spec)
    bundle = SimpleNamespace(**{**vars(bundle), "world": world, "public_sources": sources,
                                "oracle": EventOracle(events, bundle.spec.observed_until)})
    wanted = {name: deepcopy(original[name]) for name in (
        "measurements", "denominators", "progress", "reportedTotal", "mustDiscloseUncertainty", "distinctions")}
    wanted.update(keys=[], requiredSources=[], requiredTicketInputs={}, requiredFieldFacts=[],
                  reportFindings=[], reportProgress=[])
    checks._attach_reports(wanted, bundle, checks._at("2026-10-26"), keys=["DL-21743"],
                          view="as-of-2026-10-26", revisions=True)
    before = {name: deepcopy(wanted[name]) for name in (
        "keys", "reportFindings", "reportProgress", "requiredSources", "requiredTicketInputs", "requiredFieldFacts")}
    snapshot = bundle.oracle.development(bundle.spec.observed_until)
    checks._optional_current_reports(wanted, bundle, snapshot, keys=["DL-21743"], revisions=True)
    assert all(wanted[name] == value for name, value in before.items())  # Catalog only.
    answer = {name: deepcopy(wanted[name]) for name in (
        "measurements", "denominators", "progress", "reportedTotal", "reportFindings", "reportProgress")}
    answer.update(resultKeys=sorted(set(wanted["keys"])), sources=[{
        "kind": "ticket", "id": "DL-21743", "url": "http://jira/browse/DL-21743"}])
    calls = deepcopy(calls)
    calls[1]["result"]["data"]["status"] = "Closed"
    page_id = calls[0]["result"]["data"]["id"]
    calls.append({"name": "read_confluence_document", "result": {"ok": True, "data": {
        "id": page_id, "version": {"number": 2}, "offset": 0, "totalCharacters": len(body), "content": body}}})
    calls.append({"name": "get_comments", "arguments": {"key": "DL-21743"}, "result": {"ok": True,
        "data": {"key": "DL-21743", "comments": world.issues["DL-21743"]["comments"]}}})
    return bundle, wanted, answer, calls


def add_current_context(case):
    def change(answer):
        catalog = case[1]["reportCatalog"]
        row = next(item["row"] for item in catalog["findings"] if item["row"]["version"] == 2)
        answer["reportFindings"].append(deepcopy(row))
        row = next(item["row"] for item in catalog["progress"] if item["row"]["view"] == "current")
        answer["reportProgress"].append(deepcopy(row))
    return change


def test_current_correction_context_is_optional_and_preserves_required_period_rows(current_context_case):
    assert evaluate(current_context_case)["pass"]
    result = evaluate(current_context_case, change=add_current_context(current_context_case))
    assert result["pass"], result
    current = next(item["row"] for item in current_context_case[1]["reportCatalog"]["progress"]
                   if item["row"]["view"] == "current")
    assert current["jiraStatus"] == "Closed" and current["planned"] == ["계약 지문 비교기"]
    assert current_context_case[1]["reportProgress"][0]["planned"] == []


def test_optional_catalog_reuses_supplied_snapshot_and_does_not_duplicate_rows(current_context_case):
    bundle, wanted, _, _ = current_context_case
    snapshot = bundle.oracle.development(bundle.spec.observed_until)
    class NoRebuild:
        events = bundle.oracle.events
        def development(self, *args, **kwargs):
            raise AssertionError("The supplied current snapshot must be reused")
    cached = SimpleNamespace(**{**vars(bundle), "oracle": NoRebuild()})
    wanted = deepcopy(wanted)
    before = deepcopy(wanted["reportCatalog"])
    checks._optional_current_reports(wanted, cached, snapshot, keys=["DL-21743"])
    checks._optional_current_reports(wanted, cached, snapshot, keys=["DL-21743"])
    assert wanted["reportCatalog"] == before


def test_optional_current_rows_require_current_source_and_status_only_when_emitted(current_context_case):
    calls = deepcopy(current_context_case[3])
    calls = [call for call in calls if not (call["name"] == "read_confluence_document"
        and call["result"]["data"]["version"]["number"] == 2)]
    del calls[1]["result"]["data"]["status"]
    assert evaluate(current_context_case, calls=calls)["pass"]
    result = evaluate(current_context_case, calls=calls, change=add_current_context(current_context_case))
    assert "required_evidence_not_observed" in result["failures"]
    assert "required_typed_inputs_not_observed" in result["failures"]


@pytest.mark.parametrize("damage", ["date", "view", "status", "count", "replacePeriod", "unrelatedTask"])
def test_current_context_does_not_relax_date_view_status_count_period_or_task_scope(current_context_case, damage):
    def change(answer):
        add_current_context(current_context_case)(answer)
        if damage == "date": answer["reportFindings"][-1]["occurredAt"] = "2026-10-23"
        elif damage == "view": answer["reportProgress"][-1]["view"] = "known-2026-11-01"
        elif damage == "status": answer["reportProgress"][-1]["jiraStatus"] = "In Progress"
        elif damage == "count": answer["reportProgress"][-1]["acceptedEvents"] = 1
        elif damage == "replacePeriod": answer["reportProgress"].pop(0)
        elif damage == "unrelatedTask": answer["reportProgress"][-1]["key"] = "DL-21744"
    assert not evaluate(current_context_case, change=change)["pass"]


@pytest.fixture
def activity_plan_case(report_case):
    bundle, original, _, calls = report_case
    events = deepcopy(bundle.oracle.events)
    event = next(event for event in events if event.kind == "development_report")
    item = event.payload["items"][1]
    item["text"] = "역순 재시도 시험을 수용했다. 인수 회의는 다음 주 예정이다."
    item["claims"].append({"deliverable": "인수 회의", "stage": "plan", "occurred_at": None,
                           "artifact": None, "outcome": None})
    event.payload["text"] = event.payload["text"].replace("계약 지문 비교기 외부 검토는 별도 안건이다.",
                                                          "인수 회의는 다음 주 예정이다.")
    world, sources, _ = project(events, bundle.spec)
    bundle = SimpleNamespace(**{**vars(bundle), "world": world, "public_sources": sources,
                                "oracle": EventOracle(events, bundle.spec.observed_until)})
    wanted = {name: deepcopy(original[name]) for name in (
        "measurements", "denominators", "progress", "reportedTotal", "mustDiscloseUncertainty", "distinctions")}
    wanted.update(keys=[], requiredSources=[], requiredTicketInputs={}, requiredFieldFacts=[],
                  reportFindings=[], reportProgress=[])
    checks._attach_reports(wanted, bundle, bundle.spec.observed_until, keys=["DL-21744"], view="current")
    answer = {name: deepcopy(wanted[name]) for name in (
        "measurements", "denominators", "progress", "reportedTotal", "reportFindings", "reportProgress")}
    answer["reportFindings"] = [row for row in answer["reportFindings"] if row["deliverable"] != "인수 회의"]
    answer.update(resultKeys=sorted(set(wanted["keys"])), sources=[{
        "kind": "ticket", "id": "DL-21744", "url": "http://jira/browse/DL-21744"}])
    calls = deepcopy(calls)
    calls[0]["result"]["data"].update(content=event.payload["text"], totalCharacters=len(event.payload["text"]))
    return bundle, wanted, answer, calls


def test_scope_only_activity_plan_is_optional_but_named_deliverable_stays_required(activity_plan_case):
    result = evaluate(activity_plan_case)
    assert result["pass"], result
    assert not any(row["deliverable"] == "인수 회의" for row in activity_plan_case[1]["reportFindings"])
    assert activity_plan_case[1]["reportExpectations"]["optionalActivityPlans"][0]["deliverable"] == "인수 회의"
    assert not evaluate(activity_plan_case, change=lambda answer: answer["reportFindings"].clear())["pass"]


@pytest.mark.parametrize("damage", [None, "date", "outcome", "identity"])
def test_optional_activity_plan_retains_exact_grounded_claim_gates(activity_plan_case, damage):
    def change(answer):
        entry = next(item for item in activity_plan_case[1]["reportCatalog"]["findings"]
                     if item["row"]["deliverable"] == "인수 회의")
        row = deepcopy(entry["row"])
        if damage == "date": row["occurredAt"] = "2026-10-27"
        elif damage == "outcome": row["outcome"] = "accepted"
        elif damage == "identity": row["candidateKeys"] = ["DL-21743"]
        answer["reportFindings"].append(row)
    assert evaluate(activity_plan_case, change=change)["pass"] is (damage is None)


@pytest.fixture
def empty_period_case(report_case):
    bundle, wanted, answer, calls = deepcopy(report_case)
    author = _Author(bundle.spec)
    author.issue("DL-21748", "development", checks._at("2026-10-01"), "Integration", "Public integration scope", module="ETL")
    author.field("DL-21748", "development", checks._at("2026-10-31"), "status", "In Progress", "Resolved")
    events = (*bundle.oracle.events, *author.events)
    world, sources, _ = project(events, bundle.spec)
    bundle = SimpleNamespace(**{**vars(bundle), "world": world, "public_sources": sources,
                                "oracle": EventOracle(events, bundle.spec.observed_until)})
    period = checks._at("2026-11-01")
    snapshot = bundle.oracle.development(period)
    checks._optional_period_empty_reports(wanted, bundle, snapshot, keys=["DL-21748"],
                                         at=period, knowledge_at=None, view="2026-10-end")
    calls.append({"name": "get_ticket", "result": {"ok": True, "data": {"key": "DL-21748",
        "status": "Resolved", "description": world.issues["DL-21748"]["description"]}}})
    calls.append({"name": "get_comments", "arguments": {"key": "DL-21748"}, "result": {
        "ok": True, "data": {"key": "DL-21748", "comments": world.issues["DL-21748"]["comments"]}}})
    return bundle, wanted, answer, calls


def add_empty_period(case):
    def change(answer):
        row = next(item["row"] for item in case[1]["reportCatalog"]["progress"] if item["row"]["key"] == "DL-21748")
        answer["reportProgress"].append(deepcopy(row))
        answer["resultKeys"] = sorted(set(answer["resultKeys"]) | {"DL-21748"})
    return change


def test_selected_relevant_empty_period_context_is_optional_with_reported_zero(empty_period_case):
    assert evaluate(empty_period_case)["pass"]
    result = evaluate(empty_period_case, change=add_empty_period(empty_period_case))
    assert result["pass"], result
    assert not any(row["key"] == "DL-21748" for row in empty_period_case[1]["reportProgress"])


def test_empty_period_context_needs_own_public_description_and_historical_status(empty_period_case):
    calls = deepcopy(empty_period_case[3][:-1])
    del calls[-1]["result"]["data"]["description"]
    assert evaluate(empty_period_case, calls=calls)["pass"]
    result = evaluate(empty_period_case, calls=calls, change=add_empty_period(empty_period_case))
    assert "required_evidence_not_observed" in result["failures"]
    assert "required_historical_fact_not_observed" in result["failures"]


@pytest.mark.parametrize("field,value", [("acceptedEvents", 1), ("acceptedEvents", None),
    ("jiraStatus", "In Progress"), ("view", "current"), ("testAccepted", ["Invented completion"])])
def test_empty_period_context_is_not_actual_completion_or_an_arbitrary_view(empty_period_case, field, value):
    def change(answer):
        add_empty_period(empty_period_case)(answer)
        answer["reportProgress"][-1][field] = value
    assert not evaluate(empty_period_case, change=change)["pass"]


@pytest.mark.parametrize("damage", ["unselected", "noProfile", "ambiguous", "nonempty"])
def test_empty_period_context_cannot_discard_scope_or_ambiguous_prior_claims(empty_period_case, damage):
    bundle, wanted, _, _ = deepcopy(empty_period_case)
    wanted["reportCatalog"]["progress"] = [entry for entry in wanted["reportCatalog"]["progress"]
                                            if entry["row"]["key"] != "DL-21748"]
    period = checks._at("2026-11-01")
    snapshot = bundle.oracle.development(period)
    if damage == "noProfile":
        event = next(event for event in bundle.oracle.events if event.issue_key == "DL-21748" and event.kind == "issue")
        event.payload["report_context"] = None
    elif damage == "ambiguous":
        snapshot["report_correspondence"].append({"candidate_keys": ["DL-21748", "DL-21743"], "status": "ambiguous"})
    elif damage == "nonempty":
        snapshot["issues"]["DL-21748"]["progress"]["reported_implementation"] = ["Visible work"]
    checks._optional_period_empty_reports(wanted, bundle, snapshot,
        keys=[] if damage == "unselected" else ["DL-21748"], at=period, knowledge_at=None, view="2026-10-end")
    assert not any(entry["row"]["key"] == "DL-21748" for entry in wanted["reportCatalog"]["progress"])


def test_empty_period_catalog_reuses_snapshot_and_is_idempotent(empty_period_case):
    bundle, wanted, _, _ = deepcopy(empty_period_case)
    period = checks._at("2026-11-01")
    snapshot = bundle.oracle.development(period)
    before = deepcopy(wanted)
    checks._optional_period_empty_reports(wanted, bundle, snapshot, keys=["DL-21748", "DL-21748"],
                                         at=period, knowledge_at=None, view="2026-10-end")
    assert wanted == before


def test_publication_date_does_not_supply_an_undated_claim_occurrence(current_context_case):
    def change(answer):
        add_current_context(current_context_case)(answer)
        answer["reportFindings"][-1]["occurredAt"] = "2026-11-05"
    assert not evaluate(current_context_case, change=change)["pass"]


@pytest.fixture
def historical_correspondence_case(report_case):
    bundle, wanted, answer, calls = deepcopy(report_case)
    period = checks._at("2026-11-01")
    snapshot = bundle.oracle.development(period)
    matches = [match for match in snapshot["report_correspondence"] if "DL-21744" in match["candidate_keys"]]
    optional = {}
    checks._catalog_reports(optional, bundle, snapshot, matches, period, None, "2026-10-end")
    # Replace just this optional candidate's catalog entries with a historical
    # view. The required Task's current rows and proof stay unchanged.
    for kind in ("findings", "progress"):
        wanted["reportCatalog"][kind] = [entry for entry in wanted["reportCatalog"][kind]
            if ("DL-21744" not in entry["row"].get("candidateKeys", []) and entry["row"].get("key") != "DL-21744")]
        wanted["reportCatalog"][kind] += optional["reportCatalog"][kind]
    answer["reportFindings"].append(deepcopy(optional["reportCatalog"]["findings"][0]["row"]))
    answer["resultKeys"] = sorted(set(answer["resultKeys"]) | {"DL-21744"})
    return bundle, wanted, answer, calls


def test_correspondence_only_finding_does_not_require_unasserted_historical_status(historical_correspondence_case):
    result = evaluate(historical_correspondence_case)
    assert result["pass"], result


def test_same_candidate_progress_still_requires_historical_status(historical_correspondence_case):
    def change(answer):
        entry = next(entry for entry in historical_correspondence_case[1]["reportCatalog"]["progress"]
                     if entry["row"]["key"] == "DL-21744")
        answer["reportProgress"].append(deepcopy(entry["row"]))
    result = evaluate(historical_correspondence_case, change=change)
    assert "required_historical_fact_not_observed" in result["failures"]


@pytest.mark.parametrize("damage", ["document", "description", "claim"])
def test_correspondence_only_finding_retains_literal_and_candidate_proof(historical_correspondence_case, damage):
    calls = deepcopy(historical_correspondence_case[3])
    if damage == "document":
        calls = calls[1:]
    elif damage == "description":
        del calls[2]["result"]["data"]["description"]
    result = evaluate(historical_correspondence_case, calls=calls,
        change=(lambda answer: answer["reportFindings"][-1].update(occurredAt="2026-10-24")) if damage == "claim" else None)
    assert not result["pass"]
