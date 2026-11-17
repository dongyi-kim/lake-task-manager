"""Small durable display-name directory, independent of ticket/auth cache policy."""
import json
import time
from urllib.parse import urlsplit, urlunsplit

FRESH_SECONDS = 90 * 86400
RETAIN_SECONDS = 365 * 86400


def source_scope(env, base):
    # Only the synthetic world's calendar suffix changes daily; realms stay distinct.
    realm = str(env)
    if realm.startswith(("mock@", "local@")):
        realm = realm.split("@", 1)[0]
    url = urlsplit(str(base or "").strip())
    base = urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path.rstrip("/"), "", ""))
    return json.dumps([realm, base], separators=(",", ":"))


def useful_name(username, name):
    return isinstance(name, str) and bool(name.strip()) and name.strip().casefold() != str(username).strip().casefold()


class PersonNames:
    def __init__(self, cache, scope):
        self.cache, self.scope = cache, scope
        with cache._lock, cache._conn:
            cache._conn.execute("""CREATE TABLE IF NOT EXISTS person_name (
                scope TEXT NOT NULL, username TEXT NOT NULL, display_name TEXT NOT NULL,
                observed_at REAL NOT NULL, PRIMARY KEY(scope,username))""")

    def read(self, username, max_age=RETAIN_SECONDS):
        with self.cache._lock:
            row = self.cache._conn.execute(
                "SELECT display_name,observed_at FROM person_name WHERE scope=? AND username=?",
                (self.scope, str(username).strip().casefold())).fetchone()
        if row and 0 <= time.time() - row[1] <= max_age:
            return row[0], row[1]
        return None

    def observe(self, username, name, observed_at=None):
        self.observe_many([(username, name, observed_at)])

    def observe_many(self, observations):
        now, rows = time.time(), []
        for username, name, observed_at in observations:
            observed_at = now if observed_at is None else float(observed_at)
            if username and useful_name(username, name) and 0 <= now - observed_at <= RETAIN_SECONDS:
                rows.append((self.scope, str(username).strip().casefold(), name.strip(), observed_at))
        if not rows:
            return
        with self.cache._lock, self.cache._conn:
            self.cache._conn.executemany("""INSERT INTO person_name VALUES (?,?,?,?)
                ON CONFLICT(scope,username) DO UPDATE SET display_name=excluded.display_name,
                observed_at=excluded.observed_at WHERE excluded.observed_at>person_name.observed_at""",
                rows)
