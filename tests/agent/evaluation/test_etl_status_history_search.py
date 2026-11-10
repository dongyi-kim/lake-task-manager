"""Small public-Store endpoint contracts; no year corpus or business oracle."""
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from jira820 import make_app
from jira820.config import Config
import pytest

from app.mock.permissions import PermissionStore
from app.mock.query_compat import install_query_compat
from tools.codex_mcp_eval.etl.status_history_search import install_status_history_search


OBSERVED = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)


def issue(key, status, *, created="2026-01-01T12:00:00+00:00", history=(),
          component="ETL", parent=None, kind="Task", summary=None):
    return {"key": key, "project": "DL", "summary": summary or key,
        "description": "Public source", "type": kind, "component": component,
        "statusName": status, "statusCategory": "done" if status in {"Closed", "Resolved"} else "inprogress",
        "created": date.fromisoformat(created[:10]), "updated": date(2026, 1, 30),
        "resolved": date(2026, 1, 30) if status in {"Closed", "Resolved"} else None,
        "due": None, "assignee": "u01", "reporter": "u01", "labels": [], "sp": 1,
        "parentKey": parent, "epicKey": None, "comments": [], "worklog": [],
        "_timestamps": {"created": created},
        "changelog": [{"date": when, "author": "u01", "items": [{"field": "status",
            "fromString": before, "toString": after, "from": before, "to": after}]}
            for when, before, after in history]}


def public_rows():
    return [
        issue("DL-1", "Resolved", history=[("2026-01-25T12:00:00+00:00", "In Progress", "Resolved")]),
        issue("DL-2", "Resolved", parent="DL-9", history=[
            ("2026-01-04T12:00:00+00:00", "Open", "In Progress"),
            ("2026-01-08T12:00:00+00:00", "In Progress", "Resolved"),
            ("2026-01-12T12:00:00+00:00", "Resolved", "Reopened"),
            ("2026-01-13T12:00:00+00:00", "Reopened", "In Progress"),
            ("2026-01-28T12:00:00+00:00", "In Progress", "Resolved")]),
        issue("DL-3", "In Progress", parent="DL-9", summary="WAS AND retry OR recovery"),
        issue("DL-4", "Closed", summary="WAS"),
        issue("DL-5", "Closed", component="Catalog"),
        issue("DL-9", "Closed", kind="Epic"),
    ]


@pytest.fixture
def endpoints():
    clients = []
    def build(rows=None, *, install=True, denied=(), delegate_hook=None,
              search_timezone="UTC", user_timezone=None, current_user="u01"):
        store = PermissionStore(Config(project_key="DL", current_user=current_user, latency_ms=0), seed=False)
        store.users = {"u01": {"name": "u01", "displayName": "Public User",
                                "timeZone": search_timezone if user_timezone is None else user_timezone}}
        store.issues = {row["key"]: row for row in (public_rows() if rows is None else rows)}
        store.denied_issue_keys = set(denied)
        store.reindex()
        original_fields = store.serializer.issue_fields
        def fields(row, base_url=""):
            return {**original_fields(row, base_url), **row.get("_timestamps", {})}
        store.serializer.issue_fields = fields
        app = make_app(store=store)
        install_query_compat(app)
        if delegate_hook:
            route = next(route for route in app.router.routes
                         if getattr(route, "path", None) == "/rest/api/2/search")
            original = route.endpoint
            def observed(request, **kwargs):
                delegate_hook(request, kwargs)
                return original(request, **kwargs)
            route.endpoint = observed
        registration = install_status_history_search(app, observed_until=OBSERVED,
                                                     search_timezone=search_timezone) if install else None
        client = TestClient(app, raise_server_exceptions=False)
        clients.append(client)
        return client, store, app, registration
    yield build
    for client in reversed(clients):
        client.close()


def search(client, query, **params):
    return client.get("/rest/api/2/search", params={"jql": query, **params})


def keys(response):
    assert response.status_code == 200, response.text
    return [row["key"] for row in response.json()["issues"]]


def test_was_uses_public_prior_status_and_birth_state_without_current_status_overmatch(endpoints):
    client, *_ = endpoints()
    assert keys(search(client, 'status WAS "In Progress" ORDER BY key ASC')) == ["DL-1", "DL-2", "DL-3"]
    assert keys(search(client, 'status WAS "Reopened"')) == ["DL-2"]


def test_was_in_and_or_have_the_same_deduplicated_membership(endpoints):
    client, *_ = endpoints()
    assert keys(search(client, 'status WAS IN ("in progress", Reopened, "In Progress")')) == ["DL-1", "DL-2", "DL-3"]
    assert keys(search(client, 'status WAS "In Progress" OR status WAS Reopened')) == ["DL-1", "DL-2", "DL-3"]


def test_parent_permissions_order_projection_and_pagination_use_existing_endpoint(endpoints):
    client, *_ = endpoints(denied={"DL-3"})
    query = 'project = DL AND (status WAS "In Progress" OR status WAS Reopened) ORDER BY key DESC'
    first = search(client, query, startAt=0, maxResults=1, fields="summary,status")
    second = search(client, query, startAt=1, maxResults=1, fields="summary,status")
    assert keys(first) == ["DL-2"]
    assert keys(second) == ["DL-1"]
    assert first.json()["total"] == second.json()["total"] == 2
    assert set(first.json()["issues"][0]["fields"]) == {"summary", "status"}
    assert keys(search(client, 'parent = DL-9 AND status WAS "In Progress"')) == ["DL-2"]


def test_existing_negated_parent_semantics_keep_empty_parents_excluded(endpoints):
    rows = [issue("DL-1", "In Progress", parent="DL-9"),
            issue("DL-2", "In Progress", parent="DL-8"), issue("DL-3", "In Progress")]
    client, *_ = endpoints(rows)
    assert keys(search(client, 'NOT (parent = DL-9) AND status WAS "In Progress"')) == ["DL-2"]
    assert keys(search(client, 'NOT NOT (parent = DL-9) AND status WAS "In Progress"')) == ["DL-1"]


def test_quoted_logical_words_remain_one_text_predicate(endpoints):
    client, *_ = endpoints()
    assert keys(search(client, 'summary ~ "WAS AND retry OR recovery" AND status WAS "In Progress"')) == ["DL-3"]


@pytest.mark.parametrize("query", [
    'assignee WAS u01', 'status CHANGED', 'status WAS NOT Open', 'status WAS NOT IN (Open)',
    'status WAS 3', 'status WAS "3"', 'status WAS IN ()', 'status WAS Open BY u01',
    'status WAS Open AFTER "2026-01-10"', 'status WAS Open TRAILING',
    'unknownField = value', 'status >= Open', 'key = inventedFunction()',
    'status WAS "In Progress" OR unknownField = value',
    'key = DL-1 OR (status WAS Open BY u01)',
    'key = DL-1 ORDER BY unknownField ASC',
    'key = DL-1 ORDER BY',
    'created >= "-1m"', 'updated >= "-1h"', 'resolved >= "-1y"',
    r'key = DL-1 OR component IN ("ETL\name", Catalog)',
])
def test_unsupported_atom_rejects_the_entire_query_with_a_jira_400(endpoints, query):
    client, *_ = endpoints()
    response = search(client, query)
    assert response.status_code == 400, response.text
    assert response.json()["errorMessages"]
    assert response.json()["errors"] == {}


@pytest.mark.parametrize("query", [
    'project = DL AND component = ETL AND issuetype != Epic ORDER BY key ASC',
    'parent IN (DL-9) ORDER BY key DESC',
    'status IN (Resolved, Closed) AND component != Catalog ORDER BY created ASC',
])
def test_supported_current_only_queries_preserve_original_response(endpoints, query):
    baseline, *_ = endpoints(install=False)
    repaired, *_ = endpoints()
    original = search(baseline, query, startAt=0, maxResults=2, fields="summary,status,parent")
    adapted = search(repaired, query, startAt=0, maxResults=2, fields="summary,status,parent")
    assert adapted.status_code == original.status_code == 200
    assert adapted.json() == original.json()


@pytest.mark.parametrize("field", ['"Epic Link"', 'epiclink', 'customfield_10008'])
def test_epic_membership_uses_all_public_keys_and_excludes_empty_from_negatives(endpoints, field):
    rows = [issue(f"DL-{i}", "In Progress") for i in range(1, 6)]
    for row, epic in zip(rows, ("DL-8", "DL-9", None, "DL-10", "")):
        row["epicKey"] = epic
    client, *_ = endpoints(rows)
    assert keys(search(client, field + ' IN (DL-8, "DL-9", DL-8) ORDER BY key ASC')) == ["DL-1", "DL-2"]
    assert keys(search(client, field + ' = DL-8')) == ["DL-1"]
    assert keys(search(client, field + ' != DL-8')) == ["DL-2", "DL-4"]
    assert keys(search(client, field + ' NOT IN (DL-8, DL-9)')) == ["DL-4"]
    assert keys(search(client, field + ' IN (DL-11, DL-12)')) == []


@pytest.mark.parametrize("query", [
    'component IN ("ETL, Catalog", Catalog)',
    'key = DL-1 OR status IN ("In Progress, QA", Resolved)',
    '"Epic Link" IN ("DL-8,DL-9", DL-10)',
])
def test_unproven_embedded_comma_list_values_reject_the_whole_query(endpoints, query):
    client, *_ = endpoints()
    response = search(client, query, fields="summary")
    assert response.status_code == 400
    assert response.json()["errorMessages"]


def test_unassigned_fields_and_unknown_nonquery_authors_keep_original_serialization(endpoints):
    row = issue("DL-1", "Resolved", history=[("2026-01-15T12:00:00+00:00", "Open", "Resolved")])
    row["assignee"], row["reporter"] = None, "u99"
    row["changelog"][0]["author"] = "u99"
    row["comments"] = [{"id": "10001", "author": "u99", "body": "Public review",
                        "created": "2026-01-15T12:00:00+00:00"}]
    baseline, *_ = endpoints([row], install=False)
    adapted, *_ = endpoints([row])
    for path, params in (("/rest/api/2/issue/DL-1", {"expand": "changelog"}),
                         ("/rest/api/2/issue/DL-1/comment", {})):
        original = baseline.get(path, params=params)
        repaired = adapted.get(path, params=params)
        assert repaired.status_code == original.status_code == 200
        assert repaired.json() == original.json()
    assert keys(search(adapted, 'status WAS Open')) == ["DL-1"]


def test_two_apps_and_public_mutation_do_not_share_history_membership(endpoints):
    first, store, *_ = endpoints([issue("DL-1", "Closed")])
    second, *_ = endpoints([issue("DL-2", "In Progress")])
    assert keys(search(first, 'status WAS "In Progress"')) == []
    assert keys(search(second, 'status WAS "In Progress"')) == ["DL-2"]
    store.issues["DL-1"]["changelog"] = [{"date": "2026-01-15T12:00:00+00:00", "author": "u01",
        "items": [{"field": "status", "fromString": "In Progress", "toString": "Closed"}]}]
    assert keys(search(first, 'status WAS "In Progress"')) == ["DL-1"]


def test_invalid_public_status_chain_does_not_become_empty_or_broad_success(endpoints):
    bad = issue("DL-1", "Closed", history=[("2026-01-15T12:00:00+00:00", "Open", "In Progress")])
    client, *_ = endpoints([bad])
    response = search(client, 'status WAS "In Progress"')
    assert response.status_code == 400
    assert response.json()["errorMessages"]


def test_large_history_membership_does_not_expand_into_a_16kib_internal_query(endpoints):
    rows = [issue(f"DL-{1000000 + i}", "In Progress") for i in range(2200)]
    rows += [issue(f"DL-{2000000 + i}", "Closed") for i in range(2200)]
    client, store, *_ = endpoints(rows)
    original_mapping = store.issues
    query = 'status WAS "In Progress" ORDER BY key ASC'
    first = search(client, query, startAt=0, maxResults=2, fields="summary")
    last = search(client, query, startAt=2199, maxResults=2, fields="summary")
    assert keys(first) == ["DL-1000000", "DL-1000001"]
    assert keys(last) == ["DL-1002199"]
    assert first.json()["total"] == last.json()["total"] == 2200
    assert store.issues is original_mapping and len(store.issues) == 4400


def test_interleaved_searches_keep_request_views_and_raw_wire_separate(endpoints):
    barrier, observed = Barrier(2), []
    def during_delegate(request, kwargs):
        observed.append((request.query_params["jql"], set(request.app.state.store.issues), kwargs["jql"]))
        barrier.wait(timeout=5)
    client, store, app, *_ = endpoints(delegate_hook=during_delegate)
    original_mapping = store.issues
    first = 'key = DL-1 AND status WAS "In Progress" ORDER BY key ASC'
    second = 'key = DL-2 AND status WAS Reopened ORDER BY key DESC'
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(search, client, first)
        b = pool.submit(search, client, second)
        assert keys(a.result(timeout=10)) == ["DL-1"]
        assert keys(b.result(timeout=10)) == ["DL-2"]
    assert {(raw, frozenset(members)) for raw, members, _ in observed} == {
        (first, frozenset({"DL-1"})), (second, frozenset({"DL-2"}))}
    assert {rewritten for _, _, rewritten in observed} == {"ORDER BY key ASC", "ORDER BY key DESC"}
    assert app.state.store is store and store.issues is original_mapping
    assert set(store.issues) == {"DL-1", "DL-2", "DL-3", "DL-4", "DL-5", "DL-9"}


def test_install_is_per_app_idempotent_and_uninstall_restores_original_route(endpoints):
    client, store, app, registration = endpoints()
    assert registration is not None
    route_count = len(app.router.routes)
    repeated = install_status_history_search(app, observed_until=OBSERVED, search_timezone="UTC")
    assert repeated is registration
    assert len(app.router.routes) == route_count
    assert keys(search(client, 'status WAS Reopened')) == ["DL-2"]
    registration.uninstall()
    assert len(app.router.routes) == route_count - 1
    assert app.state.store is store
    assert keys(search(client, 'status WAS Reopened')) == ["DL-1", "DL-2", "DL-3", "DL-4", "DL-5", "DL-9"]


def test_public_kst_actor_and_each_users_own_timezone_are_exposed(endpoints):
    client, store, *_ = endpoints(search_timezone="Asia/Seoul")
    myself = client.get("/rest/api/2/myself")
    assert myself.status_code == 200
    assert myself.json()["name"] == "u01"
    assert myself.json()["timeZone"] == "Asia/Seoul"
    store.users["u02"] = {"name": "u02", "displayName": "UTC Reviewer", "timeZone": "UTC"}
    store.issues["DL-1"]["assignee"] = "u02"
    ticket = client.get("/rest/api/2/issue/DL-1", params={"fields": "assignee,reporter"}).json()
    assert ticket["fields"]["assignee"]["timeZone"] == "UTC"
    assert ticket["fields"]["reporter"]["timeZone"] == "Asia/Seoul"


@pytest.mark.parametrize("timezone_name", ["", "Unknown/Timezone", "Asia/Seoul"])
def test_constructor_rejects_unknown_or_inconsistent_public_actor_timezone(endpoints, timezone_name):
    with pytest.raises(ValueError):
        endpoints(search_timezone="UTC", user_timezone=timezone_name)


def test_query_rejects_a_public_actor_timezone_changed_after_install(endpoints):
    client, store, *_ = endpoints(search_timezone="Asia/Seoul")
    store.users["u01"]["timeZone"] = "UTC"
    assert search(client, 'status WAS "In Progress"').status_code == 400
    assert client.get("/rest/api/2/myself").json()["timeZone"] == "UTC"


def test_unknown_current_actor_does_not_use_the_dependencies_admin_fallback(endpoints):
    with pytest.raises(ValueError):
        endpoints(current_user="missing")


def test_during_matches_carryover_without_an_in_window_transition(endpoints):
    client, *_ = endpoints()
    response = search(client, 'status WAS "In Progress" DURING ("2026-01-10", "2026-01-20")')
    assert keys(response) == ["DL-1", "DL-2", "DL-3"]


def test_future_born_public_issue_has_no_status_before_the_observation_horizon(endpoints):
    client, *_ = endpoints([issue("DL-1", "In Progress", created="2026-02-01T00:00:00+00:00")])
    assert keys(search(client, 'status WAS "In Progress"')) == []
    assert keys(search(client, 'status WAS "In Progress" DURING ("2026-01-10", "2026-01-20")')) == []


@pytest.mark.parametrize("millis,expected", [(-1, []), (0, []), (999, []), (1000, ["DL-1"]), (1001, ["DL-1"])])
def test_during_next_change_uses_the_native_lower_plus_one_second(endpoints, millis, expected):
    changed = datetime(2026, 1, 10, 12, tzinfo=timezone.utc) + timedelta(milliseconds=millis)
    row = issue("DL-1", "Resolved", history=[(changed.isoformat(), "In Progress", "Resolved")])
    client, *_ = endpoints([row])
    assert keys(search(client, 'status WAS "In Progress" DURING ("2026-01-10 12:00", "2026-01-10 12:00")')) == expected


@pytest.mark.parametrize("millis,expected", [(-1, ["DL-1"]), (0, ["DL-1"]), (1, [])])
def test_during_entry_at_the_minute_upper_second_is_inclusive(endpoints, millis, expected):
    changed = datetime(2026, 1, 10, 12, 0, 59, tzinfo=timezone.utc) + timedelta(milliseconds=millis)
    row = issue("DL-1", "In Progress", history=[(changed.isoformat(), "Open", "In Progress")])
    client, *_ = endpoints([row])
    assert keys(search(client, 'status WAS "In Progress" DURING ("2026-01-10 12:00", "2026-01-10 12:00")')) == expected


@pytest.mark.parametrize("millis,expected", [(-1, ["DL-1"]), (0, ["DL-1"]), (1, [])])
def test_during_birth_has_no_precreation_lifetime_at_the_day_upper_second(endpoints, millis, expected):
    created = datetime(2026, 1, 10, 23, 59, 59, tzinfo=timezone.utc) + timedelta(milliseconds=millis)
    client, *_ = endpoints([issue("DL-1", "In Progress", created=created.isoformat())])
    assert keys(search(client, 'status WAS "In Progress" DURING ("2026/01/10", "2026/01/10")')) == expected


def test_during_retains_zero_duration_initial_and_equal_timestamp_status_rows(endpoints):
    stamp = "2026-01-10T12:00:10+00:00"
    rows = [issue("DL-1", "Resolved", history=[(stamp, "Open", "In Progress"),
                                               (stamp, "In Progress", "Resolved")]),
            issue("DL-2", "Resolved", created=stamp, history=[(stamp, "In Progress", "Resolved")])]
    client, *_ = endpoints(rows)
    assert keys(search(client, 'status WAS "In Progress" DURING ("2026-01-10 12:00", "2026-01-10 12:00")')) == ["DL-1", "DL-2"]


def test_during_kst_midnight_and_equivalent_public_offsets_agree_with_myself(endpoints):
    rows = [issue("DL-1", "In Progress", created="2026-01-09T15:00:00+00:00"),
            issue("DL-2", "In Progress", created="2026-01-10T00:00:00+09:00"),
            issue("DL-3", "In Progress", created="2026-01-10T15:00:00+00:00")]
    client, *_ = endpoints(rows, search_timezone="Asia/Seoul")
    assert client.get("/rest/api/2/myself").json()["timeZone"] == "Asia/Seoul"
    assert keys(search(client, 'status WAS "In Progress" DURING ("2026-01-10", "2026-01-10")')) == ["DL-1", "DL-2"]
    assert keys(search(client, 'status WAS "In Progress" DURING ("2026-01-10 00:00", "2026-01-10 00:00")')) == ["DL-1", "DL-2"]


def test_during_uses_public_history_time_without_backdating_from_comment_prose(endpoints):
    row = issue("DL-1", "Resolved", history=[("2026-01-12T12:00:00+00:00", "In Progress", "Resolved")])
    row["comments"] = [{"author": "u01", "body": "Business occurred 2026-01-09; recorded 2026-01-12",
                        "created": "2026-01-12T12:00:00+00:00"}]
    row["active_intervals"] = []
    client, *_ = endpoints([row])
    assert keys(search(client, 'status WAS "In Progress" DURING ("2026-01-10", "2026-01-10")')) == ["DL-1"]


@pytest.mark.parametrize("bounds", [
    '("2026-02-30", "2026-03-01")', '("2026-01-11", "2026-01-10")',
    '(startOfMonth(), "2026-01-20")', '("2026-01-10T12:00:00+09:00", "2026-01-20")',
    '("2026-01-10" "2026-01-20")', '("2026-01-10", "2026-01-20", "2026-01-21")',
    '("2026-01-10", "2026-01-20") BY u01',
])
def test_invalid_during_operands_fail_the_whole_or_query(endpoints, bounds):
    client, *_ = endpoints()
    response = search(client, 'key = DL-1 OR status WAS "In Progress" DURING ' + bounds)
    assert response.status_code == 400
    assert response.json()["errorMessages"]
