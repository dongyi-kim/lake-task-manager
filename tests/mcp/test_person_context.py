"""Context is scoped evidence, not a resolver or a record of every search candidate."""

import pytest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from app.auth.base import PermissionDenied, SessionExpired, UpstreamError, UpstreamUnavailable
from app.domain import person_context as context
from app.infra import cache as cache_module


@pytest.fixture
def people(build_env, monkeypatch):
    env = build_env()
    env.store.users = {
        "u01": {"name": "u01", "displayName": "현재 사용자"},
        "sua.one": {"name": "sua.one", "displayName": "강수아 가람회사"},
        "sua.two": {"name": "sua.two", "displayName": "김수아 나래회사"},
    }
    env.client.current_user_for_authorization()  # Only the normal cached /myself path.
    monkeypatch.setattr(context, "module_dir", lambda: {"byUser": {
        "U01": ["Workbench"], "SUA.ONE": ["Workbench"], "sua.two": ["Other"]}})
    return env


def test_search_preserves_candidates_order_and_adds_independent_evidence(people):
    env = people
    assert context.record_person_event(env.client, env.runtime.settings, "SUA.ONE", "mention", "u01")
    assert context.record_person_event(env.client, env.runtime.settings, "sua.two", "lookup")
    before = len(env.calls)
    result = context.search_people(env.client, env.runtime.settings, "수아")
    assert [user["id"] for user in result["users"]] == ["sua.one", "sua.two"]
    first, second = (user["matchContext"] for user in result["users"])
    assert first["configured"] and first["sameModule"] and first["recentMentionAt"]
    assert second["configured"] and second["sameModule"] is False and second["recentLookupAt"]
    assert first["recentLookupAt"] is None and second["recentMentionAt"] is None
    assert all(user["matchContext"]["recentSelectionAt"] is None for user in result["users"])
    assert result["contextAvailable"] == {"configuration": True, "history": True, "actor": True}
    assert result["coverage"] == "bounded" and result["mayHaveMore"] is None
    assert env.calls[before:] == [("get", "/rest/api/2/user/search", {
        "params": {"username": "수아", "maxResults": 9}})]


def test_local_history_keeps_instance_scope_without_jira_identity(people, monkeypatch):
    env, settings = people, people.runtime.settings
    original = env.provider.get_json
    def unavailable(path, **kwargs):
        if path.endswith("/myself"):
            pytest.fail("Local history must not fetch identity")
        return original(path, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", unavailable)
    env.cache.invalidate(f"myself:{env.client.env}")
    env.client._session_dead = True
    assert context.record_person_event(env.client, settings, "sua.one", "selection", "obsolete-actor")
    env.client._session_dead = False
    result = context.search_people(env.client, settings, "수아")
    assert result["users"][0]["matchContext"]["recentSelectionAt"]
    assert result["contextAvailable"] == {"configuration": True, "history": True, "actor": False}
    settings.jira_base += "/another-jira"
    assert context.search_people(env.client, settings, "수아")["users"][0]["matchContext"]["recentSelectionAt"] is None


def test_legacy_actor_history_remains_available_without_timestamp_refresh(people):
    env, settings = people, people.runtime.settings
    scope = context._scope(context._instance(settings), "u01")
    env.cache.touch_person_recent(scope, "sua.one", "mention")
    timestamp = env.cache.person_recent(scope, ["sua.one"])["sua.one"]["mention"]
    result = context.search_people(env.client, settings, "수아")
    assert result["users"][0]["matchContext"]["recentMentionAt"] == timestamp


def test_scoped_history_expires_and_caps_without_polluting_url_recents(people, monkeypatch):
    cache, now = people.cache, [1000.0]
    monkeypatch.setattr(cache_module.time, "time", lambda: now[0])
    for index in range(205):
        now[0] += 1
        cache.touch_person_recent("actor-a", f"person-{index}", "lookup")
    cache.touch_person_recent("actor-b", "person-0", "mention")
    assert len(cache.person_recent("actor-a", [f"person-{i}" for i in range(205)])) == 200
    assert not cache.person_recent("actor-a", ["person-0"])
    assert cache.person_recent("actor-b", ["person-0"])["person-0"]["mention"] == now[0]
    assert cache.recent_items() == []
    now[0] += cache.PERSON_RECENT_SECONDS
    assert cache.person_recent("actor-b", ["person-0"]) == {}
    cache.purge()
    assert cache._conn.execute("SELECT count(*) FROM person_recent").fetchone()[0] == 0


def test_many_homonyms_stay_bounded_and_failure_is_never_no_match(people, monkeypatch):
    env = people
    env.store.users.update({f"same-{i}": {"name": f"same-{i}", "displayName": f"수아 회사{i}"}
                            for i in range(12)})
    page = context.search_people(env.client, env.runtime.settings, "수아")
    assert page["returned"] == page["limit"] == 8
    assert page["partial"] and page["mayHaveMore"]
    assert all(row["matchContext"]["recentLookupAt"] is None for row in page["users"])
    for value in ({"error": "bad response"}, [None], [{"name": "  "}]):
        monkeypatch.setattr(env.provider, "get_json", lambda *_a, **_k: value)
        with pytest.raises(UpstreamUnavailable):
            context.search_people(env.client, env.runtime.settings, "수아")
    def denied(*_args, **_kwargs):
        raise PermissionDenied("forbidden")
    monkeypatch.setattr(env.provider, "get_json", denied)
    with pytest.raises(PermissionDenied):
        context.search_people(env.client, env.runtime.settings, "수아")


@pytest.mark.parametrize("failure", [SessionExpired("expired"), UpstreamError(429, "rate limited"),
                                      UpstreamUnavailable("offline")])
def test_optional_actor_probe_never_changes_auth_or_transport_state(people, monkeypatch, failure):
    env = people
    original = env.provider.get_json
    def get(path, **kwargs):
        if path.endswith("/myself"):
            raise failure
        return original(path, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", get)
    fields = ("_session_dead", "_upstream_down_until", "_upstream_reason", "_auth_probe_result")
    before = [getattr(env.client, field, None) for field in fields]
    result = context.search_people(env.client, env.runtime.settings, "수아")
    assert len(result["users"]) == 2 and result["contextAvailable"]["actor"] is True
    assert [getattr(env.client, field, None) for field in fields] == before


def test_raw_full_display_avoids_user_hydration_and_preserves_distinct_ids(people):
    env = people
    env.store.users.update({f"강수아 회사{i}": {"name": f"강수아 회사{i}", "displayName": f"강수아 회사{i}"}
                            for i in range(10)})
    before = len(env.calls)
    result = context.search_people(env.client, env.runtime.settings, "강수아", limit=50)
    assert len(result["users"]) == 11 and result["mayHaveMore"] is None
    assert all(path != "/rest/api/2/user" for _method, path, _kwargs in env.calls[before:])
    env.store.users.update({uid: {"name": uid, "displayName": "Same name"} for uid in ("Straße", "Strasse")})
    assert context.record_person_event(env.client, env.runtime.settings, "Straße", "lookup")
    rows = context.search_people(env.client, env.runtime.settings, "Same")["users"]
    assert {row["id"] for row in rows} == {"Straße", "Strasse"}
    assert next(row for row in rows if row["id"] == "Straße")["matchContext"]["recentLookupAt"]
    assert next(row for row in rows if row["id"] == "Strasse")["matchContext"]["recentLookupAt"] is None


def test_malformed_rows_keep_valid_candidates_and_provider_race_discards_actor(people, monkeypatch):
    env = people
    valid = {"name": "sua.one", "displayName": "강수아 가람회사"}
    original = env.provider.get_json
    monkeypatch.setattr(env.provider, "get_json", lambda path, **kwargs: [valid, None]
                        if path.endswith("/user/search") else original(path, **kwargs))
    page = context.search_people(env.client, env.runtime.settings, "수아", limit=1)
    assert page["returned"] == 1 and page["partial"] and page["mayHaveMore"] is None
    monkeypatch.setattr(env.provider, "get_json", lambda path, **kwargs:
        [{"name": f"capped-{i}", "displayName": f"Same {i}"} for i in range(8)]
        if path.endswith("/user/search") else original(path, **kwargs))
    capped = context.search_people(env.client, env.runtime.settings, "Same", limit=50)
    assert capped["returned"] == 8 and capped["mayHaveMore"] is None and capped["coverage"] == "bounded"


def test_optional_actor_probe_is_shared_across_concurrent_recordings(people):
    env = people
    before = len(env.calls)
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _index: context.record_person_event(
            env.client, env.runtime.settings, "sua.one", "lookup"), range(12)))
    assert all(results)
    assert [path for _method, path, _kwargs in env.calls[before:]] == []


def test_actor_scope_is_fenced_between_probe_and_history_access(people, monkeypatch):
    env, settings = people, people.runtime.settings
    assert context.record_person_event(env.client, settings, "sua.one", "mention")
    original_read = env.cache.person_recent
    def replaced(scope, ids):
        history = original_read(scope, ids)
        assert history
        env.client.reset_provider()
        return history
    monkeypatch.setattr(env.cache, "person_recent", replaced)
    result = context.search_people(env.client, settings, "수아")
    assert result["contextAvailable"]["actor"] is False and result["contextAvailable"]["history"] is False
    assert all(user["matchContext"]["recentMentionAt"] is None and user["matchContext"]["sameModule"] is None
               for user in result["users"])


def test_search_started_by_old_actor_never_gets_new_actor_context(people, monkeypatch):
    env, settings = people, people.runtime.settings
    original = env.provider.get_json
    probes = []
    def bob_get(path, **kwargs):
        if path.endswith("/myself"):
            probes.append("bob")
            return {"name": "sua.two"}
        return original(path, **kwargs)
    bob = SimpleNamespace(get_json=bob_get)
    env.client._provider = bob
    assert context.record_person_event(env.client, settings, "sua.one", "mention")
    env.client._provider = env.provider
    env.client._provider_generation += 1
    operation = context.capture_person_context(env.client, settings)
    def switch_during_search(path, **kwargs):
        result = original(path, **kwargs)
        if path.endswith("/user/search"):
            env.client._provider = bob
            env.client._provider_generation += 1
        return result
    monkeypatch.setattr(env.provider, "get_json", switch_during_search)
    result = context.search_people(env.client, settings, "수아")
    assert len(result["users"]) == 2 and result["partial"] and result["contextChanged"]
    assert result["contextAvailable"] == {"configuration": False, "history": False, "actor": False}
    assert all(all(value is None for value in row["matchContext"].values()) for row in result["users"])
    assert probes == []  # The stale search never probes the replacement actor.
    assert not context.record_person_event(env.client, settings, "sua.one", "lookup", expected_stamp=operation)
    assert not context.record_person_event(env.client, settings, "sua.one", "lookup", expected_stamp=None)


def test_missing_initial_binding_is_fail_safe_without_actor_probe(people, monkeypatch):
    env = people
    monkeypatch.setattr(context, "capture_person_context", lambda *_args: None)
    def unexpected(*_args):
        pytest.fail("Actor lookup must not run for an unbound operation")
    monkeypatch.setattr(context, "_actor", unexpected)
    result = context.search_people(env.client, env.runtime.settings, "수아")
    assert result["returned"] == 2 and result["partial"] and result["contextChanged"]
    assert not any(result["contextAvailable"].values())
