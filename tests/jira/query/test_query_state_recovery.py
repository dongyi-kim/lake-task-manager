"""Mutation-generation cancellation is not transport failure; only picker reads retry once."""
import pytest

from app.auth.base import PermissionDenied, QueryStateChanged, SessionExpired, UpstreamUnavailable
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient, MutationEvent


@pytest.fixture
def client():
    value = JiraClient(get_settings(), Cache(":memory:"))
    yield value
    value.close()
    value.cache.close()


def test_real_jql_generation_race_raises_consistency_type_without_publishing(client, monkeypatch):
    def fetch(*_args):
        client._record_mutation(MutationEvent("fields", "DL-1", changed_fields=("summary",)))
        return []
    monkeypatch.setattr(client, "_fetch_exhaustive_jql", fetch)
    with pytest.raises(QueryStateChanged):
        client.search_issues("key = DL-1 OR key = DL-2")
    assert not client.cache.entries_by_prefix("jqlleaf:")
    assert not client.cache.entries_by_prefix("jqlcurrent:")
    assert not client.upstream_down()


@pytest.mark.parametrize("always", [False, True])
def test_parent_candidates_retries_consistency_cancellation_once(client, monkeypatch, always):
    calls = []
    def build(*_args):
        calls.append(True)
        if always or len(calls) == 1:
            raise QueryStateChanged("query changed")
        return {"items": [{"key": "DL-1"}]}
    monkeypatch.setattr(client, "_parent_task_candidates_build", build)
    if always:
        with pytest.raises(QueryStateChanged):
            client.parent_task_candidates()
        assert not client.cache.entries_by_prefix("epic_cand:")
    else:
        assert client.parent_task_candidates() == {"items": [{"key": "DL-1"}]}
        assert client.parent_task_candidates() == {"items": [{"key": "DL-1"}]}
    assert len(calls) == 2


@pytest.mark.parametrize("error_type", [UpstreamUnavailable, SessionExpired, PermissionDenied])
def test_parent_candidates_never_retries_other_failures(client, monkeypatch, error_type):
    calls = []
    def fail(*_args):
        calls.append(True)
        raise error_type("real failure")
    monkeypatch.setattr(client, "_parent_task_candidates_build", fail)
    with pytest.raises(error_type):
        client.parent_task_candidates()
    assert calls == [True] and not client.cache.entries_by_prefix("epic_cand:")
