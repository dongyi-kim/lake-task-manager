"""
SQLite 캐시 (검토 #7) — 느린 Jira API 를 가려주는 TTL 캐시 + 진척 스냅샷.
stdlib sqlite3 만 사용(무의존).

- cache    : 요청 시그니처 -> Jira 응답 JSON. **TTL 이 두 단계**다:
             · outdated(ttl)      — 이 시각이 지나면 '낡음'. 온라인이면 다시 받아 온다.
             · dead(dead_ttl)     — 이 시각이 지나야 '없는 것'. 그 전까지는 오프라인·미인증
                                    상태에서도 낡은 값을 **그대로 준다**.
             한 단계뿐이면 만료 == 소멸이라, 망이 끊기거나 SSO 가 만료된 순간 화면이 통째로
             빈다. 사무실 밖·VPN 밖에서도 최소한 어제 본 것은 보여야 한다.
- snapshot : (entity, ref) 시계열 metric — 기능2 '최근 진척 히스토리', 기능3 '최근7일' 뒷받침
"""

import json
import sqlite3
import threading
import time
from concurrent.futures import Future
from app.infra.cache_policy import (LONG_DISPLAY_KINDS, DISPLAY_KINDS, SWR_AGGREGATE_KINDS,
                                   is_done, note_read, primary_sources, read_scope, revalidating)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    ttl        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_display (
    key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at REAL NOT NULL, ttl INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshot (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    entity   TEXT NOT NULL,
    ref      TEXT NOT NULL,
    metric   TEXT NOT NULL,
    taken_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshot_ref ON snapshot(entity, ref, taken_at);
CREATE TABLE IF NOT EXISTS recent (
    url       TEXT PRIMARY KEY,      -- 같은 대상은 한 줄만(다시 열면 시각만 갱신)
    kind      TEXT NOT NULL,         -- jira | confluence | web
    title     TEXT NOT NULL,
    meta      TEXT NOT NULL DEFAULT '',
    type      TEXT NOT NULL DEFAULT '',   -- 이슈타입(Task/Bug/…) — 목록을 검색 결과와 같은 모양으로
    data      TEXT NOT NULL DEFAULT '',   -- 표시용 JSON(key·summary·epicKey/Name·assignee·status·…) — 검색결과와 동일 포맷
    opened_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_recent_at ON recent(opened_at DESC);
CREATE TABLE IF NOT EXISTS person_recent (
    scope TEXT NOT NULL, user_id TEXT NOT NULL, kind TEXT NOT NULL, seen_at REAL NOT NULL,
    PRIMARY KEY(scope, user_id, kind)
);
CREATE INDEX IF NOT EXISTS ix_person_recent_at ON person_recent(scope, seen_at DESC);
-- Read watermarks are local UI state, not expiring Jira data or clearable search history.
CREATE TABLE IF NOT EXISTS ticket_read_state (
    scope TEXT NOT NULL, issue_key TEXT NOT NULL, viewed_through INTEGER NOT NULL,
    PRIMARY KEY(scope, issue_key)
);
CREATE TABLE IF NOT EXISTS cache_epoch (
    namespace TEXT PRIMARY KEY,
    value     INTEGER NOT NULL
);
-- Jira 쓰기는 서버가 반영한 뒤 응답만 유실될 수 있다. 일반 조회 캐시를 새로고침해도
-- 재제출 중복 방지용 receipt까지 지우면 안 되므로 별도 수명주기 테이블에 둔다.
CREATE TABLE IF NOT EXISTS mutation_receipt (
    key          TEXT PRIMARY KEY,
    fingerprint  TEXT NOT NULL,
    state        TEXT NOT NULL,
    result       TEXT NOT NULL DEFAULT '',
    context      TEXT NOT NULL DEFAULT '',
    attempted_at REAL NOT NULL,
    expires_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mutation_receipt_expiry ON mutation_receipt(expires_at);
"""


DEAD_TTL_DEFAULT = 24 * 3600      # 이만큼 지나면 진짜 없는 값으로 본다


class Cache:
    # 정리(purge) 정책 — 디스크 무한증가 방지. 캐시 행은 dead_ttl 을 넘으면 오프라인 폴백에도
    # 못 쓰므로(get_alive 가 dead_ttl 로 거른다) 지워도 안전하다. 스냅샷(시계열)은 최근 흐름만 쓰니
    # 이 기간만 남긴다. set() 이 일정 횟수마다 한 번씩 자동 정리하고, 시작 시에도 한 번 돈다.
    SNAPSHOT_KEEP_DAYS = 120
    PURGE_EVERY = 400            # set() 이 이만큼 쌓일 때마다 한 번 정리(비용 amortize)

    def __init__(self, db_path, dead_ttl=DEAD_TTL_DEFAULT):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._lock = threading.RLock()
        self.policy = None
        self.policy_revision = 0
        self._flights = {}
        self.dead_ttl = int(dead_ttl)
        self._set_count = 0
        # In-flight producers capture this fence. Any invalidation makes their pre-write result
        # ineligible for cache storage, preventing a stale background refresh from resurrecting
        # data immediately after a successful Jira write.
        self._write_fence = 0
        # 상류(Jira)가 지금 못 쓰는 상태면 True 를 주는 콜러블. 있으면 producer 를 **아예 호출하지
        # 않고** 낡은 값으로 답한다 — 죽은 세션에 매번 붙어 보느라 화면이 멎는 걸 막는다
        # (prod 의 Playwright 호출은 실패 판정까지 최대 180초가 걸린다).
        self.skip_producer = None
        # 상류에서 **실제로 받아 왔을 때** 부를 콜백(있으면). 클라이언트가 이걸로
        # 회로차단기와 '세션 죽음' 표시를 푼다 — 성공을 아는 곳이 여기뿐이다.
        self.on_upstream_ok = None
        # ── 편집에 예민한 데이터 — **캐시가 신선해도 매번 다시 받는다** ──────────────
        # 티켓 본문·코멘트·첨부·링크는 사람이 방금 고쳤을 수 있는 것들이다. TTL 이 15분이면
        # 내가 쓴 댓글이 15분 동안 안 보일 수 있는데, 그건 캐시가 아니라 버그로 보인다.
        # 그렇다고 캐시를 끄면 열 때마다 빈 화면을 보고 기다려야 한다 →
        # **캐시로 즉시 그리고, 매번 뒤에서 갱신한다**(다음 조회부터 최신).
        # 접두사로 두는 이유: 키를 만드는 곳이 열 군데 넘는데 각자 판단하게 하면 반드시 갈린다.
        self.always_revalidate = ()
        # 재검증을 실제로 돌릴 스케줄러(key, ttl, producer). 없으면 그냥 캐시만 준다.
        self.revalidator = None
        # 마지막으로 상류에서 실제로 받아 온 시각 / 낡은 값을 내준 적이 있는지 —
        # 화면 상단 알림이 "언제 기준 데이터인지" 를 말하려면 이 두 개가 필요하다.
        self.last_upstream_ok = 0.0
        self.served_stale_at = 0.0

    def _migrate(self):
        """CREATE TABLE IF NOT EXISTS 는 **기존 DB에 새 컬럼을 더해 주지 않는다** —
        이미 쓰던 사람의 캐시에도 컬럼을 채워 넣는다(없으면 조회가 통째로 깨진다)."""
        try:
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(recent)")}
            if cols and "type" not in cols:
                self._conn.execute("ALTER TABLE recent ADD COLUMN type TEXT NOT NULL DEFAULT ''")
            if cols and "data" not in cols:
                self._conn.execute("ALTER TABLE recent ADD COLUMN data TEXT NOT NULL DEFAULT ''")
            receipt_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(mutation_receipt)")
            }
            if receipt_cols and "context" not in receipt_cols:
                self._conn.execute(
                    "ALTER TABLE mutation_receipt ADD COLUMN context TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        except Exception:
            pass

    # ── TTL 캐시 ──
    def configure(self, policy, revision=0):
        with self._lock:
            self.policy, self.policy_revision = policy, revision
            self.dead_ttl = policy.displaySeconds

    def _ttl(self, key, value, original):
        if self.policy is None:
            return original
        done = False
        if key.startswith("comments:"):
            suffix = ":".join(key.split(":", 3)[1:3])  # page/projection suffixes still belong to one issue
            row = self._conn.execute(
                "SELECT payload,fetched_at FROM cache WHERE key IN (?,?,?) ORDER BY fetched_at DESC LIMIT 1",
                tuple(kind + ":" + suffix for kind in ("issue", "issueL", "issueview")),
            ).fetchone()
            done = bool(row and time.time() - row[1] <= self.dead_ttl and is_done(json.loads(row[0])))
        return self.policy.ttl(key, value, original, done)

    def display_ttl(self, key):
        if self.policy and key.split(":", 1)[0] in LONG_DISPLAY_KINDS:
            return self.policy.metadataDisplaySeconds
        return self.dead_ttl

    def metadata(self, key):
        with self._lock:
            row = self._conn.execute("SELECT payload,fetched_at,ttl FROM cache WHERE key=?", (key,)).fetchone()
            invalidated = not row
            if not row:
                row = self._conn.execute("SELECT payload,fetched_at,ttl FROM cache_display WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            value, fetched, ttl = json.loads(row[0]), row[1], row[2]
            fresh = 0 if invalidated else fetched + min(self._ttl(key, value, ttl), self.display_ttl(key))
            display = fetched + self.display_ttl(key)
            return {"kind": key.split(":", 1)[0], "fetchedAt": fetched, "freshUntil": fresh, "displayUntil": display,
                    "invalidated": invalidated,
                    "state": "dead" if time.time() > display else "fresh" if not invalidated and time.time() <= fresh else "stale"}

    def get(self, key):
        with self._lock:
            row = self._conn.execute("SELECT payload,fetched_at,ttl FROM cache WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            value = json.loads(row[0])
            if time.time() - row[1] > min(self._ttl(key, value, row[2]), self.display_ttl(key)):
                return None
            note_read(self.metadata(key))
            return value

    def refresh(self, key, ttl, producer, *, min_fetched_at=None):
        """Share foreground/background producers. Only complete successes advance source age."""
        with self._lock:
            fence = self._write_fence
            flight_key = (key, fence)
            future = self._flights.get(flight_key)
            owner = future is None
            if owner:
                # A producer may have finished between the caller's miss and this lock.
                source = self.metadata(key) if min_fetched_at is not None else None
                fresh = (self.get(key) if min_fetched_at is None or
                         (source and source["fetchedAt"] >= min_fetched_at) else None)
                if fresh is not None:
                    return fresh
                future = self._flights[flight_key] = Future()
        if not owner:
            value = future.result()
            note_read(self.metadata(key))
            return value
        try:
            with read_scope(strict=True) as sources:
                value = producer()
            # Core assemblies retain their source age. A separately retained Epic label or photo
            # must not turn today's complete ticket/list into ten-day-old, already-dead data.
            primary = sources if key.split(":", 1)[0] in LONG_DISPLAY_KINDS else primary_sources(sources)
            fetched = min((r["fetchedAt"] for r in primary), default=time.time())
            complete = value is not None and not (isinstance(value, dict) and
                       (value.get("error") or value.get("errors") or value.get("partial")))
            if key.split(":", 1)[0] in {"issue", "issueL", "issueview"} and self.policy:
                complete = complete and isinstance(value, dict) and isinstance(value.get("fields"), dict)
            if complete:
                self.set_if_fence(key, value, ttl, fence, fetched_at=fetched)
            if complete:
                self.last_upstream_ok = time.time()
                if self.on_upstream_ok:
                    try:
                        self.on_upstream_ok()
                    except Exception:
                        pass
            future.set_result(value)
            note_read(self.metadata(key))
            return value
        except BaseException as exc:
            from app.auth.base import PermissionDenied
            direct_resource = key.split(":", 1)[0] in {
                "issue", "issueL", "issueview", "epicmeta", "user", "userbadge", "avatar_url", "avatar_bytes"}
            # An Agile/list endpoint denial is not proof that each previously visible ticket
            # became inaccessible. Only a denial of the resource itself removes its display copy.
            if direct_resource and (isinstance(exc, (PermissionDenied, PermissionError))
                                    or getattr(exc, "status", None) in (403, 404)):
                with self._lock:
                    if self._write_fence == fence:
                        self.forget(key)
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                if self._flights.get(flight_key) is future:
                    self._flights.pop(flight_key, None)

    def set(self, key, value, ttl):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache(key, payload, fetched_at, ttl) VALUES (?,?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), time.time(), int(ttl)),
            )
            self._conn.execute("DELETE FROM cache_display WHERE key=?", (key,))
            # 주기적 자동 정리 — 오래 켜 두는 세션에서도 디스크가 계속 늘지 않게(비용은 amortize).
            self._set_count += 1
            if self._set_count >= self.PURGE_EVERY:
                self._set_count = 0
                self._purge_within_lock()
            self._conn.commit()

    def fence(self):
        with self._lock:
            return self._write_fence

    def forget_if_fence(self, key, fence):
        """Forget a denied resource only if no newer invalidation superseded its request."""
        with self._lock:
            if self._write_fence != fence:
                return False
            self._write_fence += 1
            self._conn.execute("DELETE FROM cache WHERE key=?", (key,))
            # Display-cache schema is optional on independently deployable older clients.
            if self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cache_display'").fetchone():
                self._conn.execute("DELETE FROM cache_display WHERE key=?", (key,))
            self._conn.commit()
            return True

    def set_if_fence(self, key, value, ttl, fence, *, fetched_at=None):
        """Store only when no invalidation happened after the producer started."""
        with self._lock:
            if self._write_fence != fence:
                return False
            self._conn.execute(
                "INSERT OR REPLACE INTO cache(key, payload, fetched_at, ttl) VALUES (?,?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), (time.time() if fetched_at is None else fetched_at), int(ttl)),
            )
            self._conn.execute("DELETE FROM cache_display WHERE key=?", (key,))
            self._set_count += 1
            if self._set_count >= self.PURGE_EVERY:
                self._set_count = 0
                self._purge_within_lock()
            self._conn.commit()
            return True

    def set_many_if_fence(self, values, ttl, fence):
        """Store a group atomically when the producer's generation fence is still current.

        JQL searches write through tens or hundreds of issues.  Committing each row separately
        turns local cache persistence into a visible UI delay, so the normalized query layer uses
        one SQLite transaction while keeping the same mutation race fence.
        """
        rows = [(str(key), json.dumps(value, ensure_ascii=False), time.time(), int(ttl))
                for key, value in values]
        if not rows:
            return True
        with self._lock:
            if self._write_fence != fence:
                return False
            self._conn.executemany(
                "INSERT OR REPLACE INTO cache(key, payload, fetched_at, ttl) VALUES (?,?,?,?)",
                rows,
            )
            self._conn.executemany("DELETE FROM cache_display WHERE key=?", [(r[0],) for r in rows])
            self._set_count += len(rows)
            if self._set_count >= self.PURGE_EVERY:
                self._set_count = 0
                self._purge_within_lock()
            self._conn.commit()
            return True

    def get_many(self, keys, *, alive=False):
        """Return fresh cache values for keys with a bounded number of SQLite round trips."""
        ordered = list(dict.fromkeys(str(key) for key in keys if key))
        if not ordered:
            return {}
        now, found = time.time(), {}
        with self._lock:
            for offset in range(0, len(ordered), 400):
                batch = ordered[offset:offset + 400]
                marks = ",".join("?" for _ in batch)
                rows = self._conn.execute(
                    f"SELECT key,payload,fetched_at,ttl FROM cache WHERE key IN ({marks})",
                    batch,
                ).fetchall()
                for key, payload, fetched_at, ttl in rows:
                    if now - fetched_at <= (self.display_ttl(key) if alive else min(self._ttl(key, json.loads(payload), ttl), self.display_ttl(key))):
                        try:
                            found[key] = json.loads(payload)
                            note_read(self.metadata(key))
                        except Exception:
                            pass
        if alive:
            for key in ordered:
                if key not in found:
                    value = self.get_alive(key)
                    if value:
                        found[key] = value[0]
        return found

    def entries_by_prefix(self, prefix):
        """Return fresh cache entries below an exact key prefix.

        JQL reverse indexes use one small row per issue/field-to-leaf edge.  Looking those rows up
        by prefix avoids scanning or decoding every cached leaf when one ticket changes.
        """
        prefix = str(prefix or "")
        if not prefix:
            return {}
        now, found = time.time(), {}
        with self._lock:
            rows = self._conn.execute(
                "SELECT key,payload,fetched_at,ttl FROM cache "
                "WHERE key>=? AND key<?", (prefix, prefix + "\uffff")
            ).fetchall()
        for key, payload, fetched_at, ttl in rows:
            if now - fetched_at <= min(self._ttl(key, json.loads(payload), ttl), self.display_ttl(key)):
                try:
                    found[key] = json.loads(payload)
                except Exception:
                    pass
        return found

    def epoch(self, namespace):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM cache_epoch WHERE namespace=?", (namespace,)
            ).fetchone()
        return int(row[0]) if row else 0

    def bump_epoch(self, namespace):
        """Atomically advance a persistent logical cache generation."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO cache_epoch(namespace,value) VALUES (?,1) "
                "ON CONFLICT(namespace) DO UPDATE SET value=value+1", (namespace,)
            )
            row = self._conn.execute(
                "SELECT value FROM cache_epoch WHERE namespace=?", (namespace,)
            ).fetchone()
            self._write_fence += 1
            self._conn.commit()
        return int(row[0])

    def _purge_within_lock(self):
        """죽은 캐시 행(dead_ttl 초과) + 오래된 스냅샷 삭제. **이미 _lock 을 쥔 채** 호출한다.
        (SQLite 는 DELETE 로 파일이 줄진 않지만 빈 페이지를 재사용하므로 크기가 안정된다.)"""
        now = time.time()
        try:
            # Immutable JQL snapshots carry complete rows. Keep only their cursor lifetime,
            # rather than retaining every historical query's payload for the display horizon.
            self._conn.execute("DELETE FROM cache WHERE key LIKE 'jqlsnap:%' AND ? - fetched_at > ttl", (now,))
            if self.policy:
                for table in ("cache", "cache_display"):
                    self._conn.execute(f"DELETE FROM {table} WHERE ? - fetched_at > CASE WHEN "
                                   "substr(key,1,instr(key,':')-1) IN ('epicmeta','avatar_url','avatar_bytes') "
                                   "THEN ? ELSE ? END", (now, self.policy.metadataDisplaySeconds, self.dead_ttl))
            else:
                self._conn.execute("DELETE FROM cache WHERE ? - fetched_at > ?", (now, self.dead_ttl))
            self._conn.execute("DELETE FROM snapshot WHERE taken_at < ?",
                               (now - self.SNAPSHOT_KEEP_DAYS * 86400,))
            self._conn.execute("DELETE FROM mutation_receipt WHERE expires_at < ?", (now,))
            self._conn.execute("DELETE FROM person_recent WHERE seen_at<=?", (now - self.PERSON_RECENT_SECONDS,))
        except Exception:
            pass

    def purge(self):
        """죽은 캐시·오래된 스냅샷 정리 — 시작 시 1회 등 밖에서 호출용."""
        with self._lock:
            self._purge_within_lock()
            self._conn.commit()

    def close(self):
        """Release the SQLite handle (benchmarks and short-lived clients)."""
        with self._lock:
            self._conn.close()

    def get_or_set(self, key, ttl, producer):
        hit = self.get(key)
        if hit is not None:
            if self._needs_revalidate(key):
                self.revalidator(key, ttl, producer)
            return hit, True
        alive = self.get_alive(key, record=False)
        display = key.split(":", 1)[0] in DISPLAY_KINDS | SWR_AGGREGATE_KINDS
        if not revalidating() and alive is not None:
            if self.skip_producer and self.skip_producer():
                self.served_stale_at = time.time()
                note_read(self.metadata(key))
                return alive[0], True
            if (self.policy is not None and display and self.revalidator
                    and not (self.metadata(key) or {}).get("invalidated", True)):
                self.revalidator(key, ttl, producer)
                self.served_stale_at = time.time()
                note_read(self.metadata(key))
                return alive[0], True
        try:
            return self.refresh(key, ttl, producer), False
        except Exception:
            if revalidating() or alive is None or self.get_alive(key, record=False) is None:
                raise
            self.served_stale_at = time.time()
            note_read(self.metadata(key))
            return alive[0], True

    def get_or_set_strict(self, key, ttl, producer):
        hit = self.get(key)
        if hit is not None:
            return hit, True
        return self.refresh(key, ttl, producer), False

    def _needs_revalidate(self, key):
        if not self.revalidator or not self.always_revalidate:
            return False
        if self.skip_producer and self.skip_producer():
            return False          # 상류가 죽었는데 갱신을 걸면 큐만 쌓인다
        return key.startswith(self.always_revalidate)

    def get_stale(self, key):
        """TTL 이 지났어도 남아 있으면 값을 준다(SWR 용). 없으면 None."""
        got = self.get_stale_at(key)
        return got[0] if got else None

    def get_stale_at(self, key):
        """(값, 받아온 시각) — 낡았는지·얼마나 낡았는지 판단해야 하는 쪽을 위해."""
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, fetched_at FROM cache WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0]), float(row[1])
        except Exception:
            return None

    def get_alive(self, key, dead_ttl=None, *, record=True):
        """죽지 않은(dead_ttl 이내) 값. 낡았어도 준다. (값, 받아온 시각) 또는 None."""
        got = self.get_stale_at(key)
        if not got and self.policy is not None:
            with self._lock:
                row = self._conn.execute("SELECT payload,fetched_at FROM cache_display WHERE key=?", (key,)).fetchone()
            if row:
                got = json.loads(row[0]), float(row[1])
        if not got:
            return None
        dead = self.display_ttl(key) if dead_ttl is None else int(dead_ttl)
        if time.time() - got[1] > dead:
            return None
        if record:
            note_read(self.metadata(key))
        return got

    def has_any(self):
        with self._lock:
            for table in ("cache", "cache_display"):
                row = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE ? - fetched_at <= CASE WHEN "
                    "substr(key,1,instr(key,':')-1) IN ('epicmeta','avatar_url','avatar_bytes') "
                    "THEN ? ELSE ? END LIMIT 1",
                    (time.time(), self.policy.metadataDisplaySeconds if self.policy else self.dead_ttl,
                     self.dead_ttl)).fetchone()
                if row:
                    return True
        return False

    def get_or_set_swr(self, key, ttl, producer, background):
        hit = self.get(key)
        if hit is not None:
            if self._needs_revalidate(key):
                background(key, ttl, producer)
            return hit, "fresh"
        alive = self.get_alive(key, record=False)
        if alive is not None and not revalidating():
            if self.skip_producer and self.skip_producer():
                note_read(self.metadata(key))
                return alive[0], "stale"
            if not (self.metadata(key) or {}).get("invalidated", True):
                background(key, ttl, producer)
                note_read(self.metadata(key))
                return alive[0], "stale"
        try:
            return self.refresh(key, ttl, producer), "miss"
        except Exception:
            if revalidating() or alive is None or self.get_alive(key, record=False) is None:
                raise
            note_read(self.metadata(key))
            return alive[0], "stale"

    def _archive_display(self, where, args):
        if self.policy is None:
            return
        kinds = tuple(DISPLAY_KINDS)
        marks = ",".join("?" for _ in kinds)
        self._conn.execute(
            f"INSERT OR REPLACE INTO cache_display SELECT * FROM cache WHERE ({where}) "
            f"AND substr(key,1,instr(key,':')-1) IN ({marks})", tuple(args) + kinds)

    def forget(self, key):
        """Definitive deletion/permission loss removes both authoritative and display values."""
        with self._lock:
            self._write_fence += 1
            for table in ("cache", "cache_display"):
                self._conn.execute(f"DELETE FROM {table} WHERE key=?", (key,))
            self._conn.commit()

    def invalidate(self, prefix=None):
        with self._lock:
            self._write_fence += 1
            if prefix:
                self._archive_display("key LIKE ?", (prefix + "%",))
                self._conn.execute("DELETE FROM cache WHERE key LIKE ?", (prefix + "%",))
            else:
                self._conn.execute("DELETE FROM cache")
                self._conn.execute("DELETE FROM cache_display")
            self._conn.commit()

    def invalidate_keys(self, keys):
        unique = list(dict.fromkeys(str(key) for key in keys if key))
        if not unique:
            return 0
        with self._lock:
            self._write_fence += 1
            for offset in range(0, len(unique), 400):
                batch = unique[offset:offset + 400]
                marks = ",".join("?" for _ in batch)
                self._archive_display(f"key IN ({marks})", batch)
                self._conn.execute(f"DELETE FROM cache WHERE key IN ({marks})", batch)
            self._conn.commit()
        return len(unique)

    # ── Jira 쓰기 receipt ────────────────────────────────────────────────────
    # 일반 cache와 의도적으로 API를 분리한다. `/api/refresh`의 전체 invalidate는 조회 결과만
    # 비워야 하고, 결과가 불확실한 쓰기의 중복 방지 증거는 TTL 동안 살아 있어야 한다.
    def mutation_receipt(self, key):
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT fingerprint,state,result,context,attempted_at,expires_at "
                "FROM mutation_receipt WHERE key=?", (str(key),)
            ).fetchone()
            if row and float(row[5]) < now:
                self._conn.execute("DELETE FROM mutation_receipt WHERE key=?", (str(key),))
                self._conn.commit()
                row = None
        if not row:
            return None
        try:
            result = json.loads(row[2]) if row[2] else None
        except Exception:
            result = None
        try:
            context = json.loads(row[3]) if row[3] else None
        except Exception:
            context = None
        return {
            "fingerprint": row[0], "state": row[1], "result": result,
            "context": context,
            "attemptedAt": float(row[4]), "expiresAt": float(row[5]),
        }

    def set_mutation_receipt(self, key, fingerprint, state, result, attempted_at, ttl,
                             context=None):
        now = time.time()
        with self._lock:
            # ``None`` means preserve an existing non-sensitive recovery context.  Tests and old
            # callers that only move ``attempted_at`` must not accidentally erase the authoritative
            # transition identity required to reconcile a response-lost write.
            if context is None:
                existing = self._conn.execute(
                    "SELECT context FROM mutation_receipt WHERE key=?", (str(key),)
                ).fetchone()
                context_json = existing[0] if existing else ""
            else:
                context_json = json.dumps(context, ensure_ascii=False)
            self._conn.execute(
                "INSERT OR REPLACE INTO mutation_receipt"
                "(key,fingerprint,state,result,context,attempted_at,expires_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(key), str(fingerprint), str(state),
                 json.dumps(result, ensure_ascii=False) if result is not None else "",
                 context_json, float(attempted_at), now + max(1, int(ttl))),
            )
            self._conn.commit()

    def delete_mutation_receipt(self, key):
        with self._lock:
            self._conn.execute("DELETE FROM mutation_receipt WHERE key=?", (str(key),))
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

    # ── 최근 열어본 항목 ──
    # 왜 서버에 두나: 이 백엔드는 상주하고 사용자는 앱 창·크롬·엣지 등 **여러 브라우저**로 같은
    # 백엔드를 연다. localStorage 에 두면 브라우저마다 목록이 갈린다. (브라우저 히스토리·캐시는
    # 웹페이지가 읽을 수 없으므로, 우리가 연 것을 우리가 기록하는 수밖에 없다.)
    RECENT_KEEP = 200                       # 이 개수만 남기고 오래된 것부터 버린다
    PERSON_RECENT_SECONDS = 7 * 86400
    PERSON_RECENT_KEEP = 200

    def touch_person_recent(self, scope, user_id, kind):
        """Explicit choices only; independent of display-cache freshness or invalidation."""
        if not scope or not user_id or kind not in {"lookup", "selection", "mention"}:
            raise ValueError("Invalid person history event")
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO person_recent(scope,user_id,kind,seen_at) VALUES (?,?,?,?) "
                "ON CONFLICT(scope,user_id,kind) DO UPDATE SET seen_at=max(seen_at,excluded.seen_at)",
                (scope, user_id, kind, now))
            self._conn.execute("DELETE FROM person_recent WHERE seen_at<=?",
                               (now - self.PERSON_RECENT_SECONDS,))
            self._conn.execute(
                "DELETE FROM person_recent WHERE scope=? AND rowid NOT IN "
                "(SELECT rowid FROM person_recent WHERE scope=? ORDER BY seen_at DESC,rowid DESC LIMIT ?)",
                (scope, scope, self.PERSON_RECENT_KEEP))
            self._conn.commit()

    def person_recent(self, scope, user_ids):
        """Read only requested candidates; never return an actor's entire interaction log."""
        ids = list(dict.fromkeys(user_ids))
        if not scope or not ids:
            return {}
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id,kind,seen_at FROM person_recent WHERE scope=? AND seen_at>? "
                "AND user_id IN (" + ",".join("?" for _ in ids) + ")",
                (scope, time.time() - self.PERSON_RECENT_SECONDS, *ids)).fetchall()
        result = {}
        for user_id, kind, seen_at in rows:
            result.setdefault(user_id, {})[kind] = seen_at
        return result

    def ticket_read_states(self, scope, keys):
        keys = list(dict.fromkeys(keys))
        if not keys:
            return {}
        with self._lock:
            rows = self._conn.execute(
                "SELECT issue_key, viewed_through FROM ticket_read_state WHERE scope=? "
                "AND issue_key IN (%s)" % ",".join("?" for _ in keys),
                (scope, *keys)).fetchall()
        return dict(rows)

    def mark_ticket_viewed(self, scope, key, viewed_through):
        """Persist the displayed-content watermark; late responses cannot move it backwards."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO ticket_read_state(scope,issue_key,viewed_through) VALUES (?,?,?) "
                "ON CONFLICT(scope,issue_key) DO UPDATE SET "
                "viewed_through=MAX(ticket_read_state.viewed_through,excluded.viewed_through)",
                (scope, key, viewed_through))
            self._conn.commit()
            return self._conn.execute(
                "SELECT viewed_through FROM ticket_read_state WHERE scope=? AND issue_key=?",
                (scope, key)).fetchone()[0]

    def touch_recent(self, url, kind, title, meta="", type_="", data=""):
        if not url:
            return
        # data 는 표시용 부가필드(JSON 문자열). dict 로 들어오면 직렬화.
        if isinstance(data, (dict, list)):
            try:
                data = json.dumps(data, ensure_ascii=False)
            except Exception:
                data = ""
        with self._lock:
            self._conn.execute(
                "INSERT INTO recent(url, kind, title, meta, type, data, opened_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET opened_at=excluded.opened_at, "
                "title=excluded.title, meta=excluded.meta, kind=excluded.kind, type=excluded.type, data=excluded.data",
                (url, kind or "web", title or url, meta or "", type_ or "", data or "", time.time()),
            )
            self._conn.execute(
                "DELETE FROM recent WHERE url NOT IN "
                "(SELECT url FROM recent ORDER BY opened_at DESC LIMIT ?)", (self.RECENT_KEEP,))
            self._conn.commit()

    def recent_items(self, limit=20, kind=None):
        sql = "SELECT url, kind, title, meta, type, data, opened_at FROM recent"
        args = []
        if kind:
            sql += " WHERE kind=?"
            args.append(kind)
        sql += " ORDER BY opened_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out = []
        for u, k, t, m, ty, data, at in rows:
            item = {"url": u, "kind": k, "title": t, "meta": m, "type": ty, "openedAt": at}
            if data:
                try:
                    d = json.loads(data)
                    if isinstance(d, dict):
                        # 표시용 부가필드(key·epicKey/Name·assignee·status·…)를 그대로 올린다.
                        # 코어 필드는 덮어쓰지 않는다.
                        for kk, vv in d.items():
                            if kk not in item:
                                item[kk] = vv
                except Exception:
                    pass
            out.append(item)
        return out

    def forget_recent(self, url=None):
        with self._lock:
            if url:
                self._conn.execute("DELETE FROM recent WHERE url=?", (url,))
            else:
                self._conn.execute("DELETE FROM recent")
            self._conn.commit()
