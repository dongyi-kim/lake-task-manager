"""Prod SSO response classification without starting Playwright or touching Jira."""

import pytest
import json

from app.auth.base import PermissionDenied, SessionExpired
from app.auth.sso_session import SsoSessionProvider


class _Response:
    def __init__(self, status, *, body="", headers=None, json_body=None):
        self.status = status
        self.headers = headers or {}
        self._body = body
        self._json_body = json_body

    def text(self):
        return self._body

    def json(self):
        if self._json_body is None:
            raise ValueError("not json")
        return self._json_body


class _Request:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _provider(*responses):
    provider = object.__new__(SsoSessionProvider)
    provider.base = "https://jira.example"
    request = _Request(*responses)
    provider._context = type("_Context", (), {"request": request})()
    return provider, request


def test_issue_403_is_permission_failure_only_when_myself_proves_session_alive():
    provider, request = _provider(
        _Response(403, body='{"errorMessages":["No permission to see this issue"]}'),
        _Response(200, json_body={"name": "alice", "displayName": "Alice"}),
    )

    with pytest.raises(PermissionDenied) as caught:
        provider._fetch("/rest/api/2/issue/DL-42", {}, False, quiet=True)

    assert caught.value.status == 403
    assert "세션 만료" not in str(caught.value)
    assert [call[0] for call in request.calls] == [
        "https://jira.example/rest/api/2/issue/DL-42",
        "https://jira.example/rest/api/2/myself",
    ]


def test_issue_403_stays_auth_failure_when_myself_is_expired():
    provider, request = _provider(
        _Response(403, body='{"errorMessages":["No permission to see this issue"]}'),
        _Response(403, body="login required"),
    )

    with pytest.raises(SessionExpired) as caught:
        provider._fetch("/rest/api/2/issue/DL-42", {}, False, quiet=True)

    assert type(caught.value) is SessionExpired
    assert "만료" in str(caught.value)
    assert len(request.calls) == 2


def test_absolute_issue_url_below_jira_context_path_is_permission_scoped():
    provider, request = _provider(
        _Response(403, body="Forbidden"),
        _Response(200, json_body={"name": "alice"}),
    )
    provider.base = "https://atlassian.example/jira"

    with pytest.raises(PermissionDenied):
        provider._fetch(
            "https://atlassian.example/jira/rest/api/2/issue/DL-42/comment",
            {}, False, quiet=True,
        )

    assert [call[0] for call in request.calls] == [
        "https://atlassian.example/jira/rest/api/2/issue/DL-42/comment",
        "https://atlassian.example/jira/rest/api/2/myself",
    ]


def test_same_origin_path_outside_jira_context_is_not_issue_scoped():
    provider, request = _provider(_Response(403, body="Forbidden"))
    provider.base = "https://atlassian.example/jira"

    with pytest.raises(SessionExpired):
        provider._fetch(
            "https://atlassian.example/rest/api/2/issue/DL-42",
            {}, False, quiet=True,
        )

    assert len(request.calls) == 1


def test_explicit_auth_denial_header_does_not_need_a_second_probe():
    provider, request = _provider(_Response(
        403,
        headers={"X-Authentication-Denied-Reason": "AUTHENTICATION_DENIED"},
    ))

    with pytest.raises(SessionExpired) as caught:
        provider._fetch("/rest/api/2/issue/DL-42", {}, False, quiet=True)

    assert type(caught.value) is SessionExpired
    assert "AUTHENTICATION_DENIED" in str(caught.value)
    assert len(request.calls) == 1


@pytest.mark.parametrize("path", [
    "/rest/api/2/myself",
    "/rest/api/2/search",
    "https://confluence.example/rest/api/content/42",
])
def test_non_issue_403_never_uses_jira_identity_to_downgrade_auth(path):
    provider, request = _provider(_Response(403, body="Forbidden"))

    with pytest.raises(SessionExpired) as caught:
        provider._fetch(path, {}, False, quiet=True)

    assert type(caught.value) is SessionExpired
    assert len(request.calls) == 1


def test_unknown_identity_probe_does_not_mask_possible_expiry_as_permission():
    provider, request = _provider(
        _Response(403, body="Forbidden"),
        _Response(503, body="gateway unavailable"),
    )

    with pytest.raises(SessionExpired) as caught:
        provider._fetch("/rest/api/latest/issue/DL-42/comment", {}, False, quiet=True)

    assert type(caught.value) is SessionExpired
    assert "확인하지 못했습니다" in str(caught.value)
    assert len(request.calls) == 2


def test_401_always_uses_auth_recovery_without_permission_probe():
    provider, request = _provider(_Response(401, body="Unauthorized"))

    with pytest.raises(SessionExpired) as caught:
        provider._fetch("/rest/api/2/issue/DL-42", {}, False, quiet=True)

    assert type(caught.value) is SessionExpired
    assert len(request.calls) == 1


def test_permission_handler_returns_direct_403_without_auth_probe(monkeypatch):
    from app import main

    monkeypatch.setattr(
        main._client, "direct_session_state",
        lambda: (_ for _ in ()).throw(AssertionError("must not probe /myself again")),
    )
    response = main._on_permission_denied(
        None, PermissionDenied("/rest/api/2/issue/DL-42"))

    assert response.status_code == 403
    assert json.loads(response.body) == {
        "error": "이 Jira 항목을 볼 권한이 없습니다.",
        "permissionDenied": True,
        "needLogin": False,
    }
