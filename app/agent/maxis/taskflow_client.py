"""Strict, stateless transport for the version-pinned Dify Workflow API.

The native workflow identity is checked independently of the model-produced result.
An echoed pin is a correlation check, not proof of graph hash, Knowledge admission,
or nested budget enforcement. The caller still owns those release/authority gates,
the synthetic-only DEV policy, and the shared coordinator budget. No routing or
profile activation happens here; there is no automatic model/HTTP retry.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from app.agent.maxis.client import DifyClient, DifyClientConfig, _strict_json_loads
from app.agent.maxis.context_guard import validate_pack_safety
from app.agent.maxis.errors import MaxisErrorCode, MaxisGatewayError
from app.agent.maxis.identifiers import canonical_published_workflow_id
from app.agent.maxis.taskflow_contracts import TaskFlowRequestV1, TaskFlowResultV1
from app.agent.maxis.telemetry import RemoteRequestTelemetryV1, request_observation

# TaskFlow is a separate synthetic-only protocol with a smaller whole-request
# budget. The chat EvidencePack's 32-KiB upper bound does not widen this route.
MAX_TASKFLOW_REQUEST_BYTES = 16 * 1024
MAX_TASKFLOW_RESPONSE_BYTES = 64 * 1024


def prepare_taskflow_request(request: TaskFlowRequestV1, config: DifyClientConfig):
    """Last-boundary validation shared by the typed facade and narrow HTTP seam."""
    try:
        if not isinstance(request, TaskFlowRequestV1):
            raise ValueError("typed request required")
        request = TaskFlowRequestV1.model_validate_json(request.model_dump_json())
        validate_pack_safety(request.evidence)
        workflow_id = canonical_published_workflow_id(request.pin.workflow_release)
        if (workflow_id != request.pin.workflow_release
                or not config.profile_fingerprint
                or request.pin.profile_fingerprint != config.profile_fingerprint):
            raise ValueError("deployment binding mismatch")
        encoded = json.dumps(request.model_dump(mode="json"), ensure_ascii=False,
                             separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > MAX_TASKFLOW_REQUEST_BYTES:
            raise ValueError("request too large")
    except (ValueError, TypeError, AttributeError):
        raise MaxisGatewayError(MaxisErrorCode.POLICY) from None
    return request, {
        "inputs": {"taskflow_request": encoded},
        "response_mode": "blocking",
        "user": request.evidence.pseudonymous_user,
    }


@dataclass(frozen=True)
class TaskFlowResponse:
    result: TaskFlowResultV1
    telemetry: RemoteRequestTelemetryV1
    elapsed_seconds: float | None = None


class TaskFlowClient:
    """Compose the existing destination/TLS/bounded HTTP client, without owning it."""

    def __init__(self, transport: DifyClient):
        self._transport = transport

    def invoke(self, request: TaskFlowRequestV1, *, api_key: str,
               timeout_seconds: float | None = None) -> TaskFlowResponse:
        request, _ = prepare_taskflow_request(request, self._transport.config)
        body, status, latency_ms, breaker = self._transport.request_taskflow_bytes(
            request, api_key=api_key, timeout_seconds=timeout_seconds,
        )
        telemetry = request_observation(
            transport_status="succeeded", http_status=status, latency_ms=latency_ms,
            workflow_id=request.pin.workflow_release,
        )
        try:
            # bytes.decode forbids JSON's UTF-16/32 auto-detection at this UTF-8 wire.
            raw = _strict_json_loads(body.decode("utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
                raise ValueError("native workflow object required")
            data = raw["data"]
            run_id = canonical_published_workflow_id(raw.get("workflow_run_id"))
            if (canonical_published_workflow_id(data.get("id")) != run_id
                    or canonical_published_workflow_id(data.get("workflow_id"))
                    != request.pin.workflow_release or data.get("status") != "succeeded"):
                raise ValueError("native workflow identity/status mismatch")
            # Only observed native workflow metadata; outputs cannot invent usage.
            tokens = data.get("total_tokens")
            tokens = tokens if type(tokens) is int and 0 <= tokens <= 100_000_000 else None
            telemetry = telemetry.model_copy(update={
                "workflow_run_id": run_id, "total_tokens": tokens,
                "usage_scope": "response_metadata" if tokens is not None else "unknown",
            })
            elapsed = data.get("elapsed_time")
            elapsed = (float(elapsed) if type(elapsed) in (int, float)
                       and 0 <= elapsed <= 3600 else None)
            outputs = data.get("outputs")
            result_json = outputs.get("result_json") if isinstance(outputs, dict) else None
            if not isinstance(result_json, str) or not result_json:
                raise ValueError("exact result JSON required")
            decoded = _strict_json_loads(result_json)
            result = TaskFlowResultV1.model_validate_json(json.dumps(decoded, ensure_ascii=False))
            if (result.request_id != request.request_id or result.revision != request.revision
                    or result.pin != request.pin
                    or result.evidence_sha256 != request.evidence_sha256):
                raise ValueError("result identity mismatch")
            if (result.draft_result is not None
                    and result.draft_result.knowledge_manifest_sha256
                    != request.pin.knowledge_manifest_sha256):
                raise ValueError("draft Knowledge binding mismatch")
        except (ValueError, TypeError, AttributeError, RecursionError):
            breaker.record_failure()
            raise MaxisGatewayError(MaxisErrorCode.INVALID_RESPONSE,
                telemetry=telemetry.model_copy(update={"result_status": "failed"})) from None
        breaker.record_success()
        return TaskFlowResponse(result=result, telemetry=telemetry, elapsed_seconds=elapsed)
