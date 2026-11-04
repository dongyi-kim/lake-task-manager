"""Real MCP calls use the same strategic query cache and mutation fences as the UI."""

import anyio
from mcp import Client


def test_generic_mcp_or_is_one_request_and_shares_ui_rows_and_frozen_pages(build_env):
    env = build_env()
    created = []
    for index in range(3):
        key = env.client.create_child(None, "Task", f"strategy {index}")["key"]
        env.client.update_fields(key, {"labels": [f"strategy-{index}"]})
        created.append(key)
    query = " OR ".join(f'labels = "strategy-{index}"' for index in range(3))
    scoped_query = f'project in ("DL") AND ({query}) ORDER BY summary ASC'
    env.calls.clear()

    def searches():
        return [call for call in env.calls if call[:2] == ("get", "/rest/api/2/search")]

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            first = await mcp.call_tool("search_issues", {
                "jql": query + " ORDER BY summary ASC", "max_results": 2,
            })
            assert not first.is_error, first.structured_content
            page = first.structured_content["data"]
            assert page["total"] == 3 and page["hasMore"]
            assert [row["key"] for row in page["issues"]] == created[:2]
            assert len(searches()) == 1
            # Membership and canonical light issue data are shared with ordinary UI requests.
            direct = env.client.search_issues_page(scoped_query, max_results=100)
            assert [row["key"] for row in direct["issues"]] == created
            for key in created:
                assert env.client.get_issue_light(key)["key"] == key
            assert len(searches()) == 1
            assert all(env.cache.get(f"issueL:{env.client.env}:{key}") for key in created)

            reordered = await mcp.call_tool("search_issues", {
                "jql": " OR ".join(f'labels = "strategy-{i}"' for i in (2, 0, 1))
                       + " ORDER BY summary ASC", "max_results": 2,
            })
            assert not reordered.is_error
            assert len(searches()) == 1

            changed = await mcp.call_tool("update_fields", {"key": created[2], "body": {
                "summary": "renamed outside old membership", "labels": ["outside-query"],
                "clientMutationId": "query-strategy-update-0001",
            }})
            assert not changed.is_error, changed.structured_content
            # The old cursor stays immutable, while the newly executed query sees the write.
            old = await mcp.call_tool("search_issues", {
                "jql": query + " ORDER BY summary ASC", "start_at": 2,
                "max_results": 2, "snapshot_id": page["snapshotId"],
            })
            assert not old.is_error, old.structured_content
            assert old.structured_content["data"]["issues"][0]["key"] == created[2]
            assert old.structured_content["data"]["issues"][0]["fields"]["summary"] == "strategy 2"
            refreshed = env.client.search_issues_page(scoped_query, max_results=100)
            assert refreshed["total"] == 2
            assert [row["key"] for row in refreshed["issues"]] == created[:2]

    anyio.run(scenario)
