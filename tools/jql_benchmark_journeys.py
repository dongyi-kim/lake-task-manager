"""Fixed backend journeys; explicit conformance never changes the common arm workload."""
from __future__ import annotations

from tools.jql_benchmark_runtime import supports


BULK = "project = DL AND component = ETL ORDER BY key ASC"
PERSON_A = 'project = DL AND component = ETL AND assignee = "etl.user00"'
PERSON_B = 'project = DL AND component = ETL AND reporter = "etl.user01"'
PEOPLE = f"({PERSON_A}) OR ({PERSON_B}) ORDER BY key ASC"
KEYS = ["DL-21010", "DL-21012", "DL-21013"]
SMALL = "key in (" + ",".join(KEYS) + ") ORDER BY key ASC"

COVERAGE = {
    "bulk": {"status": "implemented", "scope": "complete ETL search and exact ordered independent membership"},
    "people": {"status": "implemented", "scope": "two canonical people selectors and reordered query"},
    "navigation": {"status": "partial", "scope": "Task detail and complete Epic child API; workload/WBS UI traversal pending"},
    "whole_split": {"status": "implemented", "scope": "whole query, split queries, whole again with exact union parity"},
    "projection": {"status": "implemented", "scope": "light to description projection, full to light reuse"},
    "age_invalidation": {"status": "partial", "scope": "omitted TTL expiry, client invalidation, 20-day aging",
                         "missing": "upstream mutation/write cost and explicit retention-boundary qualification"},
    "scheduling": {"status": "partial", "scope": "shared API fan-out and stream close after first group",
                   "missing": "interactive UI priority and cancellation during in-flight physical page"},
    "restart": {"status": "partial", "scope": "actual later-page 503, cache restart/recovery and frozen cursor continuation",
                "missing": "full backend-process kill/restart (this journey recreates client/cache only)"},
}


def ordered(keys):
    return sorted(keys, key=lambda key: (key.rsplit("-", 1)[0], int(key.rsplit("-", 1)[1])))


def expected_keys(trial, predicate):
    return ordered(key for key, row in trial.bundle.world.issues.items() if predicate(row))


def rows_result(rows, expected):
    keys = [row.get("key") for row in rows]
    if keys != expected or len(set(keys)) != len(keys):
        raise AssertionError(f"Ordered membership mismatch: got {len(keys)}, expected {len(expected)}")
    return {"keys": keys, "total": len(keys), "parity": True}


def run_common(trial, name, warm_repeats):
    client = lambda: trial.client
    etl = lambda row: row["component"] == "ETL"
    all_etl = expected_keys(trial, etl)
    first = expected_keys(trial, lambda row: etl(row) and row["assignee"] == "etl.user00")
    second = expected_keys(trial, lambda row: etl(row) and row["reporter"] == "etl.user01")
    people = ordered(set(first) | set(second))
    def query(jql, expected):
        return rows_result(client().search_issues(jql, max_results=None), expected)
    if name == "bulk":
        operation = lambda: query(BULK, all_etl)
    elif name == "people":
        operation = lambda: query(PEOPLE, people)
    elif name == "navigation":
        epic = expected_keys(trial, lambda row: row.get("epicKey") == "DL-20000")
        def operation():
            task = client().get_issue_light("DL-21010")
            assert task["key"] == "DL-21010"
            return {"taskKey": task["key"], "epic": rows_result(client().epic_issues("DL-20000"), epic)}
    elif name == "whole_split":
        operation = lambda: query(PEOPLE, people)
    elif name == "projection":
        def operation():
            page = client().search_issues_page(SMALL, fields="summary,description", max_results=100)
            assert all("description" in row["fields"] for row in page["issues"])
            return rows_result(page["issues"], KEYS)
        trial.measure("light-prime", lambda: rows_result(client().search_issues_page(
            SMALL, fields="summary", max_results=100)["issues"], KEYS), cache_state="empty", logical_queries=1)
    elif name == "age_invalidation":
        operation = lambda: query(SMALL, KEYS)
    elif name == "scheduling":
        def operation():
            values = client()._pmap(KEYS, lambda key: client().get_issue_light(key))
            return rows_result(values, KEYS)
    elif name == "restart":
        return run_restart(trial, all_etl, warm_repeats)
    else:
        raise ValueError("Unknown common journey: " + name)
    logical = None if name in {"navigation", "scheduling"} else 1
    calls = 2 if name == "navigation" else 3 if name == "scheduling" else 1
    trial.measure("initial", operation, cache_state="empty" if name != "projection" else "light-only",
                  logical_queries=logical, backend_calls=calls)
    if name == "whole_split":
        def split():
            left = client().search_issues(PERSON_A + " ORDER BY key ASC", max_results=None)
            right = client().search_issues(PERSON_B + " ORDER BY key ASC", max_results=None)
            assert [row["key"] for row in left] == first and [row["key"] for row in right] == second
            merged = {row["key"]: row for row in left + right}
            return rows_result([merged[key] for key in ordered(merged)], people)
        trial.measure("split-reuse", split, cache_state="whole-covered", logical_queries=2, backend_calls=2)
    if name == "age_invalidation":
        from app.jira.cache_policy import MutationEvent
        trial.clock.epoch += trial.fixture.settings.cache_ttl_seconds + 1
        trial.measure("expired", operation, cache_state="expired", logical_queries=1)
        client()._record_mutation(MutationEvent("fields", KEYS[0], changed_fields=("summary",)))
        trial.measure("client-invalidation", operation, cache_state="invalidated", logical_queries=1)
        trial.clock.epoch += 20 * 86400
        trial.measure("twenty-days", operation, cache_state="aged-20-days", logical_queries=1)
    if name == "scheduling":
        def close_stream():
            iterator = client().iter_search_issue_chunks(PEOPLE)
            try:
                part = next(iterator)
                assert not part.get("error")
                return {"receivedKeys": [row["key"] for row in part["issues"]], "closedAfterFirstGroup": True}
            finally:
                iterator.close()
        trial.measure("close-first-group", close_stream, cache_state="mixed", logical_queries=1)
    for index in range(warm_repeats):
        trial.measure(f"valid-{index}", operation, cache_state="valid", logical_queries=logical, backend_calls=calls)


def run_restart(trial, all_etl, warm_repeats):
    if not supports(trial.client.search_issues_page, "snapshot_id"):
        raise RuntimeError("Common restart journey requires the existing snapshot_id API")
    trial.fail_one_later_search_page()
    trial.measure("interrupted-search", lambda: trial.client.search_issues_page(BULK, max_results=7),
                  cache_state="empty", expected_error=True, logical_queries=1)
    trial.restart_client()
    state = {}
    def initial():
        page = trial.client.search_issues_page(BULK, max_results=7)
        state.update(page)
        assert page["total"] == len(all_etl) and page["hasMore"]
        return rows_result(page["issues"], all_etl[:7])
    trial.measure("restart-recovery", initial, cache_state="interrupted-cache", logical_queries=1)
    trial.restart_client()
    def continuation():
        page = trial.client.search_issues_page(BULK, start_at=state["nextStartAt"], max_results=7,
                                               snapshot_id=state["snapshotId"])
        assert page["total"] == len(all_etl)
        return rows_result(page["issues"], all_etl[7:14])
    trial.measure("restart-cursor", continuation, cache_state="frozen-snapshot", logical_queries=1)
    for index in range(warm_repeats):
        trial.measure(f"valid-{index}", continuation, cache_state="valid", logical_queries=1)


def run_conformance(trial):
    """Only explicit options; unsupported revisions are visible, never silently substituted."""
    from app.infra import cache_policy
    page = trial.client.search_issues_page
    report = {"strategy": supports(page, "strategy"), "cacheModes": []}
    modes = getattr(cache_policy, "CacheMode", ())
    report["cacheModes"] = [mode.value for mode in modes] if modes else []
    if not report["strategy"] or report["cacheModes"] != ["live", "hot", "warm", "cool", "cold"]:
        raise RuntimeError("Explicit conformance unsupported by this revision: " + str(report))
    for mode in report["cacheModes"]:
        for strategy in ("auto", "off", "people", "full"):
            def operation(mode=mode, strategy=strategy):
                with cache_policy.read_scope(cache_mode=mode):
                    result = page(SMALL, fields="summary,description", max_results=100,
                                  strategy=strategy, max_parts=1)
                return {**rows_result(result["issues"], KEYS), "execution": result.get("execution")}
            trial.measure(f"{mode}-{strategy}", operation, cache_state="explicit-shared-storage", logical_queries=1)
