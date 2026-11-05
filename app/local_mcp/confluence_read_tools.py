"""Confluence discovery tools share the UI service, authentication and cache."""
import json
import re
from datetime import date
from html import unescape
from typing import Literal

from app.jira import confluence_service as conf
from app.services.ticket_commands import CommandError


def spaces(runtime):
    values = [str(v).strip() for v in runtime.settings.search_confluence_spaces or [] if str(v).strip()]
    if not values:
        raise ValueError("search.confluence.spaces에 조회 범위를 설정해 주세요.")
    return values


def require_page(runtime, page):
    cid = conf.resolve_page(runtime.client, page)
    data = conf.head(runtime.client, cid)
    if data["space"]["key"].casefold() not in {v.casefold() for v in spaces(runtime)}:
        raise CommandError("문서가 설정된 Confluence Space 범위 밖입니다.", 403)
    return cid, data


def _cql_parts(value):
    """Find ORDER BY only outside quotes; preserve operators/functions in the filter."""
    quote, escaped, depth = "", False, 0
    for i, char in enumerate(value):
        if escaped:
            escaped = False
        elif quote:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("CQL 괄호 순서가 올바르지 않습니다.")
        elif depth == 0 and (i == 0 or value[i - 1].isspace()) and re.match(r"ORDER\s+BY\b", value[i:], re.I):
            return value[:i].strip(), value[i:].strip()
    if quote or depth:
        raise ValueError("CQL 괄호 또는 따옴표가 닫히지 않았습니다.")
    return value.strip(), "ORDER BY lastModified DESC"


def build_cql(scope, *, query="", content_type="page", modified_after="", title="", parent_id="",
              ancestor_id="", labels=None, cql=""):
    encode = lambda v: json.dumps(v, ensure_ascii=False)
    conditions = ["space in (" + ", ".join(encode(v) for v in scope) + ")"]
    if cql:
        if len(cql.encode("utf-8")) > 16384 or any((query, title, modified_after, parent_id, ancestor_id, labels)):
            raise ValueError("CQL은 16KiB 이하이며 별도 검색 필터와 함께 사용할 수 없습니다.")
        where, order = _cql_parts(cql)
        if not where:
            raise ValueError("CQL 검색 조건이 필요합니다.")
        return conditions[0] + " AND (" + where + ") " + order
    if query:
        conditions.append("siteSearch ~ " + encode(query))
    if content_type:
        conditions.append("type = " + encode(content_type))
    if title:
        conditions.append("title = " + encode(title))
    for field, value in (("parent", parent_id), ("ancestor", ancestor_id)):
        if value:
            if not str(value).isdigit():
                raise ValueError("parent_id와 ancestor_id는 숫자 문서 ID여야 합니다.")
            conditions.append(field + " = " + str(value))
    for label in labels or []:
        conditions.append("label = " + encode(label))
    if modified_after:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", modified_after):
            raise ValueError("modified_after는 YYYY-MM-DD 형식이어야 합니다.")
        date.fromisoformat(modified_after)
        conditions.append("lastModified >= " + encode(modified_after))
    return " AND ".join(conditions) + " ORDER BY lastModified DESC"


def search_documents(runtime, *, query="", start_at=0, max_results=20, content_type="page",
                     modified_after="", space_key="", title="", parent_id="", ancestor_id="", labels=None, cql=""):
    scope = spaces(runtime)
    if space_key:
        scope = [v for v in scope if v.casefold() == space_key.casefold()]
        if not scope:
            raise CommandError("설정된 Confluence Space 범위 밖입니다.", 403)
    canonical = build_cql(scope, query=query, content_type=content_type, modified_after=modified_after,
                          title=title, parent_id=parent_id, ancestor_id=ancestor_id, labels=labels, cql=cql)
    result = runtime.client.confluence_search(canonical, start=start_at, limit=max_results)
    rows, documents = result.get("results") or [], []
    base = (runtime.settings.confluence_base or "").rstrip("/")
    for row in rows:
        content = row.get("content") or {}
        space = (content.get("space") or {}).get("key") or row.get("space") or ""
        if str(space).casefold() not in {v.casefold() for v in scope}:
            continue
        url = row.get("url") or (content.get("_links") or {}).get("webui") or ""
        documents.append({"id": str(content.get("id") or row.get("id") or ""), "space": space,
                          "title": row.get("title") or content.get("title") or "", "excerpt": row.get("excerpt") or "",
                          "url": base + url if url.startswith("/") else url,
                          "version": content.get("version") or {}, "ancestors": content.get("ancestors") or [],
                          "modified": row.get("lastModified") or (content.get("version") or {}).get("when")})
    total = result.get("totalSize", result.get("total"))
    next_start = start_at + len(rows)
    more = bool(rows) and (bool((result.get("_links") or {}).get("next")) or len(rows) >= max_results
                          or total is not None and next_start < int(total))
    return {"documents": documents, "canonicalCql": canonical, "scopeSpaces": scope,
            "startAt": start_at, "returned": len(documents), "total": total,
            "hasMore": more, "nextStartAt": next_start if more else None}


def read_document(runtime, page, *, offset=0, max_chars=12000, format="text", version=None):
    if offset < 0 or not 1 <= max_chars <= 50000 or (offset and version is None):
        raise ValueError("offset은 0 이상, max_chars는 1~50000이며 후속 조각에는 첫 응답의 version.number를 전달하세요.")
    cid, current = require_page(runtime, page)
    data = conf.body(runtime.client, cid, version=version)
    content = data["body"]["storage"]["value"]
    if format == "text":
        content = unescape(re.sub(r"<[^>]+>", " ", re.sub(r"</(?:p|div|h[1-6]|li|tr)>|<br\s*/?>", "\n", content)))
        content = re.sub(r"[ \t]+", " ", content).strip()
    end = min(offset + max_chars, len(content))
    web = (data.get("_links") or {}).get("webui") or f"/pages/viewpage.action?pageId={cid}"
    base = (runtime.settings.confluence_base or "").rstrip("/")
    return {"id": cid, "title": data.get("title") or "", "space": current["space"]["key"],
            "url": base + web if web.startswith("/") else web, "version": data["version"], "format": format,
            "content": content[offset:end], "offset": offset, "totalCharacters": len(content),
            "hasMore": end < len(content), "nextOffset": end if end < len(content) else None,
            "cacheInfo": data["cacheInfo"]}


def _page(data, start, limit):
    rows = data.get("results") or []
    more = bool(rows) and (bool((data.get("_links") or {}).get("next")) or len(rows) >= limit)
    return {"results": rows, "startAt": start, "returned": len(rows), "hasMore": more,
            "nextStartAt": start + len(rows) if more else None,
            "coverage": "accessible_only"}


def register_confluence_reads(register, runtime):
    def check_confluence_revision(page: str, known_version: int | None = None) -> dict:
        """Check latest PUBLISHED version directly, without downloading the body or trusting search/cache.
        Pass your version.number to compare. Returns latestPublishedVersion, lastModifiedAt/By, checkedAt,
        isLatest (null without a comparison) and draftState=unknown. Comments/attachments are independent;
        same published version does not prove nobody is editing an unpublished collaborative draft.
        """
        cid = conf.resolve_page(runtime.client, page)
        result = conf.check_revision(runtime.client, cid, known_version)
        require_page(runtime, cid)  # An external move may have changed the fresh Space.
        return result

    def list_confluence_spaces(start_at: int = 0, max_results: int = 50) -> dict:
        """List accessible configured Spaces. Follow nextStartAt even when results are filtered empty.
        coverage=accessible_only never proves that permission-hidden Spaces or documents do not exist.
        """
        scope = {v.casefold() for v in spaces(runtime)}
        result = _page(conf.list_spaces(runtime.client, start_at, max_results), start_at, max_results)
        result["results"] = [r for r in result["results"] if str(r.get("key", "")).casefold() in scope]
        result["returned"] = len(result["results"])
        return result

    def list_confluence_pages(space_key: str = "", parent_id: str = "", start_at: int = 0,
                              max_results: int = 50) -> dict:
        """List Space root pages or a page's direct children, including roots outside its homepage.
        Pass parent_id to expand each branch and follow nextStartAt for every page. Rows include ancestors
        and published version metadata; this is an accessible tree view, not an atomic full-Space snapshot.
        """
        scope = {v.casefold() for v in spaces(runtime)}
        if parent_id:
            parent_id, current = require_page(runtime, parent_id)
            space_key = space_key or current["space"]["key"]
        if space_key.casefold() not in scope:
            raise CommandError("설정된 Confluence Space 범위 밖입니다.", 403)
        return _page(conf.list_pages(runtime.client, space_key, parent_id, start_at, max_results), start_at, max_results)

    def get_confluence_context(page: str, kind: Literal["ancestors", "comment", "attachment", "label", "restriction"],
                               start_at: int = 0, max_results: int = 50) -> dict:
        """Read ancestors, bounded comment previews, attachment metadata, labels or page restrictions.
        Comment rows contain id, compact version/author, text preview, storage bodyLength/bodyTruncated;
        they are not full storage source. Retrieve a selected comment separately by ID and version.
        The response budget may return fewer rows than max_results. Follow nextStartAt without skipping.
        Comments/attachments change independently of the page revision. Follow nextStartAt for lists;
        direct page restrictions are returned as data. Inspect ancestor restrictions separately before
        reasoning about inherited access; this is not an effective-permission calculation. No bypass.
        """
        cid, current = require_page(runtime, page)
        if kind == "ancestors":
            rows = current.get("ancestors") or []
            return {"pageId": cid, "kind": kind, "results": rows, "hasMore": False, "nextStartAt": None}
        data = conf.list_related(runtime.client, cid, kind, start_at, max_results)
        if kind == "comment":
            from .confluence_comment_previews import comment_page
            return comment_page(cid, data, start_at, max_results)
        return {"pageId": cid, "kind": kind, **({"data": data} if kind == "restriction" else _page(data, start_at, max_results))}

    for fn in (check_confluence_revision, list_confluence_spaces, list_confluence_pages, get_confluence_context):
        register(fn, read_only=True)
