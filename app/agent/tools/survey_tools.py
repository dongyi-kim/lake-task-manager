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
