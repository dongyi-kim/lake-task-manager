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
    여기서 가른다. 키워드는 2~5개 단어로 좁혀서 넣는다("CDC 실시간 수집" 처럼).
    안 나오면 동의어로 바꿔 **한 번만** 다시 불러라 — 두 번 비면 사내에 없는 것이다.
    그때는 이 도구를 더 부르지 말고 다른 도구(get_ticket·search_web 등)로 넘어가라.

    돌려주는 것: {"jira": [{key,title,status,assignee,issuetype,updated}...],
                 "confluence": [{title,url,excerpt}...]}
    돌려주지 않는 것: 티켓 본문·코멘트. 그건 get_ticket 으로 따로 연다(비용이 다르다).
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
    # ── 완화 사다리: 검색은 전 토큰 AND 매칭이라 노이즈 단어 하나가 결과를 0으로 만든다.
    # 실측: "UI 회귀 검증 픽스처 테스크" — '테스크'가 제목에 없어서 실존 티켓(DL-9000)을
    # 못 찾고 "이력 없음"으로 답했다. 모델에게 검색어를 다시 쓰라고 시키는 대신 코드가
    # ① 일반어(테스크·티켓·업무…)를 떼고 재검색 ② 그래도 비면 토큰별로 찾아 매칭 수로
    # 랭킹한다. 원 질의가 잡히면 사다리는 안 탄다.
    _STOP = {"테스크", "태스크", "티켓", "업무", "작업", "관련", "정리", "확인", "현황",
             "무슨", "무엇", "어떤", "하는", "해줘", "주세요", "요청", "진행"}
    if not ((r.get("jira") or {}).get("items")):
        toks = [t.strip("[]()\"'") for t in query.split()]
        toks = [t for t in toks if len(t) >= 2 and t not in _STOP]
        if toks and " ".join(toks) != query:
            r = _hit(" ".join(toks))
        if not ((r.get("jira") or {}).get("items")) and len(toks) > 1:
            import re as _re2
            score, seen, core_hit = {}, {}, {}
            # 핵심 토큰 = 영문 기술 용어(Iceberg·Puffin·NDV·CDC…)와 **데이터 자산 이름**.
            # `_` 를 빼먹었던 탓에 `fdc.fdc_trace_summary_ic` 가 core 로 안 잡혔고, 그 결과
            # 아래 core_hit 가드가 테이블 질문에서만 무력화돼(core 가 비면 통과) 무관한
            # 티켓이 "관련 이력"으로 나갔다.
            core = [t for t in toks if _re2.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,}", t)]
            for t in toks:
                for it in (_hit(t).get("jira") or {}).get("items") or []:
                    k = it.get("key")
                    if k:
                        score[k] = score.get(k, 0) + 1
                        seen[k] = it
                        if t in core:
                            core_hit[k] = True
            best = sorted(score, key=lambda k: -score[k])[:lim]
            # 절반 이상(올림) 토큰이 맞아야 하고, 질의에 영문 기술 토큰이 있으면 그중
            # **최소 1개**는 맞아야 한다 — 'ETL·파이프라인' 같은 일반어만 겹친 티켓을
            # "관련 이력"으로 내밀던 실측 사고("Iceberg Puffin NDV" 질문에 '경계값 오류
            # 수정'이 관련이라고 나옴)의 재발 방지.
            need = max(1, (len(toks) + 1) // 2)
            r = {"jira": {"items": [seen[k] for k in best
                                    if score[k] >= need and (not core or core_hit.get(k))]},
                 "confluence": r.get("confluence") or {}}
    return {
        "query": query,
        "jira": [compact({k: it.get(k) for k in
                          ("key", "title", "status", "assignee", "issuetype", "epicKey", "updated")})
                 for it in (r.get("jira") or {}).get("items") or []],
        "confluence": [compact({"title": it.get("title"), "url": it.get("url"),
                                "excerpt": trim(it.get("excerpt") or it.get("snippet"), 200)})
                       for it in (r.get("confluence") or {}).get("items") or []],
    }


# 마지막으로 실행된 JQL — PMO 가 답변에 `JQL: ...` 한 줄을 **코드로** 붙이기 위한 기록.
# (모델에게 "표기하라"고 시켰지만 스키마 정리 단계에서 떨어뜨렸다 — 실측.)
# ★ thread-local 이 아니다: LangChain ToolNode 가 도구를 워커 스레드에서 돌려 기록이
#   유실됐다(실측 — 노드에서 읽으면 항상 빈 값). 단일 프로세스 앱이라 모듈 슬롯로 충분하고,
#   동시 대화 둘이 겹치면 JQL 한 줄이 섞일 수 있는 것은 수용한다(근거 줄 하나의 문제).
_last_jql = {"q": ""}


def take_last_jql() -> str:
    q = _last_jql.get("q", "")
    _last_jql["q"] = ""
    return q


@tool
def run_jql(jql: str, limit: int = 20) -> dict:
    """**JQL 을 직접 실행**한다 — 사용자가 조건을 조합해 티켓을 찾고 싶을 때
    ("우선순위 P1 이면서 담당자가 없는 진행중 티켓", "이번 달 마감인 Catalog 티켓").

    사용자의 자연어 조건을 네가 JQL 로 옮겨 넣어라. 참고:
      상태군 statusCategory in (new, indeterminate, done) / 담당 assignee = "skcc.x1042"
      / 미배정 assignee is EMPTY (단, 미배정은 find_unassigned_tickets 가 더 정확하다)
      / 컴포넌트 component = "ETL" / 기한 duedate <= "2026-08-31" / 라벨 labels = "PMO_VIT"
      / 갱신 updated >= -7d / 정렬 ORDER BY duedate ASC

    프로젝트는 자동으로 우리 프로젝트로 한정된다(다른 프로젝트 조회 불가).
    돌려주는 것: {"jql": 실행된 최종 JQL, "count", "tickets": [{key,title,status,assignee,duedate}]}
    """
    c, s = client(), settings()
    q = (jql or "").strip().rstrip(";")
    if not q:
        return {"error": "JQL 이 비었습니다."}
    # 프로젝트 한정은 **코드가** 보장한다 — 모델이 빼먹거나 다른 프로젝트를 적어도 우리
    # 프로젝트 밖은 조회되지 않는다(JQL 은 조회 전용이라 쓰기 위험은 없다).
    low = q.lower()
    if "project" not in low:
        head, sep, tail = q.partition("ORDER BY") if "ORDER BY" in q else q.partition("order by")
        cond = head.strip()
        q = f"project = {s.project_key}" + (f" AND ({cond})" if cond else "") + \
            ((" " + sep + tail) if sep else "")
    elif f"project = {s.project_key}".lower() not in low.replace('"', ""):
        return {"error": f"우리 프로젝트({s.project_key}) 밖은 조회할 수 없습니다."}
    cap = max(1, min(int(limit or 20), 50))
    try:
        raws = c.search_issues(q, max_results=cap)
        raws = _drop_fixtures(raws)
    except Exception as e:
        return {"error": f"JQL 실행 실패: {str(e)[:200]} — 문법을 고쳐 다시 시도하라.", "jql": q}
    rows = []
    for it in (raws or [])[:cap]:        # mock 이 max_results 를 무시해도 캡은 지킨다(실측)
        f = it.get("fields") or {}
        rows.append(compact({
            "key": it.get("key"), "title": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "assignee": (f.get("assignee") or {}).get("name"),
            "priority": (f.get("priority") or {}).get("name"),
            "duedate": f.get("duedate"),
        }))
    _last_jql["q"] = q
    return {"jql": q, "count": len(rows), "tickets": rows}



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
    # ★ 코멘트 행의 본문은 `html`, 일시는 `date` 다(body/created 아님). 처음에 body 로 읽는 바람에
    #   모델이 **빈 코멘트**를 받고 있었다 — "코멘트까지 읽는다"던 Historian 이 작성자·날짜만
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
    """어떤 **말이 실제로 어디에 적혀 있는지** 찾는다 — 티켓 본문과 **코멘트 원문까지**.

    테이블명·Job 이름·기술 용어처럼 **정확한 낱말**을 추적할 때 쓴다
    ("fdc.fdc_trace_summary_ic", "etl_yms_lot_yield_daily", "적재주기").
    search_work_history 는 제목만 돌려주므로 "왜 이 티켓이 걸렸는지"를 알 수 없다.
    이 도구는 **그 말이 나온 문장을 작성자·날짜와 함께** 돌려주므로, 코멘트에만 적힌 사실
    (주기 변경·담당 인수인계·다른 테이블과의 비교)을 근거로 인용할 수 있다.

    한 낱말당 **한 번만** 부른다(티켓 본문·코멘트를 여러 건 여는 비싼 조회다).
    돌려주는 것: {"hits": [{key,title,where(summary|description|comment),author,date,snippet}],
                 "documents": [{title,url,excerpt}]}
    아무 데도 안 나오면 hits 가 빈 배열이다 — 그때는 **기록이 없는 것**이고, 비슷한 다른
    테이블의 사실을 가져다 붙이면 안 된다.
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
    """Confluence 문서 **본문을 읽는다**. 검색 결과의 발췌(200자)로는 결정 내용을 알 수 없다.

    search_work_history·find_mentions·get_ticket_context 가 준 문서 URL 을 그대로 넣는다
    (페이지 id 숫자만 넣어도 된다). 분석·설계·정책 문서는 본문에 답이 있고 제목엔 없다.

    돌려주는 것: {"title","url","updated","text"} — 본문은 앞부분 3000자.
    """
    from app.agent.retrieval.harvest import _conf_id
    c = client()
    raw = str(url_or_id or "").strip()
    cid = _conf_id(raw) or (raw if raw.isdigit() else "")
    if not cid:
        return {"error": "문서 id 를 찾지 못했습니다. Confluence 페이지 URL 이나 페이지 id 를 넣으세요."}
    data = c.confluence_page(cid)
    if not data:
        return {"error": f"문서 {cid} 를 읽지 못했습니다."}
    body = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
    return compact({"title": data.get("title") or "", "url": raw if raw.startswith("http") else "",
                    "updated": ((data.get("version") or {}).get("when") or "")[:10],
                    "text": trim(_strip(body), 3000)})


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
    """새 티켓을 매달 **상위 Epic 후보**를 찾는다. 티켓 트리를 제안하기 직전에 부른다.

    빈 문자열로 부르면 Epic 전체를 최근순으로 준다. 키·요약·Epic Name 어느 쪽으로 쳐도 찾는다.
    마땅한 후보가 없으면 사용자에게 물어라 — 관련 없는 Epic 에 매달면 남의 진척률이 오염된다
    (새 Epic 을 만드는 것은 knowledge/04 의 네 조건을 다 만족할 때만이다).

    돌려주는 것: [{"key","name","summary","module"}]
    """
    # ★ 예전에는 `epic_candidates()` 를 썼는데 그 함수는 이름과 달리 **Epic 에 넣을 Task**
    #   후보를 준다(지금은 parent_task_candidates 로 이름을 바로잡았다). 그래서 이 도구가
    #   Task 를 Epic 이라며 돌려줬고, 초안의 Epic Link 가 매번 비었다(실측). 진짜 Epic 목록은
    #   `epic_options()` 다 — 화면의 'Epic 편집' 드롭다운이 쓰는 그것.
    try:
        rows = client().epic_options(query or "") or []
    except Exception as e:
        return [{"error": str(e)[:200]}]
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("key"):
            continue
        mod = _epic_module(r["key"])
        if mod == "TEST":
            continue          # 화면 검증용 픽스처 Epic — 실 업무를 달 자리가 아니다
        out.append(compact({"key": r["key"], "name": r.get("name"),
                            "summary": r.get("summary"), "module": mod}))
        if len(out) >= max(1, min(int(limit or 10), 25)):
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
