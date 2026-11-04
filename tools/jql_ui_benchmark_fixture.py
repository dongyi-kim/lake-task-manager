"""Opt-in, loopback-only instrumentation for the actual JQL UI benchmark.

Imported only by the benchmark's temporary sitecustomize. No production imports.
The same fixture and instrumentation run against baseline and candidate source.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone


ETL = ["test.ui01"] + [f"perf.etl{i:02d}" for i in range(2, 16)]
WORKBENCH = ETL[4:9] + [f"perf.wb{i:02d}" for i in range(1, 4)]
DAY = date(2026, 11, 4)
LOGICAL_NOW = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)


class CacheClock:
    """Freeze freshness only; do not change real scheduling, deadlines, or latency."""
    @staticmethod
    def time():
        return LOGICAL_NOW.timestamp()

    def __getattr__(self, name):
        return getattr(time, name)


def fixture_world():
    from app.infra.settings import get_settings, load_people, load_plan
    from app.mock.world import World

    w = World.__new__(World)
    w.today, w.plan, w.people = DAY, load_plan(), load_people()
    w.project = "DL"
    s = get_settings()
    w.sp_field, w.epic_link_field = s.sp_field_id, s.epic_link_field_id
    w.modules = ["ETL", "Workbench", "TEST"]
    w.gen_modules = ["ETL", "Workbench"]
    w._comp_ids = {m: str(100+i) for i, m in enumerate(w.modules + ["사용자 VoC"])}
    w.users = {
        u: {"name": u, "displayName": f"성능{i:02d} TEST", "realName": f"성능{i:02d}",
            "company": "TEST", "active": True}
        for i, u in enumerate(dict.fromkeys(ETL + WORKBENCH + ["test.ui02", "lead"]), 1)
    }
    w.issues, w.sprints, w.attachments = {}, {}, {}
    w._counter = 30000
    for key, module in [("DL-101", "ETL"), ("DL-102", "ETL"), ("DL-110", "Workbench")]:
        w._fx(key, "Epic", f"[{module}] 성능 검증 Epic {key}", module=module, component=module,
              assignee=ETL[0], reporter=ETL[1], sp=10, epicName=f"{key} 성능 Epic", labels=["perf-fixture"])
    sequence = 20000
    for module, people, size in [("ETL", ETL, 30), ("Workbench", WORKBENCH[5:], 12)]:
        for person_idx, user in enumerate(people):
            for n in range(size):
                sequence += 1
                key = f"DL-{sequence}"
                cat = ["todo", "inprogress", "done"][n % 3]
                age = [2, 10, 20, 45][(n // 3) % 4]
                epic = ("DL-101" if n % 2 else "DL-102") if module == "ETL" else "DL-110"
                row = w._fx(key, "Task", f"[{module}] {user} 검증 작업 {n:02d}",
                    module=module, component=module, assignee=user,
                    reporter=people[(person_idx + 1) % len(people)],
                    statusCategory=cat, statusName={"todo": "Open", "inprogress": "In Progress", "done": "Resolved"}[cat],
                    updated=DAY-timedelta(days=age), created=DAY-timedelta(days=50),
                    resolved=DAY-timedelta(days=age) if cat == "done" else None,
                    due=DAY+timedelta(days=(n % 7)-3), epicKey=epic, sp=3,
                    description="고정된 성능 비교용 본문", labels=["perf-fixture"], priority="P2-Major")
                if n in (1, 7):
                    for child in range(3):
                        sequence += 1
                        child_key = f"DL-{sequence}"
                        row["subtasks"].append(child_key)
                        w._fx(child_key, "Sub-Task", f"[{module}] 하위 {child_key}",
                            module=module, component=module, assignee=user,
                            reporter=people[(person_idx+1) % len(people)], parentKey=key, epicKey=None,
                            statusCategory=["todo", "inprogress", "done"][child],
                            statusName=["Open", "In Progress", "Resolved"][child],
                            updated=DAY-timedelta(days=2), resolved=DAY-timedelta(days=2) if child == 2 else None,
                            sp=1, priority="P2-Major", labels=["perf-fixture"])
    # Component-only matches and unresolved Done fallback must remain visible.
    for i in range(15):
        sequence += 1
        w._fx(f"DL-{sequence}", "Task", f"[ETL] 미배정 업무 {i}", module="ETL", component="ETL",
              assignee=None, reporter="lead", epicKey="DL-101", labels=["perf-fixture"],
              priority="P2-Major", sp=1)
    w._index()
    w._build_activity()
    w.confluence = {}
    return w


def install():
    if os.environ.get("JIRA_ENV") != "mock" or os.environ.get("APP_PORT") != "4457":
        raise RuntimeError("Benchmark instrumentation requires mock and fixed loopback port 4457")
    import webbrowser
    # The harness owns its browser; the dev launcher's second tab would warm caches.
    webbrowser.open = lambda *args, **kwargs: False
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    import app.mock.world as world_module
    world = fixture_world()
    world_module.get_world = lambda: world
    fingerprint = hashlib.sha256(json.dumps(world.issues, sort_keys=True, default=str).encode()).hexdigest()
    from app.auth.inprocess import InProcessProvider
    from app.infra.cache import Cache
    import app.infra.cache as cache_module
    import app.jira.jira_client as client_module
    import app.jira.jql as jql_module
    cache_module.time = CacheClock()
    client_module.time = CacheClock()
    real_compile = jql_module.compile_jql

    def fixed_compile(raw, **kwargs):
        kwargs['now'] = LOGICAL_NOW
        return real_compile(raw, **kwargs)

    client_module.compile_jql = jql_module.compile_jql = fixed_compile

    lock = threading.Lock()
    requests = []
    hits = Counter()
    original_get = InProcessProvider._get
    original_write = InProcessProvider._write
    original_cache_get = Cache.get
    original_many = Cache.get_many

    def counted_get(self, path, params, priority=None):
        start = time.perf_counter()
        with lock:
            entry = {"method": "GET", "path": path, "params": copy.deepcopy(params), "priority": priority,
                     "start": start, "done": False}
            requests.append(entry)
        details = {}
        try:
            result = original_get(self, path, params, priority)
            if path == "/rest/api/2/search":
                payload = result.json()
                details["rows"] = len(payload.get("issues", []))
                details["total"] = payload.get("total")
            details["status"] = result.status_code
            return result
        except BaseException as exc:
            details["error"] = type(exc).__name__
            raise
        finally:
            end = time.perf_counter()
            with lock:
                entry.update(details, done=True, end=end, elapsedMs=(end-start)*1000)

    def counted_write(self, method, path, json_body, params, want_json=True):
        start=time.perf_counter()
        entry={"method":method.upper(),"path":path,"params":copy.deepcopy(params),
               "fieldNames":sorted((json_body or {}).get('fields',{})),"start":start,"done":False}
        with lock:
            requests.append(entry)
        details = {}
        try:
            return original_write(self,method,path,json_body,params,want_json=want_json)
        except BaseException as exc:
            details['error']=type(exc).__name__
            raise
        finally:
            end = time.perf_counter()
            with lock:
                entry.update(details,done=True,end=end,elapsedMs=(end-start)*1000)

    def cache_get(self, key):
        result = original_cache_get(self, key)
        with lock:
            hits[(str(key).split(":", 1)[0], "hit" if result is not None else "miss")] += 1
        return result

    def cache_many(self, keys, *args, **kwargs):
        keys = list(keys)
        result = original_many(self, keys, *args, **kwargs)
        with lock:
            for key in keys:
                hits[(str(key).split(":", 1)[0], "hit" if key in result else "miss")] += 1
        return result

    InProcessProvider._get, InProcessProvider._write = counted_get, counted_write
    Cache.get, Cache.get_many = cache_get, cache_many
    from app import main
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    @main.app.middleware("http")
    async def isolated_browser(request, call_next):
        # Ignore pre-existing localhost tabs from earlier manual dev sessions.
        if request.headers.get("host", "").split(":", 1)[0] != "127.0.0.1":
            return JSONResponse({"detail": "Isolated benchmark: use 127.0.0.1"}, status_code=503)
        return await call_next(request)

    def metrics(request):
        if request.client.host not in ("127.0.0.1", "::1"):
            return JSONResponse({}, status_code=403)
        with lock:
            value = {"pid": os.getpid(), "worldHash": fingerprint, "issueCount": len(world.issues),
                     "logicalClock": LOGICAL_NOW.isoformat(),
                     "etlPeople": len(ETL), "requests": copy.deepcopy(requests),
                     "cache": {f"{kind}:{hit}": n for (kind, hit), n in hits.items()}}
        return JSONResponse(value)

    main.app.router.routes.insert(0, Route("/__jql_benchmark", metrics, methods=["GET"]))

    def reset(request):
        if request.client.host not in ("127.0.0.1", "::1"):
            return JSONResponse({}, status_code=403)
        with lock:
            if any(not entry["done"] for entry in requests):
                return JSONResponse({"pending": True}, status_code=409)
        main._cache.invalidate()
        return JSONResponse({"cleared": True})

    main.app.router.routes.insert(0, Route("/__jql_benchmark/reset", reset, methods=["POST"]))
