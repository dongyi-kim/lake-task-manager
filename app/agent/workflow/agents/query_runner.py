"""Query Runner — QueryPlan을 LLM 없이 등록된 read-only 도구로 실행한다."""

from __future__ import annotations

import re

from app.agent.workflow.state import Node, note


_LEXICAL_IGNORED = {
    "task", "ticket", "jira", "작업", "티켓", "이력", "조회", "검색",
    "위한", "위해", "관련", "새로", "만들자", "만들기", "생성", "생성한다",
}


def _search_token(token: str) -> str:
    """Jira text 검색용 최소 어간. 형태소 추측 대신 흔한 조사·서술 접미만 제거한다."""
    value = str(token or "").strip().strip(".,;:!?…")
    # 긴 접미부터 제거. 영문 기술어 뒤의 한국어 조사(`Avro로`)도 같은 규칙을 쓴다.
    for suffix in ("으로부터", "에서는", "전환하는", "생성하는", "위해서", "으로", "에서",
                   "에게", "하는", "한다", "했다", "하며", "하고", "처럼", "까지",
                   "부터", "로", "을", "를", "은", "는", "이", "가", "의", "에"):
        if value.endswith(suffix) and len(value) - len(suffix) >= 2:
            value = value[:-len(suffix)]
            break
    return value


def _lexical_terms(text: str, limit: int = 4) -> list[str]:
    terms, seen = [], set()
    for raw in re.findall(r"[A-Za-z0-9가-힣_.-]{2,}", str(text or "")):
        token = _search_token(raw)
        folded = token.casefold()
        if not token or folded in {x.casefold() for x in _LEXICAL_IGNORED} or folded in seen:
            continue
        seen.add(folded)
        terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def _jira_where(where: str, query: str) -> str:
    """Combine typed Jira lexical query with structural filters using safe JQL text clauses."""
    base = str(where or "").strip()
    query = str(query or "").strip()
    if not query:
        return base
    # ``query`` is a lexical subject, never raw JQL.  Silently repairing model-authored
    # pseudo-JQL changed invalid filters into a different lexical search and let Research
    # treat an empty result as verified absence.  Fail explicitly so the existing
    # QueryPlan completeness contract can surface the gap; structural conditions belong in
    # typed ``where`` and configured search projects remain enforced by the executor.
    looks_jql = bool(re.search(
        r"(?:^|\s)(?:project|summary|description|text|status|statusCategory|issueType|issuetype|"
        r"parent|assignee|labels?|component)\s*(?:=|!=|~|\bin\b|\bis\b)", query, re.I))
    if looks_jql:
        raise ValueError(
            "QueryPlan의 query에는 JQL을 넣을 수 없습니다. lexical subject는 query에, "
            "구조 조건은 where에 분리해야 합니다."
        )
    if re.search(r"\b(?:text|summary|description)\s*~", base, re.I):
        return base
    terms = _lexical_terms(query)
    clauses = [f'text ~ "{term}"' for term in terms[:4]]
    # Public technology queries commonly contain umbrella / feature / metric. Requiring
    # all three loses relevant tickets that omit only the umbrella name. Keep the narrow
    # all-Latin three-term form at a 2-of-3 boundary; work phrases retain strict AND.
    if len(clauses) == 3 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9.+-]*", term)
                                 for term in terms):
        lexical = "(" + " OR ".join(
            f"({clauses[left]} AND {clauses[right]})"
            for left, right in ((0, 1), (0, 2), (1, 2))) + ")"
    else:
        lexical = " AND ".join(clauses)
    if not lexical:
        return base
    return f"({base}) AND ({lexical})" if base else lexical


def _needs_evidence_materialization(state, results: list[dict]) -> bool:
    """Whether search hits must be opened before the single research synthesis pass.

    Listing/count requests intentionally keep lightweight rows. Research and create/duplicate-check
    requests need the ticket body, comments, and document body that a ReAct loop would otherwise open
    through several model round trips.
    """
    tasks = (state.get("request_plan") or {}).get("tasks") or []
    if any(str(task.get("kind") or "") == "research" for task in tasks
           if isinstance(task, dict)):
        return True
    if str(state.get("intent") or "") == "plan_work":
        return True
    sources = {str(row.get("source") or "") for row in results if isinstance(row, dict)}
    return len(sources & {"jira", "comments", "confluence", "web", "github"}) >= 2


def _materialize_evidence(results: list[dict]) -> dict:
    """Open selected Jira and Confluence hits without another LLM routing loop.

    Search order is already the QueryPlan's relevance/order contract. Preserve that order, deduplicate
    identities, and cap materialization to one human-reviewable evidence set. Individual read failures are
    explicit so Research Analyst can fall back to ReAct instead of silently synthesizing thin evidence.
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.agent import tools as T

    ticket_keys, document_refs = [], []
    for row in results:
        if not isinstance(row, dict):
            continue
        result = row.get("result") or {}
        for ticket in result.get("tickets") or []:
            key = str((ticket or {}).get("key") or "").strip().upper()
            if key and key not in ticket_keys:
                ticket_keys.append(key)
        for comment in result.get("comments") or []:
            key = str((comment or {}).get("ticketKey") or "").strip().upper()
            if key and key not in ticket_keys:
                ticket_keys.append(key)
        for document in result.get("documents") or []:
            ref = str((document or {}).get("url") or (document or {}).get("id") or "").strip()
            if ref and ref not in document_refs:
                document_refs.append(ref)

    ticket_keys, document_refs = ticket_keys[:8], document_refs[:4]

    def open_ticket(key: str) -> dict:
        try:
            value = T.BY_NAME["get_ticket"].invoke({"key": key, "comment_limit": 8}) or {}
            return value if not value.get("error") else {"key": key, "error": value["error"]}
        except Exception as exc:
            return {"key": key, "error": str(exc)[:240]}

    def open_document(ref: str) -> dict:
        try:
            value = T.BY_NAME["read_document"].invoke({"url_or_id": ref}) or {}
            return value if not value.get("error") else {"url": ref, "error": value["error"]}
        except Exception as exc:
            return {"url": ref, "error": str(exc)[:240]}

    with ThreadPoolExecutor(max_workers=max(1, min(6, len(ticket_keys) + len(document_refs)))) as pool:
        ticket_details = list(pool.map(open_ticket, ticket_keys)) if ticket_keys else []
        document_bodies = list(pool.map(open_document, document_refs)) if document_refs else []

    errors = [str(row.get("error")) for row in ticket_details + document_bodies
              if isinstance(row, dict) and row.get("error")]
    ticket_target = next((row for row in results if row.get("source") == "jira"), None) \
        or next((row for row in results if row.get("source") == "comments"), None)
    document_target = next((row for row in results if row.get("source") == "confluence"), None)
    if ticket_target is not None and ticket_details:
        ticket_target["result"] = dict(ticket_target.get("result") or {},
                                       ticketDetails=ticket_details)
        if errors:
            ticket_target["result"]["materializationErrors"] = errors
    if document_target is not None and document_bodies:
        document_target["result"] = dict(document_target.get("result") or {},
                                          documentBodies=document_bodies)
        if errors:
            document_target["result"]["materializationErrors"] = errors
    return {
        "tickets": len(ticket_details), "documents": len(document_bodies),
        "ticketDetails": ticket_details, "documentBodies": document_bodies,
        "errors": errors,
    }


class QueryRunner:
    name = Node.QUERY_RUNNER

    def node(self):
        return self._run

    @staticmethod
    def _all_pages(tool_obj, args: dict) -> tuple[list, dict]:
        rows, cursor, pages = [], "", 0
        meta = {}
        while True:
            payload = dict(args, cursor=cursor)
            result = tool_obj.invoke(payload) or {}
            pages += 1
            meta = meta or {k: result.get(k) for k in (
                "canonicalJql", "canonicalCql", "scopeProjects", "scopeSpaces", "total")}
            bucket = result.get("tickets") or result.get("documents") \
                or result.get("comments") or result.get("people") or []
            rows.extend(bucket)
            nxt = result.get("nextCursor")
            if result.get("error") or not result.get("hasMore") or not nxt or nxt == cursor:
                if result.get("error"):
                    meta["error"] = result["error"]
                break
            cursor = nxt
        meta["pages"] = pages
        return rows, meta

    def _run(self, state):
        from app.agent import tools as T
        from app.agent.tools.query_tools import execute_jql_all

        results, artifacts = [], {}
        # 이 유형은 LLM이 만든 단일 JQL만으로 끝낼 수 없다. 제목 검색 결과에서 parent를
        # 고른 뒤 그 parent의 직계 Sub-Task를 전수 조회해야 하므로 deterministic join을 먼저 돈다.
        from app.agent.workflow.assignment_completion import (
            asks_incomplete_assignees, lookup_incomplete_assignees,
        )
        from app.agent.workflow.state import last_user_text
        if asks_incomplete_assignees(last_user_text(state)):
            completion = lookup_incomplete_assignees(
                last_user_text(state), state.get("keywords") or [])
            artifacts["incomplete-assignees"] = completion
            results.append({"id": "incomplete-assignees", "source": "jira",
                            "result": completion})
        for spec in (state.get("query_plan") or {}).get("queries") or []:
            qid, source = str(spec.get("id") or ""), str(spec.get("source") or "")
            complete = spec.get("completeness") or "page"
            try:
                if source == "jira":
                    args = {"where": _jira_where(spec.get("where") or "", spec.get("query") or ""),
                            "order_by": spec.get("order_by") or "updated DESC",
                            "fields": spec.get("fields") or [], "page_size": spec.get("page_size") or 50}
                    if complete == "all":
                        raw = execute_jql_all(**args)
                    else:
                        raw = T.BY_NAME["run_jql_v2"].invoke(args)
                elif source == "confluence":
                    args = {"query": spec.get("query") or "", "where": spec.get("where") or "",
                            "page_size": spec.get("page_size") or 50}
                    if complete == "all":
                        rows, meta = self._all_pages(T.BY_NAME["search_documents"], args)
                        raw = dict(meta, documents=rows, returned=len(rows))
                    else:
                        raw = T.BY_NAME["search_documents"].invoke(args)
                elif source == "comments":
                    if not str(spec.get("query") or "").strip() \
                            and not str(spec.get("where") or "").strip():
                        raw = {"error": "빈 댓글 전수조회는 허용되지 않습니다.",
                               "comments": [], "returned": 0}
                        artifacts[qid] = raw
                        results.append({"id": qid, "source": source, "result": raw})
                        continue
                    args = {"query": spec.get("query") or "", "jql_where": spec.get("where") or "",
                            "page_size": min(spec.get("page_size") or 20, 25)}
                    if complete == "all":
                        rows, meta = self._all_pages(T.BY_NAME["search_comments"], args)
                        raw = dict(meta, comments=rows, returned=len(rows))
                    else:
                        raw = T.BY_NAME["search_comments"].invoke(args)
                elif source == "people":
                    args = {"name": spec.get("query") or "", "module": spec.get("where") or "",
                            "page_size": spec.get("page_size") or 50}
                    if complete == "all":
                        rows, meta = self._all_pages(T.BY_NAME["query_people"], args)
                        raw = dict(meta, people=rows, returned=len(rows))
                    else:
                        raw = T.BY_NAME["query_people"].invoke(args)
                elif source in ("web", "github"):
                    raw = T.BY_NAME["search_" + source].invoke(
                        {"query": spec.get("query") or "", "limit": min(spec.get("page_size") or 5, 10)})
                else:
                    raw = {"error": f"지원하지 않는 source: {source}"}
            except Exception as exc:
                raw = {"error": str(exc)[:240]}
            # full target set은 state artifact에 보존하되 LLM에는 각 source 앞부분만 전달한다.
            artifacts[qid] = raw
            compact = dict(raw)
            # 전체 집합은 artifact에 보존한다. LLM에는 정렬된 앞부분과 total만 싣는다.
            # 50건을 그대로 주입하면 생성 한 건에서도 Research Analyst 입력이 14k tokens까지
            # 불었다(PASTE1 실측). source별로 사람이 한 화면에서 검토할 양만 남긴다.
            caps = {"tickets": 12, "documents": 10, "comments": 12,
                    "people": 20, "results": 8}
            for field, cap in caps.items():
                if isinstance(compact.get(field), list) and len(compact[field]) > cap:
                    compact[field] = compact[field][:cap]
                    compact["contextTruncated"] = True
                    compact["artifactId"] = qid
            results.append({"id": qid, "source": source, "result": compact})
        if _needs_evidence_materialization(state, results):
            materialized = _materialize_evidence(results)
            if materialized["tickets"] or materialized["documents"] or materialized["errors"]:
                artifacts["evidence-materialization"] = materialized
        return {"query_results": results, "query_artifacts": artifacts,
                "assignment_completion": artifacts.get("incomplete-assignees") or {},
                "trace": note(state, self.name, f"조회 {len(results)}개 실행")}


__all__ = ["QueryRunner", "_jira_where", "_needs_evidence_materialization",
           "_materialize_evidence"]
