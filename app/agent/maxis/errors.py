"""Safe, payload-free MAXIS gateway error taxonomy."""

from __future__ import annotations

from enum import Enum


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
    ):
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
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


__all__ = ["MaxisErrorCode", "MaxisGatewayError", "classify_http_status"]
