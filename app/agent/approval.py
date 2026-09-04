"""agent/approval.py — 쓰기 전 사용자 승인(HITL)을 **도구 경계에서** 강제한다.

"쓰기 전에 물어봐"를 프롬프트로만 걸면 지켜지지 않는다. 모델이 헷갈릴 수도 있고, 더 나쁘게는
**티켓 본문·코멘트에 섞여 들어온 문장이 지시처럼 읽힐 수** 있다(우리 도구는 남이 쓴 텍스트를
그대로 컨텍스트에 싣는다). 그래서 승인은 프롬프트가 아니라 코드로 막는다.

  1. 그래프가 초안을 만들면 `stage()` 로 **토큰**을 받는다. 이때 초안 내용의 해시가 함께 박힌다.
  2. 사용자가 화면에서 승인해야 그 토큰이 ActionExecutor 에게 넘어간다.
  3. 쓰기 도구는 `consume()` 없이는 아무것도 못 한다. 토큰은 **1회용**이다.

핵심은 토큰이 **그 내용에만** 유효하다는 점이다. A 를 보여 주고 승인받은 뒤 B 를 만드는 경로가
막힌다 — 승인 화면과 실제 실행이 같은 것임을 해시가 보증한다. 모델은 토큰을 지어낼 수 없다.

저장은 프로세스 메모리다. LTM 은 사용자 PC 에서 도는 단일 프로세스 앱이고, 승인은 **한 대화
안에서 몇 초~몇 분** 사이에 소비된다. 재시작하면 승인이 날아가는 게 맞다(안전한 방향).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as _rand
import threading
import time
from contextvars import ContextVar, Token

TTL_SECONDS = 30 * 60          # 승인해 놓고 잊은 초안이 무한정 살아 있지 않게
_lock = threading.Lock()
_pending: dict[str, dict] = {}
_consumed: dict[tuple[str, str], dict] = {}
_verified: dict[str, float] = {}
_attestation_key = _rand.token_bytes(32)
_execution_attempt: ContextVar[tuple[str, str]] = ContextVar(
    "approval_execution_attempt", default=("", ""),
)


class ApprovalIdentityError(RuntimeError):
    """The currently authenticated Jira actor cannot be proven freshly."""


def _normalized_actor(raw) -> dict:
    if not isinstance(raw, dict):
        raise ApprovalIdentityError("Jira 사용자를 확인하지 못했습니다.")
    actor_id = str(
        raw.get("id") or raw.get("accountId") or raw.get("name") or raw.get("key") or ""
    ).strip()
    raw_aliases = raw.get("actorAliases") or raw.get("aliases") or ()
    if isinstance(raw_aliases, str):
        raw_aliases = (raw_aliases,)
    elif not isinstance(raw_aliases, (list, tuple, set, frozenset)):
        raw_aliases = ()
    aliases = {
        str(value or "").strip().casefold()
        for value in (
            *raw_aliases,
            actor_id,
            # ``current_user_fresh`` exposes ``name`` as a human display name.
            # Display names are not unique and must never make two Jira accounts
            # equivalent.  ``actor_id`` still falls back to raw ``name`` for a
            # legacy identity shape that has no stable id/aliases at all.
            raw.get("accountId"), raw.get("key"),
        )
        if str(value or "").strip()
    }
    if not actor_id or not aliases:
        raise ApprovalIdentityError("Jira 사용자를 확인하지 못했습니다.")
    return {"id": actor_id, "aliases": tuple(sorted(aliases))}


def current_actor_fresh() -> dict:
    """Resolve the current Jira actor without the six-hour identity cache."""
    from app.agent.tools._ctx import client, settings

    jira = client()
    fresh = getattr(jira, "current_user_fresh", None)
    if callable(fresh):
        try:
            return _normalized_actor(fresh())
        except ApprovalIdentityError:
            raise
        except Exception as exc:
            raise ApprovalIdentityError("Jira 사용자를 새로 확인하지 못했습니다.") from exc

    # Lightweight test doubles historically expose no identity endpoint.  Keep those
    # compatible only in the synthetic mock environment; production never falls back.
    try:
        environment = str(getattr(settings(), "jira_env", "") or "").split("@", 1)[0].lower()
    except Exception:
        environment = str(os.getenv("JIRA_ENV") or "").strip().lower()
    if environment == "mock":
        cached = getattr(jira, "current_user", None)
        if callable(cached):
            try:
                return _normalized_actor(cached())
            except Exception:
                pass
        return {"id": "mock-local-user", "aliases": ("mock-local-user",)}
    raise ApprovalIdentityError("Jira 사용자를 새로 확인할 수 없는 실행 환경입니다.")


def _same_actor(expected: dict, observed: dict) -> bool:
    wanted = {str(value).casefold() for value in expected.get("aliases") or () if value}
    actual = {str(value).casefold() for value in observed.get("aliases") or () if value}
    return bool(wanted and actual and wanted.intersection(actual))


def _reject_bound(token: str, record: dict | None = None) -> None:
    """Revoke one capability and its exact compound peer, if present."""
    with _lock:
        current = _pending.get(token or "")
        if not current:
            return
        removed = _pending.pop(token, None) or {}
        peer_token = str(removed.get("peer_token") or "")
        peer = _pending.get(peer_token) if peer_token else None
        if (peer and peer.get("bundle") == removed.get("bundle")
                and str(peer.get("peer_token") or "") == str(token or "")):
            _pending.pop(peer_token, None)


def verify_actor(token: str, thread_id: str | None = None) -> tuple[bool, str]:
    """Freshly prove a bound capability still belongs to the Jira session actor."""
    with _lock:
        record = _pending.get(token or "")
        if not record or (thread_id is not None and record.get("thread") != str(thread_id)):
            return False, "승인 토큰이 이 대화의 것이 아니거나 만료되었습니다."
        snapshot = dict(record)
    expected = snapshot.get("actor")
    # Legacy/direct unit-test capabilities remain valid.  Every token minted by the
    # production graph passes an actor explicitly and therefore takes the strict path.
    if not isinstance(expected, dict):
        return True, ""
    try:
        observed = current_actor_fresh()
    except ApprovalIdentityError:
        _reject_bound(token, snapshot)
        return False, (
            "현재 Jira 사용자를 새로 확인하지 못해 승인을 폐기했습니다. 다시 요청해 주세요."
        )
    if not _same_actor(expected, observed):
        _reject_bound(token, snapshot)
        return False, (
            "승인을 요청한 Jira 사용자와 현재 사용자가 달라 승인을 폐기했습니다. 다시 요청해 주세요."
        )
    return True, ""


def _attestation(record: dict, token: str, attempt_digest: str) -> dict:
    raw_actor = record.get("actor")
    actor = None
    if isinstance(raw_actor, dict):
        normalized = _normalized_actor(raw_actor)
        actor = {
            "id": normalized["id"],
            "aliases": list(normalized["aliases"]),
        }
    body = {
        "contract": "approval-consumption.v1",
        "thread": str(record.get("thread") or ""),
        "action": str(record.get("action") or ""),
        "fp": str(record.get("fp") or ""),
        "approved": record.get("approved") is True,
        # Keep the approval principal in the HMAC-sealed execution evidence after
        # the one-use pending record is removed.  Provider mutations can then
        # re-prove the exact actor at their own last responsible moment.
        "actor": actor,
        "token_digest": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "attempt_digest": attempt_digest,
        "nonce": _rand.token_hex(16),
        "ts": time.time(),
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body["seal"] = hmac.new(_attestation_key, blob, hashlib.sha256).hexdigest()
    return body


def _valid_attestation(value: dict) -> bool:
    if not isinstance(value, dict) or set(value) != {
            "contract", "thread", "action", "fp", "approved", "actor", "token_digest",
            "attempt_digest", "nonce", "ts", "seal",
    }:
        return False
    def sha256(value) -> bool:
        return (isinstance(value, str) and len(value) == 64
                and all(character in "0123456789abcdef" for character in value))

    raw_actor = value.get("actor")
    if raw_actor is not None:
        try:
            normalized_actor = _normalized_actor(raw_actor)
        except ApprovalIdentityError:
            return False
        if (raw_actor.get("id") != normalized_actor["id"]
                or raw_actor.get("aliases") != list(normalized_actor["aliases"])):
            return False
    timestamp = value.get("ts")
    now = time.time()
    if (value.get("contract") != "approval-consumption.v1"
            or value.get("approved") is not True
            or not value.get("thread") or not value.get("action")
            or not sha256(value.get("fp")) or not sha256(value.get("token_digest"))
            or not sha256(value.get("attempt_digest")) or not sha256(value.get("seal"))
            or not isinstance(value.get("nonce"), str) or len(value.get("nonce")) != 32
            or isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or timestamp < now - TTL_SECONDS or timestamp > now + 5):
        return False
    seal = str(value.get("seal") or "")
    body = {key: item for key, item in value.items() if key != "seal"}
    try:
        blob = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return False
    expected = hmac.new(_attestation_key, blob, hashlib.sha256).hexdigest()
    return hmac.compare_digest(seal, expected)


def bound_actor_write_guard(jira=None):
    """Return a fail-closed fresh-actor check for the current consumed dispatch.

    The guard is intentionally created only after ``consume`` sealed the exact
    capability.  Callers pass the same closure to every provider mutation in a
    bulk operation.  Once one check fails, all later calls stay blocked even if a
    test double catches the first exception and continues iterating.
    """
    token, nonce = _execution_attempt.get()
    if not token or not nonce:
        # Direct legacy tool calls have already been checked by consume().  The
        # production ActionExecutor always establishes an execution attempt.
        return None
    attempt_digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    with _lock:
        raw = _consumed.get((token, attempt_digest))
        attestation = dict(raw) if isinstance(raw, dict) else None
    if not _valid_attestation(attestation):
        raise ApprovalIdentityError(
            "승인 실행 증빙을 확인하지 못해 Jira 변경을 중단했습니다."
        )
    expected = attestation.get("actor")
    if expected is None:
        # Backward compatibility for capabilities staged directly in older unit
        # tests. Every production graph capability is actor-bound.
        return None
    expected = _normalized_actor(expected)
    blocked = {"error": ""}

    def assert_same_actor() -> None:
        if blocked["error"]:
            raise ApprovalIdentityError(blocked["error"])
        try:
            target = jira
            if target is None:
                from app.agent.tools._ctx import client
                target = client()
            verifier = getattr(target, "assert_mutation_actor_identity", None)
            if callable(verifier):
                verifier({"id": expected["id"], "aliases": list(expected["aliases"])})
            else:
                observed = current_actor_fresh()
                if not _same_actor(expected, observed):
                    raise ApprovalIdentityError(
                        "승인을 요청한 Jira 사용자와 현재 사용자가 다릅니다."
                    )
        except Exception as exc:
            blocked["error"] = (
                "승인 후 Jira 사용자가 바뀌었거나 새로 확인되지 않아 남은 변경을 중단했습니다."
            )
            raise ApprovalIdentityError(blocked["error"]) from exc

    return assert_same_actor


def fingerprint(payload) -> str:
    """내용 지문. 키 순서·공백이 달라도 같은 내용이면 같은 값이어야 한다."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def stage(thread_id: str, action: str, payload, *, actor: dict | None = None) -> str:
    """초안을 등록하고 승인 토큰을 발급한다. 화면은 이 토큰과 payload 를 함께 보여 준다."""
    _sweep()
    token = _rand.token_urlsafe(24)
    with _lock:
        _pending[token] = {"thread": str(thread_id or ""), "action": action,
                           "fp": fingerprint(payload), "payload": payload, "ts": time.time(),
                           "approved": False,
                           **({"actor": _normalized_actor(actor)} if actor is not None else {})}
    return token


def stage_pair(thread_id: str, primary_action: str, primary_payload,
               secondary_action: str, secondary_payload, *,
               actor: dict | None = None) -> tuple[str, str]:
    """Atomically stage the two fingerprints shown on one compound approval card.

    Two unrelated, individually valid capabilities must never be spliced together by a
    stale checkpoint or direct caller.  The reciprocal token ids bind both action/payload
    records to the exact same card; :class:`ActionExecutor` validates the pair before it
    executes either side.
    """
    _sweep()
    primary = _rand.token_urlsafe(24)
    secondary = _rand.token_urlsafe(24)
    bundle = _rand.token_urlsafe(18)
    now = time.time()
    bound_actor = _normalized_actor(actor) if actor is not None else None
    with _lock:
        _pending[primary] = {
            "thread": str(thread_id or ""), "action": primary_action,
            "fp": fingerprint(primary_payload), "payload": primary_payload, "ts": now,
            "approved": False, "bundle": bundle, "bundle_role": "primary",
            "peer_token": secondary, "peer_action": secondary_action,
            "peer_fp": fingerprint(secondary_payload),
            **({"actor": bound_actor} if bound_actor is not None else {}),
        }
        _pending[secondary] = {
            "thread": str(thread_id or ""), "action": secondary_action,
            "fp": fingerprint(secondary_payload), "payload": secondary_payload, "ts": now,
            "approved": False, "bundle": bundle, "bundle_role": "secondary",
            "peer_token": primary, "peer_action": primary_action,
            "peer_fp": fingerprint(primary_payload),
            **({"actor": bound_actor} if bound_actor is not None else {}),
        }
    return primary, secondary


def approve(token: str, thread_id: str = None) -> bool:
    """사용자가 화면에서 눌렀다. 여기서부터 쓰기가 가능해진다."""
    with _lock:
        rec = _pending.get(token)
        if not rec or (thread_id is not None and rec["thread"] != str(thread_id)):
            return False
        rec["approved"] = True
        return True


def amend_assignees(token: str, thread_id: str, assignees: dict) -> tuple[bool, str]:
    """승인 **직전**, 사용자가 카드에서 담당자를 바꿨다 — 스테이징된 내용을 고치고 지문을
    다시 묶는다.

    "보여 준 것과 같은 내용만 실행된다"는 보증은 그대로다: 이 변경은 승인 화면의 사용자
    입력에서만 오고(서버가 실재 검증), 고친 내용이 곧 사용자가 승인하는 내용이 된다.
    승인 뒤에는 못 고친다 — 그건 다시 '보여 준 것과 다른 실행'이 된다.
    """
    with _lock:
        rec = _pending.get(token or "")
        if not rec or rec["thread"] != str(thread_id or ""):
            return False, "승인 토큰이 이 대화의 것이 아니거나 만료되었습니다."
        if rec["approved"]:
            return False, "이미 승인된 내용은 고칠 수 없습니다. 취소 후 다시 요청하세요."
        if rec["action"] != "create_tickets":
            return False, "담당자 변경은 생성 초안에만 적용할 수 있습니다."
        items = (rec["payload"] or {}).get("items") or []
        for i, uid in (assignees or {}).items():
            try:
                idx = int(i)
            except (TypeError, ValueError):
                return False, f"항목 번호가 잘못되었습니다: {i}"
            if not (0 <= idx < len(items)):
                return False, f"초안에 없는 항목 번호입니다: {idx}"
            uid = str(uid or "").strip()
            if uid:
                items[idx]["assignee"] = uid
            else:
                items[idx].pop("assignee", None)
        rec["fp"] = fingerprint(rec["payload"])
        return True, ""


def amend_payload(token: str, thread_id: str, payload) -> tuple[bool, str]:
    """승인 **직전**, 스테이징 payload 를 통째로 교체하고 지문을 다시 묶는다.

    카드 인라인 편집(제목·본문·라벨·마감 등)의 서버 반영 경로다. 부분 patch 를 여기서
    또 조립하면 State draft → as_bulk_items 경로와 **두 벌**이 되어 지문이 어긋난다
    (담당자 하나일 땐 우연히 맞았지만 list·빈값 처리에서 갈라진다). 그래서 호출자
    (session.resume)가 State draft 를 고친 뒤 as_bulk_items 산출물을 그대로 넘긴다 —
    두 경로가 한 함수를 공유한다. 승인 뒤에는 못 고친다(보여 준 것과 다른 실행이 된다)."""
    with _lock:
        rec = _pending.get(token or "")
        if not rec or rec["thread"] != str(thread_id or ""):
            return False, "승인 토큰이 이 대화의 것이 아니거나 만료되었습니다."
        if rec["approved"]:
            return False, "이미 승인된 내용은 고칠 수 없습니다. 취소 후 다시 요청하세요."
        if rec["action"] not in ("create_tickets", "create_epic"):
            return False, "카드 편집은 생성 초안에만 적용할 수 있습니다."
        rec["payload"] = payload
        rec["fp"] = fingerprint(payload)
        return True, ""


def reject(token: str) -> bool:
    """Cancel a still-pending capability; never erase another in-flight attempt's proof."""
    with _lock:
        return _pending.pop(token, None) is not None


def peek(token: str) -> dict | None:
    with _lock:
        rec = _pending.get(token)
        return dict(rec) if rec else None


def change_audit_binding(
        token: str, *, correlation_id: str, record: dict | None = None) -> dict:
    """Mint the metadata-only, HMAC-sealed audit identity for one primary capability.

    ``record`` may be the ActionExecutor's pre-consumption deep copy.  It is accepted only
    when its payload still matches the server-owned fingerprint; callers cannot supply any
    digest directly.  Compound cards deliberately bind only the primary capability and say
    so in ``scope`` instead of claiming a receipt for both writes.
    """
    capability = str(token or "")
    correlation = str(correlation_id or "").strip()
    if not capability or not correlation:
        raise ValueError("change audit capability and correlation are required")
    if record is None:
        with _lock:
            current = _pending.get(capability)
            snapshot = dict(current) if isinstance(current, dict) else None
    else:
        snapshot = dict(record) if isinstance(record, dict) else None
    if not snapshot:
        raise ValueError("change audit capability record is unavailable")
    thread_id = str(snapshot.get("thread") or "")
    action = str(snapshot.get("action") or "")
    payload = snapshot.get("payload")
    payload_digest = str(snapshot.get("fp") or "")
    if (not thread_id or not action or not isinstance(payload, dict)
            or not hmac.compare_digest(payload_digest, fingerprint(payload))):
        raise ValueError("change audit capability record is inconsistent")
    actor = _normalized_actor(snapshot.get("actor"))
    bundle = str(snapshot.get("bundle") or "")
    role = str(snapshot.get("bundle_role") or "")
    if bundle and role != "primary":
        raise ValueError("only the compound primary capability may anchor change audit")
    from app.agent.maxis.audit import metadata_sha256, seal_change_audit_binding

    binding = seal_change_audit_binding(
        actor_sha256=metadata_sha256(actor["id"].strip().casefold()),
        approval_payload_sha256=payload_digest,
        capability_sha256=metadata_sha256(capability),
        correlation_sha256=metadata_sha256(correlation),
        thread_sha256=metadata_sha256(thread_id),
        action_sha256=metadata_sha256(action),
        scope="compound_primary" if bundle else "single",
    )
    return binding.model_dump(mode="python")


def verify_change_audit_binding(
        value, *, token: str, correlation_id: str, record: dict) -> dict:
    """Reject graph/checkpointer injection unless it equals the canonical sealed binding."""
    from app.agent.maxis.audit import validate_change_audit_binding

    observed = validate_change_audit_binding(value)
    expected = validate_change_audit_binding(change_audit_binding(
        token, correlation_id=correlation_id, record=record,
    ))
    if not hmac.compare_digest(
            observed.binding_seal, expected.binding_seal):
        raise ValueError("change audit binding does not match the approved capability")
    return observed.model_dump(mode="python")


def finalize_change_audit_binding(
        value, *, token: str, thread_id: str, execution_receipt=None) -> dict:
    """Bind a terminal audit identity to a verified signed receipt when one exists.

    Comment, transition, link and compound-card adapters currently issue no typed execution
    receipt.  Those paths retain the primary approval binding with ``not_issued`` rather than
    manufacturing receipt evidence.
    """
    from app.agent.maxis.audit import (
        metadata_sha256,
        seal_execution_change_binding,
        validate_change_audit_binding,
    )

    binding = validate_change_audit_binding(value)
    if not hmac.compare_digest(binding.capability_sha256, metadata_sha256(token)):
        raise ValueError("change audit capability digest mismatch")
    if not hmac.compare_digest(binding.thread_sha256, metadata_sha256(thread_id)):
        raise ValueError("change audit thread digest mismatch")
    receipt_digest = ""
    receipt_signature = ""
    if execution_receipt:
        from app.agent.workflow.execution_receipt import execution_receipt_audit_proof

        proof = execution_receipt_audit_proof(
            execution_receipt, thread_id=thread_id, token=token,
        )
        if (not proof
                or not hmac.compare_digest(
                    binding.approval_payload_sha256, proof["payload_digest"])
                or not hmac.compare_digest(
                    binding.capability_sha256, proof["capability_digest"])):
            raise ValueError("execution receipt does not match the approved audit binding")
        receipt_digest = proof["receipt_digest"]
        receipt_signature = proof["receipt_signature"]
    finalized = seal_execution_change_binding(
        binding,
        execution_receipt_sha256=receipt_digest,
        execution_receipt_signature=receipt_signature,
    )
    return finalized.model_dump(mode="python")


def consume(token: str, action: str, payload) -> tuple[bool, str]:
    """쓰기 도구가 부른다. **(성공?, 사유)**. 성공이면 토큰은 즉시 사라진다(1회용).

    payload 를 다시 받아 지문을 대조하는 것이 이 함수의 존재 이유다 — 승인받은 그 내용이
    맞는지 확인하지 않으면 토큰은 그냥 '쓰기 허가증'이 되어 버린다.
    """
    _sweep()
    actor_ok, actor_error = verify_actor(token)
    if not actor_ok:
        return False, actor_error
    with _lock:
        rec = _pending.get(token or "")
        if not rec:
            return False, "승인 토큰이 없거나 이미 사용/만료되었습니다. 사용자에게 다시 확인을 받으세요."
        if not rec["approved"]:
            return False, "아직 사용자가 승인하지 않았습니다. 승인 카드를 띄우고 기다리세요."
        if rec["action"] != action:
            return False, f"승인된 작업은 '{rec['action']}' 인데 '{action}' 을 실행하려 합니다."
        if rec["fp"] != fingerprint(payload):
            return False, ("승인 화면에 보여 준 내용과 실행하려는 내용이 다릅니다. "
                           "바뀐 내용으로 다시 승인을 받으세요.")
        consumed = _pending.pop(token, None)
        attempt_token, attempt_nonce = _execution_attempt.get()
        if attempt_token == token and attempt_nonce:
            attempt_digest = hashlib.sha256(attempt_nonce.encode("utf-8")).hexdigest()
            _consumed[(token, attempt_digest)] = _attestation(
                consumed, token, attempt_digest,
            )
        return True, ""


def begin_consumption_attempt(token: str) -> tuple[str, Token]:
    """Bind a synchronous ActionExecutor dispatch to an unguessable local attempt."""
    nonce = _rand.token_urlsafe(24)
    return nonce, _execution_attempt.set((str(token or ""), nonce))


def end_consumption_attempt(context_token: Token) -> None:
    _execution_attempt.reset(context_token)


def take_consumption(
        token: str, *, attempt_nonce: str, thread_id: str, action: str, payload) -> dict | None:
    """Take one exact positive-consumption attestation for this dispatch attempt only."""
    _sweep()
    attempt_digest = hashlib.sha256(str(attempt_nonce or "").encode("utf-8")).hexdigest()
    with _lock:
        value = _consumed.pop((token or "", attempt_digest), None)
    if not _valid_attestation(value):
        return None
    try:
        payload_fp = fingerprint(payload)
    except (TypeError, ValueError):
        return None
    expected_token = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    if (value.get("thread") != str(thread_id or "")
            or value.get("action") != str(action or "")
            or value.get("fp") != payload_fp
            or value.get("attempt_digest") != attempt_digest
            or not hmac.compare_digest(str(value.get("token_digest") or ""), expected_token)):
        return None
    return dict(value)


def verify_consumption_attestation(
        value: dict, *, token: str, thread_id: str, action: str, payload) -> bool:
    """Verify an already-taken server attestation before a receipt is sealed."""
    if not _valid_attestation(value):
        return False
    try:
        payload_fp = fingerprint(payload)
    except (TypeError, ValueError):
        return False
    exact = bool(
        value.get("thread") == str(thread_id or "")
        and value.get("action") == str(action or "")
        and value.get("fp") == payload_fp
        and isinstance(value.get("attempt_digest"), str)
        and len(value.get("attempt_digest")) == 64
        and hmac.compare_digest(
            str(value.get("token_digest") or ""),
            hashlib.sha256(str(token or "").encode("utf-8")).hexdigest(),
        )
    )
    if not exact:
        return False
    nonce = str(value.get("nonce") or "")
    with _lock:
        if nonce in _verified:
            return False
        _verified[nonce] = time.time()
    return True


def _sweep():
    cut = time.time() - TTL_SECONDS
    with _lock:
        for t in [t for t, r in _pending.items() if r["ts"] < cut]:
            _pending.pop(t, None)
        for key in [key for key, record in _consumed.items() if record["ts"] < cut]:
            _consumed.pop(key, None)
        for nonce in [nonce for nonce, ts in _verified.items() if ts < cut]:
            _verified.pop(nonce, None)


def clear():
    """테스트용."""
    with _lock:
        _pending.clear()
        _consumed.clear()
        _verified.clear()
