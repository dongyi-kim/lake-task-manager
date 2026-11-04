"""Own one desktop worker and recover boundedly from process crashes or lost liveness."""

from collections import deque
from http.client import HTTPException
import json
import os
import secrets
import signal
import subprocess
import time
import urllib.request


WORKER_ENV = "LAKE_SUPERVISED_WORKER"


class OwnedWorker:
    """An owned Windows job / POSIX session, never a process found by name or port."""

    def __init__(self, argv, cwd, env):
        self._closed = False
        if os.name != "nt":
            self._process = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True)
            self.pid = self._process.pid
            return
        import win32api
        import win32con
        import win32job
        import win32process

        self._job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(self._job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self._job, win32job.JobObjectExtendedLimitInformation, info)
        process = thread = None
        try:
            # Assignment precedes the child's first instruction: even an immediate
            # Chromium spawn or crash cannot leave unowned descendants behind.
            process, thread, self.pid, _ = win32process.CreateProcess(
                argv[0], subprocess.list2cmdline(argv), None, None, False,
                win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW | win32con.CREATE_UNICODE_ENVIRONMENT,
                env, cwd, win32process.STARTUPINFO())
            win32job.AssignProcessToJobObject(self._job, process)
            self._process = process
            win32process.ResumeThread(thread)
        except BaseException:
            if process is not None:
                win32api.TerminateProcess(process, 1)
                win32api.CloseHandle(process)
            win32api.CloseHandle(self._job)
            raise
        finally:
            if thread is not None:
                win32api.CloseHandle(thread)

    def poll(self):
        if os.name != "nt":
            return self._process.poll()
        import win32event
        import win32process

        if win32event.WaitForSingleObject(self._process, 0) == win32event.WAIT_TIMEOUT:
            return None
        return win32process.GetExitCodeProcess(self._process)

    def owns_pid(self, pid):
        """Venv redirectors may host Python in a child; validate owned tree membership."""
        if type(pid) is not int or pid <= 0:
            return False
        if pid == self.pid:
            return True
        try:
            if os.name != "nt":
                return os.getpgid(pid) == self.pid
            import win32api
            import win32job
            process = win32api.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            try:
                return bool(win32job.IsProcessInJob(process, self._job))
            finally:
                win32api.CloseHandle(process)
        except Exception:
            return False

    def close(self, *, clean_exit=False):
        if self._closed:
            return
        self._closed = True
        if os.name != "nt":
            if not clean_exit:
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self._process.wait(timeout=5)
            return
        import win32api
        import win32event
        import win32job

        try:
            if clean_exit:
                # The tray's existing update command may outlive its successful
                # parent exit. Detach only after confirmed exit 0; no recovery is
                # performed and that legitimate updater must be allowed to finish.
                info = win32job.QueryInformationJobObject(self._job, win32job.JobObjectExtendedLimitInformation)
                info["BasicLimitInformation"]["LimitFlags"] &= ~win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(self._job, win32job.JobObjectExtendedLimitInformation, info)
            else:
                win32job.TerminateJobObject(self._job, 1)
                win32event.WaitForSingleObject(self._process, 5000)
        finally:
            win32api.CloseHandle(self._process)
            win32api.CloseHandle(self._job)


def probe_worker(base_url, worker, instance, timeout=2):
    """Only this child generation can satisfy liveness, without system HTTP proxies."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base_url.rstrip("/") + "/api/health/live", timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read(4097))
        return (payload.get("status") == "ok" and payload.get("instance") == instance
                and worker.owns_pid(payload.get("pid")))
    except (OSError, HTTPException, ValueError, TypeError, AttributeError):
        return False


def run_supervisor(argv, *, cwd, env, base_url, on_ready=None, on_failure=None, worker_factory=OwnedWorker,
                   probe=probe_worker, clock=time.monotonic, sleep=time.sleep,
                   startup_grace=60, interval=5, misses_allowed=3, restart_limit=3,
                   restart_window=600, report=print):
    """Return 0 on normal quit; stop explicitly after three recoveries in ten minutes.

    Each replacement gets identical executable/arguments/cwd/environment except for
    a fresh identity nonce. No updater, configuration changes or request replay occurs.
    Injectable timing/worker boundaries allow deterministic policy tests.
    """
    restarts = deque()
    ready_notified = False

    def failed(message):
        if on_failure:
            try:
                on_failure()
            except Exception:
                pass
        report(message)
        return 1

    while True:
        instance = secrets.token_hex(16)
        child_env = {**env, WORKER_ENV: instance}
        try:
            worker = worker_factory(list(argv), cwd, child_env)
        except Exception as exc:
            return failed(f"Lake Task Manager could not start: {exc}")
        started, misses, healthy = clock(), 0, False
        clean_exit = False
        try:
            while True:
                code = worker.poll()
                if code is not None:
                    clean_exit = code == 0
                    if clean_exit:
                        return 0
                    reason = f"worker exited with code {code}"
                    break
                if probe(base_url, worker, instance):
                    misses, healthy = 0, True
                    if not ready_notified and on_ready:
                        on_ready()
                    ready_notified = True
                elif healthy or clock() - started >= startup_grace:
                    misses += 1
                    if misses >= misses_allowed:
                        reason = "worker stopped responding"
                        break
                sleep(interval)
        finally:
            worker.close(clean_exit=clean_exit)
        now = clock()
        while restarts and now - restarts[0] >= restart_window:
            restarts.popleft()
        if len(restarts) >= restart_limit:
            return failed(f"Lake Task Manager stopped: {reason}; recovery limit reached "
                          f"({restart_limit} restarts in {restart_window} seconds). Start the app again manually.")
        restarts.append(now)
        report(f"Lake Task Manager recovering: {reason} ({len(restarts)}/{restart_limit}).")
