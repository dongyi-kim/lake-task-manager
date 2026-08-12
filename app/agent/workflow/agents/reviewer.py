"""Reviewer — 사용자에게 보이기 전에 초안을 **스스로 검열**한다(Self-Check 3종).

여기서 걸러야 할 것은 두 종류이고, 성격이 완전히 다르다.

  · **기계가 판정할 수 있는 것** — 없는 부모 키, 허용되지 않은 타입, 빠진 필수값.
    이건 LLM 에게 묻지 않는다. `domain/bulk.validate_bulk` 가 화면의 Bulk 생성과 **같은 규칙**으로
    판정한다. 규칙이 두 벌이 되면 반드시 갈라지고, 그때 더 관대한 쪽이 사고를 낸다.
  · **판단이 필요한 것** — 근거 없는 서술, 요청과 어긋난 분해, 과잉 분해.
    이건 규칙으로 못 잡는다. 그래서 모델에게 **세 가지를 각각** 자문하게 한다.

    ① 근거 있는가 — 초안에 적힌 티켓 키·사람·날짜가 조사에서 실제로 나온 것인가
    ② 규칙에 맞는가 — 티켓 작성 규칙(SP·Epic Link·컴포넌트)을 어기지 않았나
    ③ 요청에 답하는가 — 사용자가 부탁한 일을 실제로 담고 있나

**기계 판정이 항상 이긴다.** 모델이 "문제없다"고 해도 `validate_bulk` 가 막으면 막힌 것이다.
반대로 모델이 문제를 찾으면 그건 사람에게 보여 준다 — 기계가 못 보는 종류라서다.
"""

from __future__ import annotations

import re as _re

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.agents.refiner import as_bulk_items, draft_full_text
from app.agent.prompts.roles import SYSTEM_REVIEWER
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import AgentState, Node, note, request_text

SCHEMA = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean",
                     "description": "①초안의 티켓 키·사람·날짜가 조사 결과에 실제로 있는가"},
        "rule_compliant": {"type": "boolean", "description": "②티켓 작성 규칙을 지켰는가"},
        "answers_request": {"type": "boolean", "description": "③사용자가 부탁한 일을 담고 있는가"},
        "problems": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "index": {"type": "integer", "description": "문제가 있는 항목 번호. 전체면 -1"},
                "check": {"type": "string", "enum": ["grounded", "rule", "request"]},
                "message": {"type": "string", "description": "무엇이 왜 문제인지 한 문장"},
                "fix": {"type": "string", "description": "어떻게 고치면 되는지"}}},
            "description": "찾은 문제. 없으면 빈 배열. 없는 문제를 만들어 내지 마라",
        },
        "summary": {"type": "string", "description": "검토 결과 1~2문장. 사용자에게 보인다"},
    },
    "required": ["grounded", "rule_compliant", "answers_request", "problems"],
}


class Reviewer(StructuredAgent):
    name = Node.REVIEWER
    temperature = 0.0          # 검열은 흔들리면 안 된다

    def node(self):
        base_run = super().node()

        def run(state):
            # ── L3b: **작은 초안은 기계 검증만으로 통과**시킨다.
            # LLM 검열이 잡는 것(근거 없는 서술·과잉 분해·요청 불일치)은 항목이 여럿이거나
            # 트리가 클 때의 병이다. 단건·자식 없음·기계검증 통과인 초안에서 LLM 검열이
            # 실제로 잡은 것이 없었고(배터리 실측), 호출 하나가 통째로 낭비였다.
            draft = state.get("draft") or {}
            items = draft.get("items") or []
            small = (len(items) == 1 and not (items[0].get("children"))
                     and (draft.get("mode") or "task") != "epic"
                     # ★ 주제 이탈·확인 필요 경고가 붙은 초안은 우회하지 않는다 —
                     #   작아 보여도 '틀린 작음'일 수 있다(실측: 뭉개진 단일 Task).
                     and not draft.get("topic_drift")
                     and "확인 필요" not in (draft.get("rationale") or ""))
            if small:
                auto = _machine_check(state)
                # 완료 조건(DoD)이 없으면 작아도 통과시키지 않는다 — 우회하면 apply 의
                # 재작성 요구가 아예 안 걸린다(실측: 단건 초안이 DoD 없이 카드까지 갔다).
                blocking_content = any(
                    "완료 조건" in str(w.get("message") or "")
                    or "Bug 필수 섹션" in str(w.get("message") or "")
                    for w in auto["warnings"])
                if auto["ok"] and not blocking_content:
                    return {"review": {"ok": True, "checks": {}, "problems": [],
                                       "errors": [], "warnings": auto["warnings"],
                                       "summary": "단건 초안 — 기계 검증 통과(자동)"},
                            "revisions": (state.get("revisions") or 0) + 1,
                            "trace": note(state, self.name, "통과(기계 검증만 — 단건 초안)")}
            return base_run(state)

        return run

    def system(self, state):
        return persona(state, SYSTEM_REVIEWER)

    def task(self, state):
        auto = _machine_check(state)
        rules = _rules_for(state)
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')}"
                       for e in (state.get("evidence") or []))
        # 담당자 제안은 여기 없다 — Assigner 와 병렬로 돌기 때문. 근거 없는 배정은
        # merge_assignments 의 코드 가드가 걸러내므로 검열 대상에서 뺀다.
        data = wrap_data(
            data_block("자동 검증 결과 (기계 판정 — 이건 이미 확정이다)", auto["text"]),
            data_block("적용되는 작성 규칙", rules),
            data_block("조사에서 실제로 나온 티켓", ev))
        return f"""\
# 명령서
아래 티켓 초안을 사용자에게 보이기 전에 검열하라.

## 제약조건
- 자동 검증이 이미 잡은 것은 다시 적지 마라. 너는 **기계가 못 잡는 것**을 본다.
- 담당자 배정은 별도 절차가 검증한다 — 담당자가 비어 있어도 문제 삼지 마라.
- `problems`는 실행을 막아야 하는 정책·근거·요청 불일치만 쓴다. 더 좋은 문장·제목·DoD를
  제안하는 편집 의견은 blocking problem이 아니다.
- 사용자가 직접 지정했거나 이전 턴에 승인한 Task/Sub-Task 구조를 과잉 분해라고 뒤집지 마라.
- `Bug`는 Task 본문과 다르다. 재현 경로·기대 동작·실제 동작이 있으면 배경/DoD가 없다는
  이유로 실패시키지 마라.
- 제목이 동사로 끝나야 한다는 규칙은 없다. Epic 없는 최상위 Task/Story도 유효하다.
- 동일한 검증된 참고가 여러 payload item에 들어간 것은 실행 차단 사유가 아니다.

## 사용자의 원래 요청 (초안의 주제는 이 문장이어야 한다 — 다른 주제로 흘렀으면 problems 로)
{request_text(state)}

## 검열 대상 초안 (전문)
{draft_full_text(state.get('draft')) or '(초안 없음)'}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        auto = _machine_check(state)
        raw_problems = [p for p in (out.get("problems") or [])
                        if isinstance(p, dict) and p.get("message")]
        problems, advice = _partition_model_problems(state, raw_problems)
        # boolean과 problems가 서로 어긋나는 모델 출력이 있다. 실행 차단은 구체적인
        # problem으로 설명 가능해야 하므로, 해당 축의 blocking problem 유무를 기준으로
        # 정규화한다. 기계 오류는 아래 auto["ok"]가 별도로 이긴다.
        checks = {
            "grounded": not any(p.get("check") == "grounded" for p in problems),
            "rule_compliant": not any(p.get("check") == "rule" for p in problems),
            "answers_request": not any(p.get("check") == "request" for p in problems),
        }
        # 완료 조건(DoD) 누락은 **한 번은 되돌려 보낸다** — 언제 끝난 것인지 못 박지 않은
        # 티켓은 나중에 아무도 닫지 못한다(실측: 배경·작업 범위만 쓰고 승인 카드까지 갔다).
        # 재작성 한도는 그래프가 쥐고 있으므로 무한 왕복은 나지 않는다.
        if (state.get("revisions") or 0) < 1:
            for w in auto["warnings"]:
                if ("완료 조건" in str(w.get("message") or "")
                        or "Bug 필수 섹션" in str(w.get("message") or "")):
                    problems.append({"index": w.get("index", -1),
                                     "message": w["message"],
                                     "fix": ("Bug 본문에 재현 경로·기대 동작·실제 동작을 "
                                             "모두 적어라" if "Bug 필수" in w["message"] else
                                             "본문에 '완료 조건 (DoD)' 섹션을 넣고 "
                                             "검증 가능한 불릿 2~5개를 적어라")})
        # 기계 판정이 이긴다 — 모델이 "문제없다"고 해도 validate_bulk 가 막으면 막힌 것이다.
        ok = auto["ok"] and all(checks.values()) and not problems
        advisory_warnings = [{"index": p.get("index", -1),
                              "message": "품질 참고(비차단): " + str(p.get("message") or "")}
                             for p in advice]
        summary = str(out.get("summary") or "")
        if ok and advice:
            summary = (f"정책·근거 검증 통과. 편집 제안 {len(advice)}건은 "
                       "비차단 참고로 남겼다.")
        review = {"ok": ok, "checks": checks, "problems": problems,
                  "errors": auto["errors"],
                  "warnings": auto["warnings"] + advisory_warnings,
                  "summary": summary}
        failed = [k for k, v in checks.items() if not v]
        return {"review": review, "revisions": (state.get("revisions") or 0) + 1,
                "trace": note(state, self.name,
                              "통과" if ok else
                              f"보류 — 자동 {len(auto['errors'])}건 · 판단 {len(problems)}건"
                              + (f" ({', '.join(failed)})" if failed else ""))}


def _partition_model_problems(state: AgentState, problems: list) -> tuple[list, list]:
    """정책 차단과 편집 조언을 나눈다.

    LLM Auditor가 실제 Jira/LTM 제약이 아닌 문체 취향을 `problems`로 올리면 불필요한
    Refiner 왕복이 생기고, 한도 뒤에는 정상 초안도 `review.ok=false`로 남는다. 아래는
    관찰된 취향성 오판만 좁게 비차단으로 내린다. 근거·부모 계층·요청 누락은 건드리지 않는다.
    """
    draft = state.get("draft") or {}
    items = [i for i in (draft.get("items") or []) if isinstance(i, dict)]
    has_bug = any(str(i.get("type") or "").strip().lower() == "bug" for i in items)
    req = request_text(state)
    explicit_shape = (draft.get("structure_source") == "user_specified"
                      or bool(state.get("structure_ok"))
                      or "이 구조로 진행" in req
                      or any(w in req for w in ("단계별 Sub-Task", "사람 나눠서")))
    blocking, advice = [], []
    for problem in problems:
        msg = str(problem.get("message") or "")
        advisory = False
        if has_bug and any(w in msg for w in ("배경", "완료 조건", "DoD")):
            advisory = True
        elif any(w in msg for w in ("담당자", "사번", "사용자")) and any(
                w in msg for w in ("존재하지", "확인되지", "실재하지", "찾을 수 없")):
            # 담당 사용자 실재 여부는 merge_assignments 뒤 코드가 bulk lookup으로 확정한다.
            advisory = True
        elif explicit_shape and any(w in msg for w in ("과잉 분해", "불필요하게 나뉘")):
            advisory = True
        elif "참고 섹션" in msg and "중복" in msg:
            advisory = True
        elif "Sub-Task" in msg and "부모" in msg and "중복" in msg:
            advisory = True
        elif any(w in msg for w in (
                "동사로 끝", "제목이 명확하지", "제목이 구체적이지",
                "완료 조건이 명확하지", "완료 조건이 구체적이지", "판정 가능하지",
                "Epic 배치가 명시되지", "Epic 배치가 누락")):
            advisory = True
        (advice if advisory else blocking).append(problem)
    return blocking, advice


def _machine_check(state: AgentState) -> dict:
    """`domain/bulk.validate_bulk` — 화면의 Bulk 생성과 **같은 규칙**. LLM 을 거치지 않는다."""
    draft = state.get("draft") or {}
    items = as_bulk_items(draft)
    if not items:
        return {"ok": False, "errors": [], "warnings": [], "text": "초안이 비어 있다."}
    # Epic 은 Bulk 규칙(validate_bulk)의 대상이 아니다 — 요약만 확인하고 통과.
    # (Epic Link·타입·SP 규칙은 전부 자식 티켓 이야기다.)
    if (draft.get("mode") or "task") == "epic":
        ok = bool((items[0].get("summary") or "").strip())
        return {"ok": ok, "errors": [] if ok else [{"index": 0, "field": "summary",
                                                    "message": "Epic 요약이 비었다"}],
                "warnings": [], "text": "Epic 초안 — 기계 검증 대상 아님(요약 확인만)."}
    try:
        from app.agent.tools._ctx import client
        from app.domain.bulk import validate_bulk
        r = validate_bulk(draft.get("mode") or "task", items, client().bulk_lookup())
    except Exception as e:
        return {"ok": False, "errors": [{"message": str(e)[:200]}], "warnings": [],
                "text": f"검증을 수행하지 못했다: {str(e)[:200]}"}
    warnings = list(r.get("warnings") or [])
    # ★ 본문 접지 — 챗 답변에만 걸던 grounding 을 **티켓 본문에도** 건다. 없는 키·틀린
    #   제목이 티켓에 박제되면 동적 RAG 가 그 날조를 다음 조사에서 다시 수확한다(실측:
    #   본문의 날조는 어떤 검사도 안 거치고 통과했다). 실패는 경고로 — 판단은 사람이.
    try:
        from app.agent.workflow import grounding
        body = " ".join(str(i.get("description") or "") + " " + str(i.get("summary") or "")
                        for i in items)
        g = grounding.check(body)
        if not g.get("ok"):
            for k in (g.get("fake_keys") or [])[:5]:
                warnings.append({"index": -1, "message": f"본문의 {k} 는 실재하지 않는 티켓이다"})
            for k, t in list((g.get("wrong_titles") or {}).items())[:3]:
                warnings.append({"index": -1, "message": f"본문의 {k} 제목이 실제와 다르다: {t}"})
    except Exception:
        pass
    # ★ 본문 골격 — 완료 조건(DoD)이 없는 티켓은 "언제 끝난 것인지" 아무도 모른다.
    #   knowledge/07 이 정한 4섹션 중 이것만 유독 잘 빠진다(실측: 배경·작업 범위만 쓰고
    #   DoD 없이 승인 카드까지 갔다). 경고로 올려 재작성 루프가 채우게 한다.
    for i, it in enumerate(items):
        desc = str(it.get("description") or "")
        if not desc.strip():
            continue
        if str(it.get("type") or "").strip().lower() == "bug":
            missing = [name for name in ("재현 경로", "기대 동작", "실제 동작")
                       if name not in desc]
            if missing:
                warnings.append({"index": i, "message":
                                 "Bug 필수 섹션이 없다: " + ", ".join(missing)})
            continue
        if not _re.search(r"완료\s*조건|DoD|Definition of Done", desc, _re.I):
            warnings.append({"index": i, "message":
                             "완료 조건(DoD)이 없다 — 무엇을 만족하면 끝인지 적어야 한다"})

    lines = [f"- [{e.get('index')}] {e.get('field')}: {e.get('message')}"
             for e in (r.get("errors") or [])]
    lines += [f"- (경고) [{w.get('index')}] {w.get('message')}" for w in warnings]
    return {"ok": bool(r.get("ok")), "errors": r.get("errors") or [],
            "warnings": warnings,
            "text": "\n".join(lines) if lines else "통과 — 형식·실값 오류 없음"}


def _rules_for(state: AgentState) -> str:
    """초안에 관련된 규칙만 끌어온다. 규칙 전문을 프롬프트에 붓지 않는다(정적 RAG)."""
    try:
        from app.agent.retrieval import static_index
        q = "티켓 작성 규칙 " + " ".join(
            str(i.get("type") or "") for i in (state.get("draft") or {}).get("items") or [])
        return "\n\n".join(h["text"] for h in static_index.search(q, k=3))
    except Exception:
        return ""
