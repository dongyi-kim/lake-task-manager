"""agent/retrieval/static_index.py — `knowledge/` 규칙 문서의 FAISS 색인 (RAG 1계층).

**빌드 명령을 기억하지 않아도 되게** 만들었다. 첫 사용 시 만들고, 문서가 바뀌면 다음 질의에서
자동으로 다시 만든다. "인덱스 새로 만드는 걸 깜빡해서 낡은 규칙으로 답했다"가 가장 흔한 사고고,
문서 4개짜리 색인은 다시 만드는 데 몇 초면 된다 — 캐시 무효화를 사람 손에 맡길 이유가 없다.

**서명에 임베딩 모델을 넣는다.** 이게 핵심이다. provider 를 fake→openai 로 바꾸면 벡터 공간이
통째로 다르다. 차원이 우연히 같으면 **에러도 안 나고 그냥 엉뚱한 걸 찾아온다** — 조용히 틀리는
쪽이 훨씬 나쁘다. 그래서 모델이 바뀌면 무조건 다시 만든다.

파일 내용 해시를 본다(mtime 아님). git checkout·브랜치 전환은 내용이 그대로여도 mtime 을
바꿔 놓기 때문에, mtime 만 보면 쓸데없이 다시 만든다.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from app.agent import config as _cfg
from app.agent.retrieval.chunk import split_markdown

_cached = {"sig": None, "store": None}      # 프로세스 내 재사용 — 매 질의마다 디스크에서 읽지 않는다


def knowledge_dir() -> Path:
    """번들된 규칙 문서. 배포 루트에 같은 이름 폴더가 있으면 **그쪽이 이긴다** —
    config 와 같은 관례다(현장마다 규칙이 다를 수 있고, 그때 소스를 고치게 하면 안 된다)."""
    from app.infra.settings import BASE_DIR, RESOURCE_DIR
    override = Path(BASE_DIR) / "knowledge"
    return override if override.is_dir() else Path(RESOURCE_DIR) / "knowledge"


def index_dir() -> Path:
    from app.infra.settings import CACHE_DIR
    return Path(CACHE_DIR) / "agent_index" / "static"


def _sources() -> list[Path]:
    d = knowledge_dir()
    return sorted(p for p in d.glob("*.md") if p.name.lower() != "readme.md") if d.is_dir() else []


def signature() -> str:
    """문서 내용 + **임베딩 모델 정체성**. 둘 중 하나만 바뀌어도 색인은 무효다."""
    h = hashlib.sha256()
    h.update(f"{_cfg.provider()}|{_cfg.embed_model()}|".encode())
    for p in _sources():
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def build(force: bool = False):
    """색인을 만든다(필요할 때만). 반환: FAISS store 또는 문서가 없으면 None."""
    from langchain_community.vectorstores import FAISS

    sig = signature()
    if not force and _cached["sig"] == sig and _cached["store"] is not None:
        return _cached["store"]

    d = index_dir()
    meta = d / "meta.json"
    if not force and meta.exists():
        try:
            if json.loads(meta.read_text(encoding="utf-8")).get("sig") == sig:
                store = FAISS.load_local(str(d), _cfg.get_embeddings(),
                                         allow_dangerous_deserialization=True)
                _cached.update(sig=sig, store=store)
                return store
        except Exception:
            pass        # 색인이 깨졌으면 조용히 다시 만든다 — 못 읽는 캐시는 없는 캐시다

    chunks = []
    for p in _sources():
        chunks.extend(split_markdown(p.read_text(encoding="utf-8"), source=p.name))
    if not chunks:
        return None

    store = FAISS.from_texts(
        [c["text"] for c in chunks],
        _cfg.get_embeddings(),
        metadatas=[{"source": c["source"], "title": c["title"], "anchor": c["anchor"]}
                   for c in chunks])

    if d.exists():
        shutil.rmtree(d, ignore_errors=True)    # 낡은 벡터가 섞이지 않게 통째로 갈아엎는다
    d.mkdir(parents=True, exist_ok=True)
    store.save_local(str(d))
    meta.write_text(json.dumps({"sig": sig, "chunks": len(chunks),
                                "provider": _cfg.provider(), "embedModel": _cfg.embed_model()},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    _cached.update(sig=sig, store=store)
    return store


def search(query: str, k: int = 4) -> list[dict]:
    """규칙 검색. [{"text","source","title","score"}] — score 는 거리(작을수록 가깝다)."""
    store = build()
    if store is None:
        return []
    hits = store.similarity_search_with_score(query, k=max(1, min(int(k or 4), 10)))
    return [{"text": doc.page_content,
             "source": (doc.metadata or {}).get("source", ""),
             "title": (doc.metadata or {}).get("title", ""),
             "score": round(float(dist), 4)} for doc, dist in hits]


def stats() -> dict:
    meta = index_dir() / "meta.json"
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        m = {}
    return {"documents": [p.name for p in _sources()],
            "indexed": m.get("chunks"), "fresh": m.get("sig") == signature(),
            "provider": m.get("provider"), "embedModel": m.get("embedModel")}


if __name__ == "__main__":          # 첫 질의를 빠르게 하고 싶을 때 미리 굽는다
    s = build(force=True)
    print(json.dumps(stats(), ensure_ascii=False, indent=2))
