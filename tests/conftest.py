"""Suite-wide pytest bootstrap shared by every functional test folder."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parent
sys.dont_write_bytecode = True
for root in (TESTS_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


@pytest.fixture(scope="session", autouse=True)
def resource_pressure_session():
    """Cover session/module setup before function-scoped isolation can run."""
    from app.infra.resource_pressure import MiB, resource_pressure

    sample = {"processBytes": 64 * MiB, "availableBytes": 4096 * MiB, "totalBytes": 8192 * MiB}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(resource_pressure, "sampler", lambda: dict(sample))
        patch.setattr(resource_pressure, "_state", {"busy": False, "supported": False})
        patch.setattr(resource_pressure, "_next", 0.0)
        yield sample


@pytest.fixture(autouse=True)
def resource_pressure_sample(monkeypatch, resource_pressure_session):
    """Keep admission deterministic in the shared, long-lived pytest process.

    A full suite retains imported modules and allocator arenas unlike one running
    app instance. Supply controlled memory inputs, while preserving the actual
    check, thresholds and hysteresis. Pressure cases may mutate these counters or
    override the sampler; native sample_memory tests still read the real OS.
    """
    from app.infra.resource_pressure import resource_pressure

    sample = dict(resource_pressure_session)
    monkeypatch.setattr(resource_pressure, "sampler", lambda: dict(sample))
    monkeypatch.setattr(resource_pressure, "_state", {"busy": False, "supported": False})
    monkeypatch.setattr(resource_pressure, "_next", 0.0)
    return sample


def pytest_sessionfinish(session, exitstatus):
    """Remove the bootstrap bytecode written before this conftest can disable it."""
    bootstrap_cache = TESTS_ROOT / "__pycache__"
    for compiled in bootstrap_cache.glob("conftest.*.pyc"):
        compiled.unlink(missing_ok=True)
    try:
        bootstrap_cache.rmdir()
    except OSError:
        pass
