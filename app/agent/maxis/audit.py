"""Metadata-only audit event helpers for remote workflow calls."""

from __future__ import annotations

import json
import os
import stat
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.maxis.context_guard import ContextGuardError, assert_safe_outbound_text


DEFAULT_AUDIT_PATH = Path(".cache") / "maxis_audit.jsonl"
_AUDIT_LOCK = threading.Lock()
_SAFE_CODE = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_OPTIONAL_SAFE_CODE = r"^(?:|[A-Za-z0-9][A-Za-z0-9._:-]*)$"


class AuditEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    contract: Literal["maxis-audit-event.v1"] = "maxis-audit-event.v1"
    event_id: Annotated[
        str, Field(pattern=r"^[0-9a-f]{32}$")
    ]
    occurred_at: datetime
    correlation_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    ]
    event: Annotated[
        str, Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")
    ]
    status: Literal["started", "succeeded", "failed", "blocked"]
    profile: Annotated[str, Field(min_length=1, max_length=64, pattern=_SAFE_CODE)]
    stage: Literal["dev", "prod", "test"]
    route_mode: Literal["local", "shadow", "maxis", "disabled"]
    workflow_key: Annotated[
        str, Field(min_length=1, max_length=64, pattern=_SAFE_CODE)
    ]
    workflow_version: Annotated[
        str, Field(default="", max_length=120, pattern=_OPTIONAL_SAFE_CODE)
    ] = ""
    knowledge_version: Annotated[
        str, Field(default="", max_length=120, pattern=_OPTIONAL_SAFE_CODE)
    ] = ""
    role_id: Annotated[
        str, Field(default="", max_length=64, pattern=_OPTIONAL_SAFE_CODE)
    ] = ""
    latency_ms: int = Field(default=0, ge=0, le=3_600_000)
    input_bytes: int = Field(default=0, ge=0, le=20_000_000)
    output_bytes: int = Field(default=0, ge=0, le=20_000_000)
    source_count: int = Field(default=0, ge=0, le=10)
    citation_count: int = Field(default=0, ge=0, le=100)
    warning_count: int = Field(default=0, ge=0, le=100)
    error_code: Annotated[
        str, Field(default="", max_length=80, pattern=r"^(?:|[a-z][a-z0-9_.-]*)$")
    ] = ""
    flags: tuple[
        Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")],
        ...,
    ] = Field(default=(), max_length=16)
    request_sha256: Annotated[
        str, Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    ] = ""

    @model_validator(mode="after")
    def validate_event(self):
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        if self.status in {"failed", "blocked"} and not self.error_code:
            raise ValueError("failed and blocked events require an error_code")
        if self.status in {"started", "succeeded"} and self.error_code:
            raise ValueError("successful lifecycle events cannot carry an error_code")
        if len(self.flags) != len(set(self.flags)):
            raise ValueError("flags must be unique")
        return self


class AuditCountV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    key: Annotated[str, Field(min_length=1, max_length=80)]
    count: int = Field(ge=1)


class AuditLogSummaryV1(BaseModel):
    """Bounded aggregate view; individual correlation rows are not returned."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    contract: Literal["maxis-audit-summary.v1"] = "maxis-audit-summary.v1"
    total_rows: int = Field(ge=0)
    active_rows: int = Field(ge=0)
    rotated_rows: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    status_counts: tuple[AuditCountV1, ...] = ()
    event_counts: tuple[AuditCountV1, ...] = ()
    error_counts: tuple[AuditCountV1, ...] = ()
    latest_at: datetime | None = None


def make_audit_event(
    *,
    correlation_id: str,
    event: str,
    status: Literal["started", "succeeded", "failed", "blocked"],
    profile: str,
    stage: Literal["dev", "prod", "test"],
    route_mode: Literal["local", "shadow", "maxis", "disabled"],
    workflow_key: str,
    workflow_version: str = "",
    knowledge_version: str = "",
    role_id: str = "",
    latency_ms: int = 0,
    input_bytes: int = 0,
    output_bytes: int = 0,
    source_count: int = 0,
    citation_count: int = 0,
    warning_count: int = 0,
    error_code: str = "",
    flags: tuple[str, ...] = (),
    request_sha256: str = "",
    occurred_at: datetime | None = None,
) -> AuditEventV1:
    """Build a safe audit row; no prompt, answer, payload, key, or token parameter exists."""

    return AuditEventV1(
        event_id=uuid4().hex,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        correlation_id=correlation_id,
        event=event,
        status=status,
        profile=profile,
        stage=stage,
        route_mode=route_mode,
        workflow_key=workflow_key,
        workflow_version=workflow_version,
        knowledge_version=knowledge_version,
        role_id=role_id,
        latency_ms=latency_ms,
        input_bytes=input_bytes,
        output_bytes=output_bytes,
        source_count=source_count,
        citation_count=citation_count,
        warning_count=warning_count,
        error_code=error_code,
        flags=flags,
        request_sha256=request_sha256,
    )


def audit_log_fields(event: AuditEventV1) -> dict:
    """Return JSON-safe metadata suitable for a structured logger or append-only ledger."""

    if not isinstance(event, AuditEventV1):
        raise TypeError("AuditEventV1 required")
    row = event.model_dump(mode="json")
    try:
        for key, value in row.items():
            if isinstance(value, str):
                assert_safe_outbound_text(value, f"audit.{key}")
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        assert_safe_outbound_text(item, f"audit.{key}")
    except ContextGuardError as exc:
        raise ValueError("unsafe audit metadata") from exc
    return row


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".1")


def _resolved_path(path: str | os.PathLike[str] | Path | None) -> Path:
    if path is not None:
        return Path(path)
    try:
        # Resolve on every call: packaged builds and tests can select their cache root
        # after this module has already been imported.
        from app.infra import settings as app_settings

        return Path(app_settings.CACHE_DIR) / "maxis_audit.jsonl"
    except (ImportError, AttributeError, TypeError):
        return DEFAULT_AUDIT_PATH


def _row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as stream:
        return sum(1 for _ in stream)


def _secure_append(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    descriptor = os.open(str(path), flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("audit append made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows ACL ownership remains the controlling boundary when POSIX modes are
        # unavailable. The create call above still avoids world-write mode on POSIX.
        pass


def record_audit_event(
    event: AuditEventV1,
    *,
    path: str | os.PathLike[str] | Path | None = None,
    max_rows: int = 5000,
    max_bytes: int = 2 * 1024 * 1024,
) -> Path:
    """Append one metadata-only row with single-backup size/row rotation."""

    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= 100_000:
        raise ValueError("max_rows must be between 1 and 100000")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1024 <= max_bytes <= 64 * 1024 * 1024:
        raise ValueError("max_bytes must be between 1024 and 67108864")
    row = audit_log_fields(event)
    encoded = (
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("audit event exceeds max_bytes")
    target = _resolved_path(path)
    if target.name in {"", ".", ".."}:
        raise ValueError("audit file path required")
    with _AUDIT_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise ValueError("audit file cannot be a symlink")
        current_size = target.stat().st_size if target.exists() else 0
        rotate = (
            target.exists()
            and (current_size + len(encoded) > max_bytes or _row_count(target) >= max_rows)
        )
        if rotate:
            backup = _backup_path(target)
            if backup.is_symlink():
                raise ValueError("audit backup cannot be a symlink")
            if backup.exists():
                backup.unlink()
            os.replace(target, backup)
        _secure_append(target, encoded)
    return target


def _read_rows(path: Path, *, max_read_bytes: int) -> list[AuditEventV1]:
    if not path.exists():
        return []
    if path.is_symlink() or path.stat().st_size > max_read_bytes:
        raise ValueError("audit ledger cannot be read safely")
    rows: list[AuditEventV1] = []
    try:
        with path.open("rb") as stream:
            for line in stream:
                if not line.strip():
                    raise ValueError("empty audit row")
                rows.append(AuditEventV1.model_validate_json(line, strict=True))
    except Exception as exc:
        raise ValueError("invalid audit ledger") from exc
    return rows


def _counts(values) -> tuple[AuditCountV1, ...]:
    return tuple(
        AuditCountV1(key=key, count=count)
        for key, count in sorted(Counter(values).items())
        if key
    )


def read_audit_summary(
    *,
    path: str | os.PathLike[str] | Path | None = None,
    include_rotated: bool = True,
    max_read_bytes: int = 4 * 1024 * 1024,
) -> AuditLogSummaryV1:
    """Read only validated rows and return counts, never prompt/output content."""

    if not isinstance(include_rotated, bool):
        raise ValueError("include_rotated must be boolean")
    if (
        isinstance(max_read_bytes, bool)
        or not isinstance(max_read_bytes, int)
        or not 1024 <= max_read_bytes <= 128 * 1024 * 1024
    ):
        raise ValueError("invalid max_read_bytes")
    target = _resolved_path(path)
    backup = _backup_path(target)
    with _AUDIT_LOCK:
        rotated = _read_rows(backup, max_read_bytes=max_read_bytes) if include_rotated else []
        active = _read_rows(target, max_read_bytes=max_read_bytes)
        total_bytes = sum(
            candidate.stat().st_size
            for candidate in ((backup,) if include_rotated else ()) + (target,)
            if candidate.exists()
        )
    rows = rotated + active
    latest = max((row.occurred_at for row in rows), default=None)
    return AuditLogSummaryV1(
        total_rows=len(rows),
        active_rows=len(active),
        rotated_rows=len(rotated),
        total_bytes=total_bytes,
        status_counts=_counts(row.status for row in rows),
        event_counts=_counts(row.event for row in rows),
        error_counts=_counts(row.error_code for row in rows),
        latest_at=latest,
    )


__all__ = [
    "AuditCountV1",
    "AuditEventV1",
    "AuditLogSummaryV1",
    "DEFAULT_AUDIT_PATH",
    "audit_log_fields",
    "make_audit_event",
    "read_audit_summary",
    "record_audit_event",
]
