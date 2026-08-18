"""검색 전문 역할이 사용하는 범위 강제·pagination 지원 read-only 도구.

검색 범위의 source of truth는 오직 ``search.jira.projects``와
``search.confluence.spaces``다. ``project_key``는 쓰기 대상이므로 이 모듈은 fallback으로
사용하지 않는다. 범위가 비어 있으면 조회를 전체로 넓히지 않고 명시적으로 실패한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from contextvars import ContextVar
from datetime import datetime, timezone

from langchain_core.tools import tool

from app.agent.pagination import PaginationAccumulator
from app.agent.tools._ctx import (client, compact, jira_scope, search_projects,
                                  search_spaces, settings, trim)


_THREAD: ContextVar[str] = ContextVar("agent_query_thread", default="")
_CURSOR_KEY = os.urandom(32)
_ORDER_RE = re.compile(
    r'^\s*(?:"[^"]+"|[A-Za-z][A-Za-z0-9_.-]*)(?:\s+(?:ASC|DESC))?'
    r'(?:\s*,\s*(?:"[^"]+"|[A-Za-z][A-Za-z0-9_.-]*)(?:\s+(?:ASC|DESC))?)*\s*$',
    re.I,
)

# Comment evidence is intentionally bounded independently from Jira's 20-row UI cache.
# A result beyond this ceiling is reported incomplete; silently truncating would let an
# ``all`` QuerySpec claim semantic coverage it does not possess.
COMMENT_SEARCH_RESULT_CAP = 200


def set_thread(thread_id: str) -> None:
    """cursor를 현재 대화에 묶는다. 서버가 주입하며 모델 입력으로 받지 않는다."""
    _THREAD.set(str(thread_id or ""))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _query_hash(source: str, canonical: str) -> str:
    return hashlib.sha256(f"{source}\n{canonical}".encode("utf-8")).hexdigest()[:24]


def _encode_cursor(source: str, canonical: str, offset: int) -> str:
    payload = {
        "v": 1, "s": source, "q": _query_hash(source, canonical),
        "o": max(0, int(offset)), "t": _THREAD.get(),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_CURSOR_KEY, raw, hashlib.sha256).digest()
    return _b64(raw) + "." + _b64(sig)


def _decode_cursor(cursor: str, source: str, canonical: str) -> int:
    if not cursor:
        return 0
    try:
        left, right = str(cursor).split(".", 1)
        raw, supplied = _unb64(left), _unb64(right)
        expected = hmac.new(_CURSOR_KEY, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("signature")
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("s") != source or payload.get("q") != _query_hash(source, canonical):
            raise ValueError("query")
        current = _THREAD.get()
        if payload.get("t") != current:
            raise ValueError("thread")
        return max(0, int(payload.get("o") or 0))
    except Exception as exc:
        raise ValueError("cursor가 현재 대화와 조회 조건에 유효하지 않습니다.") from exc


def _split_order(jql: str) -> tuple[str, str]:
    raw = str(jql or "").strip().rstrip(";").strip()
    hit = list(re.finditer(r"\bORDER\s+BY\b", raw, re.I))
    if not hit:
        return raw, ""
    pos = hit[-1]
    return raw[:pos.start()].strip(), raw[pos.end():].strip()


def _order(value: str, default: str = "updated DESC") -> str:
    raw = re.sub(r"^\s*ORDER\s+BY\s+", "", str(value or ""), flags=re.I).strip()
    raw = raw or default
    if ";" in raw or not _ORDER_RE.fullmatch(raw):
        raise ValueError("order_by는 'updated DESC, key ASC' 형태의 정렬 필드만 허용합니다.")
    fields = [part.strip() for part in raw.split(",")]
    if not any(re.sub(r'\s+(?:ASC|DESC)\s*$', '', f, flags=re.I).strip('"').lower() == "key"
               for f in fields):
        fields.append("key ASC")
    return ", ".join(fields)


def _canonical_jql(where: str, order_by: str) -> str:
    cond = str(where or "").strip().rstrip(";").strip()
    if re.search(r"\bORDER\s+BY\b", cond, re.I) or ";" in cond:
        raise ValueError("where에는 ORDER BY나 세미콜론을 넣지 말고 order_by를 별도로 사용하세요.")
    return f"{jira_scope(cond)} ORDER BY {_order(order_by)}"


def _projected_value(value):
    """요청 projection을 model context에 안전한 크기와 모양으로 줄인다."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return trim(value, 1000)
    if isinstance(value, list):
        return [_projected_value(x) for x in value[:20]]
    if isinstance(value, dict):
        preferred = ("id", "key", "name", "value", "displayName", "title")
        picked = {k: _projected_value(value.get(k)) for k in preferred if value.get(k) is not None}
        return picked or {str(k): _projected_value(v) for k, v in list(value.items())[:10]}
    return trim(value, 500)


def _issue_row(raw: dict, projected_fields: list[str] | None = None) -> dict:
    fields = (raw or {}).get("fields") or {}
    status = fields.get("status") or {}
    assignee = fields.get("assignee") or {}
    project = fields.get("project") or {}
    issue_type = fields.get("issuetype") or {}
    tier = ("subtask" if issue_type.get("subtask") else
            "epic" if str(issue_type.get("name") or "").lower() == "epic" else "task")
    standard = {"summary", "project", "issuetype", "status", "assignee", "priority",
                "components", "labels", "duedate", "created", "updated", "parent"}
    projected = {name: _projected_value(fields.get(name))
                 for name in (projected_fields or [])
                 if name not in standard and name in fields}
    return compact({
        "key": raw.get("key"), "project": project.get("key"),
        "summary": fields.get("summary"),
        "tier": tier,
        "issueType": issue_type.get("name"),
        "status": status.get("name"),
        "statusCategory": (status.get("statusCategory") or {}).get("key"),
        "assigneeId": assignee.get("name") or assignee.get("key"),
        "assignee": assignee.get("displayName") or assignee.get("name"),
        "priority": (fields.get("priority") or {}).get("name"),
        "components": [x.get("name") for x in (fields.get("components") or []) if x.get("name")],
        "labels": fields.get("labels") or [], "duedate": fields.get("duedate"),
        "created": fields.get("created"), "updated": fields.get("updated"),
        "parent": (fields.get("parent") or {}).get("key"),
        "fields": projected,
    })


def _jql_page(where: str, order_by: str, fields: list | None, page_size: int,
              cursor: str, source: str = "jira") -> dict:
    canonical = _canonical_jql(where, order_by)
    start = _decode_cursor(cursor, source, canonical)
    size = max(1, min(int(page_size or 100), 100))
    requested = [str(x).strip() for x in (fields or []) if str(x).strip()]
    base_fields = ["summary", "project", "issuetype", "status", "assignee", "priority",
                   "components", "labels", "duedate", "created", "updated", "parent"]
    for field in requested[:30]:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", field) and field not in base_fields:
            base_fields.append(field)
    page = client().search_issues_page(canonical, start_at=start, max_results=size,
                                       fields=base_fields, light=True)
    rows = [_issue_row(x, requested) for x in page["issues"]]
    next_cursor = (_encode_cursor(source, canonical, page["nextStartAt"])
                   if page.get("hasMore") else None)
    return {
        "canonicalJql": canonical,
        "scopeProjects": search_projects(),
        "startAt": page["startAt"], "pageSize": page["maxResults"],
        "total": page.get("total"), "returned": len(rows),
        "hasMore": bool(page.get("hasMore")), "nextCursor": next_cursor,
        "tickets": rows,
    }


@tool
def run_jql_v2(where: str = "", order_by: str = "updated DESC", fields: list = None,
               page_size: int = 100, cursor: str = "") -> dict:
    """Run one read-only page of JQL with configured Jira scope enforced by code.

    Put only the additional condition in `where`; code wraps it with every project in `search.jira.projects`.
    Empty configuration fails and never falls back to `project_key` or all Jira. Put sorting only in `order_by`;
    code appends `key ASC` for stable pagination. There is no 50-result total cap. Follow `nextCursor` until
    `hasMore=false` whenever completeness is `all`, and preserve `canonicalJql`, scope, total, and returned count.
    """
    try:
        return _jql_page(where, order_by, fields, page_size, cursor)
    except Exception as exc:
        return {"error": str(exc)[:300], "scopeProjects": search_projects(),
                "tickets": [], "returned": 0, "hasMore": False}


def execute_jql_all(where: str = "", order_by: str = "updated DESC",
                    fields: list | None = None, page_size: int = 100) -> dict:
    """모델 context 밖에서 모든 페이지를 순회해 안정적인 target snapshot을 만든다."""
    pager = PaginationAccumulator(max_pages=200)
    first = None
    while True:
        page = _jql_page(where, order_by, fields, page_size, pager.cursor, source="jira-all")
        first = first or page
        if not pager.add_page(page.get("tickets") or []):
            break
        if page.get("error"):
            pager.incomplete_reason = "provider_error"
            break
        if not pager.advance(
            has_more=bool(page.get("hasMore")),
            next_cursor=page.get("nextCursor"),
            total=(first or {}).get("total"),
        ):
            break
    result = {
        "canonicalJql": (first or {}).get("canonicalJql"),
        "scopeProjects": search_projects(), "total": (first or {}).get("total"),
        "tickets": pager.rows,
        "snapshotAt": datetime.now(timezone.utc).isoformat(),
    }
    result.update(pager.metadata())
    return result


def _cql_escape(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


@tool
def search_documents(query: str = "", where: str = "", content_type: str = "page",
                     modified_after: str = "", page_size: int = 50,
                     cursor: str = "") -> dict:
    """Search one read-only CQL page only within every space in `search.confluence.spaces`.

    Empty configuration fails and never widens to all spaces. `where` is an additional CQL condition and cannot
    contain `ORDER BY`. Follow `nextCursor` when complete coverage is required. Results preserve `canonicalCql`,
    scope, pagination metadata, document ID, title, URL, excerpt, and modification provenance.
    """
    spaces = search_spaces()
    if not spaces:
        return {"error": "검색 범위 미설정 — search.confluence.spaces를 지정하세요",
                "scopeSpaces": [], "documents": [], "hasMore": False}
    raw_where = str(where or "").strip().rstrip(";").strip()
    if re.search(r"\bORDER\s+BY\b", raw_where, re.I) or ";" in raw_where:
        return {"error": "where에는 ORDER BY나 세미콜론을 넣을 수 없습니다.",
                "scopeSpaces": spaces, "documents": [], "hasMore": False}
    joined = ", ".join('"' + _cql_escape(x) + '"' for x in spaces)
    conds = [f"space in ({joined})"]
    if query:
        conds.append(f'siteSearch ~ "{_cql_escape(query)}"')
    if content_type:
        conds.append(f'type = "{_cql_escape(content_type)}"')
    if modified_after:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", modified_after):
            return {"error": "modified_after는 YYYY-MM-DD 형식이어야 합니다.",
                    "scopeSpaces": spaces, "documents": [], "hasMore": False}
        conds.append(f'lastModified >= "{modified_after}"')
    if raw_where:
        conds.append(f"({raw_where})")
    canonical = " AND ".join(conds) + " ORDER BY lastModified DESC"
    try:
        start = _decode_cursor(cursor, "confluence", canonical)
        size = max(1, min(int(page_size or 50), 100))
        s = settings()
        base = (s.confluence_base or "").rstrip("/")
        if s.jira_env == "prod" and not base:
            raise ValueError("confluence_base 미설정")
        url = (base + "/rest/api/search") if (s.jira_env == "prod" and base) \
            else "/rest/api/search"
        data = client()._conf_get_json(url, params={"cql": canonical, "start": start, "limit": size})
        allowed = {x.upper() for x in spaces}
        docs = []
        for raw in data.get("results") or []:
            content = raw.get("content") or {}
            space = (content.get("space") or {}).get("key") or raw.get("space") or ""
            if str(space).upper() not in allowed:
                continue
            web = raw.get("url") or (content.get("_links") or {}).get("webui") or ""
            docs.append(compact({
                "id": content.get("id") or raw.get("id"), "space": space,
                "title": trim(raw.get("title") or content.get("title"), 180),
                "url": (base + web) if base and str(web).startswith("/") else web,
                "excerpt": trim(raw.get("excerpt"), 500),
                "modified": raw.get("lastModified") or content.get("version", {}).get("when"),
            }))
        returned = int(data.get("size") or len(data.get("results") or []))
        total = data.get("totalSize", data.get("total"))
        next_start = start + returned
        has_more = bool(returned) and (total is None or next_start < int(total))
        return {"canonicalCql": canonical, "scopeSpaces": spaces, "start": start,
                "total": total, "returned": len(docs), "hasMore": has_more,
                "nextCursor": _encode_cursor("confluence", canonical, next_start) if has_more else None,
                "documents": docs}
    except Exception as exc:
        return {"error": str(exc)[:300], "canonicalCql": canonical,
                "scopeSpaces": spaces, "documents": [], "hasMore": False}


def _search_comments_page(query: str = "", jql_where: str = "", author: str = "",
                          date_from: str = "", date_to: str = "", page_size: int = 20,
                          cursor: str = "", *, prove_complete: bool = False) -> dict:
    """Search comments by text, author, date range, and additional JQL within configured Jira projects.

    The read-only tool paginates candidate tickets and inspects comment bodies. Each hit preserves ticket key and
    summary, author, date, and snippet as provenance. Follow `nextCursor` for complete coverage and never replace a
    comment-content request with an issue-summary search.
    """
    for value, name in ((date_from, "date_from"), (date_to, "date_to")):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return {"error": f"{name}은 YYYY-MM-DD 형식이어야 합니다.", "comments": []}
    conds = [str(jql_where or "").strip()] if str(jql_where or "").strip() else []
    if query:
        safe = _cql_escape(query)
        conds.append(f'comment ~ "{safe}"')
    where = " AND ".join(f"({x})" for x in conds) if conds else "status is not EMPTY"
    source = "comments:" + hashlib.sha256(
        f"{author}|{date_from}|{date_to}|{query}".encode("utf-8")).hexdigest()[:12]
    try:
        page = _jql_page(where, "updated DESC", ["summary"], min(page_size, 25), cursor, source)
        hits = []
        snapshots = []
        omitted_hits = 0
        needle = str(query or "").casefold()
        for ticket in page.get("tickets") or []:
            key = ticket.get("key")
            if prove_complete:
                snapshot = client().comment_snapshot(key)
                if not isinstance(snapshot, dict):
                    snapshot = {"key": key, "complete": False, "comments": [],
                                "incompleteReason": "invalid_snapshot", "remaining": None}
                snapshots.append(snapshot)
                comments = snapshot.get("comments") or []
            else:
                snapshot = None
                comments = client().issue_comments(key, 100) or []
            for comment in comments:
                who = str(comment.get("authorId") or comment.get("author") or "")
                when = str(comment.get("date") or comment.get("created") or "")
                body = re.sub(r"<[^>]+>", " ", str(comment.get("html") or comment.get("body") or ""))
                if author and author.casefold() not in who.casefold():
                    continue
                if date_from and when[:10] < date_from:
                    continue
                if date_to and when[:10] > date_to:
                    continue
                if needle and needle not in body.casefold():
                    continue
                normalized_body = " ".join(body.split())
                body_truncated = len(normalized_body) > 500
                if not prove_complete or len(hits) < COMMENT_SEARCH_RESULT_CAP:
                    hit = {"id": str(comment.get("id") or ""),
                           "ticketKey": key, "ticketSummary": ticket.get("summary"),
                           "author": who, "date": when,
                           "snippet": trim(normalized_body, 500)}
                    if body_truncated:
                        hit["bodyTruncated"] = True
                    hits.append(hit)
                    if prove_complete and body_truncated and snapshot is not None:
                        # Cardinality completeness is not content completeness.  Research
                        # sees only this bounded row, so a clipped body cannot authorize an
                        # ``all comments`` acquisition shortcut.
                        snapshot["bodyTruncated"] = True
                else:
                    # Continue validating provider coverage, but never grow the artifact.
                    if snapshot is not None:
                        snapshot["resultTruncated"] = True
                    omitted_hits += 1
        if not prove_complete:
            return {"canonicalJql": page.get("canonicalJql"),
                    "scopeProjects": page.get("scopeProjects"),
                    "candidateTickets": page.get("returned"),
                    "returned": len(hits), "hasMore": page.get("hasMore"),
                    "nextCursor": page.get("nextCursor"), "comments": hits}
        candidate_total = page.get("total")
        candidate_returned = page.get("returned")
        candidate_keys = [str(row.get("key") or "").strip().upper()
                          for row in (page.get("tickets") or []) if isinstance(row, dict)]
        candidate_complete = (
            type(candidate_total) is int and candidate_total >= 0
            and type(candidate_returned) is int and candidate_returned >= 0
            and not bool(page.get("hasMore"))
            and candidate_returned == candidate_total
            and candidate_returned == len(candidate_keys)
            and len(candidate_keys) == len(set(candidate_keys))
            and all(re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", key)
                    for key in candidate_keys)
        )
        incomplete = sorted({str(row.get("key") or key) for row in snapshots
                             if (not row.get("complete") or row.get("resultTruncated")
                                 or row.get("bodyTruncated"))})
        remaining_values = [row.get("remaining") for row in snapshots]
        remaining = (sum(value for value in remaining_values if type(value) is int)
                     if all(type(value) is int for value in remaining_values) else None)
        comments_complete = not incomplete and len(snapshots) == int(candidate_returned or 0)
        comment_coverage = {
            "tickets": len(snapshots),
            "comments": sum(int(row.get("returned") or 0) for row in snapshots),
            "complete": comments_complete,
            "incompleteTickets": incomplete,
            "remaining": remaining,
            "resultTruncated": bool(omitted_hits),
            "resultRemaining": omitted_hits,
        }
        candidate_coverage = {
            "returned": candidate_returned,
            "total": candidate_total,
            "hasMore": bool(page.get("hasMore")),
            "complete": candidate_complete,
            "keys": candidate_keys,
        }
        return {"canonicalJql": page.get("canonicalJql"),
                "scopeProjects": page.get("scopeProjects"),
                "candidateTickets": candidate_returned, "candidateCoverage": candidate_coverage,
                "commentCoverage": comment_coverage,
                "complete": candidate_complete and comments_complete,
                "returned": len(hits),
                "hasMore": page.get("hasMore"), "nextCursor": page.get("nextCursor"),
                "comments": hits}
    except Exception as exc:
        return {"error": str(exc)[:300], "scopeProjects": search_projects(),
                "comments": [], "hasMore": False}


@tool
def search_comments(query: str = "", jql_where: str = "", author: str = "",
                    date_from: str = "", date_to: str = "", page_size: int = 20,
                    cursor: str = "") -> dict:
    """Search a bounded cached page of Jira comments within configured projects.

    This public/ReAct tool intentionally preserves the low-cost UI-cache behavior.  A
    server-owned QueryRunner ``completeness=all`` contract uses the private paginated helper
    below instead; models cannot opt themselves into that expensive authority path.
    """
    return _search_comments_page(
        query=query, jql_where=jql_where, author=author,
        date_from=date_from, date_to=date_to, page_size=page_size, cursor=cursor,
        prove_complete=False,
    )


def search_comments_complete_page(args: dict) -> dict:
    """Server-only one-page adapter with independently proven comment-body coverage."""
    values = dict(args or {})
    allowed = {key: values.get(key) for key in (
        "query", "jql_where", "author", "date_from", "date_to", "page_size", "cursor"
    ) if key in values}
    return _search_comments_page(**allowed, prove_complete=True)


@tool
def query_people(name: str = "", user_id: str = "", module: str = "",
                 participated_ticket: str = "", max_in_progress: int = -1,
                 page_size: int = 50, cursor: str = "") -> dict:
    """Query people by display name, user ID, module, ticket participation, and workload ceiling.

    This read-only tool returns candidate facts and provenance for People Advisor; it does not rank or recommend.
    Ticket participation is allowed only for keys in `search.jira.projects`. Follow `nextCursor` for complete
    coverage and preserve evidence and workload fields without inferring skill or performance.
    """
    from app.infra.settings import load_people
    from app.agent.tools.people_tools import scoped_person_workload, strip_title

    roster = load_people() or {}
    name_query = strip_title(str(name or "").lstrip("@").strip())
    titled = re.sub(
        r"\s*(?:TL|PL|PM|PO|EM|M|파트장|그룹장|본부장|팀장|실장|부장|차장|과장|대리|"
        r"선임|책임|수석|매니저|리더|님|씨)$", "", name_query, flags=re.I).strip()
    if len(titled) >= 2:
        name_query = titled
    rows = []
    for mod, ids in roster.items():
        if module and str(mod).casefold() != str(module).casefold():
            continue
        for uid in ids or []:
            if user_id and str(uid).casefold() != str(user_id).casefold():
                continue
            rows.append({"id": str(uid), "module": mod, "evidence": [f"roster:{mod}"]})
    if name_query and not user_id:
        # A name query is a directory lookup, not an instruction to return the whole roster.
        # Keeping all roster rows made three meeting names expand to 20 people each and then
        # compute 60 full workload bundles before asking one ambiguity question.
        roster_module = {str(uid): mod for mod, ids in roster.items() for uid in (ids or [])}
        matched = []
        try:
            users = client().provider.get_json(
                "/rest/api/2/user/search", params={"username": name_query, "maxResults": 100}) or []
            known = {}
            for raw in users:
                uid = raw.get("name") or raw.get("key")
                display = raw.get("displayName") or uid or ""
                if not uid or name_query.casefold() not in display.casefold():
                    continue
                row = known.get(uid) or {
                    "id": uid, "module": roster_module.get(str(uid), ""), "evidence": []}
                row["name"] = display
                row["email"] = raw.get("emailAddress") or ""
                row["evidence"].append("jira:user-directory")
                if uid not in known:
                    matched.append(row); known[uid] = row
            rows = matched
        except Exception:
            rows = [r for r in rows if name_query.casefold() in r["id"].casefold()]
    if participated_ticket:
        key = participated_ticket.strip().upper()
        allowed = {p.upper() for p in search_projects()}
        if key.split("-", 1)[0] not in allowed:
            return {"error": "티켓이 search.jira.projects 범위 밖입니다.", "people": []}
        from app.domain.search import _ticket_people
        participants = set(_ticket_people(client(), key) or [])
        rows = [r for r in rows if r["id"] in participants]
        for row in rows:
            row["evidence"].append(f"ticket-participant:{key}")
    canonical = json.dumps({"name": name_query, "user_id": user_id, "module": module,
                            "participated_ticket": participated_ticket,
                            "max_in_progress": max_in_progress}, ensure_ascii=False, sort_keys=True)
    try:
        start = _decode_cursor(cursor, "people", canonical)
    except Exception as exc:
        return {"error": str(exc), "people": []}
    size = max(1, min(int(page_size or 50), 100))
    # A workload ceiling requires enriching every candidate before filtering.  Without that
    # filter, enrich only this page; otherwise each cursor page repeats the same expensive
    # workload calls for every candidate already returned on earlier pages.
    candidates = rows if max_in_progress >= 0 else rows[start:start + size]
    enriched = []
    for row in candidates:
        try:
            bundle = scoped_person_workload(row["id"], 28)
            def count(bucket):
                value = (bucket or {}).get("count", 0) if isinstance(bucket, dict) else 0
                return sum(value.values()) if isinstance(value, dict) else int(value or 0)
            row["name"] = row.get("name") or bundle.get("name") or row["id"]
            row["workload"] = {"open": count(bundle.get("open")),
                               "inProgress": count(bundle.get("inProgress")),
                               "done28d": count(bundle.get("done7d"))}
            row["evidence"].append("jira:workload-28d")
        except Exception as exc:
            row["workloadError"] = str(exc)[:120]
        if max_in_progress >= 0 and row.get("workload", {}).get("inProgress", 10 ** 9) > max_in_progress:
            continue
        enriched.append(row)
    filtered = enriched if max_in_progress >= 0 else rows
    page = enriched[start:start + size] if max_in_progress >= 0 else enriched
    more = start + len(page) < len(filtered)
    return {"total": len(filtered), "returned": len(page), "hasMore": more,
            "nextCursor": _encode_cursor("people", canonical, start + len(page)) if more else None,
            "people": page}


@tool
def resolve_references(refs: list) -> dict:
    """Validate ticket, person, document, and external references and return canonical link or badge metadata.

    Input items use `{id, kind, key|user_id|page_id|url, label?}`. Call before rendering references and never emit
    raw anchor or badge HTML. Do not turn an unresolved reference into a broken link; propagate it as a warning or
    a blocking write issue. This tool is read-only.
    """
    from app.agent.references import resolve_references as _resolve
    return _resolve(refs)


__all__ = ["run_jql_v2", "search_documents", "search_comments", "query_people",
           "resolve_references",
           "execute_jql_all", "set_thread"]
