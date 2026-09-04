"""Privacy-preserving AI draft impact measurements.

The competition feedback asks whether Agent work is measurably better than a
dashboard.  This store records only review timing, edit volume and outcome
counts.  It never stores prompts, generated text, Jira keys, user IDs, API keys,
or approval capabilities.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping

_LOCK = threading.RLock()
_MAX_ROWS = 2_000
_MAJOR_REWRITE_CHARS = 200
_MAJOR_REWRITE_FIELDS = 2


def _path() -> Path:
    from app.infra.settings import CACHE_DIR

    return Path(CACHE_DIR) / "agent_impact.json"


def _digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _load() -> list[dict[str, Any]]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [row for row in raw if isinstance(row, dict)][-_MAX_ROWS:]
    except (OSError, ValueError, TypeError):
        pass
    return []


def _save(rows: list[dict[str, Any]]) -> None:
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(rows[-_MAX_ROWS:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(target)


def _safe_label(value: object, *, maximum: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) > maximum or any(ch in text for ch in "\r\n\t"):
        return ""
    return text


def _edit_volume(overrides: Mapping[str, object] | None) -> tuple[int, int]:
    """Return edited leaf count and character volume without retaining content."""
    fields = 0
    characters = 0

    def visit(value: object) -> None:
        nonlocal fields, characters
        if isinstance(value, Mapping):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
        elif value is not None:
            fields += 1
            characters += len(str(value))

    visit(overrides or {})
    return fields, characters


def note_draft(
    *,
    thread_id: str,
    approval_token: str,
    item_count: int,
    engine: str = "",
    profile: str = "",
    feature: str = "task_draft",
    role: str = "work_architect",
    workflow_bundle_version: str = "",
    knowledge_version: str = "",
    knowledge_manifest_sha256: str = "",
    now: float | None = None,
) -> None:
    """Idempotently mark the moment an approval-ready draft became visible."""
    if not approval_token:
        return
    timestamp = float(time.time() if now is None else now)
    token_digest = _digest(approval_token)
    row = {
        "schema": 1,
        "tokenDigest": token_digest,
        "threadDigest": _digest(thread_id),
        "draftedAt": timestamp,
        "itemCount": max(0, min(int(item_count or 0), 200)),
        "engine": _safe_label(engine, maximum=40),
        "profile": _safe_label(profile, maximum=40),
        "feature": _safe_label(feature, maximum=40),
        "role": _safe_label(role, maximum=40),
        "workflowBundleVersion": _safe_label(workflow_bundle_version),
        "knowledgeVersion": _safe_label(knowledge_version),
        "knowledgeManifestSha256": (
            str(knowledge_manifest_sha256).lower()
            if re.fullmatch(r"[0-9a-fA-F]{64}", str(knowledge_manifest_sha256 or ""))
            else ""
        ),
        "outcome": "pending",
    }
    with _LOCK:
        rows = _load()
        if any(existing.get("tokenDigest") == token_digest for existing in rows):
            return
        rows.append(row)
        _save(rows)


def note_review(
    *,
    thread_id: str,
    approval_token: str,
    outcome: str,
    overrides: Mapping[str, object] | None = None,
    execution_succeeded: bool | None = None,
    now: float | None = None,
) -> None:
    """Mark approval/cancellation and bounded edit-volume counters."""
    if outcome not in {"approved", "cancelled", "failed"} or not approval_token:
        return
    timestamp = float(time.time() if now is None else now)
    token_digest = _digest(approval_token)
    edit_fields, edit_chars = _edit_volume(overrides)
    with _LOCK:
        rows = _load()
        for row in reversed(rows):
            if (row.get("tokenDigest") == token_digest
                    and row.get("threadDigest") == _digest(thread_id)):
                # Terminal records are immutable/idempotent.
                if row.get("outcome") != "pending":
                    return
                drafted_at = float(row.get("draftedAt") or timestamp)
                row.update(
                    outcome=outcome,
                    reviewedAt=timestamp,
                    reviewSeconds=max(0.0, round(timestamp - drafted_at, 3)),
                    editFieldCount=min(edit_fields, 1_000),
                    editCharacterCount=min(edit_chars, 1_000_000),
                    majorRewrite=(
                        edit_fields > _MAJOR_REWRITE_FIELDS
                        or edit_chars > _MAJOR_REWRITE_CHARS
                    ),
                )
                if execution_succeeded is not None:
                    row["executionSucceeded"] = bool(execution_succeeded)
                _save(rows)
                return


def summary(*, days: int = 30, now: float | None = None) -> dict[str, object]:
    """Aggregate a stable dashboard payload without exposing per-turn identities."""
    timestamp = float(time.time() if now is None else now)
    bounded_days = max(1, min(int(days or 30), 365))
    cutoff = timestamp - bounded_days * 86_400
    with _LOCK:
        rows = [row for row in _load() if float(row.get("draftedAt") or 0) >= cutoff]
    terminal = [row for row in rows if row.get("outcome") != "pending"]
    approved = [row for row in terminal if row.get("outcome") == "approved"]
    reviewed = [row for row in terminal if isinstance(row.get("reviewSeconds"), (int, float))]
    no_major = [row for row in approved if row.get("majorRewrite") is False]
    writes = [row for row in approved if "executionSucceeded" in row]
    engines: dict[str, int] = {}
    for row in rows:
        label = _safe_label(row.get("engine"), maximum=40) or "unknown"
        engines[label] = engines.get(label, 0) + 1

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator * 100 / denominator, 1) if denominator else None

    def metrics(group: list[dict[str, Any]]) -> dict[str, object]:
        finished = [row for row in group if row.get("outcome") != "pending"]
        accepted = [row for row in finished if row.get("outcome") == "approved"]
        reviewed_rows = [
            row for row in finished
            if isinstance(row.get("reviewSeconds"), (int, float))
        ]
        unchanged = [row for row in accepted if row.get("majorRewrite") is False]
        return {
            "draftCount": len(group),
            "reviewedCount": len(finished),
            "acceptanceRatePct": rate(len(accepted), len(finished)),
            "noMajorRewriteRatePct": rate(len(unchanged), len(accepted)),
            "averageReviewSeconds": (
                round(sum(float(row["reviewSeconds"]) for row in reviewed_rows)
                      / len(reviewed_rows), 1)
                if reviewed_rows else None
            ),
        }

    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            _safe_label(row.get("feature"), maximum=40) or "task_draft",
            _safe_label(row.get("role"), maximum=40) or "work_architect",
            _safe_label(row.get("engine"), maximum=40) or "unknown",
            _safe_label(row.get("profile"), maximum=40),
            _safe_label(row.get("workflowBundleVersion")),
            _safe_label(row.get("knowledgeVersion")),
            str(row.get("knowledgeManifestSha256") or "")[:64],
        )
        grouped.setdefault(key, []).append(row)
    segments = [
        {
            "feature": key[0], "role": key[1], "engine": key[2],
            "profile": key[3], "workflowBundleVersion": key[4],
            "knowledgeVersion": key[5], "knowledgeManifestSha256": key[6],
            **metrics(group),
        }
        for key, group in sorted(grouped.items())
    ]

    return {
        "contract": "agent-impact.v1",
        "windowDays": bounded_days,
        "draftCount": len(rows),
        "pendingCount": len(rows) - len(terminal),
        "approvedCount": len(approved),
        "cancelledCount": sum(row.get("outcome") == "cancelled" for row in terminal),
        "failedCount": sum(row.get("outcome") == "failed" for row in terminal),
        "acceptanceRatePct": rate(len(approved), len(terminal)),
        "noMajorRewriteRatePct": rate(len(no_major), len(approved)),
        "averageReviewSeconds": (
            round(sum(float(row["reviewSeconds"]) for row in reviewed) / len(reviewed), 1)
            if reviewed else None
        ),
        "writeSuccessRatePct": rate(
            sum(row.get("executionSucceeded") is True for row in writes), len(writes)
        ),
        "engineCounts": engines,
        "segments": segments,
        "privacy": "aggregate_metadata_only",
    }


def reset_for_tests() -> None:
    with _LOCK:
        target = _path()
        try:
            target.unlink()
        except FileNotFoundError:
            pass


__all__ = ["note_draft", "note_review", "reset_for_tests", "summary"]
