"""Agile Epic-child fallback pagination and cache-integrity contracts."""

import pytest

from app.auth.base import PermissionDenied, UpstreamUnavailable
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


def _row(number):
    return {"key": f"DL-{number}", "fields": {"summary": f"Task {number}"}}


class _PagedAgileProvider:
    def __init__(self, rows, *, server_cap=100, fail_once_at=None):
        self.rows = list(rows)
        self.server_cap = server_cap
        self.fail_once_at = fail_once_at
        self.failed = False
        self.calls = []

    def get_json(self, path, params=None, quiet=False):
        assert path == "/rest/agile/1.0/epic/DL-1/issue"
        params = dict(params or {})
        start = params["startAt"]
        self.calls.append(start)
        if start == self.fail_once_at and not self.failed:
            self.failed = True
            raise UpstreamUnavailable("second Agile page timed out")
        size = min(params["maxResults"], self.server_cap)
        page = self.rows[start:start + size]
        return {
            "startAt": start,
            "maxResults": size,
            "total": len(self.rows),
            "isLast": start + len(page) >= len(self.rows),
            "issues": page,
        }


def _client(monkeypatch, provider):
    client = JiraClient(get_settings(), Cache(":memory:"))
    client._provider = provider
    client._provider_built = True
    # Exercise the Agile compatibility path specifically: the normal JQL path is already fully
    # paginated and is preferred whenever it returns children.
    monkeypatch.setattr(client, "_search", lambda *_args, **_kwargs: [])
    return client


def test_agile_fallback_reads_every_page_and_advances_by_actual_server_page_size(monkeypatch):
    rows = [_row(number) for number in range(1000, 1205)]
    provider = _PagedAgileProvider(rows, server_cap=100)
    client = _client(monkeypatch, provider)

    keys = client.direct_child_keys("DL-1", parent_type="Epic")

    assert keys == [row["key"] for row in rows]
    assert provider.calls == [0, 100, 200]
    assert all(client.cache.get(f"issueL:{client.env}:{key}") is not None for key in keys)
    # The complete identity set is cached; revisiting through dialog/VIT/WBS does not call Jira.
    assert client.direct_child_keys("DL-1", parent_type="Epic") == keys
    assert provider.calls == [0, 100, 200]


def test_agile_fallback_honours_a_smaller_server_cap_without_skipping_rows(monkeypatch):
    rows = [_row(number) for number in range(2000, 2005)]
    provider = _PagedAgileProvider(rows, server_cap=2)
    client = _client(monkeypatch, provider)

    assert client.direct_child_keys("DL-1", parent_type="Epic") == [
        row["key"] for row in rows
    ]
    assert provider.calls == [0, 2, 4]


def test_later_page_failure_warms_finished_issue_rows_but_not_membership(monkeypatch):
    rows = [_row(number) for number in range(3000, 3005)]
    provider = _PagedAgileProvider(rows, server_cap=2, fail_once_at=2)
    client = _client(monkeypatch, provider)
    membership_key = client._direct_child_cache_key("DL-1")

    with pytest.raises(UpstreamUnavailable, match="second Agile page timed out"):
        client.direct_child_keys("DL-1", parent_type="Epic")

    assert client.cache.get_stale(membership_key) is None
    assert client.cache.get(f"issueL:{client.env}:DL-3000") is not None
    assert client.cache.get(f"issueL:{client.env}:DL-3001") is not None
    assert client.cache.get(f"issueL:{client.env}:DL-3002") is None

    # A retry starts from page zero and can now prove/cache the complete relationship.
    assert client.direct_child_keys("DL-1", parent_type="Epic") == [
        row["key"] for row in rows
    ]
    assert provider.calls == [0, 2, 0, 2, 4]
    assert client.cache.get(membership_key) == [row["key"] for row in rows]


@pytest.mark.parametrize("payload, error_type", [
    ({"errorMessages": ["You do not have permission to view this Epic"]}, PermissionDenied),
    ({"startAt": 0, "issues": []}, UpstreamUnavailable),
])
def test_error_or_unproven_empty_page_never_becomes_cached_empty_membership(
        monkeypatch, payload, error_type):
    class Provider:
        def get_json(self, _path, params=None, quiet=False):
            return payload

    client = _client(monkeypatch, Provider())
    membership_key = client._direct_child_cache_key("DL-1")

    with pytest.raises(error_type):
        client.direct_child_keys("DL-1", parent_type="Epic")
    assert client.cache.get_stale(membership_key) is None


def test_permission_failure_can_serve_an_alive_stale_membership_without_overwriting_it(
        monkeypatch):
    class Provider:
        def get_json(self, path, params=None, quiet=False):
            raise PermissionDenied(path, "not permitted")

    client = _client(monkeypatch, Provider())
    membership_key = client._direct_child_cache_key("DL-1")
    client.cache.set(membership_key, ["DL-OLD"], -1)

    assert client.direct_child_keys("DL-1", parent_type="Epic") == ["DL-OLD"]
    assert client.cache.get(membership_key) is None
    assert client.cache.get_stale(membership_key) == ["DL-OLD"]


def test_ticket_children_does_not_promote_membership_failure_to_fresh_empty_panel(monkeypatch):
    class Provider:
        def get_json(self, path, params=None, quiet=False):
            raise UpstreamUnavailable(f"failed to load {path}")

    client = _client(monkeypatch, Provider())

    with pytest.raises(UpstreamUnavailable):
        client.ticket_children("DL-1")

    assert client.cache.get_stale(client._direct_child_cache_key("DL-1")) is None
    assert client.cache.get_stale(f"children:{client.env}:DL-1") is None
