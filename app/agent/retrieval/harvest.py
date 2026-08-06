"""agent/retrieval/harvest.py — 실시간 검색 → 본문 수확 → 증분 색인 → 의미 검색.

`dynamic_index` 가 "어떻게 넣나"라면 여기는 **"무엇을 넣나"**다.

## 한 티켓 = 한 문서

티켓과 그 코멘트를 **합쳐서 한 문서**로 본다. 코멘트를 따로 색인하면 "DL-118 코멘트 3번"만
검색돼서 무슨 티켓 이야기인지 모른 채 근거로 쓰이게 된다. 합쳐 두면 어느 조각이 걸리든
머리에 티켓 키와 제목이 붙어 있다(`chunk.py` 가 넣는다).

## 링크 2홉

검색 키워드는 **그 단어를 쓴 문서만** 찾는다. 그런데 실제 맥락은 링크 건너편에 있는 경우가
많다 — 티켓엔 "설계는 문서 참고"라고만 적혀 있고 정작 결정은 Confluence 에 있다. 그래서
1홉 결과 상위 몇 건에서 **연관 티켓·관련 문서로 한 번 더 뻗는다**.

무한정 뻗지 않는다. 2홉이면 대개 충분하고, 3홉부터는 프로젝트 절반이 딸려 온다.
"""

from __future__ import annotations

import re

from app.agent.retrieval import dynamic_index

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")

MAX_DOC_CHARS = 8000        # 한 문서에서 임베딩할 상한. 티켓 하나가 이보다 길면 뒤는 잡담이다


def _text(html_or_text: str) -> str:
    s = _TAG.sub(" ", str(html_or_text or ""))
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return _WS.sub(" ", s).strip()


def ticket_doc(client, key: str) -> dict | None:
    """티켓 + 코멘트 → 색인 문서 하나. `updated` 는 원본 값을 그대로 쓴다(장부 1차 판정)."""
    raw = client.get_issue(key) or {}
    if not raw.get("key"):
        return None
    f = raw.get("fields") or {}
    parts = [f.get("summary") or ""]
    meta = [(f.get("issuetype") or {}).get("name"), (f.get("status") or {}).get("name"),
            (f.get("assignee") or {}).get("name")]
    comps = [c.get("name") for c in (f.get("components") or []) if c.get("name")]
    parts.append(" · ".join(x for x in meta if x))
    if comps:
        parts.append("모듈: " + ", ".join(comps))
    if f.get("labels"):
        parts.append("라벨: " + ", ".join(f["labels"]))
    if f.get("description"):
        parts.append(_text(f["description"]))
    try:
        for c in client.issue_comments(key, 20) or []:
            who = c.get("authorId") or c.get("author") or ""
            parts.append(f"[코멘트 {who} {(c.get('created') or '')[:10]}] {_text(c.get('body'))}")
    except Exception:
        pass
    return {"doc_id": f"jira:{raw['key']}", "kind": "jira", "title": f.get("summary") or raw["key"],
            "url": "", "updated": f.get("updated") or "",
            "text": "\n".join(p for p in parts if p)[:MAX_DOC_CHARS]}


def confluence_doc(client, cid: str, title: str = "", url: str = "") -> dict | None:
    """Confluence 문서 본문. 검색 결과의 excerpt 만으로는 결정 내용을 알 수 없다."""
    if not cid:
        return None
    try:
        data = client._conf_get_json(f"/rest/api/content/{cid}", params={"expand": "body.storage"})
    except Exception:
        return None
    body = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
    text = _text(body)
    if not text:
        return None
    return {"doc_id": f"conf:{cid}", "kind": "confluence",
            "title": title or data.get("title") or f"문서 {cid}", "url": url,
            "updated": ((data.get("version") or {}).get("when") or ""),
            "text": (f"{title or data.get('title') or ''}\n{text}")[:MAX_DOC_CHARS]}


def harvest(client, settings, query: str, limit: int = 8, hops: int = 1,
            deep: int = 3) -> dict:
    """실시간 검색(신선도) → 본문 수확 → 증분 색인 → 의미 검색(보강).

    hops=0 이면 검색 결과만, 1 이면 상위 `deep` 건에서 링크를 타고 한 번 더 뻗는다.
    반환: {"live": {...}, "index": {indexed,skipped,chunks}, "semantic": [...]}
    """
    from app.domain.search import search_all

    live = search_all(client, settings, query, scope="all", limit=limit)
    jira_hits = (live.get("jira") or {}).get("items") or []
    conf_hits = (live.get("confluence") or {}).get("items") or []

    docs, seen = [], set()

    def add(d):
        if d and d["doc_id"] not in seen:
            seen.add(d["doc_id"])
            docs.append(d)

    for it in jira_hits:
        add(ticket_doc(client, it.get("key")))
    for it in conf_hits:
        add(confluence_doc(client, it.get("id"), _text(it.get("title")), it.get("url")))

    # ── 2홉: 상위 몇 건의 이웃까지. 여기서 "링크 건너편의 결정"이 들어온다.
    if hops >= 1:
        for it in jira_hits[:max(0, int(deep))]:
            key = it.get("key")
            try:
                for r in (client.ticket_related(key) or [])[:5]:
                    if r.get("key"):
                        add(ticket_doc(client, r["key"]))
            except Exception:
                pass
            try:
                for d in (client.ticket_documents(key) or [])[:5]:
                    cid = d.get("id") or _conf_id(d.get("url"))
                    add(confluence_doc(client, cid, d.get("title"), d.get("url")))
            except Exception:
                pass

    stat = dynamic_index.upsert(docs)
    return {"live": {"jira": jira_hits, "confluence": conf_hits},
            "index": stat,
            "semantic": dynamic_index.search(query, k=limit)}


_ID_RE = re.compile(r"(?:pageId=|/pages/)(\d+)")


def _conf_id(url: str) -> str:
    m = _ID_RE.search(str(url or ""))
    return m.group(1) if m else ""
