"""mock 인증 provider — 외부 오픈소스 jira820 서버를 **in-process(ASGI)** 로 호출.

mock 도 local 과 동일하게 jira820(이 프로젝트 world 주입, app/fakebridge)을 통해 REST 를 소비한다.
차이는 전송뿐: mock=in-process(소켓 없음, run_fake 불필요) / local=실 HTTP(:8080). → mock==local.
"""

import threading

from .base import AuthProvider, SessionExpired, UpstreamError, WRITE_HEADERS


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

    def get_json(self, path, params=None, priority=0):   # priority 무시(큐 없음)
        return self._get(path, params).json()

    def _write(self, method, path, json_body, params, want_json=True):
        with self._lock:
            fn = getattr(self._client, method)
            kw = {"params": params or {}, "headers": WRITE_HEADERS}
            if json_body is not None:
                kw["json"] = json_body
            r = fn(path, **kw)
        if r.status_code == 401:
            raise SessionExpired(f"HTTP 401 on {path}")
        if r.status_code >= 400:
            raise UpstreamError(r.status_code, path, r.text)
        if not want_json:
            return r.status_code
        try:
            return r.json()
        except Exception:
            return {}

    def post_json(self, path, json_body=None, params=None):
        return self._write("post", path, json_body or {}, params)

    def put_json(self, path, json_body=None, params=None):
        return self._write("put", path, json_body or {}, params)

    def delete(self, path, params=None):
        return self._write("delete", path, None, params, want_json=False)

    def get_text(self, path, params=None):
        return self._get(path, params).text

    def get_bytes(self, path, params=None):
        # in-process(jira820) 상대 경로만 유효(절대 외부 URL 은 mock 대상 아님)
        r = self._get(path, params)
        return r.content, r.headers.get("content-type")

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
