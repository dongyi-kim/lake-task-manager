# MCP 동명이인 판단 근거 검증

`search_users` 후보의 `matchContext`에 등록 여부, 같은 모듈, 최근 조회/선택/멘션 시각을 각각 제공한다. 이 정보는 신원이나 권한을 확정하지 않는다. 조회 범위가 제한되면 더 조회하거나 이름·회사로 좁힐 수 있고, 계정 전환 중 반환된 후보에는 개인 근거를 붙이지 않는다.

MCP 전체·Agent prompt integrity·관련 프론트엔드 검증 **300 passed (135.25초)**. 마지막 계정 전환 경합 보완 뒤 대상 회귀 **72 passed (35.68초)**. 중복되는 실행이므로 수치를 합산하지 않는다. 공개 UI POST→MCP 응답, 실제 JiraClient의 인증 실패 상태와 provider 교체, Node의 실제 API/추천 모듈을 검증했다. 일반 테스트에 고의 지연을 넣지 않았다.

독립 context/testing/breaking/change-size 리뷰는 각 Codex `gpt-6.1-sol/xhigh` 작업자가 수행했다. 다음 지적을 모두 반영했으며 실제 모델의 이름 해석 평가와 이 기능의 회귀 검증은 구분한다.

1. **P1 계정 전환의 이력 혼합** — `app/domain/person_context.py:35`. 검색/명시적 조회 시작 전 provider·generation·서버·세션 revision을 고정하고 이력 접근 뒤에도 확인한다. 변경 시 `contextChanged/partial=true`, 모든 문맥 값은 null로 처리한다. UI는 필수 actorHint도 비교한다.
2. **P2 선택적 인증 조회의 부작용** — `app/domain/person_context.py:58`. 기존 인증 메서드 대신 짧은 수명의 provider 결합 메모를 쓰는 조회로 분리했다. 401/429/네트워크 실패를 문맥 미확인으로 처리하고 전역 로그인/연결 상태를 변경하지 않는다. 실제 provider 자체의 전송 장애 처리는 그대로다.
3. **P2 후보별 추가 사용자 조회** — `app/domain/person_context.py:121`. 검색 응답의 표시 이름 또는 기존 캐시만 사용한다. username과 displayName이 같은 한글 이름 10명에서도 개별 `/user` 호출이 생기지 않는다.
4. **P2 서버의 묵시적 제한** — `app/domain/person_context.py:169`. 짧은 비어 있지 않은 결과는 `mayHaveMore=null`, 제한 도달은 true로 표시하고 전역 유일성을 주장하지 않는다.
5. **P2 정상 후보까지 버리는 불완전 응답** — `app/domain/person_context.py:114`. 일부 식별자 누락 행은 제외하고 정상 후보와 partial을 반환한다. 전체 응답 형식 오류 또는 모두 불량인 응답은 정상 빈 결과가 아니다.
6. **P2 날짜·Unicode 식별자 경계** — `app/domain/person_context.py:17`, `:21`. 표시 캐시의 날짜별 env 대신 안정적인 환경+서버+사용자 범위로 이력을 묶는다. 기존 lower 정규화와 맞춰 Straße/Strasse를 합치지 않는다.
7. **P2 일반 새로고침 뒤 기록 중단** — `app/static/lib/api.js:145`. 현재 사용자 힌트를 일반 GET memo와 분리하고 인증 세대로 보호한다. 새로고침·생성의 일반 캐시 제거 뒤에도 유지된다.
8. **P2 재인증 및 늦은 사용자 응답** — `app/static/lib/api.js:146`, `:213`. need-login/auth-ok에서 기존 힌트를 제거하고 인증 완료 때 한 번 비동기 조회한다. 이전 요청이 새 사용자를 덮어쓰지 않는다.
9. **P2 UI 기록의 실제 전달 검증** — `tests/mcp/test_person_context_ui.py:10`. 허용된 멘션만 공개 MCP 응답에 시각으로 나타나며 거부된 다른 후보의 활동은 모두 null이다. passive 목록·뱃지 조회는 기록하지 않는다.
10. **P3 입력 범위와 형 변환** — `app/local_mcp/read_tools.py:102`. SDK 입력 스키마에 엄격한 정수 1~50을 명시해 bool/string/float가 다른 제한으로 변환되지 않게 했다.
11. **규모와 context** — 서비스/저장, MCP 공개, UI 연동의 단계로 분리했다. 기본 후보는 8명, 명시적 최대는 50명이다. 50명 샘플 data는 약 11KB이므로 필요한 경우에만 늘린다. 실제 사용자 ID는 임의로 잘라내지 않는다.

이력은 7일/사용자별 200개 이벤트로 제한되며 이전 계정 구분 없는 localStorage 기록은 자동 이관하지 않는다. 합성 데이터의 이름·호칭 정성 평가는 별도 보고서에 있고, 그 점수로 새 문맥 필드의 모델 활용 품질까지 검증했다고 주장하지 않는다.
