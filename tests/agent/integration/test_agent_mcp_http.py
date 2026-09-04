"""Security contract for the MAXIS/Dify-facing HTTP MCP resource server."""

from __future__ import annotations

import time

import anyio
import httpx
import httpx2
import pytest

pytest.importorskip("mcp", reason="mcp SDK 미설치")

from app.agent.mcp_http_server import (  # noqa: E402
    HttpMcpSettings,
    OAuthIntrospectionTokenVerifier,
    build_http_app,
)
from app.agent.mcp_server import assert_http_subject_bound  # noqa: E402


def _access_token(token: str, identity: str):
    from mcp.server.auth.provider import AccessToken

    return AccessToken(
        token=token,
        client_id="maxis-dify",
        scopes=["ltm:read"],
        expires_at=int(time.time()) + 60,
        resource="https://ltm-mcp.example.test/mcp",
        subject=f"subject:{identity}",
        claims={"ltm_user_id": identity},
    )


def _http_app(monkeypatch, *, executed: list[dict] | None = None):
    """Build the real MCP ASGI stack with deterministic IdP and Jira edges."""
    from app.agent import tools as exported_tools
    from app.agent.tools import _ctx

    async def verify(_self, token: str):
        identities = {
            "matching-token": "test.ui01",
            "mismatched-token": "different.user",
        }
        identity = identities.get(token)
        return _access_token(token, identity) if identity else None

    class Jira:
        def current_user_fresh(self):
            return {
                "id": "test.ui01",
                "displayName": "Test User",
                "actorAliases": ["test.ui01"],
            }

    monkeypatch.setattr(OAuthIntrospectionTokenVerifier, "verify_token", verify)
    monkeypatch.setattr(_ctx, "client", lambda: Jira())
    if executed is not None:
        tool = exported_tools.BY_NAME["search_rules"]

        def invoke(question: str, k: int = 4):
            executed.append({"question": question, "k": k})
            return {"matches": [{"title": "test-only-policy"}]}

        monkeypatch.setattr(tool, "func", invoke)
    return build_http_app(HttpMcpSettings.from_env(_base_env()))


def _post_asgi(app, *, token: str | None = None, host: str = "ltm-mcp.example.test"):
    """Make one ordinary HTTP request through the concrete ASGI middleware stack."""
    headers = {
        "Host": host,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    async def request():
        transport = httpx2.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=transport,
                base_url="https://ltm-mcp.example.test",
                headers=headers,
            ) as client:
                return await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {"name": "ltm-test", "version": "1"},
                        },
                    },
                )

    return anyio.run(request)


def _base_env() -> dict[str, str]:
    return {
        "LTM_MCP_DEPLOYMENT_MODE": "production",
        "JIRA_ENV": "prod",
        "LTM_MCP_RESOURCE_URL": "https://ltm-mcp.example.test/mcp",
        "LTM_MCP_OAUTH_ISSUER_URL": "https://id.example.test",
        "LTM_MCP_OAUTH_INTROSPECTION_URL": "https://id.example.test/introspect",
        "LTM_MCP_OAUTH_CLIENT_ID": "ltm-mcp",
        "LTM_MCP_OAUTH_CLIENT_SECRET": "test-only",
        "LTM_MCP_BEHIND_MTLS_PROXY": "1",
    }


def test_production_settings_require_mtls_or_declared_terminating_proxy():
    env = _base_env()
    env.pop("LTM_MCP_BEHIND_MTLS_PROXY")
    with pytest.raises(RuntimeError, match="mTLS"):
        HttpMcpSettings.from_env(env)


@pytest.mark.parametrize("missing", ["LTM_MCP_DEPLOYMENT_MODE", "JIRA_ENV"])
def test_http_startup_requires_explicit_deployment_and_jira_modes(missing):
    env = _base_env()
    env.pop(missing)

    with pytest.raises(RuntimeError, match=missing):
        HttpMcpSettings.from_env(env)


@pytest.mark.parametrize(
    "deployment_mode,jira_env,expected",
    [
        ("production", "mock", "JIRA_ENV=prod"),
        ("production", "local", "JIRA_ENV=prod"),
        ("development", "prod", "JIRA_ENV=mock or local"),
        ("staging", "prod", "development or production"),
    ],
)
def test_http_startup_rejects_ambiguous_or_crossed_runtime_modes(
    deployment_mode, jira_env, expected,
):
    env = _base_env()
    env["LTM_MCP_DEPLOYMENT_MODE"] = deployment_mode
    env["JIRA_ENV"] = jira_env

    with pytest.raises(RuntimeError, match=expected):
        HttpMcpSettings.from_env(env)


def test_production_cannot_enable_the_loopback_http_escape_hatch():
    env = _base_env()
    env["LTM_MCP_ALLOW_HTTP_LOOPBACK"] = "1"

    with pytest.raises(RuntimeError, match="production MCP cannot enable"):
        HttpMcpSettings.from_env(env)


def test_plain_http_is_only_allowed_for_explicit_loopback_development():
    env = _base_env()
    env["LTM_MCP_RESOURCE_URL"] = "http://ltm.internal/mcp"
    with pytest.raises(RuntimeError, match="HTTPS"):
        HttpMcpSettings.from_env(env)

    env.update(
        LTM_MCP_DEPLOYMENT_MODE="development",
        JIRA_ENV="mock",
        LTM_MCP_RESOURCE_URL="http://127.0.0.1:4460/mcp",
        LTM_MCP_OAUTH_ISSUER_URL="http://127.0.0.1:9000",
        LTM_MCP_OAUTH_INTROSPECTION_URL="http://127.0.0.1:9000/introspect",
        LTM_MCP_ALLOW_HTTP_LOOPBACK="1",
    )
    settings = HttpMcpSettings.from_env(env)
    assert settings.resource_url.startswith("http://127.0.0.1")


def test_development_mock_http_server_cannot_bind_or_advertise_non_loopback():
    env = _base_env()
    env.update(
        LTM_MCP_DEPLOYMENT_MODE="development",
        JIRA_ENV="mock",
        LTM_MCP_RESOURCE_URL="https://ltm-dev.example.test/mcp",
    )
    with pytest.raises(RuntimeError, match="remain on loopback"):
        HttpMcpSettings.from_env(env)

    env["LTM_MCP_RESOURCE_URL"] = "https://127.0.0.1:4460/mcp"
    env["LTM_MCP_HOST"] = "0.0.0.0"
    with pytest.raises(RuntimeError, match="remain on loopback"):
        HttpMcpSettings.from_env(env)


@pytest.mark.parametrize(
    "field,value",
    [
        ("LTM_MCP_RESOURCE_URL", "https://user:pass@ltm.example.test/mcp"),
        ("LTM_MCP_OAUTH_ISSUER_URL", "https://id.example.test?secret=value"),
        ("LTM_MCP_OAUTH_INTROSPECTION_URL", "https://id.example.test/introspect#debug"),
        ("LTM_MCP_RESOURCE_URL", "https://ltm.example.test:99999/mcp"),
    ],
)
def test_endpoint_urls_reject_embedded_credentials_query_fragment_and_bad_port(field, value):
    env = _base_env()
    env[field] = value
    with pytest.raises(RuntimeError, match="HTTPS"):
        HttpMcpSettings.from_env(env)


def test_allowed_hosts_reject_wildcards():
    env = _base_env()
    env["LTM_MCP_ALLOWED_HOSTS"] = "*.example.test"
    with pytest.raises(RuntimeError, match="explicit host"):
        HttpMcpSettings.from_env(env)


@pytest.mark.parametrize("host", ["ltm.example.test/path", "user@ltm.example.test", "ltm.example.test:99999"])
def test_allowed_hosts_reject_url_paths_credentials_and_bad_ports(host):
    env = _base_env()
    env["LTM_MCP_ALLOWED_HOSTS"] = host
    with pytest.raises(RuntimeError, match="explicit host"):
        HttpMcpSettings.from_env(env)


def test_declared_mtls_proxy_must_keep_unencrypted_backend_on_loopback():
    env = _base_env()
    env["LTM_MCP_HOST"] = "0.0.0.0"
    with pytest.raises(RuntimeError, match="loopback"):
        HttpMcpSettings.from_env(env)


@pytest.mark.parametrize("token", [None, "invalid-token"])
def test_http_app_rejects_missing_and_invalid_bearer_before_mcp_dispatch(monkeypatch, token):
    response = _post_asgi(_http_app(monkeypatch), token=token)

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_http_app_rejects_an_unapproved_host_header(monkeypatch):
    response = _post_asgi(
        _http_app(monkeypatch),
        token="matching-token",
        host="attacker.example.test",
    )

    assert response.status_code == 421


def test_http_streamable_protocol_initializes_lists_and_calls_only_read_tools(monkeypatch):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from app.agent import tools as exported_tools
    from app.agent.mcp_server import _EXPORTED

    executed: list[dict] = []
    app = _http_app(monkeypatch, executed=executed)

    async def round_trip():
        transport = httpx2.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=transport,
                base_url="https://ltm-mcp.example.test",
                headers={"Authorization": "Bearer matching-token"},
            ) as client:
                async with streamable_http_client(
                    "https://ltm-mcp.example.test/mcp",
                    http_client=client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        initialized = await session.initialize()
                        listed = await session.list_tools()
                        called = await session.call_tool(
                            "search_rules", {"question": "Story Point rule", "k": 1},
                        )
                        return initialized, listed.tools, called

    initialized, tools, called = anyio.run(round_trip)
    names = {tool.name for tool in tools}

    assert initialized.server_info.name == "lake-task-manager"
    assert names == set(_EXPORTED)
    assert names.isdisjoint({tool.name for tool in exported_tools.WRITE_TOOLS})
    assert all(tool.annotations.read_only_hint is True for tool in tools)
    assert called.is_error is False
    assert called.structured_content == {"matches": [{"title": "test-only-policy"}]}
    assert executed == [{"question": "Story Point rule", "k": 1}]


def test_http_streamable_identity_mismatch_stops_before_tool_execution(monkeypatch):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    executed: list[dict] = []
    app = _http_app(monkeypatch, executed=executed)

    async def round_trip():
        transport = httpx2.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=transport,
                base_url="https://ltm-mcp.example.test",
                headers={"Authorization": "Bearer mismatched-token"},
            ) as client:
                async with streamable_http_client(
                    "https://ltm-mcp.example.test/mcp",
                    http_client=client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        return await session.call_tool(
                            "search_rules", {"question": "must not execute", "k": 1},
                        )

    called = anyio.run(round_trip)

    assert called.is_error is True
    assert "does not match" in called.content[0].text
    assert executed == []


def test_obo_token_requires_active_subject_audience_scope_and_expiry():
    now = int(time.time())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/introspect"
        return httpx.Response(
            200,
            json={
                "active": True,
                "client_id": "maxis-dify",
                "sub": "employee-opaque-id",
                "scope": "openid ltm:read",
                "aud": ["ltm-mcp"],
                "exp": now + 60,
            },
        )

    verifier = OAuthIntrospectionTokenVerifier(
        introspection_url="https://id.example.test/introspect",
        client_id="resource-server",
        client_secret="test-only",
        audience="ltm-mcp",
        transport=httpx.MockTransport(handler),
    )

    async def verify():
        return await verifier.verify_token("opaque-user-token")

    access = anyio.run(verify)
    assert access is not None
    assert access.subject == "employee-opaque-id"
    assert access.scopes == ["openid", "ltm:read"]
    assert access.claims["ltm_user_id"] == "employee-opaque-id"


def test_http_mcp_subject_must_match_the_jira_session_user(monkeypatch):
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken
    from app.agent.tools import _ctx

    class Jira:
        def current_user_fresh(self):
            return {
                "id": "test.ui01",
                "key": "jira-key-001",
                "accountId": "jira-account-001",
                "actorAliases": ["test.ui01", "legacy-alias-001"],
                "displayName": "Test User",
            }

    monkeypatch.setattr(_ctx, "client", lambda: Jira())

    def bind(identity):
        access = AccessToken(
            token="opaque",
            client_id="dify",
            scopes=["ltm:read"],
            expires_at=int(time.time()) + 60,
            subject="oauth-subject",
            claims={"ltm_user_id": identity},
        )
        return auth_context_var.set(AuthenticatedUser(access))

    for identity in (
        "test.ui01", "jira-key-001", "jira-account-001", "legacy-alias-001",
    ):
        marker = bind(identity)
        try:
            assert_http_subject_bound()
        finally:
            auth_context_var.reset(marker)

    marker = bind("different.user")
    try:
        with pytest.raises(PermissionError, match="does not match"):
            assert_http_subject_bound()
    finally:
        auth_context_var.reset(marker)


@pytest.mark.parametrize("delegated", ["Shared Display", "shared@example.test"])
def test_http_mcp_never_binds_from_display_name_or_email(monkeypatch, delegated):
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken
    from app.agent.tools import _ctx

    class Jira:
        def current_user_fresh(self):
            return {
                "id": "jira-account-001",
                "key": "JIRAKEY001",
                "accountId": "account-id-001",
                "displayName": "Shared Display",
                "name": "Shared Display",
                "emailAddress": "shared@example.test",
                "actorAliases": ["jira-account-001", "JIRAKEY001", "account-id-001"],
            }

    monkeypatch.setattr(_ctx, "client", lambda: Jira())
    access = AccessToken(
        token="opaque",
        client_id="dify",
        scopes=["ltm:read"],
        expires_at=int(time.time()) + 60,
        subject="oauth-subject",
        claims={"ltm_user_id": delegated},
    )
    marker = auth_context_var.set(AuthenticatedUser(access))
    try:
        with pytest.raises(PermissionError, match="does not match"):
            assert_http_subject_bound()
    finally:
        auth_context_var.reset(marker)


def test_http_mcp_never_authorizes_from_cached_prior_session(monkeypatch):
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken
    from app.agent.tools import _ctx

    calls = []

    class Jira:
        def current_user(self):
            return {"id": "prior.user"}

        def current_user_fresh(self):
            calls.append("fresh")
            return {"id": "new.session.user", "actorAliases": ["new.session.user"]}

    monkeypatch.setattr(_ctx, "client", lambda: Jira())
    access = AccessToken(
        token="opaque",
        client_id="dify",
        scopes=["ltm:read"],
        expires_at=int(time.time()) + 60,
        subject="oauth-subject",
        claims={"ltm_user_id": "prior.user"},
    )
    marker = auth_context_var.set(AuthenticatedUser(access))
    try:
        with pytest.raises(PermissionError, match="does not match"):
            assert_http_subject_bound()
    finally:
        auth_context_var.reset(marker)
    assert calls == ["fresh"]


def test_http_mcp_pins_fresh_subject_across_a_to_b_session_transition(monkeypatch):
    from types import SimpleNamespace

    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken
    from app.agent import tools as exported_tools
    from app.agent import mcp_server
    from app.agent.tools import _ctx, pmo_tools

    active = {"id": "prior.manager"}
    searches = []

    class Jira:
        def current_user(self):
            # Simulate the six-hour /myself cache retaining user A after the Jira
            # browser/session has moved to B.
            return {"id": "prior.manager"}

        def current_user_fresh(self):
            return {"id": active["id"], "actorAliases": [active["id"]]}

        def my_task_context(self):
            return {"taskKeys": []}

        def issues_by_keys(self, _keys):
            return []

        def search_issues(self, *_args, **_kwargs):
            searches.append("searched")
            return []

    jira = Jira()
    monkeypatch.setattr(_ctx, "client", lambda: jira)
    monkeypatch.setattr(pmo_tools, "client", lambda: jira)
    monkeypatch.setattr(
        pmo_tools, "settings",
        lambda: SimpleNamespace(managers={"prior.manager"}),
    )

    def bind(identity: str):
        access = AccessToken(
            token="opaque",
            client_id="dify",
            scopes=["ltm:read"],
            expires_at=int(time.time()) + 60,
            subject="oauth-subject",
            claims={"ltm_user_id": identity},
        )
        return auth_context_var.set(AuthenticatedUser(access))

    workload = exported_tools.BY_NAME["get_my_workload"]
    marker = bind("prior.manager")
    try:
        first = mcp_server._invoke_tool(
            workload, {}, require_authenticated_subject=True,
        )
    finally:
        auth_context_var.reset(marker)
    assert first["user"] == "prior.manager"

    active["id"] = "new.member"
    marker = bind("new.member")
    try:
        second = mcp_server._invoke_tool(
            workload, {}, require_authenticated_subject=True,
        )
        denied = mcp_server._invoke_tool(
            workload, {"user_id": "someone.else"},
            require_authenticated_subject=True,
        )
    finally:
        auth_context_var.reset(marker)

    assert second["user"] == "new.member"
    assert denied["denied"] is True
    assert searches == []
    # Request-local binding must not replace or poison the ordinary Jira cache.
    assert pmo_tools._me() == "prior.manager"


def test_introspection_can_map_an_approved_jira_identity_claim():
    now = int(time.time())
    response = {
        "active": True,
        "client_id": "maxis-dify",
        "sub": "opaque-corporate-subject",
        "jira_user": "test.ui01",
        "scope": "ltm:read",
        "aud": "ltm-mcp",
        "exp": now + 60,
    }
    verifier = OAuthIntrospectionTokenVerifier(
        introspection_url="https://id.example.test/introspect",
        client_id="resource-server",
        client_secret="test-only",
        audience="ltm-mcp",
        identity_claim="jira_user",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response)),
    )

    async def verify():
        return await verifier.verify_token("opaque-user-token")

    access = anyio.run(verify)
    assert access is not None
    assert access.subject == "opaque-corporate-subject"
    assert access.claims["ltm_user_id"] == "test.ui01"


def test_custom_identity_claim_carrying_a_display_name_cannot_bind(monkeypatch):
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from app.agent.tools import _ctx

    response = {
        "active": True,
        "client_id": "maxis-dify",
        "sub": "opaque-corporate-subject",
        "employee_label": "Shared Display",
        "scope": "ltm:read",
        "aud": "ltm-mcp",
        "exp": int(time.time()) + 60,
    }
    verifier = OAuthIntrospectionTokenVerifier(
        introspection_url="https://id.example.test/introspect",
        client_id="resource-server",
        client_secret="test-only",
        audience="ltm-mcp",
        identity_claim="employee_label",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response)),
    )

    async def verify():
        return await verifier.verify_token("opaque-user-token")

    access = anyio.run(verify)
    assert access is not None
    assert access.claims["ltm_user_id"] == "Shared Display"

    class Jira:
        def current_user_fresh(self):
            return {
                "id": "jira-account-001",
                "displayName": "Shared Display",
                "actorAliases": ["jira-account-001"],
            }

    monkeypatch.setattr(_ctx, "client", lambda: Jira())
    marker = auth_context_var.set(AuthenticatedUser(access))
    try:
        with pytest.raises(PermissionError, match="does not match"):
            assert_http_subject_bound()
    finally:
        auth_context_var.reset(marker)


@pytest.mark.parametrize("claim", ["displayName", "email", "mail", "name"])
def test_introspection_rejects_human_facing_identity_claim_configuration(claim):
    with pytest.raises(ValueError, match="stable Jira principal"):
        OAuthIntrospectionTokenVerifier(
            introspection_url="https://id.example.test/introspect",
            client_id="resource-server",
            client_secret="test-only",
            audience="ltm-mcp",
            identity_claim=claim,
        )

    env = _base_env()
    env["LTM_MCP_OAUTH_IDENTITY_CLAIM"] = claim
    with pytest.raises(RuntimeError, match="stable Jira principal"):
        HttpMcpSettings.from_env(env)


@pytest.mark.parametrize(
    "response",
    [
        {"active": False},
        {"active": True, "sub": "u", "scope": "ltm:read", "aud": "wrong", "exp": 4_102_444_800},
        {"active": True, "sub": "u", "scope": "openid", "aud": "ltm-mcp", "exp": 4_102_444_800},
        {"active": True, "scope": "ltm:read", "aud": "ltm-mcp", "exp": 4_102_444_800},
        {"active": True, "sub": "u", "scope": "ltm:read", "aud": "ltm-mcp", "exp": 1},
    ],
)
def test_invalid_obo_claims_are_denied(response):
    verifier = OAuthIntrospectionTokenVerifier(
        introspection_url="https://id.example.test/introspect",
        client_id="resource-server",
        client_secret="test-only",
        audience="ltm-mcp",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response)),
    )

    async def verify():
        return await verifier.verify_token("opaque-user-token")

    assert anyio.run(verify) is None
