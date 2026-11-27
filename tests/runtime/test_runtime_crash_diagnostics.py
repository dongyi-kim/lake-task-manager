"""Durable crash evidence contains structure, never exception payloads or frame locals."""
import asyncio
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.infra import runtime_diagnostics as diagnostics
from app.routes.runtime_diagnostics import build_runtime_diagnostics_router


def test_unhandled_hooks_preserve_previous_handlers_and_omit_payloads(tmp_path, monkeypatch):
    chained = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: chained.append("main"))
    monkeypatch.setattr(threading, "excepthook", lambda args: chained.append("thread"))
    monkeypatch.setattr(sys, "unraisablehook", lambda args: chained.append("unraisable"))
    cleanup = diagnostics.install_exception_hooks(tmp_path, fatal=False)
    try:
        try:
            secret_local = "password=private-business-description"
            raise ValueError(secret_local)
        except ValueError as error:
            sys.excepthook(type(error), error, error.__traceback__)
            threading.excepthook(SimpleNamespace(exc_type=type(error), exc_value=error,
                exc_traceback=error.__traceback__, thread=threading.current_thread()))
            sys.unraisablehook(SimpleNamespace(exc_type=type(error), exc_value=error,
                exc_traceback=error.__traceback__))
        sys.excepthook(SystemExit, SystemExit(0), None)
        saved = (tmp_path / "failures.jsonl").read_text(encoding="utf-8")
        records = [json.loads(line) for line in saved.splitlines()]
        assert [row["component"] for row in records] == ["main", "thread", "unraisable"]
        assert all(row["errorType"] == "ValueError" and row["frames"] for row in records)
        assert secret_local not in saved and str(Path.home()) not in saved
        assert chained == ["main", "thread", "unraisable", "main"]
    finally:
        cleanup()


def test_async_exception_handler_records_once_and_restores_previous(tmp_path):
    loop = asyncio.new_event_loop()
    seen = []
    original = lambda loop, context: seen.append(context)
    loop.set_exception_handler(original)
    cleanup = diagnostics.install_async_exception_handler(loop, directory=tmp_path)
    try:
        loop.call_exception_handler({"exception": RuntimeError("private-token"), "message": "private body"})
        saved = (tmp_path / "failures.jsonl").read_text(encoding="utf-8")
        assert "RuntimeError" in saved and "private" not in saved
        assert len(seen) == 1
    finally:
        cleanup()
        assert loop.get_exception_handler() is original
        loop.close()


@pytest.mark.parametrize("previously_enabled", [False, True])
def test_fatal_capture_keeps_descriptor_open_and_restores_previous_state(tmp_path, monkeypatch, previously_enabled):
    import faulthandler
    calls = []
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: previously_enabled)
    monkeypatch.setattr(faulthandler, "enable", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(faulthandler, "disable", lambda: calls.append("disabled"))
    cleanup = diagnostics.install_exception_hooks(tmp_path)
    try:
        descriptor = calls[0]["file"]
        assert not descriptor.closed and calls[0]["all_threads"]
        assert descriptor.name.endswith(".log")
    finally:
        cleanup()
    assert descriptor.closed
    assert calls[-1] == ({"all_threads": True} if previously_enabled else "disabled")


def test_real_mcp_loop_callback_failure_is_persisted_and_handler_restored(tmp_path):
    from app.local_mcp.worker_lifecycle import WorkerLifecycle

    worker = WorkerLifecycle([], diagnostics_directory=tmp_path)
    worker.monitor_interval = .01
    completed = threading.Event()

    def broken_callback():
        raise RuntimeError("private-callback-token")

    async def scenario():
        async with worker.lifespan():
            mcp_loop = worker.current.loop
            assert mcp_loop is not asyncio.get_running_loop()
            assert callable(mcp_loop.get_exception_handler())
            mcp_loop.call_soon_threadsafe(broken_callback)
            # The barrier runs after the callback's synchronous exception handler.
            mcp_loop.call_soon_threadsafe(completed.set)
            assert await asyncio.wait_for(asyncio.to_thread(completed.wait, 2), timeout=3)
            saved = (tmp_path / "failures.jsonl").read_text(encoding="utf-8")
            records = [json.loads(line) for line in saved.splitlines()]
            assert len(records) == 1 and records[0]["component"] == "asyncio"
            assert records[0]["errorType"] == "RuntimeError"
            assert any(frame["function"] == "broken_callback" for frame in records[0]["frames"])
            assert "private-callback-token" not in saved
        assert mcp_loop.is_closed() and mcp_loop.get_exception_handler() is None

    asyncio.run(scenario())


def test_record_allowlist_and_size_bound_preserve_numeric_resource_evidence(tmp_path):
    log = diagnostics.RuntimeLog(tmp_path, "worker")
    log.record("runtime_stalled", resources={"processBytes": 123, "availableBytes": 456,
        "busy": True, "prompt": "secret"}, errorType="ValueError", message="secret",
        frames=[{"file": "C:/Users/private/repo/app/main.py", "line": 42,
                 "function": "handler", "locals": "secret"}] * 10000)
    log.close()
    saved = (tmp_path / "worker.jsonl").read_bytes()
    value = json.loads(saved)
    assert len(saved) <= diagnostics.MAX_RECORD_BYTES
    assert value["resources"] == {"processBytes": 123, "availableBytes": 456, "busy": True}
    assert "secret" not in saved.decode() and "private" not in saved.decode()
    assert value["frames"][0]["file"] == "app/main.py"


def _client(directory):
    app = FastAPI()
    app.include_router(build_runtime_diagnostics_router(directory=directory, port=4457))
    return TestClient(app, base_url="http://127.0.0.1:4457", client=("127.0.0.1", 1234))


def test_browser_downloads_only_allowlisted_sanitized_bounded_evidence(tmp_path):
    (tmp_path / "worker.jsonl").write_text(json.dumps({"at": "2026-11-28T00:00:00+00:00",
        "event": "worker_started", "pid": 12, "message": "private description"}) + "\ninvalid\n", encoding="utf-8")
    (tmp_path / "jira_state.json").write_text("private token", encoding="utf-8")
    (tmp_path / "fatal-worker.log").write_text('Fatal Python error: Aborted\n\nCurrent thread 0x123:\n'
        '  File "C:/Users/private/repo/app/main.py", line 42 in handler\nprivate payload\n', encoding="utf-8")
    with _client(tmp_path) as client:
        response = client.get("/api/app/diagnostics")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        state = response.json()
        assert {row["name"] for row in state["files"]} == {"worker.jsonl", "fatal-worker.log"}
        assert "private" not in response.text and state["records"]
        download = client.get("/api/app/diagnostics/worker.jsonl/download")
        assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
        assert "private" not in download.text
        fatal = client.get("/api/app/diagnostics/fatal-worker.log/download")
        assert "Aborted" in fatal.text and "app/main.py" in fatal.text and "private" not in fatal.text
        assert client.get("/api/app/diagnostics/jira_state.json/download").status_code == 404
        assert client.get("/api/app/diagnostics", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/app/diagnostics", headers={"Host": "evil.example:4457"}).status_code == 403
        assert client.get("/api/app/diagnostics", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_missing_directory_is_empty_and_symlink_is_never_downloaded(tmp_path):
    with _client(tmp_path / "missing") as client:
        assert client.get("/api/app/diagnostics").json()["records"] == []
    target = tmp_path / "outside"
    target.write_text("private", encoding="utf-8")
    link = tmp_path / "worker.jsonl"
    try:
        link.symlink_to(target)
    except OSError:
        return  # Windows without link permission still exercises missing-directory behavior.
    with _client(tmp_path) as client:
        assert client.get("/api/app/diagnostics/worker.jsonl/download").status_code == 404


def test_browser_recovery_stream_is_listed_and_sanitized_for_download(tmp_path):
    log = diagnostics.RuntimeLog(tmp_path, "browser")
    log.record("browser_recovery_started", reason="renderer_crash", restartCount=2, delaySeconds=2,
               resources={"processBytes": 123, "jobBytes": 456}, message="private-cookie")
    log.record("browser_recovery_completed", restartCount=2)
    log.close()
    # Old/corrupt extra fields must be filtered on export too, not just on write.
    with (tmp_path / "browser.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps({"event": "browser_recovery_failed", "message": "private-cookie",
            "frames": [{"file": "C:/Users/private/repo/app/main.py", "line": 42, "function": "handler"}]}) + "\n")
    with _client(tmp_path) as client:
        state = client.get("/api/app/diagnostics")
        assert {row["name"] for row in state.json()["files"]} == {"browser.jsonl"}
        assert len(state.json()["records"]) == 3 and "private" not in state.text
        download = client.get("/api/app/diagnostics/browser.jsonl/download")
        assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
        records = [json.loads(line) for line in download.text.splitlines()]
        assert records[0]["resources"] == {"processBytes": 123, "jobBytes": 456}
        assert records[0]["restartCount"] == 2 and records[1]["event"] == "browser_recovery_completed"
        assert records[2]["frames"][0]["file"] == "app/main.py" and "private" not in download.text
