from __future__ import annotations

import json
import threading

import pytest

from app.agent.maxis import shadow_eval


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    target = tmp_path / "shadow-quality.json"
    monkeypatch.setattr(shadow_eval, "_path", lambda: target)
    shadow_eval.reset_for_tests()
    yield target
    shadow_eval.reset_for_tests()


def _dimensions(**changes) -> shadow_eval.ShadowDimensions:
    values = {
        "profile": "prod",
        "profile_fingerprint": "a" * 32,
        "workflow_key": "agent_core",
        "role_id": "work_architect",
        "workflow_version": "11111111-1111-4111-8111-111111111111",
        "knowledge_version": "knowledge-2026-09-04",
    }
    values.update(changes)
    return shadow_eval.ShadowDimensions(**values)


def _passing_comparison() -> dict:
    return {
        "structureScore": 1.0,
        "numericComparable": True,
        "numericParity": True,
        "lexicalComparable": True,
        "lexicalScore": 0.75,
        "nonEmptyParity": True,
    }


def test_compare_outputs_returns_scores_without_content():
    local = {"summary": "DL-991 비밀 계획 25%", "count": 3}
    remote = {"summary": "DL-991 다른 표현 25%", "count": 3}

    result = shadow_eval.compare_outputs(local, remote)

    assert result["numericComparable"] is True
    assert result["numericParity"] is True
    assert 0 <= result["structureScore"] <= 1
    encoded = json.dumps(result, ensure_ascii=False)
    assert "DL-991" not in encoded and "비밀 계획" not in encoded


def test_store_contains_aggregate_metadata_only(isolated_store):
    dimensions = _dimensions()
    shadow_eval.record_submitted(dimensions)
    shadow_eval.record_success(
        dimensions,
        comparison=shadow_eval.compare_outputs(
            {"summary": "local confidential DL-991 25%"},
            {"summary": "remote confidential DL-991 25%"},
        ),
        contract_valid=True,
        citation_valid=True,
        latency_ms=123,
    )

    raw = isolated_store.read_text(encoding="utf-8")
    assert "confidential" not in raw and "DL-991" not in raw
    assert "prompt" not in raw.casefold() and "correlation" not in raw.casefold()
    result = shadow_eval.summary(dimensions)
    assert result["submitted"] == 1 and result["completed"] == 1
    assert result["averageLatencyMs"] == 123.0
    assert result["privacy"] == "aggregate_metadata_only"


def test_promotion_requires_five_passing_exact_version_samples():
    dimensions = _dimensions()
    for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
        shadow_eval.record_submitted(dimensions)
        shadow_eval.record_success(
            dimensions,
            comparison=_passing_comparison(),
            contract_valid=True,
            citation_valid=True,
        )

    ready = shadow_eval.promotion_status(dimensions)
    assert ready["ready"] is True
    shadow_eval.assert_promotion_ready(dimensions)

    changed = _dimensions(knowledge_version="knowledge-next")
    blocked = shadow_eval.promotion_status(changed)
    assert blocked["ready"] is False and blocked["completed"] == 0
    with pytest.raises(ValueError, match="shadow samples 0/5"):
        shadow_eval.assert_promotion_ready(changed)


def test_promotion_rejects_failures_and_bad_citation_or_numeric_parity():
    dimensions = _dimensions()
    for index in range(shadow_eval.MIN_PROMOTION_SAMPLES):
        comparison = _passing_comparison()
        if index == 0:
            comparison["numericParity"] = False
        shadow_eval.record_success(
            dimensions,
            comparison=comparison,
            contract_valid=True,
            citation_valid=index != 1,
        )

    status = shadow_eval.promotion_status(dimensions)
    assert status["ready"] is False
    assert status["numericParityRatePct"] == 80.0
    assert any("citation validity" in reason for reason in status["reasons"])

    failed_dimensions = _dimensions(
        role_id="auditor", workflow_version="22222222-2222-4222-8222-222222222222",
    )
    for _ in range(shadow_eval.MIN_PROMOTION_SAMPLES):
        shadow_eval.record_failure(failed_dimensions)
    failed = shadow_eval.promotion_status(failed_dimensions)
    assert failed["ready"] is False and failed["successRatePct"] == 0.0


def test_async_submit_discards_output_and_records_only_aggregate():
    dimensions = _dimensions()

    class Remote(dict):
        state_patch = {"quality_gate": {"citationValid": True}}
        metadata = {"latencyMs": 42}

    assert shadow_eval.submit(
        dimensions=dimensions,
        local_output={"summary": "local private 9"},
        invoke_remote=lambda: Remote(summary="remote private 9"),
    ) is True
    shadow_eval.drain_for_tests()

    result = shadow_eval.summary(dimensions)
    assert result["completed"] == 1 and result["succeeded"] == 1
    assert result["citationValidRatePct"] == 100.0
    assert result["averageLatencyMs"] == 42.0


def test_shadow_queue_is_bounded_and_never_blocks_authoritative_caller():
    dimensions = _dimensions()
    release = threading.Event()

    class Remote(dict):
        state_patch = {"quality_gate": {"citationValid": True}}
        metadata = {"latencyMs": 1}

    def delayed_remote():
        assert release.wait(3)
        return Remote(summary="discarded")

    accepted = [shadow_eval.submit(
        dimensions=dimensions,
        local_output={"summary": "local"},
        invoke_remote=delayed_remote,
    ) for _ in range(shadow_eval.MAX_PENDING)]
    overflow = shadow_eval.submit(
        dimensions=dimensions,
        local_output={"summary": "local"},
        invoke_remote=delayed_remote,
    )

    assert all(accepted) and overflow is False
    release.set()
    shadow_eval.drain_for_tests()
    result = shadow_eval.summary(dimensions)
    assert result["submitted"] == shadow_eval.MAX_PENDING + 1
    assert result["queueDropped"] == 1
    assert result["completed"] == shadow_eval.MAX_PENDING


def test_dimension_labels_reject_content_like_values():
    with pytest.raises(ValueError, match="role_id"):
        _dimensions(role_id="work_architect\nDL-991 confidential").clean()
