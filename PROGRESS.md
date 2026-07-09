# Lake Task Manager — 진행 상황 / TODO / History

> 이 파일은 프로젝트의 **TODO 와 작업 History** 를 관리한다.
> 배경·설계 원칙은 `CLAUDE.md`, 실행 계획 세부는 각 Phase 항목 참고.
> 작업을 마칠 때마다 아래 **History** 에 날짜와 함께 한 줄 추가한다.

## 현재 상태

- **기능 1·2·3 mock + local(Fake Jira) 로 end-to-end 완료** ✅ (mock==local 패리티 검증)
- **프론트 = Vue 3 무빌드 SPA**(`static/{app.js,index.html,components/,lib/,styles/,vendor/}`). `/` 진입 = SPA(해시 라우팅 `#/wbs #/vit #/workload`). 옛 3개 HTML/auth.js/callout.js 은퇴.
- **API 리소스 세분화 + lazy**: `/api/wbs`(스켈레톤+롤업), `/api/epic/{key}/tree`·`/api/vit/{key}`·`/api/activity/{user}` 지연. keep-alive + GET memo 로 탭 전환 재fetch 없음.
- **성능**: local(basic-auth) 백엔드 fan-out(epic/vit/workload) **ThreadPool 병렬**(SSO 는 순차 폴백). VIT 조립 결과 캐시.
- dev 환경 = **Fake Jira/Confluence 서버**(무설치·무라이선스·무가상화). Docker/WSL2 불가 + DC 라이선스 중단 대응.
- 남은 것: 기능2·3 인터뷰 반영 잔여 + 사내 **prod(SSO)** 소수 대조.

빠른 실행:
```bash
pip install -r src/requirements.txt

# (A) mock — Jira 없이 바로
python src/run.py                                  # http://localhost:8000

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
