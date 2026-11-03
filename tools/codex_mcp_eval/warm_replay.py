"""Measure cached HTTP reads after the paid answer; never count this work as Agent latency."""
from __future__ import annotations

import json
import time

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


def replay(fixture, calls, repetitions=3):
    allowed = {tool["name"] for tool in fixture.tool_catalog
               if (tool.get("annotations") or {}).get("readOnlyHint") is True}
    unique = {}
    for call in calls:
        if call.get("name") not in allowed or call.get("isError") or not (call.get("result") or {}).get("ok"):
            continue
        arguments = call.get("arguments") or {}
        key = (call["name"], json.dumps(arguments, sort_keys=True, ensure_ascii=False))
        unique.setdefault(key, call)

    async def run():
        rows = []
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + fixture.token}) as http:
            async with Client(streamable_http_client(fixture.url, http_client=http)) as mcp:
                for call in unique.values():
                    for repeat in range(repetitions):
                        before = len(fixture.upstream_calls)
                        started = time.perf_counter()
                        result = await mcp.call_tool(call["name"], call.get("arguments") or {})
                        duration = time.perf_counter() - started
                        payload = result.structured_content or {}
                        upstream = fixture.upstream_calls[before:]
                        rows.append({"name": call["name"], "arguments": call.get("arguments") or {},
                            "repeat": repeat + 1, "durationSeconds": duration,
                            "ok": not result.is_error and bool(payload.get("ok")),
                            "upstreamRequests": len(upstream),
                            "upstreamDurationSeconds": sum(row["durationSeconds"] for row in upstream),
                            "cache": payload.get("cache"),
                            "dataByteIdentical": payload.get("data") == call["result"].get("data")})
        return {"measuredAfterAnswer": True, "repetitions": repetitions,
                "uniqueCalls": len(unique), "reads": rows}
    return anyio.run(run)
