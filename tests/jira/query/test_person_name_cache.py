"""Display names have a durable lifetime independent of tickets and authorization."""
from types import SimpleNamespace

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy
from app.jira.identity_service import JiraIdentityMixin


class Identity(JiraIdentityMixin):
    def __init__(self, cache, *, env="mock@2026-11-17", base="https://jira.example/"):
        self.cache, self.env = cache, env
        self.s = SimpleNamespace(jira_base=base)
        self.provider = self
        self.calls = []
        self.fail = False

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        if self.fail:
            raise ConnectionError("offline")
        return {"name": params["username"], "displayName": "Alice Example"}


@pytest.fixture
def clock(monkeypatch):
    now = [1800000000.0]
    monkeypatch.setattr("app.jira.identity_service.time.time", lambda: now[0])
    return now


def test_name_stays_fresh_for_ninety_days_across_restart_and_mock_date_rollover(tmp_path, clock):
    path = tmp_path / "names.sqlite3"
    cache = Cache(path)
    cache.configure(CachePolicy())
    first = Identity(cache)
    assert first._display_name("Alice") == "Alice Example"
    assert len(first.calls) == 1
    clock[0] += 89 * 86400
    cache.purge()
    cache.close()
    reopened = Cache(path)
    reopened.configure(CachePolicy())
    try:
        second = Identity(reopened, env="mock@2027-02-14")
        second.fail = True
        assert second.display_name_cached("ALICE") == "Alice Example"
        assert second._display_name("alice") == "Alice Example"
        assert second.calls == []
    finally:
        reopened.close()


def test_badge_lookup_primes_cached_name_and_stale_name_survives_offline_until_one_year(clock):
    cache = Cache(":memory:")
    cache.configure(CachePolicy())
    client = Identity(cache)
    try:
        assert client.user_badge("alice")["displayName"] == "Alice Example"
        assert client.display_name_cached("alice") == "Alice Example"
        clock[0] += 91 * 86400
        client.fail = True
        assert client.display_name_cached("alice") == "Alice Example"
        assert client._display_name("alice") == "Alice Example"
        assert len(client.calls) == 2
        clock[0] += 275 * 86400
        assert client.display_name_cached("alice") is None
    finally:
        cache.close()


@pytest.mark.parametrize(("env", "base"), [
    ("prod", "https://jira.example"), ("local@2026-11-17", "https://jira.example"),
    ("mock@2026-11-18", "https://other.example"),
    ("mock@2026-11-17", "https://other.example"),
])
def test_name_directory_never_crosses_source_realms(clock, env, base):
    cache = Cache(":memory:")
    first, second = Identity(cache), Identity(cache, env=env, base=base)
    try:
        assert first._display_name("alice") == "Alice Example"
        assert second.display_name_cached("alice") is None
        assert second.calls == []
        second.get_json = lambda path, params=None: {"name": params["username"], "displayName": "Other Alice"}
        assert second._display_name("alice") == "Other Alice"
        assert first.display_name_cached("alice") == "Alice Example"
    finally:
        cache.close()


def test_name_directory_does_not_fabricate_a_user_badge_or_replace_canonical_identity(clock):
    cache = Cache(":memory:")
    client = Identity(cache)
    try:
        client.remember_display_name("alice", "Alice Example")
        client.get_json = lambda path, params=None: {"name": "ALICE", "displayName": "Current Alice"}
        badge = client.user_badge("alice", strict_auth=True)
        assert badge["id"] == badge["username"] == "ALICE"
        assert badge["displayName"] == "Current Alice"
    finally:
        cache.close()


def test_replayed_observations_keep_original_age_and_unscoped_legacy_names_are_not_imported(clock):
    cache = Cache(":memory:")
    client = Identity(cache)
    try:
        cache.set("userbadge:" + client.env + ":alice", {"id": "alice", "displayName": "Alice Example"}, 21600)
        assert client.display_name_cached("alice") is None
        observed = clock[0]
        clock[0] += 100 * 86400
        client.remember_display_name("alice", "Alice Example", observed)
        assert client.display_name_cached("alice") == "Alice Example"
        clock[0] += 266 * 86400
        assert client.display_name_cached("alice") is None
        assert client.calls == []
    finally:
        cache.close()
