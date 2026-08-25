"""Repeatable micro-benchmark for PR #32's production/Lark JQL compilers."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime
from importlib.metadata import version
from zoneinfo import ZoneInfo

from app.jira.jql import compile_jql
from app.jira.jql_lark import _parser, compile_jql_lark


QUERIES = (
    "project = DL ORDER BY updated DESC",
    "project in (ZZ, AA, ZZ) AND statusCategory != done",
    "(A = 1 OR A = 2) AND (B = 3 OR C = 4)",
    "NOT (statusCategory = done OR priority = Minor)",
    "assignee = currentUser() AND updated >= -14d "
    "AND created >= startOfWeek('-1w') ORDER BY updated DESC",
    'assignee = "test.ui01" AND statusCategory = Done AND '
    '(resolved >= -28d OR (resolved IS EMPTY AND updated >= -28d)) '
    'ORDER BY updated DESC, key ASC',
)
CONTEXT = {
    "user_id": "test.ui01",
    "timezone_name": "Asia/Seoul",
    "now": datetime(2026, 8, 24, 10, 2, tzinfo=ZoneInfo("Asia/Seoul")),
    "ttl_seconds": 900,
}


def _run(compiler, iterations: int) -> float:
    started = time.perf_counter()
    for _ in range(iterations):
        for query in QUERIES:
            compiler(query, **CONTEXT)
    return time.perf_counter() - started


def _cold_process_ms(statement: str, runs: int = 5) -> float:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", statement], check=True, cwd=os.getcwd(), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        samples.append((time.perf_counter() - started) * 1_000)
    return statistics.median(samples)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1_000)
    args = parser.parse_args()
    iterations = max(1, args.iterations)

    for query in QUERIES:
        assert compile_jql(query, **CONTEXT) == compile_jql_lark(query, **CONTEXT)

    _parser.cache_clear()
    cold_started = time.perf_counter()
    compile_jql_lark(QUERIES[0], **CONTEXT)
    lark_first_compile_ms = (time.perf_counter() - cold_started) * 1_000

    production_seconds = _run(compile_jql, iterations)
    lark_seconds = _run(compile_jql_lark, iterations)
    operations = iterations * len(QUERIES)
    process_baseline_ms = _cold_process_ms("pass")
    production_import_ms = _cold_process_ms("import app.jira.jql")
    lark_import_ms = _cold_process_ms("import app.jira.jql_lark")
    print(json.dumps({
        "larkVersion": version("lark"),
        "queries": len(QUERIES),
        "operations": operations,
        "larkFirstCompileMs": round(lark_first_compile_ms, 3),
        "productionColdImportDeltaMs": round(production_import_ms - process_baseline_ms, 3),
        "larkColdImportDeltaMs": round(lark_import_ms - process_baseline_ms, 3),
        "productionUsPerCompile": round(production_seconds * 1_000_000 / operations, 3),
        "larkWarmUsPerCompile": round(lark_seconds * 1_000_000 / operations, 3),
        "warmSlowdown": round(lark_seconds / production_seconds, 3),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
