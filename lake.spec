# PyInstaller spec — 단일 exe 빌드 (lake-task-manager.exe)
#
# 하나의 exe 로 mock·local·prod 모두 커버한다. Playwright(사내 SSO 경로)를 항상 번들:
#   설치된 Chrome 재사용(Chromium 은 미번들) → exe 하나로 사내 prod 까지 무설치 실행.
#
#   pip install -r requirements-sso.txt        # playwright 포함(빌드에 필요)
#   pyinstaller lake.spec                       → dist/lake-task-manager.exe
#   (권장: 배포 repo 의  python build/build.py  — 의존성 자동 설치)
#
# 실행: exe 를 config/(jira.yml·wbs_config.yaml·people.yaml) 가 있는 폴더에 두고 더블클릭.
#   static·코드·playwright 는 exe 내부 번들 / config·cache 는 exe 옆 외부 파일.
#   prod 최초 1회:  lake-task-manager.exe login  (사내 SSO 통과 → 세션 저장)

from PyInstaller.utils.hooks import collect_submodules, collect_all

hiddenimports = collect_submodules("uvicorn") + [
    "app.main", "app.jira_client", "app.rollup", "app.progress",
    "app.cache", "app.mockdata", "app.vit", "app.workload", "app.settings",
    "app.auth.base", "app.auth.basic", "app.auth.sso_session",
]
datas = [("app/static", "app/static")]
binaries = []

# Playwright 파이썬 라이브러리(+드라이버) 항상 포함. Chromium 브라우저는 미번들(설치 Chrome 사용).
pw_datas, pw_binaries, pw_hidden = collect_all("playwright")
datas += pw_datas
binaries += pw_binaries
hiddenimports += pw_hidden

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="lake-task-manager",
    console=True,
    onefile=True,
    upx=False,
)
