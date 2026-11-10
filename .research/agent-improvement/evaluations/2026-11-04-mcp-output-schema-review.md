# MCP outputSchema 검증 및 독립 리뷰

53개 MCP 도구에 data별 출력 모델과 공통 성공/오류 계약을 추가했다. 응답을 모델로 검증하되 원래 JSON을 반환하므로 extension 필드를 제거하거나 optional 기본값을 응답에 삽입하지 않는다. 실제 호출의 부작용은 검증 전에 완료될 수 있어, 불일치 시 원래 결과를 `error.operationResult`로 보존하고 재시도 금지 정보를 제공한다.

## 검증

- MCP 전체 및 Agent prompt integrity: **263 passed**, 124.03초.
- 이후 기존 disk cache 호환 수정에 대해 티켓·워크스페이스·HTTP 출력 계약: **56 passed**, 19.82초.
- 이전 통합 실행은 평가 하네스 테스트를 포함해 **264 passed**, 128.03초. 서로 중복되는 실행이므로 합산하지 않는다.
- 모든 53개 tools/list `outputSchema`가 유효한 JSON Schema인지 확인하고 HTTP MCP structuredContent와 text JSON 일치를 확인했다.
- 공개 create_task의 실제 mock Jira 생성 완료 후 응답을 손상시켜 원래 key 보존, 동일 mutation ID replay, serializer 복구까지 **POST 1회**임을 확인했다.
- 의도적 지연·운영 Jira 쓰기·별도 배포 없음.

## 독립 리뷰에서 발견하고 수정한 항목

리뷰어: Codex `gpt-6.1-sol`, xhigh. context/testing/breaking/change-size 각 별도 작업 에이전트. 동일 이슈의 중복 제보는 아래 하나로 묶었다. 모든 항목은 수정 또는 명시된 단계 분할로 처리했다.

1. **P1 운영 오류 계약** — `app/local_mcp/schemas/common.py:30`. 운영 classifier가 message 없이 error/detail을 반환한다. 세 형태를 보존하고 실제 SessionExpired/UpstreamUnavailable callback 응답을 검사해 인증 안내·retryability 손실을 막았다.
2. **P2 선택 목록 명세 누락** — `app/local_mcp/schemas/tickets.py:201`. 편집 operations/allowedValues, 전이 목적 상태·필수 화면 필드, 링크 방향을 명시했다.
3. **P2 검색·관계 명세 누락** — `app/local_mcp/schemas/workspace.py:11`, `:111`. 검색의 상태·담당자·Epic·경로와 관계 rel/via를 명시했다. workload 부분 실패와 계층 상세 조회 포인터도 포함했다.
4. **P2 기존 scalar 호환** — `app/local_mcp/schemas/workspace.py:56`, `:380`. 기존 Confluence 문자열 total과 숫자 WBS ID를 변형하지 않고 허용한다. ID가 전파되는 dashboard 필드도 일치시켰다.
5. **P2 쓰기 회귀 검증 부족** — `tests/mcp/test_output_schema.py:97`. 내부 함수 호출 횟수 검사에 더해 실제 공개 create_task·Jira POST·receipt replay를 검증했다.
6. **P2 첨부 URL null** — `app/local_mcp/schemas/tickets.py:295`. 기존 projection이 URL 없는 첨부에 null을 반환한다. 해당 row 때문에 전체 첨부 목록을 실패시키지 않도록 허용했다.
7. **P2 기존 WBS 작업명** — `app/local_mcp/schemas/workspace.py:163`, `:380`. 기존 YAML에서 허용된 숫자·불리언 이름을 config read→people write→dashboard 경로에서 보존한다. 입력 API의 문자열 제약은 변경하지 않았다.
8. **P2 오래된 표시 캐시** — `app/local_mcp/schemas/tickets.py:74`, `:311`, `app/local_mcp/schemas/workspace.py:103`. 이전 댓글의 id/updated/authorId와 문서의 linkId/mention/rel 생략을 허용한다. 실제 cache.set 후 도구 builder 조회에서 upstream 0회·필드 생략 보존을 확인했다.
9. **변경 규모** — `app/local_mcp/runtime.py:260`. 약 2천 줄을 공통·쓰기 모델(404줄), 티켓 모델(630줄), 워크스페이스 모델(627줄), 마지막 런타임 통합 PR로 나눴다. 모델 단계는 직접 JSON Schema 검사로 독립 테스트하며 런타임 공개는 마지막 단계에서만 활성화한다.
10. **모델 context 크기** — `app/local_mcp/schemas/workspace.py:405`. 중간 검토에서 53개 출력 스키마 compact JSON 합계 274,460 bytes, 가장 큰 get_dashboard 13,806 bytes였다. 이후 필드 보강으로 달라질 수 있다. bytes는 토큰 수가 아니며 MCP 클라이언트가 모델에 전달하는 범위도 다르므로 모델 토큰 비용·품질 개선을 이 수치로 주장하지 않는다.

## 범위와 제한

현재 source와 확인한 과거 캐시 형태를 계약화했다. 임의 Jira custom field와 provider extension은 확장 가능하며 모든 운영 변형을 사전 보증하지 않는다. 출력 불일치는 진단 가능한 오류로 처리하고 원래 결과를 보존한다. 사람 지칭 정성 검증은 별도 `feature/mcp-identity-evaluation` PR이며 이 스키마 변경의 모델 비용 비교 실험은 아니다.
