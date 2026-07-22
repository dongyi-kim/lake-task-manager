"""
Lake Task Manager — 로컬 실행 런처.

용도:
  1) 개발/일반:   python run.py               (uvicorn + 앱 창 자동 오픈, 창 닫으면 종료)
  2) prod SSO:    python run.py login         (사내 SSO 1회 로그인 후 세션 저장)

앱 창 모드(기본): uvicorn 을 백그라운드 스레드로 띄우고, Playwright Chromium 을 '앱 창'으로
열어 http://localhost:PORT 를 보여준다. **그 창을 닫으면 서버·프로세스·런처(run.bat)가 함께 종료**
→ 강제종료 불필요. (playwright 미설치 또는 LAKE_NO_WINDOW=1 이면 기본 브라우저+수동종료로 폴백)

설정은 exe(또는 repo) 옆 config/jira.yml, 매핑은 config/*.yaml.
"""

import os
import sys
import threading
import time
import urllib.parse

import uvicorn

from app.settings import APP_ROOT, get_settings


# 창을 열자마자(서버 준비 전에도) 즉시 보여줄 부팅 로더 — 외부 자원 없는 self-contained data URL.
# 이렇게 하면 --app 초기 프레임이 '흰 화면 + 타이틀 localhost/' 대신 우리 스피너+제목으로 뜬다.
# 서버가 실제 응답하면 진짜 앱(http://localhost:PORT)으로 goto 해서 교체한다.
_BOOT_HTML = """<!doctype html><html lang=ko><head><meta charset=utf-8><title>Lake Task Manager</title>
<style>html,body{margin:0;height:100%}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;
background:#f9f9f7;color:#0b0b0b;font-family:system-ui,"Segoe UI","Malgun Gothic",sans-serif}
.s{width:34px;height:34px;border-radius:50%;border:3px solid #e1e0d9;border-top-color:#6d4fc0;
animation:r .8s linear infinite}@keyframes r{to{transform:rotate(360deg)}}
.t{font-weight:600;font-size:16px;letter-spacing:-.2px}.u{font-size:12.5px;color:#6b778c}
@media(prefers-color-scheme:dark){body{background:#0f0f0e;color:#f2f2f0}
.s{border-color:#333;border-top-color:#8f6fe0}.u{color:#9aa4b2}}</style></head>
<body><div class=s></div><div class=t>Lake Task Manager</div><div class=u>불러오는 중…</div></body></html>"""
_BOOT_DATA_URL = "data:text/html;charset=utf-8," + urllib.parse.quote(_BOOT_HTML)


def _sso_login(s):
    from app.auth.sso_session import login
    login(s.jira_base, s.jira_state_path)


def _serve_bg(s):
    """uvicorn 을 백그라운드(데몬) 스레드로 기동. **실제 HTTP 응답까지** 확인 후 반환.
    (server.started 는 소켓 accept 보다 살짝 이를 수 있어, 그대로 창을 열면 --app 초기 로드가
     connection-refused → 흰 화면 고착. 실 연결 확인으로 이 레이스를 제거한다.)"""
    import urllib.request
    config = uvicorn.Config("app.main:app", host=s.app_host, port=s.app_port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    probe = f"http://127.0.0.1:{s.app_port}/api/health"
    for _ in range(300):                              # 최대 ~30s
        if getattr(server, "started", False):
            try:
                urllib.request.urlopen(probe, timeout=1).read()
                break                                 # 실제로 응답함 → 창 열어도 안전
            except Exception:
                pass
        time.sleep(0.1)
    return server


def _login_one_service(page, context, name, base, paths, per_timeout, appmain):
    """서비스 하나를 앱 창에서 열어 SSO 로그인시키고 인증될 때까지 기다린다 — (ok, 이유).

    같은 IdP 면 리다이렉트만으로 즉시 인증되고, 아니면 로그인 화면이 떠서 사용자가
    직접 로그인한다. per_timeout 안에 안 되면 실패로 본다.
    """
    from app.auth.sso_session import service_probe
    try:
        # networkidle — SSO 리다이렉트 체인(앱→IdP→앱)이 끝날 때까지 기다린다.
        # domcontentloaded 는 중간 리다이렉트 페이지에서 일찍 끝나 판정이 헛돈다.
        page.goto(base, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass                                          # 아이들 안 돼도 아래 재시도 루프가 커버
    except Exception as e:
        return False, f"페이지 열기 실패({e})"
    landed = ""
    try:
        landed = page.url
    except Exception:
        pass
    print(f"[login]   {name} 창 착지: {landed}")           # IdP 로그인 페이지인지 서비스 홈인지 구분용
    deadline = time.monotonic() + per_timeout
    last = "대기 시간 초과(사용자 로그인 대기)"
    while time.monotonic() < deadline and not page.is_closed():
        ok, why = service_probe(context, base, paths)
        if ok:
            return True, why
        last = why
        page.wait_for_timeout(1500)
    return False, last


def _do_login_in_window(s, page, context, appmain, per_timeout=300):
    """[prod] **별도 임시 창**을 열어 Jira → Confluence → Bitbucket(설정된 것만) SSO 로그인.

    앱 창(page)은 건드리지 않는다 — 로그인 전용 페이지(창)를 새로 열어 거기서 IdP 로그인하고,
    끝나면 그 창만 닫는다. 쿠키/헤더는 context 공유라 로그인 결과가 앱에 그대로 반영된다.
    (init 스크립트·_openExternal 도 context 레벨이라 새 페이지에 자동 적용된다.)

    각 서비스는 도메인이 달라 SSO 쿠키가 따로 필요하다(하나만 로그인하면 나머지는 401).
    셋 중 하나라도 인증이 안 되면 세션은 저장하되 로그인 미완료로 남긴다
    (앱은 '로그인 필요' 를 다시 띄운다 — 부분 인증이라도 되는 기능은 동작).
    이미 인증된 서비스는 창을 열지 않고 통과 → 자동 취득 시 불필요한 창이 안 뜬다.
    """
    from app.auth.sso_session import service_probe
    appmain._login_requested.clear()
    targets = getattr(s, "auth_targets", None) or [("Jira", s.jira_base.rstrip("/"), ["/rest/api/2/myself"])]
    login_page = None
    results = []
    try:
        for name, base, paths in targets:
            # 이미 세션이 살아 있으면(직전 로그인·리다이렉트) 창을 열지 않고 통과
            ok, why = service_probe(context, base, paths)
            if not ok:
                if login_page is None or login_page.is_closed():
                    login_page = context.new_page()      # 로그인 전용 임시 창 (앱 창과 별개)
                ok, why = _login_one_service(login_page, context, name, base, paths, per_timeout, appmain)
            results.append((name, ok))
            print(f"[login] {name}: {'OK' if ok else '실패'} — {why}")
        # 하나라도 됐으면 세션 저장(부분 인증이라도 그 기능은 쓰게), provider 갱신
        if any(ok for _, ok in results):
            context.storage_state(path=appmain._client._state_path())
            appmain._client.reset_provider()
        all_ok = all(ok for _, ok in results)
        print("[login] " + ("전체 완료 — " if all_ok else "일부 미완료 — ")
              + ", ".join(f"{n}={'O' if ok else 'X'}" for n, ok in results))
    except Exception as e:
        print(f"[login] 오류: {e}")
    finally:
        try:
            if login_page is not None and not login_page.is_closed():
                login_page.close()                        # 임시 로그인 창만 닫기 (앱 창은 그대로)
        except Exception:
            pass


# 앱 페이지(localhost)에서 클릭한 외부 링크(Jira 등)를 캡처해 시스템 기본 브라우저로 넘긴다.
# guard: location 이 localhost 일 때만 → SSO 로그인(페이지가 Jira 로 이동한 상태)의 팝업은 건드리지 않음.
_EXT_LINK_JS = r"""
document.addEventListener('click', function(e){
  try {
    var h = location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return;   // 우리 앱 페이지에서의 클릭만
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    var href = a.href || '';
    if (!/^https?:/i.test(href)) return;
    // data-ext = 같은 호스트라도 시스템 브라우저로 (예: 티켓 '새 창에서 열기' → /browse/KEY).
    // 이게 없으면 내부 링크라 앱 창(Chromium)에 새 창으로 떠 버린다.
    if (a.host === location.host && !a.hasAttribute('data-ext')) return;
    if (window._openExternal) { e.preventDefault(); window._openExternal(href); }
  } catch (_) {}
}, true);
"""


def _sys_open(url):
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _wire_external_links(context, page):
    """외부 링크를 새 Chromium 창 대신 시스템 기본 브라우저로 열도록 바인딩+init 스크립트 설치."""
    try:
        context.expose_function("_openExternal", _sys_open)   # 현재 페이지에도 바인딩 적용됨
        context.add_init_script(_EXT_LINK_JS)                 # 이후 네비게이션(SSO 로그인 등)용
        page.evaluate(_EXT_LINK_JS)                           # 현재 페이지에 즉시 주입 — 리로드 없음(흰 화면 방지)
    except Exception:
        pass


def _set_window_icon_win(ico_path):
    """[Windows] 앱 창(제목에 'Lake Task Manager' 포함)의 아이콘을 우리 .ico 로 직접 지정.
    Chromium --app 의 favicon 래스터화(작업표시줄 저화질)보다 확실. best-effort."""
    import sys
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lp):
            n = user32.GetWindowTextLengthW(hwnd)
            if n and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if "Lake Task Manager" in buf.value:
                    found.append(hwnd)
            return True

        user32.EnumWindows(_enum, 0)
        if not found:
            return False
        big = user32.LoadImageW(None, ico_path, IMAGE_ICON, 48, 48, LR_LOADFROMFILE)   # 고DPI 작업표시줄
        small = user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        for hwnd in found:
            if big:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
            if small:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
        return True
    except Exception:
        return False


def _run_app_window(s, headless=False):
    """앱 창(Playwright Chromium, --app 모드=주소창 없는 앱 창)으로 실행. 로그인도 같은 창에서.
    창 닫으면 서버 종료 후 반환. 프로필은 임시폴더(정리)라 실행 폴더(cwd)에 아무것도 안 남긴다."""
    import shutil
    import tempfile
    url = f"http://localhost:{s.app_port}/"
    import app.main as appmain
    appmain._window_login = True                       # /api/login 이 이 창에서 로그인하도록
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="ltm-appwin-")       # 앱 창 전용 임시 프로필 (cwd 오염 방지)
    # 1) 창을 '부팅 로더 data URL' 로 즉시 연다 → 서버 준비 여부와 무관하게 흰 화면/‘localhost/’ 타이틀 없음.
    context = p.chromium.launch_persistent_context(
        user_data_dir=udd,
        headless=headless,
        no_viewport=True,                              # 뷰포트를 실제 창 크기에 추종(리사이즈 시 흰 여백 방지)
        ignore_default_args=["--enable-automation"],   # '자동화 제어 중' 안내바 제거
        args=["--no-first-run", "--no-default-browser-check",
              "--window-size=1400,900", f"--app={_BOOT_DATA_URL}"],  # 앱 모드 + 즉시 부팅로더
    )
    page = context.pages[0] if context.pages else context.wait_for_event("page")
    _wire_external_links(context, page)                # 외부 링크 훅(goto 전 설치 → 실제 페이지에도 적용)
    # 2) 서버가 실제로 응답할 때까지 대기(그동안 창엔 부팅로더가 계속 돎).
    server = _serve_bg(s)
    # 3) 준비된 서버로 실제 앱 이동. 혹시 비면 잠깐 뒤 재시도(부팅로더 유지).
    for _ in range(50):
        try:
            page.goto(url, wait_until="domcontentloaded")
            if page.evaluate("!!(document.querySelector('.app-boot')||document.querySelector('.wrap'))"):
                break
        except Exception:
            pass
        if page.is_closed():
            break
        page.wait_for_timeout(100)
    from app.settings import STATIC_DIR
    ico_path = str(STATIC_DIR / "favicon.ico")
    print(f"Lake Task Manager - {url}  (env={s.jira_env})  [이 창을 닫으면 종료됩니다]")
    # [prod] 시작하면 3개 서비스 SSO 를 자동 취득 — 아래 while 루프가 신호를 받아 임시 창에서 구동.
    # 이미 인증돼 있으면 service_probe 로 스킵돼 창이 안 뜬다(세션 유효 시 무음). 앱 창은 그대로.
    if s.jira_env == "prod":
        appmain._login_requested.set()
    # 창이 닫힐 때까지 대기. 로그인 요청(_login_requested)이 오면 같은 창에서 SSO 구동.
    # 초반 몇 초간 창 아이콘을 우리 .ico 로 재적용(Chromium 이 favicon 으로 덮어쓰는 것 대비).
    try:
        i = 0
        while not page.is_closed():
            if appmain._login_requested.is_set():
                _do_login_in_window(s, page, context, appmain)
            if i < 16:                                  # ~8초 동안 재적용
                _set_window_icon_win(ico_path)
            i += 1
            page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        context.close()
    except Exception:
        pass
    try:
        p.stop()
    except Exception:
        pass
    server.should_exit = True
    shutil.rmtree(udd, ignore_errors=True)             # 임시 프로필 정리


def _run_plain(s):
    """폴백: 기본 브라우저로 열고 서버는 블로킹 실행(종료는 Ctrl+C 또는 창 닫기)."""
    import webbrowser
    url = f"http://localhost:{s.app_port}/"
    print(f"Lake Task Manager - {url}  (env={s.jira_env})  [종료: Ctrl+C]")
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, log_level="info")


def main():
    # Windows 콘솔(cp949)에서도 유니코드 출력이 크래시하지 않도록 utf-8 로 강제.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    s = get_settings()

    # prod SSO 1회 로그인:  python run.py login
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        _sso_login(s)
        return

    if s.jira_env == "prod":
        state = APP_ROOT / s.jira_state_path
        if not state.exists():
            print(f"[prod] SSO 세션이 없습니다({state}). 앱 화면의 'SSO 로그인' 버튼을 누르세요.")

    # 앱 창 모드 (playwright 있고, 끄지 않았으면). 창 닫히면 전체 종료.
    use_window = os.getenv("LAKE_NO_WINDOW") not in ("1", "true", "True")
    if use_window:
        try:
            import playwright.sync_api  # noqa: F401
        except Exception:
            use_window = False
    if use_window:
        _run_app_window(s)
        os._exit(0)          # 남은 스레드까지 확실히 정리하고 즉시 종료(=run.bat 도 함께 끝)
    else:
        _run_plain(s)


if __name__ == "__main__":
    main()
