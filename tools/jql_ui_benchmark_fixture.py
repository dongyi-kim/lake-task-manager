"""Opt-in year-world instrumentation, imported only by disposable sitecustomize."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit


ETL = [f"etl.user{index:02d}" for index in range(8)]
CATALOG = ETL
LOGICAL_NOW = datetime(2026, 11, 10, 23, 59, 59, tzinfo=timezone(timedelta(hours=9)))


class RunOwnershipGuard:
    """Reject unrelated HTTP before handlers without buffering owned streams."""
    def __init__(self, app, *, token, counter=None):
        if not isinstance(token, str) or not token or not token.isascii():
            raise ValueError("An explicit ASCII benchmark owner token is required")
        self.app, self._token = app, token.encode("ascii")
        self._counter, self._lock = counter if counter is not None else ({"blocked": 0}, threading.Lock())

    @property
    def blocked_requests(self):
        with self._lock:
            return self._counter["blocked"]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or ()
        owners = [value for name, value in headers if name.lower() == b"x-ltm-benchmark-owner"]
        host = next((value for name, value in headers if name.lower() == b"host"), b"")
        if (len(owners) != 1 or not secrets.compare_digest(owners[0], self._token)
                or host.split(b":", 1)[0] != b"127.0.0.1"):
            with self._lock:
                self._counter["blocked"] += 1
            from starlette.responses import JSONResponse
            await JSONResponse({"detail": "Unowned benchmark request"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


class CacheClock:
    def time(self):
        return LOGICAL_NOW.timestamp()

    def __getattr__(self, name):
        return getattr(time, name)


def fixture_world(bundle=None):
    from app.infra.settings import get_settings
    from app.mock.world import World
    from tools.codex_mcp_eval.etl import build_etl_world
    bundle = bundle or build_etl_world()
    world = World.__new__(World)
    for name, value in vars(bundle.world).items():
        setattr(world, name, copy.deepcopy(value))
    settings = get_settings()
    world.sp_field, world.epic_link_field = settings.sp_field_id, settings.epic_link_field_id
    world._counter = 60000
    world.gen_modules = list(world.modules)
    world._comp_ids = {name: str(100 + index) for index, name in enumerate(world.modules)}
    world._index()
    return world


class WireLedger:
    """Observe TestClient.request once, including direct identity probes and retries."""
    def __init__(self, event_path=None):
        self.condition = threading.Condition()
        self.requests = []
        self.active = 0
        self.admitted = 0
        self.last_activity = time.monotonic()
        self.event_path = Path(event_path) if event_path else None
        if self.event_path:
            self.event_path.touch(exist_ok=False)

    def _event(self, row):
        if self.event_path:
            with self.event_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({**row, "pid": os.getpid()}, ensure_ascii=False) + "\n")

    def observe(self, request):
        def dispatch(method, url, *args, **kwargs):
            started = time.perf_counter()
            path = urlsplit(str(url)).path
            row = {"method": str(method).upper(), "path": path, "params": copy.deepcopy(kwargs.get("params") or {}),
                   "done": False, "dispatchAttempt": True, "serverReceived": None, "status": None,
                   "start": started, "fieldNames": sorted((kwargs.get("json") or {}).get("fields", {}))}
            with self.condition:
                row["sequence"] = len(self.requests)
                self.requests.append(row)
                self._event(row)
                self.active += 1
                self.last_activity = time.monotonic()
            details = {}
            try:
                response = request(method, url, *args, **kwargs)
                details.update(status=response.status_code, responseBytes=len(response.content), serverReceived=True)
                return response
            except BaseException as error:
                details["error"] = type(error).__name__
                raise
            finally:
                end = time.perf_counter()
                with self.condition:
                    row.update(details, done=True, end=end, elapsedMs=(end - started) * 1000)
                    self._event(row)
                    self.active -= 1
                    self.last_activity = time.monotonic()
                    self.condition.notify_all()
        return dispatch

    def observe_admission(self, method):
        def admitted(*args, **kwargs):
            with self.condition:
                self.admitted += 1
                self.last_activity = time.monotonic()
            try:
                return method(*args, **kwargs)
            finally:
                with self.condition:
                    self.admitted -= 1
                    self.last_activity = time.monotonic()
                    self.condition.notify_all()
        return admitted

    def snapshot(self):
        with self.condition:
            return copy.deepcopy(self.requests)

    def settle(self, timeout=10, quiet=.15):
        started, deadline = time.monotonic(), time.monotonic() + timeout
        with self.condition:
            while True:
                now = time.monotonic()
                quiet_remaining = quiet - (now - self.last_activity)
                if not self.active and not self.admitted and quiet_remaining <= 0:
                    return {"complete": True, "timedOut": False, "active": 0, "admitted": 0, "waitSeconds": now - started}
                if now >= deadline:
                    return {"complete": False, "timedOut": True, "active": self.active,
                            "admitted": self.admitted, "waitSeconds": now - started}
                self.condition.wait(min(deadline - now, max(.001, quiet_remaining))
                                    if not self.active and not self.admitted else deadline - now)


def settlement_response(request, ledger):
    """Local settlement route, independently testable without a year-world app."""
    from starlette.responses import JSONResponse
    if request.client.host not in ("127.0.0.1", "::1"):
        return JSONResponse({}, status_code=403)
    try:
        timeout = float(request.query_params.get("timeoutSeconds", "10"))
        if not math.isfinite(timeout) or not 0 <= timeout <= 120:
            raise ValueError("out of range")
    except ValueError:
        return JSONResponse({"detail": "timeoutSeconds must be a finite number between 0 and 120"},
                            status_code=400)
    return JSONResponse(ledger.settle(timeout=timeout))


def install():
    if os.environ.get("JIRA_ENV") != "mock" or os.environ.get("APP_PORT") != "4457":
        raise RuntimeError("Benchmark requires mock and the fixed port 4457")
    owner = os.environ.get("JQL_UI_OWNER_TOKEN")
    # Validate before importing the app or constructing any provider/world.
    RunOwnershipGuard(None, token=owner)
    import webbrowser
    webbrowser.open = lambda *_args, **_kwargs: False
    from tools.codex_mcp_eval.etl import build_etl_world
    from app.infra import settings
    from app.mock import world as world_module
    bundle = build_etl_world()
    world = fixture_world(bundle)
    world.plan = settings.load_plan()
    world.people = settings.load_people()
    world_module.World.ME = world.ME
    world_module.get_world = lambda: world

    from app.auth.inprocess import InProcessProvider
    from app.infra import cache, cache_policy
    from app.jira import jira_client, jql
    clock = CacheClock()
    cache.time = cache_policy.time = jira_client.time = clock
    original_compile = jql.compile_jql
    def compile_fixed(raw, **kwargs):
        kwargs["now"] = LOGICAL_NOW
        return original_compile(raw, **kwargs)
    jql.compile_jql = jira_client.compile_jql = compile_fixed

    wire_directory = Path(os.environ["JQL_UI_WIRE_DIR"])
    ledger = WireLedger(wire_directory / f"wire-{os.getpid()}.jsonl")
    original_init = InProcessProvider.__init__
    providers = []
    def initialize(provider):
        original_init(provider)
        providers.append(provider)
        store = provider._client.app.state.store
        for name in ("issues", "users", "confluence", "attachments", "activity", "sprints"):
            setattr(store, name, copy.deepcopy(getattr(world, name)))
        store.reindex()
        store.config.current_user = world.ME
        original_fields = store.serializer.issue_fields
        def timestamp_fields(issue, base_url=""):
            fields = original_fields(issue, base_url)
            fields.update(issue.get("_timestamps") or {})
            return fields
        store.serializer.issue_fields = timestamp_fields
        from tools.codex_mcp_eval.etl.status_history_search import install_status_history_search
        install_status_history_search(provider._client.app,
            observed_until=bundle.spec.observed_until, search_timezone=bundle.manifest["timezone"])
        provider._client.request = ledger.observe(provider._client.request)
    InProcessProvider.__init__ = initialize
    for name in ("_get", "_write", "web_form", "post_multipart"):
        setattr(InProcessProvider, name, ledger.observe_admission(getattr(InProcessProvider, name)))

    from app import main
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    ownership = ({"blocked": 0}, threading.Lock())
    main.app.add_middleware(RunOwnershipGuard, token=owner, counter=ownership)
    def metrics(request):
        if request.client.host not in ("127.0.0.1", "::1"):
            return JSONResponse({}, status_code=403)
        with ownership[1]:
            blocked = ownership[0]["blocked"]
        return JSONResponse({"pid": os.getpid(), "worldHash": bundle.manifest["data_sha256"],
            "manifest": bundle.manifest, "issueCount": len(world.issues), "logicalClock": LOGICAL_NOW.isoformat(),
            "etlPeople": len(ETL), "currentUser": ETL[0], "requests": ledger.snapshot(), "cache": {},
            "observerCoverage": "all provider TestClient.request dispatches; cache counters not observed",
            "ownership": {"contract": "run-owned-http-v1", "required": True, "foreignBlockedHttpRequests": blocked,
                          "blockedRequestsAreProviderRest": False},
            "providerIssueCounts": [len(provider._client.app.state.store.issues) for provider in providers]})
    def settle(request):
        return settlement_response(request, ledger)
    def reset(request):
        if request.client.host not in ("127.0.0.1", "::1"):
            return JSONResponse({}, status_code=403)
        if not ledger.settle()["complete"]:
            return JSONResponse({"pending": True}, status_code=409)
        main._cache.invalidate()
        return JSONResponse({"cleared": True})
    main.app.router.routes[:0] = [Route("/__jql_benchmark", metrics, methods=["GET"]),
        Route("/__jql_benchmark/settle", settle, methods=["POST"]),
        Route("/__jql_benchmark/reset", reset, methods=["POST"])]
