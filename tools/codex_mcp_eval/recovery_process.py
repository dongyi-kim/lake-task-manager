"""Stop an explicitly approved Codex subprocess at an observed recovery boundary."""
from __future__ import annotations

import subprocess
from threading import Event, Thread


def interruptible_process(args, *, stop_event, input, timeout, capture_output=True, **kwargs):
    if not capture_output or kwargs.get("shell"):
        raise ValueError("Recovery processes require captured pipes and no shell")
    done = Event()
    interrupted = Event()
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    def watch():
        while not done.wait(0.02):
            if stop_event.is_set() and process.poll() is None:
                interrupted.set()
                process.terminate()
                if not done.wait(2) and process.poll() is None:
                    process.kill()
                return
    watcher = Thread(target=watch, daemon=True)
    watcher.start()
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        result.intentional_interruption = interrupted.is_set()
        return result
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr) from None
    finally:
        done.set()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        watcher.join(timeout=3)
