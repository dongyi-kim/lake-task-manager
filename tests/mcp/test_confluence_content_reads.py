"""Bounded MCP evidence and complete local binary download contracts."""

import json
from urllib.parse import urlsplit

import anyio
import httpx2
import pytest
from mcp import Client

from app.routes.confluence_download import build_confluence_download_router
from app.services.ticket_commands import CommandError
from tests.mcp.test_confluence_commands import ConfluenceFixture, commands


def test_comment_read_pins_source_version_and_never_truncates_without_cursor(commands):
    command, _ = commands
    original = "<p>" + "가나다라" * 6000 + "</p>"
    created = command.create_comment(page_id="10", body=original, mutation_id="read-comment-001")
    first = command.read_comment(page_id="10", comment_id=created["id"], max_chars=8000)
    assert first["hasMore"] and 0 < first["nextOffset"] < 8000 and first["version"] == 1
    assert len(first["content"].encode("utf-8")) <= 8000
    command.update_comment(page_id="10", comment_id=created["id"], expected_version=1,
                           body="<p>new published version</p>", mutation_id="read-comment-002")
    chunks, offset = [first["content"]], first["nextOffset"]
    while offset is not None:
        result = command.read_comment(page_id="10", comment_id=created["id"], version=1, offset=offset, max_chars=8000)
        chunks.append(result["content"])
        offset = result["nextOffset"]
    assert "".join(chunks) == original
    with pytest.raises(CommandError):
        command.read_comment(page_id="10", comment_id=created["id"], offset=8000)
    with pytest.raises(CommandError):
        command.read_comment(page_id="10", comment_id=created["id"], max_chars=8001)


def test_attachment_continuation_uses_historical_version_after_new_upload(commands, monkeypatch):
    command, upstream = commands
    created = command.upload_attachment(page_id="10", filename="image.png", data=b"old bytes", mutation_id="historical-001")
    first = command.read_attachment(page_id="10", attachment_id=created["id"], max_bytes=4)
    upstream.items[created["id"]]["version"] = {"number": 2}
    upstream.files["image.png"] = b"new bytes"
    seen = []
    def get_bytes(url):
        seen.append(url)
        return (b"old bytes" if "version=1" in url else b"new bytes"), "image/png"
    monkeypatch.setattr(upstream, "get_bytes", get_bytes)
    second = command.read_attachment(page_id="10", attachment_id=created["id"], version=first["version"], offset=4)
    assert second["version"] == 1 and second["base64"] == "Ynl0ZXM="
    assert "version=1" in seen[-1]


def test_comment_unicode_byte_limit_keeps_character_cursor_and_full_content(commands):
    command, _ = commands
    original = "<p>" + "한글👩‍💻🚀" * 2500 + "</p>"
    comment = command.create_comment(page_id="10", body=original, mutation_id="unicode-comment-01")
    offset, chunks = 0, []
    while offset is not None:
        item = command.read_comment(page_id="10", comment_id=comment["id"], version=1, offset=offset, max_chars=8000)
        assert len(item["content"].encode("utf-8")) <= 8000
        assert "\ufffd" not in item["content"]
        chunks.append(item["content"])
        if item["hasMore"]:
            assert item["nextOffset"] == offset + len(item["content"])
        offset = item["nextOffset"]
    assert "".join(chunks) == original


def test_large_attachment_sdk_preview_is_small_and_http_download_is_complete(build_env):
    env, upstream = build_env(), ConfluenceFixture()
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.runtime.settings.confluence_base = "https://docs.test/wiki"
    env.client._provider = upstream
    data = bytes(range(256)) * 2048
    upstream.items["11"] = {"id": "11", "type": "attachment", "title": "large.bin",
        "container": {"id": "10"}, "space": {"key": "DL"}, "version": {"number": 1},
        "extensions": {"fileSize": len(data)}, "_links": {"download": "/download/attachments/10/large.bin"}}
    upstream.files["large.bin"] = data
    env.app.include_router(build_confluence_download_router(get_client=lambda: env.client))

    async def scenario():
        async with Client(env.runtime.server) as sdk:
            response = await sdk.call_tool("read_confluence_attachment", {"page_id": "10", "attachment_id": "11"})
            assert not response.is_error, response.structured_content
            body = response.structured_content["data"]
            assert body["nextOffset"] == 1024 and body["totalBytes"] == len(data)
            assert len(json.dumps(response.structured_content)) < 8000
            maximum = await sdk.call_tool("read_confluence_attachment", {"page_id": "10", "attachment_id": "11", "max_bytes": 4096})
            assert not maximum.is_error
            assert len(maximum.structured_content["data"]["base64"]) <= 5464
            too_big = await sdk.call_tool("read_confluence_attachment", {"page_id": "10", "attachment_id": "11", "max_bytes": 4097})
            assert too_big.is_error
            url = body["downloadUrl"]
            assert url.startswith("http://127.0.0.1:4457/") and "version=1" in url
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app, client=("127.0.0.1", 1234)),
                                      base_url="http://127.0.0.1:4457") as http:
            full = await http.get(url)
            assert full.status_code == 200 and full.content == data
            assert full.headers["content-disposition"].startswith("attachment;")
            assert full.headers["x-content-type-options"] == "nosniff"
            assert full.headers["cache-control"] == "private, no-store"
            count = len(upstream.gets)
            assert (await http.get(url, headers={"Origin": "https://evil.test"})).status_code == 403
            assert (await http.get(url, headers={"Host": "evil.test:4457"})).status_code == 403
            assert len(upstream.gets) == count
            mismatch = await http.get(urlsplit(url).path, params={"version": 2})
            assert mismatch.status_code == 409
            upstream.items["10"]["space"] = {"key": "OTHER"}
            assert (await http.get(url)).status_code == 403
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app, client=("10.0.0.2", 1234)),
                                      base_url="http://127.0.0.1:4457") as remote:
            assert (await remote.get(url)).status_code == 403
    anyio.run(scenario)


def test_comment_full_source_tool_is_registered_and_bounded(build_env):
    env, upstream = build_env(), ConfluenceFixture()
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.runtime.settings.confluence_base = "https://docs.test/wiki"
    env.client._provider = upstream
    upstream.items["12"] = {"id": "12", "type": "comment", "container": {"id": "10"},
        "space": {"key": "DL"}, "version": {"number": 1}, "body": {"storage": {"value": "<p>" + "a" * 16000 + "</p>"}}}
    async def scenario():
        async with Client(env.runtime.server) as sdk:
            result = await sdk.call_tool("read_confluence_comment", {"page_id": "10", "comment_id": "12"})
            assert not result.is_error, result.structured_content
            assert result.structured_content["data"]["nextOffset"] == 4000
            assert len(json.dumps(result.structured_content)) < 8000
    anyio.run(scenario)
