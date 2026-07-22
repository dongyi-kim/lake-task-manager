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
import threading

from .base import (AuthProvider, LoginRequired, SessionExpired, UpstreamError,
                   WRITE_HEADERS, upstream_priority)


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

    def __init__(self, base, state_path, user_agent=None):
        self.base = base.rstrip("/")
        # 세션 파일이 없으면 브라우저를 띄우기 전에 명확히 실패 → 라우트가 needLogin 으로 안내.
        if not state_path or not os.path.exists(state_path):
            raise LoginRequired(f"SSO 세션 파일이 없습니다({state_path}). 최초 로그인이 필요합니다.")
        self._state_path = state_path
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
            ctx_kw = {"storage_state": self._state_path}
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
                box[0] = fn()
            except BaseException as e:   # noqa: BLE001 - 호출자 스레드로 재전달
                box[1] = e
            done.set()
        try:
            self._browser.close()
            self._p.stop()
        except Exception:
            pass

    def _submit(self, fn, priority=0):
        """fn 을 Playwright 전용 스레드에서 실행하고 결과/예외를 호출자 스레드로 반환.

        priority: 0=사용자 요청(기본) · 1=백그라운드 갱신.
        단일 큐라 백그라운드 작업이 앞에 쌓이면 사용자의 다음 조회가 그만큼 늦어진다
        → 낮은 우선순위로 넣어 사용자 요청이 항상 앞지르게 한다.
        """
        if not self._thread.is_alive():
            raise SessionExpired("SSO provider 스레드가 종료됨 — login 재실행 필요.")
        done = threading.Event()
        box = [None, None]   # [result, error]
        self._jobs.put((priority, next(self._seq), (fn, done, box)))
        done.wait()
        if box[1] is not None:
            raise box[1]
        return box[0]

    def _fetch(self, path, params, as_text):
        # Playwright 스레드에서 실행 — body 추출(json()/text())도 반드시 이 스레드에서.
        # path 가 절대 URL(http…)이면 그대로(Confluence 등 별도 호스트), 아니면 jira base + path.
        url = path if path.startswith(("http://", "https://")) else self.base + path
        resp = self._context.request.get(url, params=params or {})
        if resp.status in (401, 403) or resp.status >= 500:
            raise SessionExpired(f"HTTP {resp.status} on {path} — 세션 만료 가능. login 재실행.")
        return resp.text() if as_text else resp.json()

    def get_json(self, path, params=None, priority=None):
        if priority is None:
            priority = upstream_priority()      # 백그라운드 갱신은 사용자 요청 뒤로
        return self._submit(lambda: self._fetch(path, params, False), priority)

    def _write(self, method, path, json_body, params, want_json=True):
        """쓰기(POST/PUT/DELETE) 공통 — XSRF 헤더(WRITE_HEADERS) + JSON 명시 직렬화 + 진단 예외.
        편집 기능이 전부 이 경로를 탄다. 제품(Jira/Confluence/Bitbucket) 구분 없이 동일."""
        import json as _json
        url = path if path.startswith(("http://", "https://")) else self.base + path
        fn = getattr(self._context.request, method)     # post/put/delete
        kw = {"params": params or {}, "headers": WRITE_HEADERS}
        if json_body is not None:
            kw["data"] = _json.dumps(json_body)          # data=dict 인코딩 어긋남 방지 → 명시 직렬화
        resp = fn(url, **kw)
        if resp.status == 401:
            raise SessionExpired(f"HTTP 401 on {path} — 세션 만료. login 재실행.")
        if resp.status >= 400:
            try:
                body = resp.text()
            except Exception:
                body = ""
            raise UpstreamError(resp.status, path, body)
        if not want_json:
            return resp.status
        try:
            return resp.json()
        except Exception:
            return {}                                    # 204 No Content 등

    def post_json(self, path, json_body=None, params=None):
        return self._submit(lambda: self._write("post", path, json_body, params))

    def put_json(self, path, json_body=None, params=None):
        return self._submit(lambda: self._write("put", path, json_body, params))

    def delete(self, path, params=None):
        return self._submit(lambda: self._write("delete", path, None, params, want_json=False))

    def get_text(self, path, params=None):
        return self._submit(lambda: self._fetch(path, params, True))

    def _fetch_bytes(self, path, params):
        # 이미지/첨부 프록시 — 인증된 브라우저 컨텍스트로 받아 바이트 반환. 절대 URL 도 허용.
        url = path if path.startswith(("http://", "https://")) else self.base + path
        resp = self._context.request.get(url, params=params or {})
        if resp.status in (401, 403) or resp.status >= 500:
            raise SessionExpired(f"HTTP {resp.status} on {path} — 세션 만료 가능. login 재실행.")
        return resp.body(), resp.headers.get("content-type")

    def get_bytes(self, path, params=None):
        return self._submit(lambda: self._fetch_bytes(path, params))

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


def login(base, state_path):
    """[CLI] headed Chromium 으로 수동 SSO 로그인 후 세션 저장 (터미널 Enter 대기)."""
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/")
    with sync_playwright() as p:
        browser = _launch(p, headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base, wait_until="domcontentloaded")
        input(">>> 사내 SSO/인증서 로그인을 끝까지 완료한 뒤, 이 창에서 Enter: ")
        context.storage_state(path=state_path)
        browser.close()
        print(f"세션 저장 완료: {state_path}")


def login_wait(base, state_path, timeout=300, poll=2.0):
    """[웹/자동] headed Chromium 을 띄우고, 로그인 완료를 폴링으로 감지해 세션 저장.

    터미널 Enter 없이 동작 → 웹 버튼(/api/login)에서 호출 가능.
    사용자가 브라우저에서 SSO 를 끝내면 /myself 200 을 감지하고 storage_state 저장 후 닫는다.
    반환: 성공 True / 타임아웃 False.
    """
    import time
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/")
    with sync_playwright() as p:
        browser = _launch(p, headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base, wait_until="domcontentloaded")
        deadline = time.monotonic() + timeout
        ok = False
        while time.monotonic() < deadline:
            if _authed(context, base):
                ok = True
                break
            time.sleep(poll)
        if ok:
            context.storage_state(path=state_path)
        browser.close()
        return ok


if __name__ == "__main__":
    from ..settings import get_settings
    s = get_settings()
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login(s.jira_base, s.jira_state_path)
    else:
        print("사용: python -m app.auth.sso_session login")
