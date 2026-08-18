"""Versioned streaming latency/token battery using the shared evaluation runner.

Run only through the network-authorized launcher::

    python -X utf8 tools/agent_eval_launcher.py perf [model] [P1 ...]

The raw result retains per-turn ``timeToFirstTokenSeconds``, full usage/callsDetail,
retrieval evidence, and the same isolation/candidate identity as the quality batteries.
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.agent_eval_review_specs import review_specs
from tools.agent_scenario_eval import parse_scenario_args, run_scenario_suite


BATTERY_VERSION = "1.0.0"
SUITE_REVIEW_ELEMENTS, CASE_REVIEW_SPECS = review_specs("perf")


def _visible_result(output: dict[str, Any], _outputs: list[dict[str, Any]]) -> bool:
    return bool(output.get("ok") is True and str(output.get("reply") or "").strip())


CASES = [
    ("P1", "오늘 시작할 업무 추천", ["나 오늘 뭐 해야 할까"], _visible_result),
    (
        "P2",
        "개념과 내부 현황을 결합한 지식 답변",
        ["데이터 리니지가 뭐고 우리가 뭘 했는지 정리해줘"],
        _visible_result,
    ),
    (
        "P3",
        "새 기능 생성 첫 turn",
        ["Workbench에 쿼리 결과 엑셀 내보내기를 추가하자. 알아서 초안 잡아줘"],
        _visible_result,
    ),
    ("P4", "단일 티켓 exact 수정", ["DL-101 우선순위를 P2-Major로 바꿔줘"], _visible_result),
]


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    model, selected, requested_out = parse_scenario_args(raw, default_model="gpt-4o-mini")
    from app.agent.prompts.base import PROMPT_VERSION

    run_scenario_suite(
        suite="perf",
        battery_version=BATTERY_VERSION,
        cases=CASES,
        model=model,
        simple_model=os.environ.get("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini"),
        prompt_version=PROMPT_VERSION,
        suite_review_elements=SUITE_REVIEW_ELEMENTS,
        case_review_specs=CASE_REVIEW_SPECS,
        selected=selected,
        requested_out=requested_out,
        streaming=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
