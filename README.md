# Lake Task Manager

> Jira 위에 얹는 로컬 업무 허브 — 내 Task부터 티켓 편집, PMO 롤업, 사내 검색과 승인형 AI까지 한 화면에서.

Lake Task Manager(LTM)는 구버전 Jira Data Center를 사용하는 SI 조직을 위한 **로컬 우선 Jira 동반 앱**이다.
Jira를 대체하거나 별도의 업무 원장을 만들지 않는다. Jira를 source of truth로 유지하면서, 일상적인 티켓
작업과 팀·PMO 관점의 집계를 더 빠르고 일관된 UI로 제공한다.

초기에는 WBS·Epic 진척률을 보는 PMO 대시보드로 시작했지만, 현재는 읽기 전용 도구가 아니다.
담당 업무 탐색, 티켓 생성·수정·댓글·상태 전이, Jira·Confluence·Bitbucket 통합검색, 인력 워크로드,
현안 추적, 그리고 사용자 승인 후에만 쓰기를 실행하는 LTM Agent까지 포함한다.

## 제품의 성격

- **Jira가 원본이다.** LTM에서 한 변경도 Jira에 기록되며, 로컬에는 캐시·환경설정·최근 열람 정보만 둔다.
- **개인의 PC에서 실행한다.** 중앙 SaaS나 별도 업무 서버가 아니라 데스크톱 셸과 `localhost:4457`에서
  같은 앱을 사용한다. 시스템 트레이에 상주하고 기존 창을 재사용한다.
- **느린 사내망을 전제로 한다.** 캐시, 점진 렌더링, 요청 단위 재시도와 부분 성공을 이용해 일부 Jira
  호출이 느리거나 실패해도 이미 준비된 화면과 조작을 최대한 유지한다.
- **운영 관점과 실무 관점을 연결한다.** 개인의 Task와 SubTask를 다루면서 동일한 Jira 데이터를
  WBS·Epic·모듈·인력 단위로 다시 조립한다.
- **AI 쓰기는 사람이 결정한다.** Agent는 이력과 근거를 조사하고 초안·변경 계획을 제안하지만,
  티켓 생성·수정·댓글은 내용에 결속된 일회성 승인을 받은 뒤에만 실행한다.
- **구버전 DC 호환성이 우선이다.** 대상 환경은 Jira DC 8.20.8, Confluence DC 9.2.4,
  Bitbucket DC 7.17.2이며 클라우드 전용 API를 가정하지 않는다.

## 주요 기능

### Task — 매일 쓰는 업무 화면

- 담당자·보고자·모듈·프로젝트·상태·완료 기간 퀵필터
- 상태축별 점진 로딩과 더 보기, 필터 전환 시 이전 결과의 캐시 재사용
- Parent Task와 SubTask 그룹, 하위 담당자·진척·접기/펼치기
- 마감일·우선순위·Epic·담당자 기준 정렬과 실시간 통계
- 티켓 하나가 변경되면 전체 목록 대신 영향받는 카드·부모·통계만 갱신

### 티켓 작업 공간

- 앱 안에서 Task·Epic·SubTask 생성, 필드 수정, 담당자 변경과 상태 전이
- 본문·댓글 리치 에디터: 표, 이미지, 체크리스트, 코드, 링크, 티켓·문서·사용자 멘션
- 댓글·첨부·문서 링크·티켓 링크의 추가, 수정과 삭제
- 타임라인, 변경 이력, 계층·형제·관련 티켓을 필요한 시점에 비동기로 조회
- 하위 티켓이 많은 경우 현재 티켓 이력을 먼저 보여주고 SubTask 이력은 사용자가 요청할 때 추가

### 팀과 PMO 뷰

- **WBS Dashboard** — Epic의 Story Point 진척률을 Module → WBS → PMO로 가중 롤업
- **현안(PMO_VIT)** — 주요 현안과 하위 티켓의 신규·진행·완료 변화를 추적
- **인력 워크로드** — 담당자별 진행·대기·최근 완료·VoC·Epic 분포와 활동 확인
- Jira `statusCategory`를 완료 기준으로 사용하며 SubTask는 Parent Task의 Epic으로 집계

### 통합검색과 빠른 이동

- `/` 또는 `Ctrl+K`로 Jira·Confluence·Bitbucket 통합검색
- 최근 열어본 티켓·문서를 서버 응답보다 먼저 표시
- 티켓 번호는 짧은 뱃지, 명시적인 Jira 링크는 제목·상태가 있는 상세 뱃지로 표현
- 본문·댓글·Agent 답변에서 동일한 티켓·문서·사용자 호버 정보 재사용

### LTM Agent

- 막연한 요청을 Jira 이력·Confluence 문서·설정된 지식에서 조사
- 필요한 정보만 질문하고 티켓 트리 또는 기존 티켓 변경 계획을 제안
- 근거와 실제 실행 효과를 승인 카드로 분리
- 승인된 payload만 생성·수정·댓글 도구에 전달
- OpenAI, Azure OpenAI, OpenAI 호환 로컬/사내 모델과 결정적 fake provider 지원

Agent의 상세 계약과 개발 규칙은 [`app/agent/README.md`](app/agent/README.md)와
[`app/agent/AGENT.md`](app/agent/AGENT.md)를 따른다.

## 동작 구조

```text
데스크톱 셸 / localhost 브라우저
              │
              ▼
     Vue 3 무빌드 SPA
              │
              ▼
       FastAPI route 계층
         │           │
         ▼           ▼
  domain 조립     LTM Agent ── 사용자 승인
         │           │
         └─────┬─────┘
               ▼
       JiraClient facade
         │           │
         ▼           ▼
 SQLite TTL 캐시   AuthProvider
                       │
                       ▼
        Jira / Confluence / Bitbucket DC
```

검색 결과, 상세 티켓, 사용자와 Epic 메타데이터는 서로 재사용된다. 지원되는 JQL은 정규화한 뒤 OR leaf로
분해해 개별 캐싱하고, 앱에서 합집합·중복 제거·정렬·pagination을 수행한다. Jira 쓰기가 성공하면
mutation 정보와 issue→leaf 역인덱스로 영향받는 상세·목록·집계 캐시만 선택적으로 갱신한다.

## 실행 환경

동일한 도메인 계산과 Jira 응답 파서가 세 환경에서 동작하며 인증 provider만 바뀐다.

| 환경 | 용도 | 상류 연결 | 인증 |
|---|---|---|---|
| `mock` | 기본 개발·UI 회귀 | `jira820` in-process, 소켓 없음 | 없음 |
| `local` | 실제 HTTP·캐시·지연 검증 | `jira820` on `127.0.0.1:8080` | Basic |
| `prod` | 사내 실사용 | Jira·Confluence·Bitbucket DC | Playwright SSO 세션 |

`mock`과 `local`은 [`app/mock/world.py`](app/mock/world.py)의 동일한 결정적 데이터를
[`app/mock/fakebridge.py`](app/mock/fakebridge.py)로 직렬화한다. 따라서 전송 계층을 제외한 결과가
같아야 하며, 이 불변식은 회귀 테스트로 보호한다.

## 빠른 시작

Python 3.11 이상을 권장한다. 명령은 저장소 루트 기준이다.

```powershell
git clone git@github.com:dongyi-kim/lake-task-manager.git
Set-Location lake-task-manager

py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --reload
```

기본 주소는 `http://127.0.0.1:4457`이다. 개발 중에는 JS·CSS·HTML 변경까지 감시하는
`python run.py --reload`를 표준 진입점으로 사용한다.

macOS·Linux의 mock/local 개발도 같은 Python 코드로 가능하다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py --reload
```

### 실제 HTTP와 지연 상황 확인

```powershell
# fake Jira(:8080)와 LTM(local)을 함께 실행
.\.venv\Scripts\python.exe run_local.py

# 캐시·점진 로딩처럼 지연이 필요한 시나리오에서만 사용
$env:FAKE_LATENCY_MS="800"
.\.venv\Scripts\python.exe run_local.py
```

수동으로 분리하려면 `run_fake.py`를 먼저 실행한 뒤 다른 터미널에서
`$env:JIRA_ENV="local"; python run.py --reload`를 실행한다.

### Prod SSO 의존성

최종 사용자는 배포 저장소의 `run.bat`을 사용하며 런처가 의존성과 Chromium을 준비한다. 소스에서 prod
경로를 직접 검증할 때만 다음 의존성이 추가로 필요하다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-sso.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

사내 prod 데이터에 대한 자동 테스트는 수행하지 않는다.

## 설정

개발 샘플과 최종 사용자 설정은 `config/`에 있다.

- [`config/jira.yml`](config/jira.yml) — 환경, Jira/Confluence/Bitbucket 주소, 검색 범위, 필드, 매니저, 캐시와 포트
- [`config/wbs_config.yaml`](config/wbs_config.yaml) — Module·WBS·Epic N:M 매핑과 가중치
- [`config/people.yaml`](config/people.yaml) — 모듈별 인력과 표시 설정
- [`config/module-aliases.yaml`](config/module-aliases.yaml) — 모듈 명칭 정규화
- `CONFIG_DIR` — 별도 설정 디렉터리를 지정하는 override

환경변수는 YAML보다 우선한다. 대표적으로 `JIRA_ENV`, `JIRA_BASE`, `APP_PORT`, `CONFIG_DIR`,
`LAKE_CACHE_DIR`을 사용할 수 있다. 기본 포트는 모든 환경에서 `4457`이다.

SQLite 캐시, SSO 세션, 브라우저 프로필과 개인 설정은 `.cache/` 아래에 저장되며 커밋하지 않는다.

## 프로젝트 구조

```text
lake-task-manager/
├─ config/                         # 환경·검색·WBS·인력 설정
├─ run.py                          # 표준 앱/개발 런처
├─ run_local.py / run_fake.py      # 실제 HTTP fake 환경
├─ app/
│  ├─ main.py                      # ASGI 조립, 인증·앱 생명주기
│  ├─ routes/                      # dashboard, Task, ticket, resource API
│  ├─ auth/                        # mock/basic/SSO AuthProvider
│  ├─ jira/                        # Jira facade와 캐시·identity·media·workload 정책
│  ├─ domain/                      # Task·WBS·VIT·workload·검색 도메인 조립
│  ├─ infra/                       # SQLite cache, settings, prefs, 진단
│  ├─ content/                     # Jira wiki/HTML 변환과 안전한 렌더링
│  ├─ mock/                        # 결정적 Jira/Confluence 개발 세계
│  ├─ agent/                       # 조사·계획·승인형 업무 Agent
│  └─ static/                      # Vue 3 SPA, 컴포넌트와 스타일
└─ tests/
   ├─ agent/                       # core, contracts, evaluation, integration
   ├─ jira/                        # query, tickets, workload, people, content
   ├─ frontend/                    # editor, shell, static assets, ticket dialog
   ├─ routes/ / runtime/ / domain/
   └─ quality/ / support/
```

각 핵심 패키지의 책임과 규칙은 해당 README에 더 자세히 적혀 있다.

- [`app/jira/README.md`](app/jira/README.md)
- [`app/domain/README.md`](app/domain/README.md)
- [`app/infra/README.md`](app/infra/README.md)
- [`app/auth/README.md`](app/auth/README.md)
- [`app/mock/README.md`](app/mock/README.md)

## 테스트

개발 중에는 변경한 기능의 폴더나 파일만 실행한다. 전체 외부 API 없는 suite는 PR과 `main` push의
GitHub Actions가 담당한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
$runId = "jira-query-" + [guid]::NewGuid().ToString("N")
.\.venv\Scripts\python.exe -B -m pytest tests/jira/query -q `
  "--basetemp=.cache/test-tmp/$runId" -o cache_dir=.cache/pytest
```

기능별 예시:

- `tests/frontend/editor` — 본문·댓글 에디터와 Jira 직렬화
- `tests/frontend/ticket_dialog` — 티켓 다이어로그
- `tests/jira/query` — JQL 분해·검색·캐시
- `tests/jira/workload` — 인력·Epic 집계
- `tests/agent/core/work_architect` — Agent 업무 계획 파이프라인

상세 정책은 [`docs/TESTING.md`](docs/TESTING.md)를 참고한다. 실 LLM/API 배터리는 비용·secret·사람 판독이
필요하므로 명시적으로 승인된 로컬 환경에서만 실행한다.

## 배포와 릴리즈

최종 배포는 [`lake-task-manager-deploy`](https://github.com/dongyi-kim/lake-task-manager-deploy)가
이 저장소의 릴리즈 태그를 받아 **소스 그대로 실행**하는 방식이다. `run.bat`이 Python 환경, 의존성,
Playwright Chromium과 자동 업데이트를 관리한다. 이 저장소의 `main` 커밋만으로는 사용자에게 배포되지
않으며, 공개 GitHub Release와 CalVer 태그(`vYYYY.MM.DD[.N]`)가 있어야 한다.

릴리즈 절차는 [`docs/RELEASE.md`](docs/RELEASE.md)를 따른다.

## 개발 원칙

- 완료 판정은 상태명이 아니라 `statusCategory.key == "done"`을 사용한다.
- Story Point·Epic Link 같은 커스텀 필드 ID와 조직별 매핑은 코드에 하드코딩하지 않는다.
- `app/domain/progress.py`와 `rollup.py`는 네트워크·인증에 의존하지 않는 순수 계산으로 유지한다.
- `JiraClient`는 인증 구현을 모르며 모든 환경이 같은 parser·cache·mutation 경로를 사용한다.
- 인증·권한·네트워크 실패를 정상적인 빈 결과로 캐싱하지 않는다.
- 성공한 쓰기만 관련 캐시를 갱신하며, 실패한 쓰기는 기존 정상 캐시를 불필요하게 지우지 않는다.
- prod SSO 세션과 자격증명, `.cache/` 산출물은 커밋하지 않는다.

상세한 저장소 작업 규칙은 [`AGENTS.md`](AGENTS.md), 진행 상태는 [`PROGRESS.md`](PROGRESS.md)에서 확인한다.
