"""Turn-bound local terminal projection of a validated MAXIS refusal.

Only the backend's typed refusal can mint this content-free marker. Its process-local
seal prevents checkpoint/model data from creating a new deterministic branch. The
existing per-Role sidecar reset and the session's turn nonce prevent later-turn reuse.
"""

import hashlib
import hmac
import json
import secrets

from app.agent.maxis.recovery import recovery_for_code
from app.agent.workflow.role_manifest import ROLE_SPECS
from app.agent.workflow.state import ROLE_QUESTIONS_RESET, note


_KEY = secrets.token_bytes(32)
_CONTRACT = "maxis-blocked-terminal.v1"
_SIDECAR = "blocked_terminal"


def _binding(state: dict) -> str:
    values = [state.get(key) for key in ("thread_id", "turn_attempt_id", "correlation_id")]
    if any(type(value) is not str or not 0 < len(value) <= 300 for value in values):
        return ""
    return hashlib.sha256(json.dumps(values, ensure_ascii=True).encode("ascii")).hexdigest()


def _signature(marker: dict) -> str:
    return hmac.new(_KEY, json.dumps(marker, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode("ascii"), hashlib.sha256).hexdigest()


def blocked_failure_patch(state: dict, role: str, error: Exception) -> dict | None:
    """Consume the typed exception, never classify arbitrary state.error prose."""
    from app.agent.maxis.backend import MaxisBlockedResult

    if not isinstance(error, MaxisBlockedResult):
        return None
    recovery = recovery_for_code(getattr(getattr(error, "recovery", None), "code", None))
    text = recovery.message + " " + recovery.next_action
    patch = {"error": f"[{role}] {text}", "trace": note(state, role, "MAXIS 응답 차단")}
    binding = _binding(state)
    if type(error) is MaxisBlockedResult and error.role_id == role and role in ROLE_SPECS and binding:
        marker = {"contract": _CONTRACT, "role": role, "code": recovery.code, "binding": binding}
        patch["runtime_role_sidecars"] = {role: {_SIDECAR: {**marker, "signature": _signature(marker)}}}
        if role == "result_integrator":
            patch.update(blocked_terminal_patch({**state, **patch}) or {})
    return patch


def _verified_block(state: dict):
    """Read the same authenticated outcome at routing and rendering boundaries."""
    binding = _binding(state)
    sidecars = state.get("runtime_role_sidecars")
    if not binding or not isinstance(sidecars, dict):
        return None
    for role in ROLE_SPECS:
        sidecar = sidecars.get(role)
        raw = sidecar.get(_SIDECAR) if isinstance(sidecar, dict) else None
        if not isinstance(raw, dict) or set(raw) != {"contract", "role", "code", "binding", "signature"}:
            continue
        if any(type(value) is not str for value in raw.values()):
            continue
        recovery = recovery_for_code(raw["code"])
        if (raw["contract"] != _CONTRACT or raw["role"] != role or raw["binding"] != binding
                or raw["code"] != recovery.code or len(raw["signature"]) != 64
                or any(char not in "0123456789abcdef" for char in raw["signature"])):
            continue
        marker = {key: value for key, value in raw.items() if key != "signature"}
        if not hmac.compare_digest(raw["signature"], _signature(marker)):
            continue
        return role, recovery
    return None


def has_blocked_terminal(state: dict) -> bool:
    """A verified current-turn refusal stops new work; error prose is not authority."""
    return _verified_block(state) is not None


def blocked_terminal_patch(state: dict) -> dict | None:
    """Return constant guidance only for an authentic marker from this exact turn."""
    verified = _verified_block(state)
    if verified is None:
        return None
    from langchain_core.messages import AIMessage

    role, recovery = verified
    text = recovery.message + " " + recovery.next_action
    return {"reply": text, "error": f"[{role}] {text}", "messages": [AIMessage(content=text)],
            "approval_token": "", "comment_token": "", "draft": {}, "change_plan": {},
            "questions": [], "runtime_role_questions": dict(ROLE_QUESTIONS_RESET),
            "trace": note(state, "result_integrator", "검증된 MAXIS 차단 안내")}
