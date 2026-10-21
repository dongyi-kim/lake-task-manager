"""Content-free remote request observations, never a quality score or cost estimate."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_DIGEST = r"^(?:|[0-9a-f]{64})$"


class RemoteRequestTelemetryV1(BaseModel):
    """Allowlisted observations from one HTTP attempt, including rejected answers.

    Native response usage is not necessarily the sum of every nested model call.
    Consequently call counts and quality remain unknown, not inferred from tokens,
    graph node count, HTTP success, or acceptance by the local response guard.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")

    contract: Literal["maxis-request-telemetry.v1"] = "maxis-request-telemetry.v1"
    transport_status: Literal["not_attempted", "succeeded", "failed"] = "not_attempted"
    http_status: int | None = Field(default=None, ge=100, le=599)
    latency_ms: int = Field(default=0, ge=0, le=3_600_000)
    requested_workflow_id: Annotated[str, Field(pattern=_UUID)] | None = None
    workflow_run_id: Annotated[str, Field(pattern=_UUID)] | None = None
    message_id_sha256: Annotated[str, Field(pattern=_DIGEST)] = ""
    task_id_sha256: Annotated[str, Field(pattern=_DIGEST)] = ""
    prompt_tokens: int | None = Field(default=None, ge=0, le=100_000_000)
    completion_tokens: int | None = Field(default=None, ge=0, le=100_000_000)
    total_tokens: int | None = Field(default=None, ge=0, le=100_000_000)
    usage_scope: Literal["response_metadata", "unknown"] = "unknown"
    reported_cost: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    cost_currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    native_model_call_count: int | None = Field(default=None, ge=0, le=10_000)
    result_status: Literal["not_evaluated", "completed", "needs_input", "blocked", "failed"] = "not_evaluated"
    quality_status: Literal["not_evaluated"] = "not_evaluated"


def _uuid(value: object) -> str | None:
    return value.lower() if isinstance(value, str) and re.fullmatch(_UUID, value.lower()) else None


def _digest(value: object) -> str:
    # Identifiers need not be UUIDs on older Dify releases. Only their digest leaves
    # the parser; hostile IDs cannot inject raw content or credentials into a ledger.
    if not isinstance(value, str) or not 0 < len(value) <= 256:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tokens(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 100_000_000 else None


def request_observation(
    *, transport_status: str, latency_ms: int, http_status: int | None = None,
    workflow_id: str = "", raw: Mapping | None = None,
) -> RemoteRequestTelemetryV1:
    """Project only native top-level metadata; never inspect answer/model output."""
    raw = raw if isinstance(raw, Mapping) else {}
    metadata = raw.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    usage = metadata.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    cost = usage.get("total_price")
    currency = usage.get("currency")
    numeric_cost = None
    if (type(cost) in (int, float) and 0 <= cost <= 1_000_000) or (
        isinstance(cost, str) and len(cost) <= 32
        and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", cost)
    ):
        number = float(cost)
        if math.isfinite(number) and 0 <= number <= 1_000_000:
            numeric_cost = number
    currency = currency if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency) else None
    # No denomination means there is no usable cost observation.
    if currency is None or numeric_cost is None:
        currency, numeric_cost = None, None
    return RemoteRequestTelemetryV1(
        transport_status=transport_status,
        http_status=http_status,
        latency_ms=min(3_600_000, max(0, latency_ms)),
        requested_workflow_id=_uuid(workflow_id),
        workflow_run_id=_uuid(raw.get("workflow_run_id")),
        message_id_sha256=_digest(raw.get("message_id") or raw.get("id")),
        task_id_sha256=_digest(raw.get("task_id")),
        prompt_tokens=_tokens(usage.get("prompt_tokens")),
        completion_tokens=_tokens(usage.get("completion_tokens")),
        total_tokens=_tokens(usage.get("total_tokens")),
        usage_scope="response_metadata" if usage else "unknown",
        reported_cost=numeric_cost,
        cost_currency=currency,
    )


def with_result_status(observation, result_status: str) -> RemoteRequestTelemetryV1 | None:
    """Revalidate at the audit boundary, including model_copy bypass attempts."""
    if observation is None:
        return None
    data = observation.model_dump(mode="python")
    data["result_status"] = result_status
    return RemoteRequestTelemetryV1.model_validate(data, strict=True)


def role_usage_observation(metadata: dict) -> dict | None:
    """Project request attempts and known token subtotals, never missing usage as zero."""
    attempts = metadata.get("requestAttempts")
    if attempts is not None:
        if not isinstance(attempts, list) or not 1 <= len(attempts) <= 2:
            raise ValueError("Invalid bounded MAXIS request attempts")
        observations = [RemoteRequestTelemetryV1.model_validate(row, strict=True) for row in attempts]
        latest = metadata.get("requestTelemetry")
        if latest is not None and observations[-1] != RemoteRequestTelemetryV1.model_validate(latest, strict=True):
            raise ValueError("MAXIS final request observation does not match attempts")
        rows = [role_usage_observation({
            "requestTelemetry": row.model_dump(mode="json"),
            "workflowBundleVersion": metadata.get("workflowBundleVersion", ""),
        }) for row in observations]
        rows = [row for row in rows if row is not None]
        if not rows:
            return None
        return {**rows[-1], **{key: sum(row[key] for row in rows) for key in (
            "calls", "failedCalls", "unknownUsageCalls", "promptTokens",
            "completionTokens", "totalTokens", "latencyMs",
        )}}
    raw = metadata.get("requestTelemetry")
    if raw is not None:
        observation = RemoteRequestTelemetryV1.model_validate(raw, strict=True)
        if observation.transport_status == "not_attempted":
            return None
        prompt, completion, total = (
            observation.prompt_tokens, observation.completion_tokens, observation.total_tokens,
        )
        failed = observation.result_status in {"failed", "blocked"}
        latency = observation.latency_ms
    else:
        # Compatibility with recorded/direct Role outputs predating native telemetry.
        usage = metadata.get("usage")
        if not isinstance(usage, dict):
            return None
        prompt, completion, total = (
            _tokens(usage.get("promptTokens")), _tokens(usage.get("completionTokens")),
            _tokens(usage.get("totalTokens")),
        )
        failed = metadata.get("resultStatus") in {"blocked", "failed"}
        latency = min(3_600_000, max(0, int(metadata.get("latencyMs") or 0)))
    return {
        "engine": "maxis", "calls": 1, "failedCalls": int(failed),
        "unknownUsageCalls": int(any(value is None for value in (prompt, completion, total))),
        "promptTokens": prompt or 0, "completionTokens": completion or 0,
        "totalTokens": total or 0, "latencyMs": latency,
        "workflowBundleVersion": str(metadata.get("workflowBundleVersion") or "")[:120],
    }
