"""Structure writes use fresh preconditions, ordered receipts and explicit partial outcomes."""

from copy import deepcopy

import pytest

from app.auth.base import MutationOutcomeUnknown, UpstreamError
from app.local_mcp.confluence_organization_tools import OrganizationChange
from app.local_mcp.runtime import LocalMcpRuntime
from app.services.confluence_organization import ConfluenceOrganization
from app.services.ticket_commands import CommandError


class Commands:
    def __init__(self):
        self.pages = {str(i): {"id": str(i), "title": f"Page {i}", "type": "page", "status": "current",
                              "space": {"key": "DL"}, "version": {"number": 1}, "ancestors": []}
                      for i in range(1, 6)}
        self.pages["3"]["ancestors"] = [{"id": "1"}]
        self.pages["4"]["space"]["key"] = "OTHER"
        self.pages["5"]["status"] = "trashed"
        self.calls, self.receipts, self.failures = [], {}, {}
        self.direct = {"read": {"operation": "read", "restrictions": {
            "user": {"results": [{"userKey": "u1"}]}, "group": {"results": [{"name": "team"}]}}}}

    def require_compatible(self, operation):
        pass

    def content(self, page_id, expected_version=None, status="current"):
        page = self.pages[str(page_id)]
        if page["status"] != status:
            raise CommandError("not found", 404)
        if expected_version is not None and page["version"]["number"] != expected_version:
            raise CommandError("changed", 409)
        return deepcopy(page)

    def request(self, method, path, body=None, params=None):
        self.calls.append((method, path, deepcopy(body), params))
        page_id = path.split("/")[4]
        if page_id in self.failures:
            raise self.failures[page_id]
        if path.endswith("/restriction/byOperation"):
            return deepcopy(self.direct)
        if path.endswith("/restriction"):
            for row in body:
                self.direct[row["operation"]] = row
            return {"results": body}
        if path.endswith("/child/page"):
            return {"results": [p for p in self.pages.values() if p["ancestors"] and p["ancestors"][-1]["id"] == page_id]}
        page = self.pages[page_id]
        if method == "DELETE":
            page["status"] = "trashed"
            return None
        if method == "PUT":
            page.update(deepcopy(body))
        return deepcopy(page)

    def execute(self, *, kind, mutation_id, payload, write, affected_ids=(), **kwargs):
        key = (kind, mutation_id)
        if key in self.receipts:
            saved, result = self.receipts[key]
            if payload != saved:
                raise ValueError("mutation ID payload changed")
            return deepcopy(result)
        result = write()
        self.receipts[key] = (deepcopy(payload), deepcopy(result))
        return result


def test_move_preserves_body_identity_and_retry_reuses_success():
    commands = Commands()
    organization = ConfluenceOrganization(commands)
    result = organization.move("3", "2", 1, "move:0001", 2)
    assert result["verified"] and result["id"] == "3" and result["previousParentId"] == "1"
    write = next(row for row in commands.calls if row[0] == "PUT")
    assert "body" not in write[2] and write[2]["ancestors"] == [{"id": "2"}]
    assert write[3]["conflictPolicy"] == "abort"
    before = len(commands.calls)
    assert organization.move("3", "2", 1, "move:0001", 2) == result
    assert len(commands.calls) == before


@pytest.mark.parametrize("source,target,version", [("1", "3", 1), ("1", "1", 1), ("3", "4", 1), ("3", "2", 2)])
def test_invalid_move_never_writes(source, target, version):
    commands = Commands()
    with pytest.raises(CommandError):
        ConfluenceOrganization(commands).move(source, target, version, "move:reject")
    assert not commands.calls


def test_leaf_trash_restore_and_children_rejection():
    commands = Commands()
    organization = ConfluenceOrganization(commands)
    with pytest.raises(CommandError, match="하위 문서"):
        organization.trash("1", 1, "trash:parent")
    assert not any(row[0] == "DELETE" for row in commands.calls)
    assert organization.trash("3", 1, "trash:leaf")["permanent"] is False
    assert organization.restore("3", 1, "restore:leaf")["status"] == "current"
    assert not any((row[3] or {}).get("status") == "trashed" for row in commands.calls if row[0] == "DELETE")


def change(key, page_id, **kwargs):
    return OrganizationChange(operation_id=key, action="move", page_id=page_id,
                              expected_version=1, parent_id="2", **kwargs).model_dump()


def test_bulk_dry_run_is_read_only_then_partial_retry_reuses_success():
    commands = Commands()
    organization = ConfluenceOrganization(commands)
    operations = [change("one", "3"), change("two", "1", depends_on=["one"])]
    assert organization.bulk(operations, "bulk:0001")["ok"]
    assert not commands.calls and not commands.receipts
    commands.failures["1"] = UpstreamError(403, "/rest/api/content/1")
    first = organization.bulk(operations, "bulk:0001", dry_run=False)
    assert first["partial"] and first["results"][0]["status"] == "completed"
    assert first["results"][1]["status"] == "failed" and first["failed"][0]["upstreamStatus"] == 403
    del commands.failures["1"]
    second = organization.bulk(operations, "bulk:0001", dry_run=False)
    assert second["ok"]
    assert sum(row[:2] == ("PUT", "/rest/api/content/3") for row in commands.calls) == 1
    with pytest.raises(ValueError, match="payload"):
        organization.bulk(operations[:1], "bulk:0001", dry_run=False)


def test_bulk_unknown_skips_dependents_but_preserves_independent_success():
    commands = Commands()
    commands.failures["3"] = MutationOutcomeUnknown("unknown:1", "unknown")
    operations = [change("one", "3"), change("two", "1", depends_on=["one"]),
                  OrganizationChange(operation_id="restore", action="restore", page_id="5", expected_version=1).model_dump()]
    result = ConfluenceOrganization(commands).bulk(operations, "bulk:unknown", False)
    assert [row["status"] for row in result["results"]] == ["outcome_unknown", "skipped", "completed"]
    assert result["partial"] and not result["ok"]


def test_restriction_fingerprint_and_explicit_empty_operation():
    commands = Commands()
    organization = ConfluenceOrganization(commands)
    state = organization.restrictions("1")
    assert state["complete"] and not state["inheritedRestrictionsIncluded"]
    change_rows = [{"operation": "read", "users": [], "groups": []}]
    with pytest.raises(CommandError, match="제한"):
        organization.set_restrictions("1", change_rows, "0" * 64, "restriction:bad")
    assert not any(row[0] == "PUT" for row in commands.calls)
    result = organization.set_restrictions("1", change_rows, state["fingerprint"], "restriction:ok")
    assert result["concurrencyProtection"] == "preflight_only"
    assert commands.direct["read"]["restrictions"] == {"user": [], "group": []}


def test_incomplete_restrictions_never_produce_writable_fingerprint():
    commands = Commands()
    commands.direct["read"]["restrictions"]["user"]["_links"] = {"next": "/more"}
    result = ConfluenceOrganization(commands).restrictions("1")
    assert not result["complete"] and result["fingerprint"] is None


def test_dry_run_projects_versions_and_rejects_cycle_via_unmoved_descendant():
    commands = Commands()
    organization = ConfluenceOrganization(commands)
    update = OrganizationChange(operation_id="rename", action="update", page_id="3", title="Renamed", expected_version=1)
    move = OrganizationChange(operation_id="move", action="move", page_id="3", expected_version=2, parent_id="2", depends_on=["rename"])
    assert organization.bulk([update.model_dump(), move.model_dump()], "dry:versions")["ok"]
    cycle = [change("move-root", "1"), OrganizationChange(operation_id="cycle", action="move",
             page_id="2", parent_id="3", expected_version=1, depends_on=["move-root"]).model_dump()]
    result = organization.bulk(cycle, "dry:cycle")
    assert not result["ok"] and result["results"][1]["status"] == "failed"
    assert not commands.calls


@pytest.mark.parametrize("fields", [{"title": ""}, {"title": "   "}, {"body": '<!DOCTYPE foo><p>body</p>'}])
def test_dry_run_rejects_invalid_update_before_projecting_next_version(fields):
    op = OrganizationChange(operation_id="bad", action="update", page_id="1", expected_version=1, **fields)
    follow = OrganizationChange(operation_id="next", action="move", page_id="1", parent_id="2", expected_version=2, depends_on=["bad"])
    result = ConfluenceOrganization(Commands()).bulk([op.model_dump(), follow.model_dump()], "dry:invalid")
    assert [r["status"] for r in result["results"]] == ["failed", "skipped"]


@pytest.mark.parametrize("data", [{}, {"results": [], "partial": True}, {"results": [], "complete": False}])
def test_incomplete_children_never_authorize_trash(data):
    commands = Commands()
    original = commands.request
    commands.request = lambda method, path, **kwargs: data if path.endswith("/child/page") else original(method, path, **kwargs)
    with pytest.raises(CommandError):
        ConfluenceOrganization(commands).trash("3", 1, "trash:incomplete")
    assert not any(row[0] == "DELETE" for row in commands.calls)


def test_pending_move_verification_is_retried_without_repeating_write():
    commands = Commands()
    original = commands.content
    def fail_after_write(*args, **kwargs):
        if commands.receipts and args[0] == "3":
            raise TimeoutError("verification interrupted")
        return original(*args, **kwargs)
    commands.content = fail_after_write
    organization = ConfluenceOrganization(commands)
    assert organization.move("3", "2", 1, "move:pending")["verificationPending"]
    commands.content = original
    assert organization.move("3", "2", 1, "move:pending")["verified"]
    assert sum(row[0] == "PUT" for row in commands.calls) == 1


@pytest.mark.parametrize("second", [
    {},
    {"results": [], "partial": True},
    {"results": [], "_links": {"next": "/more"}},
    {"results": [{"id": "3"}], "start": 1},
    {"results": [{"id": "6"}], "start": 0},
    {"results": [{"title": "Missing ID"}], "start": 1},
])
def test_projected_leaf_rejects_incomplete_or_repeated_later_pages(second):
    commands = Commands()
    original = commands.request
    starts = []
    def children(method, path, **kwargs):
        if not path.endswith("/child/page"):
            return original(method, path, **kwargs)
        start = kwargs["params"]["start"]
        starts.append(start)
        return {"results": [{"id": "3"}], "start": 0, "_links": {"next": "/more"}} if start == 0 else second
    commands.request = children
    projected = {"3": {**commands.pages["3"], "ancestors": [{"id": "2"}]}}
    with pytest.raises(CommandError) as failed:
        ConfluenceOrganization(commands)._check_leaf("1", projected)
    assert failed.value.status == 503
    assert starts == [0, 1]
    assert not commands.calls


@pytest.mark.parametrize("status,code", [(401, "authentication_required"), (403, "permission_denied"),
                                       (409, "conflict"), (503, "upstream_unavailable")])
def test_confluence_errors_do_not_touch_jira_authentication(status, code):
    runtime = object.__new__(LocalMcpRuntime)
    runtime.classify_error = lambda exc: pytest.fail("must not classify as Jira")
    error = UpstreamError(status, "https://confluence.test/rest/api/content/1")
    error.service = "confluence"
    result = runtime._error(error)
    assert result["service"] == "confluence" and result["code"] == code
