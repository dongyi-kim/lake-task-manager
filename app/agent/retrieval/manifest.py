"""agent/retrieval/manifest.py — 무엇을 언제 어떤 내용으로 색인했는지 적어 두는 장부.

**임베딩은 돈과 시간이다.** 같은 티켓을 질의할 때마다 다시 임베딩하면 데모는 되지만 실무에서는
못 쓴다. 그래서 문서 단위로 "이 버전은 이미 넣었다"를 기록하고, 바뀐 것만 다시 넣는다.

판정은 **2단**이다.

  1차 `updated` — Jira/Confluence 가 주는 수정 시각. 대부분 이걸로 끝난다. 싸다.
  2차 `content_hash` — updated 는 그대로인데 내용이 바뀌는 경우가 실제로 있다(코멘트가 붙어도
      티켓 updated 는 움직이지만, 우리는 티켓+코멘트를 **한 문서로 합쳐** 색인하므로 합친
      결과의 해시를 따로 본다). 1차를 통과해도 해시가 다르면 다시 넣는다.

`embed_sig`(provider|model)도 함께 적는다. 모델이 바뀌면 예전 벡터는 **차원이 같아도 의미가
다르다** — 에러 없이 조용히 틀리는 쪽이 훨씬 나쁘므로 전부 무효로 본다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS docs (
  doc_id       TEXT PRIMARY KEY,
  kind         TEXT,
  title        TEXT,
  url          TEXT,
  updated      TEXT,
  content_hash TEXT,
  chunk_ids    TEXT,
  embed_sig    TEXT,
  indexed_at   REAL
);
CREATE INDEX IF NOT EXISTS docs_kind ON docs(kind);
"""


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


def db_path() -> Path:
    from app.infra.settings import CACHE_DIR
    d = Path(CACHE_DIR) / "agent_index"
    d.mkdir(parents=True, exist_ok=True)
    return d / "manifest.sqlite3"


def _conn():
    # 연결은 호출마다 연다. FastAPI 는 스레드로 도는데 sqlite 연결을 스레드 간에 공유하면
    # 골치 아파지고, 이 장부는 쓰기가 드물어 연결 비용이 문제가 되지 않는다.
    c = sqlite3.connect(str(db_path()))
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    return c


def get(doc_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM docs WHERE doc_id=?", (doc_id,)).fetchone()
    return dict(r) if r else None


def is_fresh(doc_id: str, updated: str, chash: str, embed_sig: str) -> bool:
    """다시 임베딩할 필요가 **없는가**. 하나라도 어긋나면 False."""
    r = get(doc_id)
    if not r or r["embed_sig"] != embed_sig:
        return False
    if (r["updated"] or "") != (updated or ""):
        return False
    return (r["content_hash"] or "") == (chash or "")


def record(doc_id, kind, title, url, updated, chash, chunk_ids, embed_sig):
    with _conn() as c:
        c.execute("""INSERT INTO docs
                     (doc_id,kind,title,url,updated,content_hash,chunk_ids,embed_sig,indexed_at)
                     VALUES (?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(doc_id) DO UPDATE SET
                       kind=excluded.kind, title=excluded.title, url=excluded.url,
                       updated=excluded.updated, content_hash=excluded.content_hash,
                       chunk_ids=excluded.chunk_ids, embed_sig=excluded.embed_sig,
                       indexed_at=excluded.indexed_at""",
                  (doc_id, kind, title, url, updated, chash,
                   json.dumps(chunk_ids, ensure_ascii=False), embed_sig, time.time()))


def old_chunk_ids(doc_id: str) -> list[str]:
    """갱신 전에 지워야 할 벡터 id. 안 지우면 같은 문서의 옛 버전이 계속 검색된다."""
    r = get(doc_id)
    try:
        return json.loads(r["chunk_ids"]) if r and r["chunk_ids"] else []
    except Exception:
        return []


def forget(doc_ids: list[str]):
    with _conn() as c:
        c.executemany("DELETE FROM docs WHERE doc_id=?", [(d,) for d in doc_ids])


def stats() -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        by_kind = {r[0]: r[1] for r in
                   c.execute("SELECT kind, COUNT(*) FROM docs GROUP BY kind").fetchall()}
        newest = c.execute("SELECT MAX(indexed_at) FROM docs").fetchone()[0]
    return {"documents": total, "byKind": by_kind, "lastIndexedAt": newest}


def clear():
    with _conn() as c:
        c.execute("DELETE FROM docs")
