"""Public manifest pages stay small without hiding private recovery evidence."""
import json

import anyio
from mcp import Client
import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from tools.codex_mcp_eval.research_files import ResearchFiles
from tools.codex_mcp_eval.work_recovery import persisted_calls


def _investigation(tmp_path, count):
    root = tmp_path / "investigation"
    root.mkdir()
    # Private investigation artifacts, not repository/source copies. Reverse
    # insertion prevents directory iteration order from satisfying sorted output.
    paths = [f"evidence/{index:03d}-{'근거단계' * 24}.json" for index in range(count)]
    for path in reversed(paths):
        target = root / path
        target.parent.mkdir(exist_ok=True)
        target.write_text("saved evidence", encoding="utf-8")
    return ResearchFiles(root), paths


def _page(result):
    assert not result.is_error
    # Serialize the real SDK response, including both text and structured data,
    # inside a JSON-RPC envelope. A character count alone misses Unicode bytes
    # and the duplicated model-visible payload representations.
    envelope = {"jsonrpc": "2.0", "id": 1,
                "result": result.model_dump(mode="json", by_alias=True, exclude_none=True)}
    assert len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 8192
    data = result.structured_content
    assert isinstance(data, dict)
    assert isinstance(data["files"], list)
    assert type(data["hasMore"]) is bool
    if data["hasMore"]:
        assert isinstance(data["nextCursor"], str) and data["nextCursor"]
    else:
        assert data["nextCursor"] is None
    return data


async def _all_manifest_pages(client):
    arguments, seen_cursors, rows = {}, set(), []
    for _ in range(513):
        data = _page(await client.call_tool("list_files", arguments))
        rows.extend(data["files"])
        if not data["hasMore"]:
            return rows
        assert data["files"], "A nonterminal manifest page must advance"
        cursor = data["nextCursor"]
        assert cursor not in seen_cursors, "Manifest continuation must advance"
        seen_cursors.add(cursor)
        arguments = {"cursor": cursor}
    pytest.fail("Manifest never reached a terminal page")


def test_sdk_manifest_pages_bound_serialized_response_and_preserve_all_512_files(tmp_path):
    # Catches returning the whole directory in one response, skipped files,
    # repeated cursors/rows, or sorting each page independently.
    files, expected_paths = _investigation(tmp_path, 512)

    async def scenario():
        async with Client(files.server) as client:
            rows = await _all_manifest_pages(client)
            assert [row["path"] for row in rows] == expected_paths
            assert [row["size"] for row in rows] == [14] * 512
            tail = await client.call_tool("read_file", {"path": expected_paths[-1], "max_chars": 5})
            assert not tail.is_error
            pieces, offset = [tail.structured_content["content"]], tail.structured_content["nextOffset"]
            while offset is not None:
                tail = await client.call_tool("read_file", {
                    "path": expected_paths[-1], "offset": offset, "max_chars": 5})
                assert not tail.is_error
                pieces.append(tail.structured_content["content"])
                offset = tail.structured_content["nextOffset"]
            assert "".join(pieces) == "saved evidence"

    anyio.run(scenario)


def test_sdk_manifest_rejects_invalid_and_changed_continuations(tmp_path):
    # Catches treating malformed/stale cursors as a successful empty final page,
    # which would silently certify an incomplete inventory during recovery.
    files, expected_paths = _investigation(tmp_path, 40)

    async def scenario():
        async with Client(files.server) as client:
            first = _page(await client.call_tool("list_files", {}))
            assert first["hasMore"]
            invalid = await client.call_tool("list_files", {"cursor": "invalid-manifest-cursor"})
            assert invalid.is_error
            cursor = first["nextCursor"]
            forged = cursor[:-1] + ("1" if cursor[-1] == "0" else "0")
            tampered = await client.call_tool("list_files", {"cursor": forged})
            assert tampered.is_error
            files.write_file("evidence/added.json", "new")
            changed = await client.call_tool("list_files", {"cursor": first["nextCursor"]})
            assert changed.is_error
            restarted = await _all_manifest_pages(client)
            assert [row["path"] for row in restarted] == sorted([*expected_paths, "evidence/added.json"])

    anyio.run(scenario)


def test_public_pagination_preserves_full_internal_inventory_quotas_and_saved_tail_evidence(tmp_path):
    # Catches accidentally applying public pagination to the internal inventory:
    # recovery must discover saved evidence at the end and quotas count all files.
    files, expected_paths = _investigation(tmp_path, 512)
    saved = {"name": "get_ticket", "arguments": {"key": "DL-1"},
             "result": {"ok": True, "data": {"key": "DL-1", "description": "retained tail evidence"}}}
    unsaved = {"name": "get_ticket", "arguments": {"key": "DL-2"},
               "result": {"ok": True, "data": {"key": "DL-2", "description": "never saved"}}}
    files.write_file(expected_paths[-1], json.dumps(saved["result"], ensure_ascii=False))

    async def scenario():
        async with Client(files.server) as client:
            first = _page(await client.call_tool("list_files", {}))
            assert first["hasMore"]
            assert expected_paths[-1] not in [row["path"] for row in first["files"]]

    anyio.run(scenario)
    inventory = files.list_files()["files"]
    assert [row["path"] for row in inventory] == expected_paths
    assert persisted_calls(files, [saved, unsaved]) == [saved]
    with pytest.raises(ValueError, match="storage limit"):
        files.write_file("evidence/overflow.json", "x")
    files.MAX_TOTAL_BYTES = sum(row["size"] for row in inventory)
    with pytest.raises(ValueError, match="storage limit"):
        files.write_file(expected_paths[0], "saved evidence!")
    assert files.read_file(expected_paths[0])["content"] == "saved evidence"
