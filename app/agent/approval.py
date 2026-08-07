"""agent/approval.py — 쓰기 전 사용자 승인(HITL)을 **도구 경계에서** 강제한다.

"쓰기 전에 물어봐"를 프롬프트로만 걸면 지켜지지 않는다. 모델이 헷갈릴 수도 있고, 더 나쁘게는
**티켓 본문·코멘트에 섞여 들어온 문장이 지시처럼 읽힐 수** 있다(우리 도구는 남이 쓴 텍스트를
그대로 컨텍스트에 싣는다). 그래서 승인은 프롬프트가 아니라 코드로 막는다.

  1. 그래프가 초안을 만들면 `stage()` 로 **토큰**을 받는다. 이때 초안 내용의 해시가 함께 박힌다.
  2. 사용자가 화면에서 승인해야 그 토큰이 Operator 에게 넘어간다.
  3. 쓰기 도구는 `consume()` 없이는 아무것도 못 한다. 토큰은 **1회용**이다.

핵심은 토큰이 **그 내용에만** 유효하다는 점이다. A 를 보여 주고 승인받은 뒤 B 를 만드는 경로가
막힌다 — 승인 화면과 실제 실행이 같은 것임을 해시가 보증한다. 모델은 토큰을 지어낼 수 없다.

저장은 프로세스 메모리다. LTM 은 사용자 PC 에서 도는 단일 프로세스 앱이고, 승인은 **한 대화
안에서 몇 초~몇 분** 사이에 소비된다. 재시작하면 승인이 날아가는 게 맞다(안전한 방향).
"""

from __future__ import annotations

import hashlib
import json
import secrets as _rand
import threading
import time

TTL_SECONDS = 30 * 60          # 승인해 놓고 잊은 초안이 무한정 살아 있지 않게
_lock = threading.Lock()
_pending: dict[str, dict] = {}


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


def reject(token: str) -> bool:
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
        _pending.pop(token, None)
        return True, ""


def _sweep():
    cut = time.time() - TTL_SECONDS
    with _lock:
        for t in [t for t, r in _pending.items() if r["ts"] < cut]:
            _pending.pop(t, None)


def clear():
    """테스트용."""
    with _lock:
        _pending.clear()
