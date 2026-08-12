"""Query Runner — QueryPlan을 LLM 없이 등록된 read-only 도구로 실행한다."""

from __future__ import annotations

from app.agent.workflow.state import Node, note


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
        for spec in (state.get("query_plan") or {}).get("queries") or []:
            qid, source = str(spec.get("id") or ""), str(spec.get("source") or "")
            complete = spec.get("completeness") or "page"
            try:
                if source == "jira":
                    args = {"where": spec.get("where") or "", "order_by": spec.get("order_by") or "updated DESC",
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
                "trace": note(state, self.name, f"조회 {len(results)}개 실행")}


__all__ = ["QueryRunner"]
