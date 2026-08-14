"""Windows 실행 프로세스의 이름을 LTM 전용 이름으로 고정한다.

Python 코드에서 Task Manager의 프로세스 이미지 이름을 바꿀 수는 없다. 대신 base
Python 실행 이미지와 런타임 DLL을 가상환경에 LTM 이름으로 준비하고, 작업 관리자의
"프로세스" 탭도 Python이 아닌 LTM으로 표시되도록 실행 파일의 VERSIONINFO를 갱신한 뒤
앱 서버를 띄우기 전에 그 실행기로 현재 프로세스를 교체한다.
"""

from __future__ import annotations

import os
import platform as platform_module
import shutil
import struct
import sys
from pathlib import Path


DEV_PROCESS_NAME = "LakeTaskManagerDev.exe"
PROD_PROCESS_NAME = "LakeTaskManager.exe"
_PYTHON_LAUNCHERS = {"python.exe", "pythonw.exe"}
_RT_VERSION = 16
_VERSION_RESOURCE_ID = 1
_VS_FIXEDFILEINFO_SIZE = 52


def process_version_info(jira_env: str) -> dict[str, str]:
    """Windows 작업 관리자와 파일 속성에 표시할 LTM 실행 파일 정보."""
    is_prod = str(jira_env).strip().lower() == "prod"
    filename = PROD_PROCESS_NAME if is_prod else DEV_PROCESS_NAME
    description = "Lake Task Manager" if is_prod else "Lake Task Manager Dev"
    return {
        "CompanyName": "Python Software Foundation",
        "FileDescription": description,
        "FileVersion": platform_module.python_version(),
        "InternalName": Path(filename).stem,
        "OriginalFilename": filename,
        "ProductName": "Lake Task Manager",
        "ProductVersion": platform_module.python_version(),
    }


def _pad_dword(data: bytes) -> bytes:
    return data + (b"\0" * (-len(data) % 4))


def _utf16z(value: str) -> bytes:
    return value.encode("utf-16le") + b"\0\0"


def _version_block(
    key: str,
    *,
    value: bytes = b"",
    value_length: int = 0,
    value_type: int = 1,
    children: tuple[bytes, ...] = (),
) -> bytes:
    """VERSIONINFO의 32-bit 정렬 블록 하나를 만든다."""
    block = _pad_dword(struct.pack("<HHH", 0, value_length, value_type) + _utf16z(key))
    block += value
    if children:
        block = _pad_dword(block)
        for child in children:
            block += child
            block = _pad_dword(block)
    else:
        block = _pad_dword(block)
    if len(block) > 0xFFFF:
        raise ValueError(f"VERSIONINFO block is too large: {key}")
    return struct.pack("<H", len(block)) + block[2:]


def _build_version_resource(
    fixed_info: bytes,
    metadata: dict[str, str],
    *,
    language: int,
    codepage: int = 1200,
) -> bytes:
    if len(fixed_info) != _VS_FIXEDFILEINFO_SIZE:
        raise ValueError("invalid VS_FIXEDFILEINFO size")
    if struct.unpack_from("<I", fixed_info)[0] != 0xFEEF04BD:
        raise ValueError("invalid VS_FIXEDFILEINFO signature")

    strings = tuple(
        _version_block(
            key,
            value=_utf16z(value),
            value_length=len(value) + 1,
            value_type=1,
        )
        for key, value in metadata.items()
    )
    string_table = _version_block(
        f"{language:04X}{codepage:04X}",
        children=strings,
    )
    string_file_info = _version_block("StringFileInfo", children=(string_table,))
    translation = _version_block(
        "Translation",
        value=struct.pack("<HH", language, codepage),
        value_length=4,
        value_type=0,
    )
    var_file_info = _version_block("VarFileInfo", children=(translation,))
    return _version_block(
        "VS_VERSION_INFO",
        value=fixed_info,
        value_length=len(fixed_info),
        value_type=0,
        children=(string_file_info, var_file_info),
    )


def _version_template(path: Path) -> tuple[bytes, int]:
    """기존 Python 이미지에서 고정 버전 정보와 리소스 언어를 읽는다."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel32.LoadLibraryExW.restype = wintypes.HMODULE
    kernel32.EnumResourceLanguagesW.argtypes = [
        wintypes.HMODULE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ssize_t,
    ]
    kernel32.EnumResourceLanguagesW.restype = wintypes.BOOL
    kernel32.FindResourceExW.argtypes = [
        wintypes.HMODULE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.WORD,
    ]
    kernel32.FindResourceExW.restype = wintypes.HANDLE
    kernel32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    kernel32.SizeofResource.restype = wintypes.DWORD
    kernel32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    kernel32.LoadResource.restype = wintypes.HANDLE
    kernel32.LockResource.argtypes = [wintypes.HANDLE]
    kernel32.LockResource.restype = ctypes.c_void_p
    kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel32.FreeLibrary.restype = wintypes.BOOL

    module = kernel32.LoadLibraryExW(str(path), None, 0x00000002 | 0x00000020)
    if not module:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        languages: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMODULE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.WORD,
            ctypes.c_ssize_t,
        )

        @callback_type
        def collect_language(_module, _type, _name, language, _context):
            languages.append(int(language))
            return True

        ok = kernel32.EnumResourceLanguagesW(
            module,
            ctypes.c_void_p(_RT_VERSION),
            ctypes.c_void_p(_VERSION_RESOURCE_ID),
            collect_language,
            0,
        )
        if not ok or not languages:
            raise ctypes.WinError(ctypes.get_last_error())
        language = languages[0]
        resource = kernel32.FindResourceExW(
            module,
            ctypes.c_void_p(_RT_VERSION),
            ctypes.c_void_p(_VERSION_RESOURCE_ID),
            language,
        )
        if not resource:
            raise ctypes.WinError(ctypes.get_last_error())
        size = kernel32.SizeofResource(module, resource)
        loaded = kernel32.LoadResource(module, resource)
        address = kernel32.LockResource(loaded)
        if not size or not loaded or not address:
            raise ctypes.WinError(ctypes.get_last_error())
        raw = ctypes.string_at(address, size)
    finally:
        kernel32.FreeLibrary(module)

    key_end = raw.find(b"\0\0", 6)
    while key_end >= 0 and key_end % 2:
        key_end = raw.find(b"\0\0", key_end + 1)
    if key_end < 0:
        raise ValueError("VERSIONINFO root key is missing")
    value_offset = (key_end + 2 + 3) & ~3
    value_length = struct.unpack_from("<H", raw, 2)[0]
    fixed_info = raw[value_offset : value_offset + value_length]
    if len(fixed_info) != _VS_FIXEDFILEINFO_SIZE:
        raise ValueError("VS_FIXEDFILEINFO is missing")
    return fixed_info, language


def _stamp_version_info(path: Path, jira_env: str) -> bool:
    """복사한 Python 이미지의 표시 이름을 LTM으로 바꾼다.

    리소스 수정 실패는 실행 자체를 막지 않는다. 파일명 기반 프로세스 구분은 유지하고
    작업 관리자 표시명만 Python으로 폴백한다.
    """
    if not sys.platform.startswith("win"):
        return False

    import ctypes
    from ctypes import wintypes

    try:
        fixed_info, language = _version_template(path)
        resource = _build_version_resource(
            fixed_info,
            process_version_info(jira_env),
            language=language,
        )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
        kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
        kernel32.UpdateResourceW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.WORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.UpdateResourceW.restype = wintypes.BOOL
        kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
        kernel32.EndUpdateResourceW.restype = wintypes.BOOL

        update = kernel32.BeginUpdateResourceW(str(path), False)
        if not update:
            raise ctypes.WinError(ctypes.get_last_error())
        committed = False
        try:
            buffer = ctypes.create_string_buffer(resource)
            if not kernel32.UpdateResourceW(
                update,
                ctypes.c_void_p(_RT_VERSION),
                ctypes.c_void_p(_VERSION_RESOURCE_ID),
                language,
                ctypes.cast(buffer, ctypes.c_void_p),
                len(resource),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel32.EndUpdateResourceW(update, False):
                raise ctypes.WinError(ctypes.get_last_error())
            committed = True
        finally:
            if not committed:
                kernel32.EndUpdateResourceW(update, True)
        return True
    except (OSError, ValueError) as exc:
        print(
            f"[runtime] {path.name} 표시 정보 갱신 실패; 파일명만 적용합니다: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return False


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


def _sync_launcher(base_executable: Path, target: Path, jira_env: str) -> None:
    """redirector가 아닌 실제 Python 이미지와 필수 DLL을 venv에 준비한다.

    Windows venv의 ``Scripts/python.exe``는 실제 base Python을 자식으로 실행하는
    redirector다. 그것을 이름만 바꾸면 포트 소유 프로세스는 여전히 ``python.exe``다.
    따라서 ``sys._base_executable``과 런타임 DLL을 복제해 named 프로세스가 인터프리터를
    직접 호스팅하도록 한다.
    """
    candidate = target.with_name(target.name + ".new")
    try:
        shutil.copy2(base_executable, candidate)
        _stamp_version_info(candidate, jira_env)
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
    for runtime in _runtime_files(base_executable):
        _sync_file(runtime, target.parent / runtime.name)


def _repair_base_executable(current: Path) -> None:
    """named base 이미지의 multiprocessing worker가 같은 실행기를 사용하게 한다.

    CPython은 venv 안에서 이름이 바뀐 실행기를 시작하면 ``sys._base_executable``도 base
    디렉터리의 같은 이름으로 추론한다. 그 파일은 존재하지 않아 Windows spawn이 실패하므로,
    실제 인터프리터 이미지인 현재 named launcher를 base 실행기로 지정한다.
    """
    sys._base_executable = str(current)


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
        _repair_base_executable(current)
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
        _sync_launcher(base, target, jira_env)
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
