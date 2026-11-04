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

65개 도구 모두 `inputSchema`와 `outputSchema`를 제공한다. 출력은 공통 성공/오류 envelope와
도구별 `data` 타입으로 정의하며, 조회 성공에는 `cache`, `partial`, `missing`도 포함된다.
실제 반환 JSON은 기존과 동일하게 `content`의 JSON 텍스트 및 `structuredContent`에 함께 제공한다.
SDK 검증 과정에서 기본값을 추가하거나 Jira 확장 필드를 제거하지 않는다.

출력 계약은 `schemas/`에서 티켓·워크스페이스·쓰기/인증별로 관리한다. 원본 Jira 필드나 선택 목록처럼
구조가 가변적인 부분은 확장 가능하게 두고, LTM이 제공하는 식별자·pagination·집계 필드는 명시한다.
서버 응답이 계약과 다르면 `output_schema_mismatch` 오류의 `operationResult`에 원래 결과를 보존한다.
이미 쓰기가 완료됐을 수 있으므로 이 오류는 `retryable=false`, `repeatWrite=false`이며 자동 재실행하지 않는다.

| 목적 | 도구 |
|---|---|
| 인증 상태·복구·로그인 시작 | `get_auth_status`, `authenticate` |
| 작성 스킬·콘텐츠 예제 | `get_content_guide` |
| JQL·페이지 | `search_issues` |
| 티켓 본문·댓글·이력·자식·Epic | `get_ticket`, `get_comments`, `get_comment_source`, `get_history`, `get_field_history`, `get_children`, `get_epic`, `get_ticket_context` |
| 원본 댓글·변경이력 페이지, 전체 하위 계층 | `get_evidence_page`, `get_descendants` |
| 통합검색·Confluence 검색/본문 | `search_workspace`, `search_confluence_documents`, `read_confluence_document` |
| 사람·선택지·워크로드 | `search_users`, `get_user`, `list_options`, `get_workload` |
| WBS·VIT·팀 워크로드·활동 | `get_dashboard`, `get_vit_detail`, `get_epic_progress`, `get_activity` |
| 부속 정보·첨부파일 내용 | `get_attachments`, `read_attachment`, `get_documents` |
| 생성 | `create_task`, `create_subtask`, `create_epic` |
| 일괄 생성 | `bulk_validate`, `bulk_create` |
| 일괄 필드 수정 | `bulk_update` |
| PMO_VIT 등록·해제 | `get_vit_overview`, `set_vit_tracking` |
| WBS 계획·인력 관리 | `get_workspace_config`, `create_wbs_task`, `update_wbs_task`, `delete_wbs_task`, `set_module_people`, `rename_people_module`, `delete_people_module` |
| LLM 연결 설정 | `get_llm_settings`, `create_llm_config`, `update_llm_config`, `delete_llm_config`, `list_llm_models`, `check_llm_connection`, `activate_llm_config` |
| 본문·필드 수정·전이 | `update_description`, `update_fields`, `transition_ticket` |
| Task/SubTask 변환·부모 변경 | `get_hierarchy_options`, `change_ticket_hierarchy` |
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

업무 도구 55개와 인증 도구 2개, 가이드 도구 1개, LLM 설정 도구 7개로 총 **65개**를 제공한다. `get_ticket`의 `descriptionHtml`은 열람용, `descriptionEditHtml`은
수정용 본문이다. 생성 시 `body.descriptionHtml`, 이후 본문 수정은 `update_description(html, ...)`,
댓글 작성·수정은 `add_comment`·`edit_comment(html, ...)`를 사용한다. 기존 UI와 동일한 Jira serializer를 거친다.
댓글 수정 전에는 `get_comment_source(key, comment_id)`의 `html`을 읽는다. `get_comments`의 표시 HTML은
수정 원본이 아니다. 원본 조회는 UI와 같은 최신 최대 1,000개 검색 범위이며 Jira가 더 작게 제한할 수 있다.
원본을 찾지 못하면 오류를 반환하므로 미리보기로 대신 덮어쓰지 않는다.
`update_fields`의 `labels`와 `components`에는 문자열 배열을 전달하며 `[]`는 해당 필드를 모두 비운다.

계층 변경은 공개 REST PUT이 아닌 기본 Jira 8.20.8의 네이티브 폼을 사용한다. `get_hierarchy_options`에서
필수 입력과 보안 수준·제거될 필드를 확인하고, 반환된 `previewHash`와 동일 입력으로 실행한다.
완료 상태·동일 프로젝트·타입·권한 제한을 지키며, 최종 타입/부모를 재조회하고 이전·새 관계 캐시를 갱신한다.
사내 전용 widget 등 알 수 없는 폼은 중단한다. 운영 Jira UI·SSO 검증은 아직 수행하지 않았다.

`bulk_update(items, client_mutation_id)`는 최대 10개의 `{key, fields}`를 순서대로 수정한다.
예: `{"key":"DL-123","fields":{"summary":"새 제목","labels":["etl"]}}`.
성공·실패는 원래 `index`와 `key`로 반환하며 실패해도 다른 티켓은 계속 처리한다. 롤백은 없다.
완료 티켓은 먼저 명시적으로 Reopened 전이를 수행해야 하며, Reporter 변경은 실제 Jira 권한이 필요하다.
필드별 권한·선택지는 `list_options(kind="edit_fields", key=...)`로 확인한다. `epic`은 Epic 소속만
바꾸며 Task/SubTask 변환이나 SubTask 부모 변경을 의미하지 않는다. 일괄 수정 UI는 추가하지 않는다.
재시도는 성공 행을 포함한 **전체 배열·순서·ID를 그대로** 보낸다. 성공 행은 다시 쓰지 않으며,
응답 유실은 `outcome_unknown`으로 보존해 새 ID로 중복 실행하지 않는다. 실패 행의 `action`이
권한 확인·별도 재오픈·로그인·동일 요청 재시도·결과 확인을 안내한다. 긴 오류는 preview로 표시한다.
도중에 Jira 계정이 바뀌면 미완료 행을 쓰지 않고 `restore_original_user`를 안내한다. 인증 복구가
실패해도 이미 확인된 성공 행과 실패 행은 유지하며 `authentication.jira`에 복구 오류를 별도로 표시한다.

### 작성 가이드라인 스킬

실행 중인 앱 버전에 맞는 세 스킬을 제공한다. `skills/`가 원본이며 외부 Agent의 로컬 설치는 필요 없다.
스킬을 언제 읽고 적용하는지는 연결한 클라이언트가 결정한다.

| 스킬 | 작업 |
|---|---|
| `ltm-task-authoring` | Task·Epic·SubTask 생성과 기존 본문 수정 |
| `ltm-comment-authoring` | 댓글 작성·수정과 transition 댓글 |
| `ltm-bulk-task-create` | Markdown을 사용하는 일괄 생성·검증·부분 실패 처리 |

MCP Skills 확장 `io.modelcontextprotocol/skills`의 `skills/list`와 `skills/get`은 메타데이터와
URI·SHA-256·바이트 크기의 파일 manifest를 제공한다. 내용은 `resources/read`로 읽는다.
예: `skill://ltm-task-authoring/SKILL.md`. 각 스킬의 `references/`에는 콘텐츠 형식, 인물 검색·링크,
쓰기 절차와 예제가 있다. 공통 자료는 물리적으로 한 번만 보관하고 각 스킬 하위 URI로 제공한다.
서버 시작 시 파일과 manifest를 함께 고정하므로 읽기 결과의 digest가 목록과 일치한다.

최신 HTTP 연결에서 Skills 확장을 탐색할 수 있다. 기존 stdio proxy와 확장 미지원 클라이언트는
`get_content_guide()`로 목록을 읽고,
`get_content_guide(skill="ltm-task-authoring")`로 본문을 읽는다. 참고 문서는 같은 `skill`에
`path="references/content-format.md"`처럼 상대경로를 전달한다. 이 경로는 게시된 문서만 선택하며
임의 로컬 파일이나 URL을 읽지 않는다. 가이드 조회는 Jira 로그인 없이 가능하지만 MCP의
Bearer token·loopback·Host/Origin 검사는 그대로 적용한다. Jira/Confluence 조회·쓰기는 기존 인증 경로를 따른다.

본문·댓글·transition 댓글은 HTML, bulk `description`은 Markdown이다. 가이드는 실제 지원하는
멘션·Jira/Confluence/외부 URL·표·체크리스트·코드·이미지 예제와 본문 전용 영역 구분을 설명한다.
스킬은 사용자 요청 범위나 쓰기 권한을 확대하지 않고 기존 API 계약을 따른다.

### 동명이인과 최근 사용자 근거

`search_users(query, limit=8)`은 후보 순서를 유지하며 각 후보에 `matchContext`를 붙인다.
`configured`는 로컬 people config 등록 여부, `sameModule`은 현재 사용자와 확인된 공통 모듈 여부다.
`recentLookupAt`, `recentSelectionAt`, `recentMentionAt`은 각각 명시적 MCP 조회, UI 사용자 선택,
UI 멘션 삽입 시각(Unix 초)이다. 멘션 삽입은 게시 완료를 뜻하지 않는다. 검색 결과에 노출되거나
뱃지가 자동 렌더링되는 것만으로 이력을 기록하지 않는다.

이력은 Jira 주소·환경·확인된 사용자별로 분리해 7일/200건 유지한다. 이전 브라우저의 계정 구분 없는
localStorage 기록은 자동 이관하지 않으며 새 활동부터 수집한다. 설정·인증·이력 확인이 불가능하면
값을 `null`로 남기고 `contextAvailable`로 가용성을 알린다. 이력 기록 실패는 편집을 막지 않는다.
조회 중 계정·서버가 바뀌면 모든 개인 근거를 제외하고 `contextChanged=true`로 재검색을 안내한다.

`limit`은 1~50이다. `mayHaveMore=true`면 제한에 닿았고, 짧은 응답의 `null`은 Jira가 전체 건수를
주지 않아 완전성을 알 수 없다는 뜻이다. `coverage=bounded`이므로 이름 후보가 하나뿐이라는 사실만으로
전역에서 유일한 사람이라고 단정하지 않는다. 필요하면 회사·성명·실제 username으로 좁힌다.
최근성·같은 모듈·등록 여부는 판단 보조 정보이며 신원 확정이나 권한의 근거가 아니다.

### 범용 조회로 기간·대상별 업무 분석

기간별 보고서나 특정 업무의 집계 전용 도구는 제공하지 않는다. Codex 등 연결된 Agent가 범용 조회를
조합해 일간·주간·월간, 개인·모듈·Epic·Task별 질문에 답하고 근거·중복·제외·미확인을 판단한다.

- `search_issues`: 날짜·담당자·Component 등 JQL 조건으로 전체 후보를 조회한다. `updated`는 완료일이
  아니며, 현재 완료 목록과 과거 완료 이벤트도 다른 개념이다. 기간·timezone·완료 기준을 먼저 정한다.
- `get_descendants`: Epic→Task→SubTask 또는 Task→SubTask를 펼친다. 부모의 `updated`가 오래돼도
  하위 티켓을 빠뜨리지 않는다. 원본 필드와 parent/depth/Epic 상속 관계를 반환하며 사업 진척률은 계산하지 않는다.
- `get_evidence_page`: `comments` 또는 `history`의 원본 ID·작성자·시각·본문·변경값을 페이지로 읽는다.
  모든 `nextStartAt`을 따라가고 ID로 중복을 제거한다. 부모와 각 자손의 기록을 읽어 티켓별 출처를 유지한다.
- `get_ticket`: 제목만으로 확정하기 어려운 업무의 본문을 확인한다. 댓글의 후속 변경·취소도 함께 검토한다.

기존 `get_comments`는 최대 20개를 보유하는 UI 캐시, `get_history`는 표시용 요약 타임라인이다.
전체 댓글·이력 분석에는 `get_evidence_page`를 사용한다. Jira 8.20의 expanded changelog가
잘려 있으면 `sourceComplete=false`를 반환한다. 이때 `hasMore=false`도 전체 이력 확인을 뜻하지 않는다.
실시간 offset/계층 조회는 변경 중 불변 snapshot을 보장하지 않으므로 total·ID 변화를 확인한다.
권한상 숨겨진 자손은 알 수 없으며 확인된 실패·누락은 별도로 반환한다. 미확인을 0건으로 집계하지 않는다.

새 두 도구의 데이터 페이지는 최대 8KiB다. 댓글·이력 한 건이 너무 크면 `recordJson` 조각과
`nextRecordOffset`을 반환한다. 같은 `sourceIndex`에서 `max_results=1`, `record_offset`으로 이어 읽고,
같은 `recordSha256`인지 확인한 뒤 조각을 합쳐 JSON으로 해석한다. 원본 변경으로 hash가 달라지면
해당 기록을 처음부터 다시 읽는다. 하위 계층의 누락 목록은 `nextMissingStartAt`을 별도로 따라간다.
하위 목록은 일반 갱신 간격을 사용하며 완료 티켓 원본의 긴 TTL 때문에 갱신이 지연되지 않는다.
Epic 탐색은 설정 프로젝트로 제한한 JQL을 사용하며 범위 밖 티켓을 가져오는 Agile fallback은 하지 않는다.

모듈의 Component 기준, 인력 소속 기준, WBS 계획 매핑은 서로 다르다. WBS의 SP 비율과 VIT·하위
티켓 개수 비율도 같은 지표가 아니다. 함수 설명은 이 범위와 한계, pagination, 응답 필드를 명시한다.

### 인증 처리

모든 업무 도구는 서비스 인증 확인·무음 갱신을 공통 경로로 처리한다. 캐시만으로 가능한 조회에는
추가 인증 요청을 붙이지 않으며, 실제 인증 거절은 공통 복구 경로로 전달한다. 최근 확인은 20초 재사용하고
동시 Jira 확인은 서버 keepalive·UI와 같은 실행 결과를 공유한다. 인증이 요청 도중 만료되면 조회는
1회 재시도하고, 명확히 인증 거절된 Jira 쓰기는 **같은 내용과 mutation ID**로 1회 재시도한다.
응답 유실·timeout·`outcome_unknown`은 자동 쓰기 반복 대상이 아니다. 일괄 생성은 동일 원본 batch의
성공한 항목을 receipt로 재사용한다. 통합검색은 인증 가능한 서비스의 결과를 보존한다.

- `get_auth_status(service="jira"|"confluence")`: 상태 확인과 무음 복구. 로그인 창은 열지 않는다.
- `authenticate(service="jira"|"confluence", interactive=false)`: 무음 복구를 명시적으로 다시 시도한다.
- 최초 로그인 또는 사용자 입력이 필요하면 업무 응답의 `error.nextAction`에
  `authenticate(service=..., interactive=true)`를 제공한다. 호출하면 LTM 로그인 화면을 시작하거나
  진행 중인 화면을 공유하고 즉시 반환한다. UI·트레이·MCP가 동시에 요청해도 서비스별 창은 중복되지 않는다.
- 사용자가 해당 화면에서 인증을 마친 뒤 `get_auth_status`의 `authenticated:true`를 확인하고
  원래 도구를 같은 인자로 재호출한다. MCP 토큰·Jira 쿠키를 다시 가져오거나 세션 파일을 다룰 필요가 없다.
- `pending`은 확인/로그인 진행 중, `upstream_unavailable`은 연결 문제다. 이 상태만으로 로그인을 열지 않는다.

MCP 연결 자체의 Bearer 토큰은 Jira/Confluence 인증과 별개다. MCP 토큰을 재발급한 경우에만
LTM 설정에서 인증 포함 연결 설정을 다시 복사한다. 사용자 입력이 필요한 SSO를 자동 우회하지 않는다.

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
마지막 WBS Task를 삭제해도 빈 계획으로 유지할 수 있다. 이때 dashboard의 일정은 `null`, 집계는 0이며
UI는 첫 Task 등록 안내를 표시한다. Jira Epic이나 티켓은 함께 삭제되지 않는다.

Confluence 검색은 `search.confluence.spaces` 범위에서 페이지를 반환하고, 본문 읽기는 숫자 ID 또는
설정된 Confluence 호스트의 `/pages/{id}` 또는 `?pageId=` URL을 받는다. `/display/SPACE/title` 형식은
먼저 검색으로 숫자 ID를 확인한다. 검색은 `nextStartAt`, 긴 본문은 `nextOffset`을 따라 끝까지 읽을 수 있다.
문서 제목·space·version과 전체 길이를 함께 반환하며, excerpt만으로 문서를 읽었다고 표현하지 않는다.
UI 통합검색·내장 Agent·MCP는 환경/Confluence 서버/사용자/요청 projection으로 구분된 같은 캐시를 사용한다.
인증 실패나 불완전 응답을 빈 문서로 캐싱하지 않는다. Confluence 문서 생성·수정은 현재 LTM에 없는 기능이므로 제공하지 않는다.

WBS와 사용자 활동에는 UI와 같은 manager 검사, 팀 워크로드에는 같은 module/people 범위가 적용된다.
Bitbucket 검색은 현재 LTM에서도 예정 기능의 mock placeholder이므로 MCP 도구에 포함하지 않는다.
인증 쿠키, 비밀값 원문 조회, 임의 REST 호출이나 파일 접근은 노출하지 않는다.

### LLM 연결 설정

LLM 설정 도구는 **설정 → AI 연결 설정**과 같은 named config·키 저장소·검증 지문을 사용한다.
Jira 로그인 없이 동작하며, MCP로 Jira를 사용하는 데 LLM 연결 설정이 필요한 것은 아니다.

1. `get_llm_settings`로 후보와 활성 ID, 환경변수 우선 적용 여부를 확인한다.
2. `create_llm_config(name, provider)`로 후보를 만들고 `update_llm_config(config_id, changes)`로
   모델·Azure 배포명·URL·키를 저장한다. provider는 생성 후 고정이며 새 후보는 자동 활성화하지 않는다.
3. `check_llm_connection(config_id, stage="auth")`로 인증을 확인한다. `list_llm_models`는
   모델 목록만 조회하며 검증 완료로 표시하거나 활성화하지 않는다.
4. 모델을 지정한 뒤 `check_llm_connection(..., stage="models")`를 명시적으로 호출한다.
   실제 chat·embedding·capability 요청이 발생할 수 있고 과금될 수 있다. `passed`와 진단 결과를 확인한다.
5. 두 검증을 모두 통과한 현재 지문만 `activate_llm_config`로 적용한다. 키·헤더·URL·모델을
   바꾸면 필요한 검증을 다시 수행한다. 사용 중인 설정은 삭제할 수 없으며 다른 검증된 설정을 먼저 적용한다.

설정 목록은 기본 3개(최대 10개), 모델 목록은 `kind="chat"|"simple"|"embed"`별 기본 25개(최대 50개)씩
반환한다. `hasMore`, `nextStartAt`으로 이어 읽고 `total`은 해당 목록의 전체 개수다. 데이터 응답은
8KiB 이내이며 모델·설정 ID는 자르지 않는다. 단일 항목이 너무 크면 명시적인 오류와 UI 확인 안내를 준다.
긴 연결 진단은 `truncated:true`, `originalBytes`, `preview`로 제한된 미리보기임을 표시한다.

`changes.secrets`는 UI와 같은 `aoaiEndpoint`, `aoaiApiKey`, `openaiApiKey`, `compatBaseUrl`,
`compatApiKey`, `compatHeaders`, `simpleBaseUrl/ApiKey/Headers`, `embeddingBaseUrl/ApiKey/Headers`를 받는다.
헤더는 문자열 값의 JSON 객체다. 생략한 값은 유지하고 빈 문자열은 저장된 값을 지운다.
URL에 인증정보·query·fragment를 넣지 않는다. 키·헤더는 쓰기 전용이며 조회에는 `configured` 또는
빈 문자열만 제공한다. 환경변수 자체는 수정하지 않으며 환경변수 우선순위는 유지한다.

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
이름 변경은 `update_fields`의 `body.summary`로 수행한다. 편집·전이 준비 조회가 실패한 경우에는
권한 없음이나 빈 선택 목록으로 숨기지 않는다. 쓰기 전 실패가 확인되면 `writeExecuted:false`와
`retryWithSameMutationId:true`를 반환하며, 복구 후 같은 입력·mutation ID로 재시도할 수 있다.
일괄 생성의 검증/부분 실패도 최상위 `isError`·`ok:false`로 반환한다. 일부 성공이면 `partial:true`이며
`data`의 `created`·`failed`·`errors`와 원래 항목 index를 보존하므로 성공 결과까지 실패로 간주하지 않는다.
쓰기 전 상태와 권한은 만료된 display cache로 판단하지 않는다. 아직 유효한 캐시는 재사용하지만
확인 중 연결·인증 오류가 나면 쓰기를 차단한다. 인증 필요·권한 없음·설정 충돌은 서로 다른 error code로 제공한다.

완료 필드 수정 거절은 기존 `code=conflict`·409와 함께 `reason=ticket_completed`를,
편집 metadata에 없는 필드는 `code=permission_denied`·403과 `reason=field_not_editable`을 반환한다.
`nextAction`은 해당 티켓의 전이/편집 필드를 조회하는 안내다. 자동 재열기 허용이나 권한 부여가 아니다.
작업별 상태·권한과 Jira 화면의 계층 변경 기능은
`get_content_guide(skill="ltm-task-authoring", path="references/ticket-actions.md")`에서 확인한다.

MCP는 UI와 별도 작업 용량을 사용한다. 업무 도구는 동시 8개, 인증·가이드는 별도 2개이며,
초과 호출은 대기열에 쌓지 않고 `rate_limited`와 `retryAfterSeconds`를 반환한다.
`executed:false`는 도구 실행 전 거절된 경우에만 제공한다. Jira 내부 혼잡은 일부 단계가 이미
실행됐을 수 있으므로 이 표시가 없다. 재시도 시 쓰기의 mutation ID와 내용을 그대로 유지한다.
클라이언트 연결이 끊겨도 이미 시작한 쓰기는 실제 종료까지 용량을 점유하며 자동 재전송하지 않는다.

첨부 업로드는 최대 10MiB의 base64 바이트를 받는다. `read_attachment`는 티켓에 속한 ID를 검증한 후
실제 파일 바이트를 base64로 반환하며, `nextOffset`을 따라 최대 10MiB 파일을 읽는다. 로컬 파일 경로는 받지 않는다.

## 코드와 검증

Dev Tools(`#/devtools`)의 **계층 변경 원클릭 테스트**는 현재 연결된 Jira에 테스트용 Task 3개를
생성한 뒤 Task → SubTask → 다른 부모로 이동 → Task 복원을 실제 MCP 도구로 실행한다.
실행 환경·프로젝트와 타입을 확인한 뒤 버튼을 누른다. 테스트 티켓은 결과 확인을 위해 남으며
자동 삭제하지 않는다. 운영 Jira에서도 현재 로그인 계정의 생성·편집·이동 권한이 그대로 적용된다.

- `GET /api/dev/hierarchy-test/options`: 현재 환경·프로젝트와 실제 타입 이름
- `POST /api/dev/hierarchy-test`: `{ "runId": "고유 UUID", "expectedScope": "options의 scope", "taskType": "Task", "subtaskType": "Sub-task" }`
- `GET /api/dev/hierarchy-test/{runId}`: 단계별 진행·오류와 생성된 Jira 티켓 링크

POST는 실행을 시작하고 상태를 반환한다. 화면을 닫아도 서버에서 진행하며, 같은 runId와 입력으로
재개하면 저장된 단계·mutation receipt를 사용한다. 필수 입력·권한·완료 상태·결과 불명확 문제가
생기면 후속 단계를 중단한다. 타입·상태 ID나 제거될 필드를 임의로 선택하지 않는다.
일반 mock에는 Jira의 네이티브 변환 화면이 없으므로 해당 단계의 미지원 결과를 그대로 표시한다.
자동 회귀 테스트의 폼 fixture 통과와 운영 Jira 검증은 별개다.

- `runtime.py`: SDK 등록·응답·lifespan과 앱 연결
- `authentication.py`: 공통 인증 확인·복구와 인증 도구
- `evidence_tools.py`, `app/jira/evidence_service.py`: 크기를 제한한 원본 댓글·이력 페이지
- `hierarchy_tools.py`, `app/jira/hierarchy_service.py`: 범용 하위 계층 탐색과 실패 목록
- `security.py`, `settings.py`: loopback/bearer 경계와 명시적 설정 복사
- `read_tools.py`, `workspace_tools.py`, `write_tools.py`, `vit_tools.py`, `admin_tools.py`: 타입이 있는 업무 도구 catalog
- `llm_tools.py`, `app/agent/connection_settings.py`: UI와 공유하는 named LLM 설정·검증·활성화
- `stdio.py`: 표준 SDK stdio/HTTP 메시지 중계
- `app/services/ticket_commands.py`: HTTP UI와 MCP가 공유하는 쓰기 command
- `app/services/workspace_config.py`: WBS·인력 UI와 MCP가 공유하는 설정 command
- `tests/mcp/`: 실제 SDK HTTP/stdio, 캐시 재사용, CRUD, 응답 유실과 보안 경계

`python -m app.agent.mcp_server`는 이전 명령 호환용 stdio proxy다. `LTM_MCP_URL`과 `LTM_MCP_TOKEN`을
설정 화면에서 받은 값으로 지정해야 하며, 예전처럼 별도 mock 서버를 시작하지 않는다.

실제 Codex와 HTTP MCP를 연결한 조회 품질·시간·토큰 검증은
[`Codex MCP 평가 안내`](../../tools/codex_mcp_eval/README.md)의 별도 수동 배터리를 사용한다.
기존 조회 질문과 exact JQL·통합검색·부분 실패 계약을 8개 사례로 검증하며, 자동 판정과 직접 정성평가를 구분한다.
