"""An updated VoC belongs to the module even without a visible period event."""
from copy import deepcopy
import re

from test_backend_manager import manager
from test_voc_pagination import PagedVocProvider


class OldVocProvider(PagedVocProvider):
    """Reuse strict Jira field projection and paging, with independent old dates."""

    def __init__(self):
        super().__init__(page_cap=37)
        template = self.rows[-1]
        self.rows = []
        self.groups = {kind: set() for kind in (
            "field", "comment", "completed", "timespent", "closed", "updated")}
        ordinal = 0
        for index in range(1200):
            row = deepcopy(template)
            row["key"] = key = f"P-{1000 + index}"
            eligible = index % 3 == 1 or index >= 1188
            fields = row["fields"]
            fields.update(
                summary=f"Old VoC {key}" if eligible else f"Other work {key}",
                created=f"2025-{1 + index % 12:02d}-{1 + index % 20:02d}T09:00:00+0900",
                updated="2026-11-12T18:00:00+0900",
                assignee={"name": "etl.member" if eligible else "other.member"},
                reporter={"name": "reporter-bot"}, creator={"name": "reporter-bot"},
                components=[{"name": "사용자 VoC" if eligible else "Other"}],
                comment={"startAt": 0, "total": 0, "comments": []},
            )
            old_completion = f"2026-{1 + index % 9:02d}-{1 + index % 20:02d}T10:00:00+0900"
            histories = [{"id": "old-completion", "created": old_completion,
                          "author": {"name": "reporter-bot"}, "items": [self.transition("1", "2")]}]
            fields["status"] = self.status("2")
            fields["resolutiondate"] = old_completion
            if eligible:
                kind = list(self.groups)[ordinal % 6]
                self.groups[kind].add(key)
                ordinal += 1
                item = None
                if kind == "field":
                    item = {"field": "summary", "fromString": "Old title", "toString": fields["summary"]}
                elif kind == "comment":
                    fields["comment"].update(total=1, comments=[{
                        "id": "comment", "created": "2026-11-10T10:00:00+0900",
                        "author": {"name": "reporter-bot"}, "body": f"Follow-up for {key}"}])
                elif kind == "completed":
                    histories = []
                    item = self.transition("1", "2")
                    fields["resolutiondate"] = "2026-11-12T11:00:00+0900"
                elif kind == "timespent":
                    item = {"field": "timespent", "from": "0", "to": "3600"}
                elif kind == "closed":
                    item = self.transition("2", "3")
                    fields["status"] = self.status("3")
                if item:
                    histories.append({"id": "period", "created": (
                        "2026-11-12T11:00:00+0900" if kind == "completed" else "2026-11-11T11:00:00+0900"),
                        "author": {"name": "reporter-bot"}, "items": [item]})
            row["changelog"] = {"startAt": 0, "total": len(histories), "histories": histories}
            self.rows.append(row)
        self.eligible_keys = set().union(*self.groups.values())

    @staticmethod
    def status(ident):
        return {"id": ident, "name": {"1": "Ready", "2": "Resolved", "3": "Closed"}[ident],
                "statusCategory": {"key": "new" if ident == "1" else "done"}}

    @classmethod
    def transition(cls, before, after):
        return {"field": "status", "from": before, "to": after,
                "fromString": cls.status(before)["name"], "toString": cls.status(after)["name"]}

    def get_json(self, path, params=None):
        if path.endswith("/status"):
            self.calls.append((path, dict(params or {})))
            return [self.status(ident) for ident in ("1", "2", "3")]
        return super().get_json(path, params)


def configured_manager(tmp_path, provider):
    result = manager(tmp_path, provider)
    result.people_loader = lambda: {"ETL": ["etl.member"], "Other": ["other.member"]}
    result.client.s.voc_component = "사용자 VoC"
    return result


def test_old_module_voc_updated_in_window_survives_all_pages_and_warm_cache(tmp_path):
    provider = OldVocProvider()
    m = configured_manager(tmp_path, provider)
    request = {"startDate": "2026-11-10", "endDate": "2026-11-12", "modules": ["ETL"]}
    expected_events = (
        {f"{key}:history:period:0" for kind in ("field", "completed") for key in provider.groups[kind]}
        | {f"{key}:comment:comment:created" for key in provider.groups["comment"]})
    quiet = set().union(*(provider.groups[kind] for kind in ("timespent", "closed", "updated")))
    original = {row["key"]: row["fields"] for row in provider.rows if row["key"] in provider.eligible_keys}

    cold = m.create(request, background=False)
    cold_calls = len(provider.calls)
    # Reopen the manager to exercise durable sources, not only in-process state.
    warm = configured_manager(tmp_path, provider).create(request, background=False)

    for run in (cold, warm):
        assert run["status"] == "complete", run["errors"]
        assert all(run["coverage"][name] is True for name in (
            "complete", "queriesComplete", "historyComplete", "commentsComplete", "scopeComplete"))
        assert run["coverage"]["missing"] == []
        assert run["coverage"]["candidatesDiscovered"] == 1200
        assert run["coverage"]["issuesTotal"] == run["coverage"]["issuesComplete"] == 408
        issues = {issue["key"]: issue for issue in run["issues"]}
        assert set(issues) == provider.eligible_keys
        assert {event["id"] for event in run["events"]} == expected_events
        assert run["summary"]["created"] == run["summary"]["reopened"] == 0
        assert run["summary"]["comments"] == run["summary"]["completed"] == run["summary"]["fieldChanges"] == 68
        assert run["summary"]["totalEvents"] == run["summary"]["uniqueIssues"] == 204
        assert run["tree"][0]["counts"] == run["summary"]
        assert {node["key"] for node in run["tree"][0]["children"]} == provider.eligible_keys
        for key, issue in issues.items():
            assert issue["created"] == original[key]["created"]
            assert issue["updated"] == original[key]["updated"]
            assert issue["assigneeId"] == "etl.member"
            assert issue["modules"] == ["사용자 VoC"]
            assert issue["currentCompletion"]["at"][:10] == original[key]["resolutiondate"][:10]
            if key in quiet:
                assert issue["events"] == issue["eventIds"] == []
        assert m.get(run["id"])["issues"] == run["issues"]

    searches = [params for path, params in provider.calls[:cold_calls] if path.endswith("/search")]
    discovery = [params for params in searches if "project in" in params["jql"]]
    assert [params["startAt"] for params in discovery] == list(range(0, 1200, 37))
    assert all("updated >=" in params["jql"] and "created >=" not in params["jql"] for params in discovery)
    assert all("expand" not in params for params in discovery)
    assert all({"assignee", "reporter", "components", "updated"} <= set(params["fields"].split(","))
               for params in discovery)
    hydration = [params for params in searches if params["jql"].startswith("key in (")]
    batches = list(dict.fromkeys(params["jql"] for params in hydration))
    assert [len(re.findall(r'"(P-\d+)"', jql)) for jql in batches] == [100, 100, 100, 100, 8]
    assert set().union(*(set(re.findall(r'"(P-\d+)"', jql)) for jql in batches)) == provider.eligible_keys
    assert [[params["startAt"] for params in hydration if params["jql"] == jql] for jql in batches] == [
        [0, 37, 74], [0, 37, 74], [0, 37, 74], [0, 37, 74], [0]]
    assert cold["metrics"]["restRequests"] == cold_calls == 1 + len(discovery) + len(hydration)
    assert warm["summary"] == cold["summary"]
    assert warm["metrics"]["restRequests"] == len(provider.calls) - cold_calls == len(discovery)
    assert warm["metrics"]["cacheHits"] > 0
    assert all("project in" in params["jql"] for _, params in provider.calls[cold_calls:])


def test_selected_assignee_keeps_old_voc_without_events_on_exact_single_day(tmp_path):
    provider = OldVocProvider()
    # One example of each event type is enough to isolate date/person selection.
    provider.rows = [row for row in provider.rows if row["key"] in provider.eligible_keys][:6]
    provider.eligible_keys = {row["key"] for row in provider.rows}
    m = configured_manager(tmp_path, provider)

    run = m.create({"startDate": "2026-11-11", "endDate": "2026-11-11",
                    "modules": ["ETL"], "people": ["etl.member"]}, background=False)

    assert run["status"] == "complete", run["errors"]
    assert run["coverage"]["complete"] is True
    assert run["coverage"]["issuesTotal"] == run["coverage"]["issuesComplete"] == 6
    assert {issue["key"] for issue in run["issues"]} == provider.eligible_keys
    assert {event["id"] for event in run["events"]} == {"P-1001:history:period:0"}
    assert run["events"][0]["actorId"] == "reporter-bot"
    assert run["summary"]["created"] == run["summary"]["completed"] == run["summary"]["comments"] == 0
    assert run["summary"]["fieldChanges"] == run["summary"]["totalEvents"] == 1
    assert all(issue["events"] == [] for issue in run["issues"] if issue["key"] != "P-1001")
