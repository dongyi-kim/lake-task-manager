"""Instructor validation/retry over LTM-owned LangChain calls."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Annotated, Any, Callable


BACKEND_ENV = "LTM_AGENT_STRUCTURED_OUTPUT_BACKEND"
FALLBACK_POLICY_ENV = "LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK"
_LENGTH_STOPS = {"length", "max_tokens", "max_output_tokens"}


class InstructorAdapterError(RuntimeError):
    def __init__(self, kind: str, wire_attempts: int, detail: str, *,
                 cause: Exception | None = None):
        super().__init__(detail)
        self.kind, self.wire_attempts, self.cause = kind, wire_attempts, cause


def backend() -> str:
    value = str(os.getenv(BACKEND_ENV, "instructor") or "instructor").strip().casefold()
    if value not in {"instructor", "legacy"}:
        raise ValueError(f"지원하지 않는 structured output backend: {value or '(empty)'}")
    return value


def fallback_policy() -> str:
    value = str(os.getenv(FALLBACK_POLICY_ENV, "allow") or "allow").strip().casefold()
    if value not in {"allow", "forbid"}:
        raise ValueError(f"지원하지 않는 structured backend fallback policy: {value}")
    return value


def _single_root_required_patch(schema: dict, parsed: dict,
                                diagnostic: dict[str, str]) -> tuple[str, dict] | None:
    """Return a strict patch schema only when one root field is the sole JSON Schema error.

    The already parsed object is validated against a copy of the complete contract with
    only that one requirement relaxed. This prevents a cheap patch from laundering a
    second type, cardinality, enum, or extra-property violation.
    """
    if (diagnostic.get("category") != "schema"
            or diagnostic.get("keyword") != "required"
            or diagnostic.get("path") != "$"
            or not isinstance(parsed, dict)):
        return None
    field = str(diagnostic.get("missing") or "").strip()
    required = [str(value) for value in (schema.get("required") or [])]
    properties = schema.get("properties") or {}
    missing = [name for name in required if name not in parsed]
    if len(missing) != 1 or missing[0] != field or field not in properties:
        return None

    relaxed = deepcopy(schema)
    relaxed["required"] = [name for name in required if name != field]
    try:
        from jsonschema import validate

        validate(instance=parsed, schema=relaxed)
    except Exception:
        return None

    patch_schema = {
        key: deepcopy(schema[key])
        for key in ("$schema", "$id", "$defs", "definitions")
        if key in schema
    }
    patch_schema.update({
        "type": "object",
        "properties": {field: deepcopy(properties[field])},
        "required": [field],
        "additionalProperties": False,
    })
    return field, patch_schema


def invoke_prompt_json(*, schema: dict, model_name: str,
                       initial_call: Callable[[], Any], repair_call: Callable,
                       required_patch_call: Callable | None = None,
                       validate_output: Callable[[Any], dict],
                       validation_diagnostic: Callable[[Exception], dict[str, str]],
                       end_token: str, max_attempts: int = 2,
                       fail_on_length: bool = False) -> dict:
    """Use one bounded wire path; roll back only before Instructor spends a call."""
    if max_attempts not in {1, 2}:
        raise ValueError("structured output attempt limit은 1 또는 2여야 합니다.")
    attempts, raw_text, finish, failure_kind = 0, "", "unknown", ""
    failure, failure_cause = ("", {}), None
    required_patch: tuple[str, dict, dict] | None = None

    def error(kind: str, detail: str, cause: Exception | None = None):
        return InstructorAdapterError(kind, attempts, detail, cause=cause)

    def remember(exc: Exception, is_object: bool, parsed: dict | None = None):
        nonlocal failure, failure_kind, required_patch
        diagnostic = (dict(validation_diagnostic(exc) or {}) if is_object else
                      {"category": "parse", "keyword": "json_object", "path": "$"})
        failure = (str(exc)[:1000], diagnostic)
        if attempts == 1 and required_patch_call is not None and isinstance(parsed, dict):
            plan = _single_root_required_patch(schema, parsed, diagnostic)
            if plan is not None:
                field, patch_schema = plan
                required_patch = (field, patch_schema, deepcopy(parsed))
        if attempts == 1 and fail_on_length and finish.casefold() in _LENGTH_STOPS:
            failure_kind = "length"
            raise error("length", "모델 출력이 길이 한도에서 잘렸습니다.", exc)

    def wire() -> tuple[str, dict | None]:
        nonlocal attempts, raw_text, finish, failure_kind, failure_cause
        attempts += 1
        try:
            if attempts == 1:
                raw = initial_call()
            elif required_patch is not None:
                raw = required_patch_call(
                    raw_text, failure[0], failure[1], required_patch[1],
                )
            else:
                raw = repair_call(raw_text, failure[0], failure[1])
        except Exception as exc:
            failure_kind = "transport" if attempts == 1 else "validation"
            failure_cause = exc
            raise error(failure_kind, "provider 호출 실패", exc) from exc
        raw_text = str(getattr(raw, "content", raw) or "")
        text = raw_text.strip()
        if end_token and text.endswith(end_token):
            text = text[:-len(end_token)].rstrip()
        meta = getattr(raw, "response_metadata", None) or {}
        finish = str(meta.get("finish_reason") or meta.get("stop_reason") or "unknown")
        if not text and attempts == 1:
            failure_kind = "empty"
            raise error("empty", "모델 출력이 비어 있습니다.")
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError) as exc:
            remember(exc, False)
            parsed = None
        if not isinstance(parsed, dict):
            if parsed is not None:
                remember(ValueError("최상위 JSON 값이 object가 아닙니다."), False)
            parsed = None
        if parsed is not None and attempts == 2 and required_patch is not None:
            field, patch_schema, seed = required_patch
            try:
                from jsonschema import validate

                validate(instance=parsed, schema=patch_schema)
                if set(parsed) != {field}:
                    raise ValueError("required patch가 단일 필드 계약을 위반했습니다.")
                parsed = {**seed, **parsed}
                text = json.dumps(
                    parsed, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                )
            except Exception as exc:
                remember(exc, True, parsed)
                parsed = None
        return text, parsed

    def legacy() -> dict:
        for _ in range(max_attempts):
            _text, parsed = wire()
            if parsed is not None:
                try:
                    return validate_output(parsed)
                except Exception as exc:
                    remember(exc, True, parsed)
            if attempts == max_attempts:
                raise error("validation", "structured output validation 실패")
        raise AssertionError("unreachable")

    if backend() == "legacy":
        return legacy()
    try:
        import instructor
        from openai.types.chat import ChatCompletion
        from pydantic import AfterValidator, RootModel

        def product_contract(value: dict) -> dict:
            try:
                return validate_output(value)
            except Exception as exc:
                remember(exc, True, value)
                raise ValueError("LTM structured-output validation failed") from exc

        response_model = RootModel[Annotated[dict, AfterValidator(product_contract)]]
        response_model.model_json_schema = classmethod(
            lambda _cls, *_args, **_kwargs: schema)

        def create(**_kwargs):
            text, parsed = wire()
            content = text if parsed is not None else json.dumps("LTM_STRICT_JSON_REJECTED")
            return ChatCompletion.model_validate({
                "id": f"ltm-{attempts}", "object": "chat.completion", "created": 0,
                "model": model_name or "ltm-managed-model",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
            })

        result = instructor.patch(create=create, mode=instructor.Mode.JSON)(
            response_model=response_model, model=model_name or "ltm-managed-model",
            messages=[{"role": "user", "content": "LTM managed structured output"}],
            max_retries=max_attempts - 1, strict=True)
        return result.root
    except Exception as exc:
        if attempts == 0:
            if fallback_policy() == "forbid":
                raise error(
                    "backend_unavailable",
                    "Instructor backend 초기화 실패; legacy fallback이 금지되었습니다.",
                    exc,
                ) from exc
            return legacy()
        kind = failure_kind or "validation"
        raise error(kind, "Instructor validation/retry 실패",
                    failure_cause or exc) from exc


__all__ = [
    "BACKEND_ENV", "FALLBACK_POLICY_ENV", "InstructorAdapterError", "backend",
    "fallback_policy", "invoke_prompt_json",
]
