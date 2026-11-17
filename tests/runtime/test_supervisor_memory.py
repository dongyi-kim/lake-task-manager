"""Bound disposable Windows child jobs without creating system memory pressure."""

import os
import sys
import time

import pytest

from app.launcher import supervisor


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows job commit limits only")
LIMIT = 64 * 1024 * 1024


def _limits(worker):
    import win32job
    return win32job.QueryInformationJobObject(worker._job, win32job.JobObjectExtendedLimitInformation)


def _await_exit(worker):
    deadline = time.monotonic() + 5
    while worker.poll() is None and time.monotonic() < deadline:
        time.sleep(.01)
    assert worker.poll() is not None
    return worker.poll()


def test_owned_windows_job_has_commit_limit_before_child_runs(tmp_path):
    import win32job
    worker = supervisor.OwnedWorker(
        [sys.executable, "-B", "-c", "import time; time.sleep(10)"],
        str(tmp_path), dict(os.environ), memory_limit=LIMIT)
    try:
        info = _limits(worker)
        assert info["BasicLimitInformation"]["LimitFlags"] & win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
        assert info["JobMemoryLimit"] == LIMIT
        assert not worker.owns_pid(os.getpid())
    finally:
        worker.close()


def test_clean_exit_removes_commit_limit_for_legitimate_updater(tmp_path):
    import win32api
    import win32con
    import win32job

    worker = supervisor.OwnedWorker(
        [sys.executable, "-B", "-c", "pass"], str(tmp_path), dict(os.environ), memory_limit=LIMIT)
    job = win32api.DuplicateHandle(win32api.GetCurrentProcess(), worker._job,
        win32api.GetCurrentProcess(), 0, False, win32con.DUPLICATE_SAME_ACCESS)
    try:
        assert _await_exit(worker) == 0
        worker.close(clean_exit=True)
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        assert not info["BasicLimitInformation"]["LimitFlags"] & (
            win32job.JOB_OBJECT_LIMIT_JOB_MEMORY | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    finally:
        worker.close()
        win32api.CloseHandle(job)


def test_child_quota_failure_recovers_without_allocating_in_supervisor(tmp_path):
    import win32job

    counter, release = tmp_path / "starts", tmp_path / "release"
    code = '''
import pathlib, sys, time
counter, release = map(pathlib.Path, sys.argv[1:])
generation = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(generation))
if generation == 1:
    while not release.exists(): time.sleep(.01)
    try:
        buffer = bytearray(128 * 1024 * 1024)
    except MemoryError:
        sys.exit(73)
    sys.exit(74)
'''
    children, events = [], []

    def factory(argv, cwd, env):
        worker = supervisor.OwnedWorker(argv, cwd, env, memory_limit=LIMIT)
        children.append(worker)
        try:
            # Never authorize the allocation until the kernel-enforced limit is proved.
            info = _limits(worker)
            assert info["BasicLimitInformation"]["LimitFlags"] & win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
            assert info["JobMemoryLimit"] == LIMIT
            assert not worker.owns_pid(os.getpid())
            release.write_text("quota verified")
            return worker
        except BaseException:
            worker.close()
            raise

    try:
        result = supervisor.run_supervisor(
            [sys.executable, "-B", "-c", code, str(counter), str(release)],
            cwd=str(tmp_path), env=dict(os.environ), base_url="unused", worker_factory=factory,
            probe=lambda *_: True, interval=.01, record=lambda event, **fields: events.append((event, fields)),
            report=lambda _: None)
        assert result == 0 and len(children) == 2
        assert counter.read_text() == "2"
        recovered = [fields for event, fields in events if event == "worker_recovery"]
        assert len(recovered) == 1 and recovered[0]["exitCode"] == 73
    finally:
        for worker in children:
            worker.close()
