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

import json
import os

os.environ.setdefault("JIRA_ENV", "mock")


def _mcp():
    from mcp.server.fastmcp import FastMCP
    return FastMCP(
        "lake-task-manager",
        instructions=(
            "사내 데이터 플랫폼(Lake) PMO 도구. Jira 티켓·Confluence 문서 검색, 진척률(WBS 롤업), "
            "인력 워크로드, 티켓 작성 규칙을 제공한다. 읽기 전용 — 티켓 생성/변경은 LTM 화면의 "
            "승인 절차를 거쳐야 하므로 여기서는 열리지 않는다."))


# 낼 도구 — 읽기 전용만. 이름은 에이전트 도구와 같다(문서·대화에서 같은 이름으로 통하게).
_EXPORTED = (
    "search_work_history", "get_ticket", "get_ticket_context", "get_epic_tree",
    "search_rules", "get_progress", "get_team_workload", "get_my_workload",
    "find_stale_tickets", "list_ticket_options",
)


def build_server():
    server = _mcp()
    from app.agent import tools as T

    # ── Tools: LangChain @tool → MCP tool. 스키마·설명을 다시 적지 않는다. ──
    # FastMCP 는 **함수 시그니처를 읽어** 입력 스키마를 만든다. `**kwargs` 래퍼를 주면
    # "kwargs 라는 필수 인자"로 오해하므로, LangChain 쪽 스키마에서 진짜 시그니처를 만들어 입힌다.
    import inspect

    for name in _EXPORTED:
        lc_tool = T.BY_NAME[name]

        def make(fn_tool):
            def call(**kwargs) -> str:
                out = fn_tool.invoke(kwargs)
                return json.dumps(out, ensure_ascii=False, default=str)
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
        fn.__signature__ = inspect.Signature(params, return_annotation=str)
        fn.__annotations__ = {**notes, "return": str}

        server.add_tool(fn, name=lc_tool.name, description=lc_tool.description)

    # ── Resources: knowledge/ 규칙 문서 — 질의가 아니라 열람이므로 Tools 가 아니라 여기다. ──
    from app.agent.retrieval.static_index import _sources

    @server.resource("lake://knowledge/{name}", title="LTM 규칙 문서",
                     description="티켓 작성 규칙·진척률 산식·담당자 추천 정책·업무 분해 절차")
    def knowledge_doc(name: str) -> str:
        for p in _sources():
            if p.name == name or p.stem == name:
                return p.read_text(encoding="utf-8")
        names = ", ".join(p.name for p in _sources())
        return f"'{name}' 문서가 없습니다. 있는 문서: {names}"

    @server.resource("lake://knowledge", title="규칙 문서 목록")
    def knowledge_list() -> str:
        return "\n".join(p.name for p in _sources())

    # ── Prompts: 시나리오 시작 템플릿 — 클라이언트에서 골라 쓰는 용도. ──
    @server.prompt(title="업무 착수")
    def plan_work(work: str) -> str:
        """새 업무를 시작하기 전에 과거 이력을 조사하고 티켓 계획을 세운다."""
        return (f"다음 업무를 시작하려 한다: {work}\n\n"
                "search_work_history 로 관련 과거 이력(티켓·문서)을 먼저 찾고, 이미 진행 중이거나 "
                "멈춘 유사 작업이 있는지 확인하라. 그 다음 search_rules 로 티켓 작성 규칙을 확인해 "
                "티켓 트리(무엇을 몇 개, 어느 Epic 아래)를 제안하라. 티켓은 만들지 말고 계획만.")

    @server.prompt(title="버그 정리")
    def report_bug(symptom: str) -> str:
        """버그 증상을 조사해 Bug 티켓에 넣을 내용을 정리한다."""
        return (f"다음 버그가 보고됐다: {symptom}\n\n"
                "search_work_history 로 같은 증상의 기존 티켓이 있는지 먼저 확인하라(중복이면 그 키를 "
                "알려라). 없으면 Bug 티켓에 넣을 내용을 정리하라 — 제목([모듈] 증상), 재현 경로에서 "
                "빠진 정보, 관련 티켓 키. get_team_workload 로 담당 후보도 골라라.")

    @server.prompt(title="오늘 할 일")
    def my_day(user_id: str = "") -> str:
        """지연·마감임박·정체를 기준으로 오늘 집중할 일을 고른다."""
        who = f'user_id="{user_id}" 로' if user_id else "user_id 없이(세션 사용자로)"
        return (f"get_my_workload 를 {who} 불러 일감을 받아라. overdue(지연) → 오늘/내일 마감 "
                "→ staleDays 큰 것(정체) → 우선순위(P1>P2) 순으로 오늘 집중할 3~5건을 골라 "
                "이유와 함께 제시하라.")

    @server.prompt(title="진척 점검")
    def check_progress(target: str = "") -> str:
        """Epic·모듈·전체의 진척률과 그 원인을 본다."""
        return (f"get_progress 를 target=\"{target}\" 로 불러 진척률을 확인하라. 숫자가 낮은 곳은 "
                "get_epic_tree 로 안을 들여다보고, 분모 규칙(Bug·VoC·Epic Link 없음 제외)을 감안해 "
                "'왜 이 숫자인가'까지 설명하라.")

    return server


def main():
    build_server().run()        # stdio — MCP 클라이언트가 이 프로세스를 띄우고 파이프로 말한다


if __name__ == "__main__":
    main()
