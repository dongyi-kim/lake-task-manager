"""새 버전이 있는가 — UI '업데이트 가능' 표시용. 판정은 **릴리즈 태그**로 한다.

배포는 개발 repo 의 릴리즈 태그로 나간다(태그 안 된 커밋은 유저에게 안 간다).
그래서 판정은 "설치된 태그 != 최신 릴리즈 태그" 하나다.

예전엔 배포 repo 에 `git fetch` 를 걸고 `HEAD..@{u}` 커밋 수를 셌는데, 그러면
**git 이 없는 유저는 업데이트가 있는지조차 알 수 없었다** — 정작 수동 업데이트가
제일 어려운 사람들이 알림을 못 받았다.

★ api.github.com 은 쓰지 않는다. 비인증 GitHub API 는 **IP 당 시간당 60회**라, 사내
  프록시/NAT 뒤 사용자들이 그 60회를 통째로 나눠 쓴다(아침에 다 같이 켜면 전멸).
  웹 엔드포인트 /releases/latest 는 302 로 태그를 알려 주고 rate limit 이 없다.
  덤: latest 는 prerelease·draft 를 건너뛰므로 사내 검증용 태그는 유저에게 안 보인다.

엔드포인트는 **즉답**해야 하므로 조회는 백그라운드로 돌리고 캐시값을 돌려준다.
실패는 전부 조용하다(ok=False) — 표시만 하는 기능이라 실패가 앱을 방해하면 안 된다.
"""
import os
import re
import threading
import time
import urllib.request

from app.infra.version import code_rev, pinned_rev

RELEASES_LATEST = "https://github.com/dongyi-kim/lake-task-manager/releases/latest"
_TAG_RE = re.compile(r"/releases/tag/(.+?)/?$")


def latest_tag(timeout=12):
    """개발 repo 의 최신 릴리즈 태그. 못 알아내면 None.
    /releases/latest 는 /releases/tag/<태그> 로 302 하고 urllib 이 그걸 따라간다.
    릴리즈가 하나도 없으면 /releases 로 가므로 매칭에 실패해 None 이 된다(정상).

    ★ 캐시버스터가 **필요하다**(실측). GitHub 은 이 리다이렉트를 CDN 에 캐시해서, 릴리즈가
      하나도 없던 시절의 결과를 새 릴리즈를 올린 뒤에도 한참 그대로 준다. 쿼리를 붙이면
      즉시 올바른 태그가 온다. 5분 단위로 묶어 캐시 이점은 살리되 지연을 5분으로 제한한다."""
    try:
        url = RELEASES_LATEST + "?_=%d" % (int(time.time()) // 300)
        req = urllib.request.Request(url, headers={"User-Agent": "lake-task-manager"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            m = _TAG_RE.search(r.geturl() or "")
            return m.group(1) if m else None
    except Exception:
        return None


class UpdateChecker:
    def __init__(self, app_root, stale_after=600):
        self.app_root = str(app_root)           # 배포 루트(config\ 가 있는 곳) — 고정 여부를 읽는다
        self.stale_after = stale_after          # 이 시간(초) 지나면 백그라운드로 다시 확인
        self._lock = threading.Lock()
        self._refreshing = False
        self._state = {"available": False, "current": "", "latest": "",
                       "pinned": "", "checkedAt": 0.0, "ok": False}

    def _refresh(self):
        try:
            cur = code_rev()
            # LAKE_REV 로 띄운 세션(bin\test_run.bat)은 **일부러 미릴리즈 코드**를 보고 있다.
            # 고정(pinned)과 같은 취급 — 안 그러면 current('main'·SHA) != latest(태그) 라
            # 늘 '업데이트 있음' 이 뜨고, 눌러도 그 세션은 계속 그 ref 라(재시작이 환경변수를
            # 물려받는다) 배지가 영영 안 사라진다.
            pin = os.environ.get("LAKE_REV", "").strip() or pinned_rev(self.app_root)
            if pin:
                # 일부러 특정 버전에 묶어 둔 PC — 최신이 나와도 알리지 않는다.
                # (고정한 사람에게 매번 뜨는 알림은 거짓 알림이고, 그걸 끌 방법이 없다.)
                state = {"available": False, "current": cur, "latest": "", "pinned": pin,
                         "checkedAt": time.time(), "ok": True}
            else:
                new = latest_tag()
                state = {"available": bool(cur and new and cur != new),
                         "current": cur, "latest": new or "", "pinned": "",
                         "checkedAt": time.time(), "ok": bool(new)}
        except Exception:
            state = {"available": False, "current": "", "latest": "", "pinned": "",
                     "checkedAt": time.time(), "ok": False}
        with self._lock:
            self._state = state
            self._refreshing = False

    def _maybe_bg_refresh(self):
        with self._lock:
            fresh = (time.time() - self._state["checkedAt"]) < self.stale_after
            if fresh or self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self._refresh, name="update-check", daemon=True).start()

    def get(self):
        """캐시된 상태를 즉시 반환하고, 낡았으면 백그라운드 갱신을 건다(다음 조회에 최신)."""
        self._maybe_bg_refresh()
        with self._lock:
            return dict(self._state)

    def start(self):
        """시작 시 1회 미리 검사(백그라운드) — 첫 조회부터 최신에 가깝게."""
        self._maybe_bg_refresh()
