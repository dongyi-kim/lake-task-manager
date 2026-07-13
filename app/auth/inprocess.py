"""mock 인증 provider — 외부 오픈소스 jira820 서버를 **in-process(ASGI)** 로 호출.

mock 도 local 과 동일하게 jira820(이 프로젝트 world 주입, app/fakebridge)을 통해 REST 를 소비한다.
차이는 전송뿐: mock=in-process(소켓 없음, run_fake 불필요) / local=실 HTTP(:8080). → mock==local.
"""

import threading

from .base import AuthProvider, SessionExpired


class InProcessProvider(AuthProvider):
    supports_parallel = False   # TestClient(httpx) 를 락으로 직렬화 → 앱 스레드풀 동시성에도 안전

    def __init__(self):
        from fastapi.testclient import TestClient

        from app.fakebridge import build_injected_app
        self._client = TestClient(build_injected_app())
        self._lock = threading.Lock()

    def _get(self, path, params):
        with self._lock:
            r = self._client.get(path, params=params or {})
        if r.status_code in (401, 403) or r.status_code >= 500:
            raise SessionExpired(f"HTTP {r.status_code} on {path}")
        return r

    def get_json(self, path, params=None):
        return self._get(path, params).json()

    def get_text(self, path, params=None):
        return self._get(path, params).text

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
