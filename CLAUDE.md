# Lake Task Manager

Data Lake SI 사업의 **PMO 레벨 Task 관리** 도구.
현업이 쓰는 **Jira DC 8.20.8**(구버전, SSO 잠금 사내 인스턴스)을 SP/티켓의 source of truth로 두고,
그 위에 **Module → WBS Task → Epic 진척률 롤업**을 얇게 얹어 PMO가 사업 전체를 조망한다.

이 문서는 프로젝트의 배경·목표·설계 원칙·구현 접근법을 담는다.
Claude Code가 작업 시 이 맥락을 항상 우선한다.

---

## 0. 지금의 목표: Demo Page 우선 (현재 단계)

**코드 완성보다 먼저, 상위권자에게 UI 기능 컨펌을 받는 것이 최우선이다.**
그래서 가상의 조직 데이터를 만들고, 이를 시각화하는 self-contained 데모 페이지를 먼저 낸다.

### 데모-우선 로드맵
1. **[현재] Mock 데이터 데모** — 가상 Module/WBS/Epic/Story 데이터를 생성해
   단일 HTML 데모로 렌더. UI 컨셉을 상위권자에게 컨펌받는다. (Jira 불필요)
2. **[후속] Jira docker 실데이터 연동** — 로컬 Docker Jira 8.20.8 + Postgres를 띄우고
   REST seed로 같은 형태의 데이터를 심어, 데모가 실제 롤업으로 동작하게 한다.
3. **[후속] 운영 SSO 승격** — 사내 Jira에 Playwright SSO 세션으로 연결 → 반자동 스냅샷.
   가치 검증되면 PAT/서비스계정 정식 요청으로 무인 자동화 전환(§11).

**확정된 데모 결정:**
- 데모 형태: **단일 self-contained HTML** (빌드/서버 없이 `file://`로 열림).
- 데이터 소스: 생성한 **mock JSON 먼저**. Jira docker+seed는 후속 태스크로 분리.
- 데이터는 `demo/data.js`(`window.LAKE_DEMO`)로만 주입. 외부 네트워크 요청 0.

---

## 1. 배경: 우리가 풀려는 문제

우리는 대형 SI 사업이고, 하나의 팀이 **6개 모듈(파트)**로 구성된다.
SI 사업 특성상 기획 단계에서 정해진 **파트별 연간 WBS Task가 5~8개씩** 있다
(팀 전체로 약 40개 내외; 데모에서는 축소된 대표 세트를 쓴다).

### 요구사항 계층

**요구 1 — 파트별 WBS Task 진척률 추적이 필요하다.**
과거처럼 엑셀 시트로 일일이 관리하기 싫고, Jira로 일원화하고 싶다.

**요구 2 — WBS Task와 Epic을 1:1로 매핑할 수 없다.**
- WBS Task는 호흡이 너무 길어 하나의 Epic으로 담기엔 범위가 과도하다.
- 일부 WBS Task는 타 모듈이 작업해줘야 하는 내용도 포함하는데,
  이걸 다 한 Epic에 넣으면 가시성이 떨어진다.

**요구 3 — 그래서 채택한 매핑 모델 (N:M):**
- 하나의 WBS Task는 **여러 Epic**으로 구성될 수 있다.
- 하나의 Epic은 **여러 파트가 공유**하여 각자의 Task(하위 티켓)를 등록할 수 있다.
- 각 모듈은 자기가 참여한 Epic들의 진척률을 **가중치합**으로 조합해
  자신만의 WBS Task 진척률 산식을 정의한다.
  (같은 Epic이라도 모듈마다 가중치가 다를 수 있다.)

**요구 4 — Epic 진척도는 티켓 "개수"로 재면 안 된다.**
- 모든 Epic의 최종 티켓 수를 사전에 정의할 수 없다.
- 개발 중 개선/버그픽스 티켓이 계속 추가된다.
- 따라서 **Story Point 기반** 진척률이어야 한다:
  - 아직 안 쪼갠 미래 작업은 **Mock 티켓에 추정 SP**를 부여해 분모에 미리 반영.
  - 버그/운영성 티켓은 **SP=0**을 줘서 진척률에 영향 없게.

### 왜 이 도구가 필요한가
위 모델은 원래 Jira **Advanced Roadmap**이 하려던 것이지만,
우리는 **구버전 Jira DC(8.20.8)**를 써서 기능적 한계에 막혀 있다.
그렇다고 Jira 외 다른 툴 도입은 공수가 너무 크고, 엑셀 회귀도 싫다.
→ **Jira를 SP/티켓의 source of truth로 두고, PMO 롤업 계산만 얇은 외부 레이어(Lake Task Manager)에서 처리**한다.

---

## 2. 이 도구의 범위 (Scope)

**이 도구가 하는 것:**
- Jira에서 Epic별 자식 티켓의 SP를 긁어 **Epic 단위 진척률**을 산출한다.
- Epic 진척률 = `Σ(자식 SP where statusCategory=done) / Σ(자식 SP 전체)`.
- 그 Epic 진척률을 재료로 **Module → WBS Task → PMO 전체** 롤업을 조합해 보여준다(다운스트림 레이어).

**핵심 산출물(진짜 계산):** **"신뢰할 수 있는 Epic별 SP 진척률"** 하나다.
그 위의 WBS/모듈/PMO 롤업은 이 숫자를 재료로 쓰는 **다운스트림 조합**이다.

**경계 (분리해서 다룬다):**
- Epic 진척률 계산(`progress.py`, 순수 함수)은 인증·네트워크와 완전히 분리한다.
- WBS Task ↔ Epic의 N:M 매핑과 모듈별 가중치합은 **매핑 config를 입력으로 소비하는 별도 롤업 단계**다.
  데모에서는 이 다운스트림 레이어를 화면에 명시적으로 라벨링해 보여준다.

---

## 3. 데이터 모델 (Module → WBS → Epic)

PMO 조망을 위한 계층. **Epic/Story/SP는 Jira에 산다.** N:M 매핑과 가중치는 **Jira 밖(매핑 config)**에 산다.

```
Module (팀/파트, 6개)
  └─ WBS Task (모듈당 2~3개, 팀 전체 ~15개; 연간 기획 산출물)
       └─ Epic (WBS당 2~3개; N:M — 하나의 Epic을 여러 모듈이 다른 가중치로 공유 가능)  ← Jira
            └─ Story / Bug / Mock (SP 보유)                                              ← Jira
```

- **Module (6개, Data Lake 도메인 기준):**
  `Ingestion` · `Storage (Iceberg)` · `Catalog/Metastore` · `Query Engine` · `Governance/Security` · `Platform/DevOps`.
- **N:M & 가중치:** 매핑 config가 `(module, wbs, epic, weight)` 관계를 정의한다.
  같은 Epic이 모듈마다 다른 가중치로 참여한다.
- **롤업 산식:**
  - Epic 진척률 = `Σ(SP done)/Σ(SP total)` (statusCategory 기반, §6).
  - WBS Task 진척률(모듈별) = 참여 Epic 진척률의 **가중평균**(모듈별 weight).
  - Module 진척률 / PMO 전체 진척률 = 상위 가중/단순 집계.
  - `mock` SP는 **별도 항목으로 가시화**(분모엔 포함, 이중계산 리스크 경고).

---

## 4. 핵심 제약: 환경이 둘로 나뉜다

**이 프로젝트에서 가장 중요한 설계 원칙.**
개발하는 환경과 실제 코드가 도는 환경의 **인증 방식이 완전히 다르다.**

| 구분 | 개발/테스트 (로컬) | 운영 (사내) |
|------|-------------------|------------|
| Jira | Docker 로컬 8.20.8 | 사내 Jira DC 8.20.8 |
| 인증 | PAT 또는 basic auth | SSO(사내 인증서/인증프로그램) |
| 접근 방식 | localhost 자유 호출 | 사람이 브라우저로 1회 수동 로그인 → 세션 재사용 |
| 네트워크 | 제약 없음 | 사내망/방화벽 제약 |

### 왜 SSO가 문제인가
사내 Jira는 Atlassian ID/PW나 API 토큰이 아니라
**사내 자체 인증서 + 인증프로그램**으로 로그인한다.
- REST API 엔드포인트 자체는 살아있음이 확인됨(브라우저 세션으로 `/rest/api/2/*` 응답 옴).
- 하지만 PAT 메뉴 존재 여부 미확인, 관리자 플러그인 접근 불가(`/rest/plugins` 무반응).
- 정공법(PAT/서비스계정)은 조직 승인 사안이라, 당장은 **Playwright로 SSO 세션을 재사용**하는
  실용 우회로를 쓴다.

### 따라서 설계 원칙
- **인증 계층을 반드시 추상화**한다.
- **SP 계산 로직은 인증·환경과 완전히 분리**한다.
- 같은 계산 코드가 로컬(basic/PAT)과 운영(SSO 세션) 양쪽에서 그대로 돌아야 한다.
- 개발은 로컬에서 빠르게, 운영 배포 시 auth provider만 교체.

---

## 5. 검증된 구현 접근법 (샘플 코드 기반)

아래는 이미 **실제 사내 Jira에서 동작을 확인한** 방식이다(참고 PoC: `../jira_test.py`). 이 접근을 프로젝트로 확장한다.

### 5.1 운영 인증: Playwright SSO 세션 재사용

핵심 전략:
1. **최초 1회**: headed 브라우저로 사람이 직접 SSO/인증서 로그인.
   Playwright가 세션(`storage_state`)을 파일로 저장.
2. **이후**: 저장된 세션을 로드해 REST 호출.
   쿠키를 requests로 옮기지 않고 **Playwright의 `context.request`로 직접 호출**한다.
   → 브라우저 컨텍스트의 인증(쿠키+헤더)을 그대로 상속해서 SSO 헤더 요구에도 덜 깨진다.
3. 세션 만료 시 1)을 다시 실행 (수동 로그인 반복).

```python
# 최초 로그인 — headed, 수동 로그인 후 세션 저장
def login():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(JIRA_BASE, wait_until="domcontentloaded")
        input(">>> SSO/인증서 로그인 완료 후 Enter: ")
        context.storage_state(path=STATE_PATH)   # 세션 저장
        browser.close()

# 이후 — 저장된 세션으로 REST 호출
def api_session():
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state=STATE_PATH)
    return context, lambda: (browser.close(), p.stop())

def get_json(context, path):
    resp = context.request.get(f"{JIRA_BASE}{path}")
    if resp.status in (401, 403) or resp.status >= 500:
        raise RuntimeError(f"HTTP {resp.status} — 세션 만료 가능. login 재실행.")
    return resp.json()
```

**기존 크롬 프로필 재사용 옵션** (인증서가 프로필에 있으면 로그인 자체가 생략될 수 있음):
- `launch_persistent_context(user_data_dir=..., channel="chrome")` 로 실제 크롬 프로필 사용.
- 실행 전 크롬 완전 종료 필수(프로필 잠금 회피).
- 회사 관리형 크롬은 정책으로 막힐 수 있어, 안 되면 별도 컨텍스트 수동 로그인으로 폴백.

**알려진 약점 (반드시 인지):**
- SSO 세션은 만료가 짧음(수 시간~하루). → **무인 CronJob 불가, 반자동이 한계.**
  주기적 스냅샷 용도엔 충분하나 실시간 무인 대시보드엔 부적합.
- `headless=True`에서 SSO가 세션을 거부할 수 있음. 그럴 땐 `headless=False`로 테스트하고,
  로그인 때와 **동일한 user_agent**를 `new_context`에 명시하면 대개 해결.

### 5.2 개발 인증: PAT / basic auth (로컬)
로컬 Docker Jira는 SSO가 없으므로 basic auth나 PAT로 바로 REST 호출.
Playwright 경로를 타지 않아 개발 반복이 훨씬 빠르다.

### 5.3 필드 ID 조회
SP·Epic Link는 커스텀 필드라 인스턴스마다 ID가 다르다. **하드코딩 금지.**
```
GET /rest/api/2/field   → name에 "Story Points" / "Epic Link" 포함된 customfield_ ID 확보
```

### 5.4 Epic 자식 조회 + SP 롤업
```python
# JQL로 Epic 자식 페이징 조회
GET /rest/api/2/search
  jql    = "Epic Link" = EPIC-123
  fields = customfield_100XX(SP), status, issuetype, labels
  startAt / maxResults 페이징

# 집계
progress = Σ(SP where statusCategory.key == "done") / Σ(SP 전체)
```
- **완료 판정은 반드시 `statusCategory.key == "done"`.** 상태명(status name) 하드코딩 금지.
  사내 커스텀 워크플로가 많아 상태명은 신뢰 불가. statusCategory는 To Do/In Progress/Done 3종으로
  정규화되어 견고하다.
- 구버전/특정 설정에서 `"Epic Link"` JQL이 자식을 못 가져오면
  **Agile API** `GET /rest/agile/1.0/epic/{key}/issue`로 폴백.

---

## 6. 도메인 규칙 (진척률 계산)

- **SP=0 자동 제외**: 버그/운영 티켓에 SP=0을 주면 분자·분모 양쪽에 0을 더하므로
  진척률에 영향이 없다. 별도 필터 없이 수학적으로 빠진다.
  단 실수 방지용으로 이슈타입(Bug/Ops) 기반 제외를 **이중 안전장치**로 둔다.
- **Mock 티켓**: 아직 안 쪼갠 미래 작업. `mock` 라벨 필수.
  - 추정 SP 부여 + 상태 To Do → 분모에만 들어가 "아직 안 됨"이 정확히 반영됨.
  - 집계 시 mock SP를 **별도 항목으로 리포트**(검증용 가시성).
  - **이중 계산 리스크**: 실제 티켓이 쪼개져 생성되면 mock SP만큼 겹친다.
    → 실제 티켓 생성 시 mock SP를 깎거나 0으로 내리는 reconciliation은 **사람이 판단**.
    도구가 mock을 자동 삭제하지 않는다.
- **부분 크레딧 금지**: In Progress에 0.5 같은 가중은 노이즈만 크다. **Done/Total 이진**만.

---

## 7. 로컬 재현 환경 (Docker) — [후속 단계]

사내 Jira를 건드리지 않고 로컬에 **동일 버전(8.20.8)**을 띄워 개발·테스트한다.

```bash
# docker-compose.yml: atlassian/jira-software:8.20.8 + postgres:13
docker compose up -d
docker compose logs -f jira    # 부팅 2~5분
# http://localhost:8080 → 설치 마법사 → "I'll set it up myself"
#   → DB 자동/수동 입력 → 평가판 라이선스(my.atlassian.com 무료) → 관리자 계정
```

주의:
- 8.20.x는 JDK8/11 기반 → 이미지 태그는 `8.20.8` (jdk17 suffix 없음).
- 8.20.x는 EOL이라 Docker Hub 최신 태그 목록엔 안 뜰 수 있음.
  `docker manifest inspect atlassian/jira-software:8.20.8`로 존재 확인.
- Jira 8.20 최소 메모리 2GB 권장 → Docker Desktop 메모리 4GB+ 할당.
- **사내 라이선스 키를 로컬에 넣지 말 것.** 평가판으로 충분.

테스트 데이터: Scrum 프로젝트 1개 + Epic 여러 개 + Story에 SP 입력(일부 Done),
`mock` 라벨 티켓, SP=0 버그 티켓으로 엣지케이스 재현.
(데모 단계의 `demo/generate_demo.py`가 만드는 가상 데이터와 같은 형태를 REST seed로 심는다.)

---

## 8. 프로젝트 구조

```
lake-task-manager/
├── CLAUDE.md                   # 이 문서
├── .gitignore                  # jira_state.json, .env*, __pycache__ 제외
├── demo/                       # [현재 단계] mock 데이터 데모
│   ├── index.html              # self-contained PMO 대시보드 (빌드 없이 file://로 열림)
│   ├── data.js                 # 생성된 mock 데이터 (window.LAKE_DEMO)
│   └── generate_demo.py        # 가상 데이터 생성기 (stdlib only, 고정 seed) → data.js
├── docker-compose.yml          # [후속] 로컬 Jira DC 8.20.8 + Postgres
├── .env.local / .env.prod      # [후속] 환경 분리 (git 제외)
├── src/                        # [후속] 실제 파이프라인
│   ├── auth/
│   │   ├── base.py             # AuthProvider 인터페이스
│   │   ├── basic.py            # 로컬: PAT/basic auth
│   │   └── sso_session.py      # 운영: Playwright storage_state 재사용
│   ├── jira_client.py          # REST 호출 (AuthProvider 주입받음, 인증 종류 모름)
│   ├── progress.py             # SP 롤업 — 순수 계산, 환경/네트워크 의존성 0
│   ├── rollup.py               # Module/WBS 가중치 조합 (다운스트림; 매핑 config 소비)
│   └── config.py               # 환경별 설정 (JIRA_BASE, 필드ID, auth 종류)
└── tests/
    └── test_progress.py        # fixture 기반 유닛테스트 (Jira 불필요)
```

### 아키텍처 규칙
1. `progress.py`는 **순수 함수**. 입력=이슈 리스트, 출력=Epic 진척률 dict. 네트워크/인증 의존 금지.
2. `rollup.py`(다운스트림)는 Epic 진척률 + 매핑 config를 받아 WBS/Module/PMO를 조합. 역시 순수.
3. 인증은 `AuthProvider` 인터페이스 뒤로 숨긴다. 구현체를 갈아끼워 환경 전환.
4. `JiraClient`는 `AuthProvider`를 주입받아 REST 호출. **어떤 인증인지 몰라야 한다.**
5. 환경 선택은 `config.py` + 환경변수(`JIRA_ENV=local|prod`)로만. 코드 하드코딩 금지.
6. **데모의 롤업 로직(`generate_demo.py`)은 §5.4/§6 규칙을 그대로 따른다** — 나중에 `progress.py`/`rollup.py`로 승격할 때 산식이 일치하도록.

---

## 9. 테스트 원칙

- `progress.py`/`rollup.py`는 가짜 이슈 리스트(fixture)로 유닛테스트. Jira 없이 계산 로직 검증.
- 통합테스트는 **로컬 Docker Jira 상대로만**. 사내 Jira에 자동 테스트 절대 금지.
- 운영 SSO 경로(`SsoSessionProvider`)는 세션 만료·headless 감지 이슈로 **수동 검증**.
- 첫 검증: 로컬에서 Epic 하나의 집계 숫자가 Jira Scrum 보드의 Epic Report와 일치하는지 대조.
- 데모 검증: 생성기가 손계산 가능한 Epic 하나의 `done/total`과 일치하는지 self-assert.

---

## 10. 하지 말 것

- 사내 자격증명/세션 파일(`jira_state.json`, `.env.prod`) git 커밋 금지 → `.gitignore`.
- 상태명(status name) 하드코딩 금지 → `statusCategory.key` 사용.
- 커스텀 필드 ID 하드코딩 금지 → `config`에서 환경별 주입.
- 사내 Jira에 대한 **무인 CronJob 가정 금지** → SSO 세션 만료로 반자동이 한계.
- mock 티켓 자동 삭제/자동 SP 조정 금지 → reconciliation은 사람 판단.
- SSO 로그인 흐름에 자격증명을 스크립트에 심어 완전 자동화하는 방향 지양
  (유지보수 취약 + 보안 리스크). 검증되면 PAT/서비스계정 정식 요청으로 전환.
- 데모에 외부 네트워크 요청/CDN 의존 금지 → `file://`에서 그대로 열려야 한다.

---

## 11. 향후 전환 경로

이 Playwright 세션 방식은 **PoC/반자동용**이다.
로컬에서 SP 롤업 가치가 검증되면, 그 결과를 근거로
관리자에게 **PAT 또는 자동화용 서비스 계정**을 정식 요청한다.
정식 인증이 열리면 `AuthProvider`에 `PatAuthProvider`(운영용) 하나만 추가하면
나머지 코드는 그대로 무인 자동화로 승격된다.
