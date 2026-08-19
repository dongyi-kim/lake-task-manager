"""agent/tools — LangChain 도구 레지스트리.

에이전트마다 **주는 도구가 다르다**. 전부 다 주면 두 가지가 망가진다: 도구 설명이 길어져
컨텍스트를 먹고, 무엇보다 ResearchAnalyst 이 티켓을 만들어 버릴 수 있게 된다. 역할 분리는 프롬프트가
아니라 **도구 목록**으로 강제하는 게 확실하다.

`langchain_core` 가 있어야 import 된다 — 이 패키지를 부르기 전에 `config.available()` 로 게이팅한다.
"""

from __future__ import annotations

from app.agent.tools._ctx import bind, client, settings         # noqa: F401
from app.agent.tools.people_tools import (confirm_person, find_person, get_module_people,
                                          get_person_profile, get_team_workload,
                                          get_ticket_participants)
from app.agent.tools.pmo_tools import (find_stale_tickets, find_unassigned_tickets,
                                       get_my_workload, get_progress,
                                       get_user_activity, whoami)
from app.agent.tools.rag_tools import deep_search, search_rules
from app.agent.tools.survey_tools import map_ticket_neighborhood
from app.agent.tools.web_tools import search_github, search_web
from app.agent.tools.search_tools import (find_mentions, find_parent_epic, get_epic_tree,
                                          get_ticket, get_ticket_context, read_document,
                                          run_jql, search_work_history)
from app.agent.tools.query_tools import (query_people, resolve_references, run_jql_v2,
                                         search_comments, search_documents,
                                         set_thread as _set_query_thread)
from app.agent.tools.file_tools import list_attachments, read_attachment
from app.agent.tools.write_tools import (add_ticket_comment, add_ticket_comments,
                                         attach_document, create_epic, create_tickets,
                                         link_tickets, list_child_types, list_ticket_options,
                                         list_transitions, set_thread as _set_write_thread, transition_ticket,
                                         update_ticket, update_tickets, validate_ticket_plan)

# 과거를 뒤진다 — 읽기만. deep_search 는 의미 검색까지 가는 비싼 쪽이라 따로 알아볼 수 있게 뒀다.
SEARCH_TOOLS = [search_work_history, find_mentions, map_ticket_neighborhood, get_ticket,
                list_attachments, read_attachment,
                get_ticket_context, read_document, get_epic_tree, find_parent_epic,
                deep_search, run_jql, run_jql_v2, search_documents, search_comments,
                resolve_references]

# 담당자 근거를 모은다 — 읽기만.
# ★ find_person 이 맨 앞이다 — 사람 이야기는 **이름 해석부터**다. 이 도구가 없어서
#   모델이 모듈 로스터·활동 창으로 밀려나 '있는 사람을 없다'고 답했다(실사용 사고).
PEOPLE_TOOLS = [find_person, confirm_person, query_people, get_team_workload,
                get_ticket_participants, get_person_profile, get_module_people]

# 사내 규칙(정적 RAG). 초안을 짜는 쪽과 검사하는 쪽 **양쪽**이 본다.
RULE_TOOLS = [search_rules]

# PMO 조회 — 진척률·내 일·정체 티켓·타인 활동(매니저 게이트는 도구 안에 있다).
# find_person 이 여기에도 있는 이유: '지금 A가 담당한 테스크들' 은 활동 조회로 분류되기
# 쉬운데(실측), **담당 티켓과 최근 활동은 다른 것**이다. 이름을 풀 수단이 이 역할에
# 없으면 '최근 3일 활동 기록이 없습니다'로 끝난다 — 그 사람이 21건을 들고 있어도.
PMO_TOOLS = [whoami, get_my_workload, get_progress, find_stale_tickets,
             find_unassigned_tickets, get_user_activity, find_person, confirm_person]

# 외부 지식(웹·GitHub) — 일반 기술 지식 보강. 폐쇄망이면 "막혀 있다"를 돌려준다(의존 아님).
WEB_TOOLS = [search_web, search_github]

# Model-facing Role catalogs.  These are deliberately narrower than the reusable capability
# groups above: Query Runner and deterministic prefetch code already perform broad scoped
# retrieval, so Research only receives tools that can fill a newly discovered evidence gap.
# Every schema in these lists is sent on each native/fallback tool-decision call; adding a tool is
# therefore a permission and prompt-budget change and must be justified by a Role contract/test.
RESEARCH_TOOLS = [get_ticket, read_document, search_documents, search_comments,
                  query_people, list_attachments, read_attachment, search_web, search_github]

# Portfolio keeps its existing runtime capabilities, but records them as one exact Role catalog
# instead of approximating them with the much broader ``pmo + people`` manifest declaration.
PORTFOLIO_TOOLS = [*PMO_TOOLS, get_ticket, get_module_people, get_team_workload,
                   run_jql, get_ticket_participants]

# 초안을 검사한다 — 부작용 없음. 몇 번이고 불러도 된다.
REVIEW_TOOLS = [validate_ticket_plan, list_ticket_options, list_child_types, list_transitions]

# 실제로 쓴다 — 전부 approval_token 이 필요하다(agent/approval.py).
WRITE_TOOLS = [create_tickets, create_epic, update_ticket, add_ticket_comment,
               update_tickets, add_ticket_comments,
               transition_ticket, link_tickets, attach_document]

TOOL_GROUPS = {
    "search": SEARCH_TOOLS, "people": PEOPLE_TOOLS, "rule": RULE_TOOLS,
    "pmo": PMO_TOOLS, "web": WEB_TOOLS, "review": REVIEW_TOOLS, "write": WRITE_TOOLS,
    "research": RESEARCH_TOOLS, "portfolio": PORTFOLIO_TOOLS,
}


def _registry(groups: dict[str, list]) -> tuple[list, dict]:
    """같은 tool object의 여러 role 소속은 허용하고, 같은 이름의 다른 구현은 거부한다."""
    ordered, by_name = [], {}
    for group, rows in groups.items():
        for tool_obj in rows:
            name = str(getattr(tool_obj, "name", "") or "")
            if not name:
                raise RuntimeError(f"이름 없는 tool이 있습니다: group={group}")
            previous = by_name.get(name)
            if previous is not None and previous is not tool_obj:
                raise RuntimeError(f"tool 이름 충돌: {name} ({group})")
            if previous is None:
                by_name[name] = tool_obj
                ordered.append(tool_obj)
    return ordered, by_name


ALL_TOOLS, BY_NAME = _registry(TOOL_GROUPS)
_write_names = {t.name for t in WRITE_TOOLS}
READ_TOOLS = [t for t in ALL_TOOLS if t.name not in _write_names]


def set_thread(thread_id: str):
    """승인 토큰과 조회 cursor가 같은 서버 주입 thread id를 사용하게 한다."""
    _set_write_thread(thread_id)
    _set_query_thread(thread_id)

__all__ = ["SEARCH_TOOLS", "PEOPLE_TOOLS", "RULE_TOOLS", "PMO_TOOLS", "WEB_TOOLS", "REVIEW_TOOLS",
           "RESEARCH_TOOLS", "PORTFOLIO_TOOLS", "WRITE_TOOLS", "READ_TOOLS", "ALL_TOOLS", "BY_NAME",
           "TOOL_GROUPS",
           "bind", "client", "settings", "set_thread"]
