"""Exercise pressure policy without exhausting the machine running the tests."""
import threading

import pytest

from app.infra.resource_pressure import ResourceBusy, ResourcePressure, sample_memory


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
