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
CREATE TABLE IF NOT EXISTS recent (
    url       TEXT PRIMARY KEY,      -- 같은 대상은 한 줄만(다시 열면 시각만 갱신)
    kind      TEXT NOT NULL,         -- jira | confluence | web
    title     TEXT NOT NULL,
    meta      TEXT NOT NULL DEFAULT '',
    type      TEXT NOT NULL DEFAULT '',   -- 이슈타입(Task/Bug/…) — 목록을 검색 결과와 같은 모양으로
    opened_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_recent_at ON recent(opened_at DESC);
"""


DEAD_TTL_DEFAULT = 24 * 3600      # 이만큼 지나면 진짜 없는 값으로 본다


class Cache:
    def __init__(self, db_path, dead_ttl=DEAD_TTL_DEFAULT):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._lock = threading.Lock()
        self.dead_ttl = int(dead_ttl)
        # 상류(Jira)가 지금 못 쓰는 상태면 True 를 주는 콜러블. 있으면 producer 를 **아예 호출하지
        # 않고** 낡은 값으로 답한다 — 죽은 세션에 매번 붙어 보느라 화면이 멎는 걸 막는다
        # (prod 의 Playwright 호출은 실패 판정까지 최대 180초가 걸린다).
        self.skip_producer = None
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
                self._conn.commit()
        except Exception:
            pass

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
        """신선하면 그대로, 아니면 producer() 로 갱신. **갱신에 실패하면 낡은 값으로 버틴다.**

        오프라인·SSO 만료에서 화면을 지키는 지점이 여기다. 예전엔 producer 가 실패하면 그대로
        예외가 올라가 화면이 통째로 비었는데, 정작 어제 받아 둔 같은 데이터가 캐시에 있었다.
        dead_ttl 안이면 그걸 준다 — 낡았다는 사실은 화면 상단 알림이 따로 말한다.

        반환: (값, hit) — hit=True 는 상류를 안 탔다는 뜻(신선하든 낡았든).
        """
        hit = self.get(key)
        if hit is not None:
            # 편집에 예민한 키는 신선해도 뒤에서 다시 받는다(화면은 즉시 뜨고 값은 곧 최신이 된다).
            if self._needs_revalidate(key):
                self.revalidator(key, ttl, producer)
            return hit, True
        # 상류가 죽은 걸 이미 아는 상태면 붙어 보지 않는다(실패 판정에만 수십 초가 걸린다).
        if self.skip_producer and self.skip_producer():
            alive = self.get_alive(key)
            if alive is not None:
                self.served_stale_at = time.time()
                return alive[0], True
        try:
            value = producer()
        except Exception:
            alive = self.get_alive(key)
            if alive is None:
                raise                       # 낡은 값조차 없으면 숨길 게 없다 — 그대로 알린다
            self.served_stale_at = time.time()
            return alive[0], True
        self.set(key, value, ttl)
        self.last_upstream_ok = time.time()
        return value, False

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

    def get_alive(self, key, dead_ttl=None):
        """죽지 않은(dead_ttl 이내) 값. 낡았어도 준다. (값, 받아온 시각) 또는 None."""
        got = self.get_stale_at(key)
        if not got:
            return None
        dead = self.dead_ttl if dead_ttl is None else int(dead_ttl)
        return got if (time.time() - got[1]) <= dead else None

    def has_any(self):
        """캐시에 쓸 만한 게 하나라도 있는가 — 오프라인으로 띄울지, 로그인부터 시킬지 가른다."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM cache WHERE fetched_at > ? LIMIT 1",
                (time.time() - self.dead_ttl,)).fetchone()
        return bool(row)

    def get_or_set_swr(self, key, ttl, producer, background):
        """stale-while-revalidate.

        · 신선하면 그대로
        · 만료됐지만 값이 남아 있으면 **즉시 그 값을 주고** 갱신은 background(fn) 에 맡긴다
        · 아예 없으면 producer() 를 동기로 실행(첫 조회는 기다릴 수밖에 없다)

        체감 대기시간을 없애는 게 목적이다 — 재방문 시 화면은 즉시 뜨고,
        새 데이터는 다음 조회부터 반영된다.
        """
        hit = self.get(key)
        if hit is not None:
            if self._needs_revalidate(key):
                background(key, ttl, producer)   # 신선해도 다시 받는다(편집에 예민한 키)
            return hit, "fresh"
        stale = self.get_stale(key)
        if stale is not None:
            background(key, ttl, producer)
            return stale, "stale"
        value = producer()
        self.set(key, value, ttl)
        return value, "miss"

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

    # ── 최근 열어본 항목 ──
    # 왜 서버에 두나: 이 백엔드는 상주하고 사용자는 앱 창·크롬·엣지 등 **여러 브라우저**로 같은
    # 백엔드를 연다. localStorage 에 두면 브라우저마다 목록이 갈린다. (브라우저 히스토리·캐시는
    # 웹페이지가 읽을 수 없으므로, 우리가 연 것을 우리가 기록하는 수밖에 없다.)
    RECENT_KEEP = 200                       # 이 개수만 남기고 오래된 것부터 버린다

    def touch_recent(self, url, kind, title, meta="", type_=""):
        if not url:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO recent(url, kind, title, meta, type, opened_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET opened_at=excluded.opened_at, "
                "title=excluded.title, meta=excluded.meta, kind=excluded.kind, type=excluded.type",
                (url, kind or "web", title or url, meta or "", type_ or "", time.time()),
            )
            self._conn.execute(
                "DELETE FROM recent WHERE url NOT IN "
                "(SELECT url FROM recent ORDER BY opened_at DESC LIMIT ?)", (self.RECENT_KEEP,))
            self._conn.commit()

    def recent_items(self, limit=20, kind=None):
        sql = "SELECT url, kind, title, meta, type, opened_at FROM recent"
        args = []
        if kind:
            sql += " WHERE kind=?"
            args.append(kind)
        sql += " ORDER BY opened_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [{"url": u, "kind": k, "title": t, "meta": m, "type": ty, "openedAt": at}
                for u, k, t, m, ty, at in rows]

    def forget_recent(self, url=None):
        with self._lock:
            if url:
                self._conn.execute("DELETE FROM recent WHERE url=?", (url,))
            else:
                self._conn.execute("DELETE FROM recent")
            self._conn.commit()
