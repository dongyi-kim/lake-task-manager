"""Versioned user-view output capture for direct Codex/Claude review.

This suite deliberately does not judge its own candidate output. It records the complete
user-visible reply, forms, approval payload, deterministic checks, retrieval evidence, and
usage under the standard ignored raw-result directory. A Codex or Claude work agent then
performs the protocol's ``direct-raw-output-review``; the candidate LTM model is never an
LLM-as-judge.

Run only through the network-authorized launcher::

    python -X utf8 tools/agent_eval_launcher.py user-review [model] [F1 ...]
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.agent_eval_review_specs import review_specs
from tools.agent_scenario_eval import parse_scenario_args, run_scenario_suite


BATTERY_VERSION = "1.0.0"
SUITE_REVIEW_ELEMENTS, CASE_REVIEW_SPECS = review_specs("user-review")


def _visible_result(output: dict[str, Any], _outputs: list[dict[str, Any]]) -> bool:
    """Require a real user-facing terminal surface; qualitative quality stays human-owned."""
    return bool(
        output.get("ok") is True
        and (
            str(output.get("reply") or "").strip()
            or output.get("questions")
            or output.get("pending")
        )
    )


CASES = [
    (
        "F1",
        "새 기능을 티켓으로: 인터뷰에서 구조와 승인 초안까지",
        [
            "우리 기존 etl 파이프라인에 iceberg puffin ndv 통계정보를 생성하는 기능을 추가구현하고 싶어",
            "1차 목표는 PoC. 배경은 StarRocks 4.1.1 QueryQueueV2 의 Estimation 성능 개선. "
            "완료 조건은 Lake 내 Iceberg 배치적재 테이블에 통계 생성 Batch Job 구현. "
            "단계별 Sub-Task 로. Epic 은 DL-102. 알아서",
        ],
        _visible_result,
    ),
    (
        "F2",
        "데이터 자산의 현재 상태와 히스토리",
        ["fdc_flat_summary_ic 데이터에 대해 히스토리 정리", "fdc.fdc_trace_summary_ic"],
        _visible_result,
    ),
    (
        "F3",
        "사람이 현재 맡은 실제 업무",
        ["이다은 책임이 지금 맡고 있는 일 알려줘"],
        _visible_result,
    ),
    (
        "F4",
        "정체 티켓 전체에 담당자 멘션 댓글 초안",
        ["ETL 모듈에서 3개월 이상 업데이트 없는 티켓 전부에 담당자를 멘션해서 "
         "상태 점검을 요청하는 코멘트를 남겨줘"],
        _visible_result,
    ),
    (
        "F5",
        "재현 가능한 버그 승인 초안",
        ["리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다. 크롬에서 재현되고 기대는 그래프가 "
         "그려지는 것. 버그로 올려줘. 알아서"],
        _visible_result,
    ),
    (
        "F6",
        "근거 있는 다음 업무 추천",
        ["지금 무슨 업무를 시작해야 할까"],
        _visible_result,
    ),
    (
        "F7",
        "모듈 전체의 최근 7일 활동",
        ["우리 모듈의 최근 7일 업무 내역이 궁금해"],
        _visible_result,
    ),
    (
        "F8",
        "부모와 하위 작업을 포함한 티켓 진척",
        ["DL-9090 지금 어디까지 진행됐어?"],
        _visible_result,
    ),
]


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    model, selected, requested_out = parse_scenario_args(raw, default_model="gpt-4o-mini")
    # Importing this module remains network- and Agent-free. The launcher has already set
    # process isolation and provider authorization before this function invokes the suite.
    from app.agent.prompts.base import PROMPT_VERSION

    run_scenario_suite(
        suite="user-review",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
