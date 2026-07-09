"""로컬 개발 인증 — basic auth 또는 PAT(bearer). 로컬 Fake Jira(:8080)/Docker Jira 용."""

import requests

from .base import AuthProvider, SessionExpired


class BasicAuthProvider(AuthProvider):
    supports_parallel = True   # requests.Session 은 스레드 간 동시 GET 안전

    def __init__(self, base, user, token, auth="basic"):
        self.base = base.rstrip("/")
        self.session = requests.Session()
        if auth == "bearer":
            self.session.headers["Authorization"] = f"Bearer {token}"
        else:
            self.session.auth = (user, token)

    def _get(self, path, params):
        r = self.session.get(self.base + path, params=params, timeout=30)
        if r.status_code in (401, 403) or r.status_code >= 500:
            raise SessionExpired(f"HTTP {r.status_code} on {path}")
        r.raise_for_status()
        return r

    def get_json(self, path, params=None):
        return self._get(path, params).json()

    def get_text(self, path, params=None):
        return self._get(path, params).text
