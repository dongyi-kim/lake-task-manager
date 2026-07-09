<!-- updated: 2026-07-08 — dev 실행/테스트 가이드 (fake 서버 + 앱 3환경 + pytest) -->
# 테스트 & 로컬 실행 가이드

Lake Task Manager 를 로컬에서 **구동·검증**하는 방법. 모든 명령은 **repo 루트** 기준.
(배경/설계는 `CLAUDE.md`, 진행/TODO 는 `PROGRESS.md`. 이 repo 는 개발용 — exe/배포는 `lake-task-manager-deploy`.)

> Windows 는 **PowerShell** 기준으로 적는다. bash(Git Bash/WSL) 명령은 각 절 끝에 병기.
> **macOS/Linux 는 §0.5** 로 따로 묶었다 (dev 만 — prod SSO/exe 는 Windows 배포 대상이라 제외).
> `pip install -r requirements.txt` 는 최초 1회.

---

## 0. 3분 요약

| 하고 싶은 것 | 명령 (PowerShell) |
|---|---|
| **UI만 빠르게** (Jira 불필요) | `python run.py` — 브라우저 자동 오픈, `env=mock` |
| **실 HTTP 경로 검증** (fake Jira) | 터미널1 `python run_fake.py` → 터미널2 `$env:LAKE_DOTENV=".env.dev"; python run.py` |
| **유닛테스트** | `python -m pytest -q` → `21 passed` |
| **단일 exe** | `python -m PyInstaller lake.spec` → `dist/lake-task-manager.exe` (배포는 `lake-task-manager-deploy` repo) |

---

## 0.5 macOS / Linux (dev)

코드는 순수 Python(FastAPI/uvicorn/requests) 이라 맥에서 그대로 돈다. **dev(mock·local)+pytest 만** — prod SSO 와 exe 빌드는 Windows 배포용이라 맥에선 다루지 않는다.

**최초 1회 — 가상환경 + 의존성** (repo 루트에서):
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # dev 전부 커버. requirements-sso.txt(playwright)는 prod 전용, 불필요
```
> 맥은 `python` 대신 **`python3`** 인 경우가 많다. venv 활성화 후엔 `python` 으로 통일된다(아래는 활성화 가정).

**mock — UI만 빠르게** (Jira 불필요):
```bash
python run.py            # http://localhost:8000/ 자동 오픈, env=mock
```

**local — Fake Jira 로 실 HTTP** (터미널 2개):
```bash
python run_fake.py                                   # 터미널1 (:8080)
LAKE_DOTENV=.env.dev python run.py                   # 터미널2 (local → :8080)
# 지연 주입:  FAKE_LATENCY_MS=800 python run_fake.py
```
`.env.dev` 는 OS 무관(포트/URL/basic auth 만). 맥에서도 그대로 사용.

**유닛테스트**:
```bash
python -m pytest -q      # 21 passed
```

**포트 점유 정리**(맥/리눅스):
```bash
lsof -ti :8000 -ti :8080 | xargs kill -9 2>/dev/null
```

나머지(§4 테스트 상세, §5 API 스모크, §7 트러블슈팅)는 OS 공통이니 아래 참고. `curl` 은 맥 기본 내장.

---

## 1. 환경 3종 (`JIRA_ENV`)

같은 계산 코드가 인증만 바꿔 세 곳에서 돈다.

| env | Jira | 인증 | 언제 |
|---|---|---|---|
| **mock** | 없음 (`app/world.py` in-process) | 없음 | UI 확인 기본값. 제일 빠름 |
| **local** | **Fake Jira 서버 :8080** (실 HTTP) | basic auth | REST+인증+캐시 경로 검증 |
| **prod** | 사내 Jira DC | Playwright SSO 세션 | 실데이터 (수동 검증만) |

`world.py` 를 mock(in-process)·fake(HTTP)가 **공유** → **mock 출력 == local 출력** (회귀 기준).

---

## 2. mock 으로 앱 띄우기 (제일 간단)

```powershell
python run.py
```

- `http://localhost:8000/` 자동 오픈. 콘솔에 `Lake Task Manager - http://localhost:8000/  (env=mock)`.
- Jira/DB/네트워크 불필요. 3탭(WBS · PMO_VIT · 인력워크로드) 전부 동작.
- `.env` 없으면 기본이 mock. 종료: `Ctrl+C`.

bash: `python run.py`

---

## 3. Fake Jira 서버로 앱 띄우기 (local, 실 HTTP)

**터미널 2개**가 필요하다.

**터미널 1 — Fake Jira/Confluence 서버 (:8080)**
```powershell
python run_fake.py
# 지연 주입(캐시/병렬 실측):  $env:FAKE_LATENCY_MS="800"; python run_fake.py
```
`Fake Jira DC 8.20.8 - http://localhost:8080  (latency=0ms)` 뜨면 준비 완료.

**터미널 2 — 앱 (local → :8080 으로 붙음)**
```powershell
$env:LAKE_DOTENV=".env.dev"; python run.py
```
- `.env.dev` 가 `JIRA_ENV=local`, `JIRA_BASE=http://localhost:8080`, basic auth(admin/admin), 커스텀필드 ID 를 정의.
- 브라우저 없이 `requests` basic auth 로 실제 HTTP 호출 → REST·인증·SQLite 캐시 경로 그대로 탄다.

bash:
```bash
python run_fake.py                          # 터미널1
LAKE_DOTENV=.env.dev python run.py          # 터미널2
```

> **검증 포인트**: mock 화면과 local 화면의 숫자(PMO 진척률 등)가 **완전히 같아야** 한다. 다르면 회귀.

---

## 4. 유닛테스트

```powershell
python -m pytest -q
```
- 기대: `21 passed`. Jira/서버 불필요 — 순수 계산·파서·world 검증.
- 커버: `test_world`(결정적 데이터) · `test_jql`/`test_atom`(fake 파서) · `test_progress`(SP 롤업) · `test_rollup`(WBS/Module/PMO 가중조합) · `test_config`(wbs_config/people 로더·검증).
- 한 파일만: `python -m pytest tests/test_rollup.py -q`

> 사내 **prod(SSO) Jira 에는 자동 테스트 절대 금지** (`CLAUDE.md` §9/§10). local(fake) 상대로만.

---

## 5. API 스모크 (앱이 뜬 상태에서)

```powershell
curl http://localhost:8000/api/health
curl http://localhost:8000/api/wbs
curl http://localhost:8000/api/vit
curl http://localhost:8000/api/workload
curl http://localhost:8000/api/refresh      # 캐시 + 프론트 memo 무효화
```
- 리소스 단위: `/api/epic/{key}/tree` · `/api/vit/{key}` · `/api/activity/{user}` 는 화면에서 펼칠 때 lazy 호출.
- **캐시 확인**: 같은 엔드포인트 두 번째 호출이 급격히 빨라지면 warm hit. `/api/refresh` 후 다시 느려지면 정상.

---

## 6. 단일 exe

```powershell
python -m PyInstaller lake.spec --distpath .. --workpath $env:TEMP\lakebuild --noconfirm
..\lake-task-manager.exe      # 더블클릭도 동일. 기본 env=mock
```
- 산출물: repo 루트 `lake-task-manager.exe` (config/.env 와 나란히). 최종 사용자는 이 파일만 있으면 된다.
- prod SSO 1회 로그인: `..\lake-task-manager.exe login` (설치된 Chrome 사용, Chromium 미번들).

---

## 7. 자주 막히는 것

- **`:8080` 안 붙음** → fake 서버(터미널1)부터 띄웠는지, `$env:LAKE_DOTENV=".env.dev"` 를 앱 터미널에 줬는지 확인.
- **mock/local 숫자 다름** → 회귀. `world.py` 한 소스인데 갈라졌다는 뜻 → `python -m pytest -q` 부터.
- **포트 점유** → 이전 프로세스 잔존. PowerShell: `Get-NetTCPConnection -LocalPort 8000,8080 | Select -Expand OwningProcess -Unique | % { Stop-Process -Id $_ -Force }`.
- **콘솔 한글 깨짐** → `run.py` 가 stdout/stderr 를 utf-8 로 강제하지만, 그래도면 `$env:PYTHONIOENCODING="utf-8"`.
- **prod 세션 만료** → 화면의 "SSO 로그인" 버튼 또는 `<exe> login` 재실행. SSO 는 반자동이 한계(무인 불가).
