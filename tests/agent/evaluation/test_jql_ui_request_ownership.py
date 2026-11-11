"""A benchmark owner is required before app/provider work; no listener or browser."""
from copy import deepcopy
import json
from types import SimpleNamespace

import anyio
import pytest

from tools import jql_ui_benchmark as runner
from tools import jql_ui_benchmark_fixture as fixture


OWNER = "test-only-owner-not-a-real-run-secret"
HEADER = b"x-ltm-benchmark-owner"


def guarded(app, token=OWNER):
    factory = getattr(fixture, "RunOwnershipGuard", None)
    assert callable(factory), "Installed UI fixture needs a pre-handler ASGI ownership guard"
    return factory(app, token=token)


def scope(headers=()):
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/ticket/DL-9001",
            "raw_path": b"/api/ticket/DL-9001", "query_string": b"",
            "root_path": "", "headers": [(b"host", b"127.0.0.1:4457"), *headers],
            "client": ("127.0.0.1", 51000), "server": ("127.0.0.1", 4457)}


@pytest.mark.parametrize("headers", [[], [(HEADER, b"another-run")],
    [(HEADER, OWNER.encode()), (HEADER, OWNER.encode())],
    [(HEADER, OWNER.encode() + b",another-run")]],
    ids=["missing", "wrong", "duplicate", "combined-values"])
def test_unowned_http_is_rejected_before_app_or_provider_and_not_counted_as_rest(headers):
    ledger = fixture.WireLedger()
    inner_calls, sent = [], []

    async def inner(request, receive, send):
        inner_calls.append(request)
        ledger.observe(lambda *args, **kwargs: SimpleNamespace(status_code=200, content=b"{}"))(
            "GET", "/rest/api/2/issue/DL-9001")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"owned"})

    guard = guarded(inner)

    async def invoke():
        async def receive():
            pytest.fail("Denied request must not consume an application body")
        async def send(message):
            sent.append(message)
        await guard(scope(headers), receive, send)

    anyio.run(invoke)
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 403
    assert inner_calls == [] and ledger.snapshot() == []
    assert guard.blocked_requests == 1
    assert OWNER not in repr(sent) and OWNER not in repr(guard)


@pytest.mark.parametrize("streaming", [False, True], ids=["normal", "streaming"])
def test_owned_http_preserves_body_scope_and_unbuffered_streaming(streaming):
    sent, seen = [], []
    original_scope = scope([(b"X-LTM-Benchmark-Owner", OWNER.encode())])
    before_scope = deepcopy(original_scope)
    body = {"type": "http.request", "body": b"unchanged request", "more_body": False}
    first = {"type": "http.response.body", "body": b'data: {"n":1}\n\n', "more_body": streaming}

    async def scenario():
        first_sent, release = anyio.Event(), anyio.Event()

        async def inner(request, receive, send):
            assert request is original_scope
            seen.append(await receive())
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            await send(first)
            first_sent.set()
            if streaming:
                await release.wait()
                await send({"type": "http.response.body", "body": b'data: {"n":2}\n\n', "more_body": False})

        guard = guarded(inner)
        async def receive():
            return body
        async def send(message):
            sent.append(message)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(guard, original_scope, receive, send)
            try:
                with anyio.fail_after(2):
                    await first_sent.wait()
                    assert sent[1] is first  # Available before the producer's final chunk.
            finally:
                release.set()
        assert guard.blocked_requests == 0

    anyio.run(scenario)
    assert seen == [body] and original_scope == before_scope
    assert len(sent) == (3 if streaming else 2)


@pytest.mark.parametrize("token", [None, ""])
def test_guard_cannot_be_installed_without_an_explicit_owner(token):
    with pytest.raises(ValueError, match="owner|token"):
        guarded(lambda *_args: None, token)


def test_lifespan_is_delegated_without_http_authentication():
    calls = []
    async def inner(request, receive, send):
        calls.append(request)
    guard = guarded(inner)
    async def invoke():
        await guard({"type": "lifespan"}, None, None)
    anyio.run(invoke)
    assert calls == [{"type": "lifespan"}] and guard.blocked_requests == 0


def test_child_environment_uses_explicit_same_run_owner_without_changing_source(tmp_path):
    source = {"PYTHONPATH": "pinned-overlay", "JQL_UI_OWNER_TOKEN": "old-owner"}
    before = dict(source)
    env = runner.child_environment(tmp_path, tmp_path, tmp_path / "config", tmp_path / "bootstrap", 0,
                                   source, ownership_token=OWNER)
    assert env["JQL_UI_OWNER_TOKEN"] == OWNER and source == before
    assert "pinned-overlay" in env["PYTHONPATH"].split(runner.os.pathsep)


def test_control_request_supplies_owner_header_without_logging_it(monkeypatch):
    requests = []
    monkeypatch.setattr(runner, "_OWNERSHIP_TOKEN", OWNER, raising=False)
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return b'{"settled":true}'
    def fetch(request, **kwargs):
        requests.append(request)
        return Response()
    monkeypatch.setattr(runner.urllib.request, "urlopen", fetch)
    assert runner.request("/__jql_benchmark/settle", method="POST") == {"settled": True}
    assert dict((key.lower(), value) for key, value in requests[0].header_items())[HEADER.decode()] == OWNER


def test_owned_browser_context_supplies_same_owner_header(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_OWNERSHIP_TOKEN", OWNER, raising=False)
    lib = tmp_path / "app/static/lib"
    lib.mkdir(parents=True)
    (lib / "guides.js").write_text('id: "test-guide"', encoding="utf-8")
    (lib / "cacheMemo.js").write_text('now = () => Date.now() / 1000', encoding="utf-8")
    contexts = []
    page = SimpleNamespace(on=lambda *_args: None)
    context = SimpleNamespace(add_init_script=lambda *_args: None, route=lambda *_args: None,
                              new_page=lambda: page)
    def new_context(**options):
        contexts.append(options)
        return context
    assert runner.new_page(SimpleNamespace(new_context=new_context), tmp_path, []) is page
    assert {key.lower(): value for key, value in contexts[0]["extra_http_headers"].items()}[HEADER.decode()] == OWNER
    assert contexts[0]["viewport"] == {"width": 1600, "height": 1000}


@pytest.mark.parametrize("rpc", [False, True], ids=["control", "authenticated-mcp"])
def test_restart_transport_sends_same_owner_and_redacts_echoes_without_dropping_mcp_auth(monkeypatch, rpc):
    from tools import jql_process_restart_benchmark as restart
    evidence, headers_seen = [], []
    bearer = "unit-only-mcp-bearer"
    async def send(method, url, headers, data, timeout):
        headers_seen.append(headers)
        return 200, json.dumps({"echo": OWNER, "bearer": bearer})
    monkeypatch.setattr(restart, "send_http", send)
    transport = restart.Transport(SimpleNamespace(record=evidence.append), restart.Deadline(2, 1),
                                  ownership_token=OWNER)
    transport.token = bearer
    reply = transport.http("/mcp" if rpc else "/__jql_benchmark", rpc_method="tools/list" if rpc else None)
    assert headers_seen[0]["X-LTM-Benchmark-Owner"] == OWNER
    assert headers_seen[0].get("Authorization") == ("Bearer " + bearer if rpc else None)
    assert reply == {"echo": "[redacted]", "bearer": "[redacted]"}
    assert OWNER not in json.dumps(evidence) and bearer not in json.dumps(evidence)
