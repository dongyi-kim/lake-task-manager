# Jira 장애 복구 하드닝 QA 보고서

- 작성일: 2026-09-03
- 대상 브랜치: `bugfix/fault-recovery-hardening`
- 대상 이슈: GitHub #68–#78
- 환경: Jira mock/fixture 기반 자동 회귀
- 판정 원칙: 인증·네트워크·부분 응답을 정상적인 빈 결과로 확정하거나 캐싱하지 않는다. 성공한 조각은 유지하고 실패한 조각만 제한적으로 재시도한다.

## 결론

읽기 장애는 화면 전체 실패 대신 요청 단위의 부분 성공·재시도로 바뀌었다. My Tasks,
인력 워크로드, 티켓 다이어로그, WBS, VIT는 이미 받은 데이터를 유지하며 실패한 leaf,
사람, bucket 또는 패널만 다시 요청한다. Jira 권한 부족은 가능한 범위에서 조용히 제외하고,
인증 만료와 그 밖의 장애는 서로 구분한다.

유휴 복귀 인증도 보강했다. 기존 주기 keepalive는 Jira를 의도적으로 제외했기 때문에 Jira
세션을 자동 갱신하지 않았다. 이제 5분 이상 유휴 후 focus, visibility, pointer 또는 keyboard
활동이 들어오면 UI와 독립된 background probe를 수행하고, 실제 만료로 확인된 경우에만
Jira SSO 무음 갱신을 시도한다. 단순 네트워크 지연은 로그인 만료로 취급하지 않는다. 성공한
probe에서 갱신된 Jira rolling cookie는 15분 간격으로만 저장하며, 동시에 끝난 이전 provider가
새 로그인 파일을 덮지 못하도록 provider generation과 파일 revision을 함께 확인한다.

prod Resolve 화면에서 댓글 에디터가 사라진 원인도 함께 제거했다. Jira DC가 완료 transition
metadata에서 `comment`를 생략하더라도 Done/Resolve는 공용 rich editor를 표시하고, 서버가 최신
transition category와 허용 필드를 다시 확인한 뒤 완료 댓글을 필수로 전송한다. 전환 초안과
이미지는 일반 댓글 초안과 분리해 저장하므로 인증 실패나 renderer 재시작에도 복구할 수 있다.

쓰기 응답 유실은 티켓 생성, 하위 티켓 생성, Epic 생성, 새 댓글, 첨부 업로드 및 상태 전환에 durable
mutation receipt를 적용했다. 같은 `clientMutationId`를 재사용하는 재시도는 Jira 반영 여부를
먼저 확인하므로 응답만 유실된 경우 같은 객체를 다시 만들지 않는다. 다만 #73의 범위인
모든 쓰기 가운데 일반 필드 수정·삭제, 링크와 bulk/Agent 쓰기는 아직 같은 수준의
receipt 보호를 받지 않는다. 따라서 #73은 이 보고서 기준 **부분 완료**다.

## 이슈별 구현·검증 현황

| 이슈 | 재현 조건과 기존 위험 | 적용한 동작 | 직접 회귀 근거 | 상태 |
|---|---|---|---|---|
| #68 | ancestors 조회가 인증·전송 실패했는데 빈 계보로 캐싱됨 | lineage 원본을 strict cache read로 읽고 인증·전송 실패를 상위로 전달한다. 403은 best effort로 제외하되 빈 성공 캐시는 만들지 않는다. | `test_transient_ancestor_failure_is_not_cached_as_empty`, `test_expired_issue_fallback_cannot_be_promoted_into_fresh_ancestry`, `test_permission_denied_ancestor_is_best_effort_but_not_cached` | 구현·집중 회귀 있음 |
| #69 | Epic 이름이 티켓 번호로 바뀌거나 이미 아는 이름에도 로딩 표시가 재등장함 | authoritative `epicmeta`를 12시간 유지하고, key-only fallback은 성공 캐시에 넣지 않는다. warm 구조에는 읽는 시점의 최신 Epic metadata를 투영하며 정확한 key만 무효화한다. TicketDialog는 lineage가 지연·403이어도 이미 받은 `v.epicName`을 유지한다. | `test_epic_metadata_uses_long_ttl_and_ticket_invalidation_evicts_it`, `test_deferred_task_model_reuses_fresh_epic_metadata_without_loading`, `test_epic_metadata_never_caches_key_only_fallback_and_retries`, `test_ticket_epic_title_keeps_cached_view_name_while_lineage_is_delayed_or_denied` | 구현·집중 회귀 있음 |
| #70 | Workload API가 HTTP 200 안에 `error: true`를 반환하면 정상 0건처럼 남거나 retry되지 않음 | 응답 본문의 `errorKind`, status, message를 보존하고 예외와 동일한 bounded retry 경로로 보낸다. 실패 응답 memo key는 즉시 제거한다. | `test_http_200_error_bundle_uses_the_same_bounded_person_retry_path`, `test_search_failure_is_not_cached_as_zero`, `test_session_expired_propagates_not_zero` | 구현·집중 회귀 있음 |
| #71 | 사람 상세 bucket, SubTask 부모 Epic 또는 activity source 일부가 실패했는데 전체가 정상/없음으로 표시됨 | open/in-progress/done bucket과 Jira/Confluence activity source를 독립 상태·캐시로 관리한다. 성공 티켓/집계는 계속 표시하고 불완전 aggregate는 캐싱하지 않으며, 실패한 사람·bucket·source만 재시도한다. 권한 제한 Epic은 `조회 제외`, 일시 실패는 `Epic 미확인`으로 표시한다. | `test_detail_buckets_have_independent_state_and_retry_only_the_failed_bucket`, `test_retryable_parent_gap_returns_partial_person_bundle_without_caching`, `test_activity_partial_source_is_not_cached_and_success_source_is_reused`, `test_partial_parent_resolution_keeps_counts_and_rows_visible_during_targeted_retry` | 구현·집중 회귀 있음 |
| #72 | My Tasks 한 상태축/leaf 실패가 전체 snapshot을 비우거나 완료 처리함 | OR leaf 결과를 도착 순서대로 append/dedup/sort/statistics하고, leaf 실패를 격리한다. 성공 leaf는 캐시하며 partial 결과는 authoritative `mt` cache로 승격하지 않는다. 현재 필터 세대만 화면에 반영하고 실패 leaf만 재시도한다. | `test_leaf_failures_are_isolated_classified_and_later_leaves_still_arrive`, `test_retry_after_partial_failure_only_refetches_the_uncached_leaf`, `test_partial_task_stream_keeps_successes_and_does_not_publish_partial_mt_cache`, frontend `test_failed_leaf_axis_is_not_marked_done_or_rendered_as_empty` | 구현·집중 회귀 있음 |
| #73 | Jira가 write를 반영한 뒤 응답이 끊기면 사용자의 재시도가 중복 생성·댓글·첨부·전환을 만들 수 있음 | client가 동일 mutation ID와 exact payload를 보존하고, 서버가 pending/committed/success receipt를 8일 유지한다. timeout 뒤에도 원 Playwright write가 실행 중이면 완료 handle을 추적해 재POST하지 않는다. fresh reconciliation은 원 작성자까지 확인하며 과거/복수 동일 후보는 성공으로 오인하지 않는다. | `test_comment_response_loss_recovers_same_comment_without_second_post`, delayed-owner completion tests, same-actor pre-existing comment/attachment/issue tests, `test_transition_response_loss_reconciles_status_without_duplicate_transition`, frontend write recovery contracts | **부분 완료** — 일반 update/delete/link·bulk/Agent 후속 필요 |
| #74 | 오래 유휴한 뒤 첫 생성에서 인증 문제가 400/일반 실패로 오분류되고 작성물이 소실됨 | 생성 option과 create/comment/attachment route가 `SessionExpired`/transport를 숨기지 않는다. 유휴 복귀 background auth probe를 추가하고, 생성 dialog·댓글 draft는 uncertain/auth 동안 ID와 payload를 유지한다. 일반 cache 성공은 Jira auth 회복으로 보지 않으며 성공한 probe의 rolling cookie만 generation/revision fence 뒤 저장한다. | `test_creation_options_propagate_idle_auth_instead_of_caching_empty_list`, `test_task_create_does_not_misclassify_auth_or_transport_as_bad_request`, `test_attachment_auth_failure_is_not_masked_by_cached_current_user`, `test_proactive_probe_silently_renews_an_expired_jira_session`, rolling-cookie persistence tests, frontend auth/write contracts | 구현·집중 회귀 있음 |
| #75 | Workload 대량 로딩 중 세션이 만료되면 사람별 요청이 한꺼번에 실패·로그인 재시도를 유발함 | 공용 scheduler 동시성을 3으로 제한하고 single-flight한다. 첫 auth 실패 시 아직 시작하지 않은 사람 요청을 neutral loading 상태로 둔 채 queue를 멈추고, `auth-ok` 뒤 실패 대상만 재개한다. | `test_workload_reads_share_a_capped_scheduler_and_stale_filters_are_ignored`, `test_first_auth_failure_pauses_remaining_people_until_auth_ok`, `test_failed_people_retry_individually_without_refreshing_successful_rows` | 구현·집중 회귀 있음 |
| #76 | 티켓 dialog가 하위 정보 전체를 기다려 main ticket과 이미 준비된 조작까지 막음 | main ticket을 먼저 표시하고 editmeta, child type, ancestors, comments, siblings, attachments, documents, children, related 등 9개 패널을 우선순위대로 독립 로딩한다. 각 패널은 성공 상태를 유지하고 실패한 패널만 재시도한다. | `test_secondary_panels_start_in_priority_order_without_an_all_settled_barrier`, `test_each_panel_preserves_failure_state_and_can_retry_after_auth`, `test_panel_recovery_helpers_are_pure_and_select_only_failed_panels` | 구현·집중 회귀 있음 |
| #77 | WBS Epic tree 실패 경로가 잘못된 `e.ticket` 참조로 추가 오류를 만들고 전체 tree를 잃음 | 현재 Epic key에 partial state를 기록하고 이전 row를 유지한다. 실패한 Epic key만 재요청하며 성공 후 해당 partial marker만 제거한다. | `test_epic_tree_failure_uses_the_current_epic_key_and_is_retryable`, `test_epic_tree_retry_is_key_scoped_and_keeps_previous_rows_until_success` | 구현·집중 회귀 있음 |
| #78 | VIT에서 늦게 끝난 과거 필터 응답이 최신 화면을 덮고, 부분 실패가 빈 성공처럼 보임 | request generation으로 stale 응답을 무시한다. 성공 module/row와 이전 detail을 유지하고 실패 module/detail만 재시도한다. | `test_module_requests_ignore_stale_generations_and_clear_recovered_errors`, `test_partial_module_rows_and_detail_failures_remain_visible_and_retryable` | 구현·집중 회귀 있음 |

## 시나리오별 결과

아래 “통과”는 mock/fixture로 장애를 결정적으로 주입한 자동 회귀의 결과다. 실제 prod Jira와
브라우저를 조작한 수동 E2E 결과라는 뜻은 아니다.

### 1. My Tasks: 대량/부분 로딩과 필터 전환

| 시나리오 | 주입 조건 | 기대 결과 | 자동 회귀 결과 |
|---|---|---|---|
| OR leaf가 서로 다른 순서로 완료 | leaf future의 완료 순서를 뒤섞음 | 카드 key는 중복되지 않고 매 단계 정렬·통계가 갱신되며 최종 결과는 완료 순서와 무관 | 통과 근거 있음: progressive union, authoritative order tests |
| 한 leaf만 네트워크 실패 | leaf 하나가 예외, 뒤 leaf는 성공 | 성공 카드는 계속 표시하고 실패 axis만 recoverable 상태. partial을 정상 `mt` cache로 저장하지 않음 | 통과 근거 있음 |
| 실패 leaf 재시도 | 첫 요청만 실패 | 이미 성공한 leaf는 cache hit, 실패 leaf만 upstream 재요청 | 통과 근거 있음 |
| 로딩 중 quick filter 전환 | 이전 generation의 응답이 늦게 완료 | 즉시 새 필터 generation을 렌더링. 이전 결과는 leaf/issue cache를 데우되 최신 UI는 덮지 않음 | backend progressive/cache와 frontend generation contract 근거 있음; 실제 클릭 E2E 미수행 |
| 최근 완료 범위 확대 | done leaf 결과가 커짐 | 묵시 limit 때문에 active axis가 밀려나지 않음 | `test_done_window_cannot_crowd_active_axes_out_of_the_task_model` 통과 근거 있음 |
| parent 준비 후 SubTask 지연 | parent leaf는 완료, child membership은 지연 | parent 카드부터 보이고 해당 그룹만 hydrate; 전체 화면 block 없음 | deferred group/stream tests 통과 근거 있음 |
| 권한 없는 leaf | 403 | 해당 조각은 best effort로 조용히 제외, 로그인 popup/전역 실패 없음 | frontend 분류 contract 근거 있음 |
| 인증/기타 leaf 실패 | 401 또는 transport | 기존 성공 카드 보존, 일부 오류 안내 및 해당 leaf 재시도 가능 | backend/frontend contract 근거 있음 |

### 2. 인력 워크로드: 사람·bucket 단위 복구

| 시나리오 | 주입 조건 | 기대 결과 | 자동 회귀 결과 |
|---|---|---|---|
| 사람 한 명의 summary 실패 | exception 또는 HTTP 200 `error:true` | 다른 사람은 정상 표시. 실패한 사람만 bounded retry하며 실패 응답은 0건으로 캐싱하지 않음 | 통과 근거 있음 |
| 상세의 open bucket만 실패 | bucket 한 조각 실패 | 다른 bucket row 유지, open만 로딩/재시도. 상세 전체 새로고침 없음 | 통과 근거 있음 |
| due-risk 일부 실패 | 사람별 open/in-progress 중 일부 실패 | 받은 위험 티켓은 표시하되 “위험 없음”으로 확정하지 않고 incomplete 상태 표시 | 통과 근거 있음 |
| 대량 로딩 중 첫 401 | 사람 queue 진행 중 인증 만료 | 동시 요청 최대 3, 대기 queue 정지, 빨간 최종실패 대신 loading 유지, 인증 복구 뒤 대상만 재시도 | source/pure contract 통과 근거 있음; 실제 다인원 브라우저 타임라인 미측정 |
| 필터 변경 중 이전 응답 종료 | module/기간/담당자 조건을 연속 변경 | 최신 generation만 화면 반영, 완료된 이전 fetch는 하위 API cache에 남김 | scheduler/generation contract 근거 있음; 실제 클릭 E2E 미수행 |
| My Tasks 후 Workload 이동 | 겹치는 assigned JQL leaf가 warm | My Tasks의 all-assigned leaf를 workload의 1주/1달/전체 범위가 재사용 | `test_mytasks_all_assigned_leaves_are_reused_by_every_workload_window` 통과 근거 있음 |

### 3. 티켓 dialog·WBS·VIT: 독립 패널과 부분 결과

| 시나리오 | 주입 조건 | 기대 결과 | 자동 회귀 결과 |
|---|---|---|---|
| dialog main 성공, secondary 지연 | child/history/attachment 등에 지연 | main ticket과 이미 준비된 컨트롤을 즉시 사용, 각 패널은 독립 완료 | TicketDialog ordering contract 통과 근거 있음 |
| dialog 패널 한 개 실패 | comments 또는 children 예외 | 성공 패널 유지, 실패 패널만 retry. 전체 click disable 없음 | panel recovery pure/contract 통과 근거 있음 |
| Epic 하위 100개 초과 | Agile API가 page size를 100 이하로 제한 | `startAt`으로 끝까지 읽고 205개를 모두 합친 뒤에만 membership cache 게시. 중간 page 실패는 이미 받은 issue cache만 warm하고 관계 목록은 비캐시 | `test_epic_child_pagination.py` 7개 회귀 통과 근거 있음 |
| 계보 인증 실패 | ancestors 원본 read에서 401 | 빈 계보를 저장하지 않고 인증 복구 뒤 다시 요청 가능 | lineage backend tests 통과 근거 있음 |
| WBS Epic 하나 실패 | epic tree 요청 예외 | 이전 tree 유지, 정확한 Epic key만 error/retry | WBS contract 통과 근거 있음 |
| VIT 이전 generation 지연 | 이전 module/detail이 나중에 반환 | 최신 화면을 덮지 않으며 성공 row/detail은 유지 | VIT contract 통과 근거 있음 |

### 4. 쓰기·유휴 인증

| 시나리오 | 주입 조건 | 기대 결과 | 자동 회귀 결과 |
|---|---|---|---|
| 댓글 POST 반영 뒤 응답 유실 | provider가 댓글을 만든 뒤 timeout/5xx | 입력과 mutation ID 보존. fresh 댓글 조회로 기존 댓글을 찾아 두 번째 POST 없음 | backend 회귀 통과 근거 있음 |
| 티켓 생성 반영 뒤 응답 유실 | issue 생성 후 transport exception | 동일 exact payload/ID로 재시도하여 생성된 key 회수, 중복 issue 없음 | backend 회귀 통과 근거 있음 |
| 첨부 업로드 반영 뒤 응답 유실 | 파일 저장 후 timeout 또는 connection error | issue attachment field에서 filename/size/time을 확인하고 기존 attachment 반환, 중복 업로드 없음 | backend 회귀 통과 근거 있음 |
| Resolve 화면 metadata에 comment 누락 | 완료 전환에는 resolution만 있고 comment field가 없음 | Done/Resolve는 공용 rich comment editor를 항상 표시하고, 서버도 실제 transition category를 확인한 뒤 댓글을 함께 보냄 | prod-shape fixture와 frontend editor contract 통과 근거 있음 |
| Resolve 작성 중 인증/renderer 종료 | 전환 댓글·붙여넣은 이미지가 아직 제출 전이거나 첨부 일부만 완료 | transition별 IndexedDB scope에 즉시 저장하고 재진입 시 복원. 명시적인 `버리고 닫기`만 첨부 정리 후 삭제 | transition draft/attachment static contract 통과 근거 있음; 실제 브라우저 재기동 E2E 미수행 |
| 상태 전환 반영 뒤 응답 유실 | 상태와 완료 댓글은 반영됐지만 HTTP 응답 유실 | 동일 payload/ID를 복원하고 fresh 상태·전환 가능 여부·댓글을 함께 확인해 전환과 댓글을 반복하지 않음 | transition response-loss/reconciliation 회귀 통과 근거 있음 |
| 같은 사용자의 과거 동일 객체 존재 | 직전 5분에 동일 댓글·파일·템플릿 티켓이 이미 존재 | `attemptedAt` 이전 후보는 성공 근거로 쓰지 않고, 실행 이후 후보도 복수면 UNKNOWN으로 fail closed | same-actor pre-existing comment/attachment/issue 및 multiple-match 회귀 통과 근거 있음 |
| 응답 유실 직후 검색 0건 | Jira index 반영 지연 | 최초 30초 동안 ABSENT로 확정·재POST하지 않고 pending 유지 | `test_fresh_empty_reconciliation_waits_for_jira_index_instead_of_duplicate_post` 통과 근거 있음 |
| reconciliation 중 인증 만료 | pending receipt 확인 GET에서 401 | receipt와 입력 유지, 로그인 복구 뒤 동일 ID로 확인 재개 | 통과 근거 있음 |
| reconciliation 중 다시 네트워크 실패 | timeout/5xx/raw connection | fail closed: 미반영으로 간주하지 않고 uncertain 유지 | parameterized recovery tests 통과 근거 있음 |
| 캐시 cleanup만 실패 | Jira commit 및 receipt 기록 뒤 invalidation exception | write는 반복하지 않고 다음 시도에서 cleanup만 재개. 완료 이후 replay도 generation을 재증가시키지 않음 | `test_committed_receipt_retries_cache_cleanup_but_success_replay_does_not_repeat_it` 통과 근거 있음 |
| 5분 이상 유휴 후 앱 복귀 | focus/visibility/pointer/key activity | 비동기 probe 한 번, frontend 60초 cooldown 및 backend single-flight. expired만 silent renew, network unknown은 login popup 없음. 성공 시 rolling Jira cookie만 15분 throttle로 저장 | auth activity/runtime/store tests 통과 근거 있음 |
| 유휴 후 첫 create option에서 401 | issue type/priority/Epic 후보 호출에서 만료 | 빈 추천목록 또는 400으로 위장하지 않고 전역 auth recovery로 전달 | creation option/route classification tests 통과 근거 있음 |

## 캐시 정합성 확인 사항

- failure, partial, 인증 만료 응답은 정상적인 빈 `issue`, lineage, workload, My Tasks
  aggregate로 승격하지 않는다.
- JQL/stream 검색에서 받은 issue는 기존 light/full issue cache 정책을 유지한다. light cache가
  full request를 충족시키는 역방향 승격은 허용하지 않는다.
- My Tasks와 Workload는 겹치는 leaf/issue cache를 재사용하며, UI generation과 cache write를
  분리해 오래된 화면 응답도 안전하면 cache warming에는 기여한다.
- Workload의 SubTask 부모 Epic 조회가 일부 실패하면 category count와 이미 받은 티켓은
  보존하되 불완전 aggregate/bucket은 저장하지 않는다. retryable partial은 브라우저 memo에서도
  제거해 실패한 부모만 다시 읽을 기회를 남긴다.
- 활동 정보는 Jira와 Confluence source별로 독립 캐싱한다. 한 source가 실패해도 다른 source의
  성공 결과는 재사용하지만 실패를 정상 빈 배열로 저장하거나 partial 응답을 브라우저에 고착시키지 않는다.
- Epic 이름은 12시간 metadata cache를 별도로 사용하되 key-only fallback을 authoritative
  value로 저장하지 않는다. LTM 내부 mutation은 정확한 Epic key를 무효화한다.
- mutation receipt는 일반 cache invalidation과 분리돼 있다. 성공 write 뒤 파생 cache cleanup이
  실패해도 duplicate 방지 근거가 사라지지 않는다.
- same mutation ID에 다른 payload를 보내면 거부한다. receipt TTL 8일은 댓글 draft 보존 기간
  7일보다 길다.

## 자동 검증 기록과 해석

최종 영향 집중 실행은 Resolve editor/auth/mutation 묶음 **131 passed, 7 skipped**, Workload
부분 복구 묶음 **43 passed**였다. 전체 suite는 로컬에서 기존 agent fast-path 환경 의존 항목
6개를 deselect하고 **3568 passed, 41 skipped, 1 failed**였다. 남은 1건은 변경하지 않은 MCP
integration test의 자식 Python 프로세스가 이 임시 target dependency 경로를 상속하지 못해
`mcp` 모듈을 찾지 못한 실행환경 실패다. `app/agent`, 해당 테스트와 이번 diff는 없다.

skip에는 Playwright/Chromium 또는 Windows 선택 기능이 없는 환경에서만 건너뛰는 테스트가
포함된다. 숫자들은 서로 겹치므로 합산하지 않는다. CI는 정식 requirements 환경에서 아래
명령으로 다시 판정해야 한다.

```powershell
python -m pytest -q --basetemp=.cache/test-tmp/ci
```

## 브라우저 검증 범위

2026-09-03에 실제 Google Chrome을 연결해 지연 없는 local mock(`127.0.0.1:4457`)에서
`My Tasks → DL-9001 → 진행 중 → Resolved` 순서로 UI를 직접 조작했다. Resolve dialog의
`.trx .ProseMirror`는 **1개이며 visible**이었고, 접근성 트리에서도 rich editor toolbar와
`댓글을 입력하세요` 편집 영역, 기본 resolution `Done`이 함께 확인됐다. 전환은 제출하지 않아
fixture 상태는 바꾸지 않았다. 이 smoke는 이번 prod 제보의 핵심인 “Resolve 폼에서 댓글
에디터가 사라짐” 회귀가 실제 번들 UI에서 해소됐음을 확인한다.

다만 아래 장시간·장애 주입 여정은 아직 **검증 완료로 주장하지 않는다**.

- prod와 같은 불규칙 지연에서 실제 카드가 주르륵 추가되는 시각적 타임라인
- 담당자·보고자·모듈·기간 quick filter를 연속 조작했을 때의 클릭 응답 p50/p95
- My Tasks → Workload → WBS → VIT 이동 시 실제 네트워크 요청 수와 cache hit 비율
- 유휴 후 focus 순간의 무음 재인증 UX 및 로그인 창이 중복으로 뜨지 않는지
- 응답 유실 뒤 브라우저/앱 재시작을 포함한 작성 UI 복원 동작

나머지 frontend 검증은 source/static contract와 UI 독립 pure state machine의 Node 실행에
기반한다. 위 다섯 항목은 별도 실제 UI E2E로 수행해야 한다.

## merge 전 수동 시나리오

1. `LAKE_MOCK_LATENCY_MS=0`, `250`, `800` 각각에서 My Tasks의 담당자, 보고자, 모듈,
   완료 범위, 정렬을 빠르게 교차 변경한다. 이전 필터의 카드가 최신 결과를 덮지 않는지와
   각 완료 leaf가 즉시 append되는지 확인한다.
2. 같은 session에서 My Tasks → Workload → WBS → VIT → My Tasks를 이동하고 DevTools의
   요청 수를 기록한다. 첫 방문보다 재방문/겹치는 leaf의 upstream 요청이 감소해야 한다.
3. Workload를 여러 사람으로 로드하다 한 사람/bucket에 403, 한 사람에 401, 다른 사람에
   timeout을 주입한다. 403은 조용히 제한되고, 401은 queue를 멈췄다가 인증 후 대상만,
   timeout은 bounded retry 후 해당 대상만 오류가 남아야 한다.
4. 다수 SubTask 티켓 dialog에서 main ticket, comments, children, timeline을 서로 다른 지연으로
   반환한다. 준비된 항목의 클릭이 막히지 않고 실패 패널만 retry되는지 확인한다.
5. 5분 이상 유휴 후 window focus로 복귀한다. Jira session expired와 offline을 따로 주입해
   전자는 silent renew, 후자는 로그인 창 없이 degraded로 남는지 확인한다.
6. mock write를 commit-after-timeout으로 바꿔 티켓·댓글·첨부 각각 제출한다. retry와 앱
   재기동 뒤 객체 수가 1인지, 작성물이 유지되는지 확인한다.

고정 지연이 필요하지 않은 기능 회귀에는 `LAKE_MOCK_LATENCY_MS`를 사용하지 않는다. 장애
복구·동시성·체감 성능 검증에서만 명시적으로 활성화한다.

## 남은 위험과 후속 작업

1. **#73 잔여 쓰기 보호**: 일반 필드 update, 댓글 수정·삭제, 첨부 삭제,
   issue/remote document link, ticket 삭제 및 bulk/Agent write에 stable mutation ID와 operation별
   authoritative reconciler가 필요하다. 현재 create/comment/attachment/transition만 receipt 보호 대상이다.
2. **bulk 부분 성공 receipt**: 성공한 행만 committed로 기록하고 실패 행만 재시도해야 한다.
   batch 단위 ID만 두면 부분 성공 뒤 전체 재실행이 중복을 만들 수 있다.
3. **reconciliation 시간·충돌 한계**: create는 필드 조합, attachment는 filename/size/time,
   comment는 본문·작성자·시간으로 일치시킨다. `attemptedAt` 이전 후보와 복수 후보는 UNKNOWN으로
   멈춰 오인은 피하지만 Jira/로컬 시계 차이가 크면 실제 성공도 자동 확정하지 못할 수 있다.
   Jira issue property나 서버가 검색 가능한 client marker를 지원하면 그 방식이 우선이다.
4. **process-local lock 가정**: mutation lock은 desktop single-instance를 전제로 process-local이다.
   다중 backend process로 바뀌면 DB unique lease/transaction lock으로 옮겨야 한다.
5. **외부 Jira 변경과 Epic TTL**: LTM을 거치지 않은 Epic 이름 변경은 invalidate event가 없어
   최대 12시간 이전 이름이 보일 수 있다. stale-while-revalidate 또는 `updated` 기반 조건 검증을
   후속으로 비교한다.
6. **지속 활동 중 세션 만료**: 새 probe는 “유휴 후 복귀”에 최적화돼 있다. 계속 앱을 사용하되
   Jira 호출 없이 오래 머문 경우에는 다음 실제 API가 만료를 감지한다. active 상태의 저빈도
   probe가 필요한지는 Jira 부하와 세션 정책을 확인한 뒤 결정한다.
7. **실제 UI 성능 계측**: Browser 연결 후 FCP/완료 p50·p95, 클릭 지연, long task, upstream
   요청 수, leaf/issue cache hit를 baseline과 candidate에서 같은 seed로 수집해야 한다.
8. **heuristic 오류 분류**: 일부 사내 proxy가 Jira 403/401을 5xx 메시지로 감싸므로 frontend가
   status와 message를 함께 본다. 가능한 route는 구조화된 `errorKind`를 항상 제공하도록 계속
   줄여 나가야 한다.

## 최종 acceptance gate

- 전체 CI suite 통과
- #73을 부분 완료로 유지하거나 잔여 쓰기를 별도 follow-up issue로 분리
- 실제 Browser 수동 시나리오 1–6 수행 및 결과/요청 수 기록
- 성공 데이터가 일시 실패 뒤 사라지지 않음
- 403은 best effort, 401은 targeted auth recovery, transport는 bounded retry로 각각 구분됨
- 같은 mutation ID의 create/comment/attachment를 여러 번 제출해도 Jira 객체 수가 1개
- 테스트 임시 파일은 `.cache/test-tmp/<run-id>`에만 두고 실행 후 해당 run-id만 삭제
