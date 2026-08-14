"""Windows 실행 프로세스의 이름을 LTM 전용 이름으로 고정한다.

Python 코드에서 Task Manager의 프로세스 이미지 이름을 바꿀 수는 없다. 대신 base
Python 실행 이미지와 런타임 DLL을 가상환경에 LTM 이름으로 준비하고, 앱 서버를 띄우기
전에 그 실행기로 현재 프로세스를 교체한다.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


DEV_PROCESS_NAME = "LakeTaskManagerDev.exe"
PROD_PROCESS_NAME = "LakeTaskManager.exe"
_PYTHON_LAUNCHERS = {"python.exe", "pythonw.exe"}


def process_name(jira_env: str) -> str:
    """운영 pystray와 개발 서버에 서로 구분되는 실행 파일명을 사용한다."""
    return PROD_PROCESS_NAME if str(jira_env).strip().lower() == "prod" else DEV_PROCESS_NAME


def named_launcher_path(executable: str | os.PathLike[str], jira_env: str) -> Path:
    return Path(executable).resolve().with_name(process_name(jira_env))


def _same_binary(source: Path, target: Path) -> bool:
    try:
        src, dst = source.stat(), target.stat()
        return src.st_size == dst.st_size and src.st_mtime_ns == dst.st_mtime_ns
    except OSError:
        return False


def _sync_file(source: Path, target: Path) -> None:
    """Python 업데이트 시 실행기·런타임 파일을 원자적으로 교체한다.

    실행 중인 이전 인스턴스 때문에 Windows가 교체를 거부하면 기존 launcher를 그대로
    사용한다. 가상환경이 다시 만들어지면 launcher도 함께 사라지므로 세대가 영구히
    어긋나지는 않는다.
    """
    if _same_binary(source, target):
        return
    candidate = target.with_name(target.name + ".new")
    try:
        shutil.copy2(source, candidate)
        try:
            os.replace(candidate, target)
        except PermissionError:
            if not target.is_file():
                raise
    finally:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _runtime_files(base_executable: Path) -> list[Path]:
    """복제한 base Python이 venv 안에서 직접 실행될 때 필요한 DLL 목록."""
    base_dir = base_executable.parent
    names = {
        "python3.dll",
        f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    }
    return [base_dir / name for name in sorted(names) if (base_dir / name).is_file()]


def _sync_launcher(base_executable: Path, target: Path) -> None:
    """redirector가 아닌 실제 Python 이미지와 필수 DLL을 venv에 준비한다.

    Windows venv의 ``Scripts/python.exe``는 실제 base Python을 자식으로 실행하는
    redirector다. 그것을 이름만 바꾸면 포트 소유 프로세스는 여전히 ``python.exe``다.
    따라서 ``sys._base_executable``과 런타임 DLL을 복제해 named 프로세스가 인터프리터를
    직접 호스팅하도록 한다.
    """
    _sync_file(base_executable, target)
    for runtime in _runtime_files(base_executable):
        _sync_file(runtime, target.parent / runtime.name)


def reexec_with_process_name(
    jira_env: str,
    *,
    executable: str | os.PathLike[str] | None = None,
    base_executable: str | os.PathLike[str] | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    platform: str | None = None,
    execve=None,
) -> bool:
    """Windows Python 프로세스를 LTM 이름의 실행기로 교체한다.

    성공한 실제 ``os.execve``는 반환하지 않는다. 테스트용 execve가 반환한 경우에만
    ``True``를 반환한다. Windows가 아니거나 이미 named launcher이면 아무 작업도 하지
    않고 ``False``를 반환한다. launcher 준비 실패가 앱 기동 자체를 막지는 않는다.
    """
    platform = sys.platform if platform is None else platform
    if not str(platform).lower().startswith("win"):
        return False

    current = Path(sys.executable if executable is None else executable).resolve()
    target = named_launcher_path(current, jira_env)
    if current.name.casefold() == target.name.casefold():
        return False
    if current.name.casefold() not in _PYTHON_LAUNCHERS:
        return False

    base = Path(
        getattr(sys, "_base_executable", current) if base_executable is None else base_executable
    ).resolve()
    if not base.is_file():
        base = current

    args = list(sys.argv if argv is None else argv)
    env = dict(os.environ if environ is None else environ)
    exec_fn = os.execve if execve is None else execve
    try:
        _sync_launcher(base, target)
        exec_fn(str(target), [str(target), *args], env)
        return True
    except OSError as exc:
        print(
            f"[runtime] 프로세스 이름을 {target.name}(으)로 바꾸지 못해 "
            f"{current.name}(으)로 계속 실행합니다: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return False
