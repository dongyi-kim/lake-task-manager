"""Supervisor policy and owned disposable process trees, without Jira or an app server."""

import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

from app.launcher import supervisor


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class Worker:
    def __init__(self, pid, poll):
        self.pid, self._poll = pid, poll
        self.closed = []

    def poll(self):
        return self._poll()

    def close(self, *, clean_exit=False):
        self.closed.append(clean_exit)


def test_crash_retry_cap_preserves_command_and_uses_fresh_identity(tmp_path):
    created, messages, failure_notices = [], [], []
    clock = Clock()
    argv, env = [sys.executable, "run.py", "--tray"], {"APP_PORT": "4457", "KEEP": "value"}

    def factory(command, cwd, child_env):
        worker = Worker(len(created) + 1, lambda: 17)
        created.append((worker, command, cwd, child_env))
        return worker

    result = supervisor.run_supervisor(argv, cwd=str(tmp_path), env=env, base_url="http://unused",
        worker_factory=factory, clock=clock, sleep=clock.sleep, report=messages.append,
        on_failure=lambda: failure_notices.append(len(messages)))
    assert result == 1 and len(created) == 4
    assert all(row[0].closed == [False] for row in created)
    assert all(row[1:3] == (argv, str(tmp_path)) for row in created)
    assert all({key: value for key, value in row[3].items() if key != supervisor.WORKER_ENV} == env
               for row in created)
    assert len({row[3][supervisor.WORKER_ENV] for row in created}) == 4
    assert supervisor.WORKER_ENV not in env
    assert "recovery limit reached" in messages[-1]
    assert failure_notices == [3]


def test_spawn_failure_still_reports_when_showing_console_fails(tmp_path):
    events = []
    def spawn(*_):
        raise OSError("spawn failed")
    def notice():
        events.append("notice")
        raise OSError("no console")
    assert supervisor.run_supervisor(["worker"], cwd=str(tmp_path), env={}, base_url="http://unused",
        worker_factory=spawn, on_failure=notice, report=events.append) == 1
    assert events[0] == "notice" and "spawn failed" in events[1]


def test_startup_grace_and_three_misses_then_normal_quit(tmp_path):
    clock, workers, failure_notices = Clock(), [], []

    def factory(*args):
        worker = Worker(len(workers) + 1, (lambda: None) if not workers else (lambda: 0))
        workers.append(worker)
        return worker

    result = supervisor.run_supervisor(["worker"], cwd=str(tmp_path), env={}, base_url="http://unused",
        worker_factory=factory, clock=clock, sleep=clock.sleep, probe=lambda *_: False, report=lambda _: None,
        on_failure=lambda: failure_notices.append(True))
    assert result == 0 and clock.now == 70
    assert [worker.closed for worker in workers] == [[False], [True]]
    assert failure_notices == []


def test_health_resets_misses_and_removes_startup_grace_after_ready(tmp_path):
    clock, ready, workers = Clock(), [], []
    checks = iter([True, False, False, True, False, False, False])

    def factory(*args):
        worker = Worker(len(workers) + 1, (lambda: None) if not workers else (lambda: 0))
        workers.append(worker)
        return worker

    assert supervisor.run_supervisor(["worker"], cwd=str(tmp_path), env={}, base_url="http://unused",
        worker_factory=factory, clock=clock, sleep=clock.sleep, probe=lambda *_: next(checks),
        on_ready=lambda: ready.append(clock.now), report=lambda _: None) == 0
    assert clock.now == 30 and ready == [0]


def test_rolling_restart_budget_expires(tmp_path):
    clock, workers = Clock(), []

    def factory(*args):
        index = len(workers)
        def poll():
            clock.now += 601
            return 17 if index < 5 else 0
        worker = Worker(index + 1, poll)
        workers.append(worker)
        return worker

    assert supervisor.run_supervisor(["worker"], cwd=str(tmp_path), env={}, base_url="http://unused",
        worker_factory=factory, clock=clock, sleep=clock.sleep, report=lambda _: None) == 0
    assert len(workers) == 6


@pytest.mark.parametrize("payload,expected", [
    ({"status": "ok", "pid": 123, "instance": "ours"}, True),
    ({"status": "ok", "pid": 124, "instance": "ours"}, False),
    ({"status": "ok", "pid": 123, "instance": "old"}, False),
    ({"status": "degraded", "pid": 123, "instance": "ours"}, False),
    ([], False),
])
def test_probe_requires_owned_identity_and_bypasses_proxies(monkeypatch, payload, expected):
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            assert size == 4097
            return json.dumps(payload).encode()

    class Opener:
        def open(self, url, timeout):
            assert url == "http://127.0.0.1:4457/api/health/live" and timeout == 2
            return Response()

    def build_opener(proxy):
        assert proxy.proxies == {}
        return Opener()

    monkeypatch.setattr(supervisor.urllib.request, "build_opener", build_opener)
    worker = SimpleNamespace(owns_pid=lambda pid: pid == 123)
    assert supervisor.probe_worker("http://127.0.0.1:4457/", worker, "ours") is expected


def test_actual_http_liveness_accepts_owned_interpreter_and_rejects_other_process(tmp_path):
    address = tmp_path / "port"
    code = '''
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json, os, sys
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "pid": os.getpid(), "instance": "ours"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
server = HTTPServer(("127.0.0.1", 0), Handler)
Path(sys.argv[1]).write_text(str(server.server_port))
server.serve_forever()
'''
    worker = supervisor.OwnedWorker([sys.executable, "-B", "-c", code, str(address)],
                                    str(tmp_path), dict(os.environ))
    try:
        deadline = time.monotonic() + 5
        while not address.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        url = "http://127.0.0.1:" + address.read_text()
        assert supervisor.probe_worker(url, worker, "ours")
        assert not supervisor.probe_worker(url, worker, "old-generation")
        assert not worker.owns_pid(os.getpid())
    finally:
        worker.close()


def test_owned_process_crash_restarts_and_normal_quit_does_not(tmp_path):
    counter = tmp_path / "starts.txt"
    code = ("from pathlib import Path; import sys; p=Path(sys.argv[1]); "
            "n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); sys.exit(17 if n==1 else 0)")
    messages = []
    assert supervisor.run_supervisor([sys.executable, "-B", "-c", code, str(counter)],
        cwd=str(tmp_path), env=dict(os.environ), base_url="http://unused", probe=lambda *_: True,
        interval=0.01, report=messages.append) == 0
    assert counter.read_text() == "2" and len(messages) == 1


@pytest.mark.parametrize("crash", [False, True])
def test_owned_tree_cleanup_includes_descendants_after_hang_or_crash(tmp_path, crash):
    ready, release, finished = [tmp_path / name for name in ("ready", "release", "finished")]
    child = (
        "import pathlib,time,sys; r,f,d=map(pathlib.Path,sys.argv[1:]); r.write_text('ready'); "
        "\nwhile not f.exists(): time.sleep(.01)"
        "\nd.write_text('orphan survived')"
    )
    parent = (
        "import subprocess,sys,pathlib,time,os; "
        "subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],*sys.argv[2:5]]); "
        "\nwhile not pathlib.Path(sys.argv[2]).exists(): time.sleep(.01)"
        + ("\nos._exit(17)" if crash else "\nwhile True: time.sleep(1)")
    )
    observed_ready = []
    def probe(*_):
        if not ready.exists():
            return False
        observed_ready.append(True)
        return len(observed_ready) == 1

    try:
        assert supervisor.run_supervisor(
            [sys.executable, "-B", "-c", parent, child, str(ready), str(release), str(finished)],
            cwd=str(tmp_path), env=dict(os.environ), base_url="http://unused", probe=probe,
            interval=0.01, startup_grace=5, restart_limit=0, report=lambda _: None) == 1
        assert ready.exists()
        release.write_text("finish if still alive")
        time.sleep(0.2)
        assert not finished.exists()
    finally:
        release.write_text("allow cleanup if assertion failed")


def test_clean_exit_preserves_updater_descendant(tmp_path):
    ready, release, finished = [tmp_path / name for name in ("ready", "release", "finished")]
    descendant = (
        "import pathlib,time,sys; r,f,d=map(pathlib.Path,sys.argv[1:]); r.write_text('ready'); "
        "\nwhile not f.exists(): time.sleep(.01)"
        "\nd.write_text('updated')"
    )
    parent = (
        "import subprocess,sys,pathlib,time; "
        "subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],*sys.argv[2:]]); "
        "\nwhile not pathlib.Path(sys.argv[2]).exists(): time.sleep(.01)"
    )
    try:
        assert supervisor.run_supervisor(
            [sys.executable, "-B", "-c", parent, descendant, str(ready), str(release), str(finished)],
            cwd=str(tmp_path), env=dict(os.environ), base_url="http://unused", probe=lambda *_: True,
            interval=0.01, report=lambda _: None) == 0
    finally:
        release.write_text("update may finish")
    deadline = time.monotonic() + 3
    while not finished.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert finished.read_text() == "updated"
