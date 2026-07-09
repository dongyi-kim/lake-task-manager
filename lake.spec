# PyInstaller spec — 단일 exe 빌드
#
# 두 가지 빌드:
#   (기본) mock/local 용 — 경량, Playwright 미포함:
#     pip install pyinstaller
#     pyinstaller lake.spec                 → dist/lake-task-manager.exe
#
#   prod(SSO) 용 — Playwright 포함(설치된 Chrome 재사용, Chromium 은 미번들):
#     pip install -r requirements-sso.txt pyinstaller
#     LAKE_BUILD=prod pyinstaller lake.spec → dist/lake-task-manager-prod.exe
#     (Windows PowerShell:  $env:LAKE_BUILD="prod"; pyinstaller lake.spec)
#
# 실행: exe 를 config/(jira.yml·wbs_config.yaml·people.yaml) 가 있는 폴더에 두고 더블클릭.
#   static·코드는 exe 내부 번들 / config·cache 는 exe 옆 외부 파일.
#   prod 최초 1회:  lake-task-manager-prod.exe login  (사내 SSO 통과 → 세션 저장)

import os
from PyInstaller.utils.hooks import collect_submodules, collect_all

PROD = os.environ.get("LAKE_BUILD") == "prod"

hiddenimports = collect_submodules("uvicorn") + [
    "app.main", "app.jira_client", "app.rollup", "app.progress",
    "app.cache", "app.mockdata", "app.vit", "app.workload", "app.settings",
    "app.auth.base", "app.auth.basic",
]
datas = [("app/static", "app/static")]
binaries = []
excludes = []

if PROD:
    # Playwright 파이썬 라이브러리(+드라이버) 포함. Chromium 브라우저는 미번들(설치 Chrome 사용).
    pw_datas, pw_binaries, pw_hidden = collect_all("playwright")
    datas += pw_datas
    binaries += pw_binaries
    hiddenimports += pw_hidden + ["app.auth.sso_session"]
else:
    excludes += ["playwright"]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="lake-task-manager-prod" if PROD else "lake-task-manager",
    console=True,
    onefile=True,
    upx=False,
)
