# tools/agent_meeting_eval.py — 회의록 이해·조사·인터뷰·write 초안 배터리.
from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.agent_eval_review_specs import review_specs
from tools.agent_scenario_eval import parse_scenario_args, pending_items, run_scenario_suite

try:
    from app.agent.prompts.base import PROMPT_VERSION
except ImportError:
    PROMPT_VERSION = os.getenv("LAKE_AGENT_PROMPT_VERSION", "legacy")


BATTERY_VERSION = "2.0.0"
SUITE_REVIEW_ELEMENTS, CASE_REVIEW_SPECS = review_specs("meeting")


def _text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _pending(output: dict[str, Any]) -> dict[str, Any]:
    return dict(output.get("pending") or {})


def _interview_then_resume(outputs: list[dict[str, Any]], *terms: str) -> bool:
    if len(outputs) < 2:
        return False
    first = outputs[0]
    questions = _text(first.get("questions") or [])
    return (
        bool(first.get("questions"))
        and not _pending(first)
        and "skcc.x1103" in questions
        and "skcc.x1327" in questions
        and all(term in questions for term in terms)
    )


def _meeting_summary_ok(output: dict[str, Any], outputs: list[dict[str, Any]]) -> bool:
    reply = output.get("reply") or ""
    evidence = _text(output.get("evaluationEvidence") or {})
    required = ("5개", "운영 반영 보류", "StarRocks", "2026-08-22", "2026-08-25")
    people = (
        ("skcc.i2011", "이다은"),
        ("skcc.x1042", "최민서"),
        ("skcc.x1402", "최하은"),
        ("skcc.x1560", "장현우"),
        ("skcc.x1103", "이준서"),
    )
    return (
        _interview_then_resume(outputs, "PSR")
        and not _pending(output)
        and all(value in reply for value in required)
        and all(any(candidate in reply for candidate in pair) for pair in people)
        and "DL-7001" in evidence
        and "Iceberg Puffin NDV 적용 검토 노트" in evidence
        and any(token in evidence for token in ("http://", "https://", "search_web", "webContext", "error"))
    )


def _meeting_create_ok(output: dict[str, Any], outputs: list[dict[str, Any]]) -> bool:
    pending = _pending(output)
    rows = pending_items(output)
    expected = {
        "skcc.i2011": ("writer", "2026-08-22"),
        "skcc.x1402": ("StarRocks", "2026-08-25"),
        "skcc.x1103": ("검증 기준", "2026-08-28"),
    }
    if not _interview_then_resume(outputs, "RGP"):
        return False
    if pending.get("action") != "create_tickets" or len(rows) != 3 or output.get("questions"):
        return False
    if any((row.get("type") or "") not in ("Task", "Improvement", "New Feature") for row in rows):
        return False
    if any((row.get("epic") or row.get("epicKey") or "") != "DL-9200" for row in rows):
        return False
    by_owner = {str(row.get("assignee") or ""): row for row in rows}
    for owner, (subject, due) in expected.items():
        row = by_owner.get(owner) or {}
        body = str(row.get("description") or "")
        if subject.lower() not in (str(row.get("summary") or "") + body).lower():
            return False
        if str(row.get("duedate") or row.get("due") or "") != due:
            return False
        if not all(section in body for section in ("배경", "작업 범위", "완료 조건")):
            return False
        if row.get("components"):
            return False
    return True


def _meeting_comment_ok(output: dict[str, Any], outputs: list[dict[str, Any]]) -> bool:
    pending = _pending(output)
    keys = [str(key) for key in pending.get("keys") or []]
    previews = pending.get("comments") or []
    bodies = "\n".join(str(row.get("body") or "") for row in previews)
    return (
        _interview_then_resume(outputs)
        and pending.get("action") == "add_ticket_comments"
        and set(keys) == {"DL-9201", "DL-9202"}
        and not (pending.get("changes") or {})
        and len(previews) == 2
        and "5개" in bodies
        and "운영 반영" in bodies
        and "StarRocks" in bodies
        and ("skcc.x1327" in bodies or "임준서" in bodies)
        and "DL-7001" not in keys
    )


def _meeting_update_ok(output: dict[str, Any], outputs: list[dict[str, Any]]) -> bool:
    pending = _pending(output)
    changes = pending.get("changes") or {}
    expected_fields = {"summary", "priority", "duedate", "components", "labels", "description"}
    body = str(changes.get("description") or "")
    return (
        _interview_then_resume(outputs, "RGP")
        and pending.get("action") == "update_ticket"
        and pending.get("key") == "DL-9203"
        and set(changes) == expected_fields
        and changes.get("summary") == "[Catalog] Puffin NDV 검증 기준 및 결과 템플릿"
        and changes.get("priority") == "P1-Critical"
        and str(changes.get("duedate") or "") == "2026-08-29"
        and changes.get("components") == ["Catalog"]
        and changes.get("labels") == ["meeting-fixture", "puffin-ndv", "decision-20260815"]
        and all(section in body for section in ("결정 배경", "작업 범위", "검증 기준"))
        and ("skcc.x1103" in body or "이준서" in body)
        and not pending.get("comment")
    )


def _meeting_identity_ok(output: dict[str, Any], outputs: list[dict[str, Any]]) -> bool:
    pending = _pending(output)
    rows = pending_items(output)
    if not _interview_then_resume(outputs, "PSR"):
        return False
    if pending.get("action") not in ("create_ticket", "create_tickets") or len(rows) != 1:
        return False
    row = rows[0]
    text = _text(row)
    return (
        (row.get("epic") or row.get("epicKey")) == "DL-9200"
        and row.get("assignee") == "skcc.x1103"
        and str(row.get("duedate") or row.get("due") or "") == "2026-08-30"
        and "PSR" in text
        and "skcc.x1042" in text
    )


CASES = [
    (
        "MTG1",
        "회의록을 내부·외부 맥락으로 보강해 결정·담당·기한·미결을 요약",
        [
            """다음 회의록을 단순 축약하지 말고 관련 내부 이력·문서·댓글과 필요한 외부 공식 자료를 확인해 정리해줘.

## 2026-08-15 Iceberg Puffin NDV 도입 실무회의

- 참석: @이다은, {{최민서:1042}}, 하은님, 현우차장, 준서TL
- 배경: 기존 DL-7001에서 Lake 일배치 Iceberg 테이블 20개를 후보로 뽑았지만 실제 PoC와 StarRocks 소비 지원 확인은 안 됨
- 결정: 1차 PoC는 우선 5개 테이블만 대상으로 진행. StarRocks reader 검증 전에는 운영 반영 보류
- 담당·기한: @이다은은 writer PoC를 2026-08-22까지, 하은님은 StarRocks reader 검증을 2026-08-25까지, {{최민서:1042}}는 검증 기준 초안을 2026-08-28까지 작성
- 준서TL: PSR 기준을 검증 기준에 반영하고 내부 시험 결과와 외부 공식 근거를 구분해 보고할 것

PSR의 뜻은 회의록에 없고, 준서TL도 성만 생략된 호칭이야. 관련 자료를 먼저 조사하되 그래도 확정할 수 없는 것은 추측하지 말고 나에게 물어봐.""",
            "준서TL은 skcc.x1103 이준서이고, PSR은 이 회의에서만 쓰는 ‘PoC Success Review’야. 5개 표본 중 5개 모두 NDV 오차 5% 이내이고 StarRocks가 실제로 읽을 때만 통과라는 뜻이야. 이제 조사 결과와 회의 결정을 정리해줘.",
        ],
        _meeting_summary_ok,
    ),
    (
        "MTG2",
        "업무 지시가 확정된 회의록을 세 개의 실행 가능한 Task 초안으로 변환",
        [
            """아래 회의 결정은 구조와 기한까지 확정됐어. 다만 모호한 사람·용어는 자료를 찾아도 확정되지 않으면 먼저 물어봐. Epic DL-9200 아래 정확히 Task 3건의 승인 전 초안을 만들어줘.

1. @이다은 — Iceberg Puffin NDV writer PoC, 기한 2026-08-22
   - 대상 5개 테이블에서 Puffin statistics 파일 생성 여부와 NDV 값을 검증
2. 하은님 — StarRocks reader 검증, 기한 2026-08-25
   - 생성된 statistics를 reader/optimizer가 소비하는지 확인. 확인 전 운영 반영 금지
3. 준서TL — RGP 검증 기준 및 결과 템플릿, 기한 2026-08-28
   - 내부 시험 결과와 외부 공식 근거를 분리하고 절차·성능·호환성 항목을 기록
   - {{최민서:1042}}가 리뷰

각 본문은 배경, 작업 범위, 완료 조건을 갖춰. 회의록에 없는 값은 발명하지 마.""",
            "준서TL은 skcc.x1103 이준서. RGP는 이 회의에서 정의한 ‘Reader Gate Policy’로, StarRocks가 Puffin NDV를 실제 소비한 증거가 없으면 운영 반영을 금지한다는 뜻이야. 초안을 계속해줘.",
        ],
        _meeting_create_ok,
    ),
    (
        "MTG3",
        "회의 결정사항을 관련 Task 두 건에만 comment-only 승인 초안으로 알림",
        [
            """회의 결정사항을 관련 Task DL-9201, DL-9202 두 건의 댓글로 알려줘. 필드는 바꾸지 말고 댓글 승인 초안만 보여줘.

- 1차 PoC 대상은 5개 테이블
- StarRocks reader 검증 전 운영 반영 보류
- writer 결과는 @이다은, reader 결과는 하은님이 공유
- 준서TL이 최종 검토
- 배경 이력은 DL-7001이지만 그 티켓에는 댓글을 달지 않음

준서TL이 누구인지 회의록만으로 확정하지 못하면 먼저 확인해.""",
            "이 회의의 준서TL은 skcc.x1327 임준서야. 두 관련 Task의 댓글 초안을 계속해줘.",
        ],
        _meeting_comment_ok,
    ),
    (
        "MTG4",
        "회의에서 확정한 제목·필드·본문만 기존 Task 변경 승인 초안에 반영",
        [
            """회의 결정대로 DL-9203을 수정하는 승인 전 초안을 만들어줘. 댓글은 남기지 마.

- 제목: [Catalog] Puffin NDV 검증 기준 및 결과 템플릿
- priority: P1-Critical
- due: 2026-08-29
- component: Catalog
- labels 전체값: meeting-fixture, puffin-ndv, decision-20260815
- 본문 전체 교체: `결정 배경`, `작업 범위`, `검증 기준` 세 section. 5개 테이블 PoC 결과를 기록하되 StarRocks 소비 지원은 검증 전 확정하지 않는다는 내용
- 준서TL이 RGP 기준의 소유자

준서TL과 RGP가 자료 조사 후에도 확정되지 않으면 먼저 물어보고, 나머지 필드는 바꾸지 마.""",
            "준서TL은 skcc.x1103 이준서. RGP는 Reader Gate Policy이고 StarRocks 실제 소비 증거 전에는 운영 반영을 막는 기준이야. 수정 초안을 계속해줘.",
        ],
        _meeting_update_ok,
    ),
    (
        "MTG5",
        "부분 이름·호칭과 내부 약어를 조사 후 인터뷰로 확정하고 후속 Task 초안 재개",
        [
            """회의 후속 Task를 만들어줘.

- {{최민서:1042}}가 결과 템플릿 리뷰
- 준서TL이 PSR 증빙 원본 추출 담당
- 기한 2026-08-30, Epic DL-9200 아래 Task

사람과 PSR의 뜻을 내부 자료와 관련 티켓·댓글에서 먼저 찾아보고, 그래도 한 명과 한 뜻으로 확정할 수 없는 것만 물어봐. 추측한 초안은 만들지 마.""",
            "준서TL은 skcc.x1103 이준서. PSR은 PoC Success Review이고 증빙에는 테이블별 NDV 오차와 StarRocks 실제 소비 로그를 포함해야 해. Task 초안을 계속해줘.",
        ],
        _meeting_identity_ok,
    ),
]


if __name__ == "__main__":
    model, selected, requested_out = parse_scenario_args(sys.argv[1:])
    run_scenario_suite(
        suite="meeting",
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
