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


def _attestation(record: dict, token: str, attempt_digest: str) -> dict:
    body = {
        "contract": "approval-consumption.v1",
        "thread": str(record.get("thread") or ""),
        "action": str(record.get("action") or ""),
        "fp": str(record.get("fp") or ""),
        "approved": record.get("approved") is True,
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
            "contract", "thread", "action", "fp", "approved", "token_digest", "attempt_digest",
            "nonce", "ts", "seal",
    }:
        return False
    def sha256(value) -> bool:
        return (isinstance(value, str) and len(value) == 64
                and all(character in "0123456789abcdef" for character in value))

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


def fingerprint(payload) -> str:
    """내용 지문. 키 순서·공백이 달라도 같은 내용이면 같은 값이어야 한다."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def stage(thread_id: str, action: str, payload) -> str:
    """초안을 등록하고 승인 토큰을 발급한다. 화면은 이 토큰과 payload 를 함께 보여 준다."""
    _sweep()
    token = _rand.token_urlsafe(24)
    with _lock:
        _pending[token] = {"thread": str(thread_id or ""), "action": action,
                           "fp": fingerprint(payload), "payload": payload, "ts": time.time(),
                           "approved": False}
    return token


def stage_pair(thread_id: str, primary_action: str, primary_payload,
               secondary_action: str, secondary_payload) -> tuple[str, str]:
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
    with _lock:
        _pending[primary] = {
            "thread": str(thread_id or ""), "action": primary_action,
            "fp": fingerprint(primary_payload), "payload": primary_payload, "ts": now,
            "approved": False, "bundle": bundle, "bundle_role": "primary",
            "peer_token": secondary, "peer_action": secondary_action,
            "peer_fp": fingerprint(secondary_payload),
        }
        _pending[secondary] = {
            "thread": str(thread_id or ""), "action": secondary_action,
            "fp": fingerprint(secondary_payload), "payload": secondary_payload, "ts": now,
            "approved": False, "bundle": bundle, "bundle_role": "secondary",
            "peer_token": primary, "peer_action": primary_action,
            "peer_fp": fingerprint(primary_payload),
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


def consume(token: str, action: str, payload) -> tuple[bool, str]:
    """쓰기 도구가 부른다. **(성공?, 사유)**. 성공이면 토큰은 즉시 사라진다(1회용).

    payload 를 다시 받아 지문을 대조하는 것이 이 함수의 존재 이유다 — 승인받은 그 내용이
    맞는지 확인하지 않으면 토큰은 그냥 '쓰기 허가증'이 되어 버린다.
    """
    _sweep()
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
