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
        """Discover related Jira tickets and Confluence documents with LTM's unified keyword search.
        sources defaults to both services; limit=1..100 caps each source, with no pagination here.
        Returns per-source items/excerpts or errors within configured projects/spaces. Use search_issues
        or search_confluence_documents for exhaustive results, then get_ticket/get_comments or
        read_confluence_document for evidence. A source error or capped result is not a zero/total count.
        Bitbucket is not exposed. Partial source authentication failures include recovery guidance.
        Use returned canonical URLs for Jira/Confluence rich links; never invent a host from a title.
        Authoring examples are in get_content_guide's references/people-and-links.md.
        """
        from app.domain.search import search_all
        if not 1 <= limit <= 100:
            raise ValueError("limit은 1~100이어야 합니다.")
        return search_all(runtime.client, runtime.settings, query, limit=limit, only=sources or ["jira", "confluence"])

    def search_confluence_documents(query: str = "", start_at: int = 0, max_results: int = 20,
                                     content_type: Literal["page", "blogpost", ""] = "page",
                                     modified_after: str = "", space_key: str = "", title: str = "",
                                     parent_id: str = "", ancestor_id: str = "", labels: list[str] | None = None,
                                     cql: str = "") -> dict:
        """Search Confluence metadata within configured spaces, with pagination and optional complete CQL.
        Filters: keyword, exact title, Space, parent/ancestor IDs, labels, modification date.
        cql is an alternative to the other content filters; configured Space boundaries always apply.
        Follow nextStartAt until hasMore=false. Search index may lag writes: use check_confluence_revision
        for latest published metadata, and read_confluence_document for full body evidence.
        """
        from .confluence_read_tools import search_documents
        return search_documents(runtime, query=query, start_at=start_at, max_results=max_results,
                                content_type=content_type, modified_after=modified_after, space_key=space_key,
                                title=title, parent_id=parent_id, ancestor_id=ancestor_id, labels=labels, cql=cql)

    def read_confluence_document(page: str, offset: int = 0, max_chars: int = 12000,
                                  format: Literal["text", "storage"] = "text", version: int | None = None) -> dict:
        """Read a current or historical published Confluence page by ID/configured-host URL.
        Pass the first response's version.number on every subsequent nextOffset request; offset>0 requires
        version so publication during reading cannot mix revisions. text strips markup; storage preserves
        source XHTML/macros and is the editing source. cacheInfo separates body age from head verification.
        check_confluence_revision refreshes metadata without downloading the body. Neither tool proves
        absence of unpublished edits. Configured Spaces apply; arbitrary URLs are never fetched.
        """
        from .confluence_read_tools import read_document
        return read_document(runtime, page, offset=offset, max_chars=max_chars, format=format, version=version)

    def read_attachment(key: str, attachment_id: str, offset: int = 0, max_bytes: int = 262144) -> dict:
        """Download bytes of an attachment verified to belong to key; obtain attachment_id from get_attachments.
        Returns filename, mimeType, totalBytes, base64, offset, hasMore and nextOffset.
        offset/max_bytes count decoded bytes; max_bytes=1..1048576, file size at most 10MiB.
        Follow nextOffset and decode/concatenate chunks to save or inspect the complete file.
        A missing/foreign ID is an error. No arbitrary local path or URL is accepted.
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
        """Read current ticket relationships; returns ancestors, siblings, related and documents.
        Related combines explicit Jira links and mentions from the body/recent comments, capped at 20;
        inspect rel/via rather than treating every mention as a formal dependency or duplicate.
        This is discovery context, not an exhaustive dependency graph or historical membership snapshot.
        Follow relevant keys/documents for their actual content and check partial/missing before aggregation.
        """
        runtime.check_key(key)
        client = runtime.client
        return {"ancestors": client.ticket_ancestors(key), "siblings": client.ticket_siblings(key),
                "related": client.ticket_related(key), "documents": client.ticket_documents(key)}

    def get_field_history(key: str, limit: int = 100) -> dict:
        """Read recent field changes including description/summary omitted by get_history's display timeline.
        Returns history rows (date, author, field, from, to), limit and mayHaveMore. limit=1..1000;
        the latest selected rows are returned in ascending date order. date is YYYY-MM-DD, without time/zone.
        There is no offset pagination; mayHaveMore=true means the returned history may be incomplete.
        Use get_evidence_page(kind=history) for original timestamps, authors, pagination and coverage.
        """
        runtime.check_key(key)
        if not 1 <= limit <= 1000:
            raise ValueError("limit은 1~1000이어야 합니다.")
        rows = runtime.client.ticket_field_history(key, limit)
        return {"history": rows, "limit": limit, "mayHaveMore": len(rows) >= limit}

    def get_epic_progress(key: str) -> dict:
        """Read an Epic's current WBS Story Point progress, not its completion count in a date range.
        Returns name, doneSp, totalSp, mockSp, progressPct (0..100), countedIssues and excludedIssues.
        Done follows statusCategory; Bug/Ops/운영 and 사용자 VoC are excluded. Missing SP defaults to 1
        for eligible issues, explicit zero stays zero, and mock SP remains in the denominator.
        This differs from PMO_VIT's descendant-count progress. Check partial/missing before claiming a total.
        """
        runtime.check_key(key)
        return runtime.client.epic_progress_one(key)

    def get_dashboard(kind: Literal["wbs", "vit", "workload"], module: str = "") -> dict:
        """Read the current WBS, VIT or workload dashboard, using the same UI builders and permissions.
        kind=wbs returns the configured weighted Epic/SP rollup for this local installation;
        module does not filter WBS. For vit/workload, module selects a module; workload requires its access.
        Empty workload module uses the caller's visible people. VIT uses descendant counts, not Story Points.
        Return shape follows the selected dashboard; partial/missing denotes incomplete data.
        These default current dashboards are not daily/weekly/monthly historical snapshots or completion ledgers.
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
        """Read one visible PMO_VIT root's detail; returns key, descendant tree and recent root comments.
        Tree statuses are current. This does not return all descendant comments or a full history.
        Use get_vit_overview to establish registration/visibility, get_comments for discussion,
        and get_history/get_field_history for changes. Empty detail alone does not prove no work exists.
        """
        from app.domain import vit
        from app.infra.settings import load_people, load_plan
        runtime.check_key(key)
        return vit.vit_detail(runtime.client, load_plan(), load_people(), key)

    def get_activity(user_id: str) -> dict:
        """Read a local recent activity preview for a verified user_id.
        Returns user, jira, confluence, partial and sourceErrors. Jira feed is capped at 20 events;
        Confluence is capped at 25 modified documents from the last 14 days. No pagination/date bounds.
        This is activity discovery, not a complete worklog, assigned-work list or calendar-period report.
        Follow ticket/document sources and report sourceErrors rather than interpreting missing rows as zero.
        """
        runtime.require_manager()
        return runtime.client.activity(user_id)

    for fn in (search_workspace, search_confluence_documents, read_confluence_document,
               read_attachment, get_ticket_context, get_field_history, get_epic_progress,
               get_dashboard, get_vit_detail, get_activity):
        register(fn, read_only=True)
