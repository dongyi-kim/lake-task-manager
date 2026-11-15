"""Local Tracking benchmark: real collector/REST adapter, synthetic data, no external API.

Run with the repository Python: python -B tools/tracking_benchmark.py --output <outside-repo.json>
Cold means a fresh client/cache/checkpoint. Warm means a NEW run using source cache;
reopening a saved run is measured separately. Injected transport delay is not prod latency.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


class ObservedProvider:
    """Count every physical request independently of collector-internal counters."""

    def __init__(self, app, latency_ms, parallel=False):
        from fastapi.testclient import TestClient
        self.transport = TestClient(app)
        self.supports_parallel = parallel
        self.latency = latency_ms / 1000
        self.gate, self.lock = threading.Lock(), threading.Lock()
        self.records = []

    def _call(self, method, path, params=None, body=None):
        from app.auth.base import UpstreamError
        with nullcontext() if self.supports_parallel else self.gate:
            started = time.perf_counter()
            if self.latency:
                time.sleep(self.latency)
            arguments = {"params": params or {}}
            if body is not None:
                arguments["json"] = body
            response = self.transport.request(method, urlsplit(path).path, **arguments)
            with self.lock:
                self.records.append({"method": method, "path": urlsplit(path).path,
                                     "params": params or {}, "status": response.status_code,
                                     "responseBytes": len(response.content),
                                     "elapsedMs": round((time.perf_counter() - started) * 1000, 2)})
            if response.status_code >= 400:
                raise UpstreamError(response.status_code, path, response.text)
            return response.json()

    def get_json(self, path, params=None, **kwargs):
        return self._call("GET", path, params)

    def post_json(self, path, json_body=None, params=None):
        return self._call("POST", path, params, json_body)

    def close(self):
        self.transport.close()


def oracle_events(fixture, request):
    """Core exact events only; optional field/last-edit observations have separate coverage."""
    from app.tracking.analysis import period_bounds, instant
    start, end = period_bounds(request["startDate"], request["endDate"], instant(fixture.as_of))
    kinds = {"created", "completed", "reopened", "comment"}
    return Counter((event["issueKey"], event["kind"], event["actorId"], instant(event["at"]).isoformat())
                   for event in fixture.expected["events"]
                   if event["kind"] in kinds and start <= instant(event["at"]) < end)


def validate_snapshot(snapshot, fixture, request):
    from app.tracking.analysis import instant
    kinds = {"created", "completed", "reopened", "comment"}
    expected = oracle_events(fixture, request)
    actual = Counter((event["issueKey"], event["kind"], event["actorId"], instant(event["date"]).isoformat())
                     for event in snapshot["events"] if event["kind"] in kinds)
    # Normalize equivalent ISO offsets before comparing.
    from datetime import timezone
    normalize = lambda rows: Counter({(*key[:3], instant(key[3]).astimezone(timezone.utc).isoformat()): count
                                     for key, count in rows.items()})
    expected, actual = normalize(expected), normalize(actual)
    missing, extra = expected - actual, actual - expected
    if snapshot["status"] != "complete" or missing or extra:
        raise AssertionError(json.dumps({"status": snapshot["status"], "coverage": snapshot.get("coverage"),
                                        "errors": snapshot.get("errors"), "missing": list(missing.items())[:8],
                                        "extra": list(extra.items())[:8]}, ensure_ascii=False))
    return {"expectedEvents": sum(expected.values()), "missing": 0, "extra": 0}


def measure(manager, provider, fixture, request, condition):
    begin = len(provider.records)
    start = time.perf_counter()
    snapshot = manager.create(request, background=False)
    elapsed = (time.perf_counter() - start) * 1000
    records = provider.records[begin:]
    accuracy = validate_snapshot(snapshot, fixture, request)
    return snapshot, {"condition": condition, "elapsedMs": round(elapsed, 2),
                      "restRequests": len(records), "responseBytes": sum(row["responseBytes"] for row in records),
                      "requestsByPath": dict(Counter(row["path"] for row in records)),
                      "collectorMetrics": snapshot["metrics"], "summary": snapshot["summary"],
                      "accuracy": accuracy, "requests": records}


def benchmark(args, scratch):
    from app.infra.cache import Cache
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient
    from app.mock.tracking_fixture import build_tracking_fixture
    from app.tracking.manager import TrackingManager

    fixture = build_tracking_fixture(archived_count=args.archived, heavy=args.heavy)
    requests = {
        "day": {"startDate": "2026-11-14", "endDate": "2026-11-14"},
        "week": {"startDate": "2026-11-09", "endDate": "2026-11-15"},
        "month": {"startDate": "2026-11-01", "endDate": "2026-11-15"},
    }
    samples = []
    variants = [(name, False, {}) for name in requests if args.period in ("all", name)]
    if args.period in ("all", "month"):
        variants += [("month", True, {}), ("month", False, {"supports_comment": False, "supports_changelog": False})]
    for case_index, (period, parallel, compat) in enumerate(variants):
        for repeat in range(args.repeats if not compat else 1):
            app = fixture.make_app(**compat)
            provider = ObservedProvider(app, args.latency_ms, parallel)
            cache = Cache(":memory:")
            settings = get_settings()
            settings.search_jira_projects = ["TRACK"]
            settings.project_key = "TRACK"
            settings.managers = []
            client = JiraClient(settings, cache)
            client._provider = provider
            client._provider_built = True
            manager = TrackingManager(client, db_path=str(scratch / f"run-{case_index}-{repeat}.sqlite3"),
                                      plan_loader=lambda: fixture.plan, people_loader=lambda: fixture.people)
            request = {**requests[period], "people": fixture.people["Tracking"], "modules": ["Tracking"]}
            try:
                for condition in ("cold", "warm"):
                    snapshot, result = measure(manager, provider, fixture, request, condition)
                    samples.append({"period": period, "parallel": parallel, "batchEvidence": not bool(compat),
                                    "repeat": repeat + 1, **result})
                    print(f"{period} {'parallel' if parallel else 'serial'} {'batch' if not compat else 'fallback'} "
                          f"{condition}: {result['restRequests']} REST, {result['elapsedMs']} ms", flush=True)
                before = len(provider.records)
                manager.get(snapshot["id"])
                if len(provider.records) != before:
                    raise AssertionError("Reopening completed snapshot made an upstream request")
                if period == "month" and not parallel and not compat and repeat == 0:
                    narrower = {**request, **requests["day"]}
                    _, reuse = measure(manager, provider, fixture, narrower, "cross-period-day")
                    samples.append({"period": period, "parallel": parallel, "batchEvidence": True,
                                    "repeat": 1, **reuse})
                    issue = app.state.store.issues["TRACK-1"]
                    actor = fixture.people["Tracking"][0]
                    issue["comments"].append({"id": "tracking-benchmark-new-comment", "author": actor,
                        "created": date(2026, 11, 14), "tcreated": "02:30", "kind": "note",
                        "body": "Verification added after cache population.", "text": "Verification added after cache population."})
                    issue.update(updated=date(2026, 11, 16), tupdated="02:30")
                    expected = {**fixture.expected, "events": fixture.expected["events"] + [{
                        "issueKey": "TRACK-1", "kind": "comment", "actorId": actor,
                        "at": datetime(2026, 11, 14, 2, 30, tzinfo=timezone.utc).isoformat()}]}
                    changed_fixture = replace(fixture, expected=expected)
                    _, changed = measure(manager, provider, changed_fixture, request, "one-ticket-updated")
                    samples.append({"period": period, "parallel": parallel, "batchEvidence": True,
                                    "repeat": 1, **changed})
            finally:
                provider.close()
                cache._conn.close()
    groups = {}
    for sample in samples:
        label = f"{sample['period']}/{'parallel' if sample['parallel'] else 'serial'}/{'batch' if sample['batchEvidence'] else 'fallback'}/{sample['condition']}"
        groups.setdefault(label, []).append(sample)
    aggregated = {label: {"samples": len(rows), "p50Ms": round(statistics.median(row["elapsedMs"] for row in rows), 2),
                          "minMs": min(row["elapsedMs"] for row in rows), "maxMs": max(row["elapsedMs"] for row in rows),
                          "restRequests": sorted({row["restRequests"] for row in rows}),
                          "responseBytes": sorted({row["responseBytes"] for row in rows})}
                  for label, rows in groups.items()}
    code = sorted((ROOT / "app" / "tracking").glob("*.py")) + [ROOT / "app/mock/tracking_fixture.py", Path(__file__)]
    fingerprint = hashlib.sha256(b"".join(path.name.encode() + path.read_bytes() for path in code)).hexdigest()
    return {"protocol": "tracking-local-v1", "implementationFingerprint": fingerprint,
            "fixture": {"members": 15, "activeEpics": 10, "activeTasks": 30,
            "activeSubtasks": 60, "archived": args.archived, "heavy": args.heavy, "asOf": fixture.as_of},
            "environment": {"transport": "in-process REST", "injectedLatencyMs": args.latency_ms,
                            "productionMeasurement": False, "bytes": "UTF-8 JSON response body; no HTTP compression"},
            "aggregated": aggregated, "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--latency-ms", type=int, default=50)
    parser.add_argument("--archived", type=int, default=9900)
    parser.add_argument("--heavy", action="store_true")
    parser.add_argument("--period", choices=("all", "day", "week", "month"), default="all")
    args = parser.parse_args()
    if args.repeats < 1 or args.latency_ms < 0 or args.archived < 0:
        parser.error("repeats >= 1 and latency/archived >= 0 required")
    if args.output and args.output.resolve().is_relative_to(ROOT.parent):
        parser.error("Keep raw benchmark output outside the workspace")
    with TemporaryDirectory() as folder:
        scratch = Path(folder)
        os.environ.update(JIRA_ENV="mock", LAKE_CACHE_DIR=str(scratch / "runtime"),
                          CACHE_DB_PATH=str(scratch / "runtime" / "cache.sqlite3"), PYTHONDONTWRITEBYTECODE="1")
        report = benchmark(args, scratch)
    data = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data + "\n", encoding="utf-8")
        print(f"Report: {args.output}")
    else:
        print(data)


if __name__ == "__main__":
    main()
