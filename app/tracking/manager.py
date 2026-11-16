"""Durable asynchronous runs. Every unfinished group remains resumable."""
from __future__ import annotations

from concurrent.futures import CancelledError
from contextvars import copy_context
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import threading
import time
from uuid import uuid4

from app.auth.base import PermissionDenied, SessionExpired, UpstreamError, UpstreamUnavailable, background_upstream
from app.infra.settings import is_manager, load_people, load_plan
from app.domain.progress import VOC_COMPONENT
from app.tracking.analysis import KST, analyze_issue, build_result, identity_aliases, instant, period_bounds, person
from app.tracking.source import IncompleteSource, TrackingSource
from app.tracking.presentation import present_comments
from app.tracking.activity import project_activity
from app.tracking.scope import module_candidates
from app.tracking.storage import TrackingStore, encode


class TrackingManager:
    def __init__(self, client, db_path=None, *, plan_loader=load_plan, people_loader=load_people):
        self.client = client
        self.store = TrackingStore(db_path or Path(client.s.cache_db_path).with_name("tracking.sqlite3"))
        self.plan_loader, self.people_loader = plan_loader, people_loader
        self.lock = threading.RLock()
        self.page_slots = threading.BoundedSemaphore(2)
        self.workers, self.controls, self.summary_locks = {}, {}, {}

    def authorize(self):
        me = self.client.current_user_for_authorization()
        if not me or not me.get("id"):
            raise SessionExpired("Jira 사용자를 확인해 주세요.")
        if not is_manager(self.client.s, me):
            raise PermissionError("Tracking 조회 권한이 없습니다.")
        return str(me["id"]).casefold(), self.scope()

    def scope(self):
        return encode({"env": self.client.env, "base": getattr(self.client.s, "jira_base", ""),
                       "projects": getattr(self.client.s, "search_jira_projects", [self.client.s.project_key])})

    def options(self):
        self.authorize()
        people = self.people_loader()
        today = datetime.now(KST).date()
        names = {}
        for uid in sorted({str(uid) for ids in people.values() for uid in ids}):
            # Display-only cache lookup: option rendering never performs N people REST calls.
            name = (self.client.display_name_cached(uid) if hasattr(self.client, "display_name_cached") else None) or uid
            names[uid] = name
        try:
            from app.agent.config import llm_ready
            ai_enabled, ai_reason = llm_ready()
        except (ImportError, TypeError, ValueError):
            ai_enabled, ai_reason = False, "AI 설정을 확인해 주세요."
        return {"people": [{"id": uid, "name": name, "modules": [m for m, ids in people.items() if uid in ids]} for uid, name in names.items()],
                "modules": [{"id": m, "name": m, "people": list(ids)} for m, ids in people.items()],
                "defaults": {"startDate": (today - timedelta(days=6)).isoformat(), "endDate": today.isoformat(), "people": [], "modules": []},
                "timezone": "Asia/Seoul", "aiEnabled": bool(ai_enabled), "aiReason": ai_reason,
                "vocComponent": getattr(self.client.s, "voc_component", None) or VOC_COMPONENT}

    def validate(self, body):
        today = datetime.now(KST).date()
        request = {"startDate": body.get("startDate") or (today - timedelta(days=6)).isoformat(),
                   "endDate": body.get("endDate") or today.isoformat()}
        period_bounds(request["startDate"], request["endDate"])
        people = self.people_loader()
        available = {str(uid).casefold() for ids in people.values() for uid in ids}
        for field in ("people", "modules"):
            values = body.get(field) or []
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError("사람과 모듈은 ID 목록으로 지정해 주세요.")
            request[field] = list(dict.fromkeys(values))
        if any(p.casefold() not in available for p in request["people"]):
            raise ValueError("설정에 없는 사람이 포함되어 있습니다.")
        if any(m not in people for m in request["modules"]):
            raise ValueError("설정에 없는 모듈이 포함되어 있습니다.")
        return request

    def create(self, body, *, background=True):
        owner, scope = self.authorize()
        request = self.validate(body)
        _, upper = period_bounds(request["startDate"], request["endDate"])
        now = datetime.now(KST).isoformat()
        snapshot = {"id": uuid4().hex, "status": "queued", "request": request, "asOf": upper.isoformat(),
                    "createdAt": now, "updatedAt": now, "summary": {}, "coverage": {"complete": False, "queriesComplete": False,
                        "issuesTotal": 0, "issuesComplete": 0, "historyComplete": False, "commentsComplete": False, "missing": []},
                    "metrics": {"restRequests": 0, "requestBytes": 0, "responseBytes": 0, "retries": 0,
                        "cacheHits": 0, "elapsedMs": 0, "firstResultMs": None, "mcpCalls": 0, "llmTokens": 0,
                        "scope": "collection", "byteMeasurement": "utf8-json-payload", "authorizationRequestsIncluded": False},
                    "issues": [], "events": [], "tree": [], "errors": []}
        self.store.insert(snapshot, owner, scope)
        self._start(snapshot["id"], background=background)
        return self.get(snapshot["id"])

    def _owned(self, run_id):
        owner, scope = self.authorize()
        stored_owner, stored_scope, snapshot, checkpoint = self.store.read(run_id)
        if owner != stored_owner or scope != stored_scope:
            raise PermissionError("다른 Jira 계정 또는 연결의 수집 결과입니다.")
        return snapshot, checkpoint

    def get(self, run_id):
        snapshot, checkpoint = self._owned(run_id)
        return present_comments(snapshot, self.client, checkpoint)

    def list(self):
        owner, scope = self.authorize()
        return [project_activity(snapshot) for snapshot in self.store.recent(owner, scope)]

    def _start(self, run_id, *, background):
        with self.lock:
            old = self.workers.get(run_id)
            if old and old.is_alive():
                return
            self.controls[run_id] = threading.Event()
            if background:
                context = copy_context()
                worker = threading.Thread(target=lambda: context.run(self._work, run_id), daemon=True,
                                          name="tracking-" + run_id[:8])
                self.workers[run_id] = worker
                worker.start()
                return
        self._work(run_id)

    def resume(self, run_id, *, background=True):
        snapshot, cp = self._owned(run_id)
        if snapshot["status"] == "complete":
            return present_comments(snapshot, self.client, cp)
        if snapshot["status"] == "cancelled":
            raise ValueError("취소된 수집은 새 조회로 시작해 주세요.")
        if snapshot["status"] not in ("queued", "running"):
            snapshot["metrics"]["retries"] += 1
            snapshot["status"] = "queued"
            snapshot["errors"] = []
            self.store.save(run_id, snapshot, cp)
        self._start(run_id, background=background)
        return self.get(run_id)

    def cancel(self, run_id):
        snapshot, cp = self._owned(run_id)
        if snapshot["status"] != "complete":
            self.controls.setdefault(run_id, threading.Event()).set()
            snapshot["status"] = "cancelled"
            self.store.save(run_id, snapshot, cp)
        return present_comments(snapshot, self.client, cp)

    def wait(self, run_id, timeout=60):
        worker = self.workers.get(run_id)
        if worker:
            worker.join(timeout)
        return self.get(run_id)

    def record_summary(self, run_id, result):
        snapshot, cp = self._owned(run_id)
        snapshot["_activitySummaryEventIds"] = sorted(event["id"] for event in project_activity(snapshot).get("events") or [])
        snapshot["aiSummary"] = result
        usage = result.get("usage") or {}
        snapshot["metrics"]["llmTokens"] += int(usage.get("totalTokens") or 0)
        self.store.save(run_id, snapshot, cp)
        return result

    def _work(self, run_id):
        _, _, snapshot, cp = self.store.read(run_id)
        initial_elapsed = snapshot["metrics"]["elapsedMs"]
        started = time.monotonic()
        owner, scope, _, _ = self.store.read(run_id)
        signal = self.controls[run_id]
        request = snapshot["request"]
        start, _ = period_bounds(request["startDate"], request["endDate"])
        upper = instant(snapshot["asOf"])
        snapshot["status"] = "running"
        snapshot["errors"] = []
        analyzed = dict(cp.get("analyzed") or {})

        def persist():
            snapshot["updatedAt"] = datetime.now(KST).isoformat()
            snapshot["metrics"]["elapsedMs"] = initial_elapsed + round((time.monotonic() - started) * 1000)
            # A concurrent cancel must survive the completion of an in-flight REST request.
            if signal.is_set():
                snapshot["status"] = "cancelled"
            self.store.save(run_id, snapshot, cp)

        def check():
            if signal.is_set():
                raise CancelledError()
            actual_owner, actual_scope = self.authorize()
            if actual_owner != owner or actual_scope != scope:
                raise PermissionError("수집 중 Jira 계정 또는 연결이 변경되었습니다.")

        namespace = hashlib.sha256((scope + ":" + owner).encode()).hexdigest()
        source = TrackingSource(self.client, self.store, namespace, snapshot["metrics"], check, persist, self.page_slots)
        fields = ",".join(dict.fromkeys(["summary", "issuetype", "status", "assignee", "creator", "reporter", "created", "updated", "parent", "components", "subtasks", self.client.s.epic_link_field_id, "comment"]))
        plan = self.plan_loader()
        selected_modules = set(request["modules"])
        module_scope_options = {"voc_component": getattr(self.client.s, "voc_component", None) or VOC_COMPONENT,
                                "module_people": self.people_loader(),
                                "configured_epics": {e.get("key") or e.get("ticket") for task in plan.get("wbs", [])
                                                     for e in task.get("epics", [])}}
        allowed_epics = {e.get("key") or e.get("ticket") for task in plan.get("wbs", [])
                         if not selected_modules or task["module"] in selected_modules for e in task.get("epics", [])}

        def publish(queries_complete=False):
            active = [record["issue"] for record in analyzed.values()
                      if record["issue"].get("inScope") or record["issue"].get("scopeUncertain")]
            all_raw = cp["issues"]
            # Resolve parent->Epic through the current source graph, including Sub-Tasks.
            def epic_of(issue):
                parent = issue.get("parentKey")
                seen = set()
                while parent and parent not in seen:
                    seen.add(parent)
                    raw_parent = all_raw.get(parent) or {}
                    pf = raw_parent.get("fields") or {}
                    if pf.get(self.client.s.epic_link_field_id):
                        return pf[self.client.s.epic_link_field_id]
                    if parent in allowed_epics:
                        return parent
                    parent = (pf.get("parent") or {}).get("key")
                return issue.get("epicKey") or ""
            for issue in active:
                if not issue.get("epicKey"):
                    issue["epicKey"] = epic_of(issue)
            if selected_modules:
                scope_rows = [all_raw[key] for key in cp.get("candidateKeys", []) if key in all_raw]
                scoped, _, uncertain = module_candidates(scope_rows, selected_modules, allowed_epics,
                                                        self.client.s.epic_link_field_id, all_raw, **module_scope_options)
                scoped_keys = {raw["key"] for raw in scoped}
                cp["moduleScopeUnknown"] = sorted(uncertain)
                active = [issue for issue in active if issue["key"] in scoped_keys]
            active_keys = {i["key"] for i in active}
            event_list = [e for i in active for e in i["events"]]
            missing = [m for key, record in analyzed.items() for m in record["missing"]
                       if key in active_keys or record["issue"].get("scopeUncertain")]
            # Candidate completeness is global: unclassified/out-of-scope rows with missing
            # history cannot be silently discarded merely because their scope is unproven.
            for key, record in analyzed.items():
                if not record["issue"]["coverage"]["complete"]:
                    for m in record["missing"]:
                        if m not in missing:
                            missing.append(m)
            candidates = cp.get("candidateKeys", [])
            pending = [key for key in candidates if key not in analyzed]
            missing += [{"issueKey": key, "source": "issue", "reason": "pending"} for key in pending]
            missing += [{"issueKey": key, "source": "moduleScope", "reason": "unresolvedCurrentHierarchy"} for key in cp.get("moduleScopeUnknown", [])]
            complete = queries_complete and not missing and len(analyzed) == len(candidates) and snapshot["status"] == "running" and not snapshot["errors"]
            ancestors = []
            needed = {i.get("parentKey") for i in active} | {i.get("epicKey") for i in active}
            while needed:
                key = needed.pop()
                if not key or key in active_keys:
                    continue
                active_keys.add(key)
                if key in all_raw:
                    raw = all_raw[key]
                    parent_issue, _, _ = analyze_issue(raw, [], start, upper, set(), {}, self.client.s.epic_link_field_id, False, False)
                    parent_issue.update(events=[], eventIds=[], contextOnly=True)
                    ancestors.append(parent_issue)
                    needed.update((parent_issue.get("parentKey"), parent_issue.get("epicKey")))
            result = build_result(active + ancestors, event_list, plan, request["modules"], complete)
            snapshot.update(result)
            snapshot["coverage"] = {"complete": complete, "queriesComplete": queries_complete,
                "issuesTotal": len(candidates), "issuesComplete": sum(bool(r["issue"]["coverage"]["complete"]) for r in analyzed.values()),
                "candidatesDiscovered": cp.get("discoveredTotal", len(candidates)),
                "historyComplete": queries_complete and not any(m["source"] in ("history", "statusCategories", "assignmentIdentity", "issue") for m in missing),
                "scopeComplete": queries_complete and not any(m["source"] in ("assignmentIdentity", "moduleScope", "issue") for m in missing),
                "commentsComplete": queries_complete and not any(m["source"] in ("comments", "issue") for m in missing),
                "missing": missing, "fieldChanges": "opportunistic", "commentEdits": "latestObservedOnly",
                "hierarchy": "currentConfiguration", "cachePolicy": {"hotSeconds": source.HOT_TTL, "coolDays": 7},
                "sourceFetchedAt": [datetime.fromtimestamp(t, KST).isoformat() for t in sorted(set(source.source_times))]}
            snapshot["coverage"]["candidateStrategy"] = cp.get("candidateStrategy", "pending")
            if snapshot["events"] and snapshot["metrics"]["firstResultMs"] is None:
                snapshot["metrics"]["firstResultMs"] = initial_elapsed + round((time.monotonic() - started) * 1000)
            cp["analyzed"] = analyzed
            persist()

        try:
            with background_upstream():
                check()
                categories = source.statuses()
                projects = getattr(self.client.s, "search_jira_projects", [self.client.s.project_key])
                if not projects:
                    raise ValueError("Jira 검색 프로젝트 범위를 설정해 주세요.")
                def quote(value):
                    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
                # Jira date literals use the user's server timezone. One conservative day
                # before KST midnight prevents excluding boundary activity; local comparison
                # remains exact. There is intentionally no updated upper bound.
                lower = (start - timedelta(days=1)).date().isoformat()
                base_jql = "project in (%s) AND updated >= %s" % (",".join(quote(p) for p in projects), quote(lower))
                query_cp = cp["queries"].setdefault("candidates", {})
                discovery_fields = ",".join(["updated", "components", "parent", "issuetype", self.client.s.epic_link_field_id] +
                                            (["assignee"] if selected_modules else []))
                capability_key = source.key("capability", "historicalPeopleSelector")
                capability = self.store.source(capability_key)
                selected_people = request["people"]
                use_selector = bool(selected_people) and not (capability and capability["data"] is False) and not cp.get("selectorUnsupported")
                discovered = []
                if use_selector:
                    # Jira DC 8.20 updatedBy includes creation, field updates, comments,
                    # and their last edit. WAS IN separately covers period responsibility.
                    # Date-only predicates are deliberately wider than the exact KST window.
                    upper_date = (upper.astimezone(KST) + timedelta(days=1)).date().isoformat()
                    try:
                        for offset in range(0, len(selected_people), 25):
                            group = selected_people[offset:offset + 25]
                            selector = ["assignee WAS IN (%s) DURING (%s,%s)" % (",".join(quote(p) for p in group), quote(lower), quote(upper_date))]
                            selector += ["issuekey IN updatedBy(%s,%s,%s)" % (quote(p), quote(lower), quote(upper_date)) for p in group]
                            scoped_jql = base_jql + " AND (" + " OR ".join(selector) + ") ORDER BY key ASC"
                            group_cp = cp["queries"].setdefault("peopleCandidates:" + str(offset), {})
                            discovered += source.search(scoped_jql, discovery_fields, group_cp, persist, expand=False, allow_cache=False)
                        query_cp["complete"] = True
                        cp["candidateStrategy"] = "periodAssigneeAndActor"
                    except UpstreamError as exc:
                        if exc.status != 400:
                            raise
                        cp["selectorUnsupported"] = True
                        self.store.put_source(capability_key, False, source.COOL_TTL)
                        use_selector = False
                        discovered = []
                if not use_selector:
                    cp["candidateStrategy"] = "broadFallback" if selected_people else "projectActivity"
                    broad_cp = cp["queries"].setdefault("candidatesBroad", {})
                    discovered = source.search(base_jql + " ORDER BY key ASC", discovery_fields, broad_cp, persist, expand=False, allow_cache=False)
                    query_cp["complete"] = bool(broad_cp.get("complete"))
                discovered = list({raw["key"]: raw for raw in discovered}.values())
                cp["discoveredTotal"] = len(discovered)
                if selected_modules:
                    parents = cp.setdefault("scopeParents", {})
                    attempted = set()
                    cp["scopeParentMissing"] = []
                    while True:
                        scoped, needed, uncertain = module_candidates(discovered, selected_modules, allowed_epics,
                                                                     self.client.s.epic_link_field_id, parents, **module_scope_options)
                        needed = sorted(needed - attempted)
                        if not needed:
                            break
                        for offset in range(0, len(needed), 100):
                            keys = needed[offset:offset + 100]
                            label = "scopeParents:" + ",".join(keys)
                            parent_cp = cp["queries"].setdefault(label, {})
                            if parent_cp.get("complete") and not parent_cp.get("issues"):
                                parent_cp.clear()
                            fetched = source.search("key in (%s) ORDER BY key ASC" % ",".join(quote(k) for k in keys),
                                discovery_fields, parent_cp, persist, expand=False, allow_cache=False)
                            parents.update({row["key"]: {**row, "_trackingLightOnly": True} for row in fetched})
                            cp["scopeParentMissing"].extend(set(keys) - {row["key"] for row in fetched})
                            attempted.update(keys)
                    cp["moduleScopeUnknown"] = sorted(uncertain)
                    cp["issues"].update(parents)
                    discovered = scoped
                rows = source.hydrate(discovered, fields, cp["queries"], persist)
                previous_candidates = set(cp.get("candidateKeys", []))
                cp["candidateKeys"] = [raw["key"] for raw in rows]
                current_candidates = set(cp["candidateKeys"])
                analyzed = {key: record for key, record in analyzed.items() if key in current_candidates}
                for key in previous_candidates - current_candidates:
                    cp["issues"].pop(key, None)
                cp["issues"].update({raw["key"]: raw for raw in rows})
                aliases = {str(uid).casefold(): str(uid).casefold() for ids in self.people_loader().values() for uid in ids}
                for raw in rows:
                    aliases.update(identity_aliases(raw))
                persist()
                def collect(raw):
                    check()
                    key = raw["key"]
                    old = analyzed.get(key)
                    if old and old["issue"]["coverage"]["complete"]:
                        return key, old
                    issue_cp = cp["sources"].setdefault(key, {})
                    local_raw = dict(raw)
                    errors = []
                    try:
                        history, history_complete = source.history(raw, issue_cp, persist, fields)
                    except (SessionExpired, UpstreamUnavailable, PermissionError):
                        raise
                    except Exception as exc:
                        history, history_complete = issue_cp.get("history") or {"histories": []}, False
                        errors.append({"issueKey": key, "source": "history", "reason": type(exc).__name__})
                    local_raw["changelog"] = history
                    try:
                        comments, comments_complete = source.comments(raw, issue_cp, persist)
                    except (SessionExpired, UpstreamUnavailable, PermissionError):
                        # Preserve completed history even if the comments transport disconnects.
                        issue, events, missing = analyze_issue(local_raw, issue_cp.get("comments", []), start, upper, set(request["people"]), categories, self.client.s.epic_link_field_id, history_complete, False, aliases)
                        analyzed[key] = {"issue": issue, "missing": missing}
                        cp["analyzed"] = analyzed
                        persist()
                        raise
                    except Exception as exc:
                        comments, comments_complete = issue_cp.get("comments", []), False
                        errors.append({"issueKey": key, "source": "comments", "reason": type(exc).__name__})
                    issue, events, missing = analyze_issue(local_raw, comments, start, upper, set(request["people"]), categories, self.client.s.epic_link_field_id, history_complete, comments_complete, aliases)
                    return key, {"issue": issue, "missing": missing + errors}

                published_at = time.monotonic()
                parallel = getattr(self.client.provider, "supports_parallel", False)
                results = self.client._query_scheduler.run_bounded(rows, collect, parallel_capable=parallel, cancelled=signal)
                for index, (key, record) in enumerate(results):
                    analyzed[key] = record
                    if index == 0 or (index + 1) % 25 == 0 or time.monotonic() - published_at >= 0.5:
                        publish(True)
                        published_at = time.monotonic()
                # A DC key/name pair may appear only in another issue's fallback history
                # or comments. Interpret unresolved ownership again after sharing every
                # already-collected immutable identity pair; this performs zero REST.
                for raw in rows:
                    issue_cp = cp["sources"].get(raw["key"], {})
                    full_raw = {**raw, "changelog": issue_cp.get("history") or raw.get("changelog") or {}}
                    aliases.update(identity_aliases(full_raw, issue_cp.get("comments", [])))
                if request["people"]:
                    for raw in rows:
                        key = raw["key"]
                        record = analyzed.get(key)
                        if not record or not any(m["source"] == "assignmentIdentity" for m in record["missing"]):
                            continue
                        issue_cp = cp["sources"].get(key, {})
                        full_raw = {**raw, "changelog": issue_cp.get("history") or raw.get("changelog") or {}}
                        issue, _, missing = analyze_issue(full_raw, issue_cp.get("comments", []), start, upper, set(request["people"]), categories,
                            self.client.s.epic_link_field_id, bool(issue_cp.get("historyComplete")), bool(issue_cp.get("commentsComplete")), aliases)
                        analyzed[key] = {"issue": issue, "missing": missing}
                # Fetch missing current ancestors in batches. This is hierarchy context,
                # never a reason to fetch extra history or comments.
                previously_missing = cp.pop("hierarchyMissing", [])
                for label in list(cp["queries"]):
                    if label.startswith("ancestors:") and any(k in label.removeprefix("ancestors:").split(",") for k in previously_missing):
                        cp["queries"].pop(label)
                checked = {key for key, raw in cp["issues"].items() if not raw.get("_trackingLightOnly")}
                checked.update(cp.get("scopeParentMissing", []))
                while True:
                    needed = {(r.get("fields") or {}).get(self.client.s.epic_link_field_id) for r in cp["issues"].values()}
                    needed |= {((r.get("fields") or {}).get("parent") or {}).get("key") for r in cp["issues"].values()}
                    needed = sorted(k for k in needed if k and k not in checked)
                    if not needed:
                        break
                    for offset in range(0, len(needed), 100):
                        keys = needed[offset:offset + 100]
                        label = "ancestors:" + ",".join(keys)
                        hierarchy_cp = cp["queries"].setdefault(label, {})
                        fetched = source.search("key in (%s) ORDER BY key ASC" % ",".join(quote(k) for k in keys), fields.replace(",comment", ""), hierarchy_cp, persist, expand=False, allow_cache=False)
                        cp["issues"].update({raw["key"]: raw for raw in fetched})
                        checked.update(keys)
                        absent = set(keys) - {raw["key"] for raw in fetched}
                        if absent:
                            cp.setdefault("hierarchyMissing", []).extend(sorted(absent))
                if selected_modules:
                    _, _, uncertain = module_candidates(rows, selected_modules, allowed_epics, self.client.s.epic_link_field_id,
                                                        cp["issues"], **module_scope_options)
                    cp["moduleScopeUnknown"] = sorted(uncertain)
                publish(True)
                if cp.get("hierarchyMissing"):
                    snapshot["coverage"]["complete"] = False
                    snapshot["coverage"]["missing"] += [{"issueKey": k, "source": "hierarchy", "reason": "unavailable"} for k in set(cp["hierarchyMissing"])]
                    snapshot["summary"]["provisional"] = True
                snapshot["status"] = "complete" if snapshot["coverage"]["complete"] else "partial"
        except CancelledError:
            snapshot["status"] = "cancelled"
            publish(bool(cp["queries"].get("candidates", {}).get("complete")))
        except (SessionExpired, UpstreamUnavailable, PermissionError) as exc:
            snapshot["status"] = "paused"
            snapshot["errors"] = [{"source": "jira", "reason": type(exc).__name__, "needLogin": isinstance(exc, SessionExpired),
                                   "message": "Jira 연결 또는 권한을 확인한 뒤 이어서 수집해 주세요."}]
            publish(bool(cp["queries"].get("candidates", {}).get("complete")))
        except Exception as exc:
            snapshot["status"] = "partial"
            snapshot["errors"] = [{"source": "collection", "reason": type(exc).__name__,
                                   "message": str(exc) if isinstance(exc, (ValueError, IncompleteSource)) else "수집을 완료하지 못했습니다. 이어서 수집할 수 있습니다."}]
            publish(False)
        finally:
            persist()
