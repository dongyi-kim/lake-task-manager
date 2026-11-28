"""The suite controls memory inputs without bypassing real HTTP admission policy."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

import httpx

from app.infra.request_admission import RequestAdmissionMiddleware
from app.infra.resource_pressure import MiB, memory_policy, resource_pressure


def test_deterministic_memory_sample_still_refuses_and_recovers_http_work(resource_pressure_sample):
    executed = []

    async def handler(scope, receive, send):
        executed.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def scenario():
        gate = RequestAdmissionMiddleware(handler, pressure_check=resource_pressure.check)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            assert (await client.get("/api/work")).status_code == 200
            assert executed == ["/api/work"]

            soft_limit = memory_policy(resource_pressure_sample["totalBytes"])["softLimitBytes"]
            resource_pressure_sample["processBytes"] = soft_limit
            resource_pressure._next = 0  # Advance sampling without a wall-clock sleep.
            refused = await client.post("/api/write", json={"value": "must not execute"})
            assert refused.status_code == 429
            assert refused.json()["code"] == "resource_pressure"
            assert refused.json()["executed"] is False
            assert refused.headers["Retry-After"] == "1"
            assert executed == ["/api/work"]

            # The real 85% hysteresis must keep refusing below the soft limit.
            resource_pressure_sample["processBytes"] = int(soft_limit * .95)
            resource_pressure._next = 0
            assert (await client.get("/api/work")).status_code == 429
            assert executed == ["/api/work"]

            resource_pressure_sample["processBytes"] = 64 * MiB
            resource_pressure._next = 0
            assert (await client.get("/api/work")).status_code == 200
            assert executed == ["/api/work", "/api/work"]

    asyncio.run(scenario())


def test_module_setup_uses_healthy_session_sample_and_restores_original_inputs():
    """Reproduce a high-memory CI process before module-scoped fixtures run."""
    bootstrap = Path(__file__).resolve().parents[1] / "conftest.py"
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        probe = scratch / "test_module_pressure.py"
        probe.write_text(textwrap.dedent('''
            import asyncio
            import httpx
            import pytest
            from app.infra.request_admission import RequestAdmissionMiddleware
            from app.infra.resource_pressure import MiB, ResourceBusy, resource_pressure

            async def request():
                async def handler(scope, receive, send):
                    await send({"type":"http.response.start","status":200,"headers":[]})
                    await send({"type":"http.response.body","body":b"ok"})
                app = RequestAdmissionMiddleware(handler, pressure_check=resource_pressure.check)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    return (await client.get("/api/module-setup")).status_code

            @pytest.fixture(scope="module")
            def module_app():
                assert asyncio.run(request()) == 200
                yield
                # Function-scoped poison must be gone before module teardown.
                assert asyncio.run(request()) == 200

            def test_real_guard_remains_active(module_app, resource_pressure_sample):
                resource_pressure_sample["processBytes"] = 2048 * MiB
                resource_pressure._next = 0
                with pytest.raises(ResourceBusy):
                    resource_pressure.check()
        '''), encoding="utf-8")
        runner = textwrap.dedent('''
            import importlib.util, sys, pytest
            from app.infra.resource_pressure import MiB, resource_pressure
            host_sampler = lambda: {"processBytes":2048*MiB,"availableBytes":4096*MiB,"totalBytes":8192*MiB}
            resource_pressure.sampler = host_sampler
            resource_pressure._state = {"busy":False,"supported":False}
            resource_pressure._next = 0.0
            spec = importlib.util.spec_from_file_location("ltm_suite_bootstrap", sys.argv[1])
            bootstrap = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bootstrap)
            result = pytest.main([sys.argv[2], "-q", "--rootdir="+sys.argv[3], "--confcutdir="+sys.argv[3],
                                  "--basetemp="+sys.argv[3]+"/cases",
                                  "-o", "cache_dir="+sys.argv[3]+"/cache"], plugins=[bootstrap])
            assert resource_pressure.sampler is host_sampler
            assert resource_pressure._state == {"busy":False,"supported":False}
            assert resource_pressure._next == 0.0
            raise SystemExit(result)
        ''')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", "-c", runner, str(bootstrap), str(probe), str(scratch)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                                cwd=str(bootstrap.parent.parent), timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
