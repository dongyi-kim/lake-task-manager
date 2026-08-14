from __future__ import annotations

import os
from pathlib import Path

from app.infra import process_identity as P
from app.infra.settings import default_app_port


def test_environment_specific_defaults():
    assert default_app_port("mock") == 4457
    assert default_app_port("local") == 4457
    assert default_app_port("prod") == 8000
    assert P.process_name("mock") == "LakeTaskManagerDev.exe"
    assert P.process_name("prod") == "LakeTaskManager.exe"


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


def test_non_windows_and_already_named_are_noops(tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"python")
    assert P.reexec_with_process_name("mock", executable=python, platform="linux") is False

    named = tmp_path / P.PROD_PROCESS_NAME
    named.write_bytes(b"python")
    assert P.reexec_with_process_name("prod", executable=named, platform="win32") is False


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
