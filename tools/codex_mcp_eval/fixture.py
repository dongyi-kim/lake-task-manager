"""Private, read-only LTM HTTP fixture for an external Agent quality evaluation.

No app singleton, Agent workflow, SSO provider or LLM is initialized. The existing
mock World, Jira820 transport, JiraClient and LocalMcpRuntime are the real read
path. A fresh instance/cache is created for every case and never shares app data.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import socket
import threading
import tempfile
import time
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from unittest.mock import patch

import anyio
import uvicorn
from fastapi import FastAPI
from starlette.requests import Request
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
                 today: str | None = None, files_root: str | Path | None = None,
                 files_after_write=None):
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
        self.protocol_calls: list[dict[str, Any]] = []
        self.transport_calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._observer_condition = threading.Condition(self._lock)
        self._active_observers = {"provider": 0, "transport": 0, "tool": 0, "file": 0, "protocol": 0}
        self._observer_last_activity = time.monotonic()
        self._transport_scope = ContextVar("evaluation_transport_scope", default=False)
        self._thread = self._server = self._listener = None
        self._owns_case = False
        self._before: dict[str, Any] | None = None
        self._after: dict[str, Any] | None = None
        self._token = ""
        self._secret_owned = False
        self._patches = ExitStack()
        self.files = None
        if files_root is not None:
            from tools.codex_mcp_eval.research_files import ResearchFiles
            self.files = ResearchFiles(Path(files_root), after_write=files_after_write)
            original_file_call = self.files.server.call_tool
            async def observed_file_call(name, arguments, context=None):
                self._begin_observer("file")
                try:
                    return await original_file_call(name, arguments, context)
                finally:
                    self._finish_observer("file")
            self.files.server.call_tool = observed_file_call
            self.files_http = self.files.server.streamable_http_app(streamable_http_path="/research-files",
                stateless_http=True, json_response=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def token(self) -> str:
        return self._token

    @property
    def files_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}/research-files" if self.files is not None else None

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
        return [("/research-files", self.files_http)] if self.files is not None else []

    def _begin_observer(self, kind, collection=None, record=None):
        with self._observer_condition:
            self._active_observers[kind] += 1
            self._observer_last_activity = time.monotonic()
            if collection is not None:
                record.update(complete=False, sequence=len(collection))
                collection.append(record)
            self._observer_condition.notify_all()

    def _finish_observer(self, kind, record=None, **updates):
        with self._observer_condition:
            if record is not None:
                record.update(updates, complete=True)
            self._active_observers[kind] -= 1
            self._observer_last_activity = time.monotonic()
            self._observer_condition.notify_all()

    def settle_observers(self, timeout_seconds=5.0):
        """Wait for admitted provider/handler/wire work and a short quiet interval."""
        started = time.monotonic()
        deadline, quiet_seconds = started + max(0, timeout_seconds), 0.02
        with self._observer_condition:
            while True:
                active = dict(self._active_observers)
                now = time.monotonic()
                quiet_remaining = quiet_seconds - (now - self._observer_last_activity)
                if not any(active.values()) and quiet_remaining <= 0:
                    return {"complete": True, "timedOut": False, "active": active,
                            "waitSeconds": round(now - started, 6)}
                remaining = deadline - now
                if remaining <= 0:
                    return {"complete": False, "timedOut": True, "active": active,
                            "waitSeconds": round(now - started, 6)}
                self._observer_condition.wait(min(remaining, max(0.001, quiet_remaining))
                                              if not any(active.values()) else remaining)

    def _observe_protocol(self, app, server):
        """Observe guarded HTTP JSON-RPC attempts, never persist their arguments.

        Separate wire dispatches from handler invocations: SDK validation can reject
        a tools/call before a tool implementation runs. This observer handles the
        fixture's JSON response transport; no SSE payload inference is attempted.
        """
        async def observed(scope, receive, send):
            if scope.get("type") != "http" or scope.get("method") != "POST":
                return await app(scope, receive, send)
            started = time.perf_counter()
            request_body, response_body = b"", bytearray()
            replayed = False
            record = {"server": server, "method": None, "id": None,
                      "httpStatus": None, "isError": None}
            self._begin_observer("protocol", self.protocol_calls, record)
            completion = {}
            limit = 4 * 1024 * 1024
            async def receiving():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": request_body, "more_body": False}
                return await receive()
            async def sending(message):
                if message["type"] == "http.response.start":
                    record["httpStatus"] = message["status"]
                elif message["type"] == "http.response.body":
                    response_body.extend(message.get("body", b"")[:max(0, limit + 1 - len(response_body))])
                await send(message)
            try:
                # Read before dispatch so capacity/restart rejections, which need
                # not consume the body, still retain their JSON-RPC correlation.
                request_body = await Request(scope, receive).body()
                if len(request_body) <= limit:
                    try:
                        request = json.loads(request_body)
                        if isinstance(request, dict):
                            method, request_id = request.get("method"), request.get("id")
                            record["method"] = self._safe(method) if isinstance(method, str) else None
                            record["id"] = self._safe(request_id) if type(request_id) in (int, str) else None
                    except (ValueError, UnicodeError):
                        pass
                await app(scope, receiving, sending)
                if record["httpStatus"] is not None and record["httpStatus"] >= 400:
                    record["isError"] = True
                elif not response_body:
                    record["isError"] = False
                elif len(response_body) <= limit:
                    try:
                        response = json.loads(response_body)
                        if isinstance(response, dict):
                            result = response.get("result")
                            record["isError"] = bool(response.get("error") or
                                isinstance(result, dict) and result.get("isError"))
                    except (ValueError, UnicodeError):
                        pass
            except BaseException as exc:
                completion.update(isError=True, errorType=type(exc).__name__)
                raise
            finally:
                completion["durationSeconds"] = round(time.perf_counter() - started, 6)
                self._finish_observer("protocol", record, **completion)
        return observed

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
            original_factory = self.runtime.transport_host._worker.factory
            def observed_factory():
                applications = original_factory()
                self._observe_tools()  # Fresh SDK instances must keep the same evidence observers.
                return applications
            self.runtime.transport_host._worker.factory = observed_factory
            self.tool_catalog = [tool.model_dump(mode="json", by_alias=True)
                                 for tool in anyio.run(self.runtime.server.list_tools)]
            self.catalog_sha256 = _hash(self.tool_catalog)
            self._before = self.fingerprint()

            extra_apps = self.extra_http_apps()
            @asynccontextmanager
            async def lifespan(_app):
                async with self.runtime.transport_host.lifespan(), AsyncExitStack() as stack:
                    for _, http_app in extra_apps:
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
            def current_mcp():
                return self.runtime.http_app
            mcp_http = self.runtime.transport_host.wrap(current_mcp, dynamic=True)
            self.app.router.routes.append(Route("/mcp", LocalGuard(self._observe_protocol(mcp_http, "ltm"),
                credentials=self.runtime.credentials, port=self.port),
                methods=["GET", "POST", "DELETE", "OPTIONS"]))
            for path, http_app in extra_apps:
                self.app.router.routes.append(Route(path, LocalGuard(self._observe_protocol(http_app, "research_files"),
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
        original_request = self.provider._client.request
        def dispatched(method, url, *args, **kwargs):
            # Fixture seeding/conformance uses raw TestClient access outside the
            # provider scope. Actual provider dispatches include direct identity
            # probes and repeated requests inside get/web_form implementations.
            if not self._transport_scope.get():
                return original_request(method, url, *args, **kwargs)
            started = time.perf_counter()
            route = urlsplit(str(url)).path
            service = ("jira" if re.search(r"/rest/(?:api/(?:2|latest)|agile/[^/]+|auth/[^/]+)/", route)
                       else "confluence" if re.search(r"/rest/api/(?:content|search|space|user)(?:/|$)", route)
                       else None)
            record = {"method": str(method).upper(), "service": service, "path": route,
                      "params": self._safe(kwargs.get("params") or {}), "responseBytes": None,
                      "durationSeconds": None, "dispatchAttempt": True, "serverReceived": None,
                      "status": None}
            self._begin_observer("transport", self.transport_calls, record)
            if service is not None:
                with self._lock:
                    self.upstream_calls.append(record)
            completion = {}
            try:
                result = original_request(method, url, *args, **kwargs)
                completion.update(status=result.status_code, responseBytes=len(result.content), serverReceived=True)
                return result
            except BaseException as exc:
                completion["errorType"] = type(exc).__name__
                raise
            finally:
                completion["durationSeconds"] = round(time.perf_counter() - started, 6)
                self._finish_observer("transport", record, **completion)
        self.provider._client.request = dispatched
        original = self.provider._get
        def observed(path, params, priority=None):
            self._begin_observer("provider")
            token = self._transport_scope.set(True)
            try:
                return original(path, params, priority)
            except Exception as exc:
                # The shared mock provider conflates 403 with session expiry.
                prefix = "/rest/api/2/issue/"
                denied = (str(path).startswith(prefix) and
                          str(path)[len(prefix):].split("/", 1)[0] in self.store.denied_issue_keys)
                if isinstance(exc, SessionExpired) and denied:
                    raise PermissionDenied("The evaluation fixture denies access to this issue") from exc
                raise
            finally:
                self._transport_scope.reset(token)
                self._finish_observer("provider")
        self.provider._get = observed
        original_web_form = self.provider.web_form
        def observed_web_form(*args, **kwargs):
            self._begin_observer("provider")
            token = self._transport_scope.set(True)
            try:
                return original_web_form(*args, **kwargs)
            finally:
                self._transport_scope.reset(token)
                self._finish_observer("provider")
        self.provider.web_form = observed_web_form
        def reject_write(*_args, **_kwargs):
            raise PermissionDenied("Read-only evaluation fixture rejects all Jira mutations")
        self.provider._write = self.provider.post_multipart = reject_write

    def _observe_tools(self):
        original = self.runtime.server.call_tool
        async def observed(name, arguments, context=None):
            started = time.perf_counter()
            record = {"name": name, "arguments": self._safe(arguments), "isError": None}
            self._begin_observer("tool", self.tool_calls, record)
            completion = {}
            try:
                result = await original(name, arguments, context)
                completion.update(isError=result.is_error, result=self._safe(result.structured_content),
                                  content=self._safe([item.model_dump(mode="json") for item in result.content]))
                return result
            except BaseException as exc:
                completion.update(isError=True, errorType=type(exc).__name__)
                raise
            finally:
                completion["durationSeconds"] = round(time.perf_counter() - started, 6)
                self._finish_observer("tool", record, **completion)
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
                "toolCalls": len(self.tool_calls), "toolErrors": sum(bool(row.get("isError")) for row in self.tool_calls),
                "upstreamRequests": len(self.upstream_calls),
                "upstreamDurationSeconds": (round(sum(row["durationSeconds"] for row in self.upstream_calls), 6)
                    if all(row.get("durationSeconds") is not None for row in self.upstream_calls) else None),
                "cachePolicy": "fresh-client-and-private-cache-each-case", "backgroundRevalidation": False,
                "fixtureTransport": "inprocess-jira820",
                "observerCoverage": {"upstream": "provider-scoped TestClient.request dispatches",
                    "authProbes": "provider direct TestClient requests included; no SSO provider instantiated",
                    "transportRetries": "repeated TestClient.request dispatches; internal redirect hops not observed",
                    "discoveryProtocol": "guarded-http-json-rpc", "backgroundWork": "disabled"},
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
