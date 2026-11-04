"""Status grouping saves requests without changing workload counts or window reuse."""

import pytest

from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


@pytest.mark.parametrize("days", [7, 14, 28])
def test_workload_groups_cold_status_queries_and_keeps_all_window_results(monkeypatch, days):
    user = "test.ui01"
    reference = JiraClient(get_settings(), Cache(":memory:"))
    expected = {
        window: {
            "open": reference._wl_counts(reference.WL_BUCKETS["open"].format(u=user), window),
            "inProgress": reference._wl_counts(
                reference.WL_BUCKETS["inProgress"].format(u=user), window),
            "done7d": reference._wl_counts(
                f'assignee = "{user}" AND ' + reference.wl_done_jql(days)),
        }
        for window in reference.WL_ASSIGNED_WINDOWS
    }
    client = JiraClient(get_settings(), Cache(":memory:"))
    original = client.provider.get_json
    queries = []

    def get(path, params=None, **kwargs):
        if path == "/rest/api/2/search" and "statusCategory" in (params or {}).get("jql", ""):
            queries.append(params["jql"])
        return original(path, params=params, **kwargs)

    monkeypatch.setattr(client.provider, "get_json", get)
    for window in client.WL_ASSIGNED_WINDOWS:
        result = client.workload_person(user, done_days=days, assigned_window=window)
        assert not result.get("error")
        assert {bucket: result[bucket] for bucket in expected[window]} == expected[window]
        # One shared status group and the separate Done-without-resolution fallback, each
        # fitting a fixture page. The next assigned-window change must be entirely local.
        assert len(queries) == 2
