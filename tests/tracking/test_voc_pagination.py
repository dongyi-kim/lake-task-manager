"""Module VoC collection consumes every Jira discovery and rich-source page."""
from copy import deepcopy
import re

import pytest

from test_backend_manager import manager


class PagedVocProvider:
    supports_parallel = False

    def __init__(self, page_cap):
        self.page_cap = page_cap
        self.calls = []
        self.rows = []
        for index in range(290):
            eligible = index >= 170
            key = f"P-{1000 + index}"
            self.rows.append({"key": key, "fields": {
                "summary": f"VoC {key}" if eligible else f"Other work {key}",
                "created": "2026-11-11T00:00:00+0900",
                "updated": "2026-11-11T12:00:00+0900",
                "creator": {"name": "reporter-bot"},
                "reporter": {"name": "reporter-bot"},
                "assignee": {"name": "etl.member" if eligible else "other.member"},
                "components": [{"name": "사용자 VoC" if eligible else "Other"}],
                "epic": None,
                "parent": None,
                "issuetype": {"name": "Task", "subtask": False},
                "status": {"id": "1", "name": "Ready", "statusCategory": {"key": "new"}},
                "comment": {"startAt": 0, "total": 1, "comments": [{
                    "id": "comment", "created": "2026-11-11T10:00:00+0900",
                    "author": {"name": "reporter-bot"}, "body": f"Update for {key}"}]},
            }, "changelog": {"startAt": 0, "total": 1, "histories": [{
                "id": "history", "created": "2026-11-11T11:00:00+0900",
                "author": {"name": "reporter-bot"}, "items": [{
                    "field": "summary", "fromString": "Old title", "toString": f"Updated {key}"}]}]}})
        self.eligible_keys = {row["key"] for row in self.rows[170:]}

    def get_json(self, path, params=None):
        params = params or {}
        self.calls.append((path, dict(params)))
        if path.endswith("/status"):
            return [{"id": "1", "name": "Ready", "statusCategory": {"key": "new"}}]
        assert path.endswith("/search"), "Complete embedded sources must avoid per-ticket requests."
        jql = params["jql"]
        if jql.startswith("key in ("):
            keys = set(re.findall(r'"(P-\d+)"', jql))
            assert keys <= self.eligible_keys, "Unrelated discovery rows must be pruned before hydration."
            rows = [row for row in self.rows if row["key"] in keys]
        else:
            assert "project in" in jql
            rows = self.rows
        offset = params["startAt"]
        size = min(params["maxResults"], self.page_cap)
        requested = set(params["fields"].split(","))
        page = deepcopy(rows[offset:offset + size])
        for row in page:
            row["fields"] = {field: value for field, value in row["fields"].items() if field in requested}
            if "changelog" not in params.get("expand", "").split(","):
                row.pop("changelog")
        return {"startAt": offset, "maxResults": size, "total": len(rows), "issues": page}


@pytest.mark.parametrize("page_cap", [50, 100], ids=["server-cap-50", "server-cap-100"])
def test_module_voc_has_no_global_candidate_or_hydration_limit(tmp_path, page_cap):
    provider = PagedVocProvider(page_cap)
    m = manager(tmp_path, provider)
    m.people_loader = lambda: {"ETL": ["etl.member"], "Other": ["other.member"]}
    # Match the live component explicitly so this test isolates pagination from
    # component defaults and membership policy. Only the assignee links VoC to ETL.
    m.client.s.voc_component = "사용자 VoC"
    request = {"startDate": "2026-11-10", "endDate": "2026-11-12", "modules": ["ETL"]}

    run = m.create(request, background=False)

    assert run["status"] == "complete", run["errors"]
    assert run["coverage"]["complete"] is True
    assert run["coverage"]["candidatesDiscovered"] == 290
    assert run["coverage"]["issuesTotal"] == run["coverage"]["issuesComplete"] == 120
    assert {issue["key"] for issue in run["issues"]} == provider.eligible_keys
    expected_events = {f"{key}:{suffix}" for key in provider.eligible_keys
                       for suffix in ("created", "history:history:0", "comment:comment:created")}
    assert {event["id"] for event in run["events"]} == expected_events
    assert run["summary"]["uniqueIssues"] == 120
    assert run["summary"]["created"] == run["summary"]["comments"] == run["summary"]["fieldChanges"] == 120
    assert run["summary"]["totalEvents"] == 360
    assert run["tree"][0]["counts"]["totalEvents"] == 360
    assert m.get(run["id"])["events"] == run["events"]
    assert m.list()[0]["summary"] == run["summary"]

    searches = [params for path, params in provider.calls if path.endswith("/search")]
    discovery = [params for params in searches if "project in" in params["jql"]]
    assert [params["startAt"] for params in discovery] == list(range(0, 290, page_cap))
    assert all("expand" not in params for params in discovery)
    assert all(params["maxResults"] == 100 for params in searches)
    hydration = [params for params in searches if params["jql"].startswith("key in (")]
    batches = list(dict.fromkeys(params["jql"] for params in hydration))
    assert [len(re.findall(r'"(P-\d+)"', jql)) for jql in batches] == [100, 20]
    assert set().union(*(set(re.findall(r'"(P-\d+)"', jql)) for jql in batches)) == provider.eligible_keys
    assert [[params["startAt"] for params in hydration if params["jql"] == jql] for jql in batches] == [
        list(range(0, 100, page_cap)), [0]]
    assert run["metrics"]["restRequests"] == len(provider.calls) == 1 + len(discovery) + len(hydration)

    # A warm run must still revalidate all discovery pages, including the late VoC,
    # while reusing each complete rich source rather than reducing the result set.
    prior_count = len(provider.calls)
    warm = m.create(request, background=False)
    assert warm["status"] == "complete"
    assert {event["id"] for event in warm["events"]} == expected_events
    assert warm["summary"] == run["summary"]
    assert warm["coverage"]["candidatesDiscovered"] == 290
    assert warm["metrics"]["restRequests"] == len(discovery)
    assert len(provider.calls) == prior_count + len(discovery)
    assert all("project in" in params["jql"] for _, params in provider.calls[prior_count:])
