"""Immutable logical MAXIS workflow registry.

The registry intentionally excludes endpoints, credentials, and deployed revisions.  Those
belong to a selected runtime profile; this file only binds stable LTM role ids to logical Dify
applications and their wire contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ENV_RE = re.compile(r"^LAKE_MAXIS_[A-Z0-9_]+_API_KEY$")
_KNOWN_ROLES = frozenset({
    "request_architect",
    "query_specialist",
    "research_analyst",
    "knowledge_curator",
    "portfolio_analyst",
    "work_architect",
    "people_advisor",
    "auditor",
    "result_integrator",
    "editor_author",
})


class WorkflowRegistryError(ValueError):
    """The logical registry is absent, malformed, or ambiguous."""


class _RegistryRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_type: str
    endpoint: str
    response_mode: str
    conversation_mode: str
    api_key_env: str
    output_contract: str
    max_response_bytes: int = Field(ge=1024, le=4 * 1024 * 1024)
    roles: list[str] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def fixed_dify_contract(self):
        if self.app_type != "chatflow":
            raise ValueError("app_type must be chatflow")
        if self.endpoint != "/chat-messages":
            raise ValueError("endpoint must be /chat-messages")
        if self.response_mode != "blocking":
            raise ValueError("response_mode must be blocking")
        if self.conversation_mode != "stateless":
            raise ValueError("conversation_mode must be stateless")
        if not _ENV_RE.fullmatch(self.api_key_env):
            raise ValueError("api_key_env must be a dedicated LAKE_MAXIS_*_API_KEY name")
        if self.output_contract != "remote-role-result.v1":
            raise ValueError("unsupported output_contract")
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("roles must be unique")
        unknown = set(self.roles) - _KNOWN_ROLES
        if unknown:
            raise ValueError(f"unknown role ids: {sorted(unknown)}")
        return self


class _RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    workflows: dict[str, _RegistryRow]

    @model_validator(mode="after")
    def validate_document(self):
        if self.version != 1:
            raise ValueError("registry version must be 1")
        if not self.workflows:
            raise ValueError("at least one workflow is required")
        for key in self.workflows:
            if not _KEY_RE.fullmatch(key):
                raise ValueError(f"invalid workflow key: {key}")
        return self


@dataclass(frozen=True, slots=True)
class WorkflowBinding:
    key: str
    app_type: str
    endpoint: str
    response_mode: str
    conversation_mode: str
    api_key_env: str
    output_contract: str
    max_response_bytes: int
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowRegistry:
    version: int
    workflows: Mapping[str, WorkflowBinding]
    role_bindings: Mapping[str, WorkflowBinding]
    source_path: Path
    source_mtime_ns: int
    digest: str

    def resolve(self, key: str) -> WorkflowBinding:
        logical_key = str(key or "").strip()
        try:
            return self.workflows[logical_key]
        except KeyError as exc:
            raise WorkflowRegistryError(
                f"unknown MAXIS workflow binding: {logical_key or '<empty>'}"
            ) from exc

    def resolve_role(self, role_id: str) -> WorkflowBinding:
        canonical = str(role_id or "").strip()
        try:
            return self.role_bindings[canonical]
        except KeyError as exc:
            raise WorkflowRegistryError(
                f"MAXIS workflow is not bound for role: {canonical or '<empty>'}"
            ) from exc


_LOCK = threading.Lock()
_CACHE: dict[Path, tuple[int, WorkflowRegistry]] = {}


def _default_path() -> Path:
    try:
        from app.infra.settings import BASE_DIR, RESOURCE_DIR

        candidates = (
            Path(BASE_DIR) / "config" / "maxis-workflows.yml",
            Path(RESOURCE_DIR) / "config" / "maxis-workflows.yml",
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    except Exception:
        pass
    return Path(__file__).resolve().parents[3] / "config" / "maxis-workflows.yml"


def _load(path: Path, mtime_ns: int) -> WorkflowRegistry:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowRegistryError(f"cannot read MAXIS workflow registry: {path}") from exc
    try:
        document = _RegistryDocument.model_validate(raw, strict=True)
    except ValidationError as exc:
        raise WorkflowRegistryError(f"invalid MAXIS workflow registry: {path}") from exc

    canonical_document = json.dumps(
        document.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical_document).hexdigest()
    workflows: dict[str, WorkflowBinding] = {}
    role_bindings: dict[str, WorkflowBinding] = {}
    for key, row in document.workflows.items():
        binding = WorkflowBinding(
            key=key,
            app_type=row.app_type,
            endpoint=row.endpoint,
            response_mode=row.response_mode,
            conversation_mode=row.conversation_mode,
            api_key_env=row.api_key_env,
            output_contract=row.output_contract,
            max_response_bytes=row.max_response_bytes,
            roles=tuple(row.roles),
        )
        workflows[key] = binding
        for role_id in binding.roles:
            if role_id in role_bindings:
                raise WorkflowRegistryError(
                    f"role is bound to multiple MAXIS workflows: {role_id}"
                )
            role_bindings[role_id] = binding

    return WorkflowRegistry(
        version=document.version,
        workflows=MappingProxyType(workflows),
        role_bindings=MappingProxyType(role_bindings),
        source_path=path,
        source_mtime_ns=mtime_ns,
        digest=digest,
    )


def registry_fingerprint(path: str | Path | None = None) -> str:
    """Return the canonical validated registry digest, independent of path/mtime."""
    return load_registry(path).digest


def load_registry(path: str | Path | None = None) -> WorkflowRegistry:
    """Load and cache one immutable registry, invalidating it only on file mtime change."""

    registry_path = Path(path).resolve() if path is not None else _default_path().resolve()
    try:
        mtime_ns = registry_path.stat().st_mtime_ns
    except OSError as exc:
        raise WorkflowRegistryError(
            f"MAXIS workflow registry does not exist: {registry_path}"
        ) from exc
    with _LOCK:
        cached = _CACHE.get(registry_path)
        if cached and cached[0] == mtime_ns:
            return cached[1]
        registry = _load(registry_path, mtime_ns)
        _CACHE[registry_path] = (mtime_ns, registry)
        return registry


def reset_registry_cache() -> None:
    with _LOCK:
        _CACHE.clear()


__all__ = [
    "WorkflowBinding",
    "WorkflowRegistry",
    "WorkflowRegistryError",
    "load_registry",
    "registry_fingerprint",
    "reset_registry_cache",
]
