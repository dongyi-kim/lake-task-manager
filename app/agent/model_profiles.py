"""Semantic task profile -> model/provider request parameter resolution.

Role은 ``fast_structured``·``balanced``·``reasoning``만 선택한다. 모델 family, thinking
제어 방식, sampling parameter 지원 여부는 versioned YAML profile이 소유한다. 이 경계를
유지하면 Qwen·OpenAI의 parameter 차이가 Role 코드에 퍼지지 않는다.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Any, Literal

import yaml


TaskProfile = Literal["fast_structured", "balanced", "reasoning"]
TASK_PROFILES = ("fast_structured", "balanced", "reasoning")
ExecutionLayer = Literal[
    "deterministic", "projection", "lightweight_semantic", "deep_semantic",
]
EXECUTION_LAYERS = (
    "deterministic", "projection", "lightweight_semantic", "deep_semantic",
)
FINAL_ONLY_CONTRACTS = ("structured", "semantic_memo", "typed_projection")
log = logging.getLogger("agent.model_profiles")
_CACHE: dict[str, Any] = {"mtime": None, "data": None}


def _path() -> Path:
    """Project/deployment config override, then bundled repository config."""
    try:
        from app.infra.settings import BASE_DIR, RESOURCE_DIR
        candidates = (Path(BASE_DIR) / "config" / "llm_profiles.yml",
                      Path(RESOURCE_DIR) / "config" / "llm_profiles.yml")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    except Exception:
        pass
    return Path(__file__).resolve().parents[2] / "config" / "llm_profiles.yml"


def load_profiles() -> dict:
    path = _path()
    mtime = path.stat().st_mtime_ns
    if _CACHE["data"] is None or _CACHE["mtime"] != mtime:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if int(data.get("version") or 0) < 1:
            raise ValueError("llm_profiles.yml version이 필요합니다.")
        missing = set(TASK_PROFILES) - set(data.get("task_profiles") or {})
        if missing:
            raise ValueError(f"task profile 누락: {sorted(missing)}")
        _CACHE.update(mtime=mtime, data=data)
    return deepcopy(_CACHE["data"])


def reset() -> None:
    _CACHE.update(mtime=None, data=None)


def model_profile(model: str, explicit: str = "") -> tuple[str, dict]:
    data = load_profiles()
    rows = data.get("model_profiles") or {}
    if explicit:
        if explicit not in rows:
            raise ValueError(f"알 수 없는 model profile: {explicit}")
        return explicit, rows[explicit]
    value = str(model or "")
    for name, row in rows.items():
        if any(re.search(pattern, value) for pattern in (row.get("match") or [])):
            return name, row
    raise ValueError(f"model profile을 찾지 못했습니다: {model}")


def capabilities_for(model: str, explicit: str = "") -> dict:
    return dict(model_profile(model, explicit)[1].get("capabilities") or {})


def supports_execution_layer(model: str, layer: ExecutionLayer, *,
                             explicit_model_profile: str = "") -> bool:
    """Return whether a model profile is qualified for one semantic execution layer.

    Qualification is versioned configuration backed by evaluation, never inferred from a
    model name or from wire-format features such as JSON Schema/tool calling. Deterministic
    work does not invoke a model and consequently no model can claim that layer.
    """
    if layer not in EXECUTION_LAYERS:
        raise ValueError(f"알 수 없는 execution layer: {layer}")
    if layer == "deterministic":
        return False
    capabilities = capabilities_for(model, explicit_model_profile)
    declared = capabilities.get("execution_layers") or ()
    if not isinstance(declared, (list, tuple, set)):
        raise ValueError("model profile capabilities.execution_layers는 목록이어야 합니다.")
    return layer in {str(value) for value in declared}


def profile_for_contract(model: str, requested: TaskProfile, output_contract: str = "", *,
                         explicit_model_profile: str = "") -> TaskProfile:
    """Adapt a semantic profile only when a provider cannot safely return the contract.

    A thinking model that does not separate reasoning from the final answer can exhaust its
    output budget before emitting a bounded memo or structured JSON. LTM therefore keeps the
    semantic ``reasoning`` Role assignment, but uses the non-thinking balanced profile for
    final-only transport contracts. The original semantic profile still selects the contract
    row in ``resolve``.
    """
    if output_contract not in FINAL_ONLY_CONTRACTS or requested != "reasoning":
        return requested
    capabilities = capabilities_for(model, explicit_model_profile)
    if capabilities.get("reasoning") is True and capabilities.get("reasoning_separation") is False:
        return "balanced"
    return requested


def _merge(base: dict, patch: dict) -> dict:
    out = dict(base or {})
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _translated(parameters: dict, capabilities: dict, reasoning: str) -> dict:
    out = dict(parameters)
    control = str(capabilities.get("reasoning_control") or "none")
    if control == "qwen_thinking":
        body = dict(out.pop("extra_body", {}) or {})
        template = dict(body.get("chat_template_kwargs") or {})
        template["enable_thinking"] = reasoning == "on"
        body["chat_template_kwargs"] = template
        out["extra_body"] = body
    elif control == "openai_effort":
        if reasoning == "off":
            out["reasoning_effort"] = "minimal"
    else:
        out.pop("reasoning_effort", None)

    extra_names = ("top_k", "min_p", "repetition_penalty")
    body = dict(out.pop("extra_body", {}) or {})
    for key in extra_names:
        value = out.pop(key, None)
        if value is not None and capabilities.get(key) is not False:
            body[key] = value
    if body:
        out["extra_body"] = body

    for key in ("temperature", "top_p", "presence_penalty", "reasoning_effort"):
        capability = "reasoning" if key == "reasoning_effort" else key
        if capabilities.get(capability) is False:
            out.pop(key, None)
    return out


@dataclass(frozen=True)
class EffectiveConfig:
    task_profile: TaskProfile
    model_profile: str
    capabilities: dict
    parameters: dict
    sources: tuple[str, ...]

    def debug(self) -> dict:
        return {"taskProfile": self.task_profile, "modelProfile": self.model_profile,
                "capabilities": self.capabilities, "parameters": self.parameters,
                "precedence": list(self.sources)}


def resolve(model: str, provider: str, task_profile: TaskProfile = "balanced", *,
            explicit_model_profile: str = "", role_parameters: dict | None = None,
            explicit: dict | None = None, runtime_capabilities: dict | None = None,
            output_contract: str = "", semantic_profile: str = "") -> EffectiveConfig:
    """Resolve effective config with explicit > role/task > model > provider precedence."""
    if task_profile not in TASK_PROFILES:
        raise ValueError(f"알 수 없는 task profile: {task_profile}")
    data = load_profiles()
    profile_name, model_row = model_profile(model, explicit_model_profile)
    caps = _merge(model_row.get("capabilities") or {}, runtime_capabilities or {})
    task_row = (data.get("task_profiles") or {})[task_profile]
    # Semantic work and its wire-format contract are independent. A reasoning Role
    # still needs low-variance, non-thinking JSON emission on providers that cannot
    # separate reasoning from the final payload. Model profiles own that translation.
    contracts = model_row.get("contracts") or {}
    contract_profiles = contracts.get(output_contract) or {}
    # Typed projection repair/correction calls are the same bounded literal transport
    # family as the first projection. Reuse a model's explicitly qualified family profile
    # when a narrower suffix has no override; profiles without that contract (OpenAI/native)
    # keep their existing defaults.
    if (not contract_profiles and output_contract.startswith("typed_projection_")
            and isinstance(contracts.get("typed_projection"), dict)):
        contract_profiles = contracts["typed_projection"]
    contract_row = (contract_profiles.get(semantic_profile or task_profile) or {})

    params: dict = {}
    sources: list[str] = []
    for label, values in (
        ("provider_default", (data.get("provider_defaults") or {}).get(provider) or {}),
        ("model_profile", model_row.get("defaults") or {}),
        ("model_task_profile", (model_row.get("profiles") or {}).get(task_profile) or {}),
        ("model_contract_profile", contract_row),
        ("task_profile", task_row.get("parameters") or {}),
        ("role_profile", role_parameters or {}),
        ("explicit_override", explicit or {}),
    ):
        if values:
            params = _merge(params, values)
            sources.append(label)

    reasoning = str((explicit or {}).get("reasoning") or
                    (role_parameters or {}).get("reasoning") or
                    task_row.get("reasoning") or "off").lower()
    params.pop("reasoning", None)
    translated = _translated(params, caps, reasoning)
    row = EffectiveConfig(task_profile, profile_name, caps, translated, tuple(sources))
    log.debug("effective LLM config %s", json.dumps(row.debug(), ensure_ascii=False, default=str))
    return row


__all__ = ["TaskProfile", "TASK_PROFILES", "ExecutionLayer", "EXECUTION_LAYERS",
           "FINAL_ONLY_CONTRACTS", "EffectiveConfig", "load_profiles", "reset",
           "model_profile", "capabilities_for", "supports_execution_layer",
           "profile_for_contract", "resolve"]
