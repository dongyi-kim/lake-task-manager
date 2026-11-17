# Tracking·MCP 동시 수집 안정성

## 확인한 결함

- 운영 SSO의 장수명 Playwright context가 읽기를 마친 API 응답 본문을 해제하지 않음.
- JSON·HTML·첨부·쓰기 오류·인증 확인에서 응답 누적. Python 객체가 사라져도 native 응답 본문은 context 종료까지 유지됨.
- 근거: [Playwright APIResponse.dispose](https://playwright.dev/python/docs/api/class-apiresponse#api-response-dispose).
- Tracking 자동 갱신도 화면에 필요 없는 검색 페이지·분석 중간 결과까지 매번 역직렬화. 수집 시작 시 전체 체크포인트를 연속 두 번 읽음.

운영 사고 당시 프로세스 종료 로그는 확보하지 못함. 아래 검증은 재현한 메모리 누적 결함과 동시 실행 동작에 대한 검증이며, 실제 종료 원인의 확정이나 모든 부하에서의 생존 보장은 아님.

## 변경

- 응답 본문을 추출한 뒤 소유 스레드에서 즉시 해제. 성공·오류·파싱 실패·첨부 재시도 모두 적용.
- 해제 실패가 이미 성공한 쓰기를 실패로 바꾸거나 재전송시키지 않도록 처리.
- Tracking 화면 조회는 표시용 원본 이슈·댓글만 체크포인트에서 읽음. 수집·재개·취소에는 기존 전체 체크포인트 사용.
- 기존 MCP 실행 상한, SSO 대기열 상한, Jira 페이지 동시 실행 제한 유지. 결과 수를 자르는 제한은 추가하지 않음.

## 재현·검증

`tests/runtime/test_sso_response_retention.py`: Chromium 없이 실제 Playwright native request context와 로컬 HTTP 서버 사용. 외부 Jira 호출 없음.

| 조건 | 기존 HEAD | 수정본 |
|---|---:|---:|
| 128KiB 응답 64회 후 남은 native 본문 | 8,390,464바이트 | 0바이트 |
| 해제 후 다음 요청 | 해당 없음 | 정상 |

본문 크기만 측정. 전체 프로세스 RSS나 운영 서버 실측값으로 해석하지 않음. 기존 HEAD의 읽기 함수로 같은 회귀 테스트가 실패함을 별도 프로세스에서 확인.

`tests/runtime/test_sso_response_lifecycle.py`: 소유 스레드 해제, 인증·서버 오류, 파싱 실패, multipart 재시도, 쓰기 결과 보존 검증.

`tests/tracking/test_concurrent_load.py`: 한 달·15명·10 Epic·30 Task·60 Subtask, 생성·완료·댓글 총 300건. 실제 TrackingManager, Jira 페이지 스케줄러, MCP ToolLane을 로컬 provider에 연결. MCP 반복 조회·초과 요청 거절 후 재사용, 일반 FastAPI 스레드풀 응답과 Tracking 조회, UpstreamBusy 이후 이어 수집, 완료한 댓글 재조회 방지 검증. 운영 SSO와 같은 직렬 provider와 병렬 provider를 각각 검사.

`tests/tracking/test_read_pressure.py`: 수집용 중복 그래프를 화면 조회에서 역직렬화하지 않음. 댓글 렌더링·사람 이름·기존 누락 필드·JSON 타입·전체 체크포인트 보존 검증.

최종 검증: Tracking 전체, SSO, 인증 유지·복구, MCP 용량·응답·복구, Jira 스케줄러 관련 **454 passed, 1 skipped / 71.26초**. 제외된 SSO 브라우저 테스트는 기본 Chromium 경로 미설치 때문이며, 설치된 Chromium 실행 경로를 명시한 별도 실행에서 **1 passed / 4.28초**. 합계 455개 사례 통과. 전체 저장소 suite 실행 결과를 의미하지 않음.

테스트 임시 DB·캐시는 OS 임시 디렉터리에 격리하고 종료 시 정리.
