"""Saved Tracking labels use collected identities and durable local names only."""
from copy import deepcopy
from datetime import datetime, timezone
import time

from app.infra.cache import Cache
from app.jira.identity_service import JiraIdentityMixin
from test_backend_manager import Provider, manager


def with_names(m):
    class Identity(JiraIdentityMixin):
        pass
    identity = Identity()
    identity.__dict__.update(vars(m.client))
    identity.cache = Cache(":memory:")
    m.client = identity
    return identity


def snapshot(name="alice"):
    observed = datetime.now(timezone.utc).isoformat()
    event = {"id": "comment", "issueKey": "P-1", "kind": "comment", "actorId": "alice",
             "actorName": name, "author": {"id": "alice", "name": name}, "body": "[~alice] ready"}
    issue = {"key": "P-1", "fetchedAt": observed, "assigneeId": "alice", "assigneeName": name,
             "assignee": {"id": "alice", "name": name}, "events": [deepcopy(event)]}
    return {"id": "saved", "status": "complete", "events": [event], "issues": [issue]}


def test_saved_actor_assignee_and_comment_names_use_one_memoized_local_lookup(tmp_path):
    provider = Provider()
    m = manager(tmp_path, provider)
    client = with_names(m)
    try:
        client.remember_display_name("alice", "Alice Example")
        m.store.insert(snapshot(), *m.authorize())
        before = m.store.read("saved")
        lookups = []
        lookup = client.display_name_cached
        client.display_name_cached = lambda uid: (lookups.append(uid), lookup(uid))[1]

        result = m.get("saved")

        assert result["issues"][0]["assigneeName"] == "Alice Example"
        assert result["issues"][0]["assignee"]["name"] == "Alice Example"
        assert result["events"][0]["actorName"] == "Alice Example"
        assert result["events"][0]["author"]["name"] == "Alice Example"
        assert "Alice" in result["events"][0]["bodyHtml"]
        assert lookups == ["alice"]
        assert m.store.read("saved") == before
        assert provider.calls == []
    finally:
        client.cache.close()


def test_options_learn_names_already_observed_in_saved_tracking_issues(tmp_path):
    provider = Provider()
    m = manager(tmp_path, provider)
    client = with_names(m)
    m.people_loader = lambda: {"M": ["alice"]}
    try:
        m.store.insert(snapshot("Alice Example"), *m.authorize())
        assert m.options()["people"][0]["name"] == "Alice Example"
        assert client.display_name_cached("alice") == "Alice Example"
        assert provider.calls == []
    finally:
        client.cache.close()


def test_options_bootstrap_missing_roster_names_once_and_do_not_rewrite_equal_observations(tmp_path):
    provider = Provider()
    m = manager(tmp_path, provider)
    client = with_names(m)
    m.people_loader = lambda: {"M": ["alice", "unknown"]}
    try:
        m.store.insert(snapshot("Alice Example"), *m.authorize())
        reads, transactions = [], []
        recent = m.store.recent
        m.store.recent = lambda *args: (reads.append(args), recent(*args))[1]
        client.cache._conn.set_trace_callback(transactions.append)
        for _ in range(3):
            assert m.options()["people"][0]["name"] == "Alice Example"
        assert len(reads) == 1
        assert sum(row == "COMMIT" for row in transactions) == 1
        assert provider.calls == []
    finally:
        client.cache.close()


def test_reused_history_and_comment_names_never_overwrite_a_more_recent_observation(tmp_path, monkeypatch):
    from app.tracking.names import observe_tracking_names

    now = 1800000000.0
    monkeypatch.setattr(time, "time", lambda: now)
    m = manager(tmp_path, Provider())
    client = with_names(m)
    try:
        client.remember_display_name("author", "Current Alice", now - 86400)
        issue = {"key": "P-1", "fetchedAt": datetime.fromtimestamp(now, timezone.utc).isoformat()}
        event = {"issueKey": "P-1", "kind": "comment", "actorId": "author", "actorName": "Old Alice"}
        stored = {"issues": [issue], "events": [event], "coverage": {
            "sourceFetchedAt": [datetime.fromtimestamp(now - 2 * 86400, timezone.utc).isoformat(),
                                datetime.fromtimestamp(now, timezone.utc).isoformat()]}}
        old_user = {"name": "author", "displayName": "Old Alice"}
        checkpoint = {"sources": {"P-1": {"history": {"histories": [{"author": old_user}]},
                                           "comments": [{"author": old_user}]}}}
        observe_tracking_names(stored, client, checkpoint)
        assert client.display_name_cached("author") == "Current Alice"
        stored.pop("coverage")
        observe_tracking_names(stored, client, checkpoint)
        assert client.display_name_cached("author") == "Current Alice"
    finally:
        client.cache.close()
