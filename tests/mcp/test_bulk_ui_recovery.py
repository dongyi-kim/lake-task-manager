"""The UI's HTTP replay shares the same durable batch receipts as MCP."""
from fastapi.testclient import TestClient
import pytest


def test_ui_http_response_loss_replays_confirmed_rows_without_duplicate_creation(build_env):
    env = build_env()
    body = {"mode": "task", "items": [
        {"summary": f"UI response-loss row {index}", "type": "Task", "epic": None}
        for index in range(5)], "clientMutationId": "bulk:ui:http:0001"}
    before = set(env.store.issues)
    with TestClient(env.app) as http:
        body["expectedScope"] = http.get("/api/bulk/create-context").json()
        first = http.post("/api/bulk/create", json=body)
        assert first.status_code == 200 and first.json()["ok"]
        created_keys = {row["key"] for row in first.json()["created"]}
        assert len(created_keys) == 5 and set(env.store.issues) - before == created_keys
        # The browser discarded the first response. Reopening sends the persisted exact batch.
        replay = http.post("/api/bulk/create", json=body)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert set(env.store.issues) - before == created_keys
        writes = [call for call in env.calls if call[:2] == ("post", "/rest/api/2/issue")]
        assert len(writes) == 5


@pytest.mark.parametrize("field", ["environment", "jiraBase", "projectKey", "userId"])
def test_bulk_ui_scope_rechecks_authoritative_context_before_any_write(build_env, monkeypatch, field):
    env = build_env()
    with TestClient(env.app) as http:
        response = http.get("/api/bulk/create-context")
        assert response.headers["cache-control"] == "no-store"
        scope = response.json()
        # Change the runtime after GET but before POST, without any browser auth-ok event.
        if field == "environment":
            monkeypatch.setattr(env.client, "env", "changed-env")
        elif field == "jiraBase":
            monkeypatch.setattr(env.client.s, "jira_base", "https://changed-jira.invalid")
        elif field == "projectKey":
            monkeypatch.setattr(env.client.s, "project_key", "OTHER")
        else:
            env.store.config.current_user = "u02"
        body = {"mode": "task", "items": [{"summary": "must not create", "type": "Task", "epic": None}],
                "clientMutationId": "bulk:ui:scope:0001", "expectedScope": scope}
        blocked = http.post("/api/bulk/create", json=body)
        assert blocked.status_code == 409 and blocked.json()["code"] == "bulk_scope_changed"
        assert not [call for call in env.calls if call[:2] == ("post", "/rest/api/2/issue")]
        current = http.get("/api/bulk/create-context").json()
        assert current[field] != scope[field]
        # Both GET and the POST fence query upstream identity, bypassing the old /api/me cache.
        assert len([call for call in env.calls if call[:2] == ("get", "/rest/api/2/myself")]) == 3


def test_bulk_ui_lost_write_auth_error_then_reconciliation_returns_original_key(build_env, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.auth.base import SessionExpired, UpstreamUnavailable

    env = build_env()
    original_post, original_get = env.provider.post_json, env.provider.get_json
    committed = []

    def lost_response(path, json_body=None, **kwargs):
        result = original_post(path, json_body, **kwargs)
        if path == "/rest/api/2/issue":
            committed.append(result["key"])
            created = datetime.now(timezone.utc) + timedelta(seconds=2)
            env.store.issues[result["key"]].update(created=created.date(), tcreated=created.strftime("%H:%M:%S"))
            raise UpstreamUnavailable("response lost after committed fixture write")
        return result

    def unauthenticated_reconciliation(path, *args, **kwargs):
        if path == "/rest/api/2/search":
            raise SessionExpired("fixture reconciliation authentication expired")
        return original_get(path, *args, **kwargs)

    with TestClient(env.app) as http:
        body = {"mode": "task", "items": [{"summary": "lost UI response then login", "type": "Task", "epic": None}],
                "clientMutationId": "bulk:ui:auth:0001", "expectedScope": http.get("/api/bulk/create-context").json()}
        monkeypatch.setattr(env.provider, "post_json", lost_response)
        first = http.post("/api/bulk/create", json=body).json()
        assert first["failed"][0]["code"] == "outcome_unknown"
        monkeypatch.setattr(env.provider, "post_json", original_post)
        monkeypatch.setattr(env.provider, "get_json", unauthenticated_reconciliation)
        second = http.post("/api/bulk/create", json=body).json()
        assert second["failed"][0]["code"] == "authentication_required"
        monkeypatch.setattr(env.provider, "get_json", original_get)
        recovered = http.post("/api/bulk/create", json=body).json()
        assert recovered["ok"] and [row["key"] for row in recovered["created"]] == committed
        assert len(committed) == 1
        assert len([call for call in env.calls if call[:2] == ("post", "/rest/api/2/issue")]) == 1


@pytest.mark.parametrize("when", ["validation", "after-first-row"])
@pytest.mark.parametrize("replace_provider", [True, False], ids=["reset-provider", "same-provider-new-actor"])
def test_bulk_dispatch_cannot_switch_actor_mid_batch(build_env, monkeypatch, when, replace_provider):
    from copy import copy
    from app.services.ticket_commands import TicketCommands

    env = build_env()
    original_validate, original_post = TicketCommands.bulk_validate, env.provider.post_json
    # Both fixture providers share the TestClient owned/closed by build_env's finalizer.
    monkeypatch.setattr(env.provider, "close", lambda: None)
    switched = False

    def switch_identity():
        nonlocal switched
        if switched:
            return
        switched = True
        env.store.config.current_user = "u02"
        if replace_provider:
            replacement = copy(env.provider)
            env.client.reset_provider()
            env.client._provider, env.client._provider_built = replacement, True

    def validate_then_switch(self, mode, items):
        checked = original_validate(self, mode, items)
        switch_identity()
        return checked

    def post_then_switch(path, json_body=None, **kwargs):
        result = original_post(path, json_body, **kwargs)
        if path == "/rest/api/2/issue":
            switch_identity()
        return result

    with TestClient(env.app) as http:
        body = {"mode": "task", "items": [
            {"summary": f"actor fence row {index}", "type": "Task", "epic": None} for index in range(5)],
                "clientMutationId": "bulk:ui:midbatch:0001", "expectedScope": http.get("/api/bulk/create-context").json()}
        before = set(env.store.issues)
        if when == "validation":
            monkeypatch.setattr(TicketCommands, "bulk_validate", validate_then_switch)
        else:
            monkeypatch.setattr(env.provider, "post_json", post_then_switch)
        first = http.post("/api/bulk/create", json=body)
        keys = set(env.store.issues) - before
        assert len(keys) == (0 if when == "validation" else 1)
        assert all(env.store.issues[key]["reporter"] == "u01" for key in keys)
        assert first.status_code == 409 or first.json()["ok"] is False
        # Return to the original actor and recover the original batch without changing its ID.
        monkeypatch.setattr(TicketCommands, "bulk_validate", original_validate)
        monkeypatch.setattr(env.provider, "post_json", original_post)
        monkeypatch.setattr(env.client._provider, "post_json", original_post)
        env.store.config.current_user = "u01"
        recovered = http.post("/api/bulk/create", json=body)
        assert recovered.status_code == 200 and recovered.json()["ok"]
        assert len(set(env.store.issues) - before) == 5
        assert keys <= {row["key"] for row in recovered.json()["created"]}
        assert all(env.store.issues[row["key"]]["reporter"] == "u01" for row in recovered.json()["created"])
