"""app/agent/prompts — 에이전트의 **모든 프롬프트 자산**이 모이는 곳.

프롬프트는 코드이자 문서다. 역할 클래스 안에 흩어져 있으면 "우리 에이전트가 무슨 지시를
받고 있나"를 보려고 여덟 파일을 열어야 한다. 여기 모아 두면 한 번에 읽고 한 번에 고친다.

- base.py   공통: 페르소나(BASE_PERSONA) · 역할 힌트 · 자료 구분(DATA_HEADER, 인젝션 방어)
- roles.py  역할별 시스템 지시(SYSTEM_*) — Planner/Historian/Refiner/Assigner/Reviewer/
            Operator/Responder/PMO 가 자기 것을 가져다 쓴다

task(명령서) 는 State 를 인자로 받아 조립해야 해서 각 역할 파일에 남아 있지만,
**정적인 지시문**은 전부 여기서 온다.
"""

from app.agent.prompts.base import (BASE_PERSONA, DATA_HEADER, ROLE_HINT,   # noqa: F401
                                    data_block, persona, wrap_data)
