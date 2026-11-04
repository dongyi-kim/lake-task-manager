# 티켓 작업·상태·권한

## 먼저 확인할 것

- `get_ticket(key, fresh=true)`로 현재 티켓과 상태를 읽는다. `statusCategory=done`은 LTM의 필드 수정 제한 기준이다. 이름이 Resolved/Closed인지로 추측하지 않는다.
- `list_options(kind="edit_fields", key=...)`는 이 티켓의 현재 Jira 편집 metadata다. 필드가 없으면 권한뿐 아니라 workflow나 편집 화면 설정 때문일 수 있다. 다른 티켓의 metadata를 재사용해 쓰기를 허용하지 않는다.
- LTM 매니저는 Jira 관리자와 다르다. Jira 권한, workflow 조건과 validator가 최종 적용된다. metadata 조회 성공이 이후 쓰기의 성공을 보장하지 않는다.

## 작업별 계약

| 작업 | 도구와 제한 |
|---|---|
| 제목 변경 | `update_fields(body.summary=...)`. `name`·`title`은 지원 입력이 아니다. Done은 먼저 별도 재열기가 필요하다. |
| 본문·일반 필드 수정 | `update_description` 또는 `update_fields`. Done은 금지. `labels`·`components` 배열은 전체 교체이고 `[]`는 비움이다. |
| Reporter 수정 | `update_fields(body.reporter=...)`. 실제 Jira `MODIFY_REPORTER` 권한과 edit metadata가 필요하다. 생성 시 Reporter는 로그인 사용자로 정해진다. |
| 상태 전이 | `list_options(kind="transitions", key=...)`가 반환한 ID와 `fields[].inputFields`를 사용한다. Done에서도 현재 가능한 전이만 실행할 수 있다. |
| 댓글 작성 | `add_comment`. Done에도 가능하지만 Jira 댓글 권한은 필요하다. |
| 댓글 수정·삭제 | `edit_comment`·`delete_comment`. Jira의 본인/다른 사람 댓글 편집·삭제 권한을 따른다. 본문 수정 권한과 같다고 가정하지 않는다. |
| 첨부·관계·문서 링크 | 각각의 전용 도구를 사용한다. Jira 작업별 권한과 일부 작업의 LTM 편집 검사를 따른다. 이슈 링크는 계층의 parent를 바꾸지 않는다. |
| 티켓 삭제 | `delete_ticket`. UI와 같은 LTM 편집 검사와 실제 Jira 삭제 권한이 필요하다. Done 수정 금지를 모든 삭제의 금지나 허용으로 일반화하지 않는다. |
| Task의 Epic 변경 | `update_fields(body.epic=Epic키)`, 빈 문자열은 연결 해제다. 일반 필드 수정과 같은 상태·metadata 제한을 따른다. |
| Task↔SubTask 변환, SubTask 부모 변경 | `get_hierarchy_options`로 준비하고 `change_ticket_hierarchy`로 실행한다. `update_fields`의 `parent`·`issuetype` PUT이나 이슈 링크로 대체하지 않는다. |

## 완료 상태와 재열기

`ticket_completed` 오류는 `nextAction`으로 현재 전이 목록을 조회하는 방법을 준다. 이 조회는 재열기 실행 승인이 아니다. 사용자가 재열기를 요청한 경우에만 실제 제공된 재열기 전이를 별도로 실행한 뒤 새 수정 요청을 보낸다. 작업 편의를 위해 자동으로 재열기·수정·재완료하지 않는다.

재열기 전이가 없거나 필수 필드가 MCP에서 지원되지 않으면 Jira workflow/권한 확인이 필요하다. `worklog`가 필수이면 실제 소요시간을 `body.days`·`body.hours`·`body.minutes`로 전달한다. 모르는 시간을 임의로 채우지 않는다. `commentRequired=true`이면 비어 있지 않은 `body.commentHtml`도 필요하다.

## 계층 변경과 Jira 화면

Jira Server/DC 8.20의 공개 REST에는 Task↔SubTask 변환과 SubTask 부모 변경 API가 없다. Jira 화면의 기능 부재라는 뜻은 아니다. Jira는 별도 변환·이동 wizard에서 타입, 부모, 필요시 workflow 상태와 필수 필드를 확인한다.

- 자식 SubTask가 있는 Task는 그대로 SubTask로 변환할 수 없다. 완료 상태는 별도 재열기가 필요하다. 변환은 Jira Edit Issue, 부모 변경은 Move Issues 권한을 따른다.
- SubTask의 parent는 Task-tier다. Task의 Epic 연결과 구분한다. 일반 이슈 링크 추가는 어느 쪽도 대체하지 않는다.
- LTM은 기본 Jira 8.20.8 웹 폼 경로를 사용한다. 사내 플러그인 등 지원되지 않는 폼은 Jira 화면에서 처리해야 한다. mock 검증을 운영 Jira 검증으로 표현하지 않는다.
- 완료 상태의 편집 가능 여부는 Jira workflow에도 영향을 받는다. 권한 오류를 인증 유실로 해석해 로그인을 반복하거나, 다른 계정으로 우회하지 않는다.

근거: Atlassian [변환 REST 요청 JRASERVER-27893](https://jira.atlassian.com/browse/JRASERVER-27893), [부모 변경 REST 요청 JRASERVER-68763](https://jira.atlassian.com/browse/JRASERVER-68763).

MCP 흐름: `get_hierarchy_options(key, body)`의 action을 `convert_to_subtask`, `convert_to_task`, `change_parent` 중 선택한다. Task 변환 외에는 같은 프로젝트의 `parent_key`가 필요하다. 반환된 실제 타입 ID·상태 ID·필드 값을 body에 넣어 `ready=true`까지 조회한다. 보안 수준과 `changes`, `removedFields`를 확인한다. `change_ticket_hierarchy`에는 동일 body, 새 `client_mutation_id`, 마지막 `previewHash`를 `preview_hash`로 전달한다. 필드 제거를 사용자가 요청했으면 정확한 `acknowledged_removed_fields`도 전달한다. 티켓이나 대상 부모의 상태/보안 수준이 달라지면 미리보기를 다시 읽는다.

응답 유실은 동일 ID·입력으로 복구한다. 관계만으로 추가 필드 변경/삭제를 증명할 수 없으면 `outcome_unknown`을 유지하므로 새 ID로 반복하지 않는다.

## 실패 처리

`field_not_editable`의 `nextAction`은 현재 편집 필드 재조회다. 권한·화면·workflow가 바뀌지 않았다면 같은 요청을 반복하지 않는다. 인증, 지연, 부분 성공과 결과 불명확 상황은 [쓰기·재시도 절차](write-workflow.md)를 따른다. 여러 티켓 작업에서도 성공한 항목과 실패한 항목을 구분하며, 한 티켓의 권한이나 전이 ID를 전체에 적용하지 않는다.
