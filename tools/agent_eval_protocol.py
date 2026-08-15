"""Versioned measurement contract shared by the manual LTM Agent batteries.

This module is stdlib-only. Importing it never calls an LLM or mutates provider settings.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
import re
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "app" / "agent" / "evaluation_protocol.json"
DATA_INPUTS = (
    ROOT / "app" / "mock",
    ROOT / "config" / "jira.yml",
    ROOT / "config" / "module-aliases.yaml",
    ROOT / "config" / "people.yaml",
    ROOT / "config" / "wbs_config.yaml",
)
RAW_RESULT_ROOT = ROOT / ".cache" / "agent-evaluation"
TRACKED_REPORT_ROOT = ROOT / "research" / "agent-improvement" / "evaluations"


@lru_cache(maxsize=1)
def load_protocol() -> dict[str, Any]:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def _canonical(value: Any) -> Any:
    if callable(value):
        try:
            source = inspect.getsource(value)
        except (OSError, TypeError):
            source = getattr(value, "__qualname__", type(value).__name__)
        return {"callableSource": "\n".join(line.rstrip() for line in source.strip().splitlines())}
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__, "value": str(value)}


def _json_hash(value: Any) -> str:
    body = json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def battery_manifest_sha256(
    cases: Sequence[Any], specialized_review: Mapping[str, Any] | None = None,
) -> str:
    """Hash case inputs/checkers and their qualitative review contract."""
    payload: Any = cases
    if specialized_review is not None:
        payload = {"cases": cases, "specializedReview": specialized_review}
    return _json_hash(payload)


@lru_cache(maxsize=1)
def data_manifest_sha256() -> str:
    """Hash deterministic mock/config inputs; file contents never leave the machine."""
    digest = hashlib.sha256()
    files: list[Path] = []
    for item in DATA_INPUTS:
        if item.is_dir():
            files.extend(
                path for path in item.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
            )
        elif item.is_file():
            files.append(item)
    for path in sorted(files, key=lambda p: p.relative_to(ROOT).as_posix()):
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        body = path.read_bytes()
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args],
            cwd=ROOT, capture_output=True, check=False,
            text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def git_snapshot() -> dict[str, Any]:
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "commit": _git("rev-parse", "HEAD") or "unknown",
        "dirtyTrackedFiles": bool(status),
    }


def _case_ids(cases: Sequence[Any]) -> list[str]:
    ids: list[str] = []
    for index, case in enumerate(cases):
        if isinstance(case, (list, tuple)) and case:
            ids.append(str(case[0]))
        elif isinstance(case, Mapping) and case.get("id") is not None:
            ids.append(str(case["id"]))
        else:
            ids.append(str(index))
    return ids


def normalize_specialized_review_specs(
    cases: Sequence[Any],
    suite_elements: Sequence[Mapping[str, Any]],
    case_review_specs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate declarative suite/case review elements without executing a battery."""
    protocol = load_protocol()
    dimensions = {item["id"] for item in protocol["humanRubric"]["dimensions"]}
    full_ids = _case_ids(cases)
    missing = sorted(set(full_ids) - set(case_review_specs))
    extra = sorted(set(case_review_specs) - set(full_ids))
    if missing or extra:
        raise ValueError(f"specialized review case mismatch: missing={missing}, extra={extra}")

    def normalize_element(raw: Mapping[str, Any], *, owner: str) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError(f"{owner} specialized review element must be an object")
        element_id = str(raw.get("id") or "").strip()
        dimension = str(raw.get("dimension") or "").strip()
        question = str(raw.get("question") or "").strip()
        major_when = str(raw.get("majorWhen") or "").strip()
        evidence_sources = [str(value).strip() for value in (raw.get("evidenceSources") or [])]
        expected = raw.get("expected")
        if not element_id or not question or not major_when:
            raise ValueError(f"{owner} specialized review element needs id/question/majorWhen")
        if dimension not in dimensions:
            raise ValueError(f"{owner}.{element_id} has unknown dimension {dimension!r}")
        if not evidence_sources or not all(evidence_sources):
            raise ValueError(f"{owner}.{element_id} needs evidenceSources")
        if not isinstance(expected, Mapping) or not expected:
            raise ValueError(f"{owner}.{element_id} needs a concrete expected object")
        return {
            "id": element_id,
            "dimension": dimension,
            "question": question,
            "majorWhen": major_when,
            "evidenceSources": evidence_sources,
            "expected": _canonical(expected),
        }

    normalized_suite = [
        normalize_element(item, owner="suite") for item in suite_elements
    ]
    if not normalized_suite:
        raise ValueError("specialized review needs at least one suite element")
    suite_ids = [item["id"] for item in normalized_suite]
    if len(suite_ids) != len(set(suite_ids)):
        raise ValueError("suite specialized review element ids must be unique")

    normalized_cases: dict[str, Any] = {}
    for case_id in full_ids:
        raw = case_review_specs[case_id]
        goal = str(raw.get("goal") or "").strip()
        elements = [
            normalize_element(item, owner=case_id) for item in (raw.get("elements") or [])
        ]
        if not goal or not elements:
            raise ValueError(f"{case_id} needs a goal and at least one specialized element")
        ids = suite_ids + [item["id"] for item in elements]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{case_id} specialized review element ids must be unique")
        normalized_cases[case_id] = {"goal": goal, "elements": elements}
    return {"suiteElements": normalized_suite, "cases": normalized_cases}


def specialized_review_spec_for_case(
    registry: Mapping[str, Any], case_id: str,
) -> dict[str, Any]:
    """Flatten suite defaults plus one case contract for a reviewer observation."""
    case = (registry.get("cases") or {}).get(case_id)
    if not isinstance(case, Mapping):
        raise ValueError(f"specialized review spec missing for case {case_id}")
    return {
        "goal": case["goal"],
        "elements": list(registry.get("suiteElements") or []) + list(case.get("elements") or []),
    }


def build_run_metadata(
    *,
    suite: str,
    battery_version: str,
    cases: Sequence[Any],
    selected_case_ids: Sequence[str],
    model: str,
    simple_model: str,
    prompt_version: str,
    suite_review_elements: Sequence[Mapping[str, Any]] = (),
    case_review_specs: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create the immutable identity and comparability record for one harness invocation."""
    protocol = load_protocol()
    full_ids = _case_ids(cases)
    selected = [str(case_id) for case_id in selected_case_ids]
    review_registry = normalize_specialized_review_specs(
        cases, suite_review_elements, case_review_specs or {},
    )
    review_spec_hash = _json_hash(review_registry)
    manifest = battery_manifest_sha256(cases, review_registry)
    data_hash = data_manifest_sha256()
    git = git_snapshot()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    run_kind = str(os.getenv("LTM_EVAL_RUN_KIND") or "exploratory").strip().lower()
    planned_repetitions = int(os.getenv("LTM_EVAL_REPETITIONS") or "1")
    repeat_index = int(os.getenv("LTM_EVAL_REPEAT_INDEX") or "1")
    explicit_group = str(os.getenv("LTM_EVAL_RUN_GROUP_ID") or "").strip()
    run_group = explicit_group or f"exploratory-{now}"
    provider = str(os.getenv("LAKE_AGENT_PROVIDER") or "openai").strip().lower()
    candidate_order = os.getenv("LTM_EVAL_CANDIDATE_ORDER_INDEX") or "unrecorded"
    data_profile = str(os.getenv("LTM_EVAL_DATA_PROFILE") or "jira820-mock-v1")
    retry_policy = os.getenv("LTM_EVAL_RETRY_POLICY") or "no-silent-retry"
    cache_policy = os.getenv("LTM_EVAL_CACHE_POLICY") or "harness-default"
    process_isolation = os.getenv("LTM_EVAL_PROCESS_ISOLATION") or "unrecorded"
    runtime_profile = os.getenv("LTM_EVAL_RUNTIME_PROFILE") or "production-mixed-v1"
    pricing_snapshot = os.getenv("LTM_EVAL_PRICING_SNAPSHOT") or "unrecorded"

    comparable = {
        "protocolVersion": protocol["protocolVersion"],
        "rubricVersion": protocol["rubricVersion"],
        "suite": suite,
        "batteryVersion": battery_version,
        "batteryManifestSha256": manifest,
        "specializedReviewSpecSha256": review_spec_hash,
        "selectedCaseIds": selected,
        "model": model,
        "simpleModel": simple_model,
        "provider": provider,
        "dataProfile": data_profile,
        "dataManifestSha256": data_hash,
        "selectionPolicy": protocol["primaryEvidencePolicy"],
        "attemptPolicy": protocol["attemptPolicy"],
        "repetitions": planned_repetitions,
        "retryPolicy": retry_policy,
        "cachePolicy": cache_policy,
        "processIsolation": process_isolation,
        "runtimeProfile": runtime_profile,
    }
    comparability_key = _json_hash(comparable)

    reasons: list[str] = []
    qualification = protocol["qualification"]
    if run_kind != "qualification":
        reasons.append("runKind is exploratory")
    if planned_repetitions < int(qualification["minimumRepetitions"]):
        reasons.append(f"planned repetitions are below {qualification['minimumRepetitions']}")
    if set(selected) != set(full_ids) or len(selected) != len(full_ids):
        reasons.append("selected cases are not the full battery")
    if git["dirtyTrackedFiles"]:
        reasons.append("candidate has tracked working-tree changes")
    if git["commit"] == "unknown":
        reasons.append("candidate commit is unknown")
    if not explicit_group:
        reasons.append("LTM_EVAL_RUN_GROUP_ID is not explicit")
    if candidate_order == "unrecorded":
        reasons.append("LTM_EVAL_CANDIDATE_ORDER_INDEX is not recorded")
    if process_isolation == "unrecorded":
        reasons.append("evaluation process isolation is not recorded")
    if repeat_index < 1 or repeat_index > planned_repetitions:
        reasons.append("repeatIndex is outside planned repetitions")

    return {
        "protocolVersion": protocol["protocolVersion"],
        "rubricVersion": protocol["rubricVersion"],
        "battery": {
            "name": suite,
            "batteryVersion": battery_version,
            "batteryManifestSha256": manifest,
            "specializedReviewSpecSha256": review_spec_hash,
            "fullCaseCount": len(full_ids),
            "selectedCaseIds": selected,
            "selectedCaseCount": len(selected),
            "specializedReview": {
                "suiteElements": review_registry["suiteElements"],
                "cases": {
                    case_id: review_registry["cases"][case_id]
                    for case_id in selected
                },
            },
        },
        "run": {
            "runKind": run_kind,
            "runGroupId": run_group,
            "repeatIndex": repeat_index,
            "repetitions": planned_repetitions,
            "recordedAtUtc": now,
            "selectionPolicy": protocol["primaryEvidencePolicy"],
            "attemptPolicy": protocol["attemptPolicy"],
            "candidateCommit": git["commit"],
            "candidateDirty": git["dirtyTrackedFiles"],
            "promptVersion": prompt_version,
            "model": model,
            "simpleModel": simple_model,
            "provider": provider,
            "dataProfile": data_profile,
            "dataManifestSha256": data_hash,
            "candidateOrderIndex": candidate_order,
            "retryPolicy": retry_policy,
            "cachePolicy": cache_policy,
            "processIsolation": process_isolation,
            "runtimeProfile": runtime_profile,
            "pricingSnapshot": pricing_snapshot,
        },
        "comparabilityKey": comparability_key,
        "qualificationEligible": not reasons,
        "qualificationIneligibilityReasons": reasons,
    }


def _path_token(value: Any) -> str:
    """Convert metadata into a Windows-safe deterministic path segment."""
    token = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in str(value or ""))
    token = "-".join(part for part in token.split("-") if part)
    return token[:120] or "unknown"


def raw_result_path(
    suite: str, metadata: Mapping[str, Any], requested: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve a raw-output file strictly inside the git-ignored evaluation cache."""
    root = RAW_RESULT_ROOT.resolve()
    run = metadata.get("run") or {}
    battery = metadata.get("battery") or {}
    group = _path_token(run.get("runGroupId") or "unassigned")
    repeat_index = max(1, int(run.get("repeatIndex") or 1))
    version = _path_token(battery.get("batteryVersion") or "unknown")
    default = root / group / f"{_path_token(suite)}-b{version}-r{repeat_index:02d}.json"
    target = Path(requested).expanduser().resolve() if requested else default
    if not target.is_relative_to(root):
        raise ValueError("raw evaluation results must stay under .cache/agent-evaluation/")
    return target


def reserve_raw_result_path(path: str | os.PathLike[str]) -> Path:
    """Claim one raw path before paid work and refuse attempt-id reuse.

    Checkpoint writers intentionally replace their *own* target repeatedly.  A
    later process must not silently replace that attempt, though, because the
    protocol promises to retain every attempt.  The sidecar is acquired with
    O_EXCL so concurrent invocations cannot both believe they own the path.
    A stale sidecar after a crash is also evidence of an attempted run; callers
    must use a new runGroupId/repeatIndex instead of erasing it.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    claim = target.with_suffix(target.suffix + ".claim")
    if target.exists():
        raise FileExistsError(
            f"raw evaluation result already exists: {target}; "
            "use a new runGroupId or repeatIndex",
        )
    try:
        descriptor = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise FileExistsError(
            f"raw evaluation attempt is already reserved: {target}; "
            "use a new runGroupId or repeatIndex",
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"pid={os.getpid()}\n")
    if target.exists():
        claim.unlink(missing_ok=True)
        raise FileExistsError(
            f"raw evaluation result appeared while reserving: {target}; "
            "use a new runGroupId or repeatIndex",
        )
    return target


def write_raw_result(path: str | os.PathLike[str], payload: Mapping[str, Any]) -> Path:
    """Atomically persist a complete raw result without making it a tracked artifact."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8", newline="\n")
    os.replace(tmp, target)
    return target


def quantitative_metrics(
    *, attempts: int, duration_seconds: float, calls: int, prompt_tokens: int,
    completion_tokens: int, total_tokens: int, cached_tokens: int,
    cost_usd: float | None,
) -> dict[str, Any]:
    """Canonical quantitative block shared by every primary battery raw result."""
    return {
        "attempts": int(attempts),
        "durationSeconds": round(float(duration_seconds), 1),
        "calls": int(calls),
        "promptTokens": int(prompt_tokens),
        "completionTokens": int(completion_tokens),
        "totalTokens": int(total_tokens),
        "cachedTokens": int(cached_tokens),
        "costUsd": None if cost_usd is None else round(float(cost_usd), 6),
    }


def _score_ceiling(counts: Mapping[str, int]) -> float:
    ceilings = load_protocol()["humanRubric"]["checklistScoreCeilings"]
    if counts.get("major", 0) >= 2:
        return float(ceilings["multipleMajor"])
    if counts.get("major", 0) == 1:
        return float(ceilings["oneMajor"])
    if counts.get("minor", 0) >= 2:
        return float(ceilings["multipleMinorNoMajor"])
    if counts.get("minor", 0) == 1:
        return float(ceilings["oneMinorNoMajor"])
    return float(ceilings["allApplicablePass"])


def validate_checklist_results(
    checklist_results: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate complete evidence-backed checklist results and calculate score ceilings."""
    rubric = load_protocol()["humanRubric"]
    dimensions = rubric["dimensions"]
    expected_dimensions = {item["id"] for item in dimensions}
    missing_dimensions = sorted(expected_dimensions - set(checklist_results))
    extra_dimensions = sorted(set(checklist_results) - expected_dimensions)
    if missing_dimensions or extra_dimensions:
        raise ValueError(
            "checklist dimensions mismatch: "
            f"missing={missing_dimensions}, extra={extra_dimensions}"
        )

    allowed_statuses = set(rubric["checklistResultEnum"])
    normalized: dict[str, Any] = {}
    summaries: dict[str, Any] = {}
    for dimension in dimensions:
        dimension_id = dimension["id"]
        provided = checklist_results[dimension_id]
        if not isinstance(provided, Mapping):
            raise ValueError(f"{dimension_id} checklist must be an object")
        expected_items = {item["id"] for item in dimension["checklist"]}
        missing_items = sorted(expected_items - set(provided))
        extra_items = sorted(set(provided) - expected_items)
        if missing_items or extra_items:
            raise ValueError(
                f"{dimension_id} checklist mismatch: "
                f"missing={missing_items}, extra={extra_items}"
            )

        counts = {status: 0 for status in allowed_statuses}
        normalized_items: dict[str, Any] = {}
        for item in dimension["checklist"]:
            item_id = item["id"]
            result = provided[item_id]
            if not isinstance(result, Mapping):
                raise ValueError(f"{dimension_id}.{item_id} result must be an object")
            status = str(result.get("status") or "").strip().lower()
            evidence = str(result.get("evidence") or "").strip()
            if status not in allowed_statuses:
                raise ValueError(
                    f"{dimension_id}.{item_id} status must be one of {sorted(allowed_statuses)}"
                )
            if not evidence:
                raise ValueError(f"{dimension_id}.{item_id} evidence is required")
            counts[status] += 1
            normalized_items[item_id] = {"status": status, "evidence": evidence}

        applicable = len(expected_items) - counts["na"]
        if applicable < 1:
            raise ValueError(f"{dimension_id} needs at least one applicable checklist item")
        ceiling = _score_ceiling(counts)
        normalized[dimension_id] = normalized_items
        summaries[dimension_id] = {
            "counts": {key: counts[key] for key in sorted(counts)},
            "applicableItems": applicable,
            "scoreCeiling": ceiling,
        }
    return normalized, summaries


def validate_specialized_review_results(
    specialized_review_spec: Mapping[str, Any],
    specialized_review_results: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate every suite/case element and summarize findings by mapped dimension."""
    if not isinstance(specialized_review_spec, Mapping):
        raise ValueError("specializedReviewSpec must be an object")
    goal = str(specialized_review_spec.get("goal") or "").strip()
    elements = list(specialized_review_spec.get("elements") or [])
    if not goal or not elements:
        raise ValueError("specializedReviewSpec needs goal and elements")
    allowed = set(load_protocol()["humanRubric"]["checklistResultEnum"])
    dimensions = {item["id"] for item in load_protocol()["humanRubric"]["dimensions"]}
    element_ids = [str(item.get("id") or "") for item in elements]
    if not all(element_ids) or len(element_ids) != len(set(element_ids)):
        raise ValueError("specialized review element ids must be non-empty and unique")
    missing = sorted(set(element_ids) - set(specialized_review_results))
    extra = sorted(set(specialized_review_results) - set(element_ids))
    if missing or extra:
        raise ValueError(
            f"specialized review result mismatch: missing={missing}, extra={extra}"
        )

    normalized: dict[str, Any] = {}
    by_dimension: dict[str, Any] = {}
    for element in elements:
        element_id = str(element["id"])
        dimension = str(element.get("dimension") or "")
        question = str(element.get("question") or "").strip()
        major_when = str(element.get("majorWhen") or "").strip()
        evidence_sources = element.get("evidenceSources") or []
        expected_contract = element.get("expected")
        if dimension not in dimensions:
            raise ValueError(f"{element_id} has unknown dimension {dimension!r}")
        if not question or not major_when:
            raise ValueError(f"specialized review {element_id} needs question and majorWhen")
        if not isinstance(evidence_sources, list) or not evidence_sources:
            raise ValueError(f"specialized review {element_id} needs evidenceSources")
        if not isinstance(expected_contract, Mapping) or not expected_contract:
            raise ValueError(f"specialized review {element_id} needs a concrete expected object")
        result = specialized_review_results[element_id]
        if not isinstance(result, Mapping):
            raise ValueError(f"specialized review {element_id} result must be an object")
        status = str(result.get("status") or "").strip().lower()
        evidence = str(result.get("evidence") or "").strip()
        if status not in allowed:
            raise ValueError(f"specialized review {element_id} status must be one of {sorted(allowed)}")
        if not evidence:
            raise ValueError(f"specialized review {element_id} evidence is required")
        normalized[element_id] = {
            "dimension": dimension,
            "status": status,
            "evidence": evidence,
        }
        summary = by_dimension.setdefault(
            dimension,
            {"counts": {value: 0 for value in allowed}, "applicableItems": 0},
        )
        summary["counts"][status] += 1
        if status != "na":
            summary["applicableItems"] += 1

    for dimension, summary in by_dimension.items():
        if summary["applicableItems"] < 1:
            raise ValueError(f"{dimension} needs an applicable specialized review element")
        summary["counts"] = {key: summary["counts"][key] for key in sorted(allowed)}
        summary["scoreCeiling"] = _score_ceiling(summary["counts"])
    return normalized, by_dimension


def score_human_case(
    scores: Mapping[str, float], failure_codes: Iterable[str] = (), *,
    checklist_results: Mapping[str, Mapping[str, Mapping[str, Any]]],
    specialized_review_spec: Mapping[str, Any],
    specialized_review_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply rubric weights and deterministic severity caps to one human review."""
    protocol = load_protocol()
    rubric = protocol["humanRubric"]
    dimensions = rubric["dimensions"]
    expected = {item["id"] for item in dimensions}
    missing = sorted(expected - set(scores))
    extra = sorted(set(scores) - expected)
    if missing or extra:
        raise ValueError(f"human score dimensions mismatch: missing={missing}, extra={extra}")

    normalized_checklist, checklist_summary = validate_checklist_results(checklist_results)
    normalized_specialized, specialized_summary = validate_specialized_review_results(
        specialized_review_spec, specialized_review_results,
    )
    minimum = float(rubric["minimum"])
    maximum = float(rubric["maximum"])
    step = float(rubric["step"])
    normalized: dict[str, float] = {}
    combined_summary: dict[str, Any] = {}
    for item in dimensions:
        key = item["id"]
        value = float(scores[key])
        steps = round((value - minimum) / step)
        if value < minimum or value > maximum or abs(minimum + steps * step - value) > 1e-9:
            raise ValueError(f"{key} must be {minimum}..{maximum} in {step} increments")
        common_counts = checklist_summary[key]["counts"]
        specialized_counts = (specialized_summary.get(key) or {}).get("counts", {})
        combined_counts = {
            status: int(common_counts.get(status, 0)) + int(specialized_counts.get(status, 0))
            for status in rubric["checklistResultEnum"]
        }
        ceiling = _score_ceiling(combined_counts)
        combined_summary[key] = {
            "counts": combined_counts,
            "scoreCeiling": ceiling,
        }
        if value > ceiling:
            raise ValueError(
                f"{key} score {value} exceeds combined checklist ceiling {ceiling}"
            )
        normalized[key] = value

    raw = sum(normalized[item["id"]] * float(item["weight"]) for item in dimensions)
    caps = {item["code"]: float(item["maximumCaseScore"]) for item in rubric["caps"]}
    codes = sorted(set(str(code) for code in failure_codes))
    unknown = sorted(set(codes) - set(caps))
    if unknown:
        raise ValueError(f"unknown failure codes: {unknown}")
    cap = min((caps[code] for code in codes), default=maximum)
    digits = int(protocol["aggregation"]["rounding"])
    return {
        "rubricVersion": protocol["rubricVersion"],
        "dimensionScores": normalized,
        "checklistResults": normalized_checklist,
        "checklistSummary": checklist_summary,
        "specializedReviewSpecSha256": _json_hash(specialized_review_spec),
        "specializedReviewResults": normalized_specialized,
        "specializedReviewSummary": specialized_summary,
        "combinedReviewSummary": combined_summary,
        "rawScore": round(raw, digits),
        "failureCodes": codes,
        "appliedCap": cap if codes else None,
        "caseScore": round(min(raw, cap), digits),
    }


def validate_qualitative_evaluator(evaluator: Mapping[str, Any]) -> dict[str, Any]:
    """Require a Codex/Claude work agent and reject LTM/LLM-as-judge scoring."""
    policy = load_protocol()["qualitativeEvaluation"]
    family = str(evaluator.get("agentFamily") or "").strip().lower()
    allowed = {str(value).lower() for value in policy["allowedAgentFamilies"]}
    if family not in allowed:
        raise ValueError(f"qualitative evaluator must be one of {sorted(allowed)}")
    if evaluator.get("directRawOutputReview") is not True:
        raise ValueError("qualitative evaluator must directly review every raw output")
    if evaluator.get("ltmLlmUsedAsJudge") is not False:
        raise ValueError("LTM runtime LLM or LLM-as-judge cannot score qualitative quality")
    model = str(evaluator.get("agentModel") or "").strip()
    if not model:
        raise ValueError("qualitative evaluator agentModel is required")
    return {
        "policy": policy["policy"],
        "agentFamily": family,
        "agentModel": model,
        "directRawOutputReview": True,
        "ltmLlmUsedAsJudge": False,
    }


def aggregate_human_reviews(
    reviews: Sequence[Mapping[str, Any]], *, evaluator: Mapping[str, Any],
) -> dict[str, Any]:
    """Aggregate every case-attempt once; never choose a best closure output."""
    if not reviews:
        raise ValueError("at least one human review is required")
    protocol = load_protocol()
    evaluator_record = validate_qualitative_evaluator(evaluator)
    digits = int(protocol["aggregation"]["rounding"])
    seen: set[tuple[str, str, int]] = set()
    observations: list[dict[str, Any]] = []
    by_suite: dict[str, list[float]] = {}
    by_suite_dimensions: dict[str, dict[str, list[float]]] = {}
    all_dimensions: dict[str, list[float]] = {
        item["id"]: [] for item in protocol["humanRubric"]["dimensions"]
    }
    checklist_counts = {
        item["id"]: {status: 0 for status in protocol["humanRubric"]["checklistResultEnum"]}
        for item in protocol["humanRubric"]["dimensions"]
    }
    specialized_counts: dict[str, dict[str, dict[str, int]]] = {}
    severe = 0
    for review in reviews:
        suite = str(review.get("suite") or "").strip()
        case_id = str(review.get("caseId") or "").strip()
        repeat_index = int(review.get("repeatIndex") or 0)
        if not suite or not case_id or repeat_index < 1:
            raise ValueError("each review needs suite, caseId, and repeatIndex >= 1")
        identity = (suite, case_id, repeat_index)
        if identity in seen:
            raise ValueError(f"duplicate human review observation: {identity}")
        seen.add(identity)
        excerpt = str(review.get("outputExcerpt") or "").strip()
        if not excerpt:
            raise ValueError("each review needs a non-empty outputExcerpt")
        rationales = review.get("dimensionRationales") or {}
        dimension_ids = {item["id"] for item in protocol["humanRubric"]["dimensions"]}
        if set(rationales) != dimension_ids:
            raise ValueError("each review needs one rationale for every rubric dimension")
        normalized_rationales = {
            key: str(rationales[key] or "").strip() for key in sorted(rationales)
        }
        if not all(normalized_rationales.values()):
            raise ValueError("dimension rationales must be non-empty")
        score = score_human_case(
            review.get("dimensionScores") or {}, review.get("failureCodes") or (),
            checklist_results=review.get("checklistResults") or {},
            specialized_review_spec=review.get("specializedReviewSpec") or {},
            specialized_review_results=review.get("specializedReviewResults") or {},
        )
        observations.append({
            "suite": suite, "caseId": case_id, "repeatIndex": repeat_index,
            "outputExcerpt": excerpt, "dimensionRationales": normalized_rationales, **score,
        })
        by_suite.setdefault(suite, []).append(score["caseScore"])
        suite_dimensions = by_suite_dimensions.setdefault(
            suite, {dimension_id: [] for dimension_id in all_dimensions},
        )
        for dimension_id, dimension_score in score["dimensionScores"].items():
            all_dimensions[dimension_id].append(dimension_score)
            suite_dimensions[dimension_id].append(dimension_score)
            for status, count in score["checklistSummary"][dimension_id]["counts"].items():
                checklist_counts[dimension_id][status] += count
        suite_specialized = specialized_counts.setdefault(suite, {})
        for element_id, result in score["specializedReviewResults"].items():
            counts = suite_specialized.setdefault(
                element_id,
                {status: 0 for status in protocol["humanRubric"]["checklistResultEnum"]},
            )
            counts[result["status"]] += 1
        severe += 1 if score["failureCodes"] else 0
    suite_scores = {
        suite: round(sum(values) / len(values), digits)
        for suite, values in sorted(by_suite.items())
    }
    all_scores = [item["caseScore"] for item in observations]
    suite_dimension_scores = {
        suite: {
            dimension_id: round(sum(values) / len(values), digits)
            for dimension_id, values in dimensions.items()
        }
        for suite, dimensions in sorted(by_suite_dimensions.items())
    }
    overall_dimension_scores = {
        dimension_id: round(sum(values) / len(values), digits)
        for dimension_id, values in all_dimensions.items()
    }
    return {
        "protocolVersion": protocol["protocolVersion"],
        "rubricVersion": protocol["rubricVersion"],
        "qualitativeEvaluator": evaluator_record,
        "observationCount": len(observations),
        "suiteScores": suite_scores,
        "overallScore": round(sum(all_scores) / len(all_scores), digits),
        "suiteDimensionScores": suite_dimension_scores,
        "overallDimensionScores": overall_dimension_scores,
        "checklistResultCounts": checklist_counts,
        "specializedReviewResultCounts": specialized_counts,
        "cappedFailureCount": severe,
        "cappedFailureRate": round(severe / len(observations), digits),
        "observations": observations,
    }


def nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    """Nearest-rank percentile used by every latency report (no interpolation drift)."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if percentile <= 0 or percentile > 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil((percentile / 100) * len(ordered)))
    return ordered[rank - 1]


def summarize_latency(values: Sequence[float]) -> dict[str, float | int]:
    protocol = load_protocol()
    digits = int(protocol["aggregation"]["rounding"])
    return {
        "attempts": len(values),
        "p50": round(nearest_rank_percentile(values, 50), digits),
        "p95": round(nearest_rank_percentile(values, 95), digits),
    }


def render_report_standard_block(metadata: Sequence[Mapping[str, Any]]) -> str:
    """Render the mandatory measurement-criteria block for a report or PR description."""
    if not metadata:
        raise ValueError("at least one suite metadata record is required")
    protocol = load_protocol()
    first = metadata[0]
    run = first["run"]
    rows = [
        (item["battery"]["name"], item["battery"]["batteryVersion"],
         item["battery"]["batteryManifestSha256"],
         item["battery"]["specializedReviewSpecSha256"], item["comparabilityKey"])
        for item in metadata
    ]
    lines = [
        "## 측정 식별자",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| protocolVersion | `{first['protocolVersion']}` |",
        f"| rubricVersion | `{first['rubricVersion']}` |",
        f"| runKind | `{run['runKind']}` |",
        f"| runGroupId | `{run['runGroupId']}` |",
        f"| repetitions | `{run['repetitions']}` |",
        f"| candidateCommit | `{run['candidateCommit']}` |",
        f"| promptVersion | `{run['promptVersion']}` |",
        f"| model | `{run['model']}` |",
        f"| simpleModel | `{run['simpleModel']}` |",
        f"| dataManifestSha256 | `{run['dataManifestSha256']}` |",
        f"| selectionPolicy | `{run['selectionPolicy']}` |",
        f"| aggregation | `{protocol['aggregation']['overallScore']}` |",
        f"| percentileMethod | `{protocol['aggregation']['percentileMethod']}` |",
        f"| candidateOrderIndex | `{run['candidateOrderIndex']}` |",
        f"| retryPolicy | `{run['retryPolicy']}` |",
        f"| cachePolicy | `{run['cachePolicy']}` |",
        f"| processIsolation | `{run['processIsolation']}` |",
        f"| qualitativeEvaluatorPolicy | `{protocol['qualitativeEvaluation']['policy']}` |",
        "| evaluatorAgentFamily | `codex 또는 claude — 보고서 작성 시 입력` |",
        "| evaluatorAgentModel | `보고서 작성 시 입력` |",
        "| directRawOutputReview | `true` |",
        "| ltmLlmUsedAsJudge | `false` |",
        "| reviewerCount | `보고서 작성 시 입력` |",
        "| blindedReview | `보고서 작성 시 입력` |",
        "",
        "### Battery identity",
        "",
        "| Suite | batteryVersion | batteryManifestSha256 | specializedReviewSpecSha256 | comparabilityKey |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {suite} | `{version}` | `{manifest}` | `{specialized}` | `{key}` |"
        for suite, version, manifest, specialized, key in rows
    )
    lines.extend([
        "",
        "## 비교 가능성 및 evidence 선택",
        "",
        f"Primary evidence는 `{protocol['primaryEvidencePolicy']}`. focused/closure 결과로 full-run "
        "점수를 교체하지 않음. `comparabilityKey`가 다른 후보의 절대점수 증감은 계산하지 않음.",
        "",
        "## 실행 격리",
        "",
        f"Suite process는 `{protocol['isolation']['suiteProcessPolicy']}`, cache는 "
        f"`{protocol['isolation']['cachePolicy']}`. 각 case의 world/provider Store SHA-256가 바뀌면 "
        "읽기 전용 배터리 실패. provider-side prompt cache는 `cachedTokens`로 별도 보고.",
        "",
        "## 사람 품질 평가 기준",
        "",
        "정성평가자는 raw output을 직접 읽은 Codex/Claude 작업 에이전트. LTM runtime LLM과 "
        "LLM-as-judge는 정성점수 산출에 사용하지 않음.",
        "",
        f"Human rubric `{protocol['rubricVersion']}`. 각 축은 {protocol['humanRubric']['step']}점 간격, "
        "case 점수는 가중평균 후 치명 결함 cap 적용.",
        "",
        "| 축 | 가중치 |",
        "|---|---:|",
    ])
    lines.extend(
        f"| {item['label']} | {float(item['weight']) * 100:.0f}% |"
        for item in protocol["humanRubric"]["dimensions"]
    )
    lines.extend([
        "",
        "| 점수 | 공통 anchor |",
        "|---:|---|",
    ])
    lines.extend(
        f"| {score} | {description} |"
        for score, description in sorted(
            protocol["humanRubric"]["scoreAnchors"].items(), reverse=True,
        )
    )
    checklist_enum = protocol["humanRubric"]["checklistResultEnum"]
    ceiling = protocol["humanRubric"]["checklistScoreCeilings"]
    lines.extend([
        "",
        "### Checklist 판정과 점수 상한",
        "",
        "모든 checklist item에 `pass`, `minor`, `major`, `na`와 실제 출력 근거를 기록. "
        "`na`도 적용할 수 없는 이유를 근거란에 작성.",
        "",
        "| 판정 | 정의 |",
        "|---|---|",
    ])
    lines.extend(f"| `{status}` | {description} |" for status, description in checklist_enum.items())
    lines.extend([
        "",
        "| Checklist 결과 | 해당 축 최고점 |",
        "|---|---:|",
        f"| 적용 항목 전부 pass | {ceiling['allApplicablePass']:.1f} |",
        f"| minor 1건, major 0건 | {ceiling['oneMinorNoMajor']:.1f} |",
        f"| minor 2건 이상, major 0건 | {ceiling['multipleMinorNoMajor']:.1f} |",
        f"| major 1건 | {ceiling['oneMajor']:.1f} |",
        f"| major 2건 이상 | {ceiling['multipleMajor']:.1f} |",
        "",
        "### 축별 checklist와 점수 anchor",
    ])
    for dimension in protocol["humanRubric"]["dimensions"]:
        lines.extend([
            "",
            f"#### {dimension['label']} (`{dimension['id']}`)",
            "",
            dimension["question"],
            "",
            "| ID | 판단 질문 | majorWhen |",
            "|---|---|---|",
        ])
        lines.extend(
            f"| `{item['id']}` | {item['question']} | {item['majorWhen']} |"
            for item in dimension["checklist"]
        )
        lines.extend([
            "",
            "| 점수 | 축별 anchor |",
            "|---:|---|",
        ])
        lines.extend(
            f"| {score} | {description} |"
            for score, description in sorted(dimension["anchors"].items(), reverse=True)
        )
    lines.extend([
        "",
        "## 배터리·case 특수 검토요소",
        "",
        "공통 5축 외에 각 suite와 case가 선언한 entity·검색 경로·필수 출처·보존값을 모두 "
        "`pass/minor/major/na`와 실제 실행 근거로 판정. 결과는 매핑된 기존 축의 점수 상한에 합산.",
    ])
    for item in metadata:
        battery = item["battery"]
        review = battery.get("specializedReview") or {}
        lines.extend(["", f"### {battery['name']}", ""])

        def append_review_element(prefix: str, element: Mapping[str, Any]) -> None:
            expected = json.dumps(
                element.get("expected") or {}, ensure_ascii=False,
                sort_keys=True, separators=(",", ":"),
            )
            sources = ", ".join(f"`{source}`" for source in element["evidenceSources"])
            detail_indent = "    " if prefix.startswith("  ") else "  "
            lines.extend([
                f"{prefix} `{element['id']}` → `{element['dimension']}`: {element['question']}",
                f"{detail_indent}- expected: `{expected}`",
                f"{detail_indent}- evidenceSources: {sources}",
                f"{detail_indent}- majorWhen: {element['majorWhen']}",
            ])

        for element in review.get("suiteElements") or []:
            append_review_element("- suite", element)
        for case_id, case in (review.get("cases") or {}).items():
            lines.append(f"- case `{case_id}` — {case['goal']}")
            for element in case.get("elements") or []:
                append_review_element("  -", element)
    lines.extend([
        "",
        "나머지 필수 section: `실행 조건`, `배터리 범위`, `정량 결과`, "
        "`배터리별 실제 출력과 평가`, `자동 checker와 사람 판정 불일치`, "
        "`실패·재시도·제한사항`.",
    ])
    return "\n".join(lines)


def validate_report(text: str) -> list[str]:
    protocol = load_protocol()
    declared = ""
    version_match = re.search(
        r"protocolVersion[^\n]*?`?(\d+\.\d+\.\d+)`?", text,
    )
    if version_match:
        declared = version_match.group(1)
    historical = (protocol.get("historicalReportContracts") or {}).get(declared)
    required_sections = (
        historical.get("requiredSections") if historical else protocol["requiredReportSections"]
    )
    required_fields = (
        historical.get("requiredFields") if historical else protocol["requiredReportFields"]
    )
    missing = [
        f"missing report section: {section}"
        for section in required_sections
        if section not in text
    ]
    missing.extend(
        f"missing report field: {field}"
        for field in required_fields
        if field not in text
    )
    return missing


def _main() -> int:
    parser = argparse.ArgumentParser(description="LTM Agent evaluation protocol utilities")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("template", help="print the required report headings and criteria")
    validate = commands.add_parser("validate-report", help="validate a Markdown report or PR body file")
    validate.add_argument("path")
    args = parser.parse_args()
    if args.command == "template":
        protocol = load_protocol()
        print(f"# {protocol['protocolName']} report template")
        for section in protocol["requiredReportSections"]:
            print(f"\n## {section}\n")
        print("Required fields: " + ", ".join(protocol["requiredReportFields"]))
        return 0
    problems = validate_report(Path(args.path).read_text(encoding="utf-8"))
    if problems:
        print("\n".join(problems))
        return 1
    print("report conforms to " + load_protocol()["protocolVersion"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
