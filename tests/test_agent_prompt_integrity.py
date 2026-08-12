"""역할 프롬프트(md)와 실제 역할 코드가 어긋나지 않는지 — **구조적 방어장치**.

왜 테스트로 두는가: 성능 라운드에서 WorkArchitect·PeopleAdvisor 를 `ToolAgent` → `StructuredAgent`
로 바꾸며 도구를 전부 걷어냈는데, **md 는 그대로 남아** "먼저 `search_rules` 를 불러라",
"`get_module_people` 로 후보를 모아라" 하고 열 군데서 시켰다. 코드는 멀쩡히 돌고 테스트도
전부 통과하니 아무도 몰랐다 — 모델만 없는 도구를 찾아 헤맸다.

이런 어긋남은 사람이 md 를 읽어야만 보이고, 읽는 일은 잊힌다. 기계가 본다.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.agent.workflow.agents.people_advisor import PeopleAdvisor
from app.agent.workflow.agents.knowledge_curator import KnowledgeCurator
from app.agent.workflow.agents.research_analyst import ResearchAnalyst
from app.agent.workflow.agents.action_executor import ActionExecutor
from app.agent.workflow.agents.request_architect import RequestArchitect
from app.agent.workflow.agents.portfolio_analyst import PortfolioAnalyst
from app.agent.workflow.agents.query_specialist import QuerySpecialist
from app.agent.workflow.agents.work_architect import WorkArchitect
from app.agent.workflow.agents.result_integrator import ResultIntegrator
from app.agent.workflow.agents.auditor import Auditor

ROLES = {
    "request_architect": RequestArchitect, "research_analyst": ResearchAnalyst, "work_architect": WorkArchitect,
    "people_advisor": PeopleAdvisor, "auditor": Auditor, "action_executor": ActionExecutor,
    "result_integrator": ResultIntegrator, "portfolio_analyst": PortfolioAnalyst,
    "knowledge_curator": KnowledgeCurator,
    "query_specialist": QuerySpecialist,
}
MD_DIR = pathlib.Path(__file__).resolve().parents[1] / "app/agent/prompts/roles"

# 도구를 **부르라는 지시가 아닌** 줄 — 금지 예시("Wrong: …")는 도구명이 나와도 정상이다.
_ANTI_EXAMPLE = re.compile(r"^\s*(?:Wrong|Right|나쁜 예|좋은 예)\s*:")


def _all_tool_names() -> set:
    from app.agent import tools as T
    return set(T.BY_NAME)


def _own_tool_names(role) -> set:
    try:
        return {t.name for t in role().tools}
    except Exception:                       # pragma: no cover - 도구 없는 역할
        return set()


@pytest.mark.parametrize("name", sorted(ROLES))
def test_role_md_does_not_order_tools_the_role_lacks(name):
    """md 가 시키는 도구는 그 역할이 실제로 가진 것이어야 한다."""
    p = MD_DIR / f"{name}.md"
    if not p.exists():
        pytest.skip(f"{name}.md 없음")
    known, own = _all_tool_names(), _own_tool_names(ROLES[name])
    # Query Specialist는 실행자가 아니라 Query Runner용 typed query contract를 작성한다.
    # 따라서 downstream tool 이름을 명세할 수 있지만 직접 호출 금지는 별도로 강제한다.
    if name == "query_specialist":
        text = p.read_text(encoding="utf-8")
        assert "도구가 없" in text and "직접 호출하지 않는다" in text
        return
    ghosts = {}
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if _ANTI_EXAMPLE.match(line):
            continue
        for m in set(re.findall(r"`([a-z_]{4,})`", line)) | set(
                re.findall(r"\b([a-z_]{4,})\(", line)):
            if m in known and m not in own:
                ghosts.setdefault(m, i)
    assert not ghosts, (
        f"{name}.md 가 이 역할에 없는 도구를 부르라고 지시한다: "
        + ", ".join(f"{k}(L{v})" for k, v in sorted(ghosts.items()))
        + f" — 이 역할의 도구는 {sorted(own) or '없음'}. "
        "도구를 걷어냈다면 md 도 '재료는 이미 자료에 있다'로 고쳐야 한다.")


# **조회하던 것을 코드 사전취합으로 옮긴** 역할들. 이들에게만 "도구가 없다"는 선언을
# 요구한다 — 그전까지 md 가 "먼저 불러라"고 시키던 자리라서, 없다고 못 박지 않으면 모델이
# "확인해 보겠습니다"로 답하거나 조회한 척한다.
# (RequestArchitect·Auditor·KnowledgeCurator·ResultIntegrator 는 처음부터 도구가 없었고 md 도 조회를 시킨 적이
#  없으므로 대상이 아니다 — 안 하던 말을 새로 넣는 건 토큰만 늘린다.)
_CONVERTED_TO_MATERIALS = ("work_architect", "people_advisor")


@pytest.mark.parametrize("name", _CONVERTED_TO_MATERIALS)
def test_converted_role_md_declares_it_has_no_tools(name):
    assert not _own_tool_names(ROLES[name]), f"{name} 이 다시 도구를 갖게 됐다 — 이 테스트를 고쳐라"
    text = (MD_DIR / f"{name}.md").read_text(encoding="utf-8")
    assert re.search(r"NO tools|no tools|도구가 없다|도구를 쓰지", text), (
        f"{name}.md 에 '도구가 없다'는 선언이 없다 — 재료는 코드가 미리 실어 주는데 "
        "모델은 그걸 모른 채 조회하려 든다.")


# ── 모듈 목록: config 가 원본이고 md 는 사본이다 ─────────────────────────
# 실측: config(dev·prod 양쪽)에는 모듈이 7개인데 common.md·knowledge/03 은 6개로 적혀
# `Observability` 가 통째로 빠져 있었다. 그 모듈의 인력 2명은 담당 추천 지침에서 보이지
# 않았고, 모델이 Jira 컴포넌트 목록에서 Observability 를 집으면 자기 지시와 모순됐다.
# 도구 목록이 갈라진 §5-c 사고와 같은 부류라 같은 방식으로 막는다.
_MODULE_DOCS = ("app/agent/prompts/common.md",
                "app/agent/prompts/common-lite.md",
                "knowledge/01-ticket-rules.md",
                "knowledge/03-modules-and-people.md")
# TEST 는 UI 회귀 픽스처 전용(개발 world 한정) — prod config 에 없고 md 에도 없어야 한다.
_DEV_ONLY_MODULES = {"TEST"}


@pytest.mark.parametrize("rel", _MODULE_DOCS)
def test_module_list_in_docs_matches_the_roster_config(rel):
    from app.infra.settings import load_people
    root = pathlib.Path(__file__).resolve().parents[1]
    text = (root / rel).read_text(encoding="utf-8")
    want = set(load_people() or {}) - _DEV_ONLY_MODULES
    missing = sorted(m for m in want if not re.search(rf"\b{re.escape(m)}\b", text))
    assert not missing, (
        f"{rel} 에 모듈 {missing} 이 없다 — config/people.yaml 이 원본이고 이 문서는 사본이다. "
        "빠진 모듈은 담당 추천·배치 판단에서 통째로 보이지 않는다.")
    leaked = sorted(m for m in _DEV_ONLY_MODULES if re.search(rf"\b{re.escape(m)}\b", text))
    assert not leaked, f"{rel} 에 개발 전용 모듈 {leaked} 이 새어 들어갔다"


def test_every_role_md_is_loaded_by_the_loader():
    """roles/ 의 md 는 전부 로더 상수로 노출돼야 한다 — 고아 파일은 조용히 안 쓰인다."""
    from app.agent.prompts import roles as R
    loaded = {v for k, v in vars(R).items() if k.startswith("SYSTEM_") and isinstance(v, str)}
    for p in sorted(MD_DIR.glob("*.md")):
        body = p.read_text(encoding="utf-8").strip()
        assert body in loaded, f"{p.name} 이 어떤 SYSTEM_* 상수로도 로드되지 않는다"


# ── 한국어 원문형 prompt + 기계 식별자 보존 ──────────────────────────────
def test_role_prompts_are_korean_originals_not_legacy_english_blocks():
    """자연어 지시를 한국어 원문형으로 관리한다.

    code/tool/schema 식별자의 영어는 정상이다. 여기서는 과거 prompt의 영문 지시문 머리말과
    강제어가 통째로 되살아나는 회귀만 잡는다.
    """
    banned = ("You are ", "Your job", "## Hard rules", "## Output format",
              "## Grounding", "## Steps", "NEVER ", "Do NOT ")
    for p in sorted(MD_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        assert re.search(r"[가-힣]", text), f"{p.name} 에 한국어 자연어 지시가 없다"
        found = [token for token in banned if token in text]
        assert not found, f"{p.name} 에 기존 영문 지시 block이 남았다: {found}"


def test_machine_contract_identifiers_survive_korean_refactor():
    """한국어로 다시 써도 function/parameter/schema/enum/Jira 계약은 원형을 지킨다."""
    from app.agent.prompts.base import BASE_PERSONA, PROMPT_VERSION
    from app.agent.prompts.roles import (SYSTEM_EDITOR_AUTHOR, SYSTEM_RESEARCH_ANALYST,
                                         SYSTEM_ACTION_EXECUTOR, SYSTEM_REQUEST_ARCHITECT,
                                         SYSTEM_WORK_ARCHITECT)

    assert PROMPT_VERSION == "ko-role-contract-v3"
    for token in ("approval_token", "statusCategory", "Epic Link", "Story Point",
                  "Sub-Task", "PMO_VIT"):
        assert token in BASE_PERSONA, f"공통 계약에서 식별자 {token!r}가 번역·유실됐다"

    for token in ("plan_work", "ask", "my_day", "progress", "activity", "modify",
                  "chitchat", 'playbook="bug_report"'):
        assert token in SYSTEM_REQUEST_ARCHITECT, f"RequestArchitect enum {token!r}가 번역·유실됐다"

    for token in ("destination_project", "temp_id", "tier", "issue_type", "parent_ref",
                  "structure_plan", 'mode="subtask"', "questions=[]", "summary",
                  "description", "children", "parent", "rationale"):
        assert token in SYSTEM_WORK_ARCHITECT, f"WorkArchitect schema 식별자 {token!r}가 번역·유실됐다"

    for token in ("get_ticket", "read_document", "run_jql_v2", "search_documents",
                  "search_comments", "query_people", "pagination"):
        assert token in SYSTEM_RESEARCH_ANALYST, f"ResearchAnalyst tool {token!r}가 번역·유실됐다"

    for token in ("<h3>", 'data-type="taskList"', 'data-checked="false"',
                  "typed reference", "{{ref:id}}", "{{mention:id}}", "[~사번]"):
        assert token in SYSTEM_EDITOR_AUTHOR, f"Composer markup {token!r}가 번역·유실됐다"

    for token in ("approval_token", "mode=task", "create_tickets", "created"):
        assert token in SYSTEM_ACTION_EXECUTOR, f"ActionExecutor 실행 계약 {token!r}가 번역·유실됐다"


def test_prompt_exposes_the_enforced_ticket_action_contract():
    """사람/model 문서가 domain validator와 다른 field/status 규칙을 말하지 않는다."""
    from app.agent.prompts.base import BASE_PERSONA
    from app.domain.ticket_actions import CREATE_FIELDS, EDITABLE_FIELDS
    for fields in CREATE_FIELDS.values():
        for field in fields:
            assert f"`{field}`" in BASE_PERSONA
    for field in EDITABLE_FIELDS:
        assert f"`{field}`" in BASE_PERSONA
    for token in ("Epic", "Task", "Sub-Task", "statusCategory == done", "Reopened",
                  "댓글은 남길 수"):
        assert token in BASE_PERSONA


def test_evaluation_harnesses_preserve_production_model_routing():
    """Prompt 후보 사이에서는 model topology가 아니라 prompt만 달라야 한다."""
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("tools/agent_lang_ab.py", "tools/agent_compose_eval.py",
                "tools/agent_create_suite.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert 'setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")' in text, (
            f"{rel}가 production simple tier를 고정하지 않는다")
        assert 'setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", MODEL)' not in text, (
            f"{rel}가 main model을 simple tier에 강제로 덮어쓴다")
