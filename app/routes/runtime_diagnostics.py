"""Local-only browsing of fixed, bounded, payload-free runtime evidence."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.infra.runtime_diagnostics import safe_filename, safe_record
from app.local_mcp.security import local_request_error


MAX_READ_BYTES = 256 * 1024
MAX_RECORDS = 200
LOG_FILES = tuple(f"{name}.jsonl{suffix}" for name in ("worker", "supervisor", "browser", "failures", "launcher-failures")
                  for suffix in ("", ".1", ".2")) + tuple(
    f"fatal-{role}.log{suffix}" for role in ("worker", "launcher") for suffix in ("", ".1"))
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_FATAL_FRAME = re.compile(r'^\s*File "(.{1,2048})", line ([0-9]{1,9}) in ([A-Za-z0-9_.<> -]{1,80})$')


def _read(directory, name):
    if name not in LOG_FILES:
        raise FileNotFoundError
    path = directory / name
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise FileNotFoundError
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise FileNotFoundError
        truncated = opened.st_size > MAX_READ_BYTES
        source.seek(max(0, opened.st_size - MAX_READ_BYTES))
        data = source.read(MAX_READ_BYTES)
    text = data.decode("utf-8", errors="replace")
    # A tail read may start halfway through an older record.
    if truncated:
        text = text.partition("\n")[2]
    return text, opened, truncated


def _records(text, name):
    if ".jsonl" in name:
        result = []
        for line in text.splitlines():
            try:
                value = safe_record(json.loads(line))
            except (ValueError, TypeError, RecursionError):
                continue
            if value.get("event"):
                result.append({**value, "file": name})
        return result
    # faulthandler writes directly to an OS descriptor. Export only known headers
    # and normalized frame locations, never arbitrary native/extension output.
    frames, fatal_type = [], ""
    for line in text.splitlines()[:2000]:
        if line in {"Fatal Python error: Aborted", "Fatal Python error: Segmentation fault",
                    "Fatal Python error: Illegal instruction", "Fatal Python error: Bus error",
                    "Fatal Python error: Floating point exception", "Windows fatal exception: access violation",
                    "Windows fatal exception: stack overflow"}:
            fatal_type = line.split(": ", 1)[-1]
        match = _FATAL_FRAME.fullmatch(line)
        if match and len(frames) < 40:
            frames.append({"file": safe_filename(match[1]), "line": int(match[2]), "function": match[3]})
    if not fatal_type and not frames:
        return []
    return [{"event": "fatal_error", "errorType": fatal_type or "NativeFailure", "frames": frames, "file": name}]


def build_runtime_diagnostics_router(*, directory, port):
    router = APIRouter(prefix="/api/app/diagnostics")
    directory = Path(directory)

    def guard(request):
        if local_request_error(request.scope, port):
            return JSONResponse({"error": "이 컴퓨터의 LTM에서만 오류 기록을 볼 수 있습니다."},
                                status_code=403, headers=HEADERS)

    @router.get("")
    def browse(request: Request):
        denied = guard(request)
        if denied is not None:
            return denied
        files, records, truncated = [], [], False
        for name in LOG_FILES:
            try:
                content, metadata, limited = _read(directory, name)
            except (OSError, ValueError):
                continue
            files.append({"name": name, "bytes": metadata.st_size,
                          "modifiedAt": datetime.fromtimestamp(metadata.st_mtime, timezone.utc).isoformat()})
            recent = _records(content, name)
            if ".log" in name:
                for record in recent:
                    record["at"] = files[-1]["modifiedAt"]
            records.extend(recent[-MAX_RECORDS:])
            truncated = truncated or limited or len(recent) > MAX_RECORDS
        records.sort(key=lambda row: row.get("at", ""), reverse=True)
        return JSONResponse({"files": sorted(files, key=lambda row: row["modifiedAt"], reverse=True),
                             "records": records[:MAX_RECORDS],
                             "truncated": truncated or len(records) > MAX_RECORDS}, headers=HEADERS)

    @router.get("/{name}/download")
    def download(request: Request, name: str):
        denied = guard(request)
        if denied is not None:
            return denied
        try:
            text, _metadata, truncated = _read(directory, name)
        except (OSError, ValueError):
            return JSONResponse({"error": "오류 기록 파일을 찾을 수 없습니다. 목록을 새로고침하세요."},
                                status_code=404, headers=HEADERS)
        available = _records(text, name)
        records = available[-MAX_RECORDS:]
        output = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
        # Sanitized JSONL remains safe to inspect, even when the source was a native dump.
        return Response(output, media_type="application/octet-stream", headers={**HEADERS,
            "Content-Disposition": f'attachment; filename="{name}.sanitized.jsonl"',
            "X-LTM-Truncated": "true" if truncated or len(available) > MAX_RECORDS else "false"})

    return router
