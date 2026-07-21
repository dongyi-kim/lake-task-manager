# Lake Task Manager

구버전 Jira DC 환경에서 SI 프로젝트를 수행할 때 쓰는 **PMO 레벨 Task 관리** 유틸리티.
현업이 쓰는 **Jira DC 8.20.8**(구버전, SSO 잠금 사내 인스턴스)을 SP/티켓의 source of truth로 두고,
그 위에 **Module → WBS Task → Epic 진척률 롤업**을 얇게 얹어 PMO가 프로젝트 전체를 조망한다.

> **타깃 사내 인스턴스 버전(고정):** Jira DC **8.20.8** + Confluence DC **9.2.4**.
> 통합 검색(우상단)이 Confluence CQL 을 이 버전 스펙(9.x URL `/spaces/{space}/pages/{id}/{title}` 등)에
> 맞춰 파싱한다. dev mock(jira820)도 같은 버전을 구성한다(`app/fakebridge.py` 의 `confluence_version="9.2.4"`).

이 문서는 프로젝트의 배경·목표·설계 원칙·구현 접근법을 담는다.
Claude Code가 작업 시 이 맥락을 항상 우선한다.

---

## 0. 지금의 목표: Demo Page 우선 (현재 단계)

**코드 완성보다 먼저, 상위권자에게 UI 기능 컨펌을 받는 것이 최우선이다.**
그래서 가상의 조직 데이터를 만들고, 이를 시각화하는 self-contained 데모 페이지를 먼저 낸다.

### 로드맵 (상세 진행/TODO 는 `PROGRESS.md`)
1. **[완료] Mock 데이터 데모** — `demo/` 단일 HTML Gantt 로 UI 컨셉 확정.
2. **[진행] 실서비스화** — FastAPI 백엔드 + 정적 프론트의 로컬 웹앱으로 승격.
   - **Phase A [완료]**: 인프라(exe 패키징)·config·백엔드 스캐폴드·mock 모드·기능1 API/프론트.
   - **Phase B**: 기능1 WBS Dashboard 를 실 Jira 데이터로.
   - **Phase C**: 기능2 PMO_VIT 현안 트래킹 (MVP → 인터뷰).
   - **Phase D**: 기능3 인력 워크로드/활동 (MVP → 인터뷰).
3. **[후속] 운영 SSO 승격** — 사내 Jira에 Playwright SSO 세션 연결 → 반자동 스냅샷.
   검증되면 PAT/서비스계정 정식 요청으로 무인 자동화 전환(§11).

**확정된 설계 결정:**
- 구조: **FastAPI 백엔드 + 정적 프론트**. git 공유 로컬 웹앱 (공개 웹서비스 없음).
- 실행: **소스 실행**(배포 repo 의 `run.bat` 이 venv+의존성+Chromium 자동 구성 후 `run.py`). exe 빌드 없음. (dev = Fake Jira 서버 `run_fake.py`)
- 저장: 매핑/인력/가중치 = **YAML config**(커밋), Jira 캐시/스냅샷 = **SQLite**(gitignore).
- 3 환경(`JIRA_ENV=mock|local|prod`)에서 **동일 계산 코드**가 돈다.

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

- **Module (6개, 예시 도메인 기준):**
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
| Jira | Fake Jira 서버(:8080) / Docker 로컬 8.20.8 | 사내 Jira DC 8.20.8 |
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

**[현행 결정] 설치된 Chrome(`channel="chrome"`)·프로필 재사용은 폐기 — 번들 Chromium 사용.**
- 시도했던 `channel="chrome"`(설치 Chrome) 및 `launch_persistent_context`(실 프로필 재사용)는
  **회사 관리형 Chrome 정책이 자동화/인증서 흐름을 차단**해 SSO 가 깨짐(검증 실패).
- → **Playwright 번들 Chromium**(`p.chromium.launch()`, channel 없음)으로 사람이 직접 로그인하는
  방식으로 복귀(초기 PoC 방식). Chromium 은 `playwright install chromium` 으로 준비(런처 자동).
- **로그인은 앱 창 하나로 통합**(§8 실행 방식): 앱 창을 Jira 로 이동 → 로그인 → `/myself` 감지 →
  storage_state 저장 → `reset_provider` → 앱 복귀. (`app/auth/sso_session.py`, `run.py`)

**알려진 약점 (반드시 인지):**
- SSO 세션은 만료가 짧음(수 시간~하루). → **무인 CronJob 불가, 반자동이 한계.**
  주기적 스냅샷 용도엔 충분하나 실시간 무인 대시보드엔 부적합.
- `headless=True`에서 SSO가 세션을 거부할 수 있음. 그럴 땐 `headless=False`로 테스트하고,
  로그인 때와 **동일한 user_agent**를 `new_context`에 명시하면 대개 해결.

### 5.2 개발 인증: PAT / basic auth (로컬)
로컬 Fake Jira(:8080)/Docker Jira는 SSO가 없으므로 basic auth나 PAT로 바로 REST 호출.
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
- **SP 누락(None) 기본값**: SP 필드가 비어 있는 티켓은 기본값으로 계산한다.
  **Bug → 0, 나머지 → 1** (`progress.sp_of`). *명시적으로 0* 이 입력된 티켓은 그대로 0이며,
  '누락'과 '명시적 0'을 구분한다. (정규화에서 `sp=None`을 보존해야 이 규칙이 동작한다.)
- **Mock 티켓**: 아직 안 쪼갠 미래 작업. `mock` 라벨 필수.
  - 추정 SP 부여 + 상태 To Do → 분모에만 들어가 "아직 안 됨"이 정확히 반영됨.
  - 집계 시 mock SP를 **별도 항목으로 리포트**(검증용 가시성).
  - **이중 계산 리스크**: 실제 티켓이 쪼개져 생성되면 mock SP만큼 겹친다.
    → 실제 티켓 생성 시 mock SP를 깎거나 0으로 내리는 reconciliation은 **사람이 판단**.
    도구가 mock을 자동 삭제하지 않는다.
- **부분 크레딧 금지**: In Progress에 0.5 같은 가중은 노이즈만 크다. **Done/Total 이진**만.
- **기능2(현안, PMO_VIT) 진척은 예외적으로 "자손 티켓 개수 기반"**: Root 현안의 모든 자손
  (Epic→Ticket→Sub-ticket) 중 `statusCategory=done` 개수 / 전체 개수. 데일리 트래킹용 빠른 지표라
  WBS/Epic 롤업의 **SP 기반과 목적이 다르다**(둘을 섞지 말 것). 중복 노출 방지: 상위(조상)가 이미
  PMO_VIT면 그 자손 현안은 스킵.

---

## 7. 로컬 재현 환경 — 외부 오픈소스 mock `jira820`

이 PC는 사내 VDI/VBS로 **Docker·WSL2 불가**, Jira DC **deprecation으로 신규 평가 라이선스 발급도 중단**.
→ 실 Jira 없이 **외부 오픈소스 패키지 [`jira820`](https://pypi.org/project/jira820)**(범용 Jira DC 8.20.8 mock)
에 **이 프로젝트 world 를 주입**(`app/fakebridge.py`)해 개발·테스트한다. 무설치·무라이선스·무가상화.

**dev 데이터 경로가 jira820 하나로 일원화됨** (기존 `tools/fake_jira`·`app/mockdata.py` 제거):
- **mock**: jira820 을 **in-process(ASGI)** 로 호출(`app/auth/inprocess.py`). 소켓/`run_fake` 불필요. dev 기본값.
- **local**: `run_fake.py` 가 같은 jira820(world 주입)을 **:8080 실 HTTP** 로 서빙 → REST+인증+캐시 경로 검증.

```bash
python run.py                                    # mock = jira820 in-process (기본)
python run_fake.py                               # :8080 (FAKE_LATENCY_MS 로 지연 주입)
JIRA_ENV=local python run.py                     # 앱(local) → :8080 jira820
```

- 테스트 데이터 = 결정적 `app/world.py` (jira820 에 주입). Jira·DB·seed 불필요.
- jira820 은 실 Jira DC 8.20.8 형태 — statusCategory `new/indeterminate/done`, 사내 status(Open/In Progress/Resolved/Closed/Reopened)·type(Bug/Epic/…/Sub-Task), 티켓 키 `DL-xxxx`.
- **mock·local 모두 같은 jira820(같은 world·직렬화기)** → 출력 100% 일치(전송만 다름, 회귀 기준). `tests/test_local_parity.py` 가 자동 가드.
- 한계: 실 Jira 고유 quirk 는 못 잡음 → 사내 **prod(SSO)**에서 소수 대조.

### 7.1 UI 회귀 검증 픽스처 — Epic `DL-9000` (UI 고칠 때 여기부터 열어라)
world 는 랜덤 생성이라 "설명 없는 Sub-Task", "링크 있는 티켓" 같은 **검증용 데이터를 매번 뒤져야 했다.**
→ 검증 포인트별 티켓을 **고정 키**로 박아둔 Epic 을 둔다(`world._build_ui_fixtures`). **제목이 곧 검증 항목.**

| 키 | 검증 포인트 |
|----|------------|
| DL-9001 | 설명 리치요소 — 표·코드·인용·패널·콜아웃·이미지·링크 |
| DL-9002 | Heading 1~4 레벨 구분 바 |
| DL-9003 | 아주 긴 제목 말줄임(헤더·계보·형제·검색) |
| DL-9004 | 관련 Task — 서술형 링크문구 축약(`blocks`) + 본문 언급 티켓 |
| DL-9005 | 관련문서 — 편집(초안) `resumedraft.action?draftId=` · 같은 문서 3형태 중복 · display 형태 |
| DL-9006 | 첨부 칩 — 이미지/문서/큰 용량(2.4MB **표기만**) |
| DL-9007 | 코멘트 다수 — 멘션·문서링크·긴 본문 |
| DL-9008 / DL-9009 | 마감 초과(D+) / 임박(D-2) |
| DL-9010 | 타임라인 — 담당자·상태·해결 이력(+ description 변경은 제외되어야 함) |
| DL-9011 | 라벨 다수 + 미할당 + 설명 없음 |
| DL-9012~9016 | Sub-Task 부모/형제 — 설명 없음(상위 설명 자동 펼침)·있음(접힘)·완료 정렬 |

**규칙 (지켜라):**
- **대시보드 격리**: Epic 이 `wbs_config` 밖 + `PMO_VIT` 라벨 없음 + 담당자가 `people.yaml` 밖(`pmo`/`lead`)
  → WBS·현안·워크로드 집계에 **안 섞인다**. `tests/test_ui_fixtures.py` 가 이 격리를 가드한다.
- **생성 순서**: `_build_ui_fixtures()` 는 `_build_links()`/`_build_attachments()` **뒤**에 온다.
  앞에 두면 자동 생성기가 픽스처의 링크·첨부를 덮어쓴다.
- **rng 미사용**: 픽스처는 `_fx()` 로 dict 를 직접 만든다. rng 를 쓰면 **world 전체 시퀀스가 바뀌어** 기존
  데이터가 통째로 달라진다(과거에 실제로 겪은 회귀). 새 픽스처를 추가할 때도 rng 금지.
- 새 UI 검증 포인트가 생기면 **픽스처를 추가**하고 제목에 그 포인트를 적어라.

---

## 8. 프로젝트 구조

**실서비스화 형태** (FastAPI 백엔드 + 정적 프론트, git 공유 로컬 웹앱).
설정·매핑은 **`config/`**(jira.yml·wbs_config.yaml·people.yaml), 나머지는 코드/도구.
진행 상황·TODO·History 는 **`PROGRESS.md`** 로 관리한다.

> ⚠️ 아래 트리는 과거 `src/` 레이아웃 기준으로 **일부 낡음** — 현재 코드는 **repo 루트**에 있다(`src/` 없음). 구조 전면 갱신은 별도 예정.

```
lake-task-manager/               # repo 루트
├── CLAUDE.md                    # 프로젝트 지침 (루트 관례 유지)
├── config/                      # 환경설정 + 매핑. 사용자 편집 · git 커밋
│   ├── jira.yml                 # 환경설정(env·jira·confluence·cache·server). 중첩 YAML
│   ├── wbs_config.yaml          # 기능1: module → WBS task → epic(ticket=DL-xxxx, weight 정수)
│   └── people.yaml              # 기능3: module → [jira user id]
│                                # (exe 빌드 없음 — 배포는 배포 repo 의 run.bat 소스실행)
└── src/                         # (과거 레이아웃 — 실제론 아래가 전부 repo 루트에 있음)
    ├── PROGRESS.md              # TODO / 진행 History
    ├── run.py / run_fake.py     # 앱 런처(앱 창) / Fake Jira 서버 런처
    ├── requirements.txt / requirements-sso.txt   # 앱 deps / +playwright(prod SSO)
    ├── app/                     # FastAPI 백엔드 + 정적 프론트
    │   ├── main.py              # 라우트(/api/wbs·vit·workload[/{user}]·login·health·refresh) + static
    │   ├── settings.py          # config/jira.yml + wbs_config 로더/검증, frozen(exe)·컨테이너 경로 인식
    │   ├── world.py             # ★ 단일 결정적 데이터 세계 (이슈·설명·코멘트·활동·confluence)
    │   ├── worldcontent.py      # description/comment/activity 다양성 풀
    │   ├── fakebridge.py        # world 를 외부 jira820 서버에 주입(mock/local 공용 dev 백엔드)
    │   ├── progress.py          # 순수 SP 롤업 (Epic 단위)
    │   ├── rollup.py            # WBS/Module/PMO 가중 조합 (상대 가중치 자동 정규화) — 순수
    │   ├── vit.py / workload.py # 기능2 현안 / 기능3 워크로드 조합
    │   ├── cache.py             # SQLite TTL 캐시(티켓 단위) + snapshot
    │   ├── jira_client.py       # REST 호출 (AuthProvider 주입, 캐시 경유) — env 무관 단일 경로
    │   ├── auth/{base,basic,sso_session,inprocess}.py   # inprocess=mock용 jira820 in-process provider
    │   └── static/             # Vue 3 무빌드 SPA: index.html(셸)+app.js+components/(app-root·ui·views)+lib/(api·fmt·colors)+styles/(tokens·base·components·뷰별)+vendor/(vue.esm)
    └── tests/                   # world/progress/rollup/config/names/local_parity 유닛테스트
```
> dev fake 서버는 **외부 오픈소스 [`jira820`](https://pypi.org/project/jira820)** 패키지(requirements). 이전
> `tools/fake_jira`·`app/mockdata.py` 는 이 패키지로 대체·제거됨.

### 환경 3종 (`JIRA_ENV`)
- **mock**: **jira820 in-process**(world 주입, `app/auth/inprocess.py`). Jira·소켓·`run_fake` 불필요. dev 기본값.
- **local**: `run_fake.py`(:8080, 같은 jira820)에 실제 HTTP. **같은 world·직렬화기** → mock 출력과 100% 일치. REST+인증+캐시 경로 검증.
- **prod**: 사내 Jira DC (Playwright SSO 세션 재사용). **세 환경 모두 동일 파서/경로** — provider 만 교체.

`settings.py` 는 `config/`(jira.yml 포함)를 **repo 루트(dev) / exe 옆(frozen) / /srv(컨테이너)** 어디에 있든 자동으로 찾는다.

### 실행 방식 (dev = jira820)
- mock: `python run.py`  (또는 `uvicorn app.main:app`). dev `config/jira.yml` 기본이 `env: mock` → jira820 in-process.
- **local**: 터미널1 `python run_fake.py` (:8080 jira820, `FAKE_LATENCY_MS` 로 지연 주입 가능) → 터미널2 `JIRA_ENV=local python run.py`.
- 최종 사용자: **소스 실행** (배포 repo `run.bat` → venv·의존성·Chromium 자동 구성 후 앱 창. 창 닫으면 전체 종료).
  prod SSO 는 **같은 앱 창**에서 로그인 → 세션 저장 → 앱 복귀. (Playwright **번들 Chromium** — 회사 관리형 Chrome 자동화 차단 회피)
- 앱 창 대신 기본 브라우저+수동종료: `LAKE_NO_WINDOW=1 python run.py` / playwright 없으면 자동 폴백.

### 아키텍처 규칙
1. `progress.py`는 **순수 함수**. 입력=정규화 이슈 리스트, 출력=Epic 진척률 dict. 네트워크/인증 의존 금지.
2. `rollup.py`(다운스트림)는 Epic 진척률 + `wbs_config.yaml`(정규화 plan)을 받아 WBS/Module/PMO를 조합. 역시 순수.
3. 인증은 `AuthProvider` 인터페이스(`app/auth`) 뒤로 숨긴다. 구현체 교체로 환경 전환.
4. `JiraClient`는 `AuthProvider`를 주입받아 REST 호출. **어떤 인증인지 몰라야 한다.** 모든 호출은 `cache` 경유.
5. 환경 선택은 `config/jira.yml`(`env`, 환경변수 `JIRA_ENV` 로 override)로만. 커스텀 필드 ID·매핑 하드코딩 금지.
6. **세 환경(mock/local/prod) 모두 동일한 REST 파서 경로**(`jira_client`). env 분기 없음 — provider 만 다름.
7. **테스트 데이터는 `app/world.py` 단일 소스** → `app/fakebridge.py` 로 jira820 에 주입. mock(in-process)·local(HTTP)이
   같은 jira820 을 소비하므로 출력 일치(회귀 기준, `tests/test_local_parity.py`).

---

## 9. 테스트 원칙

- `progress.py`/`rollup.py`는 가짜 이슈 리스트(fixture)로 유닛테스트. Jira 없이 계산 로직 검증.
- 통합테스트는 **로컬 Fake Jira(:8080)/Docker Jira 상대로만**. 사내 Jira에 자동 테스트 절대 금지.
- 운영 SSO 경로(`SsoSessionProvider`)는 세션 만료·headless 감지 이슈로 **수동 검증**.
- 첫 검증: 로컬에서 Epic 하나의 집계 숫자가 Jira Scrum 보드의 Epic Report와 일치하는지 대조.
- 데모 검증: 생성기가 손계산 가능한 Epic 하나의 `done/total`과 일치하는지 self-assert.

---

## 10. 하지 말 것

- 사내 SSO 세션 파일(`jira_state.json`) git 커밋 금지 → `.gitignore`. (`config/jira.yml` 은 placeholder 템플릿이라 커밋 대상 — 실 비밀은 세션 파일에만.)
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
