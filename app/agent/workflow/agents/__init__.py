"""app/agent/workflow/agents — 역할별 에이전트(서브그래프).

공통 뼈대는 `base.py` — 판단만 하는 역할은 `StructuredAgent`, 도구를 부르며 걷는 역할은
`ToolAgent`(ReAct), 사용자에게 보일 문장을 만드는 역할은 `TextAgent`.
"""
