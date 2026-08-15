# tools/agent_context_change_eval.py — 대화 중 요청 컨텍스트 전환 배터리.
from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.agent_eval_review_specs import review_specs
from tools.agent_scenario_eval import parse_scenario_args, run_scenario_suite

try:
    from app.agent.prompts.base import PROMPT_VERSION
except ImportError:
    PROMPT_VERSION = os.getenv("LAKE_AGENT_PROMPT_VERSION", "legacy")


BATTERY_VERSION = "1.0.0"
SUITE_REVIEW_ELEMENTS, CASE_REVIEW_SPECS = review_specs("ctx-chg")


def _pending(output: dict[str, Any]) -> dict[str, Any]:
    return dict(output.get("pending") or {})


def _text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _ctx_unrelated_ok(output: dict[str, Any], _outputs: list[dict[str, Any]]) -> bool:
    pending = _pending(output)
    changes = pending.get("changes") or {}
    final = _text({"reply": output.get("reply"), "pending": pending})
    return (
        pending.get("action") == "update_ticket"
        and pending.get("key") == "DL-9203"
        and changes == {"priority": "P2-Major"}
        and "fdc_trace_summary_ic" not in final.lower()
        and "DL-904" not in final
    )


def _ctx_shared_info_ok(output: dict[str, Any], _outputs: list[dict[str, Any]]) -> bool:
    reply = output.get("reply") or ""
    return (
        not _pending(output)
        and "DL-9090" in reply
        and all(key in reply for key in ("DL-9093", "DL-9094", "DL-9095"))
        and "2026-08-24" not in reply
        and "fdc" not in reply.lower()
    )


def _ctx_flip_flop_ok(output: dict[str, Any], _outputs: list[dict[str, Any]]) -> bool:
    pending = _pending(output)
    changes = pending.get("changes") or {}
    final = _text({"reply": output.get("reply"), "pending": pending})
    return (
        pending.get("action") == "update_ticket"
        and pending.get("key") == "DL-9203"
        and changes == {"summary": "[Catalog] Puffin NDV 결과 템플릿 정리"}
        and not pending.get("comment")
        and "P1-Critical" not in final
        and "2026-08-31" not in final
        and "결정사항을 공유" not in final
    )


def _ctx_return_ok(output: dict[str, Any], _outputs: list[dict[str, Any]]) -> bool:
    pending = _pending(output)
    keys = [str(key) for key in pending.get("keys") or []]
    comments = pending.get("comments") or []
    final = _text({"reply": output.get("reply"), "pending": pending})
    return (
        pending.get("action") == "update_tickets"
        and keys == ["DL-9095"]
        and not (pending.get("changes") or {})
        and len(comments) == 1
        and "성능 측정" in _text(comments)
        and "이다은" not in final
        and "skcc.i2011" not in final
    )


CASES = [
    (
        "CTX1",
        "완전히 다른 요청으로 전환되면 이전 조사 맥락을 폐기하고 최신 변경만 초안화",
        [
            "fdc.fdc_trace_summary_ic 데이터 히스토리와 현재 상태를 조사해줘.",
            "이건 그만. 완전히 다른 요청이야. DL-9203의 priority만 P2-Major로 바꾸는 승인 전 초안을 만들어줘. 다른 필드는 건드리지 마.",
        ],
        _ctx_unrelated_ok,
    ),
    (
        "CTX2",
        "사용자가 정보를 공유한 뒤 다른 조회를 요청하면 공유 정보와 조회 대상을 섞지 않음",
        [
            "참고로 2026-08-24에 fdc 30분 배치 점검이 예정돼 있어. 지금은 답하지 말고 이 정보만 기억해줘.",
            "DL-9090과 하위 Task의 현재 진행상황과 남은 작업만 알려줘.",
        ],
        _ctx_shared_info_ok,
    ),
    (
        "CTX3",
        "요청이 여러 번 뒤집혀도 취소된 write 의도를 버리고 마지막 필드만 초안화",
        [
            "DL-9203의 priority를 P1-Critical로, due를 2026-08-31로 바꾸는 초안을 만들어줘.",
            "방금 필드 변경은 취소. 대신 같은 티켓에 '회의 결정사항을 공유합니다' 댓글만 다는 초안으로 바꿔줘.",
            "그 댓글도 취소. 최종 요청은 제목만 '[Catalog] Puffin NDV 결과 템플릿 정리'로 변경하는 거야. 다른 변경 없이 승인 전 초안만 보여줘.",
        ],
        _ctx_flip_flop_ok,
    ),
    (
        "CTX4",
        "잠깐 다른 주제를 거친 뒤 이전 대상에 돌아와도 현재 요청에 필요한 정보만 복원",
        [
            "DL-9090과 하위 Task의 진행상황을 확인해줘.",
            "잠깐 다른 얘기. @이다은이 지금 맡은 업무를 요약해줘.",
            "다시 DL-9090으로 돌아갈게. 아직 남은 하위 Task 하나에만 '2홉 100노드 성능 측정 결과와 원본 로그를 첨부해 주세요'라는 댓글 승인 초안을 만들어줘. 필드는 바꾸지 마.",
        ],
        _ctx_return_ok,
    ),
]


if __name__ == "__main__":
    model, selected, requested_out = parse_scenario_args(sys.argv[1:])
    run_scenario_suite(
        suite="ctx-chg",
        battery_version=BATTERY_VERSION,
        cases=CASES,
        model=model,
        simple_model=os.environ.get("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini"),
        prompt_version=PROMPT_VERSION,
        suite_review_elements=SUITE_REVIEW_ELEMENTS,
        case_review_specs=CASE_REVIEW_SPECS,
        selected=selected,
        requested_out=requested_out,
    )
