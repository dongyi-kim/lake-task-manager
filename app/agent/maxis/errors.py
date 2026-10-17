"""Safe, payload-free MAXIS gateway error taxonomy."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.maxis.telemetry import RemoteRequestTelemetryV1


class MaxisErrorCode(str, Enum):
    CONFIGURATION = "configuration"
    POLICY = "policy"
    BAD_REQUEST = "bad_request"
    AUTHENTICATION = "authentication"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    UPSTREAM = "upstream"
    HTTP = "http"
    TIMEOUT = "timeout"
    NETWORK = "network"
    INVALID_RESPONSE = "invalid_response"
    RESPONSE_TOO_LARGE = "response_too_large"
    CIRCUIT_OPEN = "circuit_open"


class MaxisGatewayError(RuntimeError):
    """An actionable classification which never embeds response or request content."""

    def __init__(
        self,
        code: MaxisErrorCode,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        telemetry: RemoteRequestTelemetryV1 | None = None,
    ):
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.telemetry = telemetry
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"MAXIS gateway error: {code.value}{suffix}")


def classify_http_status(status_code: int) -> MaxisGatewayError:
    status = int(status_code)
    if status == 400 or status == 422:
        return MaxisGatewayError(MaxisErrorCode.BAD_REQUEST, status_code=status)
    if status == 401:
        return MaxisGatewayError(MaxisErrorCode.AUTHENTICATION, status_code=status)
    if status == 403:
        return MaxisGatewayError(MaxisErrorCode.FORBIDDEN, status_code=status)
    if status == 404:
        return MaxisGatewayError(MaxisErrorCode.NOT_FOUND, status_code=status)
    if status == 408:
        return MaxisGatewayError(
            MaxisErrorCode.TIMEOUT, status_code=status, retryable=True
        )
    if status == 409:
        return MaxisGatewayError(MaxisErrorCode.CONFLICT, status_code=status)
    if status == 429:
        return MaxisGatewayError(
            MaxisErrorCode.RATE_LIMITED, status_code=status, retryable=True
        )
    if 500 <= status <= 599:
        return MaxisGatewayError(
            MaxisErrorCode.UPSTREAM, status_code=status, retryable=True
        )
    return MaxisGatewayError(MaxisErrorCode.HTTP, status_code=status)


_EVALUATION_STAGES = frozenset({
    "preparation", "gateway", "response_decode", "response_envelope",
    "retrieval_validation", "response_guard", "binding_validation", "rendering",
    "output_check", "client_close",
})


def evaluation_failure_diagnostics(
    exc: BaseException, *, stage: str, correlation_id: str = "",
    response_telemetry: RemoteRequestTelemetryV1 | None = None,
) -> dict:
    """Preserve failure provenance without exception text, HTTP bodies or headers.

    ``gateway`` includes the client's pre-HTTP guards. Only explicit status or
    validated telemetry proves an HTTP response/attempt; absence is not a zero
    model-call observation. This projection never changes a checker verdict.
    """
    from app.agent.maxis.telemetry import RemoteRequestTelemetryV1

    if stage not in _EVALUATION_STAGES:
        raise ValueError("unknown evaluation stage")
    gateway_error = isinstance(exc, MaxisGatewayError)
    code = exc.code.value if gateway_error and isinstance(exc.code, MaxisErrorCode) else "evaluation_failed"
    status = exc.status_code if gateway_error else None
    status = status if type(status) is int and 100 <= status <= 599 else None
    candidate = exc.telemetry if gateway_error and exc.telemetry is not None else response_telemetry
    observation = None
    if isinstance(candidate, RemoteRequestTelemetryV1):
        try:
            # model_copy/model_construct can bypass schema validation.
            observation = RemoteRequestTelemetryV1.model_validate(
                candidate.model_dump(mode="python", warnings=False), strict=True)
        except (TypeError, ValueError):
            pass
    if status is None and observation is not None:
        status = observation.http_status
    transport = observation.transport_status if observation is not None else "unknown"
    if stage == "preparation":
        transport = "not_attempted"
    if stage == "gateway":
        if status is not None:
            stage = "http_response"
        elif transport == "failed":
            stage = "transport"
        elif transport == "not_attempted" or (observation is None and code in {
            "bad_request", "configuration", "policy", "circuit_open",
        }):
            stage, transport = "local_validation", "not_attempted"

    def digest(value):
        return hashlib.sha256(value.encode("utf-8")).hexdigest() if (
            isinstance(value, str) and 0 < len(value) <= 256
        ) else ""

    return {
        "contract": "maxis-eval-failure.v1", "stage": stage,
        "classification": code, "httpStatus": status, "transportStatus": transport,
        "requestIdSha256": digest(correlation_id),
        "requestedWorkflowIdSha256": digest(observation.requested_workflow_id) if observation else "",
        "workflowRunIdSha256": digest(observation.workflow_run_id) if observation else "",
        "messageIdSha256": observation.message_id_sha256 if observation else "",
        "taskIdSha256": observation.task_id_sha256 if observation else "",
        "nativeModelCallCount": observation.native_model_call_count if observation else None,
        "nativeModelCallCountMeaning": "native observation only; HTTP responses are not model calls",
    }


__all__ = ["MaxisErrorCode", "MaxisGatewayError", "classify_http_status", "evaluation_failure_diagnostics"]
