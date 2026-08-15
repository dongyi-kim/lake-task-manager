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
    assert "적재 배치 실패" in text and "duplicate" in text.lower()
    assert "Respond in Korean" in text


# ── MCP 클라이언트(외부 서버 소비) — 자체 서버를 외부인 척 띄워 실왕복한다 ──────────
def test_mcp_client_wraps_external_server_tools(monkeypatch, tmp_path):
    """config 에 적힌 stdio MCP 서버의 도구가 LangChain 도구로 감싸져 실제 호출까지 된다.

    네트워크 없이 검증하기 위해 **우리 MCP 서버**를 외부 서버인 것처럼 설정에 적는다 —
    프로세스 기동·initialize·list_tools·call_tool 전 구간이 실물로 돈다.
    """
    import json as _json
    import os as _os
    import sys as _sys
    from pathlib import Path as _P
    from app.agent import mcp_client
    root = str(_P(__file__).resolve().parent.parent)
    cfg = tmp_path / "agent-mcp.json"
    cfg.write_text(_json.dumps({"servers": [{
        "name": "self", "command": _sys.executable,
        "args": ["-m", "app.agent.mcp_server"],
        "env": {**_os.environ, "PYTHONPATH": root, "JIRA_ENV": "mock",
                "LAKE_AGENT_PROVIDER": "fake"},
        "enabled": True}]}), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "_config_path", lambda: cfg)
    tools = mcp_client.tools(refresh=True)
    assert tools, "외부 서버 도구가 하나도 안 붙었다"
    names = {t.name for t in tools}
    assert any(n.startswith("mcp_self_") for n in names), names
    # 읽기 도구 하나를 실제로 부른다 — 검색은 mock world 를 상대로 실데이터를 돌려준다
    search = next(t for t in tools if "search" in t.name)
    out = search.invoke({"query": "데이터"})
    assert isinstance(out, str) and len(out) > 10 and "실패" not in out[:30], out[:200]


def test_mcp_client_is_failsoft_without_config_or_server(monkeypatch, tmp_path):
    """설정이 없으면 빈 목록, 서버 실행 파일이 없으면 그 서버만 조용히 빠진다."""
    import json as _json
    from app.agent import mcp_client
    monkeypatch.setattr(mcp_client, "_config_path", lambda: None)
    assert mcp_client.tools(refresh=True) == []
    bad = tmp_path / "agent-mcp.json"
    bad.write_text(_json.dumps({"servers": [{"name": "ghost", "command": "no-such-binary-xyz",
                                             "enabled": True}]}), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "_config_path", lambda: bad)
    assert mcp_client.tools(refresh=True) == []      # 예외가 아니라 빈 목록
