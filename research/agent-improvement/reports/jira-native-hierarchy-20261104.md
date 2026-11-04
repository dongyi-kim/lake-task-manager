# Jira 8.20.8 계층 변경 경로 검증

확인일: 2026-11-04. 공개 REST가 없다는 것이 Jira 화면 기능이 없다는 뜻은 아니다.

Atlassian 공식 [8.20.8 WAR](https://packages.atlassian.com/maven-external/com/atlassian/jira/atlassian-jira-webapp/8.20.8/atlassian-jira-webapp-8.20.8.war)의 필요한 JSP/XML/class만 HTTP Range로 읽었다. 제품 원본 파일은 git에 포함하지 않았다.

| 기능 | 실제 WebWork 경로 |
|---|---|
| Task → SubTask | `ConvertIssue.jspa` → `ConvertIssueSetIssueType.jspa` → 필요시 `ConvertIssueSetStatus.jspa` → `ConvertIssueUpdateFields.jspa` → `ConvertIssueConvert.jspa` |
| SubTask → Task-tier | 위와 같은 단계, 접두사 `ConvertSubTask` |
| SubTask 부모 변경 | `MoveSubTaskChooseOperation!default.jspa` → 작업 선택 POST → `MoveSubTaskParent!default.jspa` → `MoveSubTaskParent.jspa` POST |

확인한 계약:

- `WEB-INF/classes/actions.xml`: 실제 action alias와 단계별 메서드.
- `secure/views/issue/convertissuetosubtask-*.jsp`: `issuetype`, `parentIssueKey`, `targetStatusId`, `id`, `guid`. 필드가 없어도 UpdateFields 검증 단계를 통과해야 한다.
- `movesubtaskchooseoperation.jsp`, `movesubtaskparent.jsp`: `operation=move.subtask.parent.operation.name`, `parentIssue`, `id`.
- `decorators/panels/jiraform.jsp`: POST 기본값과 hidden `atl_token` 삽입.
- `webwork.properties`: `webwork.ui.theme=standard`. `template/standard/issuepicker.jsp`는 common 텍스트 입력을 include한다. 빈 AUI AJAX select를 기본 폼으로 가정하지 않는다.
- `AbstractConvertIssue`·`ConvertIssueBean`: 최초 guid 없는 요청만 새 wizard version 생성. 단계 POST 사이에는 guid를 유지한다.
- 변환 서비스는 Edit Issue 권한, 부모 이동은 Move Issues 권한을 검사한다. 부모의 조회·편집 가능 여부, 동일 프로젝트·타입·자기 자신/동일 부모 여부도 Jira에서 검증한다.
- 변환 확인 화면은 필드 제거 및 보안 수준 상속을 보여준다. 미리보기 없이 자동 수락하지 않는다.

구현은 기존 인증 provider의 쿠키를 사용하는 form adapter다. 임의 URL/REST를 노출하지 않으며 동일 Jira의 알려진 action만 허용한다. 세션 wizard를 직렬화하고 최종 POST를 자동 재전송하지 않는다.

검증은 독립적으로 작성한 최소 렌더링 폼과 mock Jira REST를 사용했다. 성공 HTTP만으로 완료를 판단하지 않고 실제 타입/부모를 재조회한다. 실 운영 Jira·SSO·사내 플러그인의 HTML은 실행하지 않았다. 표준 폼과 다른 widget/추가 필드가 나타나면 지원되지 않는 폼으로 중단한다.

공개 REST 부재 근거: [JRASERVER-27893](https://jira.atlassian.com/browse/JRASERVER-27893), [JRASERVER-68763](https://jira.atlassian.com/browse/JRASERVER-68763). Java 액션 근거: [8.20.8 MoveSubTaskParent](https://docs.atlassian.com/software/jira/docs/api/8.20.8/com/atlassian/jira/web/action/issue/MoveSubTaskParent.html).
