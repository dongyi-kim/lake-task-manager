# 문서 구조 정리

1. `list_confluence_spaces`에서 설정된 Space를 고르고 `list_confluence_pages`로 루트와 각 직계 자식을 순회한다. `nextStartAt`이 있으면 계속 조회한다. 접근할 수 없는 문서를 없는 문서로 단정하지 않는다.
2. 제목 대신 문서 ID로 대상을 고정한다. `check_confluence_revision`과 원문 읽기로 발행 버전을 확인한다. 같은 발행 버전은 미발행 공동편집이 없다는 증거가 아니다.
3. 단일 이동에는 `move_confluence_document(page_id,parent_id,expected_version,client_mutation_id)`를 쓴다. 빈 parent_id는 Space 루트다. 형제 순서는 position으로 지정하고 -1이면 명시 순서를 해제한다. 복제·Space 간 이동은 지원하지 않는다.
4. 여러 변경에는 `bulk_confluence_changes`에 명시적 operation_id, action, page_id, expected_version을 넣는다. action은 update/move/trash/restore다. 의존하는 앞선 행을 depends_on으로 지정한다. 같은 문서를 순차 수정하면 두 번째 행은 첫 수정 후 예상 버전을 사용한다.
5. 기본 dry_run=true로 입력·관계를 검사한다. 사용자에게 허용받은 작업을 실행할 때 dry_run=false로 전송한다. dry-run은 편집 권한이나 트리를 잠그지 않으며 실행 때 다시 확인한다. 같은 배치 재시도는 전체 원래 목록과 같은 mutation ID를 유지한다.
6. 모든 결과 행을 확인한다. completed만 완료이며 verification_pending은 같은 요청으로 확인을 재시도한다. failed/outcome_unknown의 의존 작업은 skipped가 된다. 독립적인 성공을 전체 실패로 취급해 반복하지 않는다. 전체 트랜잭션과 자동 롤백은 없다.

## 접근 제한

`get_confluence_restrictions`로 직접 제한과 fingerprint를 읽는다. complete=false이면 그 결과로 수정하지 않는다. `set_confluence_restrictions`는 read/update 중 명시한 operation만 교체한다. users는 Confluence userKey, groups는 실제 그룹 이름이다. 둘 다 빈 목록이면 해당 직접 제한을 해제하므로 사용자가 접근 범위 확대를 요청한 경우에만 사용한다.

상위 문서·Space에서 상속한 제한은 별도로 남는다. fingerprint는 사전 비교이며 원자적인 서버 CAS가 아니다. 이동 후 접근 제한이 달라질 수 있으므로 유효 접근 여부를 다시 확인한다.

## 휴지통·복원

`trash_confluence_document`는 하위 페이지가 없는 페이지 하나만 휴지통으로 옮긴다. 하위 페이지가 있으면 먼저 이동하거나 별도로 아래에서부터 처리한다. 영구 삭제는 제공하지 않는다.

`list_confluence_trashed_pages`에서 Space별 휴지통의 ID·버전을 확인한 뒤 `restore_confluence_document`를 호출한다. 복원 후 실제 위치를 조회한다. DELETE의 버전 검사는 사전 검사이며 서버의 원자적인 조건부 삭제가 아니다.

운영에서 기존 페이지 수정·이동은 실제 Confluence DC 9.2.4 공동편집 검증이 완료된 기능만 활성화한다. 미검증 오류를 버전 숫자 변경이나 다른 API로 우회하지 않는다.
