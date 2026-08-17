# tools/agent_create_suite.py — 티켓 **생성** 시나리오 배터리 (실 LLM, 수동 실행 전용).
#
# 실행: python -X utf8 tools/agent_eval_launcher.py create [모델] [케이스ID ...] [--out 결과.json]
#
# 생성 요청은 사용자가 말하는 방식이 제각각이다 — 한 줄로 던지기도 하고, 구조를 지정하기도
# 하고, 남이 쓴 글을 통째로 붙여넣기도 한다. 여기 모은 것은 **그 변주**다.
#
# 각 케이스는 **초안(pending/draft_items)** 을 본다. 답변 문장이 아니라 실제로 만들어질
# 것을 검사해야 한다 — 말은 그럴듯한데 초안이 비어 있는 실패가 실제로 있었다.
import json
import os
import re
import sys
import time
from datetime import date
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.agent_scenario_eval import validate_eval_argv  # noqa: E402

# 사람이 없는 실행이다 — 설정 화면의 확인 게이트를 면제한다(config._env_supplied).
_raw_args = list(sys.argv[1:])
if __name__ == "__main__":
    validate_eval_argv(_raw_args)
OUT = None
for i, arg in enumerate(_raw_args):
    if arg.startswith("--out="):
        OUT = arg.split("=", 1)[1]
    elif arg == "--out" and i + 1 < len(_raw_args):
        OUT = _raw_args[i + 1]
_args = [a for i, a in enumerate(_raw_args)
         if not a.startswith("-") and not (i and _raw_args[i - 1] == "--out")]
MODEL = _args[0] if _args and not _args[0].isupper() else "gpt-4o-mini"
ONLY = {a for a in _args if a.isupper()}
SIMPLE_MODEL = os.environ.get("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")

from tools.agent_eval_protocol import (build_run_metadata, quantitative_metrics,
                                       raw_result_path, reserve_raw_result_path,
                                       write_raw_result)  # noqa: E402
from tools.agent_eval_isolation import (begin_case, configure_process_isolation,
                                         finish_case,
                                         preflight_evaluation_provider)  # noqa: E402
from tools.agent_eval_review_specs import review_specs  # noqa: E402
try:  # 과거 prompt variant commit에도 같은 하네스를 적용한다.
    from app.agent.prompts.base import PROMPT_VERSION  # noqa: E402
except ImportError:  # legacy asset에는 version 상수가 없었다.
    PROMPT_VERSION = os.getenv("LAKE_AGENT_PROMPT_VERSION", "legacy")

# v5 major: deterministic pass now enforces explicit single-root due/ordinal,
# question necessity metadata, and user-facing evidence relevance. Historical
# v4 automatic pass rates therefore remain v4-only and are not rescored.
# v5.0.1 fixes nested draft child discovery for blocked/review-failed outputs.
BATTERY_VERSION = "5.0.1"
SUITE_REVIEW_ELEMENTS, CASE_REVIEW_SPECS = review_specs("create")
session = None


def _prepare_runtime():
    """Configure the live battery only when executed, never when imported by tests."""
    global session
    configure_process_isolation("create")
    os.environ.setdefault("JIRA_ENV", "mock")
    os.environ.setdefault("LAKE_AGENT_PROVIDER", "openai")
    os.environ["LAKE_AGENT_SKIP_VERIFY"] = "1"
    os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")
    from tools.agent_scenario_eval import configure_model_routing
    configure_model_routing(MODEL, SIMPLE_MODEL)
    preflight_evaluation_provider()
    from app.agent.workflow import session as runtime_session
    session = runtime_session


def items(o):
    """승인 대기 초안이 있으면 그것, 없으면 작성 중 초안(되묻는 턴)."""
    return ((o.get("pending") or {}).get("items")) or o.get("draft_items") or []


def kids(o):
    """Return every child regardless of approval/rendering state.

    Approved cards historically expose children at ``pending.children`` while a blocked
    Auditor result keeps them nested under ``draft_items[*].children``.  Looking at only
    the former made a blocked Task with three real Sub-Tasks appear structurally empty and
    also hid child-level contract defects.  Preserve source order and collapse the same
    child when a renderer mirrors it in both shapes.
    """
    rows = []
    seen = set()
    pending = o.get("pending") or {}
    candidates = list(pending.get("children") or [])
    for root in items(o):
        if isinstance(root, dict):
            candidates.extend(root.get("children") or [])
    for child in candidates:
        if not isinstance(child, dict):
            continue
        identity = json.dumps(child, ensure_ascii=False, sort_keys=True, default=str)
        if identity in seen:
            continue
        seen.add(identity)
        rows.append(child)
    return rows


def pend(o, k, d=None):
    return (o.get("pending") or {}).get(k, d)


def _body(it):
    return str(it.get("description") or "")


def has_sections(it, *names):
    return all(n in _body(it) for n in names)


def _owners(rows):
    return [str(r.get("assignee") or "") for r in rows]


def _question_text(o) -> str:
    return json.dumps(o.get("questions") or [], ensure_ascii=False)


def _asks_for_bug_identity(o) -> bool:
    questions = _question_text(o)
    return bool(o.get("questions")) and any(
        word in questions for word in ("재현", "DAG", "배치 이름", "어떤 배치", "실행 환경")
    )


def _bug3_ok(o, _outs) -> bool:
    q = _question_text(o)
    return (not items(o) and len(o.get("questions") or []) == 1
            and any(word in q for word in ("DAG", "Job", "배치 이름"))
            and "환경" in q and any(word in q for word in ("로그", "발생 시각", "재현")))


def _rule1_ok(o, _outs) -> bool:
    questions = _question_text(o)
    asks_legal_shape = any(
        word in questions for word in ("부모", "상위 Task", "최상위 Task", "Task로")
    )
    return bool(o.get("questions")) and not items(o) and asks_legal_shape


# ── 본문 품질 게이트 (전 케이스 공통) ─────────────────────────────────────
# 여태 이 스위트는 **구조만** 봤다 — 몇 건인가, 자식이 붙었나, 부모가 맞나. 본문이
# 비어 있든 섹션이 세 벌이든 통과했다. 실사용 사고(STARR NDV)는 구조가 아니라 본문에서
# 났고, DRAFT-COMPARISON 의 갭 3종도 전부 본문 이야기다. 그래서 knowledge/07 의 규율을
# 결정적 검사로 내려 전 케이스에 건다 — judge(주관) 이전의 최소선이다.
# ★ 목록을 여기 다시 적지 않는다 — 코드가 지키는 규칙과 배터리가 재는 규칙이 갈리면
#   더 관대한 쪽이 사고를 낸다(§5-e). Work Architect가 원본이고 여기는 그것을 가져다 쓴다.
from app.agent.workflow.agents.work_architect import DOD_VAGUE as _DOD_VAGUE  # noqa: E402
from app.agent.workflow.agents.work_architect import _bug_grade_body  # noqa: E402
from app.agent.workflow.agents.work_architect import _has_placeholder_body  # noqa: E402


def _body_flaws(o) -> list:
    """상위 항목 본문의 규율 위반 목록. 빈 리스트면 통과.

    Sub-Task 본문은 대상이 아니다(배경을 쓰지 않는 것이 규칙이다 — knowledge/07).
    """
    flaws = []
    for i, it in enumerate(items(o)):
        if str(it.get("type") or "").lower().startswith("sub"):
            continue
        b = _body(it)
        if len(b.strip()) < 60:
            flaws.append(f"[{i}] 본문이 사실상 비었다")
            continue
        is_bug = str(it.get("type") or "").strip().lower() == "bug"
        # Bug는 Task의 배경/범위/DoD가 아니라 재현/기대/실제 세 칸이 계약이다. 전 케이스
        # 공통 Task 게이트를 걸어 PASTE2·BUG2가 올바른 Bug 본문인데도 항상 실패했다.
        if is_bug:
            if not _bug_grade_body(b):
                for sec in ("재현", "기대", "실제"):
                    if sec not in b:
                        flaws.append(f"[{i}] Bug '{sec}' 섹션 없음")
        else:
            for sec in ("배경", "작업 범위", "완료 조건"):
                if sec not in b:
                    flaws.append(f"[{i}] '{sec}' 섹션 없음")
        # 중복·영문 섹션 — 실측 사고(참고/Knowledge/References 3벌)
        if b.count("<h3>참고</h3>") > 1:
            flaws.append(f"[{i}] 참고 섹션이 두 벌")
        for bad in ("References", "<h3>Knowledge</h3>", "Acceptance Criteria"):
            if bad in b:
                flaws.append(f"[{i}] 영문 중복 섹션 '{bad}'")
        # 범위의 제외 — "하지 않는 것을 적는 게 절반"(knowledge/07)
        if not is_bug and "작업 범위" in b and not re.search(r"제외|하지\s*않", b):
            flaws.append(f"[{i}] 작업 범위에 제외가 없다")
        # DoD 판정 방법 — "테스트 완료"는 언제 끝인지 모른다
        dods = re.findall(r'data-checked="[^"]*"[^>]*>(.*?)</li>', b, re.S)
        dods = [re.sub(r"<[^>]+>", "", d).strip() for d in dods]
        dods = [d for d in dods if d]
        if not is_bug and dods:
            vague = [d for d in dods if any(v in d for v in _DOD_VAGUE) and len(d) < 24]
            if len(vague) * 2 > len(dods):
                flaws.append(f"[{i}] DoD 절반 이상이 판정 방법 없음: {vague[:2]}")
            malformed = [d for d in dods if re.search(
                r"(?:이|가)\s+(?:을|를)\s*확인|(?:이|가)\s+하여|기능이\s+을|"
                r"사용자가.{0,20}(?:쉽게|편리하게)\s*확인\s*가능", d)]
            if malformed:
                flaws.append(f"[{i}] 문법이 깨진 DoD: {malformed[:2]}")
        # 링크도 키도 없는 **참고 섹션 안의** 불릿은 날조로 취급한다. 예전의 앞 400자
        # 탐색은 뒤의 '환경 및 추가 정보'까지 참고로 오인했다 — HTML 섹션 경계를 직접 본다.
        for sec in re.finditer(
                r"<h3>\s*참고(?:\s*(?:사항|자료|문서))?\s*</h3>\s*<ul[^>]*>(.*?)</ul>",
                b, re.S | re.I):
            for m in re.finditer(r"<li>(?!.*?(?:[A-Z]{2,}-\d+|<a href))(.{6,80}?)</li>",
                                 sec.group(1), re.S):
                flaws.append(f"[{i}] 참고에 출처 없는 불릿: {m.group(1)[:30]}")
                break
    return flaws


def _output_flaws(o) -> list:
    """본문 외의 사용자-visible 계약: 질문 수, reply/payload 일치, 렌더링 토큰."""
    flaws = []
    reply = str(o.get("reply") or "")
    rows = items(o)
    if len(o.get("questions") or []) > 3:
        flaws.append(f"질문이 {len(o.get('questions') or [])}개(최대 3개)")
    if any(_has_placeholder_body(i.get("description")) for i in rows):
        flaws.append("본문에 '적어주세요/설명해주세요/TODO' placeholder가 남았다")
    if rows and re.search(r"(?:티켓|초안|작업).{0,30}(?:만들|생성|진행).{0,12}(?:수\s*없|불가능)",
                          reply, re.I | re.S):
        flaws.append("reply는 생성 불가라지만 payload에는 초안이 있다")
    if not rows and re.search(r"초안.{0,30}(?:승인|확인해\s*주)", reply, re.I | re.S):
        flaws.append("payload가 없는데 reply가 초안 승인을 요청한다")
    if re.search(r"^\s*#{1,3}\s*명령서", reply) or "{{ref:" in reply or "{{mention:" in reply:
        flaws.append("내부 명령서/미렌더링 reference 토큰이 노출됐다")
    if rows and re.search(r"(?m)^\s*#{1,4}\s*Epic\s*$", reply) \
            and str(rows[0].get("type") or "").lower() != "epic":
        flaws.append("reply는 Epic이라지만 첫 payload 타입은 Epic이 아니다")
    return flaws


_DUE_BEFORE_DATE_RE = re.compile(
    r"(?:마감(?:일)?|기한|due(?:\s*date)?)\s*(?:은|는|이|가|을|를|로|:|=)?\s*"
    r"(\d{4}-\d{2}-\d{2})",
    re.I,
)
_DATE_BEFORE_DUE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s*(?:까지|마감(?:일)?|기한|due(?:\s*date)?)",
    re.I,
)
_SOURCE_ORDINAL_RE = re.compile(r"(?<![0-9])([0-9]+)\s*차")
_BARE_ORDINAL_RE = re.compile(
    r"(?<![0-9A-Za-z가-힣_])차(?=\s|[—–\-:·,.;!?()\[\]{}]|$)",
)


def _valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _unique_explicit_due(turns: list[str]) -> str | None:
    """Return the newest unambiguous explicit due; later user changes supersede."""
    for turn in reversed(turns):
        raw_values = {
            value
            for pattern in (_DUE_BEFORE_DATE_RE, _DATE_BEFORE_DUE_RE)
            for value in pattern.findall(str(turn or ""))
        }
        if not raw_values:
            continue
        valid = {value for value in raw_values if _valid_iso_date(value)}
        return next(iter(valid)) if len(raw_values) == 1 and len(valid) == 1 else None
    return None


def _root_payload_items(o: dict) -> list[dict]:
    rows = [row for row in items(o) if isinstance(row, dict)]
    non_subtasks = [
        row for row in rows
        if not str(row.get("type") or "").strip().lower().startswith("sub")
    ]
    return non_subtasks or rows


def _payload_due(row: dict) -> str:
    fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
    return str(row.get("duedate") or row.get("due")
               or fields.get("duedate") or fields.get("due") or "")


def _visible_row_text(row: dict) -> str:
    text = "\n".join((str(row.get("summary") or ""), _body(row)))
    return re.sub(r"<[^>]+>", " ", text)


def _latest_explicit_ordinals(turns: list[str]) -> list[str]:
    """Use the newest turn that names an ordinal; later user changes supersede."""
    for turn in reversed(turns):
        values = sorted(set(_SOURCE_ORDINAL_RE.findall(str(turn or ""))))
        if values:
            return values
    return []


def _explicit_field_flaws(o: dict, turns: list[str]) -> list[str]:
    """Deterministic user-field preservation shared by every create case."""
    flaws = []
    roots = _root_payload_items(o)
    due = _unique_explicit_due(turns)
    # A unique global-looking date cannot be mapped deterministically across
    # multiple independent roots. Their per-ticket mapping stays in the case
    # contract and human review rather than producing an automatic false red.
    if due and len(roots) == 1:
        actual = _payload_due(roots[0])
        if actual != due:
            flaws.append(
                f"root[0] 명시 마감일 불일치: 요청 {due}, payload {actual or '없음'}"
            )

    ordinals = _latest_explicit_ordinals(turns)
    rows = roots + [row for row in kids(o) if isinstance(row, dict)]
    if ordinals and rows:
        for index, row in enumerate(rows):
            for field_name in ("summary", "description"):
                value = re.sub(r"<[^>]+>", " ", str(row.get(field_name) or ""))
                if _BARE_ORDINAL_RE.search(value):
                    flaws.append(
                        f"draft[{index}] {field_name}에서 숫자 ordinal이 bare '차'로 손상"
                    )
                    break

        # 하나의 root tree와 하나의 source ordinal이면 root가 그 범위를 소유하고
        # children은 계층적으로 상속한다. 매 child 제목에 같은 `1차`를 반복할 필요는
        # 없지만, 명시했다면 bare/충돌 ordinal이어서는 안 된다.
        if len(roots) == 1 and len(ordinals) == 1:
            expected = ordinals[0]
            expected_number = re.match(r"(\d+)", expected).group(1)
            ordinal_re = re.compile(rf"(?<![0-9]){re.escape(expected_number)}\s*차")
            root_visible = _visible_row_text(roots[0])
            root_numbers = set(re.findall(r"(?<!\d)(\d{1,3})\s*차", root_visible))
            root_conflicts = sorted(value for value in root_numbers
                                    if value != expected_number)
            if root_conflicts:
                flaws.append(
                    f"draft[0] 원문 ordinal '{expected_number}차'와 충돌하는 ordinal "
                    + ", ".join(f"{value}차" for value in root_conflicts)
                )
            elif not ordinal_re.search(root_visible):
                flaws.append(f"draft[0] 원문 ordinal '{expected_number}차' 누락")
            for index, row in enumerate(rows[1:], 1):
                actual = set(re.findall(r"(?<!\d)(\d{1,3})\s*차", _visible_row_text(row)))
                conflicts = sorted(value for value in actual if value != expected_number)
                if conflicts:
                    flaws.append(
                        f"draft[{index}] root 범위 '{expected_number}차'와 충돌하는 ordinal "
                        + ", ".join(f"{value}차" for value in conflicts)
                    )
    return flaws


def _evidence_section(reply: str) -> str:
    match = re.search(r"(?mi)^(#{1,4})[ \t]*근거[^\r\n]*$", reply)
    if not match:
        return ""
    level = len(match.group(1))
    tail = reply[match.end():]
    next_section = re.search(rf"(?m)^#{{1,{level}}}[ \t]+\S", tail)
    end = match.end() + (next_section.start() if next_section else len(tail))
    return reply[match.start():end]


def _generic_direct_source(url: str) -> bool:
    parsed = urlsplit(url.rstrip(".,;"))
    host = parsed.netloc.lower()
    path = (parsed.path or "/").lower().rstrip("/")
    if path in ("", "/", "/search", "/docs", "/documentation"):
        return True
    if re.fullmatch(r"/docs/introduction/[^/]*(?:intro|introduction)", path):
        return True
    if "github.com" in host and (
        path.endswith("/docs/readme.md")
        or re.search(r"/tree/[^/]+/docs$", path)
    ):
        return True
    return False


def _approved_evidence_flaws(o: dict) -> list[str]:
    """Reject debug observations and generic pages only when shown as evidence."""
    if not (items(o) or kids(o)):
        return []
    evidence = _evidence_section(str(o.get("reply") or ""))
    if not evidence:
        return []
    flaws = []
    if re.search(
        r"(?i)\b(?:canonicaljql|scopeprojects|queryplan)\b|"
        r"\b(?:pages|returned|startat|total)=\d+",
        evidence,
    ):
        flaws.append("승인 답변 근거에 canonicalJQL/QueryPlan pagination debug 관측 노출")

    urls = re.findall(r"https?://[^\s)>]+", evidence)
    contribution_material = re.search(
        r"(?i)contributor license agreement|\bCLA\b|markdown syntax|"
        r"(?:contribut|기여).{0,40}(?:documentation|docs|문서)|"
        r"(?:documentation|docs|문서).{0,40}(?:contribut|기여)|"
        r"search the documentation|/docs/readme\.md",
        evidence,
    )
    if contribution_material or any(_generic_direct_source(url) for url in urls):
        flaws.append("승인 답변 근거에 generic search/home/docs README 또는 기여 안내를 직접 근거로 출력")
    return flaws


def _question_gate_flaws(o: dict) -> list[str]:
    """Reject optional preference questions that stop a create workflow."""
    questions = [q for q in (o.get("questions") or []) if isinstance(q, dict)]
    if not questions:
        return []
    flaws = []
    reply = str(o.get("reply") or "")
    question_only = not items(o) and not kids(o) and not o.get("pending")
    optional_only = all(q.get("required_input") is False for q in questions)
    if question_only and optional_only and re.search(r"사용자\s*입력\s*필요", reply):
        flaws.append("required_input=false 질문만으로 진행을 중단하며 사용자 입력 필요라고 표시")

    for index, question in enumerate(questions):
        required = question.get("required_input")
        text = str(question.get("question") or "")
        field = str(question.get("field") or "").strip().lower()
        if required is False and (
            field in {"structure", "ticket_structure", "shape"}
            or any(term in text for term in ("티켓 구조", "어떤 구조", "단일 Task", "Sub-Task"))
        ):
            flaws.append(
                f"question[{index}] optional 구조 선호를 질문으로 중단; 조사·안전한 기본값 필요"
            )
        if required is True:
            reason = str(question.get("why_required") or "").strip()
            generic = re.fullmatch(
                r"(?:확인|진행|작업|사용자 입력)이?\s*필요(?:합니다|함|하다)?[.!]?",
                reason,
            )
            if len(reason) < 8 or generic:
                flaws.append(
                    f"question[{index}] 필수 질문의 why_required가 비었거나 구체적이지 않음"
                )
        elif required is None and question_only:
            flaws.append(f"question[{index}] required_input 계약 누락")
    return flaws


def _creation_contract_flaws(o: dict, turns: list[str]) -> list[str]:
    """Automatic contract failures only; this is never a human-quality score."""
    return _explicit_field_flaws(o, turns) + _approved_evidence_flaws(o)


def _all_contract_flaws(last: dict, turns: list[str], outs: list[dict],
                        *, structure_ok: bool) -> list[str]:
    """Return diagnosable failures for shared and case-specific contracts."""
    turn_flaws = [
        f"turn[{index}] {flaw}"
        for index, output in enumerate(outs)
        for flaw in _question_gate_flaws(output)
    ]
    flaws = (_body_flaws(last) + _output_flaws(last)
             + _creation_contract_flaws(last, turns) + turn_flaws)
    if not structure_ok:
        # A false result with an empty defect list looks like a harness error. The detailed
        # predicate remains in the versioned case review spec, but the raw result must expose
        # which contract family failed.
        flaws.insert(0, "케이스별 구조 계약 실패 — 해당 case review spec과 payload를 대조")
    return flaws


def _duplicate_decision_ok(output: dict, _outputs=None) -> bool:
    """Question form owns duplicate decisions; prose must not echo the same form."""
    questions = output.get("questions") or []
    blob = json.dumps(questions, ensure_ascii=False)
    return (not items(output) and len(questions) == 1
            and all(value in blob for value in (
                "DL-9072", "프로듀서 Avro 직렬화 전환", "근거",
                "범위를 추가", "별도 티켓")))


# Case lambdas and the shared post-check call these globals by name. ``inspect.getsource``
# on a lambda cannot see through that reference, so the evaluation manifest must fingerprint
# the designated dependency set explicitly. Keep this list limited to functions whose source
# changes automatic pass/fail behavior; runtime/output plumbing does not belong here.
CREATE_CHECKER_DEPENDENCIES = (
    items, kids, pend, _body, has_sections, _owners, _question_text,
    _asks_for_bug_identity, _bug3_ok, _rule1_ok,
    _body_flaws, _output_flaws, _valid_iso_date, _unique_explicit_due,
    _root_payload_items, _payload_due, _visible_row_text, _latest_explicit_ordinals,
    _explicit_field_flaws, _evidence_section, _generic_direct_source,
    _approved_evidence_flaws, _question_gate_flaws, _creation_contract_flaws,
    _all_contract_flaws, _duplicate_decision_ok,
)


# (ID, 설명, [질의…], 체커(마지막 out, 전체 outs))
CASES = [
    # ── 한 줄 요청: 필수정보가 있으면 위임된 선택을 되묻지 않는다 ─────
    ("ONE1", "단순 단건 + 알아서 — 필수정보 충족 시 바로 초안", [
        "Workbench 쿼리 편집기에 단축키 도움말 팝업 추가해줘. 알아서 초안 잡아줘"],
     lambda o, _: len(items(o)) == 1 and not o.get("questions")
     and has_sections(items(o)[0], "배경", "완료 조건")),

    ("ONE2", "작은 수정 요청 — 단일 Task 로 끝난다(과잉 분해 금지)", [
        "카탈로그 화면 상단 필터에 '내 모듈만' 체크박스 하나 추가. 알아서"],
     lambda o, _: len(items(o)) == 1 and not kids(o)
     and pend(o, "structure") in (None, "", "single_task")),

    # ── 구조를 사용자가 지정 ─────────────────────────────────────────
    ("STR1", "분량 분할을 명시 — Sub-Task 로 나누고 담당을 골고루", [
        "메타데이터 미등록 테이블 30개를 등록해야 해. 사람 나눠서 진행하게 만들어줘. 알아서"],
     lambda o, _: len(kids(o)) >= 2 and len({x for x in _owners(kids(o)) if x}) >= 2),

    # 사용자가 `알아서`라고 구조 선택을 위임했고 산출물도 셋을 명시했다. 구조를 다시 묻는 것은
    # 필수 인터뷰가 아니라 위임된 선택의 재질문이므로 한 턴에 적절한 구조와 초안을 내야 한다.
    ("STR2", "기능 분화 — 위임된 구조를 재질문하지 않고 모듈별 Task로 분리", [
        "리니지 뷰어 성능 측정하고, 결과에 따라 쿼리 엔진 쪽 인덱스도 손봐야 해. "
        "그리고 사용 가이드도 써야 하고. 초안 잡아줘. 알아서"],
     lambda o, _: (not o.get("questions") and len(items(o)) == 3 and not kids(o)
                   and len({(i.get("components") or [""])[0] for i in items(o)}) >= 2
                   and sum("성능" in str(i.get("summary") or "") for i in items(o)) == 1
                   and sum("인덱스" in str(i.get("summary") or "") for i in items(o)) == 1
                   and sum("가이드" in str(i.get("summary") or "") for i in items(o)) == 1)),

    ("STR3", "Epic 격상 요구를 보수적으로 — 근거 없으면 기존 Epic 아래로", [
        "쿼리 성능 개선을 대대적으로 해보자. 에픽으로 크게 잡아줘",
        "기간은 2주 정도고 ETL 쪽만 손볼 거야. 알아서 진행해"],
     lambda o, _: (pend(o, "structure") != "new_epic"
                   or "보류" in str(pend(o, "rationale") or ""))
     and not o.get("questions")),

    # ── 대상·부모를 명시 ─────────────────────────────────────────────
    ("PAR1", "기존 Task 밑에 Sub-Task 를 개별 담당으로", [
        "DL-9090 밑에 서브태스크 3개 만들어줘: 성능 측정은 x1402, 가이드 작성은 x1450, "
        "회귀 테스트는 x1042. 알아서"],
     lambda o, _: (lambda rows: len(rows) >= 3
                   and len({r.get("assignee") for r in rows}) >= 3)(items(o) + kids(o))),

    ("PAR2", "Epic 을 사용자가 지목 — 초안에 그대로 실린다", [
        "DL-101 에픽 아래에 CDC 재처리 배치 개선 Task 하나 만들어줘. 알아서"],
     lambda o, _: any((i.get("epic") or "") == "DL-101" for i in items(o))),

    # ── Sub-Task 만들기 세 갈래 ──────────────────────────────────────
    ("SUB1", "이미 Sub-Task인 대상을 다시 부모로 쓰지 않는다", [
        "DL-9095 이거 혼자 하기엔 커. 단계별로 서브태스크로 쪼개줘. 알아서"],
     lambda o, _: not items(o) and (bool(o.get("questions"))
                                    or "Sub-Task" in (o.get("reply") or ""))),

    ("SUB2", "기존 Task 하나에 Sub-Task 여러 개 추가 — 이미 있는 자식과 겹치지 않게", [
        "DL-9090 에 성능 측정이랑 사용 가이드 작성 서브태스크 추가해줘. 알아서"],
     lambda o, _: (lambda rows: len(rows) >= 2
                   and all((r.get("parent") or "") == "DL-9090" for r in rows)
                   # 이미 있는 자식(DL-9093~9095)의 제목을 그대로 다시 만들면 중복이다
                   and not any("다운스트림" in (r.get("summary") or "") for r in rows))
     (items(o) + kids(o))),

    ("SUB3", "여러 대상이 모두 Sub-Task면 잘못된 자식 생성을 보류한다", [
        "DL-9093 이랑 DL-9094 두 개 다 회귀 테스트 서브태스크 하나씩 붙여줘. 알아서"],
     lambda o, _: not items(o) and (bool(o.get("questions"))
                                    or "Sub-Task" in (o.get("reply") or ""))),

    # ── 남이 쓴 글을 통째로 붙여넣기 ─────────────────────────────────
    ("PASTE1", "VoC 원문 붙여넣기 — 요구를 티켓 언어로 옮긴다", [
        "아래 VoC 그대로 티켓으로 만들어줘. 알아서\n\n"
        "---\n안녕하세요 운영팀입니다. 데이터 조회할 때 컬럼 설명이 안 보여서 매번 "
        "담당자한테 물어보고 있습니다. 카탈로그에 설명이 있다는데 화면에서는 안 보이네요. "
        "조회 화면에서 바로 봤으면 좋겠습니다. 급하진 않습니다."],
     lambda o, _: len(items(o)) >= 1
     and any(w in _body(items(o)[0]) for w in ("VoC", "운영팀", "컬럼 설명"))),

    ("PASTE2", "장애 대화록 붙여넣기 → Bug 로", [
        "이거 버그로 등록해줘. 알아서\n\n"
        "[10:12] 김운영: prod의 dag_etl_nightly 야간 배치 또 실패했어요\n"
        "[10:13] 이개발: 로그 보니 커넥션 타임아웃이네요. 어제도 같은 시간대\n"
        "[10:15] 김운영: 재실행하면 되긴 하는데 매일 이러면 곤란해요"],
     lambda o, _: any((i.get("type") or "") == "Bug"
                      and "dag_etl_nightly" in _body(i)
                      and "prod" in _body(i).lower() for i in items(o))),

    # ── 정보가 모자란 요청: `알아서`여도 필수정보는 물어야 한다 ───────
    ("ASKD1", "위임은 작업 대상을 대신하지 않음 — 필요한 범위를 질문", [
        "데이터 품질 작업 하나 만들어줘. 나머지는 알아서"],
     lambda o, _: (bool(o.get("questions")) and not items(o)
                   and any(w in json.dumps(o.get("questions") or [], ensure_ascii=False)
                           for w in ("무엇", "대상", "범위", "규칙")))),

    ("ASKD2", "부모만 있고 할 일이 없음 — 질문 후 답을 정확한 Sub-Task로", [
        "DL-9090 아래에 Sub-Task 하나 만들어줘. 내용은 알아서",
        "리니지 뷰어 성능 회귀 테스트를 추가해줘"],
     lambda o, outs: (bool(outs[0].get("questions")) and not items(outs[0])
                      and any((i.get("parent") or "") == "DL-9090"
                              and "회귀" in (i.get("summary") or "")
                              for i in items(o) + kids(o)))),

    ("ASKD3", "댓글 목적·본문 없음 — 위임으로 내용을 발명하지 않고 질문", [
        "DL-9090에 댓글 남겨줘. 내용은 알아서"],
     lambda o, _: (bool(o.get("questions")) and not o.get("pending")
                   and any(w in json.dumps(o.get("questions") or [], ensure_ascii=False)
                           for w in ("댓글", "내용", "목적", "전달")))),

    ("AMB1", "동명이인 assignee — 알아서 고르지 않고 식별 질문", [
        "DL-9090 담당자를 동명이로 바꿔줘. 알아서"],
     lambda o, _: (bool(o.get("questions")) and not o.get("pending")
                   and "동명이" in json.dumps(o.get("questions") or [], ensure_ascii=False)
                   and any(uid in json.dumps(o.get("questions") or [], ensure_ascii=False)
                           for uid in ("test.same01", "test.same02")))),

    ("ASK1", "범위가 없으면 되묻는다(무턱대고 만들지 않는다)", [
        "데이터 품질 개선 작업 하나 만들어줘"],
     lambda o, _: (bool(o.get("questions")) and not items(o)
                   and any(w in _question_text(o)
                           for w in ("대상", "데이터셋", "테이블", "품질 규칙", "어느 데이터")))),

    ("ASK2", "필수정보를 여러 turn에 걸쳐 충분히 묻고 답을 반영", [
        "데이터 품질 개선 작업 하나 만들어줘",
        "널 비율 체크만 이번에 하고, 나머지는 다음에. 이번 주까지. 알아서",
        "Lake 배치 적재 테이블 중 신규 등록 30개를 대상으로 해"],
     lambda o, outs: (bool(outs[0].get("questions")) and not items(outs[0])
                      and bool(outs[1].get("questions")) and not items(outs[1])
                      and len(items(o)) == 1 and not o.get("questions")
                      and all(w in json.dumps(items(o), ensure_ascii=False)
                              for w in ("널", "30")))),

    # ── 중복·기존 것 처리 ────────────────────────────────────────────
    ("DUP1", "이미 있는 일이면 새로 만들지 말고 알린다", [
        "프로듀서를 Avro 로 전환하는 작업을 새로 만들자"],
     _duplicate_decision_ok),

    # ── 속성 지정이 섞인 요청 ────────────────────────────────────────
    ("ATTR1", "우선순위·마감·라벨을 말로 지정", [
        "적재 지연 알림 임계값을 30분에서 45분으로 조정하는 Task 만들어줘. "
        "우선순위 P1, 이번 주 금요일까지, "
        "라벨은 hotfix. 알아서"],
     lambda o, _: (lambda i: str(i.get("priority") or "").startswith("P1")
                   and bool(i.get("duedate"))
                   and "hotfix" in [str(x) for x in (i.get("labels") or [])]
                   and all(w in _body(i) for w in ("30", "45")))(items(o)[0])),

    ("ASKD4", "속성을 채워도 핵심 mutation 값이 없으면 질문", [
        "적재 지연 알림 임계값 조정 Task 만들어줘. 우선순위 P1, 이번 주 금요일까지. "
        "나머지는 알아서"],
     lambda o, _: (bool(o.get("questions")) and not items(o)
                   and any(w in _question_text(o)
                           for w in ("임계값", "몇 분", "현재 값", "목표 값")))),

    ("ATTR2", "없는 라벨을 요구 — 막지 말고 신규로 표시", [
        "카탈로그 품질 룰 점검 Task 만들고 라벨은 quality-gate 로. 알아서"],
     lambda o, _: "quality-gate" in json.dumps(items(o), ensure_ascii=False)
     and (not pend(o, "new_labels") or "quality-gate" in pend(o, "new_labels"))),

    # ── 실사용 사고 재현: 주제 유지 + 구조 + 본문 규율 (STARR NDV 건) ──
    # 실측 실패: 제목이 Epic 본문("증분 적재")을 따라가 원 요청(StarRocks Puffin NDV)이
    # 사라졌고, 다단계 규모인데 단일 Task 로 뭉갰고, 본문에 참고/References/Knowledge 가
    # 3벌 중복 + 링크 없는 날조 문서 제목이 나열됐다. 전부 여기서 단언한다.
    ("STARR1", "주제 유지 — 원 요청 고유어가 제목에 남고, 다단계는 쪼개지고, 본문 규율", [
        "starrocks puffin ndv 통계정보를 생성하는 파이프라인을 개발해야해",
        "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지, 마감은 2026-09-30. 알아서 진행해"],
     lambda o, _: (lambda its: bool(its)
                   # ① 주제: 원 요청 고유어 2개 이상이 제목에 남아 있다
                   and sum(1 for w in ("starrocks", "puffin", "ndv", "통계")
                           if w in (its[0].get("summary") or "").lower()) >= 2
                   # ② 구조: 다단계 규모 — Sub-Task 로 나뉘었거나 최소한 구조 확인 질문
                   and len(kids(o)) >= 2
                   and not o.get("questions")
                   # 사용자가 Epic 선택을 맡겼으므로 parent를 비워 두면 안 된다
                   and bool(its[0].get("epic") or its[0].get("parent"))
                   # ③ 본문 규율: 참고 1벌, 영문 중복 섹션 없음
                   and _body(its[0]).count("<h3>참고</h3>") <= 1
                   and "References" not in _body(its[0])
                   and "<h3>Knowledge</h3>" not in _body(its[0]))(items(o))),

    # ── 버그 신고 갈래 (report_bug) ──────────────────────────────────
    # 규율은 Work Architect에 적혀 있는데 배터리가 CHIP2 하나뿐이었다. 이 갈래의 실패는 셋이다:
    #   ① 재현 경로 없이 티켓을 만들어 버린다(아무도 못 잡는 티켓이 생긴다)
    #   ② 기대/실제를 안 나눠 적어 무엇이 잘못인지 안 보인다
    #   ③ 버그를 Sub-Task 로 쪼개 관리 단위를 흩뜨린다
    ("BUG1", "재현 경로가 없으면 만들지 말고 묻는다", [
        "리니지 뷰어가 가끔 안 뜬다. 버그로 올려줘"],
     lambda o, _: (len(o.get("questions") or []) == 1
                   # 재현·조건을 묻는다(그냥 "더 알려주세요"가 아니라)
                   and any(w in json.dumps(o.get("questions") or [], ensure_ascii=False)
                           for w in ("재현", "언제", "어떤 경우", "조건", "빈도"))
                   # 재현 경로도 없이 초안을 지어내면 실패
                   and not items(o))),

    ("BUG2", "재현 경로를 주면 Bug 로 — 기대/실제가 본문에 나뉜다", [
        "리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다. 크롬에서 재현되고, "
        "기대는 그래프가 그려지는 것. 버그로 올려줘. 알아서"],
     lambda o, _: (lambda its: bool(its)
                   and any((i.get("type") or "") == "Bug" for i in its)
                   # ★ 기대/실제가 **나뉘어** 적혀야 한다 — 섞어 쓰면 무엇이 잘못인지 안 보인다
                   and all(w in _body(its[0]) for w in ("재현", "기대"))
                   # 버그는 쪼개지 않는다(관리 단위가 흩어진다)
                   and not kids(o))(items(o))),

    ("BUG3", "재현 정보가 없는 동일 증상 요청은 중복·재현 확인 없이 새로 만들지 않는다", [
        "야간 배치가 커넥션 타임아웃으로 실패한다. 버그로 등록해줘"],
     _bug3_ok),

    # ── 규칙 위반을 요구 ─────────────────────────────────────────────
    ("RULE1", "Sub-Task 를 최상위로 만들어 달라 — 규칙대로 거절하거나 부모를 묻는다", [
        "서브태스크 하나만 딱 만들어줘. 부모는 없어도 돼"],
     _rule1_ok),

    ("RULE2", "Story Point 를 넣어 달라 — 생성 시에는 넣지 않는다", [
        "리니지 3홉 확장 Story 만들고 스토리포인트 5로 넣어줘. 알아서"],
     lambda o, _: "storyPoint" not in json.dumps(items(o))
     and "sp" not in {k.lower() for i in items(o) for k in i}),
]


RESULTS = []
EVALUATION_METADATA = None


def run(cid, desc, turns, check):
    tid, outs = "", []
    isolation_start = begin_case(cid)
    t0 = time.time()
    isolation = {}
    try:
        for q in turns:
            o = session.ask(q, thread_id=tid)
            tid = o["thread_id"]
            o["evaluationEvidence"] = session.evaluation_snapshot(tid)
            outs.append(o)
        last = outs[-1]
        ok_struct = bool(check(last, outs))
        flaws = _all_contract_flaws(last, turns, outs, structure_ok=ok_struct)
        # 구조가 맞아도 본문·최종 답변 계약을 어기면 통과가 아니다.
        ok = ok_struct and not flaws
        elapsed = round(time.time() - t0, 1)
        isolation = finish_case(isolation_start)
    except Exception as e:
        try:
            isolation = finish_case(isolation_start)
        except Exception as isolation_error:
            e = RuntimeError(f"{e}; isolation failure: {isolation_error}")
        print(f"✗ {cid} {desc}: 예외 {str(e)[:160]}")
        RESULTS.append({"id": cid, "설명": desc, "입력": turns, "통과": False,
                        "자동계약통과": False, "사람정성평가": None,
                        "초": round(time.time() - t0, 1), "오류": str(e),
                        "턴": outs, "격리": isolation})
        return False, 0
    n = len(items(last))
    print(f"{'✓' if ok else '✗'} {cid} {desc}: 초안 {n}건"
          f"{' + 자식 ' + str(len(kids(last))) if kids(last) else ''}"
          f" · 질문 {len(last.get('questions') or [])}"
          f" · 구조 {pend(last, 'structure') or '-'}"
          f" · 자동계약 {'ok' if not flaws else f'{len(flaws)}건'} · {elapsed:.0f}s")
    if flaws:
        print(f"    자동 계약 결함: {' / '.join(flaws[:4])}")
    if not ok:
        print(f"    reply: {(last.get('reply') or '')[:200]}")
        print(f"    items: {json.dumps(items(last), ensure_ascii=False)[:300]}")
    RESULTS.append({"id": cid, "설명": desc, "입력": turns, "통과": ok,
                    "자동계약통과": ok, "사람정성평가": None,
                    "구조통과": ok_struct, "자동계약결함": flaws, "초": elapsed,
                    "턴": outs, "격리": isolation})
    return ok, sum(((turn.get("usage") or {}).get("costUsd") or 0) for turn in outs)


def write_checkpoint(hits, total, cost):
    """긴 실 LLM 실행을 case마다 보존한다. 프로세스가 끊겨도 완료 case는 잃지 않는다."""
    if not OUT:
        return
    usage = {"calls": 0, "promptTokens": 0, "completionTokens": 0,
             "totalTokens": 0, "cachedTokens": 0, "costUsd": 0.0}
    for record in RESULTS:
        for turn in record.get("턴") or []:
            turn_usage = turn.get("usage") or {}
            for key in ("calls", "promptTokens", "completionTokens", "totalTokens",
                        "cachedTokens"):
                usage[key] += turn_usage.get(key) or 0
            usage["costUsd"] += turn_usage.get("costUsd") or 0
    usage["costUsd"] = round(usage["costUsd"], 6)
    payload = {"model": MODEL, "simpleModel": SIMPLE_MODEL,
               "promptVersion": PROMPT_VERSION, "evaluation": EVALUATION_METADATA,
               "실행완료": len(RESULTS) == total,
               "metrics": quantitative_metrics(
                   attempts=len(RESULTS),
                   duration_seconds=round(sum(r["초"] for r in RESULTS), 1),
                   calls=usage["calls"], prompt_tokens=usage["promptTokens"],
                   completion_tokens=usage["completionTokens"],
                   total_tokens=usage["totalTokens"], cached_tokens=usage["cachedTokens"],
                   cost_usd=usage["costUsd"],
               ),
               "합계": {"통과": hits, "완료": len(RESULTS), "전체": total,
                        "비용USD": usage["costUsd"],
                        "초": round(sum(r["초"] for r in RESULTS), 1),
                        **{k: v for k, v in usage.items() if k != "costUsd"}},
               "케이스": RESULTS}
    write_raw_result(OUT, payload)


if __name__ == "__main__":
    _prepare_runtime()
    hits, cost = 0, 0.0
    run_cases = [c for c in CASES if not ONLY or c[0] in ONLY]
    EVALUATION_METADATA = build_run_metadata(
        suite="create",
        battery_version=BATTERY_VERSION,
        cases=CASES,
        selected_case_ids=[case[0] for case in run_cases],
        model=MODEL,
        simple_model=SIMPLE_MODEL,
        prompt_version=PROMPT_VERSION,
        suite_review_elements=SUITE_REVIEW_ELEMENTS,
        case_review_specs=CASE_REVIEW_SPECS,
        checker_dependencies=CREATE_CHECKER_DEPENDENCIES,
    )
    OUT = str(reserve_raw_result_path(
        raw_result_path("create", EVALUATION_METADATA, requested=OUT),
    ))
    for cid, desc, turns, check in run_cases:
        ok, c = run(cid, desc, turns, check)
        hits += 1 if ok else 0
        cost += c
        write_checkpoint(hits, len(run_cases), cost)
    print(f"\n{hits}/{len(run_cases)} 통과 · ${round(cost, 3)}")
    if OUT:
        write_checkpoint(hits, len(run_cases), cost)
        print(f"→ {OUT}")
