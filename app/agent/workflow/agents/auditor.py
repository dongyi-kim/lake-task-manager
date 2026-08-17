"""Auditor — 사용자에게 보이기 전에 초안을 **스스로 검열**한다(Self-Check 3종).

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

import json
import re as _re
from html import unescape

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.agents.work_architect import (
    _authoritative_explicit_due, _explicit_due_instruction_status,
    _current_request_boundary_text, _global_exact_due_for_roots,
    _delegates_existing_epic_choice, _explicit_parent_epic,
    _explicit_hierarchical_ordinal_contract,
    _expected_due_dates_by_root, _expected_parent_epics_by_root,
    as_bulk_items, draft_full_text,
)
from app.agent.prompts.roles import SYSTEM_AUDITOR
from app.agent.workflow.anchors import (
    is_ordinal, requested_outcome_contract, required_user_anchors,
    validate_draft_outcome_contract,
)
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import (
    AgentState, Node, last_user_text, note, request_text,
    verified_parent_epic_candidates,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean",
                     "description": "Whether every ticket key, person, date, and claim is grounded in evidence."},
        "rule_compliant": {"type": "boolean", "description": "Whether the draft follows ticket rules."},
        "answers_request": {"type": "boolean", "description": "Whether the draft covers the user's request."},
        "problems": {
            "type": "array", "maxItems": 6,
            "items": {"type": "object", "properties": {
                "index": {"type": "integer", "description": "Zero-based item index, or -1 for the whole draft."},
                "check": {"type": "string", "enum": ["grounded", "rule", "request"]},
                "message": {"type": "string", "maxLength": 220,
                            "description": "One Korean sentence describing what is wrong and why."},
                "fix": {"type": "string", "maxLength": 220,
                        "description": "A precise Korean repair instruction."}}},
            "description": "Blocking semantic problems only; empty when none. Never invent a defect.",
        },
        "summary": {"type": "string", "maxLength": 280,
                    "description": "One or two Korean sentences visible to the user."},
    },
    "required": ["grounded", "rule_compliant", "answers_request", "problems"],
}


class Auditor(StructuredAgent):
    name = Node.AUDITOR

    def node(self):
        base_run = super().node()

        def run(state):
            # ── L3b: **작은 초안은 기계 검증만으로 통과**시킨다.
            # LLM 검열이 잡는 것(근거 없는 서술·과잉 분해·요청 불일치)은 항목이 여럿이거나
            # 트리가 클 때의 병이다. 단건·자식 없음·기계검증 통과인 초안에서 LLM 검열이
            # 실제로 잡은 것이 없었고(배터리 실측), 호출 하나가 통째로 낭비였다.
            draft = state.get("draft") or {}
            items = draft.get("items") or []
            literal_recovery = draft.get("construction") == "literal_delegated"
            outcome_contract = requested_outcome_contract(state)
            small = (not outcome_contract and
                     ((len(items) == 1 and not (items[0].get("children"))
                     and (draft.get("mode") or "task") != "epic"
                     # ★ 주제 이탈·확인 필요 경고가 붙은 초안은 우회하지 않는다 —
                     #   작아 보여도 '틀린 작음'일 수 있다(실측: 뭉개진 단일 Task).
                     and not draft.get("topic_drift")
                     and "확인 필요" not in (draft.get("rationale") or ""))
                     # Literal recovery is already built and partitioned by deterministic
                     # code. A second semantic audit only rephrased editorial preferences.
                     or literal_recovery))
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
        return persona(state, SYSTEM_AUDITOR, role_id=self.name)

    def task(self, state):
        auto = _machine_check(state)
        rules = _rules_for(state)
        grounding = _audit_grounding_contract(state)
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')}"
                       for e in (state.get("evidence") or []))
        # 담당자 제안은 여기 없다 — PeopleAdvisor 와 병렬로 돌기 때문. 근거 없는 배정은
        # merge_assignments 의 코드 가드가 걸러내므로 검열 대상에서 뺀다.
        data = wrap_data(
            data_block("Deterministic Validation Results (Authoritative)", auto["text"]),
            data_block("Authoritative Request and Draft State Contract",
                       json.dumps(grounding, ensure_ascii=False, default=str)),
            data_block("Applicable Authoring Rules", rules),
            data_block("Tickets Present in Verified Research", ev))
        return f"""\
# Task

Audit the complete ticket draft before it is shown to the user.

## Constraints

- Do not repeat defects already found by deterministic validation. Inspect only semantic problems code cannot decide.
- Treat the authoritative request/draft state contract as facts. A populated field is not missing.
- `parent_action=select_existing` means select/link an existing Epic, never create a new Epic.
- Assignment is validated separately; an empty assignee is not an audit defect.
- Put only execution-blocking policy, grounding, or request-coverage failures in `problems`. Editorial suggestions for a better sentence, title, or DoD are not blocking.
- Preserve a Task/Sub-Task structure explicitly supplied or previously approved by the user.
- A Task-tier `Bug` is valid with the Korean sections `재현 경로`, `기대 동작`, and `실제 동작`; do not require generic Task background or DoD as well.
- A title need not end in a verb. An intentional top-level Task or Story without an Epic is valid.
- Reuse of one verified reference across multiple payload items is not blocking when it supports each item.
- Treat `requested_outcome_contract` as an authoritative literal result contract. For every `outcome_ref`, compare its exact instruction with the item's title, scope, and DoD. Evidence may refine implementation method or constraints, but omission or replacement of the requested action/object—including an opposite action—is a blocking `request` problem. Never repair it by inventing intent.
- Audit every child in the authoritative contract too. `applicable_outcome_refs` is explicit when the child maps to another requested outcome and otherwise inherited from its parent. A legitimate design, implementation, validation, or rollout stage need not repeat the parent's action verb; block only a child that replaces/reverses the applicable requested result or introduces an unrelated deliverable.
- Write `message`, `fix`, and `summary` in Korean.

## Original User Request

The draft must preserve this subject; subject drift is a blocking request-coverage problem.

{_current_request_boundary_text(state)}

## Complete Draft Under Audit

{draft_full_text(state.get('draft')) or '(no draft)'}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        auto = _machine_check(state)
        raw_problems = [p for p in (out.get("problems") or [])
                        if isinstance(p, dict) and p.get("message")]
        problems, advice = _partition_model_problems(state, raw_problems)
        # A schema-valid projection can still lose the model's problem array while retaining
        # a negative axis boolean. Treating the empty array as authoritative would turn an
        # explicit semantic failure into review.ok=true. Preserve every negative axis as a
        # concrete blocking problem; if a corresponding problem survived projection, do not
        # duplicate it.
        synthetic = {
            "grounded": ("grounded", "근거 충족 여부를 확인하지 못했다",
                         "티켓·사람·날짜·주장을 검증된 근거와 다시 대조하라"),
            "rule_compliant": ("rule", "티켓 규칙 준수 여부를 확인하지 못했다",
                               "타입·계층·필수 필드 규칙을 다시 검증하라"),
            "answers_request": ("request", "사용자 요청 충족 여부를 확인하지 못했다",
                                "원 요청의 산출물·행동·대상을 초안과 다시 대조하라"),
        }
        for axis, (check, message, fix) in synthetic.items():
            if out.get(axis) is False and not any(p.get("check") == check for p in problems):
                problems.append({"index": -1, "check": check,
                                 "message": message, "fix": fix})
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
        summary = _normalize_delegated_parent_summary(
            state, str(out.get("summary") or "")
        )
        if ok and advice:
            summary = (f"정책·근거 검증 통과. 편집 제안 {len(advice)}건은 "
                       "비차단 참고로 남겼다.")
        elif ok and raw_problems and not problems:
            summary = "권위 상태와 모순된 모델 지적을 제외하고 정책·근거 검증 통과."
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
    WorkArchitect 왕복이 생기고, 한도 뒤에는 정상 초안도 `review.ok=false`로 남는다. 아래는
    관찰된 취향성 오판만 좁게 비차단으로 내린다. 근거·부모 계층·요청 누락은 건드리지 않는다.
    """
    draft = state.get("draft") or {}
    items = [i for i in (draft.get("items") or []) if isinstance(i, dict)]
    has_bug = any(str(i.get("type") or "").strip().lower() == "bug" for i in items)
    req = _current_request_boundary_text(state)
    explicit_shape = (draft.get("structure_source") == "user_specified"
                      or bool(state.get("structure_ok"))
                      or "이 구조로 진행" in req
                      or any(w in req for w in ("단계별 Sub-Task", "사람 나눠서")))
    blocking, advice, seen = [], [], set()
    for problem in problems:
        if _unverified_delegated_parent_claim(state, problem):
            # A model cannot turn absence from the bounded candidate ledger into proof that
            # a Jira key does not exist, nor recommend a search hit whose Epic detail was not
            # opened. Deterministic validation emits the actionable parent error below.
            continue
        if _problem_contradicts_authoritative_state(state, problem):
            continue
        msg = str(problem.get("message") or "")
        raw_index = problem.get("index", -1)
        fingerprint = (str(problem.get("check") or ""),
                       int(raw_index) if isinstance(raw_index, int) else -1,
                       " ".join(msg.casefold().split()))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
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


def _unverified_delegated_parent_claim(state: AgentState, problem: dict) -> bool:
    """Reject model-only parent existence claims and unverified replacement keys."""
    if not _delegates_existing_epic_choice(state):
        return False
    text = " ".join(str(problem.get(key) or "") for key in ("message", "fix"))
    folded = text.casefold()
    parent_language = any(
        token in folded for token in (
            "parent", "상위", "부모", "연결", "배치", "아래", "하위",
        )
    )
    candidates = {
        str(row.get("key") or "").strip().upper()
        for row in verified_parent_epic_candidates(state)
    }
    mentioned = {
        match.group(1).upper()
        for match in _re.finditer(
            r"(?<![A-Z0-9])([A-Z][A-Z0-9]*-\d+)(?![A-Z0-9])", text, _re.I,
        )
    }
    unsupported_key = bool(mentioned - candidates)
    unsupported_existence = any(token in folded for token in (
        "존재하지", "실재하지", "찾을 수 없", "검색 결과에", "확인되지 않",
    ))
    # A terse summary can omit the word Epic while retaining the same unsupported
    # ``DL-x does not exist; use DL-y`` assertion. Ticket keys plus an existence claim are
    # enough to identify that model-only parent conclusion inside a delegated-parent audit.
    return (parent_language and unsupported_key) or (unsupported_existence and bool(mentioned))


def _normalize_delegated_parent_summary(state: AgentState, summary: str) -> str:
    """Replace epistemically invalid model wording with the candidate-ledger contract."""
    text = str(summary or "")
    if not _unverified_delegated_parent_claim(
            state, {"message": text, "fix": ""}):
        return text
    candidates = [
        str(row.get("key") or "").strip().upper()
        for row in verified_parent_epic_candidates(state)
        if str(row.get("key") or "").strip()
    ]
    if candidates:
        return (
            "상위 Epic 자동 선택은 현재 조회에서 상세 확인된 기존 Epic 후보("
            + ", ".join(candidates) + ")만 사용할 수 있다."
        )
    return (
        "현재 조회에서 상세 확인된 기존 Epic 후보가 없어 parent를 비우거나 "
        "후보 조회를 갱신해야 한다."
    )


def _request_parent_action(state: AgentState) -> str:
    """Classify only explicit Epic relationship language; never infer from a plan."""
    said = _current_request_boundary_text(state)
    # RequestArchitect already owns the exact distinction, including the important
    # "choose one; create only if none exists" fallback. Reuse it so audit cannot silently
    # reinterpret a fallback-create request as selection-only.
    try:
        from app.agent.workflow.agents.request_architect import (
            _EPIC_CREATION, _FALLBACK_CREATION, _selection_is_not_creation,
        )
        selection_only = _selection_is_not_creation(said)
        create = bool(_EPIC_CREATION.search(said) or _FALLBACK_CREATION.search(said))
    except Exception:
        selection_only = bool(_re.search(
            r"(?:Epic|에픽)[^.!?\n]{0,32}(?:골라|선택|찾아|정해|붙여|연결)",
            said, _re.I,
        ))
        create = bool(_re.search(
            r"(?:Epic|에픽)[^.!?\n]{0,24}(?:생성|만들)|"
            r"(?:생성|만들)[^.!?\n]{0,24}(?:Epic|에픽)", said, _re.I,
        ))
    if selection_only:
        return "select_existing"
    if create:
        return "create_new"
    if _re.search(r"\b[A-Z][A-Z0-9]*-\d+\b", said, _re.I) \
            and _re.search(r"Epic|에픽|상위|아래|밑에", said, _re.I):
        return "use_explicit_existing"
    return "unspecified"


def _draft_asserts_new_epic_creation(draft: dict) -> bool:
    """Detect an authored new-Epic action anywhere in the pending draft.

    ``mode=task`` only proves the payload's current root type; it does not prove that the
    title/body/rationale obeys a select-existing request.  In particular, treating that
    typed mode as compliance caused the Auditor to discard a correct finding about prose
    that promised a new Epic.  Inspect all authored draft text and children before allowing
    any request-intent contradiction filter to suppress a finding.
    """
    values = [str((draft or {}).get("rationale") or "")]

    def collect(item: dict) -> None:
        values.extend(str(item.get(key) or "") for key in ("summary", "description"))
        for child in item.get("children") or []:
            if isinstance(child, dict):
                collect(child)

    for item in (draft or {}).get("items") or []:
        if isinstance(item, dict):
            collect(item)
    text = "\n".join(values)
    # HTML block boundaries and sentence boundaries keep a nearby negation from masking a
    # different positive statement elsewhere in the draft.
    segments = _re.split(r"(?:</(?:p|li|h[1-6])>|[.!?\n])", text, flags=_re.I)
    creation = _re.compile(
        r"(?:새(?:로운)?\s*)?(?:Epic|에픽).{0,36}(?:생성|만들|create|make)"
        r"|(?:생성|만들|create|make).{0,36}(?:새(?:로운)?\s*)?(?:Epic|에픽)",
        _re.I,
    )
    negated = _re.compile(
        r"(?:생성|만들)(?:지\s*않|지\s*말|지\s*않기로|\s*안\s*|\s*금지|\s*제외|\s*보류)"
        r"|(?:do\s+not|don't|not|never|without)\s+(?:create|make)",
        _re.I,
    )
    return any(creation.search(segment) and not negated.search(segment)
               for segment in segments)


def _audit_grounding_contract(state: AgentState) -> dict:
    """Minimal typed facts that semantic audit may not reinterpret."""
    draft = state.get("draft") or {}
    rows = []

    def compact_body(value: str) -> str:
        plain = unescape(_re.sub(r"<[^>]+>", " ", str(value or "")))
        return " ".join(plain.split())[:700]

    for index, item in enumerate(draft.get("items") or []):
        if not isinstance(item, dict):
            continue
        parent_refs = [str(value) for value in (item.get("outcome_refs") or [])
                       if str(value)]
        children = []
        for child_index, child in enumerate(item.get("children") or []):
            if not isinstance(child, dict):
                continue
            explicit_refs = [str(value) for value in (child.get("outcome_refs") or [])
                             if str(value)]
            children.append({
                "index": child_index,
                "type": str(child.get("type") or child.get("issue_type") or "Sub-Task"),
                "summary": str(child.get("summary") or ""),
                "scope_and_dod": compact_body(child.get("description") or ""),
                "outcome_refs": explicit_refs,
                "applicable_outcome_refs": explicit_refs or parent_refs,
                "outcome_binding_source": ("explicit" if explicit_refs
                                           else "inherited_from_parent"),
            })
        rows.append({
            "index": index,
            "type": str(item.get("type") or item.get("issue_type") or ""),
            "summary": str(item.get("summary") or ""),
            "epic": str(item.get("epic") or ""),
            "parent": str(item.get("parent") or ""),
            "duedate": str(item.get("duedate") or ""),
            "assignee": str(item.get("assignee") or ""),
            "outcome_refs": parent_refs,
            "child_count": len(children),
            "children": children,
        })
    typed_epic = (str(draft.get("mode") or "task").casefold() == "epic"
                  or any(row["type"].casefold() == "epic" for row in rows))
    textual_epic = _draft_asserts_new_epic_creation(draft)
    return {
        "parent_action": _request_parent_action(state),
        "draft_mode": str(draft.get("mode") or "task"),
        "draft_creates_epic": typed_epic or textual_epic,
        "draft_asserts_new_epic_creation": textual_epic,
        "requested_outcome_contract": requested_outcome_contract(state),
        "draft_outcome_contract_id": str(draft.get("outcome_contract_id") or ""),
        "items": rows,
    }


def _problem_contradicts_authoritative_state(state: AgentState, problem: dict) -> bool:
    """Drop a semantic finding only when request *and complete draft* disprove it.

    Request intent is an audit rule, never evidence that the draft complied with the rule.
    """
    facts = _audit_grounding_contract(state)
    text = " ".join(str(problem.get(key) or "") for key in ("message", "fix"))
    folded = " ".join(text.casefold().split())
    if (facts["parent_action"] == "select_existing"
            and not facts["draft_creates_epic"]
            and ("epic" in folded or "에픽" in folded)
            and any(word in folded for word in ("생성", "만들", "create"))):
        return True

    rows = facts["items"]
    index = problem.get("index", -1)
    has_exact_scope = isinstance(index, int) and 0 <= index < len(rows)
    scoped = [rows[index]] if has_exact_scope else rows

    def populated(predicate) -> bool:
        """A global missing claim is disproved only when every root has the field."""
        values = [bool(predicate(row)) for row in scoped]
        return bool(values) and (values[0] if has_exact_scope else all(values))

    missing_claim = any(word in folded for word in (
        "없", "누락", "비어", "명시되어 있지", "설정되지", "not present", "missing",
    ))
    if missing_claim and any(word in folded for word in ("마감", "기한", "due")) \
            and populated(lambda row: row.get("duedate")):
        return True
    if missing_claim and ("epic" in folded or "에픽" in folded or "상위" in folded) \
            and populated(lambda row: row.get("epic") or row.get("parent")):
        return True
    if missing_claim and any(word in folded for word in ("제목", "summary", "요약")) \
            and populated(lambda row: row.get("summary")):
        return True
    return False


_ISSUE_TYPE_TOKEN = _re.compile(
    r"(?<![A-Za-z가-힣])(Bug|버그|Story|스토리|Feature|피처|Improvement|임프로브먼트)"
    r"(?=$|[^A-Za-z가-힣]|(?:를|을|로|은|는|와|과)(?=\s|[,.;:!?]|$))", _re.I,
)
_ISSUE_TYPE_CANONICAL = {
    "bug": "Bug", "버그": "Bug", "story": "Story", "스토리": "Story",
    "feature": "Feature", "피처": "Feature",
    "improvement": "Improvement", "임프로브먼트": "Improvement",
}
_CREATE_ACTION = _re.compile(r"만들|생성|등록|올려", _re.I)


def _explicit_issue_type_mentions(text: str) -> list[dict]:
    """Return issue types explicitly participating in a create instruction.

    A lone type is authoritative only when its suffix directly forms a create phrase. In a
    multi-type list (``Bug 1건과 Story 1건 만들어``), every type token in that create clause
    is retained so the caller can map them per root instead of applying the first globally.
    """
    source = str(text or "")
    all_mentions = [{
        "type": _ISSUE_TYPE_CANONICAL[match.group(1).casefold()],
        "start": match.start(), "end": match.end(),
    } for match in _ISSUE_TYPE_TOKEN.finditer(source)]
    if not all_mentions:
        return []
    unique = {row["type"] for row in all_mentions}
    if len(unique) > 1 and _CREATE_ACTION.search(source):
        return all_mentions
    direct = []
    for row in all_mentions:
        tail = source[row["end"]:row["end"] + 40]
        if _re.match(
            r"\s*(?:를|을|로)?\s*(?:\d+\s*건|한\s*건|두\s*건|하나)?\s*"
            r"(?:만\s*)?(?:만들|생성|등록|올려)", tail, _re.I,
        ):
            direct.append(row)
    return direct


def _type_subject_terms(value: str) -> set[str]:
    stop = {
        "건과", "건와", "그리고", "각각", "만들고", "만들어", "만들어줘",
        "생성", "생성해", "등록", "올려", "작업", "티켓", "task",
        "bug", "story", "feature", "improvement",
    }
    return {token.casefold() for token in _re.findall(
        r"[A-Za-z][A-Za-z0-9_.-]{1,}|[가-힣]{2,}", str(value or ""),
    ) if token.casefold() not in stop and not token.isdigit()}


def _visible_multi_type_mapping(material: str, mentions: list[dict],
                                roots: list[dict]) -> dict[int, str]:
    """Map postfixed type clauses to visible root summaries only on a literal bijection."""
    if len(mentions) != len(roots) or len(mentions) < 2:
        return {}
    subjects = []
    prior_end = 0
    for mention in mentions:
        subjects.append(_type_subject_terms(material[prior_end:mention["start"]]))
        prior_end = mention["end"]
    root_terms = [_type_subject_terms(str(row.get("summary") or "")) for row in roots]
    mapping: dict[int, str] = {}
    used: set[int] = set()
    for index, terms in enumerate(subjects):
        siblings = set().union(*(other for pos, other in enumerate(subjects) if pos != index))
        distinctive = terms - siblings
        if not distinctive:
            return {}
        candidates = [root_index for root_index, values in enumerate(root_terms)
                      if distinctive <= values]
        if len(candidates) != 1 or candidates[0] in used:
            return {}
        used.add(candidates[0])
        mapping[candidates[0]] = mentions[index]["type"]
    return mapping if len(mapping) == len(roots) else {}


def _expected_issue_types_by_root(state: AgentState, roots: list[dict]) -> dict[int, str]:
    """Resolve exact issue types without turning a multi-type request into a global type."""
    material = _current_request_boundary_text(state)
    mentions = _explicit_issue_type_mentions(material)
    unique = {row["type"] for row in mentions}
    if len(unique) == 1:
        expected = next(iter(unique))
        return {index: expected for index in range(len(roots))}
    if len(unique) < 2:
        return {}

    contract = requested_outcome_contract(state)
    outcome_types = {}
    for outcome in contract.get("outcomes") or []:
        values = {row["type"] for row in _explicit_issue_type_mentions(
            str(outcome.get("instruction") or ""))}
        if len(values) == 1:
            outcome_types[str(outcome.get("id") or "")] = next(iter(values))
    mapped = {}
    for index, root in enumerate(roots):
        values = {outcome_types[ref] for ref in (
            str(value) for value in (root.get("outcome_refs") or [])
        ) if ref in outcome_types}
        if len(values) == 1:
            mapped[index] = next(iter(values))
    if len(mapped) == len(roots):
        return mapped
    # Outcome ids may be unavailable in legacy/direct drafts. Fall back only when visible
    # root subjects establish the same one-to-one literal mapping.
    return _visible_multi_type_mapping(material, mentions, roots)


def _deterministic_request_field_errors(state: AgentState, roots: list[dict]) -> list[dict]:
    """Check exact user-owned fields that semantic review must never reinterpret."""
    errors: list[dict] = []

    ordinals = [value for value in required_user_anchors(state) if is_ordinal(value)]
    if len(roots) == 1 and len(ordinals) == 1:
        expected = ordinals[0]
        expected_number = _re.match(r"(\d+)", expected).group(1)
        rows = [roots[0], *[
            child for child in (roots[0].get("children") or []) if isinstance(child, dict)
        ]]
        bare = _re.compile(
            r"(?<![0-9A-Za-z가-힣_])차(?=\s|[—–\-:·,.;!?()\[\]{}]|$)", _re.I)
        for index, row in enumerate(rows):
            visible = unescape(_re.sub(
                r"<[^>]+>", " ",
                f"{row.get('summary') or ''} {row.get('description') or ''}",
            ))
            explicit_numbers = set(_re.findall(r"(?<!\d)(\d{1,3})\s*차", visible))
            conflicts = sorted(number for number in explicit_numbers
                               if number != expected_number)
            if bare.search(visible):
                errors.append({
                    "index": index,
                    "field": "ordinal",
                    "message": (f"사용자 지정 범위 {expected}에서 숫자 없는 bare '차'가 "
                                "root/child 표시에 사용됐다"),
                })
            elif conflicts:
                rendered = ", ".join(f"{number}차" for number in conflicts)
                errors.append({
                    "index": index,
                    "field": "ordinal",
                    "message": (f"사용자 지정 범위 {expected}와 충돌하는 {rendered}가 "
                                "root/child 표시에 사용됐다"),
                })
            elif index == 0 and expected_number not in explicit_numbers:
                errors.append({
                    "index": 0,
                    "field": "ordinal",
                    "message": f"root 표시에 사용자 지정 범위 {expected}가 누락됐다",
                })

    hierarchy_ordinals = _explicit_hierarchical_ordinal_contract(state)
    if len(roots) == 1 and hierarchy_ordinals:
        hierarchy_rows = [("root", 0, roots[0], hierarchy_ordinals["root"])]
        hierarchy_rows.extend(
            ("child", index, child, hierarchy_ordinals["child"])
            for index, child in enumerate((roots[0].get("children") or []), start=1)
            if isinstance(child, dict)
        )
        bare = _re.compile(
            r"(?<![0-9A-Za-z가-힣_])차(?=\s|[—–\-:·,.;!?()\[\]{}]|$)", _re.I)
        for tier, index, row, expected in hierarchy_rows:
            # The summary is the issue's visible phase ownership label. Descriptions may
            # legitimately mention both phases as background/dependencies, so they are not
            # used to infer ownership here.
            summary = unescape(_re.sub(r"<[^>]+>", " ", str(row.get("summary") or "")))
            explicit = {
                f"{int(number)}차"
                for number in _re.findall(r"(?<!\d)(\d{1,3})\s*차", summary)
            }
            conflicts = sorted(value for value in explicit if value != expected)
            if bare.search(summary):
                message = f"{tier} 표시에 숫자 없는 bare '차'가 사용됐다"
            elif expected not in explicit or conflicts:
                actual = ", ".join(sorted(explicit)) or "비어 있음"
                message = (f"{tier} 표시는 사용자 지정 범위 {expected}여야 하나 "
                           f"초안은 {actual}")
            else:
                continue
            errors.append({"index": index, "field": "ordinal", "message": message})

    for index, expected_type in _expected_issue_types_by_root(state, roots).items():
        row = roots[index]
        actual = str(row.get("type") or row.get("issue_type") or "").strip()
        if actual.casefold() != expected_type.casefold():
            errors.append({
                "index": index, "field": "type",
                "message": f"사용자가 {expected_type}를 지정했으나 초안 타입은 {actual or '비어 있음'}",
            })

    explicit_parents = _expected_parent_epics_by_root(state, roots)
    explicit_parent = _explicit_parent_epic(state)
    if explicit_parents:
        for index, expected_parent in explicit_parents.items():
            row = roots[index]
            actual = str(row.get("epic") or row.get("parent") or "").strip().upper()
            if actual != expected_parent.upper():
                errors.append({
                    "index": index, "field": "parent",
                    "message": (f"해당 요청 결과의 상위 Epic은 {expected_parent}이나 "
                                f"초안은 {actual or '비어 있음'}"),
                })
    elif explicit_parent:
        for index, row in enumerate(roots):
            actual = str(row.get("epic") or row.get("parent") or "").strip().upper()
            if actual != explicit_parent.upper():
                errors.append({
                    "index": index, "field": "parent",
                    "message": f"사용자 지정 상위 Epic은 {explicit_parent}이나 초안은 {actual or '비어 있음'}",
                })
    elif _delegates_existing_epic_choice(state) and "materialized_ticket_sources" in state:
        candidates = {
            str(row.get("key") or "").strip().upper()
            for row in verified_parent_epic_candidates(state)
            if str(row.get("key") or "").strip()
        }
        for index, row in enumerate(roots):
            actual = str(row.get("epic") or row.get("parent") or "").strip().upper()
            invalid = (actual not in candidates if candidates else bool(actual))
            if invalid:
                errors.append({
                    "index": index, "field": "parent",
                    "message": (
                        f"초안 parent {actual}은 현재 조회에서 상세 확인된 기존 Epic "
                        f"후보({', '.join(sorted(candidates))})에 포함되지 않는다"
                        if candidates else
                        f"현재 조회에서 상세 확인된 기존 Epic 후보가 없어 초안 parent "
                        f"{actual}의 연결 근거를 확인할 수 없다"
                    ),
                })
    return errors


def _machine_check(state: AgentState) -> dict:
    """`domain/bulk.validate_bulk` — 화면의 Bulk 생성과 **같은 규칙**. LLM 을 거치지 않는다."""
    draft = state.get("draft") or {}
    items = as_bulk_items(draft)
    contract_errors = validate_draft_outcome_contract(state, draft)
    due_errors = []
    roots = [item for item in (draft.get("items") or []) if isinstance(item, dict)]
    field_errors = _deterministic_request_field_errors(state, roots)
    per_outcome_due = _expected_due_dates_by_root(state, roots)
    due_status, due_literal = _explicit_due_instruction_status(state)
    expected_due = _authoritative_explicit_due(state)
    if per_outcome_due:
        for index, expected in per_outcome_due.items():
            actual_due = str(roots[index].get("duedate") or "").strip()
            if actual_due != expected:
                due_errors.append({
                    "index": index, "field": "duedate",
                    "message": (f"해당 요청 결과의 마감일은 {expected}이나 초안은 "
                                f"{actual_due or '비어 있음'}"),
                })
    elif due_status in {"invalid", "ambiguous"}:
        due_errors.append({
            "index": 0 if roots else -1,
            "field": "duedate",
            "message": (
                (f"사용자 지정 마감일 {due_literal}은 유효하지 않다"
                 if due_status == "invalid" and due_literal
                 else "사용자가 서로 다른 마감일을 지정해 하나로 확정할 수 없다")
                + " — 유효한 단일 날짜를 확인하기 전에는 승인할 수 없다"
            ),
        })
    elif due_status == "clear" and any(str(row.get("duedate") or "").strip() for row in roots):
        due_errors.append({
            "index": 0, "field": "duedate",
            "message": "사용자가 마감일 제거를 요청했으나 초안에 날짜가 남아 있다",
        })
    elif expected_due and (len(roots) == 1
                           or _global_exact_due_for_roots(state, len(roots))):
        for index, root in enumerate(roots):
            actual_due = str(root.get("duedate") or "").strip()
            if actual_due == expected_due:
                continue
            due_errors.append({
                "index": index,
                "field": "duedate",
                "message": (f"사용자 지정 마감일은 {expected_due}이나 초안은 "
                            f"{actual_due or '비어 있음'} — exact date를 그대로 보존해야 한다"),
            })
    if not items:
        return {"ok": False, "errors": contract_errors + due_errors + field_errors, "warnings": [],
                "text": "초안이 비어 있다."}
    # Epic 은 Bulk 규칙(validate_bulk)의 대상이 아니다 — 요약만 확인하고 통과.
    # (Epic Link·타입·SP 규칙은 전부 자식 티켓 이야기다.)
    if (draft.get("mode") or "task") == "epic":
        ok = bool((items[0].get("summary") or "").strip())
        errors = ([] if ok else [{"index": 0, "field": "summary",
                                  "message": "Epic 요약이 비었다"}]) \
            + contract_errors + due_errors + field_errors
        return {"ok": ok and not errors, "errors": errors,
                "warnings": [], "text": "Epic 초안 — 기계 검증 대상 아님(요약 확인만)."}
    try:
        from app.agent.tools._ctx import client
        from app.domain.bulk import validate_bulk
        r = validate_bulk(draft.get("mode") or "task", items, client().bulk_lookup())
    except Exception as e:
        return {"ok": False,
                "errors": ([{"message": str(e)[:200]}] + contract_errors
                           + due_errors + field_errors),
                "warnings": [],
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

    errors = list(r.get("errors") or []) + contract_errors + due_errors + field_errors
    lines = [f"- [{e.get('index')}] {e.get('field')}: {e.get('message')}"
             for e in errors]
    lines += [f"- (경고) [{w.get('index')}] {w.get('message')}" for w in warnings]
    return {"ok": bool(r.get("ok")) and not errors, "errors": errors,
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
