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
    # Some models put a complete JQL expression in `query` despite the typed contract. Recover its intent
    # instead of tokenizing `project`, `AND`, and field names as text search terms. Configured search projects
    # remain the outer scope; model placeholders and project clauses are never honored.
    looks_jql = bool(re.search(
        r"(?:^|\s)(?:project|summary|description|text|status|statusCategory|issueType|issuetype|"
        r"parent|assignee|labels?|component)\s*(?:=|!=|~|\bin\b|\bis\b)", query, re.I))
    if looks_jql:
        structural = re.sub(r"\bORDER\s+BY\b.*$", "", query, flags=re.I).strip()
        structural = re.sub(
            r"^\s*project\s*(?:=\s*[^\s)]+|in\s*\([^)]*\))\s+AND\s+", "", structural,
            flags=re.I)
        structural = re.sub(r"\s+AND\s+project\s*(?:=\s*[^\s)]+|in\s*\([^)]*\))", "",
                            structural, flags=re.I)
        structural = structural.replace("'", '"')
        structural = re.sub(r"issueType\s*=\s*SubTask", "issuetype = Sub-Task", structural,
                            flags=re.I)
        structural = re.sub(r'"Epic Link"\s*=\s*([A-Z][A-Z0-9]*-\d+)', r"parent = \1",
                            structural, flags=re.I)
        # Query planners often quote a bag of keywords as one Jira text phrase. That silently loses
        # relevant tickets whose words occur in separate fields/sentences. Expand only full-text phrases;
        # structural and summary phrases keep native JQL semantics. Korean `...정보` also gets a
        # conservative stem alternative for compound wording.
        def expand_text(match):
            phrase = match.group(1).strip()
            tokens = _lexical_terms(phrase, limit=5)
            clauses = []
            for token in tokens[:5]:
                if token.endswith("정보") and len(token) > 3:
                    clauses.append(f'(text ~ "{token}" OR text ~ "{token[:-2]}")')
                else:
                    clauses.append(f'text ~ "{token}"')
            return "(" + " AND ".join(clauses) + ")" if len(clauses) > 1 else \
                (clauses[0] if clauses else match.group(0))

        structural = re.sub(r'text\s*~\s*"([^"\n]+)"', expand_text, structural,
                            flags=re.I)
        return f"({base}) AND ({structural})" if base and structural else (structural or base)
    if re.search(r"\b(?:text|summary|description)\s*~", base, re.I):
        return base
    terms = _lexical_terms(query)
    lexical = " AND ".join(f'text ~ "{term}"' for term in terms[:4])
    if not lexical:
        return base
    return f"({base}) AND ({lexical})" if base else lexical


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
        return {"query_results": results, "query_artifacts": artifacts,
                "assignment_completion": artifacts.get("incomplete-assignees") or {},
                "trace": note(state, self.name, f"조회 {len(results)}개 실행")}


__all__ = ["QueryRunner", "_jira_where"]
