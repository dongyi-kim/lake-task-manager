# app/infra — 교차 관심사 (캐시 · 설정 · 환경설정 · dev 진단)

앱 전반이 기대는 하부 서비스. 특정 기능에 속하지 않는다.

## 파일
- **process_identity.py** — Windows base Python 이미지·런타임 DLL을 `LakeTaskManager.exe`/`LakeTaskManagerDev.exe`로 준비하고 앱 시작 전에 해당 프로세스로 교체.
- **cache.py** — SQLite TTL 캐시. **2단계 TTL**(outdated/dead)로 오프라인·SSO 만료에도 화면을 지키고,
  `get_or_set`(폴백) · `get_or_set_swr`(stale-while-revalidate) · exact-key 묶음 무효화 · TTL prefix
  index 조회 · 주기 purge · snapshot(시계열) · recent(최근 열람) 제공.
- **cache_policy.py** — 사용자 설정에 따른 freshness/display TTL, 요청별 수집 시각과 HTTP 응답 metadata.
- **recent_history.py** — Confluence 문서 ID 기반 최근 열람 URL 정규화·기존 이력 중복 제거(100개, 원래 열람 시각 보존).
- **settings.py** — config 로딩(jira.yml·wbs_config·people) + 경로 탐색(dev/frozen/컨테이너) + `Settings` + `is_manager`.
  ★ `SRC_DIR = Path(__file__)...parent×3` — 이 파일이 **app/infra/** 에 있어 repo 루트까지 3단계다(이동 시 주의).
- **prefs.py** — 사용자 환경설정 JSON 로드/저장(app_prefs.json — 커밋 금지).
- **devtools.py** — dev 진단 레지스트리(`DEV_TOOLS`) + 스키마/키트리 마스킹. 라우트는 `enabled()`로 게이팅.

## 규칙
- `settings.py` 경로 상수(STATIC_DIR/APP_ROOT/BASE_DIR)는 `__file__` 기준이다 — 이 파일을 옮기면 반드시 재검증.
- Jira 캐시 키와 mutation 의존성은 `app/jira`가 소유한다. 수명은 `cache_policy.py`에서 공통 판정하고, source timestamp는 읽기·조립·설정 변경으로 연장하지 않는다.
- `cache_display`는 수정 후 재조회 실패에 대비한 표시용 이전 값이다. 신규 authoritative 조회와 권한 판단은 이 테이블을 사용하지 않으며, 삭제/권한 상실에는 함께 제거한다.
- dev 진단은 **기본 꺼짐**. prod 배포는 `dev_tools: []`(AGENTS.md §12).
