"""Exact preregistered pilot/formal schedules, separate from quality scoring."""
from collections import Counter
from hashlib import sha256
import json
import re

from .cases import CASES
from .recovery import recovery_cases

REPRESENTATIVE_CASE_IDS = ("ETL-F03-boundary", "ETL-F04-boundary", "ETL-F08-normal",
                           "ETL-F09-boundary", "ETL-F12-boundary", "ETL-F14-boundary")
SCHEDULE_VERSION = "etl-year-schedule-v3"
SCHEDULE_STATUS = "content-first-pilot-and-formal-selection"

REPRESENTATIVE_COVERAGE = {
    "ETL-F03-boundary": ("three-stream monthly scope", "eight original Task lifecycles", "unlinked and paraphrased reports",
        "grouped/split many-to-many evidence", "repeated-report dedup", "ambiguous matching and duplicate dates", "module/environment decoy"),
    "ETL-F04-boundary": ("current/historical WBS mapping", "SP-weighted denominator vs count", "source revision"),
    "ETL-F08-normal": ("hot issue pagination", "partial multi-target completion", "ticket/target/table/event units", "duplicate execution report"),
    "ETL-F09-boundary": ("modification vs reload", "missing operational proof", "unknown total"),
    "ETL-F12-boundary": ("simple VoC vs operational execution", "status vs verified answer", "unknown resolution"),
    "ETL-F14-boundary": ("known-then/reconstructed/current views", "late publication", "superseded acceptance", "Jira/report conflict"),
}


def expected_case_ids(phase, arm, kind="main"):
    if phase not in {"pilot", "formal"} or arm not in {"A", "B", "C"} or kind not in {"main", "recovery"}:
        raise ValueError("Invalid ETL phase/arm/kind")
    if kind == "recovery":
        return tuple(row["id"] for row in recovery_cases()) if phase == "formal" and arm in {"A", "C"} else ()
    return REPRESENTATIVE_CASE_IDS if phase == "pilot" or arm == "B" else tuple(row["id"] for row in CASES)


def validate_selection(phase, arm, selected, kind="main"):
    expected = expected_case_ids(phase, arm, kind)
    if len(set(selected)) != len(selected) or set(selected) != set(expected):
        raise ValueError("ETL qualification requires the exact preregistered case selection")
    return expected


def build_schedule(phase, commits, *, seed="ltm-etl-year-v1"):
    if phase not in {"pilot", "formal"} or set(commits) != {"A", "B", "C"} or any(
            not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit) for commit in commits.values()):
        raise ValueError("All three actual candidate commit identities are required")
    rows = []
    for repeat in range(1, 2 if phase == "pilot" else 6):
        # Fixed cyclic order is reproducible; A/C order alternates over repeats
        # while B remains paired on precisely the six representative cases.
        order = ("A", "B", "C") if repeat % 2 else ("C", "B", "A")
        for kind in ("main", "recovery"):
            for position, arm in enumerate(order, 1):
                for case_id in expected_case_ids(phase, arm, kind):
                    rows.append({"phase": phase, "kind": kind, "arm": arm, "caseId": case_id,
                        "repeatIndex": repeat, "candidateOrderIndex": position, "candidateCommit": commits[arm],
                        "model": "gpt-6.1-sol", "reasoningEffort": "high", "seed": seed,
                        "skillCondition": "candidate" if arm == "C" else "baseline", "researchFiles": True})
    return rows


def schedule_manifest(rows):
    return {"version": SCHEDULE_VERSION, "status": SCHEDULE_STATUS, "attempts": len(rows),
            "arms": dict(Counter(row["arm"] for row in rows)),
            "sha256": sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest(),
            "qualificationClaim": False, "directReviewRequired": True}


def audit_schedule(records, planned):
    """Missing/extra/retried attempts cannot silently replace the planned set.

    Actual results must carry the declared backend commit and full measurement
    identity. This design audit never confers quality qualification by itself.
    """
    keys = ("phase", "kind", "arm", "caseId", "repeatIndex")
    identity = lambda row: tuple(row.get(key) for key in keys)
    wanted = {identity(row): row for row in planned}
    observed = Counter(identity(row) for row in records)
    failures = []
    if len(wanted) != len(planned) or set(observed) != set(wanted) or any(count != 1 for count in observed.values()):
        failures.append("exact_schedule_not_completed")
    for row in records:
        expected = wanted.get(identity(row))
        if expected and any(row.get(key) != expected[key] for key in (
            "candidateCommit", "candidateOrderIndex", "model", "reasoningEffort", "seed", "skillCondition", "researchFiles")):
            failures.append("preregistered_identity_mismatch")
        if row.get("executionComplete") is not True or row.get("infrastructureError"):
            failures.append("unfinished_or_failed_attempt")
    return {"designPassed": not failures, "failures": sorted(set(failures)), "qualificationClaim": False,
            "directReviewRequired": True, "perStreamMeanMinimum": 4.5, "materialDefectsAllowed": 0}
