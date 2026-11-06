# Confluence 문서 정리

Confluence 탭에서 문서를 찾고 읽으며, MCP로 작성·수정·구조 변경을 수행한다.
문서 즐겨찾기는 이 설치의 로컬 브라우저에 저장되고 Confluence 서버의 즐겨찾기를 변경하지 않는다.
지원 대상은 Data Center 9.2.4다. 복제, 다른 Space로 이동, Space 관리, 영구 삭제는 제공하지 않는다.

## 같은 Space의 구조 변경

`list_confluence_pages`를 루트와 각 부모에 사용하고 모든 `nextStartAt`을 순회한다.
권한상 보이지 않는 문서가 있을 수 있으므로 `coverage=accessible_only`는 전체 Space가 완전히 확인됐다는 뜻이 아니다.
`move_confluence_document`는 ID를 보존하고 부모·형제 순서를 변경한다. 빈 `parent_id`는 Space 루트,
`position=-1`은 명시 순서를 해제한다. 부모 변경으로 상속되는 접근 제한이 달라질 수 있다.

`bulk_confluence_changes`는 `move`, `update`, `trash`, `restore`를 순서대로 실행한다.
각 행에 고유 `operation_id`, 페이지 ID, `expected_version`을 넣고 필요한 앞선 행을 `depends_on`으로 연결한다.
같은 페이지의 두 번째 수정은 첫 번째 수정 후의 예상 버전을 사용한다.
기본 `dry_run=true`는 현재 관계·예상 버전을 검사하며 Jira/Confluence 쓰기를 하지 않는다.
이는 서버의 편집 권한이나 미래 구조를 잠그는 기능이 아니며 실행 때 다시 검사한다.
실행은 `dry_run=false`로 요청한다. 실패·확인 대기 행의 의존 작업은 건너뛰고 독립 작업은 계속한다.
재시도에는 **전체 원래 작업 목록과 같은 mutation ID**를 사용한다. 이미 완료된 작업은 반복하지 않는다.
전체 트랜잭션·자동 롤백은 없다. `verification_pending`이면 같은 요청으로 확인만 재시도할 수 있다.

## 제한과 휴지통

`get_confluence_restrictions`는 직접 지정된 read/update 제한과 fingerprint를 반환한다.
상속 제한이 없다는 뜻이 아니며 `complete=false`이면 그 응답으로 제한을 교체할 수 없다.
`set_confluence_restrictions`는 명시한 operation만 교체한다. 빈 users/groups는 해당 직접 제한을 해제한다.
사용자는 Confluence userKey, 그룹은 실제 그룹 이름으로 지정한다. Jira username을 userKey로 추정하지 않는다.
fingerprint 검사는 사전 비교이며 REST API의 원자적인 충돌 검사가 아니다.

휴지통 도구는 하위 페이지가 없는 페이지 하나만 처리한다. 하위 페이지를 먼저 이동하거나 명시적으로
아래에서부터 휴지통에 넣어야 한다. 복원에는 휴지통 상태의 페이지 ID와 버전이 필요하다.
DELETE에는 원자적인 version precondition이 없어 직전 버전 검사 후의 서버 경쟁 가능성은 남는다.

## 실제 서버 공동편집 검증

mock 통과는 Synchrony 검증이 아니다. 기존 문서 본문 수정과 이동은 운영에서 기본 비활성이다.
실제 DC 9.2.4의 전용 테스트 Space/문서에서 아래를 검증한 뒤 서버 설정에 기록한다.
일반 업무 문서에서 자동으로 시험하지 않는다.

1. 브라우저 두 개에서 같은 문서 편집을 열고, 한쪽에 미발행 문단을 작성한다.
2. 현재 발행 버전을 읽은 뒤 MCP에서 본문 수정한다. 다른 쪽에서 발행하고 내용 유실·중복 여부를 확인한다.
3. 같은 조건에서 부모·순서를 이동하고, 브라우저의 초안 발행 후 위치가 유지되는지 확인한다.
4. 서버에서 먼저 새 버전을 발행한 뒤 오래된 expected_version을 전송해 충돌과 추가 쓰기 0회를 확인한다.
5. 표, 코드, 이미지, 페이지 링크, 사용자 멘션, ac:/ri: 매크로의 작성→열람→수정→열람을 확인한다.
6. 버전 고정 조회, 루트 이동·순서 해제, 권한 거부와 인증 만료, 업로드 후 본문 수정 실패를 확인한다.

검증 보고서에 실제 서버 버전·공동편집 모드·문서 ID·검증 시각·기능별 결과를 남긴다.
통과한 기능에만 `CONFLUENCE_VERIFICATION_REVISION=9.2.4`와
`CONFLUENCE_VERIFIED_FEATURES=update_page,move_page`를 설정한다. 일부 실패하면 그 기능을 목록에 넣지 않는다.
이 값은 테스트 결과 기록이며 서버 버전을 바꾸거나 공동편집 충돌을 없애지 않는다.

발행 버전이 같아도 미발행 Synchrony 편집이 없다고 보장할 수 없다. `draftState=unknown`을 유지한다.
발행 버전 충돌·확인된 초안 충돌은 중단하고 초안을 삭제하거나 강제로 덮어쓰지 않는다.
