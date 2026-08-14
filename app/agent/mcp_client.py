"""agent/mcp_client.py — **외부(public) MCP 서버의 도구**를 에이전트에 붙인다.

우리는 MCP 서버(mcp_server.py)를 노출하는 쪽만 있었다 — 이 모듈은 반대 방향이다:
배포 설정(config/agent-mcp.json)에 적힌 stdio MCP 서버들을 클라이언트로 붙어,
그 도구들을 LangChain `@tool` 모양으로 감싸 ResearchAnalyst 의 조사 도구 옆에 놓는다.
예: `mcp-server-fetch`(웹 페이지 본문 열기 — DuckDuckGo 검색은 스니펫만 준다).

원칙:
  · **fail-soft** — 서버 바이너리가 없거나 폐쇄망이면 그 서버만 조용히 빠진다.
    외부 도구는 보너스이지 의존이 아니다(web_tools 와 같은 태도).
  · **호출마다 새 세션** — stdio 프로세스를 띄우고 한 번 묻고 닫는다. 상주 프로세스를
    스레드 사이에서 공유하면 죽었는지 살았는지 관리가 일이 된다. 느리지만 안전하고,
    조사 한 턴에 외부 도구는 한두 번이다.
  · 절대 규칙은 그대로 — 사내 식별자를 외부 도구 인자에 넣지 않는 것은 프롬프트
    (common.md #5)가 지키고, 여기서는 읽기 호출만 감싼다.

설정 파일(JSON): {"servers": [{"name": "fetch", "command": "uvx",
                               "args": ["mcp-server-fetch"], "enabled": true}]}
위치: 배포 루트 config/ 우선, 없으면 CONFIG_DIR(개발 샘플) — agent-prompt.md 와 같은 규칙.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("agent.mcp")

CALL_TIMEOUT = 25.0            # 외부 프로세스가 멈춰도 조사 전체가 같이 멈추면 안 된다
_lock = threading.Lock()
_cache: dict = {"sig": None, "tools": None}


def _config_path() -> Path | None:
    try:
        from app.infra.settings import BASE_DIR, CONFIG_DIR
        for p in (Path(BASE_DIR) / "config" / "agent-mcp.json",
                  Path(CONFIG_DIR) / "agent-mcp.json"):
            if p.is_file():
                return p
    except Exception:
        pass
    return None


def load_config() -> list[dict]:
    """enabled 인 서버 스펙만. 파일이 없거나 깨졌으면 빈 목록(기본 상태)."""
    p = _config_path()
    if not p:
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        out = []
        for s in (data.get("servers") or []):
            if not isinstance(s, dict) or not s.get("enabled"):
                continue
            if not (s.get("name") and s.get("command")):
                continue
            out.append({"name": str(s["name"]), "command": str(s["command"]),
                        "args": [str(a) for a in (s.get("args") or [])],
                        "env": {str(k): str(v) for k, v in (s.get("env") or {}).items()} or None})
        return out
    except Exception as e:
        log.warning("agent-mcp.json 읽기 실패: %s", e)
        return []


async def _session_do(spec: dict, fn):
    """stdio 서버를 띄워 initialize 하고 fn(session) 을 실행한 뒤 닫는다."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=spec["command"], args=spec["args"], env=spec["env"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


def _run(spec: dict, fn):
    return asyncio.run(asyncio.wait_for(_session_do(spec, fn), CALL_TIMEOUT))


def _list_tools(spec: dict) -> list:
    async def go(s):
        r = await s.list_tools()
        return list(r.tools or [])
    return _run(spec, go)


def _call_tool(spec: dict, name: str, arguments: dict) -> str:
    async def go(s):
        r = await s.call_tool(name, arguments or {})
        parts = []
        for c in (r.content or []):
            text = getattr(c, "text", None)
            parts.append(text if text is not None else str(c))
        out = "\n".join(parts)
        return out[:8000]          # 외부 본문은 길다 — 컨텍스트를 통째로 먹지 않게 자른다
    return _run(spec, go)


def _pyd_model(server: str, tool_name: str, schema: dict):
    """MCP inputSchema(JSON Schema) → pydantic 모델. 모르는 타입은 str 로 — 도구 인자는
    결국 텍스트 직렬화되므로 관대해도 안전하다."""
    from pydantic import Field, create_model
    kinds = {"string": str, "integer": int, "number": float, "boolean": bool,
             "array": list, "object": dict}
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    fields = {}
    for pname, spec_ in props.items():
        t = kinds.get((spec_ or {}).get("type"), str)
        desc = (spec_ or {}).get("description") or ""
        default = ... if pname in required else (spec_ or {}).get("default")
        fields[pname] = (t, Field(default, description=desc))
    if not fields:
        fields["query"] = (str, Field(..., description="Request content"))
    return create_model(f"mcp_{server}_{tool_name}_args", **fields)


def _wrap(spec: dict, t) -> object:
    """MCP 도구 하나 → LangChain StructuredTool. docstring 은 원 서버의 설명 그대로 —
    그게 그 도구의 명세다. 이름에 서버명을 접두해 내부 도구와 충돌하지 않게 한다."""
    from langchain_core.tools import StructuredTool
    server, name = spec["name"], t.name

    def call(**kwargs):
        try:
            return _call_tool(spec, name, kwargs)
        except Exception as e:
            return (f"External MCP tool ({server}/{name}) failed: {str(e)[:200]}. "
                    "Continue with internal research only.")

    desc = (t.description or f"Tool {name} from the {server} server") + \
        " (External MCP tool: never pass internal ticket keys, employee names, or project names.)"
    return StructuredTool.from_function(
        func=call, name=f"mcp_{server}_{name}"[:60], description=desc[:900],
        args_schema=_pyd_model(server, name, getattr(t, "inputSchema", None) or {}))


def tools(refresh: bool = False) -> list:
    """설정된 외부 MCP 서버들의 도구 목록(LangChain). 실패한 서버는 건너뛴다.

    목록은 프로세스당 한 번만 조회해 캐시한다 — list_tools 도 프로세스 기동이라 비싸다.
    설정 파일이 바뀌면 서명이 달라져 다시 읽는다.
    """
    specs = load_config()
    sig = json.dumps(specs, sort_keys=True)
    with _lock:
        if not refresh and _cache["sig"] == sig and _cache["tools"] is not None:
            return list(_cache["tools"])
        out = []
        for spec in specs:
            try:
                for t in _list_tools(spec):
                    out.append(_wrap(spec, t))
                log.info("MCP 서버 '%s' 도구 %d개 연결", spec["name"],
                         sum(1 for x in out if x.name.startswith(f"mcp_{spec['name']}_")))
            except Exception as e:
                log.warning("MCP 서버 '%s' 연결 실패(건너뜀): %s", spec["name"], str(e)[:200])
        _cache.update(sig=sig, tools=out)
        return list(out)


__all__ = ["tools", "load_config"]
