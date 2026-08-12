# app/auth — 인증 추상화 (환경별 provider)

인증을 `AuthProvider` 인터페이스 뒤로 숨긴다. **구현체 교체만으로 환경 전환**(mock/local/prod).
`JiraClient`(app/jira)는 이 인터페이스만 알고 REST 를 호출한다.

## 파일
- **base.py** — `AuthProvider` 인터페이스 + 예외(`SessionExpired`/`LoginRequired`/`UpstreamError`) +
  스레드로컬 우선순위 스코프(`write_upstream`/`background_upstream`) + XSRF 헤더 상수.
- **basic.py** — Basic/PAT provider (local/docker).
- **inprocess.py** — jira820 **in-process** ASGI provider (mock 기본, 소켓 불필요).
- **sso_session.py** — Playwright **SSO** provider (prod). 브라우저 기동·로그인 흐름·서비스 probe·세션 생존 확인.
- **sso_store.py** — 서비스별 세션 파일 저장 + legacy `jira_state.json` 마이그레이션.

## 규칙
- SSO 세션 파일(`jira_state.json`)·크리덴셜 **커밋 금지**. 사내 prod 에 자동 테스트 금지(AGENTS.md §10).
- provider 는 큐 단일화(prod)라 실패 판정에 수십 초 걸릴 수 있다 — 회로차단기/warm 은 client·run.py 가 관리.
