"""Rooted file capability for recovery evaluation, never registered by product LTM."""
from __future__ import annotations

from hashlib import sha256
import os
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import tempfile
from threading import RLock
from time import perf_counter

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations


class ResearchFiles:
    MAX_FILE_BYTES = 1024 * 1024
    MAX_TOTAL_BYTES = 32 * 1024 * 1024
    MAX_FILES = 512

    def __init__(self, root: Path, *, after_write=None):
        self.root = Path(root).absolute()
        if not self.root.is_dir() or self._linked(self.root):
            raise ValueError("Research root must be an existing, non-linked private directory")
        self.root = self.root.resolve()
        self.calls, self.after_write, self._lock = [], after_write, RLock()
        self.server = MCPServer("research-files", log_level="WARNING", instructions=(
            "Evaluation-only investigation files. Paths are relative to one private directory. "
            "No access to host files, oracle, credentials or parent directories. "
            "write_file atomically replaces one UTF-8 file. read_file paginates by characters."))
        self._register()

    @staticmethod
    def _linked(path):
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) &
                                                 getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))

    def _path(self, name):
        if (not isinstance(name, str) or not name or len(name) > 200 or "\\" in name or ":" in name
                or PureWindowsPath(name).drive or PurePosixPath(name).is_absolute()
                or any(p in {"", ".", ".."} for p in name.split("/"))):
            raise ValueError("Use a relative investigation path without traversal or drive/stream names")
        target = self.root.joinpath(*name.split("/"))
        current = self.root
        for part in name.split("/"):
            current = current / part
            if current.exists() or current.is_symlink():
                if self._linked(current) or (current.is_file() and current.stat().st_nlink > 1):
                    raise ValueError("Linked investigation paths are not allowed")
        if self._linked(self.root) or not target.resolve().is_relative_to(self.root):
            raise ValueError("Investigation path escapes its root")
        return target

    def list_files(self):
        with self._lock:
            result = []
            for directory, dirs, files in os.walk(self.root, followlinks=False):
                for name in [*dirs, *files]:
                    path = Path(directory) / name
                    relative = path.relative_to(self.root).as_posix()
                    self._path(relative)
                    if path.is_file():
                        result.append({"path": relative, "size": path.stat().st_size})
                    if len(result) > self.MAX_FILES:
                        raise ValueError("Investigation file count limit exceeded")
            return {"files": sorted(result, key=lambda row: row["path"])}

    def read_file(self, path: str, offset: int = 0, max_chars: int = 2000):
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 2000:
            raise ValueError("Invalid read offset or page size")
        with self._lock:
            target = self._path(path)
            if target.stat().st_size > self.MAX_FILE_BYTES:
                raise ValueError("Investigation file size limit exceeded")
            raw = target.read_bytes()
            content = raw.decode("utf-8")
            if offset > len(content):
                raise ValueError("Read offset exceeds file length")
            end = min(offset + max_chars, len(content))
            return {"path": path, "content": content[offset:end], "offset": offset,
                    "nextOffset": end if end < len(content) else None, "totalCharacters": len(content),
                    "digest": "sha256:" + sha256(raw).hexdigest()}

    def write_file(self, path: str, content: str):
        if not isinstance(content, str):
            raise ValueError("Investigation content must be UTF-8 text")
        raw = content.encode("utf-8")
        if len(raw) > self.MAX_FILE_BYTES:
            raise ValueError("Investigation file size limit exceeded")
        with self._lock:
            target = self._path(path)
            files = self.list_files()["files"]
            old = next((row["size"] for row in files if row["path"] == path), 0)
            if (sum(row["size"] for row in files) - old + len(raw) > self.MAX_TOTAL_BYTES
                    or (not target.exists() and len(files) >= self.MAX_FILES)):
                raise ValueError("Investigation storage limit exceeded")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".writing-", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._path(path)
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            result = {"path": path, "size": len(raw), "digest": "sha256:" + sha256(raw).hexdigest()}
            if self.after_write:
                self.after_write(path, content)
            return result

    def _register(self):
        def observed(name, fn, arguments):
            started = perf_counter()
            record = {"name": name, "arguments": arguments, "ok": False}
            try:
                result = fn(**arguments)
                record.update(ok=True, result=result)
                return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                                      structured_content=result)
            finally:
                record["durationSeconds"] = perf_counter() - started
                self.calls.append(record)

        def list_files() -> CallToolResult:
            """List saved investigation files; never lists host or parent directories."""
            return observed("list_files", self.list_files, {})

        def read_file(path: str, offset: int = 0, max_chars: int = 2000) -> CallToolResult:
            """Read UTF-8 investigation text, 1..2000 characters per page; follow nextOffset."""
            return observed("read_file", self.read_file, dict(path=path, offset=offset, max_chars=max_chars))

        def write_file(path: str, content: str) -> CallToolResult:
            """Atomically save one investigation file, at most 1MiB; no external paths or links."""
            return observed("write_file", self.write_file, dict(path=path, content=content))

        for fn in (list_files, read_file, write_file):
            self.server.tool(annotations=ToolAnnotations(read_only_hint=fn is not write_file,
                destructive_hint=False, idempotent_hint=True, open_world_hint=False))(fn)
