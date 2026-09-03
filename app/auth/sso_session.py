"""
운영 인증 — Playwright storage_state(SSO 세션) 재사용. (../../jira_test.py 방식)

핵심: **Playwright 번들 Chromium** 을 쓴다(설치된 회사 Chrome 이 아니라).
      회사 관리형 Chrome 은 정책으로 자동화/인증서 흐름을 막아 SSO 가 깨진다 →
      정책과 무관한 깨끗한 Chromium 에서 사람이 직접 로그인하는 데모 방식이 안정적.
      Chromium 은 `playwright install chromium` 으로 준비된다(런처가 자동 처리).

동작:
  1) 로그인 1회: headed Chromium 으로 사람이 사내 SSO/인증서 로그인 → storage_state 파일 저장.
     python run.py login   (또는 화면의 'SSO 로그인' 버튼)
  2) 이후: 저장된 세션을 headless Chromium 으로 로드해 context.request 로 REST 호출(쿠키/헤더 상속).
  3) 세션 만료(수 시간~하루) → 1) 재실행.

playwright 는 prod 전용 의존(requirements-sso.txt). import 는 지연.
"""

import os
import itertools
import queue
import re
import sys
import time as _time
from urllib.parse import urlsplit

# 같은 [auth] 메시지를 초당 여러 번 찍지 않게 — prod 는 상류가 단일 큐라 같은 401 이 연달아 온다.
_auth_seen = {}


def _auth_log(msg):
    now = _time.monotonic()
    if now - _auth_seen.get(msg, 0) < 20.0:
        return
    _auth_seen[msg] = now
    print(msg, file=sys.stderr, flush=True)
import threading

from .base import (AuthProvider, LoginRequired, PendingUpstreamOperation,
                   PermissionDenied, SessionExpired, UpstreamError, UpstreamUnavailable,
                   WRITE_HEADERS, PRIO_BACKGROUND, PRIO_WRITE, upstream_priority)


def _launch(p, headless):
    """Playwright 번들 Chromium 으로 실행. 없으면 'playwright install chromium' 안내."""
    try:
        return p.chromium.launch(headless=headless)
    except Exception as e:
        raise RuntimeError(
            "Chromium 을 실행하지 못했습니다. 'playwright install chromium' 이 필요합니다. "
            "(런처 exe 는 이를 자동 수행합니다)\n원인: " + str(e))


class SsoSessionProvider(AuthProvider):
    """Playwright storage_state 재사용 provider.

    ※ Playwright sync API 는 **스레드 안전하지 않다**(객체는 생성한 스레드에서만 사용 가능).
      FastAPI 는 요청마다 다른 워커 스레드에서 sync 핸들러를 돌리므로, 싱글턴 provider 를
      여러 스레드가 공유하면 'greenlet.error: Cannot switch to a different thread' 가 난다.
      → Playwright 를 **전용 스레드**에 가두고, 모든 호출(get/json/text)을 큐로 그 스레드에
        마샬링해 실행한다. 단일 context 라 자연히 직렬(supports_parallel=False).
    """

    supports_parallel = False

    # Playwright 기본 30초보다 짧게 명시한다. 상류가 끊겼을 때 첫 실패를 유한 시간 안에
    # 확정해야 큐에 매달린 나머지 FastAPI worker 도 함께 풀어 줄 수 있다.
    REQUEST_TIMEOUT_MS = 20_000
    START_TIMEOUT = 20
    JOB_TIMEOUT = 30

    def __init__(self, base, store, user_agent=None):
        self.base = base.rstrip("/")
        # 세션 파일이 없으면 브라우저를 띄우기 전에 명확히 실패 → 라우트가 needLogin 으로 안내.
        if store is None or not store.any_exists():
            raise LoginRequired("저장된 SSO 세션이 없습니다. 최초 로그인이 필요합니다.")
        self._store = store
        self._state = store.merged()
        if not self._state:
            raise LoginRequired("저장된 SSO 세션이 비어 있습니다. 로그인이 필요합니다.")
        self._ua = user_agent
        # PriorityQueue — 사용자 요청(0)이 백그라운드 갱신(1)을 앞지른다.
        # 단일 큐라 백그라운드 작업이 앞에 쌓이면 사용자의 다음 조회가 그만큼 늦어진다.
        self._jobs = queue.PriorityQueue()
        self._seq = itertools.count()          # 같은 우선순위는 들어온 순서대로
        self._ready = threading.Event()
        self._broken = threading.Event()
        self._closed = threading.Event()
        self._broken_reason = ""
        self._start_error = None
        self._thread = threading.Thread(target=self._loop, name="playwright-sso", daemon=True)
        self._thread.start()
        if not self._ready.wait(self.START_TIMEOUT):
            self._mark_broken("SSO 브라우저 기동 시간 초과")
            raise UpstreamUnavailable(
                f"SSO 브라우저가 {self.START_TIMEOUT}초 안에 시작되지 않았습니다.")
        if self._start_error is not None:
            raise self._start_error

    def _loop(self):
        """이 스레드가 Playwright 객체를 소유하고, 큐로 들어온 작업만 실행한다."""
        try:
            from playwright.sync_api import sync_playwright   # 지연 import
            self._p = sync_playwright().start()
            self._browser = _launch(self._p, headless=True)
            ctx_kw = {"storage_state": self._state}
            if self._ua:
                ctx_kw["user_agent"] = self._ua
            self._context = self._browser.new_context(**ctx_kw)
        except BaseException as e:   # noqa: BLE001 - 기동 실패를 __init__ 로 전달
            self._start_error = e
            self._ready.set()
            return
        self._ready.set()
        while True:
            _prio, _seq, job = self._jobs.get()
            if job is None:
                break
            fn, done, box = job
            if self._broken.is_set() or self._closed.is_set():
                box[1] = UpstreamUnavailable(self._broken_reason or "SSO provider 사용 불가")
                done.set()
                continue
            try:
                # PC 절전/크래시로 브라우저가 죽어 있을 수 있다(밤새 켜 둔 뒤 흔하다).
                # 그때는 **저장된 쿠키 파일로 브라우저만 다시 띄운다** — 재로그인 불필요.
                if not self._browser.is_connected():
                    self._relaunch()
                box[0] = fn()
            except BaseException as e:   # noqa: BLE001 - 호출자 스레드로 재전달
                box[1] = e
            done.set()
        try:
            self._browser.close()
            self._p.stop()
        except Exception:
            pass

    def _relaunch(self):
        """브라우저/컨텍스트만 다시 만든다. storage_state(SSO 쿠키)는 파일에서 그대로 재사용하므로
        세션이 아직 유효하면 사용자는 아무것도 안 해도 된다. 만료됐다면 다음 호출이 401 →
        SessionExpired 로 정상 안내된다."""
        for obj in (getattr(self, "_context", None), getattr(self, "_browser", None)):
            try:
                obj.close()
            except Exception:
                pass
        self._browser = _launch(self._p, headless=True)
        # 디스크에서 다시 읽는다 — 그동안 다른 서비스가 갱신됐을 수 있다.
        ctx_kw = {"storage_state": self._store.merged() or self._state}
        if self._ua:
            ctx_kw["user_agent"] = self._ua
        self._context = self._browser.new_context(**ctx_kw)

    @property
    def broken(self):
        return self._broken.is_set() or self._closed.is_set()

    def _mark_broken(self, reason):
        """이 provider 를 격리하고 아직 실행 전인 대기 작업을 즉시 깨운다.

        Python 에서 다른 스레드의 Playwright 호출을 강제로 중단할 수는 없다. 대신 그 스레드는
        daemon 으로 퇴역시키고, 큐에 있던 요청과 새 요청은 기다리지 않게 한다. JiraClient 는
        회로차단기 시간이 지난 뒤 새 provider 를 만든다.
        """
        self._broken_reason = str(reason or "SSO provider 응답 없음")[:200]
        self._broken.set()
        while True:
            try:
                _prio, _seq, job = self._jobs.get_nowait()
            except queue.Empty:
                break
            if job is None:
                continue
            _fn, done, box = job
            box[1] = UpstreamUnavailable(self._broken_reason)
            done.set()

    @staticmethod
    def _is_transport_error(exc):
        name = type(exc).__name__.lower()
        msg = str(exc).lower()
        return ("timeout" in name or "timeout" in msg or "timed out" in msg
                or "target page, context or browser has been closed" in msg
                or "browser has been closed" in msg or "connection closed" in msg
                or "socket hang up" in msg or "econn" in msg)

    def _submit(self, fn, priority=0, wait=None, *, may_commit=False):
        """fn 을 Playwright 전용 스레드에서 실행하고 결과/예외를 호출자 스레드로 반환.

        priority: -1=쓰기 · 0=사용자 요청(기본) · 1=백그라운드 갱신. 작은 값이 먼저다.
        단일 큐라 백그라운드 작업이 앞에 쌓이면 사용자의 다음 조회가 그만큼 늦어진다
        → 낮은 우선순위로 넣어 사용자 요청이 항상 앞지르게 한다. 쓰기는 그보다도 앞이다.
        """
        if self.broken:
            raise UpstreamUnavailable(self._broken_reason or "SSO provider 가 격리되었습니다.")
        if not self._thread.is_alive():
            self._mark_broken("SSO provider 스레드가 종료되었습니다.")
            raise UpstreamUnavailable(self._broken_reason)
        done = threading.Event()
        box = [None, None]   # [result, error]
        operation = PendingUpstreamOperation(done, box)
        self._jobs.put((priority, next(self._seq), (fn, done, box)))
        limit = wait if wait else self.JOB_TIMEOUT      # 업로드는 크기에 맞춘 한도를 받는다
        if not done.wait(limit):
            self._mark_broken("Jira/SSO 응답 시간 초과(%ds)" % int(limit))
            error = UpstreamUnavailable(
                "Jira 응답이 없습니다(%ds 초과). 앱은 계속 실행되며 잠시 후 자동 재시도합니다."
                % int(limit))
            # Retiring the provider cannot interrupt the current Playwright call. Preserve its
            # real completion so mutation recovery never treats an empty read as permission to
            # POST while this old call can still commit.
            if may_commit:
                # Only a POST/PUT/DELETE owner job may become proof of a committed mutation. A
                # timed-out preflight GET can also finish late, but interpreting that read result
                # as the enclosing transition/create result would be incorrect.
                error.pending_operation = operation
            raise error
        if box[1] is not None:
            if self._is_transport_error(box[1]):
                self._mark_broken(box[1])
                raise UpstreamUnavailable(
                    "Jira/SSO 연결이 응답하지 않습니다. 앱은 계속 실행되며 잠시 후 자동 재시도합니다.")
            raise box[1]
        return box[0]

    @staticmethod
    def _response_text(resp):
        try:
            return resp.text() or ""
        except Exception:
            return ""

    @staticmethod
    def _response_headers(resp):
        try:
            return {
                str(name or "").lower(): str(value or "")
                for name, value in (resp.headers or {}).items()
            }
        except Exception:
            return {}

    @staticmethod
    def _has_explicit_auth_denial(headers, body):
        """Return True only for response signals that explicitly describe authentication.

        Jira commonly uses the same HTTP 403 for an expired/anonymous SSO session and for a
        perfectly authenticated user who cannot see one issue.  A bare ``403`` or a body saying
        merely "no permission" is therefore deliberately *not* enough here.
        """
        denied_reason = headers.get("x-authentication-denied-reason", "")
        seraph_reason = headers.get("x-seraph-loginreason", "")
        challenge = headers.get("www-authenticate", "")
        location = headers.get("location", "")
        if denied_reason or challenge:
            return True
        if seraph_reason and seraph_reason.strip().upper() not in {"OK", "NONE"}:
            return True
        if re.search(r"(?:/login(?:\.jsp)?\b|/signin\b|/sso\b)", location, re.I):
            return True
        return bool(re.search(
            r'"authenticated"\s*:\s*false|"loginrequired"\s*:\s*true|'
            r'not\s+authenticated|authentication\s+(?:is\s+)?required|'
            r'authentication\s+(?:has\s+)?failed|session\s+(?:has\s+)?expired|'
            r'you\s+are\s+not\s+logged\s+in|anonymous\s+user|'
            r'(?:id|name)=["\']login-form["\']|\bos_username\b|/login\.jsp\b|'
            r'로그인(?:이|을)?\s*(?:필요|실패)|세션(?:이|은)?\s*만료',
            body or "",
            re.I,
        ))

    def _is_jira_issue_read_url(self, url):
        target, jira = urlsplit(url), urlsplit(self.base)
        same_origin = (target.scheme.lower(), target.netloc.lower()) == (
            jira.scheme.lower(), jira.netloc.lower())
        base_path = (jira.path or "").rstrip("/")
        target_path = target.path or "/"
        if base_path:
            if not (target_path == base_path or target_path.startswith(base_path + "/")):
                return False
            target_path = target_path[len(base_path):] or "/"
        # Only an issue-scoped read has the expected "this object is hidden from this user"
        # semantics.  A 403 from search/config/myself can describe a broader authentication or
        # application-access failure and must retain the normal recovery path.
        return same_origin and bool(re.match(
            r"^/rest/api/(?:2|latest)/issue/[^/]+(?:/|$)", target_path, re.I))

    def _probe_jira_identity_after_403(self, failed_url):
        """Return True(alive), False(expired), or None(transport/response unknown).

        This runs inside the provider's Playwright owner thread, so it calls the request context
        directly rather than recursively enqueueing ``get_json``.  A successful ``/myself`` must
        contain a concrete Jira identity; a proxy/login HTML response with HTTP 200 is unknown.
        """
        if not self._is_jira_issue_read_url(failed_url):
            return None
        try:
            probe = self._context.request.get(
                self.base + "/rest/api/2/myself", timeout=self.REQUEST_TIMEOUT_MS)
        except Exception:
            return None
        if probe.status in (401, 403):
            return False
        if probe.status != 200:
            return None
        try:
            raw = probe.json()
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        identity = raw.get("name") or raw.get("key") or raw.get("accountId") or ""
        return bool(identity and "anonymous" not in str(identity).lower())

    def _raise_read_access_error(self, resp, path, url, *, quiet=False, label="GET"):
        """Classify a Jira read denial without turning every per-ticket 403 into logout.

        401 and explicit authentication signals retain the existing recovery path.  An otherwise
        ambiguous Jira 403 becomes a permission error only after a direct identity probe proves
        that the same browser session is still authenticated.  If that proof cannot be obtained,
        fail closed as ``SessionExpired``; the app-level handler performs its own tri-state probe
        and converts a transport-unknown result to a retryable 503 rather than opening login.
        """
        body = self._response_text(resp)
        headers = self._response_headers(resp)
        auth_hint = self._has_explicit_auth_denial(headers, body)
        reason = (headers.get("x-authentication-denied-reason")
                  or headers.get("x-seraph-loginreason") or "")
        session_alive = None
        if resp.status == 403 and not auth_hint:
            session_alive = self._probe_jira_identity_after_403(url)
        if resp.status == 403 and session_alive is True:
            if not quiet:
                _auth_log(f"[permission] {label} 403 {path}")
            # This class is intentionally outside SessionExpired: the global auth handler must
            # not probe /myself a second time or turn an issue visibility denial into 502/login.
            raise PermissionDenied(path, body)
        if not quiet:
            suffix = f" [{reason}]" if reason else ""
            _auth_log(f"[auth] {label} {resp.status} {path}{suffix}")
        detail = reason or (
            "인증 세션이 만료되었습니다." if session_alive is False or auth_hint
            else "인증 상태를 확인하지 못했습니다.")
        raise SessionExpired(f"HTTP {resp.status} on {path} — {detail}")

    def _fetch(self, path, params, as_text, quiet=False):
        # Playwright 스레드에서 실행 — body 추출(json()/text())도 반드시 이 스레드에서.
        # path 가 절대 URL(http…)이면 그대로(Confluence 등 별도 호스트), 아니면 jira base + path.
        url = path if path.startswith(("http://", "https://")) else self.base + path
        resp = self._context.request.get(url, params=params or {}, timeout=self.REQUEST_TIMEOUT_MS)
        if resp.status in (401, 403):
            self._raise_read_access_error(resp, path, url, quiet=quiet)
        if resp.status >= 500:
            raise UpstreamUnavailable(f"HTTP {resp.status} on {path} — Jira 서버 응답 오류")
        return resp.text() if as_text else resp.json()

    def get_json(self, path, params=None, priority=None, quiet=False):
        if priority is None:
            priority = upstream_priority()      # 백그라운드 갱신은 사용자 요청 뒤로
        return self._submit(lambda: self._fetch(path, params, False, quiet=quiet), priority)

    def _xsrf_cookie(self, url):
        """이 도메인의 XSRF 토큰 쿠키 값(없으면 None). 멀티파트는 이 값을 쿼리로도 보내야 한다."""
        try:
            for c in self._context.cookies(url):
                if c.get("name") in ("atl.xsrf.token", "atlassian.xsrf.token") and c.get("value"):
                    return c["value"]
        except Exception:
            pass
        return None

    def _xsrf_headers(self, url, multipart=False):
        """이 URL(도메인)에 맞는 XSRF 쓰기 헤더. multipart=True 면 Content-Type 을 뺀다
        (멀티파트 인코더가 boundary 와 함께 직접 지정 — 여기서 넣으면 boundary 누락).

        Bitbucket DC 의 XSRF 는 **Origin/Referer 로 same-origin 을 검증**한다. 그리고
        no-check 헤더는 '비브라우저 클라이언트' 에만 적용되고, **세션 쿠키를 든 브라우저 요청은
        무시**한다(실측: BITBUCKETSESSIONID 보유 + no-check 만으론 'XSRF check failed').
        우리 요청은 SSO 세션 쿠키를 들고 나가므로 브라우저 요청으로 분류된다 →
        **Origin/Referer 를 서비스 base 로 붙여 same-origin 으로 통과**시킨다.

        · Origin/Referer = 대상 URL 의 origin(scheme://host[:port])  ← Bitbucket 검색의 핵심
        · X-Atlassian-Token: no-check + (있으면) atl.xsrf.token echo  ← Jira/Confluence·비브라우저용
        · X-Requested-With: XMLHttpRequest
        """
        from urllib.parse import urlsplit
        from .base import MULTIPART_HEADERS
        u = urlsplit(url)
        origin = f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else None
        headers = dict(MULTIPART_HEADERS if multipart else WRITE_HEADERS)
        headers["X-Requested-With"] = "XMLHttpRequest"
        if origin:
            headers["Origin"] = origin
            headers["Referer"] = origin + "/"     # same-origin 판정용(경로는 무관)
        tok = self._xsrf_cookie(url)
        if tok:
            headers["X-Atlassian-Token"] = tok        # 쿠키가 있으면 double-submit 도
        return headers

    def _write(self, method, path, json_body, params, want_json=True):
        """쓰기(POST/PUT/DELETE) 공통 — XSRF 헤더 + JSON 명시 직렬화 + 진단 예외.
        편집 기능이 전부 이 경로를 탄다. 제품(Jira/Confluence/Bitbucket) 구분 없이 동일."""
        import json as _json
        url = path if path.startswith(("http://", "https://")) else self.base + path
        fn = getattr(self._context.request, method)     # post/put/delete
        kw = {"params": params or {}, "headers": self._xsrf_headers(url)}
        if json_body is not None:
            kw["data"] = _json.dumps(json_body)          # data=dict 인코딩 어긋남 방지 → 명시 직렬화
        resp = fn(url, **kw)
        if resp.status >= 400:
            # ★ 401 도 **본문을 읽고 나서** 판단한다. 예전엔 본문 없이 '세션 만료' 로 단정했는데,
            #   첨부 업로드의 401 은 XSRF 거절일 때도 있어(Jira 는 이유를 본문에 적는다)
            #   화면에는 '401 저장 실패' 만 뜨고 진짜 이유는 아무 데도 안 남았다.
            try:
                body = resp.text()
            except Exception:
                body = ""
            hdrs = ""
            try:
                # Jira 는 XSRF 거절을 이 헤더로도 알린다(본문이 HTML 이라 안 읽힐 때가 있다).
                xa = resp.headers.get("x-authentication-denied-reason") or ""
                if xa:
                    hdrs = f" [{xa}]"
            except Exception:
                pass
            print(f"[upload] HTTP {resp.status} {path}{hdrs} :: {(body or '')[:300]}",
                  file=sys.stderr, flush=True)
            if resp.status == 401:
                raise SessionExpired(
                    f"HTTP 401 on {path}{hdrs} — {(body or '세션 만료 가능. login 재실행.')[:200]}")
            if resp.status >= 500:
                raise UpstreamUnavailable(
                    f"HTTP {resp.status} on {path} — {(body or 'Jira 서버 응답 오류')[:200]}")
            raise UpstreamError(resp.status, path, body)
        if not want_json:
            return resp.status
        try:
            return resp.json()
        except Exception:
            return {}                                    # 204 No Content 등

    # 쓰기는 **무조건 큐 맨 앞**이다. 읽기가 앞에 쌓여 있으면 그만큼 늦어지고, 늦어지다
    # 타임아웃에 걸리면 사용자가 쓴 글이 그대로 사라진다(스레드 로컬 우선순위와 무관하게 고정).
    def post_json(self, path, json_body=None, params=None):
        return self._submit(
            lambda: self._write("post", path, json_body, params), PRIO_WRITE,
            may_commit=True)

    def put_json(self, path, json_body=None, params=None):
        return self._submit(
            lambda: self._write("put", path, json_body, params), PRIO_WRITE,
            may_commit=True)

    def delete(self, path, params=None):
        return self._submit(lambda: self._write("delete", path, None, params, want_json=False),
                            PRIO_WRITE, may_commit=True)

    def _write_multipart(self, path, field, filename, data, content_type):
        """멀티파트 단일 파일 업로드 — Playwright context.request.post(multipart=...).
        multipart 는 필드명→{name,mimeType,buffer} dict. XSRF 는 Content-Type 없는 헤더로."""
        url = path if path.startswith(("http://", "https://")) else self.base + path
        body = {field: {"name": filename,
                        "mimeType": content_type or "application/octet-stream",
                        "buffer": data}}
        # ★ Jira 의 **멀티파트** XSRF 는 헤더가 아니라 **쿼리 파라미터 atl_token** 을 본다.
        #   업로드는 파일 본문을 다 읽기 전에 통과 여부를 정해야 해서, 폼 필드가 아닌 URL 에서
        #   토큰을 찾는다. 그래서 JSON 쓰기(코멘트 등)는 멀쩡한데 첨부만 'XSRF check failed' 였다.
        #   토큰 쿠키가 없으면(비브라우저 세션) 파라미터 없이 no-check 만으로 간다.
        tok = self._xsrf_cookie(url)
        params = {"atl_token": tok} if tok else {}
        # ★ Playwright 의 request 타임아웃 기본값은 **30초**다. 12MB 짜리 첨부가 사내망에서
        #   그 안에 안 올라가 'Timeout 30000ms exceeded' 로 죽었다(리포트된 버그).
        #   업로드 시간은 파일 크기에 비례하므로 크기로 정한다 — 최소 2분, 1MB 당 +20초,
        #   상한 15분. 큐 대기(JOB_TIMEOUT)와는 다른 층이라 그쪽도 함께 늘려야 의미가 있다.
        up_timeout = self._upload_timeout_ms(len(data or b""))
        resp = self._context.request.post(url, multipart=body, params=params, timeout=up_timeout,
                                          headers=self._xsrf_headers(url, multipart=True))
        if resp.status in (401, 403, 404):
            # 한 번은 **순수 no-check 로만** 다시 던져 본다.
            # 우리 기본 헤더는 브라우저처럼 보이게 꾸민다(Origin/Referer/XHR + 쿠키의 xsrf 토큰 echo).
            # Bitbucket 검색이 그래야 통과해서 그렇게 맞춰 뒀는데, Jira 의 첨부 업로드는 반대로
            # **비브라우저 클라이언트**로 보일 때(no-check 만 있을 때) 통과하는 구성이 있다.
            # 둘 중 무엇이 맞는지는 인스턴스 설정이 정하므로, 실패했을 때만 다른 쪽으로 한 번 더 본다.
            from .base import MULTIPART_HEADERS
            print(f"[upload] {resp.status} — no-check 단독 헤더로 1회 재시도: {path}",
                  file=sys.stderr, flush=True)
            resp = self._context.request.post(url, multipart=body, timeout=up_timeout,
                                              headers=dict(MULTIPART_HEADERS))
        if resp.status >= 400:
            # ★ 401 도 **본문을 읽고 나서** 판단한다. 예전엔 본문 없이 '세션 만료' 로 단정했는데,
            #   첨부 업로드의 401 은 XSRF 거절일 때도 있어(Jira 는 이유를 본문에 적는다)
            #   화면에는 '401 저장 실패' 만 뜨고 진짜 이유는 아무 데도 안 남았다.
            try:
                body = resp.text()
            except Exception:
                body = ""
            hdrs = ""
            try:
                # Jira 는 XSRF 거절을 이 헤더로도 알린다(본문이 HTML 이라 안 읽힐 때가 있다).
                xa = resp.headers.get("x-authentication-denied-reason") or ""
                if xa:
                    hdrs = f" [{xa}]"
            except Exception:
                pass
            print(f"[upload] HTTP {resp.status} {path}{hdrs} :: {(body or '')[:300]}",
                  file=sys.stderr, flush=True)
            if resp.status == 401:
                raise SessionExpired(
                    f"HTTP 401 on {path}{hdrs} — {(body or '세션 만료 가능. login 재실행.')[:200]}")
            if resp.status >= 500:
                raise UpstreamUnavailable(
                    f"HTTP {resp.status} on {path} — {(body or 'Jira 서버 응답 오류')[:200]}")
            raise UpstreamError(resp.status, path, body)
        try:
            return resp.json()
        except Exception:
            return {}

    @staticmethod
    def _upload_timeout_ms(size_bytes):
        """업로드 타임아웃(ms) — 크기에 비례. 최소 2분, 1MB 당 +20초, 상한 15분.
        (사내망 업로드는 느리다: 12MB 가 30초를 넘겨 기본값에 걸렸다.)"""
        mb = max(0, int(size_bytes or 0)) / (1024 * 1024)
        return int(min(15 * 60_000, 120_000 + mb * 20_000))

    def post_multipart(self, path, filename, data, content_type=None, field="file", params=None):
        # params 는 첨부 업로드에선 쓰지 않으나 인터페이스 통일용으로 받는다.
        # 큐 대기 한도(JOB_TIMEOUT)도 업로드에 맞춰 늘린다 — 요청 자체는 15분까지 기다릴 수
        # 있는데 큐가 180초에 끊어 버리면 결국 같은 실패가 된다(층이 둘이라 둘 다 맞춰야 한다).
        return self._submit(lambda: self._write_multipart(path, field, filename, data, content_type),
                            PRIO_WRITE,
                            wait=self._upload_timeout_ms(len(data or b"")) / 1000 + 30,
                            may_commit=True)

    def get_text(self, path, params=None):
        return self._submit(lambda: self._fetch(path, params, True))

    def _fetch_bytes(self, path, params):
        # 이미지/첨부 프록시 — 인증된 브라우저 컨텍스트로 받아 바이트 반환. 절대 URL 도 허용.
        url = path if path.startswith(("http://", "https://")) else self.base + path
        resp = self._context.request.get(url, params=params or {}, timeout=self.REQUEST_TIMEOUT_MS)
        if resp.status in (401, 403):
            self._raise_read_access_error(resp, path, url, label="GET(bytes)")
        if resp.status >= 500:
            raise UpstreamUnavailable(f"HTTP {resp.status} on {path} — Jira 서버 응답 오류")
        return resp.body(), resp.headers.get("content-type")

    def _diag_write(self, url):
        """[dev 진단] 이 URL 에 대해 실제로 보낼 XSRF 헤더 + 그 도메인 쿠키 이름들.
        값은 마스킹. atl.xsrf.token 이 있는지, 어떤 X-Atlassian-Token 을 보내는지 확인용."""
        def do():
            names, xsrf_cookie = [], None
            try:
                for c in self._context.cookies(url):
                    nm = c.get("name", "")
                    names.append(nm)
                    if nm in ("atl.xsrf.token", "atlassian.xsrf.token"):
                        xsrf_cookie = nm
            except Exception as e:
                return {"error": f"cookies() 실패: {e}"}
            h = self._xsrf_headers(url)
            token = h.get("X-Atlassian-Token", "")
            return {"url": url, "cookie_names": names,
                    "xsrf_cookie_found": xsrf_cookie or "(없음)",
                    "X-Atlassian-Token 전송값": ("no-check" if token == "no-check"
                                                else f"토큰({len(token)}자)" if token else "(없음)"),
                    "X-Requested-With": h.get("X-Requested-With", "(없음)"),
                    "Origin": h.get("Origin", "(없음)"),
                    "Referer": h.get("Referer", "(없음)")}
        return self._submit(do)

    def get_bytes(self, path, params=None):
        return self._submit(lambda: self._fetch_bytes(path, params))

    def storage_state_snapshot(self):
        """Capture the current rolling cookies on the Playwright owner thread.

        BrowserContext objects are not thread-safe.  The caller may persist the returned plain
        dict, but must generation-fence it against a concurrent login/provider replacement.
        """
        def capture():
            if self._closed.is_set() or self._broken.is_set():
                raise UpstreamUnavailable("SSO provider가 교체되어 세션 상태 저장을 건너뜁니다.")
            return self._context.storage_state()

        return self._submit(capture, PRIO_BACKGROUND)

    def renew_silent(self, targets, save_cb=None):
        """**창을 띄우지 않고** 세션을 되살린다 — 리다이렉트만으로 끝나는 흔한 경우를 위해서다.

        provider 컨텍스트는 **headless** 라, 여기서 실제 페이지를 열어 서비스로 이동하면
        앱→IdP→앱 리다이렉트 체인(JS·meta-refresh 포함)이 화면에 아무것도 안 뜨고 끝난다.
        `context.request.get`(다른 곳의 '조용한 시도')은 **HTTP 30x 만** 따라가 SSO 의
        JS/meta 리다이렉트를 놓치고 자주 실패한다 → 그때마다 사용자에게 창이 떴다.
        이 메서드는 진짜 페이지 네비게이션이라 그 체인을 완주한다. IdP 세션이 아직 살아 있으면
        사람은 아무것도 안 보고 인증이 갱신된다. 로그인 폼이 떠야 하는(사람 입력이 필요한)
        경우에는 갱신이 안 되고 False → 호출부가 그제서야 보이는 창을 연다.

        targets: [(base, [probe_path…]), …]. 하나라도 새로 인증되면 갱신된 storage_state 를
        save_cb(state) 로 넘긴다(디스크 저장은 호출부 몫). 반환: 새로 인증된 게 있으면 True.
        """
        def do():
            page = self._context.new_page()          # headless → 화면에 안 뜬다
            renewed = False
            try:
                for base, paths in (targets or []):
                    b = (base or "").rstrip("/")
                    if not b:
                        continue
                    if service_probe(self._context, b, paths)[0]:
                        continue                     # 이미 살아 있으면 건드리지 않는다
                    try:
                        page.goto(b, wait_until="domcontentloaded")
                        try:
                            page.wait_for_load_state("networkidle", timeout=6000)
                        except Exception:
                            pass                     # 아이들 안 돼도 아래 프로브로 판정
                    except Exception:
                        continue
                    if service_probe(self._context, b, paths)[0]:
                        renewed = True
            finally:
                try:
                    page.close()
                except Exception:
                    pass
            if renewed and save_cb:
                try:
                    save_cb(self._context.storage_state())
                except Exception:
                    pass
            return renewed

        # 사용자 대기를 막지 않게 쓰기(-1) 우선순위로 — 이건 사람이 기다리는 로그인 경로다.
        return self._submit(do, PRIO_WRITE)

    def close(self):
        """퇴역은 즉시 반환한다. 멎은 Playwright 호출 때문에 로그인/종료까지 멎으면 안 된다."""
        try:
            self._closed.set()
            self._mark_broken("SSO provider 가 교체되었습니다.")
            # 현재 실행 중인 호출이 돌아오는 즉시, 남은 작업보다 먼저 루프를 끝낸다.
            self._jobs.put((-999, next(self._seq), None))
            self._thread.join(timeout=0.2)
        except Exception:
            pass


def service_probe(context, base, paths):
    """서비스(Jira/Confluence/Bitbucket) 인증 상태를 진단 — (ok, 이유문자열).

    SSO 쿠키는 **도메인별**이라 서비스마다 따로 로그인해야 한다.
    실패 원인을 구분해 로그로 남긴다(미인증 401 · SSO 리다이렉트 미완료 · 경로 문제).
    """
    b = (base or "").rstrip("/")
    if not b:
        return False, "base 미설정"
    last = "응답 없음"
    for path in paths:
        try:
            resp = context.request.get(b + path)
        except Exception as e:
            last = f"{path}: 요청 실패({e})"
            continue
        st = resp.status
        if st == 200:
            try:
                body = resp.json()
            except Exception:
                last = f"{path}: 200(로그인 페이지 HTML=미인증)"
                continue
            # 응답 형태가 서비스마다 다르다 — 사용자 식별자가 잡히면 인증으로 본다
            name = _extract_user(body)
            if name:
                return True, f"인증됨({name})"
            last = f"{path}: 200 이나 사용자 없음"
        elif st in (401, 403):
            last = f"{path}: {st}(인증 필요)"
        elif st in (301, 302, 303, 307, 308):
            last = f"{path}: {st}(SSO 리다이렉트 미완료)"
        else:
            last = f"{path}: HTTP {st}"
    return False, last


def _extract_user(body):
    """다양한 응답에서 사용자 식별자를 뽑는다(Jira myself · Confluence current · Bitbucket users)."""
    if isinstance(body, dict):
        for k in ("name", "username", "userKey", "accountId", "key", "slug"):
            v = body.get(k)
            if v and v != "anonymous":
                return v
        vals = body.get("values")               # Bitbucket users?limit=1 → {values:[{...}]}
        if isinstance(vals, list) and vals:
            return _extract_user(vals[0])
    return None


def conf_authed(context, base):
    return service_probe(context, base,
                         ["/rest/api/user/current", "/rest/api/user/current.json"])[0]


def _authed(context, base):
    """현재 컨텍스트가 인증됐는지 — /myself 200 + name(비익명) 이면 True."""
    try:
        resp = context.request.get(base + "/rest/api/2/myself")
        if resp.status == 200:
            body = resp.json()
            name = body.get("name") or body.get("key")
            return bool(name) and name != "anonymous"
    except Exception:
        pass
    return False


def login(base, store, service="jira"):
    """[CLI] headed Chromium 으로 수동 SSO 로그인 후 세션 저장 (터미널 Enter 대기)."""
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/")
    with sync_playwright() as p:
        browser = _launch(p, headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base, wait_until="domcontentloaded")
        input(">>> 사내 SSO/인증서 로그인을 끝까지 완료한 뒤, 이 창에서 Enter: ")
        store.save(service, context.storage_state())
        browser.close()
        print(f"세션 저장 완료: {store.path(service)}")


def _login_attempt(p, base, store, service, headless, timeout, poll):
    """브라우저 하나 띄워 로그인 완료(폴링)를 기다린다 → 성공하면 세션 저장하고 True.
    headless=True 면 **창이 안 뜬다** — 사내 인증이 클라이언트 인증서로 자동 처리되는 환경에선
    이걸로 충분해 사용자는 창을 아예 못 본다."""
    import time
    browser = _launch(p, headless=headless)
    try:
        context = browser.new_context()
        page = context.new_page()
        page.goto(base, wait_until="domcontentloaded")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _authed(context, base):
                store.save(service, context.storage_state())
                return True
            time.sleep(poll)
        return False
    finally:
        try:
            browser.close()
        except Exception:
            pass


#: 창 없이(headless) cert 자동 인증을 기다리는 시간. 인증서 자동 선택이면 리다이렉트 몇 초면 끝난다.
#: 이 안에 안 되면 '사람 입력이 필요한 로그인' 으로 보고 보이는 창으로 넘어간다.
HEADLESS_LOGIN_SECS = 35


def login_wait(base, store, service="jira", timeout=300, poll=2.0):
    """[웹/자동] SSO 로그인 완료를 폴링으로 감지해 세션 저장.

    ★ **먼저 창 없이(headless)** 시도한다 — 사내 SSO 가 로컬 인증서로 자동 인증되면 창이
    아예 안 뜬다. 그 안에 안 끝나면(=사람이 직접 입력해야 하면) 그제서야 보이는 창을 연다.
    반환: 성공 True / 타임아웃 False.
    """
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/")
    with sync_playwright() as p:
        # ① 창 없이 — 인증서 자동이면 여기서 끝(사용자는 아무 창도 못 본다).
        if _login_attempt(p, base, store, service, headless=True,
                          timeout=min(HEADLESS_LOGIN_SECS, timeout), poll=poll):
            return True
        # ② 자동으로 안 됐다 → 사람 입력이 필요하니 보이는 창을 연다.
        # 세션 저장은 _login_attempt 안에서 그 서비스 것만 한다(파일 통째 덮어쓰기 방지).
        return _login_attempt(p, base, store, service, headless=False, timeout=timeout, poll=poll)


if __name__ == "__main__":
    from app.infra.settings import get_settings
    s = get_settings()
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        from .sso_store import SsoStore
        login(s.jira_base, SsoStore(s.jira_state_path, {
            "jira": s.jira_base, "confluence": s.confluence_base, "bitbucket": s.bitbucket_base}))
    else:
        print("사용: python -m app.auth.sso_session login")
