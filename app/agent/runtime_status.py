"""Feature readiness resolved from the same pinned routes used for execution.

MAXIS connection verification, semantic authority and local model configuration are
different facts. A healthy remote candidate never makes a local route ready.
"""
from __future__ import annotations

from app.agent.maxis import backend

INTERNAL_ASSIST_CAPABILITY = "maxis-internal-assist.v1"
CORE_ROLES = (
    "request_architect", "query_specialist", "knowledge_curator",
    "work_architect", "auditor", "result_integrator",
)
INTERNAL_SOURCES = frozenset({"jira", "confluence", "comments", "people"})


class RuntimeUnavailable(backend.MaxisRuntimeError):
    """A configured feature cannot execute its declared runtime."""


def capability_for_pin(pin: dict) -> str:
    routes = pin.get("runtime_role_routes") or {}
    return (INTERNAL_ASSIST_CAPABILITY
            if pin.get("runtime_profile")
            and all(routes.get(role) == "maxis" for role in CORE_ROLES)
            else "")


def bounded_internal_assist(state: dict) -> bool:
    return bool(capability_for_pin(state))


def _row(engine: str, profile: str, ready: bool, code: str = "", reason: str = "",
         action: str = "") -> dict:
    label = ""
    if profile:
        from app.agent.maxis import profiles
        try:
            label = profiles.get(profile).get("display_name") or "MAXIS 연결"
        except (KeyError, ValueError):
            label = "MAXIS 연결"
    return {"engine": engine, "profile": profile, "profileLabel": label, "ready": ready,
            "reasonCode": code, "reason": reason, "nextAction": action}


def _remote_failure(engine: str, profile: str, exc: Exception) -> dict:
    # Project a fixed vocabulary; backend exceptions can contain deployment details.
    text = str(exc).lower()
    if "kill switch" in text:
        return _row(engine, profile, False, "maxis_stopped",
                    "MAXIS 긴급 중단이 켜져 있습니다.", "maxis_settings")
    if "synthetic" in text:
        return _row(engine, profile, False, "maxis_validation_only",
                    "이 연결은 합성 검증 모드입니다. MAXIS 설정에서 실제 사용할 프로필을 적용하세요.",
                    "maxis_settings")
    if "signed" in text or "qualification" in text or "promotion" in text:
        return _row(engine, profile, False, "maxis_qualification_required",
                    "현재 MAXIS 배포의 운영 자격 검증을 완료하세요.", "maxis_settings")
    return _row(engine, profile, False, "maxis_runtime_unavailable",
                "MAXIS 프로필의 연결 확인·활성화와 기능별 실행 경로를 확인하세요.",
                "maxis_settings")


def resolve(feature: str, state: dict | None = None) -> dict:
    """Return content-free readiness; never probe, activate or call a model.

    Passing a pinned state checks that exact execution selection. Without one,
    construct the same pin a new conversation/editor request would use.
    """
    if feature not in {"conversation", "editor"}:
        raise ValueError("Unknown Agent runtime feature.")
    from app.agent import config
    from app.agent.maxis import profiles

    engine, profile = "local", ""
    try:
        routes = ((state or {}).get("runtime_role_routes")
                  if state is not None else backend._canonical_role_routes()) or {}
        roles = CORE_ROLES if feature == "conversation" else ("editor_author",)
        modes = {routes.get(role, "local") for role in roles}
        engine = "maxis" if modes == {"maxis"} else (
            "mixed" if "maxis" in modes else "local")
        profile = str((state or {}).get("runtime_profile") or profiles.active_profile_name() or "")
        pin = state if state is not None else backend.thread_runtime_pin()
        # resolve_role is the execution boundary: it validates the pin, current
        # activation, kill switch and release authority before returning MAXIS.
        for role in roles:
            backend.resolve_role(role, pin)
        if engine == "maxis":
            return _row(engine, profile, True)
        ready, reason = config.llm_ready()
        return _row(engine, profile if engine == "mixed" else "", ready,
                    "" if ready else "local_llm_required", reason,
                    "" if ready else ("maxis_settings" if profile else "llm_settings"))
    except Exception as exc:
        return _remote_failure(engine, profile, exc)


def require(feature: str, state: dict) -> dict:
    result = resolve(feature, state)
    if not result["ready"]:
        raise RuntimeUnavailable(result["reason"])
    return result


def status() -> dict:
    return {feature: resolve(feature) for feature in ("conversation", "editor")}


def require_internal_sources(state: dict, plan: dict) -> None:
    """Reject unsupported acquisition before the first tool or network operation."""
    if not bounded_internal_assist(state):
        return
    rows = plan.get("queries") or plan.get("reads") or []
    if any(not isinstance(row, dict)
           or str(row.get("source") or "") not in INTERNAL_SOURCES for row in rows):
        raise RuntimeUnavailable(
            "MAXIS 내부 업무 모드는 Jira·Confluence·코멘트·확인된 사람 조회를 지원합니다. "
            "외부 웹·GitHub·외부 MCP 조사는 지원하지 않습니다.")
