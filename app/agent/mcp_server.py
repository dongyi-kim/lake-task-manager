"""agent/mcp_server.py — LTM 도구를 **MCP(Model Context Protocol)** 로 노출한다.

## 왜 MCP 인가

우리 에이전트는 LTM 안에서 돈다. 그런데 팀에는 이미 다른 AI 클라이언트(Claude Desktop,
IDE 의 코딩 어시스턴트)를 쓰는 사람들이 있다 — 그들이 LTM 의 검색·진척률·워크로드를 쓰려면
지금은 화면을 열어 눈으로 옮겨야 한다. MCP 서버를 세우면 **MCP 를 말하는 어떤 클라이언트든**
우리 도구를 그대로 쓴다. 도구를 클라이언트 수만큼 다시 만드는 대신 프로토콜 하나로 낸다.

## Primitives 세 종을 어떻게 나눴나

  · **Tools**     — 읽기 전용 에이전트 도구 그대로(검색·티켓·진척률·워크로드·규칙 검색).
                    LangChain `@tool` 의 이름/설명/스키마를 **한 번 더 적지 않고** 변환한다 —
                    두 벌이 되면 반드시 갈라진다.
  · **Resources** — `knowledge/` 규칙 문서. 도구(질의)가 아니라 문서(열람)라서 Resources 가 맞다.
                    클라이언트가 통째로 읽어 컨텍스트에 싣는 용도다.
  · **Prompts**   — 우리 시나리오 4종(업무 착수·버그 신고·오늘 할 일·진척도)의 시작 프롬프트.
                    사용자가 클라이언트에서 골라 쓰는 템플릿이다.

## 쓰기 도구는 내지 않는다

MCP 클라이언트에는 우리 승인 카드(HITL 화면)가 없다. 승인 토큰을 발급받을 길이 없으므로
쓰기 도구를 내 봤자 전부 거부되고, 우회로를 열면 HITL 이 뚫린다. **읽기만 낸다.**
(외부 클라이언트에서의 쓰기는 그 클라이언트의 확인 UX 가 생기면 그때 여는 것이 맞다.)

실행:
    python -m app.agent.mcp_server          # stdio — MCP 클라이언트 설정에 이 명령을 적는다

Claude Desktop 설정 예(claude_desktop_config.json):
    {"mcpServers": {"lake-task-manager": {
        "command": "python", "args": ["-m", "app.agent.mcp_server"],
        "cwd": "<이 저장소 경로>", "env": {"JIRA_ENV": "mock"}}}}
"""

from __future__ import annotations

import os

os.environ.setdefault("JIRA_ENV", "mock")


def _mcp():
    from mcp.server import MCPServer
    return MCPServer(
        "lake-task-manager",
        instructions=(
            "Read-only PMO tools for the internal Lake data platform. Search Jira tickets and "
            "Confluence documents, calculate WBS progress, inspect team workload, and retrieve "
            "ticket-authoring policy. Ticket creation and mutation are unavailable because they "
            "require the approval flow in the LTM UI."))


# 낼 도구 — 읽기 전용만. 이름은 에이전트 도구와 같다(문서·대화에서 같은 이름으로 통하게).
_EXPORTED = (
    "search_work_history", "get_ticket", "get_ticket_context", "get_epic_tree",
    "search_rules", "get_progress", "get_team_workload", "get_my_workload",
    "find_stale_tickets", "list_ticket_options",
)


def build_server():
    server = _mcp()
    from mcp.types import ToolAnnotations
    from app.agent import tools as T

    # ── Tools: LangChain @tool → MCP tool. 스키마·설명을 다시 적지 않는다. ──
    # MCPServer 는 **함수 시그니처를 읽어** 입력 스키마를 만든다. `**kwargs` 래퍼를 주면
    # "kwargs 라는 필수 인자"로 오해하므로, LangChain 쪽 스키마에서 진짜 시그니처를 만들어 입힌다.
    import inspect

    for name in _EXPORTED:
        lc_tool = T.BY_NAME[name]

        def make(fn_tool):
            def call(**kwargs) -> dict[str, object]:
                out = fn_tool.invoke(kwargs)
                return out if isinstance(out, dict) else {"result": out}
            return call

        fn = make(lc_tool)
        fn.__name__ = lc_tool.name
        fn.__doc__ = lc_tool.description

        params, notes = [], {}
        fields = getattr(lc_tool.args_schema, "model_fields", None) or {}
        for pname, field in fields.items():
            required = field.is_required()
            params.append(inspect.Parameter(
                pname, inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if required else field.default,
                annotation=field.annotation if field.annotation is not None else str))
            notes[pname] = field.annotation
        fn.__signature__ = inspect.Signature(params, return_annotation=dict[str, object])
        fn.__annotations__ = {**notes, "return": dict[str, object]}

        server.add_tool(
            fn, name=lc_tool.name, description=lc_tool.description,
            annotations=ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, openWorldHint=False,
            ),
            structured_output=True,
        )

    # ── Resources: knowledge/ 규칙 문서 — 질의가 아니라 열람이므로 Tools 가 아니라 여기다. ──
    from app.agent.retrieval.static_index import _sources

    @server.resource("lake://knowledge/{name}", title="LTM policy document",
                     description=("Ticket-authoring policy, progress calculation, assignee "
                                  "selection, and work decomposition guidance"))
    def knowledge_doc(name: str) -> str:
        for p in _sources():
            if p.name == name or p.stem == name:
                return p.read_text(encoding="utf-8")
        names = ", ".join(p.name for p in _sources())
        return f"Document '{name}' was not found. Available documents: {names}"

    @server.resource("lake://knowledge", title="Policy document index")
    def knowledge_list() -> str:
        return "\n".join(p.name for p in _sources())

    # ── Prompts: 시나리오 시작 템플릿 — 클라이언트에서 골라 쓰는 용도. ──
    @server.prompt(title="Plan new work")
    def plan_work(work: str) -> str:
        """Research history and propose a ticket plan before starting new work."""
        return (f"The user wants to start this work: {work}\n\n"
                "First call search_work_history for related tickets and documents. Check whether "
                "similar work is active or stalled. Then call search_rules and propose a ticket "
                "tree: which tickets, how many, and under which Epic. Do not create tickets; return "
                "a plan in Korean.")

    @server.prompt(title="Prepare a Bug report")
    def report_bug(symptom: str) -> str:
        """Research a symptom and prepare the content for a Bug ticket."""
        return (f"The user reported this symptom: {symptom}\n\n"
                "Call search_work_history to check for an existing ticket with the same symptom. "
                "If it is a duplicate, identify that key. Otherwise prepare the Bug content: title "
                "in `[module] symptom` form, missing reproduction details, and related ticket keys. "
                "Use get_team_workload for evidence-backed assignee candidates. Respond in Korean.")

    @server.prompt(title="Prioritize today's work")
    def my_day(user_id: str = "") -> str:
        """Choose today's focus using overdue, imminent, and stale work."""
        who = f'with user_id="{user_id}"' if user_id else "without user_id, using the session user"
        return (f"Call get_my_workload {who}. Select three to five items in this order: overdue, "
                "due today or tomorrow, largest staleDays, then priority P1 before P2. Give a short "
                "reason for each and respond in Korean.")

    @server.prompt(title="Inspect progress")
    def check_progress(target: str = "") -> str:
        """Inspect Epic-, module-, or portfolio-level progress and its causes."""
        return (f"Call get_progress with target=\"{target}\". For low-progress areas, inspect the "
                "contents with get_epic_tree. Apply denominator exclusions for Bug, VoC, and work "
                "without Epic Link, then explain why the percentage has that value. Respond in Korean.")

    return server


def main():
    build_server().run()        # stdio — MCP 클라이언트가 이 프로세스를 띄우고 파이프로 말한다


if __name__ == "__main__":
    main()
