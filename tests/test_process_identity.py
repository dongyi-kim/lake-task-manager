from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path
from types import SimpleNamespace

import watchfiles
import pytest

import run as launcher
from app.infra import process_identity as P
from app.infra.settings import default_app_port


def test_environment_specific_defaults():
    assert default_app_port("mock") == 4457
    assert default_app_port("local") == 4457
    assert default_app_port("prod") == 8000
    assert P.process_name("mock") == "LakeTaskManagerDev.exe"
    assert P.process_name("prod") == "LakeTaskManager.exe"
    assert P.process_version_info("mock")["FileDescription"] == "Lake Task Manager Dev"
    assert P.process_version_info("prod")["FileDescription"] == "Lake Task Manager"
    assert P.process_version_info("prod")["OriginalFilename"] == "LakeTaskManager.exe"


def test_version_resource_contains_ltm_metadata():
    fixed_info = struct.pack("<13I", 0xFEEF04BD, *([0] * 12))
    resource = P._build_version_resource(
        fixed_info,
        {
            "FileDescription": "Lake Task Manager Dev",
            "OriginalFilename": "LakeTaskManagerDev.exe",
        },
        language=0x0409,
    )

    assert struct.unpack_from("<H", resource)[0] == len(resource)
    assert "VS_VERSION_INFO".encode("utf-16le") in resource
    assert "Lake Task Manager Dev".encode("utf-16le") in resource
    assert "LakeTaskManagerDev.exe".encode("utf-16le") in resource


def test_version_block_length_excludes_next_block_alignment_padding():
    block = P._version_block(
        "Key",
        value=P._utf16z("AB"),
        value_length=3,
        value_type=1,
    )

    assert struct.unpack_from("<H", block)[0] == len(block)
    assert len(block) % 4 == 2


@pytest.mark.skipif(not os.sys.platform.startswith("win"), reason="Windows PE resource test")
def test_windows_launcher_version_info_is_stamped(tmp_path):
    import win32api

    target = tmp_path / P.DEV_PROCESS_NAME
    shutil.copy2(os.sys._base_executable, target)

    assert P._stamp_version_info(target, "mock") is True
    translations = win32api.GetFileVersionInfo(str(target), "\\VarFileInfo\\Translation")
    language, codepage = translations[0]
    table = f"{language:04X}{codepage:04X}"
    description = win32api.GetFileVersionInfo(
        str(target),
        f"\\StringFileInfo\\{table}\\FileDescription",
    )
    assert description == "Lake Task Manager Dev"


def test_named_launcher_reexec_contract(tmp_path):
    scripts = tmp_path / "venv" / "Scripts"
    base_dir = tmp_path / "python-home"
    scripts.mkdir(parents=True)
    base_dir.mkdir()
    source = scripts / "python.exe"
    base = base_dir / "python.exe"
    source.write_bytes(b"venv-python-redirector")
    base.write_bytes(b"base-python-image")
    (base_dir / "python3.dll").write_bytes(b"stable-abi")
    (base_dir / f"python{os.sys.version_info.major}{os.sys.version_info.minor}.dll").write_bytes(
        b"python-runtime"
    )
    calls = []

    def fake_execve(executable, argv, environ):
        calls.append((executable, argv, environ))

    changed = P.reexec_with_process_name(
        "mock",
        executable=source,
        base_executable=base,
        argv=["run.py", "demo"],
        environ={"JIRA_ENV": "mock"},
        platform="win32",
        execve=fake_execve,
    )

    target = scripts / "LakeTaskManagerDev.exe"
    assert changed is True
    assert target.read_bytes() == base.read_bytes()
    assert (scripts / "python3.dll").read_bytes() == b"stable-abi"
    assert (
        scripts / f"python{os.sys.version_info.major}{os.sys.version_info.minor}.dll"
    ).read_bytes() == b"python-runtime"
    assert calls == [
        (str(target.resolve()), [str(target.resolve()), "run.py", "demo"], {"JIRA_ENV": "mock"})
    ]


def test_non_windows_and_already_named_are_noops(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.write_bytes(b"python")
    assert P.reexec_with_process_name("mock", executable=python, platform="linux") is False

    named = tmp_path / P.PROD_PROCESS_NAME
    named.write_bytes(b"python")
    monkeypatch.setattr(P.sys, "_base_executable", str(tmp_path / "missing.exe"))
    assert P.reexec_with_process_name("prod", executable=named, platform="win32") is False
    assert P.sys._base_executable == str(named.resolve())


def test_existing_launcher_is_refreshed(tmp_path):
    scripts = tmp_path / "venv" / "Scripts"
    base_dir = tmp_path / "python-home"
    scripts.mkdir(parents=True)
    base_dir.mkdir()
    source = scripts / "pythonw.exe"
    base = base_dir / "python.exe"
    target = scripts / P.PROD_PROCESS_NAME
    source.write_bytes(b"venv-redirector")
    base.write_bytes(b"new-python-image")
    target.write_bytes(b"old")
    os.utime(target, ns=(1, 1))

    P.reexec_with_process_name(
        "prod",
        executable=source,
        base_executable=base,
        argv=["run.py"],
        platform="win32",
        execve=lambda *_: None,
    )
    assert target.read_bytes() == b"new-python-image"


def test_hot_reload_watches_backend_config_and_static_assets(monkeypatch):
    calls = {}

    class FakeThread:
        def __init__(self, *, target, daemon):
            calls["thread"] = {"target": target, "daemon": daemon}

        def start(self):
            calls["thread_started"] = True

    monkeypatch.setattr(launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(watchfiles, "run_process", lambda *paths, **kwargs: calls.update(
        {"paths": paths, "kwargs": kwargs}
    ))

    launcher._run_hot_reload(
        SimpleNamespace(app_host="127.0.0.1", app_port=4457, jira_env="mock")
    )

    assert calls["thread_started"] is True
    source_root = Path(launcher.__file__).resolve().parent
    assert {Path(path) for path in calls["paths"]} == {
        source_root / "app", source_root / "config",
    }
    assert calls["kwargs"]["target"] is launcher._serve_hot_reload_worker
    assert calls["kwargs"]["args"] == ("127.0.0.1", 4457)
    assert calls["kwargs"]["target_type"] == "function"
    assert calls["kwargs"]["ignore_permission_denied"] is True
    assert launcher.os.environ["WATCHFILES_FORCE_POLLING"] == "true"


def test_hot_reload_worker_runs_uvicorn_without_nested_reloader(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))

    launcher._serve_hot_reload_worker("127.0.0.1", 4457)

    assert calls == [("app.main:app", {
        "host": "127.0.0.1", "port": 4457, "log_level": "info",
    })]
