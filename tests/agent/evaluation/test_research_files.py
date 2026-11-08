"""Evaluation-only file access cannot escape its private investigation directory."""
from hashlib import sha256
import json

import anyio
from mcp import Client
import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from tools.codex_mcp_eval.research_files import ResearchFiles


def test_atomic_write_chunked_read_and_no_access_to_sibling_oracle(tmp_path):
    root = tmp_path / "investigation"
    root.mkdir()
    (tmp_path / "oracle.json").write_text("secret answer", encoding="utf-8")
    files = ResearchFiles(root)
    saved = files.write_file("evidence/page.json", "한글 근거 " * 400)
    content, offset = "", 0
    while offset is not None:
        page = files.read_file("evidence/page.json", offset, 25)
        content += page["content"]
        offset = page["nextOffset"]
    assert saved["digest"] == "sha256:" + sha256(content.encode()).hexdigest()
    assert [r["path"] for r in files.list_files()["files"]] == ["evidence/page.json"]
    with pytest.raises(ValueError):
        files.write_file("progress.json", "x" * (files.MAX_FILE_BYTES + 1))
    assert not (root / "progress.json").exists()


@pytest.mark.parametrize("path", ["../oracle.json", "/oracle.json", "C:/oracle.json", r"C:\oracle.json",
    r"..\oracle.json", "evidence/../../oracle.json", "evidence//x", "./x", "evidence/x:stream", ""])
def test_paths_are_relative_without_traversal_or_windows_streams(tmp_path, path):
    files = ResearchFiles(tmp_path)
    with pytest.raises(ValueError):
        files.write_file(path, "bad")
    with pytest.raises(ValueError):
        files.read_file(path)


def test_failed_atomic_replace_preserves_previous_checkpoint(tmp_path, monkeypatch):
    import tools.codex_mcp_eval.research_files as module
    files = ResearchFiles(tmp_path)
    files.write_file("progress.json", '{"generation":1}')
    def fail(*args):
        raise OSError("simulated interrupted replace")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        files.write_file("progress.json", '{"generation":2}')
    assert json.loads(files.read_file("progress.json")["content"])["generation"] == 1
    assert len(files.list_files()["files"]) == 1


def test_real_sdk_file_tools_are_separate_from_ltm_and_trace_writes(tmp_path):
    files = ResearchFiles(tmp_path)
    async def scenario():
        async with Client(files.server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert names == {"list_files", "read_file", "write_file"}
            result = await client.call_tool("write_file", {"path": "resume.md", "content": "다음 페이지부터 재개"})
            assert not result.is_error
            denied = await client.call_tool("read_file", {"path": "../oracle.json"})
            assert denied.is_error
    anyio.run(scenario)
    assert any(c["name"] == "write_file" and c["ok"] for c in files.calls)


def test_recovery_cli_only_allows_the_explicit_file_server(tmp_path):
    from tools.codex_mcp_eval import cli
    command = cli.command("stub", tmp_path, mcp_url="http://127.0.0.1:4457/mcp",
                          files_url="http://127.0.0.1:4457/research-files")
    assert 'features.shell_tool=false' in command
    assert 'mcp_servers.research_files.url="http://127.0.0.1:4457/research-files"' in command
    event = json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "research_files", "tool": "write_file"}})
    assert cli.parse_events(event)["forbiddenToolEvents"]
    assert not cli.parse_events(event, allow_research_files=True)["forbiddenToolEvents"]
    bad = event.replace('"write_file"', '"execute_shell"')
    assert cli.parse_events(bad, allow_research_files=True)["forbiddenToolEvents"]


def test_hardlinked_host_file_and_storage_quota_are_rejected(tmp_path):
    import os
    root = tmp_path / "private"
    root.mkdir()
    outside = tmp_path / "host.txt"
    outside.write_text("host", encoding="utf-8")
    os.link(outside, root / "linked.txt")
    files = ResearchFiles(root)
    with pytest.raises(ValueError, match="Linked"):
        files.read_file("linked.txt")
    with pytest.raises(ValueError, match="Linked"):
        files.write_file("linked.txt", "changed")
    assert outside.read_text(encoding="utf-8") == "host"
    (root / "linked.txt").unlink()
    files.MAX_TOTAL_BYTES = 5
    files.write_file("one.txt", "12345")
    with pytest.raises(ValueError, match="storage limit"):
        files.write_file("two.txt", "6")
    files.write_file("one.txt", "x")
    assert files.read_file("one.txt")["content"] == "x"
