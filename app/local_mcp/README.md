# Local MCP

실행 중인 LTM이 `http://127.0.0.1:<설정 포트>/mcp`에서 표준 Streamable HTTP MCP를 제공한다.
별도 Jira 로그인, LLM 키, 별도 서버 실행이 필요 없다. UI와 같은 `JiraClient`, 인증 provider,
JQL leaf/issue/user/Epic 캐시, mutation invalidation을 사용한다.

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
| 티켓·댓글·이력·자식·Epic | `get_ticket`, `get_comments`, `get_history`, `get_children`, `get_epic` |
| 사람·선택지·워크로드 | `search_users`, `get_user`, `list_options`, `get_workload` |
| 부속 정보 | `get_attachments`, `get_documents` |
| 생성 | `create_task`, `create_subtask`, `create_epic` |
| 수정·전이 | `update_fields`, `transition_ticket` |
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

쓰기 도구는 **Jira에 직접 반영**한다. 외부 클라이언트에서 사용자가 허용한 작업만 실행해야 한다.
내장 LTM Agent의 승인 카드는 그대로 유지되며, MCP가 그 승인 토큰을 발급하거나 우회하지 않는다.
실제 Jira metadata·권한과 UI의 application command를 공유한다. 완료된 티켓의 속성은 먼저 Jira가
제공하는 재개 전이를 실행해야 수정할 수 있다. Reporter는 생성 시 생략하고 현재 Jira 사용자로 지정된다.

모든 쓰기에는 고유 mutation ID가 필요하다. 생성/필드/전이는 `body.clientMutationId`, 나머지는
`client_mutation_id`를 사용한다. 동일 요청의 재시도에는 **같은 ID와 같은 내용**을 사용한다.
응답이 유실되어 `outcome_unknown`이 반환된 경우 새 ID로 반복하지 않는다. 생성·댓글 추가·전이·첨부는
기존 Jira 대조 로직으로 복구한다. 나머지 쓰기는 결과를 증명할 수 없으면 재실행하지 않고 unknown을 유지하므로,
최신 티켓을 조회해 반영 여부를 확인한다. 실패는 MCP `isError`와 구조화된 error code로 반환한다.

첨부는 최대 10MiB의 base64 바이트를 받는다. 로컬 파일 경로를 읽는 도구나 임의 REST·관리자 API 도구는 없다.

## 코드와 검증

- `runtime.py`: SDK 등록·응답·lifespan과 앱 연결
- `security.py`, `settings.py`: loopback/bearer 경계와 명시적 설정 복사
- `read_tools.py`, `write_tools.py`: 타입이 있는 도구 catalog
- `stdio.py`: 표준 SDK stdio/HTTP 메시지 중계
- `app/services/ticket_commands.py`: HTTP UI와 MCP가 공유하는 쓰기 command
- `tests/mcp/`: 실제 SDK HTTP/stdio, 캐시 재사용, CRUD, 응답 유실과 보안 경계

`python -m app.agent.mcp_server`는 이전 명령 호환용 stdio proxy다. `LTM_MCP_URL`과 `LTM_MCP_TOKEN`을
설정 화면에서 받은 값으로 지정해야 하며, 예전처럼 별도 mock 서버를 시작하지 않는다.
