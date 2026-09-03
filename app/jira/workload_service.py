"""Workload rollups, workload ticket buckets, and user activity for JiraClient."""

from datetime import date
import re
import xml.etree.ElementTree as ET

from app.auth.base import SessionExpired, UpstreamUnavailable
from app.domain import progress
from app.domain.mytasks import pri_rank
from app.domain.progress import VOC_COMPONENT, norm_cat


class _UncacheableWorkload(RuntimeError):
    """Carry a usable partial projection through a cache producer without storing it."""

    def __init__(self, value, missing):
        super().__init__("workload parent Epic resolution is incomplete")
        self.value = value
        self.missing = list(missing or [])


def workload_category(component, issue_type, is_subtask=None):
    """Map every My Tasks execution-ticket type into the workload chart."""
    if component == VOC_COMPONENT:
        return "voc"
    if is_subtask if is_subtask is not None else (issue_type == "Sub-Task"):
        return "subtask"
    normalized_type = str(issue_type or "").strip().casefold()
    if not normalized_type or normalized_type == "epic":
        return None
    # My Tasks treats every non-Epic top-level issue as executable work.  Keep the
    # workload projection aligned so Story/Bug/Improvement/custom Jira types do not
    # disappear after the broad assignee/status search has already returned them.
    return "task"


class JiraWorkloadMixin:
    """Workload-facing JiraClient methods backed by the owner's search and issue caches."""

    WL_DONE_DAYS = (7, 14, 28)
    WL_DONE_DEFAULT = 7
    WL_ASSIGNED_WINDOWS = ("1w", "1m", "all")
    WL_ASSIGNED_DEFAULT = "all"
    WL_ASSIGNED_DAYS = {"1w": 7, "1m": 30}
    WL_BUCKETS = {
        # Keep these as the broad, canonical status leaves used by MyTasks' ``all`` filters.
        # Workload applies its 1w/1m window locally so every window can reuse the same JQL leaf
        # membership and issueL rows instead of creating one Jira cache lineage per date range.
        "open": 'assignee = "{u}" AND statusCategory = "To Do"',
        "inProgress": 'assignee = "{u}" AND statusCategory = "In Progress"',
    }

    def epic_progress_one(self, key):
        result = progress.epic_progress(self.epic_issues(key))
        result["name"] = self.epic_name(key)
        return result

    def workload(self, plan, people):
        return self._fetch_workload(plan, people)

    @staticmethod
    def _wl_zero():
        return {
            "count": {"task": 0, "subtask": 0, "voc": 0},
            "hr": {"task": 0, "subtask": 0, "voc": 0},
            "epics": {},
        }

    def _wl_effective_epics(self, issues):
        """Resolve SubTask Epic membership without turning one hidden parent into total failure."""
        issues = list(issues or [])
        epic_field = self.s.epic_link_field_id
        result = {}
        parent_by_issue = {}
        for issue in issues:
            key = issue.get("key")
            if not key:
                continue
            fields = issue.get("fields", {}) or {}
            direct = (fields.get(epic_field) if epic_field else None) or None
            result[key] = direct
            issue_type = fields.get("issuetype") or {}
            parent_key = (fields.get("parent") or {}).get("key")
            if not direct and issue_type.get("subtask") and parent_key:
                parent_by_issue[key] = parent_key

        parent_keys = sorted(set(parent_by_issue.values()))
        missing_by_issue = {}
        if parent_keys:
            batch = self.issues_by_keys_result(parent_keys, light=True)
            parent_epics = {
                row.get("key"): (((row.get("fields") or {}).get(epic_field)
                                  if epic_field else None) or None)
                for row in batch.get("issues", [])
                if row.get("key")
            }
            missing_parents = {
                row.get("key"): row for row in batch.get("missing", []) if row.get("key")
            }
            for key, parent_key in parent_by_issue.items():
                if parent_key in parent_epics:
                    result[key] = parent_epics[parent_key]
                    continue
                raw = missing_parents.get(parent_key) or {
                    "key": parent_key,
                    "kind": "other",
                    "error": "Jira parent issue was not returned",
                }
                missing_by_issue[key] = {
                    "issueKey": key,
                    "parentKey": parent_key,
                    "kind": raw.get("kind") or "other",
                    "retryable": (raw.get("kind") or "other")
                    not in ("permission", "unavailable"),
                    "message": str(raw.get("error") or "")[:240],
                }
        return result, missing_by_issue

    @staticmethod
    def _wl_missing_parents(missing_by_issue):
        grouped = {}
        for row in (missing_by_issue or {}).values():
            parent_key = row.get("parentKey")
            if not parent_key:
                continue
            current = grouped.setdefault(parent_key, {
                "parentKey": parent_key,
                "issueKeys": [],
                "kind": row.get("kind") or "other",
                "retryable": bool(row.get("retryable")),
                "message": row.get("message") or "",
            })
            current["issueKeys"].append(row.get("issueKey"))
        return [grouped[key] for key in sorted(grouped)]

    @staticmethod
    def _wl_retry_kind(missing):
        kinds = {row.get("kind") or "other" for row in (missing or [])
                 if row.get("retryable")}
        for kind in ("auth", "transport", "other"):
            if kind in kinds:
                return kind
        return None

    @staticmethod
    def _wl_raise_retryable(missing):
        kind = JiraWorkloadMixin._wl_retry_kind(missing)
        if not kind:
            return
        message = next((row.get("message") for row in missing
                        if row.get("kind") == kind and row.get("message")), "")
        if kind == "auth":
            raise SessionExpired(message or "Jira parent issue authentication failed")
        if kind == "transport":
            raise UpstreamUnavailable(message or "Jira parent issue request failed")
        raise RuntimeError(message or "Jira parent issue request failed")

    def _wl_counts(self, jql, assigned_window=None):
        result = {
            "count": {"task": 0, "subtask": 0, "voc": 0},
            "hr": {"task": 0, "subtask": 0, "voc": 0},
            "epics": {},
        }
        # The normalized JQL layer already exhaustively caches the leaf.  Do not truncate that
        # warm result before workload-category filtering; the old 300 cap could hide valid Tasks
        # merely because unrelated issue types occupied earlier rows.
        issues = self._search(jql, max_results=None)
        if assigned_window is not None:
            issues = [issue for issue in issues
                      if self._wl_in_assigned_window(issue, assigned_window)]
        effective_epics, missing_by_issue = self._wl_effective_epics(issues)
        for issue in issues:
            fields = issue.get("fields", {}) or {}
            components = [row.get("name") for row in (fields.get("components") or [])]
            component = VOC_COMPONENT if VOC_COMPONENT in components else (
                components[0] if components else ""
            )
            issue_type = fields.get("issuetype") or {}
            category = workload_category(
                component, issue_type.get("name", ""), issue_type.get("subtask"),
            )
            if not category:
                continue
            hours = round((fields.get("timespent") or 0) / 3600.0, 1)
            result["count"][category] += 1
            result["hr"][category] += hours
            # A failed parent read does not prove that the SubTask has no Epic. Keep it in the
            # category totals, but do not misclassify it into the authoritative ``__none__`` group.
            if issue.get("key") in missing_by_issue:
                continue
            epic_key = effective_epics.get(issue.get("key"))
            group_key = epic_key if epic_key else (
                "__voc__" if VOC_COMPONENT in components else "__none__"
            )
            group = result["epics"].setdefault(group_key, {"count": 0, "hr": 0})
            group["count"] += 1
            group["hr"] += hours
        missing = self._wl_missing_parents(missing_by_issue)
        if missing:
            result.update({
                "partial": True,
                "missingParents": missing,
                "retryable": bool(self._wl_retry_kind(missing)),
            })
        return result

    def workload_person(self, user_id, done_days=None, assigned_window=None):
        done_days = self.wl_done_days(done_days)
        assigned_window = self.wl_assigned_window(assigned_window)
        cache_key = (f"workload:{self.env}:{user_id}:done:{done_days}:"
                     f"assigned:{assigned_window}")
        def produce():
            bundle = {
                "id": user_id,
                "open": self._wl_counts(
                    self.WL_BUCKETS["open"].format(u=user_id), assigned_window
                ),
                "inProgress": self._wl_counts(
                    self.WL_BUCKETS["inProgress"].format(u=user_id), assigned_window
                ),
                "done7d": self._wl_counts(
                    f'assignee = "{user_id}" AND ' + self.wl_done_jql(done_days)
                ),
                "assignedWindow": assigned_window,
                "doneDays": done_days,
            }
            missing = []
            for bucket in ("open", "inProgress", "done7d"):
                for row in bundle[bucket].get("missingParents", []):
                    missing.append(dict(row, bucket=bucket))
            if missing:
                bundle.update({
                    "partial": True,
                    "missingParents": missing,
                    "retryable": bool(self._wl_retry_kind(missing)),
                })
                raise _UncacheableWorkload(bundle, missing)
            return bundle

        try:
            bundle = self.cache.get_or_set(
                cache_key, self.s.cache_ttl_seconds, produce,
            )[0]
        except _UncacheableWorkload as exc:
            bundle = exc.value
            retry_kind = self._wl_retry_kind(exc.missing)
            if retry_kind:
                bundle.update({
                    "errorKind": retry_kind,
                    "message": next((row.get("message") for row in exc.missing
                                     if row.get("kind") == retry_kind
                                     and row.get("message")),
                                    "일부 SubTask의 상위 Epic을 확인하지 못했습니다."),
                })
        except SessionExpired:
            raise
        except UpstreamUnavailable as exc:
            bundle = {
                "id": user_id,
                "error": True,
                "errorKind": "network",
                "message": str(exc)[:240],
                "open": self._wl_zero(),
                "inProgress": self._wl_zero(),
                "done7d": self._wl_zero(),
            }
        except Exception as exc:
            status = int(getattr(exc, "status", 0) or 0)
            bundle = {
                "id": user_id,
                "error": True,
                "errorKind": "permission" if status == 403 else "other",
                "status": status or None,
                "message": str(exc)[:240],
                "open": self._wl_zero(),
                "inProgress": self._wl_zero(),
                "done7d": self._wl_zero(),
            }
        # Epic labels are intentionally a read-time projection over the shared 12-hour cache.
        # They must not be frozen inside this shorter workload aggregate: an Epic rename now only
        # needs to invalidate ``epicmeta:{env}:{key}``, and a temporary miss stays retryable.
        result = dict(bundle)
        epic_keys = sorted({
            key
            for bucket in ("open", "inProgress", "done7d")
            for key in (result.get(bucket, {}).get("epics") or {})
            if not key.startswith("__")
        })
        result["epicNames"] = self._epic_name_map(epic_keys)
        return dict(result, displayName=self._display_name(user_id))

    def _fetch_workload(self, plan, people):
        modules = list(people)
        user_ids = [user_id for module in modules for user_id in people.get(module, [])]
        by_user = {
            bundle["id"]: bundle
            for bundle in self._pmap(user_ids, self.workload_person)
        }
        return {
            module: [by_user[user_id] for user_id in people.get(module, []) if user_id in by_user]
            for module in modules
        }

    def _wl_ticket(self, issue, epic=None):
        fields = issue.get("fields", {}) or {}
        status = fields.get("status") or {}
        issue_type = fields.get("issuetype") or {}
        type_name = "Sub-Task" if issue_type.get("subtask") else issue_type.get("name", "")
        components = [
            row.get("name") for row in (fields.get("components") or []) if row.get("name")
        ]
        priority = (fields.get("priority") or {}).get("name") or None
        return {
            "key": issue.get("key", ""),
            "summary": fields.get("summary", ""),
            "type": type_name,
            "status": status.get("name", ""),
            "statusCategory": norm_cat((status.get("statusCategory") or {}).get("key")),
            "due": fields.get("duedate") or None,
            "resolved": fields.get("resolutiondate") or None,
            "priority": priority,
            "priRank": pri_rank(priority),
            "epic": epic or fields.get(self.s.epic_link_field_id) or None,
            "voc": self.s.voc_component in components,
            "components": components,
        }

    @staticmethod
    def wl_done_days(days):
        try:
            value = int(days)
        except Exception:
            return JiraWorkloadMixin.WL_DONE_DEFAULT
        return value if value in JiraWorkloadMixin.WL_DONE_DAYS else JiraWorkloadMixin.WL_DONE_DEFAULT

    @staticmethod
    def wl_done_jql(days):
        days = JiraWorkloadMixin.wl_done_days(days)
        return (
            'statusCategory = Done AND (resolved >= -%dd '
            'OR (resolved IS EMPTY AND updated >= -%dd))' % (days, days)
        )

    @staticmethod
    def wl_assigned_window(window):
        value = str(window or "").strip().lower()
        return (value if value in JiraWorkloadMixin.WL_ASSIGNED_WINDOWS
                else JiraWorkloadMixin.WL_ASSIGNED_DEFAULT)

    def _wl_in_assigned_window(self, issue, window):
        """Apply the Open/In-Progress updated window to a shared broad status leaf."""
        window = self.wl_assigned_window(window)
        days = self.WL_ASSIGNED_DAYS.get(window)
        if days is None:
            return True
        raw = ((issue or {}).get("fields") or {}).get("updated") or ""
        try:
            updated = date.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError):
            return False
        today = self.s_today() if hasattr(self, "s_today") else date.today()
        return (today - updated).days <= days

    def workload_bucket(self, user, bucket, days=None, assigned_window=None):
        done_days = self.wl_done_days(days)
        assigned_window = self.wl_assigned_window(assigned_window)
        if bucket == "done7d":
            jql = 'assignee = "{u}" AND ' + self.wl_done_jql(done_days)
        else:
            jql = self.WL_BUCKETS.get(bucket)
        if not jql:
            return None

        def produce():
            # Fetch the complete broad status leaf so MyTasks and every assigned-window choice
            # share one membership cache.  Windowing is a cheap local projection below.
            issues = self._search(jql.format(u=user), max_results=None)
            if bucket != "done7d":
                issues = [issue for issue in issues
                          if self._wl_in_assigned_window(issue, assigned_window)]
            effective_epics, missing_by_issue = self._wl_effective_epics(issues)
            result = [
                self._wl_ticket(issue, effective_epics.get(issue.get("key")))
                for issue in issues
                if self._wl_keep(issue)
            ]
            for row in result:
                missing = missing_by_issue.get(row.get("key"))
                if missing:
                    row["epicResolution"] = {
                        "complete": False,
                        "parentKey": missing.get("parentKey"),
                        "kind": missing.get("kind"),
                        "retryable": bool(missing.get("retryable")),
                    }
            missing = self._wl_missing_parents(missing_by_issue)
            if missing:
                raise _UncacheableWorkload(result, missing)
            return result

        suffix = f":{done_days}" if bucket == "done7d" else f":{assigned_window}"
        try:
            rows = self.cache.get_or_set(
                f"workload_bucket:{self.env}:{user}:{bucket}{suffix}",
                self.s.cache_ttl_seconds,
                produce,
            )[0]
        except _UncacheableWorkload as exc:
            rows = exc.value
        # Copy before decorating: old cache rows may contain an embedded name from a previous app
        # version, and mutating the cached list would recreate the same stale-name problem.
        result = []
        for row in rows or []:
            item = dict(row)
            item.pop("epicName", None)
            result.append(item)
        self._attach_epic_names(result)
        return result

    def _epic_name_map(self, keys):
        keys = sorted({key for key in (keys or []) if key})
        if not keys:
            return {}
        return self.epic_metadata_title_map(keys, best_effort=True)

    def _attach_epic_names(self, tickets):
        epic_keys = sorted({
            ticket.get("epic") for ticket in tickets if ticket.get("epic")
        })
        names = self._epic_name_map(epic_keys)
        for ticket in tickets:
            name = names.get(ticket.get("epic"))
            if name:
                ticket["epicName"] = name
        return len(names) == len(epic_keys)

    def _wl_keep(self, issue):
        fields = issue.get("fields", {}) or {}
        components = [row.get("name") for row in (fields.get("components") or [])]
        component = VOC_COMPONENT if VOC_COMPONENT in components else (
            components[0] if components else ""
        )
        issue_type = fields.get("issuetype") or {}
        return workload_category(
            component, issue_type.get("name", ""), issue_type.get("subtask"),
        ) is not None

    def workload_tickets(self, user, days=None, assigned_window=None):
        return {
            "user": user,
            "assignedWindow": self.wl_assigned_window(assigned_window),
            "open": self.workload_bucket(user, "open", assigned_window=assigned_window),
            "inProgress": self.workload_bucket(
                user, "inProgress", assigned_window=assigned_window),
            "done7d": self.workload_bucket(user, "done7d", days),
        }

    def activity(self, user):
        return self._fetch_activity(user)

    def _fetch_activity(self, user):
        result = {"user": user}
        source_errors = []
        sources = (
            ("jira", lambda: self._parse_activity(user)),
            ("confluence", lambda: self._fetch_confluence(user)),
        )
        for source, producer in sources:
            try:
                result[source] = self.cache.get_or_set_strict(
                    f"activity:{self.env}:{user}:{source}",
                    self.s.cache_ttl_seconds,
                    producer,
                )[0]
            except Exception as exc:
                # A dead session or transport failure belongs to the existing HTTP/UI retry path.
                # The source that already succeeded remains warm, so that retry is source-targeted.
                if isinstance(exc, (SessionExpired, UpstreamUnavailable,
                                    TimeoutError, ConnectionError)):
                    raise
                if self._is_permission_failure(exc):
                    kind = "permission"
                elif isinstance(exc, (ET.ParseError, ValueError, TypeError, AttributeError)):
                    kind = "malformed"
                else:
                    raise
                result[source] = []
                source_errors.append({
                    "source": source,
                    "kind": kind,
                    "retryable": False,
                    "message": str(exc)[:240],
                })
        result["partial"] = bool(source_errors)
        result["sourceErrors"] = source_errors
        return result

    def _parse_activity(self, user, limit=20):
        atom = "{http://www.w3.org/2005/Atom}"
        activity = "{http://activitystrea.ms/spec/1.0/}"
        raw = self.provider.get_text(
            "/activity", params={"maxResults": limit, "streams": f"user IS {user}"},
        )
        root = ET.fromstring(raw)
        result = []
        for entry in root.findall(f"{atom}entry"):
            category = entry.find(f"{atom}category")
            kind = category.get("term") if category is not None else ""
            key = ""
            for link in entry.findall(f"{atom}link"):
                if link.get("rel") == "alternate":
                    match = re.search(
                        r"/browse/([A-Z][A-Z0-9]+-\d+)", link.get("href") or "",
                    )
                    if match:
                        key = match.group(1)
            obj = entry.find(f"{activity}object")
            summary = ""
            if obj is not None:
                summary = (obj.findtext(f"{atom}summary") or "").strip()
                if not key:
                    key = (obj.findtext(f"{atom}title") or "").strip()
            if not summary:
                title = re.sub(r"<[^>]+>", "", entry.findtext(f"{atom}title") or "")
                summary = title.split(" - ", 1)[1].strip() if " - " in title else title.strip()
            result.append({
                "date": entry.findtext(f"{atom}updated") or entry.findtext(f"{atom}published") or "",
                "kind": kind,
                "key": key,
                "summary": summary,
            })
        return result

    def _fetch_confluence(self, user):
        if not self.s.confluence_base:
            return []
        data = self.provider.get_json("/rest/api/content/search", params={
            "cql": f'contributor = "{user}" and lastmodified >= now("-14d")',
            "expand": "version,space",
            "limit": 25,
        })
        if not isinstance(data, dict) or not isinstance(data.get("results", []), list):
            raise ValueError("Confluence activity response is malformed")
        return [{
            "date": ((row.get("version") or {}).get("when") or ""),
            "title": row.get("title", ""),
            "space": ((row.get("space") or {}).get("key") or ""),
        } for row in data.get("results", [])]
