"""OpenAI/AOAI/OpenAI-compatible 모델의 기능을 합성 입력으로 탐지하고 캐시한다."""

from __future__ import annotations

import time


_CACHE: dict[tuple[str, str], dict] = {}


def _key(tier: str, config_id: str = "") -> tuple[str, str]:
    from app.agent import config as cfg
    return cfg.settings_signature(config_id), str(tier or "complex")


def get(tier: str = "complex", config_id: str = "") -> dict:
    row = dict(_CACHE.get(_key(tier, config_id)) or {})
    # Versioned model profile is the pre-probed floor. Runtime probe results override it.
    try:
        from app.agent import config as cfg
        from app.agent.model_profiles import capabilities_for
        definition = cfg.chat_definition(tier, config_id=config_id)
        declared = capabilities_for(definition.model, definition.model_profile)
        checked = {k: v for k, v in declared.items() if isinstance(v, bool)}
        checked.update(row.get("checked") or {})
        row["checked"] = checked
    except Exception:
        pass
    return row


def record(tier: str, capability: str, supported: bool, error: str = "", config_id: str = "") -> None:
    key = _key(tier, config_id)
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


def native_tools_allowed(config_id: str = "", *, tier: str = "complex") -> bool:
    """현재 provider에 native tool payload를 보내도 되는가.

    운영 LLM gateway는 provider 표기와 무관하게 chat text만 지원한다고 본다. 기능 탐지
    명목으로 ``tools``를 한 번 보내 보는 것 자체가 prod 오류 로그를 만들므로 아예 요청하지
    않는다. local tool은 prompt JSON 계획과 deterministic runner를 통해 계속 실행할 수 있다.
    ``tier``는 실제 tool-decision 호출 endpoint여야 하며 synthesis tier로 대체하지 않는다.
    """
    from app.agent import config as cfg
    # 운영은 provider 이름과 무관하게 native tool 지원이 0이라고 가정한다. 사내 gateway를
    # AOAI 이름으로 등록해도 tools payload가 새어 나가면 안 된다.
    try:
        from app.infra.settings import get_settings
        if str(get_settings().jira_env or "").casefold() == "prod":
            return False
    except Exception:
        pass
    declared = get(tier, config_id).get("checked") or {}
    current_provider = cfg.provider(config_id) if config_id else cfg.provider()
    return current_provider != "openai_compat" and declared.get("tools") is not False


def probe_tier(tier: str = "complex", config_id: str = "") -> dict:
    """프로젝트 정보 없이 JSON mode와 tool-calling 지원 여부만 검사한다."""
    from langchain_core.tools import tool
    from app.agent import config as cfg

    schema = {
        "title": "CapabilityProbe",
        "type": "object",
        "properties": {"value": {"type": "string", "enum": ["pong"]}},
        "required": ["value"], "additionalProperties": False,
    }
    result = {"tier": tier, "model": cfg.chat_model(tier, config_id), "checked": {}, "errors": {}}

    def attempt(name, fn):
        started = time.time()
        try:
            fn()
            result["checked"][name] = True
            result.setdefault("latencyMs", {})[name] = int((time.time() - started) * 1000)
            record(tier, name, True, config_id=config_id)
        except Exception as exc:
            message = _brief(exc)
            result["checked"][name] = False
            result["errors"][name] = message
            result.setdefault("latencyMs", {})[name] = int((time.time() - started) * 1000)
            record(tier, name, False, message, config_id=config_id)

    attempt("plain_chat", lambda: cfg.get_llm(temperature=0, tier=tier, max_tokens=8, config_id=config_id)
            .invoke("Return only the word pong."))
    attempt("json_schema", lambda: cfg.get_llm(temperature=0, tier=tier, max_tokens=32, config_id=config_id)
            .with_structured_output(schema, method="json_schema")
            .invoke("Return value=pong."))
    attempt("json_object", lambda: cfg.get_llm(temperature=0, tier=tier, max_tokens=32, config_id=config_id)
            .with_structured_output(schema, method="json_mode")
            .invoke("Return one JSON object with value=pong."))

    @tool
    def capability_echo(value: str) -> str:
        """Return the supplied harmless probe value."""
        return value

    def one_tool():
        msg = cfg.get_llm(temperature=0, tier=tier, max_tokens=64, config_id=config_id).bind_tools(
            [capability_echo], tool_choice="capability_echo", parallel_tool_calls=False).invoke(
                "Call capability_echo once with value=pong.")
        calls = getattr(msg, "tool_calls", None) or []
        if not calls or calls[0].get("name") != "capability_echo":
            raise ValueError("서버가 요청한 tool call을 반환하지 않았습니다.")
    if native_tools_allowed(config_id, tier=tier):
        attempt("tools", one_tool)
    else:
        message = "운영/provider 정책: native tools 요청을 보내지 않음"
        result["checked"]["tools"] = False
        result["errors"]["tools"] = message
        record(tier, "tools", False, message, config_id=config_id)

    def parallel_tools():
        msg = cfg.get_llm(temperature=0, tier=tier, max_tokens=96, config_id=config_id).bind_tools(
            [capability_echo], parallel_tool_calls=True).invoke(
                "Call capability_echo twice independently, with value=one and value=two.")
        calls = getattr(msg, "tool_calls", None) or []
        if len(calls) < 2:
            raise ValueError("parallel tool calls를 반환하지 않았습니다.")
    if native_tools_allowed(config_id, tier=tier):
        attempt("parallel_tools", parallel_tools)
    else:
        message = "native tools가 비활성화되어 parallel tools도 사용하지 않음"
        result["checked"]["parallel_tools"] = False
        result["errors"]["parallel_tools"] = message
        record(tier, "parallel_tools", False, message, config_id=config_id)
    result["degraded"] = not all(result["checked"].values())
    return result


def probe_all(config_id: str = "") -> dict:
    """같은 effective endpoint만 한 번 호출하되 main/simple 결과를 모두 표시한다."""
    from app.agent import config as cfg
    rows, by_identity = {}, {}
    for tier in ("complex", "simple"):
        definition = cfg.chat_definition(tier, config_id=config_id)
        identity = (definition.provider, definition.model, definition.base_url,
                    definition.api_version)
        if identity in by_identity:
            row = dict(by_identity[identity]); row["tier"] = tier
            rows[tier] = row
            for cap, ok in (row.get("checked") or {}).items():
                record(tier, cap, ok, (row.get("errors") or {}).get(cap, ""), config_id=config_id)
        else:
            row = probe_tier(tier, config_id)
            rows[tier] = row; by_identity[identity] = row
    return rows


__all__ = ["get", "record", "reset", "native_tools_allowed", "probe_tier", "probe_all"]
