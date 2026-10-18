"""Recovery guidance never turns a rejected result into an applicable payload."""

import json

import pytest

from app.agent.maxis.recovery import blocked_recovery


@pytest.mark.parametrize("warning,code", [
    ("contract_guard:payload_type_mismatch", "invalid_result_contract"),
    ("contract_guard:payload_claims_required", "ungrounded_result"),
    ("contract_guard:claim_not_grounded", "ungrounded_result"),
    ("contract_guard:intent_source_fact_forbidden", "ungrounded_result"),
    ("contract_guard:payload_claim_coverage_missing", "ungrounded_result"),
    ("contract_guard:payload_claim_text_mismatch", "ungrounded_result"),
])
def test_known_guard_codes_have_specific_constant_recovery(warning, code):
    recovery = blocked_recovery(warnings=(warning,), evidence_flags=())
    assert recovery.code == code
    assert recovery.next_action
    assert "이 응답은 변경에 적용하지 않았습니다" in recovery.message


@pytest.mark.parametrize("flag,code", [
    ("stale_source", "stale_evidence"),
    ("stale_role_context", "stale_evidence"),
    ("source_truncated", "incomplete_evidence"),
    ("partial_role_context", "incomplete_evidence"),
    ("role_context_limit", "incomplete_evidence"),
])
def test_trusted_evidence_limit_explains_requery(flag, code):
    recovery = blocked_recovery(warnings=(), evidence_flags=(flag,))
    assert recovery.code == code
    assert "조회" in recovery.next_action


@pytest.mark.parametrize("known_code", [
    "contract_guard:payload_type_mismatch",
    "contract_guard:intent_source_fact_forbidden",
    "contract_guard:payload_claim_coverage_missing",
    "contract_guard:payload_claim_text_mismatch",
])
def test_unknown_and_adversarial_warning_text_is_not_displayed(known_code):
    warnings = (known_code + " secret-value", "private-url",)
    recovery = blocked_recovery(warnings=warnings, evidence_flags=("unknown_flag",))
    assert recovery.code == "unusable_result"
    output = json.dumps(recovery.__dict__, ensure_ascii=False)
    assert "secret-value" not in output
    assert "private-url" not in output
    assert set(recovery.__dict__) == {"code", "message", "next_action"}


def test_trusted_freshness_precedes_remote_claims():
    recovery = blocked_recovery(
        warnings=("contract_guard:payload_type_mismatch",),
        evidence_flags=("stale_source", "source_truncated"),
    )
    assert recovery.code == "stale_evidence"


def test_empty_block_is_not_mislabeled_as_user_missing_input():
    recovery = blocked_recovery(warnings=(), evidence_flags=())
    assert recovery.code == "unusable_result"
    assert "적용 가능한 결과" in recovery.message
    assert "missing_inputs" not in recovery.__dict__
