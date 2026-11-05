"""Bounded comment evidence and binary attachment downloads, never giant MCP text."""

import base64
import html
import re
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from app.auth.base import UpstreamUnavailable
from app.jira import confluence_service as reads
from .confluence_commands import MAX_ATTACHMENT_BYTES, identifier
from .ticket_commands import CommandError


def attachment_bytes(commands, *, page_id, attachment_id, version=None):
    page_id, attachment_id = identifier(page_id), identifier(attachment_id)
    if version is not None and (type(version) is not int or version < 1):
        raise CommandError("version은 1 이상의 정수여야 합니다.")
    item = commands._child(page_id, attachment_id, "attachment")
    if version is not None and (item.get("version") or {}).get("number") != version:
        item = commands.request("GET", f"/rest/api/content/{attachment_id}", params={
            "version": version, "expand": "container,version,space"})
        if (str(item.get("id")) != attachment_id or item.get("type") != "attachment"
                or str((item.get("container") or {}).get("id")) != page_id
                or (item.get("version") or {}).get("number") != version):
            raise CommandError("요청한 첨부 버전을 확인하지 못했습니다.", 409, reason="version_conflict")
    version = (item.get("version") or {}).get("number")
    if type(version) is not int or version < 1:
        raise UpstreamUnavailable("첨부 버전을 확인하지 못했습니다.")
    if int((item.get("extensions") or {}).get("fileSize") or 0) > MAX_ATTACHMENT_BYTES:
        raise CommandError("첨부 읽기는 10MiB 이하 파일을 지원합니다.")
    base = str(commands.settings.confluence_base or "").rstrip("/")
    download = (item.get("_links") or {}).get("download") or ""
    target = base + download if download.startswith("/download/") else urljoin(base + "/", download)
    parsed, configured = urlsplit(target), urlsplit(base)
    path = unquote(parsed.path)
    prefix = configured.path.rstrip("/") + f"/download/attachments/{page_id}/"
    if (not download or parsed.scheme not in ("http", "https") or parsed.netloc != configured.netloc
            or parsed.username or parsed.password or not path.startswith(prefix)
            or any(part in (".", "..") for part in path.split("/")) or "\\" in path):
        raise CommandError("Confluence가 반환한 첨부 다운로드 주소를 확인할 수 없습니다.")
    query = dict(parse_qsl(parsed.query))
    query["version"] = str(version)
    target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    try:
        data, mime = commands.client.provider.get_bytes(target)
    except Exception as exc:
        exc.service = "confluence"
        raise
    if not isinstance(data, bytes):
        raise UpstreamUnavailable("첨부를 내려받지 못했습니다.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise CommandError("첨부 읽기는 10MiB 이하 파일을 지원합니다.")
    return {"id": attachment_id, "pageId": page_id, "filename": item.get("title") or "",
            "version": version, "mimeType": mime, "totalBytes": len(data)}, data


def read_attachment(commands, *, page_id, attachment_id, offset=0, max_bytes=1024, version=None):
    if offset < 0 or not 1 <= max_bytes <= 4096:
        raise CommandError("offset은 0 이상, max_bytes는 1~4096이어야 합니다. 전체 파일은 downloadUrl로 받으세요.")
    if offset and version is None:
        raise CommandError("후속 첨부 조각에는 첫 응답의 version을 전달하세요.")
    metadata, data = attachment_bytes(commands, page_id=page_id, attachment_id=attachment_id, version=version)
    end = min(offset + max_bytes, len(data))
    port = int(getattr(commands.settings, "app_port", 4457))
    local = (f"http://127.0.0.1:{port}/api/confluence/pages/{metadata['pageId']}/attachments/"
             f"{metadata['id']}/download?version={metadata['version']}")
    return {**metadata, "base64": base64.b64encode(data[offset:end]).decode(),
            "offset": offset, "hasMore": end < len(data), "nextOffset": end if end < len(data) else None,
            "downloadUrl": local}


def read_comment(commands, *, page_id, comment_id, version=None, offset=0, max_chars=4000, format="storage"):
    page_id, comment_id = identifier(page_id), identifier(comment_id)
    if offset < 0 or not 1 <= max_chars <= 8000 or format not in ("storage", "text"):
        raise CommandError("offset은 0 이상, max_chars는 1~8000, format은 storage 또는 text여야 합니다.")
    if offset and version is None:
        raise CommandError("후속 댓글 조각에는 첫 응답의 version을 전달하세요.")
    current = commands._child(page_id, comment_id, "comment")
    version = (current.get("version") or {}).get("number") if version is None else version
    if type(version) is not int or version < 1:
        raise CommandError("version은 1 이상의 정수여야 합니다.")
    key = f"confrelated:{reads.scope_key(commands.client)}:{page_id}:comment:{comment_id}:{version}:storage"
    def complete(item):
        return (str(item.get("id")) == comment_id and item.get("type") == "comment"
                and str((item.get("container") or {}).get("id")) == page_id
                and (item.get("version") or {}).get("number") == version
                and isinstance(((item.get("body") or {}).get("storage") or {}).get("value"), str))
    item = reads.cached_read(commands.client, "confrelated", f"/rest/api/content/{comment_id}",
        {"version": version, "expand": "body.storage,container,version,space"}, complete, key=key)
    text = item["body"]["storage"]["value"]
    if format == "text":
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    # Character cursors remain stable; bound UTF-8 bytes too so multibyte text
    # cannot inflate a nominally small source chunk into a giant tool response.
    chunk = text[offset:offset + max_chars].encode("utf-8")[:8000].decode("utf-8", errors="ignore")
    end = min(offset + len(chunk), len(text))
    return {"id": comment_id, "pageId": page_id, "version": version, "format": format,
            "content": chunk, "offset": offset, "totalCharacters": len(text),
            "hasMore": end < len(text), "nextOffset": end if end < len(text) else None}
