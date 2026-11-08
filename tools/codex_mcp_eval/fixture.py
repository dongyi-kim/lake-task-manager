"""Private, read-only LTM HTTP fixture for an external Agent quality evaluation.

No app singleton, Agent workflow, SSO provider or LLM is initialized. The existing
mock World, Jira820 transport, JiraClient and LocalMcpRuntime are the real read
path. A fresh instance/cache is created for every case and never shares app data.
"""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import threading
import tempfile
import time
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import anyio
import uvicorn
from fastapi import FastAPI
from starlette.routing import Route

from app.auth.base import PermissionDenied, SessionExpired
from app.auth.inprocess import InProcessProvider
from app.infra.cache import Cache
from app.infra.settings import get_settings, is_manager, load_people, load_plan
from app.jira.jira_client import JiraClient
from app.local_mcp.llm_tools import LLM_TOOLS
from app.local_mcp.runtime import LocalMcpRuntime
from app.local_mcp.security import LocalGuard
from app.mock.world import World, get_world
from app.routes.tickets import build_ticket_router
from app.services.ticket_commands import CommandError
from tools.agent_eval_isolation import _canonical


_ACTIVE_CASE = threading.Lock()


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(_canonical(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class _ReadOnlyRuntime(LocalMcpRuntime):
    def register(self, fn, *, read_only, output_type=None):
        # Live connection settings and model-listing requests are outside this private mock,
        # even when read-only. Absent handlers reject both catalog and direct-call access.
        if read_only and fn.__name__ not in LLM_TOOLS | {"get_workspace_config"}:
            super().register(fn, read_only=True, output_type=output_type)


class EvaluationFixture:
    """One case's authenticated HTTP server and immutable-world evidence.

    ``port=4457`` is the manual runner contract; a busy port fails instead of choosing
    another. Tests may explicitly request ``port=0``. ``fault='partial'`` makes the
    known DL-9095 child unavailable through the same path as a permission failure.
    """
    def __init__(self, run_dir: str | Path, port: int = 4457, *, fault: str | None = None,
                 today: str | None = None):
        self.run_dir = Path(run_dir).resolve()
        if (not self.run_dir.is_relative_to(Path(tempfile.gettempdir()).resolve())
                and not any(part in {".cache", ".tmp", ".test"} for part in self.run_dir.parts)):
            raise ValueError("Evaluation fixture files must stay under .cache, .tmp or .test")
        if not 0 <= port <= 65535 or fault not in (None, "partial"):
            raise ValueError("Invalid fixture port or fault")
        self.port, self.fault = port, fault
        self.today = date.fromisoformat(today) if today else date.today()
        self.tool_calls: list[dict[str, Any]] = []
        self.upstream_calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._thread = self._server = self._listener = None
        self._owns_case = False
        self._before: dict[str, Any] | None = None
        self._after: dict[str, Any] | None = None
        self._token = ""
        self._secret_owned = False
        self._patches = ExitStack()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def token(self) -> str:
        return self._token

    def _safe(self, value):
        value = _canonical(value)
        if isinstance(value, dict):
            return {key: "[redacted]" if key.casefold() in {"authorization", "cookie", "token", "secret"}
                    else self._safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._safe(item) for item in value]
        return value.replace(self._token, "[redacted]") if isinstance(value, str) and self._token else value

    def configure_scope(self, settings):
        if not settings.search_jira_projects or not settings.search_confluence_spaces:
            raise ValueError("Configured Jira projects and Confluence spaces are required")

    def runtime_options(self):
        return {}

    def extra_http_apps(self):
        return []

    def __enter__(self):
        if not _ACTIVE_CASE.acquire(blocking=False):
            raise RuntimeError("Evaluation cases must run sequentially in this process")
        self._owns_case = True
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            # Exclusive case directories make accidental reuse of a prior cache impossible.
            self.cache_path = self.run_dir / "jira-cache.sqlite3"
            if self.cache_path.exists() or (self.run_dir / "mcp-secret.json").exists():
                raise FileExistsError("Evaluation case cache already exists; use a new attempt directory")
            self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self._listener.bind(("127.0.0.1", self.port))
            self.port = self._listener.getsockname()[1]

            source_settings = get_settings()
            # Snapshot public configuration atomically; this read-only fixture has no settings
            # mutators and must neither pickle nor share the live singleton's synchronization.
            with source_settings._cache_policy_lock:
                settings = SimpleNamespace(**copy.deepcopy({
                    name: value for name, value in vars(source_settings).items()
                    if not name.startswith("_")}))
            settings.jira_env, settings.app_port = "mock", self.port
            settings.description_format = settings.comment_format = "wiki"
            # Even generated links describe only the disposable fixture, never production.
            settings.jira_base = settings.confluence_base = "http://127.0.0.1:8080"
            settings.jira_user = settings.jira_token = ""
            self.configure_scope(settings)
            self.settings = settings
            get_world.cache_clear()
            self.world = World(load_plan(), load_people(), self.today)
            from app.mock import world, fakebridge
            self._patches.enter_context(patch.object(world, "get_world", lambda: self.world))
            self._patches.enter_context(patch.object(fakebridge, "get_world", lambda: self.world))
            self.cache = Cache(str(self.cache_path))
            self.client = JiraClient(settings, self.cache)
            from app.auth import inprocess
            self._patches.enter_context(patch.object(inprocess, "_LAT_MS", 0))
            self._patches.enter_context(patch.object(inprocess, "_LAT_DESC_MS", 0))
            self._patches.enter_context(patch.dict("os.environ", {"FAKE_LATENCY_MS": "0"}))
            # Construct the known in-process provider directly; no environment can switch it to SSO.
            self.provider = InProcessProvider()
            self.client._provider, self.client._provider_built = self.provider, True
            self.store = self.provider._client.app.state.store
            if self.fault == "partial":
                self.store.denied_issue_keys = {"DL-9095"}
            self.cache.always_revalidate = ()
            self.cache.revalidator = None
            self.client._refresh_bg = lambda *_args, **_kwargs: None
            self.client._warm_jql_leaves_bg = lambda *_args, **_kwargs: None
            self._observe_provider()

            def require_manager():
                if not is_manager(settings, self.client.current_user()):
                    raise CommandError("매니저 권한이 필요합니다.", 403)
            self.runtime = _ReadOnlyRuntime(
                get_client=lambda: self.client, settings=settings,
                secret_path=self.run_dir / "mcp-secret.json", require_manager=require_manager,
                require_module_access=lambda _module: require_manager(), scoped_people=load_people,
                **self.runtime_options())
            self._token = self.runtime.credentials.token()
            self._secret_owned = True
            self._observe_tools()
            self.tool_catalog = [tool.model_dump(mode="json", by_alias=True)
                                 for tool in anyio.run(self.runtime.server.list_tools)]
            self.catalog_sha256 = _hash(self.tool_catalog)
            self._before = self.fingerprint()

            extra_apps = self.extra_http_apps()
            @asynccontextmanager
            async def lifespan(_app):
                async with AsyncExitStack() as stack:
                    for http_app in [self.runtime.http_app, *[app for _, app in extra_apps]]:
                        await stack.enter_async_context(http_app.router.lifespan_context(http_app))
                    self.runtime.started = True
                    try:
                        yield
                    finally:
                        self.runtime.started = False
            self.app = FastAPI(lifespan=lifespan)
            # Real UI GET route for cache-sharing checks; omit every mutation route.
            reads = build_ticket_router(get_client=lambda: self.client)
            reads.routes[:] = [route for route in reads.routes if "GET" in (getattr(route, "methods", None) or ())]
            self.app.include_router(reads)
            self.app.router.routes.append(Route("/mcp", LocalGuard(
                self.runtime.http_app, credentials=self.runtime.credentials, port=self.port),
                methods=["GET", "POST", "DELETE", "OPTIONS"]))
            for path, http_app in extra_apps:
                self.app.router.routes.append(Route(path, LocalGuard(http_app,
                    credentials=self.runtime.credentials, port=self.port),
                    methods=["GET", "POST", "DELETE", "OPTIONS"]))
            self._server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", access_log=False,
                                                       proxy_headers=False, timeout_graceful_shutdown=5))
            self._thread = threading.Thread(target=lambda: self._server.run(sockets=[self._listener]),
                                            name="ltm-evaluation-http", daemon=True)
            self._thread.start()
            deadline = time.monotonic() + 10
            while not self._server.started and self._thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not self._server.started:
                raise RuntimeError("Evaluation HTTP listener did not become ready")
            return self
        except BaseException:
            self.close()
            raise

    def _observe_provider(self):
        original = self.provider._get
        def observed(path, params, priority=None):
            started = time.perf_counter()
            record = {"method": "GET", "path": str(path), "params": self._safe(params or {}), "responseBytes": None}
            try:
                result = original(path, params, priority)
                record["status"] = result.status_code
                record["responseBytes"] = len(result.content)
                return result
            except Exception as exc:
                # The shared mock provider conflates 403 with session expiry. Keep
                # the actual HTTP request, but classify this explicit fixture denial
                # like the production permission error, never as lost authentication.
                prefix = "/rest/api/2/issue/"
                denied = (str(path).startswith(prefix) and
                          str(path)[len(prefix):].split("/", 1)[0] in self.store.denied_issue_keys)
                if isinstance(exc, SessionExpired) and denied:
                    record.update(status=403, errorType="PermissionDenied")
                    raise PermissionDenied("The evaluation fixture denies access to this issue") from exc
                record["errorType"] = type(exc).__name__
                raise
            finally:
                record["durationSeconds"] = round(time.perf_counter() - started, 6)
                with self._lock:
                    self.upstream_calls.append(record)
        self.provider._get = observed
        def reject_write(*_args, **_kwargs):
            raise PermissionDenied("Read-only evaluation fixture rejects all Jira mutations")
        self.provider._write = self.provider.post_multipart = reject_write

    def _observe_tools(self):
        original = self.runtime.server.call_tool
        async def observed(name, arguments, context=None):
            started = time.perf_counter()
            record = {"name": name, "arguments": self._safe(arguments)}
            try:
                result = await original(name, arguments, context)
                record.update(isError=result.is_error, result=self._safe(result.structured_content),
                              content=self._safe([item.model_dump(mode="json") for item in result.content]))
                return result
            except Exception as exc:
                record.update(isError=True, errorType=type(exc).__name__)
                raise
            finally:
                record["durationSeconds"] = round(time.perf_counter() - started, 6)
                with self._lock:
                    self.tool_calls.append(record)
        self.runtime.server.call_tool = observed

    def assert_conformance(self) -> dict[str, Any]:
        """Preflight the real mock REST boundary without touching JiraClient caches.

        Host-only oracle checks are never model tools. Both visibility modes are
        checked before evaluation, then the configured fault is restored. Direct
        TestClient requests intentionally leave measured provider/tool traces empty.
        """
        parent = "DL-9090"
        expected = sorted(key for key, row in self.store.issues.items()
                          if row.get("parentKey") == parent)
        assert len(expected) == 3, "Pinned parent fixture must have exactly three children"
        original_denied = self.store.denied_issue_keys
        checks = {}
        try:
            for label, denied in (("normal", set()), ("denied", {expected[-1]})):
                self.store.denied_issue_keys = denied
                wanted = [key for key in expected if key not in denied]
                pages = [self.provider._client.get("/rest/api/2/search", params={
                    "jql": f'parent = "{parent}" ORDER BY key ASC',
                    "startAt": offset, "maxResults": 1}).json() for offset in range(len(wanted) + 1)]
                actual = [row["key"] for page in pages for row in page["issues"]]
                assert actual == wanted, f"Mock parent search mismatch: {label}"
                assert all(page["total"] == len(wanted) for page in pages)
                parent_result = self.provider._client.get(f"/rest/api/2/issue/{parent}").json()
                assert sorted(row["key"] for row in parent_result["fields"]["subtasks"]) == expected
                if denied:
                    response = self.provider._client.get(f"/rest/api/2/issue/{expected[-1]}")
                    assert response.status_code == 403
                checks[label] = {"total": len(wanted), "keys": actual}
        finally:
            self.store.denied_issue_keys = original_denied
        return {"parentJql": checks, "pagination": "complete", "directDenial": 403,
                "cacheWarmed": False}

    def fingerprint(self) -> dict[str, Any]:
        return {"worldSha256": _hash({"today": self.world.today, "counter": self.world._counter,
                                      "issues": self.world.issues, "confluence": self.world.confluence,
                                      "attachments": self.world.attachments}),
                "providerStoreSha256": _hash({"issues": self.store.issues, "confluence": self.store.confluence,
                                              "attachments": self.store.attachments, "activity": self.store.activity}),
                "providerIssueCount": len(self.store.issues)}

    def ground_truth(self) -> dict[str, Any]:
        """Host-side deterministic checker input; never expose this to the evaluated model."""
        projects = {project.upper() for project in self.settings.search_jira_projects}
        def unfinished(user):
            return sorted(key for key, row in self.store.issues.items()
                          if key.rsplit("-", 1)[0].upper() in projects
                          and row.get("assignee") == user and row.get("statusCategory") != "done")
        return {"personId": "skcc.i2011", "personOpenKeys": unfinished("skcc.i2011"),
                "currentUserId": World.ME, "currentUserOpenKeys": unfinished(World.ME),
                "issues": copy.deepcopy(self.store.issues), "users": copy.deepcopy(self.store.users),
                "confluence": copy.deepcopy(self.store.confluence), "today": self.world.today.isoformat()}

    def summary(self) -> dict[str, Any]:
        after = self._after or self.fingerprint()
        before = self._before or after
        return {"worldSha256Before": before["worldSha256"], "worldSha256After": after["worldSha256"],
                "providerStoreSha256Before": before["providerStoreSha256"],
                "providerStoreSha256After": after["providerStoreSha256"],
                "worldUnchanged": before["worldSha256"] == after["worldSha256"],
                "providerStoreUnchanged": before["providerStoreSha256"] == after["providerStoreSha256"],
                "providerIssueCount": after["providerIssueCount"], "toolCatalogSha256": self.catalog_sha256,
                "toolCalls": len(self.tool_calls), "toolErrors": sum(row.get("isError", False) for row in self.tool_calls),
                "upstreamRequests": len(self.upstream_calls),
                "upstreamDurationSeconds": round(sum(row["durationSeconds"] for row in self.upstream_calls), 6),
                "cachePolicy": "fresh-client-and-private-cache-each-case", "backgroundRevalidation": False,
                "fault": self.fault, "latencyMs": 0, "worldDate": self.today.isoformat()}

    def close(self):
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)
        if self._listener:
            self._listener.close()
        if hasattr(self, "store"):
            self._after = self.fingerprint()
        if hasattr(self, "client"):
            self.client.close()
        if hasattr(self, "cache"):
            self.cache.close()
        if self._secret_owned:
            (self.run_dir / "mcp-secret.json").unlink(missing_ok=True)
            self._secret_owned = False
        self._patches.close()
        get_world.cache_clear()
        if self._owns_case:
            self._owns_case = False
            _ACTIVE_CASE.release()
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Evaluation HTTP listener did not stop")

    def __exit__(self, *_exc):
        self.close()
