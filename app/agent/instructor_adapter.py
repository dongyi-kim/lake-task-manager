"""Instructor validation/retry over LTM-owned LangChain calls."""

from __future__ import annotations

import json
import os
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


def invoke_prompt_json(*, schema: dict, model_name: str,
                       initial_call: Callable[[], Any], repair_call: Callable,
                       validate_output: Callable[[Any], dict],
                       validation_diagnostic: Callable[[Exception], dict[str, str]],
                       end_token: str, max_attempts: int = 2,
                       fail_on_length: bool = False) -> dict:
    """Use one bounded wire path; roll back only before Instructor spends a call."""
    if max_attempts not in {1, 2}:
        raise ValueError("structured output attempt limit은 1 또는 2여야 합니다.")
    attempts, raw_text, finish, failure_kind = 0, "", "unknown", ""
    failure, failure_cause = ("", {}), None

    def error(kind: str, detail: str, cause: Exception | None = None):
        return InstructorAdapterError(kind, attempts, detail, cause=cause)

    def remember(exc: Exception, is_object: bool):
        nonlocal failure, failure_kind
        diagnostic = (dict(validation_diagnostic(exc) or {}) if is_object else
                      {"category": "parse", "keyword": "json_object", "path": "$"})
        failure = (str(exc)[:1000], diagnostic)
        if attempts == 1 and fail_on_length and finish.casefold() in _LENGTH_STOPS:
            failure_kind = "length"
            raise error("length", "모델 출력이 길이 한도에서 잘렸습니다.", exc)

    def wire() -> tuple[str, dict | None]:
        nonlocal attempts, raw_text, finish, failure_kind, failure_cause
        attempts += 1
        try:
            raw = (initial_call() if attempts == 1 else
                   repair_call(raw_text, failure[0], failure[1]))
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
        return text, parsed

    def legacy() -> dict:
        for _ in range(max_attempts):
            _text, parsed = wire()
            if parsed is not None:
                try:
                    return validate_output(parsed)
                except Exception as exc:
                    remember(exc, True)
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
                remember(exc, True)
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
