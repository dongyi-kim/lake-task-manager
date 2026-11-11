"""Independent ledger expectations and deterministic numeric/evidence gates.

This module is host-only. Matching numbers are necessary, never sufficient for
quality; direct review of the actual Korean answer is a separate hard gate.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from html.parser import HTMLParser
import json
import math
import re

from tools.codex_mcp_eval.work_evidence import (document_complete, evidence_collection,
    has_stalled_pagination, literal_source_records, source_observed)
from .report_checks import merge_requirements, report_contract, row_identity
from .evidence_equivalence import mapping_identity_keys, unchanged_status_observed


def _at(value):
    if "T" not in value:
        value += "T00:00:00"
    return datetime.fromisoformat(value + ("+09:00" if "+" not in value else ""))


def _public_report_findings(bundle, matches):
    """Project host associations into only identifiers/claims visible in sources.

    Authored item IDs are private. Public reports number their sections; occurrence
    dates may lack a time, and an internal artifact label may be unpublished.
    Neither an unpublished label nor an exact unseen hour enters this contract.
    """
    reports = {(e.payload["document"], e.payload["version"]): e for e in bundle.oracle.events
               if e.kind == "development_report" and e.sources}
    findings = []
    for match in matches:
        event = reports[(match["document"], match["version"])]
        position = next(index for index, item in enumerate(event.payload["items"], 1)
                       if item["item_id"] == match["item_id"])
        item = event.payload["items"][position - 1]
        context = {**event.payload["report_context"], **item.get("context", {})}
        claim = match["claim"]
        source = bundle.public_sources[event.sources[0]]
        headings = re.findall(r"<h2>(\d+)\.", source["text"])
        section = int(headings[position - 1])
        artifact = claim.get("artifact")
        findings.append({"documentId": source["id"], "version": match["version"], "section": section,
            "deliverable": claim["deliverable"], "stage": claim["stage"],
            "occurredAt": claim["occurred_at"].date().isoformat() if claim["occurred_at"] else None,
            "artifact": artifact if artifact and artifact in source["text"] else None,
            "outcome": claim["outcome"] if claim["stage"] in {"test_acceptance", "final_acceptance"}
                       or claim["outcome"] == "pending" else None,
            "candidateKeys": list(match["candidate_keys"]), "matchStatus": match["status"],
            "inScope": context.get("module") == "ETL" and context.get("environment") == "prod"})
    return _unique_rows(findings)


def _unique_rows(rows):
    return [json.loads(text) for text in sorted({json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows})]


def _field_fact(event, **view):
    return {"key": event.issue_key, "field": {"epic": "Epic Link"}.get(event.payload["field"], event.payload["field"]),
            "from": event.payload["old"], "to": event.payload["value"],
            "occurredAt": event.occurred_at.isoformat(), "recordedAt": event.recorded_at.isoformat(),
            "commentSources": list(event.sources), **view}


def _require_report_status(row, bundle, key, state, at, knowledge_at):
    if at == bundle.spec.observed_until:
        row["requiredTicketInputs"].setdefault(key, {})["status"] = state["status"]
        return
    known = knowledge_at or bundle.spec.observed_until
    changes = sorted((event for event in bundle.oracle.events if event.kind == "field" and
        event.issue_key == key and event.payload["field"] == "status" and event.sources and event.recorded_at <= known),
        key=lambda event: (event.occurred_at, event.recorded_at, event.identity))
    before = [event for event in changes if event.occurred_at < at]
    after = [event for event in changes if event.occurred_at >= at]
    anchor = before[-1] if before else after[0] if after else None
    if anchor:
        row["requiredFieldFacts"].append(_field_fact(anchor, viewAt=at.isoformat(), knowledgeAt=known.isoformat(),
            valueAtView=state["status"], relation="after-transition" if before else "before-transition"))
    else:
        row["requiredTicketInputs"].setdefault(key, {})["status"] = state["status"]
        row["requiredFieldFacts"].append({"key": key, "field": "status", "unchanged": True,
            "valueAtView": state["status"], "viewAt": at.isoformat(), "knowledgeAt": known.isoformat()})


def _report_progress_row(snapshot, key, view):
    state = snapshot["issues"][key]
    progress = state["progress"]
    ambiguous = any(match["status"] != "verified" and key in match["candidate_keys"]
                    for match in snapshot["report_correspondence"])
    return {"key": key, "view": view, "jiraStatus": state["status"],
        "implementation": list(progress["reported_implementation"]),
        "testAccepted": list(progress["test_accepted_deliverables"]),
        "finalAccepted": list(progress["final_accepted_deliverables"]),
        "planned": list(progress["planned_deliverables"]),
        "acceptedEvents": None if ambiguous else progress["reported_acceptance_events"]}


def _report_requirements(bundle, snapshot, keys, at, knowledge_at, *, matches=(), progress=False):
    requirements = {"requiredSources": [source for match in matches for source in match["proof_sources"]],
                    "requiredTicketInputs": {}, "requiredFieldFacts": []}
    for key in keys:
        state = snapshot["issues"].get(key)
        if state is None:
            # Excluded module context can belong to another stream. Its report
            # finding asserts correspondence, not a Jira status or progress.
            issue = next(event for event in bundle.oracle._visible(at, knowledge_at, [key])
                         if event.kind == "issue")
            state = {"sources": list(issue.sources)}
        requirements["requiredSources"] += [source for source in state["sources"]
            if bundle.public_sources[source]["kind"] == "jira_description"]
        if progress:
            requirements["requiredSources"] += state["progress"]["sources"]
            _require_report_status(requirements, bundle, key, state, at, knowledge_at)
    return requirements


def _catalog_reports(row, bundle, snapshot, matches, at, knowledge_at, view):
    """Optional context stays inside the requested snapshots/revision policy.

    The catalog is independent of the answer and carries every added evidence
    obligation. It is host-only and never adds optional rows to subject prompts.
    """
    catalog = row.setdefault("reportCatalog", {"findings": [], "progress": []})
    indexed = {name: {row_identity(entry["row"]): entry for entry in entries}
               for name, entries in catalog.items()}
    def add(kind, public_row, requirements, **extra):
        identity = row_identity(public_row)
        if identity not in indexed[kind]:
            entry = {"row": public_row, **requirements, **extra}
            indexed[kind][identity] = entry
            catalog[kind].append(entry)
        else:
            merge_requirements(indexed[kind][identity], requirements)
    for match in matches:
        requirements = _report_requirements(bundle, snapshot, match["candidate_keys"],
                                             at, knowledge_at, matches=[match])
        for finding in _public_report_findings(bundle, [match]):
            report_source = next(source for source in match["report_sources"]
                if bundle.public_sources[source].get("kind") == "confluence_revision")
            add("findings", finding, requirements, reportSource=report_source)
    keys = {key for match in matches for key in match["candidate_keys"] if key in snapshot["issues"]}
    for key in sorted(keys):
        requirements = _report_requirements(bundle, snapshot, [key], at, knowledge_at, progress=True)
        add("progress", _report_progress_row(snapshot, key, view), requirements)


def _scope_only_activity_plan(match):
    return match["claim"]["stage"] == "plan" and "same_item_companion" in match["matched_dimensions"]


def _attach_reports(row, bundle, at, *, keys, view, knowledge_at=None, revisions=False, include_decoy=False):
    snapshot = bundle.oracle.development(at, knowledge_at=knowledge_at)
    selected = set(keys)
    if include_decoy:
        selected.add("DL-50003")
    available_matches = list(snapshot["report_correspondence"])
    matches = [match for match in available_matches if selected.intersection(match["candidate_keys"])]
    old_matches = []
    if revisions:
        for old in snapshot["superseded_report_versions"]:
            event = next(e for e in bundle.oracle.events if e.kind == "development_report"
                         and e.payload["document"] == old["document"] and e.payload["version"] == old["version"])
            old_view = bundle.oracle.development(at, knowledge_at=event.recorded_at)
            available_old = [match for match in old_view["report_correspondence"]
                             if match["document"] == old["document"] and match["version"] == old["version"]]
            available_matches += available_old
            old_matches += [match for match in available_old if selected.intersection(match["candidate_keys"])]
    _catalog_reports(row, bundle, snapshot, available_matches, at, knowledge_at, view)
    all_matches = matches + old_matches
    row["reportFindings"] = _unique_rows(row["reportFindings"] + _public_report_findings(
        bundle, [match for match in all_matches if not _scope_only_activity_plan(match)]))
    contract = row.setdefault("reportExpectations", {"taskProgress": {}, "knowledgeComparisons": {},
        "excludedTaskKeys": [], "correspondenceEvidence": "module/environment/function/deliverable/participant/release/period",
        "privateIntentIsEvidence": False, "titleSimilarityIsEnough": False,
        "acceptedEventsMeaning": "Unique reported acceptance events in this evidence/view; null when attribution or multiplicity is unresolved. Zero reported acceptances does not prove zero actual work or full completion."})
    contract["optionalActivityPlans"] = _unique_rows(contract.get("optionalActivityPlans", []) +
        _public_report_findings(bundle, [match for match in all_matches if _scope_only_activity_plan(match)]))
    for match in all_matches:
        row["requiredSources"] += match["proof_sources"]
        row["keys"] += match["candidate_keys"]
    for key in keys:
        state = snapshot["issues"][key]
        progress = state["progress"]
        row["requiredSources"] += [source for source in state["sources"]
                                    if bundle.public_sources[source]["kind"] == "jira_description"] + progress["sources"]
        _require_report_status(row, bundle, key, state, at, knowledge_at)
        contract["taskProgress"][key] = {field: deepcopy(progress[field]) for field in (
            "state", "known", "reported_implementation", "test_accepted_deliverables", "final_accepted_deliverables",
            "planned_deliverables", "reported_acceptance_events", "duplicate_status", "overall_percent", "conflicts")}
        row["reportProgress"].append(_report_progress_row(snapshot, key, view))
        row["keys"].append(key)
    if include_decoy:
        contract["excludedTaskKeys"].append("DL-50003")
    row["requiredSearchClass"] = "confluence"
    row["mustDiscloseUncertainty"] |= any(match["status"] != "verified" for match in matches) or any(
        snapshot["issues"][key]["progress"]["duplicate_status"] == "unknown" or
        snapshot["issues"][key]["progress"]["conflicts"] for key in keys)
    row["distinctions"] += ["public correspondence vs private intent", "many-to-many report evidence",
        "implementation vs test acceptance vs final acceptance vs plan", "occurrence vs reporting period vs publication",
        "repeated reporting vs new acceptance", "superseded revision vs reconstructed past", "Jira status vs business acceptance"]
    return snapshot


def _optional_period_empty_reports(row, bundle, snapshot, *, keys, at, knowledge_at, view):
    """Allow selected public Task context with no attributed report claim in this view.

    Zero counts reported acceptance events, never actual work or completion.
    Ambiguous correspondence remains a claim and cannot become an empty row.
    """
    profiles = {event.issue_key for event in bundle.oracle._visible(at, knowledge_at, keys)
                if event.kind == "issue" and event.payload.get("report_context")
                and event.payload["type"] == "Task"}
    attributed = {key for match in snapshot["report_correspondence"] for key in match["candidate_keys"]}
    catalog = row.setdefault("reportCatalog", {"findings": [], "progress": []})
    existing = {row_identity(entry["row"]) for entry in catalog["progress"]}
    for key in sorted((set(keys) & profiles & snapshot["issues"].keys()) - attributed):
        public_row = _report_progress_row(snapshot, key, view)
        if public_row["acceptedEvents"] != 0 or any(public_row[field] for field in (
                "implementation", "testAccepted", "finalAccepted", "planned")):
            continue
        if row_identity(public_row) not in existing:
            catalog["progress"].append({"row": public_row, **_report_requirements(
                bundle, snapshot, [key], at, knowledge_at, progress=True)})


def _optional_current_reports(row, bundle, snapshot, *, keys, revisions=False, revision_cache=None):
    """Allow labelled current context without adding period rows or mandatory reads."""
    selected = set(keys)
    matches = [match for match in snapshot["report_correspondence"]
               if selected.intersection(match["candidate_keys"])]
    cache = revision_cache if revision_cache is not None else {}
    if revisions:
        for old in snapshot["superseded_report_versions"]:
            event = next(event for event in bundle.oracle.events if event.kind == "development_report"
                and event.payload["document"] == old["document"] and event.payload["version"] == old["version"])
            if event.recorded_at not in cache:
                cache[event.recorded_at] = bundle.oracle.development(bundle.spec.observed_until,
                                                                    knowledge_at=event.recorded_at)
            matches += [match for match in cache[event.recorded_at]["report_correspondence"]
                if match["document"] == old["document"] and match["version"] == old["version"]
                and selected.intersection(match["candidate_keys"])]
    temporary = {}
    _catalog_reports(temporary, bundle, snapshot, matches, bundle.spec.observed_until, None, "current")
    catalog = row.setdefault("reportCatalog", {"findings": [], "progress": []})
    for kind, entries in temporary["reportCatalog"].items():
        existing = {row_identity(entry["row"]) for entry in catalog[kind]}
        # Findings have no view field. An identical required period finding must
        # retain its existing proof, rather than silently acquiring a mandatory
        # current-status read just because optional context was made available.
        catalog[kind] += [entry for entry in entries if row_identity(entry["row"]) not in existing]


def _attach_lifecycle_expectations(row, lifecycle):
    """Keep substantive progress review/proof without unsolicited scalar metrics."""
    row["lifecycleExpectations"] = deepcopy(lifecycle)
    row["keys"] = sorted(set(row["keys"]) | set(lifecycle))
    for progress in lifecycle.values():
        row["requiredSources"] += progress["sources"]


def _native_progress_expectations(bundle, snapshot, epic_keys, arithmetic_inputs, *, wbs=None):
    """Independent current native values; never a subject-output or product reduction."""
    epics = {}
    for key in epic_keys:
        children = [(child, state) for child, state in snapshot["issues"].items()
                    if state.get("epic") == key and state["type"] != "Sub-Task"]
        eligible = [(child, state) for child, state in children
                    if state["type"] not in {"Bug", "Ops", "운영"}
                    and bundle.world.issues[child]["component"] != "사용자 VoC"]
        value = snapshot["epics"][key]
        epics[key] = {"name": bundle.world.issues[key]["summary"], "doneSp": value["sp_done"],
            "totalSp": value["sp_total"], "progressPct": value["percent"],
            "mockSp": round(sum(1 if state["sp"] is None else state["sp"] for child, state in eligible
                                if "mock" in bundle.world.issues[child].get("labels", [])), 1),
            "countedIssues": len(eligible), "excludedIssues": len(children) - len(eligible)}
    contract = {"view": "current", "epics": epics, "arithmeticKeys": sorted(arithmetic_inputs),
                "arithmeticTicketInputs": deepcopy(arithmetic_inputs)}
    if wbs is not None:
        row = snapshot["wbs"][wbs]
        contract["wbs"] = {"id": wbs, "weights": deepcopy(row["epic_weights"]), "progressPct": row["percent"]}
    return contract


def _fresh_complete_native_data(call):
    result = call.get("result") or {}
    if not isinstance(result, dict):
        return None
    cache, data = result.get("cache"), result.get("data")
    if (call.get("isError") or result.get("ok") is not True or result.get("partial") is not False
            or type(result.get("missing")) is not int or result["missing"] != 0
            or not isinstance(cache, dict) or cache.get("available") is not True
            or cache.get("state") != "fresh" or cache.get("invalidated") is not False
            or not isinstance(data, dict) or data.get("partial") or data.get("missing")
            or data.get("missingKeys") or data.get("errors") or data.get("hasMore")
            or data.get("complete") is False or data.get("sourceComplete") is False or data.get("incompleteReason")):
        return None
    return data


def _native_progress_values_match(data, wanted):
    return (isinstance(data, dict) and data.get("name") == wanted["name"]
        and all(type(data.get(name)) in (int, float) and _number_equal(data[name], wanted[name])
                for name in ("doneSp", "totalSp", "progressPct", "mockSp")))


def _dashboard_progress_matches(call, contract):
    # The actual MCP contract requires kind; there is no implicit WBS default.
    if call.get("name") != "get_dashboard" or (call.get("arguments") or {}).get("kind") != "wbs":
        return False
    data = _fresh_complete_native_data(call)
    if data is None or not isinstance(data.get("epics"), dict):
        return False
    for key, wanted in contract["epics"].items():
        entry = data["epics"].get(key)
        if not _native_progress_values_match(entry, wanted) or entry.get("key") != key:
            return False
    wanted_wbs = contract.get("wbs")
    if wanted_wbs is not None:
        rows = data.get("wbs")
        if not isinstance(rows, list):
            return False
        rows = [row for row in rows if isinstance(row, dict) and row.get("id") == wanted_wbs["id"]]
        if (len(rows) != 1 or type(rows[0].get("progressPct")) not in (int, float)
                or not _number_equal(rows[0]["progressPct"], wanted_wbs["progressPct"])):
            return False
        parts = rows[0].get("epics")
        if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
            return False
        indexed = {part.get("epicKey"): part for part in parts if isinstance(part.get("epicKey"), str)}
        if len(indexed) != len(parts) or indexed.keys() != wanted_wbs["weights"].keys():
            return False
        if not all(type(indexed[key].get("weight")) in (int, float)
                   and type(indexed[key].get("epicPct")) in (int, float)
                   and _number_equal(indexed[key]["weight"], weight)
                   and _number_equal(indexed[key]["epicPct"], contract["epics"][key]["progressPct"])
                   for key, weight in wanted_wbs["weights"].items()):
            return False
    return True


def _native_progress_route(calls, contract, field_ids):
    epics = contract.get("epics", {})
    if contract.get("view") != "current" or not epics:
        return False
    complete = [call for call in calls if _fresh_complete_native_data(call) is not None]
    if any(_dashboard_progress_matches(call, contract) for call in complete):
        return True
    identities = {key: {"summary": wanted["name"], "type": "Epic"} for key, wanted in epics.items()}
    # Reuse every already-supported typed metadata location, including search
    # rows. The existing extractor never promotes arbitrary references/prose.
    if not ticket_inputs_match(complete, identities, field_ids):
        return False
    for key, wanted in epics.items():
        def matches(call):
            argument = (call.get("arguments") or {}).get("key")
            if (call.get("name") != "get_epic_progress" or not isinstance(argument, str)
                    or argument.strip().upper() != key):
                return False
            data = call["result"]["data"]
            return (_native_progress_values_match(data, wanted)
                and all(type(data.get(name)) is int and data[name] >= 0 and data[name] == wanted[name]
                        for name in ("countedIssues", "excludedIssues")))
        if not any(matches(call) for call in complete):
            return False
    return True


def _native_progress_scope(expected):
    contract = expected["nativeProgressEvidence"]
    keys = set(expected["keys"]) - set(contract["arithmeticKeys"])
    # Native arithmetic never discharges report/context membership or proof.
    keys.update(row["key"] for row in expected["reportProgress"])
    keys.update(key for row in expected["reportFindings"] for key in row["candidateKeys"])
    inputs = deepcopy(expected["requiredTicketInputs"])
    for key, arithmetic in contract["arithmeticTicketInputs"].items():
        inputs[key] = {name: value for name, value in inputs.get(key, {}).items()
                       if name not in arithmetic or value != arithmetic[name]}
        if not inputs[key]:
            inputs.pop(key)
    for row in expected["reportProgress"]:
        if row["view"] == "current":
            inputs.setdefault(row["key"], {})["status"] = row["jiraStatus"]
    return keys, inputs


def build_expectations(bundle):
    oracle, events = bundle.oracle, bundle.oracle.events
    start, end = bundle.spec.months[0]
    now = bundle.spec.observed_until
    issues = {event.issue_key: event for event in events if event.kind == "issue"}
    result = {}
    current_report_snapshot, current_revision_cache = None, {}

    def current_report_context(row, *, revisions=False):
        nonlocal current_report_snapshot
        if current_report_snapshot is None:
            current_report_snapshot = oracle.development(now)
        _optional_current_reports(row, bundle, current_report_snapshot, keys=row["keys"],
                                  revisions=revisions, revision_cache=current_revision_cache)

    def new(family, variant, keys=(), sources=(), *, distinctions=(), unknown=False):
        row = {"keys": sorted(set(keys)), "measurements": [], "denominators": [],
               "requiredSources": sorted(set(sources)), "mustDiscloseUncertainty": unknown,
               "distinctions": list(distinctions), "progress": None, "reportedTotal": None,
               "reportFindings": [], "reportProgress": [], "requiredTicketInputs": {}, "requiredFieldFacts": []}
        result[f"ETL-F{family:02}-{variant}"] = row
        return row

    def measure(row, stream, scope, metric, value, unit):
        row["measurements"].append(dict(stream=stream, scope=scope, metric=metric, value=value, unit=unit))

    def proof(keys, *, kinds=None, field_names=None):
        keys = set(keys)
        return sorted({source for event in events if event.issue_key in keys
                       and (kinds is None or event.kind in kinds) for source in event.sources
                       if event.kind != "field" or (event.payload["field"] != "priority" and
                           (field_names is None or event.payload["field"] in field_names))
                       if ":pre:" not in event.identity and ":post:" not in event.identity})

    def ticket_inputs(keys, fields):
        expected = {}
        for key in keys:
            public = bundle.world.issues[key]
            values = {"sp": public["sp"], "status": public["statusName"], "type": public["type"],
                      "epic": public["epicKey"], "summary": public["summary"], "component": public["component"],
                      "created_date": public["created"].isoformat(),
                      "resolved_date": public["resolved"].isoformat() if public["resolved"] else None}
            expected[key] = {field: values[field] for field in fields}
        return expected

    for family, spans in ((1, (("2026-10-15", "2026-10-16"), ("2026-10-24", "2026-10-25"))),):
        for variant, (left, right) in zip(("normal", "boundary"), spans):
            a, b = _at(left), _at(right)
            records = [e for e in events if e.stream in {"development", "operations", "simple_voc"}
                       and e.issue_key and e.sources and a <= e.recorded_at < b]
            row = new(family, variant, [e.issue_key for e in records],
                [s for e in records for s in e.sources], distinctions=("recordedAt vs occurredAt", "activity vs execution"))
            report_keys = ["DL-21740", "DL-21742", "DL-21743", "DL-21744"] if variant == "normal" else [
                "DL-21743", "DL-21745", "DL-21747", "DL-21749"]
            report_snapshot = _attach_reports(row, bundle, b if variant == "normal" else _at("2026-11-01"),
                keys=report_keys, view="as-of-" + right if variant == "normal" else "2026-10-end",
                revisions=variant == "boundary", include_decoy=variant == "boundary")
            current_report_context(row, revisions=variant == "boundary")
            for stream in ("development", "operations", "simple_voc"):
                observed = {e.issue_key for e in records if e.stream == stream}
                if stream == "development":
                    observed |= {key for match in report_snapshot["report_correspondence"]
                        if match["status"] == "verified" and match["claim"]["stage"] != "plan" and
                        match["claim"]["occurred_at"] is not None and a <= match["claim"]["occurred_at"] < b
                        for key in match["candidate_keys"] if bundle.world.issues[key]["component"] == "ETL"}
                measure(row, stream, left if family == 1 else left + "/" + right, "activity_tickets",
                        None if stream == "development" and variant == "boundary" else len(observed), "tickets")
                row["keys"] += list(observed)
            _optional_period_empty_reports(row, bundle, report_snapshot, keys=row["keys"],
                at=b if variant == "normal" else _at("2026-11-01"), knowledge_at=None,
                view="as-of-" + right if variant == "normal" else "2026-10-end")
    for variant, keys in (("normal", ["DL-21743", "DL-21744"]),
                          ("boundary", ["DL-21745", "DL-21747", "DL-21749"])):
        row = new(2, variant, distinctions=("weekly report vs actual occurrence", "ambiguous matching and undated duplicate"))
        report_at = _at("2026-10-26" if variant == "normal" else "2026-11-01")
        report_view = "as-of-2026-10-26" if variant == "normal" else "2026-10-end"
        report_snapshot = _attach_reports(row, bundle, report_at, keys=keys,
                        view="as-of-2026-10-26" if variant == "normal" else "2026-10-end")
        _optional_period_empty_reports(row, bundle, report_snapshot, keys=row["keys"],
                                      at=report_at, knowledge_at=None, view=report_view)
        current_report_context(row)
        measure(row, "development", "2026-10-19/2026-10-26", "activity_tickets", 2 if variant == "normal" else None, "tickets")
    for variant, month in (("normal", 0), ("boundary", 11)):
        a, b = bundle.spec.months[month]
        dev = oracle.active_development(a, b)
        ops = [k for k, e in issues.items() if e.stream == "operations" and a <= e.occurred_at < b]
        voc = [k for k, e in issues.items() if e.stream == "simple_voc" and a <= e.occurred_at < b]
        keys = dev + ops + voc
        row = new(3, variant, keys,
                  distinctions=("carryover vs new requests", "three separate streams"))
        row["requiredTicketInputs"] = ticket_inputs(keys, ("summary", "component", "type", "created_date", "resolved_date", "status"))
        scope = a.strftime("%Y-%m")
        measure(row, "development", scope, "active_tickets", len(dev), "tickets")
        measure(row, "operations", scope, "request_tickets", len(ops), "tickets")
        measure(row, "simple_voc", scope, "request_tickets", len(voc), "tickets")
        if variant == "boundary":
            lifecycle_keys = {key for keys in bundle.manifest["development_lifecycle_coverage"].values() for key in keys}
            lifecycle = {key: value["progress"] for key, value in oracle.development(b)["issues"].items() if key in lifecycle_keys}
            _attach_lifecycle_expectations(row, lifecycle)
            row["mustDiscloseUncertainty"] = True
            row["distinctions"] += ["created vs started", "verified deliverables vs arbitrary overall percent",
                "substantive activity vs administrative updated", "reopened acceptance withdrawal",
                "historical contributor vs current assignee", "dependency wait vs implementation completion"]
            report_keys = sorted({key for match in oracle.development(b)["report_correspondence"] for key in match["candidate_keys"]
                                  if bundle.world.issues[key]["component"] == "ETL"})
            report_snapshot = _attach_reports(row, bundle, b, keys=report_keys, view="2026-10-end", revisions=True, include_decoy=True)
            _optional_period_empty_reports(row, bundle, report_snapshot, keys=row["keys"],
                                          at=b, knowledge_at=None, view="2026-10-end")
        current_report_context(row, revisions=variant == "boundary")

    for family in (4, 5, 6, 7):
        for variant in ("normal", "boundary"):
            at = _at("2026-01-15") if ((family in (4, 5) and variant == "normal")
                or (family in (6, 7) and variant == "boundary")) else now
            snapshot = oracle.development(at)
            if family == 4:
                weights = snapshot["wbs"]["ETL-1"]["epic_weights"]
                keys = [k for k, r in snapshot["issues"].items() if k in weights or r["epic"] in weights]
            elif family == 5:
                epic_key = "DL-20000" if variant == "normal" else "DL-20004"
                keys = [k for k, r in snapshot["issues"].items() if k == epic_key or r["epic"] == epic_key]
            elif family == 6:
                keys = [k for k, r in snapshot["issues"].items() if k == "DL-21000" or r["parent"] == "DL-21000"]
            else:
                keys = ["DL-21001"]
            row = new(family, variant, keys, proof(keys, kinds={"issue", "field"}),
                      distinctions=("SP-weighted progress vs child count", "parent status vs child completion", "historical hierarchy"))
            if family in {4, 5}:
                task_keys = [key for key in keys if snapshot["issues"][key]["type"] != "Epic"]
                row["requiredTicketInputs"] = ticket_inputs(task_keys, ("sp", "type", "epic", "status", "component") if at == now else ("sp", "type", "epic", "created_date"))
                row["requiredSources"] = proof(task_keys, kinds={"field"}, field_names={"status", "parent", "epic"}) if at != now else []
                if at == now:
                    row["nativeProgressEvidence"] = _native_progress_expectations(bundle, snapshot,
                        list(weights) if family == 4 else [epic_key], row["requiredTicketInputs"],
                        wbs="ETL-1" if family == 4 else None)
            if family == 4:
                mapping = [e for e in events if e.kind == "mapping" and e.payload["wbs"] == "ETL-1" and e.occurred_at < at]
                required_mapping = max(mapping, key=lambda e: e.occurred_at)
                row["requiredSources"] += list(required_mapping.sources)
                identity_mappings = [required_mapping]
                if variant == "boundary":
                    prior = oracle.development(_at("2026-01-15"))["wbs"]["ETL-1"]["epic_weights"]
                    row["mappingComparison"] = {"2026-01-15": prior, "current": weights}
                    row["requiredSources"] += [source for event in mapping if event.payload["version"] == 1 for source in event.sources]
                    identity_mappings += [event for event in mapping if event.payload["version"] == 1]
                row["mappingIdentityEvidence"] = [{"source": source,
                    "documentId": bundle.public_sources[source]["id"], "version": event.payload["version"],
                    "wbs": event.payload["wbs"], "epicKeys": sorted(event.payload["epics"])}
                    for event in identity_mappings for source in event.sources]
                value = snapshot["wbs"]["ETL-1"]["percent"]
                scope = "ETL-1@" + ("2026-01-15" if at != now else "current")
                weighted = (sum(snapshot["epics"][key]["percent"] * weight for key, weight in weights.items())
                            if value is not None else None)
                row["mustDiscloseUncertainty"] = value is None
                row["denominators"] = [dict(scope=scope, metric="SP-weighted epic progress",
                    numerator=weighted, denominator=sum(weights.values()), unit="weighted percent")]
            elif family == 5:
                r = snapshot["epics"][epic_key]
                value = r["percent"]
                scope = epic_key + "@" + ("2026-01-15" if at != now else "current")
                row["denominators"] = [dict(scope=scope, metric="SP progress", numerator=r["sp_done"],
                    denominator=r["sp_total"], unit="SP")]
            elif family == 6:
                children = [r for r in snapshot["issues"].values() if r["parent"] == "DL-21000"]
                done = sum(r["status"] in {"Resolved", "Closed"} for r in children)
                value = 100 * done / len(children) if children else None
                scope = "DL-21000@" + ("2026-01-15" if at != now else "current")
                row["denominators"] = [dict(scope=scope, metric="child completion count", numerator=done,
                    denominator=len(children), unit="tickets")]
                row["progress"] = dict(done=done, total=len(children), percent=value) if children else None
                row["parentStatus"] = snapshot["issues"]["DL-21000"]["status"]
                if variant == "boundary":
                    # Public parent membership audit proves one historical
                    # child; its independently denied history cannot prove done.
                    row["keys"] = sorted(set(keys + [k for k, r in oracle.development(now)["issues"].items()
                                                       if r["parent"] == "DL-21000"]))
                    row["requiredSources"] = proof(["DL-21000"], kinds={"issue", "field"})
                    row["requiredSources"] += [s for e in events if e.identity == "DL-21000:membership-audit" for s in e.sources]
                    row["progress"], value = None, None
                    row["denominators"][0]["numerator"] = None
                    row["mustDiscloseUncertainty"], row["deniedKey"] = True, "DL-21001"
            else:
                row["historicalState"] = deepcopy(snapshot["issues"]["DL-21001"])
                row["reportedTotal"] = 1
                continue
            measure(row, "development", scope, "percent", value, "percent")
            if family == 5 and variant == "boundary":
                report_keys = [key for key in keys if snapshot["issues"][key]["progress"]["report_correspondence"]]
                _attach_reports(row, bundle, now, keys=report_keys, view="current")

    row = new(6, "normal", distinctions=("named Task deliverables vs unknown whole-task percentage",))
    _attach_reports(row, bundle, now, keys=["DL-21744"], view="current")

    units = {"request_tickets": "tickets", "completed_tickets": "tickets", "requested_targets": "targets",
             "completed_unique_tables": "tables", "work_events": "events"}

    def operations(family, variant, keys, scope, *, interval=None, requested_metrics=None):
        data = oracle.operations(*(interval or (start, end)), issue_keys=keys)
        row = new(family, variant, keys, proof(keys), unknown=not data["complete"],
            distinctions=("request tickets vs requested targets vs completed tickets", "unique tables vs execution events",
                          "partial completion", "environment and verified rename", "duplicate reporting and corrections"))
        for metric in units if requested_metrics is None else requested_metrics:
            measure(row, "operations", scope, metric, data[metric], units[metric])
        row["operations"] = data
        return row

    customer_keys = ["DL-31000", "DL-31001", "DL-31002"]
    operations(8, "normal", customer_keys, "customers@2025-11")
    load_keys = [k for k, e in issues.items() if e.stream == "operations" and start <= e.occurred_at < end
                 and e.payload["operation"] == "load"]
    operations(8, "boundary", load_keys, "load@2025-11", requested_metrics=(
        "request_tickets", "requested_targets", "completed_unique_tables", "work_events"))
    # A missing proof can occur even when this specific cohort has no missing
    # execution: its unresolved second target is explicitly not a completed table.
    result["ETL-F08-boundary"]["mustDiscloseUncertainty"] = True
    for family, kinds in ((9, ("modify", "reload")), (10, ("suspend", "delete"))):
        for variant in ("normal", "boundary"):
            cohorts = {}
            for operation in (kinds if variant == "boundary" or family == 10 else kinds[:1]):
                cohorts[operation] = [k for k, e in issues.items() if e.stream == "operations"
                    and e.payload["operation"] == operation and start <= e.occurred_at < end
                    and (variant == "boundary" or _at("2025-11-14") <= e.occurred_at < _at("2025-11-17"))]
            row = new(family, variant, [k for ks in cohorts.values() for k in ks],
                      proof([k for ks in cohorts.values() for k in ks]),
                      distinctions=("modify vs reload vs suspend vs delete", "execution vs period-end inventory"))
            for operation, keys in cohorts.items():
                # Boundary asks November activity; normal selects requests in a
                # short window and asks their actual outcomes as of observation.
                data = oracle.operations(start, end if variant == "boundary" else now, issue_keys=keys)
                row["mustDiscloseUncertainty"] |= not data["complete"]
                scope = operation + "@" + ("2025-11" if variant == "boundary" else "2025-11-14/2025-11-17")
                for metric, unit in units.items():
                    measure(row, "operations", scope, metric, data[metric], unit)
            if family == 10 and variant == "boundary":
                state = oracle.asset_state(end)
                unknown_assets = any(e.payload.get("evidence_missing") and e.issue_key in issues
                    and issues[e.issue_key].payload.get("operation") in {"load", "suspend", "delete"}
                    and e.occurred_at < end for e in events)
                measure(row, "operations", "2025-11-end", "active_tables",
                        None if unknown_assets else len(state["active_tables"]), "tables")
                row["knownActiveTables"] = state["active_tables"]
                row["requiredSources"] += [e.sources[0] for e in events if e.kind == "inventory"]
                # Opening inventory and *all* asset-changing executions are
                # required, beyond the stop/delete request cohort.
                row["requiredSources"] += [s for e in events if e.kind in {"execution", "rename", "correction"}
                                           and e.occurred_at < end for s in e.sources]
    for variant, at in (("normal", end), ("boundary", bundle.spec.months[1][1])):
        state = oracle.asset_state(at)
        edges = [edge for edge in state["federation_edges"] if edge[0] in {"polaris.prod.sales", "polaris.prod.marketing"}]
        relevant = [e for e in events if e.kind == "execution" and e.payload["execution_ref"].startswith("FED-SALES")
                    and e.occurred_at < at]
        row = new(11, variant, [e.issue_key for e in relevant], [s for e in relevant for s in e.sources],
                  distinctions=("Polaris view vs external table vs many-to-many edge", "temporal detach"))
        scope = "sales-marketing@" + ("2025-11-end" if variant == "normal" else "2025-12-end")
        for metric, value, unit in (("views", len({a for a, b in edges}), "views"),
            ("external_tables", len({b for a, b in edges}), "tables"), ("federation_edges", len(edges), "edges")):
            measure(row, "operations", scope, metric, value, unit)
        row["edges"] = edges
    for variant, month in (("normal", 0), ("boundary", 11)):
        a, b = bundle.spec.months[month]
        keys = [k for k, e in issues.items() if e.stream == "simple_voc" and a <= e.occurred_at < b]
        row = new(12, variant, keys, proof(keys, kinds={"issue", "note"}),
                  distinctions=("VoC table mention vs operational execution", "linked issue vs same event"))
        for metric, value, unit in (("request_tickets", len(keys), "tickets"), ("executed_operations", 0, "events")):
            measure(row, "simple_voc", a.strftime("%Y-%m"), metric, value, unit)
        if variant == "boundary":
            resolved = oracle.simple_voc(a, b)
            measure(row, "simple_voc", a.strftime("%Y-%m"), "resolved_requests", resolved["resolved_requests"], "tickets")
            row["mustDiscloseUncertainty"] = resolved["resolved_requests"] is None
            row["unknownRequestKeys"] = resolved["unknown_request_keys"]
            row["knownResolvedRequests"] = resolved["known_resolved_requests"]
    for variant in ("normal", "boundary"):
        ops = customer_keys if variant == "boundary" else customer_keys[:1]
        row = operations(13, variant, ops, "linked-customers@2025-11")
        linked = [f"DL-{41000 + int(k[3:]) - 31000}" for k in ops]
        dev = [f"DL-{21000 + int(k[3:]) - 31000}" for k in ops]
        row["keys"] = sorted(set(row["keys"] + linked + dev))
        row["requiredSources"] += proof(linked + dev, kinds={"issue", "note"})
        row["distinctions"] += ["VoC→development→operations is not three executions"]
    row = new(14, "normal", ["DL-31003"], proof(["DL-31003"]),
              distinctions=("knowledge then vs reconstructed past", "published revision vs Jira truth"))
    for scope, knowledge in (("known-at-2025-12-01", end), ("reconstructed-now", None)):
        data = oracle.operations(start, end, knowledge_at=knowledge, issue_keys=["DL-31003"])
        for metric in ("completed_unique_tables", "work_events"):
            measure(row, "operations", scope, metric, data[metric], units[metric])
    for version in (1, 2):
        row["requiredSources"] += oracle.published_report("etl-monthly-2025-11", version=version)["sources"]
    row = new(14, "boundary", unknown=True,
              distinctions=("known then vs reconstructed past vs current", "late publication and revision", "Jira/report contradiction"))
    report_keys = ["DL-21743", "DL-21744", "DL-21748"]
    cutoff = _at("2026-11-01")
    then = _attach_reports(row, bundle, cutoff, knowledge_at=cutoff, keys=report_keys, view="known-2026-11-01")
    reconstructed = _attach_reports(row, bundle, cutoff, keys=report_keys, view="reconstructed-2026-11-01", revisions=True)
    _attach_reports(row, bundle, now, keys=report_keys, view="current", revisions=True)
    fields = ("reported_implementation", "test_accepted_deliverables", "final_accepted_deliverables", "planned_deliverables", "reported_acceptance_events")
    row["reportExpectations"]["knowledgeComparisons"] = {key: {
        "knownThen": {field: deepcopy(then["issues"][key]["progress"][field]) for field in fields},
        "reconstructedNow": {field: deepcopy(reconstructed["issues"][key]["progress"][field]) for field in fields}}
        for key in report_keys}
    for row in result.values():
        required = set(row["requiredSources"])
        for event in events:
            if event.kind == "field" and required.intersection(event.sources):
                row["requiredFieldFacts"].append(_field_fact(event))
                required.difference_update(event.sources)
        row["requiredSources"] = list(required)
        row["requiredSources"] = sorted(set(row["requiredSources"]))
        row["requiredFieldFacts"] = _unique_rows(row["requiredFieldFacts"])
        row["keys"] = sorted(set(row["keys"]))
        row["reportProgress"] = _unique_rows(row["reportProgress"])
        row["distinctions"] = sorted(set(row["distinctions"]))
    return result


def _number_equal(actual, wanted):
    if wanted is None:
        return actual is None
    return type(actual) in (int, float) and math.isfinite(actual) and math.isclose(actual, wanted, rel_tol=1e-8, abs_tol=1e-6)


def _rows_match(actual, expected, identity, numeric):
    if not isinstance(actual, list) or len(actual) != len(expected):
        return False
    index = {tuple(row.get(k) for k in identity): row for row in actual if isinstance(row, dict)}
    if len(index) != len(actual):
        return False
    return all((found := index.get(tuple(row.get(k) for k in identity))) is not None
               and all(_number_equal(found.get(k), row[k]) for k in numeric) for row in expected)


def _denominators_match(actual, expected):
    """Only the two public SP metric labels are equivalent; units distinguish them."""
    def normalized(rows):
        if not isinstance(rows, list):
            return rows
        return [{**row, "metric": "SP progress"} if isinstance(row, dict) and
                row.get("metric") in {"SP progress", "SP-weighted epic progress"} else row for row in rows]
    return _rows_match(normalized(actual), normalized(expected), ("scope", "metric", "unit"), ("numerator", "denominator"))


def _report_rows_match(actual, expected):
    if not isinstance(actual, list) or len(actual) != len(expected):
        return False
    def normalized(row):
        if not isinstance(row, dict):
            return None
        row = deepcopy(row)
        for name in ("candidateKeys", "implementation", "testAccepted", "finalAccepted", "planned"):
            if name in row:
                values = row[name]
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values) or len(values) != len(set(values)):
                    return None
                row[name] = sorted(values)
        return json.dumps(row, ensure_ascii=False, sort_keys=True)
    actual_rows, expected_rows = [normalized(row) for row in actual], [normalized(row) for row in expected]
    return None not in actual_rows and len(set(actual_rows)) == len(actual_rows) and set(actual_rows) == set(expected_rows)


def _complete_report_search(calls):
    groups = {}
    for call in calls:
        if call.get("name") != "search_confluence_documents":
            continue
        data = (call.get("result") or {}).get("data") or {}
        canonical = data.get("canonicalCql")
        if not isinstance(canonical, str) or not canonical:
            continue
        start, rows, total = data.get("startAt"), data.get("documents"), data.get("total")
        if type(start) is not int or start < 0 or not isinstance(rows, list):
            continue
        if start == 0:
            groups[canonical] = {"rows": {}, "total": total, "valid": True, "ended": False}
        state = groups.setdefault(canonical, {"rows": {}, "total": total, "valid": False, "ended": False})
        end = start + len(rows)
        state["valid"] &= (data.get("returned") == len(rows) and state["total"] == total and
            ((data.get("hasMore") is True and bool(rows) and data.get("nextStartAt") == end) or
             (data.get("hasMore") is False and data.get("nextStartAt") is None and (type(total) is not int or end == total))))
        for index, item in enumerate(rows, start):
            identity = item.get("id") if isinstance(item, dict) else None
            if not identity or index in state["rows"] and state["rows"][index] != identity:
                state["valid"] = False
            state["rows"][index] = identity
        state["ended"] |= data.get("hasMore") is False
    return any(state["valid"] and state["ended"] and set(state["rows"]) == set(range(len(state["rows"])))
               and len(set(state["rows"].values())) == len(state["rows"]) for state in groups.values())


def _normalized_public_text(value):
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
        def handle_data(self, data):
            self.parts.append(data)
        def handle_starttag(self, tag, attrs):
            if tag in {"p", "div", "br", "li", "tr", "td", "h1", "h2", "h3"}:
                self.parts.append(" ")
        def handle_endtag(self, tag):
            if tag in {"p", "div", "li", "tr", "td", "h1", "h2", "h3"}:
                self.parts.append(" ")
    parser = Text()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def _complete_epic_tree(call):
    """Current, complete native Epic rows are typed evidence, not quoted prose."""
    result = call.get("result") or {}
    if call.get("name") != "get_epic" or call.get("isError") or result.get("ok") is not True:
        return None
    cache, data = result.get("cache"), result.get("data")
    if not isinstance(cache, dict) or not isinstance(data, dict):
        return None
    if (result.get("partial") is not False or type(result.get("missing")) is not int or result["missing"] != 0
            or cache.get("available") is not True or cache.get("state") != "fresh" or cache.get("invalidated") is not False
            or data.get("partial") or data.get("errors") or data.get("missing")
            or data.get("missingKeys") or data.get("hasMore") or data.get("complete") is False):
        return None
    epic = data.get("epic")
    owner = epic.get("key") if isinstance(epic, dict) else None
    requested = (call.get("arguments") or {}).get("key")
    if (not isinstance(owner, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", owner)
            or not isinstance(requested, str) or owner != requested.strip().upper()):
        return None
    tree = data.get("tree")
    if not isinstance(tree, list):
        return None
    seen = set()
    for row in tree:
        if (not isinstance(row, dict) or not isinstance(row.get("key"), str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", row["key"]) or row["key"] in seen
                or not all(name in row for name in ("sp", "type", "statusName", "statusCat", "component"))
                or not isinstance(row["type"], str) or not row["type"] or row["type"] in {"Epic", "Sub-Task"}
                or not isinstance(row["statusName"], str) or not row["statusName"]
                or not isinstance(row["statusCat"], str) or row["statusCat"] not in {"todo", "inprogress", "done"}
                or row["component"] is not None and not isinstance(row["component"], str)
                or row["sp"] is not None and (type(row["sp"]) not in (int, float) or not math.isfinite(row["sp"]))):
            return None
        seen.add(row["key"])
    return owner, tree


def observed_ticket_inputs(calls, field_ids):
    """Typed metadata can prove arithmetic inputs without downloading a body."""
    rows = {}
    def day(value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        try:
            parsed = datetime.fromisoformat(value)
            return (parsed.astimezone(_at("2000-01-01").tzinfo) if parsed.tzinfo else parsed).date().isoformat()
        except ValueError:
            return value
    for call in calls:
        if call.get("isError") or (call.get("result") or {}).get("ok") is not True:
            continue
        epic_tree = _complete_epic_tree(call)
        if epic_tree is not None:
            owner, tree = epic_tree
            for record in tree:
                metadata = {name: record[name] for name in ("sp", "type", "component")}
                metadata.update(status=record["statusName"], epic=owner)
                rows.setdefault(record["key"], []).append(metadata)
        for record in literal_source_records(call, "ticket"):
            key = record.get("key")
            if not key:
                continue
            fields = record.get("fields")
            if isinstance(fields, dict):
                metadata = {name: fields[name] for name in ("summary",) if name in fields}
                if "status" in fields:
                    metadata["status"] = (fields["status"] or {}).get("name")
                if "issuetype" in fields:
                    metadata["type"] = (fields["issuetype"] or {}).get("name")
                if "components" in fields:
                    metadata["component"] = next((item.get("name") for item in fields["components"] or [] if isinstance(item, dict)), None)
                for name, source in (("sp", field_ids.get("sp")), ("epic", field_ids.get("epic"))):
                    if source and source in fields:
                        metadata[name] = fields[source]
                for name, source in (("created_date", "created"), ("resolved_date", "resolutiondate")):
                    if source in fields:
                        metadata[name] = day(fields[source])
            else:
                metadata = {name: record[name] for name in ("summary", "sp", "status", "type") if name in record}
                if "epicKey" in record:
                    metadata["epic"] = record["epicKey"]
                if "components" in record:
                    metadata["component"] = next(iter(record["components"] or []), None)
                for name, source in (("created_date", "created"), ("resolved_date", "resolved")):
                    if source in record:
                        metadata[name] = day(record[source])
            rows.setdefault(key, []).append(metadata)
    return rows


def _observed_result_keys(calls, field_ids):
    """Literal ticket identities and known typed relations; never arguments/body text."""
    keys = set()
    def add(value):
        if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", value):
            keys.add(value)
    def ticket(record):
        add(record.get("key"))
        for name in ("epicKey", "parentKey"):
            add(record.get(name))
        fields = record.get("fields") or {}
        if isinstance(fields, dict):
            parent = fields.get("parent")
            if isinstance(parent, dict):
                add(parent.get("key"))
            add(fields.get(field_ids.get("epic")))
            for child in fields.get("subtasks") or []:
                if isinstance(child, dict):
                    add(child.get("key"))
    for call in calls:
        if call.get("isError") or (call.get("result") or {}).get("ok") is not True:
            continue
        for record in literal_source_records(call, "ticket"):
            ticket(record)
        epic_tree = _complete_epic_tree(call)
        if epic_tree is not None:
            owner, tree = epic_tree
            add(owner)
            for record in tree:
                ticket(record)
                for child in record.get("children") or []:
                    if isinstance(child, dict):
                        add(child.get("key"))
    return keys


def _result_keys_match(actual, expected, calls, field_ids, mapping_keys=()):
    if not isinstance(actual, list) or not all(isinstance(key, str) for key in actual) or actual != sorted(set(actual)):
        return False
    required, supplied = set(expected), set(actual)
    return required <= supplied and supplied - required <= _observed_result_keys(calls, field_ids) | set(mapping_keys)


def ticket_inputs_match(calls, expected, field_ids):
    observed = observed_ticket_inputs(calls, field_ids)
    return all(any(all(name in metadata and (_number_equal(metadata[name], value) if type(value) in (int, float)
                        else metadata[name] == value) for name, value in required.items())
                   for metadata in observed.get(key, [])) for key, required in expected.items())


def _same_instant(left, right):
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    try:
        a, b = datetime.fromisoformat(left), datetime.fromisoformat(right)
        return a.tzinfo is not None and b.tzinfo is not None and a == b
    except ValueError:
        return False


def field_facts_observed(calls, facts, public_sources):
    """Accept a needed transition once: original history OR its dated comment.

    Current fields are checked separately as typed inputs. A history ID alone,
    quoted history text, a different owner, or a changed timestamp/value is not
    equivalent proof. Lossless chunks use the existing digest-verified reducer.
    """
    if not facts:
        return True
    successful = [call for call in calls if not call.get("isError") and (call.get("result") or {}).get("ok") is True]
    histories, comments = {}, {}
    for key in {fact["key"] for fact in facts}:
        trusted = [call for call in successful if call.get("name") == "get_evidence_page" and
            (data := call["result"].get("data") or {}).get("key") == key and data.get("kind") == "history" and
            data.get("sourceComplete") is True and not data.get("partial") and not call["result"].get("partial") and
            type(data.get("total")) is int and data["total"] >= 0 and type(data.get("startAt")) is int and data["startAt"] >= 0]
        histories[key] = evidence_collection(trusted, key, "history")
        comments[key] = list(evidence_collection(successful, key, "comments")["records"])
        for call in successful:
            data = call["result"].get("data") or {}
            owner = data.get("key", (call.get("arguments") or {}).get("key"))
            if owner == key:
                comments[key] += literal_source_records(call, "comment")
    needed_comments = {source: public_sources[source] for fact in facts for source in fact.get("commentSources", [])}
    observed_comments = observed_sources(successful, needed_comments)
    for fact in facts:
        key = fact["key"]
        if fact.get("relation"):
            expected_value = fact["to"] if fact["relation"] == "after-transition" else fact["from"]
            occurred, at = datetime.fromisoformat(fact["occurredAt"]), datetime.fromisoformat(fact["viewAt"])
            if (expected_value != fact["valueAtView"] or
                    (occurred < at) != (fact["relation"] == "after-transition") or
                    datetime.fromisoformat(fact["recordedAt"]) > datetime.fromisoformat(fact["knowledgeAt"])):
                return False
        if unchanged_status_observed(successful, fact):
            continue
        if fact.get("unchanged"):
            history = histories[key]
            if not history["complete"] or any(item.get("field") == fact["field"] for record in history["records"] for item in record.get("items", [])):
                return False
            continue
        history_match = (_same_instant(fact["occurredAt"], fact["recordedAt"]) and any(
            _same_instant(record.get("created"), fact["recordedAt"]) and any(
                item.get("field") == fact["field"] and item.get("fromString", item.get("from")) == fact["from"]
                and item.get("toString", item.get("to")) == fact["to"] for item in record.get("items", []))
            for record in histories[key]["records"]))
        comment_match = any(source in observed_comments and any(
            str(record.get("id")) == str(public_sources[source]["id"]) and
            _same_instant(record.get("created"), fact["recordedAt"]) for record in comments[key])
            for source in fact["commentSources"])
        if not history_match and not comment_match:
            return False
    return True


def observed_sources(calls, public_sources):
    """Source ownership and literal payloads, never arguments/excerpts as evidence."""
    successful = [c for c in calls if not c.get("isError") and (c.get("result") or {}).get("ok") is True]
    observed = set()
    comments = {}
    def contains(value, text):
        if isinstance(value, str):
            normalized = _normalized_public_text(text)
            return text in value or bool(normalized) and normalized in _normalized_public_text(value)
        if isinstance(value, dict):
            return any(contains(child, text) for child in value.values())
        if isinstance(value, list):
            return any(contains(child, text) for child in value)
        return False
    for identity, source in public_sources.items():
        kind, key = source["kind"], source.get("key")
        if kind == "jira_description":
            for call in successful:
                for record in literal_source_records(call, "ticket"):
                    if record.get("key") == key and contains(record, source["text"]):
                        observed.add(identity)
        elif kind == "jira_comment":
            if key not in comments:
                comments[key] = evidence_collection(successful, key)["records"]
            if any(str(record.get("id")) == source["id"] and contains(record, source["text"]) for record in comments[key]):
                observed.add(identity)
            # Full comments embedded by the public get_comments/get_ticket APIs.
            for call in successful:
                arguments = call.get("arguments") or {}
                data = call["result"].get("data") or {}
                if arguments.get("key") == key:
                    records = literal_source_records(call, "comment")
                    if any(str(r.get("id")) == source["id"] and contains(r, source["text"]) for r in records):
                        observed.add(identity)
        elif kind == "confluence_revision":
            if document_complete(successful, source["id"], version=source["version"]):
                observed.add(identity)
    return observed


def check(case, output, calls, ground_truth=None):
    truth = ground_truth or {}
    expected = truth.get("cases", {}).get(case["id"])
    failures = []
    answer, reply = output.get("structuredAnswer") or {}, output.get("reply") or ""
    if expected is None:
        return {"pass": False, "failures": ["oracle_unavailable"], "humanReviewRequired": True}
    if not output.get("ok") or not reply:
        failures.append("execution_not_complete")
    if output.get("forbiddenToolEvents"):
        failures.append("unapproved_tool_used")
    if has_stalled_pagination(output.get("mcpToolCalls") or calls):
        failures.append("read_continuation_not_advancing")
    required_keys, required_inputs = expected["keys"], expected["requiredTicketInputs"]
    if expected.get("nativeProgressEvidence") and _native_progress_route(
            calls, expected["nativeProgressEvidence"], truth.get("ticketFieldIds", {})):
        required_keys, required_inputs = _native_progress_scope(expected)
    mapping_keys = mapping_identity_keys(calls, expected.get("mappingIdentityEvidence", []), truth.get("publicSources", {}))
    if not _result_keys_match(answer.get("resultKeys"), required_keys, calls, truth.get("ticketFieldIds", {}), mapping_keys):
        failures.append("result_scope_mismatch")
    if not _rows_match(answer.get("measurements"), expected["measurements"], ("stream", "scope", "metric", "unit"), ("value",)):
        failures.append("measurement_mismatch")
    if not _denominators_match(answer.get("denominators"), expected["denominators"]):
        failures.append("denominator_mismatch")
    findings_ok, progress_ok, report_requirements = report_contract(
        answer, expected, calls, truth.get("publicSources", {}))
    if not findings_ok:
        failures.append("report_correspondence_or_claim_mismatch")
    if not progress_ok:
        failures.append("report_progress_stage_or_duplicate_mismatch")
    required_evidence = merge_requirements({"requiredSources": deepcopy(expected["requiredSources"]),
        "requiredTicketInputs": deepcopy(required_inputs),
        "requiredFieldFacts": deepcopy(expected["requiredFieldFacts"])}, report_requirements)
    if not ticket_inputs_match(calls, required_evidence["requiredTicketInputs"], truth.get("ticketFieldIds", {})):
        failures.append("required_typed_inputs_not_observed")
    if not field_facts_observed(calls, required_evidence["requiredFieldFacts"], truth.get("publicSources", {})):
        failures.append("required_historical_fact_not_observed")
    if answer.get("progress") != expected["progress"] or answer.get("reportedTotal") != expected["reportedTotal"]:
        failures.append("progress_or_total_mismatch")
    required = set(required_evidence["requiredSources"])
    public = {key: value for key, value in truth.get("publicSources", {}).items() if key in required}
    if required - observed_sources(calls, public):
        failures.append("required_evidence_not_observed")
    successful = [c for c in calls if not c.get("isError") and (c.get("result") or {}).get("ok") is True]
    if not successful:
        failures.append("no_mcp_evidence")
    if expected.get("requiredSearchClass") == "confluence" and not _complete_report_search(successful):
        failures.append("required_source_search_not_observed")
    if expected.get("deniedKey") and not any((c.get("arguments") or {}).get("key") == expected["deniedKey"]
        and (c.get("isError") or (c.get("result") or {}).get("ok") is False) for c in calls):
        failures.append("denied_branch_not_observed")
    if required and not answer.get("sources"):
        failures.append("sources_missing")
    if any(not source_observed(calls, source) for source in answer.get("sources") or []):
        failures.append("source_not_observed")
    # Semantic assertions (including honest uncertainty and parent status) must
    # be judged from the real prose; keyword presence cannot qualify a run.
    return {"pass": not failures, "failures": list(dict.fromkeys(failures)),
            "humanReviewRequired": True, "notAQualitativeScore": True,
            "semanticReviewRequirements": ["request_fulfillment", "factual_grounding", "contract_actionability",
                                           "safety_uncertainty", "communication_rendering"],
            "streamHardGate": {"directMeanMinimum": 4.5, "materialDefectsAllowed": 0}}
