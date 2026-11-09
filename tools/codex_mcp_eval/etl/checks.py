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
            "inScope": source["title"].startswith("ETL 개발")})
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


def _attach_reports(row, bundle, at, *, keys, view, knowledge_at=None, revisions=False, include_decoy=False):
    snapshot = bundle.oracle.development(at, knowledge_at=knowledge_at)
    selected = set(keys)
    if include_decoy:
        selected.add("DL-50003")
    matches = [match for match in snapshot["report_correspondence"] if selected.intersection(match["candidate_keys"])]
    old_matches = []
    if revisions:
        for old in snapshot["superseded_report_versions"]:
            event = next(e for e in bundle.oracle.events if e.kind == "development_report"
                         and e.payload["document"] == old["document"] and e.payload["version"] == old["version"])
            old_view = bundle.oracle.development(at, knowledge_at=event.recorded_at)
            old_matches += [match for match in old_view["report_correspondence"]
                            if match["document"] == old["document"] and match["version"] == old["version"]
                            and selected.intersection(match["candidate_keys"])]
    all_matches = matches + old_matches
    row["reportFindings"] = _unique_rows(row["reportFindings"] + _public_report_findings(bundle, all_matches))
    contract = row.setdefault("reportExpectations", {"taskProgress": {}, "knowledgeComparisons": {},
        "excludedTaskKeys": [], "correspondenceEvidence": "module/environment/function/deliverable/participant/release/period",
        "privateIntentIsEvidence": False, "titleSimilarityIsEnough": False})
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
        ambiguous = any(match["status"] != "verified" and key in match["candidate_keys"] for match in matches)
        row["reportProgress"].append({"key": key, "view": view, "jiraStatus": state["status"],
            "implementation": list(progress["reported_implementation"]),
            "testAccepted": list(progress["test_accepted_deliverables"]),
            "finalAccepted": list(progress["final_accepted_deliverables"]), "planned": list(progress["planned_deliverables"]),
            "acceptedEvents": None if ambiguous else progress["reported_acceptance_events"], "percent": progress["overall_percent"]})
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


def build_expectations(bundle):
    oracle, events = bundle.oracle, bundle.oracle.events
    start, end = bundle.spec.months[0]
    now = bundle.spec.observed_until
    issues = {event.issue_key: event for event in events if event.kind == "issue"}
    result = {}

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
    for variant, keys in (("normal", ["DL-21743", "DL-21744"]),
                          ("boundary", ["DL-21745", "DL-21747", "DL-21749"])):
        row = new(2, variant, distinctions=("weekly report vs actual occurrence", "ambiguous matching and undated duplicate"))
        _attach_reports(row, bundle, _at("2026-10-26" if variant == "normal" else "2026-11-01"), keys=keys,
                        view="as-of-2026-10-26" if variant == "normal" else "2026-10-end")
        measure(row, "development", "2026-10-19/2026-10-26", "activity_tickets", 2 if variant == "normal" else None, "tickets")
    for variant, month in (("normal", 0), ("boundary", 11)):
        a, b = bundle.spec.months[month]
        dev = oracle.active_development(a, b)
        ops = [k for k, e in issues.items() if e.stream == "operations" and a <= e.occurred_at < b]
        voc = [k for k, e in issues.items() if e.stream == "simple_voc" and a <= e.occurred_at < b]
        keys = dev + ops + voc
        exceptions = [key for key, event in issues.items() if event.stream == "development" and
                      event.occurred_at < b and key not in dev and bundle.world.issues[key]["statusCategory"] != "done"]
        row = new(3, variant, keys + exceptions, proof(exceptions, kinds={"field", "development_progress"}, field_names={"status"}),
                  distinctions=("carryover vs new requests", "three separate streams"))
        row["requiredTicketInputs"] = ticket_inputs(keys + exceptions, ("summary", "component", "type", "created_date", "resolved_date", "status"))
        scope = a.strftime("%Y-%m")
        measure(row, "development", scope, "active_tickets", len(dev), "tickets")
        measure(row, "operations", scope, "request_tickets", len(ops), "tickets")
        measure(row, "simple_voc", scope, "request_tickets", len(voc), "tickets")
        if variant == "boundary":
            lifecycle_keys = {key for keys in bundle.manifest["development_lifecycle_coverage"].values() for key in keys}
            lifecycle = {key: value["progress"] for key, value in oracle.development(b)["issues"].items() if key in lifecycle_keys}
            row["lifecycleExpectations"] = deepcopy(lifecycle)
            row["keys"] = sorted(set(row["keys"]) | set(lifecycle))
            for key, progress in lifecycle.items():
                row["requiredSources"] += progress["sources"]
                item_scope = key + "@2026-10-end"
                measure(row, "development", item_scope, "verified_deliverables",
                        len(progress["completed_deliverables"]), "deliverables")
                measure(row, "development", item_scope, "percent", progress["overall_percent"], "percent")
            row["mustDiscloseUncertainty"] = True
            row["distinctions"] += ["created vs started", "verified deliverables vs arbitrary overall percent",
                "substantive activity vs administrative updated", "reopened acceptance withdrawal",
                "historical contributor vs current assignee", "dependency wait vs implementation completion"]
            report_keys = sorted({key for match in oracle.development(b)["report_correspondence"] for key in match["candidate_keys"]
                                  if bundle.world.issues[key]["component"] == "ETL"})
            _attach_reports(row, bundle, b, keys=report_keys, view="2026-10-end", revisions=True, include_decoy=True)

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
                row["requiredTicketInputs"] = ticket_inputs(task_keys, ("sp", "type", "epic", "status") if at == now else ("sp", "type", "epic", "created_date"))
                row["requiredSources"] = proof(task_keys, kinds={"field"}, field_names={"status", "parent", "epic"}) if at != now else []
            if family == 4:
                mapping = [e for e in events if e.kind == "mapping" and e.occurred_at < at]
                required_mapping = max(mapping, key=lambda e: e.occurred_at)
                row["requiredSources"] += list(required_mapping.sources)
                if variant == "boundary":
                    prior = oracle.development(_at("2026-01-15"))["wbs"]["ETL-1"]["epic_weights"]
                    row["mappingComparison"] = {"2026-01-15": prior, "current": weights}
                    row["requiredSources"] += [source for event in mapping if event.payload["version"] == 1 for source in event.sources]
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

    def operations(family, variant, keys, scope, *, interval=None):
        data = oracle.operations(*(interval or (start, end)), issue_keys=keys)
        row = new(family, variant, keys, proof(keys), unknown=not data["complete"],
            distinctions=("request tickets vs requested targets vs completed tickets", "unique tables vs execution events",
                          "partial completion", "environment and verified rename", "duplicate reporting and corrections"))
        for metric, unit in units.items():
            measure(row, "operations", scope, metric, data[metric], unit)
        row["operations"] = data
        return row

    customer_keys = ["DL-31000", "DL-31001", "DL-31002"]
    operations(8, "normal", customer_keys, "customers@2025-11")["reportedTotal"] = 2
    load_keys = [k for k, e in issues.items() if e.stream == "operations" and start <= e.occurred_at < end
                 and e.payload["operation"] == "load"]
    operations(8, "boundary", load_keys, "load@2025-11")
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
                data = oracle.operations(start, bundle.spec.end, issue_keys=keys)
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
        row["reportedTotal"] = len(keys)
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
        if fact.get("unchanged"):
            history = histories[key]
            if not history["complete"] or any(item.get("field") == fact["field"] for record in history["records"] for item in record.get("items", [])):
                return False
            continue
        if fact.get("relation"):
            expected_value = fact["to"] if fact["relation"] == "after-transition" else fact["from"]
            occurred, at = datetime.fromisoformat(fact["occurredAt"]), datetime.fromisoformat(fact["viewAt"])
            if (expected_value != fact["valueAtView"] or
                    (occurred < at) != (fact["relation"] == "after-transition") or
                    datetime.fromisoformat(fact["recordedAt"]) > datetime.fromisoformat(fact["knowledgeAt"])):
                return False
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
    if answer.get("resultKeys") != expected["keys"]:
        failures.append("result_scope_mismatch")
    if not _rows_match(answer.get("measurements"), expected["measurements"], ("stream", "scope", "metric", "unit"), ("value",)):
        failures.append("measurement_mismatch")
    if not _rows_match(answer.get("denominators"), expected["denominators"], ("scope", "metric", "unit"), ("numerator", "denominator")):
        failures.append("denominator_mismatch")
    if not _report_rows_match(answer.get("reportFindings", []), expected["reportFindings"]):
        failures.append("report_correspondence_or_claim_mismatch")
    if not _report_rows_match(answer.get("reportProgress", []), expected["reportProgress"]):
        failures.append("report_progress_stage_or_duplicate_mismatch")
    if not ticket_inputs_match(calls, expected["requiredTicketInputs"], truth.get("ticketFieldIds", {})):
        failures.append("required_typed_inputs_not_observed")
    if not field_facts_observed(calls, expected["requiredFieldFacts"], truth.get("publicSources", {})):
        failures.append("required_historical_fact_not_observed")
    if answer.get("progress") != expected["progress"] or answer.get("reportedTotal") != expected["reportedTotal"]:
        failures.append("progress_or_total_mismatch")
    required = set(expected["requiredSources"])
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
            "semanticReviewRequirements": ["factual_grounding", "request_fulfillment", "safety_uncertainty",
                                           "readability", "source_traceability"],
            "streamHardGate": {"directMeanMinimum": 4.5, "materialDefectsAllowed": 0}}
