"""The new year battery has its own contracts and public, read-only evidence path."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path


def battery():
    assert importlib.util.find_spec("tools.codex_mcp_eval.etl.cases"), "ETL battery is missing"
    from tools.codex_mcp_eval.etl import cases
    return cases


@pytest.fixture(scope="module")
def bundle():
    from tools.codex_mcp_eval.etl import build_etl_world
    return build_etl_world()


def test_new_battery_has_exactly_fourteen_normal_boundary_families():
    cases = battery()
    assert cases.SUITE == "codex-mcp-etl-year"
    assert cases.BATTERY_VERSION == "3.0.2"
    assert len(cases.CASES) == 28
    assert {row["family"] for row in cases.CASES} == {f"F{i:02}" for i in range(1, 15)}
    assert all({r["variant"] for r in cases.CASES if r["family"] == family} == {"normal", "boundary"}
               for family in {r["family"] for r in cases.CASES})
    assert len({r["id"] for r in cases.CASES}) == 28
    assert all("WORK-R" not in r["id"] and "oracle" not in r["prompts"][0].lower() for r in cases.CASES)
    # Period/module discovery questions must not hand the complete cohort to the subject.
    assert all("DL-" not in r["prompts"][0] for r in cases.CASES if r["family"] in {"F01", "F02", "F03"})


def test_case_schemas_accept_requested_result_shapes_including_recovery(bundle):
    from jsonschema import Draft202012Validator, ValidationError
    from tools.codex_mcp_eval.etl.checks import build_expectations
    from tools.codex_mcp_eval.etl.recovery import recovery_cases

    cases = battery()
    expected = build_expectations(bundle)
    for case in [*cases.CASES, *recovery_cases()]:
        row = expected[case.get("sourceCaseId", case["id"])]
        schema = cases.schema_for_case(case)
        Draft202012Validator.check_schema(schema)
        answer = {"reply": "요청한 단위와 근거를 확인했습니다.", "resultKeys": row["keys"], "sources": [],
                  **{field: copy.deepcopy(row[field]) for field in (
                      "reportedTotal", "progress", "measurements", "denominators", "reportFindings", "reportProgress")}}
        validator = Draft202012Validator(schema)
        validator.validate(answer)
        if case["id"] in {"ETL-F06-normal", "ETL-F14-boundary"}:
            # Named stages/counts already live in reportProgress; an unsolicited
            # whole-task percentage must not expand the requested output contract.
            answer["measurements"].append({"stream": "development", "scope": "current", "metric": "percent",
                                           "value": None, "unit": "percent"})
            with pytest.raises(ValidationError):
                validator.validate(answer)


def test_expectations_distinguish_sp_child_ratio_and_operation_units(bundle):
    battery()
    from tools.codex_mcp_eval.etl.checks import build_expectations
    expected = build_expectations(bundle)
    assert len(expected) == 28
    sample = expected["ETL-F08-normal"]
    values = {r["metric"]: r["value"] for r in sample["measurements"]}
    assert values["request_tickets"] == 3 and values["requested_targets"] == 5
    assert values["completed_unique_tables"] == 2 and values["work_events"] == 2
    assert expected["ETL-F08-boundary"]["mustDiscloseUncertainty"] is True
    assert expected["ETL-F04-normal"]["denominators"][0]["metric"] == "SP-weighted epic progress"
    assert expected["ETL-F06-boundary"]["denominators"][0]["metric"] == "child completion count"
    assert expected["ETL-F06-normal"]["reportProgress"][0]["acceptedEvents"] == 2
    assert all(source in bundle.public_sources for row in expected.values() for source in row["requiredSources"])
    partial = expected["ETL-F06-boundary"]
    assert partial["deniedKey"] == "DL-21001" and partial["progress"] is None
    assert partial["denominators"][0]["numerator"] is None
    assert not any(source.startswith("jira:DL-21001:") for source in partial["requiredSources"])
    unknown_voc = expected["ETL-F12-boundary"]
    assert unknown_voc["knownResolvedRequests"] == 47
    assert next(row for row in unknown_voc["measurements"] if row["metric"] == "resolved_requests")["value"] is None
    lifecycle = expected["ETL-F03-boundary"]["lifecycleExpectations"]
    assert len(lifecycle) == 8 and all(row["overall_percent"] is None for row in lifecycle.values())
    assert lifecycle["DL-21013"]["known"] is False
    assert lifecycle["DL-21012"]["completed_deliverables"] == ["CDC 체크포인트 저장", "중복 방지 키 검증"]
    assert lifecycle["DL-21017"]["completed_deliverables"] == []


@pytest.fixture(scope="module")
def operation_expectations(bundle):
    from tools.codex_mcp_eval.etl.checks import build_expectations
    return build_expectations(bundle)


def test_operation_expectations_preserve_each_questions_requested_metrics(operation_expectations):
    normal = operation_expectations["ETL-F08-normal"]
    boundary = operation_expectations["ETL-F08-boundary"]
    assert [row["metric"] for row in normal["measurements"]] == [
        "request_tickets", "completed_tickets", "requested_targets", "completed_unique_tables", "work_events"]
    assert [row["metric"] for row in boundary["measurements"]] == [
        "request_tickets", "requested_targets", "completed_unique_tables", "work_events"]
    # The unrequested scalar remains available to the host for semantic/evidence review.
    assert "completed_tickets" in boundary["operations"]


def requested_load_answer(expected):
    return {"reply": "Requested units are distinct; unverified work remains unknown.",
        "resultKeys": expected["keys"], "sources": [], "reportedTotal": None, "progress": None,
        "denominators": [], "reportFindings": [], "reportProgress": [],
        "measurements": [{"stream": "operations", "scope": "load@2025-11", "metric": metric,
            "value": expected["operations"][metric], "unit": unit} for metric, unit in (
                ("request_tickets", "tickets"), ("requested_targets", "targets"),
                ("completed_unique_tables", "tables"), ("work_events", "events"))]}


@pytest.mark.parametrize("recovery", [False, True], ids=["boundary", "recovery-client"])
def test_requested_four_measurements_pass_the_boundary_measurement_gate(operation_expectations, bundle, recovery):
    cases = battery()
    case = next(row for row in cases.CASES if row["id"] == "ETL-F08-boundary")
    if recovery:
        from tools.codex_mcp_eval.etl.recovery_runner import recovery_battery
        cases = recovery_battery(cases, cases.REFERENCE_DATE, "client-restart", "restarted")
        case = cases.CASES[0]
    answer = requested_load_answer(operation_expectations["ETL-F08-boundary"])
    checked = cases.check(case, {"ok": True, "reply": answer["reply"], "structuredAnswer": answer},
                          [], {"cases": operation_expectations, "publicSources": bundle.public_sources})
    assert "measurement_mismatch" not in checked["failures"]
    assert "no_mcp_evidence" in checked["failures"]  # This isolates measurement correctness, not overall qualification.


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "wrong_metric", "wrong_stream",
                                     "wrong_scope", "wrong_unit", "wrong_value"])
def test_boundary_measurement_gate_stays_strict(operation_expectations, bundle, mutation):
    cases = battery()
    expected = operation_expectations["ETL-F08-boundary"]
    answer = requested_load_answer(expected)
    rows = answer["measurements"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "extra":
        rows.append({"stream": "operations", "scope": "load@2025-11", "metric": "completed_tickets",
                     "value": expected["operations"]["completed_tickets"], "unit": "tickets"})
    elif mutation == "duplicate":
        rows[-1] = copy.deepcopy(rows[0])
    elif mutation == "wrong_value":
        rows[0]["value"] += 1
    else:
        field = mutation.removeprefix("wrong_")
        rows[0][field] = "unrequested"
    case = next(row for row in cases.CASES if row["id"] == "ETL-F08-boundary")
    checked = cases.check(case, {"ok": True, "reply": answer["reply"], "structuredAnswer": answer},
                          [], {"cases": operation_expectations, "publicSources": bundle.public_sources})
    assert "measurement_mismatch" in checked["failures"]


def test_normal_still_requires_explicitly_requested_completed_tickets(operation_expectations, bundle):
    cases = battery()
    case = next(row for row in cases.CASES if row["id"] == "ETL-F08-normal")
    answer = requested_load_answer(operation_expectations[case["id"]])
    for row in answer["measurements"]:
        row["scope"] = "customers@2025-11"
    checked = cases.check(case, {"ok": True, "reply": answer["reply"], "structuredAnswer": answer},
                          [], {"cases": operation_expectations, "publicSources": bundle.public_sources})
    assert "measurement_mismatch" in checked["failures"]


def test_report_contract_keeps_private_item_labels_out_and_distinguishes_public_ambiguity(bundle):
    from tools.codex_mcp_eval.etl.checks import build_expectations
    expected = build_expectations(bundle)
    monthly = expected["ETL-F03-boundary"]
    assert len(monthly["lifecycleExpectations"]) == 8
    reports = monthly["reportExpectations"]
    assert set(reports["taskProgress"]) >= {"DL-21740", "DL-21743", "DL-21744", "DL-21745", "DL-21747", "DL-21749"}
    assert reports["taskProgress"]["DL-21740"]["reported_acceptance_events"] == 1
    assert reports["taskProgress"]["DL-21749"]["reported_acceptance_events"] is None
    findings = monthly["reportFindings"]
    assert findings and all(type(item["section"]) is int and item["section"] > 0 for item in findings)
    assert not any("item_id" in item or "document" in item or "intended_task_key" in item for item in findings)
    ambiguous = [r for r in findings if r["deliverable"] == "지연 구간 보정"]
    assert ambiguous and all(r["matchStatus"] == "ambiguous" and r["candidateKeys"] == ["DL-21745", "DL-21747"] for r in ambiguous)
    assert all("DL-50003" not in item["candidateKeys"] for item in findings if item["inScope"])
    assert any(item["candidateKeys"] == ["DL-50003"] and item["inScope"] is False for item in findings)
    assert all(item["occurredAt"] is None or len(item["occurredAt"]) == 10 for item in findings)
    assert all(item["artifact"] != "R17 계약 비교 시험" for item in findings)  # unpublished authoring label
    current = expected["ETL-F14-boundary"]["reportExpectations"]
    assert current["taskProgress"]["DL-21748"]["state"] == "conflicting"
    assert current["knowledgeComparisons"]["DL-21743"]["knownThen"]["final_accepted_deliverables"] == ["계약 지문 비교기"]
    assert current["knowledgeComparisons"]["DL-21743"]["reconstructedNow"]["final_accepted_deliverables"] == []
    for row in expected.values():
        for source in row["requiredSources"]:
            assert source in bundle.public_sources
    assert "reportFindings" in battery().OUTPUT_SCHEMA["required"]


def test_current_sp_rollup_uses_typed_metadata_without_forcing_bodies_or_history(bundle):
    from tools.codex_mcp_eval.etl.checks import build_expectations
    row = build_expectations(bundle)["ETL-F04-boundary"]
    assert row["requiredTicketInputs"]
    assert all(not source.startswith("jira:") for source in row["requiredSources"])
    assert all({"sp", "status", "type", "epic"} <= set(fields) for fields in row["requiredTicketInputs"].values())


def test_real_report_reads_are_required_for_split_task_stage_and_event_gate(tmp_path, bundle):
    import anyio
    from mcp import Client
    from tools.codex_mcp_eval.etl.checks import build_expectations
    from tools.codex_mcp_eval.etl.fixture import EtlFixture
    expected = build_expectations(bundle)
    case = next(row for row in battery().CASES if row["id"] == "ETL-F06-normal")
    wanted = expected[case["id"]]
    answer = {"reply": "DL-21744의 구현·시험 수용·최종 인수·계획은 별도 산출물. 시험과 최종 인수는 고유 2사건이며 전체 분모가 없어 퍼센트는 미확정.",
        "resultKeys": wanted["keys"], "measurements": [], "denominators": [], "progress": None, "reportedTotal": None,
        "reportFindings": copy.deepcopy(wanted["reportFindings"]), "reportProgress": copy.deepcopy(wanted["reportProgress"]),
        "sources": [{"kind": "ticket", "id": "DL-21744", "url": "http://127.0.0.1:8080/browse/DL-21744"}]}
    output = {"ok": True, "reply": answer["reply"], "structuredAnswer": answer}
    with EtlFixture(tmp_path / "reports", port=0, bundle=bundle) as fixture:
        truth = fixture.ground_truth()
        async def read_ticket():
            async with Client(fixture.runtime.server) as client:
                assert not (await client.call_tool("get_ticket", {"key": "DL-21744", "detail": "read"})).is_error
                assert not (await client.call_tool("get_evidence_page", {"key": "DL-21744", "kind": "comments"})).is_error
                aggregate_inputs = expected["ETL-F04-boundary"]["requiredTicketInputs"]
                keys = list(aggregate_inputs)[:2]
                light = await client.call_tool("get_tickets", {"keys": keys, "include_description": False})
                assert not light.is_error
                assert all("description" not in item["fields"] for item in light.structured_content["data"]["issues"])
                from tools.codex_mcp_eval.etl.checks import ticket_inputs_match
                assert ticket_inputs_match(fixture.tool_calls, {key: aggregate_inputs[key] for key in keys}, truth["ticketFieldIds"])
        anyio.run(read_ticket)
        before = battery().check(case, output, fixture.tool_calls, truth)
        assert not before["pass"] and "required_evidence_not_observed" in before["failures"]
        async def read_reports():
            async with Client(fixture.runtime.server) as client:
                response = await client.call_tool("search_confluence_documents", {"query": "ETL 개발", "max_results": 100})
                assert not response.is_error
                for document in response.structured_content["data"]["documents"]:
                    if document["title"] in {"ETL 개발 일간보고 2026-10-15", "ETL 개발 주간보고 2026-10-26", "ETL 개발 월간보고 2026-10"}:
                        read = await client.call_tool("read_confluence_document", {"page": document["id"]})
                        assert not read.is_error
        anyio.run(read_reports)
        after = battery().check(case, output, fixture.tool_calls, truth)
        from tools.codex_mcp_eval.etl.checks import observed_sources
        assert after["pass"], (after, sorted(set(wanted["requiredSources"]) - observed_sources(fixture.tool_calls, truth["publicSources"])))
        async def read_history():
            async with Client(fixture.runtime.server) as client:
                assert not (await client.call_tool("get_evidence_page", {"key": "DL-21744", "kind": "history"})).is_error
        anyio.run(read_history)
        field_comment_ids = {bundle.public_sources[source]["id"] for event in bundle.oracle.events
                             if event.issue_key == "DL-21744" and event.kind == "field" for source in event.sources}
        def without_copies(value):
            if isinstance(value, list):
                return [without_copies(item) for item in value if not isinstance(item, dict) or str(item.get("id")) not in field_comment_ids]
            if isinstance(value, dict):
                return {key: without_copies(item) for key, item in value.items()}
            return value
        equivalent = without_copies(copy.deepcopy(fixture.tool_calls))
        assert battery().check(case, output, equivalent, truth)["pass"]
        # This current-state question must also work without any history read.
        current_only = [call for call in equivalent if not (call["name"] == "get_evidence_page" and call["arguments"]["kind"] == "history")]
        assert battery().check(case, output, current_only, truth)["pass"]
        wrong_current = copy.deepcopy(current_only)
        next(call["result"]["data"] for call in wrong_current if call["name"] == "get_ticket" and call["arguments"]["key"] == "DL-21744")["status"] = "In Progress"
        assert "required_typed_inputs_not_observed" in battery().check(case, output, wrong_current, truth)["failures"]
        # Historical views require the dated transition, not just today's state.
        from tools.codex_mcp_eval.etl.checks import field_facts_observed
        facts = [fact for fact in expected["ETL-F14-boundary"]["requiredFieldFacts"] if fact["key"] == "DL-21744"]
        assert facts
        assert field_facts_observed(equivalent, facts, truth["publicSources"])
        assert field_facts_observed(fixture.tool_calls[:-1], facts, truth["publicSources"])  # original dated comment
        assert not field_facts_observed(current_only, facts, truth["publicSources"])
        for mutation in ("wrong_state", "wrong_time", "wrong_owner"):
            changed = copy.deepcopy(equivalent)
            for call in changed:
                if call["name"] != "get_evidence_page" or call["arguments"]["kind"] != "history":
                    continue
                if mutation == "wrong_owner":
                    call["result"]["data"]["key"] = "DL-21743"
                for item in call["result"]["data"]["items"]:
                    if any(field.get("field") == "status" for field in item.get("items", [])):
                        if mutation == "wrong_time":
                            item["created"] = "2026-10-30T16:00:00+09:00"
                        elif mutation == "wrong_state":
                            for field in item["items"]:
                                field["toString"] = field["to"] = "In Progress"
            assert not field_facts_observed(changed, facts, truth["publicSources"]), mutation
        answer["reportProgress"][0]["acceptedEvents"] = 4
        assert "report_progress_stage_or_duplicate_mismatch" in battery().check(case, output, fixture.tool_calls, truth)["failures"]
        answer["reportProgress"][0]["acceptedEvents"] = 2
        answer["reportFindings"][0]["candidateKeys"] = ["DL-50003"]
        assert "report_correspondence_or_claim_mismatch" in battery().check(case, output, fixture.tool_calls, truth)["failures"]


def test_checker_does_not_accept_oracle_numbers_without_observed_evidence(bundle):
    cases = battery()
    from tools.codex_mcp_eval.etl.checks import build_expectations
    expected = build_expectations(bundle)
    row = next(c for c in cases.CASES if c["id"] == "ETL-F08-normal")
    answer = {"reply": "운영 실적을 확인했습니다.", "resultKeys": expected[row["id"]]["keys"],
              "reportedTotal": None, "progress": None, "sources": [],
              "measurements": expected[row["id"]]["measurements"], "denominators": [], "reportFindings": [], "reportProgress": []}
    result = cases.check(row, {"ok": True, "reply": answer["reply"], "structuredAnswer": answer},
                         [], {"cases": expected, "publicSources": bundle.public_sources})
    assert not result["pass"] and "required_evidence_not_observed" in result["failures"]
    assert result["humanReviewRequired"] is True


def test_unknown_report_still_requires_a_successful_complete_source_search(bundle):
    from tools.codex_mcp_eval.etl.checks import build_expectations
    cases = battery()
    expected = build_expectations(bundle)
    row = next(c for c in cases.CASES if c["id"] == "ETL-F02-boundary")
    answer = {"reply": "당시 공개 발행본을 확인할 수 없어 재현 불가.", "resultKeys": [], "reportedTotal": None,
              "progress": None, "sources": [], "measurements": expected[row["id"]]["measurements"], "denominators": []}
    answer.update(reportFindings=expected[row["id"]]["reportFindings"], reportProgress=expected[row["id"]]["reportProgress"])
    calls = [{"name": "search_confluence_documents", "arguments": {"query": "2025-10 ETL"},
              "result": {"ok": False, "data": {"hasMore": False, "startAt": 0, "total": 0}}}]
    check = cases.check(row, {"ok": True, "reply": answer["reply"], "structuredAnswer": answer},
                        calls, {"cases": expected, "publicSources": bundle.public_sources})
    assert not check["pass"] and "required_source_search_not_observed" in check["failures"]


def test_review_contract_requires_direct_review_for_each_stream_and_every_case():
    cases = battery()
    suite, specs = cases.review_contract()
    assert set(specs) == {c["id"] for c in cases.CASES}
    assert {item["expected"].get("stream") for item in suite} >= {"development", "operations", "simple_voc"}
    for spec in specs.values():
        assert spec["elements"]
        assert all(item["expected"].get("semanticReviewRequired") is True for item in spec["elements"])
        assert all(item["majorWhen"] and item["evidenceSources"] for item in spec["elements"])


def test_both_baseline_arms_offer_the_deployed_research_resource_bytes():
    assert importlib.util.find_spec("tools.codex_mcp_eval.etl.skills"), "ETL deployed skill treatment is missing"
    from tools.codex_mcp_eval.etl.skills import research_catalog, DEPLOYED_COMMIT
    from app.local_mcp.skill_catalog import SkillCatalog
    import hashlib
    import subprocess
    baseline_a, baseline_b = research_catalog("baseline"), research_catalog("baseline")
    current = research_catalog("candidate")
    assert baseline_a.list_skills() == baseline_b.list_skills()
    for skill in ("ltm-work-research", "ltm-work-reporting"):
        uri = f"skill://{skill}/SKILL.md"
        blob = subprocess.run(["git", "show", f"{DEPLOYED_COMMIT}:app/local_mcp/skills/{skill}/SKILL.md"],
                              capture_output=True, check=True).stdout
        assert baseline_a.read(uri)["digest"] == "sha256:" + hashlib.sha256(blob).hexdigest()
        assert current.read(uri) == SkillCatalog(include_research=True).read(uri)
    assert len(baseline_a.list_skills()) == 6


def test_fixture_serves_only_public_world_and_both_document_versions(tmp_path, bundle):
    battery()
    from tools.codex_mcp_eval.etl.fixture import EtlFixture
    (tmp_path / "files").mkdir()
    with EtlFixture(tmp_path / "attempt", port=0, files_root=tmp_path / "files", bundle=bundle) as fixture:
        assert len(fixture.store.issues) == 10000
        assert fixture.files.list_files()["files"] == []
        proof = fixture.assert_conformance()
        assert proof["cacheWarmed"] is False
        history_proof = proof["statusHistorySearch"]
        assert history_proof["currentUser"] == "etl.user00" and history_proof["timeZone"] == "Asia/Seoul"
        assert [row["keys"] for row in history_proof["probes"]] == [["DL-21010"], []]
        assert history_proof["unsupportedStatus"] == 400
        assert history_proof["monthlyActiveCount"] == 108
        assert history_proof["contract"]["nativeEvidence"]["liveServerOrPluginParityTested"] is False
        raw = fixture.provider._client
        page_id = fixture.document_ids["etl-monthly-2025-11"]
        first = raw.get("/rest/api/content/" + page_id, params={"version": 1, "expand": "body.storage,version"}).json()
        second = raw.get("/rest/api/content/" + page_id, params={"version": 2, "expand": "body.storage,version"}).json()
        assert first["body"] != second["body"]
        public = json.dumps([fixture.tool_catalog, fixture.store.issues, fixture.store.confluence], default=str)
        assert not any(term in public for term in ("requiredSources", "mustDiscloseUncertainty", "ETL-F08-normal"))
        assert not hasattr(fixture.world, "oracle") and not hasattr(fixture.store, "oracle")
        assert not fixture.upstream_calls
        assert fixture.summary()["worldUnchanged"]

        import anyio
        from mcp import Client
        async def collect():
            async with Client(fixture.runtime.server) as client:
                for key in ("DL-31000", "DL-31001", "DL-31002"):
                    read = await client.call_tool("get_ticket", {"key": key, "detail": "read"})
                    assert not read.is_error
                    offset = 0
                    while True:
                        response = await client.call_tool("get_evidence_page", {"key": key, "kind": "comments", "start_at": offset})
                        assert not response.is_error, response
                        data = response.structured_content["data"]
                        if not data["hasMore"]:
                            break
                        offset = data["nextStartAt"]
        anyio.run(collect)
        truth = fixture.ground_truth()
        expected = truth["cases"]["ETL-F08-normal"]
        answer = {"reply": "운영 고객 1개, 테스트 고객 1개를 별도 테이블로 확인. 대기 대상은 미실행이며 공동 보고는 중복 실행이 아닙니다.",
                  "resultKeys": expected["keys"], "measurements": copy.deepcopy(expected["measurements"]),
                  "reportedTotal": None, "progress": None, "denominators": [],
                  "reportFindings": [], "reportProgress": [],
                  "sources": [{"kind": "ticket", "id": "DL-31000", "url": "http://127.0.0.1:8080/browse/DL-31000"}]}
        case = next(c for c in battery().CASES if c["id"] == "ETL-F08-normal")
        output = {"ok": True, "reply": answer["reply"], "structuredAnswer": answer}
        check = battery().check(case, output, fixture.tool_calls, truth)
        assert check["pass"], check
        answer["measurements"][0]["value"] += 1
        assert "measurement_mismatch" in battery().check(case, output, fixture.tool_calls, truth)["failures"]


def test_recovery_catalog_cuts_real_reads_and_records_restart_requirements():
    battery()
    from tools.codex_mcp_eval.etl.recovery import RECOVERY_SCENARIOS, RecoveryController
    assert len(RECOVERY_SCENARIOS) == 6
    for scenario in ("search-pagination", "comments-pagination", "history-pagination"):
        controller = RecoveryController(scenario)
        name = "search_issues" if scenario == "search-pagination" else "get_evidence_page"
        kind = "comments" if scenario == "comments-pagination" else "history"
        body = {"ok": True, "data": {"key": "DL-31000", "kind": kind, "startAt": 0,
                                    "hasMore": True, "nextStartAt": 100, "snapshotId": "observed"}}
        controller.observe_ltm(name, {"key": "DL-31000", "kind": kind}, body)
        assert controller.trigger and controller.stop.is_set()
    for scenario in ("client-restart", "backend-restart"):
        controller = RecoveryController(scenario)
        assert controller.contract["originalDialogueAvailable"] is False
        assert controller.contract["requiresSavedFilesRead"] is True
        assert controller.contract["backendRestart"] == (scenario == "backend-restart")


def test_preregistered_schedule_is_pilot_eighteen_formal_three_hundred_seventy():
    assert importlib.util.find_spec("tools.codex_mcp_eval.etl.qualification"), "ETL schedule is missing"
    from tools.codex_mcp_eval.etl.qualification import build_schedule, validate_selection, REPRESENTATIVE_CASE_IDS
    commits = {"A": "a" * 40, "B": "b" * 40, "C": "c" * 40}
    pilot = build_schedule("pilot", commits)
    formal = build_schedule("formal", commits)
    assert len(pilot) == 18 and len(formal) == 370
    assert len({r["caseId"] for r in pilot}) == len(REPRESENTATIVE_CASE_IDS) == 6
    assert len([r for r in formal if r["arm"] == "B"]) == 30
    assert len([r for r in formal if r["kind"] == "recovery"]) == 60
    assert all(row["researchFiles"] is True for row in pilot + formal)
    assert len([r for r in formal if r["repeatIndex"] == 1]) == 74
    validate_selection("formal", "B", REPRESENTATIVE_CASE_IDS)
    with pytest.raises(ValueError, match="exact"):
        validate_selection("formal", "B", REPRESENTATIVE_CASE_IDS[:-1])
    with pytest.raises(ValueError, match="exact"):
        validate_selection("formal", "A", REPRESENTATIVE_CASE_IDS)


def test_group_cut_uses_saved_source_and_actual_incoming_identity(tmp_path):
    from tools.codex_mcp_eval.etl.recovery import RecoveryController
    from tools.codex_mcp_eval.research_files import ResearchFiles
    controller = RecoveryController("group-boundary")
    files = ResearchFiles(tmp_path)
    controller.files = files
    cache = {"state": "fresh", "available": True, "compliant": True, "invalidated": False}
    body = {"ok": True, "partial": False, "missing": 0, "cache": cache,
            "data": {"hasMore": False, "snapshotId": "actual-snapshot", "canonicalJql": "actual-query",
                     "startAt": 0, "total": 0, "returned": 0, "issues": [], "nextStartAt": None}}
    controller.observe_ltm("search_issues", {"jql": "actual-query"}, body)
    files.write_file("evidence/source.json", json.dumps(body))
    files.write_file("progress.json", json.dumps({"status": "complete", "unrelated": True}))
    assert controller.trigger is None
    target = {"ok": True, "partial": False, "missing": 0, "cache": cache,
              "data": {"key": "DL-31000", "kind": "comments", "sourceComplete": True,
                       "partial": False, "hasMore": False, "startAt": 0, "total": 0, "items": []}}
    controller.observe_ltm("get_evidence_page", {"key": "DL-31000", "kind": "comments"}, target)
    assert controller.trigger["failedGroup"] == {"key": "DL-31000", "kind": "comments"}


def test_failed_group_audit_rejects_starting_another_group_first():
    from tools.codex_mcp_eval.etl.recovery_runner import audit_failed_group_first
    target = {"key": "DL-31000", "kind": "comments"}
    def call(key, kind):
        return {"name": "get_evidence_page", "arguments": {"key": key, "kind": kind},
                "result": {"ok": True, "data": {"key": key, "kind": kind, "hasMore": False,
                    "sourceComplete": True, "partial": False}}}
    # A fabricated terminal flag lacks host completion order and full source
    # coverage. Actual sequential/persisted-prefix positives use the SDK in
    # test_etl_recovery_search_order.py.
    assert audit_failed_group_first(target, [call("DL-31000", "comments")]) == ["failed_group_not_completed"]
    assert audit_failed_group_first(target, [call("DL-31001", "comments"), call("DL-31000", "comments")]) == [
        "next_group_started_before_failed_group_completed"]


def test_shared_runner_selects_etl_without_changing_old_work_battery():
    from tools.codex_mcp_eval import runner
    from tools.codex_mcp_eval.etl.cases import REFERENCE_DATE
    options = runner.parse_args(["--battery", "etl", "--date", REFERENCE_DATE, "--cases", "ETL-F08-normal"])
    assert options.research_files is True
    selected = runner._battery("etl", "2026-11-10")
    assert selected.SUITE == battery().SUITE and len(selected.CASES) == 28
    old = runner._battery("work", "2026-11-09")
    assert old.SUITE == "codex-mcp-work" and old.BATTERY_VERSION == "4.0.0" and len(old.CASES) == 22
    from tools.codex_mcp_eval.etl.qualification import REPRESENTATIVE_CASE_IDS
    pilot = runner.parse_args(["--battery", "etl", "--date", REFERENCE_DATE,
                               "--etl-phase", "pilot", "--etl-arm", "C"])
    assert set(pilot.cases) == set(REPRESENTATIVE_CASE_IDS) and pilot.skill_condition == "candidate"
    with pytest.raises(SystemExit):
        runner.parse_args(["--battery", "etl", "--date", "2026-11-09"])
    with pytest.raises(SystemExit):
        runner.parse_args(["--battery", "etl", "--date", REFERENCE_DATE,
                           "--etl-phase", "pilot", "--etl-arm", "B", "--cases", "ETL-F08-normal"])
    with pytest.raises(SystemExit):
        runner.parse_args(["--battery", "etl", "--date", REFERENCE_DATE,
                           "--recovery-scenario", "between-pages"])


def test_shared_runner_etl_launch_uses_real_http_evidence_and_equal_files(tmp_path):
    import anyio
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from tools.codex_mcp_eval import runner
    from tools.codex_mcp_eval.etl.cases import REFERENCE_DATE
    from tools.codex_mcp_eval.etl.fixture import EtlFixture
    runtime_paths, model_paths = [], []
    def fixture(path, **kwargs):
        runtime_paths.append(path)
        assert not path.resolve().is_relative_to(tmp_path.resolve())
        return EtlFixture(path, **{**kwargs, "port": 0})
    def cli_run(_binary, prompt, workspace, **kwargs):
        model_paths.append(workspace)
        assert not workspace.resolve().is_relative_to(tmp_path.resolve())
        if not kwargs.get("mcp_url"):
            return {"ok": True, "reply": "LTM_PREFLIGHT_OK", "durationSeconds": 0,
                    "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2}}
        assert kwargs.get("files_url") and "oracle" not in prompt.lower()
        workspace.mkdir(parents=True)
        assert list(workspace.iterdir()) == []
        async def read():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + kwargs["token"]}) as http:
                async with Client(streamable_http_client(kwargs["mcp_url"], http_client=http)) as client:
                    ticket = await client.call_tool("get_ticket", {"key": "DL-21001", "detail": "read"})
                    assert not ticket.is_error
                    comments = await client.call_tool("get_evidence_page", {"key": "DL-21001", "kind": "comments"})
                    assert not comments.is_error
        anyio.run(read)
        answer = {"reply": "DL-21001은 현재 Resolved이며 부모는 DL-21002. 이전 DL-21000에서 이동한 이력이 확인됨.",
                  "resultKeys": ["DL-21001"], "progress": None, "reportedTotal": 1,
                  "measurements": [], "denominators": [], "reportFindings": [], "reportProgress": [], "sources": [
                      {"kind": "ticket", "id": "DL-21001", "url": "http://127.0.0.1:8080/browse/DL-21001"}]}
        return {"ok": True, "reply": answer["reply"], "structuredAnswer": answer, "events": [],
                "durationSeconds": 1, "usage": {"promptTokens": 10, "completionTokens": 2, "totalTokens": 12}}
    arguments = ["--battery", "etl", "--date", REFERENCE_DATE,
        "--cases", "ETL-F07-normal", "--codex", "offline-stub",
        "--output-root", str(tmp_path / "raw"), "--run-group", "etl-offline-http"]
    dependencies = dict(cli_run=cli_run, fixture_factory=fixture,
                        identity_fn=lambda *a, **k: {"runtime": "offline-test", "version": "test"})
    result = runner.launch(arguments, **dependencies)
    payload = json.loads(next((tmp_path / "raw" / "etl-offline-http").glob("*.json")).read_text(encoding="utf-8"))
    assert result == 0, payload
    identity = payload["evaluation"]["run"]["externalRuntime"]
    assert identity["researchFilesEqualAcrossArms"] is True and len(identity["backendCommit"]) == 40
    assert payload["cases"][0]["automaticPass"] is True
    assert payload["metrics"]["totalTokens"] == 12
    assert all(not path.exists() for path in runtime_paths + model_paths)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    call_count = len(model_paths)
    with pytest.raises(FileExistsError):
        runner.launch(arguments, **dependencies)
    assert len(model_paths) == call_count
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize("scenario,new_backend", [("client-restart", False), ("backend-restart", True)])
@pytest.mark.parametrize("shape,prefix", [("flat", ""), ("local-arguments", "investigation/run/"),
                                         ("local-reference", "investigation/run/")])
def test_recovery_executes_actual_lifecycle_and_retains_both_phase_costs(tmp_path, bundle, scenario,
                                                                     new_backend, shape, prefix):
    assert importlib.util.find_spec("tools.codex_mcp_eval.etl.recovery_runner"), "ETL recovery runner is missing"
    import anyio
    from mcp import Client
    from types import SimpleNamespace
    from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER
    from tools.codex_mcp_eval.etl.fixture import EtlFixture
    from tools.codex_mcp_eval.etl.recovery_runner import recovery_battery, run_recovery_case
    from unittest.mock import patch
    active, instances, roots, late = [], [], [], []
    def factory(path, **kwargs):
        fixture = EtlFixture(path, **{**kwargs, "port": 0, "bundle": bundle})
        active[:] = [fixture]
        instances.append(fixture)
        roots.append(Path(kwargs["files_root"]).resolve())
        return fixture
    def cli_run(_binary, _prompt, _workspace, **kwargs):
        fixture = active[0]
        first = kwargs.get("stop_event") is not None
        async def read():
            async with Client(fixture.runtime.server) as client:
                if first:
                    response = await client.call_tool("get_evidence_page", {"key": "DL-31001", "kind": "comments"})
                    assert not response.is_error
                    await fixture.files.server.call_tool("write_file", {"path": prefix + "evidence/first.json",
                        "content": json.dumps(response.structured_content, ensure_ascii=False)})
                    if shape == "flat":
                        checkpoint = {"key": "DL-31001", "kind": "comments", "status": "complete"}
                    elif shape == "local-arguments":
                        checkpoint = {"status": "complete", "lastSavedArguments": {
                            "key": "DL-31001", "kind": "comments"}, "lastChunk": prefix + "evidence/first.json"}
                    else:
                        checkpoint = {"state": "complete", "lastSaved": prefix + "evidence/first.json"}
                    result = await fixture.files.server.call_tool("write_file", {"path": prefix + "progress.json",
                        "content": json.dumps({"groups": {"comments/DL-31001": checkpoint,
                            "history/DL-31002": {"state": "pending"}}})})
                    assert not result.is_error
                    assert not kwargs["stop_event"].is_set(), "checkpoint vocabulary must not cause a cut"
                    unsaved = await client.call_tool("get_evidence_page", {"key": "DL-31002", "kind": "history"})
                    assert not unsaved.is_error
                    late.append(unsaved.structured_content)
                    assert kwargs["stop_event"].is_set(), "the real next-group response must cause the interruption"
                else:
                    await fixture.files.server.call_tool("read_file", {"path": prefix + "progress.json"})
                    await fixture.files.server.call_tool("write_file", {"path": prefix + "evidence/phase1-only.json",
                        "content": json.dumps(late[0], ensure_ascii=False)})
                    await client.call_tool("get_ticket", {"key": "DL-31001", "detail": "read"})
        anyio.run(read)
        return {"ok": False, "reply": "", "intentionalInterruption": first, "durationSeconds": 1,
                "usage": {"promptTokens": 10, "completionTokens": 2, "totalTokens": 12 if first else None},
                "events": []}
    options = SimpleNamespace(date="2026-11-10", model="gpt-6.1-sol", effort="high", timeout=30,
                              recovery_scenario=scenario, recovery_mode="restarted", skill_condition="baseline",
                              skill_transport="default")
    selected = recovery_battery(battery(), options.date, scenario, "restarted")
    with patch.dict("os.environ", {NETWORK_PREFLIGHT_MARKER: "1"}):
        record = run_recovery_case(selected.CASES[0], options, "offline-stub", tmp_path, cli_run,
                                   selected, artifact_root=tmp_path, fixture_factory=factory, bundle=bundle)
    assert len(record["phases"]) == 2
    assert len(instances) == (2 if new_backend else 1)
    assert len(set(roots)) == 1
    assert record["durationSeconds"] == 2
    assert record["output"]["usage"]["promptTokens"] == 20
    assert record["output"]["usage"]["totalTokens"] is None
    assert record["recovery"]["backendRestarted"] is new_backend
    assert record["recovery"]["trigger"]["failedGroup"] == {"key": "DL-31002", "kind": "history"}
    assert len(record["phases"][0]["persistedCalls"]) == 1
    assert record["phases"][0]["persistedCalls"][0]["result"]["data"]["key"] == "DL-31001"
    assert record["recovery"]["eligiblePersistedCalls"] == (0 if new_backend else 1)
    assert not any(call["result"] == late[0] for call in record["groundingCalls"])
    assert len(list(tmp_path.glob("*/phase-*-events.json"))) == 2
