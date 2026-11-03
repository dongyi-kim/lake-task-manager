# Local MCP

실행 중인 LTM이 `http://127.0.0.1:<설정 포트>/mcp`에서 표준 Streamable HTTP MCP를 제공한다.
별도 Jira 로그인, LLM 키, 별도 서버 실행이 필요 없다. UI와 같은 `JiraClient`, 인증 provider,
JQL leaf/issue/user/Epic/Confluence 캐시, mutation invalidation을 사용한다.

## 연결

1. LTM을 실행하고 **설정 → MCP 연결**을 연다.
2. HTTP를 지원하는 클라이언트에는 **HTTP 설정 복사 (인증 포함)** 결과를 붙여넣는다.
3. stdio만 지원하는 클라이언트에는 **stdio 설정 복사 (인증 포함)** 결과를 사용한다.
   stdio 프로세스는 실행 중인 LTM으로 메시지를 전달하며 앱을 자동 실행하거나 mock 데이터를 만들지 않는다.
4. 주소만 필요한 경우 **주소 복사**를 사용한다. 이 값에는 인증정보가 없다.

화면은 실제 포트, MCP 준비 상태와 Jira 인증 상태를 별도로 표시한다. 토큰은 기본적으로 가려지며
명시적인 표시/설정 복사 때만 반환된다. 재발급하면 기존 설정은 즉시 사용할 수 없으므로 새 설정을 복사한다.
연결을 끄면 도구 요청을 즉시 거절한다. 꺼진 상태에서도 설정 준비와 토큰 재발급은 가능하다.

인증 포함 설정은 비밀값이다. 서버에는 `.cache/local_mcp_secret.json`에 별도로 저장하며
일반 prefs·상태 응답·로그·URL에는 토큰을 넣지 않는다. 연결은 실제 loopback peer와 정확한 Host/Origin을
검사한다. 표준 런처는 forwarded header로 peer를 덮어쓰지 않는다. LAN 공개 MCP 또는 reverse proxy 용도가 아니다.

## 도구

| 목적 | 도구 |
|---|---|
| JQL·페이지 | `search_issues` |
| 티켓 본문·댓글·이력·자식·Epic | `get_ticket`, `get_comments`, `get_history`, `get_field_history`, `get_children`, `get_epic`, `get_ticket_context` |
| 통합검색·Confluence 검색/본문 | `search_workspace`, `search_confluence_documents`, `read_confluence_document` |
| 사람·선택지·워크로드 | `search_users`, `get_user`, `list_options`, `get_workload` |
| WBS·VIT·팀 워크로드·활동 | `get_dashboard`, `get_vit_detail`, `get_epic_progress`, `get_activity` |
| 부속 정보·첨부파일 내용 | `get_attachments`, `read_attachment`, `get_documents` |
| 생성 | `create_task`, `create_subtask`, `create_epic` |
| 일괄 생성 | `bulk_validate`, `bulk_create` |
| PMO_VIT 등록·해제 | `get_vit_overview`, `set_vit_tracking` |
| WBS 계획·인력 관리 | `get_workspace_config`, `create_wbs_task`, `update_wbs_task`, `delete_wbs_task`, `set_module_people`, `rename_people_module`, `delete_people_module` |
| 본문·필드 수정·전이 | `update_description`, `update_fields`, `transition_ticket` |
| 댓글 | `add_comment`, `edit_comment`, `delete_comment` |
| 티켓·문서 링크 | `add_issue_link`, `delete_issue_link`, `add_document`, `delete_document` |
| 첨부·삭제 | `add_attachment`, `delete_attachment`, `delete_ticket` |

검색은 `search.jira.projects` 범위로 제한한다. `search_issues`는 한 페이지 최대 100건이며 전체 건수에
숨은 제한이 없다. `hasMore`, `nextStartAt`, `snapshotId`를 따라 동일 snapshot의 다음 페이지를 요청한다.
로컬 정렬을 지원하지 않는 JQL은 기존 Jira pagination으로 폴백하며, 이 경우 snapshot ID가 없다.
읽기 결과는 MCP structured content의 `{ok: true, data: ...}`에 있다. 제한이 있는 댓글/이력 조회는
요청 limit을 응답에 명시한다. 이력은 기본적으로 현재 티켓만 포함하고 `include_children`을 켜면 하위 이력도 조회한다.
읽기에는 `cache` 출처 정보와 `partial` 여부도 포함한다. 출처를 수집할 수 있으면 가장 오래된
`fetchedAt`·`freshUntil`·`displayUntil`, stale 여부와 policy revision을 반환한다.
관측 정보가 없으면 `available: false, state: unknown`으로 표시하며 최신 데이터라고 단정하지 않는다.

총 **49개** 도구를 제공한다. `get_ticket`의 `descriptionHtml`은 열람용, `descriptionEditHtml`은
수정용 본문이다. 생성 시 `body.descriptionHtml`, 이후 본문 수정은 `update_description(html, ...)`,
댓글 작성·수정은 `add_comment`·`edit_comment(html, ...)`를 사용한다. 기존 UI와 동일한 Jira serializer를 거친다.
`update_fields`의 `labels`와 `components`에는 문자열 배열을 전달하며 `[]`는 해당 필드를 모두 비운다.

일괄 생성은 먼저 `bulk_validate(mode, items)`로 UI와 같은 실제 타입·담당자·컴포넌트·관계 검증을 한다.
`mode="task"` 항목에는 `epic` 키가 필수이며 독립 Task는 `epic:null`을 명시한다.
`mode="subtask"`는 이미 존재하는 Task의 `parent` 키를 받으므로 부모를 먼저 생성한 다음 별도 batch로 자식을 만든다.
각 항목은 `summary`, `type`이 필수이며 `priority`, `duedate`, `assignee`, `components`, `labels`,
Markdown `description`을 함께 지정할 수 있다. 한 batch는 최대 100개이고 입력 순서대로 생성한다.
`created`·`failed`에는 원래 항목 `index`가 남는다. 일부 성공은 롤백되지 않는다.
재시도는 **전체 원본 items·순서·batch mutation ID를 그대로** 전달한다. 이미 성공한 항목은 결과를 재사용하고
확정 실패만 다시 시도한다. 응답 유실은 동일한 항목 ID로 대조하며, 확인할 수 없으면 `outcome_unknown`을 유지한다.

PMO_VIT는 별도 설정 파일이 아닌 Jira의 정확한 `PMO_VIT` 라벨로 등록한다.
`get_vit_overview`는 등록 티켓·표시되는 root·상위 등록 때문에 숨겨진 하위 root를 구분한다.
Component로 모듈을 나누고 하위 티켓의 완료 개수/전체 개수로 진척률을 계산한다. WBS의 Story Point 계산과 다르다.
`set_vit_tracking`은 최신 라벨을 읽고 해당 라벨만 추가·삭제하여 다른 라벨을 보존한다.
신규 등록은 `create_task`의 `labels:["PMO_VIT"]`와 모듈 `components`를 사용한다.

WBS Task는 Jira Task와 다른 **로컬 일정 계획**이다. 관리자에게만 제공되는 **설정 → WBS·인력 관리**와
MCP가 같은 검증·원자 저장 경로를 사용한다. ID·모듈·이름·기간·Epic 상대 가중치를 등록하고 수정할 수 있다.
인력 관리는 모듈별 Jira username 목록을 관리하며 Jira 사용자/컴포넌트나 관리자 권한을 변경하지 않는다.
모든 설정 쓰기는 `get_workspace_config`에서 받은 `expected_revision`을 전달한다. 다른 변경과 충돌하면
409를 반환하므로 다시 읽고 변경사항을 적용한다. 성공 후 WBS·인력 관련 캐시를 무효화하고 열린 화면에도 설정 변경을 알린다.

Confluence 검색은 `search.confluence.spaces` 범위에서 페이지를 반환하고, 본문 읽기는 숫자 ID 또는
설정된 Confluence 호스트의 `/pages/{id}` 또는 `?pageId=` URL을 받는다. `/display/SPACE/title` 형식은
먼저 검색으로 숫자 ID를 확인한다. 검색은 `nextStartAt`, 긴 본문은 `nextOffset`을 따라 끝까지 읽을 수 있다.
문서 제목·space·version과 전체 길이를 함께 반환하며, excerpt만으로 문서를 읽었다고 표현하지 않는다.
UI 통합검색·내장 Agent·MCP는 환경/Confluence 서버/사용자/요청 projection으로 구분된 같은 캐시를 사용한다.
인증 실패나 불완전 응답을 빈 문서로 캐싱하지 않는다. Confluence 문서 생성·수정은 현재 LTM에 없는 기능이므로 제공하지 않는다.

WBS와 사용자 활동에는 UI와 같은 manager 검사, 팀 워크로드에는 같은 module/people 범위가 적용된다.
Bitbucket 검색은 현재 LTM에서도 예정 기능의 mock placeholder이므로 MCP 도구에 포함하지 않는다.
LLM 설정, 인증 쿠키, 비밀값 관리, 임의 REST 호출도 업무 도구로 노출하지 않는다.

티켓 쓰기 도구는 **Jira에 직접 반영**하고 WBS·인력 관리 도구는 로컬 설정을 저장한다.
외부 클라이언트에서 사용자가 허용한 작업만 실행해야 한다.
내장 LTM Agent의 승인 카드는 그대로 유지되며, MCP가 그 승인 토큰을 발급하거나 우회하지 않는다.
실제 Jira metadata·권한과 UI의 application command를 공유한다. 완료된 티켓의 속성은 먼저 Jira가
제공하는 재개 전이를 실행해야 수정할 수 있다. Reporter는 생성 시 생략하고 현재 Jira 사용자로 지정된다.

모든 Jira 쓰기에는 고유 mutation ID가 필요하다. 생성/필드/전이는 `body.clientMutationId`, 나머지는
`client_mutation_id`를 사용한다. 동일 요청의 재시도에는 **같은 ID와 같은 내용**을 사용한다.
응답이 유실되어 `outcome_unknown`이 반환된 경우 새 ID로 반복하지 않는다. 생성·댓글 추가·전이·첨부는
기존 Jira 대조 로직으로 복구한다. 나머지 쓰기는 결과를 증명할 수 없으면 재실행하지 않고 unknown을 유지하므로,
최신 티켓을 조회해 반영 여부를 확인한다. 단일 작업 실패는 MCP `isError`와 구조화된 error code로 반환한다.
일괄 생성의 검증/부분 실패는 `data.ok:false`와 항목별 `errors`·`failed`로 반환하므로 반드시 확인한다.

첨부 업로드는 최대 10MiB의 base64 바이트를 받는다. `read_attachment`는 티켓에 속한 ID를 검증한 후
실제 파일 바이트를 base64로 반환하며, `nextOffset`을 따라 최대 10MiB 파일을 읽는다. 로컬 파일 경로는 받지 않는다.

## 코드와 검증

- `runtime.py`: SDK 등록·응답·lifespan과 앱 연결
- `security.py`, `settings.py`: loopback/bearer 경계와 명시적 설정 복사
- `read_tools.py`, `workspace_tools.py`, `write_tools.py`, `vit_tools.py`, `admin_tools.py`: 타입이 있는 업무 도구 catalog
- `stdio.py`: 표준 SDK stdio/HTTP 메시지 중계
- `app/services/ticket_commands.py`: HTTP UI와 MCP가 공유하는 쓰기 command
- `app/services/workspace_config.py`: WBS·인력 UI와 MCP가 공유하는 설정 command
- `tests/mcp/`: 실제 SDK HTTP/stdio, 캐시 재사용, CRUD, 응답 유실과 보안 경계

`python -m app.agent.mcp_server`는 이전 명령 호환용 stdio proxy다. `LTM_MCP_URL`과 `LTM_MCP_TOKEN`을
설정 화면에서 받은 값으로 지정해야 하며, 예전처럼 별도 mock 서버를 시작하지 않는다.
