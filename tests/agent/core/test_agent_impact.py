from __future__ import annotations

import json

from app.agent import impact


def test_impact_store_retains_only_aggregate_metadata(monkeypatch, tmp_path):
    target = tmp_path / "impact.json"
    monkeypatch.setattr(impact, "_path", lambda: target)
    impact.note_draft(
        thread_id="thread-with-user-context",
        approval_token="one-time-secret-capability",
        item_count=2,
        engine="maxis",
        profile="dev",
        feature="create_tickets",
        role="work_architect",
        workflow_bundle_version="bundle-1",
        knowledge_version="knowledge-1",
        knowledge_manifest_sha256="a" * 64,
        now=100.0,
    )
    impact.note_review(
        thread_id="thread-with-user-context",
        approval_token="one-time-secret-capability",
        outcome="approved",
        overrides={"items": {"0": {"summary": "sensitive revised body"}}},
        execution_succeeded=True,
        now=112.5,
    )

    wire = target.read_text(encoding="utf-8")
    assert "one-time-secret-capability" not in wire
    assert "thread-with-user-context" not in wire
    assert "sensitive revised body" not in wire
    row = json.loads(wire)[0]
    assert row["reviewSeconds"] == 12.5
    assert row["editFieldCount"] == 1
    assert row["editCharacterCount"] == len("sensitive revised body")
    assert row["majorRewrite"] is False

    got = impact.summary(days=30, now=120.0)
    assert got["draftCount"] == 1
    assert got["acceptanceRatePct"] == 100.0
    assert got["noMajorRewriteRatePct"] == 100.0
    assert got["writeSuccessRatePct"] == 100.0
    assert got["privacy"] == "aggregate_metadata_only"
    assert got["segments"] == [{
        "feature": "create_tickets",
        "role": "work_architect",
        "engine": "maxis",
        "profile": "dev",
        "workflowBundleVersion": "bundle-1",
        "knowledgeVersion": "knowledge-1",
        "knowledgeManifestSha256": "a" * 64,
        "draftCount": 1,
        "reviewedCount": 1,
        "acceptanceRatePct": 100.0,
        "noMajorRewriteRatePct": 100.0,
        "averageReviewSeconds": 12.5,
    }]


def test_impact_events_are_idempotent(monkeypatch, tmp_path):
    target = tmp_path / "impact.json"
    monkeypatch.setattr(impact, "_path", lambda: target)
    for _ in range(2):
        impact.note_draft(
            thread_id="t", approval_token="token", item_count=1, now=1.0,
        )
    for _ in range(2):
        impact.note_review(
            thread_id="t", approval_token="token", outcome="cancelled", now=4.0,
        )

    rows = json.loads(target.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["outcome"] == "cancelled"
    assert impact.summary(days=1, now=5.0)["cancelledCount"] == 1
