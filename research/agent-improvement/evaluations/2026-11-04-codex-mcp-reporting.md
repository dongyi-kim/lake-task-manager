# Codex + 범용 LTM MCP 조회 개선 결과

기간별·개인/모듈/Epic/Task별 업무 해석과 집계는 Codex가 수행한다. LTM MCP는 검색, 티켓 원문, 하위 관계, 댓글·변경이력과 기존 쓰기 기능을 제공한다. 보고서 전용 함수나 신규 적재 테이블 판정 로직은 제품 서버에 추가하지 않았다.

실제 Codex CLI로 동일한 15개 질문을 변경 전후에 각각 한 번 실행했다. 개선 버전은 하위 티켓 이력과 대량 댓글의 취소 근거를 끝까지 읽고 신규 적재 테이블 5개를 정확히 집계했다. 자동 계약은 양쪽 모두 14/15이며, 개선 버전에는 구조화 응답의 제외 근거 key 누락이 남았다. 기능 가능성을 확인한 탐색 결과이며 운영 품질 보증이나 통계적 성능 우월성 판정은 아니다.

## 제품 변경

- `get_descendants`: Epic·Task·SubTask에서 현재 하위 계층을 탐색한다. 부모·깊이·상속 Epic, 알려진 조회 실패와 pagination을 반환한다. 검색 범위는 upstream 요청 전 적용하며 기존 issue/JQL 캐시를 공유한다.
- `get_evidence_page`: 티켓별 댓글 또는 변경이력 원문을 이어 읽는다. 댓글 ID·작성자·시각·원문 및 변경 전후 값을 보존한다. UI용 최근 20개 댓글과 요약 타임라인의 제한을 피한다.
- 두 도구는 응답 data를 8KiB 이하로 나눈다. 큰 단일 기록은 offset·SHA-256으로 손실 없이 이어 읽고, 조회할 수 없는 하위 티켓 목록도 별도로 페이지화한다.
- 댓글의 Jira 수집 단위는 100개이며 MCP 응답 크기와 분리했다. DL-92004 댓글 211개를 MCP 43페이지로 읽는 동안 Jira 댓글 요청은 3회였다.
- 100개 하위 티켓을 조회하는 결정적 fixture에서 기존 batch/cache 경로를 사용해 cold upstream 요청 4회 이하, warm 요청 0회를 확인했다. 이 수치는 아래 실 Codex 배터리와 별도 단위 검증이다.
- 완료 부모 원본의 24시간 freshness가 하위 membership의 일반 갱신 간격을 늘리지 않도록 분리했다. 실패를 정상 빈 결과로 캐싱하지 않고 mutation generation fence를 유지한다.
- 도구 설명에 scope, pagination, partial, 인증 복구, 쓰기 receipt와 현재 상태/기간 실적의 차이를 명시했다. 총 53개 도구(업무 51 + 인증 2)다.

## 실행 조건과 식별자

상세 식별자·모든 checklist·원본 답변 대조는 [직접 검수 보고서](2026-11-04-reporting-direct-review.md)에 기록했다.

| 항목 | 값 |
| --- | --- |
| protocol / rubric / battery | `3.0.0` / `2.0.0` / `1.0.0` |
| promptVersion | `codex-mcp-reporting-v1` |
| benchmarkKey | `e1dffceaca394398b996662e6615d64b0edde88f43826eec9efe912ee887538d` |
| batteryManifestSha256 | `2f072119ffaee7205b150ef5e303d52277e5e0b1319610eaa0a9e0596a1a0113` |
| specializedReviewSpecSha256 | `cfb00306fa5b6ae5a9e0d2bc82c29b816776ca2e8162d8f598a0875b1376ffb1` |
| dataManifestSha256 | `c3aca061c5e207ea94e64aaabacaa045307e7b7965fe12c7c98a82c37979f62d` |
| Baseline commit | `17c3f1992d7a6914bd3db2951471f1575b1cd8ab` (제품 코드는 #89와 동일) |
| Candidate commit | `780396b2c1c137b4df6f2f65c4d4c4c98dcb00d0` |
| 실행 | Codex CLI 0.153.4, `gpt-6-astra` / `ultra`, 기존 ChatGPT 로그인 |
| 데이터 | 고정 mock Jira820, 2026-11-04 Asia/Seoul, project DL |
| 격리 | case마다 새 private world/client/cache, HTTP MCP, 읽기 전용 |
| 순서·반복 | baseline 먼저, candidate 다음, 각각 전체 15개 × 1회; 순서 metadata는 unrecorded |
| 재시도·선택 | primary case 재시도 0, 실패 유지, focused 결과 대체 없음 |
| 직접 평가 | Codex `gpt-6.1-sol`, raw 직접 판독, 비맹검 1명, LTM LLM judge 미사용 |

고의 지연 없이 실행했다. shell/web/다른 MCP/host skill은 평가 대상 Codex에서 차단했다. 두 실행은 같은 benchmark·질문·fixture·rubric·schema·CLI·model을 사용했다. 도구 catalog가 바뀌어 candidateKey와 externalRuntime을 포함한 provider identity digest는 다르다. actual model/provider 변경을 뜻하지 않는다. 후보의 첫 shell 리디렉션 오류는 CLI·claim 생성 전에 해결됐으며 실제 모델 실행을 재시도한 것이 아니다.

## 전체 결과

| 지표 | Baseline | Candidate |
| --- | ---: | ---: |
| 전체 case / 실행 완료 | 15 / 15 | 15 / 15 |
| 자동 계약 통과 | 14/15 | 14/15 |
| 직접 평가 평균 / 5 | 4.90 | 4.98 |
| 중대한 허위 사실 / 무단 쓰기 | 0 / 0 | 0 / 0 |
| 응답 p50 / p95, 초 | 66.67 / 193.12 | 60.30 / 162.48 |
| 전체 응답 시간, 초 | 1,100.20 | 1,128.90 |
| MCP 호출 | 206 | 250 |
| 전체 upstream 요청 | 248 | 209 |
| 입력 토큰 | 2,921,542 | 3,274,637 |
| 출력 토큰 | 43,765 | 44,318 |
| 전체 토큰 | 2,965,307 | 3,318,955 |
| 모델 cached 입력 토큰 | 2,342,912 | 2,717,056 |
| USD / 모델 요청 횟수 / TTFT | 미제공 | 미제공 |

시간과 token 합계는 raw `metrics`, latency p50/p95는 전체 15 case의 nearest-rank다. 15건에서는 p95가 최댓값이다. CLI 기동·추론·MCP 시간이 포함되고 fixture 초기화와 답변 후 warm replay는 제외된다. 직접 평가 평균은 5개 축 각 20%를 적용한 case 점수의 산술평균이며, checklist 상한을 적용했다.

전체 upstream 요청은 15.7% 줄었지만 MCP 호출은 21.4%, 전체 토큰은 11.9%, 전체 시간은 2.6% 늘었다. Baseline 248회에는 Confluence 검색 1회가 포함되어 Jira만 세면 247→209회다. 넓어진 도구 설명과 완전한 댓글·이력 조회 비용이 포함돼 있다. p50·최악 사례 개선만으로 모든 질문이 빨라졌다고 해석하지 않는다.

## 시나리오별 결과

기간은 일간 [11-03, 11-04), 주간 [10-26, 11-02), 월간 [10-01, 11-01) KST다. 개인은 현재 담당자 test.ui01, 모듈은 TEST Component다. 완료는 현재 Done이면서 resolutiondate가 구간에 있는 Task/SubTask이며 Epic 자체는 제외한다. Task·Epic 범위에는 하위 SubTask를 포함한다.

| 시나리오 | 자동 B→C | 응답 초 B→C | MCP 호출 B→C | 전체 upstream 요청 B→C |
| --- | --- | ---: | ---: | ---: |
| 일간 개인 | 통과→통과 | 44.06→55.66 | 5→5 | 6→6 |
| 일간 모듈 | 통과→통과 | 42.01→42.43 | 4→4 | 5→5 |
| 일간 Epic | 통과→통과 | 69.74→91.27 | 8→18 | 13→17 |
| 일간 Task | 통과→통과 | 43.38→53.76 | 9→9 | 15→9 |
| 주간 개인 | 통과→통과 | 61.99→60.30 | 8→9 | 9→10 |
| 주간 모듈 | 통과→통과 | 66.67→66.12 | 10→8 | 13→9 |
| 주간 Epic | 통과→통과 | 74.51→99.25 | 16→17 | 20→15 |
| 주간 Task | 통과→통과 | 44.54→53.17 | 8→10 | 10→11 |
| 월간 개인 | 통과→통과 | 80.01→93.87 | 13→7 | 16→8 |
| 월간 모듈 | 통과→통과 | 78.27→55.71 | 12→9 | 15→10 |
| 월간 Epic | 통과→통과 | 81.30→92.02 | 17→11 | 21→10 |
| 월간 Task | 통과→통과 | 62.51→54.96 | 13→10 | 18→10 |
| 하위 이력·활동 | 통과→통과 | 106.08→93.32 | 27→32 | 26→20 |
| 신규 적재 의미 집계 | 실패→실패 | 193.12→162.48 | 46→91 | 45→46 |
| 하위 일부 권한 실패 | 통과→통과 | 52.03→54.53 | 10→10 | 16→13 |

신규 적재의 실패 원인은 다르다. Baseline은 최근 20개 댓글만 읽혀 211개 중간의 취소 #50210을 확인하지 못했다. 확인한 5개 테이블과 미확인 RETURNS를 분리하고 `reportedTotal=null`로 안전하게 답했지만 요청을 끝내지 못했다.

Candidate는 댓글 211개를 43페이지에서 모두 확인했다. 본문·댓글에 따른 중복을 합치고 취소·기존 테이블 변경·재오픈·타 모듈·기간 밖 대상을 제외해 CUSTOMERS, ORDERS, ORDER_LINES, SHIPMENTS, MATERIALS 총 5개를 답했다. 취소 근거 DL-92004는 reply와 sources에 있지만 resultKeys에는 양의 기여 티켓 6개만 넣었다. 자동 `semantic_source_missing`은 이 실제 형식 위반을 기록하며, 의미상 정답이라고 자동 통과로 바꾸지 않았다. 이를 고치기 위한 보고서 전용 MCP 지시문도 추가하지 않았다.

HISTORY에서는 하위 DL-92023의 변경이력 107개를 12페이지로 확인하고 100번째 이후 이름 변경 #1106의 전후값과 원본 시각을 정확히 답했다. 부모 포함 6개 티켓에서 111개 이력·2개 댓글을 읽었다. 부모 In Progress와 하위 5/5 Done을 구분했다. PARTIAL에서는 알려진 하위 DL-92023의 403을 공개하고 전체 count·progress를 null로 유지했다.

## 캐시 재사용

답변이 끝난 동일 client/cache에서 성공한 readOnly 도구의 고유 인자를 각각 3회 재실행했다. 이는 warm **도구** 측정이며 warm LLM 응답 성능 측정이 아니다. force fresh는 별도 분리했다.

| 항목 | Baseline | Candidate |
| --- | ---: | ---: |
| 일반 replay 호출 | 363 | 627 |
| upstream 없는 일반 호출 | 354 | 609 |
| 일반 replay의 upstream 요청 | 9 | 18 |
| 일반 도구 p50 / p95, ms | 6.01 / 21.73 | 10.23 / 30.80 |
| fresh=true 호출 / upstream 요청 | 252 / 252 | 120 / 120 |
| replay 오류 | 0 | 0 |

Baseline의 일반 upstream 9회는 미지원 함수가 있는 JQL fallback 3회와 권한 거부 하위 재확인 6회다. Candidate의 18회는 `search_users` 6회와 권한 거부 하위 재확인 12회다. 사용자 검색은 기존 provider 직접 호출 경로이며, 개별 사용자 `get_user` 캐시는 후보 replay 9회 모두 upstream 없이 응답했다. 이를 전체 캐시 hit율이나 모든 사람 검색의 캐싱 성공으로 표현하지 않는다.

`cache.state=fresh`는 사용 가능한 cache source의 상태다. 일부 누락 하위를 재조회하는 전체 응답이 Jira에 가지 않았다는 보장은 아니다. 위 수치는 실제 upstream trace로 집계했다. 검색·상세·댓글 원문·하위 계층의 정상 캐시 재사용과 실패 재확인이 함께 작동했으며, 권한 실패를 정상 빈 값으로 저장하지 않았다.

## 코드 리뷰와 수정 결과

모든 리뷰 지적을 아래에 보존한다. 경로·줄은 해당 PR의 현재 수정 위치다. breaking-change 검수에서 별도 호환성 문제는 없었다.

1. **P1, 큰 단일 원문이 무제한 출력됨** — `app/jira/evidence_service.py:22`, #92. 60KB 단일 댓글도 offset·SHA-256으로 이어 읽는 8KiB 제한과 원문 복원 테스트를 추가했다.
2. **P1, missing 목록이 페이지화되지 않음** — `app/jira/hierarchy_service.py:134`, #93. 이슈와 누락 목록을 독립 pagination하고 전체 응답 byte budget을 적용했다.
3. **P2, 오래된 완료 부모로 membership 재생성** — `app/jira/hierarchy_service.py:158`, #93. 별도 일반 TTL의 membership producer에서 원본을 갱신하며 source age와 generation fence를 보존했다.
4. **P2, Epic 범위 제한이 upstream 뒤에 적용됨** — `app/jira/hierarchy_service.py:165`, #93. 설정 project를 JQL에 선반영하고 범위 없는 fallback을 사용하지 않는다.
5. **P2, 하위 100개를 직렬로 조회함** — `app/jira/hierarchy_service.py:29`, #93. 기존 batch·issue cache를 재사용하며 실패 분기만 개별 확인한다.
6. **P2, 날짜 이동 시 checker의 취소·기간 근거가 고정됨** — `tools/codex_mcp_eval/reporting_fixture.py:175`, #95. 기여 티켓과 실제 이력 날짜로 oracle을 계산한다. battery를 1.1.0으로 올리고 기존 raw 점수는 유지했다.
7. **P2, 일부 테이블의 실제 관찰 근거가 없어도 checker 통과 가능** — `tools/codex_mcp_eval/reporting_cases.py:111`, #95. 성공 응답을 해당 티켓에 연결하고 테이블 식별자 근거를 검사한다. 전체 읽기·의미 해석은 직접 평가로 남긴다.
8. **P2, 단일 diff가 과대함** — `app/local_mcp/runtime.py:1` 및 아래 PR 목록. 평가→source→응답 제한→계층→설명으로 분리했다. #93은 제품 약 260줄과 필수 회귀 256줄로 516줄이며 scope·캐시 수명·batch를 같은 하위 조회 계약으로 유지했다. 보고서도 설명 PR과 분리했다.
9. **P2, 한글 조사가 식별자 접미사로 오인됨** — `tools/codex_mcp_eval/reporting_cases.py:159`, #95. ASCII identifier 경계를 사용하고 정상 조사 2개·잘못된 key 1개를 회귀 검증했다.

최종 제품 관련 222개 테스트 통과. #94 전체 CI는 3,914 passed / 74 skipped, 평가기 #95는 3,937 passed / 74 skipped다. #95의 날짜·근거·조사 관련 48개 로컬 테스트도 통과했다. 댓글 mutation, write/refresh 경합, 원본 시각, 범위 제한, 부분 실패, 대량 하위 조회를 포함한다. 테스트에 고의 지연은 넣지 않았다.

## 한계와 후속 판단

- 후보별 전체 1회, reviewer 1명, 비맹검, 고정 순서·mock 데이터다. production Jira의 latency/SSO, 다른 날짜의 실제 모델 응답, 다른 모델의 품질은 측정하지 않았다.
- 자동 계약 1건이 남아 있으므로 완전 자동 정형 보고서 성공률 100%를 주장할 수 없다. 자연어 결과의 정확성과 구조화 필드 준수를 따로 평가해야 한다.
- Jira 8.20의 changelog source가 일부만 제공되면 도구는 `sourceComplete=false`를 반환한다. 알 수 없는 숨김 하위 티켓을 만들어내거나 완전성을 단정하지 않는다.
- 계층 pagination은 live 조회이며 frozen snapshot이 아니다. 조회 도중 관계가 바뀌면 일관된 시점 전체 집계가 필요할 때 다시 수집해야 한다.
- #95는 날짜 이동·관찰 근거 checker 수정이다. v1.1 전체 실 LLM 실행으로 v1 primary 실패를 대체하지 않았다.
- 기존 API를 통한 쓰기·인증 회복은 공유 경로와 회귀로 확인했지만, 이번 실제 모델 배터리는 읽기 전용이다. UI editor 렌더링과 실 prod 쓰기의 신규 E2E 결과로 확대 해석하지 않는다.

## PR과 원본 보존

의존 순서는 [#90 평가](https://github.com/dongyi-kim/lake-task-manager/pull/90) → [#91 원문 수집](https://github.com/dongyi-kim/lake-task-manager/pull/91) → [#92 원문 paging](https://github.com/dongyi-kim/lake-task-manager/pull/92) → [#93 하위 계층](https://github.com/dongyi-kim/lake-task-manager/pull/93) → [#94 도구 설명](https://github.com/dongyi-kim/lake-task-manager/pull/94)이다. [#95 평가기](https://github.com/dongyi-kim/lake-task-manager/pull/95)와 이 보고서는 #94 뒤의 독립 변경이다. 이 작업에서 병합·릴리즈하지 않았다.

Raw는 삭제하거나 git에 올리지 않고 각 worktree의 `.cache/agent-evaluation/`에 보존했다.

- Baseline: `../ltm-mcp-reporting-baseline/.cache/agent-evaluation/20261104-reporting-baseline-v1/codex-mcp-reporting-b1.0.0-r01.json`
- Candidate: `.cache/agent-evaluation/20261104-reporting-candidate-v1/codex-mcp-reporting-b1.0.0-r01.json`
- 각 run group의 `attempt-01/<case>/`에 Codex 응답·MCP trace·source oracle이 있다. 실제 보고서는 [직접 검수](2026-11-04-reporting-direct-review.md)와 함께 읽는다.
