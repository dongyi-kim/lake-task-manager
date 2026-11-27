"""Slow-Jira defaults reach each native transport without real network waits."""
import asyncio
from types import SimpleNamespace

import pytest

from app.auth.basic import BasicAuthProvider
from app.auth.base import PermissionDenied
from app.auth.sso_session import SsoSessionProvider


@pytest.mark.parametrize("method", ["get", "post", "put", "delete", "bytes"])
def test_sso_regular_rest_passes_explicit_two_minute_native_timeout(method):
    calls = []
    response = SimpleNamespace(status=200, headers={}, json=lambda: {}, body=lambda: b"data", dispose=lambda: None)
    def request(url, **kwargs):
        calls.append((url, kwargs))
        return response
    provider = object.__new__(SsoSessionProvider)
    provider.base = "https://jira.example"
    provider._context = SimpleNamespace(request=SimpleNamespace(**{name: request for name in ("get", "post", "put", "delete")}))
    provider._xsrf_headers = lambda *_args: {}
    if method == "get":
        provider._fetch("/rest/api/2/issue/DL-1", {}, False)
    elif method == "bytes":
        provider._fetch_bytes("/attachment/1", {})
    else:
        provider._write(method, "/rest/api/2/issue/DL-1/comment/1", None, {})
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 120000
    assert provider.QUEUE_TIMEOUT == 60 and provider.JOB_TIMEOUT == 150


def test_sso_denied_read_identity_probe_stays_inside_running_budget():
    calls = []
    responses = [SimpleNamespace(status=403, headers={}, text=lambda: "Forbidden", dispose=lambda: None),
                 SimpleNamespace(status=200, json=lambda: {"name": "fixture-user"}, dispose=lambda: None)]
    def get(url, **kwargs):
        calls.append(kwargs["timeout"])
        return responses.pop(0)
    provider = object.__new__(SsoSessionProvider)
    provider.base = "https://jira.example"
    provider._context = SimpleNamespace(request=SimpleNamespace(get=get))
    with pytest.raises(PermissionDenied):
        provider._fetch("/rest/api/2/issue/DL-1", {}, False, quiet=True)
    assert calls == [120000, 20000]
    assert sum(calls) < provider.JOB_TIMEOUT * 1000


def test_silent_renewal_retains_its_own_short_budget():
    provider = object.__new__(SsoSessionProvider)
    observed = []
    def submit(fn, priority=0, wait=None, **kwargs):
        observed.append((wait, kwargs))
        return False
    provider._submit = submit
    assert provider.renew_silent([]) is False
    assert observed == [(30, {})]


@pytest.mark.parametrize("method", ["get_json", "get_bytes", "post_json", "put_json", "delete", "post_multipart"])
def test_basic_rest_passes_finite_connect_and_slow_read_timeouts(method):
    calls = []
    response = SimpleNamespace(status_code=200, headers={}, text="{}", content=b"data",
                               json=lambda: {}, raise_for_status=lambda: None)
    def request(url, **kwargs):
        calls.append(kwargs)
        return response
    provider = object.__new__(BasicAuthProvider)
    provider.base = "https://jira.example"
    provider.session = SimpleNamespace(**{name: request for name in ("get", "post", "put", "delete")})
    if method == "post_multipart":
        provider.post_multipart("/rest/api/2/issue/DL-1/attachments", "fixture.txt", b"data")
    else:
        getattr(provider, method)("/rest/api/2/issue/DL-1/comment/1")
    assert len(calls) == 1 and calls[0]["timeout"] == (10, 120)


def test_stdio_proxy_allows_normal_backend_queue_and_run_without_network(monkeypatch):
    from app.local_mcp import stdio
    observed = []
    class CapturedClient(Exception):
        pass
    class Client:
        async def __aenter__(self):
            raise CapturedClient
        async def __aexit__(self, *_args):
            pass
    def client(**kwargs):
        observed.append(kwargs)
        return Client()
    monkeypatch.setattr(stdio, "connection_settings", lambda: ("http://127.0.0.1:4457/mcp", "fixture-only"))
    monkeypatch.setattr(stdio.httpx2, "AsyncClient", client)
    with pytest.raises(CapturedClient):
        asyncio.run(stdio.proxy())
    assert len(observed) == 1 and observed[0]["timeout"] == 300
    assert observed[0]["follow_redirects"] is False and observed[0]["trust_env"] is False
