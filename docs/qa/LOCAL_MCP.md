# Local MCP 검증 — 2026-11-03

## 2026-11-09 연결·상태 복구 후속

앱은 별도 MCP 워커의 heartbeat를 1초마다 확인합니다. 일시적인 업무 지연이나 Jira 오류만으로
워커를 교체하지 않습니다. 워커 종료·연속 heartbeat 누락 시 이전 세대와 실제 동기 작업의 종료를
기다린 뒤 새 SDK/profile manager를 만듭니다. 종료 대기는10초, 워커 복구는10분 내3회로 제한하며,
복구 불가 상태는 정상 실행 모드의 기존 앱 watchdog에 전달합니다. 개발 reload는 기존 watcher를
유지합니다. URL·토큰·업무 캐시·mutation receipt를 유지하고 업무 요청을 자동 재전송하지 않습니다.

모든 profile의 `get_mcp_health`는 Jira·인증 조회 없이 세대·상태·복구 여부를 반환합니다.
`request_mcp_restart(expected_generation)`는 현재 세대에만 요청을 예약하며 중복을 병합합니다.
요청 응답의 마지막 body 전송과 SDK 정리 전에는 재기동하지 않으며 끊어진 ACK로 재기동하지 않습니다.
죽은 MCP 연결에서는 이 도구를 호출할 수 있으리라 기대하지 않고 백그라운드 복구나 트레이를 사용합니다.

트레이의 **상태 확인**은 앱·MCP 상태와 최근 복구 사유를 보여 줍니다. 별도 작업 스레드에서
1초 제한의 PID·instance 확인을 포함한 로컬 `/api/health/live`와 최신 MCP heartbeat를 확인합니다.
대화 상자가 열려 있어도 트레이의 앱 열기·종료 처리를 막지 않습니다. 추가 Jira 호출은 없습니다.

Claude Desktop stdio 프록시는 개별 HTTP 연결 실패를 원래 요청 ID의 오류로 반환하고 계속 실행합니다.
다음 읽기는 같은 주소로 새 HTTP 연결을 열 수 있습니다. 완료 여부가 불명확한 변경은 자동 재전송하지
않으며 `retryable=false`, `retryReadOnly=true`, `repeatWrite=false`를 반환합니다. 구버전 프록시가
이미 종료된 경우 업데이트 후 Claude Desktop을 트레이에서 완전히 종료하고 다시 실행해야 합니다.

검증은 실제 SDK/HTTP/stdio와 격리한 mock Jira를 사용했습니다. 같은 stdio 세션에서 LTM 종료 중
연속 실패 후 복구, MCP-only 세대 교체 후 캐시 공유, 변경 완료 후 응답 유실에도 실행1회,
정상 동시 읽기 유지, 시작 전 종료 신호 보존, 멈춘 loop 위에 두 번째 워커를 만들지 않음을 확인했습니다.
독립 Sol/high 리뷰의 중요 지적2건을 수정했습니다. Windows 네이티브 대화 상자의 복구 중·복구 실패
문구는 접근성 트리로 확인하고 Enter로 닫았습니다. 화면 캡처는 `FrameArrived timed out`이므로
시각 배치나 실제 트레이 메뉴 전체를 직접 검증한 결과로 취급하지 않습니다.
실제 사용자 prod 로그·Claude 설정은 이 작업 환경에서 확인하지 못했습니다. 전체 오프라인 CI는 PR별 확인합니다.

## 기존 기능 검증

아래 SDK·UI 기능 검증은 실제 Jira와 LLM을 호출하지 않았다. 지연 주입은 0ms이며, 격리한
Jira820 fixture와 실행 중인 LTM의 동일 JiraClient를 사용했다. 기존 creation permission 변경 위에 MCP를 구현했다.
이후 수행한 실제 Codex + HTTP MCP 품질 평가는 아래 별도 결과와
[전체 보고서](../../research/agent-improvement/evaluations/2026-11-04-codex-mcp-external-quality.md)에 기록했다.

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

## 실제 Codex 조회 평가 — 2026-11-04

고정한 `77924ce`에서 Codex CLI 0.153.4 / gpt-6-astra / ultra / 기존 ChatGPT 로그인을 사용했다.
기존 이력·사람·우선 업무·진척 질문과 통합검색·exact JQL pagination·OR·부분 실패를 합친
8개 사례가 자동 **8/8**, 독립 직접 검수 **4.86/5**를 기록했다. 총 540.50초,
사례 p50 51.08초 / p95 110.06초, MCP 116회, input 1,072,390 / output 19,994 /
cached 823,808 token이다. 모델 요청 수·USD 비용·TTFT는 관측할 수 없어 추정하지 않았다.

실제 LLM 실행은 PR85 단독 브랜치이며 새 cache-policy 모듈은 결합하지 않았다. 다섯 PR의 실제
캐시 정책·MCP·일괄 생성·mock parent 통합 검증 **39 passed**는 별도 증거다. 실행한 코드의
전체 CI는 **3725 passed, 53 skipped**이며 위 targeted 수치와 합산하지 않는다.

원본 r01의 preflight 오판, r02의 S5 검사 과제약 및 mock parent/권한 차이는 삭제하지 않고
보고서에 남겼다. r03에도 문서 날짜 하루 오기와 과거 마감일 충돌 조회 누락은 경미한 결함으로
기록했다. 절대 `.cache` 경로를 제외하던 기존 runtime fingerprint 결함은 상대 경로 기준으로
수정하고 별도 사후 attestation을 남겼으며, 원본 실행 metadata를 고치지 않았다.
이 결과는 단일 exploratory 평가이며 운영 SSO 또는 정식 qualification을 대신하지 않는다.
