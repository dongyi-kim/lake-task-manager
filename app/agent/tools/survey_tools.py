"""agent/tools/survey_tools.py — 본문을 읽기 **전에** 후보 지도를 만든다.

관련 티켓 탐색을 모델의 눈먼 검색에 맡기면 걸음(도구 호출)을 검색 반복에 태운다(실측).
그런데 한 티켓 주변의 신호는 전부 **결정적으로 긁을 수 있다**:

  계보(형제·조상·자식) · 이슈링크/본문·코멘트 언급(related) · 관련 문서 ·
  같은 라벨 · 같은 컴포넌트 최근 · 참여자(리포터·담당·코멘트·멘션)

그래서 순서를 바꾼다 — **코드가 신호를 전부 취합해 겹침으로 점수를 매긴 지도**를 주고,
모델은 "어느 후보를 열어 읽을지"만 고른다. 여러 신호에 동시에 걸린 티켓(예: 형제이면서
본문에도 언급됨)이 강한 후보다 — 그 판단조차 코드가 셈으로 해 준다.
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agent.tools._ctx import client, compact, settings

MAX_PER_SIGNAL = 12
MAX_OUT = 25


def _add(cand: dict, key: str, via: str, title: str = "", status: str = "", done=None):
    if not key:
        return
    row = cand.setdefault(key, {"key": key, "via": [], "title": "", "status": "", "done": None})
    if via not in row["via"]:
        row["via"].append(via)
    row["title"] = row["title"] or title
    row["status"] = row["status"] or status
    if row["done"] is None and done is not None:
        row["done"] = done


def neighborhood(seed_key: str) -> dict:
    """한 티켓 주변의 후보 지도(순수 코드 — LLM 없음). 도구와 사전 주입 양쪽이 쓴다."""
    c = client()
    seed = (seed_key or "").strip()
    raw = c.get_issue(seed) or {}
    if not raw.get("key"):
        return {"error": f"{seed} 티켓을 찾을 수 없습니다."}
    f = raw.get("fields") or {}
    cand: dict[str, dict] = {}
    docs: list[dict] = []

    # ── 계보 — 형제(같은 부모/Epic)·조상·자식
    try:
        for s in (c.ticket_siblings(seed) or [])[:MAX_PER_SIGNAL]:
            if not s.get("current"):
                _add(cand, s.get("key"), "형제", s.get("summary", ""),
                     done=(s.get("statusCategory") == "done"))
    except Exception:
        pass
    try:
        for a in (c.ticket_ancestors(seed) or []):
            _add(cand, a.get("key"), "조상", a.get("summary", ""), a.get("status", ""),
                 done=(a.get("statusCategory") == "done"))
    except Exception:
        pass
    try:
        for ch in (c.ticket_children(seed) or [])[:MAX_PER_SIGNAL]:
            _add(cand, ch.get("key"), "자식", ch.get("summary", ""),
                 done=(ch.get("statusCat") or ch.get("statusCategory")) == "done")
    except Exception:
        pass

    # ── 링크·언급 — 이슈링크 + 본문/코멘트에서 언급된 티켓
    try:
        for r in (c.ticket_related(seed) or [])[:MAX_PER_SIGNAL]:
            _add(cand, r.get("key"), f"링크({r.get('rel') or '관련'})", r.get("summary", ""))
    except Exception:
        pass

    # ── 관련 문서(설명·코멘트에서 언급된 Confluence)
    try:
        for d in (c.ticket_documents(seed) or [])[:8]:
            if d.get("title") or d.get("url"):
                docs.append(compact({"title": d.get("title"), "url": d.get("url")}))
    except Exception:
        pass

    # ── 같은 라벨 / 같은 컴포넌트 최근 — JQL 로 결정적으로
    s = settings()
    labels = [x for x in (f.get("labels") or []) if x and x != "mock"]
    comps = [x.get("name") for x in (f.get("components") or []) if x.get("name")]
    try:
        if labels:
            lab = ", ".join(f'"{x}"' for x in labels[:3])
            for it in c.search_issues(
                    f"project = {s.project_key} AND labels in ({lab}) ORDER BY updated DESC",
                    max_results=MAX_PER_SIGNAL):
                fld = it.get("fields") or {}
                _add(cand, it.get("key"), f"라벨({labels[0]})", fld.get("summary", ""),
                     done=((fld.get("status") or {}).get("statusCategory") or {}).get("key") == "done")
    except Exception:
        pass
    try:
        if comps:
            for it in c.search_issues(
                    f'project = {s.project_key} AND component = "{comps[0]}" '
                    "AND statusCategory != done ORDER BY updated DESC",
                    max_results=MAX_PER_SIGNAL):
                fld = it.get("fields") or {}
                _add(cand, it.get("key"), f"컴포넌트({comps[0]})", fld.get("summary", ""), done=False)
    except Exception:
        pass

    # ── 참여자(리포터·담당·코멘트 작성·멘션) — 사람 신호
    people = []
    try:
        from app.domain.search import _ticket_people
        people = list(_ticket_people(c, seed) or [])[:12]
    except Exception:
        pass

    cand.pop(seed, None)
    rows = sorted(cand.values(), key=lambda r: (-len(r["via"]), r.get("done") is True))[:MAX_OUT]
    return {"seed": seed, "summary": f.get("summary") or "",
            "candidates": [compact(r) for r in rows],
            "documents": docs, "participants": people,
            "note": "via 가 여러 개인 후보(여러 신호에 겹침)가 강한 후보다. "
                    "본문·코멘트는 get_ticket 으로 필요한 것만 열어라."}


@tool
def map_ticket_neighborhood(key: str) -> dict:
    """티켓 하나의 **주변 지도** — 본문을 읽기 전에 후보를 한눈에 모은다.

    계보(형제·조상·자식) · 이슈링크·본문/코멘트 언급 · 관련 문서 · 같은 라벨 · 같은 컴포넌트
    최근 · 참여자를 **한 번에** 긁어 겹침(via 수)으로 정렬해 준다. 검색을 여러 번 반복하는
    대신 이걸 먼저 불러라 — 훨씬 싸고 빠짐이 없다.

    돌려주는 것: {"candidates": [{key,title,via:[신호…],done}...], "documents": [...],
                 "participants": [uid...]}
    via 가 여러 개(예: 형제+링크)인 후보가 강한 후보다. 그 다음에 get_ticket 으로
    **필요한 것만** 열어 읽는다.
    """
    return neighborhood(key)


# ── 진척 조사 ──────────────────────────────────────────────────────
# "이 티켓 지금 어디까지 됐어?"의 답은 상태 필드에 없다(그건 'In Progress' 한 단어다).
# 실제 진척은 네 군데에 흩어져 있고, 넷 다 봐야 "무엇이 끝났고 무엇이 막혔는지"가 나온다:
#   ① 티켓 자체 변동(상태·담당·마감·우선순위)  ② 코멘트의 진행 보고
#   ③ 결과를 적는 유관 문서의 최근 수정        ④ 하위 Sub-Task 완료 / 막던 티켓의 해소
# 모델의 도구 순회에 맡기면 한두 갈래만 보고 답한다(실측) — 코드가 전부 모아 준다.
def progress_report(key: str, comment_limit: int = 10) -> dict:
    """한 티켓의 진척 재료를 전부 모은다(순수 코드 — LLM 없음)."""
    c = client()
    seed = (key or "").strip().upper()
    raw = c.get_issue(seed) or {}
    if not raw.get("key"):
        return {"error": f"{seed} 티켓을 찾을 수 없습니다."}
    f = raw.get("fields") or {}
    st = (f.get("status") or {})
    out = {
        "key": seed, "title": f.get("summary") or "",
        "status": st.get("name") or "",
        "done": ((st.get("statusCategory") or {}).get("key") == "done"),
        "assignee": ((f.get("assignee") or {}).get("name")
                     or (f.get("assignee") or {}).get("key") or ""),
        "due": f.get("duedate") or "", "updated": str(f.get("updated") or "")[:10],
    }

    # 다섯 갈래(변동·코멘트·하위·링크·문서)는 서로 독립이다 — **병렬로 모은다**.
    # mock 은 밀리초라 티가 안 나지만 prod Jira/Confluence 는 호출당 수백 ms~수 초라
    # 직렬 5갈래(+링크 내부 N회)가 통째로 대기 시간이 된다(사용자 지적: prod 는 느리다).
    from concurrent.futures import ThreadPoolExecutor

    def _changes():
        # ① 필드 변동 — 상태 전이·마감 연기·우선순위 상향은 그 자체가 진척 사건이다
        return [compact({"date": r.get("date"), "who": r.get("author"),
                         "field": r.get("field"),
                         "from": r.get("from"), "to": r.get("to")})
                for r in (c.ticket_field_history(seed) or [])][-8:]

    def _comments():
        # ② 코멘트 — 진행 보고의 1차 출처. 최근 것이 뒤에 오게 둔다(시간순 서술을 위해)
        from app.agent.tools.search_tools import _strip
        # 코멘트는 html 로 온다(본문 키가 body 가 아니다 — 실측). 사번을 함께 남긴다:
        # 이름만 남기면 "누가 무엇을 보고했나"를 답변에서 검증할 수 없다.
        rows = [compact({"date": str(m.get("date") or "")[:10],
                         "who": m.get("authorId") or m.get("author") or "",
                         "text": _strip(m.get("html") or "")[:400]})
                for m in (c.issue_comments(seed, comment_limit) or [])]
        rows.reverse()                     # 오래된 것부터 — 진척은 시간순 이야기다
        return rows

    def _children():
        # ④ 하위 Sub-Task — 몇 개 중 몇 개가 끝났는지가 진척의 뼈대다
        kids, kdone = [], 0
        for ch in (c.ticket_children(seed) or []):
            d = (ch.get("statusCat") or ch.get("statusCategory")) == "done"
            kdone += 1 if d else 0
            kids.append(compact({"key": ch.get("key"), "title": ch.get("summary"),
                                 "status": ch.get("status"), "done": d,
                                 "assignee": ch.get("assignee"),
                                 "updated": str(ch.get("updated") or "")[:10]}))
        return kids, kdone

    def _links():
        # ④-b 링크 티켓 — 특히 **막고 있던 것이 풀렸는지**가 진척을 좌우한다
        rels = [r for r in (c.ticket_related(seed) or [])[:8] if r.get("key")]

        def _one(r):
            ri = ((c.get_issue(r["key"]) or {}).get("fields") or {})
            rst = ri.get("status") or {}
            return compact({
                "key": r["key"], "rel": r.get("rel") or "관련", "title": r.get("summary"),
                "status": rst.get("name"),
                "done": ((rst.get("statusCategory") or {}).get("key") == "done"),
                "updated": str(ri.get("updated") or "")[:10]})
        if not rels:
            return []
        with ThreadPoolExecutor(max_workers=4) as ex2:
            return list(ex2.map(_one, rels))

    def _docs():
        # ③ 유관 문서 — **결과를 적는 문서의 최근 수정**이 곧 진척 근거다. 본문도 조금 싣는다
        from app.agent.retrieval.harvest import _conf_id
        from app.agent.tools._ctx import trim
        from app.agent.tools.search_tools import _strip
        docs = []
        for d in (c.ticket_documents(seed) or [])[:3]:
            row = {"title": d.get("title"), "url": d.get("url")}
            cid = _conf_id(d.get("url") or "")
            if cid:
                page = c.confluence_page(cid) or {}
                body = ((page.get("body") or {}).get("storage") or {}).get("value") or ""
                row["updated"] = ((page.get("version") or {}).get("when") or "")[:10]
                row["excerpt"] = trim(_strip(body), 900)
            docs.append(compact(row))
        return docs

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {k: ex.submit(f) for k, f in (
            ("changes", _changes), ("comments", _comments), ("children", _children),
            ("links", _links), ("documents", _docs))}

    def _get(k, default):
        try:
            return futs[k].result()
        except Exception:
            return default
    out["changes"] = _get("changes", [])
    out["comments"] = _get("comments", [])
    kids, kdone = _get("children", ([], 0))
    out["children"] = kids
    out["children_done"] = f"{kdone}/{len(kids)}" if kids else ""
    out["links"] = _get("links", [])
    out["documents"] = _get("documents", [])
    out["note"] = ("진척은 상태 한 단어가 아니라 이 네 갈래(필드 변동·코멘트·하위 티켓·"
                   "유관 문서)를 이어 붙인 이야기다. 문서의 '최종 수정'은 결과 기록 시점이다.")
    return compact(out)


@tool
def get_ticket_progress(ticket_key: str) -> dict:
    """티켓 **하나의 진척 상황**을 조사한다 — "DL-123 지금 어디까지 됐어?"에 쓴다.

    상태 필드만 보면 'In Progress' 한 단어뿐이라 답이 안 된다. 이 도구는 진척의 근거
    네 갈래를 한 번에 모아 준다:
      · changes  — 상태 전이·마감 연기·우선순위 변경 등 티켓 자체의 변동 이력
      · comments — 착수·중간 보고·블로커 같은 진행 보고 원문(작성자·날짜 포함)
      · children — 하위 Sub-Task 목록과 완료 개수(예: "2/3")
      · links    — 연결된 티켓(특히 이 일을 막던 Bug 가 풀렸는지)
      · documents— 결과를 기록하는 Confluence 문서의 **최종 수정일과 본문 발췌**

    Epic·모듈 전체의 진척률(%)은 get_progress 를 쓴다. 이 도구는 티켓 한 건 전용이다.
    """
    return progress_report(ticket_key)
