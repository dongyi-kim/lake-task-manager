"""SSO 세션(쿠키) 저장소 — **서비스별로 따로** 둔다.

왜 한 파일이 아니라 서비스별인가:
    Playwright 의 storage_state 는 컨텍스트가 가진 쿠키를 통째로 덤프한다. 그래서 Confluence 만
    다시 로그인하고 저장하면(그 컨텍스트엔 Confluence 쿠키뿐이라) **Jira/Bitbucket 쿠키가 통째로
    사라진다.** 실제로 login_wait() 경로가 그랬다. 서비스별로 나눠 두면 한 서비스를 갱신해도
    나머지는 손대지 않는다. 만료도 서비스마다 다르게 오므로 이 단위가 맞다.

왜 DB 가 아닌가:
    - 이건 **비밀정보**다. 우리 DB(cache.sqlite3)는 언제든 지워도 되는 캐시다 — 거기 섞으면
      "캐시 지웠더니 로그아웃" 이 된다. 수명이 다른 것을 같은 저장소에 두지 않는다.
    - '트랜잭션' 이 필요한 실제 이유는 **찢어진 쓰기**(쓰다 죽으면 JSON 이 잘려 로그인이 날아감)인데,
      그건 임시파일 + os.replace(원자적 교체)로 끝난다. DB 는 이 한 줄짜리 문제에 과한 답이다.
    - 동시 writer 가 없다(각자 PC 에서 도는 1인 앱). 조회·조인도 필요 없다.

파일 형태는 Playwright storage_state 그대로 — 그래야 직렬화 계층이 없다.
    {"cookies": [...], "origins": [...]}
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

SERVICES = ("jira", "confluence", "bitbucket")


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _cookie_matches(cookie, host):
    """쿠키 domain 이 이 호스트 것인가. Jira 는 보통 '.corp.example' 처럼 상위 도메인에 붙는다."""
    if not host:
        return False
    d = str(cookie.get("domain") or "").lower().lstrip(".")
    if not d:
        return False
    return host == d or host.endswith("." + d)


class SsoStore:
    """서비스별 세션 파일 묶음. 경로는 기존 state_path 옆의 `sso/` 디렉터리."""

    def __init__(self, legacy_state_path, service_bases=None):
        self.legacy = Path(legacy_state_path)
        self.dir = self.legacy.parent / "sso"
        # Login, silent renewal and rolling-cookie persistence can finish on different threads.
        # Atomic replace prevents torn JSON, while this lock also gives conditional writes a
        # process-local compare-and-swap boundary so an old provider cannot overwrite a new login.
        self._lock = threading.Lock()
        # {"jira": "https://jira.corp", ...} — 쿠키를 서비스에 배분할 때 쓴다
        self.bases = {k: v for k, v in (service_bases or {}).items() if v}

    # ── 경로 ──
    def path(self, service):
        return self.dir / (service.lower() + ".json")

    def _read(self, p):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and "cookies" in d:
                return d
        except Exception:
            pass
        return None

    # ── 읽기 ──
    def merged(self):
        """모든 서비스 세션을 하나의 storage_state 로 합친다(provider 컨텍스트가 이걸 먹는다).
        하나도 없으면 None — 호출부가 '로그인 필요' 로 처리한다."""
        self.migrate_legacy()
        cookies, origins, seen = [], [], set()
        for svc in SERVICES:
            d = self._read(self.path(svc))
            if not d:
                continue
            for c in d.get("cookies") or []:
                k = (c.get("name"), c.get("domain"), c.get("path"))
                if k in seen:
                    continue           # 같은 쿠키가 여러 서비스 파일에 있으면 먼저 것을 쓴다
                seen.add(k)
                cookies.append(c)
            for o in d.get("origins") or []:
                origins.append(o)
        if not cookies and not origins:
            return None
        return {"cookies": cookies, "origins": origins}

    def status(self):
        """서비스별 보유 여부·갱신 시각 — 설정창에서 '언제 로그인했나' 를 보여줄 수 있게."""
        out = {}
        for svc in SERVICES:
            p = self.path(svc)
            try:
                out[svc] = {"exists": p.exists(), "savedAt": p.stat().st_mtime if p.exists() else None}
            except Exception:
                out[svc] = {"exists": False, "savedAt": None}
        return out

    def any_exists(self):
        self.migrate_legacy()
        return any(self.path(s).exists() for s in SERVICES) or self.legacy.exists()

    # ── 쓰기 ──
    def save(self, service, state):
        """한 서비스 세션만 저장. state 는 Playwright storage_state dict.
        그 서비스 호스트의 쿠키만 남긴다 — 안 그러면 서비스별로 나눈 의미가 없다.
        (호스트를 모르면 통째로 저장: 최소한 잃지는 않는다.)"""
        with self._lock:
            self._save_unlocked(service, state)

    def _save_unlocked(self, service, state):
        service = service.lower()
        host = _host(self.bases.get(service, ""))
        cookies = state.get("cookies") or []
        if host:
            picked = [c for c in cookies if _cookie_matches(c, host)]
            # 필터가 0개면 도메인 규칙이 예상과 다른 것 — 버리지 말고 전부 둔다.
            cookies = picked or cookies
        origins = [o for o in (state.get("origins") or [])
                   if not host or _host(o.get("origin", "")) == host] if host else (state.get("origins") or [])
        self._atomic_write(self.path(service), {"cookies": cookies, "origins": origins})

    def _revision_unlocked(self, service):
        try:
            stat = self.path(service).stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def revision(self, service):
        """Opaque disk revision used to fence a delayed rolling-cookie snapshot."""
        with self._lock:
            return self._revision_unlocked(service)

    def save_if_unchanged(self, service, state, expected_revision):
        """Save only if no login/renewal replaced this service state in the meantime."""
        with self._lock:
            if self._revision_unlocked(service) != expected_revision:
                return False
            self._save_unlocked(service, state)
            return True

    def save_all_from(self, state):
        """로그인 창 하나에서 세 서비스를 모두 돌았을 때 — 도메인별로 갈라 각각 저장한다."""
        for svc in SERVICES:
            if self.bases.get(svc):
                self.save(svc, state)

    def _atomic_write(self, path, data):
        """임시파일에 쓰고 os.replace 로 바꿔치기 — 쓰는 도중 죽어도 이전 세션이 살아남는다
        (한 파일에 직접 쓰면 잘린 JSON 이 남아 로그인이 통째로 날아간다)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)          # 쿠키다 — 같은 PC 다른 사용자에게 열어 두지 않는다
            except Exception:
                pass
        except BaseException:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise

    # ── 이전 형식 이관 ──
    def migrate_legacy(self):
        """예전 단일 파일(jira_state.json)을 도메인 기준으로 갈라 서비스별 파일로 옮긴다.
        원본은 지우지 않는다 — 되돌릴 여지를 남긴다(다음부터는 서비스별 파일이 우선)."""
        if any(self.path(s).exists() for s in SERVICES):
            return False
        d = self._read(self.legacy)
        if not d:
            return False
        self.save_all_from(d)
        return True
