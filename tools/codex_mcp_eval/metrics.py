"""Null-aware accounting for observed attempts, shared by normal/recovery runs.

Cached input and reasoning output are subsets of input/output tokens. They never
increase totalTokens. Diagnostic replay is deliberately outside these reducers.
"""
from tools.agent_eval_protocol import quantitative_metrics


USAGE_FIELDS = ("calls", "promptTokens", "completionTokens", "totalTokens", "cachedTokens",
                "reasoningTokens", "costUsd")
DISCOVERY_METHODS = frozenset({"initialize", "server/discover", "tools/list", "resources/list",
                               "resources/templates/list", "prompts/list"})


def aggregate_usage(outputs):
    outputs = list(outputs)
    usage, coverage = {}, {}
    for field in USAGE_FIELDS:
        values = [(output.get("usage") or {}).get(field) for output in outputs]
        observed = [value for value in values if type(value) in (int, float) and value >= 0]
        usage[field] = sum(observed) if values and len(observed) == len(values) else None
        coverage[field] = {"observedAttempts": len(observed), "totalAttempts": len(values),
                           "observedTotal": sum(observed) if observed else None}
    return usage, coverage


def measured_outputs(records):
    return [phase.get("output") or {} for record in records
            for phase in (record.get("phases") or [record]) if phase.get("modelAttempted") is not False]


def model_metrics(records):
    outputs = measured_outputs(records)
    usage, coverage = aggregate_usage(outputs)
    metrics = quantitative_metrics(attempts=len(outputs),
        duration_seconds=sum(record.get("durationSeconds", 0) for record in records),
        calls=usage["calls"], prompt_tokens=usage["promptTokens"], completion_tokens=usage["completionTokens"],
        total_tokens=usage["totalTokens"], cached_tokens=usage["cachedTokens"], cost_usd=usage["costUsd"])
    metrics["reasoningTokens"] = usage["reasoningTokens"]
    return metrics, coverage


def upstream_summary(records, *, service=None):
    observed = bool(records) and all(isinstance(row.get("upstreamCalls"), list) for row in records)
    calls = [call for row in records for call in row["upstreamCalls"]] if observed else []
    if service is not None:
        observed = observed and all(call.get("service") in {"jira", "confluence"} for call in calls)
        calls = [call for call in calls if call.get("service") == service]
    def total(field):
        values = [call.get(field) for call in calls]
        return round(sum(values), 6) if observed and all(value is not None for value in values) else None
    return {"observed": observed, "requests": len(calls) if observed else None,
            "responseBytes": total("responseBytes"), "sumRequestSeconds": total("durationSeconds")}


def file_metrics(calls):
    return {"calls": len(calls),
        "durationSeconds": sum(call.get("durationSeconds", 0) for call in calls),
        "readBytes": sum(len((call.get("result") or {}).get("content", "").encode("utf-8"))
                         for call in calls if call["name"] == "read_file" and call.get("ok")),
        "writeBytes": sum(len((call.get("arguments") or {}).get("content", "").encode("utf-8"))
                          for call in calls if call["name"] == "write_file" and call.get("ok"))}


def protocol_metrics(records):
    observed = bool(records) and all(isinstance(record.get("protocolCalls"), list) for record in records)
    if not observed:
        return {"observed": False, "ltmToolAttempts": None, "fileToolAttempts": None,
                "discoveryRequests": None, "otherProtocolRequests": None}
    calls = [call for record in records for call in record["protocolCalls"]]
    return {"observed": True,
        "ltmToolAttempts": sum(call["method"] == "tools/call" and call["server"] == "ltm" for call in calls),
        "fileToolAttempts": sum(call["method"] == "tools/call" and call["server"] == "research_files" for call in calls),
        "discoveryRequests": sum(call["method"] in DISCOVERY_METHODS for call in calls),
        "otherProtocolRequests": sum(call["method"] not in DISCOVERY_METHODS | {"tools/call"} for call in calls)}
