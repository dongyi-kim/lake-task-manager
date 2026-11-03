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

관련 API·Jira ticket·Agent MCP consumer·dependency/static contract 회귀 **273 passed**.
마지막 변경 후 MCP 전용 **18 passed**. 설정 UI 별도 **8 passed**이며 실제 `app.main`
endpoint로 복사한 토큰·rotate·disable·재활성화까지 확인했다. 이 숫자는 일부 테스트가 겹친다.

캐시 정책 PR의 실제 `read_scope`와 결합한 검증은 통합 branch에서 수행한다. 독립 MCP branch에서는
모듈이 없을 경우 metadata를 unknown으로 반환하며, source 집계 계약은 별도 fixture로 검증했다.
Jira가 이미 반영했지만 응답이 유실된 일반 수정은 안전한 재실행을 추측하지 않는다. 생성·댓글 추가·전이·첨부 외의
미확정 결과는 티켓을 다시 읽어 확인해야 한다. 실제 prod SSO 연결과 네이티브 exe 빌드는 이 검증에 포함하지 않았다.
