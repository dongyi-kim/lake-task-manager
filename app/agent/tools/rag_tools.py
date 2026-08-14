"""agent/tools/rag_tools.py — RAG 두 계층을 도구로 노출한다.

`search_work_history`(키워드)와 `deep_search`(의미)는 **경쟁하지 않는다**. 전자는 그 단어를 쓴
문서를 찾고, 후자는 그 단어를 안 썼지만 같은 이야기를 하는 문서를 찾는다. 둘 다 필요하다 —
"CDC"로 검색하면 "변경분 실시간 반영"이라 적힌 6개월 전 티켓은 절대 안 나온다.

비용이 다르므로 도구도 나눠 둔다. `deep_search` 는 본문을 긁고 임베딩까지 하므로 첫 호출이
느리다(그 뒤로는 장부 덕에 빠르다). 매번 부를 도구가 아니라 **한 주제에 한 번** 부를 도구다.
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agent.tools._ctx import client, compact, settings, trim


@tool
def search_rules(question: str, k: int = 4) -> list:
    """Search internal policy for ticket authoring, progress calculation, and assignee selection.

    Call this before creating tickets or recommending an assignee. Do not rely on memory for
    rules whose violation can harm the user, such as where Story Point is valid or what happens
    when Epic Link is missing.

    Pass a focused policy question, for example: "Where is Story Point valid?", "Which tickets
    are excluded from progress?", "How is an assignee selected?", or "When should a Task be
    decomposed into Sub-Tasks?"
    """
    from app.agent.retrieval import static_index
    try:
        hits = static_index.search(question, k=k)
    except Exception as e:
        return [{"error": f"규칙 색인을 읽지 못했습니다: {str(e)[:200]}"}]
    return [{"rule": h["text"], "출처": h["source"]} for h in hits]


@tool
def deep_search(topic: str, limit: int = 8) -> dict:
    """Research one topic deeply with keyword search, one-hop expansion, and semantic search.

    Use this when `search_work_history` finds no lead or returns leads without enough context.
    It searches live data, collects linked tickets and relevant Confluence bodies, indexes those
    records, and retrieves semantically similar history even when the wording differs.

    This is expensive. Call it at most once per topic; subsequent calls may be cached. Returns
    `{"keyword": [...], "documents": [...], "similar": [...], "indexed": {...}}`.
    """
    from app.agent.retrieval.harvest import harvest
    try:
        r = harvest(client(), settings(), topic, limit=max(1, min(int(limit or 8), 15)))
    except Exception as e:
        return {"error": str(e)[:300]}
    return {
        "keyword": [compact({k: it.get(k) for k in ("key", "title", "status", "assignee", "updated")})
                    for it in r["live"]["jira"]],
        "documents": [compact({"title": trim(it.get("title"), 100), "url": it.get("url")})
                      for it in r["live"]["confluence"]],
        "similar": [compact({"id": s["doc_id"], "kind": s["kind"], "title": trim(s["title"], 100),
                             "updated": (s.get("updated") or "")[:10],
                             "excerpt": trim(s["text"], 300)})
                    for s in r["semantic"]],
        # 색인 통계를 노출하는 건 모델을 위해서가 아니라 **사람이 로그에서 볼 수 있게** 하려는 것이다.
        "indexed": r["index"],
    }
