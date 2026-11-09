"""Cold-session recovery fault injection is testable without any model execution."""
import json
import subprocess
import sys
from types import SimpleNamespace
from threading import Event, Timer

import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from tools.codex_mcp_eval.recovery_process import interruptible_process
from tools.codex_mcp_eval.work_recovery import RecoveryController, RECOVERY_SCENARIOS, persisted_calls
from tools.codex_mcp_eval.research_files import ResearchFiles


def test_interruptible_process_stops_and_retains_stdout():
    stop = Event()
    timer = Timer(0.4, stop.set)
    timer.start()
    try:
        result = interruptible_process([sys.executable, "-u", "-c", "import time; print('retained'); time.sleep(10)"],
            stop_event=stop, input="", encoding="utf-8", errors="replace", capture_output=True, timeout=3, shell=False)
    finally:
        timer.cancel()
    assert result.intentional_interruption and result.returncode != 0
    assert "retained" in result.stdout


def test_persisted_calls_exclude_unsaved_evidence_and_failed_reads(tmp_path):
    calls = [{"name": "get_ticket", "arguments": {"key": "DL-1"},
              "result": {"ok": True, "data": {"key": "DL-1", "description": "real"}}},
             {"name": "get_ticket", "arguments": {"key": "DL-2"},
              "result": {"ok": True, "data": {"key": "DL-2", "description": "unsaved"}}}]
    files = ResearchFiles(tmp_path)
    files.write_file("evidence/one.json", json.dumps(calls[0]["result"]))
    files.write_file("evidence/fake.json", '{"key":"DL-2","description":"invented"}')
    assert persisted_calls(files, calls) == calls[:1]


@pytest.mark.parametrize("scenario", RECOVERY_SCENARIOS)
def test_each_recovery_fault_has_an_observable_trigger(tmp_path, scenario):
    controller = RecoveryController(scenario)
    files = ResearchFiles(tmp_path, after_write=controller.after_write)
    controller.files = files
    files.write_file("manifest.json", '{"schemaVersion":1}')
    data = {"key": "DL-1", "kind": "comments", "startAt": 0, "snapshotId": "old", "hasMore": True, "nextStartAt": 2,
            "items": [{"recordJson": "part", "nextRecordOffset": 10}] if scenario == "record-chunk" else []}
    response = {"ok": True, "data": data}
    controller.observe_ltm("get_evidence_page", {"key": "DL-1", "kind": "comments", "start_at": 0}, response)
    files.write_file("evidence/page.json", json.dumps(response))
    files.write_file("progress.json", '{"generation":1,"key":"DL-1","kind":"comments","snapshotId":"old","nextStartAt":2}')
    if scenario == "after-response":
        assert not controller.stop.is_set()
        controller.observe_ltm("get_evidence_page", {"key": "DL-1", "start_at": 2},
                               {"ok": True, "data": {"key": "DL-1", "startAt": 2, "items": []}})
    assert controller.stop.is_set() and controller.trigger


@pytest.mark.parametrize("shape", ["nextArguments", "nextCall", "arguments", "direct"])
def test_saved_search_cursor_triggers_between_pages_for_client_progress_shapes(tmp_path, shape):
    controller = RecoveryController("between-pages")
    files = ResearchFiles(tmp_path, after_write=controller.after_write)
    controller.files = files
    query = "project = DL ORDER BY key ASC"
    page = {"snapshotId": "observed", "hasMore": True, "nextStartAt": 2, "issues": []}
    response = {"ok": True, "data": page}
    controller.observe_ltm("search_issues", {"jql": query, "start_at": 0}, response)
    cursor = {"jql": query, "start_at": 2, "snapshot_id": "observed", "max_results": 2}
    progress = cursor if shape == "direct" else {shape: {"name": "search_issues", "arguments": cursor}
                                                 if shape == "nextCall" else cursor}
    progress = {"generation": 1, "collection": {"search": progress}}
    files.write_file("progress.json", json.dumps(progress))
    assert not controller.stop.is_set(), "a cursor without saved source evidence cannot trigger"
    files.write_file("evidence/search-0.json", json.dumps(response))
    files.write_file("progress-000002.json", json.dumps(progress))
    assert controller.stop.is_set() and controller.trigger["reason"] == "page checkpoint saved"


@pytest.mark.parametrize("mismatch", ["jql", "snapshot_id", "start_at"])
def test_checkpoint_must_match_the_saved_search_query_snapshot_and_cursor(tmp_path, mismatch):
    controller = RecoveryController("between-pages")
    files = ResearchFiles(tmp_path, after_write=controller.after_write)
    controller.files = files
    query = "project = DL ORDER BY key ASC"
    response = {"ok": True, "data": {"snapshotId": "observed", "hasMore": True, "nextStartAt": 2}}
    controller.observe_ltm("search_issues", {"jql": query, "start_at": 0}, response)
    files.write_file("evidence/search.json", json.dumps(response))
    cursor = {"jql": query, "start_at": 2, "snapshot_id": "observed"}
    cursor[mismatch] = 4 if mismatch == "start_at" else "another"
    files.write_file("progress.json", json.dumps({"arguments": cursor}))
    assert not controller.stop.is_set()


@pytest.mark.parametrize("name,missing", [("search_issues", "jql"), ("search_issues", "snapshot_id"),
                                         ("get_evidence_page", "key"), ("get_evidence_page", "kind")])
def test_same_offset_in_an_unbound_collection_is_not_a_source_checkpoint(tmp_path, name, missing):
    controller = RecoveryController("between-pages")
    files = ResearchFiles(tmp_path, after_write=controller.after_write)
    controller.files = files
    arguments = ({"jql": "project = DL", "start_at": 0} if name == "search_issues"
                 else {"key": "DL-1", "kind": "comments", "start_at": 0})
    identity = {"snapshotId": "actual"} if name == "search_issues" else {"key": "DL-1", "kind": "comments"}
    response = {"ok": True, "data": {**identity, "hasMore": True, "nextStartAt": 2}}
    controller.observe_ltm(name, arguments, response)
    files.write_file("evidence/source.json", json.dumps(response))
    cursor = {**arguments, "start_at": 2}
    if name == "search_issues":
        cursor["snapshot_id"] = "actual"
    del cursor[missing]
    files.write_file("progress.json", json.dumps({"collection": {"unrelated": {"nextArguments": cursor}}}))
    assert not controller.stop.is_set(), "same offset alone does not bind a cursor to the saved source"


@pytest.mark.parametrize("scenario", RECOVERY_SCENARIOS)
@pytest.mark.parametrize("mode", ["uninterrupted", "restarted"])
def test_real_mcp_cold_restart_and_controlled_source_changes(tmp_path, monkeypatch, scenario, mode):
    import anyio
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER
    from tools.codex_mcp_eval import work_cases
    from tools.codex_mcp_eval.recovery_fixture import RecoveryFixture
    from tools.codex_mcp_eval.recovery_runner import run_recovery_case
    monkeypatch.setenv(NETWORK_PREFLIGHT_MARKER, "1")
    query = ('labels = work_research_fixture AND key in '
             '(DL-94001,DL-94002,DL-94006,DL-94007,DL-94017,DL-94018) ORDER BY key ASC')
    def fake_cli(binary, prompt, workspace, **kwargs):
        async def exercise():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + kwargs["token"]}) as http:
                async with Client(streamable_http_client(kwargs["mcp_url"], http_client=http)) as jira:
                    async with Client(streamable_http_client(kwargs["files_url"], http_client=http)) as files:
                        async def save(path, content):
                            result = await files.call_tool("write_file", {"path": path, "content": json.dumps(content, ensure_ascii=False)})
                            assert not result.is_error
                        def stopped():
                            return kwargs.get("stop_event") is not None and kwargs["stop_event"].is_set()
                        listing = (await files.call_tool("list_files", {})).structured_content
                        old_snapshot = None
                        for row in listing["files"]:
                            page = await files.call_tool("read_file", {"path": row["path"]})
                            text = page.structured_content["content"]
                            if row["path"] == "progress.json":
                                try:
                                    old_snapshot = json.loads(text).get("snapshotId")
                                except ValueError:
                                    pass
                        await jira.call_tool("get_content_guide", {"skill": "ltm-work-reporting"})
                        if old_snapshot:
                            rejected = await jira.call_tool("search_issues", {"jql": query, "snapshot_id": old_snapshot, "start_at": 2, "max_results": 2})
                            assert rejected.is_error, "a cold cache must not reuse the old snapshot"
                        await save("manifest.json", {"schemaVersion": 1, "question": prompt.splitlines()[0]})
                        start, snapshot, keys = 0, None, []
                        while True:
                            response = await jira.call_tool("search_issues", {"jql": query, "start_at": start,
                                "max_results": 2, "snapshot_id": snapshot})
                            assert not response.is_error
                            data = response.structured_content["data"]
                            await save(f"evidence/search-{start}.json", response.structured_content)
                            snapshot = data.get("snapshotId")
                            keys.extend(row["key"] for row in data["issues"])
                            await save("progress.json", {"generation": start + 1, "collection": {"search": {
                                "nextArguments": {"jql": query, "snapshot_id": snapshot,
                                                  "start_at": data.get("nextStartAt"), "max_results": 2}}}})
                            if stopped():
                                return None
                            if not data["hasMore"]:
                                break
                            start = data["nextStartAt"]
                        tickets = {}
                        for key in keys:
                            response = await jira.call_tool("get_ticket", {"key": key, "detail": "read"})
                            assert not response.is_error
                            tickets[key] = response.structured_content["data"]
                            await save(f"evidence/{key}.json", response.structured_content)
                        start, offset = 0, 0
                        while True:
                            response = await jira.call_tool("get_evidence_page", {"key": "DL-94006", "start_at": start,
                                "kind": "comments", "max_results": 1 if offset else 100, "record_offset": offset})
                            assert not response.is_error
                            if stopped():
                                return None
                            page = response.structured_content["data"]
                            await save(f"evidence/comments-{start}-{offset}.json", response.structured_content)
                            if stopped():
                                return None
                            await save("progress.json", {"generation": start + 100, "key": "DL-94006",
                                                        "kind": "comments", "nextStartAt": page.get("nextStartAt")})
                            if not page["hasMore"]:
                                break
                            item = page["items"][-1]
                            start, offset = page["nextStartAt"], item.get("nextRecordOffset") or 0
                        search = await jira.call_tool("search_confluence_documents", {"title": "운영 결정"})
                        identifier = search.structured_content["data"]["documents"][0]["id"]
                        response = await jira.call_tool("read_confluence_document", {"page": identifier})
                        assert not response.is_error
                        await save("evidence/document.json", response.structured_content)
                        count = 1 if "실행 전 취소" in tickets["DL-94002"]["descriptionHtml"] else 2
                        return {"reply": "prod.dw.customers 신규 실적, 취소·기간 밖 제외. " + " ".join(keys),
                            "resultKeys": sorted(keys), "reportedTotal": count, "progress": None, "operations": [],
                            "sources": [{"kind": "ticket", "id": "DL-94001", "url": tickets["DL-94001"]["url"]}]}
        answer = anyio.run(exercise)
        return {"ok": answer is not None, "reply": answer["reply"] if answer else "",
                "structuredAnswer": answer, "intentionalInterruption": answer is None,
                "events": [], "usage": {}, "durationSeconds": 1}
    def fixture(path, **kwargs):
        return RecoveryFixture(path, **{**kwargs, "port": 0})
    options = SimpleNamespace(date="2026-11-09", model="stub", effort="stub", timeout=10,
        skill_transport="fallback", recovery_scenario=scenario, recovery_mode=mode)
    case = next(c for c in work_cases.CASES if c["id"] == "WORK-R02-boundary")
    record = run_recovery_case(case, options, "stub", tmp_path / "run", fake_cli, work_cases,
                               artifact_root=tmp_path, fixture_factory=fixture)
    assert record["automaticPass"], record["automaticContract"]
    assert len(record["phases"]) == (2 if mode == "restarted" else 1)
    assert bool(record["recovery"]["trigger"]) == (mode == "restarted")
    if mode == "uninterrupted" or scenario == "source-changed":
        assert record["recovery"]["eligiblePersistedCalls"] == 0
    else:
        assert record["recovery"]["eligiblePersistedCalls"] > 0
    assert record["output"]["structuredAnswer"]["reportedTotal"] == (1 if scenario == "source-changed" else 2)
    assert not list(tmp_path.rglob("jira-cache.sqlite3"))
    assert not list(tmp_path.rglob("mcp-secret.json"))
