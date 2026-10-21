"""Offline MAXIS input diagnostics and existing-raw measurement reuse.

No network, credentials, runtime activation, model calls or semantic grading.
Both commands write a NEW immutable diagnostic artifact under the eval cache.
They are not a live workflow, full-app benchmark or qualification launcher.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agent_eval_protocol import (  # noqa: E402
    git_snapshot, load_protocol, raw_result_path, reserve_raw_result_path, write_raw_result,
)
from tools.maxis_performance_metrics import editor_observations, summarize_attempts  # noqa: E402

CONTRACT = "maxis-offline-baseline.v1"
_SOURCES = ("maxis_performance_baseline.py", "maxis_performance_metrics.py",
            "maxis_role_eval_cases.py", "maxis_context_diagnostics.py")
_GUARD_CODES = frozenset({
    "invalid_payload_schema", "noncanonical_payload_schema", "payload_schema_digest_mismatch",
    "contract_validation_failed", "context_too_large", "role_context_too_large", "query_too_large",
    "context_collection_limit", "context_depth_limit", "text_too_long", "context_key_not_allowed",
    "scope_not_allowed", "scope_not_configured", "forbidden_authority_field", "unpseudonymized_identity",
    "invalid_datetime", "timezone_required", "future_observation_time", "future_retrieval_time",
})


def _source_hashes() -> dict:
    return {name: hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest() for name in _SOURCES}


def _cache_path(path: str | Path) -> Path:
    cache = (ROOT / ".cache/agent-evaluation").resolve()
    target = Path(path).resolve()
    if (not cache.is_relative_to(ROOT.resolve()) or not target.is_relative_to(cache)
            or target.suffix != ".json"):
        raise ValueError("JSON artifact must resolve inside the workspace evaluation cache")
    return target


def diagnostics() -> dict:
    from app.agent.maxis.backend import MaxisRuntimeError
    from app.agent.maxis.context_guard import ContextGuardError
    from app.agent.maxis.contracts import EvidencePackV1
    from tools.maxis_context_diagnostics import describe_context
    from tools.maxis_role_eval_cases import BATTERY_VERSION, build_fixture_payload, load_cases

    cases = load_cases()
    rows = []
    for case in cases:
        row = {"caseId": case["id"], "roleId": case["role_id"],
               "benchmarkRefs": case["benchmark_refs"], "buildSucceeded": False}
        started = time.perf_counter()
        try:
            built = build_fixture_payload(case["id"])
            pack = EvidencePackV1.model_validate_json(json.dumps(built["payload"]["evidencePack"]))
            info = describe_context(case["role_id"], built["state"], pack)
            required = case["expected"].get("required_context_keys", [])
            if not set(required).issubset(info["allowedKeys"]):
                raise ValueError("fixture required keys must be declared outbound fields")
            unavailable = (set(info["perFieldSizeOmittedKeys"]) | set(info["packBudgetOmittedKeys"]))
            missing = sorted(set(required) - (set(info["finalKeys"]) - unavailable))
            row.update({"buildSucceeded": True, "context": info,
                        "requiredContextKeys": required, "missingRequiredContextKeys": missing,
                        "requiredContextComplete": not missing})
        except (ContextGuardError, MaxisRuntimeError) as exc:
            # Preserve actionable constant codes, never the exception path or text.
            code = getattr(exc, "code", None)
            row.update({"errorCategory": type(exc).__name__,
                        "errorCode": code if code in _GUARD_CODES else "guarded_input_rejected",
                        "requiredContextComplete": False})
        row["offlineProjectionMs"] = round((time.perf_counter() - started) * 1000, 3)
        rows.append(row)
    manifest = hashlib.sha256(json.dumps({"cases": cases, "sources": _source_hashes()}, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":")).encode()).hexdigest()
    return {"batteryVersion": BATTERY_VERSION, "batteryManifestSha256": manifest,
            "scope": "offline-role-input-components", "cases": rows,
            "attempts": len(rows), "buildSuccessCount": sum(row["buildSucceeded"] for row in rows),
            "requiredContextCompleteCount": sum(row["requiredContextComplete"] for row in rows),
            "semanticCorrectnessAssessed": False, "qualificationEligible": False,
            "runtimeLatencyAssessed": False,
            "limitations": ["no remote response or model quality measured", "not full workflow or UI",
                            "fixture-required fields are not a universal semantic completeness proof",
                            "offlineProjectionMs is not end-to-end latency"]}


def replay_editor(path: str) -> dict:
    source = _cache_path(path)
    cache = (ROOT / ".cache/agent-evaluation").resolve()
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("source exceeds diagnostic read limit")
    body = source.read_bytes()
    raw = json.loads(body)
    summary = summarize_attempts(editor_observations(raw))
    return {"scope": "existing-editor-raw-measurements", "sourceSha256": hashlib.sha256(body).hexdigest(),
            "sourceRelativePath": source.relative_to(cache).as_posix(),
            "measurements": summary, "semanticCorrectnessAssessed": False,
            "qualificationEligible": False, "limitations": [
                "historical source, not a newly executed candidate", "no regrading or cross-run comparison",
                "native LLM calls, prices and user correction time are not inferred"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-group", help="new artifact identity; never reuse an existing attempt")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("diagnose", help="run all repository-owned offline role input fixtures")
    replay = commands.add_parser("replay-editor", help="summarize an existing Editor raw run, without API calls")
    replay.add_argument("source")
    args = parser.parse_args(argv)
    group = args.run_group or ("maxis-offline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                              + "-" + uuid.uuid4().hex[:8])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", group):
        parser.error("invalid run-group identity")
    from tools.maxis_role_eval_cases import BATTERY_VERSION
    metadata = {"run": {"runGroupId": group, "repeatIndex": 1},
                "battery": {"batteryVersion": BATTERY_VERSION if args.command == "diagnose" else "1.0.0"}}
    reserved = False
    try:
        target = _cache_path(raw_result_path(args.command, metadata))
        reserve_raw_result_path(target)
        reserved = True
        result = diagnostics() if args.command == "diagnose" else replay_editor(args.source)
        protocol = load_protocol()
        result.update({"contract": CONTRACT, "runGroupId": group,
                       "protocolVersion": protocol["protocolVersion"],
                       "rubricVersion": protocol["rubricVersion"],
                       "runKind": "offline-diagnostic", "diagnosticToolSnapshot": git_snapshot(),
                       "harnessSourceSha256": _source_hashes()})
        write_raw_result(target, result)
    except (ValueError, OSError) as exc:
        # Record our failed diagnostic without ever touching someone else's claim.
        if reserved:
            try:
                write_raw_result(target, {"contract": CONTRACT, "runGroupId": group,
                    "runKind": "offline-diagnostic", "complete": False,
                    "qualificationEligible": False, "errorCategory": type(exc).__name__})
            except OSError:
                pass  # The retained claim still prevents overwrite on retry.
        print("offline diagnostic failed: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
