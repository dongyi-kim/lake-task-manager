# app/jira — Jira/Confluence/Bitbucket REST 클라이언트

상류(Jira DC 8.20.8 등)와 대화하는 유일한 계층. **AuthProvider 를 주입**받아 어떤 인증인지 모른 채
REST 를 호출하고, **모든 호출은 캐시(app/infra)를 경유**한다. mock/local/prod 세 환경이 **동일 파서 경로**다
(provider 만 교체).

## 파일
- **jira_client.py** — `JiraClient`. 이슈 조회·검색·Epic 트리·워크로드·티켓 뷰·쓰기·변경이력·계보를 조립하는 공개 facade.
- **identity_service.py** — 사용자 display name/멘션 뱃지 캐시와 `/myself` 기반 세션 상태·회로차단기.
- **media_service.py** — 사용자 아바타, 링크 제목/favicon, 인증 첨부 미디어 프록시와 SSRF 허용 호스트 정책.
- **workload_service.py** — 인력별 workload 집계·버킷, SubTask Epic 상속, 활동 피드 조립.
- **cache_policy.py** — JQL 역인덱스와 mutation 기반 선택적 캐시 무효화 정책.
- **jql.py** — 앱이 사용하는 JQL subset의 AST 파싱·context 해소·DNF leaf 분해·canonical 정렬과
  OR 합성 결과의 로컬 정렬. 미지원 문법·함수·정렬은 `jira_client`의 기존 Jira 실행 경로로 폴백한다.

## 규칙 (AGENTS.md §8 아키텍처 규칙)
- **어떤 인증인지 몰라야 한다** — 환경 분기 없음, provider 만 다르다.
- 커스텀 필드 ID·상태명 하드코딩 금지 → config/`statusCategory`.
- **쓰기 후 무효화**: 편집이 바꾸는 화면은 반드시 재조회되게 무효화한다. 무거운 티켓 무효화(`_invalidate_ticket`)와
  경량(`_invalidate_ticket_content`, 뷰모드 체크박스용) · 인력뷰(`_invalidate_people_views`, 담당변경/전이/생성)를 구분.
- 지원되는 검색은 모든 OR leaf를 개별 캐싱한 뒤 앱에서 합집합·중복 제거·ORDER·pagination한다.
  leaf payload는 issue key 목록만 가지며, `issue→leaf` 역인덱스와 `predicate field→leaf` 의존
  인덱스로 성공한 `MutationEvent`가 영향을 주는 leaf만 만료한다. snapshot/row generation은 별도로
  올려 기존 pagination cursor는 불변으로 두고 새 조회만 최신 row를 사용한다.
- 병렬 provider는 leaf를 동시에 완주한다. prod SSO/mock처럼 직렬 provider에서 leaf가 4개를 넘으면
  첫 exhaustive snapshot을 동치 전체 JQL로 먼저 만들고, 모든 leaf는 background 우선순위로 빠짐없이
  warming한다. projection과 무관한 leaf membership은 탭 간 공유하고, snapshot은 generation별 issue
  row를 공유해 중복 payload와 SQLite commit을 줄인다.
- `JiraClient`의 공개 메서드 계약은 유지하면서 결합도가 낮은 기능군부터 mixin 서비스로 분리한다.
  서비스는 인증 구현을 알지 않고 facade가 제공하는 `self.cache`/`self.provider`만 사용한다.
