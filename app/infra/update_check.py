"""배포 repo(APP_ROOT) 가 원격보다 뒤처졌는지 검사 — UI '업데이트 가능' 표시용.

업데이트 = **배포 repo 의 원격(origin) 최신 여부**다(run.bat 이 부팅 때 하는 것과 같은 판정:
`git rev-list --count HEAD..@{u}`). git 이 없거나 repo 가 아니거나 네트워크가 막히면 조용히
'업데이트 없음(ok=False)' 으로 답한다 — 표시만 하는 기능이라 실패가 앱을 방해하면 안 된다.

엔드포인트는 **즉답**해야 하므로 fetch 는 백그라운드로 돌리고 캐시값을 돌려준다.
"""
import subprocess
import threading
import time


class UpdateChecker:
    def __init__(self, root, stale_after=600):
        self.root = str(root)
        self.stale_after = stale_after          # 이 시간(초) 지나면 백그라운드로 다시 fetch
        self._lock = threading.Lock()
        self._refreshing = False
        self._state = {"available": False, "behind": 0, "current": "",
                       "checkedAt": 0.0, "ok": False}

    def _git(self, *args, timeout=20):
        return subprocess.run(["git", "-C", self.root, *args],
                              capture_output=True, text=True, timeout=timeout)

    def _refresh(self, fetch=True):
        try:
            if fetch:
                self._git("fetch", "--quiet", timeout=25)
            cur = self._git("rev-parse", "--short", "HEAD", timeout=10)
            cnt = self._git("rev-list", "--count", "HEAD..@{u}", timeout=10)
            behind = int((cnt.stdout or "").strip() or "0")
            state = {"available": behind > 0, "behind": behind,
                     "current": (cur.stdout or "").strip(),
                     "checkedAt": time.time(), "ok": True}
        except Exception:
            # git 없음 / repo 아님 / upstream 없음 / 네트워크 실패 → 표시 안 함(조용히).
            state = {"available": False, "behind": 0, "current": "",
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
