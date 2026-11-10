"""Direct event reducers: no product aggregation, text extraction or JQL evaluation."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from datetime import datetime

from .events import Event, stamp


class EventOracle:
    def __init__(self, events: tuple[Event, ...], observed_until: datetime):
        self.events = events
        self.observed_until = observed_until

    def _visible(self, at=None, knowledge_at=None, issue_keys=None):
        at, known = at or self.observed_until, knowledge_at or self.observed_until
        stamp(at), stamp(known)
        keys = set(issue_keys) if issue_keys is not None else None
        return [e for e in self.events if e.occurred_at < at and e.recorded_at <= known
                and e.sources and (keys is None or e.issue_key in keys)]

    def active_development(self, start, end):
        """Ongoing inventory from sourced status intervals at the observation horizon."""
        stamp(start), stamp(end)
        if start >= end:
            raise ValueError("The requested interval must be nonempty")
        end = min(end, self.observed_until)
        if start >= end:
            return []
        visible = self._visible(end)
        states = {e.issue_key: (e.payload.get("initial_status", "In Progress"), e.occurred_at)
                  for e in visible if e.kind == "issue" and e.stream == "development"}
        changes = [e for e in visible if e.issue_key in states and e.stream == "development"
                   and e.kind == "field" and e.payload["field"] == "status"]
        ongoing, active = {"In Progress", "Reopened"}, set()
        for e in sorted(changes, key=lambda row: (row.occurred_at, row.recorded_at, row.identity)):
            status, since = states[e.issue_key]
            if e.occurred_at < since:
                continue
            if status in ongoing and since < e.occurred_at and e.occurred_at > start:
                active.add(e.issue_key)
            states[e.issue_key] = (e.payload["value"], e.occurred_at)
        for key, (status, since) in states.items():
            if status in ongoing and since < end:
                active.add(key)
        return sorted(active)

    def operations(self, start, end, *, knowledge_at=None, issue_keys=None):
        stamp(start), stamp(end)
        if start >= end:
            raise ValueError("The requested interval must be nonempty")
        visible = self._visible(knowledge_at=knowledge_at, issue_keys=issue_keys)
        requests = [e for e in visible if e.stream == "operations" and e.kind == "issue"
                    and start <= e.occurred_at < end]
        cancelled = {e.payload["corrects"] for e in visible if e.kind == "correction"}
        # Execution references are authored public business identifiers, not ticket IDs.
        executions = {}
        for e in sorted(visible, key=lambda e: (e.recorded_at, e.identity)):
            if e.kind == "execution" and start <= e.occurred_at < end and e.payload["execution_ref"] not in cancelled:
                executions[e.payload["execution_ref"]] = e
        aliases = self.asset_state(end, knowledge_at=knowledge_at)["aliases"]
        canonical = lambda name: aliases.get(name, name)
        assets = {canonical(a) for e in executions.values() for a in e.payload.get("tables", [])}
        requested = {canonical(a) for e in requests for a in e.payload.get("targets", [])}
        request_keys = {e.issue_key for e in requests}
        proven = {e.issue_key for e in visible if e.kind in {"execution", "correction"}}
        unknown_keys = sorted((request_keys - proven) | {
            e.issue_key for e in visible if e.issue_key in request_keys and e.payload.get("evidence_missing")})
        statuses = {}
        for e in sorted(visible, key=lambda e: (e.occurred_at, e.recorded_at, e.identity)):
            if e.kind == "field" and e.payload["field"] == "status" and e.occurred_at < end:
                statuses[e.issue_key] = e.payload["value"]
        per_kind = {}
        for e in executions.values():
            per_kind.setdefault(e.payload["operation"], set()).add(e.payload["execution_ref"])
        sources = sorted({s for e in [*requests, *executions.values(),
                          *(e for e in visible if e.kind == "correction")] for s in e.sources})
        return {"request_tickets": len(requests), "completed_tickets": sum(
                    statuses.get(e.issue_key) in {"Resolved", "Closed"} for e in requests),
                "requested_targets": sum(len(e.payload.get("targets", [])) for e in requests),
                "completed_unique_tables": None if unknown_keys else len(assets), "completed_assets": sorted(assets),
                "work_events": None if unknown_keys else len(executions), "incomplete_targets": sorted(requested - assets),
                "complete": not unknown_keys, "unknown_request_keys": unknown_keys,
                "known_completed_unique_tables": len(assets), "known_work_events": len(executions),
                "events_by_operation": {k: len(v) for k, v in sorted(per_kind.items())}, "sources": sources}

    def simple_voc(self, start, end, *, knowledge_at=None):
        visible = self._visible(end, knowledge_at)
        rows = [e for e in visible if e.stream == "simple_voc"
                and e.kind == "issue" and start <= e.occurred_at < end]
        keys = {e.issue_key for e in rows}
        answers = [e for e in visible if e.issue_key in keys and e.payload.get("verified_answer")]
        verified = {e.issue_key for e in answers}
        unknown = sorted(keys - verified)
        return {"request_tickets": len(rows), "executed_operations": 0,
                "resolved_requests": None if unknown else len(verified),
                "known_resolved_requests": len(verified), "unknown_request_keys": unknown,
                "sources": sorted({s for e in [*rows, *answers] for s in e.sources})}

    def asset_state(self, at, *, knowledge_at=None):
        events = sorted(self._visible(at, knowledge_at), key=lambda e: (e.occurred_at, e.recorded_at, e.identity))
        aliases, active, edges = {}, set(), set()
        cancelled = {e.payload["corrects"] for e in events if e.kind == "correction"}
        for e in events:
            p = e.payload
            if e.kind == "inventory":
                active.update(p["tables"])
            if e.kind == "rename":
                aliases[p["old"]] = p["new"]
                if p["old"] in active:
                    active.remove(p["old"])
                    active.add(p["new"])
            if e.kind != "execution" or p["execution_ref"] in cancelled:
                continue
            tables = {aliases.get(a, a) for a in p.get("tables", [])}
            if p["operation"] == "load":
                active.update(tables)
            elif p["operation"] in {"suspend", "delete"}:
                active.difference_update(tables)
            for edge in p.get("edges_add", []):
                edges.add(tuple(edge))
            for edge in p.get("edges_remove", []):
                edges.discard(tuple(edge))
        return {"active_tables": sorted(active), "aliases": aliases,
                "federation_edges": [list(edge) for edge in sorted(edges)]}

    def development(self, at, *, knowledge_at=None, issue_keys=None):
        events = sorted(self._visible(at, knowledge_at), key=lambda e: (e.occurred_at, e.recorded_at, e.identity))
        state, mappings, progress_events, components = {}, {}, {}, {}
        for e in events:
            p = e.payload
            if e.kind == "issue" and e.stream in {"development", "development_epics"}:
                state[e.issue_key] = {"status": p.get("initial_status", "In Progress"), "sp": p.get("sp"),
                                      "parent": p.get("parent"), "epic": p.get("epic"),
                                      "type": p["type"], "assignee": p["assignee"], "sources": list(e.sources)}
                components[e.issue_key] = p.get("module")
            elif e.kind == "field" and e.issue_key in state:
                field = {"status": "status", "parent": "parent", "epic": "epic", "assignee": "assignee"}.get(p["field"])
                if field:
                    state[e.issue_key][field] = p["value"]
                    state[e.issue_key]["sources"].extend(e.sources)
            elif e.kind == "mapping":
                mappings[p["wbs"]] = p["epics"]
            elif e.kind == "development_progress":
                progress_events.setdefault(e.issue_key, []).append(e)
        for key, row in state.items():
            row["progress"] = self._development_progress(progress_events.get(key, []), key)
            children = [v for v in state.values() if v["parent"] == key]
            row["child_completion_percent"] = (100 * sum(v["status"] in {"Resolved", "Closed"} for v in children)
                                               / len(children)) if children else None
        epic_progress = {}
        for key, row in state.items():
            if row["type"] == "Epic":
                # Independent authored-fact reduction of the documented SP contract.
                # Exclusion affects this rollup only, never activity or child-count views.
                children = [v for child_key, v in state.items() if v["epic"] == key
                            and v["type"] not in {"Sub-Task", "Bug", "Ops", "운영"}
                            and components[child_key] != "사용자 VoC"]
                total = sum(1 if v["sp"] is None else v["sp"] for v in children)
                done = sum(1 if v["sp"] is None else v["sp"] for v in children
                           if v["status"] in {"Resolved", "Closed"})
                epic_progress[key] = {"sp_total": round(total, 1), "sp_done": round(done, 1),
                                      "percent": round(100 * done / total, 1) if total > 0 else 0.0}
        wbs = {}
        for key, mapping in mappings.items():
            valid = [(epic_progress.get(epic, {}).get("percent"), weight) for epic, weight in mapping.items()]
            weight_total = sum(weight for _, weight in valid) or 1
            wbs[key] = {"metric": "SP-weighted epic progress", "epic_weights": mapping,
                        "percent": round(sum(value * (weight / weight_total) for value, weight in valid), 1)
                        if valid and all(value is not None for value, _ in valid) else None}
        keys = set(issue_keys) if issue_keys is not None else None
        correspondences, superseded = self._report_correspondence(events, at)
        for key, row in state.items():
            self._apply_report_progress(row, key, correspondences)
        return {"issues": {k: v for k, v in state.items() if keys is None or k in keys},
                "epics": epic_progress, "wbs": wbs, "report_correspondence": correspondences,
                "superseded_report_versions": superseded}

    @staticmethod
    def _report_correspondence(events, at):
        profiles = {e.issue_key: (e.payload["report_context"], e) for e in events
                    if e.kind == "issue" and e.payload.get("report_context")}
        latest, superseded = {}, []
        for event in events:
            if event.kind != "development_report":
                continue
            document = event.payload["document"]
            if document in latest:
                old = latest[document]
                superseded.append({"document": document, "version": old.payload["version"], "sources": list(old.sources)})
            latest[document] = event
        rows = []
        for event in latest.values():
            report = event.payload
            for item in report["items"]:
                context = {**report["report_context"], **item.get("context", {})}
                for claim in item["claims"]:
                    occurred = claim["occurred_at"]
                    if occurred is not None and occurred >= at:
                        continue
                    if occurred is None:
                        if report["period_end"] > at:
                            continue
                        # A correction can withdraw a past assertion without dating
                        # its new pending decision/plan to that assertion's period.
                        if claim.get("period_bound") is False and event.recorded_at >= at:
                            continue
                    candidates, proofs = [], list(event.sources)
                    for key, (profile, issue) in profiles.items():
                        if item.get("jira_keys") and key not in item["jira_keys"]:
                            continue
                        if profile["module"] != context.get("module") or profile["environment"] != context.get("environment"):
                            continue
                        if profile["release"] != context.get("release"):
                            continue
                        if not set(profile["scopes"]) & set(context.get("scopes", [])):
                            continue
                        if claim["deliverable"] not in profile["deliverables"]:
                            continue
                        if not set(profile["participants"]) & set(context.get("participants", [])):
                            continue
                        if report["period_end"] <= issue.occurred_at:
                            continue
                        candidates.append(key)
                        proofs.extend(issue.sources)
                    rows.append({"document": report["document"], "version": report["version"],
                        "item_id": item["item_id"], "candidate_keys": sorted(candidates),
                        "status": "verified" if len(candidates) == 1 else "ambiguous" if candidates else "unresolved",
                        "proof_sources": sorted(set(proofs)), "report_sources": list(event.sources),
                        "matched_dimensions": ["module", "environment", "function", "deliverable", "participant", "release", "period"] if candidates else [],
                        "period_start": stamp(report["period_start"]), "period_end": stamp(report["period_end"]),
                        "release": context.get("release"),
                        "published_at": stamp(event.recorded_at), "claim": deepcopy(claim)})
        return rows, superseded

    @staticmethod
    def _apply_report_progress(row, key, correspondences):
        progress = row["progress"]
        progress.update({"reported_implementation": [], "test_accepted_deliverables": [],
            "final_accepted_deliverables": [], "planned_deliverables": [], "report_sources": [],
            "reported_acceptance_events": 0, "duplicate_status": "resolved", "conflicts": [],
            "report_correspondence": []})
        accepted_events, uncertain_events = set(), Counter()
        for match in correspondences:
            if key not in match["candidate_keys"]:
                continue
            progress["report_correspondence"].append(deepcopy(match))
            if match["status"] != "verified":
                continue
            claim = match["claim"]
            stage, name, occurred = claim["stage"], claim["deliverable"], claim["occurred_at"]
            progress["sources"].extend(match["proof_sources"])
            progress["report_sources"].extend(match["report_sources"])
            if stage == "plan":
                progress["planned_deliverables"].append(name)
                continue
            progress["known"] = True
            progress["state"] = "in_progress"
            if occurred is not None:
                previous = progress["last_substantive_at"]
                progress["last_substantive_at"] = max(previous, stamp(occurred)) if previous else stamp(occurred)
            if stage == "implementation":
                progress["reported_implementation"].append(name)
            elif claim["outcome"] == "accepted":
                target = "test_accepted_deliverables" if stage == "test_acceptance" else "final_accepted_deliverables"
                progress[target].append(name)
                progress["completed_deliverables"].append(name)
                identity = (stage, name, claim.get("artifact"), match["release"])
                if occurred is None:
                    uncertain_events[identity] += 1
                else:
                    accepted_events.add((*identity, stamp(occurred)))
            elif stage == "final_acceptance" and row["status"] in {"Resolved", "Closed"}:
                progress["conflicts"].append({"jira_status": row["status"], "report_outcome": claim["outcome"],
                                             "deliverable": name, "sources": match["proof_sources"]})
        duplicate_unknown = any(count > 1 or any(event[:4] == identity for event in accepted_events)
                                for identity, count in uncertain_events.items())
        progress["reported_acceptance_events"] = None if duplicate_unknown else len(accepted_events) + len(uncertain_events)
        progress["duplicate_status"] = "unknown" if duplicate_unknown else "resolved"
        for field in ("reported_implementation", "test_accepted_deliverables", "final_accepted_deliverables",
                      "planned_deliverables", "completed_deliverables", "sources", "report_sources"):
            progress[field] = sorted(set(progress[field]))
        if progress["conflicts"]:
            progress["state"] = "conflicting"

    @staticmethod
    def _development_progress(events, key):
        result = {"state": "unknown", "known": False, "started_at": None, "overall_percent": None,
                  "completed_deliverables": [], "pending_deliverables": [], "blockers": [],
                  "last_substantive_at": None, "last_administrative_at": None,
                  "contributors": [], "sources": []}
        completed, contributors = set(), set()
        for event in events:
            if event.issue_key != key or event.kind != "development_progress":
                continue
            p, at = event.payload, stamp(event.occurred_at)
            result["sources"].extend(event.sources)
            phase = p["phase"]
            if phase == "administrative":
                result["last_administrative_at"] = at
                continue
            if phase == "unverified":
                continue
            result["known"] = True
            if phase == "not_started":
                result["state"] = "not_started"
                continue
            result["state"] = {"blocked": "awaiting_dependency", "rework": "rework"}.get(phase, "in_progress")
            if phase == "started":
                result["started_at"] = at
            result["last_substantive_at"] = at
            completed.update(p.get("completed", []))
            completed.difference_update(p.get("invalidate", []))
            if "pending" in p:
                result["pending_deliverables"] = p["pending"]
            if "blockers" in p:
                result["blockers"] = p["blockers"]
            if p.get("contributor"):
                contributors.add(p["contributor"])
        result["completed_deliverables"] = sorted(completed)
        result["contributors"] = sorted(contributors)
        result["sources"] = sorted(set(result["sources"]))
        return result

    def published_report(self, document, *, version=None, knowledge_at=None):
        candidates = [e for e in self._visible(knowledge_at=knowledge_at) if e.kind == "publication"
                      and e.payload["document"] == document and (version is None or e.payload["version"] == version)]
        if not candidates:
            return {"known": False, "reason": "No accessible published revision", "sources": []}
        row = max(candidates, key=lambda e: e.payload["version"])
        return {"known": True, **deepcopy(row.payload), "sources": list(row.sources)}
