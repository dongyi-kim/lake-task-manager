# app/infra — 교차 관심사 (캐시 · 설정 · 환경설정 · dev 진단)

앱 전반이 기대는 하부 서비스. 특정 기능에 속하지 않는다.

## 파일
- **cache.py** — SQLite TTL 캐시. **2단계 TTL**(outdated/dead)로 오프라인·SSO 만료에도 화면을 지키고,
  `get_or_set`(폴백) · `get_or_set_swr`(stale-while-revalidate) · 주기 purge · snapshot(시계열) · recent(최근 열람) 제공.
- **settings.py** — config 로딩(jira.yml·wbs_config·people) + 경로 탐색(dev/frozen/컨테이너) + `Settings` + `is_manager`.
  ★ `SRC_DIR = Path(__file__)...parent×3` — 이 파일이 **app/infra/** 에 있어 repo 루트까지 3단계다(이동 시 주의).
- **prefs.py** — 사용자 환경설정 JSON 로드/저장(app_prefs.json — 커밋 금지).
- **devtools.py** — dev 진단 레지스트리(`DEV_TOOLS`) + 스키마/키트리 마스킹. 라우트는 `enabled()`로 게이팅.

## 규칙
- `settings.py` 경로 상수(STATIC_DIR/APP_ROOT/BASE_DIR)는 `__file__` 기준이다 — 이 파일을 옮기면 반드시 재검증.
- 캐시 키 네임스페이스·TTL 은 `app/jira/jira_client.py` 가 소유(여기 cache 는 저장소일 뿐).
- dev 진단은 **기본 꺼짐**. prod 배포는 `dev_tools: []`(AGENTS.md §12).
