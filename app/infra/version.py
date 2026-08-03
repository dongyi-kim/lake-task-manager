# version.py — 지금 도는 앱 코드가 **어느 버전인가**. 화면 표시·업데이트 판정의 단일 소스.
#
# 배포 정본은 **개발 repo 의 릴리즈 태그**다(태그 안 된 커밋은 유저에게 나가지 않는다).
# 그래서 버전 표시도 SHA 가 아니라 태그가 먼저다 — 지원할 때 "7e02047 쓰시네요" 보다
# "v2026.08.03 쓰시네요" 가 훨씬 낫고, 릴리즈 노트·TRACE 와 같은 축으로 맞춰진다.
#
# ★ 여기가 한 곳이어야 하는 이유: run.py 의 '떠 있는 인스턴스가 옛 버전인가' 판정은
#   디스크 값과 실행 중 값을 **문자열로 비교**한다. 두 곳이 각자 다른 방식으로 버전을
#   구하면(한쪽은 태그, 한쪽은 SHA) 늘 다르다고 나와 무한 재시작이 된다.

import subprocess
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent.parent   # app/infra/ -> app/ -> 소스 루트
_MARKER = ".fetched_rev"                                   # 런처(fetch-src.ps1)가 남기는 태그


def _git(root, *args, timeout=3):
    return subprocess.check_output(["git", "-C", str(root), *args],
                                   stderr=subprocess.DEVNULL, timeout=timeout).decode().strip()


def code_rev(root=None):
    """앱 코드 버전 문자열. 못 알아내면 "".

    ① .fetched_rev — 런처가 받아 둔 릴리즈 태그(ZIP 유저: git 이 아예 없다)
    ② git describe --tags --exact-match — 태그 위에 서 있는 체크아웃
    ③ 짧은 SHA — 태그가 아닌 커밋(개발 중)
    """
    root = Path(root or SRC_ROOT)
    try:
        m = root / _MARKER
        if m.is_file():
            v = m.read_text(encoding="utf-8", errors="ignore").strip()
            if v:
                return v[:60]
    except Exception:
        pass
    for args in (("describe", "--tags", "--exact-match"), ("rev-parse", "--short", "HEAD")):
        try:
            v = _git(root, *args)
            if v:
                return v
        except Exception:
            continue
    return ""


def pinned_rev(app_root):
    """config/lake-task-manager.rev 의 고정값. 고정이 아니면 "".

    이 파일이 특정 태그를 가리키면 그 PC 는 **일부러 묶여 있는 것**이다 — 최신이 나와도
    업데이트 알림을 띄우면 안 된다(고정한 사람에게 매번 거짓 알림이 된다).
    """
    try:
        p = Path(app_root) / "config" / "lake-task-manager.rev"
        if not p.is_file():
            return ""
        v = (p.read_text(encoding="utf-8", errors="ignore").splitlines() or [""])[0].strip()
        return "" if v.lower() in ("", "latest") else v
    except Exception:
        return ""
