"""Bounded, metadata-only quality evaluation for MAXIS shadow traffic.

Shadow execution must never affect the authoritative local result.  This module keeps
that boundary explicit: local and remote values exist only inside one bounded worker,
while the durable store contains counters and numeric scores grouped by immutable
deployment metadata.  Prompts, generated content, Jira keys, user/thread/correlation
identifiers, and per-request rows are never accepted by the persistence API.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


MAX_WORKERS = 2
MAX_PENDING = 8
MAX_BUCKETS = 500
MIN_PROMOTION_SAMPLES = 5
MIN_SUCCESS_RATE = 0.95
MIN_CONTRACT_RATE = 1.0
MIN_CITATION_RATE = 1.0
MIN_STRUCTURE_RATE = 0.80
MIN_NUMERIC_PARITY_RATE = 0.80

_LOCK = threading.RLock()
_QUEUE_SLOTS = threading.BoundedSemaphore(MAX_PENDING)
_EXECUTOR: ThreadPoolExecutor | None = None
_FUTURES: set[Future] = set()
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_NUMBER = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?%?")
_PROFILES = frozenset({"dev", "internal-pilot", "prod"})
_WORKFLOWS = frozenset({"agent_core", "editor_author", "pmo_brief"})
_ROLES = frozenset({
    "all_roles", "request_architect", "query_specialist", "knowledge_curator",
    "portfolio_analyst", "work_architect", "auditor", "result_integrator",
    "editor_author",
})


@dataclass(frozen=True)
class ShadowDimensions:
    """Non-sensitive deployment dimensions allowed in the aggregate store."""

    profile: str
    profile_fingerprint: str
    workflow_key: str
    role_id: str
    workflow_version: str
    knowledge_version: str

    def clean(self) -> "ShadowDimensions":
        values = {
            name: _label(getattr(self, name), name)
            for name in (
                "profile", "profile_fingerprint", "workflow_key", "role_id",
                "workflow_version", "knowledge_version",
            )
        }
        if values["profile"] not in _PROFILES:
            raise ValueError("Invalid shadow evaluation profile.")
        if not re.fullmatch(r"[0-9a-f]{32}", values["profile_fingerprint"]):
            raise ValueError("Invalid shadow evaluation profile_fingerprint.")
        if values["workflow_key"] not in _WORKFLOWS:
            raise ValueError("Invalid shadow evaluation workflow_key.")
        if values["role_id"] not in _ROLES:
            raise ValueError("Invalid shadow evaluation role_id.")
        return ShadowDimensions(**values)


@dataclass(frozen=True)
class _OutputSignature:
    """Content-free transient signature used by the comparison worker."""

    leaf_types: frozenset[str]
    numbers: frozenset[str]
    tokens: frozenset[str]
    non_empty: bool


def _path() -> Path:
    from app.infra.settings import CACHE_DIR

    return Path(CACHE_DIR) / "maxis_shadow_quality.json"


def _label(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_LABEL.fullmatch(text):
        raise ValueError(f"Invalid shadow evaluation {name}.")
    return text


def _bucket_id(dimensions: ShadowDimensions) -> str:
    clean = dimensions.clean()
    raw = "|".join((
        clean.profile, clean.profile_fingerprint, clean.workflow_key, clean.role_id,
        clean.workflow_version, clean.knowledge_version,
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _empty_store() -> dict[str, Any]:
    return {"schemaVersion": 1, "buckets": {}}


def _load() -> dict[str, Any]:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _empty_store()
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        return _empty_store()
    buckets = value.get("buckets")
    if not isinstance(buckets, dict):
        return _empty_store()
    return {"schemaVersion": 1, "buckets": {
        str(key): row for key, row in buckets.items() if isinstance(row, dict)
    }}


def _save(value: dict[str, Any]) -> None:
    buckets = value.get("buckets") if isinstance(value, dict) else None
    if isinstance(buckets, dict) and len(buckets) > MAX_BUCKETS:
        newest = sorted(
            buckets.items(),
            key=lambda item: str((item[1] or {}).get("updatedAt") or ""),
            reverse=True,
        )[:MAX_BUCKETS]
        value = {"schemaVersion": 1, "buckets": dict(newest)}
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(target)


def _plain(value: Any) -> Any:
    """Project a transient model object to JSON values without retaining it."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _plain(dump(mode="json"))
    return str(value)


def _leaf_types(value: Any, path: str = "$") -> set[tuple[str, str]]:
    if isinstance(value, dict):
        result: set[tuple[str, str]] = set()
        for key, nested in value.items():
            result.update(_leaf_types(nested, f"{path}.{key}"))
        return result or {(path, "object")}
    if isinstance(value, list):
        result = set()
        for nested in value:
            # List indexes are intentionally collapsed so item count cannot become a
            # durable identifier-like fingerprint of one business request.
            result.update(_leaf_types(nested, f"{path}[]"))
        return result or {(path, "array")}
    kind = "null" if value is None else type(value).__name__
    return {(path, kind)}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for nested in value.values() for text in _strings(nested)]
    if isinstance(value, list):
        return [text for nested in value for text in _strings(nested)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    return []


def _ratio(left: set[Any], right: set[Any]) -> float:
    union = left | right
    return 1.0 if not union else len(left & right) / len(union)


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _bounded(values: set[str], maximum: int) -> frozenset[str]:
    return frozenset(sorted(values)[:maximum])


def _signature(value: Any) -> _OutputSignature:
    plain = _plain(value)
    leaves = {
        _hash_token(path + "\0" + kind)
        for path, kind in _leaf_types(plain)
    }
    numbers: set[str] = set()
    tokens: set[str] = set()
    non_empty = False
    for text in _strings(plain):
        non_empty = non_empty or bool(text.strip())
        numbers.update(_hash_token(item) for item in _NUMBER.findall(text))
        tokens.update(
            _hash_token(item.casefold()) for item in _TOKEN.findall(text)
        )
    return _OutputSignature(
        leaf_types=_bounded(leaves, 2_000),
        numbers=_bounded(numbers, 500),
        tokens=_bounded(tokens, 5_000),
        non_empty=non_empty,
    )


def _compare_signatures(
    local: _OutputSignature, remote: _OutputSignature,
) -> dict[str, Any]:
    return {
        "structureScore": round(
            _ratio(set(local.leaf_types), set(remote.leaf_types)), 6,
        ),
        "numericComparable": bool(local.numbers or remote.numbers),
        "numericParity": local.numbers == remote.numbers,
        "lexicalComparable": bool(local.tokens or remote.tokens),
        "lexicalScore": round(_ratio(set(local.tokens), set(remote.tokens)), 6),
        "nonEmptyParity": local.non_empty == remote.non_empty,
    }


def compare_outputs(local_output: Any, remote_output: Any) -> dict[str, Any]:
    """Return numeric comparison scores; no content is returned or persisted."""
    return _compare_signatures(_signature(local_output), _signature(remote_output))


def _new_bucket(dimensions: ShadowDimensions) -> dict[str, Any]:
    clean = dimensions.clean()
    return {
        "profile": clean.profile,
        "profileFingerprint": clean.profile_fingerprint,
        "workflowKey": clean.workflow_key,
        "roleId": clean.role_id,
        "workflowVersion": clean.workflow_version,
        "knowledgeVersion": clean.knowledge_version,
        "submitted": 0,
        "completed": 0,
        "succeeded": 0,
        "failed": 0,
        "queueDropped": 0,
        "contractValid": 0,
        "citationValid": 0,
        "structureScoreSum": 0.0,
        "numericComparable": 0,
        "numericParity": 0,
        "lexicalComparable": 0,
        "lexicalScoreSum": 0.0,
        "nonEmptyParity": 0,
        "latencyMsSum": 0,
        "updatedAt": "",
    }


def _update(dimensions: ShadowDimensions, update: Callable[[dict[str, Any]], None]) -> None:
    clean = dimensions.clean()
    key = _bucket_id(clean)
    with _LOCK:
        store = _load()
        buckets = store["buckets"]
        row = buckets.get(key)
        if not isinstance(row, dict):
            row = _new_bucket(clean)
            buckets[key] = row
        update(row)
        row["updatedAt"] = datetime.now(timezone.utc).isoformat()
        _save(store)


def _increment(row: dict[str, Any], key: str, amount: int = 1) -> None:
    row[key] = max(0, int(row.get(key) or 0) + int(amount))


def record_submitted(dimensions: ShadowDimensions) -> None:
    _update(dimensions, lambda row: _increment(row, "submitted"))


def record_queue_drop(dimensions: ShadowDimensions) -> None:
    def update(row: dict[str, Any]) -> None:
        _increment(row, "submitted")
        _increment(row, "queueDropped")

    _update(dimensions, update)


def record_failure(dimensions: ShadowDimensions, *, latency_ms: int = 0) -> None:
    def update(row: dict[str, Any]) -> None:
        _increment(row, "completed")
        _increment(row, "failed")
        row["latencyMsSum"] = max(
            0, int(row.get("latencyMsSum") or 0) + max(0, int(latency_ms or 0))
        )

    _update(dimensions, update)


def record_success(
    dimensions: ShadowDimensions,
    *,
    comparison: Mapping[str, Any],
    contract_valid: bool,
    citation_valid: bool,
    latency_ms: int = 0,
) -> None:
    """Fold one completed comparison into a deployment bucket."""

    def finite_score(name: str) -> float:
        value = float(comparison.get(name) or 0.0)
        return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0

    def update(row: dict[str, Any]) -> None:
        _increment(row, "completed")
        _increment(row, "succeeded")
        _increment(row, "contractValid", int(bool(contract_valid)))
        _increment(row, "citationValid", int(bool(citation_valid)))
        row["structureScoreSum"] = round(
            float(row.get("structureScoreSum") or 0.0)
            + finite_score("structureScore"), 6,
        )
        numeric_comparable = bool(comparison.get("numericComparable"))
        _increment(row, "numericComparable", int(numeric_comparable))
        if numeric_comparable:
            _increment(row, "numericParity", int(bool(comparison.get("numericParity"))))
        lexical_comparable = bool(comparison.get("lexicalComparable"))
        _increment(row, "lexicalComparable", int(lexical_comparable))
        if lexical_comparable:
            row["lexicalScoreSum"] = round(
                float(row.get("lexicalScoreSum") or 0.0)
                + finite_score("lexicalScore"), 6,
            )
        _increment(row, "nonEmptyParity", int(bool(comparison.get("nonEmptyParity"))))
        row["latencyMsSum"] = max(
            0, int(row.get("latencyMsSum") or 0) + max(0, int(latency_ms or 0))
        )

    _update(dimensions, update)


def _citation_valid(remote_output: Any) -> bool:
    patch = getattr(remote_output, "state_patch", None)
    if not isinstance(patch, Mapping):
        return False
    quality = patch.get("quality_gate")
    return bool(isinstance(quality, Mapping) and quality.get("citationValid") is True)


def _latency_ms(remote_output: Any) -> int:
    metadata = getattr(remote_output, "metadata", None)
    if not isinstance(metadata, Mapping):
        return 0
    return max(0, int(metadata.get("latencyMs") or 0))


def _evaluate(
    dimensions: ShadowDimensions,
    local_signature: _OutputSignature,
    invoke_remote: Callable[[], Any],
) -> None:
    record_submitted(dimensions)
    try:
        remote_output = invoke_remote()
        comparison = _compare_signatures(local_signature, _signature(remote_output))
        record_success(
            dimensions,
            comparison=comparison,
            contract_valid=True,
            citation_valid=_citation_valid(remote_output),
            latency_ms=_latency_ms(remote_output),
        )
    except Exception:
        # Exception messages may contain model/provider content.  Only the aggregate
        # failure counter crosses the persistence boundary.
        record_failure(dimensions)


def _release(future: Future) -> None:
    with _LOCK:
        _FUTURES.discard(future)
    _QUEUE_SLOTS.release()


def submit(
    *,
    dimensions: ShadowDimensions,
    local_output: Any,
    invoke_remote: Callable[[], Any],
) -> bool:
    """Submit without blocking local Agent completion; reject when the queue is full."""
    global _EXECUTOR

    clean = dimensions.clean()
    # Derive a content-free signature on the authoritative call stack.  The asynchronous
    # queue never retains the local generated text, even transiently.
    local_signature = _signature(local_output)
    if not _QUEUE_SLOTS.acquire(blocking=False):
        record_queue_drop(clean)
        return False
    with _LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(
                max_workers=MAX_WORKERS, thread_name_prefix="maxis-shadow",
            )
        try:
            future = _EXECUTOR.submit(
                _evaluate, clean, local_signature, invoke_remote,
            )
        except Exception:
            _QUEUE_SLOTS.release()
            raise
        _FUTURES.add(future)
        future.add_done_callback(_release)
    return True


def _pct(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) * 100.0 / float(denominator), 1)


def _matching_rows(dimensions: ShadowDimensions, *, all_roles: bool) -> list[dict[str, Any]]:
    clean = dimensions.clean()
    with _LOCK:
        rows = list(_load()["buckets"].values())
    return [row for row in rows if (
        row.get("profile") == clean.profile
        and row.get("profileFingerprint") == clean.profile_fingerprint
        and row.get("workflowKey") == clean.workflow_key
        and (all_roles or row.get("roleId") == clean.role_id)
        and row.get("workflowVersion") == clean.workflow_version
        and row.get("knowledgeVersion") == clean.knowledge_version
    )]


def summary(dimensions: ShadowDimensions, *, all_roles: bool = False) -> dict[str, Any]:
    """Aggregate exact deployment-version buckets without returning raw events."""
    rows = _matching_rows(dimensions, all_roles=all_roles)
    keys = (
        "submitted", "completed", "succeeded", "failed", "queueDropped",
        "contractValid", "citationValid", "numericComparable", "numericParity",
        "lexicalComparable", "nonEmptyParity", "latencyMsSum",
    )
    totals = {key: sum(max(0, int(row.get(key) or 0)) for row in rows) for key in keys}
    structure_sum = sum(float(row.get("structureScoreSum") or 0.0) for row in rows)
    lexical_sum = sum(float(row.get("lexicalScoreSum") or 0.0) for row in rows)
    completed = totals["completed"]
    succeeded = totals["succeeded"]
    return {
        "contract": "maxis-shadow-quality.v1",
        "privacy": "aggregate_metadata_only",
        **{key: totals[key] for key in (
            "submitted", "completed", "succeeded", "failed", "queueDropped",
            "numericComparable",
        )},
        "successRatePct": _pct(succeeded, completed),
        "contractValidRatePct": _pct(totals["contractValid"], succeeded),
        "citationValidRatePct": _pct(totals["citationValid"], succeeded),
        "structureScorePct": _pct(structure_sum, succeeded),
        "numericParityRatePct": _pct(
            totals["numericParity"], totals["numericComparable"],
        ),
        "lexicalAgreementPct": _pct(lexical_sum, totals["lexicalComparable"]),
        "nonEmptyParityRatePct": _pct(totals["nonEmptyParity"], succeeded),
        "averageLatencyMs": (
            round(totals["latencyMsSum"] / completed, 1) if completed else None
        ),
    }


def promotion_status(
    dimensions: ShadowDimensions, *, all_roles: bool = False,
) -> dict[str, Any]:
    """Evaluate the immutable deployment bucket against the production gate."""
    metrics = summary(dimensions, all_roles=all_roles)
    reasons: list[str] = []
    if metrics["completed"] < MIN_PROMOTION_SAMPLES:
        reasons.append(
            f"shadow samples {metrics['completed']}/{MIN_PROMOTION_SAMPLES}"
        )
    checks = (
        ("successRatePct", MIN_SUCCESS_RATE, "success rate"),
        ("contractValidRatePct", MIN_CONTRACT_RATE, "contract validity"),
        ("citationValidRatePct", MIN_CITATION_RATE, "citation validity"),
        ("structureScorePct", MIN_STRUCTURE_RATE, "structure parity"),
    )
    for key, threshold, label in checks:
        value = metrics.get(key)
        if value is None or float(value) < threshold * 100:
            reasons.append(f"{label} below {threshold * 100:.0f}%")
    numeric_rate = metrics.get("numericParityRatePct")
    if metrics["numericComparable"] and (
            numeric_rate is None or float(numeric_rate) < MIN_NUMERIC_PARITY_RATE * 100):
        reasons.append(
            f"numeric parity below {MIN_NUMERIC_PARITY_RATE * 100:.0f}%"
        )
    return {
        **metrics,
        "ready": not reasons,
        "minimumSamples": MIN_PROMOTION_SAMPLES,
        "reasons": reasons,
    }


def assert_promotion_ready(dimensions: ShadowDimensions) -> None:
    status = promotion_status(dimensions)
    if not status["ready"]:
        raise ValueError(
            "Prod MAXIS promotion requires a passing shadow quality gate: "
            + "; ".join(status["reasons"])
        )


def drain_for_tests(timeout: float = 5.0) -> None:
    """Wait for already-submitted work; tests never need to expose queue internals."""
    with _LOCK:
        pending = tuple(_FUTURES)
    if pending:
        wait(pending, timeout=timeout)


def reset_for_tests() -> None:
    drain_for_tests()
    with _LOCK:
        try:
            _path().unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "MIN_PROMOTION_SAMPLES", "ShadowDimensions", "assert_promotion_ready",
    "compare_outputs", "drain_for_tests", "promotion_status", "record_failure",
    "record_queue_drop", "record_submitted", "record_success", "reset_for_tests",
    "submit", "summary",
]
