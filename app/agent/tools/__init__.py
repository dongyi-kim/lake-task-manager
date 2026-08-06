"""agent/tools — LangChain 도구 레지스트리.

에이전트마다 **주는 도구가 다르다**. 전부 다 주면 두 가지가 망가진다: 도구 설명이 길어져
컨텍스트를 먹고, 무엇보다 Historian 이 티켓을 만들어 버릴 수 있게 된다. 역할 분리는 프롬프트가
아니라 **도구 목록**으로 강제하는 게 확실하다.

`langchain_core` 가 있어야 import 된다 — 이 패키지를 부르기 전에 `config.available()` 로 게이팅한다.
"""

from __future__ import annotations

from app.agent.tools._ctx import bind, client, settings         # noqa: F401
from app.agent.tools.people_tools import (get_module_people, get_person_profile,
                                          get_team_workload, get_ticket_participants)
from app.agent.tools.pmo_tools import (find_stale_tickets, get_my_workload, get_progress,
                                       get_user_activity, whoami)
from app.agent.tools.rag_tools import deep_search, search_rules
from app.agent.tools.web_tools import search_github, search_web
from app.agent.tools.search_tools import (find_parent_epic, get_epic_tree, get_ticket,
                                          get_ticket_context, search_work_history)
from app.agent.tools.write_tools import (add_ticket_comment, attach_document, create_tickets,
                                         link_tickets, list_child_types, list_ticket_options,
                                         list_transitions, set_thread, transition_ticket,
                                         update_ticket, validate_ticket_plan)

# 과거를 뒤진다 — 읽기만. deep_search 는 의미 검색까지 가는 비싼 쪽이라 따로 알아볼 수 있게 뒀다.
SEARCH_TOOLS = [search_work_history, get_ticket, get_ticket_context, get_epic_tree,
                find_parent_epic, deep_search]

# 담당자 근거를 모은다 — 읽기만.
PEOPLE_TOOLS = [get_team_workload, get_ticket_participants, get_person_profile, get_module_people]

# 사내 규칙(정적 RAG). 초안을 짜는 쪽과 검사하는 쪽 **양쪽**이 본다.
RULE_TOOLS = [search_rules]

# PMO 조회 — 진척률·내 일·정체 티켓·타인 활동(매니저 게이트는 도구 안에 있다).
PMO_TOOLS = [whoami, get_my_workload, get_progress, find_stale_tickets, get_user_activity]

# 외부 지식(웹·GitHub) — 일반 기술 지식 보강. 폐쇄망이면 "막혀 있다"를 돌려준다(의존 아님).
WEB_TOOLS = [search_web, search_github]

# 초안을 검사한다 — 부작용 없음. 몇 번이고 불러도 된다.
REVIEW_TOOLS = [validate_ticket_plan, list_ticket_options, list_child_types, list_transitions]

# 실제로 쓴다 — 전부 approval_token 이 필요하다(agent/approval.py).
WRITE_TOOLS = [create_tickets, update_ticket, add_ticket_comment, transition_ticket,
               link_tickets, attach_document]

READ_TOOLS = SEARCH_TOOLS + PEOPLE_TOOLS + RULE_TOOLS + PMO_TOOLS + WEB_TOOLS + REVIEW_TOOLS
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS

BY_NAME = {t.name: t for t in ALL_TOOLS}

__all__ = ["SEARCH_TOOLS", "PEOPLE_TOOLS", "RULE_TOOLS", "PMO_TOOLS", "WEB_TOOLS", "REVIEW_TOOLS",
           "WRITE_TOOLS", "READ_TOOLS", "ALL_TOOLS", "BY_NAME",
           "bind", "client", "settings", "set_thread"]
