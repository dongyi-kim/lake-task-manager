"""Bound MCP read preparation before schema validation and duplicated wire fields."""

MAX_TOOL_RESPONSE_BYTES = 16 * 1024 * 1024
RESPONSE_LIMIT_MESSAGE = (
    "MCP 응답이 16 MiB를 초과했습니다. 조회 범위나 페이지 크기를 줄여 주세요. "
    "댓글과 이력은 get_evidence_page로 나누어 읽고, 한 개의 본문이 너무 크면 Jira 원문을 확인하세요. "
    "응답 내용을 잘라서 반환하지 않았습니다.")


class ResponseTooLarge(ValueError):
    """A complete response cannot fit; never describe a prefix as a successful read."""

    def __init__(self, message=RESPONSE_LIMIT_MESSAGE, *, limit=None):
        super().__init__(message)
        self.limit = MAX_TOOL_RESPONSE_BYTES if limit is None else limit


def error_payload(exc):
    return {"code": "response_too_large", "status": 413, "message": str(exc),
            "limitBytes": exc.limit, "retryable": False, "needLogin": False}
