"""Transparent stdio-to-running-LTM proxy. Never starts LTM or creates a Jira client."""

import os
import sys
from urllib.parse import urlsplit

import anyio
import httpx2
from mcp.client.streamable_http import streamable_http_client
from mcp.server.stdio import stdio_server


def connection_settings():
    url, token = os.getenv("LTM_MCP_URL", ""), os.getenv("LTM_MCP_TOKEN", "")
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or not parsed.port or parsed.path != "/mcp" or parsed.query or parsed.fragment
            or parsed.username or parsed.password or not token):
        raise ValueError("실행 중인 LTM 설정에서 인증 포함 stdio 연결 설정을 복사해 주세요.")
    return url, token


async def proxy():
    url, token = connection_settings()
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=120,
                                  follow_redirects=False, trust_env=False) as http:
        async with stdio_server() as (incoming, outgoing), streamable_http_client(url, http_client=http) as (reads, writes):
            async with anyio.create_task_group() as group:
                async def forward(source, target):
                    async for message in source:
                        if isinstance(message, Exception):
                            raise RuntimeError("LTM MCP 연결을 완료하지 못했습니다.") from None
                        await target.send(message)
                    group.cancel_scope.cancel()
                group.start_soon(forward, incoming, writes)
                group.start_soon(forward, reads, outgoing)


def main():
    try:
        anyio.run(proxy)
    except (Exception, KeyboardInterrupt):
        # Transport exceptions can embed headers/URLs. Do not print their repr or traceback.
        print("LTM MCP에 연결하지 못했습니다. LTM 실행 여부와 설정의 주소·인증 토큰을 확인하세요.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
