"""MAXIS/Dify app keys stored with Windows current-user DPAPI.

The browser can write replacement keys and read their presence/source, but it never
receives plaintext back. Environment variables win only for the explicitly selected
stable profile ID. A normal file fallback is intentionally not provided:
persisting secrets without DPAPI on a non-Windows host fails closed.  Tests may inject
an in-memory/encrypting backend.
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import re
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
_MIGRATION_MARKER = b"LTM-MAXIS-NEW-STORE-v1\n"
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
        # Pin the Unicode and pointer-width ABI explicitly.  This avoids ctypes'
        # platform-dependent implicit conversions in 64-bit packaged/local runtimes.
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_pointer = ctypes.POINTER(_DATA_BLOB)
        self._crypt32.CryptProtectData.argtypes = (
            blob_pointer,
            wintypes.LPCWSTR,
            blob_pointer,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            blob_pointer,
        )
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = (
            blob_pointer,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_pointer,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            blob_pointer,
        )
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
        self._kernel32.LocalFree.restype = wintypes.HLOCAL

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

    return Path(CACHE_DIR) / "maxis" / "credentials.dpapi"


def _legacy_path() -> Path:
    """Return the pre-migration store path used only as a read fallback.

    The legacy file is deliberately never removed or overwritten here.  The first
    subsequent credential mutation writes the complete decrypted payload to the new
    path; because reads prefer that path, clearing credentials cannot accidentally
    resurrect a value left in the legacy store.
    """

    from app.infra.settings import CACHE_DIR

    return Path(CACHE_DIR) / "maxis_credentials.dpapi"


def _migration_marker_path() -> Path:
    """A non-secret tombstone preventing deleted new stores from reviving legacy keys."""

    from app.infra.settings import CACHE_DIR

    return Path(CACHE_DIR) / "maxis" / "legacy-store-disabled"


def normal_profile_name(profile_name: str) -> str:
    """Validate a stable storage ID, independently of its user-facing label."""
    value = str(profile_name or "").strip().lower()
    if value not in ("dev", "internal-pilot", "prod") and not re.fullmatch(
        r"maxis-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        value,
    ):
        raise ValueError("Invalid MAXIS profile ID.")
    return value


_normal_profile = normal_profile_name


def _load_all() -> dict[str, dict[str, str]]:
    path = _path()
    if not path.exists():
        # Once any mutation established the new store as authoritative, absence means
        # empty/revoked.  Never fall back to stale legacy keys after deletion.
        if _migration_marker_path().exists():
            return {}
        legacy = _legacy_path()
        if not legacy.exists():
            return {}
        path = legacy
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
    for name, row in decoded.items():
        try:
            if normal_profile_name(name) != name:
                continue
        except ValueError:
            continue
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
    marker = _migration_marker_path()
    marker_tmp = marker.with_suffix(".tmp")
    try:
        # Commit the no-secret tombstone first.  A crash can therefore make legacy
        # credentials unavailable, but can never resurrect them after a revocation.
        marker_tmp.write_bytes(_MIGRATION_MARKER)
        os.replace(marker_tmp, marker)
        tmp.write_bytes(_MAGIC + protected)
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
            if marker_tmp.exists():
                marker_tmp.unlink()
        except Exception:
            pass
        raise


def load_for(profile_name: str) -> dict[str, str]:
    """Internal runtime API.  Never serialize this return value to HTTP/logs."""
    name = _normal_profile(profile_name)
    with _LOCK:
        return dict(_load_all().get(name) or {})


def _separation_values(profile_name: str) -> tuple[bytes, ...]:
    """Return effective secrets solely for cross-environment equality checks.

    Stored values from the inactive profile remain part of the comparison.  Shared
    deployment environment variables replace stored values only for the explicitly
    selected profile, matching the process binding contract without ever returning a
    digest or plaintext through a public API.
    """

    name = _normal_profile(profile_name)
    values = load_for(name)
    selected = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower()
    if selected == name:
        for key, env_name in ENV_NAMES.items():
            override = str(os.getenv(env_name) or "").strip()
            if override:
                values[key] = override
    return tuple(
        value.encode("utf-8")
        for key in WORKFLOW_KEYS
        if (value := str(values.get(key) or "").strip())
    )


def profiles_share_any_credential(left_profile: str, right_profile: str) -> bool:
    """Compare profile credentials without exposing either values or digests."""

    left = _separation_values(left_profile)
    right = _separation_values(right_profile)
    return any(
        hmac.compare_digest(left_value, right_value)
        for left_value in left
        for right_value in right
    )


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


def environment_overrides(profile_name: str = "") -> dict[str, str]:
    """Return configured environment key names, optionally bound to one profile.

    The environment variable names do not encode connection identity. They are therefore
    usable for a credential lookup only when ``LAKE_MAXIS_PROFILE`` explicitly selects
    that same profile. This prevents one connection's process key from being sent while
    a manager probes another inactive candidate. Calling without a profile remains
    a browser-safe inventory of variable *names* for the global settings status.
    """

    configured = {
        key: env for key, env in ENV_NAMES.items()
        if str(os.getenv(env) or "").strip()
    }
    if not profile_name:
        return configured
    name = _normal_profile(profile_name)
    selected = str(os.getenv("LAKE_MAXIS_PROFILE") or "").strip().lower()
    return configured if selected == name else {}


def get_for(profile_name: str, workflow_key: str) -> str:
    """Resolve one key for backend use; a profile-bound environment key wins."""
    name = _normal_profile(profile_name)
    key = str(workflow_key or "").strip().lower()
    if key not in WORKFLOW_KEYS:
        raise ValueError("Unsupported MAXIS workflow key.")
    configured = environment_overrides()
    overrides = environment_overrides(name)
    if key in overrides:
        return str(os.getenv(ENV_NAMES[key]) or "").strip()
    if key in configured:
        raise CredentialStoreUnavailable(
            f"{configured[key]} requires LAKE_MAXIS_PROFILE={name} before use."
        )
    return str(load_for(name).get(key) or "").strip()


def resolved_for(profile_name: str) -> dict[str, str]:
    return {key: get_for(profile_name, key) for key in WORKFLOW_KEYS}


def status_for(profile_name: str) -> dict[str, dict[str, object]]:
    """Browser-safe status.  No suffix, digest, or plaintext key is returned."""
    name = _normal_profile(profile_name)
    configured_environment = environment_overrides()
    overrides = environment_overrides(name)
    try:
        stored = load_for(name)
        store_error = ""
    except CredentialStoreUnavailable as exc:
        stored = {}
        store_error = str(exc)
    return {
        key: {
            "configured": bool(
                key in overrides
                or (key not in configured_environment and stored.get(key))
            ),
            "source": "environment" if key in overrides else (
                "blocked" if key in configured_environment else (
                    "dpapi" if stored.get(key) else "none"
                )
            ),
            "environmentVariable": configured_environment.get(key, ""),
            "storeError": (
                f"{configured_environment[key]} is not bound to this profile."
                if key in configured_environment and key not in overrides
                else store_error
            ),
        }
        for key in WORKFLOW_KEYS
    }


def verification_digest(profile_name: str) -> str:
    """Private probe invalidation digest; never expose it through an API."""
    values = resolved_for(profile_name)
    material = "|".join(f"{key}:{values[key]}" for key in WORKFLOW_KEYS)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
