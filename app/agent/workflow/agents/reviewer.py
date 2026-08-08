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

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.agents.refiner import as_bulk_items, draft_text
from app.agent.prompts.roles import SYSTEM_REVIEWER
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import AgentState, Node, last_user_text, note

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
                     and (draft.get("mode") or "task") != "epic")
            if small:
                auto = _machine_check(state)
                if auto["ok"]:
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
- 과잉 분해(아직 방식이 안 정해졌는데 실행 단위로 쪼갠 것)도 문제로 잡는다.

## 사용자의 원래 요청
{last_user_text(state)}

## 검열 대상 초안
{draft_text(state.get('draft')) or '(초안 없음)'}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        auto = _machine_check(state)
        problems = [p for p in (out.get("problems") or []) if isinstance(p, dict) and p.get("message")]
        checks = {"grounded": bool(out.get("grounded")),
                  "rule_compliant": bool(out.get("rule_compliant")),
                  "answers_request": bool(out.get("answers_request"))}
        # 기계 판정이 이긴다 — 모델이 "문제없다"고 해도 validate_bulk 가 막으면 막힌 것이다.
        ok = auto["ok"] and all(checks.values()) and not problems
        review = {"ok": ok, "checks": checks, "problems": problems,
                  "errors": auto["errors"], "warnings": auto["warnings"],
                  "summary": out.get("summary") or ""}
        failed = [k for k, v in checks.items() if not v]
        return {"review": review, "revisions": (state.get("revisions") or 0) + 1,
                "trace": note(state, self.name,
                              "통과" if ok else
                              f"보류 — 자동 {len(auto['errors'])}건 · 판단 {len(problems)}건"
                              + (f" ({', '.join(failed)})" if failed else ""))}


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
    lines = [f"- [{e.get('index')}] {e.get('field')}: {e.get('message')}"
             for e in (r.get("errors") or [])]
    lines += [f"- (경고) [{w.get('index')}] {w.get('message')}" for w in (r.get("warnings") or [])]
    return {"ok": bool(r.get("ok")), "errors": r.get("errors") or [],
            "warnings": r.get("warnings") or [],
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
