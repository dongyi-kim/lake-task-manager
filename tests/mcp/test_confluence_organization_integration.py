"""Real MCP registration and HTTP fixture validate complete organization workflows."""

import anyio
from mcp import Client
from jsonschema import Draft202012Validator

from app.mock.confluence import install_confluence
from app.local_mcp.schemas.confluence_organization import DATA_MODELS


def setup(env):
    env.runtime.settings.confluence_base = "http://confluence.test"
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.store.confluence = {"u01": []}
    install_confluence(env.provider._client.app)
    return env.provider._client.app.state.confluence


def test_sdk_conditional_bulk_move_restrictions_trash_restore_and_replay(build_env):
    env = build_env()
    state = setup(env)
    async def scenario():
        async with Client(env.runtime.server) as sdk:
            catalog = {tool.name: tool for tool in (await sdk.list_tools()).tools}
            assert set(DATA_MODELS).issubset(catalog)
            async def call(name, args):
                result = await sdk.call_tool(name, args)
                assert not result.is_error, result.structured_content
                Draft202012Validator(catalog[name].output_schema).validate(result.structured_content)
                return result.structured_content["data"]
            async def create(title, parent=None):
                return (await call("create_confluence_page", {"space": "DL", "title": title,
                    "body": "<p>Preserve source</p>", "parent_id": parent, "client_mutation_id": "org:create:" + title}))["id"]
            source, target = await create("Source"), await create("Target")
            child = await create("Child", source)
            await call("list_confluence_pages", {"parent_id": source})
            await call("read_confluence_document", {"page": child})
            operations = [{"operation_id": "rename", "action": "update", "page_id": child,
                           "expected_version": 1, "title": "Renamed"},
                          {"operation_id": "move", "action": "move", "page_id": child,
                           "expected_version": 2, "parent_id": target, "depends_on": ["rename"]}]
            payload = {"operations": operations, "client_mutation_id": "org:bulk:move"}
            preview = await call("bulk_confluence_changes", payload)
            assert preview["dryRun"] and state.get(child)["version"]["number"] == 1
            executed = await call("bulk_confluence_changes", {**payload, "dry_run": False})
            assert all(row["status"] == "completed" for row in executed["results"])
            assert state.get(child)["parent"] == target and state.get(child)["version"]["number"] == 3
            assert state.get(child)["body"]["storage"]["value"] == "<p>Preserve source</p>"
            puts = sum(method == "put" for method, *_ in env.calls)
            await call("bulk_confluence_changes", {**payload, "dry_run": False})
            assert sum(method == "put" for method, *_ in env.calls) == puts
            assert (await call("list_confluence_pages", {"parent_id": source}))["results"] == []
            assert (await call("list_confluence_pages", {"parent_id": target}))["results"][0]["id"] == child
            access = await call("get_confluence_restrictions", {"page_id": child})
            await call("set_confluence_restrictions", {"page_id": child, "expected_fingerprint": access["fingerprint"],
                "restrictions": [{"operation": "read", "users": [], "groups": ["team"]}], "client_mutation_id": "org:access:0001"})
            stale = await sdk.call_tool("set_confluence_restrictions", {"page_id": child, "expected_fingerprint": access["fingerprint"],
                "restrictions": [{"operation": "read", "users": [], "groups": []}], "client_mutation_id": "org:access:stale"})
            assert stale.is_error and stale.structured_content["error"]["code"] == "conflict"
            await call("trash_confluence_document", {"page_id": child, "expected_version": 3, "client_mutation_id": "org:trash:0001"})
            trash = await call("list_confluence_trashed_pages", {"space_key": "DL"})
            assert trash["results"][0]["id"] == child and trash["results"][0]["version"]["number"] == 3
            await call("restore_confluence_document", {"page_id": child, "expected_version": 3, "client_mutation_id": "org:restore:0001"})
            assert state.get(child)["status"] == "current" and state.get(child)["version"]["number"] == 4
    anyio.run(scenario)


def test_sdk_bulk_permission_failure_skips_dependency_and_retries_only_failed_work(build_env):
    env = build_env()
    state = setup(env)
    state.seed()
    one = state.create({"type": "page", "title": "One", "space": {"key": "DL"}, "body": {"storage": {"value": "one"}}})["id"]
    two = state.create({"type": "page", "title": "Two", "space": {"key": "DL"}, "body": {"storage": {"value": "two"}}})["id"]
    state.denied.add(two)
    operations = [{"operation_id": "one", "action": "update", "page_id": one, "title": "One new", "expected_version": 1},
                  {"operation_id": "two", "action": "update", "page_id": two, "title": "Two new", "expected_version": 1},
                  {"operation_id": "move", "action": "move", "page_id": two, "parent_id": one,
                   "expected_version": 2, "depends_on": ["two"]}]
    args = {"operations": operations, "dry_run": False, "client_mutation_id": "org:bulk:partial"}
    async def scenario():
        async with Client(env.runtime.server) as sdk:
            first = await sdk.call_tool("bulk_confluence_changes", args)
            assert first.is_error, first.structured_content
            data = first.structured_content["data"]
            assert data["partial"] and [row["status"] for row in data["results"]] == ["completed", "failed", "skipped"]
            assert data["failed"][0]["code"] == "permission_denied"
            state.denied.remove(two)
            second = await sdk.call_tool("bulk_confluence_changes", args)
            assert not second.is_error, second.structured_content
            assert state.get(one)["version"]["number"] == 2
            assert state.get(two)["version"]["number"] == 3 and state.get(two)["parent"] == one
    anyio.run(scenario)


def test_bulk_recovers_confirmed_confluence_auth_denial_without_repeating_success(build_env):
    from app.auth.base import SessionExpired
    env = build_env()
    state = setup(env)
    state.seed()
    pages = [state.create({"type": "page", "title": title, "space": {"key": "DL"}})["id"] for title in ("One", "Two")]
    original = env.provider.put_json
    rejected = []
    def put(path, *args, **kwargs):
        if path.endswith("/" + pages[1]) and not rejected:
            rejected.append(path)
            raise SessionExpired("Confluence session expired")
        return original(path, *args, **kwargs)
    env.provider.put_json = put
    checks, check = [], env.runtime.authentication.check
    def auth(service="jira", **kwargs):
        checks.append((service, kwargs.get("force", False)))
        return check(service, **kwargs)
    env.runtime.authentication.check = auth
    async def scenario():
        async with Client(env.runtime.server) as sdk:
            result = await sdk.call_tool("bulk_confluence_changes", {"client_mutation_id": "org:bulk:reauth",
                "dry_run": False, "operations": [{"operation_id": str(i), "action": "update", "page_id": cid,
                    "expected_version": 1, "title": "Changed " + cid} for i, cid in enumerate(pages)]})
            assert not result.is_error, result.structured_content
            assert [state.get(cid)["version"]["number"] for cid in pages] == [2, 2]
            assert ("confluence", True) in checks and not any(service == "jira" for service, _ in checks)
    anyio.run(scenario)


def test_sdk_dry_run_moves_all_paginated_children_before_trashing_parent(build_env):
    env = build_env()
    state = setup(env)
    state.seed()
    parent, target = [state.create({"title": title, "space": {"key": "DL"}})["id"]
                      for title in ("Parent", "Target")]
    children = [state.create({"title": title, "space": {"key": "DL"}, "ancestors": [{"id": parent}]})["id"]
                for title in ("Child one", "Child two")]
    original = env.provider.get_json
    def capped_page(path, params=None, **kwargs):
        # Model a server-side page cap while still using the shared HTTP endpoint.
        if path.endswith(f"/content/{parent}/child/page"):
            params = {**(params or {}), "limit": 1}
        return original(path, params=params, **kwargs)
    env.provider.get_json = capped_page
    operations = [{"operation_id": str(i), "action": "move", "page_id": cid,
                   "parent_id": target, "expected_version": 1} for i, cid in enumerate(children)]
    operations.append({"operation_id": "trash", "action": "trash", "page_id": parent,
                       "expected_version": 1, "depends_on": ["0", "1"]})
    args = {"operations": operations, "client_mutation_id": "org:paginated:leaf"}
    async def scenario():
        async with Client(env.runtime.server) as sdk:
            preview = await sdk.call_tool("bulk_confluence_changes", args)
            assert not preview.is_error, preview.structured_content
            assert [row["status"] for row in preview.structured_content["data"]["results"]] == ["validated"] * 3
            assert not any(method in ("put", "delete") for method, *_ in env.calls)
            starts = [kwargs["params"]["start"] for method, path, kwargs in env.calls
                      if method == "get" and path.endswith(f"/content/{parent}/child/page")]
            assert starts == [0, 1]
            result = await sdk.call_tool("bulk_confluence_changes", {**args, "dry_run": False})
            assert not result.is_error, result.structured_content
            assert state.get(parent, status="trashed")["status"] == "trashed"
            assert all(state.get(cid)["parent"] == target for cid in children)
    anyio.run(scenario)


def test_sdk_verified_intermediate_moves_replay_after_receipt_store_restart(build_env, tmp_path):
    from app.infra.cache import Cache
    env = build_env()
    state = setup(env)
    state.seed()
    parent, target = [state.create({"title": title, "space": {"key": "DL"}})["id"]
                      for title in ("Parent", "Target")]
    child = state.create({"title": "Child", "space": {"key": "DL"}, "ancestors": [{"id": parent}]})["id"]
    path = tmp_path / "move-receipts.sqlite3"
    env.client.cache = Cache(path)
    args = {"client_mutation_id": "org:sequential:moves", "dry_run": False, "operations": [
        {"operation_id": "first", "action": "move", "page_id": child, "parent_id": target, "expected_version": 1},
        {"operation_id": "second", "action": "move", "page_id": child, "parent_id": "", "expected_version": 2,
         "depends_on": ["first"]}]}
    async def scenario():
        async with Client(env.runtime.server) as sdk:
            first = await sdk.call_tool("bulk_confluence_changes", args)
            assert not first.is_error, first.structured_content
            assert state.get(child)["parent"] == "" and state.get(child)["version"]["number"] == 3
            puts = sum(method == "put" for method, *_ in env.calls)
            env.client.cache.close()
            env.client.cache = Cache(path)
            env.client._confluence_receipt_stores = {}
            replay = await sdk.call_tool("bulk_confluence_changes", args)
            assert not replay.is_error, replay.structured_content
            assert [row["status"] for row in replay.structured_content["data"]["results"]] == ["completed", "completed"]
            assert sum(method == "put" for method, *_ in env.calls) == puts
    try:
        anyio.run(scenario)
    finally:
        env.client.cache.close()


def test_sdk_pending_move_verification_retries_read_before_dependent_write(build_env):
    from app.auth.base import UpstreamUnavailable
    env = build_env()
    state = setup(env)
    state.seed()
    target, child = [state.create({"title": title, "space": {"key": "DL"}})["id"]
                     for title in ("Target", "Child")]
    original, failed = env.provider.get_json, []
    def read(path, params=None, **kwargs):
        if path.endswith("/content/" + child) and state.get(child)["version"]["number"] == 2 and not failed:
            failed.append(path)
            raise UpstreamUnavailable("verification interrupted")
        return original(path, params=params, **kwargs)
    env.provider.get_json = read
    args = {"client_mutation_id": "org:pending:verification", "dry_run": False, "operations": [
        {"operation_id": "move", "action": "move", "page_id": child, "parent_id": target, "expected_version": 1},
        {"operation_id": "rename", "action": "update", "page_id": child, "title": "Renamed", "expected_version": 2,
         "depends_on": ["move"]}]}
    async def scenario():
        async with Client(env.runtime.server) as sdk:
            first = await sdk.call_tool("bulk_confluence_changes", args)
            assert first.is_error, first.structured_content
            assert [row["status"] for row in first.structured_content["data"]["results"]] == ["verification_pending", "skipped"]
            assert state.get(child)["version"]["number"] == 2
            second = await sdk.call_tool("bulk_confluence_changes", args)
            assert not second.is_error, second.structured_content
            assert state.get(child)["title"] == "Renamed" and state.get(child)["version"]["number"] == 3
            assert sum(method == "put" for method, *_ in env.calls) == 2
    anyio.run(scenario)
