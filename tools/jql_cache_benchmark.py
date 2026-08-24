"""Deterministic API-level JQL/cache benchmark for a selected repository revision.

Temporary databases stay below ``.cache`` and are removed by TemporaryDirectory. The script is
stdlib-only until it switches ``sys.path`` to the requested repository, so the same file can run
against a pre-feature worktree and the candidate worktree.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def summarize(values):
    return {
        "samples": len(values),
        "p50Ms": round(statistics.median(values), 2) if values else 0.0,
        "p95Ms": round(percentile(values, .95), 2),
        "minMs": round(min(values), 2) if values else 0.0,
        "maxMs": round(max(values), 2) if values else 0.0,
    }


def run(repo: Path, latency_ms: int, cold_iterations: int, warm_iterations: int):
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    os.environ["JIRA_ENV"] = "mock"
    os.environ["LAKE_MOCK_LATENCY_MS"] = str(max(0, latency_ms))

    from app.infra.cache import Cache
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient

    cache_root = repo / ".cache"
    cache_root.mkdir(exist_ok=True)

    def client_at(path):
        client = JiraClient(get_settings(), Cache(str(path)))
        provider = client.provider
        original = provider.get_json
        counts = {"all": 0, "search": 0, "issue": 0}

        def counted(url, params=None, **kwargs):
            counts["all"] += 1
            if url == "/rest/api/2/search":
                counts["search"] += 1
            elif str(url).startswith("/rest/api/2/issue/"):
                counts["issue"] += 1
            return original(url, params=params, **kwargs)

        provider.get_json = counted
        return client, counts

    scenarios = {
        "exactRepeat": [],
        "andReordered": [],
        "orReordered": [],
        "orThenLeaf": [],
        "issueApiRepeat": [],
    }
    request_counts = {name: [] for name in scenarios}
    correctness = []
    mutation_rows = []
    with tempfile.TemporaryDirectory(prefix="jql-bench-", dir=cache_root) as temp:
        temp = Path(temp)
        cold_iterations = max(1, int(cold_iterations))
        warm_iterations = max(cold_iterations, int(warm_iterations))
        warm_base, warm_extra = divmod(warm_iterations, cold_iterations)
        for iteration in range(cold_iterations):
            def measure(name, first, second, expect_same=True):
                client, counts = client_at(temp / f"{name}-{iteration}.sqlite3")
                try:
                    before = time.perf_counter()
                    left = first(client)
                    middle = time.perf_counter()
                    repeats = warm_base + (1 if iteration < warm_extra else 0)
                    for warm_index in range(repeats):
                        count_before = dict(counts)
                        warm_start = time.perf_counter()
                        right = second(client)
                        end = time.perf_counter()
                        scenarios[name].append((end - warm_start) * 1000)
                        request_counts[name].append({
                            "coldMs": round((middle - before) * 1000, 2)
                            if warm_index == 0 else None,
                            "warmUpstream": counts["all"] - count_before["all"],
                            "warmSearch": counts["search"] - count_before["search"],
                        })
                        if expect_same and isinstance(left, list) and isinstance(right, list):
                            correctness.append({
                                "scenario": name,
                                "sameKeys": ([x.get("key") for x in left]
                                             == [x.get("key") for x in right]),
                            })
                finally:
                    try:
                        client.provider.close()
                    except Exception:
                        pass
                    close_cache = getattr(client.cache, "close", None)
                    if close_cache:
                        close_cache()
                    else:
                        client.cache._conn.close()

            base = "project = DL AND statusCategory != done AND components = Workbench"
            measure(
                "exactRepeat",
                lambda c: c.search_issues(base + " ORDER BY updated DESC", max_results=200),
                lambda c: c.search_issues(base + " ORDER BY updated DESC", max_results=200),
            )
            measure(
                "andReordered",
                lambda c: c.search_issues(base + " ORDER BY updated DESC", max_results=200),
                lambda c: c.search_issues(
                    "components = Workbench AND project = DL AND statusCategory != done "
                    "ORDER BY updated DESC", max_results=200),
            )
            or_left = (
                "project = DL AND assignee = test.ui01 OR "
                "project = DL AND reporter = test.ui01 ORDER BY updated DESC"
            )
            or_right = (
                "reporter = test.ui01 AND project = DL OR "
                "assignee = test.ui01 AND project = DL ORDER BY updated DESC"
            )
            measure(
                "orReordered",
                lambda c: c.search_issues(or_left, max_results=200),
                lambda c: c.search_issues(or_right, max_results=200),
            )
            measure(
                "orThenLeaf",
                lambda c: c.search_issues(or_left, max_results=200),
                lambda c: c.search_issues(
                    "project = DL AND assignee = test.ui01 ORDER BY key ASC", max_results=200),
                expect_same=False,
            )
            measure(
                "issueApiRepeat",
                lambda c: [c.get_issue("DL-9001")],
                lambda c: [c.get_issue("DL-9001")],
            )

            mutation_client, mutation_counts = client_at(
                temp / f"mutation-{iteration}.sqlite3")
            try:
                mutation_client._reprime = lambda *args, **kwargs: None
                mutation_jql = "key = DL-9001 ORDER BY updated DESC"
                mutation_client.search_issues(mutation_jql, max_results=5)
                mutation_client.search_issues(mutation_jql, max_results=5)
                summary = f"JQL cache benchmark {latency_ms}-{iteration}"
                write_start = time.perf_counter()
                mutation_client.update_fields("DL-9001", {"summary": summary})
                write_end = time.perf_counter()
                count_before = dict(mutation_counts)
                refreshed = mutation_client.search_issues(mutation_jql, max_results=5)
                refresh_end = time.perf_counter()
                mutation_rows.append({
                    "writeMs": round((write_end - write_start) * 1000, 2),
                    "nextQueryMs": round((refresh_end - write_end) * 1000, 2),
                    "nextQueryUpstream": mutation_counts["all"] - count_before["all"],
                    "fresh": bool(refreshed) and
                             (refreshed[0].get("fields") or {}).get("summary") == summary,
                })
            finally:
                try:
                    mutation_client.provider.close()
                except Exception:
                    pass
                close_cache = getattr(mutation_client.cache, "close", None)
                if close_cache:
                    close_cache()
                else:
                    mutation_client.cache._conn.close()

    return {
        "repo": str(repo),
        "latencyMs": latency_ms,
        "coldIterations": cold_iterations,
        "warmIterations": warm_iterations,
        "scenarios": {
            name: {**summarize(values), "requests": request_counts[name]}
            for name, values in scenarios.items()
        },
        "mutation": {
            "write": summarize([row["writeMs"] for row in mutation_rows]),
            "nextQuery": summarize([row["nextQueryMs"] for row in mutation_rows]),
            "nextQueryUpstream": sum(row["nextQueryUpstream"] for row in mutation_rows),
            "allFresh": all(row["fresh"] for row in mutation_rows),
            "samples": mutation_rows,
        },
        "correctness": correctness,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--latency-ms", type=int, default=250)
    parser.add_argument("--cold-iterations", type=int, default=5)
    parser.add_argument("--warm-iterations", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.repo.resolve(), args.latency_ms,
                 args.cold_iterations, args.warm_iterations)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
