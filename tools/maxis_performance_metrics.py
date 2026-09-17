"""Observation-only metrics. Contract success is never a usefulness judgement."""
from __future__ import annotations

import math
from collections import Counter

from tools.agent_eval_protocol import nearest_rank_percentile

CONTRACT = "maxis-performance-observations.v1"
STATUSES = frozenset({"ok", "needs_input", "blocked", "error", "unknown"})
TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _number(value, *, integer=False):
    if value is None:
        return None
    valid = type(value) is int if integer else type(value) in (int, float)
    if not valid or (type(value) is float and not math.isfinite(value)) or value < 0:
        raise ValueError("measurement must be finite and nonnegative")
    return value


def _measurement(values, *, latency=False):
    observed = [value for value in values if value is not None]
    complete = bool(values) and len(observed) == len(values)
    total = _number(sum(observed)) if observed else None
    result = {"observedAttempts": len(observed), "complete": complete,
              "observedTotal": total, "total": total if complete else None}
    if latency:
        # Do not label a successful/observed subset as the whole-run percentile.
        result.update({f"p{p}": nearest_rank_percentile(observed, p) if complete else None
                       for p in (50, 95)})
    return result


def summarize_attempts(rows: list[dict]) -> dict:
    """Summarize normalized attempts, retaining failures and unknown observations.

    Inputs carry only an attemptId, roleId, status, responseReceived, latencyMs,
    usage, nativeLlmCalls and costUsd. Missing measurements are NOT zero. No
    response text, exception text or arbitrary input fields are copied to output.
    """
    from app.agent.workflow.role_manifest import ROLE_SPECS

    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("attempts must be a list of objects")
    seen = set()
    for row in rows:
        identity = row.get("attemptId")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("each attempt requires a unique identity")
        seen.add(identity)
        if not isinstance(row.get("roleId"), str) or row["roleId"] not in ROLE_SPECS:
            raise ValueError("unknown canonical role")
        if not isinstance(row.get("status"), str) or row["status"] not in STATUSES:
            raise ValueError("unknown observation status")
        if row.get("responseReceived") is not None and type(row["responseReceived"]) is not bool:
            raise ValueError("responseReceived must be boolean or unknown")
        usage = row.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("usage must be an object or unknown")
        values = [_number((usage or {}).get(key), integer=True) for key in TOKEN_KEYS]
        if all(value is not None for value in values) and values[0] + values[1] != values[2]:
            raise ValueError("token measurements are inconsistent")

    def aggregate(items):
        return {
            "attempts": len(items),
            "statuses": dict(sorted(Counter(row["status"] for row in items).items())),
            "responsesReceived": sum(row.get("responseReceived") is True for row in items),
            "responseObservationComplete": bool(items) and all(
                type(row.get("responseReceived")) is bool for row in items),
            "latencyMs": _measurement([_number(row.get("latencyMs")) for row in items], latency=True),
            "tokens": {key: _measurement([
                _number((row.get("usage") or {}).get(key), integer=True) for row in items
            ]) for key in TOKEN_KEYS},
            "nativeLlmCalls": _measurement([
                _number(row.get("nativeLlmCalls"), integer=True) for row in items]),
            "costUsd": _measurement([_number(row.get("costUsd")) for row in items]),
        }

    return {
        "contract": CONTRACT, "semanticCorrectnessAssessed": False,
        "qualificationEligible": False,
        "callsMeaning": "responsesReceived is not the number of native LLM calls",
        "percentileMethod": "nearest-rank-over-all-attempts-if-complete",
        "overall": aggregate(rows),
        "byRole": {role: aggregate([row for row in rows if row["roleId"] == role])
                   for role in sorted({row["roleId"] for row in rows})},
        "usableCompletions": None, "necessaryClarifications": None,
        "userTaskSeconds": None, "correctionSeconds": None,
        "costPerUsableCompletionUsd": None,
    }


def editor_observations(raw: dict) -> list[dict]:
    """Read the existing frozen Editor component format, without regrading it.

    A source run must retain every selected case. Focused runs remain focused;
    no cross-run merge, best-attempt selection or comparison is performed here.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("metadata"), dict):
        raise ValueError("raw metadata is missing")
    battery = raw["metadata"].get("battery")
    if not isinstance(battery, dict):
        raise ValueError("raw battery metadata is missing")
    if battery.get("name") != "maxis-editor-component":
        raise ValueError("unsupported raw battery")
    selected = battery.get("selectedCaseIds")
    cases = raw.get("cases")
    if not isinstance(selected, list) or not selected or not isinstance(cases, list):
        raise ValueError("raw battery selection is missing")
    if any(not isinstance(case, dict) for case in cases):
        raise ValueError("raw attempts must be objects")
    ids = [case.get("caseId") for case in cases]
    if (any(not isinstance(case_id, str) or not case_id for case_id in selected + ids)
            or len(set(ids)) != len(ids) or len(set(selected)) != len(selected)
            or set(selected) != set(ids)):
        raise ValueError("raw attempts are duplicate, missing or outside the selection")
    observations = []
    for case in cases:
        response = case.get("response")
        if response is not None and not isinstance(response, dict):
            raise ValueError("raw response must be an object")
        status = (response or {}).get("status", "unknown")
        if not isinstance(status, str):
            raise ValueError("raw status must be a string")
        if case.get("errorCode"):
            status = "error"
        if status not in STATUSES:
            status = "unknown"
        observations.append({
            "attemptId": case["caseId"], "roleId": "editor_author", "status": status,
            "latencyMs": case.get("latencyMs"), "responseReceived": case.get("responseReceived"),
            "usage": case.get("usage"),
            # Native counts/prices are absent from this raw contract. Do not infer
            # two calls from the workflow topology or treat missing price as zero.
            "nativeLlmCalls": None, "costUsd": None,
        })
    return observations
