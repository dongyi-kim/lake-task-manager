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
import urllib.request
from pathlib import Path

import uvicorn

from app.infra.settings import APP_ROOT, get_settings


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


def _serve_bg(s, wait=True):
    """uvicorn 을 백그라운드(데몬) 스레드로 기동. wait=True 면 **실제 HTTP 응답까지** 확인 후 반환.
    (server.started 는 소켓 accept 보다 살짝 이를 수 있어, 그대로 창을 열면 --app 초기 로드가
     connection-refused → 흰 화면 고착. 실 연결 확인으로 이 레이스를 제거한다.)
    wait=False 면 즉시 반환(창을 먼저 띄우고 창의 goto 재시도가 준비를 커버 — 트레이/윈도 공용)."""
    import urllib.request
    config = uvicorn.Config("app.main:app", host=s.app_host, port=s.app_port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    if not wait:
        return server
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


def _wait_port_free(port, timeout=12):
    """포트가 빌 때까지(=직전 인스턴스가 완전히 종료) 잠깐 기다린다. 업데이트 후 재기동 전용."""
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sk = socket.socket()
        sk.settimeout(0.4)
        try:
            rc = sk.connect_ex(("127.0.0.1", int(port)))
        except Exception:
            rc = 1
        finally:
            sk.close()
        if rc != 0:
            return True          # 연결 실패 = 리스너 없음 = 포트 비었음
        time.sleep(0.3)
    return False


def _disk_rev():
    """디스크의 현재 앱 코드 커밋(짧은 해시) — run.bat 이 방금 최신으로 당겨 둔 값.
    못 읽으면 "" (그럼 강제 재시작 판정을 안 한다 = 안전측)."""
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent            # run.py = 앱 코드(submodule) 루트
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=3).decode().strip()
    except Exception:
        return ""


def _running_rev(base):
    """떠 있는 인스턴스가 **기동 시점에 박아둔** 코드 커밋. 못 읽으면 ""."""
    import json
    import urllib.request
    try:
        body = urllib.request.urlopen(base + "/api/app/rev", timeout=2).read()
        return ((json.loads(body) or {}).get("rev") or "").strip()
    except Exception:
        return ""


def _quit_existing(base, port):
    """떠 있는 (옛) 인스턴스를 **조용히 종료**시키고 포트가 풀릴 때까지 기다린다.
    트레이 [종료]와 같은 경로(깨끗한 exit 0)라 그 인스턴스의 run.bat 콘솔도 프롬프트 없이 닫힌다
    (Ctrl+C 종료가 아니라 정상 종료 → '일괄 작업을 끝내시겠습니까' 안 뜬다).
    종료가 시작돼 포트가 풀리면 True, 스스로 못 끄면(트레이 모드 아님 등) False."""
    import json
    import urllib.request
    try:
        req = urllib.request.Request(base + "/api/app/quit", data=b"", method="POST")
        body = urllib.request.urlopen(req, timeout=8).read()
        action = (json.loads(body or b"{}") or {}).get("action")
    except Exception:
        action = None
    if action != "quit":
        return False                       # 옛 인스턴스가 스스로 못 끈다 → 강제하지 않음(폴백)
    return _wait_port_free(port)            # 포트가 풀리면 True


def _signal_existing_instance(s):
    """**단일 인스턴스** — 이미 떠 있는 인스턴스가 있으면:
      · 그게 **옛 버전**(디스크 rev ≠ 실행 rev)이면 → 조용히 종료시키고 **False**(내가 최신으로 이어받음)
      · 최신이면 → 창 포커스/열기 시키고 **True**(내가 안 뜸)
    아무도 없으면(연결 실패) False → 이 프로세스가 정상 기동한다.
    """
    import json
    import urllib.request
    base = f"http://127.0.0.1:{s.app_port}"
    try:
        urllib.request.urlopen(base + "/api/health", timeout=1.5).read()
    except Exception:
        return False                                  # 아무도 없다 → 내가 뜬다
    # 떠 있는 게 옛 버전이면(run.bat 이 방금 최신으로 당겼는데 실행 중인 건 옛 코드) 자동 재시작.
    disk, running = _disk_rev(), _running_rev(base)
    if disk and running and disk != running:
        print(f"실행 중인 앱이 옛 버전입니다(실행 {running} ≠ 최신 {disk}) — 종료 후 최신으로 다시 시작합니다.")
        if _quit_existing(base, s.app_port):
            return False                              # 옛 인스턴스 종료됨 → 내가 최신으로 기동(수동 확인 불필요)
        print("(옛 인스턴스를 자동 종료할 수 없어 기존 창을 사용합니다.)")   # 폴백
    # 최신(또는 rev 미확정/폴백) — 기존 동작: 창 포커스/열기
    action = None
    try:
        req = urllib.request.Request(base + "/api/app/open", data=b"", method="POST")
        body = urllib.request.urlopen(req, timeout=8).read()
        if body:
            action = (json.loads(body) or {}).get("action")
    except Exception:
        pass
    if action == "focus":
        print("Lake Task Manager 가 이미 실행 중입니다 — 기존 창을 앞으로 가져왔습니다.")
    elif action == "open":
        print("Lake Task Manager 백엔드가 실행 중입니다 — 새 앱 창을 엽니다.")
    else:
        print(f"Lake Task Manager 가 이미 실행 중입니다: {base}")
    return True


def _warm_session_bg(s):
    """[prod] 서버가 준비되면 **백그라운드로** SSO 세션을 미리 데운다 — 앱 창(Chromium) 기동과 **병렬**.

    인증 provider 는 첫 인증 요청 때 헤드리스 Chromium 을 띄우는데, 지금은 그게 SPA 의 첫 데이터
    조회 시점(창이 뜬 뒤)에야 시작돼 첫 화면이 그만큼 늦다. 부팅 직후 여기서 미리 띄워 두면
    창이 뜨자마자 데이터가 즉답한다(세션이 살아 있는 재시작에서 특히 효과). 실패는 무해 —
    세션이 없거나 죽었으면 기존 창 SSO 흐름이 그대로 처리한다."""
    if s.jira_env != "prod":
        return

    def run():
        probe = f"http://127.0.0.1:{s.app_port}/api/health"
        for _ in range(80):                     # 서버 준비 대기(~20s)
            try:
                if urllib.request.urlopen(probe, timeout=2).status == 200:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        try:
            import app.main as appmain
            appmain._client.warm_session()      # provider(헤드리스 Chromium)+세션 미리 로드/검증
        except Exception:
            pass
    threading.Thread(target=run, name="sso-warm", daemon=True).start()


def _silent_sso(context, base, paths):
    """**창 없이** 인증을 시도한다 — 리다이렉트만으로 끝나는 경우를 위해서다.

    같은 IdP 로 이미 로그인돼 있으면 서비스 첫 페이지를 한 번 받는 것만으로 세션이 선다
    (앱→IdP→앱 리다이렉트 체인이 자동으로 끝난다). 그때까지 창을 띄우면, 사람이 할 일이
    하나도 없는데 브라우저가 떴다 사라지는 것을 보게 된다.
    사람이 직접 입력해야 하는 경우에만 창을 연다(아래 _login_one_service).
    """
    from app.auth.sso_session import service_probe
    try:
        # request 는 창이 아니라 컨텍스트의 네트워크 스택을 쓴다 — 쿠키는 공유되고 화면엔 안 뜬다.
        context.request.get(base, timeout=20000)
    except Exception:
        return False, "조용한 시도 실패"
    return service_probe(context, base, paths)


def _silent_renew_via_provider(appmain, base, paths):
    """headless provider 로 **창 없이** 세션 갱신을 시도한다 — 리다이렉트만으로 끝나면 창이 안 뜬다.

    _silent_sso 는 `context.request.get`(HTTP 30x 만) 이라 SSO 의 JS/meta 리다이렉트를 못 따라가
    자주 헛돈다. provider 는 headless 컨텍스트라 거기서 진짜 페이지를 열면 리다이렉트 체인을
    끝까지 태우면서도 화면엔 아무것도 안 뜬다. 성공하면 provider 가 갱신된 세션을 저장·보유하므로
    이후 REST 는 바로 정상 응답이 온다. 사람이 입력해야 하는 경우엔 False → 호출부가 창을 연다."""
    try:
        prov = appmain._client.provider          # 살아 있는 headless provider (없으면 LoginRequired)
    except Exception:
        return False
    fn = getattr(prov, "renew_silent", None)
    if not fn:
        return False
    try:
        store = appmain._client.sso_store()
        return bool(fn([(base, paths)], save_cb=store.save_all_from))
    except Exception:
        return False


def _headless_cert_login(p, appmain, name, base, paths, timeout=10):
    """**창 없이**(headless) cert 자동 인증을 시도한다 — 첫 로그인 포함(provider 없어도 됨).

    사내 SSO 가 로컬 클라이언트 인증서로 자동 처리되면, id/pw 창을 띄울 이유가 없다. 별도
    headless 브라우저를 앱 창과 같은 Playwright(p)로 띄워 서비스로 이동하고, 인증이 서면
    (service_probe 200) storage_state 를 저장한다. 시간 안에 안 되면(사람 입력 필요) False →
    호출부가 그제서야 보이는 창을 연다. 기존 세션이 있으면 쿠키를 실어 리다이렉트만으로도 끝난다."""
    from app.auth.sso_session import service_probe
    browser = None
    try:
        browser = p.chromium.launch(headless=True)
        store = appmain._client.sso_store()
        state = store.merged() if store.any_exists() else None
        ctx = browser.new_context(storage_state=state) if state else browser.new_context()
        pg = ctx.new_page()
        pg.goto(base, wait_until="domcontentloaded")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if service_probe(ctx, base, paths)[0]:
                store.save_all_from(ctx.storage_state())
                appmain._client.reset_provider()
                print(f"[login]   {name} 창 없이 인증됨(cert 자동)")
                return True
            pg.wait_for_timeout(1000)
        return False
    except Exception as e:
        print(f"[login]   {name} headless 로그인 시도 실패(창으로 폴백): {e}")
        return False
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass


def _poke_auth_changed(page):
    """앱 페이지에 '인증 상태가 바뀌었다' 를 즉시 알린다 — 설정창·상단 알림이 다음 폴링(4초)을
    기다리지 않고 **바로** 다시 확인하게. (앱 창 모드에서만 page 가 우리 앱이라 의미가 있다.)"""
    try:
        page.evaluate("() => window.dispatchEvent(new CustomEvent('auth-ok'))")
    except Exception:
        pass


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


def _do_login_in_window(s, page, context, appmain, per_timeout=300, only_primary=False, p=None):
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
    if only_primary:
        # ★ **앱을 켤 때는 Jira 만 인증한다.** 예전엔 Jira·Confluence·Bitbucket 을 차례로 돌았는데,
        #   서비스마다 창을 띄우고 최대 5분씩 기다리는 동안 로그인 창이 화면에 남고, 세션 저장은
        #   전부 끝난 뒤라 그 사이 앱의 인증 표시가 됐다 안 됐다 했다. 앱이 뜨자마자 필요한 것은
        #   Jira 하나뿐이다 — 나머지는 트레이의 [SSO 인증] 에서 필요할 때 한다.
        targets = targets[:1]
    login_page = None
    results = []
    try:
        for name, base, paths in targets:
            # 이미 세션이 살아 있으면(직전 로그인·리다이렉트) 창을 열지 않고 통과
            ok, why = service_probe(context, base, paths)
            silent_ok = False
            if not ok:
                # ① 먼저 **창 없이** — headless provider 로 리다이렉트 체인을 완주시킨다.
                #    IdP 세션이 살아 있으면 사용자는 창을 아예 못 본다(대부분의 '계속 뜨는' 창이 이 경우).
                if _silent_renew_via_provider(appmain, base, paths):
                    ok = True
                    silent_ok = True                      # provider 가 이미 저장·보유 → 아래서 또 저장 안 함
                    print(f"[login]   {name} 창 없이 인증됨(리다이렉트)")
            if not ok and p is not None:
                # ② 그래도 안 되면(첫 로그인 등) **창 없이 cert 자동 인증**을 시도한다.
                #    사내 인증이 로컬 인증서로 자동 처리되면 여기서 끝 — id/pw 창이 안 뜬다.
                if _headless_cert_login(p, appmain, name, base, paths):
                    ok = True
                    silent_ok = True
            if not ok:
                # ③ 사람이 직접 입력해야 하는 경우에만 보이는 창을 연다.
                if login_page is None or login_page.is_closed():
                    login_page = context.new_page()      # 로그인 전용 임시 창 (앱 창과 별개)
                ok, why = _login_one_service(login_page, context, name, base, paths, per_timeout, appmain)
            results.append((name, ok))
            print(f"[login] {name}: {'OK' if ok else '실패'} — {why}")
            # 되는 대로 **바로** 저장·반영한다. 전부 끝난 뒤에 저장하면 그 사이 앱은 여전히
            # 미인증으로 보이고, 사용자는 '됐다는데 안 됐다' 를 겪는다.
            if ok:
                if not silent_ok:
                    # 창 로그인으로 붙은 경우만 앱 창(headed) 세션을 저장·반영한다.
                    # (창 없이 갱신됐으면 provider 가 이미 자기 세션을 저장·보유하고 있다.)
                    try:
                        appmain._client.sso_store().save_all_from(context.storage_state())
                        appmain._client.reset_provider()
                    except Exception as e:
                        print(f"[login]   세션 저장 실패: {e}")
                # 인증이 방금 섰다 → 설정창·상단 알림이 **바로** 다시 확인하게 앱 페이지를 깨운다.
                _poke_auth_changed(page)
                # 이 서비스 로그인 창은 여기서 닫는다 — 다음 서비스를 기다리는 동안 떠 있을 이유가 없다.
                try:
                    if login_page is not None and not login_page.is_closed():
                        login_page.close()
                        login_page = None
                except Exception:
                    pass
        # 저장·반영은 위에서 서비스마다 이미 했다.
        # ★ **앱 페이지를 새로고침하지 않는다.** 인증이 잠깐 끊겼다 붙을 때마다 새로고침하면
        #   보던 티켓 창·쓰던 글·스크롤이 전부 날아간다(사용자가 겪은 '홈으로 돌아가고 새로 뜨는'
        #   그것이다). 세션은 이미 provider 에 반영됐고, 화면의 인증 표시는 상단 알림이
        #   주기적으로 확인해 스스로 바뀐다.
        all_ok = all(ok for _, ok in results)
        print("[login] " + ("전체 완료 — " if all_ok else "일부 미완료 — ")
              + ", ".join(f"{n}={'O' if ok else 'X'}" for n, ok in results))
    except Exception as e:
        print(f"[login] 오류: {e}")
    finally:
        # 임시 로그인 창 + SSO 과정에서 뜬 팝업까지 전부 닫는다(앱 창 page 만 남긴다).
        # (IdP 가 새 창을 띄우면 login_page.close() 만으론 안 닫혀 'Jira 창이 계속 떠 있음' 이 된다.)
        try:
            for pg in list(context.pages):
                if pg is not page and not pg.is_closed():
                    pg.close()
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
    // data-external = 같은 호스트라도 시스템 브라우저로 (예: 티켓 '새 창에서 열기' → /browse/KEY).
    // 이게 없으면 내부 링크라 앱 창(Chromium)에 새 창으로 떠 버린다.
    // ★ 이름을 풀어 쓴다 — 예전엔 data-ext 였는데, 파일 확장자(extension)를 같은 이름으로
    //   붙이는 순간 **첨부 링크가 전부 외부 열기로 가로채졌다**. 짧은 이름은 뜻이 둘로 읽힌다.
    if (a.host === location.host && !a.hasAttribute('data-external')) return;
    // 내려받기 링크는 가로채지 않는다 — 브라우저가 저장하게 두고, 저장 결과는 아래
    // _wire_downloads 가 '다운로드' 폴더로 옮겨 알린다.
    if (a.hasAttribute('download')) return;
    // 일반 브라우저(크롬 등)에서는 손대지 않는다 — 새 탭은 브라우저가 더 잘 연다.
    // _openExternal 바인딩이 있다는 건 곧 '여기는 앱 창' 이라는 뜻이다.
    if (!window._openExternal) return;
    e.preventDefault();
    // ★ 서버(/api/open)를 **먼저** 쓴다. expose_function 바인딩은 Playwright 가 이벤트를
    //   넘겨줄 때만 실행돼, 메인 루프가 펌프를 못 도는 순간(로그인 창 대기·다운로드 저장 중 등)
    //   클릭이 그대로 삼켜진다 — '눌러도 아무 반응 없음' 이 그것이다. 서버 경로는 평범한 HTTP 라
    //   그런 타이밍을 타지 않는다. 허용 목록 밖 주소(임의 웹 문서)는 400 이 오므로 바인딩으로 뒤로 뺀다.
    fetch('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                         body: JSON.stringify({ url: href }) })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (!j || !j.ok) throw new Error('not opened'); })
      .catch(function () { try { window._openExternal(href); } catch (_) {} });
  } catch (_) {}
}, true);
"""


def _sys_open(url):
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _downloads_dir():
    """OS 의 '다운로드' 폴더. 없으면 홈. (레지스트리까지 뒤지지 않는다 — 기본 위치면 충분하다)"""
    import pathlib
    d = pathlib.Path.home() / "Downloads"
    return d if d.is_dir() else pathlib.Path.home()


def _wire_downloads(context):
    """앱 창의 다운로드를 받아 둘 큐를 만든다(저장은 _drain_downloads 가 메인 루프에서).

    **왜 앱 창에서만 파일이 이상하게 저장됐나** — 앱 창은 Playwright 가 띄운 Chromium 이고,
    Playwright 는 다운로드를 가로채 자기 임시 폴더에 **GUID 이름(확장자 없음)** 으로 떨궈 둔다.
    원래 이름(suggested_filename)은 이벤트에만 들어 있어서, 우리가 save_as 로 옮겨 줘야
    사용자 폴더에 제 이름으로 남는다. 안 옮기면 창을 닫을 때 그 임시 파일도 지워진다.
    (일반 Chrome 에는 이 가로채기가 없으니 그냥 잘 받아진다 — 그래서 증상이 앱 창에서만 났다.)

    ★ 저장(save_as)을 **이벤트 핸들러 안에서 하면 안 된다.** sync API 에서 핸들러는 드라이버
      스레드에서 불리는데, 거기서 다시 Playwright 호출을 하면 완료되지 않는다(실제로 이벤트는
      왔는데 파일이 안 옮겨졌다). 핸들러는 객체만 담고, 실제 저장은 메인 루프가 한다.
    """
    import queue
    q = queue.Queue()
    context.on("download", lambda dl: q.put(dl))
    return q


def _drain_downloads(context, q):
    """받아 둔 다운로드를 '다운로드' 폴더로 옮기고 결과를 화면에 알린다. 메인 루프에서 호출."""
    while True:
        try:
            dl = q.get_nowait()
        except Exception:
            return
        try:
            name = dl.suggested_filename or "download"
            dest = _downloads_dir() / name
            stem, dot, ext = name.rpartition(".")
            i = 2
            while dest.exists():                       # 조용히 덮어쓰면 아까 받은 파일이 사라진다
                dest = dest.with_name(f"{stem} ({i}).{ext}" if dot else f"{name} ({i})")
                i += 1
            dl.save_as(str(dest))
            _notify_page(context, {"ok": True, "name": dest.name, "path": str(dest)})
        except Exception as e:                         # 실패도 알린다 — 조용한 실패가 제일 나쁘다
            _notify_page(context, {"ok": False, "error": str(e)[:200]})


def _notify_page(context, detail):
    import json
    for pg in list(context.pages):
        try:
            pg.evaluate("(d) => window.dispatchEvent(new CustomEvent('lake-download', {detail: d}))",
                        detail)
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


def _hide_console_win():
    """[Windows] 이 프로세스의 콘솔 창을 숨긴다 — run.bat 더블클릭으로 뜬 콘솔을 앱이 뜬 뒤 치운다.
    '닫는' 게 아니라 숨기는 것(FreeConsole 아님)이라 로그 출력은 계속 유효하고, 프로세스가 끝나면
    숨은 콘솔도 함께 사라진다. 셋업/기동 중 에러는 그 전까지 콘솔에 보이고, 창이 실제로 뜬
    뒤에만 숨긴다(on_ready). best-effort. LAKE_KEEP_CONSOLE=1 이면 안 숨긴다(디버깅용)."""
    if not sys.platform.startswith("win"):
        return
    if os.getenv("LAKE_KEEP_CONSOLE") in ("1", "true", "True"):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:
        pass


# ── 전역 단축키(Windows): Ctrl+Alt+Space → 앱 창을 **지금 보고 있는 모니터**로 불러온다 ──────────
#   '내가 창으로 가는' 게 아니라 '창이 나에게 오는' 동작이다. 포그라운드 창이 있는 모니터의 작업영역
#   중앙으로 앱 창을 옮겨 전면화·포커스한다(다른 모니터·최소화·닫힘 모두 커버). 핸들은 64비트라
#   ctypes restype/argtypes 를 지정해 잘림(truncation)을 막는다. LAKE_NO_HOTKEY=1 로 끌 수 있다.
#   (가상 데스크톱 간 이동은 범위 밖 — 모니터 기준. 대부분의 '다른 화면 보는 중'은 이걸로 해결된다.)
def _cfg_win(u, ctypes, wintypes):
    H, V, D = wintypes.HWND, ctypes.c_void_p, wintypes.DWORD
    for fn, res, args in [
        (u.GetForegroundWindow, H, []),
        (u.MonitorFromWindow, V, [H, D]),
        (u.MonitorFromPoint, V, [wintypes.POINT, D]),
        (u.GetMonitorInfoW, wintypes.BOOL, [V, V]),
        (u.GetWindowTextLengthW, ctypes.c_int, [H]),
        (u.GetWindowTextW, ctypes.c_int, [H, wintypes.LPWSTR, ctypes.c_int]),
        (u.GetClassNameW, ctypes.c_int, [H, wintypes.LPWSTR, ctypes.c_int]),
        (u.IsIconic, wintypes.BOOL, [H]),
        (u.ShowWindow, wintypes.BOOL, [H, ctypes.c_int]),
        (u.GetWindowRect, wintypes.BOOL, [H, V]),
        (u.SetWindowPos, wintypes.BOOL, [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]),
        (u.SetForegroundWindow, wintypes.BOOL, [H]),
        (u.BringWindowToTop, wintypes.BOOL, [H]),
        (u.GetWindowThreadProcessId, D, [H, V]),
        (u.AttachThreadInput, wintypes.BOOL, [D, D, wintypes.BOOL]),
        (u.RegisterHotKey, wintypes.BOOL, [H, ctypes.c_int, wintypes.UINT, wintypes.UINT]),
        (u.UnregisterHotKey, wintypes.BOOL, [H, ctypes.c_int]),
    ]:
        fn.restype = res
        fn.argtypes = args


def _monitor_of(hwnd):
    """hwnd(없으면 커서)가 있는 모니터의 (작업영역 (l,t,r,b), HMONITOR). 실패 시 (None, None)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    _cfg_win(u, ctypes, wintypes)

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]
    MDN = 2                                          # MONITOR_DEFAULTTONEAREST
    if hwnd:
        mon = u.MonitorFromWindow(hwnd, MDN)
    else:
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        mon = u.MonitorFromPoint(pt, MDN)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not u.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return None, None
    w = mi.rcWork
    return (w.left, w.top, w.right, w.bottom), mon


def _find_app_hwnd():
    """Chromium 앱 창(제목에 'Lake Task Manager', 클래스 Chrome_WidgetWin_*) HWND. 없으면 None."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    _cfg_win(u, ctypes, wintypes)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lp):
        n = u.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if "Lake Task Manager" in buf.value:
                cls = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(hwnd, cls, 64)
                if cls.value.startswith("Chrome_WidgetWin"):
                    found.append(hwnd)
        return True
    u.EnumWindows(_enum, 0)
    return found[0] if found else None


def _desktop_diag(hwnd, ref_hwnd):
    """[Windows·읽기전용 진단] 앱 창과 현재(내가 보는 창) 데스크톱을 두 방법으로 비교한다.
    반환 dict {on: IsWindowOnCurrentVirtualDesktop, win: 앱창 데스크톱GUID, cur: 현재 데스크톱GUID,
    same: win==cur}. 멀티데스크톱에서 어떤 판정이 맞는지 로그로 보고 정확히 고치기 위한 것. 이동은 안 한다.
    COM 미지원이면 None."""
    if not (hwnd and ref_hwnd):
        return None
    import ctypes
    from ctypes import POINTER, byref, wintypes
    try:
        ole = ctypes.windll.ole32

        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                        ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

        def mk(d1, d2, d3, tail):
            g = GUID()
            g.d1, g.d2, g.d3 = d1, d2, d3
            for i, b in enumerate(tail):
                g.d4[i] = b
            return g
        CLSID = mk(0xAA509086, 0x5CA9, 0x4C25, (0x8F, 0x95, 0x58, 0x9D, 0x3C, 0x07, 0xB4, 0x8A))
        IID = mk(0xA5CD92FF, 0x29BE, 0x454C, (0x8D, 0x04, 0xD8, 0x28, 0x79, 0xFB, 0x3F, 0x1B))
        try:
            ole.CoInitialize(None)
        except Exception:
            pass
        p = ctypes.c_void_p()
        if ole.CoCreateInstance(byref(CLSID), None, 1, byref(IID), byref(p)) != 0 or not p.value:
            return None
        slot = ctypes.cast(ctypes.cast(p, POINTER(ctypes.c_void_p))[0], POINTER(ctypes.c_void_p))
        WF = ctypes.WINFUNCTYPE
        IsOnCur = WF(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, POINTER(wintypes.BOOL))(slot[3])
        GetId = WF(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, POINTER(GUID))(slot[4])
        MoveTo = WF(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, POINTER(GUID))(slot[5])
        Release = WF(ctypes.c_ulong, ctypes.c_void_p)(slot[2])

        def _gs(g):
            return "%08X-%04X-%04X-%s" % (g.d1, g.d2, g.d3, bytes(g.d4).hex().upper())
        try:
            info = {"on": None, "win": "", "cur": "", "same": None}
            b = wintypes.BOOL()
            if IsOnCur(p, hwnd, byref(b)) == 0:
                info["on"] = bool(b.value)
            gw, gc = GUID(), GUID()
            if GetId(p, hwnd, byref(gw)) == 0:
                info["win"] = _gs(gw)
            if GetId(p, ref_hwnd, byref(gc)) == 0:
                info["cur"] = _gs(gc)
            if info["win"] and info["cur"]:
                info["same"] = info["win"] == info["cur"]
            return info                              # ★ 읽기전용(이동 안 함) — 두 판정법을 함께 본다
        finally:
            try:
                Release(p)
            except Exception:
                pass
    except Exception:
        return None


def _move_window_to_current_desktop(hwnd):
    """[Windows] 앱 창(hwnd)을 **지금 보고 있는 가상 데스크톱**으로 옮긴다.
    내부 IVirtualDesktopManagerInternal::MoveViewToDesktop 을 쓴다 — 공용 MoveWindowToDesktop 은
    교차프로세스(Chromium 창)에 E_ACCESSDENIED 지만 이건 **동작한다**(라이브 검증: Win11 24H2/26100).
    이미 현재 데스크톱이면 no-op. 되면 True. 빌드별 IID·vtable 차이는 **다중 IID 후보 + GetCount 검증
    + 예외처리**로 방어 — 안 되면 False 를 돌려 호출부가 그냥 포커스(기존 동작)로 폴백한다.
    IUnknown/서비스 체인: ImmersiveShell(로컬서버) → IApplicationViewCollection.GetViewForHwnd →
    IVirtualDesktopManagerInternal.{GetCurrentDesktop, MoveViewToDesktop}."""
    if not hwnd or not sys.platform.startswith("win"):
        return False
    import ctypes
    from ctypes import POINTER, byref, wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                    ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    def _g(s):
        a, b, c, d, e = s.split("-")
        g = GUID(); g.d1, g.d2, g.d3 = int(a, 16), int(b, 16), int(c, 16)
        for i, x in enumerate(bytes.fromhex(d + e)):
            g.d4[i] = x
        return g

    def _vc(ptr, idx, argtypes, *args):
        slot = ctypes.cast(ctypes.cast(ptr, POINTER(ctypes.c_void_p))[0], POINTER(ctypes.c_void_p))
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(slot[idx])(ptr, *args)

    def _rel(ptr):
        try:
            _vc(ptr, 2, [])                          # IUnknown::Release
        except Exception:
            pass
    ole = ctypes.windll.ole32
    try:
        ole.CoInitialize(None)
    except Exception:
        pass
    view = ctypes.c_void_p()
    vdmi = ctypes.c_void_p()
    sp = ctypes.c_void_p()
    try:
        # ImmersiveShell — 로컬 서버(CLSCTX_LOCAL_SERVER=4). IServiceProvider 로 하위 서비스를 얻는다.
        if ole.CoCreateInstance(byref(_g("C2F03A33-21F5-47FA-B4BB-156362A2F239")), None, 4,
                                byref(_g("6D5140C1-7436-11CE-8034-00AA006009FA")), byref(sp)) != 0 or not sp.value:
            return False
        # IApplicationViewCollection → GetViewForHwnd(idx6) → 이 창의 IApplicationView
        for avc in ("1841C6D7-4F9D-42C0-AF41-8747538F10E5",):
            vcp = ctypes.c_void_p()
            if _vc(sp, 3, [POINTER(GUID), POINTER(GUID), POINTER(ctypes.c_void_p)],
                   byref(_g(avc)), byref(_g(avc)), byref(vcp)) == 0 and vcp.value:
                got = _vc(vcp, 6, [wintypes.HWND, POINTER(ctypes.c_void_p)], hwnd, byref(view)) == 0
                _rel(vcp)
                if got and view.value:
                    break
        if not view.value:
            return False
        # IVirtualDesktopManagerInternal — 후보 IID 중 GetCount(idx3)가 1~64 로 정상인 것을 채택(vtable 검증).
        for vd in ("4970BA3D-FD4E-4647-BEA3-D89076EF4B9C",       # Win11 24H2
                   "53F5CA0B-158F-4124-900C-057158060B27",
                   "A3175F2D-239C-4BD2-8AA0-EEBA8B0B138E",
                   "B2F925B9-5A0F-4D2E-9F4D-2B1507593C10",
                   "094AFE11-44F2-4BA0-976F-29A97E263EE0"):
            v = ctypes.c_void_p()
            if _vc(sp, 3, [POINTER(GUID), POINTER(GUID), POINTER(ctypes.c_void_p)],
                   byref(_g("C5E0CDCA-7B6E-41B2-9FC4-D93975CC467B")), byref(_g(vd)), byref(v)) != 0 or not v.value:
                continue
            cnt = ctypes.c_uint(0)
            if _vc(v, 3, [POINTER(ctypes.c_uint)], byref(cnt)) == 0 and 1 <= cnt.value <= 64:
                vdmi = v
                break
            _rel(v)
        if not vdmi.value:
            return False
        cur = ctypes.c_void_p()
        if _vc(vdmi, 6, [POINTER(ctypes.c_void_p)], byref(cur)) != 0 or not cur.value:   # GetCurrentDesktop
            return False
        hr = _vc(vdmi, 4, [ctypes.c_void_p, ctypes.c_void_p], view, cur)                 # MoveViewToDesktop
        _rel(cur)
        return hr == 0
    except Exception:
        return False
    finally:
        for ptr in (view, vdmi, sp):
            if ptr.value:
                _rel(ptr)


def _place_window_on(hwnd, ref_fg, work, target_mon):
    """앱 창을 지금 보는 **데스크톱/모니터**로 데려와 전면화·포커스.
      1) 가상 데스크톱: 다른 데스크톱이면 **현재 데스크톱으로 옮긴다**(데스크톱을 바꾸지 않는다).
      2) 모니터: **다른 모니터에 있을 때만** 현재 모니터로 옮긴다(같은 화면이면 안 건드림 = 불필요한 이동 방지).
      3) 복구 + 전면화 + 포커스."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    _cfg_win(u, ctypes, wintypes)

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
    # 데스크톱 처리는 호출부(_summon_to_current)가 이미 끝냈다 — 여기선 모니터+포커스만.
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)                        # SW_RESTORE
    cur_mon = u.MonitorFromWindow(hwnd, 2)           # MONITOR_DEFAULTTONEAREST
    if work and target_mon and cur_mon != target_mon:
        # 다른 모니터에 있을 때만 옮긴다 — 크기는 유지(SWP_NOSIZE)하고 위치만 현재 모니터 중앙으로.
        r = RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        ww, wh = work[2] - work[0], work[3] - work[1]
        x = work[0] + max(0, (ww - w) // 2)
        y = work[1] + max(0, (wh - h) // 2)
        u.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0040)   # SWP_NOSIZE | SWP_SHOWWINDOW
    # SetForegroundWindow 는 우리가 포그라운드 스레드가 아니면 무시된다 → 입력 스레드를 잠깐 붙인다.
    fg_tid = u.GetWindowThreadProcessId(ref_fg, None) if ref_fg else 0
    cur_tid = k.GetCurrentThreadId()
    attached = bool(fg_tid and fg_tid != cur_tid and u.AttachThreadInput(fg_tid, cur_tid, True))
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(fg_tid, cur_tid, False)


def _open_on_current(open_hook, fg, work, mon):
    """(기존 창이 있으면 닫힌 뒤) 새 창을 연다 — 새 창은 **현재 데스크톱**에 뜬다(데스크톱 안 바뀜).
    뜨면 현재 모니터로 데려와 포커스."""
    def go():
        for _ in range(40):                          # 기존 창이 닫힐 때까지 ~4s
            if _find_app_hwnd() is None:
                break
            time.sleep(0.1)
        try:
            if open_hook:
                open_hook()                          # 창 없음 → 새 창(현재 데스크톱)
        except Exception:
            pass
        for _ in range(60):                          # 새 창 대기 ~6s
            time.sleep(0.1)
            h = _find_app_hwnd()
            if h:
                _place_window_on(h, fg, work, mon)   # 이미 현재 데스크톱 → 모니터+포커스만
                return
    threading.Thread(target=go, name="hotkey-open", daemon=True).start()


def _summon_to_current(open_hook):
    """단축키 동작 — 앱 창을 **지금 보고 있는 가상 데스크톱/모니터**로 데려와 포커스한다.
    다른 데스크톱에 있으면 내부 API(MoveViewToDesktop)로 **창을 현재 데스크톱으로 옮긴다** — 사용자를
    다른 데스크톱으로 보내지 않는다(재오픈·깜빡임 없음). 그 API 가 안 되는 빌드면 폴백으로 그냥 포커스
    (그땐 Windows 가 전환). 그다음 다른 모니터면 현재 모니터로 옮기고 전면화. LAKE_HOTKEY_DEBUG=1 로그."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    _cfg_win(u, ctypes, wintypes)                    # ★ restype 지정 뒤에 handle 을 읽어야 잘림이 없다
    fg = u.GetForegroundWindow()                     # 포커스 뺏기 전 '내가 보는 창'
    work, mon = _monitor_of(fg)
    hwnd = _find_app_hwnd()
    if hwnd:
        moved = _move_window_to_current_desktop(hwnd)   # 다른 데스크톱이면 현재로 옮긴다(교차프로세스 OK)
        if os.getenv("LAKE_HOTKEY_DEBUG") in ("1", "true", "True"):
            print("[hotkey] hwnd=%s fg=%s moved=%s diag=%s" % (hwnd, fg, moved, _desktop_diag(hwnd, fg)),
                  file=sys.stderr, flush=True)
        _place_window_on(hwnd, fg, work, mon)        # 다른 모니터면 현재 모니터로 + 전면화 + 포커스
        return
    _open_on_current(open_hook, fg, work, mon)       # 창이 아예 없을 때만 새로 연다


def _parse_hotkey(spec):
    """'ctrl+alt+space' / 'ctrl+shift+j' / 'alt+f2' 같은 조합 문자열 → (modifiers, vk, label). 실패 시 None.
    수식키 ctrl/alt/shift/win 중 하나 이상 + 키(a~z, 0~9, space, F1~F24) 하나."""
    if not spec:
        return None
    MODS = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8, "super": 0x8, "cmd": 0x8}
    mods, vk, labels = 0, None, []
    for pt in [x for x in spec.strip().lower().replace(" ", "").split("+") if x]:
        if pt in MODS:
            mods |= MODS[pt]
            labels.append({"control": "Ctrl", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift",
                           "win": "Win", "super": "Win", "cmd": "Win"}[pt])
        elif pt == "space":
            vk, _ = 0x20, labels.append("Space")
        elif len(pt) == 1 and ("a" <= pt <= "z" or "0" <= pt <= "9"):
            vk, _ = ord(pt.upper()), labels.append(pt.upper())
        elif pt.startswith("f") and pt[1:].isdigit() and 1 <= int(pt[1:]) <= 24:
            vk, _ = 0x70 + int(pt[1:]) - 1, labels.append("F" + pt[1:])
        else:
            return None
    if vk is None or mods == 0:
        return None
    return mods, vk, "+".join(labels)


def _register_global_hotkey(on_fire):
    """[Windows] 전역 단축키(기본 Ctrl+Alt+Space, LAKE_HOTKEY 로 변경) — 누르면 on_fire(). 전용 스레드 루프."""
    if not sys.platform.startswith("win"):
        return
    if os.getenv("LAKE_NO_HOTKEY") in ("1", "true", "True"):
        return

    def run():
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        _cfg_win(u, ctypes, wintypes)
        MOD_NOREPEAT, WM_HOTKEY, HID = 0x4000, 0x0312, 1
        # 조합은 LAKE_HOTKEY 로 바꿀 수 있다(예: ctrl+alt+j) — 다른 앱/IDE 와 겹칠 때. 기본 Ctrl+Alt+Space.
        spec = os.getenv("LAKE_HOTKEY") or "ctrl+alt+space"
        parsed = _parse_hotkey(spec)
        if not parsed:
            print(f"[hotkey] 잘못된 LAKE_HOTKEY={spec!r} — 기본 Ctrl+Alt+Space 사용", file=sys.stderr)
            parsed = _parse_hotkey("ctrl+alt+space")
        mods, vk, label = parsed
        if not u.RegisterHotKey(None, HID, mods | MOD_NOREPEAT, vk):
            print(f"[hotkey] {label} 등록 실패(다른 앱이 선점했을 수 있음 — LAKE_HOTKEY 로 조합 변경 가능)", file=sys.stderr)
            return
        print(f"[hotkey] {label} — 앱 창을 현재 모니터로 호출")
        msg = wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        on_fire()
                    except Exception as e:
                        print(f"[hotkey] 처리 오류: {e}", file=sys.stderr)
        finally:
            u.UnregisterHotKey(None, HID)
    threading.Thread(target=run, name="global-hotkey", daemon=True).start()


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


def _appwin_profile():
    """앱 창 Chromium 프로필 경로 — SSO 세션 파일 옆에 둔다(같이 백업/삭제되게).
    고정 경로라 쿠키·로그인 상태가 창을 닫았다 열어도 남는다."""
    from pathlib import Path

    from app.infra.settings import get_settings
    try:
        base = Path(get_settings().jira_state_path).resolve().parent
    except Exception:
        base = Path.cwd()
    d = base / ".appwin-profile"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _window_session(s, auto_login=False, headless=False, on_ready=None):
    """앱 창(Playwright Chromium, --app 모드) 하나를 열고 **닫힐 때까지** 대기.
    서버는 이미 떠 있다고 가정(창의 goto 재시도가 준비를 커버) — 여기선 서버를 시작/종료하지 않는다.
    → 트레이가 이 함수를 스레드로 여러 번 재사용(창을 닫아도 백엔드는 유지). 임시 프로필은 정리.

    auto_login: 최초 오픈에서만 True — prod SSO 3서비스 자동 취득 신호. (재오픈 땐 조용히 연다)
    on_ready:   창이 실제로 화면에 뜬 시점에 한 번 호출(트레이의 중복 클릭 가드 해제용).
    """
    import shutil
    import tempfile
    url = f"http://127.0.0.1:{s.app_port}/"            # localhost 는 Windows 에서 ::1 폴백 지연
    import app.main as appmain
    appmain._window_login = True                       # /api/login 이 이 창에서 로그인하도록
    from playwright.sync_api import TimeoutError as PWTimeoutError
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()

    def _launch(udd):
        return p.chromium.launch_persistent_context(
            user_data_dir=udd,
            headless=headless,
            accept_downloads=True,                     # ★ 아래 _wire_downloads 참고
            no_viewport=True,                          # 뷰포트를 실제 창 크기에 추종(리사이즈 시 흰 여백 방지)
            ignore_default_args=["--enable-automation"],   # '자동화 제어 중' 안내바 제거
            args=["--no-first-run", "--no-default-browser-check",
                  # Chromium 자체 다운로드 말풍선(좌상단)을 끈다 — 저장은 우리가 하고 알림도
                  # 우리가 띄우는데, 브라우저 것까지 뜨면 같은 일을 두 번 말하는 데다 그 안내가
                  # 가리키는 임시 경로는 사용자에게 아무 의미가 없다(Playwright 임시 파일).
                  "--disable-features=DownloadBubble,DownloadBubbleV2",
                  "--window-size=1400,900", f"--app={_BOOT_DATA_URL}"],  # 앱 모드 + 즉시 부팅로더
        )

    # 앱 창 프로필은 **고정 경로**를 쓴다 — 매번 임시 디렉터리를 새로 만들면 쿠키가 늘 초기화된다.
    # 창을 여러 개 열면 같은 프로필을 동시에 못 쓰므로(Chromium 이 잠근다) 그때만 임시로 떨어진다.
    shared = _appwin_profile()
    udd, tmp = str(shared), False
    try:
        context = _launch(udd)
    except Exception:
        udd, tmp = tempfile.mkdtemp(prefix="ltm-appwin-"), True
        context = _launch(udd)
    page = context.pages[0] if context.pages else context.wait_for_event("page")
    _wire_external_links(context, page)                # 외부 링크 훅(goto 전 설치 → 실제 페이지에도 적용)
    dlq = _wire_downloads(context)                     # 첨부 다운로드 큐(저장은 아래 루프에서)
    for _ in range(300):                               # goto 재시도(서버 준비 대기 포함, ~30s). 부팅로더 유지.
        try:
            page.goto(url, wait_until="domcontentloaded")
            if page.evaluate("!!(document.querySelector('.app-boot')||document.querySelector('.wrap'))"):
                break
        except Exception:
            pass
        if page.is_closed():
            break
        page.wait_for_timeout(100)
    from app.infra.settings import STATIC_DIR
    ico_path = str(STATIC_DIR / "favicon.ico")
    print(f"Lake Task Manager - {url}  (env={s.jira_env})")
    if on_ready:
        try:
            on_ready()                                 # 창이 떴다 — 트레이가 다시 눌림을 받아도 된다
        except Exception:
            pass
    # [prod] 최초 오픈에서만 3서비스 SSO 자동 취득 — 아래 while 루프가 신호를 받아 임시 창에서 구동.
    # 이미 인증돼 있으면 service_probe 로 스킵(무음). (재오픈 땐 auto_login=False 라 신호 안 보냄)
    if auto_login and s.jira_env == "prod":
        appmain._login_requested.set()
    appmain.note_window_opened()               # 단일 인스턴스: '살아있는 창 있음' 으로 집계
    try:
        i = 0
        pump = True          # Playwright 이벤트를 넘겨받을 수 있는 상태인가
        while not page.is_closed():
            if appmain._login_requested.is_set():
                # 시작 직후의 자동 취득은 **Jira 만**(only_primary). 트레이에서 고른 서비스 로그인은
                # 그쪽 경로(_run_tray)가 따로 돈다.
                _do_login_in_window(s, page, context, appmain, only_primary=True, p=p)
            if appmain.consume_focus_request():         # 재실행/트레이 '앱 열기' 가 이 창을 앞으로
                try:
                    page.bring_to_front()               # ★ 반드시 이 창의 스레드에서(Playwright 스레드 고정)
                except Exception:
                    pass
            _drain_downloads(context, dlq)              # 받아 둔 다운로드를 여기서(메인 스레드) 저장
            if i < 16:                                  # ~8초 동안 창 아이콘 재적용(favicon 덮어쓰기 대비)
                _set_window_icon_win(ico_path)
            i += 1
            # ★★ 여기서 그냥 time.sleep 만 돌면 **다운로드 저장도, _openExternal 바인딩도 영영
            #     실행되지 않는다.** Playwright 동기 API 는 우리가 Playwright 호출 안에 있는
            #     동안에만 이벤트(download, expose_function 호출)를 넘겨주기 때문이다.
            #     실제로 그 탓에 앱 창에서 첨부가 임시 폴더에 해시 이름으로 남고(저장 훅 미실행),
            #     '새 창에서 열기' 도 아무 반응이 없었다(바인딩 미실행).
            #
            #     그렇다고 page.wait_for_timeout 을 쓰면 절전 등으로 파이프가 먹통일 때 영원히
            #     멈춘다(트레이가 창을 죽은 걸로 못 알아챘던 문제). 그래서 **타임아웃이 있는**
            #     대기로 이벤트를 펌프하고, 드라이버가 죽으면 로컬 sleep 으로 내려앉는다.
            #     is_closed() 는 로컬 상태라 왕복이 없어 그 뒤에도 종료 감지는 계속된다.
            if pump:
                try:
                    context.wait_for_event("close", timeout=400)
                    break                               # 컨텍스트가 실제로 닫혔다
                except PWTimeoutError:
                    continue                            # 정상 — 400ms 동안 이벤트만 넘기고 돌아온다
                except Exception:
                    pump = False                        # 드라이버가 죽었다 → 아래 sleep 으로
            time.sleep(0.5)
    except Exception:
        pass
    appmain.note_window_closed()               # 창이 닫혔다 — '살아있는 창' 집계에서 뺀다
    # 정리는 별도 스레드에 맡기고 기다리지 않는다 — 죽은 드라이버에서 close()/stop() 이 멎을 수 있고,
    # 여기서 막히면 호출한 쪽(트레이)이 창이 닫힌 걸 영영 모른다.
    def _cleanup():
        for closer in (context.close, p.stop):
            try:
                closer()
            except Exception:
                pass
        if tmp:
            shutil.rmtree(udd, ignore_errors=True)     # 임시 프로필만 정리(고정 프로필은 보존)
    threading.Thread(target=_cleanup, name="appwin-cleanup", daemon=True).start()


def _run_app_window(s):
    """[비-트레이 폴백] 서버 + 앱 창 1개. 창 닫으면 서버도 종료(기존 동작)."""
    server = _serve_bg(s, wait=False)                  # 창 먼저 뜨게 non-blocking
    _warm_session_bg(s)                                # SSO 세션 미리 데우기(창 기동과 병렬)
    _window_session(s, auto_login=True, on_ready=_hide_console_win)   # 창 뜨면 run.bat 콘솔 숨김
    try:
        server.should_exit = True
    except Exception:
        pass


# ── 시작 메뉴 / 자동시작 바로가기 (Windows) ─────────────────────────────────
# 시작 메뉴 .lnk 를 만들어 두면 '시작'에서 검색해 실행할 수 있다(요구사항). 자동시작은
# 시작프로그램 폴더의 .lnk 존재로 on/off (트레이 메뉴 토글). .lnk 생성은 PowerShell WScript.Shell.
_SHORTCUT_NAME = "Lake Task Manager.lnk"


def _launcher_bat():
    """배포 repo 의 run.bat (업데이트+venv+run.py 를 다 하는 런처)."""
    from app.infra.settings import APP_ROOT
    return APP_ROOT / "run.bat"


def _start_menu_dir():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _startup_dir():
    return _start_menu_dir() / "Startup"


def _make_shortcut(lnk_path, target, icon):
    """PowerShell 로 .lnk 생성(최소화 실행). best-effort."""
    if not sys.platform.startswith("win"):
        return False
    try:
        lnk_path.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            "$w=New-Object -ComObject WScript.Shell;"
            f"$s=$w.CreateShortcut('{lnk_path}');"
            f"$s.TargetPath='{target}';"
            f"$s.WorkingDirectory='{target.parent}';"
            f"$s.WindowStyle=7;"                        # 7 = 최소화(콘솔 안 튀게)
            + (f"$s.IconLocation='{icon}';" if icon else "")
            + "$s.Save()"
        )
        import subprocess
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, timeout=15)
        return lnk_path.exists()
    except Exception:
        return False


def _icon_path():
    from app.infra.settings import STATIC_DIR
    ico = STATIC_DIR / "favicon.ico"
    return str(ico) if ico.exists() else ""


def _ensure_start_menu_shortcut():
    """시작 메뉴에 바로가기가 없으면 만든다(검색 가능하게). 이미 있으면 no-op."""
    try:
        lnk = _start_menu_dir() / _SHORTCUT_NAME
        if not lnk.exists() and _launcher_bat().exists():
            _make_shortcut(lnk, _launcher_bat(), _icon_path())
    except Exception:
        pass


def _autostart_enabled():
    try:
        return (_startup_dir() / _SHORTCUT_NAME).exists()
    except Exception:
        return False


def _set_autostart(enable):
    """자동시작(부팅 시 실행) on/off — 시작프로그램 폴더의 .lnk 생성/삭제."""
    try:
        lnk = _startup_dir() / _SHORTCUT_NAME
        if enable:
            _make_shortcut(lnk, _launcher_bat(), _icon_path())
        elif lnk.exists():
            lnk.unlink()
    except Exception:
        pass


def _run_tray(s):
    """[트레이 상주] 백엔드는 상시 유지, 앱 창은 트레이 메뉴로 열고/닫는다.
    창을 닫아도 서버·트레이는 살아있고 [앱 열기]로 재오픈. 종료는 트레이 [종료]로만."""
    import pystray
    from PIL import Image
    from app.infra.settings import STATIC_DIR

    server = _serve_bg(s, wait=False)                  # 백엔드 상시(창과 독립)
    _warm_session_bg(s)                                # SSO 세션 미리 데우기(창 기동과 병렬)
    _ensure_start_menu_shortcut()                      # '시작'에서 검색 가능하게

    # [앱 열기] 중복 클릭 가드 — **시각으로만** 판단한다.
    # 예전엔 'open' 불리언으로 한 번에 하나만 열었는데, 절전 등으로 창 스레드가 멎으면 그 값이
    # True 로 굳어 이후 [앱 열기]가 아무 반응 없이 무시됐다(밤새 켜 둔 뒤 겪던 증상).
    # 시각 기반은 무슨 일이 있어도 스스로 풀린다 — 최악이라도 몇 초 뒤엔 다시 열 수 있다.
    _open = {"t": 0.0, "ready": True}
    _OPEN_WAIT = 15.0      # 창이 뜨기 전: Chromium 기동에 몇 초 걸린다. 그 사이 클릭은 다 같은 뜻이다.
    _OPEN_MIN = 1.5        # 창이 뜬 뒤: 트레이 더블클릭 한 번이 두 개로 세어지는 것만 막는다.

    def open_window(initial=False):
        """[앱 열기] — 창을 하나 연다. 연달아 누른 것은 한 번으로 친다.

        트레이 아이콘은 한 번 눌러도 이벤트가 여러 번 오고(기본 항목 + 더블클릭), 창이 뜨기까지
        몇 초 걸려 사용자는 '안 눌렸나' 하고 또 누른다. 그대로 두면 창이 서너 개 뜬다.
        """
        now = time.monotonic()
        gap = now - _open["t"]
        if gap < (_OPEN_MIN if _open["ready"] else _OPEN_WAIT):
            print("[tray] 앱 창을 이미 여는 중입니다 — 중복 클릭 무시")
            return
        _open["t"], _open["ready"] = now, False

        def ready():
            _open["ready"] = True
            # 앱 창이 실제로 떴다 → run.bat 콘솔을 숨긴다(최초 1회). 그 전(셋업/기동)엔 콘솔이 보인다.
            if not _open.get("console_hidden"):
                _open["console_hidden"] = True
                _hide_console_win()

        def run():
            try:
                _window_session(s, auto_login=initial, on_ready=ready)
            except Exception as e:                     # 조용히 죽지 않게 로그는 남긴다
                print("app window error:", e)
            finally:
                _open["ready"] = True                  # 실패해도 다음 클릭은 받아야 한다
        threading.Thread(target=run, name="app-window", daemon=True).start()

    # 단일 인스턴스: 재실행(런처 재기동)이 백엔드에 '창 열기/포커스' 를 요청하면 이 hook 으로 연다.
    # (살아있는 창이 있으면 백엔드가 대신 focus 이벤트를 세워, 창 루프가 bring_to_front 한다.)
    import app.main as _appmain
    _appmain.set_open_window_hook(open_window)

    # 전역 단축키 Ctrl+Alt+Space — 앱 창을 '지금 보고 있는 모니터'로 불러온다(없으면 연다).
    _register_global_hotkey(lambda: _summon_to_current(_appmain.request_focus_or_open))

    # ── 서비스별 SSO 인증 상태 ────────────────────────────────────────────
    # 인증 여부의 **단일 원천은 백엔드**다(같은 프로세스의 provider 로 실제 호출해 본다).
    # 트레이는 그 결과를 비추기만 한다 — 메뉴가 열릴 때 조회하면 그동안 트레이가 멎으므로
    # 백그라운드에서 주기적으로 채워 두고, 메뉴는 캐시된 값을 즉시 그린다.
    _sso = {}            # {서비스명: True(인증) | False(로그인 필요) | None(미설정)}
    _busy = set()        # 로그인 진행 중인 서비스

    def probe_sso():
        if s.jira_env != "prod":
            return
        try:
            from app.main import _probe_service
        except Exception:
            return
        for svc in getattr(s, "services", []):
            if not svc.get("configured"):
                _sso[svc["name"]] = None
                continue
            try:
                _sso[svc["name"]] = bool(_probe_service(svc).get("authenticated"))
            except Exception:
                _sso[svc["name"]] = False

    def sso_poller(icon):
        # 매분 인증 상태를 확인한다 — 이게 곧 **keep-warm** 이다. 살아 있는 세션에 주기적으로
        # 인증 요청을 보내 유휴 만료를 늦춘다. 죽었으면(리다이렉트만으로 살릴 수 있는 경우)
        # 조용히 다시 세운다 — 사용자가 아무것도 안 눌러도 세션이 스스로 복구된다.
        # ★ probe_sso 는 provider 큐(Playwright 전용 스레드)로 도는 인증 GET 이라 **그 자체가
        #   keep-warm** 이다 — 매분 살아 있는 세션에 요청을 보내 유휴 만료를 늦춘다.
        #   여기서 컨텍스트를 직접 만지면 안 된다: Playwright 동기 API 는 자기를 만든 스레드에서만
        #   써야 해서, 다른 스레드에서 ctx.request/storage_state 를 부르면
        #   "cannot switch to a different thread" 로 터진다(앱 시작 때 그 에러가 났다).
        while True:
            probe_sso()
            try:
                icon.update_menu()
            except Exception:
                pass
            time.sleep(60)

    def login_service(name, base):
        """그 서비스만 로그인 — 저장도 그 서비스 파일에만 되므로 나머지 세션은 그대로 산다."""
        if name in _busy:
            return
        _busy.add(name)
        try:
            from app.auth.sso_session import login_wait
            import app.main as appmain
            ok = login_wait(base, appmain._client.sso_store(), service=name.lower(), timeout=300)
            if ok:
                appmain._client.reset_provider()
            print(f"[tray] {name} 로그인 {'성공' if ok else '실패/취소'}")
        except Exception as e:
            print(f"[tray] {name} 로그인 오류: {e}")
        finally:
            _busy.discard(name)
            probe_sso()

    def sso_items():
        """서비스별 메뉴 항목 — 상태 표시 + 클릭하면 그 서비스만 로그인."""
        if s.jira_env != "prod":
            return [pystray.MenuItem(f"SSO 없음 (env={s.jira_env})", None, enabled=False)]
        # ★ pystray 는 콜백의 인자 개수를 검사한다(기본값 인자도 센다) → 기본인자 트릭 대신
        #   클로저 팩토리로 서비스명을 가둔다.
        def item_for(name, base):
            def label(item):
                st = _sso.get(name)
                if name in _busy:
                    return f"  … {name} 로그인 중"
                if st is None:
                    return f"  – {name} (미설정)"
                return f"  ● {name} 인증됨" if st else f"  ○ {name} — 클릭해 로그인"

            def on_click(icon, item):
                if _sso.get(name) is None:
                    return                       # 미설정 서비스는 열 창이 없다
                threading.Thread(target=login_service, args=(name, base),
                                 name="sso-login-" + name, daemon=True).start()

            def enabled(item):
                return _sso.get(name) is not None

            return pystray.MenuItem(label, on_click, enabled=enabled)

        return [item_for(svc["name"], svc["base"]) for svc in getattr(s, "services", [])]

    # 트레이 아이콘 이미지 (png 우선)
    img = None
    for cand in ("icon.png", "favicon.ico"):
        try:
            img = Image.open(str(STATIC_DIR / cand))
            break
        except Exception:
            continue

    def on_open(icon, item):
        # 트레이 [앱 열기]도 '이미 떠 있으면 포커스, 없으면 새 창' 으로 통일한다.
        import app.main as appmain
        appmain.request_focus_or_open()

    def on_autostart(icon, item):
        _set_autostart(not _autostart_enabled())
        icon.update_menu()

    def on_restart(icon, item):
        # 업데이트 후 재기동 — run.bat 을 새 콘솔로 (2초 뒤 시작해 현재 인스턴스가 포트를 놓게 함).
        # run.bat 이 git pull(자동) + venv/deps + 앱 재기동을 한다. 그 뒤 현재 인스턴스는 종료.
        launched = False
        try:
            bat = _launcher_bat()
            if bat.exists() and sys.platform.startswith("win"):
                import subprocess
                # ★ 명령을 **문자열 한 줄**로 넘긴다. 리스트로 주면 subprocess 가 run.bat 경로를
                #   \"…\" 로 이스케이프하는데, cmd.exe 는 그 백슬래시-따옴표를 못 읽어 경로가 깨져
                #   run.bat 이 **실행되지 않았다**(=앱은 꺼지는데 재시작이 안 됨). 문자열로 주면
                #   cmd 가 `timeout … & "경로"` 를 그대로 파싱한다(경로 공백도 따옴표로 안전).
                cmd = 'cmd /c timeout /t 2 /nobreak >nul & "%s"' % str(bat)
                # LAKE_RESTART=1 로 재기동을 표시한다 → 새 인스턴스는 단일 인스턴스 억제를
                # 건너뛰고(내가 새 인스턴스가 돼야 함), 종료 중인 이 인스턴스의 포트가 풀릴 때까지
                # 잠깐 기다린다. 안 그러면 새 인스턴스가 "이미 떠 있음"으로 오판해 창만 열고 종료해,
                # 결국 둘 다 사라진다(=업데이트 후 아무것도 안 뜸).
                env = dict(os.environ, LAKE_RESTART="1")
                subprocess.Popen(cmd, cwd=str(bat.parent), env=env,
                                 creationflags=0x00000010)   # CREATE_NEW_CONSOLE (독립 실행)
                launched = True
        except Exception as e:
            print(f"[tray] 재시작 런처 실행 실패: {e}", file=sys.stderr, flush=True)
        if not launched:
            # 런처를 못 띄웠으면 **끄지 않는다** — 끄기만 하면 사용자는 '업데이트 후 재시작' 을
            # 눌렀는데 앱이 그냥 사라진 것으로 겪는다. 계속 켜 둔 채 원인(런처 경로)을 남긴다.
            print("[tray] run.bat 을 찾지 못해 재시작을 건너뜁니다(앱은 유지).", file=sys.stderr, flush=True)
            return
        try:
            server.should_exit = True
        except Exception:
            pass
        icon.stop()

    def on_quit(icon, item):
        try:
            server.should_exit = True
        except Exception:
            pass
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("앱 열기", on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("SSO 인증", pystray.Menu(lambda: sso_items())),
        pystray.MenuItem("SSO 상태 새로고침", lambda icon, item: (probe_sso(), icon.update_menu())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("업데이트 후 재시작", on_restart),
        pystray.MenuItem("컴퓨터 시작 시 자동 실행", on_autostart,
                         checked=lambda item: _autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("종료", on_quit),
    )
    icon = pystray.Icon("lake-task-manager", img, "Lake Task Manager", menu)
    _appmain.set_restart_hook(lambda: on_restart(icon, None))   # UI '업데이트' 버튼 → 트레이 재시작과 동일 경로
    _appmain.set_quit_hook(lambda: on_quit(icon, None))         # 새 run.bat 이 '옛 버전이니 빠져라' 로 호출 → 조용히 종료
    threading.Thread(target=sso_poller, args=(icon,), name="sso-poller", daemon=True).start()
    print(f"Lake Task Manager - 트레이 상주 (env={s.jira_env}). 창을 닫아도 백엔드는 유지됩니다.")
    open_window(initial=True)                          # 시작 시 창 1개 오픈
    icon.run()                                         # 메인 스레드 블로킹(트레이 루프)


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

    if os.getenv("LAKE_RESTART"):
        # 업데이트 후 재기동: 직전 인스턴스가 방금 종료 중이다. 단일 인스턴스 억제를 건너뛰고
        # (내가 새 인스턴스가 돼야 함) 포트가 풀릴 때까지 기다린 뒤 정상 기동한다.
        _wait_port_free(s.app_port)
    elif _signal_existing_instance(s):
        # 단일 인스턴스: 이미 떠 있으면 그 인스턴스에 창을 띄우/포커스 시키고 끝낸다(새 백엔드 안 띄움).
        # (시작프로그램/바로가기 재실행 시 백엔드가 두 개 뜨거나 창이 여러 개 나는 것을 막는다.)
        return

    if s.jira_env == "prod":
        state = APP_ROOT / s.jira_state_path
        if not state.exists():
            print(f"[prod] SSO 세션이 없습니다({state}). 앱 화면의 'SSO 로그인' 버튼을 누르세요.")

    # 앱 창 모드 (playwright 있고, 끄지 않았으면).
    use_window = os.getenv("LAKE_NO_WINDOW") not in ("1", "true", "True")
    if use_window:
        try:
            import playwright.sync_api  # noqa: F401
        except Exception:
            use_window = False
    if not use_window:
        _run_plain(s)
        return

    # 트레이 상주 모드 (pystray 있고, Windows 이고, 끄지 않았으면) — 창 닫아도 백엔드 유지.
    use_tray = os.getenv("LAKE_NO_TRAY") not in ("1", "true", "True") and sys.platform.startswith("win")
    if use_tray:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
        except Exception:
            use_tray = False
    if use_tray:
        _run_tray(s)         # icon.run() 이 블로킹; [종료] 시 반환
        os._exit(0)
    else:
        _run_app_window(s)   # 폴백: 창 닫으면 전체 종료(기존 동작)
        os._exit(0)          # 남은 스레드까지 확실히 정리하고 즉시 종료(=run.bat 도 함께 끝)


if __name__ == "__main__":
    main()
