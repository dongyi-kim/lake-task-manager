"""Business reads beyond individual Jira tickets; no Agent/LLM initialization."""

import base64
import json
import re
from datetime import date
from html import unescape
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from app.auth.base import UpstreamUnavailable
from app.services.ticket_commands import CommandError


def _spaces(runtime):
    spaces = [str(value).strip() for value in runtime.settings.search_confluence_spaces or [] if str(value).strip()]
    if not spaces:
        raise ValueError("search.confluence.spaces에 Confluence 조회 범위를 설정해 주세요.")
    return spaces


def register_workspace(register, runtime):
    def search_workspace(query: str, sources: list[Literal["jira", "confluence"]] | None = None,
                         limit: int = 20) -> dict:
        """Use LTM's unified keyword search across Jira and Confluence. Results are excerpts,
        not full bodies: follow with get_ticket or read_confluence_document. Configured scopes apply.
        Bitbucket is not exposed: LTM's current Bitbucket search is placeholder data, not live results.
        """
        from app.domain.search import search_all
        if not 1 <= limit <= 100:
            raise ValueError("limit은 1~100이어야 합니다.")
        return search_all(runtime.client, runtime.settings, query, limit=limit, only=sources or ["jira", "confluence"])

    def search_confluence_documents(query: str = "", start_at: int = 0, max_results: int = 20,
                                     content_type: Literal["page", "blogpost", ""] = "page",
                                     modified_after: str = "") -> dict:
        """Search Confluence documents using shared authentication/cache. Follow nextStartAt for
        complete coverage; returned IDs can be passed to read_confluence_document for full content.
        Empty query lists recently changed documents within configured spaces. modified_after: YYYY-MM-DD.
        """
        if not 1 <= max_results <= 100 or start_at < 0:
            raise ValueError("max_results는 1~100, start_at은 0 이상이어야 합니다.")
        spaces = _spaces(runtime)
        conditions = ["space in (" + ", ".join(json.dumps(space, ensure_ascii=False) for space in spaces) + ")"]
        if query:
            conditions.append("siteSearch ~ " + json.dumps(query, ensure_ascii=False))
        if content_type:
            conditions.append("type = " + json.dumps(content_type))
        if modified_after:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", modified_after):
                raise ValueError("modified_after는 YYYY-MM-DD 형식이어야 합니다.")
            date.fromisoformat(modified_after)
            conditions.append("lastModified >= " + json.dumps(modified_after))
        cql = " AND ".join(conditions) + " ORDER BY lastModified DESC"
        result = runtime.client.confluence_search(cql, start=start_at, limit=max_results)
        rows, documents = result.get("results") or [], []
        base = (runtime.settings.confluence_base or "").rstrip("/")
        allowed = {space.casefold() for space in spaces}
        for row in rows:
            content = row.get("content") or {}
            space = (content.get("space") or {}).get("key") or row.get("space") or ""
            if str(space).casefold() not in allowed:
                continue
            url = row.get("url") or (content.get("_links") or {}).get("webui") or ""
            documents.append({"id": str(content.get("id") or row.get("id") or ""), "space": space,
                              "title": row.get("title") or content.get("title") or "",
                              "excerpt": row.get("excerpt") or "",
                              "url": base + url if url.startswith("/") else url,
                              "modified": row.get("lastModified") or (content.get("version") or {}).get("when")})
        total = result.get("totalSize", result.get("total"))
        next_start = start_at + len(rows)
        # Some Confluence/search adapters report only the scanned total. A full
        # page may have a successor even when totalSize equals this page's end.
        more = bool(rows) and (bool((result.get("_links") or {}).get("next"))
                              or len(rows) >= max_results or total is not None and next_start < int(total))
        return {"documents": documents, "canonicalCql": cql, "scopeSpaces": spaces,
                "startAt": start_at, "returned": len(documents), "total": total,
                "hasMore": more, "nextStartAt": next_start if more else None}

    def read_confluence_document(page: str, offset: int = 0, max_chars: int = 12000,
                                  format: Literal["text", "storage"] = "text") -> dict:
        """Read Confluence content by numeric page ID, or configured-host /pages/{id} / ?pageId= URL.
        Legacy /display/SPACE/title links must first be resolved with search_confluence_documents.
        text removes markup; storage returns source XHTML. Follow nextOffset to read all content.
        Also returns title, space, URL, version and update time. Never fetches arbitrary URLs.
        """
        if offset < 0 or not 1 <= max_chars <= 50000:
            raise ValueError("offset은 0 이상, max_chars는 1~50000이어야 합니다.")
        spaces = _spaces(runtime)
        identifier = page.strip()
        if not identifier.isdigit():
            url, base = urlsplit(identifier), urlsplit(runtime.settings.confluence_base or "")
            if (url.scheme not in ("http", "https") or url.netloc != base.netloc
                    or url.username or url.password or not url.path.startswith(base.path.rstrip("/") + "/")):
                raise ValueError("설정된 Confluence 주소의 페이지 URL 또는 숫자 ID를 사용하세요.")
            match = re.search(r"/pages/([0-9]+)(?:/|$)", url.path)
            identifier = match.group(1) if match else parse_qs(url.query).get("pageId", [""])[0]
            if not identifier:
                raise ValueError("URL에 페이지 ID가 없습니다. search_confluence_documents로 검색한 숫자 ID를 사용하세요.")
        runtime.check_object_id(identifier)
        data = runtime.client.confluence_page(identifier, expand="body.storage,space,version", strict=True)
        space = (data.get("space") or {}).get("key") or ""
        if space.casefold() not in {value.casefold() for value in spaces}:
            raise CommandError("문서가 설정된 Confluence space 범위 밖입니다.", 403)
        body = ((data.get("body") or {}).get("storage") or {}).get("value")
        if not isinstance(body, str):
            raise UpstreamUnavailable("Confluence 문서 본문이 응답에 없습니다.")
        if format == "text":
            body = unescape(re.sub(r"<[^>]+>", " ", re.sub(r"</(?:p|div|h[1-6]|li|tr)>|<br\s*/?>", "\n", body)))
            body = re.sub(r"[ \t]+", " ", body).strip()
        end = min(offset + max_chars, len(body))
        base = (runtime.settings.confluence_base or "").rstrip("/")
        web = (data.get("_links") or {}).get("webui") or f"/pages/viewpage.action?pageId={identifier}"
        return {"id": identifier, "title": data.get("title") or "", "space": space,
                "url": base + web if web.startswith("/") else web, "version": data.get("version") or {},
                "format": format, "content": body[offset:end], "offset": offset,
                "totalCharacters": len(body), "hasMore": end < len(body), "nextOffset": end if end < len(body) else None}

    def read_attachment(key: str, attachment_id: str, offset: int = 0, max_bytes: int = 262144) -> dict:
        """Read actual attachment bytes as base64, selected by verified ticket and attachment ID.
        Follow nextOffset to read the full file (maximum file size 10MiB; maximum chunk 1MiB).
        Supports images and documents without local filesystem access or guessing file content.
        """
        runtime.check_key(key)
        runtime.check_object_id(attachment_id)
        if offset < 0 or not 1 <= max_bytes <= 1024 * 1024:
            raise ValueError("offset은 0 이상, max_bytes는 1~1048576이어야 합니다.")
        rows = (runtime.client.get_issue(key).get("fields") or {}).get("attachment") or []
        attachment = next((row for row in rows if str(row.get("id")) == attachment_id), None)
        if not attachment:
            raise CommandError("이 티켓에 속한 첨부 ID가 아닙니다.", 404)
        if int(attachment.get("size") or 0) > 10 * 1024 * 1024:
            raise ValueError("첨부 읽기는 10MiB 이하 파일을 지원합니다.")
        path = urlsplit(attachment.get("content") or "").path
        if not path.startswith(f"/secure/attachment/{attachment_id}/"):
            raise ValueError("Jira 첨부 경로를 확인할 수 없습니다.")
        data, content_type = runtime.client.fetch_media(path, strict=True)
        if data is None:
            raise UpstreamUnavailable("첨부를 내려받지 못했습니다.")
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("첨부 읽기는 10MiB 이하 파일을 지원합니다.")
        end = min(offset + max_bytes, len(data))
        return {"id": attachment_id, "filename": attachment.get("filename") or "",
                "mimeType": content_type or attachment.get("mimeType"), "totalBytes": len(data),
                "offset": offset, "base64": base64.b64encode(data[offset:end]).decode(),
                "hasMore": end < len(data), "nextOffset": end if end < len(data) else None}

    def get_ticket_context(key: str) -> dict:
        """Read parent/Epic ancestors, siblings, related tickets and linked documents using UI caches."""
        runtime.check_key(key)
        client = runtime.client
        return {"ancestors": client.ticket_ancestors(key), "siblings": client.ticket_siblings(key),
                "related": client.ticket_related(key), "documents": client.ticket_documents(key)}

    def get_field_history(key: str, limit: int = 100) -> dict:
        """Read changes to every field, including description/summary changes excluded from the UI timeline."""
        runtime.check_key(key)
        if not 1 <= limit <= 1000:
            raise ValueError("limit은 1~1000이어야 합니다.")
        rows = runtime.client.ticket_field_history(key, limit)
        return {"history": rows, "limit": limit, "mayHaveMore": len(rows) >= limit}

    def get_epic_progress(key: str) -> dict:
        """Read the same Epic progress and story-point aggregation used by the WBS dashboard."""
        runtime.check_key(key)
        return runtime.client.epic_progress_one(key)

    def get_dashboard(kind: Literal["wbs", "vit", "workload"], module: str = "") -> dict:
        """Read LTM WBS, VIT or workload dashboard using the same builders and access checks.
        WBS requires manager permission. A workload module requires module access; empty module
        uses the current user's visible people. No snapshot/config is written by this read.
        """
        from app.domain import rollup, vit, workload
        from app.infra.settings import load_people, load_plan
        client, plan = runtime.client, load_plan()
        client.miss_begin()
        if kind == "wbs":
            runtime.require_manager()
            result = rollup.build(plan, client.epic_progress_map(plan))
        elif kind == "vit":
            result = (vit.build_vit_module(client, plan, load_people(), module) if module
                      else vit.build_vit(client, plan, load_people(), jira_base=runtime.settings.jira_base))
        elif module:
            runtime.require_module_access(module)
            result = workload.build_workload_module(client, plan, load_people(), module)
        else:
            result = workload.build_workload(client, plan, runtime.scoped_people(), jira_base=runtime.settings.jira_base)
        missing = client.miss_count()
        if isinstance(result, dict) and missing:
            result.update(partial=True, missing=missing)
        return result

    def get_vit_detail(key: str) -> dict:
        """Read the VIT dashboard's issue detail, descendants and progress using its shared builder."""
        from app.domain import vit
        from app.infra.settings import load_people, load_plan
        runtime.check_key(key)
        return vit.vit_detail(runtime.client, load_plan(), load_people(), key)

    def get_activity(user_id: str) -> dict:
        """Read recent Jira/Confluence activity under the same manager permission as the UI API."""
        runtime.require_manager()
        return runtime.client.activity(user_id)

    for fn in (search_workspace, search_confluence_documents, read_confluence_document,
               read_attachment, get_ticket_context, get_field_history, get_epic_progress,
               get_dashboard, get_vit_detail, get_activity):
        register(fn, read_only=True)
