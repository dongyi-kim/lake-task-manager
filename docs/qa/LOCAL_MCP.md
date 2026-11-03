# Local MCP 검증 — 2026-11-03

실제 Jira와 LLM을 호출하지 않았다. 지연 주입은 0ms이며, 격리한 Jira820 fixture와 실행 중인
LTM의 동일 JiraClient를 사용했다. 기존 creation permission 변경 위에 MCP를 구현했다.

| 경로 | 결과 |
|---|---|
| 설정 → HTTP 인증 포함 JSON → 실제 MCP SDK initialize/list/call | 통과; 복사 API의 URL·headers를 그대로 사용 |
| stdio 자식 프로세스 → 실제 localhost HTTP listener → 동일 서버 | 통과; 서버 store의 생성 결과 및 UI가 데운 상세 캐시 재사용 |
| 검색·snapshot 페이지 | UI와 같은 검색 결과, 반복 조회 upstream 증가 없음, 페이지 중복 없음 |
| 사용자·선택지·워크로드·이력·자식·문서 조회 | 실제 SDK 도구 호출 통과; metadata 및 명시적 partial 표시 |
| 생성·필드·상태·댓글·Epic·SubTask·첨부·관계·문서·삭제 | SDK CRUD 후 UI API/검색 재조회에서 변경 확인 |
| 재전송 | 같은 create ID는 POST 1회; 필드 수정 응답 유실 후 동일 ID 재요청은 write 1회와 outcome_unknown 유지 |
| 권한·대상 검증 | Reporter 권한 거부, 외부 프로젝트 거부, 다른 티켓 첨부/링크 삭제 거부, ID 경로 삽입 거부 |
| 연결 경계 | bearer 필수, 실제 peer·Host·Origin 검증, X-Forwarded-For 위조 거부, rotate 즉시 이전 토큰 거부, disable 즉시 503 |
| 캐시 출처 | worker 내부 read scope, 가장 오래된 출처 시간, dead/stale/fresh/unknown 구분; 미관측 unknown |
| 브라우저 설정 UI | 1280×900 light / 390×844 dark, 복사·마스킹·재발급·비활성화·클립보드 거부·늦은 응답 무시 |
| Confluence 검색·본문 | UI 통합검색 → MCP 조회 upstream 0회 추가; 전체 검색페이지/본문 offset 누락 없이 연결 |
| Confluence 실패·권한 | 인증 실패·불완전 body·space 확장 누락·partial/error 응답은 정상 빈 결과로 캐싱하지 않음; 사용자별 캐시 분리 |
| 서비스별 인증 | Confluence 인증/연결 실패를 해당 서비스 오류로 반환; 정상 Jira 세션의 전역 실패 처리를 호출하지 않음 |
| Confluence 경합 | 이전 generation의 늦은 403이 새로 저장된 정상 캐시를 삭제하지 않음 |
| 본문·첨부 읽기 | description 생성→수정→UI 열람, 실제 첨부 bytes 업로드→조각 읽기→원본 일치 |
| 대시보드 권한 | WBS/활동 manager, module workload 접근 거부 시 upstream 조회 없음 |
| 대량 생성 | UI와 같은 사전 검증, Task/Epic 및 SubTask/parent 관계, 원본 index·순서 유지, 부분 실패 재시도 시 성공 항목 POST 추가 없음 |
| 대량 생성 응답 유실 | 서버 반영 후 응답 유실 시 동일 batch 재시도에서 중복 POST 없음; 확인 못 한 항목은 unknown으로 유지하고 성공한 형제 결과 보존 |
| 라벨·컴포넌트 | 실제 SDK로 각각 설정·수정·빈 배열로 해제하고 UI가 읽는 issue 필드에 반영 확인 |
| PMO_VIT | 등록 시 기존 라벨 보존, 등록된 ancestor 아래 root 숨김, 해제 시 PMO_VIT 라벨만 제거 |
| WBS·인력 설정 | 실제 SDK 저장→HTTP WBS/워크로드 조회에 반영, 설정의 미지 필드 보존, 충돌409·권한403·유실 응답 재시도·원자 저장 실패 검증 |
| 열린 화면의 설정 변경 | status revision polling 및 동일 창 이벤트로 MyTasks·워크로드·WBS·VIT 데이터 갱신; UI 관련 15 cases 통과 |
| 최상위 쓰기 결과 | 검증 실패·전체 실패는 isError/ok:false, 부분 성공도 isError/partial:true로 표시; 성공 항목·원래 index·unknown 결과 보존, 기존 HTTP 응답은 유지 |
| 쓰기 전 상태 검증 | 만료된 Open display cache와 최신 Done 상태가 다르면 MCP/일반 UI 수정 모두 차단; 연결 불가 시 stale 상태로 수정 허용하지 않으며 유효한 캐시는 재사용 |
| 오류 분류 | 실제 SDK에서 401 인증·403 권한·409 충돌·입력 오류를 구분; 서버에 연결할 수 없어 권한을 확인하지 못한 경우 logout으로 단정하지 않음 |
| 마지막 WBS 삭제 | SDK/UI API에서 마지막 Task 삭제 후 빈 dashboard·0 집계·null 일정 반환, 첫 Task 재등록 가능; 열린 실제 WBS 화면은 오류 없이 빈 상태 안내로 전환 |
| Epic·VIT 하위 조회 실패 | worker별 누락 수를 MCP partial에 반영, 반복403에도 불완전 tree를 정상 캐시로 저장하지 않음; 기존 완전 캐시와 fetchedAt을 보존하고 복구 후 완전 결과 캐싱 |

초기 관련 API·Jira ticket·Agent MCP consumer·dependency/static contract 회귀 **273 passed**.
초기 MCP 전용 **18 passed**. 설정 UI 별도 **8 passed**이며 실제 `app.main`
endpoint로 복사한 토큰·rotate·disable·재활성화까지 확인했다. 이 숫자는 일부 테스트가 겹친다.

Confluence는 [공식 DC Search API](https://developer.atlassian.com/server/confluence/rest/v924/api-group-search/)의
`expand=content.space` 계약에 따라 space/version을 명시적으로 확장한다. 같은 공통 검색 메서드를 UI/Agent/MCP가 사용한다.
Bitbucket은 실제 LTM 검색이 mock placeholder여서 도구로 제공하지 않는다. 문서검색·읽기와 대량 생성·WBS/인력 관리 등을 포함해 최종 catalog는 49개다.

확장 기능 및 관련 검색·Agent query/reference·런처 회귀는 **192 passed, 1 skipped**.
skip은 독립 MCP branch에 아직 없는 캐시 정책 모듈의 통합 전용 테스트이며, 통합 branch에서 실행한다.
마지막 source 검증 보강 후 관련 query/workspace **111 passed**, 서비스별 오류 분리 후 workspace **11 passed**.
대량 생성·VIT·WBS/인력 추가 후 MCP 전체와 creation permission·mutation recovery·process identity 관련
검증은 **115 passed, 1 skipped**. 별도 관리자 설정 SDK/HTTP 검증 **17 passed**는 이 실행과 겹친다.
대량 생성 전용 시나리오는 입력 오류 시 POST 0회, 일부 확정 실패 재시도, 응답 유실 후 중복 방지,
Epic·parent 연결, 같은 batch ID의 내용 변경 거부를 포함한다.

캐시 정책 PR의 실제 `read_scope`와 결합한 통합 branch 검증도 완료했다. MCP/cache/process/관리/설정 UI
관련 **107 passed** 이후 최종 권한·쓰기 수정에 대한 통합 **28 passed**를 추가 확인했다.
후자는 실제 정책 **7 cases**(UI→MCP 캐시 출처·stale·명시적 새로고침·수정 후 UI 반영,
stale 상태로 수정 금지와 fresh 재사용), 관리자 **19 cases**, 열린 화면 브라우저 **2 cases**다.
마지막 오류 분류 보강은 실제 SDK 오류 계약·관리·일괄 생성 **37 passed**이며 앞선 실행과 일부 겹친다.
마지막 WBS 삭제 보강은 관리자/진척률 **27 passed**, 실제 Chromium 관리자 UI **5 passed**로 확인했다.
하위 조회 partial·캐시 보강 후 MCP/Epic pagination/rollup **81 passed, 1 skipped**를 확인했다.
최종 통합에서는 신규 partial **4 cases** + 실제 cache policy **7 cases** + 실제 관리자 브라우저 **5 cases**,
총 **16 passed**로 다시 확인했다.
CI 전체 실행의 **3675 passed, 52 skipped** 중 남은 한 실패는 이전 뱃지 API만 제공하던 테스트 fake였다.
실제 경로처럼 `get_issue_light(strict=True)`를 제공하고 strict 요청을 단언하도록 갱신한 뒤 해당 파일 **5 passed**를 확인했다.
독립 MCP branch에서 캐시 정책 모듈이 없으면 metadata를 unknown으로 반환한다.
Jira가 이미 반영했지만 응답이 유실된 일반 수정은 안전한 재실행을 추측하지 않는다. 생성·댓글 추가·전이·첨부 외의
미확정 결과는 티켓을 다시 읽어 확인해야 한다. 실제 prod SSO 연결과 네이티브 exe 빌드는 이 검증에 포함하지 않았다.
