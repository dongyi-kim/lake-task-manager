"""Real SDK/ASGI transport fixtures; no app singleton, LLM, external Jira or injected delay."""

from types import SimpleNamespace
from time import perf_counter

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jira820 import make_app
from jira820.config import Config

from app.auth.inprocess import InProcessProvider
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.local_mcp.runtime import LocalMcpRuntime
from app.mock.permissions import PermissionStore, install_permission_route
from app.routes.ticket_commands import build_ticket_command_router
from app.routes.tickets import build_ticket_router
from app.routes.resources import build_resource_router
from app.services.ticket_commands import CommandError


@pytest.fixture
def build_env(tmp_path, request):
    opened = []
    build_seconds = []
    def build(port=4457, *, include_research_skills=False):
        started = perf_counter()
        config = Config(project_key="DL", current_user="u01", latency_ms=0)
        store = PermissionStore(config)
        store.permissions["MODIFY_REPORTER"] = False
        jira = make_app(store=store)
        install_permission_route(jira)
        transport = TestClient(jira)
        opened.append(transport)
        provider = object.__new__(InProcessProvider)
        provider._client, provider._serial = transport, lambda fn, priority=None: fn()
        settings = SimpleNamespace(**vars(get_settings()))
        settings.jira_env, settings.project_key, settings.app_port = "mock", "DL", port
        settings.search_jira_projects = ["DL"]
        settings.sp_field_id, settings.epic_link_field_id = config.sp_field, config.epic_link_field
        settings.epic_name_field_id = config.epic_name_field
        cache = Cache(":memory:")
        opened.append(cache)
        client = JiraClient(settings, cache)
        client._provider, client._provider_built = provider, True
        calls = []
        original_get, original_write = provider.get_json, provider._write
        def get(path, *args, **kwargs):
            calls.append(("get", path, kwargs))
            return original_get(path, *args, **kwargs)
        def write(method, path, body, params, **kwargs):
            calls.append((method, path, body))
            return original_write(method, path, body, params, **kwargs)
        provider.get_json, provider._write = get, write
        def require_edit(key):
            if store.get_issue(key).get("reporter") != "u01":
                raise CommandError("이 티켓은 편집할 수 없습니다.", 403)
        app = FastAPI()
        candidate = {"include_research_skills": True} if include_research_skills else {}
        runtime = LocalMcpRuntime(get_client=lambda: client, settings=settings,
                                  secret_path=tmp_path / f"credentials-{port}.json", require_edit=require_edit,
                                  **candidate)
        app.include_router(build_ticket_router(get_client=lambda: client))
        app.include_router(build_resource_router(get_client=lambda: client, cache=cache, settings=settings))
        app.include_router(build_ticket_command_router(
            get_client=lambda: client, settings=settings, may_edit=lambda key: True,
            require_edit=require_edit, session_user=lambda: {"id": "u01"},
        ))
        runtime.install(app)
        build_seconds.append(perf_counter() - started)
        return SimpleNamespace(app=app, runtime=runtime, client=client, provider=provider,
                               store=store, calls=calls, cache=cache)
    yield build
    # The factory runs during test calls, so pytest's setup duration cannot isolate its cost.
    # Record it in JUnit without sharing mutable Jira, auth, cache or receipt state.
    request.node.user_properties.extend([
        ("mcp_environment_build_count", len(build_seconds)),
        ("mcp_environment_build_seconds", f"{sum(build_seconds):.6f}"),
    ])
    for resource in reversed(opened):
        resource.close()
