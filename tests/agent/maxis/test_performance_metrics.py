import copy

import pytest

from tools.maxis_performance_metrics import editor_observations, summarize_attempts


def row(identity="one", **overrides):
    return {"attemptId": identity, "roleId": "editor_author", "status": "ok",
            "responseReceived": True, "latencyMs": 100,
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            **overrides}


def test_failures_are_included_and_missing_usage_is_not_zero():
    result = summarize_attempts([row(), row("two", status="error", latencyMs=900,
                                            responseReceived=False, usage=None)])
    overall = result["overall"]
    assert overall["attempts"] == 2
    assert overall["responsesReceived"] == 1
    assert overall["latencyMs"]["p95"] == 900
    assert overall["tokens"]["total_tokens"] == {
        "observedAttempts": 1, "complete": False, "observedTotal": 3, "total": None}
    assert overall["costUsd"]["total"] is None
    assert overall["nativeLlmCalls"]["total"] is None
    assert result["usableCompletions"] is None
    assert result["qualificationEligible"] is False


def test_observed_zero_is_distinct_from_unknown():
    result = summarize_attempts([row(usage=dict.fromkeys(
        ("prompt_tokens", "completion_tokens", "total_tokens"), 0), costUsd=0, nativeLlmCalls=0)])
    assert result["overall"]["tokens"]["total_tokens"]["total"] == 0
    assert result["overall"]["costUsd"]["total"] == 0


def test_missing_latency_never_becomes_a_success_subset_percentile():
    result = summarize_attempts([row(), row("two", latencyMs=None)])
    assert result["overall"]["latencyMs"]["p95"] is None
    assert result["overall"]["latencyMs"]["observedTotal"] == 100


def test_aggregate_overflow_does_not_emit_infinity():
    with pytest.raises(ValueError):
        summarize_attempts([row(latencyMs=1e308), row("two", latencyMs=1e308)])


def test_role_aggregation_and_nonmutation():
    rows = [row(secret="sensitive", response="private"), row("two", roleId="portfolio_analyst")]
    before = copy.deepcopy(rows)
    result = summarize_attempts(rows)
    assert rows == before
    assert set(result["byRole"]) == {"editor_author", "portfolio_analyst"}
    assert "private" not in str(result) and "sensitive" not in str(result)


@pytest.mark.parametrize("overrides", [
    {"latencyMs": -1}, {"latencyMs": float("nan")}, {"latencyMs": True},
    {"costUsd": float("inf")}, {"nativeLlmCalls": 1.5},
    {"responseReceived": "yes"}, {"roleId": "unknown"}, {"status": "approved"},
    {"roleId": []}, {"status": {}},
    {"usage": {"total_tokens": True}}, {"usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 9}},
])
def test_invalid_measurements_fail_closed(overrides):
    with pytest.raises(ValueError):
        summarize_attempts([row(**overrides)])


def test_duplicate_attempt_and_empty_observations():
    with pytest.raises(ValueError, match="unique"):
        summarize_attempts([row(), row()])
    result = summarize_attempts([])["overall"]
    assert result["attempts"] == 0
    assert result["tokens"]["total_tokens"]["total"] is None
    assert result["latencyMs"]["p95"] is None


def raw():
    return {"metadata": {"battery": {"name": "maxis-editor-component", "selectedCaseIds": ["CMP2", "CMP3"]}},
            "cases": [{"caseId": "CMP2", "latencyMs": 10, "responseReceived": True,
                       "automaticPass": True, "response": {"status": "blocked"}},
                      {"caseId": "CMP3", "latencyMs": 20, "errorCode": "timeout"}]}


def test_existing_editor_adapter_keeps_errors_and_does_not_grade_automatic_pass():
    result = summarize_attempts(editor_observations(raw()))
    assert result["overall"]["statuses"] == {"blocked": 1, "error": 1}
    assert result["usableCompletions"] is None


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unsupported"])
def test_editor_adapter_rejects_incomplete_or_unknown_inputs(mutation):
    value = raw()
    if mutation == "missing":
        value["cases"].pop()
    elif mutation == "duplicate":
        value["cases"].append(value["cases"][0])
    else:
        value["metadata"]["battery"]["name"] = "different-suite"
    with pytest.raises(ValueError):
        editor_observations(value)


@pytest.mark.parametrize("value", [None, [], {}, {"metadata": []}, {"metadata": {"battery": []}}])
def test_malformed_editor_metadata_is_rejected(value):
    with pytest.raises(ValueError):
        editor_observations(value)
