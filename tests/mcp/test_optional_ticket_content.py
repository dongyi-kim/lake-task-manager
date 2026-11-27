"""Optional raw bodies/comments use real SDK contracts and bounded REST searches."""
from copy import deepcopy
import json
from time import perf_counter
import tracemalloc

import anyio
from mcp import Client
import pytest

from app.auth.base import QueryStateChanged
from app.infra.json_payload import encoded_size
from app.jira.jql import _Parser, _split_order, tokenize
from test_two_phase_body_collection import BodySource, OMITTED


class ContentSource(BodySource):
    """Search projection with independent description/comment state and strict JQL."""

    def __init__(self, env, total=100, *, comments=25):
        super().__init__(env, total)
        self.before_content = None
        for index, row in enumerate(self.rows.values(), 1):
            row["fields"].update(issuetype={"name": "Task", "subtask": False},
                status={"id": "1", "name": "Open", "statusCategory": {"key": "new"}},
                components=[], created="2026-11-18T09:00:00.000+0900",
                assignee={"name": "u01", "displayName": "User"},
                reporter={"name": "u01", "displayName": "User"})
            row["fields"]["comment"] = {"startAt": 0, "maxResults": comments, "total": comments,
                "comments": [{"id": str(index * 1000 + offset), "body": f"{row['key']} raw comment {offset}",
                    "created": "2026-11-18T09:00:00.000+0900", "updated": "2026-11-18T09:00:00.000+0900",
                    "author": {"name": "u01", "displayName": "User"}}
                    for offset in range(comments)]}

    def get_json(self, path, params=None, **kwargs):
        assert path == "/rest/api/2/search", "optional content must use bounded search, not scalar issue/comment GETs"
        params = dict(params or {})
        fields = set(params["fields"].split(","))
        description, comments = "description" in fields or "*all" in fields, "comment" in fields or "*all" in fields
        content = description or comments
        if content and self.before_content is not None:
            self.before_content()
        where, order = _split_order(tokenize(params["jql"]))
        tree = _Parser(where).parse()
        rows = sorted((row for row in self.rows.values() if self._matches(tree, row)), key=lambda row: row["key"],
                      reverse=bool(order and order[-1].value.lower() == "desc"))
        start = params.get("startAt", 0)
        size = min(params["maxResults"], self.body_cap if content else self.metadata_cap)
        selected = rows[start:start + size]
        kind = "combined" if description and comments else "description" if description else "comments" if comments else "metadata"
        self.calls.append({"path": path, "params": params, "kind": kind,
            "matchedKeys": [row["key"] for row in rows], "returnedKeys": [row["key"] for row in selected]})
        return {"startAt": start, "maxResults": size, "total": len(rows), "issues": [
            {"key": row["key"], "fields": {field: deepcopy(value) for field, value in row["fields"].items()
                if (field in fields or "*all" in fields) and value is not OMITTED}}
            for row in selected]}


async def call_data(mcp, name, arguments):
    result = await mcp.call_tool(name, arguments)
    assert not result.is_error, result.structured_content or result.content
    return result.structured_content["data"]


def coverage(total, returned, *, complete, start=0, more=False, next_start=None):
    return {"total": total, "returned": returned, "startAt": start,
            "complete": complete, "hasMore": more, "nextStartAt": next_start}


def assert_coverage(actual, expected):
    assert {key: actual[key] for key in expected} == expected


def test_explicit_bulk_opt_out_is_accepted_and_never_requests_heavy_fields(build_env):
    env = build_env()
    source = BodySource(env, total=3)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            for name, arguments in [
                ("search_issues", {"jql": "project = DL", "max_results": 10}),
                ("get_tickets", {"keys": ["DL-0001", "DL-0002", "DL-0003"]}),
            ]:
                result = await mcp.call_tool(name, {**arguments, "include_description": False, "include_comments": False})
                assert not result.is_error, result.structured_content
                assert all("description" not in row["fields"] and "comment" not in row["fields"]
                           for row in result.structured_content["data"]["issues"])
            assert source.calls
            assert all(not {"description", "comment", "attachment", "issuelinks"}.intersection(
                call["params"]["fields"].split(",")) for call in source.calls)

    anyio.run(scenario)


@pytest.mark.parametrize("tool", ["search_issues", "get_tickets"])
def test_cold_combined_content_uses_one_hydration_and_warm_cross_query_reuses_both(build_env, tool):
    env = build_env()
    source = ContentSource(env, total=3)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            arguments = ({"jql": "project = DL", "max_results": 10} if tool == "search_issues"
                         else {"keys": ["DL-0001", "DL-0002", "DL-0003"]})
            arguments.update(include_description=True, include_comments=True)
            first = await call_data(mcp, tool, arguments)
            assert first["commentsRequested"] is True and first["commentsMissingKeys"] == []
            assert [call["kind"] for call in source.calls] == (["metadata", "combined"] if tool == "search_issues" else ["combined"])
            for row in first["issues"]:
                assert row["fields"]["description"] == source.rows[row["key"]]["fields"]["description"]
                assert row["fields"]["comment"] == source.rows[row["key"]]["fields"]["comment"]
                assert len(row["fields"]["comment"]["comments"]) == 25
                assert_coverage(row["commentsCoverage"], coverage(25, 25, complete=True))
            before = len(source.calls)
            repeated = await call_data(mcp, tool, arguments)
            assert repeated["issues"] == first["issues"] and len(source.calls) == before
            cross = await call_data(mcp, "search_issues", {"jql": "project = DL AND labels = alpha",
                "max_results": 10, "include_description": True, "include_comments": True, "detail": "summary"})
            assert cross["commentsMissingKeys"] == []
            assert all(len(row["fields"]["comment"]["comments"]) == 25 for row in cross["issues"])
            assert [call["kind"] for call in source.calls[before:]] == ["metadata"]
            assert all(call["params"]["maxResults"] <= 10 and len(call["matchedKeys"]) <= 10
                       for call in source.calls if call["kind"] != "metadata")

    anyio.run(scenario)


@pytest.mark.parametrize("first_field,second_kind", [("include_description", "comments"), ("include_comments", "description")])
def test_each_content_field_has_independent_cache_eligibility(build_env, first_field, second_kind):
    env = build_env()
    source = ContentSource(env, total=3)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            arguments = {"jql": "project = DL", "max_results": 10, "include_description": False, "include_comments": False}
            await call_data(mcp, "search_issues", {**arguments, first_field: True})
            assert [call["kind"] for call in source.calls] == ["metadata", "description" if first_field == "include_description" else "comments"]
            before = len(source.calls)
            both = await call_data(mcp, "search_issues", {**arguments, "include_description": True, "include_comments": True})
            assert both["commentsMissingKeys"] == [] and both["descriptionMissingKeys"] == []
            assert [call["kind"] for call in source.calls[before:]] == [second_kind]
            before = len(source.calls)
            opted_out = await call_data(mcp, "search_issues", arguments)
            assert all("comment" not in row["fields"] and "description" not in row["fields"] for row in opted_out["issues"])
            assert all(call["kind"] == "metadata" for call in source.calls[before:])

    anyio.run(scenario)


@pytest.mark.parametrize("shape,expected,missing", [
    ("complete", coverage(25, 25, complete=True), False),
    ("partial", coverage(40, 25, complete=False, more=True), False),
    ("empty", coverage(0, 0, complete=True), False),
    ("bare_array", coverage(None, 25, complete=False, start=None, more=None), False),
    ("bare_empty", coverage(None, 0, complete=False, start=None, more=None), False),
    ("null", coverage(None, 0, complete=False, start=None, more=None), True),
    ("omitted", coverage(None, 0, complete=False, start=None, more=None), True),
    ("invalid", coverage(None, 0, complete=False, start=None, more=None), True),
])
def test_raw_comment_rows_and_coverage_never_invent_completeness_or_cap_at_twenty(build_env, shape, expected, missing):
    env = build_env()
    source = ContentSource(env, total=1)
    raw = source.rows["DL-0001"]["fields"]["comment"]
    if shape == "partial":
        raw["total"] = 40
    elif shape == "empty":
        raw.update(total=0, maxResults=0, comments=[])
    elif shape == "bare_array":
        raw = raw["comments"]
    elif shape == "bare_empty":
        raw = []
    elif shape == "null":
        raw = None
    elif shape == "omitted":
        raw = OMITTED
    elif shape == "invalid":
        raw = {"total": 1, "startAt": 0, "comments": "redacted"}
    source.rows["DL-0001"]["fields"]["comment"] = raw

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await call_data(mcp, "search_issues", {"jql": "project = DL", "include_comments": True})
            assert result["commentsRequested"] is True
            assert result["commentsMissingKeys"] == (["DL-0001"] if missing else [])
            issue = result["issues"][0]
            assert_coverage(issue["commentsCoverage"], expected)
            if raw is not OMITTED:
                assert issue["fields"]["comment"] == raw
            else:
                assert "comment" not in issue["fields"]
            if shape == "partial" and issue["commentsCoverage"].get("nextCall") is not None:
                next_call = issue["commentsCoverage"]["nextCall"]
                assert next_call["name"] == "get_evidence_page"
                assert next_call["arguments"]["key"] == "DL-0001"
                assert next_call["arguments"]["kind"] == "comments"
                assert next_call["arguments"]["start_at"] == 0

    anyio.run(scenario)


def test_get_ticket_explicit_content_is_one_search_and_preserves_all_raw_comments(build_env):
    env = build_env()
    source = ContentSource(env, total=1)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await call_data(mcp, "get_ticket", {"key": "DL-0001", "detail": "read",
                "include_description": True, "include_comments": True})
            assert "source-start:" in result["descriptionHtml"]
            assert result["comments"] == source.rows["DL-0001"]["fields"]["comment"]["comments"]
            assert result["commentsRequested"] is True and result["commentsMissingKeys"] == []
            assert_coverage(result["commentsCoverage"], coverage(25, 25, complete=True))
            assert [call["kind"] for call in source.calls] == ["combined"]
            before = len(source.calls)
            shared = await call_data(mcp, "search_issues", {"jql": "project = DL AND labels = alpha",
                "include_description": True, "include_comments": True})
            assert shared["issues"][0]["fields"]["comment"]["comments"] == result["comments"]
            assert [call["kind"] for call in source.calls[before:]] == ["metadata"]
            before = len(source.calls)
            excluded = await call_data(mcp, "get_ticket", {"key": "DL-0001", "detail": "full",
                "include_description": False, "include_comments": False})
            assert not {"description", "descriptionHtml", "descriptionEditHtml", "descriptionSections", "descriptionFormat"}.intersection(excluded)
            assert "comments" not in excluded
            assert excluded["key"] == "DL-0001" and excluded["summary"] == "DL-0001"
            assert all(call["kind"] == "metadata" for call in source.calls[before:])

    anyio.run(scenario)


@pytest.mark.parametrize("raw", [OMITTED, None, {"comments": "redacted", "total": 1, "startAt": 0}])
def test_get_ticket_missing_comments_are_unknown_empty_list_not_complete(build_env, raw):
    env = build_env()
    source = ContentSource(env, total=1)
    source.rows["DL-0001"]["fields"]["comment"] = raw

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await call_data(mcp, "get_ticket", {"key": "DL-0001", "include_description": False, "include_comments": True})
            assert result["comments"] == []
            assert result["commentsRequested"] is True and result["commentsMissingKeys"] == ["DL-0001"]
            assert_coverage(result["commentsCoverage"], coverage(None, 0, complete=False, start=None, more=None))

    anyio.run(scenario)


@pytest.mark.parametrize("detail", ["full", "summary", "read", "edit"])
def test_get_ticket_omitted_and_null_description_options_preserve_existing_detail_contract(build_env, detail):
    env = build_env()
    created = env.provider._client.post("/rest/api/2/issue", json={"fields": {
        "project": {"key": "DL"}, "issuetype": {"name": "Task"}, "summary": "detail defaults",
        "description": "preserve this description"}})
    assert created.status_code == 201
    key = created.json()["key"]

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            default = await call_data(mcp, "get_ticket", {"key": key, "detail": detail})
            explicit_null = await call_data(mcp, "get_ticket", {"key": key, "detail": detail, "include_description": None})
            assert explicit_null == default
            assert "comments" not in default
            if detail == "summary":
                assert "descriptionHtml" not in default and "descriptionEditHtml" not in default
            elif detail == "edit":
                assert "preserve this description" in default["descriptionEditHtml"]
                assert "descriptionHtml" not in default
            else:
                assert "preserve this description" in default["descriptionHtml"]
                assert ("descriptionEditHtml" in default) is (detail == "full")

    anyio.run(scenario)


def test_combined_content_revision_change_live_validation_and_mutation_fence(build_env):
    env = build_env()
    source = ContentSource(env, total=3)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            args = {"jql": "project = DL", "include_description": True, "include_comments": True}
            await call_data(mcp, "search_issues", args)
            fields = source.rows["DL-0002"]["fields"]
            fields["updated"] = "2026-11-18T11:00:00.000+0900"
            fields["description"] = "new description revision"
            fields["comment"]["comments"][0]["body"] = "new comment revision"
            before = len(source.calls)
            changed = await call_data(mcp, "search_issues", {**args, "jql": "project = DL AND labels = alpha"})
            calls = source.calls[before:]
            assert [call["kind"] for call in calls] == ["metadata", "combined"]
            assert calls[1]["matchedKeys"] == ["DL-0002"]
            row = next(row for row in changed["issues"] if row["key"] == "DL-0002")
            assert row["fields"]["comment"]["comments"][0]["body"] == "new comment revision"
            before = len(source.calls)
            result = await mcp.call_tool("search_issues", {**args, "cache_mode": "live"})
            assert not result.is_error, result.structured_content
            assert result.structured_content["cache"]["compliant"] is True
            assert result.structured_content["cache"]["upstreamValidated"] is True
            assert [call["kind"] for call in source.calls[before:]] == ["metadata", "combined"]

            def mutation():
                source.before_content = None
                env.client.advance_jql_generation()

            source.before_content = mutation
            failed = await mcp.call_tool("search_issues", {**args, "cache_mode": "live"})
            assert failed.is_error and failed.structured_content["error"]["code"] == "query_state_changed"
            assert failed.structured_content.get("data") is None

    anyio.run(scenario)


def test_combined_output_budget_refuses_large_comments_plus_description_without_truncation(build_env):
    env = build_env()
    source = ContentSource(env, total=1, comments=1)
    fields = source.rows["DL-0001"]["fields"]
    fields["description"] = "d" * (9 * 1024 * 1024)
    fields["comment"]["comments"][0]["body"] = "c" * (9 * 1024 * 1024)

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            failed = await mcp.call_tool("get_tickets", {"keys": ["DL-0001"], "include_description": True, "include_comments": True})
            assert failed.is_error and failed.structured_content.get("data") is None
            assert "16 MiB" in failed.structured_content["error"]["message"]
            comments_only = await call_data(mcp, "get_tickets", {"keys": ["DL-0001"], "include_comments": True})
            raw = comments_only["issues"][0]["fields"]["comment"]["comments"][0]["body"]
            assert raw == fields["comment"]["comments"][0]["body"]
            assert "description" not in comments_only["issues"][0]["fields"]
            description_only = await call_data(mcp, "get_tickets", {"keys": ["DL-0001"], "include_description": True})
            assert description_only["issues"][0]["fields"]["description"] == fields["description"]
            before = len(source.calls)
            warm_combined = await mcp.call_tool("get_tickets", {"keys": ["DL-0001"],
                "include_description": True, "include_comments": True})
            assert warm_combined.is_error and warm_combined.structured_content.get("data") is None
            assert "16 MiB" in warm_combined.structured_content["error"]["message"]
            assert len(source.calls) == before, "cached size refusal must not refetch or truncate either field"

    anyio.run(scenario)


@pytest.mark.parametrize("detail", ["read", "edit", "summary", "full"])
def test_rich_description_omission_is_an_explicit_error_and_raw_body_stays_available(build_env, detail):
    env = build_env()
    source = ContentSource(env, total=1, comments=0)
    original = "source-start:" + "x" * (600 * 1024) + ":source-end"
    source.rows["DL-0001"]["fields"]["description"] = original

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("get_ticket", {"key": "DL-0001", "detail": detail,
                                                      "include_description": True})
            assert result.is_error
            payload = result.structured_content
            assert not payload["ok"] and "data" not in payload
            error = payload["error"]
            assert error["code"] == "response_too_large" and error["status"] == 413
            assert error["limitBytes"] == env.client.UI_DESCRIPTION_MAX_BYTES
            assert "512 KiB" in error["message"] and "get_tickets" in error["message"]
            assert "16 MiB" not in error["message"]
            # The real ticket_view applied its UI-only display guard. That must
            # neither fabricate an empty MCP body nor shrink the shared raw cache.
            raw = await call_data(mcp, "get_tickets", {"keys": ["DL-0001"], "include_description": True})
            assert raw["issues"][0]["fields"]["description"] == original
    anyio.run(scenario)


def test_real_jira820_search_projects_raw_comments_like_its_comment_endpoint(build_env):
    env = build_env()
    transport = env.provider._client
    created = transport.post("/rest/api/2/issue", json={"fields": {"project": {"key": "DL"},
        "issuetype": {"name": "Task"}, "summary": "optional real comment parity", "description": "real source description"}})
    assert created.status_code == 201
    key = created.json()["key"]
    for body in ("real comment one", "real comment two"):
        assert transport.post(f"/rest/api/2/issue/{key}/comment", json={"body": body}).status_code == 201
    page = transport.get("/rest/api/2/search", params={"jql": "key = " + key, "fields": "summary,updated,description,comment"}).json()
    fields = page["issues"][0]["fields"]
    assert "comment" in fields, "installed jira820 omits requested comment search fields; fixture capability must be fixed explicitly"
    direct = transport.get(f"/rest/api/2/issue/{key}/comment").json()
    assert fields["comment"]["comments"] == direct["comments"]
    assert fields["comment"]["total"] == direct["total"] == 2


def test_optional_content_ten_returned_of_hundred_rest_ledger_and_bounded_memory(build_env, record_property):
    env = build_env()
    source = ContentSource(env, total=100)
    for row in source.rows.values():
        row["fields"]["description"] = row["key"] + ":" + "d" * (512 * 1024)
    ledger = {}

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            await mcp.list_tools()
            for phase, args in [
                ("cold", {"jql": "project = DL", "include_description": True, "include_comments": True}),
                ("warm", {"jql": "project = DL", "include_description": True, "include_comments": True}),
                ("cross_query", {"jql": "project = DL AND labels = alpha", "include_description": True, "include_comments": True}),
            ]:
                before, started = len(source.calls), perf_counter()
                tracemalloc.start()
                try:
                    page = await call_data(mcp, "search_issues", {**args, "max_results": 10})
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                assert page["returned"] == 10 and page["total"] == 100
                assert page["commentsMissingKeys"] == [] and page["descriptionMissingKeys"] == []
                assert all(row["fields"]["description"] == source.rows[row["key"]]["fields"]["description"]
                           and len(row["fields"]["comment"]["comments"]) == 25 for row in page["issues"])
                calls = source.calls[before:]
                ledger[phase] = {"rest": len(calls), "metadata": sum(call["kind"] == "metadata" for call in calls),
                    "description": sum(call["kind"] == "description" for call in calls),
                    "comments": sum(call["kind"] == "comments" for call in calls),
                    "combined": sum(call["kind"] == "combined" for call in calls),
                    "peakBytes": peak, "elapsedMs": round((perf_counter() - started) * 1000)}
                assert peak < 160 * 1024 * 1024, "one ten-ticket result must not materialize all hundred large bodies"
            assert [(ledger[phase]["rest"], ledger[phase]["metadata"], ledger[phase]["combined"])
                    for phase in ("cold", "warm", "cross_query")] == [(2, 1, 1), (0, 0, 0), (1, 1, 0)]

    anyio.run(scenario)
    record_property("optional_ticket_content_ledger", json.dumps(ledger, sort_keys=True))
    print("OPTIONAL_TICKET_CONTENT_LEDGER=" + json.dumps(ledger, sort_keys=True))


def test_optional_content_complete_hundred_ticket_rest_and_mcp_call_ledger(build_env, record_property):
    env = build_env()
    source = ContentSource(env, total=100)
    requested = [f"DL-{index:04}" for index in range(1, 101)]
    ledger = {}

    def verify_rows(rows):
        assert [row["key"] for row in rows] == requested
        for row in rows:
            assert row["fields"]["description"] == source.rows[row["key"]]["fields"]["description"]
            assert row["fields"]["comment"] == source.rows[row["key"]]["fields"]["comment"]
            assert len(row["fields"]["comment"]["comments"]) == 25
            assert row["commentsCoverage"]["complete"] is True

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            await mcp.list_tools()
            for phase in ("cold", "warm"):
                before, started = len(source.calls), perf_counter()
                result = await mcp.call_tool("get_tickets", {"keys": requested,
                    "include_description": True, "include_comments": True})
                assert not result.is_error, result.structured_content
                batch = result.structured_content["data"]
                assert batch["complete"] and batch["returned"] == 100
                assert batch["commentsMissingKeys"] == batch["descriptionMissingKeys"] == batch["missingKeys"] == []
                verify_rows(batch["issues"])
                calls = source.calls[before:]
                ledger[phase] = {"rest": len(calls), "mcp": 1,
                    "metadata": sum(call["kind"] == "metadata" for call in calls),
                    "combined": sum(call["kind"] == "combined" for call in calls),
                    "dataBytes": encoded_size(batch),
                    "sdkResultBytes": encoded_size(result.model_dump(mode="json", by_alias=True, exclude_none=True)),
                    "elapsedMs": round((perf_counter() - started) * 1000)}
            before, started, rows, offset, mcp_calls = len(source.calls), perf_counter(), [], 0, 0
            data_bytes = sdk_bytes = 0
            while True:
                result = await mcp.call_tool("search_issues", {"jql": "project = DL AND labels = alpha",
                    "include_description": True, "include_comments": True, "max_results": 10, "start_at": offset})
                assert not result.is_error, result.structured_content
                page = result.structured_content["data"]
                data_bytes += encoded_size(page)
                sdk_bytes += encoded_size(result.model_dump(mode="json", by_alias=True, exclude_none=True))
                mcp_calls += 1
                rows.extend(page["issues"])
                if not page["hasMore"]:
                    break
                assert page["nextStartAt"] == offset + page["returned"]
                offset = page["nextStartAt"]
            verify_rows(rows)
            calls = source.calls[before:]
            ledger["cross_query"] = {"rest": len(calls), "mcp": mcp_calls,
                "metadata": sum(call["kind"] == "metadata" for call in calls),
                "combined": sum(call["kind"] == "combined" for call in calls),
                "dataBytes": data_bytes, "sdkResultBytes": sdk_bytes,
                "elapsedMs": round((perf_counter() - started) * 1000)}
            assert [(ledger[phase]["rest"], ledger[phase]["metadata"], ledger[phase]["combined"], ledger[phase]["mcp"])
                    for phase in ("cold", "warm", "cross_query")] == [(11, 1, 10, 1), (0, 0, 0, 1), (1, 1, 0, 10)]
            assert all(call["params"]["maxResults"] <= 10 and len(call["matchedKeys"]) <= 10
                       for call in source.calls if call["kind"] != "metadata")

    anyio.run(scenario)
    record_property("optional_complete_hundred_ledger", json.dumps(ledger, sort_keys=True))
    print("OPTIONAL_COMPLETE_HUNDRED_LEDGER=" + json.dumps(ledger, sort_keys=True))
