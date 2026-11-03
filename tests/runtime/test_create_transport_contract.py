"""Final provider payloads never set Reporter; diagnostics contain schema, not content."""

import json
from types import SimpleNamespace

import pytest

from app.auth import issue_create
from app.auth.base import UpstreamError
from app.auth.basic import BasicAuthProvider
from app.auth.inprocess import InProcessProvider
from app.auth.sso_session import SsoSessionProvider


@pytest.mark.parametrize("provider_class", [BasicAuthProvider, InProcessProvider, SsoSessionProvider])
@pytest.mark.parametrize("value", [None, {"name": "self"}, {"name": "other"}])
def test_all_transports_reject_reporter_presence_before_any_request(provider_class, value):
    provider = object.__new__(provider_class)  # No session/client: any attempted I/O would fail.
    with pytest.raises(ValueError, match="reporter"):
        provider._write("post", "/rest/api/2/issue", {"fields": {"reporter": value}}, {})


def test_guard_covers_context_path_and_update_section_but_allows_existing_issue_edit():
    with pytest.raises(ValueError):
        issue_create.check_create_body("post", "https://jira.example/jira/rest/api/latest/issue/",
                                       {"update": {"reporter": [{"set": "self"}]}})
    issue_create.check_create_body("put", "/rest/api/2/issue/DL-1", {"fields": {"reporter": None}})


def test_sso_create_failure_logs_field_names_and_revision_without_values(caplog, capsys, monkeypatch):
    secret = "private-user-description-token"
    body = json.dumps({"errors": {"reporter": secret}, "errorMessages": [secret]})
    calls = []
    def post(url, **kwargs):
        calls.append(json.loads(kwargs["data"]))
        return SimpleNamespace(status=400, headers={"x-authentication-denied-reason": secret}, text=lambda: body)
    provider = object.__new__(SsoSessionProvider)
    provider.base = "https://jira.example"
    provider._context = SimpleNamespace(request=SimpleNamespace(post=post))
    provider._xsrf_headers = lambda url: {"Cookie": secret}
    monkeypatch.setattr(issue_create, "_revision", lambda: "test-revision")
    payload = {"fields": {"summary": secret, "description": secret, "assignee": {"name": secret}}}
    with pytest.raises(UpstreamError) as caught:
        provider._write("post", "/rest/api/2/issue", payload, {})
    assert caught.value.status == 400 and len(calls) == 1
    assert "reporter" not in calls[0]["fields"]
    assert "test-revision" in caplog.text and "reporter_present=False" in caplog.text
    assert "reporter" in caplog.text and "summary" in caplog.text
    assert secret not in caplog.text + capsys.readouterr().err


@pytest.mark.parametrize("body", ["<html>private body</html>", "[]", "null", '{"errors":[]}'])
def test_malformed_error_responses_do_not_break_or_leak_diagnostics(body, caplog, monkeypatch):
    monkeypatch.setattr(issue_create, "_revision", lambda: "test-revision")
    issue_create.log_create_response("post", "/rest/api/2/issue", {"fields": {"summary": "secret"}}, 502, body)
    assert "status=502" in caplog.text
    assert "private body" not in caplog.text and "secret" not in caplog.text
