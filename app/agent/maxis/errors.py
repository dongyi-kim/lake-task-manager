"""Safe, payload-free MAXIS gateway error taxonomy."""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.maxis.telemetry import RemoteRequestTelemetryV1


class MaxisErrorCode(str, Enum):
    CONFIGURATION = "configuration"
    POLICY = "policy"
    BAD_REQUEST = "bad_request"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_CREDENTIALS = "model_credentials"
    MODEL_PARAMETERS = "model_parameters"
    WORKFLOW_CONTRACT = "workflow_contract"
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
        draft_regeneration_allowed: bool = False,
        draft_regeneration_role: str = "",
        telemetry: RemoteRequestTelemetryV1 | None = None,
    ):
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.draft_regeneration_allowed = draft_regeneration_allowed
        self.draft_regeneration_role = draft_regeneration_role
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


_DIFY_ERROR_CODES = {
    "model_not_found": MaxisErrorCode.MODEL_UNAVAILABLE,
    "model_not_supported": MaxisErrorCode.MODEL_UNAVAILABLE,
    "model_not_available": MaxisErrorCode.MODEL_UNAVAILABLE,
    "provider_not_initialize": MaxisErrorCode.MODEL_CREDENTIALS,
    "provider_not_initialized": MaxisErrorCode.MODEL_CREDENTIALS,
    "invalid_api_key": MaxisErrorCode.MODEL_CREDENTIALS,
    "unsupported_parameter": MaxisErrorCode.MODEL_PARAMETERS,
    "unsupported_value": MaxisErrorCode.MODEL_PARAMETERS,
}
_DIFY_ERROR_MARKERS = (
    (MaxisErrorCode.MODEL_UNAVAILABLE, (
        "ModelNotFoundError", "ModelNotSupportedError",
    )),
    (MaxisErrorCode.MODEL_CREDENTIALS, (
        "ProviderTokenNotInitError", "Incorrect API key provided:",
    )),
    (MaxisErrorCode.MODEL_PARAMETERS, (
        "Unsupported parameter:", "Unsupported value:",
    )),
    (MaxisErrorCode.WORKFLOW_CONTRACT, (
        "invalid_core_query_draft", "invalid_core_request_draft",
        "invalid_core_work_draft", "invalid_core_result_draft",
        "invalid_core_knowledge_draft", "invalid_core_control_input",
        "invalid_core_query_input", "invalid_core_request_input",
        "invalid_core_work_input", "invalid_core_result_input",
        "invalid_core_knowledge_input", "invalid_editor_draft_spec",
        "invalid_editor_input", "invalid_editor_plan", "invalid_editor_selection_id",
        "invalid_editor_selection_limit",
    )),
)
_REGENERATABLE_DRAFT_MARKERS = frozenset({
    "invalid_core_query_draft", "invalid_core_request_draft",
    "invalid_core_work_draft", "invalid_core_result_draft", "invalid_core_knowledge_draft",
})
_EDITOR_SELECTION_MARKERS = frozenset({
    "invalid_editor_selection_id", "invalid_editor_selection_limit",
})


def classify_dify_error(status_code: int, payload: object) -> MaxisGatewayError:
    """Refine a bounded JSON 400/422 into a fixed, content-free diagnostic.

    This is advisory failure classification, never authorization or model identity.
    Only known wire codes and fixed provider/our-code markers are recognized. HTTP
    authentication, rate limits and server failures retain their existing semantics.
    No remote string is attached to the exception or retained outside this call.
    """
    fallback = classify_http_status(status_code)
    if status_code not in (400, 422) or not isinstance(payload, dict):
        return fallback
    nested = payload.get("error")
    rows = [payload, nested] if isinstance(nested, dict) else [payload]
    for row in rows:
        for key in ("code", "type"):
            value = row.get(key)
            if isinstance(value, str) and len(value) <= 128:
                code = _DIFY_ERROR_CODES.get(value.lower())
                if code is not None:
                    return MaxisGatewayError(code, status_code=status_code)
    messages = [row["message"] for row in rows if isinstance(row.get("message"), str)]
    # Classify all recognized locations before granting regeneration. A nested
    # model/input failure must veto an outer draft marker, irrespective of order.
    for code, markers in _DIFY_ERROR_MARKERS:
        matched = [marker for marker in markers for message in messages
                   if re.search(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", message)]
        if matched:
            return MaxisGatewayError(
                code, status_code=status_code,
                draft_regeneration_allowed=(
                    bool(set(matched) <= _REGENERATABLE_DRAFT_MARKERS)
                    or bool(set(matched) <= _EDITOR_SELECTION_MARKERS)
                ),
                draft_regeneration_role=(
                    "editor_author" if set(matched) <= _EDITOR_SELECTION_MARKERS else ""
                ),
            )
    return fallback


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


__all__ = ["MaxisErrorCode", "MaxisGatewayError", "classify_http_status",
           "classify_dify_error", "evaluation_failure_diagnostics"]
