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
- **jql_execution.py** — 논리적인 캐시 단위와 실제 Jira 요청 묶음을 분리한다. 사람·상태·Epic 조건을
  증명 가능한 범위에서 묶고, 응답의 실제 식별 필드로 개별 membership을 나눈다.

## 규칙 (AGENTS.md §8 아키텍처 규칙)
- **어떤 인증인지 몰라야 한다** — 환경 분기 없음, provider 만 다르다.
- 커스텀 필드 ID·상태명 하드코딩 금지 → config/`statusCategory`.
- **쓰기 후 무효화**: 편집이 바꾸는 화면은 반드시 재조회되게 무효화한다. 무거운 티켓 무효화(`_invalidate_ticket`)와
  경량(`_invalidate_ticket_content`, 뷰모드 체크박스용) · 인력뷰(`_invalidate_people_views`, 담당변경/전이/생성)를 구분.
- **생성 권한**: Reporter는 생성 요청에서 완전히 생략하고 Jira의 현재 사용자 기본값에 맡긴다.
  부모 편집 권한은 자식 생성 권한이 아니다. Reporter 변경은 LTM 매니저 여부와 별개로 해당 티켓의
  Jira `MODIFY_REPORTER`와 `editmeta`를 모두 확인한다. 생성 실패 로그의 revision·fields·reporter_present·error_fields로
  실제 전송 필드와 Jira 오류 필드를 비교하며, 요청 값·쿠키·원문 오류 메시지는 진단 로그에 남기지 않는다.
- 정규화·합집합·중복 제거·ORDER·pagination과 issue 캐시 write-through는 UI·MCP가 공유한다.
  재사용 가능한 사람·상태·Epic leaf는 개별 캐싱하되, cold leaf를 최대 5명/5개 Epic 단위 요청으로
  묶는다. 같은 상태의 나머지 조건이 같을 때만 사용자/상태 필드로 분리하며, 필요한 필드가 없으면
  완전한 개별 membership으로 추정하지 않는다. 임의 cold OR는 전체 쿼리로 캐싱하고 모든 leaf를
  추가 warming하지 않는다. 기존 fresh leaf가 있으면 먼저 재사용하고 누락된 부분만 조회한다.
  leaf/전체 쿼리 payload는 issue key 목록만 가지며, `issue→leaf` 역인덱스와 `predicate field→leaf` 의존
  인덱스로 성공한 `MutationEvent`가 영향을 주는 leaf만 만료한다. snapshot/row generation은 별도로
  올려 기존 pagination cursor는 불변으로 두고 새 조회만 최신 row를 사용한다.
- 직렬 provider는 요청 묶음을 하나씩 실행하고 완료된 묶음부터 표시한다. 모든 페이지가 완료되고
  mutation fence가 유지돼야 membership을 저장한다. 인증·네트워크 실패를 정상 빈 목록으로 캐싱하거나
  개별 leaf 재시도로 증폭하지 않는다. projection과 무관한 membership은 탭 간 공유하며,
  issue/full-light projection 경계와 원래 수집 시각을 유지한다.
- `JiraClient`의 공개 메서드 계약은 유지하면서 결합도가 낮은 기능군부터 mixin 서비스로 분리한다.
  서비스는 인증 구현을 알지 않고 facade가 제공하는 `self.cache`/`self.provider`만 사용한다.
