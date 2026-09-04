"""MAXIS/Dify app keys stored with Windows current-user DPAPI.

The browser can write replacement keys and read their presence/source, but it never
receives plaintext back.  Environment variables always win at runtime.  A normal
file fallback is intentionally not provided: persisting secrets without DPAPI on a
non-Windows host fails closed.  Tests may inject an in-memory/encrypting backend.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


WORKFLOW_KEYS = ("agent_core", "editor_author", "pmo_brief")
ENV_NAMES = {
    "agent_core": "LAKE_MAXIS_AGENT_CORE_API_KEY",
    "editor_author": "LAKE_MAXIS_EDITOR_AUTHOR_API_KEY",
    "pmo_brief": "LAKE_MAXIS_PMO_BRIEF_API_KEY",
}
_MAGIC = b"LTM-MAXIS-DPAPI-v1\x00"
_LOCK = threading.RLock()


class CredentialStoreUnavailable(RuntimeError):
    """Raised when a secure persistent credential backend is unavailable."""


class CredentialBackend(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(value: bytes) -> tuple[_DATA_BLOB, object]:
    # Keep ``buf`` alive until CryptProtectData/CryptUnprotectData returns.
    buf = ctypes.create_string_buffer(value)
    return (_DATA_BLOB(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf)


class WindowsDpapiBackend:
    """CryptProtectData without LOCAL_MACHINE scope: only this Windows user can read it."""

    _UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialStoreUnavailable(
                "MAXIS credential persistence requires Windows current-user DPAPI."
            )
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def protect(self, value: bytes) -> bytes:
        source, source_buf = _blob(value)
        result = _DATA_BLOB()
        # ``source_buf`` is deliberately referenced until after the native call.
        _ = source_buf
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(source), "Lake Task Manager MAXIS credentials", None,
            None, None, self._UI_FORBIDDEN, ctypes.byref(result),
        )
        if not ok:
            raise CredentialStoreUnavailable("Windows DPAPI encryption failed.")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            self._kernel32.LocalFree(result.pbData)

    def unprotect(self, value: bytes) -> bytes:
        source, source_buf = _blob(value)
        result = _DATA_BLOB()
        _ = source_buf
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None,
            self._UI_FORBIDDEN, ctypes.byref(result),
        )
        if not ok:
            raise CredentialStoreUnavailable("Windows DPAPI decryption failed.")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            self._kernel32.LocalFree(result.pbData)


_backend_override: CredentialBackend | None = None


def set_backend_for_tests(backend: CredentialBackend | None) -> None:
    """Inject an isolated backend.  Production code must leave this as ``None``."""
    global _backend_override
    _backend_override = backend


def _backend() -> CredentialBackend:
    return _backend_override or WindowsDpapiBackend()


def _path() -> Path:
    from app.infra.settings import CACHE_DIR
    return Path(CACHE_DIR) / "maxis_credentials.dpapi"


def _normal_profile(profile_name: str) -> str:
    value = str(profile_name or "").strip().lower()
    if value not in ("dev", "prod"):
        raise ValueError("MAXIS profile must be dev or prod.")
    return value


def _load_all() -> dict[str, dict[str, str]]:
    path = _path()
    if not path.exists():
        return {}
    try:
        blob = path.read_bytes()
        if not blob.startswith(_MAGIC):
            raise CredentialStoreUnavailable("Unknown MAXIS credential store format.")
        raw = _backend().unprotect(blob[len(_MAGIC):])
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("credential payload must be an object")
    except CredentialStoreUnavailable:
        raise
    except Exception as exc:
        raise CredentialStoreUnavailable("MAXIS credential store could not be read.") from exc

    out: dict[str, dict[str, str]] = {}
    for name in ("dev", "prod"):
        row = decoded.get(name)
        if not isinstance(row, dict):
            continue
        out[name] = {
            key: str(row.get(key) or "").strip()
            for key in WORKFLOW_KEYS if str(row.get(key) or "").strip()
        }
    return out


def _write_all(rows: dict[str, dict[str, str]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    protected = _backend().protect(payload)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(_MAGIC + protected)
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def load_for(profile_name: str) -> dict[str, str]:
    """Internal runtime API.  Never serialize this return value to HTTP/logs."""
    name = _normal_profile(profile_name)
    with _LOCK:
        return dict(_load_all().get(name) or {})


def save_for(profile_name: str, patch: dict | None = None,
             clear: list[str] | tuple[str, ...] = ()) -> dict:
    """Persist key changes and return presence/source metadata only."""
    name = _normal_profile(profile_name)
    patch = patch or {}
    unknown = (set(patch) | set(clear or ())) - set(WORKFLOW_KEYS)
    if unknown:
        raise ValueError("Unsupported MAXIS credential field.")
    with _LOCK:
        rows = _load_all()
        current = dict(rows.get(name) or {})
        for key in clear or ():
            current.pop(key, None)
        for key, raw in patch.items():
            value = str(raw or "").strip()
            if value:
                current[key] = value
        rows[name] = current
        _write_all(rows)
    return status_for(name)


def delete_for(profile_name: str) -> None:
    name = _normal_profile(profile_name)
    with _LOCK:
        rows = _load_all()
        rows.pop(name, None)
        _write_all(rows)


def environment_overrides() -> dict[str, str]:
    return {key: env for key, env in ENV_NAMES.items() if str(os.getenv(env) or "").strip()}


def get_for(profile_name: str, workflow_key: str) -> str:
    """Resolve one key for backend use.  Environment always wins."""
    name = _normal_profile(profile_name)
    key = str(workflow_key or "").strip().lower()
    if key not in WORKFLOW_KEYS:
        raise ValueError("Unsupported MAXIS workflow key.")
    env_value = str(os.getenv(ENV_NAMES[key]) or "").strip()
    if env_value:
        return env_value
    return str(load_for(name).get(key) or "").strip()


def resolved_for(profile_name: str) -> dict[str, str]:
    return {key: get_for(profile_name, key) for key in WORKFLOW_KEYS}


def status_for(profile_name: str) -> dict[str, dict[str, object]]:
    """Browser-safe status.  No suffix, digest, or plaintext key is returned."""
    name = _normal_profile(profile_name)
    overrides = environment_overrides()
    try:
        stored = load_for(name)
        store_error = ""
    except CredentialStoreUnavailable as exc:
        stored = {}
        store_error = str(exc)
    return {
        key: {
            "configured": bool(os.getenv(ENV_NAMES[key]) or stored.get(key)),
            "source": "environment" if key in overrides else (
                "dpapi" if stored.get(key) else "none"
            ),
            "environmentVariable": overrides.get(key, ""),
            "storeError": store_error,
        }
        for key in WORKFLOW_KEYS
    }


def verification_digest(profile_name: str) -> str:
    """Private probe invalidation digest; never expose it through an API."""
    values = resolved_for(profile_name)
    material = "|".join(f"{key}:{values[key]}" for key in WORKFLOW_KEYS)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
