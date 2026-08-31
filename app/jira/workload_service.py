"""Workload rollups, workload ticket buckets, and user activity for JiraClient."""

from datetime import date
import re
import xml.etree.ElementTree as ET

from app.auth.base import SessionExpired
from app.domain import progress
from app.domain.mytasks import pri_rank
from app.domain.progress import VOC_COMPONENT, norm_cat


def workload_category(component, issue_type, is_subtask=None):
    """Map Jira issue metadata to the workload chart's three supported categories."""
    if component == VOC_COMPONENT:
        return "voc"
    if is_subtask if is_subtask is not None else (issue_type == "Sub-Task"):
        return "subtask"
    if issue_type == "Task":
        return "task"
    return None


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
        """Resolve SubTask Epic membership through its parent with one light prefetch."""
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
        if parent_keys:
            self.prefetch_issues(parent_keys, light=True)
            parent_epics = {}
            for parent_key in parent_keys:
                parent_fields = (self.get_issue_light(parent_key) or {}).get("fields") or {}
                parent_epics[parent_key] = (
                    parent_fields.get(epic_field) if epic_field else None
                ) or None
            for key, parent_key in parent_by_issue.items():
                result[key] = parent_epics.get(parent_key)
        return result

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
        effective_epics = self._wl_effective_epics(issues)
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
            epic_key = effective_epics.get(issue.get("key"))
            group_key = epic_key if epic_key else (
                "__voc__" if VOC_COMPONENT in components else "__none__"
            )
            group = result["epics"].setdefault(group_key, {"count": 0, "hr": 0})
            group["count"] += 1
            group["hr"] += hours
        return result

    def workload_person(self, user_id, done_days=None, assigned_window=None):
        done_days = self.wl_done_days(done_days)
        assigned_window = self.wl_assigned_window(assigned_window)
        cache_key = (f"workload:{self.env}:{user_id}:done:{done_days}:"
                     f"assigned:{assigned_window}")
        bundle = self.cache.get(cache_key)
        if bundle is None:
            try:
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
                epic_keys = sorted({
                    key
                    for bucket in ("open", "inProgress", "done7d")
                    for key in bundle[bucket]["epics"]
                    if not key.startswith("__")
                })
                bundle["epicNames"] = self._epic_name_map(epic_keys)
                self.cache.set(cache_key, bundle, self.s.cache_ttl_seconds)
            except SessionExpired:
                raise
            except Exception:
                bundle = {
                    "id": user_id,
                    "error": True,
                    "open": self._wl_zero(),
                    "inProgress": self._wl_zero(),
                    "done7d": self._wl_zero(),
                }
        return dict(bundle, displayName=self._display_name(user_id))

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
            effective_epics = self._wl_effective_epics(issues)
            result = [
                self._wl_ticket(issue, effective_epics.get(issue.get("key")))
                for issue in issues
                if self._wl_keep(issue)
            ]
            self._attach_epic_names(result)
            return result

        suffix = f":{done_days}" if bucket == "done7d" else f":{assigned_window}"
        return self.cache.get_or_set(
            f"workload_bucket:{self.env}:{user}:{bucket}{suffix}",
            self.s.cache_ttl_seconds,
            produce,
        )[0]

    @staticmethod
    def epic_label(badge, key):
        badge = badge or {}
        return badge.get("epicName") or badge.get("summary") or key

    def _epic_name_map(self, keys):
        keys = sorted({key for key in (keys or []) if key})
        if not keys:
            return {}
        try:
            self.prefetch_issues(keys, light=True)
        except Exception:
            pass
        names = {}
        for key in keys:
            try:
                badge = self.ticket_badge(key)
            except Exception:
                badge = None
            names[key] = self.epic_label(badge, key)
        return names

    def _attach_epic_names(self, tickets):
        names = self._epic_name_map([
            ticket.get("epic") for ticket in tickets if ticket.get("epic")
        ])
        for ticket in tickets:
            if ticket.get("epic"):
                ticket["epicName"] = names.get(ticket["epic"], ticket["epic"])

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
        cache_key = f"activity:{self.env}:{user}"
        return self.cache.get_or_set(
            cache_key,
            self.s.cache_ttl_seconds,
            lambda: self._fetch_activity(user),
        )[0]

    def _fetch_activity(self, user):
        return {
            "user": user,
            "jira": self._parse_activity(user),
            "confluence": self._fetch_confluence(user),
        }

    def _parse_activity(self, user, limit=20):
        atom = "{http://www.w3.org/2005/Atom}"
        activity = "{http://activitystrea.ms/spec/1.0/}"
        result = []
        try:
            raw = self.provider.get_text(
                "/activity", params={"maxResults": limit, "streams": f"user IS {user}"},
            )
            root = ET.fromstring(raw)
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
        except Exception:
            pass
        return result

    def _fetch_confluence(self, user):
        if not self.s.confluence_base:
            return []
        try:
            data = self.provider.get_json("/rest/api/content/search", params={
                "cql": f'contributor = "{user}" and lastmodified >= now("-14d")',
                "expand": "version,space",
                "limit": 25,
            })
            return [{
                "date": ((row.get("version") or {}).get("when") or ""),
                "title": row.get("title", ""),
                "space": ((row.get("space") or {}).get("key") or ""),
            } for row in data.get("results", [])]
        except Exception:
            return []
