"""Runtime boundary between canonical LTM Roles and MAXIS/Dify workflows.

This module deliberately sits above the HTTP client and profile registry.  Workflow
Roles depend only on the small :class:`RoleRuntime` protocol below, while the concrete
adapter is loaded lazily so a local-only LTM installation keeps working without MAXIS
configuration.  A ``maxis`` route never falls back to the local LLM: an unavailable or
invalid remote result is a visible failure.  ``shadow`` runs are observational only and
their payload can never reach LangGraph state, approval, or the UI.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from app.agent.maxis.telemetry import with_result_status
from app.agent.maxis.recovery import BlockedRecovery, blocked_recovery


log = logging.getLogger("agent.maxis.runtime")
_request_observation: ContextVar[dict | None] = ContextVar(
    "maxis_runtime_request_observation", default=None,
)

RuntimeRoute = Literal["local", "shadow", "maxis"]
_ROUTES = frozenset({"local", "shadow", "maxis"})
_PRIVATE_STATE_KEYS = frozenset({
    "approval_token", "comment_token", "execution_receipt", "question_receipt_projection",
    "change_audit_binding",
})
_PSEUDONYM_KEY = secrets.token_bytes(32)
_EVIDENCE_ATTESTATION_KEY = secrets.token_bytes(32)
_SYNTHETIC_USER_IDENTITY = "synthetic-maxis-dev-user"
_SYNTHETIC_SOURCE_ID = "synthetic_dev_fixture"
_SYNTHETIC_CANONICAL_ID = "ltm:maxis-dev:synthetic-fixture"
_SYNTHETIC_TITLE = "Synthetic MAXIS development fixture"
_USER_INTENT_SOURCE_TITLE = "LTM guarded user intent"
_PERSON_REFERENCE = re.compile(r"\[\[person:([0-9a-f]{12})\]\]")
_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$")
REMOTE_CONTRACT_BUILD_VERSION = "ltm-maxis-runtime.v1"
_TEXT_PAYLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"text": {"type": "string", "minLength": 1}},
    "required": ["text"],
}
_QUERY_SOURCE_KINDS = {
    "jira": "jira",
    "comments": "jira",
    "confluence": "confluence",
    "bitbucket": "bitbucket",
    "knowledge": "knowledge",
    "people": "ltm",
}
# Research Analyst consumes these locally and projects curated findings into later
# Role context.  The raw rows never cross MAXIS until EvidencePack gains an explicit
# public-source host/scope contract.
_LOCAL_ONLY_QUERY_SOURCES = frozenset({"web", "github"})
class MaxisRuntimeError(RuntimeError):
    """A fail-closed runtime/profile/remote-contract error."""

    _numeric_regeneration_allowed = False


def _numeric_regeneration_binding_matches(answer, *, role_id, workflow_version,
                                         knowledge_version, knowledge_manifest_sha256,
                                         knowledge_sentinel_sha256):
    """A rejected draft can get feedback only with all immutable bindings intact.

    Called after ResponseGuard has parsed the envelope but rejected its numbers.
    This check never accepts content; every fresh draft still passes both guards.
    """
    from app.agent.maxis.contracts import RemoteRoleResultV1
    try:
        envelope = RemoteRoleResultV1.model_validate_json(answer, strict=True)
        return bool(all((role_id, workflow_version, knowledge_version,
                         knowledge_manifest_sha256, knowledge_sentinel_sha256))
            and envelope.status == "ok" and envelope.role_id == role_id
            and envelope.workflow_version == workflow_version
            and envelope.knowledge_version == knowledge_version
            and envelope.knowledge_manifest_sha256 == knowledge_manifest_sha256
            and hmac.compare_digest(hashlib.sha256(envelope.knowledge_proof.encode()).hexdigest(),
                                    knowledge_sentinel_sha256))
    except (TypeError, ValueError):
        return False


def _capture_failed_request(function):
    """Carry only validated, content-free observations across exception boundaries."""
    @wraps(function)
    def invoke_with_observation(*args, **kwargs):
        captured: dict = {}
        token = _request_observation.set(captured)
        try:
            return function(*args, **kwargs)
        except MaxisRuntimeError as exc:
            if captured and not isinstance(exc, MaxisNeedsInput):
                exc.metadata = dict(captured)
            raise
        finally:
            _request_observation.reset(token)
    return invoke_with_observation


class MaxisBlockedResult(MaxisRuntimeError):
    """Validated refusal with local guidance, never a draft or an interview."""

    def __init__(self, role_id: str, recovery: BlockedRecovery):
        self.role_id = role_id
        self.recovery = recovery
        super().__init__(recovery.message + " " + recovery.next_action)


class MaxisNeedsInput(MaxisRuntimeError):
    """A validated remote interview outcome, distinct from transport failure."""

    def __init__(
        self,
        role_id: str,
        missing_inputs,
        *,
        state_patch: dict | None = None,
        metadata: dict | None = None,
    ):
        super().__init__(f"MAXIS Role에 사용자 입력이 필요합니다: {role_id}")
        self.role_id = str(role_id or "")[:128]
        self.missing_inputs = tuple(
            str(value or "").strip()[:500]
            for value in (missing_inputs or ())
            if str(value or "").strip()
        )[:8]
        self.state_patch = dict(state_patch or {})
        self.metadata = dict(metadata or {})


class RuntimeProfileChanged(MaxisRuntimeError):
    """The current runtime no longer matches the profile pinned to a thread."""


@dataclass(frozen=True)
class RoleResolution:
    """One canonical Role's immutable execution decision for the current thread."""

    route: RuntimeRoute
    binding: Any = None
    profile: str = ""
    profile_fingerprint: str = ""
    workflow_bundle_version: str = ""
    knowledge_version: str = ""
    knowledge_dataset_id: str = ""
    knowledge_manifest_sha256: str = ""
    remote_log_policy_id: str = ""
    remote_log_mode: str = ""
    remote_log_retention_days: int | None = None


@dataclass(frozen=True)
class AcquisitionMarker:
    """Opaque request ledger shared with explicitly propagated worker contexts."""

    cache: Any
    cursor: Any


class RemoteRoleOutput(dict):
    """A schema-compatible Role payload carrying an explicit, per-call safe sidecar."""

    def __init__(
        self,
        value: dict,
        state_patch: dict | None = None,
        metadata: dict | None = None,
    ):
        super().__init__(value)
        self.state_patch = dict(state_patch or {})
        self.metadata = dict(metadata or {})


def _person_reference_marker(identity: str) -> str:
    digest = hmac.new(
        _PSEUDONYM_KEY, identity.casefold().encode("utf-8"), "sha256"
    ).hexdigest()[:12]
    return f"[[person:{digest}]]"


def editor_person_reference_map(values) -> dict[str, str]:
    """Build an in-process opaque-ref -> Jira mention map for Editor Author.

    The map is deliberately never serialized into ``EvidencePackV1``. Dify sees only
    the HMAC-derived ``[[person:...]]`` marker already produced by ``ContextGuard``;
    after the remote response passes ``ResponseGuard``, LTM can restore only a marker
    that was present in the locally verified ticket context for this one call.
    """
    if isinstance(values, (str, bytes)):
        values = (values,)
    result: dict[str, str] = {}
    for value in values or ():
        identity = str(value or "").strip()
        if (
            not 2 <= len(identity) <= 128
            or any(character.isspace() for character in identity)
            or any(character in identity for character in "[]<>'\"")
        ):
            continue
        result[_person_reference_marker(identity)] = f"[~{identity}]"
    return result


def _rehydrate_editor_person_references(value: Any, references: dict[str, str]) -> Any:
    """Restore exact authorized Editor refs and reject every unknown marker."""
    if isinstance(value, str):
        markers = set(match.group(0) for match in _PERSON_REFERENCE.finditer(value))
        unknown = markers - set(references)
        if unknown:
            raise MaxisRuntimeError(
                "MAXIS Editor 응답에 검증되지 않은 person reference가 있습니다."
            )
        restored = value
        for marker in sorted(markers):
            restored = restored.replace(marker, references[marker])
        return restored
    if isinstance(value, list):
        return [_rehydrate_editor_person_references(item, references) for item in value]
    if isinstance(value, tuple):
        return tuple(_rehydrate_editor_person_references(item, references) for item in value)
    if isinstance(value, dict):
        return {
            str(key): _rehydrate_editor_person_references(item, references)
            for key, item in value.items()
        }
    return value


_READONLY_PERSON_ROLES = frozenset({"knowledge_curator", "portfolio_analyst", "result_integrator"})


def _rehydrate_readonly_role_people(role_id, value, evidence, identities, kinds):
    """Resolve people after response validation; never grant draft/assignee authority."""
    if role_id not in _READONLY_PERSON_ROLES:
        return value
    from app.agent.maxis.person_references import result_person_references
    try:
        references = result_person_references(
            _json_value(value), evidence, identities, _person_reference_marker, kinds,
        )
    except Exception as exc:
        raise MaxisRuntimeError(
            "MAXIS 답변의 사용자 참조를 확인하지 못했습니다. "
            "Jira 사용자 연결과 동명이인 여부를 확인하세요."
        ) from exc
    return _rehydrate_editor_person_references(value, references) if references else value


@runtime_checkable
class RoleRuntime(Protocol):
    """Minimum contract used by the workflow runtime.

    Concrete adapters may wrap the local MAXIS profile/credential store and the Dify
    HTTP client.  Tests use the same contract without network or secret access.
    """

    def resolve_role(self, role_id: str, state: dict) -> RoleResolution: ...

    def invoke(
        self,
        role_id: str,
        payload: dict,
        thread_id: str,
        correlation_id: str,
    ) -> dict: ...

    def profile_fingerprint(self) -> str: ...


def _remote_execution_allowed(role_id: str) -> bool:
    from app.agent.workflow.role_manifest import remote_execution_allowed

    return remote_execution_allowed(role_id)


def _canonical_role_routes(*, explicit_engine: str = "") -> dict[str, RuntimeRoute]:
    """Snapshot every canonical remote-capable Role route without loading a client."""
    from app.agent.workflow.role_manifest import ROLE_SPECS

    override = str(explicit_engine or "").strip().lower()
    if override and override not in _ROUTES:
        raise MaxisRuntimeError("engine은 local, shadow, maxis 중 하나여야 합니다.")
    if override:
        return {
            role_id: (override if _remote_execution_allowed(role_id) else "local")
            for role_id in ROLE_SPECS
        }

    try:
        from app.agent.maxis import profiles
    except Exception:
        # A missing optional MAXIS package is a local installation, not permission to
        # reinterpret an explicitly remote thread (handled by the caller above).
        return {role_id: "local" for role_id in ROLE_SPECS}

    routes: dict[str, RuntimeRoute] = {}
    for role_id in ROLE_SPECS:
        if not _remote_execution_allowed(role_id):
            routes[role_id] = "local"
            continue
        try:
            route = str(profiles.route_for(role_id) or "local").strip().lower()
        except Exception as exc:
            raise MaxisRuntimeError(
                f"MAXIS Role route를 확인하지 못했습니다: {role_id}"
            ) from exc
        if route not in _ROUTES:
            raise MaxisRuntimeError(f"지원하지 않는 MAXIS route입니다: {role_id}")
        routes[role_id] = route  # type: ignore[assignment]
    return routes


def _profile_snapshot(profile_name: str = "", *, required: bool = False) -> dict:
    """Return safe profile metadata; never read or return an API key."""
    try:
        from app.agent.maxis import profiles
    except Exception as exc:
        if required:
            raise MaxisRuntimeError("MAXIS 프로필 모듈을 불러올 수 없습니다.") from exc
        return {}

    try:
        profile = profiles.resolve(profile_name) if profile_name else profiles.active()
    except Exception as exc:
        if required:
            raise MaxisRuntimeError("활성 MAXIS 프로필을 확인할 수 없습니다.") from exc
        return {}
    if not profile:
        if required:
            raise MaxisRuntimeError("활성 MAXIS 프로필이 없습니다.")
        return {}
    if required:
        if profiles.kill_switch_enabled():
            raise MaxisRuntimeError("MAXIS kill switch가 활성화되어 있습니다.")
        if not bool(profile.get("enabled")):
            raise MaxisRuntimeError("검증·활성화된 MAXIS 프로필이 아닙니다.")
        # Connection identity is independent of the Jira environment. Outbound data
        # follows the verified profile's explicit policy, never its display name.
    return {
        "name": str(profile.get("name") or ""),
        "display_name": str(profile.get("display_name") or "MAXIS 연결"),
        "stage": str(profile.get("stage") or ""),
        "fingerprint": str(profile.get("fingerprint") or ""),
        "base_url": str(profile.get("base_url") or ""),
        "data_policy": str(profile.get("data_policy") or ""),
        "workflow_bundle_version": str(profile.get("workflow_bundle_version") or ""),
        "knowledge_version": str(profile.get("knowledge_version") or ""),
        "knowledge_dataset_id": str(profile.get("knowledge_dataset_id") or ""),
        "knowledge_manifest_sha256": str(
            profile.get("knowledge_manifest_sha256") or ""
        ),
        "knowledge_sentinel_sha256": str(
            profile.get("knowledge_sentinel_sha256") or ""
        ),
        "knowledge_document_count": int(profile.get("knowledge_document_count") or 0),
        "remote_log_policy_id": str(profile.get("remote_log_policy_id") or ""),
        "remote_log_mode": str(profile.get("remote_log_mode") or ""),
        "remote_log_retention_days": profile.get("remote_log_retention_days"),
        "workflows": profile.get("workflows") if isinstance(profile.get("workflows"), dict) else {},
        "enabled": bool(profile.get("enabled")),
    }


def _require_authoritative_routes(metadata: dict, role_routes: dict[str, str]) -> None:
    """Apply the profile authority gate to every requested ``maxis`` Role route."""

    authoritative_roles = sorted(
        role_id for role_id, route in role_routes.items() if route == "maxis"
    )
    if not authoritative_roles:
        return
    try:
        from app.agent.maxis import profiles

        workflow_keys = {
            profiles._workflow_key(role_id)  # package-private canonical mapping
            for role_id in authoritative_roles
        }
        profiles.require_authoritative(
            str(metadata.get("name") or ""), workflow_keys,
        )
    except Exception as exc:
        raise MaxisRuntimeError(
            "MAXIS authoritative route가 release security gate에 의해 차단되었습니다: "
            + str(exc)
        ) from exc


def _registry_digest(*, required: bool) -> str:
    try:
        from app.agent.maxis.registry import registry_fingerprint

        return registry_fingerprint()
    except Exception as exc:
        if required:
            raise MaxisRuntimeError("MAXIS Workflow registry digest를 확인하지 못했습니다.") from exc
        return "registry-unavailable"


def _runtime_fingerprint(
    profile: dict,
    role_routes: dict[str, str],
    *,
    registry_digest: str,
    contract_build: str = REMOTE_CONTRACT_BUILD_VERSION,
) -> str:
    """Pin both profile content and feature routing without hashing any secret."""
    material = {
        "profile": str(profile.get("fingerprint") or "local-runtime.v1"),
        "routes": {key: role_routes[key] for key in sorted(role_routes)},
        "registry": str(registry_digest or "registry-unavailable"),
        "contractBuild": str(contract_build or ""),
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def profile_fingerprint() -> str:
    """Return the active profile + route fingerprint used by new Agent threads."""
    routes = _canonical_role_routes()
    remote = any(route != "local" for route in routes.values())
    profile = _profile_snapshot(required=remote)
    return _runtime_fingerprint(
        profile, routes, registry_digest=_registry_digest(required=remote),
    )


def thread_runtime_pin(*, engine: str = "", profile: str = "") -> dict:
    """Create the immutable runtime sidecar stored at the first turn of a thread."""
    routes = _canonical_role_routes(explicit_engine=engine)
    remote = any(route != "local" for route in routes.values())
    profile_name = str(profile or "").strip()
    # An all-local thread has no MAXIS dependency unless its caller deliberately pins
    # a named profile.  Reading the active candidate here used to bind unrelated local
    # conversations to later control-plane edits and made them fail on resume.
    metadata = (
        _profile_snapshot(profile_name, required=remote)
        if remote or profile_name
        else {}
    )
    _require_authoritative_routes(metadata, routes)
    registry_digest = _registry_digest(required=remote)
    fingerprint = _runtime_fingerprint(
        metadata, routes, registry_digest=registry_digest,
    )
    core_route = routes.get("request_architect", "local")
    return {
        "runtime_engine": core_route,
        "runtime_profile": str(metadata.get("name") or ""),
        "runtime_profile_label": str(metadata.get("display_name") or "MAXIS 연결") if remote else "",
        "runtime_profile_fingerprint": fingerprint,
        "runtime_role_routes": dict(routes),
        "runtime_engine_source": "explicit" if engine else "profile",
        "runtime_registry_digest": registry_digest,
        "runtime_contract_build": REMOTE_CONTRACT_BUILD_VERSION,
        "workflow_bundle_version": str(metadata.get("workflow_bundle_version") or ""),
        "knowledge_version": str(metadata.get("knowledge_version") or ""),
        "knowledge_dataset_id": str(metadata.get("knowledge_dataset_id") or ""),
        "knowledge_manifest_sha256": str(
            metadata.get("knowledge_manifest_sha256") or ""
        ),
    }


def _pin_from_state(state: dict) -> dict:
    return {
        key: state.get(key)
        for key in (
            "runtime_engine", "runtime_profile", "runtime_profile_label", "runtime_profile_fingerprint",
            "runtime_role_routes", "runtime_engine_source", "workflow_bundle_version",
            "knowledge_version", "knowledge_dataset_id", "knowledge_manifest_sha256",
            "runtime_registry_digest", "runtime_contract_build",
        )
        if state.get(key) not in (None, "")
    }


def verify_thread_runtime(state: dict, *, engine: str = "", profile: str = "") -> None:
    """Refuse a turn/resume when its active profile or route snapshot changed."""
    pinned = str(state.get("runtime_profile_fingerprint") or "").strip()
    if not pinned:
        raise RuntimeProfileChanged(
            "이 대화에는 고정된 Agent 실행 프로필이 없습니다. 새 대화를 시작해 주세요."
        )
    if (not state.get("runtime_registry_digest")
            or not state.get("runtime_contract_build")):
        raise RuntimeProfileChanged(
            "이 대화에는 고정된 MAXIS registry/contract 정보가 없습니다. 새 대화를 시작해 주세요."
        )
    requested_engine = str(engine or "").strip().lower()
    if requested_engine and requested_engine != str(state.get("runtime_engine") or "local"):
        raise RuntimeProfileChanged(
            "이 대화가 시작된 Agent 실행 경로와 요청한 경로가 다릅니다. 새 대화를 시작해 주세요."
        )
    requested_profile = str(profile or "").strip()
    if requested_profile and requested_profile != str(state.get("runtime_profile") or ""):
        raise RuntimeProfileChanged(
            "이 대화가 시작된 MAXIS 프로필과 요청한 프로필이 다릅니다. 새 대화를 시작해 주세요."
        )

    source = str(state.get("runtime_engine_source") or "profile")
    explicit = str(state.get("runtime_engine") or "") if source == "explicit" else ""
    current_routes = _canonical_role_routes(explicit_engine=explicit)
    pinned_routes = state.get("runtime_role_routes") or {}
    if dict(current_routes) != dict(pinned_routes):
        raise RuntimeProfileChanged(
            "이 대화를 시작한 뒤 MAXIS Workflow route가 변경되었습니다. 새 대화를 시작해 주세요."
        )
    remote = any(route != "local" for route in current_routes.values())
    pinned_profile_name = str(state.get("runtime_profile") or "").strip()
    # Legacy checkpoints are migrated to an explicit all-local pin with no profile.
    # Keep that pin independent from a MAXIS candidate that may later become active;
    # otherwise a harmless control-plane change would invalidate an unrelated local
    # conversation.  New local pins that deliberately captured a named profile keep
    # their original profile binding and continue to be checked below.
    metadata = (
        _profile_snapshot(pinned_profile_name, required=remote)
        if remote or pinned_profile_name
        else {}
    )
    _require_authoritative_routes(metadata, current_routes)
    current_registry = _registry_digest(required=remote)
    if remote and (
        str(state.get("knowledge_dataset_id") or "")
        != str(metadata.get("knowledge_dataset_id") or "")
        or str(state.get("knowledge_manifest_sha256") or "")
        != str(metadata.get("knowledge_manifest_sha256") or "")
    ):
        raise RuntimeProfileChanged(
            "이 대화를 시작한 뒤 MAXIS Knowledge dataset/manifest가 변경되었습니다. "
            "새 대화를 시작해 주세요."
        )
    if (str(state.get("runtime_registry_digest")) != current_registry
            or str(state.get("runtime_contract_build")) != REMOTE_CONTRACT_BUILD_VERSION
            or _runtime_fingerprint(
                metadata, current_routes, registry_digest=current_registry,
            ) != pinned):
        raise RuntimeProfileChanged(
            "이 대화를 시작한 뒤 MAXIS 프로필이 변경되었습니다. 새 대화를 시작해 주세요."
        )


def _legacy_checkpoint_is_clearly_local(prior: dict) -> bool:
    """Return whether an unpinned checkpoint has no evidence of remote execution.

    Pre-MAXIS checkpoints have no runtime fields at all.  Partially written newer
    checkpoints may have lost their fingerprint, so every surviving runtime marker
    must still be compatible with an all-local conversation before migration is
    allowed.  Ambiguous or remote-bearing state is deliberately not repaired.
    """

    if not prior or prior.get("runtime_profile_fingerprint"):
        return False
    if any(str(prior.get(key) or "").strip() for key in (
        "runtime_profile",
        "runtime_registry_digest",
        "runtime_contract_build",
        "workflow_bundle_version",
        "knowledge_version",
        "knowledge_dataset_id",
        "knowledge_manifest_sha256",
    )):
        return False
    for key in ("runtime_engine", "runtime_last_role_engine"):
        value = str(prior.get(key) or "").strip().lower()
        if value not in {"", "local"}:
            return False
    if str(prior.get("runtime_engine_source") or "").strip().lower() not in {
        "", "explicit",
    }:
        return False
    if "runtime_role_routes" in prior:
        routes = prior.get("runtime_role_routes")
        if not isinstance(routes, dict) or not routes or any(
            str(route or "").strip().lower() != "local"
            for route in routes.values()
        ):
            return False
    if "runtime_role_engines" in prior:
        engines = prior.get("runtime_role_engines")
        # LangGraph materializes the reducer identity (``{}``) when it hydrates
        # checkpoints created before this field existed.  An empty map therefore
        # carries no remote-execution evidence; populated or malformed state still
        # has to prove that every recorded execution was local.
        if not isinstance(engines, dict) or any(
            str(engine or "").strip().lower() != "local"
            for engine in engines.values()
        ):
            return False
    # These reducers exist only in the MAXIS-era runtime.  Non-empty values without
    # their immutable pin are ambiguous even when their public fields look harmless.
    if any(prior.get(key) for key in (
        "runtime_role_telemetry",
        "runtime_role_sidecars",
        "runtime_role_questions",
    )):
        return False
    return True


def prepare_thread_runtime(
    prior: dict,
    *,
    engine: str = "",
    profile: str = "",
) -> dict:
    """Reuse one thread pin or create it before the first graph invocation."""
    if prior and prior.get("runtime_profile_fingerprint"):
        verify_thread_runtime(prior, engine=engine, profile=profile)
        return _pin_from_state(prior)
    if prior:
        # Checkpoints created before the runtime contract cannot safely become remote
        # mid-conversation.  They remain explicitly local and acquire a stable migration pin.
        if (
            str(engine or "").strip().lower() in {"shadow", "maxis"}
            or profile
            or not _legacy_checkpoint_is_clearly_local(prior)
        ):
            raise RuntimeProfileChanged(
                "기존 대화는 MAXIS 실행 경로로 전환할 수 없습니다. 새 대화를 시작해 주세요."
            )
        from app.agent.workflow.role_manifest import ROLE_SPECS

        routes = {role_id: "local" for role_id in ROLE_SPECS}
        return {
            "runtime_engine": "local",
            "runtime_profile": "",
            "runtime_profile_fingerprint": _runtime_fingerprint(
                {}, routes, registry_digest=_registry_digest(required=False),
            ),
            "runtime_role_routes": routes,
            "runtime_engine_source": "explicit",
            "runtime_registry_digest": _registry_digest(required=False),
            "runtime_contract_build": REMOTE_CONTRACT_BUILD_VERSION,
            "workflow_bundle_version": "",
            "knowledge_version": "",
            "knowledge_dataset_id": "",
            "knowledge_manifest_sha256": "",
        }
    return thread_runtime_pin(engine=engine, profile=profile)


def resolve_role(role_id: str, state: dict) -> RoleResolution:
    """Resolve a canonical Role and keep service/write Roles permanently local."""
    if not _remote_execution_allowed(role_id):
        return RoleResolution(route="local")

    pinned = bool(state.get("runtime_profile_fingerprint"))
    pinned_routes = state.get("runtime_role_routes")
    if pinned:
        # A thread pin is an immutable decision for every canonical Role.  Falling
        # back to today's global setting when one entry disappeared from a damaged
        # checkpoint could silently turn a previously authoritative MAXIS Role into
        # a local execution.  Validate the complete pin before *any* local return.
        if not isinstance(pinned_routes, dict) or role_id not in pinned_routes:
            raise RuntimeProfileChanged(
                "이 대화의 MAXIS Role route pin이 누락되거나 손상되었습니다. "
                "새 대화를 시작해 주세요."
            )
        verify_thread_runtime(state)
        route = str(pinned_routes.get(role_id) or "local").lower()
    elif isinstance(pinned_routes, dict) and role_id in pinned_routes:
        route = str(pinned_routes.get(role_id) or "local").lower()
    else:
        route = str(_canonical_role_routes().get(role_id) or "local").lower()
    if route not in _ROUTES:
        raise MaxisRuntimeError(f"지원하지 않는 MAXIS route입니다: {role_id}")
    if route == "local":
        return RoleResolution(route="local")

    profile_name = str(state.get("runtime_profile") or "")
    metadata = _profile_snapshot(profile_name, required=True)
    _require_authoritative_routes(metadata, {role_id: route})
    try:
        from app.agent.maxis.registry import load_registry

        binding = load_registry().resolve_role(role_id)
    except Exception as exc:
        raise MaxisRuntimeError(
            f"MAXIS Workflow binding을 확인하지 못했습니다: {role_id}"
        ) from exc
    return RoleResolution(
        route=route,  # type: ignore[arg-type]
        binding=binding,
        profile=str(metadata.get("name") or ""),
        profile_fingerprint=str(state.get("runtime_profile_fingerprint")
                                or metadata.get("fingerprint") or ""),
        workflow_bundle_version=str(metadata.get("workflow_bundle_version") or ""),
        knowledge_version=str(metadata.get("knowledge_version") or ""),
        knowledge_dataset_id=str(metadata.get("knowledge_dataset_id") or ""),
        knowledge_manifest_sha256=str(
            metadata.get("knowledge_manifest_sha256") or ""
        ),
        remote_log_policy_id=str(metadata.get("remote_log_policy_id") or ""),
        remote_log_mode=str(metadata.get("remote_log_mode") or ""),
        remote_log_retention_days=metadata.get("remote_log_retention_days"),
    )


def _json_value(value: Any) -> Any:
    """Make bounded prompt state JSON-safe without stringifying arbitrary objects."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            _json_value(value), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MaxisRuntimeError("MAXIS evidence를 canonical JSON으로 만들 수 없습니다.") from exc


def _outbound_content_projection(value: Any) -> Any:
    """Remove navigable URLs from evidence bodies before the DLP boundary.

    Source URLs have their own exact-host policy.  A URL nested in a Jira/Confluence
    result body would otherwise bypass that policy because the body is opaque JSON text.
    The original result is still digest/HMAC verified first; only this network projection
    is redacted.
    """
    if isinstance(value, str):
        return re.sub(r"https?://[^\s<>\"']+", "[url omitted]", value, flags=re.I)
    if isinstance(value, list):
        return [_outbound_content_projection(item) for item in value]
    if isinstance(value, tuple):
        return [_outbound_content_projection(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _outbound_content_projection(item)
            for key, item in value.items()
        }
    return value


def _primary_row_scope(source: str, value: dict) -> str:
    """Return one row's canonical authority scope, never a prose/envelope claim."""
    if source in {"jira", "comments"}:
        # A row's own key wins over relationship keys embedded below it.
        for field in ("key", "ticketKey", "parentKey", "epicKey"):
            match = re.fullmatch(
                r"([A-Z][A-Z0-9]*)-\d+", str(value.get(field) or "").strip(), re.I,
            )
            if match:
                return match.group(1).upper()
    elif source == "confluence":
        for field in ("space", "spaceKey"):
            clean = str(value.get(field) or "").strip()
            if clean:
                return clean
    elif source == "bitbucket":
        for field in ("projectKey", "project", "repositoryProject"):
            raw = value.get(field)
            clean = str((raw or {}).get("key") if isinstance(raw, dict) else raw or "").strip()
            if clean:
                return clean
    return ""


def _observed_result_scopes(source: str, result: dict) -> tuple[str, ...]:
    values: list[str] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 7 or len(values) >= 200:
            return
        if isinstance(value, dict):
            scope = _primary_row_scope(source, value)
            if scope:
                values.append(scope)
            for key, item in value.items():
                if key not in {"scopeProjects", "scopeSpaces", "scopeRepositories"}:
                    visit(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value[:200]:
                visit(item, depth + 1)

    visit(result)
    return tuple(dict.fromkeys(values))


def _result_scopes(source: str, result: dict, explicit=()) -> tuple[str, ...]:
    declared = [str(value or "").strip() for value in (explicit or ())]
    field = {
        "jira": "scopeProjects",
        "comments": "scopeProjects",
        "confluence": "scopeSpaces",
        "bitbucket": "scopeRepositories",
    }.get(source)
    if field:
        raw = result.get(field)
        if isinstance(raw, (list, tuple)):
            declared.extend(str(value or "").strip() for value in raw)
    declared_scopes = tuple(dict.fromkeys(value for value in declared if value))
    observed = _observed_result_scopes(source, result)
    if observed and declared_scopes and not set(observed).issubset(declared_scopes):
        raise MaxisRuntimeError(
            "MAXIS evidence의 실제 canonical scope가 조회 envelope와 일치하지 않습니다."
        )
    # A zero-hit result legitimately has no row key; only then may the deterministic
    # query compiler's declared scope stand on its own.
    return observed or declared_scopes


_DROP_SCOPE_VALUE = object()


def _scoped_result_projection(source: str, result: dict, scope: str, scopes: tuple[str, ...]):
    """Remove rows owned by sibling scopes before creating one EvidenceSource."""
    if len(scopes) <= 1:
        return result

    def select(value: Any, depth: int = 0):
        if depth > 9:
            return _DROP_SCOPE_VALUE
        if isinstance(value, dict):
            owned = _primary_row_scope(source, value)
            if owned and owned != scope:
                return _DROP_SCOPE_VALUE
            selected = {}
            for key, item in value.items():
                # Global counts/cursors describe the combined query and cannot be cited as
                # a per-scope fact. Scope envelope fields are rebuilt below.
                if key in {"total", "returned", "pages", "remaining", "nextCursor"}:
                    continue
                child = select(item, depth + 1)
                if child is not _DROP_SCOPE_VALUE:
                    selected[str(key)] = child
            return selected
        if isinstance(value, (list, tuple)):
            rows = []
            for item in value[:500]:
                child = select(item, depth + 1)
                if child is not _DROP_SCOPE_VALUE:
                    rows.append(child)
            return rows
        return value

    projected = select(result)
    if not isinstance(projected, dict):
        projected = {}
    field = {
        "jira": "scopeProjects", "comments": "scopeProjects",
        "confluence": "scopeSpaces", "bitbucket": "scopeRepositories",
    }.get(source)
    if field:
        projected[field] = [scope]
    projected["scopePartitioned"] = True
    return projected


def _evidence_attestation_material(
    *, query_id: str, source: str, retrieved_at: str, scopes: tuple[str, ...],
    partial: bool, content_sha256: str, provenance: dict,
) -> bytes:
    return _canonical_json({
        "queryId": query_id,
        "source": source,
        "retrievedAt": retrieved_at,
        "scopes": list(scopes),
        "partial": partial,
        "contentSha256": content_sha256,
        "provenance": provenance,
    }).encode("utf-8")


def begin_local_acquisition() -> AcquisitionMarker | None:
    """Snapshot cache provenance immediately before one deterministic read."""
    try:
        from app.agent.tools._ctx import client
        from app.agent.maxis.context_guard import ContextPolicy

        cache = client().cache
        marker = cache.provenance_marker(
            max_age_seconds=ContextPolicy(allowed_scopes={}).freshness_seconds)
        return AcquisitionMarker(cache=cache, cursor=marker)
    except Exception:
        return None


def finish_local_acquisition(marker: AcquisitionMarker | None) -> dict:
    """Return the oldest actually-served row time and stale/upstream metadata.

    An available cache with no tracked reads is partial: supported dynamic sources may
    not silently substitute the wall clock for missing provenance.  A caller without an
    LTM cache is marked untracked for the same fail-closed reason.
    """
    now = datetime.now(timezone.utc)
    if marker is None:
        return {
            "retrieved_at": now,
            "partial": True,
            "served_stale": False,
            "upstream_observed": False,
            "read_count": 0,
            "tracked": False,
        }
    try:
        snapshot = marker.cache.provenance_since(marker.cursor)
    except Exception:
        snapshot = {}
    count = int(snapshot.get("count") or 0) if isinstance(snapshot, dict) else 0
    raw_oldest = snapshot.get("oldestFetchedAt") if isinstance(snapshot, dict) else None
    try:
        observed = datetime.fromtimestamp(float(raw_oldest), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        observed = now
    tracked = count > 0 and raw_oldest is not None
    return {
        "retrieved_at": observed,
        "partial": bool(not tracked or snapshot.get("servedStale") is True),
        "served_stale": bool(snapshot.get("servedStale") is True),
        "upstream_observed": bool(snapshot.get("upstream") is True),
        "read_count": min(10000, max(0, count)),
        "tracked": tracked,
    }


def attest_query_result(
    query_id: str,
    source: str,
    result: dict,
    *,
    retrieved_at: datetime | str | None = None,
    scopes=(),
    partial: bool | None = None,
    provenance: dict | None = None,
) -> dict:
    """Issue a process-local proof for one deterministic acquisition result.

    QueryRunner/editor acquisition call this immediately after a local read.  The HMAC
    prevents later semantic state from fabricating a fresh timestamp or broader scope.
    It is deliberately process-local: an old checkpoint must be reacquired after restart.
    """
    canonical_source = str(source or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", canonical_source):
        raise MaxisRuntimeError("MAXIS evidence source 식별자가 올바르지 않습니다.")
    if not isinstance(result, dict):
        raise MaxisRuntimeError("MAXIS evidence result는 객체여야 합니다.")
    instant = retrieved_at or datetime.now(timezone.utc)
    if isinstance(instant, str):
        try:
            instant = datetime.fromisoformat(instant.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MaxisRuntimeError("MAXIS evidence 수신시각이 올바르지 않습니다.") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise MaxisRuntimeError("MAXIS evidence 수신시각에는 timezone이 필요합니다.")
    received = instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    query = str(query_id or "").strip()[:128]
    if not query:
        raise MaxisRuntimeError("MAXIS evidence query id가 없습니다.")
    resolved_scopes = _result_scopes(canonical_source, result, scopes)
    content_sha256 = hashlib.sha256(_canonical_json(result).encode("utf-8")).hexdigest()
    raw_provenance = provenance if isinstance(provenance, dict) else {}
    proof_provenance = {
        "servedStale": bool(raw_provenance.get("served_stale")),
        "upstreamObserved": bool(
            raw_provenance.get("upstream_observed", provenance is None)
        ),
        "readCount": min(10000, max(0, int(raw_provenance.get("read_count") or 0))),
        "tracked": bool(raw_provenance.get("tracked", provenance is None)),
    }
    # A caller may add incompleteness, but cannot override a failed required read.
    is_partial = bool(
        partial or result.get("error") or result.get("incomplete")
        or result.get("materializationErrors") or result.get("contextTruncated")
        or result.get("complete") is False
    )
    is_partial = bool(is_partial or proof_provenance["servedStale"]
                      or not proof_provenance["tracked"])
    material = _evidence_attestation_material(
        query_id=query, source=canonical_source, retrieved_at=received,
        scopes=resolved_scopes, partial=is_partial, content_sha256=content_sha256,
        provenance=proof_provenance,
    )
    signature = hmac.new(_EVIDENCE_ATTESTATION_KEY, material, "sha256").hexdigest()
    return {
        "contract": "ltm-evidence-attestation.v1",
        "queryId": query,
        "source": canonical_source,
        "retrievedAt": received,
        "scopes": list(resolved_scopes),
        "partial": is_partial,
        "contentSha256": content_sha256,
        "provenance": proof_provenance,
        "signature": signature,
    }


def _context_time(state: dict) -> datetime:
    raw = str(state.get("correlation_started_at") or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _role_context(role_id: str, state: dict) -> tuple[list[dict], frozenset[str]]:
    """Project only the Role's declared, non-authority state into bounded context."""
    from app.agent.maxis.context_guard import MAX_ROLE_CONTEXT_UTF8_BYTES
    from app.agent.workflow.role_manifest import ROLE_SPECS

    spec = ROLE_SPECS[role_id]  # canonical-id assertion
    denied = {
        "messages", "query_results", "query_artifacts", "materialized_ticket_sources",
        "approval_token", "comment_token", "execution_receipt",
        "question_receipt_projection", "change_audit_binding", "trace", "error", "user_id",
        "request_text", "prompt", "role_context_carryover", "query_business_input",
        # request_text is carried once in the guarded query; the carryover ledger is
        # local provenance metadata and never model context.
    }
    # This is an outbound allowlist, not a mirror of the much wider local Role input
    # contract.  Adding a new local State field must never make it cross MAXIS by accident.
    allowed = {
        key for key in spec.remote_context_keys
        if key not in denied and not key.startswith("runtime_")
    }
    order = list(spec.remote_context_keys)
    values = [key for key in order if key in allowed and state.get(key) not in (
        None, "", [], {},
    )]
    current_observed_at = _context_time(state)
    current_has_observed_at = bool(
        str(state.get("correlation_started_at") or "").strip()
    )
    carryover = (
        state.get("role_context_carryover")
        if isinstance(state.get("role_context_carryover"), dict)
        else {}
    )
    rows: list[dict] = []
    omitted = max(0, len(values) - 8)
    for index, key in enumerate(values[:8]):
        value = _json_value(state[key])
        if role_id == "editor_author" and key == "seed_html":
            # The same bounded seed also appears in the intent source. Do not let
            # its parallel Role context retain URLs removed by the source policy.
            value = _outbound_content_projection(value)
        encoded = _canonical_json(value)
        encoded_utf8 = encoded.encode("utf-8")
        observed_at = current_observed_at
        has_observed_at = current_has_observed_at
        carried = carryover.get(key)
        digest = hashlib.sha256(encoded_utf8).hexdigest()
        if isinstance(carried, dict) and carried.get("sha256") == digest:
            raw_observed_at = str(carried.get("observed_at") or "").strip()
            try:
                parsed_observed_at = datetime.fromisoformat(
                    raw_observed_at.replace("Z", "+00:00")
                )
                if (
                    parsed_observed_at.tzinfo is not None
                    and parsed_observed_at.utcoffset() is not None
                ):
                    observed_at = parsed_observed_at
                    has_observed_at = True
            except ValueError:
                has_observed_at = False
        partial = not has_observed_at
        if len(encoded) > 8192 or len(encoded_utf8) > MAX_ROLE_CONTEXT_UTF8_BYTES:
            # Do not slice JSON into an invalid or misleading value. A typed omission is
            # visible through ``partial`` and blocks freshness-sensitive draft Roles.
            value = {"omitted": True, "originalChars": len(encoded)}
            partial = True
        if omitted and index == min(7, len(values[:8]) - 1):
            partial = True
        rows.append({
            "context_id": "rctx_" + hashlib.sha256(
                f"{state.get('correlation_id') or 'direct'}:{role_id}:{key}".encode("utf-8")
            ).hexdigest()[:20],
            "name": key,
            "value": value,
            "observed_at": observed_at,
            "partial": partial,
        })
    return rows, frozenset(allowed)


def _known_identity_values(state: dict, cfg: Any) -> tuple[str, ...]:
    """Collect locally-known roster ids for one-way pseudonymization only.

    The returned identifiers are handed directly to ``ContextGuard`` and are never
    persisted in the EvidencePack or sent as a lookup table.  This covers assignees and
    commenters embedded in otherwise approved Jira evidence, not merely the caller.
    """
    identities: set[str] = set()
    for value in (
        state.get("user_id"), state.get("user_identity"),
        getattr(cfg, "jira_user", ""),
    ):
        clean = str(value or "").strip()
        if clean:
            identities.add(clean)
    for value in (getattr(cfg, "managers", None) or ()):
        clean = str(value or "").strip()
        if clean:
            identities.add(clean)
    for value in (state.get("_maxis_identity_values") or ()):
        clean = str(value or "").strip()
        if clean:
            identities.add(clean)
    try:
        from app.infra.settings import load_people

        for rows in (load_people() or {}).values():
            for value in (rows or ()):
                clean = str(value or "").strip()
                if clean:
                    identities.add(clean)
    except Exception as exc:
        # A configured remote route may not silently omit a known roster from DLP.
        raise MaxisRuntimeError("MAXIS 전송 전 roster 가명화 범위를 확인할 수 없습니다.") from exc

    person_keys = {
        "assignee", "assigneeId", "assigneeName", "author", "authorId",
        "display", "displayName", "owner", "reporter", "reporterId",
        "userId", "userName", "username", "who", "accountId", "account_id",
        "emailAddress",
    }
    visited = 0

    def collect(value: Any, depth: int = 0) -> None:
        nonlocal visited
        if depth > 7 or visited >= 5000:
            return
        visited += 1
        if isinstance(value, dict):
            identity_row = any(key in value for key in person_keys)
            for key, item in value.items():
                if key in person_keys or (key == "name" and identity_row):
                    if isinstance(item, str) and 2 <= len(item.strip()) <= 160:
                        identities.add(item.strip())
                    elif isinstance(item, (dict, list, tuple)):
                        collect(item, depth + 1)
                else:
                    collect(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value[:200]:
                collect(item, depth + 1)

    # Only locally-held state is inspected. The extracted lookup values are not added to
    # the pack; ContextGuard replaces their occurrences with irreversible markers.
    for key, value in state.items():
        if key not in _PRIVATE_STATE_KEYS and not str(key).startswith("runtime_"):
            collect(value)
    return tuple(sorted(identities))


def _reference_work_intent_context(
    rows: list[dict], intent: str, source_index: int,
) -> list[dict]:
    """Deduplicate exact request text in read-only Work context, not local State.

    ``{"source_content_index": N}`` in request_plan.goal or a task's instruction
    denotes sources[N].content in this EvidencePack: complete guarded user-intent
    text, never factual authority. The integer survives identity redaction without
    exempting any user text. Citation IDs still come from that source row.
    Other plan fields/values are unchanged.
    Call only after _role_context checks carryover digests and observation times
    against the original value; projected references must not refresh old context.
    """
    projected = []
    for row in rows:
        if row["name"] != "request_plan" or not isinstance(row["value"], dict):
            projected.append(row)
            continue
        plan = _json_value(row["value"])
        if plan.get("goal") == intent:
            plan["goal"] = {"source_content_index": source_index}
        tasks = plan.get("tasks")
        if isinstance(tasks, list):
            for task in tasks:
                if isinstance(task, dict) and task.get("instruction") == intent:
                    task["instruction"] = {"source_content_index": source_index}
        projected.append({**row, "value": plan})
    return projected


def _allowed_source_hosts(cfg: Any) -> dict[str, tuple[str, ...]]:
    hosts: dict[str, tuple[str, ...]] = {}
    for kind, attr in (
        ("jira", "jira_base"),
        ("confluence", "confluence_base"),
        ("bitbucket", "bitbucket_base"),
    ):
        raw = str(getattr(cfg, attr, "") or "").strip()
        if not raw:
            continue
        try:
            authority = urlsplit(raw).netloc.strip().lower()
        except ValueError as exc:
            raise MaxisRuntimeError(
                f"MAXIS {kind} source host 설정을 확인할 수 없습니다."
            ) from exc
        if not authority:
            raise MaxisRuntimeError(f"MAXIS {kind} source host 설정이 비어 있습니다.")
        hosts[kind] = (authority,)
    return hosts


def _verified_evidence_sources(state: dict) -> list[dict]:
    rows = list(state.get("query_results") or [])
    rows.extend(state.get("_maxis_evidence_rows") or [])
    detached_proofs = state.get("query_evidence_attestations") or {}
    if not isinstance(detached_proofs, dict):
        raise MaxisRuntimeError("MAXIS evidence 증명 ledger 형식이 올바르지 않습니다.")
    sources: list[dict] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("result"), dict):
            raise MaxisRuntimeError("MAXIS evidence row 형식이 올바르지 않습니다.")
        source = str(row.get("source") or "").strip().lower()
        if source in _LOCAL_ONLY_QUERY_SOURCES:
            # This row can have informed the local Research Analyst, but it is neither
            # citation-grade evidence nor an allowed outbound source.  Ignoring it here
            # preserves that trust boundary without blocking downstream remote Roles.
            continue
        proof = row.get("_remoteEvidence")
        if not isinstance(proof, dict):
            proof = detached_proofs.get(
                f"{str(row.get('source') or '')}:{str(row.get('id') or '')}"
            )
        if not isinstance(proof, dict) or proof.get("contract") != \
                "ltm-evidence-attestation.v1":
            raise MaxisRuntimeError("MAXIS evidence의 로컬 조회 증명이 없습니다.")
        query_id = str(proof.get("queryId") or "")
        proof_source = str(proof.get("source") or "").strip().lower()
        if proof_source != source:
            raise MaxisRuntimeError("MAXIS evidence source 증명이 일치하지 않습니다.")
        kind = _QUERY_SOURCE_KINDS.get(source)
        if not kind:
            raise MaxisRuntimeError(
                f"MAXIS EvidencePack이 아직 지원하지 않는 source입니다: {source}"
            )
        retrieved_at = str(proof.get("retrievedAt") or "")
        raw_scopes = proof.get("scopes")
        if not isinstance(raw_scopes, list) or any(
                not isinstance(value, str) for value in raw_scopes):
            raise MaxisRuntimeError("MAXIS evidence scope 증명이 올바르지 않습니다.")
        scopes = tuple(dict.fromkeys(value.strip() for value in raw_scopes if value.strip()))
        partial = proof.get("partial")
        digest = str(proof.get("contentSha256") or "")
        signature = str(proof.get("signature") or "")
        provenance = proof.get("provenance")
        if not isinstance(provenance, dict) or set(provenance) != {
            "servedStale", "upstreamObserved", "readCount", "tracked",
        }:
            raise MaxisRuntimeError("MAXIS evidence cache provenance가 없습니다.")
        if (
            not isinstance(provenance.get("servedStale"), bool)
            or not isinstance(provenance.get("upstreamObserved"), bool)
            or not isinstance(provenance.get("tracked"), bool)
            or isinstance(provenance.get("readCount"), bool)
            or not isinstance(provenance.get("readCount"), int)
            or not 0 <= provenance["readCount"] <= 10000
            or ((provenance["servedStale"] or not provenance["tracked"])
                and partial is not True)
        ):
            raise MaxisRuntimeError("MAXIS evidence cache provenance가 올바르지 않습니다.")
        if not isinstance(partial, bool) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MaxisRuntimeError("MAXIS evidence digest 증명이 올바르지 않습니다.")
        actual_digest = hashlib.sha256(
            _canonical_json(row["result"]).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(actual_digest, digest):
            raise MaxisRuntimeError("MAXIS evidence 내용이 조회 이후 변경되었습니다.")
        material = _evidence_attestation_material(
            query_id=query_id, source=source, retrieved_at=retrieved_at,
            scopes=scopes, partial=partial, content_sha256=digest,
            provenance=provenance,
        )
        expected = hmac.new(_EVIDENCE_ATTESTATION_KEY, material, "sha256").hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise MaxisRuntimeError("MAXIS evidence 조회 증명을 검증하지 못했습니다.")
        if kind not in {"knowledge", "ltm"} and not scopes:
            raise MaxisRuntimeError("MAXIS scoped evidence에 canonical scope가 없습니다.")
        selected_scopes = scopes or ("",)
        for scope in selected_scopes:
            scoped_result = _scoped_result_projection(
                source, row["result"], scope, scopes,
            )
            jira_keys: set[str] = set()
            if kind == "jira":
                stack = [scoped_result]
                while stack:
                    value = stack.pop()
                    if isinstance(value, dict):
                        for field, child in value.items():
                            if (
                                field in {
                                    "key", "issueKey", "issue_key",
                                    "ticketKey", "ticket_key",
                                }
                                and isinstance(child, str)
                                and _JIRA_KEY.fullmatch(child.strip().upper())
                            ):
                                jira_keys.add(child.strip().upper())
                            else:
                                stack.append(child)
                    elif isinstance(value, list):
                        stack.extend(value)
            exact_jira_key = next(iter(jira_keys)) if len(jira_keys) == 1 else ""
            content = _canonical_json(_outbound_content_projection(scoped_result))
            source_id = "src_" + hashlib.sha256(
                f"{query_id}:{source}:{scope}:{digest}".encode("utf-8")
            ).hexdigest()[:20]
            sources.append({
                "source_id": source_id,
                # Local-only provenance for derived metrics. canonical_id may be
                # a ticket key for rendering, so it cannot identify the read kind.
                # ContextGuard projects explicit wire fields and drops this marker.
                "_query_source": source,
                "kind": kind,
                "canonical_id": exact_jira_key or f"ltm-query:{source}:{query_id}",
                "title": (
                    f"Jira {exact_jira_key}" if exact_jira_key
                    else f"LTM verified {source} retrieval {index + 1}"
                ),
                "scope": scope,
                "url": "",
                "content": content,
                "retrieved_at": retrieved_at,
                "updated_at": None,
                "partial": partial,
            })
    return sources


def _query_business_pack(
    guard, *, state: dict, user_intent: str, explicit_user_intent: bool,
    synthetic_only: bool, **inputs,
):
    """Admit a backend-owned task only when the entire prior pack is unchanged."""
    from dataclasses import replace
    from app.agent.maxis.context_guard import ContextGuard, ContextGuardError, validate_pack_safety
    from app.agent.maxis.contracts import EvidencePackV1, canonical_payload_schema
    from app.agent.workflow.contracts import role_output_schema

    # Snapshot iterables once, before either independent guard consumes them.
    for key in ("sources", "role_context", "identity_values"):
        inputs[key] = tuple(inputs[key])
    baseline = guard.build(**inputs)
    if inputs["use_case"] != "query_specialist":
        return baseline
    canonical_schema, _ = canonical_payload_schema(role_output_schema("query_specialist"))
    if baseline.payload_schema_json != canonical_schema:
        return baseline
    name = "query_business_input"
    carryover = state.get("role_context_carryover")
    if (name in state or (isinstance(carryover, dict) and name in carryover)
            or any(row["name"] == name for row in inputs["role_context"])):
        raise MaxisRuntimeError(
            "query_business_input_reserved: MAXIS Query 예약 입력과 충돌합니다. 새 요청으로 다시 시도해 주세요."
        )
    if not explicit_user_intent and not synthetic_only:
        raise MaxisRuntimeError(
            "query_business_input_required: MAXIS Query에 명시적인 원 요청이 필요합니다."
        )
    if len(inputs["role_context"]) >= 8:
        raise MaxisRuntimeError(
            "query_business_input_capacity: MAXIS Query 요청을 추가할 문맥 공간이 없습니다."
        )
    identities = tuple(dict.fromkeys(
        value.strip() for value in (*inputs["identity_values"], inputs["user_identity"])
        if value.strip()
    ))
    # This is the captured pre-enrichment intent, never recovered from query/source text.
    from app.agent.runtime_status import INTERNAL_SOURCES, bounded_internal_assist
    capabilities = ({"allowed_sources": sorted(INTERNAL_SOURCES)}
                    if not synthetic_only and bounded_internal_assist(state) else {})
    expected_json = _canonical_json({"text": guard.guard_query(user_intent, identities)})
    context_id = "rctx_" + hashlib.sha256(
        f"{inputs['request_id']}:query_specialist:{name}".encode("utf-8")
    ).hexdigest()[:20]
    row = {"context_id": context_id, "name": name, "value": {"text": user_intent},
           "observed_at": inputs["requested_at"], "partial": False}
    candidate_guard = ContextGuard(
        replace(guard.policy, allowed_context_keys=guard.policy.allowed_context_keys | {name}),
        guard._pseudonym_key,
    )
    error = (
        "query_business_input_no_loss: 기존 근거와 문맥을 바꾸지 않고 MAXIS Query 요청을 "
        "추가할 수 없습니다. 근거를 새로 조회하거나 요청 범위를 줄여 다시 시도해 주세요."
    )
    try:
        candidate = candidate_guard.build(**{
            **inputs, "role_context": (*inputs["role_context"], row),
        })
    except ContextGuardError as exc:
        raise MaxisRuntimeError(error) from exc
    task_rows = [item for item in candidate.role_context if item.name == name]
    if len(task_rows) != 1:
        raise MaxisRuntimeError(error)
    task = task_rows[0]
    if (task.context_id != context_id or task.observed_at != inputs["requested_at"]
            or task.partial or task.content_json != expected_json
            or task.content_sha256 != hashlib.sha256(expected_json.encode("utf-8")).hexdigest()):
        raise MaxisRuntimeError(error)
    without_task = candidate.model_dump(mode="json")
    without_task["role_context"] = [
        item for item in without_task["role_context"] if item["name"] != name
    ]
    if without_task != baseline.model_dump(mode="json"):
        raise MaxisRuntimeError(error)
    if capabilities:
        # Only these backend-owned protocol enums bypass identity pseudonymization.
        # The complete user text has already passed normal redaction and no-loss
        # admission above; an identity named "jira" must not corrupt the wire enum.
        augmented = _canonical_json({**json.loads(expected_json), **capabilities})
        data = candidate.model_dump()
        for item in data["role_context"]:
            if item["name"] == name:
                item["content_json"] = augmented
                item["content_sha256"] = hashlib.sha256(augmented.encode("utf-8")).hexdigest()
        try:
            candidate = EvidencePackV1.model_validate(data)
            validate_pack_safety(candidate, max_total_utf8_bytes=guard.policy.max_total_utf8_bytes)
        except (ValueError, ContextGuardError) as exc:
            raise MaxisRuntimeError(error) from exc
    return candidate


def _retrieval_count_metrics(sources: list[dict]) -> list[dict]:
    """Count only complete, attested search rows after scope partitioning.

    The caller supplies _verified_evidence_sources output, never model/state
    metrics. These are returned-row counts for this exact source/scope, not
    project totals or proof that an entity does not exist.
    """
    metrics = []
    for source in sources:
        if source.get("partial") is not False or source.get("truncated"):
            continue
        kind = source.get("kind")
        field, query = {"jira": ("tickets", "canonicalJql"),
                        "confluence": ("documents", "canonicalCql")}.get(kind, ("", ""))
        label_kind = kind
        if kind == "jira" and source.get("_query_source") == "comments":
            field, label_kind = "comments", "comments"
        if not field:
            continue
        try:
            result = json.loads(source["content"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(result, dict):
            continue
        # Bounded reads use hasMore; exhaustive QueryRunner reads use complete.
        # Accept the latter only when no conflicting/unknown pagination flag is
        # present. Both still originate from verified acquisition receipts.
        complete = (result.get("hasMore") is False and result.get("complete") is not False
                    or "hasMore" not in result and result.get("complete") is True)
        if (not complete
                or not isinstance(result.get(query), str) or not result[query].strip()
                or not isinstance(result.get(field), list)
                or any(not isinstance(row, dict) for row in result[field])
                or any(result.get(key) for key in ("error", "errors", "truncated", "partial",
                    "incomplete", "contextTruncated", "materializationErrors"))
                or result.get("ok") is False or result.get("status") in ("failed", "error")):
            continue
        source_id = source["source_id"]
        metrics.append({
            "metric_id": "returned_" + hashlib.sha256(source_id.encode()).hexdigest()[:20],
            "label": f"{source['scope']} 범위의 해당 {label_kind} 검색 반환 항목 수",
            "value": len(result[field]), "unit": "건",
            "formula_version": "verified-scoped-returned-count.v1",
            "source_ids": [source_id],
        })
    return metrics


def _synthetic_query_context_allowed(role_id: str, evidence) -> bool:
    """Only canonical Query may carry its exact request-bound synthetic task."""
    if role_id != "query_specialist":
        return not evidence.role_context
    from app.agent.maxis.contracts import canonical_payload_schema
    from app.agent.workflow.contracts import role_output_schema

    canonical_schema, _ = canonical_payload_schema(role_output_schema("query_specialist"))
    if evidence.payload_schema_json != canonical_schema:
        return not evidence.role_context
    if len(evidence.role_context) != 1:
        return False
    row = evidence.role_context[0]
    expected_json = _canonical_json({
        "text": "Synthetic MAXIS integration exercise for canonical role query_specialist.",
    })
    expected_id = "rctx_" + hashlib.sha256(
        f"{evidence.request_id}:query_specialist:query_business_input".encode("utf-8")
    ).hexdigest()[:20]
    return (
        row.name == "query_business_input" and row.context_id == expected_id
        and row.content_json == expected_json
        and row.content_sha256 == hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
        and row.observed_at == evidence.requested_at and row.partial is False
    )


def _context_pack(
    role_id: str,
    state: dict,
    messages: list,
    payload_schema: dict,
):
    """Build the only content-bearing object allowed to cross the MAXIS boundary."""
    from app.agent.maxis.context_guard import ContextGuard, ContextPolicy
    from app.agent.tools._ctx import settings
    from app.agent.workflow.role_manifest import ROLE_SPECS

    ROLE_SPECS[role_id]  # canonical-id assertion before constructing outbound data
    sources = _verified_evidence_sources(state)
    request_id = str(state.get("correlation_id") or "").strip()
    if not request_id:
        seed = f"{state.get('thread_id') or 'direct'}:{role_id}"
        request_id = "direct_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    # The Role's system/task messages frequently embed raw state. They are local prompt
    # implementation detail and must never become the remote query. Use only the original
    # user intent retained in state; the Dify workflow owns its canonical Role instructions.
    # ``request_text`` is the one Session-owned, bounded user-intent field.  Never fall
    # back to role task messages, model scratch, or a generic ``prompt`` field: those may
    # contain raw query results/state and would bypass source scope/freshness attestation.
    from app.agent.maxis import profiles
    # Resolve the server-owned policy, not a name convention or a caller-supplied flag.
    # The runtime fingerprint binds policy changes; invoke revalidates that binding
    # and independently rejects non-fixture data for synthetic-only profiles.
    try:
        profile_policy = profiles.get(str(state.get("runtime_profile") or ""))["data_policy"]
    except (KeyError, ValueError):
        # Pure offline/qualification projection has no saved connection. This does
        # not authorize dispatch: invoke independently requires a verified profile,
        # matching fingerprint and, for synthetic profiles, an exact fixture pack.
        profile_policy = ""
    synthetic_only = profile_policy == "synthetic_only"
    task_text = str(state.get("request_text") or "").strip()
    continuation_answer = (
        str(state.get("continuation_answer") or "").strip()
        if role_id in {"request_architect", "query_specialist", "work_architect", "knowledge_curator", "auditor"}
        and task_text and state.get("turn_continuation") is True
        else ""
    )
    # A continuation needs both the durable root request and the current bounded answer.
    # Never recover that answer from ``messages``: the transcript can contain unrelated
    # history, tool output, or secrets. ContextGuard performs the same URL/identity/
    # sensitive-value checks over this explicit query as for every other outbound field.
    if continuation_answer:
        task_text = (
            "Original request:\n"
            f"{task_text[:2500]}\n"
            "Current continuation answer:\n"
            f"{continuation_answer[:800]}"
        )
    explicit_user_intent = bool(task_text)
    user_intent = (
        f"Synthetic MAXIS integration exercise for canonical role {role_id}."
        if synthetic_only else (
            re.sub(
                r"https?://[^\s<>\"']+", "[url omitted]", task_text[:3600], flags=re.I,
            ) or "No additional user text was supplied."
        )
    )
    query = f"Canonical LTM role {role_id}. Process this guarded user intent:\n{user_intent}"
    if not synthetic_only:
        # Keep explicit synthetic mode's exact fixed-fixture gate. Role payloads add
        # policy vocabulary, never facts, before the ordinary ContextGuard checks.
        from app.agent.maxis.retrieval_query import enrich_role_query
        query = enrich_role_query(query, role_id, payload_schema)
    original_query = query
    cfg = settings()
    role_context, allowed_context_keys = _role_context(role_id, state)
    requested_at = datetime.now(timezone.utc)
    if synthetic_only:
        # Synthetic mode is a protocol/load-test surface. Even when Jira runs in mock mode,
        # a person can paste real internal text into the prompt or a mock ticket. Replace
        # every content-bearing field with one fixed fixture before crossing the boundary.
        synthetic_content = f"Synthetic LTM fixture for {role_id}; state is ready."
        sources = [{
            "source_id": _SYNTHETIC_SOURCE_ID,
            "kind": "ltm",
            "canonical_id": _SYNTHETIC_CANONICAL_ID,
            "title": _SYNTHETIC_TITLE,
            "scope": "",
            "url": "",
            "content": synthetic_content,
            "retrieved_at": requested_at,
            "updated_at": None,
            "partial": False,
        }]
        role_context = []
        allowed_context_keys = ()
    elif explicit_user_intent and (
        not sources or role_id in {"editor_author", "work_architect", "auditor"}
    ):
        # RequestArchitect and other pre-retrieval semantic Roles legitimately derive
        # plans or responses from the user's guarded intent before QueryRunner has any
        # citation-grade connector result.  The intent already crosses the boundary in
        # ``query``; represent that same bounded material as an LTM-owned source so a
        # remote Role must cite it instead of using the source-free provenance escape
        # hatch. Work Architect also needs the requested work as a citeable
        # recommendation beside connector facts; a read does not attest the user's
        # reproduction, scope, or desired outcome. Auditor needs that same intent
        # to judge request coverage, including the explicit continuation answer.
        # It does not attest the draft, external facts, or approval. Other Roles retain the empty-ledger
        # policy. An intent row must never displace an authoritative read source.
        # Editor additionally needs its exact requested target/mode/seed to be a
        # citeable *intent*, not an external fact. This enables verbatim seed
        # preservation in the remote assembler without inventing provenance.
        intent_content = user_intent
        if role_id == "work_architect" and len(sources) >= 10:
            raise MaxisRuntimeError("MAXIS Work Architect 요청 근거를 추가할 공간이 없습니다.")
        if role_id == "auditor" and len(sources) >= 10:
            raise MaxisRuntimeError("MAXIS Auditor 요청 근거를 추가할 공간이 없습니다.")
        if role_id == "editor_author":
            if len(sources) >= 10:
                raise MaxisRuntimeError("MAXIS Editor 요청 근거를 추가할 공간이 없습니다.")
            intent_content = _canonical_json(_outbound_content_projection({
                "ticket_key": str(state.get("ticket_key") or ""),
                "kind": str(state.get("kind") or ""),
                "prompt": user_intent,
                "seed_html": str(state.get("seed_html") or ""),
            }))
            if len(intent_content) > 1500:
                raise MaxisRuntimeError("MAXIS Editor 요청·seed가 안전한 전송 한도를 초과했습니다.")
        intent_identity = hashlib.sha256(
            f"{request_id}:{role_id}".encode("utf-8")
        ).hexdigest()
        intent_source_id = "src_intent_" + intent_identity[:20]
        sources = [*sources, {
            "source_id": intent_source_id,
            "kind": "ltm",
            "canonical_id": "ltm:user-intent:" + intent_identity[:32],
            "title": _USER_INTENT_SOURCE_TITLE,
            "scope": "",
            "url": "",
            "content": intent_content,
            "retrieved_at": requested_at,
            "updated_at": None,
            "partial": False,
        }]
        if role_id == "work_architect" and len(sources) > 1:
            # Keep the request once, in citation-grade *intent*, alongside reads.
            # The original query also passes ContextGuard below, including identity
            # expansion; references cannot admit an otherwise oversized request.
            # ContextGuard checks all content and marks budget truncation as partial;
            # invoke's existing freshness policy blocks such draft inputs.
            query = enrich_role_query(
                "Use the guarded user intent source.", role_id, payload_schema,
            )
            role_context = _reference_work_intent_context(
                role_context, user_intent, len(sources) - 1,
            )
    allowed_scopes = (
        {"jira": (), "confluence": (), "bitbucket": ()}
        if synthetic_only else {
            "jira": tuple(getattr(cfg, "search_jira_projects", None) or ()),
            "confluence": tuple(getattr(cfg, "search_confluence_spaces", None) or ()),
            "bitbucket": tuple(getattr(cfg, "search_bitbucket_projects", None) or ()),
        }
    )
    policy = ContextPolicy(
        # synthetic_only is independent of the locally configured Jira process.  Do
        # not even project its project/space/repository or host inventory into the
        # EvidencePack that crosses the MAXIS boundary.
        allowed_scopes=allowed_scopes,
        allowed_source_hosts=({} if synthetic_only else _allowed_source_hosts(cfg)),
        allowed_context_keys=allowed_context_keys,
        allow_http_urls=(
            False if synthetic_only
            else str(getattr(cfg, "jira_env", "mock")).lower() != "prod"
        ),
    )
    guard = ContextGuard(policy, _PSEUDONYM_KEY)
    identity = (
        _SYNTHETIC_USER_IDENTITY if synthetic_only
        else str(state.get("user_id") or state.get("thread_id") or "anonymous")
    )
    knowledge_manifest_sha256 = str(
        state.get("knowledge_manifest_sha256") or ""
    ).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", knowledge_manifest_sha256):
        raise MaxisRuntimeError(
            "MAXIS Knowledge manifest SHA-256이 대화에 고정되지 않았습니다."
        )
    identity_values = () if synthetic_only else _known_identity_values(state, cfg)
    if query != original_query:
        original_identities = tuple(dict.fromkeys(
            value.strip() for value in (*identity_values, identity) if value.strip()
        ))
        guard.guard_query(original_query, original_identities)
    return _query_business_pack(
        guard, state=state, user_intent=user_intent,
        explicit_user_intent=explicit_user_intent, synthetic_only=synthetic_only,
        request_id=request_id,
        use_case=role_id,
        query=query,
        payload_schema=payload_schema,
        user_identity=identity,
        sources=sources,
        metrics=() if synthetic_only else _retrieval_count_metrics(sources),
        role_context=role_context,
        identity_values=identity_values,
        additional_flags=(("synthetic_only",) if synthetic_only else ()),
        requested_at=requested_at,
        knowledge_manifest_sha256=knowledge_manifest_sha256,
    )


def role_payload(
    role_id: str,
    state: dict,
    messages: list,
    schema: dict,
    *,
    output_kind: Literal["structured", "text"],
) -> dict:
    """Build the versioned, role-specific transport without approval capabilities."""
    from app.agent.workflow.role_manifest import ROLE_SPECS

    spec = ROLE_SPECS.get(role_id)
    if spec is None:
        raise MaxisRuntimeError(f"알 수 없는 canonical Role입니다: {role_id}")
    if output_kind == "structured":
        if not isinstance(schema, dict):
            raise MaxisRuntimeError("MAXIS Role payload schema가 객체가 아닙니다.")
        actual_schema = _json_value(schema)
    else:
        actual_schema = _json_value(_TEXT_PAYLOAD_SCHEMA)
    evidence_pack = _context_pack(role_id, state, messages, actual_schema)
    return {
        "contractVersion": "ltm.remote-role.v1",
        "roleId": role_id,
        "outputKind": output_kind,
        "evidencePack": evidence_pack.model_dump(mode="json"),
        "runtime": {
            "engine": str(state.get("runtime_engine") or ""),
            "engineSource": str(state.get("runtime_engine_source") or "profile"),
            "profile": str(state.get("runtime_profile") or ""),
            "profileFingerprint": str(state.get("runtime_profile_fingerprint") or ""),
            "registryDigest": str(state.get("runtime_registry_digest") or ""),
            "contractBuild": str(state.get("runtime_contract_build") or ""),
            "workflowBundleVersion": str(state.get("workflow_bundle_version") or ""),
            "knowledgeVersion": str(state.get("knowledge_version") or ""),
            "knowledgeDatasetId": str(state.get("knowledge_dataset_id") or ""),
            "knowledgeManifestSha256": str(
                state.get("knowledge_manifest_sha256") or ""
            ),
            "roleRoutes": _json_value(state.get("runtime_role_routes") or {}),
        },
    }


def _workflow_key(role_id: str) -> str:
    if role_id == "portfolio_analyst":
        return "pmo_brief"
    if role_id == "editor_author":
        return "editor_author"
    return "agent_core"


def _binding_value(binding: Any, *names: str, default: Any = "") -> Any:
    for name in names:
        if isinstance(binding, dict) and name in binding:
            return binding[name]
        if hasattr(binding, name):
            return getattr(binding, name)
    return default


def _client_config(metadata: dict, binding: Any):
    """Construct the foundation client config while keeping its transport replaceable."""
    from app.agent.maxis.client import DifyClientConfig

    stage = str(metadata.get("stage") or "").lower()
    return DifyClientConfig(
        profile=str(metadata.get("name") or ""),
        stage=stage,
        base_url=str(metadata.get("base_url") or ""),
        profile_fingerprint=str(metadata.get("fingerprint") or ""),
        require_https=stage in {"internal-pilot", "prod"},
        verify_tls=True,
        max_response_bytes=int(_binding_value(
            binding, "max_response_bytes", default=1024 * 1024
        )),
    )


def _remote_state_patch(envelope: Any, evidence: Any) -> dict:
    """Project one validated response into state without source content or provider trace."""
    citation_sources = {
        str(row.citation_id): str(row.source_id) for row in envelope.citations
    }
    now = datetime.now(timezone.utc)
    retrieved = [row.retrieved_at for row in evidence.sources]
    oldest = min(retrieved) if retrieved else None
    max_age = max(0, int((now - oldest).total_seconds())) if oldest else 0
    source_rows = []
    for row in evidence.sources:
        canonical_id = str(row.canonical_id)
        # External URLs never become UI links.  A single verified Jira issue can be
        # opened through LTM's same-origin route, which preserves the user's current
        # Jira session and prevents an untrusted model from choosing the destination.
        local_url = (
            f"/browse/{canonical_id}"
            if str(row.kind) == "jira" and _JIRA_KEY.fullmatch(canonical_id)
            else ""
        )
        source_rows.append({
            "sourceId": str(row.source_id),
            "kind": str(row.kind),
            "canonicalId": canonical_id,
            "title": str(row.title),
            "url": local_url,
            "retrievedAt": row.retrieved_at.isoformat(),
            "updatedAt": row.updated_at.isoformat() if row.updated_at else "",
            "partial": bool(row.partial),
            "truncated": bool(row.truncated),
        })
    fact_rows = [{
        "text": str(row.text),
        "sourceIds": [str(value) for value in row.source_ids],
        "observedAt": row.observed_at.isoformat(),
    } for row in evidence.facts]
    claim_rows = []
    for row in envelope.claims:
        claim_rows.append({
            "kind": str(row.kind),
            "text": str(row.text),
            "sourceIds": list(dict.fromkeys(
                citation_sources[value]
                for value in row.citation_ids
                if value in citation_sources
            )),
        })
    freshness = {
        "status": str(evidence.freshness),
        "checkedAt": now.isoformat(),
        "oldestSourceAt": oldest.isoformat() if oldest else "",
        "maxAgeSeconds": max_age,
        "partial": bool(evidence.partial),
    }
    missing = [str(value) for value in envelope.missing_inputs]
    warnings = [str(value) for value in envelope.warnings]
    quality = {
        "ok": envelope.status == "ok" and not missing,
        "schemaValid": True,
        "citationValid": True,
        "freshnessValid": evidence.freshness in {"fresh", "empty"}
        and not evidence.partial,
        "numericParity": True,
        "errors": ([] if envelope.status != "blocked" else ["remote_blocked"]),
        "warnings": warnings,
    }
    return {
        "evidence_workspace": {
            "sources": source_rows,
            "facts": fact_rows,
            "claims": claim_rows,
            "missingInputs": missing,
            "warnings": warnings,
            "freshness": freshness,
            "partial": bool(evidence.partial),
        },
        "freshness": freshness,
        "quality_gate": quality,
    }


def _audit_correlation(value: str) -> str:
    clean = str(value or "").strip()
    if len(clean) <= 128 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", clean):
        return clean
    return "corr_" + hashlib.sha256(clean.encode("utf-8")).hexdigest()[:24]


def _record_remote_audit(
    *,
    correlation_id: str,
    status: Literal["started", "succeeded", "failed", "blocked"],
    resolution: RoleResolution,
    metadata: dict,
    workflow_key: str,
    workflow_version: str,
    knowledge_version: str,
    role_id: str,
    latency_ms: int = 0,
    input_bytes: int = 0,
    output_bytes: int = 0,
    source_count: int = 0,
    citation_count: int = 0,
    warning_count: int = 0,
    error_code: str = "",
    remote_request=None,
    runtime_started: float | None = None,
    result_status: str = "",
) -> None:
    """Append one bounded lifecycle row; content and exception text are impossible inputs."""
    try:
        from app.agent.maxis.audit import make_audit_event, record_audit_event

        outcome = result_status or {
            "started": "not_evaluated", "succeeded": "completed",
            "blocked": "blocked", "failed": "failed",
        }[status]
        observed = with_result_status(remote_request, outcome)
        captured = _request_observation.get()
        if captured is not None and observed is not None:
            # No prompt, raw response or exception message can enter this sidecar.
            captured.update({
                "requestTelemetry": observed.model_dump(mode="json"),
                "workflowBundleVersion": resolution.workflow_bundle_version,
                "profile": resolution.profile,
                "errorCode": error_code,
            })

        event = make_audit_event(
            correlation_id=_audit_correlation(correlation_id),
            event="remote_role.invoke",
            status=status,
            profile=str(resolution.profile or metadata.get("name") or ""),
            stage=str(metadata.get("stage") or ""),
            route_mode=resolution.route,
            workflow_key=workflow_key,
            workflow_version=workflow_version,
            knowledge_version=knowledge_version,
            role_id=role_id,
            latency_ms=min(3_600_000, max(0, int(latency_ms or 0))),
            input_bytes=min(20_000_000, max(0, int(input_bytes or 0))),
            output_bytes=min(20_000_000, max(0, int(output_bytes or 0))),
            source_count=min(10, max(0, int(source_count or 0))),
            citation_count=min(100, max(0, int(citation_count or 0))),
            warning_count=min(100, max(0, int(warning_count or 0))),
            error_code=error_code,
            remote_request=observed,
            runtime_latency_ms=(
                min(3_600_000, max(0, int((time.monotonic() - runtime_started) * 1000)))
                if runtime_started is not None else None
            ),
        )
        record_audit_event(event)
    except Exception as exc:
        raise MaxisRuntimeError("MAXIS metadata 감사 로그를 기록하지 못했습니다.") from exc


def _profile_execution_identity(metadata: dict, workflow_key: str) -> tuple:
    """Return the content-free profile fields that may not change during one call."""

    workflow = (metadata.get("workflows") or {}).get(workflow_key) or {}
    return (
        str(metadata.get("name") or ""),
        str(metadata.get("stage") or ""),
        str(metadata.get("fingerprint") or ""),
        str(metadata.get("base_url") or ""),
        str(metadata.get("data_policy") or ""),
        str(metadata.get("workflow_bundle_version") or ""),
        str(metadata.get("knowledge_version") or ""),
        str(metadata.get("knowledge_dataset_id") or ""),
        str(metadata.get("knowledge_manifest_sha256") or ""),
        str(metadata.get("knowledge_sentinel_sha256") or ""),
        str(workflow.get("binding") or ""),
        str(workflow.get("version") or ""),
        bool(metadata.get("enabled")),
    )


def _revalidate_after_remote(
    state: dict,
    *,
    profile_name: str,
    metadata: dict,
    workflow_key: str,
) -> None:
    """Discard an in-flight result when its pinned runtime stopped being valid."""

    has_full_pin = all(str(state.get(key) or "").strip() for key in (
        "runtime_profile_fingerprint",
        "runtime_registry_digest",
        "runtime_contract_build",
    ))
    if has_full_pin:
        # This rechecks the kill switch, profile, routes, registry, contract build and
        # Knowledge pins against the immutable thread snapshot.
        verify_thread_runtime(state)
        return
    current = _profile_snapshot(profile_name, required=True)
    if _profile_execution_identity(current, workflow_key) != (
        _profile_execution_identity(metadata, workflow_key)
    ):
        raise RuntimeProfileChanged(
            "MAXIS 호출 중 실행 프로필이 변경되었습니다. 원격 결과를 폐기합니다."
        )


def _verified_retrieval_metadata(response: Any) -> dict[str, Any]:
    """Project only bounded retrieval verification statistics, never resource data."""

    from app.agent.maxis.client import MAX_RETRIEVER_RESOURCES

    retrieval = getattr(response, "retrieval", None)
    if retrieval is None or getattr(retrieval, "dataset_verified", None) is not True:
        raise MaxisRuntimeError("MAXIS Knowledge dataset 검색 결과를 검증하지 못했습니다.")
    count = int(getattr(retrieval, "resource_count", 0) or 0)
    scored = int(getattr(retrieval, "scored_resource_count", 0) or 0)
    if not 1 <= count <= MAX_RETRIEVER_RESOURCES or not 0 <= scored <= count:
        raise MaxisRuntimeError("MAXIS Knowledge dataset 검색 결과를 검증하지 못했습니다.")
    minimum = getattr(retrieval, "minimum_score", None)
    maximum = getattr(retrieval, "maximum_score", None)
    for score in (minimum, maximum):
        if score is not None and (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
        ):
            raise MaxisRuntimeError("MAXIS Knowledge 검색 점수 요약이 올바르지 않습니다.")
    if (
        (scored == 0 and (minimum is not None or maximum is not None))
        or (scored > 0 and (minimum is None or maximum is None))
        or (
            minimum is not None
            and maximum is not None
            and float(minimum) > float(maximum)
        )
    ):
        raise MaxisRuntimeError("MAXIS Knowledge 검색 점수 요약이 올바르지 않습니다.")
    return {
        "verified": True,
        "resourceCount": count,
        "scoredResourceCount": scored,
        "minimumScore": minimum,
        "maximumScore": maximum,
    }


def record_change_audit(
    state: dict,
    *,
    event: Literal[
        "change.proposal", "change.approved", "change.cancelled", "change.execution"
    ],
    status: Literal["started", "succeeded", "failed", "blocked"],
    error_code: str = "",
    change_binding: dict | None = None,
) -> bool:
    """Append one metadata-only HITL/change event joined to the Role correlation.

    The API intentionally has no payload, prompt, result, key, actor or approval-token argument.
    Its binding accepts only a server-HMAC-sealed digest projection. Legacy/direct checkpoints
    without a correlation id are left unchanged.
    """
    correlation = str((state or {}).get("correlation_id") or "").strip()
    if not correlation:
        return False

    def safe_code(value: Any, fallback: str, maximum: int = 64) -> str:
        clean = str(value or "").strip()[:maximum]
        return clean if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", clean) else fallback

    profile_name = safe_code((state or {}).get("runtime_profile"), "local")
    metadata: dict = {}
    if profile_name != "local":
        try:
            metadata = _profile_snapshot(profile_name, required=False)
        except Exception:
            metadata = {}
    stage = str(metadata.get("stage") or "").strip().lower()
    if stage not in {"dev", "internal-pilot", "prod", "test"}:
        stage = "test" if profile_name == "local" else "dev"

    executed = (state or {}).get("runtime_role_engines") or {}
    pinned_routes = (state or {}).get("runtime_role_routes") or {}
    if isinstance(executed, dict) and "maxis" in executed.values():
        route_mode = "maxis"
    elif isinstance(pinned_routes, dict) and "shadow" in pinned_routes.values():
        route_mode = "shadow"
    else:
        route_mode = "local"

    from app.agent.maxis.audit import make_audit_event, record_audit_event

    try:
        audit_event = make_audit_event(
            correlation_id=_audit_correlation(correlation),
            event=event,
            status=status,
            profile=profile_name,
            stage=stage,
            route_mode=route_mode,
            workflow_key="ltm-change",
            workflow_version=safe_code(
                (state or {}).get("workflow_bundle_version"), "", 120
            ) if (state or {}).get("workflow_bundle_version") else "",
            knowledge_version=safe_code(
                (state or {}).get("knowledge_version"), "", 120
            ) if (state or {}).get("knowledge_version") else "",
            role_id="action_executor",
            error_code=error_code,
            change_binding=change_binding,
        )
        record_audit_event(audit_event)
    except Exception as exc:
        raise MaxisRuntimeError("변경 승인 metadata 감사 로그를 기록하지 못했습니다.") from exc
    return True


@_capture_failed_request
def invoke(
    role_id: str,
    payload: dict,
    thread_id: str,
    correlation_id: str,
    *,
    person_references: dict[str, str] | None = None,
    person_reference_values: tuple[str, ...] = (),
    person_reference_types: dict[str, set[str]] | None = None,
    allow_guarded_person_references: bool = False,
) -> dict:
    """Invoke one configured Dify Role and normalize only its typed envelope."""
    runtime_started = time.monotonic()
    state = {
        "runtime_engine": (payload.get("runtime") or {}).get("engine", ""),
        "runtime_profile": (payload.get("runtime") or {}).get("profile", ""),
        "runtime_profile_fingerprint": (payload.get("runtime") or {}).get(
            "profileFingerprint", ""
        ),
        "runtime_role_routes": (payload.get("runtime") or {}).get("roleRoutes", {}),
        "runtime_engine_source": (payload.get("runtime") or {}).get(
            "engineSource", "profile"
        ),
        "runtime_registry_digest": (payload.get("runtime") or {}).get(
            "registryDigest", ""
        ),
        "runtime_contract_build": (payload.get("runtime") or {}).get(
            "contractBuild", ""
        ),
        "workflow_bundle_version": (payload.get("runtime") or {}).get(
            "workflowBundleVersion", ""
        ),
        "knowledge_version": (payload.get("runtime") or {}).get("knowledgeVersion", ""),
        "knowledge_dataset_id": (payload.get("runtime") or {}).get(
            "knowledgeDatasetId", ""
        ),
        "knowledge_manifest_sha256": (payload.get("runtime") or {}).get(
            "knowledgeManifestSha256", ""
        ),
    }
    resolution = resolve_role(role_id, state)
    if resolution.route == "local":
        raise MaxisRuntimeError("local Role을 MAXIS backend로 호출할 수 없습니다.")
    workflow_key = _workflow_key(role_id)
    metadata = _profile_snapshot(resolution.profile, required=True)
    workflow_row = (metadata.get("workflows") or {}).get(workflow_key) or {}
    workflow_version = str(workflow_row.get("version") or "").strip()
    knowledge_version = str(metadata.get("knowledge_version") or "").strip()
    knowledge_dataset_id = str(metadata.get("knowledge_dataset_id") or "").strip()
    knowledge_manifest_sha256 = str(
        metadata.get("knowledge_manifest_sha256") or ""
    ).strip().lower()
    knowledge_sentinel_sha256 = str(
        metadata.get("knowledge_sentinel_sha256") or ""
    ).strip().lower()
    raw_pack = payload.get("evidencePack") or {}
    raw_request_id = raw_pack.get("request_id") if isinstance(raw_pack, dict) else ""
    correlation = str(correlation_id or raw_request_id or "").strip()
    if not correlation:
        correlation = "corr_" + hashlib.sha256(
            f"{thread_id}:{role_id}".encode("utf-8")
        ).hexdigest()[:24]
    try:
        input_bytes = len(_canonical_json(raw_pack).encode("utf-8"))
    except Exception:
        input_bytes = 0
    source_count = len(raw_pack.get("sources") or ()) if isinstance(raw_pack, dict) else 0
    audit_common = {
        "correlation_id": correlation,
        "resolution": resolution,
        "metadata": metadata,
        "workflow_key": workflow_key,
        "workflow_version": workflow_version,
        "knowledge_version": knowledge_version,
        "role_id": role_id,
        "input_bytes": input_bytes,
        "source_count": source_count,
        "runtime_started": runtime_started,
    }
    _record_remote_audit(status="started", **audit_common)
    try:
        from app.agent.maxis.contracts import EvidencePackV1

        evidence = EvidencePackV1.model_validate_json(
            json.dumps(raw_pack, ensure_ascii=False),
            strict=True,
        )
    except Exception as exc:
        _record_remote_audit(
            status="blocked", error_code="evidence_contract", **audit_common,
        )
        raise MaxisRuntimeError("MAXIS EvidencePack 계약 검증에 실패했습니다.") from exc
    if str(metadata.get("data_policy") or "") == "synthetic_only":
        expected_content = f"Synthetic LTM fixture for {role_id}; state is ready."
        expected_query = (
            f"Canonical LTM role {role_id}. Process this guarded user intent:\n"
            f"Synthetic MAXIS integration exercise for canonical role {role_id}."
        )
        synthetic_sources = tuple(evidence.sources)
        expected_pseudonym = "ltm_" + hmac.new(
            _PSEUDONYM_KEY,
            _SYNTHETIC_USER_IDENTITY.casefold().encode("utf-8"),
            "sha256",
        ).hexdigest()[:16]
        source = synthetic_sources[0] if len(synthetic_sources) == 1 else None
        if (
            evidence.contract != "evidence-pack.v1"
            or evidence.use_case != role_id
            or evidence.query != expected_query
            or evidence.pseudonymous_user != expected_pseudonym
            or evidence.scope.projects
            or evidence.scope.spaces
            or evidence.scope.repositories
            or evidence.facts
            or evidence.metrics
            or not _synthetic_query_context_allowed(role_id, evidence)
            or evidence.freshness != "fresh"
            or evidence.partial
            or evidence.flags != ("synthetic_only",)
            or source is None
            or source.source_id != _SYNTHETIC_SOURCE_ID
            or source.kind != "ltm"
            or source.canonical_id != _SYNTHETIC_CANONICAL_ID
            or source.title != _SYNTHETIC_TITLE
            or source.scope
            or source.url
            or source.content != expected_content
            or source.retrieved_at != evidence.requested_at
            or source.updated_at is not None
            or source.content_sha256 != hashlib.sha256(
                expected_content.encode("utf-8")
            ).hexdigest()
            or source.partial
            or source.truncated
        ):
            _record_remote_audit(
                status="blocked", error_code="synthetic_policy", **audit_common,
            )
            raise MaxisRuntimeError(
                "합성 검증 모드의 MAXIS 프로필은 고정된 합성 fixture만 전송할 수 있습니다."
            )
    if (
        not knowledge_dataset_id
        or not re.fullmatch(r"[0-9a-f]{64}", knowledge_manifest_sha256)
        or str(state.get("knowledge_dataset_id") or "") != knowledge_dataset_id
        or str(state.get("knowledge_manifest_sha256") or "").lower()
        != knowledge_manifest_sha256
        or evidence.knowledge_manifest_sha256 != knowledge_manifest_sha256
    ):
        _record_remote_audit(
            status="blocked", error_code="knowledge_manifest_mismatch", **audit_common,
        )
        raise MaxisRuntimeError(
            "MAXIS Knowledge dataset/manifest가 고정된 프로필과 다릅니다."
        )
    if (
        not workflow_version
        or not knowledge_version
        or not re.fullmatch(r"[0-9a-f]{64}", knowledge_sentinel_sha256)
    ):
        _record_remote_audit(
            status="blocked", error_code="revision_pin_missing", **audit_common,
        )
        raise MaxisRuntimeError(
            "MAXIS Workflow/Knowledge published revision이 프로필에 고정되지 않았습니다."
        )
    # A pack can age while a remote attempt is running. Its signed/hashed content
    # and original freshness label stay immutable; reevaluate time at each invocation.
    from app.agent.maxis.context_guard import ContextPolicy
    now = datetime.now(timezone.utc)
    observation_times = (
        [row.retrieved_at for row in evidence.sources]
        + [row.observed_at for row in evidence.role_context]
    )
    freshness_seconds = ContextPolicy(allowed_scopes={}).freshness_seconds
    expired = any(
        (now - instant).total_seconds() > freshness_seconds
        for instant in observation_times
    )
    if role_id in {"work_architect", "auditor", "editor_author"} and (
            evidence.freshness in {"mixed", "stale"} or evidence.partial or expired):
        _record_remote_audit(
            status="blocked", error_code="freshness_policy", **audit_common,
        )
        raise MaxisRuntimeError(
            "MAXIS 업무 초안 근거가 완전성·15분 freshness 기준을 충족하지 못했습니다. "
            "다시 조회해 주세요."
        )
    try:
        from app.agent.maxis import credentials
        from app.agent.maxis.client import DifyClient

        api_key = credentials.get_for(resolution.profile, workflow_key)
        if not api_key:
            _record_remote_audit(
                status="blocked", error_code="credential_missing", **audit_common,
            )
            raise MaxisRuntimeError(f"MAXIS {workflow_key} API key가 없습니다.")
        with DifyClient(_client_config(metadata, resolution.binding)) as client:
            invoke_inputs = {
                # EvidencePack is the sole content-bearing outbound object.  The
                # client independently validates it before any network request.
                "context_pack": evidence,
                "role_id": role_id,
                "correlation_id": correlation,
                "output_contract": str(_binding_value(
                    resolution.binding, "output_contract",
                    default="remote-role-result.v1",
                )),
                "workflow_version": workflow_version,
                "knowledge_version": knowledge_version,
            }
            response = client.invoke(
                resolution.binding,
                api_key=api_key,
                query=evidence.query,
                user=evidence.pseudonymous_user,
                inputs=invoke_inputs,
                workflow_id=workflow_version,
                expected_knowledge_dataset_id=knowledge_dataset_id,
            )
    except MaxisRuntimeError:
        raise
    except Exception as exc:
        raw_code = getattr(getattr(exc, "code", None), "value", "")
        code = str(raw_code or "transport_failure")
        blocked = code in {
            "configuration", "policy", "bad_request", "authentication",
            "forbidden", "not_found", "conflict",
        }
        _record_remote_audit(
            status="blocked" if blocked else "failed",
            error_code=("gateway_" + code)[:80],
            remote_request=getattr(exc, "telemetry", None),
            latency_ms=getattr(getattr(exc, "telemetry", None), "latency_ms", 0),
            **audit_common,
        )
        guidance = {
            "workflow_contract": "서버 Workflow 응답 계약 오류. 게시 Workflow의 역할별 검증 결과를 확인하세요.",
            "model_unavailable": "서버 Workflow에 설정된 모델을 사용할 수 없습니다. 모델 선택과 게시 상태를 확인하세요.",
            "model_credentials": "서버 모델 인증 오류. MAXIS 프로젝트의 모델 연결을 확인하세요.",
            "model_parameters": "서버 모델 옵션 오류. Workflow 모델의 지원 옵션을 확인하세요.",
        }.get(code, "")
        raise MaxisRuntimeError(
            f"MAXIS Role 호출에 실패했습니다: {role_id}"
            + (f" — {guidance}" if guidance else "")
        ) from exc

    # Carry the native request observation through every post-HTTP rejection branch.
    # HTTP success alone is neither contract acceptance nor measured human usefulness.
    audit_common["remote_request"] = getattr(response, "telemetry", None)

    # A manager may flip the kill switch or change routes/profile while the blocking
    # Dify call is in flight.  Revalidate before reading or projecting any remote
    # content so the completed response cannot cross the local trust boundary.
    try:
        _revalidate_after_remote(
            state,
            profile_name=resolution.profile,
            metadata=metadata,
            workflow_key=workflow_key,
        )
    except Exception as exc:
        _record_remote_audit(
            status="blocked",
            error_code="runtime_changed_in_flight",
            latency_ms=int(getattr(response, "latency_ms", 0) or 0),
            **audit_common,
        )
        raise MaxisRuntimeError(
            "MAXIS 호출 중 실행 설정이 변경되어 원격 결과를 폐기했습니다."
        ) from exc

    try:
        retrieval_metadata = _verified_retrieval_metadata(response)
    except MaxisRuntimeError:
        _record_remote_audit(
            status="blocked",
            error_code="knowledge_dataset_unverified",
            latency_ms=int(getattr(response, "latency_ms", 0) or 0),
            **audit_common,
        )
        raise

    answer = str(getattr(response, "answer", "") or "")
    if not answer:
        _record_remote_audit(
            status="blocked", error_code="response_empty",
            latency_ms=int(getattr(response, "latency_ms", 0) or 0),
            **audit_common,
        )
        raise MaxisRuntimeError(f"MAXIS Role 응답이 비어 있습니다: {role_id}")
    # The schema that crossed the same guarded boundary as the evidence is the sole
    # payload authority.  Never trust a mutable sibling field in ``role_payload``.
    result_schema = evidence.payload_schema()
    response_block_code = "response_revision"
    try:
        from app.agent.maxis.response_guard import ResponseGuard, ResponseGuardError

        envelope, typed = ResponseGuard().validate_remote_role(
            answer,
            expected_role_id=role_id,
            payload_model=result_schema,
            evidence=evidence,
            expected_knowledge_manifest_sha256=knowledge_manifest_sha256,
        )
        expected_workflow = workflow_version
        if expected_workflow and envelope.workflow_version != expected_workflow:
            raise MaxisRuntimeError("MAXIS Workflow version이 고정된 프로필과 다릅니다.")
        expected_knowledge = knowledge_version
        if expected_knowledge and envelope.knowledge_version != expected_knowledge:
            raise MaxisRuntimeError("MAXIS Knowledge version이 고정된 프로필과 다릅니다.")
        proof_digest = hashlib.sha256(
            envelope.knowledge_proof.encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(proof_digest, knowledge_sentinel_sha256):
            response_block_code = "knowledge_sentinel_mismatch"
            raise MaxisRuntimeError(
                "MAXIS Knowledge release가 활성 프로필과 다릅니다."
            )
        if envelope.status == "blocked":
            response_block_code = "remote_status_blocked"
            raise MaxisBlockedResult(role_id, blocked_recovery(
                warnings=envelope.warnings, evidence_flags=evidence.flags,
            ))
    except MaxisRuntimeError:
        _record_remote_audit(
            status="blocked", error_code=response_block_code,
            latency_ms=int(getattr(response, "latency_ms", 0) or 0),
            output_bytes=len(answer.encode("utf-8")), **audit_common,
        )
        raise
    except ResponseGuardError as exc:
        _record_remote_audit(
            status="blocked", error_code=("response_" + exc.code)[:80],
            latency_ms=int(getattr(response, "latency_ms", 0) or 0),
            output_bytes=len(answer.encode("utf-8")), **audit_common,
        )
        error = MaxisRuntimeError(
            f"MAXIS Role 응답 계약 검증에 실패했습니다: {role_id}"
        )
        if exc.code == "unverified_numeric_claim":
            error._numeric_regeneration_allowed = _numeric_regeneration_binding_matches(
                answer, role_id=role_id, workflow_version=workflow_version,
                knowledge_version=knowledge_version, knowledge_manifest_sha256=knowledge_manifest_sha256,
                knowledge_sentinel_sha256=knowledge_sentinel_sha256,
            )
        raise error from exc
    except Exception as exc:
        _record_remote_audit(
            status="blocked", error_code="response_contract",
            latency_ms=int(getattr(response, "latency_ms", 0) or 0),
            output_bytes=len(answer.encode("utf-8")), **audit_common,
        )
        raise MaxisRuntimeError(
            f"MAXIS Role 응답 계약 검증에 실패했습니다: {role_id}"
        ) from exc
    if envelope.status == "needs_input":
        latency_ms = int(getattr(response, "latency_ms", 0) or 0)
        usage = getattr(response, "usage", None)
        metadata_out = {
            "profile": resolution.profile,
            "workflowBundleVersion": resolution.workflow_bundle_version,
            "knowledgeVersion": resolution.knowledge_version,
            "knowledgeDatasetId": resolution.knowledge_dataset_id,
            "knowledgeManifestSha256": resolution.knowledge_manifest_sha256,
            "remoteLogPolicyId": resolution.remote_log_policy_id,
            "remoteLogMode": resolution.remote_log_mode,
            "remoteLogRetentionDays": resolution.remote_log_retention_days,
            "correlationId": correlation,
            "messageId": str(getattr(response, "message_id", "") or ""),
            "knowledgeRetrieval": retrieval_metadata,
            "latencyMs": latency_ms,
            "requestTelemetry": (
                with_result_status(response.telemetry, "needs_input").model_dump(mode="json")
                if getattr(response, "telemetry", None) is not None else None
            ),
            "usage": {
                "promptTokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completionTokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "totalTokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        }
        _record_remote_audit(
            status="succeeded", latency_ms=latency_ms,
            result_status="needs_input",
            output_bytes=len(answer.encode("utf-8")),
            citation_count=len(envelope.citations),
            warning_count=len(envelope.warnings), **audit_common,
        )
        raise MaxisNeedsInput(
            role_id,
            envelope.missing_inputs,
            state_patch=_remote_state_patch(envelope, evidence),
            metadata=metadata_out,
        )
    if typed is None:
        _record_remote_audit(
            status="blocked", error_code="typed_payload_missing",
            latency_ms=int(getattr(response, "latency_ms", 0) or 0),
            output_bytes=len(answer.encode("utf-8")), **audit_common,
        )
        raise MaxisRuntimeError(f"MAXIS Role typed payload가 없습니다: {role_id}")
    try:
        typed = _rehydrate_readonly_role_people(
            role_id, typed, raw_pack, person_reference_values, person_reference_types or {},
        )
        if role_id == "editor_author" and person_references:
            typed = _rehydrate_editor_person_references(typed, person_references)
        encoded_typed = _canonical_json(_json_value(typed))
        unresolved_markers = {
            match.group(0) for match in _PERSON_REFERENCE.finditer(encoded_typed)
        }
        if unresolved_markers and allow_guarded_person_references:
            # Shadow evaluation deliberately has no opaque-ref -> Jira-id map.  It may
            # retain a marker only when that exact marker was already present in the
            # guarded EvidencePack; an invented marker remains a blocked response.
            encoded_evidence = _canonical_json(raw_pack)
            if role_id != "editor_author" or any(
                marker not in encoded_evidence for marker in unresolved_markers
            ):
                raise MaxisRuntimeError(
                    "MAXIS Editor 응답에 검증되지 않은 person reference가 있습니다."
                )
        elif unresolved_markers:
            raise MaxisRuntimeError(
                "MAXIS Role 응답의 가명 person reference를 로컬 사용자로 복원할 수 없습니다."
            )
    except MaxisRuntimeError:
        _record_remote_audit(
            status="blocked", error_code="person_reference_unresolved",
            latency_ms=int(getattr(response, "latency_ms", 0) or 0),
            output_bytes=len(answer.encode("utf-8")), **audit_common,
        )
        raise

    latency_ms = int(getattr(response, "latency_ms", 0) or 0)
    _record_remote_audit(
        status="succeeded",
        latency_ms=latency_ms,
        output_bytes=len(answer.encode("utf-8")),
        citation_count=len(envelope.citations),
        warning_count=len(envelope.warnings),
        **audit_common,
    )

    log.info(
        "MAXIS Role 완료 role=%s route=%s profile=%s latency_ms=%s",
        role_id,
        resolution.route,
        resolution.profile,
        latency_ms,
    )
    usage = getattr(response, "usage", None)
    return {
        "output": _json_value(typed),
        "statePatch": _remote_state_patch(envelope, evidence),
        "metadata": {
            "profile": resolution.profile,
            "workflowBundleVersion": resolution.workflow_bundle_version,
            "knowledgeVersion": resolution.knowledge_version,
            "knowledgeDatasetId": resolution.knowledge_dataset_id,
            "knowledgeManifestSha256": resolution.knowledge_manifest_sha256,
            "remoteLogPolicyId": resolution.remote_log_policy_id,
            "remoteLogMode": resolution.remote_log_mode,
            "remoteLogRetentionDays": resolution.remote_log_retention_days,
            "correlationId": correlation,
            "messageId": str(getattr(response, "message_id", "") or ""),
            "knowledgeRetrieval": retrieval_metadata,
            "latencyMs": latency_ms,
            "requestTelemetry": (
                with_result_status(response.telemetry, "completed").model_dump(mode="json")
                if getattr(response, "telemetry", None) is not None else None
            ),
            "usage": {
                "promptTokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completionTokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "totalTokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        },
    }


def invoke_role(
    role_id: str,
    state: dict,
    messages: list,
    schema: dict,
    *,
    output_kind: Literal["structured", "text"],
) -> dict:
    """Resolve and execute a remote Role; callers still own schema/apply guards."""
    resolution = resolve_role(role_id, state)
    if resolution.route == "local":
        raise MaxisRuntimeError("local Role에는 remote invoke를 사용할 수 없습니다.")
    payload = role_payload(role_id, state, messages, schema, output_kind=output_kind)
    person_reference_values = ()
    person_reference_types = {}
    if role_id in _READONLY_PERSON_ROLES:
        from app.agent.tools._ctx import settings
        from app.agent.maxis.person_references import identity_types
        person_reference_values = _known_identity_values(state, settings())
        person_reference_types = identity_types(state, settings())
    attempts = []
    for attempt in range(2):
        try:
            result = invoke(
                role_id, payload, str(state.get("thread_id") or ""),
                str(state.get("correlation_id") or ""),
                person_references=(
                    editor_person_reference_map(state.get("_maxis_identity_values") or ())
                    if role_id == "editor_author" else None
                ),
                person_reference_values=person_reference_values,
                person_reference_types=person_reference_types,
            )
            break
        except MaxisRuntimeError as exc:
            from app.agent.maxis.errors import MaxisGatewayError, MaxisErrorCode
            from app.agent.maxis.response_guard import ResponseGuardError
            metadata = dict(getattr(exc, "metadata", None) or {})
            observation = metadata.get("requestTelemetry")
            if observation is not None:
                attempts.append(observation)
            cause = exc.__cause__
            native_role = role_id in {
                "query_specialist", "request_architect", "work_architect",
                "knowledge_curator", "result_integrator",
            }
            local_numeric_rejection = (
                native_role and isinstance(cause, ResponseGuardError)
                and cause.code == "unverified_numeric_claim"
                and exc._numeric_regeneration_allowed is True
            )
            remote_draft_rejection = (
                isinstance(cause, MaxisGatewayError)
                and ((cause.draft_regeneration_role == "editor_author" and role_id == "editor_author")
                     or (not cause.draft_regeneration_role and native_role))
                and cause.code == MaxisErrorCode.WORKFLOW_CONTRACT and cause.draft_regeneration_allowed
                and cause.status_code in {400, 422}
            )
            regenerate = (
                attempt == 0 and resolution.route == "maxis"
                and (output_kind == "structured"
                     or (role_id == "result_integrator" and output_kind == "text"))
                and (remote_draft_rejection or local_numeric_rejection)
                and observation is not None
            )
            if not regenerate:
                if attempts:
                    metadata["requestAttempts"] = attempts
                    exc.metadata = metadata
                raise
            # A new invocation rechecks the same immutable profile, source freshness,
            # kill switch and credentials. It grants no authority and never accepts
            # the rejected candidate or calls a local model. Audit rows already persist.
            from app.agent.maxis.draft_feedback import with_native_feedback
            try:
                reason = "numeric_grounding" if local_numeric_rejection else cause.draft_regeneration_reason
                payload = with_native_feedback(payload, role_id, reason=reason)
            except (KeyError, TypeError, ValueError):
                metadata["requestAttempts"] = attempts
                exc.metadata = metadata
                raise exc
    if attempts:
        metadata = dict(result.get("metadata") or {})
        if metadata.get("requestTelemetry") is not None:
            attempts.append(metadata["requestTelemetry"])
        metadata["requestAttempts"] = attempts
        result = {**result, "metadata": metadata}
    output = result.get("output") if isinstance(result, dict) else None
    if not isinstance(output, dict):
        raise MaxisRuntimeError(f"MAXIS Role output이 객체가 아닙니다: {role_id}")
    state_patch = result.get("statePatch") if isinstance(result, dict) else None
    metadata = result.get("metadata") if isinstance(result, dict) else None
    return RemoteRoleOutput(
        output,
        state_patch if isinstance(state_patch, dict) else {},
        metadata if isinstance(metadata, dict) else {},
    )


def run_shadow(
    role_id: str,
    state: dict,
    messages: list,
    schema: dict,
    *,
    output_kind: Literal["structured", "text"],
    local_output: Any,
) -> bool:
    """Queue one observational comparison and immediately return the local result path.

    The bounded worker owns the only transient references to prompts and outputs.  Its
    durable evaluator accepts deployment labels and aggregate counters only; the remote
    candidate is never returned to graph state, approval payloads, logs, or the UI.
    """
    from copy import deepcopy

    from app.agent.maxis import shadow_eval

    try:
        resolution = resolve_role(role_id, state)
        if resolution.route != "shadow":
            return False
        metadata = _profile_snapshot(resolution.profile, required=True)
        workflow_key = _workflow_key(role_id)
        workflow = (metadata.get("workflows") or {}).get(workflow_key) or {}
        dimensions = shadow_eval.ShadowDimensions(
            profile=str(metadata.get("name") or resolution.profile),
            # The profile fingerprint intentionally excludes routes.  Promoting a route
            # from shadow to maxis must keep the exact evaluated deployment bucket.
            profile_fingerprint=str(metadata.get("fingerprint") or ""),
            workflow_key=workflow_key,
            role_id=role_id,
            workflow_version=str(workflow.get("version") or ""),
            knowledge_version=str(metadata.get("knowledge_version") or ""),
        )
        # Build the guarded, size-bounded wire contract before enqueueing.  The worker
        # therefore never retains the full LangGraph state or original prompt objects;
        # only the same EvidencePack payload allowed to cross the network is captured.
        frozen_payload = deepcopy(
            role_payload(role_id, state, messages, schema, output_kind=output_kind)
        )
        frozen_local = deepcopy(local_output)
    except Exception as exc:
        log.info(
            "MAXIS shadow enqueue 실패 role=%s error_type=%s",
            role_id, type(exc).__name__,
        )
        return False

    def invoke_candidate():
        result = invoke(
            role_id,
            frozen_payload,
            "",
            "",
            allow_guarded_person_references=(role_id == "editor_author"),
        )
        return RemoteRoleOutput(
            result.get("output") or {},
            result.get("statePatch") if isinstance(result, dict) else {},
            result.get("metadata") if isinstance(result, dict) else {},
        )

    try:
        accepted = shadow_eval.submit(
            dimensions=dimensions,
            local_output=frozen_local,
            invoke_remote=invoke_candidate,
        )
    except Exception as exc:
        # Shadow instrumentation can never fail the authoritative local response.
        log.info(
            "MAXIS shadow enqueue 실패 role=%s error_type=%s",
            role_id, type(exc).__name__,
        )
        return False
    log.info(
        "MAXIS shadow 예약 role=%s accepted=%s", role_id, bool(accepted),
    )
    return accepted


__all__ = [
    "AcquisitionMarker", "MaxisBlockedResult", "MaxisNeedsInput", "MaxisRuntimeError",
    "REMOTE_CONTRACT_BUILD_VERSION",
    "RemoteRoleOutput", "RoleResolution",
    "RoleRuntime", "RuntimeProfileChanged", "attest_query_result",
    "begin_local_acquisition", "finish_local_acquisition", "invoke", "invoke_role",
    "editor_person_reference_map", "prepare_thread_runtime", "profile_fingerprint",
    "record_change_audit", "resolve_role", "role_payload", "run_shadow", "thread_runtime_pin",
    "verify_thread_runtime",
]
