"""Small, cached admission checks; no payloads, cache locks or network access."""
import ctypes
import os
from pathlib import Path
import threading
import time

from app.auth.base import UpstreamBusy


MiB = 1024 * 1024


class ResourceBusy(UpstreamBusy):
    """No new work should start until memory headroom recovers."""


def sample_memory():
    if os.name == "nt":
        from ctypes import wintypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total", "available", "totalPage", "availablePage",
                    "totalVirtual", "availableVirtual", "extended")]
        class ProcessMemory(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peakWorking", "working", "peakPaged", "paged", "peakNonPaged",
                    "nonPaged", "pagefile", "peakPagefile", "private")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
        memory, process = MemoryStatus(), ProcessMemory()
        memory.length, process.size = ctypes.sizeof(memory), ctypes.sizeof(process)
        if not kernel.GlobalMemoryStatusEx(ctypes.byref(memory)) or not psapi.GetProcessMemoryInfo(
                kernel.GetCurrentProcess(), ctypes.byref(process), process.size):
            raise OSError(ctypes.get_last_error(), "Memory counters unavailable")
        return {"processBytes": process.private, "availableBytes": memory.available, "totalBytes": memory.total}
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        values[name] = int(value.split()[0]) * 1024
    resident = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return {"processBytes": resident, "availableBytes": values.get("MemAvailable", values["MemFree"]),
            "totalBytes": values["MemTotal"]}


def worker_memory_limit():
    """Commit cap for the Windows owned worker tree; supervisor stays outside it."""
    try:
        return min(2048 * MiB, max(512 * MiB, sample_memory()["totalBytes"] // 3))
    except (OSError, ValueError, KeyError, AttributeError):
        return 2048 * MiB


class ResourcePressure:
    def __init__(self, *, sampler=sample_memory, clock=time.monotonic, interval=.5,
                 soft_limit=None, reserve=None):
        self.sampler, self.clock, self.interval = sampler, clock, interval
        self.soft_limit, self.reserve = soft_limit, reserve
        self._lock = threading.Lock()
        self._next = 0
        self._state = {"busy": False, "supported": False}

    def snapshot(self):
        return dict(self._state)

    def check(self):
        now = self.clock()
        if now >= self._next and self._lock.acquire(blocking=False):
            try:
                if now >= self._next:
                    self._next = now + self.interval
                    try:
                        sample = self.sampler()
                        total = sample["totalBytes"]
                        limit = self.soft_limit if self.soft_limit is not None else min(1024 * MiB, max(256 * MiB, total // 4))
                        reserve = self.reserve if self.reserve is not None else min(512 * MiB, max(128 * MiB, total // 20))
                        recovering = self._state["busy"]
                        busy = (sample["processBytes"] >= limit * (.85 if recovering else 1)
                                or sample["availableBytes"] <= reserve * (1.25 if recovering else 1))
                        self._state = {**sample, "softLimitBytes": limit, "reserveBytes": reserve,
                                       "busy": busy, "supported": True}
                    except (OSError, ValueError, KeyError, AttributeError):
                        pass  # A transient counter failure must not clear observed pressure.
            finally:
                self._lock.release()
        if self._state["busy"]:
            raise ResourceBusy("메모리 여유가 부족해 새 작업을 잠시 받지 않습니다. 잠시 후 다시 시도해 주세요.")


resource_pressure = ResourcePressure()
