"""SDK authentication lifecycle against isolated clients; no real SSO or injected latency."""

import base64

import anyio
import pytest
from mcp import Client

from app.auth.base import LoginRequired, PermissionDenied, SessionExpired, UpstreamError, UpstreamUnavailable
from app.services.ticket_commands import CommandError


def prod(env, monkeypatch, results=None):
    monkeypatch.setattr(env.client, "env", "prod")
    login = [True]
    monkeypatch.setattr(env.client, "needs_login", lambda: login[0])
    calls = []
    states = iter(results or [{"ok": True}])
    last = {"ok": True}
    def probe(**options):
        nonlocal last
        calls.append(options)
        last = next(states, last)
        login[0] = not last.get("ok", False)
        return last
    monkeypatch.setattr(env.client, "proactive_auth_probe", probe)
    return calls


def invoke(env, name, arguments=None):
    async def call():
        async with Client(env.runtime.server) as mcp:
            return await mcp.call_tool(name, arguments or {})
    return anyio.run(call)


def test_initial_login_has_explicit_action_and_can_resume_without_new_connection(build_env, monkeypatch):
    env = build_env()
    prod(env, monkeypatch, [{"needLogin": True}, {"needLogin": True}, {"ok": True}, {"ok": True}])
    opened = []
    env.runtime.authentication.request_login = lambda service: opened.append(service) or {"started": True, "pending": True}
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            first = await mcp.call_tool("get_user", {})
            assert first.is_error
            error = first.structured_content["error"]
            assert error["code"] == "authentication_required" and error["service"] == "jira"
            assert error["nextAction"] == {"tool": "authenticate", "arguments": {"service": "jira", "interactive": True}}
            assert opened == [] and env.calls == []
            login = await mcp.call_tool("authenticate", {"interactive": True})
            assert not login.is_error and login.structured_content["data"]["pending"]
            assert not login.structured_content["data"]["authenticated"] and opened == ["jira"]
            status = await mcp.call_tool("get_auth_status", {})
            assert status.structured_content["data"]["authenticated"]
            user = await mcp.call_tool("get_user", {})
            assert not user.is_error and user.structured_content["data"]["id"] == "u01"
    anyio.run(scenario)


def test_read_expiration_recovers_and_replays_once(build_env, monkeypatch):
    env = build_env()
    probes = prod(env, monkeypatch)
    calls = []
    def read_fixture() -> dict:
        calls.append(1)
        if len(calls) == 1:
            raise SessionExpired("expired between probe and read")
        return {"latest": True}
    env.runtime.register(read_fixture, read_only=True, output_type=dict)
    result = invoke(env, "read_fixture")
    assert not result.is_error and result.structured_content["data"] == {"latest": True}
    assert len(calls) == 2 and probes == [{"force": False, "wait": True}, {"force": True, "wait": True}]


def test_jira_create_rejected_by_auth_reuses_same_receipt_and_commits_once(build_env, monkeypatch):
    env = build_env()
    prod(env, monkeypatch)
    original = env.provider._write
    attempts = []
    def expire_once(method, path, body, params, **kwargs):
        if path == "/rest/api/2/issue" and method == "post":
            attempts.append(body)
            if len(attempts) == 1:
                raise SessionExpired("explicit upstream 401")
        return original(method, path, body, params, **kwargs)
    monkeypatch.setattr(env.provider, "_write", expire_once)
    body = {"type": "Task", "summary": "recover this exact creation", "clientMutationId": "auth:create:0001"}
    before = len(env.store.issues)
    created = invoke(env, "create_task", {"body": body})
    assert not created.is_error, created.structured_content
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(env.store.issues) == before + 1
    repeated = invoke(env, "create_task", {"body": body})
    assert not repeated.is_error and repeated.structured_content["data"] == created.structured_content["data"]
    assert len(attempts) == 2 and len(env.store.issues) == before + 1


def test_response_loss_does_not_replay_write_or_start_login(build_env, monkeypatch):
    env = build_env()
    probes = prod(env, monkeypatch)
    original = env.provider._write
    attempts = []
    def lost(method, path, body, params, **kwargs):
        value = original(method, path, body, params, **kwargs)
        if path == "/rest/api/2/issue" and method == "post":
            attempts.append(1)
            raise UpstreamUnavailable("response lost after commit")
        return value
    monkeypatch.setattr(env.provider, "_write", lost)
    env.runtime.authentication.request_login = lambda _: pytest.fail("response loss is not logout")
    before = len(env.store.issues)
    result = invoke(env, "create_task", {"body": {
        "type": "Task", "summary": "committed once", "clientMutationId": "auth:unknown:0001"}})
    assert result.is_error and result.structured_content["error"]["code"] == "outcome_unknown"
    assert len(attempts) == 1 and len(env.store.issues) == before + 1 and len(probes) == 1


@pytest.mark.parametrize("exception", [PermissionDenied("/issue"), UpstreamError(400, "/issue"),
                                      UpstreamError(500, "/issue"), UpstreamUnavailable("offline")])
def test_non_auth_failures_do_not_renew_or_repeat(build_env, monkeypatch, exception):
    env = build_env()
    probes = prod(env, monkeypatch)
    called = []
    def rejected_read() -> dict:
        called.append(1)
        raise exception
    env.runtime.register(rejected_read, read_only=True, output_type=dict)
    assert invoke(env, "rejected_read").is_error
    assert len(called) == len(probes) == 1


def test_unrecoverable_expiration_does_not_repeat_and_never_returns_empty_identity(build_env, monkeypatch):
    env = build_env()
    prod(env, monkeypatch, [{"ok": True}, {"needLogin": True}])
    def expired():
        raise LoginRequired("first login")
    monkeypatch.setattr(env.client, "current_user_for_authorization", expired)
    result = invoke(env, "get_user")
    assert result.is_error and result.structured_content["error"]["needLogin"]
    assert "data" not in result.structured_content


@pytest.mark.parametrize("state,code", [({"pending": True}, "authentication_pending"),
                                       ({"ok": False, "needLogin": False}, "upstream_unavailable")])
def test_pending_or_offline_auth_does_not_open_login_or_execute_write(build_env, monkeypatch, state, code):
    env = build_env()
    prod(env, monkeypatch, [state])
    env.runtime.authentication.request_login = lambda _: pytest.fail("not confirmed logout")
    result = invoke(env, "create_task", {"body": {
        "type": "Task", "summary": "blocked preflight", "clientMutationId": "auth:blocked:0001"}})
    assert result.is_error and result.structured_content["error"]["code"] == code
    assert not result.structured_content["error"]["needLogin"] and env.calls == []
    explicit = invoke(env, "authenticate", {"interactive": True})
    assert not explicit.structured_content["data"]["needLogin"]


def test_confluence_login_is_independent_and_unified_search_keeps_jira_results(build_env, monkeypatch):
    env = build_env()
    prod(env, monkeypatch)
    env.runtime.settings.confluence_base = "https://confluence.invalid"
    attempts = []
    def confluence_expired(*_args, **_kwargs):
        attempts.append(1)
        raise SessionExpired("Confluence login needed")
    monkeypatch.setattr(env.client, "_conf_get_json", confluence_expired)
    opened = []
    env.runtime.authentication.request_login = lambda service: opened.append(service) or {"started": True, "pending": True}
    result = invoke(env, "search_workspace", {"query": "test"})
    assert not result.is_error and result.structured_content["partial"]
    assert result.structured_content["authentication"]["confluence"]["needLogin"]
    assert "jira" in result.structured_content["data"] and not getattr(env.client, "_session_dead", False)
    login = invoke(env, "authenticate", {"service": "confluence", "interactive": True})
    assert login.structured_content["data"]["pending"] and opened == ["confluence"]
    monkeypatch.setattr(env.client, "_conf_get_json", lambda *_a, **_k: {"username": "confluence-user"})
    monkeypatch.setattr(env.client, "_provider_generation", env.client._provider_generation + 1)
    assert invoke(env, "get_auth_status", {"service": "confluence"}).structured_content["data"]["authenticated"]


def test_transport_catalog_exposes_auth_tools_with_safe_defaults(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            catalog = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            assert len(catalog) == 54
            assert catalog["get_auth_status"].annotations.read_only_hint
            assert not catalog["authenticate"].annotations.read_only_hint
            assert catalog["authenticate"].input_schema["properties"]["interactive"]["default"] is False
    anyio.run(scenario)


def test_wrapped_identity_401_recovers_but_transport_caused_401_does_not(build_env, monkeypatch):
    env = build_env()
    probes = prod(env, monkeypatch)
    calls = []
    def guarded_write(client_mutation_id: str) -> dict:
        calls.append(client_mutation_id)
        if len(calls) == 1:
            raise CommandError("identity expired before write", 401)
        return {"saved": True}
    env.runtime.register(guarded_write, read_only=False, output_type=dict)
    result = invoke(env, "guarded_write", {"client_mutation_id": "auth:guarded:0001"})
    assert not result.is_error and len(probes) == 2 and calls == ["auth:guarded:0001"] * 2
    def unavailable_read() -> dict:
        raise CommandError("identity unreachable", 401) from UpstreamUnavailable("offline")
    env.runtime.register(unavailable_read, read_only=True, output_type=dict)
    result = invoke(env, "unavailable_read")
    assert result.is_error and result.structured_content["error"]["code"] == "authorization_unavailable"
    assert len(probes) == 2


def test_prod_cached_read_reuses_issue_without_extra_auth_request(build_env, monkeypatch):
    env = build_env()
    prod(env, monkeypatch)
    monkeypatch.setattr(env.client, "needs_login", lambda: False)
    monkeypatch.setattr(env.client, "proactive_auth_probe", lambda **_: pytest.fail("fresh hit must not probe Jira"))
    direct = env.client.ticket_view("DL-1")
    before = len(env.calls)
    result = invoke(env, "get_ticket", {"key": "DL-1"})
    assert not result.is_error and result.structured_content["data"] == direct
    assert len(env.calls) == before


def test_search_auth_expiring_after_preflight_only_repeats_failed_source(build_env, monkeypatch):
    from app.domain import search
    env = build_env()
    prod(env, monkeypatch)
    env.runtime.settings.confluence_base = "https://confluence.invalid"
    monkeypatch.setattr(env.client, "_conf_get_json", lambda *_a, **_k: {"username": "u01"})
    counts = {"jira": 0, "confluence": 0}
    def jira(*_args):
        counts["jira"] += 1
        if counts["jira"] == 1:
            raise SessionExpired("Jira expired after preflight")
        return {"items": [{"key": "DL-1", "type": "jira", "title": "Ticket", "url": "/browse/DL-1"}]}
    def confluence(*_args):
        counts["confluence"] += 1
        return {"items": [{"id": "1", "type": "confluence", "title": "Page", "url": "/pages/1"}]}
    monkeypatch.setattr(search, "_search_jira", jira)
    monkeypatch.setattr(search, "_search_confluence", confluence)
    result = invoke(env, "search_workspace", {"query": "auth race"})
    assert not result.is_error and not result.structured_content["partial"]
    assert result.structured_content["data"]["confluence"]["items"] == [
        {"id": "1", "type": "confluence", "title": "Page", "url": "/pages/1"}]
    assert counts == {"jira": 2, "confluence": 1}


def test_confluence_replaced_provider_result_is_not_published(build_env, monkeypatch):
    env = build_env()
    prod(env, monkeypatch)
    env.runtime.settings.confluence_base = "https://confluence.invalid"
    def replaced(*_args, **_kwargs):
        env.client._provider_generation += 1
        return {"username": "old-user"}
    monkeypatch.setattr(env.client, "_conf_get_json", replaced)
    result = invoke(env, "get_auth_status", {"service": "confluence"})
    assert result.structured_content["data"]["pending"]
    assert not result.structured_content["data"]["authenticated"]
    assert env.runtime.authentication._confluence_result is None


def test_anonymous_confluence_200_renews_silently_before_requesting_login(build_env, monkeypatch):
    env = build_env()
    prod(env, monkeypatch)
    env.runtime.settings.confluence_base = "https://confluence.invalid"
    responses = iter([{"type": "anonymous"}, {"type": "known", "username": "u01"}])
    monkeypatch.setattr(env.client, "_conf_get_json", lambda *_a, **_k: next(responses))
    renewed = []
    monkeypatch.setattr(env.client, "_renew_service", lambda service: renewed.append(service) or True)
    env.runtime.authentication.request_login = lambda _: pytest.fail("silent renewal needs no user window")
    result = invoke(env, "authenticate", {"service": "confluence", "interactive": True})
    assert result.structured_content["data"]["authenticated"] and result.structured_content["data"]["recovered"]
    assert renewed == ["Confluence"]


def test_bulk_partial_auth_failure_reuses_successful_rows(build_env, monkeypatch):
    env = build_env()
    prod(env, monkeypatch)
    original = env.provider._write
    attempts = []
    def expire_second(method, path, body, params, **kwargs):
        if path == "/rest/api/2/issue" and method == "post":
            attempts.append(body)
            if len(attempts) == 2:
                raise SessionExpired("second row rejected")
        return original(method, path, body, params, **kwargs)
    monkeypatch.setattr(env.provider, "_write", expire_second)
    items = [{"summary": f"bulk auth {n}", "type": "Task", "epic": None} for n in range(2)]
    before = len(env.store.issues)
    result = invoke(env, "bulk_create", {"mode": "task", "items": items, "client_mutation_id": "auth:bulk:0001"})
    assert not result.is_error, result.structured_content
    assert len(result.structured_content["data"]["created"]) == 2
    assert len(env.store.issues) == before + 2 and len(attempts) == 3
    assert attempts[1] == attempts[2]


@pytest.mark.parametrize("tool,arguments,path", [
    ("get_user", {"user_id": "u01"}, "/rest/api/2/user"),
    ("search_users", {"query": "u01"}, "/rest/api/2/user/search"),
])
@pytest.mark.parametrize("recovered", [True, False])
def test_people_lookup_provider_expiry_recovers_or_reports_login(
        build_env, monkeypatch, tool, arguments, path, recovered):
    env = build_env()
    probes = prod(env, monkeypatch, [{"ok": True}] if recovered else [{"needLogin": True}])
    # A saved session can expire before the app knows about it: no preflight is expected.
    monkeypatch.setattr(env.client, "needs_login", lambda: False)
    original = env.provider.get_json
    attempts = []
    def expire_once(request_path, *args, **kwargs):
        if request_path == path:
            attempts.append(request_path)
            if len(attempts) == 1:
                raise SessionExpired("user lookup rejected with 401")
        return original(request_path, *args, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", expire_once)
    result = invoke(env, tool, arguments)
    assert probes == [{"force": True, "wait": True}]
    if not recovered:
        assert result.is_error and "data" not in result.structured_content
        assert result.structured_content["error"]["nextAction"]["tool"] == "authenticate"
        assert len(attempts) == 1
        assert env.cache.get("userbadge:prod:u01") is None
        # The rejected read must not become a successful empty cache entry.
        result = invoke(env, tool, arguments)
    assert not result.is_error, result.structured_content
    data = result.structured_content["data"]
    assert (data["id"] if tool == "get_user" else data["users"][0]["id"]) == "u01"
    assert len(attempts) == 2
    if tool == "get_user":
        monkeypatch.setattr(env.provider, "get_json", lambda *_a, **_k: pytest.fail("cached badge must not fetch"))
        assert invoke(env, tool, arguments).structured_content["data"] == data
        assert probes == [{"force": True, "wait": True}]


@pytest.mark.parametrize("tool,arguments,expected", [
    ("get_user", {"user_id": "no-such-fixture-user"}, None),
    ("search_users", {"query": "no-such-fixture-user"}, {"users": []}),
])
def test_people_lookup_unknown_user_keeps_legitimate_empty_result(build_env, monkeypatch, tool, arguments, expected):
    from httpx import Response
    env = build_env()
    prod(env, monkeypatch)
    monkeypatch.setattr(env.client, "needs_login", lambda: False)
    monkeypatch.setattr(env.client, "proactive_auth_probe", lambda **_: pytest.fail("unknown user is not logout"))
    if tool == "get_user":
        # jira820 synthesizes badges for unknown IDs; model Jira's actual missing-user response.
        monkeypatch.setattr(env.provider._client, "get", lambda *_a, **_k:
                            Response(404, json={"errorMessages": ["The user does not exist."]}))
    result = invoke(env, tool, arguments)
    assert not result.is_error
    data = result.structured_content["data"]
    if tool == "search_users":
        assert data["users"] == [] and data["returned"] == 0 and data["coverage"] == "bounded"
    else:
        assert data == expected


def test_bulk_repeated_auth_rejection_preserves_successes_and_login_action(build_env, monkeypatch):
    env = build_env()
    prod(env, monkeypatch)
    original = env.provider._write
    attempts = []
    def reject_second(method, path, body, params, **kwargs):
        if path == "/rest/api/2/issue" and method == "post":
            attempts.append(body)
            if len(attempts) > 1:
                raise SessionExpired("second row remains unauthenticated")
        return original(method, path, body, params, **kwargs)
    monkeypatch.setattr(env.provider, "_write", reject_second)
    items = [{"summary": f"bulk persistent auth {n}", "type": "Task", "epic": None} for n in range(2)]
    before = len(env.store.issues)
    result = invoke(env, "bulk_create", {"mode": "task", "items": items, "client_mutation_id": "auth:bulk:0002"})
    payload = result.structured_content
    assert result.is_error and payload["partial"]
    assert len(payload["data"]["created"]) == len(payload["data"]["failed"]) == 1
    assert len(env.store.issues) == before + 1 and len(attempts) == 3
    assert attempts[1] == attempts[2]
    assert payload["authentication"]["jira"]["nextAction"] == {
        "tool": "authenticate", "arguments": {"service": "jira", "interactive": True}}


@pytest.mark.parametrize("exception", [SessionExpired("fixture expired session"), UpstreamUnavailable("fixture offline")])
def test_ui_people_lookups_keep_best_effort_failure_behavior(build_env, monkeypatch, exception):
    from app.domain.search import search_users
    env = build_env()
    def expired(*_args, **_kwargs):
        raise exception
    monkeypatch.setattr(env.provider, "get_json", expired)
    assert env.client.user_badge("u01") is None
    assert search_users(env.client, env.runtime.settings, "u01") == []
    assert env.cache.get("userbadge:mock:u01") is None


@pytest.mark.parametrize("tool,arguments,path", [
    ("get_user", {"user_id": "u01"}, "/rest/api/2/user"),
    ("search_users", {"query": "u01"}, "/rest/api/2/user/search"),
])
def test_people_lookup_transport_failure_is_not_empty_data_or_logout(build_env, monkeypatch, tool, arguments, path):
    env = build_env()
    prod(env, monkeypatch)
    monkeypatch.setattr(env.client, "needs_login", lambda: False)
    monkeypatch.setattr(env.client, "proactive_auth_probe", lambda **_: pytest.fail("network failure must not probe auth"))
    attempts = []
    def unavailable(request_path, **_kwargs):
        attempts.append(request_path)
        raise UpstreamUnavailable("fixture connection unavailable")
    monkeypatch.setattr(env.provider._client, "get", unavailable)
    result = invoke(env, tool, arguments)
    assert result.is_error and "data" not in result.structured_content
    error = result.structured_content["error"]
    assert error["code"] == "upstream_unavailable" and error["needLogin"] is False
    assert error["retryable"] and "nextAction" not in error
    assert attempts == [path]
    assert env.cache.get("userbadge:prod:u01") is None


def _uploaded_auth_attachment(env):
    task = invoke(env, "create_task", {"body": {"type": "Task", "summary": "download recovery fixture",
                                             "clientMutationId": "auth:attachment:create:0001"}})
    assert not task.is_error, task.structured_content
    key = task.structured_content["data"]["key"]
    uploaded = invoke(env, "add_attachment", {"key": key, "filename": "recovery.txt",
        "base64_data": base64.b64encode(b"verified attachment content").decode(),
        "client_mutation_id": "auth:attachment:upload:0001"})
    assert not uploaded.is_error, uploaded.structured_content
    # Complete the upload's queued cache warming before changing env and injecting read failure.
    env.client._bg_pool.submit(lambda: None).result(timeout=5)
    return key, uploaded.structured_content["data"]["id"]


@pytest.mark.parametrize("failure", ["recoverable", "login_required", "network"])
def test_attachment_download_preserves_provider_auth_and_network_failures(build_env, monkeypatch, failure):
    env = build_env()
    key, attachment_id = _uploaded_auth_attachment(env)
    probes = prod(env, monkeypatch, [{"needLogin": True}] if failure == "login_required" else [{"ok": True}])
    monkeypatch.setattr(env.client, "needs_login", lambda: False)
    # Membership comes from the real Jira issue response, not a stubbed attachment list.
    member = next(row for row in env.client.get_issue(key)["fields"]["attachment"]
                  if str(row["id"]) == attachment_id)
    assert member["filename"] == "recovery.txt"
    original = env.provider.get_bytes
    attempts = []
    def download(path, *args, **kwargs):
        attempts.append(path)
        if failure == "network":
            raise UpstreamUnavailable("fixture attachment connection unavailable")
        if len(attempts) == 1:
            raise SessionExpired("fixture attachment rejected with 401")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(env.provider, "get_bytes", download)
    result = invoke(env, "read_attachment", {"key": key, "attachment_id": attachment_id})
    assert attempts[0].startswith(f"/secure/attachment/{attachment_id}/")
    if failure == "recoverable":
        assert not result.is_error, result.structured_content
        assert base64.b64decode(result.structured_content["data"]["base64"]) == b"verified attachment content"
        assert attempts == [attempts[0]] * 2
    else:
        assert result.is_error and "data" not in result.structured_content
        assert len(attempts) == 1
        error = result.structured_content["error"]
        if failure == "login_required":
            assert error["code"] == "authentication_required" and error["needLogin"]
            assert error["nextAction"] == {"tool": "authenticate", "arguments": {
                "service": "jira", "interactive": True}}
        else:
            assert error["code"] == "upstream_unavailable" and not error["needLogin"]
            assert error["retryable"] and "nextAction" not in error
    assert probes == ([] if failure == "network" else [{"force": True, "wait": True}])


@pytest.mark.parametrize("exception", [SessionExpired("fixture expired session"), UpstreamUnavailable("fixture offline")])
def test_ui_media_lookup_keeps_best_effort_failure_behavior(build_env, monkeypatch, exception):
    env = build_env()
    def failed(*_args, **_kwargs):
        raise exception
    monkeypatch.setattr(env.provider, "get_bytes", failed)
    assert env.client.fetch_media("/secure/attachment/123/fixture.txt") == (None, None)


@pytest.mark.parametrize("recovered", [True, False])
def test_attachment_list_provider_expiry_never_caches_empty_membership(build_env, monkeypatch, recovered):
    env = build_env()
    key, attachment_id = _uploaded_auth_attachment(env)
    probes = prod(env, monkeypatch, [{"ok": True}] if recovered else [{"needLogin": True}])
    monkeypatch.setattr(env.client, "needs_login", lambda: False)
    original = env.provider.get_json
    attempts = []
    def expire_once(path, *args, **kwargs):
        if path == f"/rest/api/2/issue/{key}":
            attempts.append(path)
            if len(attempts) == 1:
                raise SessionExpired("fixture attachment membership rejected with 401")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", expire_once)
    result = invoke(env, "get_attachments", {"key": key})
    assert probes == [{"force": True, "wait": True}]
    if not recovered:
        assert result.is_error and "data" not in result.structured_content
        assert result.structured_content["error"]["nextAction"]["tool"] == "authenticate"
        assert env.cache.get(f"attachments:prod:{key}") is None
        assert len(attempts) == 1
        result = invoke(env, "get_attachments", {"key": key})
    assert not result.is_error, result.structured_content
    rows = result.structured_content["data"]["attachments"]
    assert [row["id"] for row in rows] == [attachment_id]
    assert rows[0]["filename"] == "recovery.txt"
    assert len(attempts) == 2
    assert env.cache.get(f"attachments:prod:{key}") == rows
