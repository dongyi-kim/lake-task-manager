# Lake Task Manager — 진행 상황 / TODO / History

> 이 파일은 프로젝트의 **TODO 와 작업 History** 를 관리한다.
> 배경·설계 원칙은 `AGENTS.md`, 실행 계획 세부는 각 Phase 항목 참고.
> 작업을 마칠 때마다 아래 **History** 에 날짜와 함께 한 줄 추가한다.

## 현재 상태

- **기능 1·2·3 mock + local(Fake Jira) 로 end-to-end 완료** ✅ (mock==local 패리티 검증)
- **프론트 = Vue 3 무빌드 SPA**(`static/{app.js,index.html,components/,lib/,styles/,vendor/}`). `/` 진입 = SPA(해시 라우팅 `#/wbs #/vit #/workload`). 옛 3개 HTML/auth.js/callout.js 은퇴.
- **API 리소스 세분화 + lazy**: `/api/wbs`(스켈레톤+롤업), `/api/epic/{key}/tree`·`/api/vit/{key}`·`/api/activity/{user}` 지연. keep-alive + GET memo 로 탭 전환 재fetch 없음.
- **성능**: local(basic-auth) 백엔드 fan-out(epic/vit/workload) **ThreadPool 병렬**(SSO 는 순차 폴백). VIT 조립 결과 캐시.
- dev 환경 = **Fake Jira/Confluence 서버**(무설치·무라이선스·무가상화). Docker/WSL2 불가 + DC 라이선스 중단 대응.
- 남은 것: 기능2·3 인터뷰 반영 잔여 + 사내 **prod(SSO)** 소수 대조.
- **MAXIS/Dify 지원**: `codex/maxis-support`의 [Draft PR #80](https://github.com/dongyi-kim/lake-task-manager/pull/80) 하나에서 단위 커밋·TODO·Changelog 관리. UTF-8 문맥 예산, 안전한 실패 진단, 차단 안내와 session TaskFlow preview 연결을 보강 중이다. 기본 route/PROD 활성화는 하지 않으며, 공유 모델 credential 조회 불일치·서버 통제 verifier·사용자 UI/승인 연결은 남아 있다. [16-case baseline 직접 평가](research/agent-improvement/evaluations/maxis-component-baseline-20261017.md)는 과거 실행의 보존 기록이고 새 품질 향상이나 qualification 통과가 아니다.

빠른 실행:
```bash
pip install -r src/requirements.txt

# (A) mock — Jira 없이 바로
python src/run.py --reload                         # http://localhost:4457

# (B) local — Fake Jira 로 실제 HTTP 경로 검증
cd src && python run_fake.py                       # 터미널1: Fake Jira :8080
JIRA_ENV=local python src/run.py                   # 터미널2: 앱(local → fake)
#   FAKE_LATENCY_MS=150 python run_fake.py  → 캐시 실측(1회차 느림, 2회차 즉시)

python -m pytest src/tests -q                      # 21 passed
```

> 디렉터리: 설정·매핑은 **`config/`(jira.yml·wbs_config.yaml·people.yaml)**, 배포 exe 는 config/ 와 나란히.
> config: **`config/wbs_config.yaml`**(module→WBS task→epic ticket `DL-xxxx`+정수 weight), `config/people.yaml`.

---

## Phase A — 인프라 & 백엔드 스캐폴드  ✅ (2026-07-07)

- [x] `requirements.txt` / `requirements-sso.txt` / `.env.example` / `.gitignore`
- [x] `config/plan.yaml` (모듈 7 = Jira Component: ETL·DevOps·Observability·Workbench·Runtime·Catalog·DataOps), `config/people.yaml`
- [x] 순수 로직: `app/progress.py`(Epic SP 롤업), `app/rollup.py`(WBS/Module/PMO 가중 조합)
- [x] `app/settings.py`(.env + yaml 로더·검증, frozen/exe 경로 인식), `app/mockdata.py`(결정적 mock 이슈)
- [x] `app/cache.py` — SQLite TTL 캐시 + snapshot(시계열)
- [x] `app/auth/*`(base/basic/sso_session) + `app/jira_client.py`(mock/local/prod, 캐시 경유)
- [x] `app/main.py` — FastAPI `/api/wbs`·`/api/health`·`/api/refresh` + static 마운트
- [x] `app/static/index.html` — WBS Gantt(데모 이식, `/api/wbs` 소비) + `vit.html`·`workload.html` 플레이스홀더
- [x] `docker-compose.yml`(app / --profile jira) + `Dockerfile`
- [x] 단일 exe 패키징: `run.py`(런처) + `lake.spec`(PyInstaller)
- [x] `seed/seed_jira.py` — 결정적 REST 시드(idempotent, `epic_map.json` 산출)
- [x] `tests/`(progress·rollup·config) — **10 passed**
- [x] 검증: mock 모드 uvicorn 기동 → `/api/health`·`/api/wbs`·`/` 200, PMO 40.3% 롤업 확인

**미검증(라이브 Jira 필요):** `seed_jira.py` 실제 실행, `local`/`prod` 어댑터의 라이브 호출.
→ Phase B 에서 Docker Jira 부팅(수동 라이선스) 후 대조 검증.

---

## Phase B — 기능1~3 local 검증  ✅ (Fake Jira 로 대체 — 네이티브/Docker/seed 폐기)

> Docker·WSL2 불가 + DC 라이선스 발급 중단 → 네이티브 H2/`seed_jira`/`DEV_JIRA_NATIVE.md` 접근을 폐기하고
> **Fake Jira/Confluence 서버(`tools/fake_jira`, :8080)** 로 대체. `local` 이 실제 HTTP+인증+캐시 경로를 검증.

- [x] `python run_fake.py`(:8080) + `LAKE_DOTENV=.env.dev` 앱 → `/api/*` local(fake) end-to-end
- [x] mock==local 패리티(같은 `world`), 티켓 단위 캐시 cold→warm, `FAKE_LATENCY_MS` 지연 실측
- [x] 워크로드 JQL 카운트·활동 ATOM 파싱·Confluence CQL 파싱 검증
- [ ] 사내 **prod(SSO)** 소수 대조 (실 Jira 확보 시)

## Phase C — 기능2: PMO_VIT 현안 트래킹 (MVP mock 완료 → 인터뷰)  🟡

- [x] `GET /api/vit`: Component별 그룹핑 + 최근 진척 히스토리 (mock 합성 / local·prod=JQL `labels=PMO_VIT` 스켈레톤)
- [x] `app/static/vit.html` MVP UI (요약칩 + 모듈별 현안 카드 + 상태 pill + 진척 바 + 최근 진척 타임라인)
- [x] mock 검증: 21 현안(모듈당 3), 히스토리 렌더
- [x] 인터뷰 1차 반영:
  - **시작일·마감일** 표시, 마감 없으면 빨간 **"마감 미정"**
  - 진척 = **자손 티켓 개수 기반**(done/total) — Epic→Ticket→Sub-ticket 전체 (SP 기반 WBS와 목적 분리)
  - **하위 티켓 Created/Done/Resolved 소식**(최근 21일)
  - **Root 티켓 코멘트** 공유
  - Component 없으면 **'Module 미지정'**
  - mock **개수 다양화**(0~6) + **masonry 레이아웃**
  - **조상 dedup**: 상위가 이미 PMO_VIT면 자손 현안 스킵(`ancestors`)
  - **[자세히]**: 소속 티켓 **Tree + Status + 티켓별 최근 진척** 펼치기
- [ ] local/prod 실 Jira 연동 검증 (Docker 후: tree/ancestors/comments 실데이터, 자손 깊이 심화)
- [ ] 추가 인터뷰: 우선순위/블로커 표시 여부, 정렬 기준(마감임박/업데이트), 소식 기간(21일?) 확정

## Phase D — 기능3: 인력 워크로드/활동 (MVP mock 완료 → 인터뷰)  🟡

- [x] `GET /api/workload`: 모듈별/인력별 진행중 수 + 최근7일 완료 수, **Epic/Task/Sub-task 분리** (mock / local·prod JQL 스켈레톤, 인력별 캐시)
- [x] `GET /api/activity/{user}`: 최근 Jira/Confluence 활동 요약 (mock / prod=`/activity`+CQL 스켈레톤, TTL 캐시)
- [x] `app/static/workload.html` MVP UI (모듈→인력 **접이식** 표 + E/T/S 카운트 + **[+] 활동** 확장, 티켓 링크)
- [x] mock 검증: 7모듈 16인력, 카운트/활동 렌더
- [ ] local/prod 실 Jira 연동 검증 (Docker 후: JQL 카운트·`/activity` ATOM 파싱·Confluence CQL)
- [ ] **인터뷰**: '진행중' 정의(In Progress만? open 전체?), 활동 기간·소스(Confluence 포함 범위), 정렬

---

## Backlog / 검토 필요

- [x] exe 실제 빌드/배포 (`pyinstaller lake.spec` → 루트 `lake-task-manager.exe`, static 번들 포함, 추적)
- [ ] prod SSO: 설치된 Chrome 재사용(`channel="chrome"`) 경로 실장 (현재 `SsoSessionProvider` 는 storage_state 방식) + 병렬화 SSO 폴백(순차) 실검증
- [ ] 캐시 백그라운드 워밍 / 스냅샷 스케줄(반자동)

## 결정 로그 (요약)

- 앱 구조 = FastAPI 백엔드 + 정적 프론트 (브라우저가 사내 Jira 직접 호출 불가 → 서버 필수).
- 저장 = YAML config(커밋) + SQLite 캐시(gitignore).
- 배포 = docker compose + 단일 exe(무의존). config/.env 는 외부 파일.
- 모듈 식별자 = Jira **Component**. 현안 = **PMO_VIT** 라벨.

---

## History (append-only)

- **2026-10-08** — 별도 `codex/maxis-core-control-graph`에서 Core 연결 검사와 업무 LLM의 배타적 분기를 16노드·17엣지 source에 통합(`c411514`). 원본 DSL을 환경값 적용 전에 검증하고 두 모델·분류기·합류·기존 Guard 결속을 유지. malformed title 3건 재현·수정, 관련 13파일 863회귀·독립 검토 두 번 9/10 통과. [검증 범위](deploy/maxis-dify/core-control-route.md)에 초기 실패와 공유 schema 비교 후속 항목 기록. 실제 DEV 게시·품질 향상 미검증, API/PROD/route·main·push 변경 없음.

- **2026-10-08** — 별도 `codex/maxis-editor-selection-v2`에서 Editor 내부 DraftSpec의 동등한 target/constraint 배열을 `current_unit_ids`로 통합. v1/혼합·중복·타입·소유권·링크·terminal Guard와 외부 계약 유지. frozen 회귀 917개, 독립 검토 두 번 9/10; 별도 유효 v1 768조합의 writer/plan parity 확인. 실제 DEV 게시·품질 비교와 UI는 이 커밋의 검증 범위가 아님.

- **2026-07-09** — **정리/성능**: 죽은 코드 제거(`_ptotal`, `subsPreview`, ProgressBar marker/`.pbar-mark`, `.mini .tb`, fmt 미사용 export), 스테일 문서 현행화(plan.yaml→wbs_config.yaml, docker/seed/native 폐기 반영). **성능**: local 백엔드 fan-out(epic/vit/workload) **ThreadPool 병렬**(`_pmap`, `supports_parallel`; SSO 순차 폴백), VIT 조립 캐시(`vit_build:`)+코멘트 lazy(리스트에서 미조회), 프론트 **keep-alive**+GET memo(탭 재fetch 제거)+간트 렌더 coalesce.
- **2026-07-09** — **Vue 3 무빌드 SPA 전환 완료**: 3개 self-contained HTML → 단일 SPA(`static/{index.html,app.js,components/,lib/,styles/,vendor(vue.esm)}`), 해시 라우팅. 색/헬퍼/컴포넌트(`tokens.css`,`colors.js`,`fmt.js`,`ProgressBar`,`StatusPill`,`TypeBadge`) 단일 소스화. `auth.js`→`LoginOverlay`, `callout.js`→`FormulaCallout`. `/` = SPA(옛 페이지 은퇴). 폰트 Pretendard(CDN).
- **2026-07-08** — **world 시간·VoC**: 이슈 created/updated/resolved + 활동/Confluence 에 결정적 hh:MM 부여(뉴스·활동 시간표시). VoC 판정 Component `VoC`→**`사용자 VoC`**. world 대규모 보강(비-WBS epic·독립 task·1~6월 과거 데이터).
- **2026-07-08** — **SSO 로그인 UX**: lazy AuthProvider + 세션없음/만료 시 401 `needLogin` + 웹 로그인(설치 Chrome 폴링). README(PM/모듈리더용).
- **2026-07-07** — **Fake Jira/Confluence API 서버** 구축(`tools/fake_jira`, :8080) + **단일 world 생성기**(`app/world.py`, mock·fake 공유). 우리가 쓰는 전 엔드포인트(search/issue/comment/agile/activity ATOM/Confluence CQL/field/status/issuetype/project·statuses/workflow) 서빙. mock==local 패리티·티켓단위 캐시(cold 5.4s→warm 0.03s)·21 tests 검증. 사내 fidelity 반영: status(Open/In Progress/Resolved/Closed/Reopened)→statusCategory(new/indeterminate/done), type(Bug/Epic/Improvement/New Feature/Story/Task/Sub-Task).
- **2026-07-07** — config 재설계: `plan.yaml`→**`wbs_config.yaml`**(module→WBS task→epic `ticket`+weight). **논리 epic id 제거**(실 티켓 `DL-xxxx` 사용, Epic 이름은 Jira 에서). **가중치 정수·상대값 자동 정규화**(합=1 강제 폐지). PROJECT_KEY=DL.
- **2026-07-07** — dev 환경 결정: 이 PC 는 Docker/WSL2 불가(VDI/VBS, 중첩가상화 막힘) → **네이티브 Jira 8.20.8 + 내장 H2** 로 로컬 검증. 설치 가이드 `src/DEV_JIRA_NATIVE.md` + 씨딩 전 진단 `src/seed/preflight.py` 추가.
- **2026-07-07** — 캐시 티켓 단위화: `issue:{env}:{key}`(모든 이슈/하위이슈)·`comments:{env}:{key}`, 검색은 write-through(`get_issue`/`_search`). 통짜 `vit:{env}` 제거.
- **2026-07-07** — Phase D(기능3) MVP mock 완료: `/api/workload`(모듈별 인력 진행중/최근7일 완료, E/T/S 분리) + `/api/activity/{user}`(Jira/Confluence 요약, 캐시) + `workload.html`(접이식 표 + [+] 활동). 전 페이지 **가로폭 최대화(full-width)**. 소식·트리에 **티켓 제목 + 개별 Jira 링크** 추가.
- **2026-07-07** — 기능2 인터뷰 1차 반영: 시작·마감(마감없음=빨강), **자손 개수 기반 진척**, 하위 소식(Created/Done/Resolved), Root 코멘트, Module 미지정, 개수 다양화+masonry, **조상 dedup**, **[자세히] 소속 티켓 Tree**. payload 를 tree 기반으로 재설계(진척/소식/dedup은 build_vit 중앙 계산).
- **2026-07-07** — Phase C(기능2) MVP mock 완료: `/api/vit`(Component 그룹 + 최근 진척 히스토리, mock 합성 + local/prod JQL 스켈레톤) + `vit.html`(현안 카드·상태 pill·타임라인). 인터뷰 대기.
- **2026-07-07** — 디렉터리 재편: 루트=사용자 파일(.env·config, 배포 exe)만, 코드/도구 전부 `src/` 로 이동. `settings.py` 루트 자동탐색(dev/컨테이너/exe)·docker context=상위·seed epic_map 경로 수정. 재검증 12 passed.
- **2026-07-07** — SP 누락(None) 기본값 규칙 추가: Bug=0, 나머지=1 (`progress.sp_of`). 문서·테스트·mock 반영.
- **2026-07-07** — Phase A 완료. FastAPI 앱(mock/local/prod) + config(YAML) + SQLite 캐시 + docker/exe 패키징 + seed + 테스트(10 passed). mock 모드 end-to-end 검증(/api/wbs PMO 40.3%).
- **2026-07-07** — 데모 확정: `demo/` 단일 HTML 간트차트(시간축 일/주/월, 모듈별 색, WBS 일정 바 + 진척 채움, ㄴ자 트리 연결선). 모듈 7 = ETL/DevOps/Observability/Workbench/Runtime/Catalog/DataOps.
- **2026-09-17** — MAXIS 성능 계획의 오프라인 입력 진단·기존 측정 재사용 기반 구현. 8개 원격 Role·11개 합성 사례, 문맥 누락/미관측/실패 보존. 실 LLM·생산 설정·DEV 보호 변경 없음. 범위·검증·남은 작업: [성능 기반 보고서](research/agent-improvement/maxis-performance-baseline-20260917.md).
- **2026-09-17** — 별도 `codex/maxis-role-evaluation`에서 8개 Role·11개 고정 합성 사례의 실제 응답 평가 경로 구현. source/profile 결속, 응답/Knowledge 독립 검사, 중단·미시도·usage 미관측 보존. 관련 테스트 199개 통과, 독립 검토 6→8→9/10. 활성 route/게시 Workflow 변경 없음. 범위·증적: [역할 평가 보고서](research/agent-improvement/maxis-role-evaluation-20260917.md).
- **2026-09-17** — `2851c28` clean 소스로 실제 DEV Role 11건 평가 완료: ok 0, blocked 5, needs_input 5, bad_request 1. 만료된 canary 검증은 정상 3-app probe로 갱신(3/3); 모든 route local 유지. [직접 진단](research/agent-improvement/evaluations/maxis-role-component-20260917-baseline.md)에 원본 hash·계약/검색 실패·미관측을 기록. 별도 UTF-8 수정 branch `codex/maxis-context-utf8` commit `90a99bd`, 확대 회귀 174개 통과. 병합·배포하지 않음.
- **2026-09-21** — `codex/maxis-role-policy-retrieval`/`377e9ed`에서 검색 전용 결정적 Policy Query 구현·DEV v20 Core/PMO 게시. 회귀 642개·canary 3/3, 같은 11-case 정책 no-hit 5→0이나 ok 0·blocked 10·Editor 오류 1, 지연 28.154→36.704초. [실측 보고서](research/agent-improvement/evaluations/maxis-role-component-20260921-policy-query.md)에 실패·식별자·181파일 동결 증적·후속 개선 기록. 모든 route local·PROD/키 불변. Editor 단위 범위 수정은 별도 `codex/maxis-editor-unit-scope`/`317d2a4`, 관련 504회귀·독립 검토 통과, 병합·원격 게시하지 않음.
- **2026-10-08** — 별도 `codex/maxis-eval-tree-identity`에서 평가 후보 source identity 수정. 저장소 상대 cache 제외로 `.cache/worktrees`의 코드 누락 방지, MAXIS Role source ledger에 검색 selector 추가. 신규 4회귀 red→green, 관련 99 tests, 독립 검토 9/10·9/10. protocol 4.0.0 전환; rubric/battery/checker 불변·과거 raw 보존·fresh baseline 필요. 실제 LLM·배포·운영 routing 변경 없음. [전환 계약](app/agent/EVALUATION.md#protocol-400-source-identity-전환).
- **2026-10-08** — 위 identity 수정 commit `5abdc71` clean 소스로 protocol 4 DEV baseline 11/11 재측정. Canary 3/3·source/profile binding 유지, ok 0·blocked 10·Editor bad_request 1. gateway 34.462초·p50 2.550초·p95 6.847초, 관측 10건 token 40,589(전체는 미관측). [fresh baseline 진단](research/agent-improvement/evaluations/maxis-role-component-20261008-identity-baseline.md)에 raw hash·빈 출력·제약 기록. 운영 route local·PROD 불변, 통계적 비교/공식 품질 채점/새 배포 없음.
- **2026-10-08** — 별도 `codex/maxis-editor-unit-scope-v20`/`8739a8c`에서 wrapper-title 근거 범위 수정 포트·907회귀·독립 검토 9/10 두 번 후 DEV v21 Editor만 게시. 첫 import 응답 assertion 실패는 보존하고 exact draft 확인 후 import 없이 publish 재개. Canary 3/3이나 Editor 자동 2/5·Role 정상 0/11로 품질 미통과. [Editor 직접 검토](research/agent-improvement/evaluations/maxis-editor-component-20261008-unit-scope.md)에 benchmark LF/CRLF 차이, [Role 진단](research/agent-improvement/evaluations/maxis-role-component-20261008-editor-scope.md)에 selector 계약 오류 재현과 fixed 4457 bind 실패로 UI 미실행 기록. Core/PMO·Knowledge·PROD 불변, 운영 route local. main 병합·push 없음.
- **2026-10-08** — 위 Editor 계약 code `1a35224`를 DEV v22에 게시. 실패한 import 응답 검증은 보존하고 읽기 전용 exact draft 확인 후 publish-only 재개했다. 새 v21 기준선 5건 직접 품질 2.52/5·사용 가능0/5. v22 canary는 Editor·PMO 통과, 변경 없는 Core LLM 자체 blocked로2/3·profile verified=false라 후보 Editor/Role 배터리는 미실행. [기준선과 평가 제한](research/agent-improvement/evaluations/maxis-editor-component-20261008-selection-v2.md) 및 [게시 증거](deploy/maxis-dify/deployments/dev-editor-selection-2026.10.08.yml)에 기록. Gate 우회·성공할 때까지 재시도·PROD/route 변경·push 없음.
- **2026-10-08** — 별도 `codex/maxis-json-schema-types`에서 공유 Dify 구조화 출력 검증의 boolean/number 동등 비교 결함 21건 재현·수정. 7개 LLM 노드와 두 원본 검증 진입점, 순환·중첩·비 JSON 값·원본 보존을 포함한 신규 141개 및 관련 14파일 1,004회귀 통과(344.03초). 독립 검토 두 번 9/10. prompt·DSL·모델·Guard 불변. [검증 범위](deploy/maxis-dify/core-control-route.md#json-스키마-타입-검증). 이 커밋은 원격 게시나 실제 품질 향상의 증거가 아님.
- **2026-10-08** — clean `7cc40a5`에서 DEV Core v23만 게시. import의 경고 응답을 보존하고 exact draft·설정·의존성을 재확인한 뒤 import 재전송 없이 publish-only 재개. Canary 3/3, native Core control 1 LLM·업무 9건은 각각 semantic 1 LLM 관측. Editor 자동 3/5이나 Role 정상 0/11로 전반 품질 향상은 미입증. [게시·실측 증적](deploy/maxis-dify/deployments/dev-core-control-2026.10.08.yml)에 두 실제 배터리 raw와 추가 모델 없는 native 보조를 기록. 직접 품질 검토 진행, 운영 비활성·PROD/route 불변·push 없음.
- **2026-10-08** — 위 두 primary 실행의 직접 품질 검토 완료. [Editor paired 보고서](research/agent-improvement/evaluations/maxis-editor-component-20261008-core-control.md)는 새 동일 패널 2.54→2.68/5·사용 가능 1/5→1/5, CMP6 개선과 CMP8 오류 퇴행을 함께 보존. [Role 후보 보고서](research/agent-improvement/evaluations/maxis-role-component-20261008-core-control.md)는 2.11/5·업무 산출물 사용 가능 0/11이며 paired baseline 품질 비교가 아님. 총 735개 공통·특수 판단, 원본·기존 패널 이견·미관측 전체 token/cost 보존. 단일 탐색·비블라인드 한계로 전반 품질 향상 및 운영 적격 미입증. 별도 내부 용어집 1.0.2는 48개 정의·12개 출처로 확장해 로컬 계약/이름·문맥 조회만 검증; Knowledge 업로드·runtime 연결 없음.
- **2026-10-08** — 별도 `codex/maxis-editor-selector-input-v2`에서 selector 입력의 작성용 계약 혼선을 제거. 내부 `editor-input.v2`로 producer/Guard/Enforcer를 함께 갱신하고 v1/혼합 입력을 거부하며 writer·최종 응답 계약·안전 Guard·모델·23노드/26엣지는 유지. 5 red 재현 후 관련 9파일 826회귀(272.25초), 독립 검토 두 번 9/10. 동일 고정 입력 5건에서 350–363 UTF-8 bytes 감소; 실제 token/품질 효과는 미측정. [검증과 호환 경계](research/agent-improvement/maxis-editor-selector-input-v2-20261008.md)에 기록. DEV 게시·재평가·운영 전환·PROD·main·push 변경 없음.
- **2026-10-08** — 이후 clean `ff5daa4` 소스를 DEV Editor v24에 게시하고 canary 3/3을 확인했다. 동일 전체 배터리 1회에서 Editor 자동 2/5(이전 3/5), Role 정상 0/11(blocked 10·bad_request 1); 계약 단순화만으로 품질 향상은 입증되지 않았다. [게시·실측 증적](deploy/maxis-dify/deployments/dev-editor-input-2026.10.08.yml)에 원본 hash·실패·미관측 전체 token/cost를 보존했다. 직접 품질 검토 진행 중이며 Core/PMO·키·PROD 불변, 운영 inactive/local·main 병합/push 없음.
- **2026-10-08** — v24 직접 품질 검토 완료. [Editor 새 동일 패널 비교](research/agent-improvement/evaluations/maxis-editor-component-20261008-input-v2.md)는 3.08→2.48/5·사용 가능 1/5→0/5; 이전 v23 패널 2.68은 별도로 보존했다. [Role 후보 단독 검토](research/agent-improvement/evaluations/maxis-role-component-20261008-input-v2.md)는 2.02/5·업무 산출물 사용 가능 0/11, 빈 요청의 안전 중단 1건은 별도 경계 성공이다. 공통·특수 735판정과 원본 실패를 기록했으며 전반 품질 향상·운영 적격은 미입증이다. 다음 작업은 실패 Editor 2건의 기존 native 기록에 대한 읽기 전용 원인 진단이다.
- **2026-10-08** — 별도 `codex/maxis-editor-selector-eligibility`에서 입력 전용 44줄 기능으로 선택 가능한 writing 범위와 링크를 명시. 원문 v2·unit·Guard·writer·모델·23노드/26엣지 보존. 최초 관련 전체 853 pass/1 fail은 옛 wrapper를 치환하던 테스트의 no-op이었으며, 수정 후 같은 관련 10파일과 byte 경계 추가를 포함한 857회귀(284.37초)·독립 검토 두 번 9/10 통과. 고정 입력은 160–161bytes 증가하고 의미 품질 향상은 미검증. [검증·비용·호환 경계](research/agent-improvement/maxis-editor-selector-eligibility-20261008.md)에 기록. API/DEV 게시/PROD/활성화/main/push 변경 없음.
- **2026-10-08** — 이후 clean `c595d0a`를 DEV Editor v25에 게시(import 1·publish 1)하고 canary 3/3 확인. 새 실행 도구의 최적화 모드 조기 거부·295개 오프라인 검증·각 독립 검토 두 번 9/10을 기록했다. 전체 배터리 각각 1회에서 Editor 자동 1/5·25.1초, Role status=`ok` 1/11·blocked 10·31.889초이며 기술 오류는 없었다. [새 동일 패널 Editor 직접 평가](research/agent-improvement/evaluations/maxis-editor-component-20261008-eligibility.md)는 2.82→2.18/5·사용 가능 1/5→0/5, [Role 후보 단독 평가](research/agent-improvement/evaluations/maxis-role-component-20261008-eligibility.md)는 2.32/5·사용 가능 0/11이다. 735개 직접 판정과 퇴행을 보존했고 전반 품질 향상·운영 적격은 미입증이다. 증적은 별도 `codex/maxis-editor-eligibility-evidence`에 정리한다. 다음은 CMP2/9 기존 native 기록의 bounded 읽기 전용 진단이며 Guard 완화·추가 모델 호출 없이 중단 경로를 구분한다. Core/PMO·키·Knowledge·PROD 불변, 운영 inactive/local·main 병합/push 없음.
- **2026-10-17** — MAXIS 지원을 [단일 Draft PR #80](https://github.com/dongyi-kim/lake-task-manager/pull/80)로 유지하며 기능 연결·우선 수정 수행. `3635c6c` UTF-8 예산 복원, `fd22287` 안전한 평가 실패 진단, `c8b2329` session→TaskFlow→QueryRunner/근거 검증 preview 및 admission/취소 경합 보호, `8dd1ce3` 검증된 차단 응답의 로컬 안내·그래프 후속 실행/승인 준비 차단. 최종 관련 15파일 570 tests 통과(기존 dependency 경고 1개), 독립 검토 우선 수정 8→7→9/10·TaskFlow 7→7→7→9/10. [16-case 기준 평가](research/agent-improvement/evaluations/maxis-component-baseline-20261017.md)에 공통 512·특수 48판정과 원본 source identity·미관측을 보존하고 승인 후 서버 검증 실패 상태를 정정했다. 새 LLM/API 평가·서버 설정·게시·PROD/기본 route 변경 없음. 서버 verifier·실제 UI·승인 채택·idle sweep·품질 qualification은 미완료이며 preview 기본 비활성. PR TODO/Changelog에 완료·부분 구현·차단을 구분한다.
- **2026-10-17** — 위 `0dbbc9c`의 [첫 전체 CI](https://github.com/dongyi-kim/lake-task-manager/actions/runs/39028040802)는 6,108 passed / 1 failed / 42 skipped / 1 warning. 실패는 기존 production-graph 테스트가 blocked 뒤 원격 ResultIntegrator 추가 호출을 요구하던 이전 기대값이었다. 새 로컬 종료 계약에 맞춰 추가 호출 금지와 정확한 복구 reply/error/AIMessage·질문/승인 카드 부재를 검증하도록 강화했다. 관련 47 tests 통과; runtime 코드 변경 없이 후속 단위 커밋/CI로 확인하며 첫 실패 기록은 보존한다.
- **2026-10-17** — MAXIS 품질 개선 후속: `d8d6a8c`는 envelope 통과와 문장 산출물·질문·안전 경계·응답 부재를 분리하고 remote battery 2.0.0 및 checker manifest를 도입. `9e1b4ea`는 Work 조회 근거 옆에 요청 출처를 보존하고 정확한 중복만 typed source index로 전달; 원래 query DLP/UTF-8 한도·관측 시각·partial/stale 차단 유지. 초기 예산·가명화 회귀 수정 후 관련 11파일 456 tests/기존 경고 1개, 독립 검토 평가기 7→8.5·입력 6→8.5/10. clean `9e1b4ea`의 DEV 합성 Work 1회는 5,998-byte 완전 입력/검색·응답 계약 통과에도 빈 blocked로, 새 판정 `unexpected_blocked`·사용 가능 0/1. [직접 검토](research/agent-improvement/evaluations/maxis-work-intent-outcome-20261017.md)에 공통 32·특수 4판정, 3.245초 gateway/3,494 tokens/미관측 비용과 원본 식별자를 보존했다. 입력·평가 개선을 의미 품질/속도 개선으로 확대하지 않으며 MX-03/16은 부분 완료. 원격 Work prompt의 index 해석·구체 복구 계약 설명이 다음 후보; 서버 게시·PROD·기본 route/credential 변경 및 전체 qualification 없음.
