"""Typed, bounded authority for multi-turn interview continuations.

Conversation messages remain available as history and evidence, but they are not an execution
contract.  This module captures only the original request identity and user-authored answers to
typed questions/refinements.  New topics receive an empty contract at the Session boundary.
"""

from __future__ import annotations

import copy
import re

from app.agent.workflow.contracts import ContinuationContract


# Korean case particles are Unicode ``\w`` characters, so ``\b`` does not match in common
# forms such as ``DL-9201과``.  Bound the ASCII Jira token itself instead.
_KEY_RE = re.compile(
    r"(?<![A-Z0-9])[A-Z][A-Z0-9]{1,9}-\d+(?![A-Z0-9-])", re.I,
)
_USERNAME_RE = re.compile(
    r"(?<![A-Z0-9_.])skcc\.[a-z]\d{2,8}(?![A-Z0-9_.])", re.I,
)
_UNASSIGNED_RE = re.compile(
    r"미\s*할당|할당\s*없|담당(?:자)?\s*없|정하지\s*못|정해지지\s*않|"
    r"\bunassigned\b|\bnone\b",
    re.I,
)
_TOP_LEVEL_PARENT_RE = re.compile(
    r"최상위(?:\s*Task)?|부모(?:는|가|를)?\s*(?:없|제거|비움)|"
    r"parent\s*(?:none|없음)|\btop[- ]?level\b",
    re.I,
)
_DISPLAY_ASSIGNEE_RE = re.compile(
    r"(?:담당(?:자)?|assignee|owner)(?:은|는|이|가|을|를|:)?\s*"
    r"([가-힣]{2,5})(?:님|씨)?|^\s*([가-힣]{2,5})(?:님|씨)?\s*$",
    re.I,
)
_WRITE_KINDS = {"plan", "ticket", "write", "comment", "modify"}
_READ_INTENTS = {"ask", "my_day", "progress", "activity"}

_DUE = re.compile(
    r"(?:마감|기한|due(?:\s*date)?|deadline).{0,24}"
    r"(?:20\d{2}[-./]\d{1,2}[-./]\d{1,2}|오늘|내일|모레|이번\s*주|다음\s*주|"
    r"(?:월|화|수|목|금|토|일)요일|없(?:음|어|습니다)?)|"
    r"(?:20\d{2}[-./]\d{1,2}[-./]\d{1,2}|오늘|내일|모레|이번\s*주|다음\s*주)"
    r".{0,8}(?:까지|마감|기한)",
    re.I,
)
_SCOPE = re.compile(
    r"(?:범위|scope|포함|제외|나머지.{0,20}(?:다음|제외|하지)|"
    r"(?:체크|검증|구현|작업|처리|적용)(?:은|는|을|를)?\s*만|"
    r"\b(?:only|except)\b)",
    re.I,
)
_TARGET = re.compile(
    r"(?:대상(?:은|는|이|가|을|를|으로|로)|테이블|데이터셋|dataset|table|"
    r"파이프라인|화면|기능|문서|티켓)",
    re.I,
)
_PARENT = re.compile(
    r"(?:상위|부모|parent|Epic|에픽).{0,36}"
    r"(?:[A-Z][A-Z0-9]{1,9}-\d+|최상위|새\s*(?:Epic|에픽)|선택|골라|연결|아래)",
    re.I,
)
_ASSIGNEE = re.compile(
    r"(?:담당(?:자)?|assignee|owner|배정|미할당).{0,80}"
    r"(?:skcc\.[a-z]\d{2,8}|미할당|[가-힣]{2,5}|정하지\s*못|정해지지\s*않)",
    re.I,
)
_CONTINUE = re.compile(r"계속|이어서|나머지는.{0,20}그대로|회의\s*메모\s*그대로", re.I)
_ADDITIVE_TARGET = re.compile(r"(?:도|까지)\s*(?:추가|포함)|(?:추가|함께|같이)\s*(?:해|포함)", re.I)
_DUE_VALUE = re.compile(
    r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}(?:\s*까지)?|"
    r"(?:오늘|내일|모레|이번\s*주|다음\s*주|다다음\s*주)"
    r"(?:\s*(?:월|화|수|목|금|토|일)요일)?(?:\s*까지)?|"
    r"(?:월|화|수|목|금|토|일)요일(?:\s*까지)?|마감\s*없(?:음|어|습니다)?",
    re.I,
)
_FIELD_LABEL = (
    r"상위|부모|parent|Epic|에픽|범위|scope|마감|기한|due(?:\s*date)?|deadline|"
    r"담당(?:자)?|assignee|owner|대상|target|테이블|dataset|문서|ticket"
)
_CLAUSE_SPLIT = re.compile(
    rf"[.!?]+(?:\s+|$)|[;\n]+|,\s*(?=(?:{_FIELD_LABEL}))|"
    rf"\s+(?:하고|이며|이고)\s+(?=(?:{_FIELD_LABEL}))",
    re.I,
)
_SCALAR_REPLACEMENT = re.compile(r"(?:말고|대신|아니고|아니라)", re.I)
_SCALAR_CHANGE = re.compile(
    r"^\s*(?:으로|로)?\s*(?:바꿔|바꾸|변경|수정|교체|전환)", re.I,
)


def _latest_user_text(state: dict) -> str:
    for message in reversed(state.get("messages") or []):
        if getattr(message, "type", "") == "human":
            return str(getattr(message, "content", "") or "").strip()
    return ""


def _validated(value) -> dict:
    if not isinstance(value, dict) or not value:
        return {}
    try:
        return ContinuationContract.model_validate(value).model_dump()
    except Exception:
        return {}


def _ordered_unique(values, *, limit: int) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if value and value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return result


def jira_keys(value, *, limit: int = 16) -> list[str]:
    """Return canonical Jira keys from one bounded authority value."""
    return _ordered_unique(
        [match.group(0).upper() for match in _KEY_RE.finditer(str(value or ""))],
        limit=limit,
    )


def is_top_level_parent_choice(value) -> bool:
    """Return whether the user explicitly chose no parent/top-level placement."""
    return bool(_TOP_LEVEL_PARENT_RE.search(str(value or "")))


def parse_assignee_decision(value) -> tuple[str, str]:
    """Parse only lexical identity authority; directory resolution remains a tool boundary.

    Returns ``(kind, value)`` where kind is ``unassigned``, ``user_id``,
    ``display_name`` or ``unknown``.  Semantic role/ownership inference deliberately does not
    belong in this leaf parser.
    """
    text = " ".join(str(value or "").strip().split())
    if _UNASSIGNED_RE.search(text):
        return "unassigned", ""
    user = _USERNAME_RE.search(text)
    if user:
        return "user_id", user.group(0).casefold()
    match = _DISPLAY_ASSIGNEE_RE.search(text)
    name = next((group for group in (match.groups() if match else ()) if group), "")
    return ("display_name", name) if name else ("unknown", "")


def _latest_scalar_replacement(text: str, family: str) -> str:
    """Project an explicit scalar correction to its final user-authored alternative.

    This is deliberately a leaf grammar.  It does not infer intent; it only orders typed
    candidates already recognized for the requested field.  That keeps ``old 말고 new`` and
    ``old에서 new로 변경`` from becoming two targets or from preserving the stale value.
    """
    source = str(text or "")
    candidates: list[tuple[int, int, str]] = []

    def collect(pattern: re.Pattern) -> None:
        candidates.extend((match.start(), match.end(), match.group(0))
                          for match in pattern.finditer(source))

    if family in {"owner", "assignee"}:
        collect(_USERNAME_RE)
        collect(_UNASSIGNED_RE)
    elif family in {"parent", "epic"}:
        collect(_KEY_RE)
        collect(_TOP_LEVEL_PARENT_RE)
    elif family in {"due", "duedate", "date", "deadline"}:
        collect(_DUE_VALUE)
    elif family in {"target", "ticket"}:
        collect(_KEY_RE)
    else:
        return source

    ordered: list[tuple[int, int, str]] = []
    for candidate in sorted(candidates, key=lambda row: (row[0], -(row[1] - row[0]))):
        if ordered and candidate[0] < ordered[-1][1]:
            continue
        ordered.append(candidate)
    if len(ordered) < 2:
        return source

    latest = ""
    for previous, current in zip(ordered, ordered[1:]):
        between = source[previous[1]:current[0]]
        after = source[current[1]:current[1] + 32]
        explicit_alternative = bool(_SCALAR_REPLACEMENT.search(between))
        from_to_change = bool(
            re.search(r"(?:에서|으로부터)\s*$", between, re.I)
            and _SCALAR_CHANGE.search(after)
        )
        if explicit_alternative or from_to_change:
            latest = current[2]
    return latest or source


def _task_action(intent: str, tasks: list[dict]) -> str:
    writes = [task for task in tasks if isinstance(task, dict) and (
        task.get("write_intent") is True
        or str(task.get("kind") or "").strip().casefold() in _WRITE_KINDS
    )]
    if not writes:
        return "read" if intent in _READ_INTENTS else "respond"
    kinds = {str(task.get("kind") or "").strip().casefold() for task in writes}
    comments = "comment" in kinds
    non_comments = bool(kinds - {"comment"})
    if comments and non_comments:
        return "mixed"
    if comments:
        return "comment"
    return "create" if intent == "plan_work" else "update"


def _instruction_target_keys(tasks: list[dict]) -> list[str]:
    result: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        instruction = str(task.get("instruction") or "")
        for match in _KEY_RE.finditer(instruction):
            after = instruction[match.end():match.end() + 48]
            before = instruction[max(0, match.start() - 24):match.start()]
            excluded = bool(re.search(
                r"(?:에는|에는\s*)?(?:댓글|코멘트).{0,20}"
                r"(?:달지|남기지|작성하지|없음|제외)",
                after, re.I,
            ) or re.search(r"(?:배경|참고|제외)\s*$", before, re.I))
            key = match.group(0).upper()
            if not excluded and key not in result:
                result.append(key)
    return result[:16]


def build_continuation_contract(state: dict, *, existing=None) -> dict:
    """Build a validated contract from authoritative state, retaining prior decisions only."""
    carried = _validated(existing if existing is not None else state.get("continuation_contract"))
    continuation = state.get("turn_continuation") is True and bool(carried)
    plan = state.get("request_plan") or {}
    tasks = [copy.deepcopy(task) for task in (plan.get("tasks") or [])
             if isinstance(task, dict)] if isinstance(plan, dict) else []
    intent = str(state.get("intent") or (carried.get("intent") if continuation else "") or "")
    allowed_intents = {
        "ask", "plan_work", "my_day", "progress", "activity", "modify", "chitchat",
    }
    if intent not in allowed_intents:
        return {}
    root = (str(carried.get("root_request") or "").strip() if continuation else "")
    root = root or str(state.get("request_text") or "").strip() or _latest_user_text(state)
    if not root:
        return {}

    explicit_targets = [str(key or "").strip().upper()
                        for key in (state.get("bulk_targets") or []) if _KEY_RE.fullmatch(
                            str(key or "").strip())]
    task_targets = _instruction_target_keys(tasks)
    mentioned = [str(key or "").strip().upper()
                 for key in (state.get("mentioned_keys") or []) if _KEY_RE.fullmatch(
                     str(key or "").strip())]
    prior_targets = (carried.get("target_keys") or []) if continuation else []
    derived_targets = explicit_targets or task_targets or mentioned
    outcome_ids = _ordered_unique(
        [str(task.get("id") or "").strip() for task in tasks], limit=6)
    target_decisions = [
        row for row in (carried.get("decisions") or [])
        if _question_family(row.get("field")) in {
            "target", "table", "entity", "document", "ticket",
        }
    ] if continuation else []
    scoped_target_decisions = [
        row for row in target_decisions if ":" in str(row.get("field") or "")
    ]
    target_decision = target_decisions[-1] if target_decisions else None
    if scoped_target_decisions:
        # Per-outcome target answers are siblings, not latest-wins revisions of one global
        # target. Keep their union while their exact association remains in ``decisions``.
        decision_keys = [
            match.group(0).upper()
            for row in scoped_target_decisions
            for match in _KEY_RE.finditer(row.get("value") or "")
        ]
        target_keys = _ordered_unique([*prior_targets, *decision_keys], limit=16)
    elif target_decision:
        decision_keys = [match.group(0).upper()
                         for match in _KEY_RE.finditer(target_decision.get("value") or "")]
        target_keys = _ordered_unique(
            ([*prior_targets, *decision_keys]
             if _ADDITIVE_TARGET.search(target_decision.get("value") or "")
             else decision_keys),
            limit=16,
        )
    else:
        target_keys = _ordered_unique([*prior_targets, *derived_targets], limit=16)

    derived_action = _task_action(intent, tasks)
    prior_outcomes = carried.get("outcome_ids") or []
    action = (str(carried.get("action") or derived_action)
              if continuation and outcome_ids == prior_outcomes else derived_action)

    payload = {
        "version": "continuation.v1",
        "root_request": root,
        "intent": intent,
        "action": action,
        "target_keys": target_keys,
        "outcome_ids": outcome_ids,
        "decisions": copy.deepcopy(carried.get("decisions") or []) if continuation else [],
    }
    return _validated(payload)


def _question_family(field: str) -> str:
    value = str(field or "").strip().casefold()
    return value.split(":", 1)[0]


def _clauses(text: str) -> list[str]:
    return [part.strip(" \t\r\n,;.!?") for part in _CLAUSE_SPLIT.split(str(text or ""))
            if part.strip(" \t\r\n,;.!?")]


_SCOPE_HINT_STOPWORDS = {
    "parent", "epic", "상위", "부모", "담당", "담당자", "assignee", "owner",
    "target", "대상", "ticket", "티켓", "task", "태스크", "테스크", "작업",
    "어느", "어떤", "무엇", "누구", "인가요", "알려", "주세요", "정해", "선택",
}


def _scoped_clause(text: str, field: str, question: dict | None) -> str:
    """Bind a scoped question to one user clause, or return empty when ambiguous.

    A previous implementation applied the first Jira key/username in the whole utterance to
    every scoped question.  Use the field suffix and the rendered question's outcome label as
    the only bridge.  This intentionally fails closed when two sibling answers cannot be
    associated with their outcomes.
    """
    suffix = str(field or "").partition(":")[2].strip()
    if not suffix:
        return str(text or "").strip()
    hint_text = " ".join((suffix, str((question or {}).get("question") or "")))
    hints = {
        token.casefold() for token in re.findall(r"[가-힣A-Za-z0-9_.-]{2,}", hint_text)
        if token.casefold() not in _SCOPE_HINT_STOPWORDS
    }
    clauses = [part.strip() for part in re.split(
        r"[,;!?\n]+|[.]+(?:\s+|$)", str(text or ""))
               if part.strip()]
    if not hints or not clauses:
        return ""
    ranked = []
    for index, clause in enumerate(clauses):
        folded = clause.casefold()
        score = sum(1 for hint in hints if hint in folded)
        if score:
            ranked.append((score, -index, clause))
    if not ranked:
        return ""
    ranked.sort(reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return ""
    return ranked[0][2]


def _field_value(text: str, field: str, question: dict | None = None,
                 *, require_scope: bool = False) -> str:
    """Return the smallest useful user-authored value for one typed field."""
    family = _question_family(field)
    scoped = _scoped_clause(text, field, question) if ":" in str(field or "") else ""
    if require_scope and not scoped:
        return ""
    source_text = scoped or str(text or "")
    parts = _clauses(source_text) or [source_text.strip()]
    options = [str(option or "").strip() for option in ((question or {}).get("options") or [])
               if str(option or "").strip()]

    def matches(part: str) -> bool:
        if any(option.casefold() in part.casefold() for option in options):
            return True
        if family in {"person", "reviewer", "requester", "instructor"}:
            return bool(_USERNAME_RE.search(part))
        if family in {"owner", "assignee"}:
            return bool(_ASSIGNEE.search(part) or _USERNAME_RE.search(part)
                        or re.search(r"미할당", part))
        if family in {"parent", "epic"}:
            return bool(_PARENT.search(part) or re.search(
                r"최상위\s*Task|새\s*(?:Epic|에픽)", part, re.I))
        if family in {"due", "duedate", "date", "deadline"}:
            return bool(_DUE_VALUE.search(part))
        if family in {"target", "table", "entity", "document", "ticket"}:
            return bool(_KEY_RE.search(part) or _TARGET.search(part))
        if family in {"scope", "rule", "action"}:
            return bool(_SCOPE.search(part))
        if family in {"term", "acronym", "meaning"}:
            return bool(re.search(r".{1,40}(?:은|는|이란|란|:|=)\s*.{2,}", part, re.S))
        if family in {"structure", "shape", "type"}:
            return bool(re.search(
                r"(?:Epic|에픽|Task|태스크|테스크|Sub-?Task|서브\s*태스크|단일|여러|복수)",
                part, re.I,
            ))
        return False

    selected = [part for part in parts if matches(part)]
    value = ". ".join(_ordered_unique(selected, limit=4)) or source_text.strip()
    value = _latest_scalar_replacement(value, family)

    # Canonical identity/link/date values should not carry an adjacent imperative such as
    # ``Task 초안을 계속 만들어줘`` into another field's authority.
    if family in {"person", "reviewer", "requester", "instructor", "owner", "assignee"}:
        username = _USERNAME_RE.search(value)
        if username:
            return username.group(0)
        if re.search(r"미\s*할당|정하지\s*못|정해지지\s*않|담당(?:자)?\s*없", value):
            return "미할당"
    if family in {"parent", "epic"}:
        key = _KEY_RE.search(value)
        if key:
            return key.group(0).upper()
        choice = re.search(r"최상위\s*Task|새\s*(?:Epic|에픽)", value, re.I)
        if choice:
            return choice.group(0)
    if family in {"due", "duedate", "date", "deadline"}:
        due = _DUE_VALUE.search(value)
        if due:
            return " ".join(due.group(0).split())
    if family in {"target", "ticket"}:
        keys = _ordered_unique(
            [match.group(0).upper() for match in _KEY_RE.finditer(value)], limit=16)
        if keys and not _ADDITIVE_TARGET.search(value):
            return ", ".join(keys)
    return " ".join(value.split())[:1000]


def _matches_question_answer(text: str, question: dict) -> bool:
    field = str(question.get("field") or "").strip()
    family = _question_family(field)
    folded = str(text or "").strip()
    if not folded:
        return False
    options = [str(option or "").strip() for option in (question.get("options") or [])
               if str(option or "").strip()]
    if any(option.casefold() in folded.casefold() for option in options):
        return True
    if family in {"person", "reviewer", "requester", "instructor"}:
        return bool(_USERNAME_RE.search(folded))
    if family in {"owner", "assignee"}:
        return bool(_USERNAME_RE.search(folded) or re.search(
            r"미할당|담당(?:자)?(?:은|는|이|가)?\s*[가-힣]{2,5}", folded, re.I,
        ))
    if family in {"parent", "epic"}:
        return bool(_PARENT.search(folded) or re.search(
            r"\b[A-Z][A-Z0-9]{1,9}-\d+\b|최상위\s*Task|새\s*(?:Epic|에픽)",
            folded, re.I,
        ))
    if family in {"due", "duedate", "date", "deadline"}:
        return bool(_DUE.search(folded) or re.search(
            r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}|오늘|내일|모레|이번\s*주|다음\s*주",
            folded, re.I,
        ))
    if family in {"term", "acronym", "meaning"}:
        return bool(re.search(r".{1,40}(?:은|는|이란|란|:|=)\s*.{2,}", folded, re.S))
    if family in {"target", "table", "entity", "document", "ticket"}:
        return bool(_KEY_RE.search(folded) or _TARGET.search(folded))
    if family in {"scope", "rule", "action"}:
        return bool(_SCOPE.search(folded) or len(folded) <= 240)
    if family in {"structure", "shape", "type"}:
        return bool(re.search(
            r"(?:Epic|에픽|Task|태스크|테스크|Sub-?Task|서브\s*태스크|단일|여러|복수)",
            folded, re.I,
        ))
    return False


def capture_continuation_decisions(text: str, questions) -> list[dict]:
    """Capture typed answers and explicit constraint refinements from one user turn."""
    value = " ".join(str(text or "").strip().split())
    if not value or len(value) > 1000:
        return []
    decisions: list[dict] = []

    matched_questions = []
    for raw in questions or []:
        if not isinstance(raw, dict):
            continue
        field = str(raw.get("field") or "").strip()
        if field and _matches_question_answer(value, raw):
            matched_questions.append((field, raw))
    scoped_family_counts: dict[str, int] = {}
    for field, _raw in matched_questions:
        if ":" in field:
            family = _question_family(field)
            scoped_family_counts[family] = scoped_family_counts.get(family, 0) + 1

    def add(field: str, source: str, question: dict | None = None) -> None:
        field = str(field or "").strip()
        if not field or any(row["field"] == field for row in decisions):
            return
        field_value = _field_value(
            value, field, question,
            require_scope=(":" in field and scoped_family_counts.get(
                _question_family(field), 0) > 1),
        )
        if not field_value:
            return
        decisions.append({
            "field": field,
            "value": field_value,
            "source": source,
        })

    def add_explicit(field: str) -> None:
        family = _question_family(field)
        aliases = {
            "duedate": {"due", "duedate", "date", "deadline"},
            "assignee": {"owner", "assignee"},
        }.get(family, {family})
        if any(_question_family(row["field"]) in aliases for row in decisions):
            return
        add(field, "explicit_refinement")

    for field, raw in matched_questions:
        add(field, "interview_answer", raw)

    # These are generic execution constraints, not a replay of arbitrary conversation text.
    # They may partially answer another question (ASK2 supplies scope+due before target), so
    # Session accepts them only as a pair unless one also matched the pending typed question.
    if _DUE.search(value):
        add_explicit("duedate")
    if _SCOPE.search(value):
        add_explicit("scope")
    if _PARENT.search(value):
        add_explicit("parent")
    if _ASSIGNEE.search(value):
        add_explicit("assignee")
    return decisions[:8]


def merge_continuation_decisions(contract, decisions) -> dict:
    """Latest user answer wins per typed field while unrelated decisions remain."""
    base = _validated(contract)
    if not base:
        return {}
    merged = [copy.deepcopy(row) for row in base.get("decisions") or []]
    old_scoped_target_keys = {
        match.group(0).upper()
        for row in merged
        if isinstance(row, dict)
        and ":" in str(row.get("field") or "")
        and _question_family(row.get("field")) in {
            "target", "table", "entity", "document", "ticket",
        }
        for match in _KEY_RE.finditer(str(row.get("value") or ""))
    }
    baseline_targets = [
        str(key or "").strip().upper() for key in (base.get("target_keys") or [])
        if str(key or "").strip().upper() not in old_scoped_target_keys
    ]
    for raw in decisions or []:
        if not isinstance(raw, dict):
            continue
        try:
            candidate = {
                "field": str(raw.get("field") or "").strip(),
                "value": " ".join(str(raw.get("value") or "").strip().split()),
                "source": str(raw.get("source") or ""),
            }
            probe = dict(base, decisions=[candidate])
            candidate = _validated(probe).get("decisions", [])[0]
        except Exception:
            continue
        merged = [row for row in merged if row.get("field") != candidate["field"]]
        merged.append(candidate)
    base["decisions"] = merged[-16:]
    target_rows = [
        row for row in base["decisions"]
        if _question_family(row.get("field")) in {
            "target", "table", "entity", "document", "ticket",
        }
    ]
    scoped_rows = [row for row in target_rows if ":" in str(row.get("field") or "")]
    if scoped_rows:
        decision_keys = [
            match.group(0).upper()
            for row in scoped_rows
            for match in _KEY_RE.finditer(str(row.get("value") or ""))
        ]
        base["target_keys"] = _ordered_unique(
            [*baseline_targets, *decision_keys], limit=16)
    elif target_rows:
        latest = target_rows[-1]
        decision_keys = [match.group(0).upper()
                         for match in _KEY_RE.finditer(str(latest.get("value") or ""))]
        base["target_keys"] = _ordered_unique(
            ([*baseline_targets, *decision_keys]
             if _ADDITIVE_TARGET.search(str(latest.get("value") or ""))
             else decision_keys),
            limit=16,
        )
    else:
        base["target_keys"] = _ordered_unique(baseline_targets, limit=16)
    return _validated(base)


def authoritative_decision_values(state_or_contract) -> list[str]:
    """Return ordered, de-duplicated user values from the typed ledger."""
    raw = (state_or_contract.get("continuation_contract")
           if isinstance(state_or_contract, dict) and "continuation_contract" in state_or_contract
           else state_or_contract)
    contract = _validated(raw)
    return _ordered_unique(
        [row.get("value") for row in contract.get("decisions") or []], limit=16)


def has_current_continuation_decision(contract, text: str) -> bool:
    """Return whether the latest utterance supplied at least one ledger value."""
    current = " ".join(str(text or "").split())
    folded = current.casefold()
    current_keys = {match.group(0).upper() for match in _KEY_RE.finditer(current)}
    for value in authoritative_decision_values(contract):
        normalized = " ".join(str(value or "").split())
        if normalized and normalized.casefold() in folded:
            return True
        keys = {match.group(0).upper() for match in _KEY_RE.finditer(normalized)}
        if keys and keys <= current_keys:
            return True
    return False


def has_interview_answer(decisions) -> bool:
    return any(isinstance(row, dict) and row.get("source") == "interview_answer"
               for row in decisions or [])


def has_multi_field_refinement(decisions) -> bool:
    fields = {str(row.get("field") or "") for row in decisions or []
              if isinstance(row, dict) and row.get("source") == "explicit_refinement"}
    return len(fields) >= 2


def has_continuation_cue(text: str) -> bool:
    return bool(_CONTINUE.search(str(text or "")))


def has_typed_continuation_contract(value) -> bool:
    """Return whether a checkpoint already carries a strict v1 authority envelope."""
    return bool(_validated(value))


__all__ = [
    "authoritative_decision_values", "build_continuation_contract",
    "capture_continuation_decisions", "has_continuation_cue",
    "has_current_continuation_decision", "has_interview_answer",
    "has_multi_field_refinement", "has_typed_continuation_contract",
    "is_top_level_parent_choice", "jira_keys", "merge_continuation_decisions",
    "parse_assignee_decision",
]
