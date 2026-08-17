"""agent/retrieval/dynamic_index.py — 티켓·문서의 증분 벡터 색인 (RAG 2계층).

## 신선도와 비용을 동시에 갖는 법

벡터 색인만으로 Jira 를 검색하면 **어제 만든 티켓을 못 찾는다**. 그렇다고 매번 전부 임베딩하면
비싸고 느리다. 그래서 순서를 이렇게 둔다.

  ① **1차 검색은 항상 실시간** — `search_all`(JQL/CQL)이 방금 만든 티켓도 잡아 온다.
     신선도는 여기서 100% 보장된다. 벡터는 신선도를 책임지지 않는다.
  ② 잡힌 것들을 **장부와 대조** — 안 바뀐 건 건너뛴다(대개 대부분).
  ③ 새것/바뀐 것만 임베딩해 **upsert**.
  ④ 그제서야 **벡터 검색** — 여기서 얻는 건 ①이 못 하는 것이다:
     키워드가 하나도 안 겹치지만 **의미가 같은** 과거 티켓("변경분 실시간 반영" ↔ "CDC").

즉 벡터는 키워드 검색을 대체하지 않고 **보강**한다. 이 구조라서 TTL 이 벡터에 걸리지 않는다 —
벡터는 오래돼도 무해하고(원본이 안 바뀌었으면 여전히 정확하다), 낡을 수 있는 건 결과 목록뿐이다.

## upsert 가 delete-then-add 인 이유

FAISS 는 제자리 갱신이 없다. 옛 조각을 지우지 않고 새 조각만 넣으면 **같은 티켓의 옛 버전이
계속 검색된다** — "이미 취소된 방침"을 근거로 답하는 일이 생긴다. 그래서 장부에 적어 둔
`chunk_ids` 로 먼저 지우고 넣는다.
"""

from __future__ import annotations

import json
import shutil
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from app.agent import config as _cfg
from app.agent.retrieval import manifest
from app.agent.retrieval.chunk import split_text

_cached = {"sig": None, "store": None}


def embed_sig() -> str:
    raw = json.dumps(_cfg.embedding_identity(chunking_version="text-v1"), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def index_dir() -> Path:
    from app.infra.settings import CACHE_DIR
    return Path(CACHE_DIR) / "agent_index" / "dynamic"


def _load():
    """디스크의 색인을 연다. 없으면 None(아직 아무것도 안 넣은 상태)."""
    from langchain_community.vectorstores import FAISS
    sig = embed_sig()
    if _cached["sig"] == sig and _cached["store"] is not None:
        return _cached["store"]

    d = index_dir()
    meta = d / "meta.json"
    if meta.exists():
        try:
            if json.loads(meta.read_text(encoding="utf-8")).get("embedSig") == sig:
                store = FAISS.load_local(str(d), _cfg.get_embeddings(),
                                         allow_dangerous_deserialization=True)
                _cached.update(sig=sig, store=store)
                return store
        except Exception:
            pass
        reset()        # 모델이 바뀌었거나 색인이 깨졌다 — 벡터도 장부도 통째로 버린다
    return None


def _save(store):
    d = index_dir()
    d.mkdir(parents=True, exist_ok=True)
    store.save_local(str(d))
    (d / "meta.json").write_text(json.dumps({"embedSig": embed_sig(),
                                              **_cfg.embedding_identity(chunking_version="text-v1"),
                                              "created_at": datetime.now(timezone.utc).isoformat()},
                                             ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    _cached.update(sig=embed_sig(), store=store)


def reset():
    """벡터와 장부를 함께 버린다. **따로 버리면 장부는 있는데 벡터가 없는 상태**가 되어,
    '이미 넣었다'고 건너뛰면서 아무것도 못 찾는 조용한 고장이 된다."""
    shutil.rmtree(index_dir(), ignore_errors=True)
    manifest.clear()
    _cached.update(sig=None, store=None)


def upsert(docs: list[dict]) -> dict:
    """문서들을 색인에 반영한다. **안 바뀐 건 건너뛴다.**

    docs: [{"doc_id","kind","title","url","updated","text"}]
    반환: {"indexed": n, "skipped": n, "chunks": n} — 건너뛴 수가 이 설계의 성적표다.
    """
    from langchain_community.vectorstores import FAISS

    sig = embed_sig()
    store = _load()
    fresh_texts, fresh_meta, fresh_ids, plan = [], [], [], []
    skipped = 0

    for d in docs or []:
        did, text = (d.get("doc_id") or "").strip(), (d.get("text") or "").strip()
        if not did or not text:
            continue
        chash = manifest.content_hash(text)
        if manifest.is_fresh(did, d.get("updated") or "", chash, sig):
            skipped += 1
            continue
        chunks = split_text(text, source=did, title=d.get("title") or did)
        ids = [f"{did}#{i}" for i in range(len(chunks))]
        for cid, ch in zip(ids, chunks):
            fresh_ids.append(cid)
            fresh_texts.append(ch["text"])
            fresh_meta.append({"doc_id": did, "kind": d.get("kind", ""),
                               "title": d.get("title", ""), "url": d.get("url", ""),
                               "updated": d.get("updated", "")})
        plan.append((did, d, chash, ids))

    if not fresh_texts:
        return {"indexed": 0, "skipped": skipped, "chunks": 0}

    # 옛 조각을 먼저 지운다 — 안 지우면 같은 문서의 옛 버전이 계속 검색된다.
    stale = [cid for did, *_ in plan for cid in manifest.old_chunk_ids(did)]
    if store is not None and stale:
        try:
            store.delete([c for c in stale if c in store.docstore._dict])
        except Exception:
            pass        # 지우기가 안 되면 중복이 남을 뿐, 새 내용은 어차피 들어간다

    emb = _cfg.get_embeddings()
    if store is None:
        store = FAISS.from_texts(fresh_texts, emb, metadatas=fresh_meta, ids=fresh_ids)
    else:
        store.add_texts(fresh_texts, metadatas=fresh_meta, ids=fresh_ids)
    _save(store)

    for did, d, chash, ids in plan:
        manifest.record(did, d.get("kind", ""), d.get("title", ""), d.get("url", ""),
                        d.get("updated") or "", chash, ids, sig)

    return {"indexed": len(plan), "skipped": skipped, "chunks": len(fresh_texts)}


def search(query: str, k: int = 6, kind: str = "") -> list[dict]:
    """의미 기반 검색. 키워드가 안 겹쳐도 **같은 이야기를 하는** 과거 기록을 찾는다."""
    store = _load()
    if store is None:
        return []
    n = max(1, min(int(k or 6), 20))
    hits = store.similarity_search_with_score(query, k=n * 3 if kind else n)
    out, seen = [], set()
    for doc, dist in hits:
        m = doc.metadata or {}
        if kind and m.get("kind") != kind:
            continue
        did = m.get("doc_id")
        if did in seen:             # 한 티켓의 여러 조각이 다 걸리면 목록이 그걸로 덮인다
            continue
        seen.add(did)
        out.append({"doc_id": did, "kind": m.get("kind"), "title": m.get("title"),
                    "url": m.get("url"), "updated": m.get("updated"),
                    "text": doc.page_content, "score": round(float(dist), 4)})
        if len(out) >= n:
            break
    return out


def stats() -> dict:
    s = dict(manifest.stats())
    store = _load()
    s["vectors"] = len(getattr(store, "docstore", None).__dict__.get("_dict", {})) if store else 0
    s["embedSig"] = embed_sig()
    return s
