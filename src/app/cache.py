"""
SQLite 캐시 (검토 #7) — 느린 Jira API 를 가려주는 TTL 캐시 + 진척 스냅샷.
stdlib sqlite3 만 사용(무의존).

- cache    : 요청 시그니처 -> Jira 응답 JSON (TTL 만료 시 재호출)
- snapshot : (entity, ref) 시계열 metric — 기능2 '최근 진척 히스토리', 기능3 '최근7일' 뒷받침
"""

import json
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    ttl        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshot (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    entity   TEXT NOT NULL,
    ref      TEXT NOT NULL,
    metric   TEXT NOT NULL,
    taken_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshot_ref ON snapshot(entity, ref, taken_at);
"""


class Cache:
    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    # ── TTL 캐시 ──
    def get(self, key):
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, fetched_at, ttl FROM cache WHERE key=?", (key,)
            ).fetchone()
        if not row:
            return None
        payload, fetched_at, ttl = row
        if time.time() - fetched_at > ttl:
            return None                      # 만료
        return json.loads(payload)

    def set(self, key, value, ttl):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache(key, payload, fetched_at, ttl) VALUES (?,?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), time.time(), int(ttl)),
            )
            self._conn.commit()

    def get_or_set(self, key, ttl, producer):
        """캐시 히트면 반환, 아니면 producer() 실행 후 저장."""
        hit = self.get(key)
        if hit is not None:
            return hit, True
        value = producer()
        self.set(key, value, ttl)
        return value, False

    def invalidate(self, prefix=None):
        with self._lock:
            if prefix:
                self._conn.execute("DELETE FROM cache WHERE key LIKE ?", (prefix + "%",))
            else:
                self._conn.execute("DELETE FROM cache")
            self._conn.commit()

    # ── 스냅샷(시계열) ──
    def add_snapshot(self, entity, ref, metric):
        with self._lock:
            self._conn.execute(
                "INSERT INTO snapshot(entity, ref, metric, taken_at) VALUES (?,?,?,?)",
                (entity, ref, json.dumps(metric, ensure_ascii=False), time.time()),
            )
            self._conn.commit()

    def recent_snapshots(self, entity, ref, limit=30):
        with self._lock:
            rows = self._conn.execute(
                "SELECT metric, taken_at FROM snapshot WHERE entity=? AND ref=? "
                "ORDER BY taken_at DESC LIMIT ?",
                (entity, ref, limit),
            ).fetchall()
        return [{"metric": json.loads(m), "takenAt": t} for m, t in rows]
