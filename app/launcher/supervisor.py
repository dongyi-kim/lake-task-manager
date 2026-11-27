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
    """An owned Windows job / POSIX session, never a process found by name or port.

    Windows limits committed memory across the worker and its descendants while
    keeping the supervisor outside the job. POSIX retains process-group ownership;
    it has no portable aggregate hard memory limit and uses the soft pressure guard.
    """

    def __init__(self, argv, cwd, env, *, memory_limit=None):
        self._closed = False
        if os.name != "nt":
            self._process = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True)
            self.pid = self._process.pid
            return
        import win32api
        import win32con
        import win32job
        import win32process

        if memory_limit is None:
            from app.infra.resource_pressure import worker_memory_limit
            memory_limit = worker_memory_limit()
        # A venv redirector or embedding host can leave multiple enclosing jobs.
        # Give admission an explicit handle to the exact aggregate limit we own.
        job_name = "Local\\LTMWorker-" + secrets.token_hex(16)
        self._job = win32job.CreateJobObject(None, job_name)
        env = {**env, "LAKE_WORKER_JOB_NAME": job_name}
        process = thread = None
        try:
            info = win32job.QueryInformationJobObject(self._job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] = (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY)
            info["JobMemoryLimit"] = memory_limit
            win32job.SetInformationJobObject(self._job, win32job.JobObjectExtendedLimitInformation, info)
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
                info["BasicLimitInformation"]["LimitFlags"] &= ~(
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY)
                info["JobMemoryLimit"] = 0
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
                   restart_window=600, report=print, record=lambda *args, **kwargs: None,
                   persistent_recovery=False, recovery_control=None, cooldown_initial=30, cooldown_max=300):
    """Return 0 on intentional quit, preserving finite recovery unless opted in.

    Each replacement gets identical executable/arguments/cwd/environment except for
    a fresh identity nonce. No updater, configuration changes or request replay occurs.
    Production keeps the parent alive after repeated faults, with capped backoff and
    independent Retry/Quit controls. Injected callers retain the finite retry policy.
    """
    restarts = deque()
    ready_notified = False
    cooldown_delay = cooldown_initial
    if persistent_recovery and recovery_control is None:
        from app.launcher.recovery_control import RecoveryControl
        recovery_control = RecoveryControl()

    def observe(callback, *args, **kwargs):
        # Console/log/native observer failures must not terminate an owned worker
        # or the protected parent. Ownership operations intentionally stay strict.
        try:
            callback(*args, **kwargs)
        except Exception:
            pass

    def emit(event, **fields):
        observe(record, event, **fields)

    def notify_failure(message):
        if on_failure:
            observe(on_failure)
        observe(report, message)

    def quitting():
        return recovery_control is not None and recovery_control.quit_requested.is_set()

    def wait_for(delay, *, allow_retry=False):
        deadline = clock() + delay
        while True:
            if quitting():
                return "quit"
            if allow_retry and recovery_control is not None and recovery_control.retry_requested.is_set():
                recovery_control.retry_requested.clear()
                return "retry"
            remaining = deadline - clock()
            if remaining <= 0:
                return "elapsed"
            # No uninterruptible five-minute sleep: Quit also works during startup
            # monitoring and after a native Retry callback has queued a new attempt.
            sleep(min(.25, remaining) if recovery_control is not None else remaining)

    def cooldown(reason):
        nonlocal cooldown_delay, ready_notified
        ready_notified = False
        if recovery_control is not None:
            recovery_control.retry_requested.clear()
        notify_failure(f"Lake Task Manager recovering: {reason}; retrying in {cooldown_delay} seconds. "
                       "Use the recovery tray to retry now or quit, or press Ctrl+C in this console.")
        emit("worker_cooldown", reason=reason, delaySeconds=cooldown_delay)
        if recovery_control is not None:
            observe(recovery_control.show_cooldown, cooldown_delay, reason)
        outcome = wait_for(cooldown_delay, allow_retry=True)
        cooldown_delay = min(cooldown_max, cooldown_delay * 2)
        return outcome != "quit"

    try:
        while not quitting():
            instance = secrets.token_hex(16)
            child_env = {**env, WORKER_ENV: instance}
            try:
                worker = worker_factory(list(argv), cwd, child_env)
                emit("worker_spawned", workerPid=worker.pid)
            except Exception as exc:
                emit("worker_spawn_failed", errorType=type(exc).__name__)
                if persistent_recovery:
                    if not cooldown("worker could not start"):
                        return 0
                    continue
                notify_failure(f"Lake Task Manager could not start: {exc}")
                return 1
            started, misses, healthy = clock(), 0, False
            clean_exit = intentional_stop = False
            reason = "supervisor interrupted"
            code = None
            try:
                while True:
                    if quitting():
                        intentional_stop = True
                        emit("worker_stop_requested", workerPid=worker.pid)
                        return 0
                    code = worker.poll()
                    if code is not None:
                        clean_exit = code == 0
                        if clean_exit:
                            emit("worker_exit", workerPid=worker.pid, exitCode=0)
                            return 0
                        reason = f"worker exited with code {code}"
                        break
                    if probe(base_url, worker, instance):
                        if not healthy and recovery_control is not None:
                            observe(recovery_control.hide)
                        misses, healthy = 0, True
                        if not ready_notified and on_ready:
                            observe(on_ready)
                        ready_notified = True
                    elif healthy or clock() - started >= startup_grace:
                        misses += 1
                        if misses >= misses_allowed:
                            reason = "worker stopped responding"
                            break
                    wait_for(interval)
            finally:
                if not clean_exit and not intentional_stop:
                    emit("worker_recovery", workerPid=worker.pid, reason=reason, misses=misses,
                           exitCode=code, uptimeSeconds=round(clock() - started, 2))
                worker.close(clean_exit=clean_exit)
            now = clock()
            while restarts and now - restarts[0] >= restart_window:
                restarts.popleft()
            if len(restarts) >= restart_limit:
                if persistent_recovery:
                    if not cooldown(reason):
                        return 0
                    continue
                notify_failure(f"Lake Task Manager stopped: {reason}; recovery limit reached "
                               f"({restart_limit} restarts in {restart_window} seconds). Start the app again manually.")
                return 1
            restarts.append(now)
            observe(report, f"Lake Task Manager recovering: {reason} ({len(restarts)}/{restart_limit}).")
        return 0
    finally:
        if recovery_control is not None:
            observe(recovery_control.close)
