"""Real form encoding and provider boundaries; no Jira/SSO/network calls."""

from types import SimpleNamespace
from urllib.parse import parse_qsl

import pytest
import requests
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.auth.base import PermissionDenied, SessionExpired, UpstreamUnavailable
from app.auth.basic import BasicAuthProvider
from app.auth.inprocess import InProcessProvider
from app.auth.sso_session import SsoSessionProvider
from app.auth.web_forms import MAX_HTML_BYTES, form_request, form_response

BASE = "https://jira.example/jira"
FIELDS = [("id", "123"), ("atl_token", "csrf+secret&"), ("guid", "next-guid"),
          ("customfield_12", "한글 + a"), ("customfield_12", "b&c")]


class Reply:
    def __init__(self, status=200, body=b"<form></form>", headers=None, data=None):
        self.status = self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=UTF-8"}
        self.content, self.data, self.disposed = body, data, False

    def body(self):
        return self.content

    def json(self):
        if self.data is None:
            raise ValueError("not json")
        return self.data

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def dispose(self):
        self.disposed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.dispose()


class Sender:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def send(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def request(self, method, url, **kwargs):
        return self.send(method, url, **kwargs)

    def fetch(self, url, method="GET", **kwargs):
        return self.send(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self.send("GET", url, **kwargs)


def sso(*responses):
    provider = object.__new__(SsoSessionProvider)
    provider.base = BASE
    sender = Sender(*responses)
    provider._context = SimpleNamespace(request=sender)
    jobs = []

    def submit(fn, priority=0, *, may_commit=False):
        jobs.append((priority, may_commit))
        return fn()

    provider._submit = submit
    return provider, sender, jobs


@pytest.mark.parametrize("path", [
    "/secure/ConvertIssueSetIssueType.jspa?id=123",
    "/jira/secure/ConvertIssueSetIssueType.jspa?id=123",
    BASE + "/secure/ConvertIssueSetIssueType.jspa?id=123",
    "ConvertIssueSetIssueType.jspa?id=123",
])
def test_context_path_and_duplicate_form_values_are_preserved(path):
    url, headers, body = form_request(BASE, path, FIELDS, False)
    assert url == BASE + "/secure/ConvertIssueSetIssueType.jspa?id=123"
    assert parse_qsl(body.decode("ascii")) == FIELDS
    assert headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    assert headers["Origin"] == "https://jira.example"
    assert headers["Referer"] == BASE + "/"


@pytest.mark.parametrize("path", [
    "https://attacker.example/secure/ConvertIssue.jspa", "//attacker.example/secure/ConvertIssue.jspa",
    "https://jira.example.attacker.example/jira/secure/ConvertIssue.jspa",
    "https://jira.example:444/jira/secure/ConvertIssue.jspa",
    "https://user:password@jira.example/jira/secure/ConvertIssue.jspa",
    "https://jira.example/another/secure/ConvertIssue.jspa",
    "https://jira.example/jira/../secure/ConvertIssue.jspa", "/secure/Admin.jspa",
    "/rest/api/2/issue", "/secure/ConvertIssue.jspa#fragment", "/secure/ConvertIssue.jspa\n",
    "\\\\attacker.example\\secure\\ConvertIssue.jspa",
])
def test_foreign_or_unknown_actions_fail_before_queue_or_transport(path):
    provider, sender, jobs = sso()
    with pytest.raises(ValueError):
        provider.web_form(path, [])
    assert sender.calls == jobs == []


@pytest.mark.parametrize("path,fields,commit", [
    ("/secure/ConvertIssueConvert.jspa", None, False),
    ("/secure/ConvertIssueConvert.jspa", [], False),
    ("/secure/ConvertIssueSetIssueType.jspa", [], True),
    ("/browse/DL-123", [], False),
])
def test_commit_endpoints_cannot_be_read_or_mislabelled(path, fields, commit):
    with pytest.raises(ValueError):
        form_request(BASE, path, fields, commit)


def test_sso_uses_session_form_bytes_without_redirect_retry_or_json_headers():
    replies = [Reply(302, headers={"Location": "/jira/browse/DL-123"}), Reply(), Reply()]
    provider, sender, jobs = sso(*replies)
    result = provider.web_form("/secure/ConvertIssueConvert.jspa", FIELDS, may_commit=True)
    assert result["location"] == "/jira/browse/DL-123"
    assert len(sender.calls) == 1
    method, url, args = sender.calls[0]
    assert method == "POST" and url == BASE + "/secure/ConvertIssueConvert.jspa"
    assert parse_qsl(args["data"].decode("ascii")) == FIELDS
    assert args["max_redirects"] == args["max_retries"] == 0
    assert args["timeout"] == provider.REQUEST_TIMEOUT_MS
    assert "json" not in args and "X-Atlassian-Token" not in args["headers"]
    assert jobs == [(-1, True)] and replies[0].disposed
    provider.web_form("/secure/MoveSubTaskParent!default.jspa?id=123")
    assert jobs[-1] == (0, False)
    provider.web_form("/secure/ConvertIssueSetIssueType.jspa", FIELDS)
    assert jobs[-1] == (-1, False)


def test_basic_provider_encodes_same_form_and_never_follows_redirect():
    provider = BasicAuthProvider(BASE, "user", "secret")
    reply = Reply(302, headers={"Location": "/jira/browse/DL-123"})
    provider.session = Sender(reply)
    provider.web_form("/secure/MoveSubTaskParent.jspa", FIELDS, may_commit=True)
    method, url, args = provider.session.calls[0]
    assert method == "POST" and url.startswith(BASE)
    assert parse_qsl(args["data"].decode("ascii")) == FIELDS
    assert args["allow_redirects"] is False and args["stream"] is True
    assert len(provider.session.calls) == 1 and reply.disposed


def test_inprocess_real_http_form_encoding_and_session_cookies():
    app, calls = FastAPI(), []

    @app.api_route("/secure/MoveSubTaskParent.jspa", methods=["POST"])
    async def form(request: Request):
        calls.append((request.headers, await request.body()))
        return Response(status_code=302, headers={"Location": "/browse/DL-123"})

    @app.get("/browse/DL-123")
    def redirected():
        pytest.fail("Transport must not follow redirects")

    provider = object.__new__(InProcessProvider)
    provider._client = TestClient(app)
    provider._client.cookies.set("JSESSIONID", "mock-session")
    provider._serial = lambda fn, priority=None: fn()
    try:
        result = provider.web_form("/secure/MoveSubTaskParent.jspa", FIELDS, may_commit=True)
        assert result["status"] == 302 and len(calls) == 1
        assert parse_qsl(calls[0][1].decode("ascii")) == FIELDS
        assert "JSESSIONID=mock-session" in calls[0][0]["cookie"]
    finally:
        provider.close()


@pytest.mark.parametrize("status,body,headers,error", [
    (401, b"secret", {}, SessionExpired),
    (200, b'<form id="login-form"><input name="os_username"></form>', {}, SessionExpired),
    (302, b"", {"Location": "https://sso.example/login?token=secret"}, SessionExpired),
    (302, b"", {"Location": "/jira/secure/SessionTimeout.jspa"}, SessionExpired),
    (403, b"", {"X-Seraph-LoginReason": "AUTHENTICATION_DENIED"}, SessionExpired),
    (503, b"secret", {}, UpstreamUnavailable),
])
def test_errors_never_expose_form_body_or_tokens(status, body, headers, error, capsys):
    with pytest.raises(error) as raised:
        form_response(BASE, status, headers, body, lambda: pytest.fail("No identity probe expected"))
    assert "secret" not in str(raised.value)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("status,data,error", [
    (200, {"name": "alice"}, PermissionDenied),
    (401, None, SessionExpired),
    (503, None, UpstreamUnavailable),
    (200, None, UpstreamUnavailable),
    (200, {}, UpstreamUnavailable),
])
def test_sso_bare_403_uses_same_session_identity_proof(status, data, error):
    provider, sender, _ = sso(Reply(403), Reply(status, data=data))
    with pytest.raises(error):
        provider.web_form("/secure/ConvertIssue.jspa?id=123")
    assert len(sender.calls) == 2
    assert sender.calls[1][1] == BASE + "/rest/api/2/myself"
    assert sender.calls[1][2]["max_redirects"] == 0


def test_oversized_html_is_not_truncated_or_parsed():
    provider, sender, _ = sso(Reply(body=b"x" * (MAX_HTML_BYTES + 1)))
    with pytest.raises(UpstreamUnavailable, match="1MiB"):
        provider.web_form("/secure/ConvertIssue.jspa")
    assert len(sender.calls) == 1
    basic = BasicAuthProvider(BASE, "user", "secret")
    basic.session = Sender(Reply(body=b"x" * (MAX_HTML_BYTES + 1)))
    with pytest.raises(UpstreamUnavailable, match="1MiB"):
        basic.web_form("/secure/ConvertIssue.jspa")


def test_basic_network_failure_is_single_attempt_and_sanitized():
    provider = BasicAuthProvider(BASE, "user", "secret")
    provider.session = Sender(requests.Timeout("secret request body"))
    with pytest.raises(UpstreamUnavailable) as raised:
        provider.web_form("/secure/ConvertIssueConvert.jspa", FIELDS, may_commit=True)
    assert "secret" not in str(raised.value) and len(provider.session.calls) == 1


def test_sso_transport_error_is_sanitized_before_queue_retirement_logs(capsys):
    provider, sender, jobs = sso(TimeoutError("POST url?atl_token=secret form=secret"))
    with pytest.raises(UpstreamUnavailable) as raised:
        provider.web_form("/secure/ConvertIssueConvert.jspa", FIELDS, may_commit=True)
    assert "secret" not in str(raised.value)
    assert provider._is_transport_error(raised.value)
    assert raised.value.diagnostic == {"reason": "transport_timeout"}
    assert jobs == [(-1, True)] and len(sender.calls) == 1
    assert capsys.readouterr() == ("", "")


def test_normal_jira_page_with_login_link_is_not_a_login_form():
    html = b'<a href="/login.jsp">Log in</a><form action="ConvertIssueSetIssueType.jspa"></form>'
    assert form_response(BASE, 200, {}, html, lambda: None)["html"] == html.decode()


def test_sso_non_transport_exception_keeps_no_exception_or_form_values():
    provider, sender, _ = sso(TypeError("secret form body or url"))
    with pytest.raises(UpstreamUnavailable) as raised:
        provider.web_form("/secure/ConvertIssue.jspa?id=123")
    assert raised.value.diagnostic == {"reason": "transport_failure"}
    assert "secret" not in str(raised.value) and len(sender.calls) == 1
