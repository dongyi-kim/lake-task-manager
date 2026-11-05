"""Real MCP SDK + shared HTTP fixture, including immutable revision reads."""
from datetime import date
import json

import anyio
import pytest
from mcp import Client

from app.mock.confluence import install_confluence


def documents(env):
    env.runtime.settings.confluence_base = "http://confluence.test"
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.store.confluence = {"u01": [
        {"title": "Runbook", "space": "DL", "body": "first revision", "date": date.today(), "ancestors": ["Guides"]},
        {"title": "Independent root", "space": "DL", "body": "standalone", "date": date.today()},
        {"title": "Secret", "space": "OTHER", "body": "private", "date": date.today()}]}
    jira = env.provider._client.app
    install_confluence(jira)
    state = jira.state.confluence
    state.seed()
    by_title = {row["title"]: row["id"] for row in state.pages.values()}
    return state, by_title


def test_sdk_tree_revision_and_pinned_body_share_ui_cache(build_env):
    env = build_env()
    state, ids = documents(env)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            async def call(name, args):
                result = await mcp.call_tool(name, args)
                assert not result.is_error, result.structured_content
                return result.structured_content["data"]
            spaces = await call("list_confluence_spaces", {})
            assert [r["key"] for r in spaces["results"]] == ["DL"]
            roots = await call("list_confluence_pages", {"space_key": "DL"})
            assert {r["title"] for r in roots["results"]} == {"Guides", "Independent root"}
            children = await call("list_confluence_pages", {"parent_id": ids["Guides"]})
            assert [r["id"] for r in children["results"]] == [ids["Runbook"]]
            assert children["results"][0]["ancestors"][0]["id"] == ids["Guides"]
            env.client.confluence_page(ids["Runbook"], expand="body.storage,space,version")
            before = len(env.calls)
            first = await call("read_confluence_document", {"page": ids["Runbook"], "max_chars": 5})
            assert len(env.calls) == before
            response = env.provider._client.put("/rest/api/content/" + ids["Runbook"], json={
                "version": {"number": 2}, "body": {"storage": {"value": "<p>different new version</p>"}}})
            assert response.status_code == 200
            revision = await call("check_confluence_revision", {"page": ids["Runbook"], "known_version": 1})
            assert revision["latestPublishedVersion"] == 2 and revision["isLatest"] is False
            second = await call("read_confluence_document", {"page": ids["Runbook"], "offset": first["nextOffset"], "version": 1})
            assert first["content"] + second["content"] == "first revision"
            new = await call("read_confluence_document", {"page": ids["Runbook"]})
            assert new["content"] == "different new version"
            denied = await mcp.call_tool("read_confluence_document", {"page": ids["Secret"]})
            assert denied.is_error and "private" not in str(denied.structured_content)
    anyio.run(scenario)


def test_exact_cql_search_and_activity_use_configured_confluence_host(build_env):
    env = build_env()
    _, ids = documents(env)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("search_confluence_documents", {
                "cql": 'title = "Runbook" OR space = "OTHER" ORDER BY title ASC'})
            assert not result.is_error, result.structured_content
            assert [r["id"] for r in result.structured_content["data"]["documents"]] == [ids["Runbook"]]
            result = await mcp.call_tool("search_confluence_documents", {"parent_id": ids["Guides"]})
            assert [r["id"] for r in result.structured_content["data"]["documents"]] == [ids["Runbook"]]
    anyio.run(scenario)
    activity = env.client._fetch_confluence("u01")
    assert activity and env.calls[-1][1].startswith("http://confluence.test/rest/api/content/search")


def test_dc_fixture_preserves_versions_storage_comments_attachments_and_restrictions(build_env):
    env = build_env()
    state, ids = documents(env)
    http, cid = env.provider._client, ids["Runbook"]
    source = '<ac:structured-macro ac:name="info"><ac:rich-text-body><table><tbody><tr><td>table</td></tr></tbody></table></ac:rich-text-body></ac:structured-macro>'
    updated = http.put(f"/rest/api/content/{cid}", json={"title": "Renamed", "version": {"number": 2},
                       "body": {"storage": {"value": source, "representation": "storage"}}})
    assert updated.status_code == 200 and updated.json()["id"] == cid
    assert http.put(f"/rest/api/content/{cid}", json={"version": {"number": 2}}).status_code == 409
    old = http.get(f"/rest/api/content/{cid}", params={"version": 1, "expand": "body.storage"}).json()
    assert old["title"] == "Runbook" and old["body"]["storage"]["value"] == "<p>first revision</p>"
    assert updated.json()["body"]["storage"]["value"] == source
    comment = http.post("/rest/api/content", json={"type": "comment", "container": {"id": cid},
                       "body": {"storage": {"value": "<p>A comment</p>"}}}).json()
    assert http.put(f"/rest/api/content/{comment['id']}", json={"version": {"number": 2},
                    "body": {"storage": {"value": "<p>Edited</p>"}}}).status_code == 200
    assert state.get(cid)["version"]["number"] == 2
    attached = http.post(f"/rest/api/content/{cid}/child/attachment", files={"file": ("tiny.txt", b"data", "text/plain")})
    attachment = attached.json()["results"][0]
    assert http.get(attachment["_links"]["download"]).content == b"data"
    assert http.post(f"/rest/api/content/{cid}/label", json=[{"prefix": "global", "name": "runbook"}]).status_code == 200
    assert http.get(f"/rest/api/content/{cid}/label").json()["results"][0]["name"] == "runbook"
    changed = http.put(f"/rest/api/content/{cid}/restriction", json=[{"operation": "update", "restrictions": {"user": [{"username": "u01"}], "group": []}}])
    assert changed.status_code == 200 and "read" in changed.json()
    state.denied.add(cid)
    assert http.get(f"/rest/api/content/{cid}").status_code == 403


@pytest.mark.parametrize("symbol", ["x", "🧪"], ids=["ascii", "multibyte"])
def test_comment_context_bounds_large_bodies_and_paginates_without_omission(build_env, symbol):
    env = build_env()
    state, ids = documents(env)
    page = ids["Runbook"]
    comments = []
    for index in range(55):
        body = "<p>" + (symbol * 500000 if index == 0 else f"comment {index} " + symbol * 1000) + "</p>"
        row = state.create({"type": "comment", "container": {"id": page},
                            "body": {"storage": {"value": body}},
                            "version": {"number": 1, "by": {"displayName": "Editor", "username": "u01",
                                                           "avatarUrls": {"large": "unused" * 100000}}}})
        comments.append(row["id"])
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            seen, start = [], 0
            while True:
                response = await mcp.call_tool("get_confluence_context", {
                    "page": page, "kind": "comment", "start_at": start, "max_results": 50})
                assert not response.is_error, response.structured_content
                data = response.structured_content["data"]
                assert len(json.dumps(data, ensure_ascii=False).encode("utf-8")) <= 8000
                assert data["results"] and data["returned"] < 50
                for item in data["results"]:
                    assert item["format"] == "text-preview" and item["bodyTruncated"]
                    assert len(item["preview"]) <= 400
                    assert "body" not in item and "avatarUrls" not in item["version"]["by"]
                if start == 0:
                    assert data["results"][0]["bodyLength"] == 500007
                seen.extend(item["id"] for item in data["results"])
                if not data["hasMore"]:
                    break
                assert data["nextStartAt"] == start + data["returned"]
                start = data["nextStartAt"]
            assert seen == comments and len(seen) == len(set(seen))
    anyio.run(scenario)


def test_mock_does_not_invent_unrequested_ancestor_expansion(build_env):
    env = build_env()
    _, ids = documents(env)
    http = env.provider._client
    path = "/rest/api/content/" + ids["Runbook"]
    unexpanded = http.get(path, params={"expand": "body.storage,space,version"}).json()
    assert "ancestors" not in unexpanded
    expanded = http.get(path, params={"expand": "body.storage,space,version,ancestors"}).json()
    assert [row["id"] for row in expanded["ancestors"]] == [ids["Guides"]]
