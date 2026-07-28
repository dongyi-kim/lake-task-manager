# app/jira — Jira/Confluence/Bitbucket REST 클라이언트

상류(Jira DC 8.20.8 등)와 대화하는 유일한 계층. **AuthProvider 를 주입**받아 어떤 인증인지 모른 채
REST 를 호출하고, **모든 호출은 캐시(app/infra)를 경유**한다. mock/local/prod 세 환경이 **동일 파서 경로**다
(provider 만 교체).

## 파일
- **jira_client.py** — `JiraClient`. 이슈 조회·검색·Epic 트리·워크로드 카운트·티켓 뷰 조립·쓰기(편집/전이/코멘트/첨부/링크)·
  아바타·미디어 프록시·Confluence·Bitbucket·변경이력·계보(조상/형제/자식). 캐시 키 네임스페이스·TTL·무효화 규칙의 소유자.

## 규칙 (CLAUDE.md §8 아키텍처 규칙)
- **어떤 인증인지 몰라야 한다** — 환경 분기 없음, provider 만 다르다.
- 커스텀 필드 ID·상태명 하드코딩 금지 → config/`statusCategory`.
- **쓰기 후 무효화**: 편집이 바꾸는 화면은 반드시 재조회되게 무효화한다. 무거운 티켓 무효화(`_invalidate_ticket`)와
  경량(`_invalidate_ticket_content`, 뷰모드 체크박스용) · 인력뷰(`_invalidate_people_views`, 담당변경/전이/생성)를 구분.
- 이 파일은 크다(~3천 줄). 향후 mixin 분할 후보지만 지금은 단일 클래스(공유 `self.cache`/`self.provider`).
