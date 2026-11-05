"""Confluence failures must never update Jira's authentication/circuit state."""

from app.auth.base import (MutationOutcomeUnknown, MutationPreflightUnavailable,
                           PermissionDenied, SessionExpired, UpstreamBusy,
                           UpstreamError, UpstreamUnavailable)
from app.services.ticket_commands import CommandError


def confluence_error(exc):
    result = {"service": "confluence", "message": str(exc), "retryable": False}
    if isinstance(exc, MutationOutcomeUnknown):
        return {**result, "code": "outcome_unknown", "status": 503,
                "clientMutationId": exc.mutation_id, "retryable": True,
                "repeatWrite": False, "retryWithSameMutationId": True}
    if isinstance(exc, MutationPreflightUnavailable):
        return {**result, "code": "upstream_unavailable", "status": 503,
                "retryable": True, "writeExecuted": False, "needLogin": False,
                "retryWithSameMutationId": True}
    if isinstance(exc, UpstreamBusy):
        return {**result, "code": "rate_limited", "status": 429,
                "retryable": True, "retryAfterSeconds": 1}
    status = getattr(exc, "status", None)
    if isinstance(exc, PermissionDenied):
        status = 403
    if isinstance(exc, (CommandError, UpstreamError, PermissionDenied)):
        result.update(status=status, code={401: "authentication_required", 403: "permission_denied",
                      404: "not_found", 409: "conflict", 429: "rate_limited"}.get(
                          status, "upstream_unavailable" if status and status >= 500 else "invalid_request"))
        result["retryable"] = status == 429 or bool(status and status >= 500)
        if status == 401:
            result["needLogin"] = True
        if getattr(exc, "reason", None):
            result["reason"] = exc.reason
        if isinstance(getattr(exc, "next_action", None), dict):
            result["nextAction"] = exc.next_action
        return result
    if isinstance(exc, SessionExpired):
        return {**result, "code": "authentication_required", "status": 401, "needLogin": True}
    if isinstance(exc, UpstreamUnavailable):
        return {**result, "code": "upstream_unavailable", "status": 503,
                "retryable": True, "needLogin": False}
    if isinstance(exc, (ValueError, TypeError)):
        return {**result, "code": "invalid_request", "status": 400}
    return {**result, "code": "internal_error", "status": 500,
            "message": "Confluence 요청을 완료하지 못했습니다."}
