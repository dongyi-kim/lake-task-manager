from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.agent import approval


ACTOR_A = {"id": "alice", "actorAliases": ["alice", "acct-a"]}
ACTOR_B = {"id": "bob", "actorAliases": ["bob", "acct-b"]}


@pytest.fixture(autouse=True)
def clear_approvals():
    approval.clear()
    yield
    approval.clear()


def test_bound_capability_is_revoked_when_fresh_actor_changes(monkeypatch):
    payload = {"key": "DL-1", "body": "approved text"}
    token = approval.stage("actor-thread", "add_ticket_comment", payload, actor=ACTOR_A)
    assert approval.approve(token, "actor-thread")
    monkeypatch.setattr(approval, "current_actor_fresh", lambda: approval._normalized_actor(ACTOR_B))

    ok, error = approval.consume(token, "add_ticket_comment", payload)

    assert ok is False
    assert "현재 사용자가 달라" in error
    assert approval.peek(token) is None


def test_display_name_collision_does_not_equate_distinct_jira_actors(monkeypatch):
    first = {
        "id": "acct-a", "name": "Kim", "actorAliases": ["acct-a", "alice"],
    }
    second = {
        "id": "acct-b", "name": "Kim", "actorAliases": ["acct-b", "bob"],
    }
    payload = {"key": "DL-1", "body": "approved text"}
    token = approval.stage("actor-thread", "add_ticket_comment", payload, actor=first)
    assert approval.approve(token, "actor-thread")
    monkeypatch.setattr(
        approval, "current_actor_fresh", lambda: approval._normalized_actor(second),
    )

    ok, error = approval.consume(token, "add_ticket_comment", payload)

    assert ok is False
    assert "현재 사용자가 달라" in error
    assert approval.peek(token) is None


def test_actor_is_rechecked_after_resume_before_write_boundary(monkeypatch):
    payload = {"key": "DL-2", "body": "approved text"}
    token = approval.stage("actor-thread", "add_ticket_comment", payload, actor=ACTOR_A)
    actors = iter((
        approval._normalized_actor(ACTOR_A),
        approval._normalized_actor(ACTOR_B),
    ))
    monkeypatch.setattr(approval, "current_actor_fresh", lambda: next(actors))

    assert approval.verify_actor(token, "actor-thread") == (True, "")
    assert approval.approve(token, "actor-thread")
    ok, error = approval.consume(token, "add_ticket_comment", payload)

    assert ok is False
    assert "현재 사용자가 달라" in error
    assert approval.peek(token) is None


def test_sealed_actor_blocks_remaining_bulk_items_after_session_switch(monkeypatch):
    from app.jira.jira_client import JiraClient

    payload = {
        "items": [
            {"key": "DL-21", "body": "one"},
            {"key": "DL-22", "body": "two"},
            {"key": "DL-23", "body": "three"},
        ],
    }
    token = approval.stage(
        "bulk-actor-thread", "add_ticket_comments", payload, actor=ACTOR_A,
    )
    assert approval.approve(token, "bulk-actor-thread")
    monkeypatch.setattr(
        approval, "current_actor_fresh", lambda: approval._normalized_actor(ACTOR_A),
    )

    class SwitchingClient:
        def __init__(self):
            self.identity_checks = 0
            self.writes = []

        @staticmethod
        def _mutation_batch_scope():
            return nullcontext()

        def assert_mutation_actor_identity(self, expected):
            assert set(expected["aliases"]) == {"acct-a", "alice"}
            self.identity_checks += 1
            if self.identity_checks > 1:
                raise ValueError("Jira session switched from alice to bob")

        def add_comment(self, key, body, mutation_id=None, before_write=None):
            assert before_write is not None
            before_write()
            self.writes.append((key, body))

    jira = SwitchingClient()
    nonce, context_token = approval.begin_consumption_attempt(token)
    try:
        assert approval.consume(token, "add_ticket_comments", payload)[0]
        guard = approval.bound_actor_write_guard(jira)
        result = JiraClient.bulk_comment(
            jira, payload["items"], before_write=guard,
        )
    finally:
        approval.end_consumption_attempt(context_token)

    attestation = approval.take_consumption(
        token, attempt_nonce=nonce, thread_id="bulk-actor-thread",
        action="add_ticket_comments", payload=payload,
    )
    assert attestation["actor"] == {
        "id": "alice", "aliases": ["acct-a", "alice"],
    }
    assert jira.writes == [("DL-21", "one")]
    assert jira.identity_checks == 2
    assert [row["index"] for row in result["failed"]] == [1, 2]


def test_write_tool_never_calls_jira_after_actor_mismatch(monkeypatch):
    from app.agent.tools import _ctx
    from app.agent.tools.write_tools import add_ticket_comment

    calls = []
    fake = SimpleNamespace(add_comment=lambda *args: calls.append(args))
    _ctx.bind(fake, SimpleNamespace(jira_env="mock"))
    try:
        payload = {"key": "DL-3", "body": "approved text"}
        token = approval.stage(
            "actor-thread", "add_ticket_comment", payload, actor=ACTOR_A,
        )
        assert approval.approve(token, "actor-thread")
        monkeypatch.setattr(
            approval, "current_actor_fresh", lambda: approval._normalized_actor(ACTOR_B),
        )

        result = add_ticket_comment.invoke({
            "key": "DL-3", "body": "approved text", "approval_token": token,
        })
    finally:
        _ctx.bind()

    assert result["ok"] is False
    assert calls == []
    assert approval.peek(token) is None


def test_session_resume_rejects_wrong_actor_before_approval_or_graph_write(monkeypatch):
    from app.agent.maxis import backend
    from app.agent.workflow import session

    payload = {"key": "DL-4", "body": "approved text"}
    token = approval.stage("actor-thread", "add_ticket_comment", payload, actor=ACTOR_A)
    graph = SimpleNamespace(
        get_state=lambda _config: SimpleNamespace(values={}),
        invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("graph execution must not start")
        ),
    )
    monkeypatch.setattr(session, "get_graph", lambda: graph)
    monkeypatch.setattr(backend, "verify_thread_runtime", lambda _state: None)
    monkeypatch.setattr(
        approval, "current_actor_fresh", lambda: approval._normalized_actor(ACTOR_B),
    )

    result = session.resume("actor-thread", token)

    assert result["ok"] is False
    assert "현재 사용자가 달라" in result["error"]
    assert approval.peek(token) is None


def test_graph_mints_capability_with_fresh_actor_binding(monkeypatch):
    from app.agent.workflow import graph

    monkeypatch.setattr(
        approval, "current_actor_fresh", lambda: approval._normalized_actor(ACTOR_A),
    )
    token = graph._propose({
        "thread_id": "actor-stage",
        "change_plan": {"key": "DL-5", "changes": {"duedate": "2026-09-30"}},
    })["approval_token"]

    record = approval.peek(token)
    assert record is not None
    assert set(record["actor"]["aliases"]) == {"acct-a", "alice"}


def test_jira_mutation_actor_bypasses_cached_identity(monkeypatch):
    from app.jira.identity_service import JiraIdentityMixin

    identity = JiraIdentityMixin()
    monkeypatch.setattr(
        identity, "current_user",
        lambda: (_ for _ in ()).throw(AssertionError("cached identity must not be used")),
    )
    monkeypatch.setattr(identity, "current_user_fresh", lambda: {
        "id": "alice", "actorAliases": ["alice", "acct-a"],
    })

    assert identity.mutation_actor_identity() == {
        "id": "alice", "aliases": ["acct-a", "alice"],
    }
