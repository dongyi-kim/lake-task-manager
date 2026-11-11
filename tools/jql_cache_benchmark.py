"""One-revision backend benchmark; no Git switching, source copies or production APIs.

Default trials are five empty-cache and twenty valid-cache observations per journey.
Use --cold-iterations 1 --warm-iterations 1 --journeys bulk for a bounded smoke.
Each invocation reserves a new external artifact directory and disposable OS scratch.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
from unittest.mock import patch


ARMS = ("H", "F", "F+A", "F+B", "F+A+B")


def git(repo, *arguments):
    return subprocess.check_output(["git", "-C", str(repo), *arguments], stderr=subprocess.PIPE)


def repository_identity(repo):
    repo = Path(repo).resolve()
    if Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve() != repo:
        raise ValueError("Benchmark must run at the application's actual Git root")
    status = git(repo, "status", "--porcelain=v1", "-z")
    tree = hashlib.sha256(git(repo, "diff", "--binary", "HEAD"))
    for path in sorted(git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")):
        if path:
            file = repo / os.fsdecode(path)
            tree.update(path)
            tree.update(hashlib.sha256(file.read_bytes()).digest())
    requirements = {file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                    for file in repo.glob("requirements*.txt")}
    return {"checkout": str(repo), "commit": git(repo, "rev-parse", "HEAD").decode().strip(),
            "branch": git(repo, "branch", "--show-current").decode().strip(), "dirty": bool(status),
            "statusSha256": hashlib.sha256(status).hexdigest(), "workingTreeSha256": tree.hexdigest(),
            "requirementsSha256": requirements}


def reserve_attempt(repo, output):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    # The deploy parent's runtime workspace is also outside the durable research destination.
    protected = [repo]
    if (repo.parent / ".git").exists():
        protected.append(repo.parent)
    if any(output.is_relative_to(root) for root in protected):
        raise ValueError("Durable benchmark artifacts must stay outside repository workspaces")
    output.mkdir(parents=True, exist_ok=False)
    return output


def dependency_identity(label):
    packages = {}
    for name in ("jira820", "fastapi", "httpx", "httpx2", "starlette", "mcp"):
        try:
            package = importlib.metadata.distribution(name)
            direct = package.read_text("direct_url.json")
            packages[name] = {"version": package.version, "location": str(package.locate_file("")),
                              "directUrl": json.loads(direct) if direct else None}
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"declaredEnvironment": label, "python": sys.version, "executable": sys.executable,
            "packages": packages}


def summarize(values):
    values = sorted(values)
    return {"samples": len(values), "p50Ms": statistics.median(values) if values else None,
            "p95Ms": values[min(len(values) - 1, round((len(values) - 1) * .95))] if values else None}


def run(repo, latency_ms=0, cold_iterations=5, warm_iterations=20, *, arm,
        output_dir, dependency_label, seed="ltm-etl-year-v1", jitter_ms=0,
        parallel=False, journeys=None, suite="common"):
    repo = Path(repo).resolve()
    if repo != Path(__file__).resolve().parents[1] or Path.cwd().resolve() != repo:
        raise ValueError("Use the one current application checkout as the working directory")
    if arm not in ARMS or suite not in {"common", "conformance"} or not dependency_label:
        raise ValueError("An explicit arm, suite and dependency environment are required")
    if any(type(value) is not int or value < 0 for value in (latency_ms, jitter_ms, warm_iterations)) or cold_iterations < 1:
        raise ValueError("Latency, jitter and warm counts must be nonnegative; cold count must be positive")
    identity = repository_identity(repo)
    output = reserve_attempt(repo, output_dir)
    with tempfile.TemporaryDirectory() as scratch, ExitStack() as stack:
        scratch = Path(scratch)
        # Import-time singleton resources must also stay outside both source repositories.
        stack.enter_context(patch.dict(os.environ, {"JIRA_ENV": "mock", "PYTHONDONTWRITEBYTECODE": "1",
            "LAKE_CACHE_DIR": str(scratch / "runtime"), "CACHE_DB_PATH": str(scratch / "runtime.sqlite3")}))
        sys.dont_write_bytecode = True
        from tools.jql_benchmark_runtime import BackendTrial, RawAttempt, transport_metrics
        attempt = stack.enter_context(RawAttempt(output, {
            "protocol": "jql-backend-v2", "stage": "preflight", "arm": arm,
            "revision": identity, "dependencies": dependency_identity(dependency_label),
            "worldSeed": seed, "scratchPath": str(scratch), "suite": suite,
            "requestedJourneys": journeys, "latencyMs": latency_ms, "jitterMs": jitter_ms,
            "coldIterations": cold_iterations, "warmIterations": warm_iterations,
            "mcpCalls": 0, "modelUsage": None}))
        from tools.jql_benchmark_journeys import COVERAGE, run_common, run_conformance
        from tools.codex_mcp_eval.etl import YearWorldSpec, build_etl_world
        names = list(journeys or COVERAGE) if suite == "common" else ["explicit-options"]
        if suite == "common" and (len(names) != len(set(names)) or set(names) - COVERAGE.keys()):
            raise ValueError("Select distinct declared backend journeys")
        bundle = build_etl_world(YearWorldSpec(seed=seed))
        manifest = {"protocol": "jql-backend-v2", "runKind": "single-revision-backend", "arm": arm,
                    "revision": identity, "dependencies": dependency_identity(dependency_label), "world": bundle.manifest,
                    "scratchPath": str(scratch), "suite": suite, "selectedJourneys": names,
                    "coldIterations": cold_iterations, "warmIterations": warm_iterations if suite == "common" else None,
                    "repetitionPolicy": "common: empty trials plus distributed valid repeats; conformance: option sequences only",
                    "latencyMs": latency_ms, "jitterMs": jitter_ms,
                    "jitterPolicy": "SHA256(worldSeed,method,url,params), symmetric integer milliseconds",
                    "providerMode": "parallel-synthetic-inprocess" if parallel else "serial-inprocess",
                    "freshnessClock": "ETL observation instant; cache/Jira clock and JQL datetime frozen",
                    "cachePolicy": "omitted in common suite; explicit options only in conformance",
                    "backgroundWork": "disabled consistently by shared EvaluationFixture; settlement still recorded",
                    "observerCoverage": "provider-scoped TestClient dispatches including auth, pages, retries, failed dispatches",
                    "requestTiming": "wire observer excludes artificial delay; phase latency includes it",
                    "metricSemantics": {"logicalQueryCount": "known top-level search inputs; null for opaque navigation helpers",
                        "logicalDnfLeaves": "null unless independently available; never inferred from HTTP pages",
                        "uniquePhysicalJql": "distinct dispatched JQL strings within the reported aggregation scope",
                        "searchPageAttempts": "all search HTTP dispatches, including repeated pages and failures"},
                    "mcpCalls": 0, "modelUsage": None, "journeyCoverage": COVERAGE,
                    "armDesign": {"anchor": "H", "cells": ["F", "F+A", "F+B", "F+A+B"],
                        "attribution": "H→F separately; B effects conditional on correctness foundation F",
                        "prerequisiteCommits": None},
                    "qualificationEligible": False,
                    "limitations": ["One revision only; clean prerequisite/arm commits not supplied",
                        "No actual UI ten-repeat journeys, production SSO or model/token measurements",
                        "Partial journeys list missing contracts explicitly; conformance here checks parity only",
                        "No production background-revalidation performance claim"]}
        records = []
        with (output / "configuration.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
        base, remainder = divmod(warm_iterations, cold_iterations)
        for name in names:
            for repeat in range(cold_iterations):
                trial = BackendTrial(scratch / name / str(repeat), bundle, latency_ms, jitter_ms, parallel)
                try:
                    with trial:
                        if suite == "common":
                            run_common(trial, name, base + (repeat < remainder))
                        else:
                            run_conformance(trial)
                finally:
                    if hasattr(trial, "fixture"):
                        record = trial.result(name, repeat)
                        records.append(record)
                        attempt.record(record)
        all_calls = [call for record in records for call in record["upstreamCalls"]]
        end_identity = repository_identity(repo)
        phases = [phase for record in records for phase in record["phases"]]
        result = {**manifest, "revisionAfter": end_identity,
            "revisionStable": identity == end_identity, "complete": True,
            "summary": {"allCorrect": all(record["ok"] for record in records),
                "trials": len(records), **transport_metrics(all_calls),
                "emptyLatency": summarize([p["durationMs"] for p in phases if p["cacheState"] == "empty"]),
                "validLatency": summarize([p["durationMs"] for p in phases if p["cacheState"] == "valid"])}}
        attempt.result = result
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--arm", choices=ARMS, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dependency-label", required=True)
    parser.add_argument("--dependency-dir", type=Path)
    parser.add_argument("--latency-ms", type=int, choices=(0, 250, 800), default=0)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--cold-iterations", type=int, default=5)
    parser.add_argument("--warm-iterations", type=int, default=20)
    parser.add_argument("--seed", default="ltm-etl-year-v1")
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--suite", choices=("common", "conformance"), default="common")
    parser.add_argument("--journeys", nargs="+")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.dependency_dir:
        sys.path.insert(0, str(args.dependency_dir.resolve()))
    result = run(args.repo, args.latency_ms, args.cold_iterations, args.warm_iterations,
        arm=args.arm, output_dir=args.output_dir, dependency_label=args.dependency_label,
        seed=args.seed, jitter_ms=args.jitter_ms, parallel=args.parallel, suite=args.suite, journeys=args.journeys)
    print(json.dumps({"output": str(args.output_dir), "correct": result["summary"]["allCorrect"],
                      "revisionStable": result["revisionStable"], "qualificationEligible": False}))
    return 0 if result["summary"]["allCorrect"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
