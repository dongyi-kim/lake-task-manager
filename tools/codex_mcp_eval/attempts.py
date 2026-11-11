"""Retain paid outputs before checking, artifact writes or fixture teardown."""
from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path

from tools.codex_mcp_eval.metrics import aggregate_usage, file_metrics


class ResultPersistenceError(RuntimeError):
    """Both result writes failed; the caller still owns the complete paid payload."""
    def __init__(self, payload, path):
        super().__init__("Evaluation result persistence failed; paid payload retained on this exception")
        self.payload, self.path = payload, Path(path)


def persist_result(path, payload, *, writer):
    """Stop the run on a failed primary write and retain an explicit failure sidecar.

    Returns False after a successful sidecar. If the filesystem cannot retain
    either file, raise with the complete payload instead of replacing it.
    """
    path = Path(path)
    try:
        writer(path, payload)
        return True
    except Exception as exc:
        payload.update(infrastructureError="result_write_failed", executionComplete=False,
                       persistenceErrorType=type(exc).__name__)
        fallback = path.with_name(path.stem + "-persistence-failure" + path.suffix)
        try:
            writer(fallback, payload)
        except Exception as fallback_error:
            payload["fallbackPersistenceErrorType"] = type(fallback_error).__name__
            raise ResultPersistenceError(payload, path) from fallback_error
        return False


def observer_offsets(fixture):
    """Return append boundaries used for phase deltas or diagnostic exclusions."""
    result = {name: len(getattr(fixture, name)) for name in
              ("tool_calls", "upstream_calls", "protocol_calls", "transport_calls", "skill_reads")
              if hasattr(fixture, name)}
    files = getattr(fixture, "files", None)
    if files is not None:
        result["fileCalls"] = len(files.calls)
    return result


def snapshot_attempt(record, fixture, output=None, *, offsets=None, limits=None):
    """Capture observed costs now; offsets select deltas on a reused fixture.

    Offset/limit keys are tool_calls/upstream_calls/protocol_calls/transport_calls/
    skill_reads/fileCalls. Limits freeze paid trace membership before diagnostics.
    No summary/checker/serialization is called here. Mark modelAttempted=True
    before invoking the CLI so invocation failures retain unknown usage honestly.
    """
    if output is not None:
        record["output"] = deepcopy(output)
        record["modelAttempted"] = True
    output = record.setdefault("output", {})
    record["durationSeconds"] = output.get("durationSeconds", 0)
    offsets = offsets or {}
    limits = limits or {}
    with getattr(fixture, "_lock", nullcontext()):
        for source, target in (("tool_calls", "mcpToolCalls"), ("upstream_calls", "upstreamCalls"),
                               ("protocol_calls", "protocolCalls"), ("transport_calls", "transportCalls"),
                               ("skill_reads", "skillReads")):
            if hasattr(fixture, source):
                record[target] = deepcopy(getattr(fixture, source)[offsets.get(source, 0):limits.get(source)])
    if "mcpToolCalls" in record:
        output["mcpToolCalls"] = deepcopy(record["mcpToolCalls"])
    files = getattr(fixture, "files", None)
    if files is not None:
        record["fileCalls"] = deepcopy(files.calls[offsets.get("fileCalls", 0):limits.get("fileCalls")])
        record["fileMetrics"] = file_metrics(record["fileCalls"])
    record["attemptCaptured"] = True
    return record


def settle_attempt(record, fixture, *, offsets=None, limits=None, timeout_seconds=5.0, after_teardown=False):
    """Bound observer completion and merge its attributable tail into paid costs.

    Call before checking/restarting a backend and again after teardown. Preserve
    the early checkpoint separately. Pass frozen limits after diagnostic replay;
    an unsettled attempt must not start a diagnostic or a new measured phase.
    """
    barrier = getattr(fixture, "settle_observers", None)
    result = (dict(barrier(timeout_seconds=timeout_seconds)) if barrier else
              {"complete": None, "timedOut": None, "active": None, "coverage": "barrier-unavailable"})
    result.update(timeoutSeconds=timeout_seconds, afterTeardown=after_teardown)
    snapshot_attempt(record, fixture, offsets=offsets, limits=limits)
    record["settlement"] = result
    record.setdefault("settlementHistory", []).append(deepcopy(result))
    if after_teardown and result.get("complete") is False:
        mark_infrastructure_failure(record, TimeoutError(), "observer_settlement_timeout")
    return record


def mark_infrastructure_failure(record, exc, stage):
    """Keep all captured evidence while making a later infrastructure fault explicit."""
    record.setdefault("infrastructureError", stage)
    record["errorType"] = type(exc).__name__
    record.setdefault("infrastructureErrors", []).append({"stage": stage, "errorType": type(exc).__name__})
    record["automaticPass"] = False
    contract = record.setdefault("automaticContract", {"pass": False, "failures": []})
    contract["pass"] = False
    if stage not in contract.setdefault("failures", []):
        contract["failures"].append(stage)
    return record


def combine_phase_attempts(record, phases):
    """Merge executed phase costs once, preserving each original phase record."""
    phases = list(phases)
    record["phases"] = deepcopy(phases)
    active = [phase for phase in phases if phase.get("modelAttempted") is not False]
    output = deepcopy((active[-1].get("output") or {}) if active else {})
    output["usage"], record["usageCoverage"] = aggregate_usage(phase.get("output") or {} for phase in active)
    record["durationSeconds"] = output["durationSeconds"] = sum(
        (phase.get("output") or {}).get("durationSeconds", phase.get("durationSeconds", 0)) for phase in active)
    for field in ("mcpToolCalls", "upstreamCalls", "protocolCalls", "transportCalls", "skillReads", "fileCalls"):
        if any(field in phase for phase in phases):
            record[field] = [deepcopy(call) for phase in phases for call in phase.get(field, [])]
    output["mcpToolCalls"] = deepcopy(record.get("mcpToolCalls", []))
    output["forbiddenToolEvents"] = [deepcopy(event) for phase in phases
        for event in (phase.get("output") or {}).get("forbiddenToolEvents", [])]
    if "fileCalls" in record:
        record["fileMetrics"] = file_metrics(record["fileCalls"])
    record["output"] = output
    record["modelAttempted"] = bool(active)
    for phase in phases:
        if phase.get("infrastructureError"):
            record.setdefault("infrastructureError", phase["infrastructureError"])
            record["automaticPass"] = False
    return record
