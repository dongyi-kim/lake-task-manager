"""MCP 서버 — Primitives 3종이 실제로 서빙되는가.

MCP SDK 의 in-memory 클라이언트로 **프로토콜을 실제로 왕복**한다. build_server() 가 안 죽는 것과
클라이언트가 도구를 부를 수 있는 것은 다른 문제다 — 스키마 변환이 깨져 있으면 목록은 나오는데
호출이 실패한다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("mcp", reason="mcp SDK 미설치")
pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

import anyio                                             # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from app.agent.mcp_server import _EXPORTED, build_server  # noqa: E402


def _run(coro_fn):
    return anyio.run(coro_fn)


@pytest.fixture(scope="module")
def server():
    return build_server()._mcp_server


def test_all_read_tools_are_listed(server):
    async def go():
        async with create_connected_server_and_client_session(server) as c:
            r = await c.list_tools()
            return {t.name for t in r.tools}
    names = _run(go)
    assert set(_EXPORTED) <= names


def test_no_write_tool_leaks_through(server):
    """MCP 클라이언트에는 승인 카드가 없다 — 쓰기가 새어 나가면 HITL 이 뚫린다."""
    async def go():
        async with create_connected_server_and_client_session(server) as c:
            r = await c.list_tools()
            return {t.name for t in r.tools}
    names = _run(go)
    from app.agent import tools as T
    for w in T.WRITE_TOOLS:
        assert w.name not in names, f"쓰기 도구 {w.name} 가 MCP 로 새어 나갔다"


def test_a_tool_call_round_trips(server):
    async def go():
        async with create_connected_server_and_client_session(server) as c:
            r = await c.call_tool("search_work_history", {"query": "데이터", "limit": 3})
            return r.content[0].text
    body = _run(go)
    assert '"jira"' in body


def test_progress_tool_round_trips_with_numbers(server):
    async def go():
        async with create_connected_server_and_client_session(server) as c:
            r = await c.call_tool("get_progress", {"target": ""})
            return r.content[0].text
    body = _run(go)
    assert "overallPct" in body


def test_knowledge_resources_are_readable(server):
    async def go():
        async with create_connected_server_and_client_session(server) as c:
            r = await c.read_resource("lake://knowledge/01-ticket-rules.md")
            return r.contents[0].text
    text = _run(go)
    assert "Story Point" in text


def test_scenario_prompts_are_listed_and_render(server):
    async def go():
        async with create_connected_server_and_client_session(server) as c:
            lst = await c.list_prompts()
            names = {p.name for p in lst.prompts}
            got = await c.get_prompt("report_bug", {"symptom": "적재 배치 실패"})
            return names, got.messages[0].content.text
    names, text = _run(go)
    assert {"plan_work", "report_bug", "my_day", "check_progress"} <= names
    assert "적재 배치 실패" in text and "중복" in text
