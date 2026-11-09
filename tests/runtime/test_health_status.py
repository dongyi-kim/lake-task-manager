"""Tray health is readable without Jira and its dialog cannot hold the tray pump."""
import ast
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from app.launcher.health_status import app_responding, format_health_status


def test_app_probe_is_bounded_and_fenced_to_our_process_and_instance(monkeypatch):
    from app.launcher import supervisor
    monkeypatch.setenv(supervisor.WORKER_ENV, "our-instance")
    calls = []
    def probe(url, owner, instance, timeout):
        assert owner.owns_pid(os.getpid()) and not owner.owns_pid(os.getpid() + 1)
        calls.append((url, instance, timeout))
        return False
    monkeypatch.setattr(supervisor, "probe_worker", probe)
    assert not app_responding(4457)
    assert calls == [("http://127.0.0.1:4457", "our-instance", 1)]


def test_tray_status_detects_app_loop_outage_even_with_healthy_native_owners():
    tree = ast.parse((Path(__file__).resolve().parents[2] / "run.py").read_text(encoding="utf-8"))
    tray = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_tray")
    snapshot = next(node for node in tray.body if isinstance(node, ast.FunctionDef) and node.name == "health_snapshot")
    namespace = {"server": SimpleNamespace(started=True, should_exit=False),
        "s": SimpleNamespace(app_port=4457), "tray_probe": {},
        "_runtime_status": lambda _: {"healthy": True}, "app_responding": lambda port: False,
        "_appmain": SimpleNamespace(_local_mcp=SimpleNamespace(transport_host=SimpleNamespace(
            health_snapshot=lambda: {"state": "healthy"})))}
    exec(compile(ast.Module(body=[snapshot], type_ignores=[]), "run.py", "exec"), namespace)
    assert namespace["health_snapshot"]()["app"]["state"] == "unresponsive"


def test_health_summary_distinguishes_transport_recovery_from_app_liveness():
    text = format_health_status({"app": {"state": "healthy", "port": 4457}, "mcp": {
        "state": "recovering", "generation": 2, "restartCount": 1,
        "automaticRecovery": True, "lastRecoveryReason": "worker_exited"}})
    assert "앱: 정상" in text and "MCP: 복구 중" in text and "4457" in text
    assert "자동 복구: 켜짐" in text and "최근 복구 사유: 워커 종료" in text
    assert "재기동: 1회" in text
    failed = format_health_status({"app": {"state": "healthy"}, "mcp": {
        "state": "recovery_failed", "automaticRecovery": True, "lastRecoveryReason": "heartbeat_timeout"}})
    assert "MCP: 복구 실패" in failed and "응답 없음" in failed


def test_tray_health_callback_uses_coalesced_background_job():
    tree = ast.parse((Path(__file__).resolve().parents[2] / "run.py").read_text(encoding="utf-8"))
    tray = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_tray")
    callback = next(node for node in tray.body if isinstance(node, ast.FunctionDef) and node.name == "on_health")
    job = next(node for node in tray.body if isinstance(node, ast.FunctionDef) and node.name == "run_tray_job")
    entered, release, opened = threading.Event(), threading.Event(), threading.Event()
    calls = []
    snapshot = {"app": {"state": "healthy"}, "mcp": {"state": "healthy"}}
    def dialog(value):
        calls.append(value)
        entered.set()
        assert release.wait(2)
    namespace = {"threading": threading, "sys": sys, "_tray_jobs": set(),
        "_tray_jobs_lock": threading.Lock(), "show_health_status": dialog,
        "health_snapshot": lambda: snapshot}
    exec(compile(ast.Module(body=[job, callback], type_ignores=[]), "run.py", "exec"), namespace)
    try:
        namespace["on_health"](SimpleNamespace(), None)
        assert entered.wait(1)
        namespace["on_health"](SimpleNamespace(), None)
        namespace["run_tray_job"]("open", opened.set)
        assert opened.wait(1) and calls == [snapshot]
    finally:
        release.set()
