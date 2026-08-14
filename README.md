# Lake Task Manager — 개발자 가이드

구버전 **Jira DC**(8.20.8) 환경에서 SI 프로젝트를 수행할 때 쓰는 **읽기전용 PMO 진척 대시보드 유틸리티**.
Jira 를 source of truth 로 두고, 그 위에 **Module → WBS Task → Epic 진척률 롤업**을 얇게 얹는다.
**FastAPI 백엔드 + Vue3 무빌드 SPA**, 소스 실행 배포(배포 repo `run.bat`).

> **타깃 사내 인스턴스 버전(고정):** Jira DC **8.20.8** + Confluence DC **9.2.4**.
> 통합 검색(우상단)이 Confluence CQL·9.x URL 스펙에 맞춰 파싱하며, dev mock(jira820)도 같은 버전을 구성한다
> (`app/fakebridge.py` 의 `confluence_version="9.2.4"`).

> 최종 사용자용 사용 안내는 배포 repo 루트 README(사용자 가이드)에 있다.
> 설계·도메인 규칙 상세는 [`AGENTS.md`](AGENTS.md), 진행/백로그는 [`PROGRESS.md`](PROGRESS.md).
> **AI 에이전트(업무 착수 어시스턴트)** — 개발 계약·역할·프롬프트·도구 지침은 [`app/agent/AGENT.md`](app/agent/AGENT.md).
> 명령은 모두 **repo 루트** 기준. Windows 는 **PowerShell**, 그 외는 bash 를 병기한다.

---

## 1. 3 환경 (`JIRA_ENV`)

같은 계산 코드가 세 환경에서 동일하게 돈다. 환경은 `config/jira.yml` 의 `env:` 로 정하고, **환경변수 `JIRA_ENV` 로 override** 한다.

| 환경 | 설명 | Jira | 인증 |
|------|------|------|------|
| `mock` | 외부 mock [`jira820`](https://pypi.org/project/jira820) 을 **in-process**로(이 프로젝트 world 주입, `app/fakebridge.py`). **개발 기본값.** | 불필요 | 없음 |
| `local` | `run_fake.py`(:8080, 같은 jira820)에 **실 HTTP**. REST+인증+캐시 경로 검증. | jira820(:8080) | basic auth |
| `prod` | 사내 Jira DC, Playwright SSO 세션 재사용. 실데이터(수동 검증만). | 사내 DC | SSO 세션 |

> **핵심 불변식**: mock(in-process)·local(실 HTTP) 모두 **같은 jira820(같은 world·직렬화기)** → **mock 출력 == local 출력**.
> 다르면 회귀(`tests/test_local_parity.py` 자동 가드). *(dev fake 는 이전 `tools/fake_jira`·`mockdata.py` → jira820 로 일원화됨.)*

---

## 2. 개발 셋업

```bash
git clone git@github.com:dongyi-kim/lake-task-manager.git
cd lake-task-manager
pip install -r requirements.txt        # 최초 1회 (app + pyinstaller)
# 운영(SSO) 경로까지 만지려면:  pip install -r requirements-sso.txt
```

**macOS / Linux**: 코드는 순수 Python(FastAPI/uvicorn/requests)이라 그대로 돈다. dev(mock·local)+pytest 만 — prod SSO·exe 빌드는 Windows 배포용.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # dev 전부 커버 (requirements-sso 는 prod 전용, 불필요)
```

- 설정은 **`config/jira.yml`**(중첩 YAML), 매핑은 `config/{wbs_config.yaml,people.yaml}`. dev 체크아웃에선 `config/` 가 자동으로 잡힌다.

---

## 3. 실행

```powershell
# mock 로컬 개발 (기본) — hot reload, http://localhost:4457 자동 오픈
python run.py --reload

# hot reload 없이 일반 앱 창으로 실행
python run.py

# local 원클릭 — fake(:8080) + 앱(local), hot reload
python run_local.py

# local (수동, 터미널 2개) — 위 원클릭과 동일한 경로. 개별 제어가 필요할 때
python run_fake.py                              # 터미널1 — Fake Jira :8080
$env:JIRA_ENV="local"; python run.py --reload   # 터미널2 — 앱(local → fake)

```

bash: `JIRA_ENV=local python run.py --reload` / 지연 주입: `FAKE_LATENCY_MS=800 python run_local.py` (또는 `run_fake.py`)

- `config/jira.yml` 의 dev 기본이 `env: mock` 이라 `python run.py --reload` 는 mock. fake 검증만 `JIRA_ENV=local` 로 켠다.
- 콘솔에 `Lake Task Manager dev - http://localhost:4457/  (env=mock, hot reload)`. 종료 `Ctrl+C`.
- Windows에서는 개발 서버가 `LakeTaskManagerDev.exe`, prod pystray가 `LakeTaskManager.exe`로 표시된다. hot reload와 프로세스 이름을 함께 유지하려면 직접 `uvicorn` 대신 `python run.py --reload`를 사용한다.
- **검증 포인트**: mock 화면과 local 화면의 숫자(PMO 진척률 등)가 **완전히 같아야** 한다. 다르면 회귀.

### API 스모크 (앱이 뜬 상태에서)
```bash
curl http://localhost:4457/api/health
curl http://localhost:4457/api/wbs
curl http://localhost:4457/api/vit
curl http://localhost:4457/api/workload
curl http://localhost:4457/api/refresh      # 캐시 + 프론트 memo 무효화
```
- 리소스 단위 `/api/epic/{key}/tree`·`/api/vit/{key}`·`/api/activity/{user}` 는 화면에서 펼칠 때 lazy 호출.
- **캐시 확인**: 같은 엔드포인트 2번째 호출이 급격히 빨라지면 warm hit. `/api/refresh` 후 다시 느려지면 정상.

---

## 4. 테스트

```bash
python -m pytest tests/test_rollup.py -q   # 로컬: 변경 관련 test만
```

- PR과 `main` push는 GitHub Actions가 외부 API 없는 전체 `pytest` suite를 자동 실행.
- 로컬에서는 변경 범위에 필요한 test만 실행하고 전체 판정은 CI 결과를 사용.
- 실 LLM/API 배터리는 비용·secret·사람 판독이 필요하므로 승인된 로컬 환경에서만 수동 실행.
- 상세 명령과 범위: [`docs/TESTING.md`](docs/TESTING.md)
- 사내 **prod(SSO) Jira/Confluence 및 실 LLM API에 자동 테스트 절대 금지**.

---

## 5. 배포 (소스 실행)

**exe 빌드는 없다.** 최종 배포는 **배포 repo** `dongyi-kim/lake-task-manager-deploy`(이 repo 를 submodule 로 핀)에서
**소스를 그대로 실행**한다 — 배포 repo 의 `run.bat` 이 최초 1회 venv + 의존성 + Chromium 을 자동 구성하고 `run.py` 를 띄운다.

> 회사 관리형 Chrome 이 SSO 자동화를 막아 exe(playwright channel=chrome) 방식이 깨졌다 →
> **Playwright 전용 Chromium**(`playwright install chromium`)을 쓰는 소스 실행으로 전환. Chromium 은 프로즌 exe 로 안정적으로 번들하기 어려워 소스 실행이 정석.

## 6. 릴리즈 = **태그 달기**

유저의 `run.bat` 은 이 repo 의 `releases/latest` 를 보고 **그 태그의 소스**를 받아 실행한다.
그래서 **태그가 안 된 커밋은 유저에게 나가지 않는다** — dev 커밋은 자유롭게 쌓아도 된다.
(git 이 없는 유저도 태그 아카이브를 직접 받으므로 똑같이 자동 업데이트된다.)

배포 repo 루트에서:

```bash
powershell -File bin/release.ps1              # 오늘 날짜로 배포 (CalVer: v2026.08.03)
powershell -File bin/release.ps1 -Pre         # prerelease — 유저에겐 안 나감(사내 검증용)
powershell -File bin/release.ps1 -DryRun      # 무엇을 할지만 출력
# 검증 후 승격:
gh release edit v2026.08.03 --prerelease=false --repo dongyi-kim/lake-task-manager
```

- **롤백은 태그 삭제가 아니라 새 태그로.** 이미 받아 간 클라이언트·CDN 캐시 때문에 삭제는 지저분하다.
- 런처(`bin/`)나 `config/` 구조가 바뀌어 **옛 런처로는 못 도는** 배포라면 `RELEASE.json` 의
  `minLauncher` 를 올린다 → 옛 런처 유저에게 소스를 적용하지 않고 재설치를 안내한다.
  자세히: [docs/RELEASE.md](docs/RELEASE.md).

---

## 7. 프로젝트 구조 (요약)

```
lake-task-manager/
├── config/{jira.yml,wbs_config.yaml,people.yaml}   # 환경설정 + 매핑 (git 커밋, 사용자 편집)
├── run.py / run_fake.py            # 앱 런처 / Fake Jira 서버 런처
├── lake.spec                       # PyInstaller 단일 exe 스펙
├── requirements.txt / requirements-sso.txt
├── app/                            # FastAPI 백엔드 + 정적 프론트
│   ├── main.py                     # 라우트(/api/wbs·vit·workload[/{user}]·login·health·refresh) + static
│   ├── settings.py                 # config/jira.yml + YAML 로더/검증, frozen(exe)·컨테이너 경로 인식
│   ├── progress.py / rollup.py     # 순수 계산 (Epic SP 롤업 / WBS·Module·PMO 가중 조합)
│   ├── jira_client.py / cache.py   # REST 클라이언트(AuthProvider 주입) / SQLite TTL 캐시
│   ├── vit.py / workload.py        # 기능2 현안 / 기능3 워크로드
│   ├── world.py / worldcontent.py  # 단일 결정적 데이터 세계
│   ├── fakebridge.py               # world 를 외부 jira820 서버에 주입 (mock/local 공용 dev 백엔드)
│   ├── auth/{base,basic,sso_session,inprocess}.py  # 인증 추상화 (inprocess=mock용 jira820 in-process)
│   └── static/                     # Vue 3 무빌드 SPA
└── tests/                          # world/progress/rollup/config/names/local_parity 유닛테스트

dev fake Jira = 외부 오픈소스 [`jira820`](https://pypi.org/project/jira820) (requirements).
```

### 아키텍처 규칙 (요약 — 상세는 `AGENTS.md`)
- `progress.py`/`rollup.py` 는 **순수 함수**. 네트워크·인증 의존 금지.
- 인증은 `AuthProvider`(`app/auth`) 뒤로 추상화 → 구현체 교체로 환경 전환. `JiraClient` 는 어떤 인증인지 몰라야 한다.
- 환경 선택은 `config/jira.yml`(`env`, 환경변수 `JIRA_ENV` override) 로만. **커스텀 필드 ID·매핑 하드코딩 금지** (전부 config).
- 완료 판정은 상태명이 아니라 **`statusCategory.key == "done"`**.

---

## 8. 자주 막히는 것

| 증상 | 원인 / 조치 |
|---|---|
| `:8080` 안 붙음 | fake 서버(터미널1)부터 띄웠는지, 앱 터미널에 `JIRA_ENV=local` 줬는지 확인 |
| mock/local 숫자 다름 | 회귀 — `world.py` 한 소스인데 갈라짐. `python -m pytest -q` 부터 |
| 포트 점유 | PowerShell: `Get-NetTCPConnection -LocalPort 4457,8080 \| Select -Expand OwningProcess -Unique \| % { Stop-Process -Id $_ -Force }` / mac·linux: `lsof -ti:4457 -ti:8080 \| xargs kill -9` |
| 콘솔 한글 깨짐 | `run.py` 가 utf-8 강제하지만, 그래도면 `PYTHONIOENCODING=utf-8` |
| prod 세션 만료 | 화면의 "SSO 로그인" 버튼 또는 `<exe> login` 재실행 (SSO 는 반자동이 한계) |
| exe 가 옛 화면 | 프론트 번들 캐시 — 브라우저 `Ctrl+Shift+R`, exe/서버 재시작 |
