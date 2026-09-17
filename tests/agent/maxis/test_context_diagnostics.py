from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from app.agent.maxis import backend
from app.agent.maxis.context_guard import ContextGuard, ContextPolicy
from app.agent.workflow.role_manifest import ROLE_SPECS
from tools.maxis_context_diagnostics import describe_context


NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def _pack(role, state, *, synthetic=False, budget=6144):
    rows, allowed = backend._role_context(role, state)
    return ContextGuard(ContextPolicy(
        allowed_scopes={}, allowed_context_keys=allowed,
        max_total_utf8_bytes=budget,
    ), b"offline-context-diagnostics-key").build(
        request_id="private-request-id", use_case=role, query="private-user-input",
        payload_schema={"type": "object"}, user_identity="private-identity",
        requested_at=max([NOW, *(row["observed_at"] for row in rows)]),
        sources=[], role_context=[] if synthetic else rows,
        additional_flags=("synthetic_only",) if synthetic else (),
    )


@pytest.mark.parametrize("role", ["work_architect", "result_integrator"])
def test_count_and_field_size_omissions_are_distinct_and_observational(role):
    keys = ROLE_SPECS[role].remote_context_keys
    state = {key: "private-value" for key in keys}
    state.update(correlation_started_at=NOW.isoformat(), **{keys[0]: "z" * 4000})
    pack = _pack(role, state)
    before_state, before_pack = deepcopy(state), pack.model_dump()
    result = describe_context(role, state, pack)
    assert result["eligibleKeys"] == list(keys)
    assert result["projectedKeys"] == list(keys[:8])
    assert result["countLimitOmittedKeys"] == list(keys[8:])
    assert result["perFieldSizeOmittedKeys"] == [keys[0]]
    assert result["freshnessMissingKeys"] == []
    assert result["partial"] is True
    assert result == describe_context(role, state, pack)
    assert (state, pack.model_dump()) == (before_state, before_pack)


def test_aggregate_budget_omission_and_removal_are_separate():
    role = "work_architect"
    keys = ROLE_SPECS[role].remote_context_keys[:8]
    state = {key: "a" * 750 for key in keys}
    state["correlation_started_at"] = NOW.isoformat()
    result = describe_context(role, state, _pack(role, state))
    assert result["perFieldSizeOmittedKeys"] == []
    assert result["packBudgetOmittedKeys"]
    assert result["packBudgetRemovedKeys"]
    assert not set(result["packBudgetOmittedKeys"]) & set(result["packBudgetRemovedKeys"])
    assert result["packUtf8Bytes"] <= result["limits"]["packUtf8Bytes"]


def test_missing_value_freshness_unknown_names_and_values_never_leak():
    state = {"situation": "private-value", "private-field-name": "private-secret",
             "approval_token": "Authorization: Bearer private-secret-value"}
    pack = _pack("work_architect", state)
    unknown = pack.role_context[0].model_copy(update={"name": "private-context-name"})
    pack = pack.model_copy(update={
        "role_context": (*pack.role_context, unknown),
        "flags": (*pack.flags, "private-flag-text"),
    })
    result = describe_context("work_architect", state, pack)
    assert result["freshnessMissingKeys"] == ["situation"]
    assert "request_plan" in result["missingValueKeys"]
    assert result["finalKeys"] == ["situation"]
    assert "private-" not in json.dumps(result)
    assert result == describe_context("work_architect", state, pack)


def test_dev_replacement_is_not_a_pack_budget_failure():
    state = {"situation": "private-value", "correlation_started_at": NOW.isoformat()}
    result = describe_context("work_architect", state, _pack("work_architect", state, synthetic=True))
    assert result["syntheticReplacementKeys"] == ["situation"]
    assert result["packBudgetRemovedKeys"] == result["unexplainedRemovedKeys"] == []
    assert result["finalKeys"] == []


def test_role_mismatch_and_unknown_role_are_rejected_without_echo():
    pack = _pack("work_architect", {})
    for role in ("result_integrator", "private-role-value"):
        with pytest.raises(ValueError, match="role mismatch or unknown role") as caught:
            describe_context(role, {}, pack)
        assert "private-" not in str(caught.value)


def test_carried_observation_still_counts_when_field_is_omitted():
    value = "a" * 4000
    state = {"situation": value, "role_context_carryover": {"situation": {
        "sha256": hashlib.sha256(json.dumps(value).encode()).hexdigest(),
        "observed_at": NOW.isoformat(),
    }}}
    result = describe_context("work_architect", state, _pack("work_architect", state))
    assert result["perFieldSizeOmittedKeys"] == ["situation"]
    assert result["freshnessMissingKeys"] == []


def test_source_counts_and_query_bytes_are_metadata_only(evidence):
    source = evidence.sources[0].model_copy(update={"truncated": True})
    pack = evidence.model_copy(update={"use_case": "work_architect", "sources": (source,)})
    result = describe_context("work_architect", {}, pack)
    assert result["sourceCount"] == result["truncatedSourceCount"] == 1
    assert result["queryUtf8Bytes"] == len(pack.query.encode("utf-8"))
    encoded = json.dumps(result)
    for private in (source.canonical_id, source.source_id, source.title, pack.query, pack.request_id):
        assert private not in encoded
