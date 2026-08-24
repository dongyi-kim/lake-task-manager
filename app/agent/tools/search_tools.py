"""agent/tools/search_tools.py — ResearchAnalyst 의 탐색 도구.

"~~한 업무를 해야 한다" 는 막연한 요구에서 **현재 상황**을 복원하는 것이 이 도구들의 일이다.
한 방에 되지 않는다: 검색으로 실마리를 잡고(①) → 티켓을 열어 읽고(②) → 거기서 링크를 타고
문서·연관 티켓으로 번져 나간다(③). 그래서 도구를 이 세 단계로 나눠 두고, 어느 단계까지
갈지는 **모델이 판단**하게 한다(ReAct). 한 도구가 다 해 버리면 매번 최대 비용을 치른다.

docstring 은 **LLM 이 읽는 명세**다 — 언제 쓰는지, 무엇이 나오는지, 무엇이 안 나오는지까지 적는다.
"""

from __future__ import annotations

import re

from langchain_core.tools import tool

from app.agent.tools._ctx import (client, compact, jira_key_allowed, jira_scope,
                                  search_projects, search_spaces, settings, trim)


def _issue_brief(raw: dict, sp_field: str = None, epic_field: str = None) -> dict:
    f = (raw or {}).get("fields") or {}
    st = f.get("status") or {}
    a = f.get("assignee") or {}
    priority = f.get("priority") or {}
    return compact({
        "key": raw.get("key"),
        "type": (f.get("issuetype") or {}).get("name"),
        "summary": f.get("summary"),
        "status": st.get("name"),
        "done": ((st.get("statusCategory") or {}).get("key") or "").lower() == "done",
        "assignee": a.get("name"),
        "components": [c.get("name") for c in (f.get("components") or []) if c.get("name")],
        "labels": f.get("labels") or [],
        "priority": priority.get("name") if isinstance(priority, dict) else priority,
        "duedate": f.get("duedate"),
        "sp": f.get(sp_field) if sp_field else None,
        # Hierarchy is source data, not a lexical inference. QueryRunner follows these
        # bounded keys when a user references a child and delegates its Epic choice.
        "parentKey": ((f.get("parent") or {}).get("key") or None),
        "epicKey": (f.get(epic_field) if epic_field else None) or None,
        "created": (f.get("created") or "")[:10],
        "updated": (f.get("updated") or "")[:10],
    })


@tool
def search_work_history(query: str, limit: int = 8) -> dict:
    """Search Jira tickets and Confluence documents for prior work on a focused topic.

    Call this first when the user starts or investigates work, to determine whether it is new or
    already underway. Use a focused query of two to five terms. If it returns nothing, retry once
    with synonyms, then stop and move to a different evidence source such as `get_ticket` or
    `search_web`.

    Returns `{"jira": [{key,title,status,assignee,issuetype,updated}...],
    "confluence": [{title,url,excerpt}...]}`. It does not return ticket bodies or comments; open
    only selected tickets with `get_ticket`.
    """
    from app.domain.search import search_all
    c, s = client(), settings()
    lim = max(1, min(int(limit or 8), 20))

    def _hit(q):
        return search_all(c, s, q, scope="all", limit=lim)

    r = _hit(query)
    # 테이블·Job 이름이 들어온 질의는 **접미형(스키마 제거)으로 한 번 더** 본다 —
    # 같은 테이블을 어떤 코멘트는 `fdc_trace_summary_ic`, 어떤 티켓은 `fdc.fdc_...` 로 적는다.
    # 부분문자열 매칭이라 접미형이 상위집합이므로, 원 질의가 빈 경우 여기서 회복된다.
    if not ((r.get("jira") or {}).get("items")):
        from app.agent.tools._ident import find_identifiers, variants
        for ident in find_identifiers(query)[:1]:
            for v in variants(ident):
                if v == query:
                    continue
                r2 = _hit(v)
                if (r2.get("jira") or {}).get("items"):
                    r = r2
                    break
    # 원 질의가 비면 **완화 사다리**를 탄다 — 정의는 `_relaxed` 한 곳에만 둔다
    # (예전엔 이 도구 안에만 있어서 주제 조사 경로가 회복을 못 받았다).
    if not ((r.get("jira") or {}).get("items")):
        _items = _relaxed(query, lim)
        if _items:
            r = {"jira": {"items": _items}, "confluence": r.get("confluence") or {}}
    return {
        "query": query,
        "jira": [compact({k: it.get(k) for k in
                          ("key", "title", "status", "assignee", "issuetype", "epicKey", "updated")})
                 for it in (r.get("jira") or {}).get("items") or []],
        "confluence": [compact({"title": it.get("title"), "url": it.get("url"),
                                "excerpt": trim(it.get("excerpt") or it.get("snippet"), 200)})
                       for it in (r.get("confluence") or {}).get("items") or []],
    }


# 마지막 JQL은 대화 실행 context 안에서만 유지한다. 전역 dict는 동시에 실행된 두 대화의
# 근거 문장을 섞을 수 있다.
from contextvars import ContextVar
_last_jql = ContextVar("agent_last_jql", default="")


def take_last_jql() -> str:
    q = _last_jql.get()
    _last_jql.set("")
    return q


@tool
def run_jql(jql: str, limit: int = 20) -> dict:
    """Compatibility JQL tool. New roles should use `run_jql_v2`.

    The tool separates the input conditions from `ORDER BY` and executes them through the v2
    path. Scope is always the complete `search.jira.projects` set. There is no 50-item total cap:
    `limit` is the page size, capped at 100, and `nextCursor` retrieves the next page.
    """
    from app.agent.tools.query_tools import _jql_page, _split_order
    where, order = _split_order(jql)
    if not where and not order:
        return {"error": "JQL 이 비었습니다.", "tickets": []}
    try:
        result = _jql_page(where, order or "updated DESC", None, limit, "")
        result["jql"] = result.get("canonicalJql")
        result["count"] = result.get("returned", 0)
        _last_jql.set(result.get("canonicalJql") or "")
        return result
    except Exception as e:
        return {"error": f"JQL 실행 실패: {str(e)[:200]}", "tickets": []}



# UI 회귀 픽스처 티켓 — 개발 world 에만 있고 **사용자 답변에 나오면 안 된다.**
# 실측(추천 칩 CHIP5 "우리 모듈 최근 7일"): 답이 통째로 [UI] 픽스처 다섯 건이었다.
# 초안 쪽에서 픽스처 모듈을 막아 뒀는데 **조회 쪽에는 같은 가드가 없었다** — 가드는
# 만드는 자리와 읽는 자리 양쪽에 있어야 한다.
_FIXTURE_COMPONENT = "TEST"


def _drop_fixtures(raws):
    out = []
    for it in (raws or []):
        comps = [c.get("name") for c in ((it.get("fields") or {}).get("components") or [])]
        if _FIXTURE_COMPONENT in comps:
            continue
        out.append(it)
    return out

@tool
def get_ticket(key: str, comment_limit: int = 5) -> dict:
    """Open one ticket with its body and recent comments.

    Use a key selected from search results. Decisions and progress evidence often live in comments,
    and comment authors can support assignee analysis. Each call is a separate round trip, so open
    only the most relevant tickets, usually two to four.

    Returns summary, status, assignee, hierarchy (`parentKey`/`epicKey`), components,
    labels, priority, due date, SP, a shortened description, and recent comments.
    """
    if not jira_key_allowed(key):
        return {"error": "티켓이 search.jira.projects 범위 밖이거나 검색 범위가 비어 있습니다."}
    c = client()
    raw = c.get_issue(key) or {}
    if not raw.get("key"):          # 없는 키에 빈 껍데기가 돌아오기도 한다 — key 로 판정한다
        return {"error": f"{key} 티켓을 찾을 수 없습니다. 키를 다시 확인하세요."}
    out = _issue_brief(raw, getattr(settings(), "sp_field_id", None),
                       getattr(settings(), "epic_link_field_id", None))
    out["description"] = trim(((raw.get("fields") or {}).get("description")), 1200)
    # comments 는 **비어 있어도 싣는다** — "코멘트가 없다"와 "안 가져왔다"는 모델에게 다른 정보다.
    # ★ 코멘트 행의 본문은 `html`, 일시는 `date` 다(body/created 아님). 처음에 body 로 읽는 바람에
    #   모델이 **빈 코멘트**를 받고 있었다 — "코멘트까지 읽는다"던 ResearchAnalyst 이 작성자·날짜만
    #   보고 있던 셈이다. 테스트가 머리글만 확인해서 놓쳤다(약한 단언의 값).
    import re as _re
    try:
        rows = c.issue_comments(key, max(1, min(int(comment_limit or 5), 20))) or []
        out["comments"] = [
            compact({"author": cm.get("authorId") or cm.get("author"),
                     "created": (cm.get("date") or cm.get("created") or "")[:10],
                     "body": trim(_re.sub(r"<[^>]+>", " ", cm.get("html") or cm.get("body") or ""), 500)})
            for cm in rows]
    except Exception as e:                      # 코멘트가 막혀도 티켓 본문은 쓸모가 있다
        out["comments_error"] = str(e)[:200]
    return out


def _strip(html_or_text: str) -> str:
    """태그·하이라이트 마크를 벗긴 평문. `<mark>` 는 화면용이라 모델에겐 소음이다."""
    import re as _re
    return _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", str(html_or_text or ""))).strip()


def _snippet(hay: str, term: str, width: int = 120) -> str:
    """`term` 이 나온 자리 앞뒤를 잘라 인용문을 만든다. 없으면 빈 문자열."""
    text = _strip(hay)
    i = text.lower().find((term or "").lower())
    if i < 0:
        return ""
    a, b = max(0, i - width), min(len(text), i + len(term) + width)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


@tool
def find_mentions(term: str, limit: int = 8) -> dict:
    """Find exactly where a term appears, including ticket bodies and original comments.

    Use this for precise identifiers such as table names, Job names, or technical terms. Unlike
    title-only history search, it returns the sentence containing the term with author and date,
    which supports claims recorded only in comments.

    Call it once per term because it opens multiple ticket bodies and comments. Returns
    `{"hits": [{key,title,where(summary|description|comment),author,date,snippet}],
    "documents": [{title,url,excerpt}]}`. An empty `hits` array means no internal record was found;
    do not substitute facts from a merely similar identifier.
    """
    from concurrent.futures import ThreadPoolExecutor

    from app.agent.tools._ident import variants
    from app.domain.search import search_all
    c, s = client(), settings()
    term = (term or "").strip()
    if not term:
        return {"term": "", "hits": [], "documents": []}
    lim = max(1, min(int(limit or 8), 12))
    vs = variants(term) or [term]

    # 표기 변형별로 검색(접미형이 상위집합) — 키는 합집합, 문서는 먼저 나온 것.
    seen, docs = {}, []
    with ThreadPoolExecutor(max_workers=2) as ex:
        for r in ex.map(lambda v: search_all(c, s, v, scope="all", limit=lim + 4), vs):
            for it in (r.get("jira") or {}).get("items") or []:
                seen.setdefault(it.get("key"), it)
            for d in (r.get("confluence") or {}).get("items") or []:
                if d.get("title") and all(d["title"] != x["title"] for x in docs):
                    docs.append({"title": _strip(d.get("title")), "url": d.get("url") or "",
                                 "excerpt": trim(_strip(d.get("excerpt") or d.get("snippet")), 200)})

    # ★ **여기에도 완화 사다리를 태운다**(실사용 사고). `search_work_history` 에만 있어서,
    #   주제 조사 경로(`_topic_dossier` → 이 도구)는 여전히 전 토큰 AND 로만 찾고 있었다 —
    #   "iceberg 통계데이터 생성" 같은 여러 낱말 주제가 통째로 0건이 되고, 실존 티켓이
    #   있는데도 "사내 어디에서도 못 찾았다"로 답했다.
    #   **가드도 사다리도 '만드는 자리와 읽는 자리 양쪽'에 있어야 한다** — 이 저장소가
    #   반복해서 배운 것이고, 이번엔 회복 경로가 한쪽에만 있었다.
    if not seen and " " in term:
        for it in _relaxed(term, lim + 4):
            seen.setdefault(it.get("key"), it)

    keys = [k for k in list(seen)[:lim] if k]

    def _dig(key):
        """티켓 하나에서 term 이 적힌 자리를 찾는다 — 요약 → 본문 → 코멘트 순."""
        rows = []
        try:
            raw = c.get_issue(key) or {}
            f = raw.get("fields") or {}
            title = f.get("summary") or key
            for where, hay in (("summary", title), ("description", f.get("description"))):
                sn = _snippet(hay, term)
                if sn:
                    rows.append({"key": key, "title": title, "where": where, "snippet": sn})
                    break        # 요약에 있으면 본문까지 인용할 이유는 없다
            for cm in (c.issue_comments(key, 20) or []):
                sn = _snippet(cm.get("html") or cm.get("body"), term)
                if sn:
                    rows.append({"key": key, "title": title, "where": "comment",
                                 "author": cm.get("authorId") or cm.get("author"),
                                 "date": (cm.get("date") or "")[:10], "snippet": sn})
        except Exception:
            pass                 # 한 티켓이 막혀도 나머지 인용은 살린다
        return rows

    hits = []
    if keys:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for rows in ex.map(_dig, keys):
                hits.extend(rows)
    # 변형 표기로 찾아 term 자체는 안 보이는 티켓도 있다 — 제목만이라도 남긴다.
    for k in keys:
        if not any(h["key"] == k for h in hits):
            hits.append({"key": k, "title": (seen[k] or {}).get("title") or k,
                         "where": "summary", "snippet": ""})
    out = {"term": term, "variants": vs, "count": len(hits),
           "hits": [compact(h) for h in hits[:24]], "documents": docs[:5]}
    # ★ 정확 표기가 어디에도 없으면 **유사 식별자**를 찾아 준다 — 사용자는 오탈자
    #   (flat↔trace)나 다른 표기로 묻는다(실측: 있는 데이터를 '기록 없음'으로 답했다).
    if not hits and not docs:
        sim = _similar_identifiers(term, c, s)
        if sim:
            out["similar"] = sim
            out["note"] = ("정확한 표기로는 기록이 없다. similar 의 식별자가 사용자가 "
                           "말한 것일 가능성이 높다 — 그 식별자로 조사하고, 답변에서 "
                           "표기 차이를 짚어라.")
    return out


def _similar_identifiers(term: str, c, s) -> list:
    """오탈자·유사 표기 구조 — 토큰 겹침으로 실존 식별자를 추정한다.

    'fdc_flat_summary_ic'(없음) → 토큰 {fdc,flat,summary,ic} 중 특징적인 것으로 검색해
    제목들에서 식별자를 수확 → 겹침 점수 → 'fdc.fdc_trace_summary_ic'(3/4). 판정은
    호출자(모델)가 하되, 후보는 코드가 보장한다."""
    import re as _re2

    from app.agent.tools._ident import find_identifiers
    from app.domain.search import search_all
    toks = {t for t in _re2.split(r"[._\s]+", (term or "").lower()) if len(t) >= 2}
    if len(toks) < 3:
        return []
    probes = sorted(toks, key=len, reverse=True)[:2]
    cand: dict[str, int] = {}
    for p in probes:
        try:
            r = search_all(c, s, p, scope="all", limit=10)
        except Exception:
            continue
        blob = " ".join(str(it.get("title") or "")
                        for it in ((r.get("jira") or {}).get("items") or []))
        blob += " " + " ".join(str(d.get("title") or "")
                               for d in ((r.get("confluence") or {}).get("items") or []))
        for ident in find_identifiers(blob):
            cand.setdefault(ident, 0)
    for ident in list(cand):
        itoks = {t for t in _re2.split(r"[._]+", ident.lower()) if len(t) >= 2}
        cand[ident] = len(toks & itoks)
    need = max(2, len(toks) - 2)
    best = sorted(((k, v) for k, v in cand.items() if v >= need), key=lambda kv: -kv[1])
    return [{"term": k, "matched": v, "of": len(toks)} for k, v in best[:3]]


@tool
def read_document(url_or_id: str) -> dict:
    """Read the body of one Confluence document.

    Pass a document URL returned by `search_work_history`, `find_mentions`, or
    `get_ticket_context`; a numeric page id is also accepted. Search excerpts are too short for
    decisions usually recorded inside analysis, design, or policy documents.

    Returns `{"title","url","updated","text"}` with up to the first 3,000 body characters.
    """
    from app.agent.retrieval.harvest import _conf_id
    c = client()
    raw = str(url_or_id or "").strip()
    cid = _conf_id(raw) or (raw if raw.isdigit() else "")
    if not cid:
        return {"error": "문서 id 를 찾지 못했습니다. Confluence 페이지 URL 이나 페이지 id 를 넣으세요."}
    spaces = search_spaces()
    if not spaces:
        return {"error": "검색 범위 미설정 — search.confluence.spaces를 지정하세요"}
    data = c.confluence_page(cid, expand="body.storage,space,version")
    if not data:
        return {"error": f"문서 {cid} 를 읽지 못했습니다."}
    space = ((data.get("space") or {}).get("key") or "").upper()
    if space not in {x.upper() for x in spaces}:
        return {"error": "문서가 search.confluence.spaces 범위 밖입니다.",
                "space": space, "scopeSpaces": spaces}
    body = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
    return compact({"title": data.get("title") or "", "url": raw if raw.startswith("http") else "",
                    "updated": ((data.get("version") or {}).get("when") or "")[:10],
                    "text": trim(_strip(body), 3000)})


@tool
def get_ticket_context(key: str) -> dict:
    """Expand one hop from a ticket to related tickets, documents, and material history.

    Use this after selecting one central ticket. Keyword search can miss predecessors, follow-up
    work, and linked Confluence design documents. Open returned document URLs with
    `read_document` when their bodies are needed.

    Returns `{"related": [...], "documents": [{title,url}...], "timeline": [...]}`.
    """
    if not jira_key_allowed(key):
        return {"error": "티켓이 search.jira.projects 범위 밖이거나 검색 범위가 비어 있습니다."}
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
    """Return an Epic's full child tree: Story, Task, Bug, and their Sub-Tasks.

    Use this after identifying a possible parent Epic to decide where new work belongs and whether
    the proposed work duplicates an existing child.
    """
    if not jira_key_allowed(epic_key):
        return {"error": "Epic이 search.jira.projects 범위 밖이거나 검색 범위가 비어 있습니다."}
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
    """Find candidate parent Epics immediately before proposing a ticket tree.

    An empty query returns all Epics in recent order. A key, summary, or Epic Name can be searched.
    If no candidate fits, ask the user; attaching work to an unrelated Epic corrupts its progress.
    Create a new Epic only when all four criteria in `knowledge/04` are satisfied.

    Returns `[{"key","name","summary","module"}]`.
    """
    # 화면용 epic_options()는 write destination project를 전제로 하므로 조회에 쓰지 않는다.
    # Agent의 후보 탐색은 search config의 **모든** project를 바깥 scope로 강제한다.
    projects = search_projects()
    if not projects:
        return [{"error": "검색 범위 미설정 — search.jira.projects를 지정하세요"}]
    q = str(query or "").strip()
    cond = "issuetype = Epic"
    if q:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,9}-\d+", q):
            key = q.upper()
            if not jira_key_allowed(key):
                return []
            cond += f' AND key = "{key}"'
        else:
            safe = q.replace("\\", "\\\\").replace('"', '\\"')
            cond += f' AND summary ~ "{safe}"'
    jql = jira_scope(cond) + " ORDER BY updated DESC, key ASC"
    wanted = max(1, min(int(limit or 10), 25))
    c = client()
    epic_name_field = getattr(settings(), "epic_name_field_id", "") or ""
    fields = ["summary", "project", "issuetype", "components", "updated"]
    if epic_name_field:
        fields.append(epic_name_field)
    try:
        start, rows, snapshot_id = 0, [], None
        while len(rows) < wanted:
            page_kwargs = {"start_at": start, "max_results": 100,
                           "fields": fields, "light": True}
            if snapshot_id:
                page_kwargs["snapshot_id"] = snapshot_id
            page = c.search_issues_page(jql, **page_kwargs)
            snapshot_id = page.get("snapshotId") or snapshot_id
            rows.extend(page.get("issues") or [])
            if not page.get("hasMore") or page.get("nextStartAt") is None:
                break
            start = int(page["nextStartAt"])
    except Exception as e:
        return [{"error": str(e)[:200]}]
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("key"):
            continue
        if not jira_key_allowed(r["key"]):
            continue
        f = r.get("fields") or {}
        comps = [x.get("name") for x in (f.get("components") or []) if x.get("name")]
        mod = str(comps[0]) if comps else ""
        if mod == "TEST":
            continue          # 화면 검증용 픽스처 Epic — 실 업무를 달 자리가 아니다
        summary = f.get("summary") or r.get("summary")
        name = f.get(epic_name_field) if epic_name_field else None
        out.append(compact({"key": r["key"], "name": name or summary,
                            "summary": summary, "module": mod}))
        if len(out) >= wanted:
            break
    return out


def _epic_module(key: str) -> str:
    """그 Epic 의 모듈 — 티켓 컴포넌트와 맞는 Epic 을 고르는 근거가 된다."""
    try:
        f = (client().get_issue(key) or {}).get("fields") or {}
        comps = [c.get("name") for c in (f.get("components") or []) if c.get("name")]
        return str(comps[0]) if comps else ""
    except Exception:
        return ""


# ── 완화 사다리 — 전 토큰 AND 검색이 0건일 때의 회복 경로(**공용**) ──────────
# 검색은 전 토큰 AND 매칭이라 노이즈 단어 하나가 결과를 0으로 만든다. 실측: "UI 회귀 검증
# 픽스처 테스크" — '테스크'가 제목에 없어서 실존 티켓(DL-9000)을 못 찾고 "이력 없음"으로
# 답했다. 모델에게 검색어를 다시 쓰라고 시키는 대신 코드가
#   ① 일반어(테스크·티켓·업무…)를 떼고 재검색 ② 그래도 비면 토큰별로 찾아 매칭 수로 랭킹.
#
# ★ **정의는 한 곳에만 둔다.** 예전엔 `search_work_history` 안에만 있어서, 주제 조사 경로
#   (`_topic_dossier` → `find_mentions`)는 이 회복을 못 받았다 — 같은 질문이 어느 도구를
#   타느냐에 따라 찾히고 안 찾히고가 갈렸다. 실사용 사고가 정확히 그 경로에서 났다.
_STOP = {"테스크", "태스크", "티켓", "업무", "작업", "관련", "정리", "확인", "현황",
         "무슨", "무엇", "어떤", "하는", "해줘", "주세요", "요청", "진행", "내역", "총정리"}


def _relaxed(query: str, lim: int = 8) -> list:
    """완화 검색 결과(jira items 목록). 회복하지 못하면 빈 목록."""
    import re as _re2

    from app.domain.search import search_all
    c, s = client(), settings()

    def _hit(q):
        return (search_all(c, s, q, scope="all", limit=lim).get("jira") or {}).get("items") or []

    toks = [t.strip("[]()\"'") for t in (query or "").split()]
    toks = [t for t in toks if len(t) >= 2 and t not in _STOP]
    if not toks:
        return []
    if " ".join(toks) != (query or "").strip():
        got = _hit(" ".join(toks))
        if got:
            return got
    if len(toks) < 2:
        return []

    # 핵심 토큰 = 영문 기술 용어(Iceberg·Puffin·NDV·CDC…)와 데이터 자산 이름.
    core = [t for t in toks if _re2.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,}", t)]
    score, seen, core_hit, token_hit = {}, {}, {}, set()
    for t in toks:
        for it in _hit(t):
            k = it.get("key")
            if not k:
                continue
            token_hit.add(t)
            score[k] = score.get(k, 0) + 1
            seen[k] = it
            if t in core:
                core_hit[k] = True

    # 절반 이상(올림)이 맞아야 하고, 질의에 영문 기술 토큰이 있으면 그중 **최소 1개**는
    # 맞아야 한다 — 일반어만 겹친 티켓을 "관련 이력"으로 내밀던 실측 사고의 재발 방지.
    #
    # ★ 분모는 **우리 말뭉치에 실제로 있는 토큰**이다(실사용 사고). "iceberg 통계데이터 생성"
    #   으로 물었을 때 `Iceberg Puffin 통계적용 PoC` 를 못 찾았다 — 'iceberg' 는 맞았는데
    #   '통계데이터'·'생성' 이 한 건도 안 맞아 문턱에 걸렸다. **아무 티켓에도 없는 낱말은
    #   사용자가 우리와 다르게 부른 것**이지 관련성의 잣대가 아니다. 그것까지 분모에 넣으면
    #   자세히 물을수록 결과가 사라진다.
    useful = [t for t in toks if t in token_hit]
    need = max(1, (len(useful or toks) + 1) // 2)
    best = sorted(score, key=lambda k: -score[k])[:lim]
    return [seen[k] for k in best if score[k] >= need and (not core or core_hit.get(k))]
