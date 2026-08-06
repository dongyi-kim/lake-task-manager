"""(이동됨) → `app.agent.prompts`. 기존 import 경로를 살리는 얇은 다리.

프롬프트는 에이전트의 자산이라 workflow(그래프 배선) 밑이 아니라 agent 바로 아래가 자리다.
"""
from app.agent.prompts import *              # noqa: F401,F403
from app.agent.prompts import base as _b     # noqa: F401
