"""Server-owned, one-use transport for answers to typed Agent questions.

The browser returns only an opaque question identity and a value.  Question text, field,
kind, requiredness and fast-path eligibility are recovered from the server challenge; none
of those authority-bearing attributes are accepted from the client or placed in messages.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
import secrets
import threading
import time
from typing import Literal, Mapping

from pydantic import TypeAdapter, ValidationError

from app.agent.workflow.contracts import (
    QuestionAnswerChallenge,
    QuestionAnswerReceipt,
    QuestionContract,
    QuestionReceiptProjection,
)
from app.agent.workflow.continuation import merge_continuation_decisions


QUESTION_CHALLENGE_TTL_SECONDS = 10 * 60
QUESTION_CHALLENGE_LIMIT = 2048
_TURN_LOCK_STRIPES = 64
_QUESTION_ADAPTER = TypeAdapter(QuestionContract)
_RECEIPT_ADAPTER = TypeAdapter(QuestionAnswerReceipt)
_PROJECTION_ADAPTER = TypeAdapter(QuestionReceiptProjection)


def digest_value(value) -> str:
    """Canonical JSON SHA-256 used for checkpoint-adjacent typed bindings."""
    wire = json.dumps(
        value if value is not None else {}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def checkpoint_digest(revision: str) -> str:
    return digest_value({"checkpoint_revision": str(revision or "")})


@dataclass
class _Challenge:
    challenge_id: str
    binding_digest: str
    thread_id: str
    checkpoint_revision: str
    checkpoint_digest: str
    request_plan_digest: str
    continuation_digest: str
    questions: tuple[dict, ...]
    question_ids: tuple[str, ...]
    expires_at: float
    status: Literal["pending", "inflight", "consumed"] = "pending"
    lease_id: str = ""

    def public(self) -> dict:
        return QuestionAnswerChallenge(
            challenge_id=self.challenge_id,
            questions=[{"question_id": value} for value in self.question_ids],
            expires_at=int(self.expires_at),
        ).model_dump()


@dataclass(frozen=True)
class ReceiptClaim:
    """Local lifecycle handle.  Only projection/message_text may cross into AgentState."""

    status: Literal["fast", "semantic", "rejected"]
    message_text: str
    projection: dict = field(default_factory=dict)
    continuation_contract: dict = field(default_factory=dict)
    remaining: tuple[str, ...] = ()
    reason: str = ""
    saved_calls: int = 0
    _challenge_id: str = field(default="", repr=False)
    _lease_id: str = field(default="", repr=False)
    _checkpoint_revision: str = field(default="", repr=False)

    @property
    def owns_lease(self) -> bool:
        return bool(self._challenge_id and self._lease_id)


_LOCK = threading.RLock()
_CHALLENGES: dict[str, _Challenge] = {}
_BINDINGS: dict[str, str] = {}
_TURN_LOCKS = tuple(threading.RLock() for _ in range(_TURN_LOCK_STRIPES))


@contextmanager
def question_turn_lock(thread_id: str):
    """Serialize checkpoint reads and graph turns for one conversation only."""
    key = str(thread_id or "")
    stripe = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
    lock = _TURN_LOCKS[stripe % len(_TURN_LOCKS)]
    with lock:
        yield


def _purge(current: float) -> None:
    removable = [
        challenge_id for challenge_id, challenge in _CHALLENGES.items()
        if ((challenge.status != "inflight" and challenge.expires_at <= current)
            or (challenge.status == "inflight"
                and challenge.expires_at + QUESTION_CHALLENGE_TTL_SECONDS <= current))
    ]
    for challenge_id in removable:
        challenge = _CHALLENGES.pop(challenge_id, None)
        if challenge and _BINDINGS.get(challenge.binding_digest) == challenge_id:
            _BINDINGS.pop(challenge.binding_digest, None)


def _strict_questions(questions) -> tuple[dict, ...]:
    rows: list[dict] = []
    raw_rows = questions if isinstance(questions, (list, tuple)) else []
    if not 1 <= len(raw_rows) <= 3:
        return ()
    for raw in raw_rows:
        if not isinstance(raw, dict) or raw.get("contract") != "question.v1":
            return ()
        try:
            model = _QUESTION_ADAPTER.validate_python(raw, strict=True)
        except ValidationError:
            return ()
        required = model.ownership == "user_required"
        if (not model.field or model.required_input is not required
                or required != bool(model.why_required)
                or required == bool(model.fallback)):
            return ()
        if model.kind in {"choice", "multi"}:
            if not model.options or len(model.options) != len(set(model.options)):
                return ()
        elif model.options:
            return ()
        rows.append(model.model_dump())
    return tuple(rows)


def _binding(
    *,
    thread_id: str,
    checkpoint_revision: str,
    request_plan,
    continuation_contract,
    questions: tuple[dict, ...],
) -> tuple[str, str, str, str]:
    cp_digest = checkpoint_digest(checkpoint_revision)
    plan_digest = digest_value(request_plan)
    continuation_digest = digest_value(continuation_contract)
    binding = digest_value({
        "contract": "question-answer-binding.v1",
        "thread_id": str(thread_id or ""),
        "checkpoint_digest": cp_digest,
        "request_plan_digest": plan_digest,
        "continuation_digest": continuation_digest,
        "questions": questions,
    })
    return binding, cp_digest, plan_digest, continuation_digest


def issue_question_challenge(
    *,
    thread_id: str,
    checkpoint_revision: str,
    request_plan,
    continuation_contract,
    questions,
    now: float | None = None,
    ttl_seconds: int = QUESTION_CHALLENGE_TTL_SECONDS,
) -> dict | None:
    """Mint one stable challenge only for an entirely typed active question set."""
    rows = _strict_questions(questions)
    revision = str(checkpoint_revision or "").strip()
    thread = str(thread_id or "").strip()
    if not rows or not revision or not thread or not 1 <= int(ttl_seconds) <= 3600:
        return None
    current = time.time() if now is None else float(now)
    try:
        binding, cp_digest, plan_digest, continuation_digest = _binding(
            thread_id=thread, checkpoint_revision=revision,
            request_plan=request_plan, continuation_contract=continuation_contract,
            questions=rows,
        )
    except (TypeError, ValueError):
        return None
    with _LOCK:
        _purge(current)
        prior_id = _BINDINGS.get(binding, "")
        prior = _CHALLENGES.get(prior_id)
        if prior and prior.expires_at > current:
            return None if prior.status == "consumed" else prior.public()
        if len(_CHALLENGES) >= QUESTION_CHALLENGE_LIMIT:
            return None
        question_ids = tuple(
            digest_value({"binding": binding, "index": index, "question": row})
            for index, row in enumerate(rows)
        )
        challenge = _Challenge(
            challenge_id=secrets.token_urlsafe(32), binding_digest=binding,
            thread_id=thread, checkpoint_revision=revision,
            checkpoint_digest=cp_digest, request_plan_digest=plan_digest,
            continuation_digest=continuation_digest, questions=rows,
            question_ids=question_ids, expires_at=current + int(ttl_seconds),
        )
        _CHALLENGES[challenge.challenge_id] = challenge
        _BINDINGS[binding] = challenge.challenge_id
        return challenge.public()


def _safe_literal(value) -> str:
    values = value if isinstance(value, list) else [value]
    safe: list[str] = []
    for raw in values[:5]:
        if not isinstance(raw, str):
            continue
        cleaned = "".join(char for char in raw if ord(char) >= 32).strip()
        if cleaned:
            safe.append(cleaned[:1000])
    return " | ".join(safe)


def _message(values: list[tuple[str, str]] | list[str]) -> str:
    rendered: list[str] = []
    for row in values[:3]:
        if isinstance(row, tuple):
            field_name, value = row
            rendered.append(f"{field_name}: {value}")
        else:
            rendered.append(str(row))
    text = "\n".join(rendered)[:3200]
    return text or "질문 답변을 제출했습니다."


def _answer_valid(question: Mapping, value) -> bool:
    kind = question.get("kind")
    if kind == "multi":
        return (isinstance(value, list) and bool(value)
                and all(item in question.get("options", []) for item in value))
    if not isinstance(value, str):
        return False
    if kind == "choice":
        return value in question.get("options", [])
    if kind == "date":
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return False
        return parsed.isoformat() == value
    return kind == "text"


def _project(field_name: str, value) -> tuple[str, str] | None:
    field_name = str(field_name or "").casefold()
    if isinstance(value, list):
        return None
    # Parent answers name an entity but do not prove whether it is an Epic membership or a
    # Sub-Task parent relation. Keep them on semantic RequestArchitect until that relation is
    # represented by a server-owned typed contract.
    if field_name in {"parent", "epic", "parent_resolution"}:
        return None
    # Fast authority is tied to the server-owned canonical field name. Generic aliases
    # such as ``date`` can represent a start date, so they remain semantic answers.
    if field_name == "duedate":
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None
        return ("duedate", value) if parsed.isoformat() == value else None
    if field_name == "phase":
        ordinal = value[:-1] if value.endswith("차") else ""
        if (ordinal.isdecimal() and 1 <= int(ordinal) <= 999
                and value == f"{int(ordinal)}차"):
            return "phase", value
    return None


def claim_question_receipt(
    raw,
    *,
    thread_id: str,
    checkpoint_revision: str,
    request_plan,
    continuation_contract,
    now: float | None = None,
) -> ReceiptClaim:
    """Atomically validate and reserve a receipt; malformed/stale input has no authority."""
    try:
        receipt = _RECEIPT_ADAPTER.validate_python(raw, strict=True)
    except ValidationError:
        return ReceiptClaim(
            status="rejected", message_text="", reason="invalid receipt wire shape",
        )
    current = time.time() if now is None else float(now)
    with _LOCK:
        _purge(current)
        challenge = _CHALLENGES.get(receipt.challenge_id)
        if challenge is None:
            return ReceiptClaim(
                status="rejected", message_text="", reason="unknown question challenge",
            )
        if challenge.status != "pending":
            return ReceiptClaim(
                status="rejected", message_text="",
                reason="question receipt was already submitted",
            )
        if challenge.expires_at <= current:
            return ReceiptClaim(
                status="rejected", message_text="", reason="question challenge expired",
            )
        try:
            binding, cp_digest, plan_digest, continuation_digest = _binding(
                thread_id=str(thread_id or ""),
                checkpoint_revision=str(checkpoint_revision or ""),
                request_plan=request_plan, continuation_contract=continuation_contract,
                questions=challenge.questions,
            )
        except (TypeError, ValueError):
            return ReceiptClaim(
                status="rejected", message_text="", reason="invalid current binding",
            )
        if (binding != challenge.binding_digest or cp_digest != challenge.checkpoint_digest
                or plan_digest != challenge.request_plan_digest
                or continuation_digest != challenge.continuation_digest):
            return ReceiptClaim(
                status="rejected", message_text="", reason="stale question challenge",
            )
        supplied = [row.question_id for row in receipt.answers]
        supplied_set = set(supplied)
        known_ids = set(challenge.question_ids)
        if not supplied_set.issubset(known_ids):
            return ReceiptClaim(
                status="rejected", message_text="", reason="unknown question identity",
            )
        lease = secrets.token_urlsafe(24)
        challenge.status = "inflight"
        challenge.lease_id = lease
        by_id = dict(zip(challenge.question_ids, challenge.questions))
        answer_by_id = {row.question_id: row.value for row in receipt.answers}
        semantic_rows = [
            (str(by_id[identity].get("field") or "answer"),
             _safe_literal(answer_by_id[identity]))
            for identity in supplied
        ]
        required = {
            identity for identity, question in zip(
                challenge.question_ids, challenge.questions,
            ) if question.get("required_input") is True
        }
        remaining = tuple(identity for identity in challenge.question_ids
                          if identity in required and identity not in supplied_set)
        answers_valid = all(
            _answer_valid(by_id[identity], answer_by_id[identity])
            for identity in supplied
        )
        semantic_continuation = dict(continuation_contract) \
            if isinstance(continuation_contract, dict) else {}
        if answers_valid:
            merged = merge_continuation_decisions(
                continuation_contract,
                [{"field": str(by_id[identity].get("field") or ""),
                  "value": _safe_literal(answer_by_id[identity]),
                  "source": "interview_answer"} for identity in supplied],
            )
            if merged:
                semantic_continuation = merged
        if remaining:
            return ReceiptClaim(
                status="semantic", message_text=_message(semantic_rows),
                continuation_contract=semantic_continuation,
                remaining=remaining, reason="answer-coverage",
                _challenge_id=challenge.challenge_id, _lease_id=lease,
                _checkpoint_revision=challenge.checkpoint_revision,
            )
        if not answers_valid:
            return ReceiptClaim(
                status="semantic", message_text=_message(semantic_rows),
                continuation_contract=semantic_continuation,
                reason="answer-shape", _challenge_id=challenge.challenge_id,
                _lease_id=lease, _checkpoint_revision=challenge.checkpoint_revision,
            )

        projected_rows: list[dict] = []
        refinement: dict[str, str] = {}
        for identity in supplied:
            projected = _project(
                str(by_id[identity].get("field") or ""), answer_by_id[identity],
            )
            if projected is None or projected[0] in refinement:
                projected_rows.clear()
                refinement.clear()
                break
            canonical_field, canonical_value = projected
            refinement[canonical_field] = canonical_value
            projected_rows.append({
                "question_id": identity, "field": canonical_field,
                "value": canonical_value,
            })
        all_active_answered = supplied_set == set(challenge.question_ids)
        projection: dict = {}
        post_continuation: dict = {}
        status: Literal["fast", "semantic"] = "semantic"
        if projected_rows and all_active_answered:
            post_continuation = merge_continuation_decisions(
                continuation_contract,
                [{"field": row["field"], "value": row["value"],
                  "source": "interview_answer"} for row in projected_rows],
            )
            try:
                if not post_continuation:
                    raise ValueError("receipt cannot project an invalid continuation contract")
                projection = _PROJECTION_ADAPTER.validate_python({
                    "contract": "question-answer-projection.v1",
                    "authority": "session.question-answer-receipt.v1",
                    "checkpoint_digest": challenge.checkpoint_digest,
                    "request_plan_digest": challenge.request_plan_digest,
                    "continuation_digest": digest_value(post_continuation),
                    "answered": projected_rows,
                    "remaining": [], "complete": True,
                    "request_refinement": refinement,
                }, strict=True).model_dump()
            except (ValidationError, TypeError, ValueError):
                challenge.status = "pending"
                challenge.lease_id = ""
                return ReceiptClaim(
                    status="rejected", message_text="",
                    reason="invalid server answer projection",
                )
            status = "fast"
        return ReceiptClaim(
            status=status, message_text=_message(semantic_rows), projection=projection,
            continuation_contract=(post_continuation if status == "fast"
                                   else semantic_continuation),
            reason="complete" if status == "fast" else "semantic-projector-required",
            saved_calls=1 if status == "fast" else 0,
            _challenge_id=challenge.challenge_id, _lease_id=lease,
            _checkpoint_revision=challenge.checkpoint_revision,
        )


def commit_question_receipt(claim: ReceiptClaim) -> bool:
    if not claim.owns_lease:
        return False
    with _LOCK:
        challenge = _CHALLENGES.get(claim._challenge_id)
        if (not challenge or challenge.status != "inflight"
                or challenge.lease_id != claim._lease_id):
            return False
        challenge.status = "consumed"
        challenge.lease_id = ""
        return True


def release_question_receipt(claim: ReceiptClaim) -> bool:
    if not claim.owns_lease:
        return False
    with _LOCK:
        challenge = _CHALLENGES.get(claim._challenge_id)
        if (not challenge or challenge.status != "inflight"
                or challenge.lease_id != claim._lease_id):
            return False
        challenge.status = "pending"
        challenge.lease_id = ""
        return True


def finish_question_receipt(
    claim: ReceiptClaim,
    *,
    success: bool,
    checkpoint_revision: str,
) -> bool:
    """Commit completed/advanced turns; release only a failure that changed no checkpoint."""
    if not claim.owns_lease:
        return False
    if success or str(checkpoint_revision or "") != claim._checkpoint_revision:
        return commit_question_receipt(claim)
    return release_question_receipt(claim)


def reset_question_receipts_for_tests() -> None:
    with _LOCK:
        _CHALLENGES.clear()
        _BINDINGS.clear()


__all__ = [
    "ReceiptClaim", "checkpoint_digest", "claim_question_receipt",
    "commit_question_receipt", "digest_value", "finish_question_receipt",
    "issue_question_challenge", "question_turn_lock", "release_question_receipt",
]
