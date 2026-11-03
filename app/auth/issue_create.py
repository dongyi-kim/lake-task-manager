"""Creation transport contract and value-free diagnostics shared by Jira providers."""

import json
import logging
import re
from functools import lru_cache
from urllib.parse import urlsplit

from app.infra.version import code_rev

_LOG = logging.getLogger(__name__)
_FIELD_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}\Z")


def is_issue_create(method, path):
    return method.lower() == "post" and urlsplit(path).path.rstrip("/").endswith(
        ("/rest/api/2/issue", "/rest/api/latest/issue"))


def check_create_body(method, path, body):
    """Omit Reporter entirely; even null/self requires MODIFY_REPORTER on some Jira setups.

    Fail before sending an invalid payload rather than silently dropping an explicit edit.
    This also catches a misconfigured custom field ID accidentally targeting Reporter.
    """
    if is_issue_create(method, path) and isinstance(body, dict):
        if any("reporter" in (body.get(section) or {}) for section in ("fields", "update")):
            raise ValueError("티켓 생성 시 보고자는 Jira가 현재 사용자로 지정합니다. reporter 필드를 보낼 수 없습니다.")


@lru_cache(maxsize=1)
def _revision():
    return code_rev() or "unknown"


def log_create_response(method, path, payload, status, body=""):
    """Never log field values, error messages, URLs, user identity, cookies, or headers."""
    if not is_issue_create(method, path) or status < 400:
        return
    fields = (payload or {}).get("fields") or {}
    try:
        error = json.loads(body or "{}")
    except (TypeError, ValueError):
        error = {}
    errors = error.get("errors") if isinstance(error, dict) else {}
    errors = errors if isinstance(errors, dict) else {}
    safe_names = lambda values: sorted(name if isinstance(name, str) and _FIELD_ID.fullmatch(name)
                                       else "<unknown>" for name in values)
    _LOG.warning(
        "Jira create failed revision=%s status=%s fields=%s reporter_present=%s error_fields=%s",
        _revision(), status, safe_names(fields), "reporter" in fields, safe_names(errors),
    )
