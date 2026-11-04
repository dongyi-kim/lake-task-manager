# app/auth — 인증 추상화 (환경별 provider)

인증을 `AuthProvider` 인터페이스 뒤로 숨긴다. **구현체 교체만으로 환경 전환**(mock/local/prod).
`JiraClient`(app/jira)는 이 인터페이스만 알고 REST 를 호출한다.

## 파일
- **base.py** — `AuthProvider` 인터페이스 + 예외(`SessionExpired`/`LoginRequired`/`PermissionDenied`/`UpstreamError`) +
  스레드로컬 우선순위 스코프(`write_upstream`/`background_upstream`) + XSRF 헤더 상수.
- **basic.py** — Basic/PAT provider (local/docker).
- **issue_create.py** — 생성 요청의 Reporter 생략 계약과 값이 없는 실패 진단(버전·필드명·HTTP 상태).
- **inprocess.py** — jira820 **in-process** ASGI provider (mock 기본, 소켓 불필요).
- **sso_session.py** — Playwright **SSO** provider (prod). 브라우저 기동·로그인 흐름·서비스 probe·세션 생존 확인.
- **sso_store.py** — 서비스별 세션과 공유 IdP 세션 분리 저장 + legacy `jira_state.json` 마이그레이션.
- **sso_navigation.py** — LTM 로그인 창의 최상위 이동·리다이렉트 origin 수집. 사용자 브라우저 프로필은 읽지 않는다.

## Prod 세션 유지

- 서버가 약 4분마다 Jira와 활성 부가 서비스의 세션을 확인한다. 앱을 계속 사용하는 중에도
  4분마다 activity probe가 가능하며, 포커스·화면 복귀·온라인 복구 시에도 비동기로 확인한다.
- Jira probe와 무음 재인증은 client의 single-flight 경로를 공유한다. 겹친 요청은 즉시 pending을
  받고, 최근 결과는 20초 재사용한다. 주기 작업은 사용자 조회·쓰기보다 낮은 큐 우선순위를 쓴다.
- `/myself` 확인은 서비스 세션의 sliding 만료를 유지하고, 실제 만료가 확인되면 IdP 리다이렉트로
  무음 재인증한다. IdP의 절대 만료나 사용자 입력이 필요한 로그인은 자동 연장할 수 없다.
- 정상 확인 뒤 rolling 쿠키도 최대 4분 간격으로 저장한다. 서비스 파일에는 해당 서비스의 상태만,
  `sso/identity.json`에는 LTM 로그인 과정에서 관찰한 IdP origin의 상태만 저장한다.
  provider 세대와 서비스·IdP 파일 revision 검증으로 늦게 끝난 저장이 새 로그인을 덮지 못하게 한다.
- 네트워크 오류는 로그인 만료와 구분한다. `/api/status`의 `authMaintenance`에 현재 상태,
  마지막 확인·무음 복구 시각, 다음 확인 예정 시각과 실패 분류가 표시되며 비밀 값은 포함하지 않는다.

회귀 검증은 `tests/runtime/test_auth_maintenance.py`, `test_sso_navigation_ui.py`,
`tests/frontend/shell/test_auth_activity.py`에서 fake clock과 로컬 Jira·IdP를 사용한다.

## 규칙
- SSO 대기열은 32개로 제한한다. 포화·실행 전 대기 만료는 `UpstreamBusy`로 반환하고 정상
  브라우저·인증을 폐기하지 않는다. 실행 시간 한도는 실제 시작부터 계산한다.
- 이미 시작한 쓰기의 응답이 유실되면 pending operation과 durable receipt를 유지한다.
  혼잡이 다단계 쓰기 중간에 발생해도 전체 미실행으로 간주하거나 자동으로 다시 쓰지 않는다.
- SSO 세션 파일(`jira_state.json`)·크리덴셜 **커밋 금지**. 사내 prod 에 자동 테스트 금지(AGENTS.md §10).
- provider 는 큐 단일화(prod)라 실패 판정에 수십 초 걸릴 수 있다 — 회로차단기/warm 은 client·run.py 가 관리.
