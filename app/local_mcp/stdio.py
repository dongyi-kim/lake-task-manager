"""Transparent stdio-to-running-LTM proxy. Never starts LTM or creates a Jira client."""

import os
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import anyio
import httpx2
from mcp.client.streamable_http import StreamableHTTPTransport
from mcp.server.stdio import stdio_server
from mcp.shared._context_streams import create_context_streams
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, INTERNAL_ERROR, JSONRPCError, JSONRPCRequest


class ResilientHTTPTransport(StreamableHTTPTransport):
    """Keep one failed POST from cancelling the SDK transport's entire task group."""

    async def _handle_post_request(self, context):
        try:
            await super()._handle_post_request(context)
        except (httpx2.TransportError, OSError, TimeoutError):
            message = context.session_message.message
            if not isinstance(message, JSONRPCRequest):
                return  # Notifications have no response; the next request can reconnect.
            error = ErrorData(code=INTERNAL_ERROR,
                message="LTM 연결을 완료하지 못했습니다. 연결 복구 후 읽기를 재시도하세요. "
                        "변경 요청은 완료됐을 수 있으며 자동 재전송하지 않았습니다.",
                data={"code": "ltm_connection_unavailable", "retryable": False,
                      "retryReadOnly": True, "retryAfterSeconds": 1,
                      "executionUnknown": True, "requestReplayed": False, "repeatWrite": False})
            await context.read_stream_writer.send(SessionMessage(JSONRPCError(
                jsonrpc="2.0", id=message.id, error=error)))


@asynccontextmanager
async def resilient_http_client(url, http):
    """Use the pinned SDK's protocol/cancellation machinery with a per-POST boundary.

    LTM HTTP endpoints are stateless. Retaining this transport keeps negotiated
    protocol state and opens a new HTTP connection on the next caller's request;
    a failed business POST is never placed back on the write stream.
    """
    transport = ResilientHTTPTransport(url)
    read_writer, reads = create_context_streams(0)
    writes, write_reader = create_context_streams(0)
    async with read_writer, reads, writes, write_reader, anyio.create_task_group() as tasks:
        def get_stream():
            tasks.start_soon(transport.handle_get_stream, http, read_writer)
        tasks.start_soon(transport.post_writer, http, write_reader, read_writer, writes, get_stream, tasks)
        try:
            yield reads, writes
        finally:
            if transport.session_id:
                await transport.terminate_session(http)
            tasks.cancel_scope.cancel()


def connection_settings():
    url, token = os.getenv("LTM_MCP_URL", ""), os.getenv("LTM_MCP_TOKEN", "")
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or not parsed.port or parsed.path not in ("/mcp", "/mcp/jira", "/mcp/confluence", "/mcp/reporting", "/mcp/settings") or parsed.query or parsed.fragment
            or parsed.username or parsed.password or not token):
        raise ValueError("실행 중인 LTM 설정에서 인증 포함 stdio 연결 설정을 복사해 주세요.")
    return url, token


async def proxy():
    url, token = connection_settings()
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=120,
                                  follow_redirects=False, trust_env=False) as http:
        async with stdio_server() as (incoming, outgoing), resilient_http_client(url, http) as (reads, writes):
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
