"""Common, one-checkout restart recovery through authenticated public MCP.

Three distinct application processes share one disposable SQLite cache. An actual
later-page 503 precedes recovery; every frozen result page is checked independently.
No browser, model, source copy, Git mutation, or implicit business-call retry.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.jql_cache_benchmark import ARMS, dependency_identity, repository_identity, reserve_attempt
from tools.jql_benchmark_runtime import RawAttempt
from tools.jql_ui_benchmark import (BASE, child_environment, prepare, server_executable,
                                    stop_owned_server, wire_tail)

BULK = "project = DL AND component = ETL ORDER BY key ASC"
PAGE_SIZE = 100
EXPECTED_COUNT = 2245
OPTIONAL_DIAGNOSTICS = {"snapshotCreatedAt", "snapshotFrozen"}


def expected_etl_keys(bundle):
    return sorted((key for key, row in bundle.world.issues.items()
                   if row.get("project") == "DL" and row.get("component") == "ETL"),
                  key=lambda key: (key.rsplit("-", 1)[0], int(key.rsplit("-", 1)[1])))


class FrozenPages:
    """Accept only the exact next independent membership slice of one snapshot."""
    def __init__(self, expected, page_size=PAGE_SIZE, *, advertised=()):
        self.expected, self.page_size = list(expected), page_size
        if not self.expected or len(set(self.expected)) != len(self.expected):
            raise ValueError("Expected membership must be nonempty and unique")
        self.offset = self.pages = 0
        self.snapshot_id = self.canonical = self.created = None
        self.advertised, self.present = set(advertised), set()
        self.complete = False

    def add(self, page):
        if self.complete:
            raise ValueError("Snapshot membership is already complete")
        if not isinstance(page, dict) or not isinstance(page.get("issues"), list):
            raise ValueError("Missing public search page")
        if not all(isinstance(row, dict) for row in page["issues"]):
            raise ValueError("Invalid issue membership rows")
        keys = [row.get("key") for row in page["issues"]]
        expected = self.expected[self.offset:self.offset + self.page_size]
        if keys != expected:
            raise ValueError(f"Exact ordered membership failed at offset {self.offset}; got {len(keys)}, expected {len(expected)}")
        end = self.offset + len(keys)
        more = end < len(self.expected)
        checks = {"startAt": self.offset, "maxResults": self.page_size, "returned": len(keys),
                  "total": len(self.expected), "hasMore": more,
                  "nextStartAt": end if more else None}
        for name, expected_value in checks.items():
            if page.get(name) != expected_value or type(page.get(name)) is not type(expected_value):
                raise ValueError(f"Invalid frozen page {name} at offset {self.offset}")
        present = OPTIONAL_DIAGNOSTICS & page.keys()
        if self.advertised - present or self.pages and present != self.present:
            raise ValueError("Optional snapshot diagnostic presence changed or contradicts advertised schema")
        if "snapshotFrozen" in present and (type(page["snapshotFrozen"]) is not bool or page["snapshotFrozen"] != bool(self.pages)):
            raise ValueError("Incoherent snapshotFrozen diagnostic")
        if "snapshotCreatedAt" in present and type(page["snapshotCreatedAt"]) not in (int, float):
            raise ValueError("Invalid snapshotCreatedAt diagnostic")
        identity = (page.get("snapshotId"), page.get("canonicalJql"), page.get("snapshotCreatedAt"))
        if not all(isinstance(value, str) and value for value in identity[:2]):
            raise ValueError("Missing common snapshot identity fields")
        if self.pages and identity != (self.snapshot_id, self.canonical, self.created):
            raise ValueError("Frozen snapshot identity changed")
        self.snapshot_id, self.canonical, self.created = identity
        self.present = present
        self.offset, self.pages, self.complete = end, self.pages + 1, not more

    def result(self):
        return {"complete": self.complete, "expectedCount": len(self.expected), "returnedCount": self.offset,
                "pageCount": self.pages, "continuationPages": max(0, self.pages - 1), "snapshotId": self.snapshot_id,
                "optionalSnapshotDiagnostics": {"present": sorted(self.present), "missing": sorted(OPTIONAL_DIAGNOSTICS - self.present),
                                                "advertised": sorted(self.advertised)}}


def tool_payload(result):
    payload = result.get("structuredContent")
    if not isinstance(payload, dict):
        raise ValueError("MCP tool did not return structuredContent")
    if result.get("isError") and payload.get("ok") is not False:
        raise ValueError("MCP isError contradicts tool envelope")
    return payload


def typed_failure(payload):
    error = payload.get("error")
    if (payload.get("ok") is not False or not isinstance(error, dict)
            or not isinstance(error.get("code"), str) or not error["code"]):
        raise ValueError("Expected a typed tool failure, not a successful or empty result")
    return error


def require_common_tool(catalog):
    tool = next((row for row in catalog.get("tools", []) if row.get("name") == "search_issues"), None)
    properties = (tool or {}).get("inputSchema", {}).get("properties", {})
    required = {"jql", "start_at", "max_results", "snapshot_id", "detail", "include_description"}
    missing = required - properties.keys()
    if missing:
        raise ValueError("Missing common search_issues API: " + ", ".join(sorted(missing)))
    return tool


def advertised_diagnostics(tool):
    def properties(value):
        found = set()
        if isinstance(value, dict):
            found.update(OPTIONAL_DIAGNOSTICS & value.get("properties", {}).keys())
            for child in value.values():
                found.update(properties(child))
        elif isinstance(value, list):
            for child in value:
                found.update(properties(child))
        return found
    return properties(tool.get("outputSchema", {}))


def protocol_counts(rows):
    calls = [row for row in rows if row.get("kind") == "mcp"]
    return {"protocolHttpAttempts": len(calls), "businessCalls": sum(row["method"] == "tools/call" for row in calls),
            "discoveryCalls": sum(row["method"] in {"initialize", "tools/list"} for row in calls),
            "notificationCalls": sum(row["method"].startswith("notifications/") for row in calls),
            "httpFailures": sum((row.get("httpStatus") or 0) >= 400 or bool(row.get("error")) for row in calls),
            "durationMs": round(sum(row.get("durationMs", 0) for row in calls), 3)}


def dispatch_metrics(rows):
    searches = [row for row in rows if row["path"] == "/rest/api/2/search"]
    return {"dispatchAttempts": len(rows), "searchPageAttempts": len(searches),
            "authProbeAttempts": sum(row["path"] == "/rest/api/2/myself" for row in rows),
            "uniquePhysicalJql": len({row["params"].get("jql") for row in searches}),
            "failedDispatches": sum((row.get("status") or 0) >= 400 or bool(row.get("error")) for row in rows),
            "dispatchDurationMs": round(sum(row.get("elapsedMs", 0) for row in rows), 3)}


def continuation_dispatches(rows, *, pid, start_sequence):
    return sum(row["pid"] == pid and row["sequence"] >= start_sequence for row in rows)


class LaterPageFault:
    """Keep the disposable phase-A outage active through every admitted retry."""
    def __init__(self, armed_path, log_path, *, enabled):
        self.armed, self.log, self.enabled = Path(armed_path), Path(log_path), enabled
        self.lock = threading.Lock()

    def inject(self, scope):
        if not self.enabled or scope.get("path") != "/rest/api/2/search":
            return False
        query = urllib.parse.parse_qs(scope.get("query_string", b"").decode())
        if int(query.get("startAt", [0])[0]) <= 0:
            return False
        with self.lock:
            if not self.armed.exists():
                return False
            record = {"pid": os.getpid(), "path": scope["path"], "params": query,
                      "status": 503, "injected": True, "at": time.perf_counter()}
            with self.log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return True


def fault_evidence(records, calls, *, phase_a_pid):
    wire = [row for row in calls if row["pid"] == phase_a_pid and row.get("status") == 503]
    def signature(row):
        params = tuple(sorted((name, tuple(str(value) for value in values) if isinstance(values, list)
                               else (str(values),)) for name, values in row["params"].items()))
        return row["pid"], row["path"], row["status"], params
    valid = all(row.get("injected") is True and row.get("pid") == phase_a_pid
                and row.get("status") == 503 and row.get("path") == "/rest/api/2/search" for row in records)
    return {"injectedCount": len(records), "wire503Count": len(wire),
            "matched": bool(records) and valid and Counter(map(signature, records)) == Counter(map(signature, wire))}


def recovery_complete(result):
    phases = result.get("phases", [])
    if [phase.get("name") for phase in phases] != ["interrupted", "recovery", "continuation"]:
        return False
    pids = [phase.get("backendPid") for phase in phases]
    paths = [phase.get("cachePath") for phase in phases]
    membership = result.get("membership", {})
    faults = result.get("faultEvidence", {})
    return (all(type(pid) is int and pid > 0 for pid in pids) and len(set(pids)) == 3
            and bool(paths[0]) and len(set(paths)) == 1
            and all(phases[index].get("cacheBefore") == phases[index - 1].get("cacheAfter")
                    and bool(phases[index].get("cacheBefore")) for index in (1, 2))
            and all(phase.get("stopped") is True and phase.get("portClosed") is True
                    and phase.get("settlement", {}).get("complete") is True
                    and phase["settlement"].get("active") == 0 and phase["settlement"].get("admitted") == 0
                    for phase in phases)
            and membership.get("complete") is True and membership.get("expectedCount") == EXPECTED_COUNT
            and membership.get("returnedCount") == EXPECTED_COUNT and membership.get("pageCount") == 23
            and membership.get("continuationPages") == 22 and bool(result.get("injectedFailure"))
            and faults.get("matched") is True and faults.get("injectedCount", 0) > 0
            and faults["injectedCount"] == faults.get("wire503Count") == result.get("injectedWire503Count")
            and faults["injectedCount"] == result.get("injectedFaultCount")
            and result.get("frozenContinuationRestAttempts") == 0
            and all(result.get(name) is True for name in ("revisionStable", "scratchRemoved", "port4457Closed", "withinOverallDeadline"))
            and result.get("wireTail", {}).get("complete") is True)


def require_settled(state):
    if (state.get("complete") is not True or state.get("active") != 0 or state.get("admitted") != 0):
        raise ValueError("Incomplete provider dispatch/admission settlement")
    return state


class Deadline:
    def __init__(self, overall, per_call=120):
        if not 0 < per_call <= 120 or overall <= 0:
            raise ValueError("Positive deadlines required; per-call limit cannot exceed 120 seconds")
        self.end, self.per_call = time.monotonic() + overall, per_call

    def remaining(self):
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Process-restart overall deadline exhausted")
        return min(self.per_call, remaining)


async def send_http(method, url, headers, data, timeout):
    import httpx2
    async with httpx2.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        response = await client.request(method, url, headers=headers, content=data)
        return response.status_code, response.text


class Transport:
    """One HTTP attempt per operation; retain exact sanitized request/response bodies."""
    def __init__(self, attempt, deadline):
        self.attempt, self.deadline, self.rows = attempt, deadline, []
        self.token, self.version, self.phase = "", "2025-06-18", "preflight"

    def event(self, row):
        self.attempt.record(row)

    def http(self, path, method="GET", body=None, *, rpc_method=None, timeout_cap=None):
        row = {"kind": "mcp" if rpc_method else "http", "phase": self.phase, "sequence": len(self.rows),
               "method": rpc_method or method, "path": path, "request": body, "done": False}
        self.rows.append(row)
        self.event({**row, "event": "start"})
        started = time.perf_counter()
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if rpc_method:
            headers.update(Authorization="Bearer " + self.token, **{"MCP-Protocol-Version": self.version})
        try:
            timeout = self.deadline.remaining()
            if timeout_cap is not None:
                timeout = min(timeout, timeout_cap)
            if timeout <= 0:
                raise TimeoutError("Operation deadline exhausted")
            async def exchange():
                return await asyncio.wait_for(send_http(method, BASE + path, headers,
                    None if body is None else json.dumps(body).encode("utf-8"), timeout), timeout)
            row["httpStatus"], text = asyncio.run(exchange())
            # Never persist the bearer credential, including unexpected error echoes.
            text = text.replace(self.token, "[redacted]") if self.token else text
            try:
                row["response"] = json.loads(text) if text else None
            except ValueError:
                row["responseText"] = text
                raise ValueError("Expected a JSON protocol response")
            if row["httpStatus"] >= 400:
                raise RuntimeError(f"HTTP {row['httpStatus']} at {path}")
            return row["response"]
        except Exception as error:
            row["error"] = {"type": type(error).__name__, "message": str(error).replace(self.token, "[redacted]")
                            if self.token else str(error)}
            raise
        finally:
            row.update(done=True, durationMs=round((time.perf_counter() - started) * 1000, 3))
            self.event({**row, "event": "done"})

    def rpc(self, method, params=None, *, notification=False):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            body["id"] = len(self.rows) + 1
        response = self.http("/mcp", "POST", body, rpc_method=method)
        if notification:
            return None
        if not isinstance(response, dict) or "error" in response or not isinstance(response.get("result"), dict):
            raise ValueError(f"JSON-RPC {method} failed; see retained raw response")
        return response["result"]

    def discover(self):
        initialized = self.rpc("initialize", {"protocolVersion": self.version, "capabilities": {},
            "clientInfo": {"name": "ltm-common-process-restart", "version": "1"}})
        self.version = initialized["protocolVersion"]
        self.rpc("notifications/initialized", notification=True)
        tools, cursor, seen = [], None, set()
        while True:
            catalog = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(catalog.get("tools", []))
            cursor = catalog.get("nextCursor")
            if not cursor:
                break
            if cursor in seen or len(seen) >= 20:
                raise ValueError("Tool discovery cursor failed to progress")
            seen.add(cursor)
        return require_common_tool({"tools": tools})

    def search(self, verifier):
        arguments = {"jql": BULK, "start_at": verifier.offset, "max_results": PAGE_SIZE,
                     "detail": "summary", "include_description": False}
        if verifier.snapshot_id:
            arguments["snapshot_id"] = verifier.snapshot_id
        return tool_payload(self.rpc("tools/call", {"name": "search_issues", "arguments": arguments}))


def cache_witness(path):
    """Record the SQLite/WAL lineage while the owned application is stopped."""
    result = {}
    for suffix in ("", "-wal", "-shm"):
        file = Path(str(path) + suffix)
        if file.exists():
            result[file.name] = {"bytes": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    return result


def port_closed():
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", 4457)) != 0


@contextmanager
def application(repo, scratch, output, env, transport, name, revision, result):
    if not port_closed():
        raise RuntimeError("Port 4457 is occupied; no alternate port is permitted")
    transport.phase = name
    cache = Path(env["CACHE_DB_PATH"])
    phase = {"name": name, "cachePath": str(cache), "cacheBefore": cache_witness(cache)}
    result["phases"].append(phase)
    started = time.perf_counter()
    process = None
    with (output / f"server-{name}.log").open("x", encoding="utf-8") as log:
        try:
            process = subprocess.Popen([server_executable(repo), str(repo / "run.py"), "--reload"],
                cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            phase["launcherPid"] = process.pid
            transport.event({"kind": "lifecycle", "event": "spawn", **phase})
            startup_deadline = min(transport.deadline.end, time.monotonic() + 60)
            while time.monotonic() < startup_deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Application exited during {name} startup")
                try:
                    health = transport.http("/api/health", timeout_cap=startup_deadline - time.monotonic())
                    metrics = transport.http("/__jql_benchmark", timeout_cap=startup_deadline - time.monotonic())
                    break
                except Exception:
                    # Startup probes retry; business MCP calls never do.
                    time.sleep(min(.25, transport.deadline.remaining()))
            else:
                raise TimeoutError(f"Application startup deadline exhausted in {name}")
            phase.update(backendPid=metrics["pid"], health=health, worldHash=metrics["worldHash"],
                         startupMs=round((time.perf_counter() - started) * 1000, 3))
            if not health.get("rev") or not revision.startswith(health["rev"]):
                raise ValueError("Running health revision does not match the frozen checkout")
            if metrics["worldHash"] != result["world"]["data_sha256"] or metrics["issueCount"] != 10000:
                raise ValueError("Running process has a different public world")
            phase["toolSchema"] = transport.discover()
            phase["advertisedSnapshotDiagnostics"] = sorted(advertised_diagnostics(phase["toolSchema"]))
            phase["primingSettlement"] = require_settled(transport.http("/__jql_benchmark/settle", "POST"))
            phase["primingCost"] = dispatch_metrics(transport.http("/__jql_benchmark")["requests"])
            yield phase
        finally:
            if process is not None:
                try:
                    phase["settlement"] = transport.http("/__jql_benchmark/settle", "POST")
                    phase["finalMetrics"] = transport.http("/__jql_benchmark")
                    phase["upstreamMetrics"] = dispatch_metrics(phase["finalMetrics"]["requests"])
                except Exception as error:
                    phase["settlementError"] = {"type": type(error).__name__, "message": str(error)}
                finally:
                    stop_owned_server(process)
                    phase.update(stopped=process.poll() is not None, portClosed=port_closed(), cacheAfter=cache_witness(cache))
                    phase["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
                    phase["protocolMetrics"] = protocol_counts(row for row in transport.rows if row["phase"] == name)
                    transport.event({"kind": "lifecycle", "event": "stop", **phase})


def install():
    """Disposable sitecustomize entrypoint; a real ASGI 503 stays inside wire observation."""
    from app.auth.inprocess import InProcessProvider
    from starlette.responses import JSONResponse
    from tools.jql_ui_benchmark_fixture import install as install_year_world
    original_init = InProcessProvider.__init__
    fault = LaterPageFault(os.environ["JQL_RESTART_ARMED"], os.environ["JQL_RESTART_FAULT_LOG"],
                          enabled=os.environ.get("JQL_RESTART_INJECT") == "1")
    def initialize(provider):
        original_init(provider)
        if os.environ.get("JQL_RESTART_INJECT") != "1":
            return
        app = provider._client.app
        original = app.middleware_stack or app.build_middleware_stack()
        async def interrupt(scope, receive, send):
            if fault.inject(scope):
                await JSONResponse({"errorMessages": ["common restart: injected later-page failure"]}, status_code=503)(scope, receive, send)
            else:
                await original(scope, receive, send)
        app.middleware_stack = interrupt
    InProcessProvider.__init__ = initialize
    install_year_world()


def run(repo, output, *, arm, dependency_label, overall_timeout=900, per_call_timeout=120):
    repo = Path(repo).resolve()
    if repo != Path(__file__).resolve().parents[1] or repo != Path.cwd().resolve():
        raise ValueError("Use the one current application checkout")
    if arm not in ARMS or not dependency_label:
        raise ValueError("Explicit common arm and dependency label are required")
    deadline = Deadline(overall_timeout, per_call_timeout)
    identity = repository_identity(repo)
    output = reserve_attempt(repo, output)
    manifest = {"protocol": "jql-process-restart-v2", "arm": arm, "revision": identity,
                "dependencies": dependency_identity(dependency_label), "query": BULK, "pageSize": PAGE_SIZE,
                "options": {"detail": "summary", "include_description": False},
                "cachePolicy": "omitted", "strategy": "omitted", "latencyMs": 0,
                "overallTimeoutSeconds": overall_timeout, "perCallTimeoutSeconds": per_call_timeout,
                "faultProfile": {"phaseA": "Every search page with startAt>0 returns 503 while armed, until owned process stops",
                    "phaseBC": "injection disabled; same persisted cache", "driverBusinessRetries": 0,
                    "publicExpectation": "typed unsuccessful search after existing bounded MCP read recovery",
                    "providerSemantics": "Mock InProcessProvider maps Jira 503 to SessionExpired; mock auth.check can replay the read once. This is fixture behavior, not a production authentication claim.",
                    "evidence": "append-only faults.jsonl must match every phase-A PID wire503, including retry pages"},
                "modelUsage": None, "qualificationEligible": False}
    result = {**manifest, "phases": [], "complete": False}
    started = time.perf_counter()
    scratch_path, verifier = None, None
    with RawAttempt(output, manifest) as attempt:
        transport = Transport(attempt, deadline)
        fault_log = output / "faults.jsonl"
        fault_log.touch(exist_ok=False)
        try:
            if not port_closed():
                raise RuntimeError("Port 4457 must be free before the common restart run")
            with tempfile.TemporaryDirectory() as scratch:
                scratch_path = Path(scratch)
                result["scratchPath"] = str(scratch_path)
                from tools.codex_mcp_eval.etl import build_etl_world
                from app.local_mcp.security import Credentials
                bundle = build_etl_world()
                result["world"] = bundle.manifest
                expected = expected_etl_keys(bundle)
                if len(expected) != EXPECTED_COUNT:
                    raise ValueError("Independent public ETL membership is not 2245")
                verifier = FrozenPages(expected)
                with (output / "world.json").open("x", encoding="utf-8") as stream:
                    json.dump(bundle.manifest, stream, ensure_ascii=False, indent=2)
                config, bootstrap = prepare(repo, scratch_path, bundle)
                (bootstrap / "sitecustomize.py").write_text(
                    "from tools.jql_process_restart_benchmark import install\ninstall()\n", encoding="utf-8")
                (output / "wire").mkdir()
                env = child_environment(repo, scratch_path, config, bootstrap, 0)
                env.update(LAKE_REV=identity["commit"], JQL_UI_WIRE_DIR=str(output / "wire"),
                           JQL_RESTART_ARMED=str(scratch_path / "fault-armed"), JQL_RESTART_FAULT_LOG=str(fault_log))
                transport.token = Credentials(scratch_path / "runtime/local_mcp_secret.json").token()
                env["JQL_RESTART_INJECT"] = "1"
                with application(repo, scratch_path, output, env, transport, "interrupted", identity["commit"], result) as phase:
                    verifier.advertised = set(phase["advertisedSnapshotDiagnostics"])
                    Path(env["JQL_RESTART_ARMED"]).touch(exist_ok=False)
                    result["injectedFailure"] = typed_failure(transport.search(verifier))
                env["JQL_RESTART_INJECT"] = "0"
                with application(repo, scratch_path, output, env, transport, "recovery", identity["commit"], result) as phase:
                    verifier.advertised = set(phase["advertisedSnapshotDiagnostics"])
                    payload = transport.search(verifier)
                    if payload.get("ok") is not True or payload.get("partial") is True:
                        raise ValueError("Recovery did not return a complete successful page")
                    verifier.add(payload["data"])
                with application(repo, scratch_path, output, env, transport, "continuation", identity["commit"], result) as phase:
                    verifier.advertised = set(phase["advertisedSnapshotDiagnostics"])
                    before = transport.http("/__jql_benchmark")
                    phase["continuationStartSequence"] = len(before["requests"])
                    while not verifier.complete:
                        payload = transport.search(verifier)
                        if payload.get("ok") is not True or payload.get("partial") is True:
                            raise ValueError("Frozen continuation failed or reported partial data")
                        verifier.add(payload["data"])
                    phase["paginationSettlement"] = require_settled(transport.http("/__jql_benchmark/settle", "POST"))
        except BaseException as error:
            result["error"] = {"type": type(error).__name__, "message": str(error)}
        finally:
            result["membership"] = verifier.result() if verifier else {"complete": False}
            result["scratchRemoved"] = scratch_path is None or not scratch_path.exists()
            result["port4457Closed"] = port_closed()
            result["wireTail"] = wire_tail(output / "wire")
            calls = result["wireTail"]["requests"]
            continuation = next((phase for phase in result["phases"] if phase["name"] == "continuation"), {})
            if "continuationStartSequence" in continuation:
                result["frozenContinuationRestAttempts"] = continuation_dispatches(calls,
                    pid=continuation["backendPid"], start_sequence=continuation["continuationStartSequence"])
            result["faults"] = [json.loads(line) for line in fault_log.read_text(encoding="utf-8").splitlines()]
            phase_a = next((phase for phase in result["phases"] if phase["name"] == "interrupted"), {})
            result["faultEvidence"] = fault_evidence(result["faults"], calls, phase_a_pid=phase_a.get("backendPid"))
            result["injectedFaultCount"] = result["faultEvidence"]["injectedCount"]
            result["injectedWire503Count"] = result["faultEvidence"]["wire503Count"]
            result["protocolMetrics"] = protocol_counts(transport.rows)
            result["mcpCalls"] = result["protocolMetrics"]["businessCalls"]
            result["upstreamMetrics"] = dispatch_metrics(calls)
            result["revisionAfter"] = repository_identity(repo)
            result["revisionStable"] = result["revisionAfter"] == identity
            result["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
            result["withinOverallDeadline"] = time.monotonic() <= deadline.end
            result["complete"] = "error" not in result and recovery_complete(result)
            attempt.result = result
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--arm", choices=ARMS, required=True)
    parser.add_argument("--dependency-label", required=True)
    parser.add_argument("--dependency-dir", type=Path)
    parser.add_argument("--overall-timeout", type=float, default=900)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if args.dependency_dir:
        overlay = str(args.dependency_dir.resolve())
        sys.path.insert(0, overlay)
        os.environ["PYTHONPATH"] = os.pathsep.join([overlay, os.environ.get("PYTHONPATH", "")])
    result = run(args.repo, args.run, arm=args.arm, dependency_label=args.dependency_label,
                 overall_timeout=args.overall_timeout, per_call_timeout=args.timeout)
    print(json.dumps({"output": str(args.run), "complete": result["complete"], "error": result.get("error"),
                      "returnedCount": result["membership"].get("returnedCount"), "mcpCalls": result["mcpCalls"],
                      "dispatchAttempts": result["upstreamMetrics"]["dispatchAttempts"]}), flush=True)
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
