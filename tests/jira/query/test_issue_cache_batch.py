"""Shared issue-cache hits keep their provenance without scalar recency commits."""
import time

import pytest

from app.auth.base import SessionExpired, UpstreamUnavailable
from app.infra.cache import Cache
from app.infra.cache_policy import read_scope
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


@pytest.fixture
def issues(tmp_path, monkeypatch):
    client = JiraClient(get_settings(), Cache(str(tmp_path / "issues.sqlite3")))
    clock, calls = [time.time()], []
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])

    def row(key, label="new"):
        return {"key": key, "fields": {"summary": label}}

    def seed(key, kind="issueL", age=10, label="cached", ttl=600):
        ck = f"{kind}:{client.env}:{key}"
        client.cache.set_if_fence(ck, row(key, label), ttl, client.cache.fence(), fetched_at=clock[0] - age)
        return ck, client.cache.metadata(ck)

    def request(path, params=None, **kwargs):
        calls.append(path)
        assert path.startswith("/rest/api/2/issue/")
        return row(path.rsplit("/", 1)[1])

    monkeypatch.setattr(client.provider, "get_json", request)
    try:
        yield client, clock, calls, row, seed
    finally:
        client.close()
        client.cache.close()


@pytest.mark.parametrize("light", [False, True])
def test_bulk_hits_have_one_recency_transaction_and_keep_identity_age_order(issues, light):
    client, clock, calls, row, seed = issues
    keys = [f"DL-{i}" for i in range(1, 129)]
    before = dict(seed(key, "issueL" if light else "issue") for key in keys)
    clock[0] += 20
    sql = []
    client.cache._conn.set_trace_callback(sql.append)
    with read_scope() as sources:
        result = client.issues_by_keys_result(list(reversed(keys)) + [keys[0]], light=light)
    client.cache._conn.set_trace_callback(None)
    assert result["complete"] and result["missing"] == []
    assert result["requestedKeys"] == list(reversed(keys))
    assert result["issues"] == [row(key, "cached") for key in reversed(keys)]
    assert calls == []
    assert len([s for s in sql if s == "COMMIT"]) == 1
    assert len(sources) == 128
    for ck, original in before.items():
        current = client.cache.metadata(ck)
        assert (current["rowIdentity"], current["fetchedAt"]) == (original["rowIdentity"], original["fetchedAt"])
        assert client.cache._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (ck,)).fetchone()[0] == clock[0]
    assert not client.cache._conn.in_transaction


@pytest.mark.parametrize("full_age,light_age,expected", [(20, 10, "light"), (10, 20, "full"), (10, 10, "full")])
@pytest.mark.parametrize("api", ["single", "bulk"])
def test_light_projection_uses_newest_eligible_source_and_full_wins_ties(issues, full_age, light_age, expected, api):
    client, _, calls, _, seed = issues
    seed("DL-1", "issue", full_age, "full")
    seed("DL-1", "issueL", light_age, "light")
    result = client.get_issue_light("DL-1") if api == "single" else client.issues_by_keys_result(["DL-1"], light=True)["issues"][0]
    assert result["fields"]["summary"] == expected and calls == []


def test_prefetch_batches_cache_probes_and_full_projection_never_accepts_light(issues, monkeypatch):
    client, _, calls, _, seed = issues
    for i in range(1, 20):
        seed(f"DL-{i}", "issueL")
    sql = []
    client.cache._conn.set_trace_callback(sql.append)
    assert client.prefetch_issues([f"DL-{i}" for i in range(1, 20)], light=True) is None
    client.cache._conn.set_trace_callback(None)
    assert sql.count("COMMIT") == 1 and calls == []
    result = client.issues_by_keys_result(["DL-1"], light=False)
    assert result["issues"][0]["fields"]["summary"] == "new"
    assert calls == ["/rest/api/2/issue/DL-1"]


def test_mixed_hits_and_prefetched_rows_do_not_fall_back_to_scalar_getters(issues, monkeypatch):
    client, _, calls, row, seed = issues
    seed("DL-1")
    batches = []
    def prefetch(keys, light=False):
        batches.append(list(keys))
        for key in keys:
            seed(key, "issueL", label="prefetched")
    monkeypatch.setattr(client, "prefetch_issues", prefetch)
    monkeypatch.setattr(client, "get_issue_light", lambda key: pytest.fail(f"eligible row reread: {key}"))
    result = client.issues_by_keys_result(["DL-3", "DL-1", "DL-2"], light=True)
    assert batches == [["DL-3", "DL-2"]]
    assert result["issues"] == [row("DL-3", "prefetched"), row("DL-1", "cached"), row("DL-2", "prefetched")]
    assert result["complete"] and calls == []


@pytest.mark.parametrize("change", ["expire", "invalidate", "replace"])
def test_prefetch_rechecks_all_original_hits_before_returning_mixed_result(issues, monkeypatch, change):
    client, clock, calls, row, seed = issues
    ck, _ = seed("DL-1", age=0, ttl=5)
    def prefetch(keys, light=False):
        assert keys == ["DL-2"]
        seed("DL-2", label="prefetched")
        if change == "expire":
            clock[0] += 1000
        elif change == "invalidate":
            client.cache.invalidate_keys([ck])
        else:
            seed("DL-1", label="replaced", age=0)
    monkeypatch.setattr(client, "prefetch_issues", prefetch)
    result = client.issues_by_keys_result(["DL-2", "DL-1"], light=True)
    assert result["complete"]
    expected = [row("DL-2", "new" if change == "expire" else "prefetched"),
                row("DL-1", "replaced" if change == "replace" else "new")]
    assert result["issues"] == expected
    assert len(calls) == {"expire": 2, "invalidate": 1, "replace": 0}[change]


def test_stale_offline_fallback_keeps_original_age_and_live_rejects_it(issues, monkeypatch):
    client, _, _, row, seed = issues
    ck, original = seed("DL-1", age=10000, ttl=1)
    monkeypatch.setattr(client.provider, "get_json", lambda *a, **k: (_ for _ in ()).throw(UpstreamUnavailable("offline")))
    result = client.issues_by_keys_result(["DL-1"], light=True)
    assert result["complete"] and result["issues"] == [row("DL-1", "cached")]
    assert client.cache.get_stale_at(ck)[1] == original["fetchedAt"]
    with read_scope(cache_mode="live"):
        result = client.issues_by_keys_result(["DL-1"], light=True)
    assert not result["complete"] and result["missing"][0]["kind"] == "transport"


def test_live_only_reuses_current_request_validation_and_hot_expires(issues):
    client, clock, calls, _, seed = issues
    seed("DL-1")
    with read_scope(cache_mode="live"):
        assert client.issues_by_keys_result(["DL-1"], light=True)["complete"]
        assert client.issues_by_keys_result(["DL-1"], light=True)["complete"]
    assert len(calls) == 1
    with read_scope(cache_mode="live"):
        assert client.issues_by_keys_result(["DL-1"], light=True)["complete"]
    assert len(calls) == 2
    clock[0] += client.s.cache_ttl_seconds + 1
    with read_scope(cache_mode="hot"):
        assert client.issues_by_keys_result(["DL-1"], light=True)["complete"]
    assert len(calls) == 3


def test_expired_newer_light_does_not_hide_eligible_full_and_cool_retains_original_age(issues):
    client, _, calls, _, seed = issues
    seed("DL-1", "issue", age=100, label="full")
    seed("DL-1", "issueL", age=10, label="expired", ttl=1)
    assert client.issues_by_keys_result(["DL-1"], light=True)["issues"][0]["fields"]["summary"] == "full"
    with read_scope(cache_mode="cool") as sources:
        assert client.issues_by_keys_result(["DL-1"], light=True)["issues"][0]["fields"]["summary"] == "expired"
    assert len(sources) == 2 and all(not source["upstreamValidated"] for source in sources)
    assert calls == []


def test_bulk_hit_does_not_commit_a_caller_owned_transaction(issues):
    client, clock, calls, _, seed = issues
    ck, _ = seed("DL-1")
    accessed = client.cache._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (ck,)).fetchone()[0]
    clock[0] += 20
    client.cache._conn.execute("BEGIN")
    assert client.issues_by_keys_result(["DL-1"], light=True)["complete"]
    assert client.cache._conn.in_transaction and calls == []
    client.cache._conn.rollback()
    assert client.cache._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (ck,)).fetchone()[0] == accessed


@pytest.mark.parametrize("error,kind", [(SessionExpired("auth"), "auth"), (UpstreamUnavailable("down"), "transport"), (KeyError("hidden"), "unavailable")])
def test_cached_success_survives_per_key_missing_and_retryable_errors_fail_fast(issues, monkeypatch, error, kind):
    client, _, _, row, seed = issues
    seed("DL-1")
    monkeypatch.setattr(client.provider, "get_json", lambda *a, **k: (_ for _ in ()).throw(error))
    result = client.issues_by_keys_result(["DL-2", "DL-1"], light=True)
    assert result["issues"] == [row("DL-1", "cached")]
    assert result["missing"][0]["key"] == "DL-2" and result["missing"][0]["kind"] == kind
    if kind != "unavailable":
        with pytest.raises(type(error)):
            client.issues_by_keys(["DL-1", "DL-2"], light=True)


def test_malformed_hit_invalidates_both_projections_and_remains_retryable(issues):
    client, _, _, _, seed = issues
    seed("DL-1", "issue")
    ck, _ = seed("DL-1", "issueL", age=0)
    client.cache.set(ck, {"key": "DL-999", "fields": {}}, 600)
    result = client.issues_by_keys_result(["DL-1"], light=True)
    assert not result["complete"] and result["missing"][0]["kind"] == "other"
    assert client.cache.get(ck) is None
    assert client.cache.get(f"issue:{client.env}:DL-1") is None
    assert client.issues_by_keys_result(["DL-1"], light=True)["complete"]


def test_full_null_payload_remains_a_getter_miss(issues):
    client, _, calls, row, _ = issues
    client.cache.set(f"issue:{client.env}:DL-1", None, 600)
    assert client.issues_by_keys_result(["DL-1"])["issues"] == [row("DL-1")]
    assert calls == ["/rest/api/2/issue/DL-1"]
