"""OpenAI/AOAI/OpenAI-compatible 모델의 기능을 합성 입력으로 탐지하고 캐시한다."""

from __future__ import annotations

import time


_CACHE: dict[tuple[str, str], dict] = {}


def _key(tier: str) -> tuple[str, str]:
    from app.agent import config as cfg
    return cfg.settings_signature(), str(tier or "complex")


def get(tier: str = "complex") -> dict:
    return dict(_CACHE.get(_key(tier)) or {})


def record(tier: str, capability: str, supported: bool, error: str = "") -> None:
    key = _key(tier)
    row = _CACHE.setdefault(key, {"tier": tier, "checked": {}, "errors": {}})
    row["checked"][capability] = bool(supported)
    if error:
        row["errors"][capability] = str(error)[:240]
    elif capability in row["errors"]:
        row["errors"].pop(capability, None)


def reset() -> None:
    _CACHE.clear()


def _brief(exc: Exception) -> str:
    return " ".join(str(exc or "").split())[:240]


def probe_tier(tier: str = "complex") -> dict:
    """프로젝트 정보 없이 JSON mode와 tool-calling 지원 여부만 검사한다."""
    from langchain_core.tools import tool
    from app.agent import config as cfg

    schema = {
        "title": "CapabilityProbe",
        "type": "object",
        "properties": {"value": {"type": "string", "enum": ["pong"]}},
        "required": ["value"], "additionalProperties": False,
    }
    result = {"tier": tier, "model": cfg.chat_model(tier), "checked": {}, "errors": {}}

    def attempt(name, fn):
        started = time.time()
        try:
            fn()
            result["checked"][name] = True
            result.setdefault("latencyMs", {})[name] = int((time.time() - started) * 1000)
            record(tier, name, True)
        except Exception as exc:
            message = _brief(exc)
            result["checked"][name] = False
            result["errors"][name] = message
            result.setdefault("latencyMs", {})[name] = int((time.time() - started) * 1000)
            record(tier, name, False, message)

    attempt("plain_chat", lambda: cfg.get_llm(temperature=0, tier=tier, max_tokens=8)
            .invoke("Return only the word pong."))
    attempt("json_schema", lambda: cfg.get_llm(temperature=0, tier=tier, max_tokens=32)
            .with_structured_output(schema, method="json_schema")
            .invoke("Return value=pong."))
    attempt("json_object", lambda: cfg.get_llm(temperature=0, tier=tier, max_tokens=32)
            .with_structured_output(schema, method="json_mode")
            .invoke("Return one JSON object with value=pong."))

    @tool
    def capability_echo(value: str) -> str:
        """Return the supplied harmless probe value."""
        return value

    def one_tool():
        msg = cfg.get_llm(temperature=0, tier=tier, max_tokens=64).bind_tools(
            [capability_echo], tool_choice="capability_echo", parallel_tool_calls=False).invoke(
                "Call capability_echo once with value=pong.")
        calls = getattr(msg, "tool_calls", None) or []
        if not calls or calls[0].get("name") != "capability_echo":
            raise ValueError("서버가 요청한 tool call을 반환하지 않았습니다.")
    attempt("tools", one_tool)

    def parallel_tools():
        msg = cfg.get_llm(temperature=0, tier=tier, max_tokens=96).bind_tools(
            [capability_echo], parallel_tool_calls=True).invoke(
                "Call capability_echo twice independently, with value=one and value=two.")
        calls = getattr(msg, "tool_calls", None) or []
        if len(calls) < 2:
            raise ValueError("parallel tool calls를 반환하지 않았습니다.")
    attempt("parallel_tools", parallel_tools)
    result["degraded"] = not all(result["checked"].values())
    return result


def probe_all() -> dict:
    """같은 모델 이름은 한 번만 호출하되 main/simple tier 결과를 모두 표시한다."""
    from app.agent import config as cfg
    rows, by_model = {}, {}
    for tier in ("complex", "simple"):
        model = cfg.chat_model(tier)
        if model in by_model:
            row = dict(by_model[model]); row["tier"] = tier
            rows[tier] = row
            for cap, ok in (row.get("checked") or {}).items():
                record(tier, cap, ok, (row.get("errors") or {}).get(cap, ""))
        else:
            row = probe_tier(tier)
            rows[tier] = row; by_model[model] = row
    return rows


__all__ = ["get", "record", "reset", "probe_tier", "probe_all"]
