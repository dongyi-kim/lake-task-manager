"""로컬 개발 인증 — basic auth 또는 PAT(bearer). 로컬 Fake Jira(:8080)/Docker Jira 용."""

import requests

from .base import (AuthProvider, MULTIPART_HEADERS, SessionExpired, UpstreamError, UpstreamUnavailable,
                   WRITE_HEADERS, XSRF_HEADER)
from .issue_create import check_create_body, log_create_response


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
        url = path if path.startswith(("http://", "https://")) else self.base + path
        r = self.session.get(url, params=params, timeout=30)
        if r.status_code in (401, 403) or r.status_code >= 500:
            raise SessionExpired(f"HTTP {r.status_code} on {path}")
        r.raise_for_status()
        return r

    def get_json(self, path, params=None, priority=0, quiet=False):   # priority·quiet 무시(큐 없음)
        return self._get(path, params).json()

    def _write(self, method, path, json_body, params, want_json=True):
        check_create_body(method, path, json_body)
        url = path if path.startswith(("http://", "https://")) else self.base + path
        fn = getattr(self.session, method)
        r = fn(url, json=json_body, params=params, timeout=30, headers=WRITE_HEADERS)
        log_create_response(method, path, json_body, r.status_code, r.text if r.status_code >= 400 else "")
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

    def post_multipart(self, path, filename, data, content_type=None, field="file", params=None):
        url = path if path.startswith(("http://", "https://")) else self.base + path
        files = {field: (filename, data, content_type or "application/octet-stream")}
        r = self.session.post(url, files=files, params=params, timeout=60, headers=MULTIPART_HEADERS)
        if r.status_code == 401:
            raise SessionExpired(f"HTTP 401 on {path}")
        if r.status_code >= 400:
            raise UpstreamError(r.status_code, path, r.text)
        try:
            return r.json()
        except Exception:
            return {}

    def get_text(self, path, params=None):
        return self._get(path, params).text

    def web_form(self, path, fields=None, *, may_commit=False):
        from .web_forms import check_html_size, form_request, form_response, identity_response
        url, headers, body = form_request(self.base, path, fields, may_commit)

        def identity_probe():
            try:
                with self.session.get(self.base + "/rest/api/2/myself", timeout=30,
                                      allow_redirects=False) as response:
                    data = response.json() if response.status_code == 200 else None
                    return identity_response(response.status_code, data)
            except (requests.RequestException, ValueError):
                return None

        try:
            with self.session.request("GET" if body is None else "POST", url, data=body,
                                      headers=headers, timeout=30, allow_redirects=False,
                                      stream=True) as response:
                data = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    check_html_size(len(data) + len(chunk))
                    data.extend(chunk)
                return form_response(self.base, response.status_code, response.headers,
                                     bytes(data), identity_probe)
        except requests.RequestException:
            raise UpstreamUnavailable("Jira 폼 서버에 연결할 수 없습니다.") from None

    def get_bytes(self, path, params=None):
        url = path if path.startswith(("http://", "https://")) else self.base + path
        r = self.session.get(url, params=params, timeout=30)
        if r.status_code in (401, 403) or r.status_code >= 500:
            raise SessionExpired(f"HTTP {r.status_code} on {path}")
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type")
