"""Tracking checkpoints are durable records, separate from evictable source cache."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class TrackingStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL,
                    created REAL NOT NULL, snapshot TEXT NOT NULL, checkpoint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (
                    key TEXT PRIMARY KEY, fetched REAL NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL);
            """)
            # The previous process cannot own a live worker after restart. Never pretend its
            # unfinished groups completed; retain every checkpoint and make resume explicit.
            for row in db.execute("SELECT id,snapshot FROM runs").fetchall():
                snapshot = json.loads(row[1])
                if snapshot.get("status") in ("queued", "running"):
                    snapshot["status"] = "paused"
                    snapshot["errors"] = [{"source": "runtime", "reason": "interrupted", "message": "수집이 중단되었습니다. 이어서 수집할 수 있습니다."}]
                    db.execute("UPDATE runs SET snapshot=? WHERE id=?", (encode(snapshot), row[0]))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def insert(self, snapshot, owner, scope):
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (snapshot["id"], owner, scope,
                       time.time(), encode(snapshot), encode({"queries": {}, "issues": {}, "sources": {}})))

    def read(self, run_id):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT owner,scope,snapshot,checkpoint FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row[0], row[1], json.loads(row[2]), json.loads(row[3])

    def save(self, run_id, snapshot, checkpoint):
        with self.lock, self.connect() as db:
            db.execute("UPDATE runs SET snapshot=?,checkpoint=? WHERE id=?", (encode(snapshot), encode(checkpoint), run_id))

    def recent(self, owner, scope, limit=20):
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT snapshot FROM runs WHERE owner=? AND scope=? ORDER BY created DESC LIMIT ?",
                              (owner, scope, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def source(self, key):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT fetched,expires,payload FROM sources WHERE key=?", (key,)).fetchone()
        if row is None or row[1] < time.time():
            return None
        return {"fetchedAt": row[0], "data": json.loads(row[2])}

    def put_source(self, key, data, ttl, fetched_at=None):
        fetched_at = time.time() if fetched_at is None else fetched_at
        with self.lock, self.connect() as db:
            db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?)", (key, fetched_at, fetched_at + ttl, encode(data)))

    def sources(self, keys):
        result = {}
        with self.lock, self.connect() as db:
            for offset in range(0, len(keys), 400):
                batch = keys[offset:offset + 400]
                for key, fetched, payload in db.execute("SELECT key,fetched,payload FROM sources WHERE expires>=? AND key IN (%s)" % ",".join("?" for _ in batch), [time.time(), *batch]):
                    result[key] = {"fetchedAt": fetched, "data": json.loads(payload)}
        return result

    def put_sources(self, records, ttl):
        now = time.time()
        values = []
        for record in records:
            key, data = record[:2]
            fetched = record[2] if len(record) > 2 else now
            values.append((key, fetched, fetched + ttl, encode(data)))
        with self.lock, self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO sources VALUES (?,?,?,?)", values)

    def purge_sources(self):
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM sources WHERE expires < ?", (time.time(),))
