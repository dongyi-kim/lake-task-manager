#!/usr/bin/env python3
"""Run the synthetic-only dev Knowledge suite without changing LTM routing.

The first case is always a pilot.  If the service response cannot identify retrieval
documents against the checked-in immutable ledger, the remaining 24 calls are not sent.
Only a metadata-only diagnostic artifact is written beneath ``.cache/maxis/evals``.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import os
import sys
import time
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.maxis import credentials, profiles  # noqa: E402
from app.agent.maxis.knowledge_config import resolve_path as knowledge_path
from app.agent.maxis.knowledge_eval import (  # noqa: E402
    DEFAULT_PILOT_CASE,
    KnowledgeEvalError,
    RetrievalIdentityUnavailable,
    build_artifact,
    build_synthetic_pack,
    case_result,
    evaluation_source_file_sha256,
    evaluate_case,
    load_identity_map,
    load_suite,
    make_client,
    result_schema_path,
    safe_remote_error,
    validate_evaluation_binding,
    workflow_binding_for_case,
    write_artifact_atomic,
)
from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER  # noqa: E402


MAXIS_EVAL_DESTINATION_IDENTITY_ENV = (
    "LTM_MAXIS_EVAL_DESTINATION_IDENTITY_SHA256"
)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the dev-only synthetic MAXIS/Dify Knowledge evaluation."
    )
    parser.add_argument(
        "--profile", choices=("dev",), default="dev",
        help="Only the synthetic-only dev profile is accepted.",
    )
    parser.add_argument(
        "--pilot-case", default=DEFAULT_PILOT_CASE,
        help="Case sent first to prove document identity is observable.",
    )
    parser.add_argument(
        "--pilot-only", action="store_true",
        help="Run only the mandatory one-case identity pilot.",
    )
    return parser.parse_args(argv)


def _require_network_handoff() -> str:
    """Reject accidental direct execution before opening the credential store.

    This follows the existing Agent evaluation launcher's process-local handoff
    threat model. The marker is not hostile same-user authorization; it makes the
    safe launcher path mandatory and prevents an unmarked direct runner invocation.
    """

    identity = str(
        os.getenv(MAXIS_EVAL_DESTINATION_IDENTITY_ENV) or ""
    ).strip().lower()
    if (
        os.getenv(NETWORK_PREFLIGHT_MARKER) != "1"
        or len(identity) != 64
        or any(character not in "0123456789abcdef" for character in identity)
    ):
        raise KnowledgeEvalError("network_authorization_required")
    return identity


def _destination_identity(base_url: Any) -> str:
    canonical = str(base_url or "").strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _credential_free_dev_api_base() -> str:
    """Use the launcher's exact non-secret destination canonicalization."""

    path = ROOT / "deploy" / "maxis-dify" / "launch_knowledge_evals.py"
    spec = importlib.util.spec_from_file_location("ltm_maxis_eval_launch_boundary", path)
    if spec is None or spec.loader is None:
        raise KnowledgeEvalError("network_handoff_configuration_invalid")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return str(module.credential_free_dev_api_base() or "").strip()
    except Exception:
        raise KnowledgeEvalError("network_handoff_configuration_invalid") from None


def _governed_document_ids(identity: Any) -> set[str]:
    """Return identities present in every immutable governed-document ledger."""

    ledgers = (
        set(identity.document_ids_by_uuid.values()),
        set(identity.document_ids_by_name.values()),
        set(identity.segment_sha256_by_document),
        set(identity.canonical_text_by_document),
    )
    return set.intersection(*ledgers) if ledgers else set()


def _select_pilot(suite: Any, identity: Any, case_id: str) -> Any:
    """Accept only an ACL-authorized positive case backed by governed Knowledge."""

    pilot = suite.case(case_id)
    expected = set(pilot.include_document_ids)
    if (
        pilot.case_type != "positive"
        or pilot.expected_outcome != "retrieve"
        or pilot.ignore_query_instructions
        or pilot.signed_human_review_required
        or not pilot.target_document_id
        or pilot.target_document_id not in expected
    ):
        raise KnowledgeEvalError("pilot_case_not_positive_retrieval")
    governed = _governed_document_ids(identity)
    allowed = set(identity.allowed_document_ids_by_role.get(pilot.role_id, frozenset()))
    if not expected or not expected.issubset(governed) or not expected.issubset(allowed):
        raise KnowledgeEvalError("pilot_case_not_governed")
    return pilot


def _pilot_governed_identity_observed(
    pilot: Any,
    observation: Any,
    identity: Any,
) -> bool:
    """Prove the pilot retrieved its expected, ledger-bound governed document."""

    expected = set(pilot.include_document_ids)
    observed = set(observation.retrieved_document_ids)
    governed = _governed_document_ids(identity)
    allowed = set(identity.allowed_document_ids_by_role.get(pilot.role_id, frozenset()))
    return bool(
        observed
        and pilot.target_document_id in observed
        and expected.issubset(observed)
        and observed.issubset(governed)
        and observed.issubset(allowed)
        and observation.dataset_ok
        and observation.retrieval_identity_ok
        and observation.role_acl_ok
        and observation.segment_ledger_ok
    )


def _validate_bundle() -> None:
    path = ROOT / "deploy" / "maxis-dify" / "validate_bundle.py"
    spec = importlib.util.spec_from_file_location("ltm_maxis_bundle_validator", path)
    if spec is None or spec.loader is None:
        raise KnowledgeEvalError("bundle_validator_unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        module.validate_bundle(ROOT)
    except Exception as exc:
        raise KnowledgeEvalError("bundle_validation_failed") from exc


def _profile_identity(profile: dict[str, Any]) -> Any:
    if not profile.get("knowledge_environment_binding") and not profile.get("knowledge_source_manifest"):
        return load_identity_map()
    return load_identity_map(
        environment_path=knowledge_path(profile.get("knowledge_environment_binding")),
        knowledge_manifest_path=knowledge_path(profile.get("knowledge_source_manifest")),
    )


def _profile_source_hashes(profile: dict[str, Any]) -> dict[str, str]:
    return (evaluation_source_file_sha256(profile) if profile.get("knowledge_source_manifest")
            else evaluation_source_file_sha256())


def _preflight() -> tuple[dict[str, Any], dict[str, Any], Any, Any]:
    _validate_bundle()
    suite = load_suite()
    try:
        profile = profiles.resolve("dev")
    except Exception as exc:
        raise KnowledgeEvalError("dev_profile_unavailable") from exc
    if (
        profile.get("name") != "dev"
        or profile.get("stage") != "dev"
        or profile.get("data_policy") != "synthetic_only"
        or profile.get("verified") is not True
    ):
        raise KnowledgeEvalError("dev_profile_not_verified")
    if profile.get("active") is True or profile.get("enabled") is True:
        raise KnowledgeEvalError("dev_profile_must_remain_inactive")
    identity = _profile_identity(profile)
    route_snapshot = profiles.routes()
    if set(route_snapshot.values()) != {"local"}:
        raise KnowledgeEvalError("dev_routes_must_remain_local")
    try:
        binding = {
            **profiles.knowledge_eval_attestation_binding(profile),
            "source_file_sha256": _profile_source_hashes(profile),
        }
    except Exception as exc:
        raise KnowledgeEvalError("evaluation_binding_invalid") from exc
    validate_evaluation_binding(
        profile=profile,
        binding=binding,
        suite=suite,
        identity=identity,
    )
    return deepcopy(profile), deepcopy(binding), suite, identity


def _binding_unchanged(
    frozen_profile: dict[str, Any],
    frozen_binding: dict[str, Any],
) -> bool:
    """Recompute the full binding after execution; drift aborts the artifact."""

    try:
        current_profile = profiles.resolve("dev")
        current_suite = load_suite()
        current_identity = _profile_identity(current_profile)
        current_binding = {
            **profiles.knowledge_eval_attestation_binding(current_profile),
            "source_file_sha256": _profile_source_hashes(current_profile),
        }
        validate_evaluation_binding(
            profile=current_profile,
            binding=current_binding,
            suite=current_suite,
            identity=current_identity,
        )
    except Exception:
        return False
    return (
        current_profile == frozen_profile
        and current_binding == frozen_binding
    )


def _evidence_id(suite_sha256: str, started: datetime) -> str:
    stamp = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"dev-keval-{stamp}-{suite_sha256[:8]}"


def _elapsed_ms(started_ns: int) -> int:
    return max(0, (time.perf_counter_ns() - started_ns) // 1_000_000)


def run(argv: list[str] | None = None) -> tuple[Path, str, dict[str, Any]]:
    args = _arguments(argv)
    if args.profile != "dev":
        raise KnowledgeEvalError("evaluation_environment_not_dev")
    # Keep this before ``_preflight``: profile verification reads the encrypted
    # credential store while evaluating its persisted probe binding.
    handoff_destination_identity = _require_network_handoff()
    credential_free_base_url = _credential_free_dev_api_base()
    if not hmac.compare_digest(
        handoff_destination_identity,
        _destination_identity(credential_free_base_url),
    ):
        raise KnowledgeEvalError("network_handoff_mismatch")
    profile, binding, suite, identity = _preflight()
    # Recheck the frozen resolved profile before any per-case credential or socket
    # operation. This detects profile drift after the credential-free handoff check.
    if not hmac.compare_digest(
        handoff_destination_identity,
        _destination_identity(profile.get("base_url")),
    ):
        raise KnowledgeEvalError("network_handoff_mismatch")
    pilot = _select_pilot(suite, identity, args.pilot_case)
    ordered = (pilot,) if args.pilot_only else (
        pilot, *(case for case in suite.cases if case.case_id != pilot.case_id)
    )
    active_snapshot = profiles.active_profile_name()
    routes_snapshot = profiles.routes()
    started = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    clients: dict[str, Any] = {}
    aborted = False
    try:
        for index, case in enumerate(ordered):
            workflow = workflow_binding_for_case(case)
            workflow_profile = (profile.get("workflows") or {}).get(workflow.key) or {}
            workflow_version = str(workflow_profile.get("version") or "")
            pack = build_synthetic_pack(
                case,
                suite_sha256=suite.sha256,
                knowledge_manifest_sha256=str(binding["knowledge_manifest_sha256"]),
                requested_at=started,
            )
            case_started_ns = time.perf_counter_ns()
            try:
                api_key = credentials.get_for("dev", workflow.key)
                if not api_key:
                    raise KnowledgeEvalError("credential_unavailable")
                client = clients.get(workflow.key)
                if client is None:
                    client = make_client(profile, workflow)
                    clients[workflow.key] = client
                observation = evaluate_case(
                    client=client,
                    binding=workflow,
                    api_key=api_key,
                    case=case,
                    pack=pack,
                    identity=identity,
                    workflow_version=workflow_version,
                    knowledge_version=str(profile.get("knowledge_version") or ""),
                    knowledge_sentinel_sha256=str(
                        profile.get("knowledge_sentinel_sha256") or ""
                    ),
                )
                # The client reports transport time on success, but a truthful
                # evaluation must use the same whole-call wall clock for success,
                # timeout, and every other failure path.
                observation = replace(
                    observation, latency_ms=_elapsed_ms(case_started_ns),
                )
                pilot_identity_ok = bool(
                    index != 0
                    or _pilot_governed_identity_observed(pilot, observation, identity)
                )
                results.append(case_result(
                    case,
                    workflow_key=workflow.key,
                    observation=observation,
                    error_code=(
                        "" if pilot_identity_ok
                        else "pilot_governed_identity_not_observed"
                    ),
                ))
                if not pilot_identity_ok:
                    aborted = True
                    break
            except RetrievalIdentityUnavailable as exc:
                results.append(case_result(
                    case,
                    workflow_key=workflow.key,
                    error_code=safe_remote_error(exc),
                    identity_unavailable=True,
                    latency_ms=_elapsed_ms(case_started_ns),
                ))
                aborted = True
                break
            except Exception as exc:
                results.append(case_result(
                    case,
                    workflow_key=workflow.key,
                    error_code=safe_remote_error(exc),
                    latency_ms=_elapsed_ms(case_started_ns),
                ))
                # A pilot transport/contract/configuration failure makes the remaining
                # calls uninterpretable. Only a ledger-bound target-document identity
                # observed above can open the full-suite fan-out.
                if index == 0:
                    aborted = True
                    break
            finally:
                if "api_key" in locals():
                    del api_key
    finally:
        for client in clients.values():
            client.close()
    if (
        profiles.active_profile_name() != active_snapshot
        or profiles.routes() != routes_snapshot
        or not _binding_unchanged(profile, binding)
    ):
        aborted = True
    completed = datetime.now(timezone.utc)
    schema_sha256 = hashlib.sha256(result_schema_path().read_bytes()).hexdigest()
    artifact = build_artifact(
        evidence_id=_evidence_id(suite.sha256, started),
        profile=profile,
        binding=binding,
        suite=suite,
        schema_sha256=schema_sha256,
        cases=results,
        started_at=started,
        completed_at=completed,
        pilot_case_id=pilot.case_id,
        aborted=aborted,
        execution_scope="pilot_only" if args.pilot_only else "full_suite",
    )
    path, artifact_sha256 = write_artifact_atomic(artifact)
    return path, artifact_sha256, artifact


def main(argv: list[str] | None = None) -> int:
    try:
        path, digest, artifact = run(argv)
    except KnowledgeEvalError as exc:
        print(f"Knowledge evaluation did not start: {exc.code}", file=sys.stderr)
        return 2
    print(
        "Knowledge evaluation artifact: "
        f"{path} ({artifact['outcome']}, sha256={digest})"
    )
    if artifact["outcome"] == "aborted":
        return 2
    if artifact["outcome"] == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
