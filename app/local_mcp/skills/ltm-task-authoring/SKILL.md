---
name: ltm-task-authoring
description: LTM MCP로 Task·Epic·SubTask 한 건을 생성하거나 기존 티켓 본문을 작성·수정한다. 표·멘션·링크·이미지를 포함하는 본문 작성에 사용하며 여러 티켓의 일괄 생성은 ltm-bulk-task-create를 사용한다.
---

# LTM 티켓 작성

사용자가 요청한 업무의 범위·완료 조건·근거를 보존해 Jira 티켓으로 작성한다. 새로운 담당자·기한·업무 사실을 만들어 넣지 않는다. 생성·본문 수정 전에 읽을 수 있는 초안과 대상을 공유하고 확인받는 것을 권장한다. 해당 내용이 이미 승인됐거나 바로 제출하라는 지시가 있으면 재확인하지 않는다. 초안만 요청했다면 게시하지 않는다.

## 필요한 참고 문서

- [제출 전 초안 검토](references/draft-review.md): 검토할 내용, 확인 시점, 기존 승인 재사용.
- 본문 작성 시 [작성 형식](references/content-format.md)의 **Jira Task 본문 작성 스타일**을 읽는다. 표·bullet·어미 없는 개조식·간결한 문체와 점검사항·작업목표의 체크박스 활용을 권장하며, 리치 본문 형식도 같은 문서를 따른다.
- 사람·Jira·Confluence·외부 링크를 넣을 때 [사람과 링크](references/people-and-links.md)를 읽는다.
- 게시 전과 오류 복구 시 [쓰기·검증 절차](references/write-workflow.md)를 따른다.
- 완료 상태·권한·타입/부모 변경을 다룰 때 [티켓 작업 가이드](references/ticket-actions.md)를 읽는다.
- [예제 데이터](references/examples.json)는 치환용 샘플이다. 실제 조회 결과나 실행할 요청이 아니다.

## 생성

1. 사용자 요청에서 제목, 업무 범위, 티켓 계층과 알려진 필드를 정리한다. 주어진 문서·티켓을 근거로 삼을 때는 해당 내용을 읽는다.
2. 독립 Task는 `list_options(kind="task_types")`, 기존 Epic/Task의 자식은 `list_options(kind="child_types", key=...)`로 실제 허용 type을 확인한다. 이어 `list_options(kind="create_fields", issue_type=실제이름)`의 **모든 필드 페이지**를 `nextStartAt`으로 읽어 필수값과 `inputFields` 경로를 확인한다. `field_id`를 추가하면 해당 필드의 정확한 기본값·허용 선택지를 별도로 페이지 조회한다. `managed=true`인 project/issuetype/reporter는 LTM/Jira가 처리한다. 각 페이지의 `unsupportedRequired`를 모아, 지원 입력이나 Jira 기본값이 없는 필수 custom field가 있으면 해당 생성은 Jira에서 처리해야 함을 알린다.
3. `create_task(body, epic_key)`는 Task-tier를 만든다. 기존 Epic 연결은 `epic_key`, 독립 티켓은 빈 문자열이다. SubTask는 기존 Task-tier의 `parent_key`로 `create_subtask`를 호출한다. Epic은 `create_epic`을 사용한다.
4. `body.summary`, `body.type`과 필요한 선택 필드만 채운다. Epic 생성은 `type` 대신 `summary`와 선택적인 `epicName`을 받는다. 본문은 `body.descriptionHtml`에 HTML로 넣는다. 생성 요청에 `reporter`를 넣지 않는다.
5. `body.assignee`는 확인한 Jira 사용자 ID, `components`·`labels`는 문자열 배열, `duedate`는 `YYYY-MM-DD`다. 각 쓰기에는 새 `clientMutationId`를 정하고 정확한 요청을 보존한다.
6. 반환된 key로 생성 결과를 확인한다. 첨부가 필요하면 이 key에 업로드한 후 본문에 반환 경로를 넣어 별도 수정한다. 첨부 단계가 실패해도 티켓을 다시 생성하지 않는다.

본문에는 업무에 맞는 제목·목록·표를 선택한다. 목적, 작업 범위, 완료 조건, 관련 근거가 유용할 수 있지만 사용자가 제공한 양식이나 짧은 요청을 불필요한 고정 템플릿으로 늘리지 않는다.

## 기존 본문 수정

1. `get_ticket(key, fresh=true)`로 최신 `descriptionEditHtml`과 상태를 읽는다. 열람용 `descriptionHtml`이나 화면 DOM 전체를 새 본문으로 복사하지 않는다.
2. 요청한 부분만 바꾸고 기존 링크·멘션 ID·이미지 경로·다른 섹션을 보존한다. 변경에 필요한 권한과 필드는 `list_options(kind="edit_fields", key=...)`로 확인할 수 있다.
3. `update_description(key, html, client_mutation_id)`은 **본문 전체 교체**다. `html=""`은 삭제 의도가 있을 때만 쓴다. 완료 티켓 본문은 직접 수정할 수 없으며, 요청 범위에 포함된 실제 재열기 전이가 먼저 필요하다.
4. 게시 후 `get_ticket(key, fresh=true)`로 요청한 변경과 보존할 내용을 확인한다. 결과에는 티켓 key와 실제 수행한 변경, 남은 후속 단계를 구분한다.

실제로 참고한 Jira 이슈·Confluence 문서·외부 자료는 본문 참조와 함께 **티켓의 이슈 링크·문서 연결에도 적극 등록하는 것을 권장**한다. [사람과 링크](references/people-and-links.md)의 참조 연결 절차에 따라 초안에 연결 대상을 포함하고, 생성된 key에 승인 범위의 연결을 추가·확인한다. 생성 응답의 `cascade`는 제안이며 실행된 상태 전이가 아니다.
