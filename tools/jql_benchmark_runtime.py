"""Disposable backend fixture, raw attempt retention and actual dispatch reducers."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
import hashlib
import inspect
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class RawAttempt:
    """Reserve outputs exclusively and retain earlier trials when later work fails."""
    def __init__(self, directory, manifest):
        self.directory, self.manifest = Path(directory), manifest
        self.result = None

    def __enter__(self):
        with (self.directory / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(self.manifest, stream, ensure_ascii=False, indent=2)
        self.stream = (self.directory / "trials.jsonl").open("x", encoding="utf-8")
        return self

    def record(self, value):
        self.stream.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        self.stream.flush()

    def __exit__(self, kind, error, _trace):
        self.stream.close()
        result = self.result or {"complete": False}
        if error:
            result = {**result, "complete": False, "error": {"type": kind.__name__, "message": str(error)}}
        with (self.directory / "result.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, default=str)


class FreshnessClock:
    def __init__(self, epoch):
        self.epoch = epoch

    def time(self):
        return self.epoch

    def __getattr__(self, name):
        return getattr(time, name)


def transport_metrics(calls):
    """Count every observed dispatch, including failures and repeated query pages."""
    from tools.codex_mcp_eval.metrics import upstream_summary
    jira = [call for call in calls if call.get("service") == "jira"]
    searches = [call for call in jira if call.get("path") == "/rest/api/2/search"]
    queries = {str((call.get("params") or {}).get("jql", "")) for call in searches}
    return {"jiraRestAttempts": len(jira), "searchPageAttempts": len(searches),
            "uniquePhysicalJql": len(queries), "physicalJql": sorted(queries),
            "jira": upstream_summary([{"upstreamCalls": calls}], service="jira"),
            "confluence": upstream_summary([{"upstreamCalls": calls}], service="confluence"),
            "authProbeAttempts": sum(call.get("path") == "/rest/api/2/myself" for call in jira),
            "failedDispatches": sum(call.get("errorType") is not None or
                                     (call.get("status") or 0) >= 400 for call in calls)}


class BackendTrial:
    """Use the existing year fixture/observer, with no business MCP or model calls.

    The fixture's isolated ephemeral listener is setup infrastructure. This harness calls
    JiraClient directly and never starts the application/UI on port 4457.
    """
    def __init__(self, scratch, bundle, latency_ms=0, jitter_ms=0, parallel=False):
        self.scratch, self.bundle = Path(scratch), bundle
        self.latency_ms, self.jitter_ms, self.parallel = latency_ms, jitter_ms, parallel
        self.phases = []
        self.clock = FreshnessClock(bundle.spec.observed_until.timestamp())

    def __enter__(self):
        from tools.codex_mcp_eval.etl.fixture import EtlFixture
        self.stack = ExitStack()
        try:
            self.fixture = self.stack.enter_context(EtlFixture(self.scratch / "fixture", port=0, bundle=self.bundle))
            self.client = self.fixture.client
            self.setup_calls = len(self.fixture.upstream_calls)
            self._install_clock()
            provider = self.fixture.provider
            provider.supports_parallel = self.parallel
            if self.parallel:
                # A declared synthetic read-only parallel provider, never an SSO claim.
                self.stack.enter_context(patch.object(provider, "_serial", lambda fn, priority=None: fn()))
            original_request = provider._client.request
            def delayed(method, url, *args, **kwargs):
                identity = [self.bundle.spec.seed, str(method), str(url), kwargs.get("params") or {}]
                spread = int(digest(identity)[:8], 16) % (2 * self.jitter_ms + 1) - self.jitter_ms
                delay = max(0, self.latency_ms + spread)
                if delay:
                    time.sleep(delay / 1000)
                return original_request(method, url, *args, **kwargs)
            self.stack.enter_context(patch.object(provider._client, "request", delayed))
            return self
        except BaseException:
            self.stack.close()
            raise

    def _install_clock(self):
        from app.infra import cache, cache_policy
        from app.jira import jira_client, jql
        clock = self.clock
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(clock.time(), tz)
        for module in (cache, cache_policy, jira_client):
            if hasattr(module, "time"):
                self.stack.enter_context(patch.object(module, "time", clock))
        self.stack.enter_context(patch.object(jql, "datetime", FrozenDateTime))

    def measure(self, name, operation, *, cache_state, expected_error=False,
                logical_queries=None, backend_calls=1):
        fixture = self.fixture
        before = len(fixture.upstream_calls)
        started = time.perf_counter()
        phase = {"name": name, "cacheState": cache_state, "logicalQueryCount": logical_queries,
                 "logicalDnfLeaves": None, "backendApiCalls": backend_calls,
                 "freshnessEpoch": self.clock.time(), "expectedError": expected_error}
        try:
            phase["result"] = operation()
            phase["ok"] = not expected_error
        except Exception as error:
            phase.update(ok=expected_error, error={"type": type(error).__name__, "message": str(error)})
        phase["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
        phase["backgroundSettlement"] = fixture.settle_observers()
        calls = fixture.upstream_calls[before:]
        phase.update(transport_metrics(calls), upstreamCalls=list(calls))
        self.phases.append(phase)
        return phase

    def restart_client(self):
        from app.infra.cache import Cache
        from app.jira.jira_client import JiraClient
        old = self.client
        old._provider, old._provider_built = None, False
        old.close()
        self.fixture.cache.close()
        self.fixture.cache = Cache(str(self.fixture.cache_path))
        self.client = self.fixture.client = JiraClient(self.fixture.settings, self.fixture.cache)
        self.client._provider, self.client._provider_built = self.fixture.provider, True
        self.client._refresh_bg = lambda *_args, **_kwargs: None
        self.fixture.cache.always_revalidate = ()
        self.fixture.cache.revalidator = None

    def fail_one_later_search_page(self):
        """Return one real ASGI 503; the shared observer sees the failed dispatch."""
        from starlette.responses import JSONResponse
        from urllib.parse import parse_qs
        app = self.fixture.provider._client.app
        original = app.middleware_stack or app.build_middleware_stack()
        fired, lock = [], threading.Lock()
        async def middleware(scope, receive, send):
            query = parse_qs(scope.get("query_string", b"").decode())
            with lock:
                fail = (scope.get("path") == "/rest/api/2/search" and
                        int(query.get("startAt", [0])[0]) > 0 and not fired)
                if fail:
                    fired.append(True)
            if fail:
                await JSONResponse({"errorMessages": ["benchmark injected later-page interruption"]},
                                   status_code=503)(scope, receive, send)
            else:
                await original(scope, receive, send)
        self.stack.enter_context(patch.object(app, "middleware_stack", middleware))

    def result(self, journey, repeat):
        fixture = self.fixture
        settlement = fixture.settle_observers()
        summary = fixture.summary()
        return {"journey": journey, "repeat": repeat, "phases": self.phases,
                **transport_metrics(fixture.upstream_calls), "upstreamCalls": list(fixture.upstream_calls),
                "setupRestAttempts": self.setup_calls, "backgroundSettlement": settlement,
                "worldUnchanged": summary["worldUnchanged"] and summary["providerStoreUnchanged"],
                "ok": all(phase["ok"] and phase["backgroundSettlement"]["complete"] for phase in self.phases)
                      and settlement["complete"] and summary["worldUnchanged"] and summary["providerStoreUnchanged"],
                "mcpCalls": 0, "modelUsage": None}

    def __exit__(self, *_error):
        self.stack.close()


def supports(function, parameter):
    return parameter in inspect.signature(function).parameters
