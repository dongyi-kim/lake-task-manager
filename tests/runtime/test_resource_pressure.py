"""Exercise pressure policy without exhausting the machine running the tests."""
import threading
import json
import os
import sys
import time

import pytest

from app.infra.resource_pressure import (
    MiB, ResourceBusy, ResourcePressure, memory_policy, sample_memory,
)


@pytest.mark.parametrize("ram_gib,hard_gib,soft_gib,reserve_mib", [
    (8, 8 / 3, 2, 512), (16, 16 / 3, 4, 1024), (32, 8, 4, 2048),
])
def test_adaptive_headroom_admits_large_work_but_keeps_os_reserve(
        ram_gib, hard_gib, soft_gib, reserve_mib):
    policy = memory_policy(ram_gib * 1024 * MiB)
    assert policy["hardLimitBytes"] == int(hard_gib * 1024 * MiB)
    assert policy["softLimitBytes"] == soft_gib * 1024 * MiB
    assert policy["reserveBytes"] == reserve_mib * MiB
    value = {"totalBytes": ram_gib * 1024 * MiB, "processBytes": 1536 * MiB,
             "availableBytes": 3 * 1024 * MiB, "jobBytes": 1536 * MiB,
             "jobLimitBytes": policy["hardLimitBytes"]}
    gate = ResourcePressure(sampler=lambda: dict(value), interval=0)
    gate.check()
    value["availableBytes"] = policy["reserveBytes"]
    with pytest.raises(ResourceBusy):
        gate.check()
    value["availableBytes"] = int(policy["reserveBytes"] * 1.3)
    gate.check()


def test_memory_policy_keeps_small_machine_limits_bounded():
    policy = memory_policy(2 * 1024 * MiB)
    assert policy["hardLimitBytes"] == 2 * 1024 * MiB // 3
    assert policy["softLimitBytes"] == 512 * MiB
    assert policy["reserveBytes"] == 128 * MiB


def test_memory_pressure_refuses_then_recovers_with_headroom():
    value = {"processBytes": 10, "availableBytes": 1000, "totalBytes": 4000}
    gate = ResourcePressure(sampler=lambda: dict(value), soft_limit=100, reserve=100, interval=0)
    gate.check()
    value["processBytes"] = 100
    with pytest.raises(ResourceBusy):
        gate.check()
    assert gate.snapshot()["busy"] is True
    value["processBytes"] = 95
    with pytest.raises(ResourceBusy):
        gate.check()
    value["processBytes"] = 80
    gate.check()
    value["availableBytes"] = 90
    with pytest.raises(ResourceBusy):
        gate.check()
    value["availableBytes"] = 110
    with pytest.raises(ResourceBusy):
        gate.check()
    value["availableBytes"] = 130
    gate.check()
    assert gate.snapshot()["busy"] is False


def test_sampling_is_cached_and_status_is_io_free():
    samples, clock = [], [1.0]
    def sample():
        samples.append(1)
        return {"processBytes": 10, "availableBytes": 1000, "totalBytes": 4000}
    gate = ResourcePressure(sampler=sample, clock=lambda: clock[0], interval=.5,
                            soft_limit=100, reserve=100)
    gate.check()
    for _ in range(100):
        gate.check()
        gate.snapshot()
    assert len(samples) == 1
    clock[0] += .5
    gate.check()
    assert len(samples) == 2


def test_sampler_failure_cannot_bypass_already_observed_pressure():
    state = [True]
    def sample():
        if state[0]:
            return {"processBytes": 200, "availableBytes": 1000, "totalBytes": 4000}
        raise OSError("counter unavailable")
    gate = ResourcePressure(sampler=sample, interval=0, soft_limit=100, reserve=100)
    with pytest.raises(ResourceBusy):
        gate.check()
    state[0] = False
    with pytest.raises(ResourceBusy):
        gate.check()


def test_concurrent_check_does_not_wait_on_sampling_lock():
    gate = ResourcePressure(sampler=lambda: {}, soft_limit=100, reserve=100)
    done = threading.Event()
    with gate._lock:
        worker = threading.Thread(target=lambda: (gate.check(), done.set()))
        worker.start()
        assert done.wait(1)
    worker.join(1)


def test_real_sampler_returns_only_memory_counters():
    result = sample_memory()
    assert set(result) == {"processBytes", "availableBytes", "totalBytes"}
    assert result["processBytes"] > 0
    assert 0 <= result["availableBytes"] <= result["totalBytes"]


def test_browser_tree_pressure_refuses_even_when_python_is_small():
    value = {"processBytes": 10, "availableBytes": 1000, "totalBytes": 4000,
             "jobBytes": 750, "jobLimitBytes": 1000}
    gate = ResourcePressure(sampler=lambda: dict(value), soft_limit=100, reserve=100, interval=0)
    with pytest.raises(ResourceBusy):
        gate.check()
    assert gate.snapshot()["jobSoftLimitBytes"] == 750
    value["jobBytes"] = 700
    with pytest.raises(ResourceBusy):
        gate.check()
    value["jobBytes"] = 600
    gate.check()
    assert gate.snapshot()["busy"] is False


@pytest.mark.skipif(os.name != 'nt', reason='Windows job counters')
def test_owned_worker_reads_current_aggregate_job_commit(tmp_path):
    from app.launcher.supervisor import OwnedWorker
    from support.paths import REPO_ROOT
    target = tmp_path / 'memory.json'
    program = (
        'import json, pathlib, sys; '
        'from app.infra.resource_pressure import sample_memory; '
        'pathlib.Path(sys.argv[1]).write_text(json.dumps(sample_memory()))'
    )
    worker = OwnedWorker([sys.executable, '-B', '-c', program, str(target)],
        cwd=str(REPO_ROOT), env={**os.environ, 'LAKE_SUPERVISED_WORKER': 'counter-test'},
        memory_limit=256 * 1024 * 1024)
    try:
        deadline = time.monotonic() + 10
        while worker.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert worker.poll() == 0
        data = json.loads(target.read_text())
        assert data['jobLimitBytes'] == 256 * 1024 * 1024
        assert data['jobBytes'] >= data['processBytes'] > 0
    finally:
        worker.close()
