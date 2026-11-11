"""Literal business contracts for the independent year-world ledger and projection."""
from datetime import datetime
import importlib.util
import json

import pytest


def build():
    assert importlib.util.find_spec("tools.codex_mcp_eval.etl.world") is not None, "year-world generator is missing"
    from tools.codex_mcp_eval.etl import build_etl_world
    return build_etl_world()


@pytest.fixture(scope="module")
def bundle():
    return build()


def dt(value):
    return datetime.fromisoformat(value + "+09:00")


def test_current_workspace_wbs_matches_latest_published_mapping(bundle):
    current = {row["key"]: row["weight"] for row in bundle.world.plan["wbs"][0]["epics"]}
    assert current == {"DL-20000": 2, "DL-20002": 1}


def test_all_five_epic_contexts_have_tasks_and_defined_sp_progress(bundle):
    progress = bundle.oracle.development(dt("2026-01-15T00:00:00"))
    for key in ("DL-20000", "DL-20001", "DL-20002", "DL-20003", "DL-20004"):
        assert progress["epics"][key]["sp_total"] > 0, key
        assert progress["epics"][key]["percent"] is not None, key
    assert progress["wbs"]["ETL-1"]["percent"] is not None


def test_year_population_and_monthly_carryover_have_distinct_entities(bundle):
    assert len(bundle.world.issues) == 10000
    assert bundle.manifest["counts"] == {
        "development_epics": 5, "development": 800, "operations": 864,
        "simple_voc": 576, "other": 7755, "total": 10000,
    }
    previous = set()
    union = set()
    for start, end in bundle.spec.months:
        active = set(bundle.oracle.active_development(start, end))
        assert 98 <= len(active) <= 108
        if previous:
            assert 0 < len(active & previous) < 100
        union.update(active)
        previous = active
        assert bundle.oracle.operations(start, end)["request_tickets"] == 72
        assert bundle.oracle.simple_voc(start, end)["request_tickets"] == 48
    assert len(union) == 800


def test_operations_distinguish_ticket_target_event_and_unique_asset(bundle):
    result = bundle.oracle.operations(dt("2025-11-01T00:00:00"), dt("2025-12-01T00:00:00"),
                                      issue_keys=["DL-31000", "DL-31001", "DL-31002"])
    assert result["request_tickets"] == 3
    assert result["requested_targets"] == 5
    assert result["completed_tickets"] == 3
    assert result["completed_unique_tables"] == 2
    assert result["work_events"] == 2
    assert result["incomplete_targets"] == ["prod.crm.orders_pending"]
    assert set(result["completed_assets"]) == {"prod.crm.customers", "test.crm.customers"}
    assert bundle.oracle.simple_voc(bundle.spec.start, bundle.spec.end)["executed_operations"] == 0


def test_late_correction_separates_reconstructed_past_from_known_then(bundle):
    start, end = dt("2025-11-01T00:00:00"), dt("2025-12-01T00:00:00")
    then = bundle.oracle.operations(start, end, knowledge_at=end, issue_keys=["DL-31003"])
    now = bundle.oracle.operations(start, end, issue_keys=["DL-31003"])
    assert then["completed_unique_tables"] == 1
    assert now["completed_unique_tables"] == 0
    assert then["work_events"] == 1 and now["work_events"] == 0
    assert bundle.oracle.published_report("etl-monthly-2025-11", version=1)["version"] == 1
    assert bundle.oracle.published_report("etl-monthly-2025-11", version=2)["version"] == 2
    assert bundle.oracle.published_report("unpublished-2025-10") == {
        "known": False, "reason": "No accessible published revision", "sources": []}


def test_verified_rename_environment_and_temporal_federation_stay_distinct(bundle):
    before = bundle.oracle.asset_state(dt("2025-11-01T00:00:00"))
    after_stop = bundle.oracle.asset_state(dt("2025-12-01T00:00:00"))
    assert "prod.crm.m01_suspend_00" in before["active_tables"]
    assert "prod.crm.m01_suspend_00" not in after_stop["active_tables"]
    assert "prod.crm.m01_modify_00" in after_stop["active_tables"]
    state = bundle.oracle.asset_state(dt("2025-12-01T00:00:00"))
    assert state["aliases"]["prod.crm.customer_v1"] == "prod.crm.customers"
    assert "test.crm.customers" in state["active_tables"]
    assert "prod.crm.customers" in state["active_tables"]
    edges = state["federation_edges"]
    assert ["polaris.prod.sales", "external.prod.crm_customers"] in edges
    assert ["polaris.prod.sales", "external.prod.crm_orders"] in edges
    assert ["polaris.prod.marketing", "external.prod.crm_customers"] in edges
    after = bundle.oracle.asset_state(dt("2026-01-01T00:00:00"))
    assert ["polaris.prod.sales", "external.prod.crm_orders"] not in after["federation_edges"]


def test_projection_contains_resolvable_evidence_without_private_answer_fields(bundle):
    for event in bundle.oracle.events:
        if event.sources:
            assert all(source in bundle.public_sources for source in event.sources)
    public = json.dumps(bundle.public_sources, ensure_ascii=False, default=str)
    assert all(token not in public for token in ("oracle", "expectedAnswer", "answerLabel", "privateEvent"))
    hot = bundle.world.issues["DL-31000"]
    assert len(hot["comments"]) >= 211 and len(hot["changelog"]) >= 107
    # Both directional first pages miss the decisive multi-target acceptance.
    comments = sorted(hot["comments"], key=lambda row: row["created"])
    assert not any("orders_pending" in row["body"] for row in comments[:100] + comments[-100:])
    assert any("orders_pending" in row["body"] for row in comments[100:-100])
    histories = hot["changelog"]
    assert histories[105]["items"][0]["field"] == "status"
    assert all(h["items"][0]["field"] == "priority" for h in histories[:100] + histories[-100:])
    assert all(h["items"][0]["fromString"] != h["items"][0]["toString"] for h in histories)
    others = [row for row in bundle.world.issues.values() if row["component"] != "ETL"]
    assert len(others) == 7755
    assert all(row["description"] and row["comments"] and row["changelog"] for row in others)
    assert len({row["summary"] for row in others}) == 7755


def test_oracle_does_not_read_mutable_public_projection(bundle):
    wanted = bundle.oracle.operations(bundle.spec.start, bundle.spec.end)
    row = bundle.world.issues["DL-31000"]
    before = row["description"]
    try:
        row["description"] = "No operations occurred"
        assert bundle.oracle.operations(bundle.spec.start, bundle.spec.end) == wanted
    finally:
        row["description"] = before
    second = build()
    assert second.manifest["data_sha256"] == bundle.manifest["data_sha256"]
    assert second.world.issues["DL-41000"] == bundle.world.issues["DL-41000"]


def test_historical_hierarchy_sp_progress_and_parent_status_are_separate(bundle):
    past = bundle.oracle.development(dt("2026-01-15T00:00:00"), issue_keys=["DL-21000", "DL-21001"])
    current = bundle.oracle.development(bundle.spec.observed_until, issue_keys=["DL-21000", "DL-21001"])
    assert past["issues"]["DL-21001"]["parent"] == "DL-21000"
    assert current["issues"]["DL-21001"]["parent"] == "DL-21002"
    assert current["issues"]["DL-21000"]["status"] == "In Progress"
    assert current["issues"]["DL-21000"]["child_completion_percent"] == 100
    assert current["issues"]["DL-21000"]["sp"] == 8
    assert current["wbs"]["ETL-1"]["metric"] == "SP-weighted epic progress"


def test_world_records_are_accepted_by_jira820_serializer(bundle):
    from jira820.config import Config
    from jira820.store import Store
    store = Store(Config(project_key="DL", modules=bundle.world.modules), seed=False)
    store.users, store.issues = bundle.world.users, bundle.world.issues
    store.reindex()
    for key in ("DL-21000", "DL-21001", "DL-31000", "DL-41000", "DL-50000"):
        result = store.serializer.issue_res(store.issues[key], "http://fixture", expand="changelog")
        assert result["key"] == key and result["fields"]["description"]
        assert result["changelog"]["total"] == len(store.issues[key]["changelog"])


def test_inaccessible_execution_proof_keeps_operation_totals_unknown(bundle):
    result = bundle.oracle.operations(bundle.spec.start, bundle.spec.end, issue_keys=["DL-31016"])
    assert result["request_tickets"] == 1
    assert result["complete"] is False
    assert result["completed_unique_tables"] is None
    assert result["work_events"] is None
    assert result["unknown_request_keys"] == ["DL-31016"]
    before_proof = bundle.oracle.operations(bundle.spec.start, bundle.spec.end,
        knowledge_at=dt("2025-11-01T12:00:00"), issue_keys=["DL-31000"])
    assert before_proof["completed_unique_tables"] is None
    assert before_proof["complete"] is False


def test_simple_inquiry_resolution_requires_a_recorded_answer(bundle):
    result = bundle.oracle.simple_voc(dt("2026-10-01T00:00:00"), dt("2026-11-01T00:00:00"))
    assert result["request_tickets"] == 48
    assert result["executed_operations"] == 0
    assert result["resolved_requests"] is None
    assert result["known_resolved_requests"] == 47
    assert result["unknown_request_keys"] == ["DL-41575"]
    assert any("답변·확인 기록이 없어" in c["body"] for c in bundle.world.issues["DL-41575"]["comments"])


def test_parent_public_history_proves_membership_without_child_completion(bundle):
    notes = bundle.world.issues["DL-21000"]["comments"]
    membership = [c for c in notes if "2026-01-15" in c["body"] and "하위 티켓 전체 목록" in c["body"]]
    assert len(membership) == 1
    assert "DL-21001" in membership[0]["body"]
    assert "완료 상태는 각 하위 티켓의 이력에서 별도로 확인" in membership[0]["body"]


def test_business_periods_include_start_and_exclude_end_in_kst(bundle):
    start, end = dt("2025-11-01T00:00:00"), dt("2025-12-01T00:00:00")
    first = bundle.oracle.operations(start, dt("2025-11-01T00:00:01"), issue_keys=["DL-31011"])
    last = bundle.oracle.operations(start, end, issue_keys=["DL-31012"])
    following = bundle.oracle.operations(end, dt("2026-01-01T00:00:00"), issue_keys=["DL-31012"])
    assert first["work_events"] == 1
    assert last["work_events"] == 0
    assert following["work_events"] == 1


def test_published_revisions_are_accessible_through_real_confluence_rest(bundle):
    from fastapi.testclient import TestClient
    from jira820 import make_app
    from jira820.config import Config
    from jira820.store import Store
    from jira820.server_confluence import install_confluence

    store = Store(Config(project_key="DL", modules=bundle.world.modules), seed=False)
    store.users, store.issues, store.confluence = bundle.world.users, bundle.world.issues, bundle.world.confluence
    app = make_app(store=store)
    state = install_confluence(app)
    ids = bundle.install_confluence_revisions(state)
    with TestClient(app) as client:
        url = "/rest/api/content/" + ids["etl-monthly-2025-11"]
        original = client.get(url, params={"version": 1, "expand": "body.storage,version"})
        corrected = client.get(url, params={"version": 2, "expand": "body.storage,version"})
        assert original.status_code == corrected.status_code == 200
        assert original.json()["version"]["number"] == 1
        assert corrected.json()["version"]["number"] == 2
        assert original.json()["body"]["storage"]["value"] != corrected.json()["body"]["storage"]["value"]
        assert original.json()["version"]["when"] == "2025-12-01T12:00:00+09:00"
        assert corrected.json()["version"]["when"] == "2025-12-06T12:00:00+09:00"


def test_dormant_task_starts_recently_without_turning_creation_into_work(bundle):
    before = bundle.oracle.development(dt("2026-10-20T00:00:00"))["issues"]["DL-21010"]
    after = bundle.oracle.development(dt("2026-11-01T00:00:00"))["issues"]["DL-21010"]
    assert before["status"] == "Open"
    assert before["progress"]["state"] == "not_started"
    assert before["progress"]["started_at"] is None
    assert after["status"] == "In Progress"
    assert after["progress"]["started_at"] == "2026-10-28T10:00:00+09:00"
    assert bundle.world.issues["DL-21010"]["created"].isoformat() == "2025-11-01"


@pytest.mark.parametrize("key,phase,completed,pending", [
    ("DL-21012", "in_progress", ["CDC 체크포인트 저장", "중복 방지 키 검증"], ["장애 복구 통합 검증"]),
    ("DL-21013", "unknown", [], []),
    ("DL-21014", "in_progress", ["계약 샘플", "재시도 격리", "지연 관측"], ["다중 원천 회귀 검증"]),
    ("DL-21015", "awaiting_dependency", ["연결 계약 초안"], ["원천 방화벽 승인"]),
    ("DL-21017", "rework", [], ["역순 이벤트 회귀 수정"]),
    ("DL-21018", "in_progress", ["스키마 매핑 인수인계"], ["수신팀 통합 검증"]),
    ("DL-21019", "in_progress", ["쿼리 계획 재현"], ["성능 회귀 검증"]),
])
def test_verified_comment_progress_is_distinct_from_jira_state(bundle, key, phase, completed, pending):
    row = bundle.oracle.development(dt("2026-11-01T00:00:00"))["issues"][key]
    progress = row["progress"]
    assert progress["state"] == phase
    assert progress["completed_deliverables"] == completed
    assert progress["pending_deliverables"] == pending
    assert progress["overall_percent"] is None
    assert all(source in bundle.public_sources for source in progress["sources"])
    assert progress["known"] is (key != "DL-21013")
    if key == "DL-21012":
        assert row["status"] == "In Progress"
        assert progress["last_substantive_at"] == "2026-10-20T14:00:00+09:00"
    if key == "DL-21019":
        assert progress["last_substantive_at"] == "2026-09-15T11:00:00+09:00"
        assert progress["last_administrative_at"] == "2026-10-31T16:00:00+09:00"
    if key == "DL-21015":
        assert progress["blockers"] == [{"key": "DL-50002", "reason": "원천 방화벽 승인"}]
        assert bundle.world.issues["DL-50002"]["statusCategory"] != "done"
        assert "원천 방화벽 승인" in bundle.world.issues["DL-50002"]["description"]


def test_progress_reducer_rejects_unpublished_private_fact_and_tracks_rework(bundle):
    unknown = bundle.oracle.development(bundle.spec.observed_until)["issues"]["DL-21013"]["progress"]
    assert unknown["completed_deliverables"] == []
    private = [event for event in bundle.oracle.events if event.issue_key == "DL-21013" and not event.sources]
    assert private and private[0].payload["deliverables"] == ["승인되지 않은 초안"]
    public = json.dumps(bundle.public_sources, ensure_ascii=False, default=str)
    assert "승인되지 않은 초안" not in public
    accepted = bundle.oracle.development(dt("2026-08-01T00:00:00"))["issues"]["DL-21017"]
    reopened = bundle.oracle.development(dt("2026-11-01T00:00:00"))["issues"]["DL-21017"]
    assert accepted["status"] == "Resolved"
    assert accepted["progress"]["completed_deliverables"] == ["역순 이벤트 검증"]
    assert reopened["status"] == "Reopened"
    assert reopened["progress"]["completed_deliverables"] == []


def test_manifest_exposes_private_lifecycle_coverage_and_real_monthly_counts(bundle):
    assert set(bundle.manifest["development_lifecycle_coverage"]) == {
        "dormant_recent_start", "comment_verified_partial", "insufficient_comments", "long_running_incremental",
        "dependency_wait", "reopened_rework", "handoff", "administrative_update"}
    assert bundle.manifest["monthly_active_development"] == {
        start.strftime("%Y-%m"): len(bundle.oracle.active_development(start, end)) for start, end in bundle.spec.months}
    # Private lifecycle labels describe coverage, never model-visible answer hints.
    public = json.dumps(bundle.public_sources, ensure_ascii=False, default=str)
    assert "dormant_recent_start" not in public and "comment_verified_partial" not in public


def test_development_claims_map_to_dated_public_comments_and_knowledge_cutoff(bundle):
    at = dt("2026-11-01T00:00:00")
    state = bundle.oracle.development(at)["issues"]
    for keys in bundle.manifest["development_lifecycle_coverage"].values():
        for key in keys:
            progress = state[key]["progress"]
            evidence = "\n".join(bundle.public_sources[source]["text"] for source in progress["sources"])
            assert all(item in evidence for item in progress["completed_deliverables"] + progress["pending_deliverables"])
            assert all(source.startswith("jira:" + key + ":comment:") for source in progress["sources"])
            assert all("recordedAt" in bundle.public_sources[source] for source in progress["sources"])
    known_then = bundle.oracle.development(at, knowledge_at=dt("2026-10-10T00:00:00"))["issues"]["DL-21012"]["progress"]
    assert known_then["completed_deliverables"] == ["CDC 체크포인트 저장"]
    assert known_then["pending_deliverables"] == ["중복 방지 키 검증", "장애 복구 통합 검증"]
    handoff = state["DL-21018"]
    assert len(handoff["progress"]["contributors"]) == 1
    assert handoff["progress"]["contributors"][0] != handoff["assignee"]


def test_confluence_only_acceptance_resolves_public_context_without_jira_comment_copy(bundle):
    state = bundle.oracle.development(bundle.spec.observed_until)
    row = state["issues"]["DL-21743"]
    assert row["progress"]["test_accepted_deliverables"] == ["계약 지문 비교기"]
    assert row["progress"]["final_accepted_deliverables"] == []
    assert any(source.startswith("confluence:") for source in row["progress"]["sources"])
    assert not any("계약 지문 비교기" in c["body"] for c in bundle.world.issues["DL-21743"]["comments"])
    matches = [r for r in state["report_correspondence"] if r["item_id"] == "source-fingerprint"]
    assert matches and all(r["status"] == "verified" and r["candidate_keys"] == ["DL-21743"] for r in matches)
    assert all("DL-50003" not in r["candidate_keys"] for r in matches)
    assert all(len(r["proof_sources"]) >= 2 for r in matches)
    decoy = [r for r in state["report_correspondence"] if r["item_id"] == "storage-fingerprint"]
    assert len(decoy) == 1 and decoy[0]["candidate_keys"] == ["DL-50003"]
    assert not set(decoy[0]["report_sources"]) & set(row["progress"]["report_sources"])


def test_report_many_to_many_stage_separation_and_repeated_reporting_deduplicate(bundle):
    state = bundle.oracle.development(bundle.spec.observed_until)
    grouped = [r for r in state["report_correspondence"] if r["item_id"] == "order-recovery"]
    assert {key for r in grouped for key in r["candidate_keys"]} == {"DL-21740", "DL-21742"}
    first = state["issues"]["DL-21740"]["progress"]
    assert first["test_accepted_deliverables"] == ["재시작 토큰 저장"]
    assert first["reported_acceptance_events"] == 1
    assert len(first["report_sources"]) >= 3
    split = state["issues"]["DL-21744"]["progress"]
    assert split["reported_implementation"] == ["역순 입력 정렬"]
    assert split["test_accepted_deliverables"] == ["역순 재시도 시험"]
    assert split["final_accepted_deliverables"] == ["시간창 역전 처리 인수"]
    assert split["planned_deliverables"] == ["극단 지연 시나리오"]
    assert split["overall_percent"] is None


def test_private_intent_does_not_resolve_ambiguous_report_or_duplicate(bundle):
    state = bundle.oracle.development(bundle.spec.observed_until)
    ambiguous = [r for r in state["report_correspondence"] if r["item_id"] == "late-window"]
    assert len(ambiguous) == 1
    assert ambiguous[0]["status"] == "ambiguous"
    assert ambiguous[0]["candidate_keys"] == ["DL-21745", "DL-21747"]
    assert all("지연 구간 보정" not in state["issues"][key]["progress"]["completed_deliverables"]
               for key in ("DL-21745", "DL-21747"))
    duplicate = state["issues"]["DL-21749"]["progress"]
    assert duplicate["test_accepted_deliverables"] == ["경계 조건 검증"]
    assert duplicate["reported_acceptance_events"] is None
    assert duplicate["duplicate_status"] == "unknown"


def test_report_late_revision_and_jira_conflict_preserve_knowledge_and_status(bundle):
    then = bundle.oracle.development(dt("2026-11-01T00:00:00"), knowledge_at=dt("2026-11-01T00:00:00"))
    now = bundle.oracle.development(bundle.spec.observed_until)
    assert then["issues"]["DL-21743"]["progress"]["final_accepted_deliverables"] == ["계약 지문 비교기"]
    assert now["issues"]["DL-21743"]["progress"]["final_accepted_deliverables"] == []
    contradiction = now["issues"]["DL-21748"]
    assert contradiction["status"] == "Resolved"
    assert contradiction["progress"]["state"] == "conflicting"
    assert contradiction["progress"]["conflicts"]
    assert now["superseded_report_versions"]
    assert then["issues"]["DL-21744"]["progress"]["final_accepted_deliverables"] == []
    assert now["issues"]["DL-21744"]["progress"]["final_accepted_deliverables"] == ["시간창 역전 처리 인수"]
    implementation_only = bundle.oracle.development(dt("2026-10-17T00:00:00"))["issues"]["DL-21743"]["progress"]
    assert implementation_only["reported_implementation"] == ["계약 지문 비교기"]
    assert implementation_only["completed_deliverables"] == []


def test_development_reports_are_discoverable_and_readable_over_rest_and_mcp(bundle):
    from tempfile import TemporaryDirectory
    from pathlib import Path
    import anyio
    from mcp import Client
    from tools.codex_mcp_eval.etl.fixture import EtlFixture

    with TemporaryDirectory() as directory:
        scratch = Path(directory)
        (scratch / "files").mkdir()
        with EtlFixture(scratch / "run", port=0, files_root=scratch / "files", bundle=bundle) as fixture:
            raw = fixture.provider._client
            result = raw.get("/rest/api/search", params={"cql": 'space = DL AND title ~ "ETL 개발"', "limit": 100})
            assert result.status_code == 200
            titles = [row["title"] for row in result.json()["results"]]
            assert any("일간보고" in title for title in titles)
            assert any("주간보고" in title for title in titles)
            assert any("월간보고" in title for title in titles)
            async def read_reports():
                async with Client(fixture.runtime.server) as client:
                    found = await client.call_tool("search_confluence_documents", {"query": "ETL 개발", "max_results": 100})
                    assert not found.is_error, found
                    docs = found.structured_content["data"]["documents"]
                    selected = next(row for row in docs if row["title"] == "ETL 개발 일간보고 2026-10-24")
                    original = await client.call_tool("read_confluence_document", {"page": selected["id"], "version": 1})
                    corrected = await client.call_tool("read_confluence_document", {"page": selected["id"], "version": 2})
                    assert not original.is_error and not corrected.is_error
                    assert "인수 승인" in original.structured_content["data"]["content"]
                    assert "인수 보류" in corrected.structured_content["data"]["content"]
                    assert original.structured_content["data"]["version"]["number"] == 1
                    assert corrected.structured_content["data"]["version"]["number"] == 2
                    assert "DL-" not in corrected.structured_content["data"]["content"]
            anyio.run(read_reports)


def test_report_source_projection_exposes_business_text_and_no_private_correspondence(bundle):
    public = json.dumps([bundle.world.issues, bundle.world.confluence, bundle.public_sources], ensure_ascii=False, default=str)
    assert "intended_task_key" not in public and "matched_dimensions" not in public
    comments = "\n".join(c["body"] for issue in bundle.world.issues.values() for c in issue["comments"])
    reports = [event for event in bundle.oracle.events if event.kind == "development_report"]
    assert len(reports) == 6
    for report in reports:
        assert not report.issue_key
        source = bundle.public_sources[report.sources[0]]
        assert source["kind"] == "confluence_revision"
        assert source["ancestors"]
        for item in report.payload["items"]:
            assert item["text"] not in comments
            assert all(claim["deliverable"] in source["text"] for claim in item["claims"])
            assert any(scope in source["text"] for scope in item["context"]["scopes"])


def test_undated_distinct_acceptance_stages_are_not_false_duplicate_ambiguity(bundle):
    from copy import deepcopy
    from dataclasses import replace
    from tools.codex_mcp_eval.etl.oracle import EventOracle

    # Retain the authored named stages but withhold exact dates from the reducer.
    # A test acceptance and final acceptance remain two distinct approval events.
    events = []
    for event in bundle.oracle.events:
        if event.kind == "development_report":
            payload = deepcopy(event.payload)
            for item in payload["items"]:
                for claim in item["claims"]:
                    if claim["deliverable"] in {"역순 재시도 시험", "시간창 역전 처리 인수"}:
                        claim["occurred_at"] = None
            event = replace(event, payload=payload)
        events.append(event)
    reduced = EventOracle(tuple(events), bundle.spec.observed_until).development(bundle.spec.observed_until)
    progress = reduced["issues"]["DL-21744"]["progress"]
    assert progress["reported_acceptance_events"] == 2
    assert progress["duplicate_status"] == "resolved"
