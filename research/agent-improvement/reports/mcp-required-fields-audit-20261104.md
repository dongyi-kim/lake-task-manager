# MCP 필수 입력·선택지 계약 점검 — 2026-11-04

기준은 앱 main `2fdb8feb`의 **55개 도구**와 별도 [LLM 설정 PR #124](https://github.com/dongyi-kim/lake-task-manager/pull/124)의 **7개 도구**다. 아래에는 정적 스키마·설명·실제 validation 경로를 대조한 결과를 기록한다. 운영 Jira의 특정 오류 로그를 수집한 결과는 아니다. SDK 실행은 격리된 jira820 fixture이며 실제 Jira·SSO·LLM을 호출하지 않는다.

## 발견한 문제와 보강

1. **알 수 없는 필드가 묵시적으로 버려졌다.** MCP 생성·Epic·필드 수정·전이 입력이 UI 모델의 `extra=ignore`를 상속했고, SDK 최상위 인자 모델도 알 수 없는 인자를 버렸다. `timeSpent`를 보냈는데 실제 인자인 `days/hours/minutes`가 비어 있거나, `name/title`로 보낸 리네임이 반영되지 않을 수 있었다. MCP의 body와 최상위 인자를 모두 `extra=forbid`로 바꾸고 게시된 inputSchema에도 반영한다. `dry_run` 같은 미지원 인자는 쓰기 전에 거절한다. 리네임은 `body.summary`임을 명시하며 UI 입력 계약은 유지한다. [입력 정의](../../../app/local_mcp/write_tools.py), [SDK 등록](../../../app/local_mcp/runtime.py)
2. **전이 metadata와 MCP 인자의 연결이 부족했다.** 필수 `worklog`는 `body.days`, `body.hours`, `body.minutes` 중 양수인 실제 소요시간으로 전달해야 한다. `fields[].inputFields`와 도구/스키마/작성 가이드에 대응을 표시한다. 완료 댓글 조건도 `commentRequired`로 제공한다. 시간을 모르면 사용자에게 확인하며 임의 값으로 필수값을 채우지 않는다. [전이 회귀](../../../tests/mcp/test_transitions.py)
3. **생성 시 Jira별 필수 항목을 미리 조회할 수 없었다.** `list_options(kind="create_fields", issue_type=...)`를 추가한다. 모든 필드 페이지에 required/default 여부, 관리 필드, 정확한 입력 경로를 제공하고 `field_id` 선택 시 기본값과 허용 선택지를 별도로 페이지 조회한다. 미지원 필수 custom field는 `unsupportedRequired`로 명시한다. [구현](../../../app/local_mcp/create_options.py), [회귀](../../../tests/mcp/test_create_options.py)
4. **metadata 조회 실패가 빈 성공으로 보였다.** MCP 전이·편집·생성 metadata는 strict 조회로 실패 원인을 유지한다. 생성 metadata의 기존 빈/다른 타입 fallback 캐시는 신뢰하지 않고, 정확한 project/type을 확인한 strict 캐시만 재사용한다. 권한·인증·네트워크 실패를 필수 항목 없음으로 해석하지 않는다. [옵션 조회](../../../app/local_mcp/read_tools.py)
5. **bulk 항목 스키마가 비어 있었다.** `bulk_create`에 알려진 필드 타입과 summary/type, 모드별 epic/parent 조건을 표시한다. `bulk_validate`는 잘못된 초안도 받아야 하므로 required/type 제약으로 미리 차단하지 않고 기존 index/field/error/warning 결과를 유지한다. [SDK 회귀](../../../tests/mcp/test_required_inputs.py)
6. **기타 입력 의미를 보강했다.** 변경별 mutation ID 형식·재시도 규칙, 사람 ID와 display name 구분, 날짜 형식, 링크 방향 enum, WBS 날짜/가중치 조건을 설명한다. 기존 null/empty/전체 교체 의미는 유지한다.

## 전체 카탈로그 대조

`정적`은 정의·스키마·runtime validation을 대조했다는 뜻이며 운영 실행 성공을 뜻하지 않는다. `SDK`는 이번 변경의 관련 실제 SDK/격리 fixture 시나리오가 존재한다는 뜻이다. 실행 수치와 최종 CI는 각 PR 검증 기록을 따른다.

| 도구 | 필수·조건부 입력 / 조회 순서 | 확인 근거 |
|---|---|---|
| `get_auth_status`, `authenticate` | 필수 인자 없음. service=jira/confluence, interactive=true는 사용자 로그인 창 시작. 완료는 authenticated로 확인 | 정적, 기존 `test_authentication.py` |
| `get_content_guide` | 필수 인자 없음. skill 선택 후 manifest에 있는 path만 요청 | 정적, 기존 guide/resource 회귀 |
| `search_issues` | jql 필수. 같은 JQL과 snapshotId로 nextStartAt 순회; 한 페이지 1~100 | 정적, 기존 검색·출력 회귀 |
| `get_ticket`, `get_children`, `get_epic`, `get_ticket_context`, `get_attachments`, `get_documents`, `get_epic_progress` | 실제 scope 내 key 필수. 본문 수정은 descriptionEditHtml 사용, 관계/집계는 partial 확인 | 정적, SDK 쓰기 후 상세·자식·Epic·첨부·문서 재조회 |
| `get_comments`, `get_history`, `get_field_history` | key 필수. limit 1~1000이나 원본 cache/feed 한도와 완전성은 별도; 원본 전량은 evidence 페이지 | 정적, 기존 evidence/output 회귀 |
| `get_comment_source` | key와 실제 comment_id 필수. get_comments/get_evidence_page에서 ID 확인 | 정적, 기존 comment_source 회귀 |
| `get_evidence_page` | key 필수. kind 선택, nextStartAt과 큰 record의 nextRecordOffset을 구분 | 정적, 기존 evidence_payload 회귀 |
| `get_descendants` | root_key 필수. issue와 missing ledger를 각각 끝까지 페이지 조회 | 정적, 기존 hierarchy 회귀 |
| `search_workspace` | query 필수. sources는 jira/confluence; limit은 source별 1~100 | 정적, 기존 workspace 회귀 |
| `search_confluence_documents` | 필수 인자 없음. optional modified_after=YYYY-MM-DD, nextStartAt 순회 | 정적, 기존 workspace 회귀 |
| `read_confluence_document` | page 필수: 실제 숫자 ID 또는 설정 host의 지원 URL. offset/max_chars는 문자 단위 | 정적, 기존 workspace 회귀 |
| `read_attachment` | key와 실제 attachment_id 필수. offset/max_bytes는 디코딩한 바이트 단위 | 정적, SDK 첨부·소유권 회귀 |
| `search_users`, `get_user` | search query 생략 가능, get_user의 빈 user_id는 본인. 동명이인/범위는 matchContext와 coverage로 판단 | 정적, 기존 person_context 회귀 |
| `list_options` | kind 필수. child_types/edit_fields/transitions는 key; create_fields는 issue_type. 값은 실제 목록을 사용 | SDK: 필수 시간·댓글, strict 실패, 필드/선택지 pagination |
| `get_workload`, `get_activity` | 확인한 user_id 필수. workload 기간/권한과 activity manager 권한은 설명에 명시 | 정적, 기존 workspace/output 회귀 |
| `get_dashboard`, `get_vit_detail`, `get_vit_overview` | dashboard kind 필수, detail key 필수, overview 인자 없음. module 적용·권한·현재 집계 의미 명시 | 정적, 기존 WBS/VIT 회귀 |
| `create_task`, `create_subtask` | body.type/summary/clientMutationId 필수. SubTask parent_key 필수; Task epic_key optional. create_fields로 조건부 필수값 확인 | SDK 생성·재시도·자식 관계, 잘못된 필드 0회 쓰기 |
| `create_epic` | body.summary/clientMutationId 필수. epicName 생략 시 summary, taskKeys는 생성 후 별도 링크 결과 | SDK Epic 생성, 잘못된 필드 거절 |
| `bulk_validate`, `bulk_create` | mode/items 필수, create는 client_mutation_id도 필수. task epic 키는 null도 명시, subtask parent는 실제 기존 key | SDK indexed errors/warnings·부분 성공·같은 배치 재시도 |
| `update_fields`, `update_description` | key와 mutation ID. 리네임 body.summary, 본문 html은 전체 교체. edit_fields 확인, Done은 재열기 필요 | SDK 리네임·본문·캐시 재조회·실패/재시도 |
| `transition_ticket` | key, body.id/clientMutationId 필수. 현재 transitions에서 필수 시간·담당자·resolution·댓글·미지원 항목 확인 | SDK 필수 worklog/댓글, 재열기, 동일 ID 재시도 |
| `add_comment`, `edit_comment`, `delete_comment` | key와 mutation ID 필수. add/edit는 비어 있지 않은 HTML; edit/delete는 실제 comment_id | SDK 작성·수정·삭제와 재조회 |
| `add_issue_link`, `delete_issue_link` | 양쪽 key와 mutation ID; 삭제는 실제 link_id. type_name은 link_types에서 조회, direction은 inward/outward | SDK 양쪽 관계·소유권·잘못된 방향 거절 |
| `add_document`, `delete_document` | key와 mutation ID; 등록 HTTP(S) url, 삭제 get_documents의 link_id | SDK 문서 링크 등록·삭제 |
| `add_attachment`, `delete_attachment`, `delete_ticket` | key와 mutation ID. 업로드 filename/base64_data, 첨부 삭제 attachment_id. 파일 경로·임의 REST ID 불가 | SDK 업로드·소유권·삭제·티켓 검색에서 제거 |
| `set_vit_tracking` | key/tracked/client_mutation_id 필수; labels 보존, Done 편집 제한 | 정적, SDK 기존 bulk/VIT 회귀 |
| `get_workspace_config` | 인자 없음, manager 필요; 실제 revision을 다음 변경에 사용 | 정적, 기존 admin 회귀 |
| `create_wbs_task`, `update_wbs_task`, `delete_wbs_task` | expected_revision 필수. 생성 task의 모든 필드, 변경/삭제 task_id 필수; 날짜·Epic 가중치 관계 조건 명시 | 정적, 기존 admin 회귀 |
| `set_module_people`, `rename_people_module`, `delete_people_module` | module/expected_revision 필수. set은 user_ids 전체 교체, rename은 new_name; 실제 user ID 사용 | 정적, 기존 admin 회귀 |

## 별도 LLM 연결 카탈로그 7개

| 도구 | 필수 입력 / 조건 | 근거 |
|---|---|---|
| `get_llm_settings` | 필수 인자 없음; 페이지 continuation. 키·헤더는 설정 여부만 조회 | PR #124 SDK |
| `create_llm_config` | name/provider 필수; 후보 생성과 활성화는 별개 | PR #124 SDK |
| `update_llm_config` | config_id/changes 필수. 알려진 필드만, provider 변경 불가, 키/헤더 쓰기 전용 | PR #124 SDK |
| `delete_llm_config` | config_id 필수, 활성 연결 삭제 금지 | PR #124 SDK |
| `list_llm_models` | config_id 필수; kind별 모델 페이지, 정확한 ID 유지 | PR #124 SDK, 1,000 모델 pagination |
| `check_llm_connection` | config_id 필수; auth/models 단계. models는 실제 요청·비용 가능, 사용자 요청 시만 실행 | PR #124 fake/stub SDK, 실제 LLM 호출 없음 |
| `activate_llm_config` | config_id 필수; 현재 설정의 인증·모델 검증 지문이 일치해야 함 | PR #123/124 SDK |

## 적용 범위와 한계

- `Time Spent is required`는 해당 전이의 실제 작업시간을 채워야 한다는 의미다. 이번 점검으로 무시되는 잘못된 인자와 불충분한 입력 안내를 보완하지만, 사용자에게 알려진 실제 소요시간을 자동으로 만들어 내지 않는다.
- Jira의 workflow validator가 metadata에 선언하지 않은 조건을 추가할 수 있다. 서버가 반환한 필드 오류는 확인해야 하며, 정적 schema만으로 모든 Jira 설정을 미리 표현했다고 주장하지 않는다.
- 생성/전이의 미지원 필수 custom field는 명시적으로 알린다. 이 MCP는 임의 REST/custom-field 쓰기 우회 도구가 아니다.
- create_fields는 전체 필드와 선택지를 별도로 페이지 조회한다. 매 응답 data는 6KiB 이내이며, 큰 단일 ID/기본값/선택지는 413으로 알리고 자르거나 몰래 제외하지 않는다. 필드 전체 coverage는 모든 field 페이지를 확인해야 한다.
- 실패한 preflight가 실제 쓰기 결과 불명으로 잘못 남는 문제와, 실제 POST/PUT 응답 유실의 중복 방지는 별도 mutation 회귀에서 구분한다.
