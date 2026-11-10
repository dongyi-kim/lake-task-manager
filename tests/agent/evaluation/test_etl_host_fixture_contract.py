"""Small authored fixtures for public identity and corrected report provenance."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tools.codex_mcp_eval.etl import world as authoring
from tools.codex_mcp_eval.etl.oracle import EventOracle
from tools.codex_mcp_eval.etl.projection import project


def at(value):
    return datetime.fromisoformat(value + "T00:00:00+09:00")


@pytest.fixture
def reports():
    author = authoring._Author(authoring.YearWorldSpec())
    for key in ("DL-21743", "DL-21748", "DL-50003"):
        author.issue(key, "development", at("2026-10-01"), key, "Public task scope",
                     module="Storage" if key == "DL-50003" else "ETL")
    author.field("DL-21748", "development", at("2026-10-31"), "status", "In Progress", "Resolved")
    authoring._development_reports(author)
    return author


def test_manifest_identity_changes_when_only_public_plan_changes(monkeypatch):
    def tiny_development(author):
        author.issue("DL-20000", "development_epics", author.spec.start, "Epic", "Scope", kind="Epic")
        author.document("mapping", author.spec.start, "Public mapping", kind="mapping",
                        extra={"wbs": "ETL-1", "epics": {"DL-20000": 1}})

    monkeypatch.setattr(authoring, "_development", tiny_development)
    for name in ("_operations", "_simple_and_other", "_development_reports"):
        monkeypatch.setattr(authoring, name, lambda author: None)
    original = authoring.build_etl_world()
    real_project = authoring.project

    def alternate_plan(*args):
        result = real_project(*args)
        result[0].plan["wbs"][0]["epics"][0]["weight"] = 2
        return result

    monkeypatch.setattr(authoring, "project", alternate_plan)
    changed = authoring.build_etl_world()
    assert original.world.issues == changed.world.issues
    assert original.world.users == changed.world.users
    assert original.documents == changed.documents
    assert original.manifest["data_sha256"] != changed.manifest["data_sha256"]
    assert changed.manifest["version"] == "etl-year-world-v4"


def test_fixture_world_identity_tracks_plan_without_changing_provider_identity():
    from tools.codex_mcp_eval.etl.fixture import EtlFixture

    fixture = EtlFixture.__new__(EtlFixture)
    fixture.world = SimpleNamespace(today=at("2026-11-10").date(), _counter=1,
        issues={}, confluence={}, attachments={}, users={}, plan={"wbs": [{"module": "ETL"}]})
    fixture.store = SimpleNamespace(issues={}, confluence={}, attachments={}, activity={}, users={})
    fixture.provider = SimpleNamespace(_client=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
    first = fixture.fingerprint()
    fixture.world.plan["wbs"][0]["module"] = "Catalog"
    changed = fixture.fingerprint()
    assert changed["worldSha256"] != first["worldSha256"]
    assert changed["providerStoreSha256"] == first["providerStoreSha256"]
    fixture.world.plan["wbs"][0]["module"] = "ETL"
    assert fixture.fingerprint() == first


def test_daily_correction_keeps_source_text_but_separates_pending_from_new_plan(reports):
    event = next(event for event in reports.events if event.identity == "doc:etl-dev-daily-2026-10-24:v2")
    item = event.payload["items"][0]
    assert item["text"] == (
        "11월 5일 정정: 10월 24일 계약 지문 비교기 인수 승인 기록은 test 환경 시연과 혼동했다. "
        "운영 인수 보류이며 추가 합계 검증을 계획했다. 스키마 변화 표식 담당 검토.")
    assert [(claim["deliverable"], claim["stage"], claim["outcome"], claim["occurred_at"], claim["artifact"])
            for claim in item["claims"]] == [
        ("계약 지문 비교기", "final_acceptance", "pending", None, None),
        ("추가 합계 검증", "plan", None, None, None),
    ]
    public, _, _ = project(tuple(reports.events), reports.spec)
    assert "추가 합계 검증" in public.issues["DL-21743"]["description"]
    current = EventOracle(tuple(reports.events), reports.spec.observed_until).development(reports.spec.observed_until)
    matches = [row for row in current["report_correspondence"] if row["document"] == event.payload["document"]]
    assert len(matches) == 2
    assert all(row["candidate_keys"] == ["DL-21743"] and row["status"] == "verified" for row in matches)


def test_monthly_pending_outcome_uses_explicit_november_event_not_rejection(reports):
    event = next(event for event in reports.events if event.payload.get("document") == "etl-dev-monthly-2026-10")
    item = event.payload["items"][3]
    assert item["text"] == (
        "다중 원천 통합 검증: Jira 상태는 10월 31일 종료로 기록됐다. "
        "11월 2일 고객 검수에서 원천 간 합계 불일치가 남아 인수 보류를 통보받았다.")
    claim = item["claims"][0]
    assert claim["outcome"] == "pending"
    assert claim["occurred_at"].isoformat() == "2026-11-02T10:00:00+09:00"
    assert claim["stage"] == "final_acceptance" and claim["artifact"] is None


def test_correction_supersedes_acceptance_without_backdating_new_undated_work(reports):
    oracle = EventOracle(tuple(reports.events), reports.spec.observed_until)
    cutoff = at("2026-11-01")
    then = oracle.development(cutoff, knowledge_at=cutoff)
    reconstructed = oracle.development(cutoff)
    current = oracle.development(reports.spec.observed_until)
    old = then["issues"]["DL-21743"]["progress"]
    rebuilt = reconstructed["issues"]["DL-21743"]["progress"]
    now = current["issues"]["DL-21743"]["progress"]
    assert old["final_accepted_deliverables"] == ["계약 지문 비교기"]
    assert old["planned_deliverables"] == []
    assert rebuilt["final_accepted_deliverables"] == []
    assert rebuilt["planned_deliverables"] == []
    assert now["final_accepted_deliverables"] == []
    assert now["planned_deliverables"] == ["추가 합계 검증"]
    assert now["test_accepted_deliverables"] == ["계약 지문 비교기"]
    assert not any(row["claim"]["deliverable"] == "추가 합계 검증"
                   for row in reconstructed["report_correspondence"])
    monthly = [row for row in current["report_correspondence"]
               if row["claim"]["deliverable"] == "다중 원천 통합 검증"]
    assert len(monthly) == 1 and monthly[0]["claim"]["outcome"] == "pending"
    assert not any(row["claim"]["deliverable"] == "다중 원천 통합 검증"
                   for row in reconstructed["report_correspondence"])
    assert current["issues"]["DL-21748"]["progress"]["conflicts"][0]["report_outcome"] == "pending"


def test_correction_publication_boundary_does_not_invent_occurrence_timestamps(reports):
    oracle = EventOracle(tuple(reports.events), reports.spec.observed_until)
    correction = next(event for event in reports.events
                      if event.identity == "doc:etl-dev-daily-2026-10-24:v2")
    published = correction.recorded_at
    cutoff = at("2026-11-01")
    before = oracle.development(cutoff, knowledge_at=published - timedelta(microseconds=1))
    known = oracle.development(cutoff, knowledge_at=published)
    assert before["issues"]["DL-21743"]["progress"]["final_accepted_deliverables"] == ["계약 지문 비교기"]
    assert known["issues"]["DL-21743"]["progress"]["final_accepted_deliverables"] == []
    assert known["issues"]["DL-21743"]["progress"]["planned_deliverables"] == []
    at_publication = oracle.development(published)
    after_publication = oracle.development(published + timedelta(microseconds=1))
    document = correction.payload["document"]
    assert not any(row["document"] == document for row in at_publication["report_correspondence"])
    claims = [row["claim"] for row in after_publication["report_correspondence"] if row["document"] == document]
    assert len(claims) == 2
    assert all(claim["occurred_at"] is None for claim in claims)
    assert after_publication["issues"]["DL-21743"]["progress"]["planned_deliverables"] == ["추가 합계 검증"]
