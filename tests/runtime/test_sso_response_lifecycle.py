"""Long-lived SSO contexts must release Playwright's retained response bodies."""

import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from app.auth.base import PermissionDenied, SessionExpired, UpstreamError, UpstreamUnavailable
from app.auth.sso_session import SsoSessionProvider, _authed, service_probe


class Response:
    def __init__(self, status=200, *, value=None, malformed=False, dispose_error=False):
        self.status = status
        self.headers = {"content-type": "application/json"}
        self.value = {"name": "alice"} if value is None else value
        self.malformed = malformed
        self.dispose_error = dispose_error
        self.disposals = []

    def json(self):
        assert not self.disposals, "body must be read before disposal"
        if self.malformed:
            raise ValueError("not JSON")
        return self.value

    def text(self):
        assert not self.disposals
        return "response body"

    def body(self):
        assert not self.disposals
        return b"attachment"

    def dispose(self):
        self.disposals.append(threading.get_ident())
        if self.dispose_error:
            raise RuntimeError("connection closed during cleanup")


class Request:
    def __init__(self):
        self.responses = []
        self.returned = []
        self.owner_threads = []

    def get(self, *_args, **_kwargs):
        self.owner_threads.append(threading.get_ident())
        response = self.responses.pop(0) if self.responses else Response()
        self.returned.append(response)
        return response

    post = put = delete = fetch = get


@pytest.fixture
def provider(monkeypatch):
    request = Request()
    context = SimpleNamespace(request=request, cookies=lambda *_: [])
    browser = SimpleNamespace(new_context=lambda **_: context, is_connected=lambda: True,
                              close=lambda: None)
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(
        start=lambda: SimpleNamespace(stop=lambda: None))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr("app.auth.sso_session._launch", lambda *_a, **_kw: browser)
    store = SimpleNamespace(any_exists=lambda: True, merged=lambda: {"cookies": []})
    result = SsoSessionProvider("https://jira.example", store)
    yield result, request
    result.close()


def assert_released_on_owner(request):
    assert request.returned
    for response, owner in zip(request.returned, request.owner_threads):
        assert response.disposals == [owner]
        assert owner != threading.get_ident()


def test_repeated_reads_do_not_accumulate_retained_bodies(provider):
    session, request = provider
    for _ in range(40):
        assert session.get_json("/rest/api/2/search") == {"name": "alice"}
        assert sum(not r.disposals for r in request.returned) == 0
    assert_released_on_owner(request)


@pytest.mark.parametrize("method,expected", [
    ("get_text", "response body"),
    ("get_bytes", (b"attachment", "application/json")),
    ("post_json", {"name": "alice"}),
    ("put_json", {"name": "alice"}),
    ("delete", 200),
])
def test_body_and_write_results_survive_disposal(provider, method, expected):
    session, request = provider
    assert getattr(session, method)("/rest/api/2/issue/DL-1") == expected
    assert_released_on_owner(request)


@pytest.mark.parametrize("method", ["get_json", "get_text", "get_bytes", "post_json", "put_json", "delete"])
@pytest.mark.parametrize("status,error", [(401, SessionExpired), (500, UpstreamUnavailable)])
def test_error_responses_are_released_without_changing_classification(provider, method, status, error):
    session, request = provider
    request.responses = [Response(status)]
    with pytest.raises(error):
        getattr(session, method)("/rest/api/2/issue/DL-1")
    assert_released_on_owner(request)


def test_permission_failure_releases_failed_response_and_identity_probe(provider):
    session, request = provider
    request.responses = [Response(403), Response()]
    with pytest.raises(PermissionDenied):
        session.get_json("/rest/api/2/issue/DL-1")
    assert_released_on_owner(request)


def test_json_parse_failure_still_releases_response(provider):
    session, request = provider
    request.responses = [Response(malformed=True)]
    with pytest.raises(ValueError, match="not JSON"):
        session.get_json("/rest/api/2/search")
    assert_released_on_owner(request)


@pytest.mark.parametrize("status", [200, 204, 400])
def test_cleanup_failure_never_changes_write_result_or_replays_write(provider, status):
    session, request = provider
    request.responses = [Response(status, malformed=status == 204, dispose_error=True)]
    if status == 400:
        with pytest.raises(UpstreamError) as caught:
            session.post_json("/rest/api/2/issue/DL-1", {"fields": {}})
        assert caught.value.status == 400
    else:
        assert session.post_json("/rest/api/2/issue/DL-1", {"fields": {}}) == (
            {} if status == 204 else {"name": "alice"})
    assert len(request.returned) == 1
    assert_released_on_owner(request)


@pytest.mark.parametrize("final_status", [200, 400, 500])
def test_multipart_retry_releases_both_responses(provider, final_status):
    session, request = provider
    request.responses = [Response(403), Response(final_status)]
    action = lambda: session.post_multipart("/rest/api/2/issue/DL-1/attachments", "a.txt", b"a")
    if final_status >= 400:
        with pytest.raises(UpstreamError if final_status == 400 else UpstreamUnavailable):
            action()
    else:
        assert action() == {"name": "alice"}
    assert len(request.returned) == 2
    assert_released_on_owner(request)


def test_multipart_rejected_body_is_released_before_retry_transport_failure(provider, monkeypatch):
    session, request = provider
    rejected = Response(403)
    request.responses = [rejected]
    send = request.post
    calls = []

    def retry(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return send(*args, **kwargs)
        assert rejected.disposals == [threading.get_ident()]
        raise TimeoutError("request timed out")

    monkeypatch.setattr(request, "post", retry)
    with pytest.raises(UpstreamUnavailable):
        session.post_multipart("/rest/api/2/issue/DL-1/attachments", "a.txt", b"a")
    assert len(calls) == 2
    assert_released_on_owner(request)


@pytest.mark.parametrize("probe", ["service", "jira"])
@pytest.mark.parametrize("status,malformed", [(200, False), (200, True), (401, False)])
def test_authentication_probes_release_responses(provider, probe, status, malformed):
    session, request = provider
    request.responses = [Response(status, malformed=malformed)]
    def call():
        if probe == "service":
            return service_probe(session._context, session.base, ["/rest/api/2/myself"])[0]
        return _authed(session._context, session.base)
    assert session._submit(call) is (status == 200 and not malformed)
    assert_released_on_owner(request)


def test_form_identity_cleanup_failure_preserves_permission_classification(provider):
    session, request = provider
    request.responses = [Response(403), Response(dispose_error=True)]
    with pytest.raises(PermissionDenied):
        session.web_form("/secure/ConvertIssue.jspa?id=1")
    assert_released_on_owner(request)
