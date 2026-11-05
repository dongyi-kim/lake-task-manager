"""Compact ticket projections preserve upstream update time without extra Jira lookups."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.jira.jira_client import JiraClient, _display_node
from app.jira.workload_service import JiraWorkloadMixin
from app.domain.vit import _project
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.services.ticket_activity import _recent_timestamp, ticket_activity


def test_workload_wbs_and_vit_preserve_full_timestamp():
    updated = "2026-11-05T09:12:34.567+0900"
    raw = {"key": "DL-1", "fields": {"updated": updated, "subtasks": [
        {"key": "DL-2", "fields": {"updated": updated}}]}}
    owner = SimpleNamespace(s=SimpleNamespace(epic_link_field_id="epic", voc_component="VoC"),
                            direct_child_keys=lambda *_a, **_k: [], prefetch_issues=lambda *_a, **_k: None)
    assert JiraWorkloadMixin._wl_ticket(owner, raw)["updated"] == updated
    wbs = _display_node(raw, "sp", with_subs=True)
    assert wbs["updated"] == wbs["children"][0]["updated"] == updated
    assert JiraClient._node_from_issue(owner, raw)["updated"] == updated
    vit = _project({"key": "DL-1", "summary": "Task", "type": "Task", "statusCategory": "todo",
                    "status": "Open", "updated": updated, "tree": [{"key": "DL-2", "type": "Sub-Task", "updated": updated}]}, "2026-11-01")
    assert vit["updated"] == vit["children"][0]["updated"] == updated


def test_real_mock_detail_and_comment_cache_support_metadata_with_provider_offline(monkeypatch):
    cache = Cache(":memory:")
    client = JiraClient(get_settings(), cache)
    try:
        raw = client._get_issue_view("DL-9007")
        comments = client.issue_comments("DL-9007")
        assert raw["fields"]["updated"]
        assert cache.get_stale(f"issueview:{client.env}:DL-9007")["fields"]["updated"] == raw["fields"]["updated"]
        assert all("updated" in row for row in comments)
        def offline(*_a, **_k):
            raise AssertionError("metadata must reuse actual cached projections while offline")
        monkeypatch.setattr(client.provider, "get_json", offline)
        result = ticket_activity(client, client.s, cache, ["DL-9007"])
        times = [raw["fields"]["updated"]] + [row.get(field) for row in comments for field in ("date", "updated")]
        expected = max((value for stamp in times if (value := _recent_timestamp(stamp, result["observedAt"])) is not None), default=None)
        assert result["tickets"]["DL-9007"]["latestRelevantAt"] == expected
    finally:
        client.close()
        cache.close()


def test_newer_detail_refreshes_only_old_comments_and_failure_keeps_source_age(monkeypatch):
    cache = Cache(":memory:")
    clock = [1000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    calls = []
    entered, release = Event(), Event()
    def fetch(path, **_kwargs):
        calls.append(path)
        entered.set()
        assert release.wait(2)
        return {"comments": [{"id": "1", "updated": "2026-11-05T09:30:00+0900"}]}
    owner = SimpleNamespace(cache=cache, env="mock", CACHE_COMMENTS=20, s=SimpleNamespace(cache_ttl_seconds=600),
                            provider=SimpleNamespace(get_json=fetch), _project_comment_row=lambda row: row)
    key = "comments:mock:DL-1"
    cache.set(key, [{"id": "1", "updated": "2026-11-05T09:00:00+0900"}], 600)
    cache.set("issue:mock:DL-1", {"fields": {"summary": "detail"}}, 600)
    clock[0] += 5
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(JiraClient._issue_comments, owner, "DL-1", fresh_after=1001000)
            if not entered.wait(2):
                first.result()
                pytest.fail("comments producer did not start")
            second = pool.submit(JiraClient._issue_comments, owner, "DL-1", fresh_after=1001000)
            release.set()
            assert first.result() == second.result()
        assert calls == ["/rest/api/2/issue/DL-1/comment"]
        assert cache.get_stale("issue:mock:DL-1")["fields"]["summary"] == "detail"
        # The next identical proof uses the source timestamp, without another Jira request.
        JiraClient._issue_comments(owner, "DL-1", fresh_after=1001000)
        assert len(calls) == 1
        previous = cache.get_stale_at(key)
        clock[0] += 5
        owner.provider.get_json = lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError("offline"))
        with pytest.raises(ConnectionError, match="offline"):
            JiraClient._issue_comments(owner, "DL-1", fresh_after=1006000)
        assert cache.get_stale_at(key) == previous
    finally:
        cache.close()
