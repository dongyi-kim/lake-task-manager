"""agent/tools/search_tools.py — Historian 의 탐색 도구.

"~~한 업무를 해야 한다" 는 막연한 요구에서 **현재 상황**을 복원하는 것이 이 도구들의 일이다.
한 방에 되지 않는다: 검색으로 실마리를 잡고(①) → 티켓을 열어 읽고(②) → 거기서 링크를 타고
문서·연관 티켓으로 번져 나간다(③). 그래서 도구를 이 세 단계로 나눠 두고, 어느 단계까지
갈지는 **모델이 판단**하게 한다(ReAct). 한 도구가 다 해 버리면 매번 최대 비용을 치른다.

docstring 은 **LLM 이 읽는 명세**다 — 언제 쓰는지, 무엇이 나오는지, 무엇이 안 나오는지까지 적는다.
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agent.tools._ctx import client, compact, settings, trim


def _issue_brief(raw: dict, sp_field: str = None) -> dict:
    f = (raw or {}).get("fields") or {}
    st = f.get("status") or {}
    a = f.get("assignee") or {}
    return compact({
        "key": raw.get("key"),
        "type": (f.get("issuetype") or {}).get("name"),
        "summary": f.get("summary"),
        "status": st.get("name"),
        "done": ((st.get("statusCategory") or {}).get("key") or "").lower() == "done",
        "assignee": a.get("name"),
        "components": [c.get("name") for c in (f.get("components") or []) if c.get("name")],
        "labels": f.get("labels") or [],
        "duedate": f.get("duedate"),
        "sp": f.get(sp_field) if sp_field else None,
        "created": (f.get("created") or "")[:10],
        "updated": (f.get("updated") or "")[:10],
    })


@tool
def search_work_history(query: str, limit: int = 8) -> dict:
    """업무 키워드로 **과거 이력**을 찾는다 — Jira 티켓과 Confluence 문서를 함께 뒤진다.

    업무 착수 요청을 받으면 **가장 먼저** 이걸 부른다. "이 일이 처음인가, 이미 하던 일인가"를
    여기서 가른다. 키워드는 2~5개 단어로 좁혀서 넣는다("CDC 실시간 수집" 처럼). 잘 안 나오면
    동의어·약어로 바꿔 **다시 부른다**(예: CDC / 변경데이터캡처 / 실시간연동).

    돌려주는 것: {"jira": [{key,title,status,assignee,issuetype,updated}...],
                 "confluence": [{title,url,excerpt}...]}
    돌려주지 않는 것: 티켓 본문·코멘트. 그건 get_ticket 으로 따로 연다(비용이 다르다).
    """
    from app.domain.search import search_all
    c, s = client(), settings()
    r = search_all(c, s, query, scope="all", limit=max(1, min(int(limit or 8), 20)))
    return {
        "query": query,
        "jira": [compact({k: it.get(k) for k in
                          ("key", "title", "status", "assignee", "issuetype", "epicKey", "updated")})
                 for it in (r.get("jira") or {}).get("items") or []],
        "confluence": [compact({"title": it.get("title"), "url": it.get("url"),
                                "excerpt": trim(it.get("excerpt") or it.get("snippet"), 200)})
                       for it in (r.get("confluence") or {}).get("items") or []],
    }


@tool
def get_ticket(key: str, comment_limit: int = 5) -> dict:
    """티켓 하나를 **본문·코멘트까지** 연다. search_work_history 로 찾은 키를 넣는다.

    "그래서 그때 무슨 결정이 났나"는 요약이 아니라 **코멘트**에 있다. 담당자 추천 근거를 모을
    때도 코멘트 작성자가 중요한 신호다. 다만 티켓마다 왕복이 생기니 **정말 볼 것만** 연다
    (검색 결과 상위 2~4건 정도).

    돌려주는 것: 요약·상태·담당자·컴포넌트·라벨·마감·SP·본문(요약본)·최근 코멘트.
    """
    c = client()
    raw = c.get_issue(key) or {}
    if not raw.get("key"):          # 없는 키에 빈 껍데기가 돌아오기도 한다 — key 로 판정한다
        return {"error": f"{key} 티켓을 찾을 수 없습니다. 키를 다시 확인하세요."}
    out = _issue_brief(raw, getattr(settings(), "sp_field_id", None))
    out["description"] = trim(((raw.get("fields") or {}).get("description")), 1200)
    # comments 는 **비어 있어도 싣는다** — "코멘트가 없다"와 "안 가져왔다"는 모델에게 다른 정보다.
    try:
        out["comments"] = [
            compact({"author": cm.get("authorId") or cm.get("author"),
                     "created": (cm.get("created") or "")[:10],
                     "body": trim(cm.get("body"), 500)})
            for cm in (c.issue_comments(key, max(1, min(int(comment_limit or 5), 20))) or [])]
    except Exception as e:                      # 코멘트가 막혀도 티켓 본문은 쓸모가 있다
        out["comments_error"] = str(e)[:200]
    return out


@tool
def get_ticket_context(key: str) -> dict:
    """티켓 하나에서 **바깥으로 한 홉** 나간다 — 연관 티켓·관련 문서·주요 이력.

    업무는 티켓 하나로 끝나지 않는다. 선행 작업, 갈라져 나온 후속 티켓, 설계를 적어 둔 Confluence
    문서가 링크로 이어져 있다. 검색 키워드만으로는 이 이웃들이 안 잡히므로, **핵심 티켓을 하나
    고른 뒤** 이 도구로 주변을 훑는다. 여기서 나온 문서 URL 은 read_document 로 본문을 읽는다.

    돌려주는 것: {"related": [...], "documents": [{title,url}...], "timeline": [주요 이력]}
    """
    c = client()
    out = {"key": key}
    for name, fn, proj in (
            ("related", c.ticket_related,
             lambda x: compact({"key": x.get("key"), "summary": x.get("summary") or x.get("title"),
                                "status": x.get("status"), "how": x.get("relation") or x.get("via")})),
            ("documents", c.ticket_documents,
             lambda x: compact({"title": x.get("title"), "url": x.get("url")})),
            ("timeline", c.ticket_timeline,
             lambda x: compact({"when": (x.get("created") or x.get("when") or "")[:10],
                                "who": x.get("author") or x.get("authorId"),
                                "what": trim(x.get("summary") or x.get("what") or x.get("field"), 120)})),
    ):
        try:
            rows = fn(key) or []
            rows = rows.get("items", rows) if isinstance(rows, dict) else rows
            out[name] = [proj(x) for x in rows[:12] if isinstance(x, dict)]
        except Exception as e:
            out[name] = []
            out[name + "_error"] = str(e)[:150]
    return out


@tool
def get_epic_tree(epic_key: str) -> dict:
    """Epic 하나의 **자식 트리 전체**(Story/Task/Bug + 각자의 Sub-Task).

    "이미 이 일을 담고 있는 Epic 이 있나"를 확인했으면, 그 안에 뭐가 얼마나 들어 있는지 본다.
    새 티켓을 **어디에 붙일지**, 이미 있는 것과 **겹치지 않는지** 판단하는 데 쓴다.
    """
    try:
        rows = client().epic_tree(epic_key) or []
    except Exception as e:
        return {"error": str(e)[:200]}
    def node(n):
        d = compact({"key": n.get("key"), "type": n.get("type"), "summary": n.get("summary"),
                     "status": n.get("statusName"), "done": n.get("statusCat") == "done",
                     "sp": n.get("sp"), "end": n.get("end")})
        kids = [node(s) for s in (n.get("subs") or n.get("children") or [])]
        if kids:
            d["subtasks"] = kids
        return d
    return {"epic": epic_key, "children": [node(n) for n in rows]}


@tool
def find_parent_epic(query: str = "", limit: int = 10) -> list:
    """새 티켓을 매달 **상위 Epic/Task 후보**를 찾는다. 티켓 트리를 제안하기 직전에 부른다.

    빈 문자열로 부르면 최근·활성 후보를 준다. 마땅한 후보가 없으면 Epic 을 새로 만드는 것도
    선택지다 — 무리하게 관련 없는 Epic 에 매달지 않는다.
    """
    try:
        rows = client().epic_candidates(query or "", limit=max(1, min(int(limit or 10), 25))) or []
    except Exception as e:
        return [{"error": str(e)[:200]}]
    return [compact({"key": r.get("key"), "summary": r.get("summary") or r.get("name"),
                     "type": r.get("type"), "status": r.get("status")}) for r in rows]
