"""dispatch_url.py — Jira 링크 가로채기용 URL 디스패처.

Windows 에 '브라우저'로 등록해 두고 사용자가 **기본 브라우저**로 지정하면, 메신저·메일·문서 등
다른 앱에서 여는 모든 URL 이 여기로 들어온다. 동작:

  * 사내 Jira 의 /browse/{KEY} 링크  → Lake Task Manager 창 소환 + 그 티켓 다이얼로그
    (앱이 안 떠 있으면 그냥 실제 브라우저로 Jira 를 연다 — 링크가 죽는 일은 없어야 한다)
  * 그 외 모든 URL              → 실제 브라우저(Chrome/Edge/Firefox 자동 탐지)로 그대로 전달

등록/해제 (관리자 불필요, HKCU):
    python dispatch_url.py --register      # 등록 + Windows 기본 앱 설정 화면 열기
    python dispatch_url.py --unregister

주의: 브라우저 **안**에서 클릭한 링크는 OS 를 거치지 않으므로 여기로 오지 않는다(그건 확장 영역).
빠른 기동이 생명이라 앱 코드를 import 하지 않는다(표준库 + yaml 만).
"""
import os
import re
import subprocess
import sys

APP_NAME = "LakeTaskManagerLinks"
APP_LABEL = "Lake Task Manager 링크"
PROG_ID = "LTMLink.URL"
_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_cfg():
    """config/jira.yml 에서 jira.base 와 server.port 만 뽑는다. 실패하면 (None, 8000)."""
    try:
        import yaml
        with open(os.path.join(_ROOT, "config", "jira.yml"), encoding="utf-8") as f:
            c = yaml.safe_load(f) or {}
        base = ((c.get("jira") or {}).get("base") or "").rstrip("/")
        port = int(((c.get("server") or {}).get("port")) or 8000)
        return base or None, port
    except Exception:
        return None, 8000


def _app_port():
    for name in ("APP_PORT", "LAKE_APP_PORT"):
        v = os.environ.get(name)
        if v and v.isdigit():
            return int(v)
    return _load_cfg()[1]


def _jira_ticket_of(url):
    """URL 이 사내 Jira 의 /browse/{KEY} 면 KEY, 아니면 None. (그 외 Jira 경로도 None → 브라우저로)"""
    base, _ = _load_cfg()
    if not base:
        return None
    from urllib.parse import urlparse
    try:
        u, b = urlparse(url), urlparse(base)
    except Exception:
        return None
    if (u.scheme, u.netloc.lower()) != (b.scheme, b.netloc.lower()):
        return None
    m = re.match(r"^/browse/([A-Za-z][A-Za-z0-9]*-\d+)$", u.path or "")
    return m.group(1).upper() if m else None


def _send_to_app(key):
    """떠 있는 앱에 티켓 열기를 요청. 성공하면 True."""
    import json
    import urllib.request
    port = _app_port()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/app/open-ticket",
        data=json.dumps({"key": key}).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _find_browser():
    """실제 브라우저 실행파일 — App Paths 레지스트리에서 탐지(우리를 다시 부르는 순환 방지)."""
    import winreg
    for exe in ("chrome.exe", "msedge.exe", "firefox.exe"):
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe) as k:
                    path = winreg.QueryValue(k, None)
                if path and os.path.exists(path):
                    return path
            except OSError:
                pass
    return None


def _forward(url):
    b = _find_browser()
    if b:
        subprocess.Popen([b, url])
        return
    # 마지막 폴백 — Edge 프로토콜(우리에게 되돌아오지 않는다)
    try:
        os.startfile("microsoft-edge:" + url)
    except Exception:
        pass


def _dispatch(url):
    key = _jira_ticket_of(url)
    if key and _send_to_app(key):
        return
    _forward(url)


# ── Windows 등록 (HKCU — 관리자 불필요) ─────────────────────────────────────
def _cmdline():
    """레지스트리에 넣을 실행 커맨드 — 콘솔 창이 안 뜨는 pythonw 를 쓴다."""
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    if os.path.exists(pyw):
        py = pyw
    return f'"{py}" "{os.path.abspath(__file__)}" "%1"'


def register():
    import winreg
    cmd = _cmdline()
    HKCU = winreg.HKEY_CURRENT_USER

    def setv(path, name, value):
        with winreg.CreateKey(HKCU, path) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    prog = "Software\Classes\\" + PROG_ID
    setv(prog, None, APP_LABEL)
    setv(prog + r"\shell\open\command", None, cmd)

    cap = "Software\Clients\StartMenuInternet\\" + APP_NAME
    setv(cap, None, APP_LABEL)
    setv(cap + r"\Capabilities", "ApplicationName", APP_LABEL)
    setv(cap + r"\Capabilities", "ApplicationDescription",
         "Jira 링크는 Lake Task Manager 로, 나머지는 기본 브라우저로 전달")
    setv(cap + r"\Capabilities\URLAssociations", "http", PROG_ID)
    setv(cap + r"\Capabilities\URLAssociations", "https", PROG_ID)
    setv(cap + r"\shell\open\command", None, cmd)
    setv(r"Software\RegisteredApplications", APP_NAME,
         "Software\Clients\StartMenuInternet\\" + APP_NAME + r"\Capabilities")

    print("등록 완료. Windows 설정에서 기본 브라우저를 '" + APP_LABEL + "' 로 지정하세요.")
    try:
        os.startfile("ms-settings:defaultapps")
    except Exception:
        print("설정 > 앱 > 기본 앱 에서 직접 지정하세요.")


def unregister():
    import winreg
    HKCU = winreg.HKEY_CURRENT_USER

    def kill(path):
        try:
            winreg.DeleteKey(HKCU, path)
        except OSError:
            pass

    for p in ("Software\Classes\\" + PROG_ID + r"\shell\open\command",
              "Software\Classes\\" + PROG_ID + r"\shell\open",
              "Software\Classes\\" + PROG_ID + r"\shell",
              "Software\Classes\\" + PROG_ID,
              "Software\Clients\StartMenuInternet\\" + APP_NAME + r"\Capabilities\URLAssociations",
              "Software\Clients\StartMenuInternet\\" + APP_NAME + r"\Capabilities",
              "Software\Clients\StartMenuInternet\\" + APP_NAME + r"\shell\open\command",
              "Software\Clients\StartMenuInternet\\" + APP_NAME + r"\shell\open",
              "Software\Clients\StartMenuInternet\\" + APP_NAME + r"\shell",
              "Software\Clients\StartMenuInternet\\" + APP_NAME):
        kill(p)
    try:
        with winreg.OpenKey(HKCU, r"Software\RegisteredApplications", 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
    except OSError:
        pass
    print("해제 완료. 기본 브라우저를 원래 브라우저로 되돌리세요.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
    elif args[0] == "--register":
        register()
    elif args[0] == "--unregister":
        unregister()
    else:
        _dispatch(args[0])
