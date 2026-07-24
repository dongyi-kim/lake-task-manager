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
import sys
import time as _time

# 같은 [auth] 메시지를 초당 여러 번 찍지 않게 — prod 는 상류가 단일 큐라 같은 401 이 연달아 온다.
_auth_seen = {}


def _auth_log(msg):
    now = _time.monotonic()
    if now - _auth_seen.get(msg, 0) < 20.0:
        return
    _auth_seen[msg] = now
    print(msg, file=sys.stderr, flush=True)
import threading

from .base import (AuthProvider, LoginRequired, SessionExpired, UpstreamError,
                   WRITE_HEADERS, PRIO_WRITE, upstream_priority)


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
        self._start_error = None
        self._thread = threading.Thread(target=self._loop, name="playwright-sso", daemon=True)
        self._thread.start()
        self._ready.wait()
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

    # 한 요청이 이보다 오래 걸리면 스레드가 먹통이라고 본다. 넉넉히 두되 **무한 대기는 안 된다** —
    # 예전엔 timeout 이 없어, 죽은 브라우저에서 호출이 멎으면 앱 전체가 응답을 잃었다.
    JOB_TIMEOUT = 180

    def _submit(self, fn, priority=0):
        """fn 을 Playwright 전용 스레드에서 실행하고 결과/예외를 호출자 스레드로 반환.

        priority: -1=쓰기 · 0=사용자 요청(기본) · 1=백그라운드 갱신. 작은 값이 먼저다.
        단일 큐라 백그라운드 작업이 앞에 쌓이면 사용자의 다음 조회가 그만큼 늦어진다
        → 낮은 우선순위로 넣어 사용자 요청이 항상 앞지르게 한다. 쓰기는 그보다도 앞이다.
        """
        if not self._thread.is_alive():
            raise SessionExpired("SSO provider 스레드가 종료됨 — login 재실행 필요.")
        done = threading.Event()
        box = [None, None]   # [result, error]
        self._jobs.put((priority, next(self._seq), (fn, done, box)))
        if not done.wait(self.JOB_TIMEOUT):
            raise SessionExpired(
                "Jira 응답이 없습니다(%ds 초과). 절전 후 세션이 끊겼을 수 있습니다 — "
                "[SSO 로그인] 을 다시 실행하세요." % self.JOB_TIMEOUT)
        if box[1] is not None:
            raise box[1]
        return box[0]

    def _fetch(self, path, params, as_text, quiet=False):
        # Playwright 스레드에서 실행 — body 추출(json()/text())도 반드시 이 스레드에서.
        # path 가 절대 URL(http…)이면 그대로(Confluence 등 별도 호스트), 아니면 jira base + path.
        url = path if path.startswith(("http://", "https://")) else self.base + path
        resp = self._context.request.get(url, params=params or {})
        if resp.status in (401, 403) or resp.status >= 500:
            # ★ **무엇이** 401 인지 찍는다. '인증 계속 풀림' 이 어느 요청에서 시작되는지
            #   여기 없이는 알 수 없다(401 은 세션 만료·XSRF·권한이 다 같은 코드로 온다).
            reason = ""
            try:
                reason = resp.headers.get("x-authentication-denied-reason") or ""
            except Exception:
                pass
            # quiet=상태 확인(트레이·설정창 주기 프로브). 401 은 오류가 아니라 '아직 미인증'
            # 이라는 **정상 응답**이라, 매분 로그를 남기면 진짜 문제의 로그가 파묻힌다.
            if not quiet:
                _auth_log(f"[auth] GET {resp.status} {path}" + (f" [{reason}]" if reason else ""))
            raise SessionExpired(f"HTTP {resp.status} on {path} — 세션 만료 가능. login 재실행.")
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
        return self._submit(lambda: self._write("post", path, json_body, params), PRIO_WRITE)

    def put_json(self, path, json_body=None, params=None):
        return self._submit(lambda: self._write("put", path, json_body, params), PRIO_WRITE)

    def delete(self, path, params=None):
        return self._submit(lambda: self._write("delete", path, None, params, want_json=False),
                            PRIO_WRITE)

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
        resp = self._context.request.post(url, multipart=body, params=params,
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
            resp = self._context.request.post(url, multipart=body,
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
            raise UpstreamError(resp.status, path, body)
        try:
            return resp.json()
        except Exception:
            return {}

    def post_multipart(self, path, filename, data, content_type=None, field="file", params=None):
        # params 는 첨부 업로드에선 쓰지 않으나 인터페이스 통일용으로 받는다.
        return self._submit(lambda: self._write_multipart(path, field, filename, data, content_type),
                            PRIO_WRITE)

    def get_text(self, path, params=None):
        return self._submit(lambda: self._fetch(path, params, True))

    def _fetch_bytes(self, path, params):
        # 이미지/첨부 프록시 — 인증된 브라우저 컨텍스트로 받아 바이트 반환. 절대 URL 도 허용.
        url = path if path.startswith(("http://", "https://")) else self.base + path
        resp = self._context.request.get(url, params=params or {})
        if resp.status in (401, 403) or resp.status >= 500:
            _auth_log(f"[auth] GET(bytes) {resp.status} {path}")
            raise SessionExpired(f"HTTP {resp.status} on {path} — 세션 만료 가능. login 재실행.")
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
                            page.wait_for_load_state("networkidle", timeout=12000)
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
        try:
            self._jobs.put((99, next(self._seq), None))
            self._thread.join(timeout=10)
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
    from ..settings import get_settings
    s = get_settings()
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        from .sso_store import SsoStore
        login(s.jira_base, SsoStore(s.jira_state_path, {
            "jira": s.jira_base, "confluence": s.confluence_base, "bitbucket": s.bitbucket_base}))
    else:
        print("사용: python -m app.auth.sso_session login")
