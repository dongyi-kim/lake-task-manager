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
    """**티켓 작성 규칙·진척률 산식·담당자 추천 정책**을 찾아본다(사내 규칙 문서).

    티켓을 만들거나 담당자를 제안하기 전에 반드시 확인한다. 규칙을 기억에 의존해 답하지 마라 —
    Story Point 를 어디에 매길 수 있는지, Epic Link 를 빠뜨리면 무슨 일이 생기는지처럼
    **틀리면 사용자가 손해 보는** 것들이 여기 있다.

    질문 형태로 넣는다: "Story Point 는 어디에 매기나" / "진척률에서 빠지는 티켓" /
    "담당자를 어떻게 고르나" / "Sub-Task 를 언제 쪼개나"
    """
    from app.agent.retrieval import static_index
    try:
        hits = static_index.search(question, k=k)
    except Exception as e:
        return [{"error": f"규칙 색인을 읽지 못했습니다: {str(e)[:200]}"}]
    return [{"rule": h["text"], "출처": h["source"]} for h in hits]


@tool
def deep_search(topic: str, limit: int = 8) -> dict:
    """주제 하나를 **깊게** 판다 — 키워드 검색 + 링크 한 홉 + **의미 기반 검색**.

    `search_work_history` 로 실마리가 안 잡히거나, 잡혔는데 맥락이 부족할 때 쓴다. 하는 일:
      ① 실시간 검색(방금 만든 티켓도 잡힌다)
      ② 상위 결과의 **연관 티켓·관련 Confluence 문서 본문**까지 수확
      ③ 그것들을 색인해 두고 **의미가 비슷한 과거 기록**을 찾아 준다
         — 키워드가 하나도 안 겹쳐도 같은 이야기면 잡힌다("CDC" ↔ "변경분 실시간 반영")

    비싸다. 한 주제에 **한 번**만 부른다(두 번째부터는 캐시가 있어 빠르다).
    돌려주는 것: {"keyword": [...], "documents": [...], "similar": [...], "indexed": {...}}
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
