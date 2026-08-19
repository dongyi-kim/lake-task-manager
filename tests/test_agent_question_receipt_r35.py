"""Server-owned question-answer receipt authority and RequestArchitect bypass."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from langchain_core.messages import HumanMessage
import pytest

from app.agent.workflow.question_receipt import (
    claim_question_receipt,
    commit_question_receipt,
    issue_question_challenge,
    release_question_receipt,
    reset_question_receipts_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_receipts():
    reset_question_receipts_for_tests()


def _question(field="duedate", *, question="마감일을 알려 주세요.", kind="date"):
    return {
        "contract": "question.v1",
        "question": question,
        "kind": kind,
        "options": [],
        "field": field,
        "ownership": "user_required",
        "required_input": True,
        "why_required": "실행 가능한 값을 확정해야 함",
        "fallback": "",
    }


def _binding(*, checkpoint="cp-1", plan=None, continuation=None):
    return {
        "thread_id": "thread-1",
        "checkpoint_revision": checkpoint,
        "request_plan": plan or {"goal": "g", "tasks": []},
        "continuation_contract": continuation or {
            "version": "continuation.v1",
            "root_request": "작업을 만들어줘",
            "intent": "plan_work",
            "action": "create",
            "target_keys": [],
            "outcome_ids": ["task-1"],
            "decisions": [],
        },
    }


def _receipt(challenge, values):
    return {
        "contract": "question_answer.receipt.v1",
        "challenge_id": challenge["challenge_id"],
        "answers": [
            {"question_id": row["question_id"], "value": value}
            for row, value in zip(challenge["questions"], values)
        ],
    }


def test_strict_questions_get_stable_server_ids_but_loose_questions_do_not():
    binding = _binding()
    questions = [
        _question(question="같은 문구"),
        _question(field="date", question="같은 문구"),
    ]
    first = issue_question_challenge(questions=questions, **binding)
    again = issue_question_challenge(questions=questions, **binding)

    assert first == again
    assert first["contract"] == "question-answer-challenge.v1"
    assert first["questions"][0]["question_id"] != first["questions"][1]["question_id"]
    assert issue_question_challenge(
        questions=[{"question": "loose", "required_input": True}], **binding,
    ) is None


def test_receipt_is_bound_to_thread_checkpoint_plan_and_continuation():
    binding = _binding()
    challenge = issue_question_challenge(questions=[_question()], **binding)
    raw = _receipt(challenge, ["2026-09-01"])

    for changed in (
        {**binding, "thread_id": "other"},
        {**binding, "checkpoint_revision": "cp-2"},
        {**binding, "request_plan": {"goal": "changed", "tasks": []}},
        {**binding, "continuation_contract": {
            **binding["continuation_contract"], "outcome_ids": ["other"],
        }},
    ):
        result = claim_question_receipt(raw, **changed)
        assert result.status == "rejected"
        assert result.projection == {}


def test_exact_required_set_and_authoritative_answer_types_are_enforced():
    binding = _binding()
    questions = [
        _question("duedate", question="날짜", kind="date"),
        {**_question("phase", question="단계", kind="choice"),
         "options": ["1차", "2차"]},
    ]
    challenge = issue_question_challenge(questions=questions, **binding)
    complete = _receipt(challenge, ["2026-09-01", "1차"])

    missing = {**complete, "answers": complete["answers"][:1]}
    duplicate = {**complete, "answers": [complete["answers"][0], complete["answers"][0]]}
    unknown = {**complete, "answers": [
        complete["answers"][0], {"question_id": "f" * 64, "value": "1차"},
    ]}
    invalid_choice = _receipt(challenge, ["2026-09-01", "3차"])
    invalid_date = _receipt(challenge, ["2026-9-1", "1차"])

    result = claim_question_receipt(missing, **binding)
    assert result.status == "semantic" and result.owns_lease
    assert result.remaining == (challenge["questions"][1]["question_id"],)
    release_question_receipt(result)

    for raw in (duplicate, unknown):
        result = claim_question_receipt(raw, **binding)
        assert result.status == "rejected"
        assert result.projection == {}

    for raw in (invalid_choice, invalid_date):
        result = claim_question_receipt(raw, **binding)
        assert result.status == "semantic" and result.owns_lease
        assert result.projection == {}
        release_question_receipt(result)

    result = claim_question_receipt(complete, **binding)
    assert result.status == "fast"
    assert result.projection["request_refinement"] == {
        "duedate": "2026-09-01", "phase": "1차",
    }
    release_question_receipt(result)


def test_strict_but_non_projectable_target_receipt_uses_semantic_path():
    binding = _binding()
    challenge = issue_question_challenge(
        questions=[_question("target", kind="text")], **binding,
    )
    result = claim_question_receipt(
        _receipt(challenge, ["orders_daily 테이블"]), **binding,
    )

    assert result.status == "semantic"
    assert result.projection == {}
    assert "orders_daily" in result.message_text
    assert challenge["challenge_id"] not in result.message_text
    release_question_receipt(result)


@pytest.mark.parametrize("field", ["due", "date", "deadline"])
def test_due_alias_receipts_remain_semantic(field):
    binding = _binding()
    challenge = issue_question_challenge(
        questions=[_question(field, kind="date")], **binding,
    )
    result = claim_question_receipt(
        _receipt(challenge, ["2026-09-01"]), **binding,
    )

    assert result.status == "semantic"
    assert result.projection == {}
    assert result.saved_calls == 0
    release_question_receipt(result)


def test_atomic_one_use_claim_releases_on_failure_and_commits_on_success():
    binding = _binding()
    challenge = issue_question_challenge(questions=[_question()], **binding)
    raw = _receipt(challenge, ["2026-09-01"])
    barrier = threading.Barrier(3)
    results = []

    def claim():
        barrier.wait()
        results.append(claim_question_receipt(raw, **binding))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(row.status for row in results) == ["fast", "rejected"]
    owner = next(row for row in results if row.status == "fast")
    release_question_receipt(owner)
    retry = claim_question_receipt(raw, **binding)
    assert retry.status == "fast"
    assert commit_question_receipt(retry) is True
    assert claim_question_receipt(raw, **binding).status == "rejected"


def test_projection_and_human_message_never_contain_challenge_capability():
    binding = _binding()
    challenge = issue_question_challenge(questions=[_question()], **binding)
    result = claim_question_receipt(
        _receipt(challenge, ["2026-09-01"]), **binding,
    )

    visible = json.dumps({
        "messages": [HumanMessage(content=result.message_text).model_dump()],
        "question_receipt_projection": result.projection,
    }, ensure_ascii=False, default=str)
    assert challenge["challenge_id"] not in visible
    assert "마감일을 알려 주세요" not in visible
    release_question_receipt(result)


def test_request_architect_fast_path_uses_only_typed_projection(monkeypatch):
    from app.agent.workflow.agents import base
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.question_receipt import digest_value

    original = "기존 초안의 마감일을 정해줘"
    plan = {
        "goal": original,
        "tasks": [{
            "id": "task-1", "kind": "ticket", "instruction": original,
            "depends_on": [], "write_intent": True, "completion_criteria": [],
        }],
        "request_questions": [], "blocking_questions": [], "assumptions": [],
    }
    continuation = {
        "version": "continuation.v1", "root_request": original,
        "intent": "plan_work", "action": "create", "target_keys": [],
        "outcome_ids": ["task-1"],
        "decisions": [{"field": "duedate", "value": "2026-09-01",
                       "source": "interview_answer"}],
    }
    projection = {
        "contract": "question-answer-projection.v1",
        "authority": "session.question-answer-receipt.v1",
        "checkpoint_digest": "a" * 64,
        "request_plan_digest": digest_value(plan),
        "continuation_digest": digest_value(continuation),
        "answered": [{"question_id": "b" * 64, "field": "duedate",
                      "value": "2026-09-01"}],
        "remaining": [],
        "complete": True,
        "request_refinement": {"duedate": "2026-09-01"},
    }

    def forbidden(*_args, **_kwargs):
        raise AssertionError("semantic RequestArchitect was called")

    monkeypatch.setattr(base.StructuredAgent, "_run", forbidden)
    got = RequestArchitect()._run({
        "messages": [HumanMessage(content="duedate: 2026-09-01")],
        "turn_continuation": True,
        "intent": "plan_work",
        "request_text": original,
        "request_plan": plan,
        "continuation_contract": continuation,
        "question_receipt_projection": projection,
        "draft": {"mode": "task", "items": [{"summary": "초안"}]},
        "trace": [],
    })

    assert got["request_plan"] == plan
    assert got["request_refinement"] == {"duedate": "2026-09-01"}
    assert got["trace"][0]["fastPath"]["savedCalls"] == 1


def test_mixed_typed_and_loose_question_set_mints_no_challenge():
    assert issue_question_challenge(
        questions=[_question(), {"question": "loose", "required_input": True}],
        **_binding(),
    ) is None


@pytest.mark.parametrize("patch", [
    {"field": "client-forged"},
    {"question": "client-forged"},
    {"kind": "text"},
    {"required_input": True},
])
def test_answer_rows_forbid_client_authored_authority(patch):
    from app.agent.workflow.contracts import QuestionAnswerReceipt

    row = {"question_id": "a" * 64, "value": "answer", **patch}
    with pytest.raises(Exception):
        QuestionAnswerReceipt.model_validate({
            "contract": "question_answer.receipt.v1",
            "challenge_id": "A" * 32, "answers": [row],
        }, strict=True)


@pytest.mark.parametrize("value", [
    "", " x", "x ", "x\n", "x\x00", True, 3, ["a", "a"], [], ["x"] * 6,
    "x" * 1001,
])
def test_answer_wire_rejects_blank_control_coercion_duplicate_and_oversize(value):
    from app.agent.workflow.contracts import QuestionAnswerReceipt

    with pytest.raises(Exception):
        QuestionAnswerReceipt.model_validate({
            "contract": "question_answer.receipt.v1",
            "challenge_id": "A" * 32,
            "answers": [{"question_id": "a" * 64, "value": value}],
        }, strict=True)


def test_partial_authentic_receipt_has_one_atomic_semantic_owner():
    binding = _binding()
    questions = [_question(), _question("phase", question="단계", kind="text")]
    challenge = issue_question_challenge(questions=questions, **binding)
    raw = _receipt(challenge, ["2026-09-01"])
    barrier = threading.Barrier(3)
    results = []

    def claim():
        barrier.wait()
        results.append(claim_question_receipt(raw, **binding))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(row.status for row in results) == ["rejected", "semantic"]
    owner = next(row for row in results if row.status == "semantic")
    assert owner.owns_lease and "2026-09-01" in owner.message_text
    assert owner.saved_calls == 0 and len(owner.remaining) == 1
    release_question_receipt(owner)


def test_expired_and_noncanonical_parent_answers_never_gain_fast_authority():
    binding = _binding()
    expired = issue_question_challenge(
        questions=[_question()], now=100, ttl_seconds=10, **binding,
    )
    assert claim_question_receipt(
        _receipt(expired, ["2026-09-01"]), now=111, **binding,
    ).status == "rejected"

    reset_question_receipts_for_tests()
    for value in ("DL-100", "top_level", "select_existing", "최상위 Task로"):
        challenge = issue_question_challenge(
            questions=[_question("parent_resolution", kind="text")], **binding,
        )
        result = claim_question_receipt(_receipt(challenge, [value]), **binding)
        assert result.status == "semantic" and result.saved_calls == 0
        release_question_receipt(result)
        reset_question_receipts_for_tests()


def test_subtask_top_level_and_new_epic_parent_receipts_stay_semantic():
    from app.agent.workflow.agents.request_architect import _question_receipt_fast_patch
    from app.agent.workflow.question_receipt import digest_value

    binding = _binding()
    binding["request_plan"]["goal"] = "Sub-Task를 만든다"
    binding["request_plan"]["tasks"] = [{
        "id": "task-1", "kind": "ticket", "instruction": "Sub-Task를 만든다",
        "depends_on": [], "write_intent": True, "completion_criteria": [],
    }]
    challenge = issue_question_challenge(
        questions=[_question("parent_resolution", kind="text")], **binding,
    )
    top_level = claim_question_receipt(
        _receipt(challenge, ["top_level"]), **binding,
    )
    assert top_level.status == "semantic" and top_level.saved_calls == 0
    release_question_receipt(top_level)

    subtask_plan = binding["request_plan"]
    subtask_continuation = {
        **binding["continuation_contract"], "root_request": "Sub-Task를 만든다",
        "decisions": [{"field": "parent", "value": "DL-100",
                       "source": "interview_answer"}],
    }
    subtask_projection = {
        "contract": "question-answer-projection.v1",
        "authority": "session.question-answer-receipt.v1",
        "checkpoint_digest": "a" * 64,
        "request_plan_digest": digest_value(subtask_plan),
        "continuation_digest": digest_value(subtask_continuation),
        "answered": [{"question_id": "c" * 64, "field": "parent",
                      "value": "DL-100"}],
        "remaining": [], "complete": True,
        "request_refinement": {"parent": "DL-100"},
    }
    assert _question_receipt_fast_patch({
        "turn_continuation": True, "intent": "plan_work",
        "request_text": "Sub-Task를 만든다", "request_plan": subtask_plan,
        "continuation_contract": subtask_continuation,
        "question_receipt_projection": subtask_projection,
        # Deliberately drifted legacy mode: the typed request plan remains authoritative.
        "draft": {"mode": "task", "items": [{"type": "Sub-Task", "summary": "child"}]},
        "materialized_ticket_sources": {
            "parentCandidateKeys": ["DL-100"],
            "ticketDetails": [{"key": "DL-100", "type": "Epic"}],
        },
    }) == {}

    plan = {**binding["request_plan"], "goal": "새 Epic을 생성한다"}
    plan["tasks"] = [{**plan["tasks"][0], "instruction": "새 Epic을 생성한다"}]
    continuation = {
        **binding["continuation_contract"], "root_request": "새 Epic을 생성한다",
        "decisions": [{"field": "parent", "value": "DL-100",
                       "source": "interview_answer"}],
    }
    projection = {
        "contract": "question-answer-projection.v1",
        "authority": "session.question-answer-receipt.v1",
        "checkpoint_digest": "a" * 64,
        "request_plan_digest": digest_value(plan),
        "continuation_digest": digest_value(continuation),
        "answered": [{"question_id": "b" * 64, "field": "parent",
                      "value": "DL-100"}],
        "remaining": [], "complete": True,
        "request_refinement": {"parent": "DL-100"},
    }
    assert _question_receipt_fast_patch({
        "turn_continuation": True, "intent": "plan_work",
        # Even if legacy request_text drifted, the authoritative plan still says new Epic.
        "request_text": "작업을 만든다", "request_plan": plan,
        "continuation_contract": continuation,
        "question_receipt_projection": projection,
        "draft": {"mode": "epic", "items": [{"summary": "새 Epic"}]},
        "materialized_ticket_sources": {
            "parentCandidateKeys": ["DL-100"],
            "ticketDetails": [{"key": "DL-100", "type": "Epic"}],
        },
    }) == {}


def _prior_state(question=None):
    plan = {
        "goal": "초안을 만든다",
        "tasks": [{
            "id": "task-1", "kind": "ticket", "instruction": "초안을 만든다",
            "depends_on": [], "write_intent": True, "completion_criteria": [],
        }],
        "request_questions": [], "blocking_questions": [], "assumptions": [],
    }
    continuation = {
        "version": "continuation.v1", "root_request": "초안을 만든다",
        "intent": "plan_work", "action": "create", "target_keys": [],
        "outcome_ids": ["task-1"], "decisions": [],
    }
    return {
        "intent": "plan_work", "request_text": "초안을 만든다",
        "request_plan": plan, "continuation_contract": continuation,
        "questions": [question or _question()],
        "draft": {"mode": "task", "items": [{"summary": "초안"}]},
    }


class _FakeGraph:
    def __init__(self, values, *, fail=False, advance=False):
        self.values = values
        self.revision = "cp-1"
        self.fail = fail
        self.advance = advance
        self.inputs = []

    def get_state(self, _config):
        return SimpleNamespace(
            values=self.values,
            config={"configurable": {"checkpoint_id": self.revision}},
            next=(),
        )

    def invoke(self, initial, _config):
        self.inputs.append(initial)
        if self.advance:
            self.revision = "cp-2"
        if self.fail:
            raise RuntimeError("graph failed")
        self.revision = "cp-2"
        self.values = {**initial, "reply": "ok", "questions": []}
        return self.values

    def stream(self, initial, _config, **_kwargs):
        self.inputs.append(initial)
        if self.advance:
            self.revision = "cp-2"
        yield ("", "updates", {})


def _patch_session_runtime(monkeypatch, graph):
    from app.agent.workflow import session

    monkeypatch.setattr(session, "get_graph", lambda: graph)
    monkeypatch.setattr(session, "_config", lambda thread_id, meter=None: {
        "configurable": {"thread_id": thread_id},
    })
    monkeypatch.setattr(session, "_initial", lambda tid, text, role, user: {
        "messages": [HumanMessage(content=text)], "thread_id": tid,
        "questions": [], "question_receipt_projection": {},
    })
    monkeypatch.setattr(session, "_shape", lambda tid, state, snap=None: {
        "thread_id": tid, "ok": True, "reply": state.get("reply", "ok"),
        "trace": [], "error": "",
    })
    return session


def test_prepare_turn_keeps_authentic_semantic_receipt_on_frozen_plan(monkeypatch):
    from app.agent.workflow import session

    question = _question("target", kind="text")
    prior = _prior_state(question)
    graph = _FakeGraph(prior)
    monkeypatch.setattr(session, "_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(session, "_initial", lambda tid, text, role, user: {
        "messages": [HumanMessage(content=text)], "thread_id": tid,
        "questions": [], "question_receipt_projection": {},
    })
    challenge = issue_question_challenge(
        thread_id="thread-1", checkpoint_revision="cp-1",
        request_plan=prior["request_plan"],
        continuation_contract=prior["continuation_contract"],
        questions=[question],
    )
    prepared = session._prepare_turn(
        graph, thread_id="thread-1", text="", user_role="", user_id="",
        question_receipt=_receipt(challenge, ["orders_daily"]),
    )

    assert prepared.claim.status == "semantic" and prepared.claim.owns_lease
    assert prepared.initial["turn_continuation"] is True
    assert prepared.initial["request_plan"] == prior["request_plan"]
    assert prepared.initial["question_receipt_projection"] == {}
    assert prepared.initial["continuation_contract"]["decisions"][-1] == {
        "field": "target", "value": "orders_daily", "source": "interview_answer",
    }
    release_question_receipt(prepared.claim)


def test_prepare_turn_fast_projection_has_no_capability_or_question_text(monkeypatch):
    from app.agent.workflow import session

    prior = _prior_state()
    graph = _FakeGraph(prior)
    monkeypatch.setattr(session, "_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(session, "_initial", lambda tid, text, role, user: {
        "messages": [HumanMessage(content=text)], "thread_id": tid,
        "questions": [], "question_receipt_projection": {},
    })
    challenge = issue_question_challenge(
        thread_id="thread-1", checkpoint_revision="cp-1",
        request_plan=prior["request_plan"],
        continuation_contract=prior["continuation_contract"],
        questions=prior["questions"],
    )
    prepared = session._prepare_turn(
        graph, thread_id="thread-1", text="", user_role="", user_id="",
        question_receipt=_receipt(challenge, ["2026-09-01"]),
    )

    wire = json.dumps(prepared.initial, ensure_ascii=False, default=str)
    message = prepared.initial["messages"][-1]
    assert prepared.claim.status == "fast"
    assert challenge["challenge_id"] not in wire
    assert prior["questions"][0]["question"] not in wire
    assert message.additional_kwargs == {}
    assert prepared.initial["question_receipt_projection"]
    release_question_receipt(prepared.claim)


def test_session_success_commits_and_failure_releases_receipt(monkeypatch):
    prior = _prior_state()
    binding = {
        "thread_id": "thread-1", "checkpoint_revision": "cp-1",
        "request_plan": prior["request_plan"],
        "continuation_contract": prior["continuation_contract"],
    }
    challenge = issue_question_challenge(questions=prior["questions"], **binding)
    raw = _receipt(challenge, ["2026-09-01"])
    graph = _FakeGraph(prior)
    session = _patch_session_runtime(monkeypatch, graph)
    assert session.ask("", "thread-1", question_receipt=raw)["ok"] is True
    graph.revision, graph.values = "cp-1", prior
    assert claim_question_receipt(raw, **binding).status == "rejected"

    reset_question_receipts_for_tests()
    challenge = issue_question_challenge(questions=prior["questions"], **binding)
    raw = _receipt(challenge, ["2026-09-01"])
    graph = _FakeGraph(prior, fail=True)
    session = _patch_session_runtime(monkeypatch, graph)
    with pytest.raises(RuntimeError):
        session.ask("", "thread-1", question_receipt=raw)
    retry = claim_question_receipt(raw, **binding)
    assert retry.status == "fast"
    release_question_receipt(retry)


@pytest.mark.parametrize("advance,expected", [(False, "fast"), (True, "rejected")])
def test_stream_disconnect_releases_only_an_unchanged_checkpoint(
        monkeypatch, advance, expected):
    prior = _prior_state()
    binding = {
        "thread_id": "thread-1", "checkpoint_revision": "cp-1",
        "request_plan": prior["request_plan"],
        "continuation_contract": prior["continuation_contract"],
    }
    challenge = issue_question_challenge(questions=prior["questions"], **binding)
    raw = _receipt(challenge, ["2026-09-01"])
    graph = _FakeGraph(prior, advance=advance)
    session = _patch_session_runtime(monkeypatch, graph)
    monkeypatch.setattr(session, "_events", lambda _ns, _payload: iter([
        {"type": "node", "node": "request_architect"},
    ]))

    events = session.stream("", "thread-1", question_receipt=raw)
    assert next(events)["type"] == "start"
    assert next(events)["type"] == "node"
    events.close()
    graph.revision, graph.values = "cp-1", prior
    retried = claim_question_receipt(raw, **binding)
    assert retried.status == expected
    if retried.owns_lease:
        release_question_receipt(retried)


def test_shape_issues_only_public_bounded_challenge_and_resets_projection(monkeypatch):
    from app.agent.workflow import session

    prior = _prior_state()
    snap = SimpleNamespace(
        values=prior, config={"configurable": {"checkpoint_id": "cp-1"}}, next=(),
    )
    monkeypatch.setattr(session, "_people_names", lambda _out: {})
    out = session._shape("thread-1", prior, snap)

    assert out["questionReceipt"]["contract"] == "question-answer-challenge.v1"
    assert out["questions"][0]["question_id"]
    assert "question_receipt_projection" not in out
    assert session._turn_start_patch("새 요청", {})["question_receipt_projection"] == {}


def test_only_request_architect_role_declares_projection_input():
    from app.agent.workflow.role_manifest import ROLE_SPECS

    owners = [role_id for role_id, spec in ROLE_SPECS.items()
              if "question_receipt_projection" in spec.input_keys]
    assert owners == ["request_architect"]


def test_route_rejects_mixed_authority_and_accepts_receipt_only(monkeypatch):
    from app.agent import routes
    from app.agent.workflow import session

    raw = {
        "contract": "question_answer.receipt.v1", "challenge_id": "A" * 32,
        "answers": [{"question_id": "a" * 64, "value": "answer"}],
    }
    mixed = routes.api_chat(routes._ChatBody(text="new", questionReceipt=raw))
    assert mixed.status_code == 400

    captured = {}
    monkeypatch.setattr(session, "ask", lambda text, *args, **kwargs: (
        captured.update(text=text, **kwargs) or {"ok": True}
    ))
    accepted = routes.api_chat(routes._ChatBody(text="", questionReceipt=raw))
    assert accepted.status_code == 200
    assert captured["text"] == "" and captured["question_receipt"] is not None


def test_frontend_submits_only_question_identity_and_answer():
    from pathlib import Path

    source = Path("app/static/components/views/AgentView.js").read_text(encoding="utf-8")
    assert 'contract: "question_answer.receipt.v1"' in source
    assert "question_id: q.question_id" in source
    assert '{ text: questionReceipt ? "" : text' in source
    assert "question: q.question" not in source
    assert "field: q.field" not in source


def test_question_receipt_reuses_common_canonical_digest_exactly():
    from app.agent.workflow.canonical_digest import digest_value as common_digest
    from app.agent.workflow.question_receipt import digest_value as receipt_digest

    assert receipt_digest is common_digest
    assert common_digest({"b": [2, 3], "a": "한글"}) == common_digest(
        {"a": "한글", "b": [2, 3]},
    )
    with pytest.raises(ValueError):
        common_digest({"unsafe": float("nan")})
