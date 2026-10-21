"""Local, content-free recovery guidance for a validated blocked remote result.

This does not repair or accept a rejected payload. Callers must validate the result,
release binding and knowledge proof before projecting its warning codes here.
Only exact known codes select constant messages; arbitrary remote text is ignored.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BlockedRecovery:
    code: str
    message: str
    next_action: str


_STALE = frozenset({"stale_source", "stale_role_context"})
_INCOMPLETE = frozenset({
    "source_limit", "source_truncated", "partial_source", "role_context_limit",
    "partial_role_context", "fact_source_omitted", "metric_source_omitted",
})
_SCHEMA = frozenset({
    "contract_guard:payload_type_mismatch", "contract_guard:invalid_json",
    "contract_guard:json_object_required", "contract_guard:missing_inputs_required",
    "contract_guard:ok_has_missing_inputs",
})
_GROUNDING = frozenset({
    "contract_guard:claim_not_grounded", "contract_guard:payload_claims_required",
    "contract_guard:intent_source_fact_forbidden",
    "contract_guard:payload_claim_coverage_missing",
    "contract_guard:payload_claim_text_mismatch",
})
_GUIDANCE = {
    "stale_evidence": (
        "조회 근거가 오래되어 적용 가능한 결과를 확정하지 못했습니다.",
        "현재 티켓·문서와 이전 대화의 근거를 다시 조회한 뒤 요청하세요.",
    ),
    "incomplete_evidence": (
        "전달된 근거에 누락 또는 잘림이 있어 적용 가능한 결과를 확정하지 못했습니다.",
        "조회 범위를 나누어 누락된 자료를 다시 조회한 뒤 요청하세요.",
    ),
    "invalid_result_contract": (
        "Workflow 응답의 필드·타입 또는 상태가 요청 계약과 맞지 않습니다.",
        "관리자가 해당 Workflow의 출력 스키마와 필수 필드를 점검해야 합니다.",
    ),
    "ungrounded_result": (
        "응답 내용과 제공된 근거의 연결을 검증하지 못했습니다.",
        "관리자가 해당 Workflow의 주장·출처 연결과 초안 필드의 근거를 점검해야 합니다.",
    ),
    "unusable_result": (
        "MAXIS에서 적용 가능한 결과를 받지 못했습니다.",
        "요청 범위와 조회 근거를 확인하고, 관리자가 Workflow 차단 기록을 점검해야 합니다.",
    ),
}


def blocked_recovery(*, warnings: tuple[str, ...],
                     evidence_flags: tuple[str, ...]) -> BlockedRecovery:
    """Explain a blocked result without echoing content or inventing missing fields.

Evidence flags come from the locally guarded request, not the remote response.
They describe a known input limitation, not a claimed root cause inside the model.
"""
    flags = {value for value in evidence_flags if type(value) is str}
    codes = {value for value in warnings if type(value) is str}
    # A validated guard code identifies the rejection. Context flags describe
    # limitations, not proof that they caused a different schema/grounding failure.
    if codes & _SCHEMA:
        code = "invalid_result_contract"
    elif codes & _GROUNDING:
        code = "ungrounded_result"
    elif flags & _STALE:
        code = "stale_evidence"
    elif flags & _INCOMPLETE:
        code = "incomplete_evidence"
    else:
        code = "unusable_result"
    return recovery_for_code(code)


def recovery_for_code(code: str) -> BlockedRecovery:
    """Recreate local guidance without trusting persisted or exception prose."""
    if type(code) is not str or code not in _GUIDANCE:
        code = "unusable_result"
    message, next_action = _GUIDANCE[code]
    # A later Role can fail after an earlier, explicitly approved write succeeded.
    # Describe this rejected response only, never claim that the whole turn wrote nothing.
    return BlockedRecovery(code, message + " 이 응답은 변경에 적용하지 않았습니다.", next_action)
