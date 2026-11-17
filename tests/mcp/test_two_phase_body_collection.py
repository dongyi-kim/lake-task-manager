"""Two-phase body collection against a field- and JQL-respecting REST source."""
from copy import deepcopy
import json
from time import perf_counter

import anyio
from mcp import Client
import pytest

from app.auth.base import UpstreamUnavailable
from app.jira.jql import And, Atom, Not, Or, _Parser, _split_order, tokenize


OMITTED = object()


class BodySource:
    """A physical Jira search double; no unchecked all-query fixture shortcuts."""

    def __init__(self, env, total=100, *, metadata_cap=100, body_cap=10):
        self.calls = []
        self.metadata_cap, self.body_cap = metadata_cap, body_cap
        self.fail_body_attempt = None
        self.body_attempts = 0
        self.before_body = None
        self.rows = {}
        for index in range(1, total + 1):
            key = f"DL-{index:04}"
            fields = {field: None for field in env.client._issue_fields(light=True).split(",")}
            fields.update(summary=key, updated="2026-11-17T09:00:00.000+0900",
                status={"name": "Open"}, project={"key": "DL"}, labels=["alpha", "beta"],
                description="source-start:" + key + ":" + "x" * 4096 + ":source-end")
            self.rows[key] = {"key": key, "fields": fields}
        env.provider.get_json = self.get_json

    @staticmethod
    def _matches(node, row):
        if isinstance(node, And):
            return all(BodySource._matches(child, row) for child in node.children)
        if isinstance(node, Or):
            return any(BodySource._matches(child, row) for child in node.children)
        if isinstance(node, Not):
            return not BodySource._matches(node.child, row)
        assert isinstance(node, Atom)
        tokens = tokenize(node.value)
        field, op = tokens[0].value.lower(), tokens[1].value.lower()
        values = [json.loads(token.value) if token.kind == "STRING" else token.value
                  for token in tokens[2:] if token.kind in {"STRING", "WORD"}]
        actual = {"project": row["key"].split("-")[0], "key": row["key"], "issuekey": row["key"],
                  "status": row["fields"]["status"]["name"], "labels": row["fields"]["labels"],
                  "updated": row["fields"]["updated"]}
        assert field in actual, node.value
        if op in {"in", "="}:
            return any(value in values for value in actual[field]) if isinstance(actual[field], list) else actual[field] in values
        if op == ">=":
            return actual[field] >= values[0]
        raise AssertionError("unsupported fixture predicate: " + node.value)

    def get_json(self, path, params=None, **kwargs):
        assert path == "/rest/api/2/search", "body collection must use key batches, never scalar GETs"
        params = dict(params or {})
        fields = set(params["fields"].split(","))
        body = "description" in fields or "*all" in fields
        if body and self.before_body is not None:
            self.before_body()
        where, order = _split_order(tokenize(params["jql"]))
        tree = _Parser(where).parse()

        def has_key_predicate(node):
            if isinstance(node, Atom):
                terms = tokenize(node.value)
                return terms[0].value.lower() in {"key", "issuekey"} and terms[1].value.lower() in {"in", "="}
            if isinstance(node, (And, Or)):
                return any(has_key_predicate(child) for child in node.children)
            return False
        rows = [row for row in self.rows.values() if self._matches(tree, row)]
        descending = bool(order and order[-1].value.lower() == "desc")
        rows.sort(key=lambda row: row["key"], reverse=descending)
        start = params.get("startAt", 0)
        size = min(params["maxResults"], self.body_cap if body else self.metadata_cap)
        selected = rows[start:start + size]
        self.calls.append({"path": path, "kind": "body" if body else "metadata", "params": params,
                           "keyRestricted": has_key_predicate(tree),
                           "matchedKeys": [row["key"] for row in rows],
                           "returnedKeys": [row["key"] for row in selected]})
        if body:
            self.body_attempts += 1
            if self.body_attempts == self.fail_body_attempt:
                raise UpstreamUnavailable("interrupted body batch")
        return {"startAt": start, "maxResults": size, "total": len(rows), "issues": [
            {"key": row["key"], "fields": {field: deepcopy(value) for field, value in row["fields"].items()
                if (field in fields or "*all" in fields) and value is not OMITTED}}
            for row in selected]}

    def since(self, count):
        return self.calls[count:]


async def search(mcp, jql="project = DL", **kwargs):
    result = await mcp.call_tool("search_issues", {"jql": jql, "include_description": True,
                                                "max_results": 10, **kwargs})
    assert not result.is_error, result.structured_content
    return result.structured_content["data"]


def assert_body_key_batches(calls):
    for call in calls:
        if call["kind"] == "body":
            assert call["params"]["maxResults"] <= 10
            assert len(call["matchedKeys"]) <= 10
            assert call["keyRestricted"]


def test_broad_cold_search_discovers_light_hundred_then_fetches_only_ten_bodies(build_env, monkeypatch):
    env = build_env()
    source = BodySource(env)

    def forbidden(*args, **kwargs):
        raise AssertionError("body searches must not allocate a whole-query snapshot")

    monkeypatch.setattr(env.client, "_jql_snapshot", forbidden)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            page = await search(mcp)
            assert page["total"] == 100 and page["returned"] == 10
            assert page["nextStartAt"] == 10 and page["hasMore"]
            assert page["snapshotId"] is None and page["snapshotFrozen"] is False
            assert page["descriptionMissingKeys"] == []
            assert all(row["fields"]["description"] == source.rows[row["key"]]["fields"]["description"]
                       for row in page["issues"])
            assert [call["kind"] for call in source.calls] == ["metadata", "body"]
            assert source.calls[0]["params"]["maxResults"] == 100
            assert source.calls[1]["returnedKeys"] == [f"DL-{i:04}" for i in range(1, 11)]
            assert_body_key_batches(source.calls)

    anyio.run(scenario)


def test_repeat_is_zero_rest_and_changed_query_reuses_same_ticket_bodies(build_env):
    env = build_env()
    source = BodySource(env)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            first = await search(mcp)
            before = len(source.calls)
            repeated = await search(mcp)
            assert repeated["issues"] == first["issues"] and len(source.calls) == before
            changed = await search(mcp, "project = DL AND labels = alpha")
            assert changed["issues"] == first["issues"]
            assert [call["kind"] for call in source.since(before)] == ["metadata"]

    anyio.run(scenario)


def test_mixed_known_bodies_hydrates_only_missing_keys_and_small_cold_batch_stays_one_call(build_env):
    env = build_env()
    source = BodySource(env)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            keys = ["DL-0001", "DL-0002", "DL-0003"]
            seeded = await mcp.call_tool("get_tickets", {"keys": keys, "include_description": True})
            assert not seeded.is_error and seeded.structured_content["data"]["complete"]
            assert len(source.calls) == 1 and source.calls[0]["kind"] == "body"
            before = len(source.calls)
            result = await search(mcp)
            assert result["descriptionMissingKeys"] == []
            calls = source.since(before)
            assert [call["kind"] for call in calls] == ["metadata", "body"]
            assert calls[1]["matchedKeys"] == [f"DL-{i:04}" for i in range(4, 11)]
            assert_body_key_batches(source.calls)

    anyio.run(scenario)


def test_null_and_empty_bodies_are_known_but_omitted_description_is_retried(build_env):
    env = build_env()
    source = BodySource(env, total=3)
    source.rows["DL-0001"]["fields"]["description"] = None
    source.rows["DL-0002"]["fields"]["description"] = ""
    source.rows["DL-0003"]["fields"]["description"] = OMITTED

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            first = await search(mcp)
            assert first["descriptionMissingKeys"] == ["DL-0003"]
            descriptions = {row["key"]: row["fields"].get("description", OMITTED) for row in first["issues"]}
            assert descriptions == {"DL-0001": None, "DL-0002": "", "DL-0003": OMITTED}
            before = len(source.calls)
            await search(mcp, "project = DL AND labels = beta")
            calls = source.since(before)
            assert [call["kind"] for call in calls] == ["metadata", "body"]
            assert calls[1]["matchedKeys"] == ["DL-0003"]

    anyio.run(scenario)


def test_changed_updated_revision_fetches_only_the_changed_ticket_body(build_env):
    env = build_env()
    source = BodySource(env)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            await search(mcp)
            source.rows["DL-0003"]["fields"].update(updated="2026-11-17T10:00:00.000+0900", description="new body revision")
            before = len(source.calls)
            changed = await search(mcp, "project = DL AND labels = beta")
            calls = source.since(before)
            assert [call["kind"] for call in calls] == ["metadata", "body"]
            assert calls[1]["matchedKeys"] == ["DL-0003"]
            assert next(row for row in changed["issues"] if row["key"] == "DL-0003")["fields"]["description"] == "new body revision"

    anyio.run(scenario)


def test_live_revalidates_both_metadata_and_bodies_after_warm_reads(build_env):
    env = build_env()
    source = BodySource(env)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            await search(mcp)
            before = len(source.calls)
            result = await mcp.call_tool("search_issues", {"jql": "project = DL", "include_description": True,
                                                          "max_results": 10, "cache_mode": "live"})
            assert not result.is_error, result.structured_content
            assert result.structured_content["cache"]["compliant"] is True
            assert result.structured_content["cache"]["upstreamValidated"] is True
            assert [call["kind"] for call in source.since(before)] == ["metadata", "body"]

    anyio.run(scenario)


def test_changed_revision_between_discovery_and_hydration_is_explicit_and_retryable(build_env):
    env = build_env()
    source = BodySource(env)

    def change_during_hydration():
        source.before_body = None
        source.rows["DL-0001"]["fields"].update(updated="2026-11-17T11:00:00.000+0900", description="raced body revision")

    source.before_body = change_during_hydration

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("search_issues", {"jql": "project = DL", "include_description": True, "max_results": 10})
            assert result.is_error and result.structured_content["error"]["code"] == "query_state_changed"
            assert result.structured_content.get("data") is None
            resumed = await search(mcp)
            first = resumed["issues"][0]["fields"]
            assert first["updated"] == "2026-11-17T11:00:00.000+0900"
            assert first["description"] == "raced body revision"

    anyio.run(scenario)


def test_interrupted_hydration_retry_reuses_completed_server_capped_body_chunk(build_env):
    env = build_env()
    source = BodySource(env, body_cap=3)
    source.fail_body_attempt = 2

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            failed = await mcp.call_tool("search_issues", {"jql": "project = DL", "include_description": True, "max_results": 10})
            assert failed.is_error and failed.structured_content.get("data") is None
            assert [call["kind"] for call in source.calls] == ["metadata", "body", "body"]
            completed = set(source.calls[1]["returnedKeys"])
            assert completed == {"DL-0001", "DL-0002", "DL-0003"}
            source.fail_body_attempt = None
            before = len(source.calls)
            resumed = await search(mcp)
            assert resumed["returned"] == 10 and resumed["descriptionMissingKeys"] == []
            retry = source.since(before)
            assert all(call["kind"] == "body" for call in retry)
            assert not completed.intersection(key for call in retry for key in call["matchedKeys"])
            assert_body_key_batches(source.calls)

    anyio.run(scenario)


def test_server_capped_metadata_pages_follow_actual_cursor_without_skipping_keys(build_env):
    env = build_env()
    source = BodySource(env, total=13, metadata_cap=4, body_cap=3)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            keys, cursors, start = [], [], 0
            while True:
                page = await search(mcp, start_at=start)
                assert 0 < page["returned"] <= 4
                keys.extend(row["key"] for row in page["issues"])
                if not page["hasMore"]:
                    break
                assert page["nextStartAt"] == start + page["returned"]
                cursors.append(page["nextStartAt"])
                start = page["nextStartAt"]
            assert keys == [f"DL-{i:04}" for i in range(1, 14)]
            assert cursors == [4, 8, 12]
            assert [call["params"]["startAt"] for call in source.calls if call["kind"] == "metadata"] == [0, 4, 8, 12]
            assert_body_key_batches(source.calls)

    anyio.run(scenario)


def test_hundred_ticket_cold_warm_cross_query_rest_ledger(build_env, record_property):
    env = build_env()
    source = BodySource(env)
    ledger = {}

    async def collect(mcp, jql):
        seen, start = [], 0
        while True:
            page = await search(mcp, jql, start_at=start)
            seen.extend(row["key"] for row in page["issues"])
            assert page["descriptionMissingKeys"] == []
            assert all(row["fields"]["description"] == source.rows[row["key"]]["fields"]["description"]
                       for row in page["issues"])
            if not page["hasMore"]:
                break
            assert page["nextStartAt"] == start + page["returned"]
            start = page["nextStartAt"]
        assert seen == [f"DL-{i:04}" for i in range(1, 101)]

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            await mcp.list_tools()
            for name, query in [("cold", "project = DL"), ("warm", "project = DL"),
                                ("cross_query", "project = DL AND labels = alpha")]:
                before, started = len(source.calls), perf_counter()
                await collect(mcp, query)
                calls = source.since(before)
                ledger[name] = {"metadata": sum(call["kind"] == "metadata" for call in calls),
                                "body": sum(call["kind"] == "body" for call in calls),
                                "rest": len(calls), "elapsedMs": round((perf_counter() - started) * 1000)}
            assert [(ledger[name]["metadata"], ledger[name]["body"], ledger[name]["rest"])
                    for name in ("cold", "warm", "cross_query")] == [(1, 10, 11), (0, 0, 0), (1, 0, 1)]
            assert_body_key_batches(source.calls)

    anyio.run(scenario)
    record_property("two_phase_body_rest_ledger", json.dumps(ledger, sort_keys=True))
    print("TWO_PHASE_BODY_REST_LEDGER=" + json.dumps(ledger, sort_keys=True))


def test_small_known_key_mixed_batch_reuses_bodies_without_losing_cold_shortcut(build_env):
    env = build_env()
    source = BodySource(env, total=4)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            first = {"keys": ["DL-0001", "DL-0002"], "include_description": True}
            for _ in range(2):
                result = await mcp.call_tool("get_tickets", first)
                assert not result.is_error, result.structured_content
            assert len(source.calls) == 1  # Cold one request, identical warm read zero.
            before = len(source.calls)
            mixed = {"keys": ["DL-0002", "DL-0003"], "include_description": True}
            result = await mcp.call_tool("get_tickets", mixed)
            assert not result.is_error, result.structured_content
            calls = source.since(before)
            assert [call["kind"] for call in calls] == ["metadata", "body"]
            assert calls[-1]["matchedKeys"] == ["DL-0003"]
            before = len(source.calls)
            result = await mcp.call_tool("get_tickets", mixed)
            assert not result.is_error and len(source.calls) == before
    anyio.run(scenario)


@pytest.mark.parametrize("change", ["session", "mutation"])
def test_known_batch_rejects_state_change_between_output_pages(build_env, change):
    env = build_env()
    source = BodySource(env, total=11)
    original = env.client.search_issues_page
    changed = [False]
    def page_then_change(*args, **kwargs):
        page = original(*args, **kwargs)
        if page["hasMore"] and not changed[0]:
            changed[0] = True
            if change == "session":
                with env.client._provider_lock:
                    env.client._provider_generation += 1
            else:
                env.client.advance_jql_generation()
        return page
    env.client.search_issues_page = page_then_change

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("get_tickets", {
                "keys": list(source.rows), "include_description": True})
            assert result.is_error, "A batch must not mix observations across authentication/mutation generations"
            assert result.structured_content["error"]["code"] == "query_state_changed"
    anyio.run(scenario)
