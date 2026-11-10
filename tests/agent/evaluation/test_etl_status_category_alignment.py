"""Public status scheme must agree on Store search and serialized REST wires."""
from copy import deepcopy
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from jira820 import make_app
from jira820.config import Config
import pytest

from app.mock.permissions import PermissionStore
from app.mock.query_compat import install_query_compat
from tools.codex_mcp_eval.etl.status_history_search import install_status_history_search


def provider(*categories):
    store = PermissionStore(Config(project_key="DL", current_user="u01", latency_ms=0), seed=False)
    store.users = {"u01": {"name": "u01", "displayName": "Public actor", "timeZone": "UTC"}}
    store.issues = {f"DL-{i}": {"key": f"DL-{i}", "project": "DL", "summary": "Public work",
        "type": "Task", "statusName": "Reopened", "statusCategory": category,
        "created": date(2026, 1, 1), "updated": date(2026, 1, 2), "resolved": None,
        "due": None, "component": "ETL", "assignee": "u01", "reporter": "u01",
        "comments": [], "changelog": [], "subtasks": [], "labels": []}
        for i, category in enumerate(categories, 1)}
    store.reindex()
    app = make_app(store=store)
    install_query_compat(app)
    return app, store


def install(app):
    return install_status_history_search(app,
        observed_until=datetime(2026, 1, 31, tzinfo=timezone.utc), search_timezone="UTC")


def test_public_categories_align_issue_search_and_status_catalog_without_changing_ids():
    app, store = provider("inprogress")
    ids = {name: sid for name, _cat, sid in store.config.statuses}
    registration = install(app)
    with TestClient(app) as client:
        issue = client.get("/rest/api/2/issue/DL-1").json()["fields"]["status"]
        search = client.get("/rest/api/2/search", params={
            "jql": "statusCategory = indeterminate", "fields": "status"})
        assert search.status_code == 200, search.text
        assert [row["key"] for row in search.json()["issues"]] == ["DL-1"]
        assert search.json()["issues"][0]["fields"]["status"] == issue
        assert issue["statusCategory"]["key"] == "indeterminate"
        catalog = client.get("/rest/api/2/status").json()
        assert next(row for row in catalog if row["name"] == "Reopened") == issue
        assert client.get("/rest/api/2/search", params={"jql": "statusCategory = new"}).json()["total"] == 0
    assert {name: sid for name, _cat, sid in store.config.statuses} == ids
    assert store.serializer.status_map["Reopened"] == ("inprogress", ids["Reopened"])
    assert registration.contract["publicStatusScheme"]["version"] == "public-status-category-v1"


def test_alignment_is_instance_local_idempotent_and_restores_on_uninstall():
    adapted, store = provider("inprogress")
    ordinary, untouched = provider("inprogress")
    original_config, original_map = store.config.statuses, store.serializer.status_map
    untouched_config, untouched_map = deepcopy(untouched.config.statuses), deepcopy(untouched.serializer.status_map)
    registration = install(adapted)
    assert install(adapted) is registration
    with TestClient(ordinary) as client:
        assert client.get("/rest/api/2/issue/DL-1").json()["fields"]["status"]["statusCategory"]["key"] == "new"
    assert untouched.config.statuses == untouched_config and untouched.serializer.status_map == untouched_map
    registration.uninstall()
    assert store.config.statuses is original_config
    assert store.serializer.status_map is original_map


@pytest.mark.parametrize("categories", [("inprogress", "todo"), ("unknown",), (None,)])
def test_conflicting_or_unknown_public_categories_fail_before_any_adapter_mutation(categories):
    app, store = provider(*categories)
    before = (deepcopy(store.config.statuses), deepcopy(store.serializer.status_map),
              store.serializer.user_obj, list(app.router.routes))
    with pytest.raises(ValueError, match="public status"):
        install(app)
    assert store.config.statuses == before[0] and store.serializer.status_map == before[1]
    assert store.serializer.user_obj == before[2] and app.router.routes == before[3]


def test_undeclared_public_status_cannot_acquire_an_invented_identity():
    app, store = provider("inprogress")
    store.issues["DL-1"]["statusName"] = "Undeclared workflow state"
    with pytest.raises(ValueError, match="public status"):
        install(app)


@pytest.mark.parametrize("name", ["_public_status_scheme", "StatusHistoryInstallation", "install_status_history_search"])
def test_status_wire_semantics_are_bound_into_battery_fingerprint(monkeypatch, name):
    from tools import agent_eval_protocol as protocol
    from tools.codex_mcp_eval.etl import cases, status_history_search
    helper = getattr(status_history_search, name)
    assert helper in cases.CHECKER_DEPENDENCIES
    original = protocol.inspect.getsource
    before = protocol.battery_manifest_sha256([], checker_dependencies=cases.CHECKER_DEPENDENCIES)
    monkeypatch.setattr(protocol.inspect, "getsource", lambda value:
        original(value) + "\n# alternate public wire semantics" if value is helper else original(value))
    assert protocol.battery_manifest_sha256([], checker_dependencies=cases.CHECKER_DEPENDENCIES) != before
