"""Optional summaries must consume collected evidence without starting another query."""
import pytest


def snapshot():
    return {"id": "run-a", "status": "partial", "request": {"startDate": "2026-11-01", "endDate": "2026-11-16"},
            "asOf": "2026-11-16T12:00:00+09:00", "summary": {"comments": 1, "provisional": True},
            "coverage": {"complete": False}, "issues": [{"key": "DL-123", "summary": "재처리 검증"}],
            "events": [{"id": "comment:1", "issueKey": "DL-123", "kind": "comment", "body": "중복 적재 없음 확인"}]}


def test_summary_passes_collected_evidence_and_keeps_partial_coverage(monkeypatch):
    from app.tracking import summary
    seen = []
    monkeypatch.setattr(summary, "_ready", lambda: (True, ""))
    def invoke(payload):
        seen.append(payload)
        return {"items": [{"text": "DL-123 중복 적재 없음 확인", "evidenceIds": ["comment:1"]}]}, {"totalTokens": 27}
    monkeypatch.setattr(summary, "_invoke", invoke)
    result = summary.summarize_snapshot(snapshot())
    assert result["coverage"] == {"complete": False}
    assert result["provisional"] is True
    assert result["jiraRequests"] == 0
    assert result["usage"]["totalTokens"] == 27
    assert seen[0]["events"][0]["body"] == "중복 적재 없음 확인"


@pytest.mark.parametrize("item", [
    {"text": "결과", "evidenceIds": ["unknown"]},
    {"text": "DL-999 완료", "evidenceIds": ["comment:1"]},
    {"text": "결과", "evidenceIds": []},
])
def test_unbound_summary_is_rejected(monkeypatch, item):
    from app.tracking import summary
    monkeypatch.setattr(summary, "_ready", lambda: (True, ""))
    monkeypatch.setattr(summary, "_invoke", lambda data: ({"items": [item]}, {}))
    with pytest.raises(summary.SummaryFailure):
        summary.summarize_snapshot(snapshot())


def test_large_evidence_is_not_silently_truncated_or_sent(monkeypatch):
    from app.tracking import summary
    data = snapshot()
    data["events"][0]["body"] = "가" * 100_001
    monkeypatch.setattr(summary, "_ready", lambda: (True, ""))
    monkeypatch.setattr(summary, "_invoke", lambda _: pytest.fail("oversized input sent"))
    with pytest.raises(summary.SummaryInputError):
        summary.summarize_snapshot(data)


def test_display_projections_are_excluded_before_summary_input_limits(monkeypatch):
    from app.tracking import summary
    data = snapshot()
    event = data["events"][0]
    event["bodyHtml"] = "<p>" + "display markup " * 10_000 + "</p>"
    event["bodyText"] = "display text " * 10_000
    original_event = dict(event)
    monkeypatch.setattr(summary, "_ready", lambda: (True, ""))

    def invoke(payload):
        assert payload["events"] == [{key: value for key, value in original_event.items()
                                     if key not in ("bodyHtml", "bodyText")}]
        return {"items": [{"text": "DL-123", "evidenceIds": ["comment:1"]}]}, {}

    monkeypatch.setattr(summary, "_invoke", invoke)
    summary.summarize_snapshot(data)
    assert data["events"][0] == original_event


def test_unconfigured_model_does_not_invoke(monkeypatch):
    from app.tracking import summary
    monkeypatch.setattr(summary, "_ready", lambda: (False, "연결 설정 필요"))
    monkeypatch.setattr(summary, "_invoke", lambda _: pytest.fail("unconfigured model called"))
    with pytest.raises(summary.SummaryUnavailable, match="연결 설정 필요"):
        summary.summarize_snapshot(snapshot())


def test_known_ticket_still_requires_its_own_citation(monkeypatch):
    from app.tracking import summary
    data = snapshot()
    data["issues"].append({"key": "DL-124", "summary": "다른 업무"})
    monkeypatch.setattr(summary, "_ready", lambda: (True, ""))
    monkeypatch.setattr(summary, "_invoke", lambda _: ({"items": [
        {"text": "DL-124 검증 완료", "evidenceIds": ["comment:1"]}]}, {}))
    with pytest.raises(summary.SummaryFailure):
        summary.summarize_snapshot(data)
