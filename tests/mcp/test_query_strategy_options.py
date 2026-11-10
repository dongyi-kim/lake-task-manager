"""Public strategy options bind real search calls and cursor identity."""
import anyio
from jsonschema import Draft202012Validator
from mcp import Client


def test_mcp_strategy_budget_and_continuation(build_env):
    env = build_env()
    keys = [env.client.create_child(None, "Task", f"strategy option {index}")["key"] for index in range(2)]

    async def scenario():
        async with Client(env.runtime.server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            for name in ("search_issues", "get_tickets"):
                schema = tools[name].input_schema["properties"]
                assert set(schema["strategy"]["enum"]) == {"auto", "off", "people", "full"}
                assert schema["max_parts"]["minimum"] == 1 and schema["max_parts"]["maximum"] == 64
            args = {"jql": " OR ".join(f"key = {key}" for key in keys), "strategy": "full", "max_parts": 1, "max_results": 1}
            env.calls.clear()
            result = await client.call_tool("search_issues", args)
            assert not result.is_error, result.structured_content
            Draft202012Validator(tools["search_issues"].output_schema).validate(result.structured_content)
            page = result.structured_content["data"]
            assert page["total"] == 2 and page["execution"]["physicalQueryCount"] == 1
            assert page["execution"]["effectiveStrategy"] == "off"
            assert len([call for call in env.calls if call[1] == "/rest/api/2/search"]) == 1
            before = len(env.calls)
            continuation = {**args, "start_at": 1, "snapshot_id": page["snapshotId"]}
            continued = await client.call_tool("search_issues", continuation)
            assert not continued.is_error and len(env.calls) == before
            assert continued.structured_content["data"]["execution"]["physicalQueryCount"] == 0
            changed = await client.call_tool("search_issues", {**continuation, "strategy": "off"})
            assert changed.is_error and len(env.calls) == before
            for bad in (True, 0, 65, "1"):
                rejected = await client.call_tool("search_issues", {**args, "max_parts": bad})
                assert rejected.is_error and len(env.calls) == before
            batch = await client.call_tool("get_tickets", {
                "keys": keys, "strategy": "off", "max_parts": 1})
            assert not batch.is_error, batch.structured_content
            Draft202012Validator(tools["get_tickets"].output_schema).validate(batch.structured_content)
            assert batch.structured_content["data"]["execution"]["maxParts"] == 1
    anyio.run(scenario)
