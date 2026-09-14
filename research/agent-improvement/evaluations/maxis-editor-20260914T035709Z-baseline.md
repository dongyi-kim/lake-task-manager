# MAXIS Editor — 신규 baseline 직접 평가 (03:57 UTC)

## 측정 식별자

최종 응답 5개와 차단 전 editor-plan 5개, 정확한 요청·근거·특수 계약을 직접 읽었다. 실제 결과는 모두 빈 blocked로 **사용 가능 0/5**다. 후속 후보 점수는 이 문서에 포함하지 않는다.

| 필드 | 기록값 |
|---|---|
| protocolVersion | `3.0.0` |
| rubricVersion | `2.0.0` |
| batteryVersion | `4.0.0` |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| runKind | `exploratory` |
| runGroupId | `maxis-editor-20260914T035709Z-53ace571` |
| repetitions | `1` |
| candidateCommit | `c7eca692d46e1562f7a4fadbe48c176003073449` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| promptVersion | `ltm-maxis-dify-2026.09.14-editor-plan-v11` |
| model | `gpt-5.4-mini` |
| simpleModel | `not-used` |
| provider | `dify` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| structuredOutputBackend | `dify` |
| structuredBackendVersion | `legacy-internal` |
| structuredBackendFallbackPolicy | `forbid` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` |
| candidateKey | `485c8ccd0ffc64a27284e418b0dc2cab58c0102e36ecb84b24cabc3746ce9c91` |
| comparabilityKey | `b0f15400349ba9812c9712af404e1312b2c3dede6fdb8f804ceaa8e8bd178161` |
| candidateOrderIndex | `1` |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state` |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |

## 비교 가능성 및 evidence 선택

selectionPolicy: `complete-run-no-substitution`; 모든 5개 case 포함, 이후 성공으로 대체하지 않음. 이전 v4와 benchmarkKey는 같지만 이번 결과를 새 baseline으로 독립 평가했다. 후보는 아직 평가하지 않았으며 과거 점수를 덮어쓰지 않았다. 원본 editor 전체 suite와는 `comparableToSourceSuite=false`다.

- [실제 baseline](../../../.cache/agent-evaluation/maxis-editor-20260914T035709Z-53ace571/baseline.json), SHA-256 `ed80900bc254f7b2c2cdb60d727ff864689c8dd8b046a143c2c0d35dfd6ab16f`
- [차단 전 plan](../../../.cache/agent-evaluation/maxis-editor-20260914T035709Z-53ace571/llm-candidates.json), SHA-256 `4ec699075797924c7108f993a1c16095656882715562bcae4029ecb5c3481ec1`
- [직접 평가 JSON](../../../.cache/agent-evaluation/maxis-editor-20260914T035709Z-53ace571/direct-review-baseline.json), SHA-256 `60688d8a177bb3f4cee50d10e5b868b20161221fce0c58171b3954b6bf82f91d`
- [이전 v4 비교 기록](maxis-editor-20260914-v4.md)은 별도 실행의 보존 자료다.

Run batteryVersion 4.0.0과 재사용 input의 component_battery_version 2.0.0은 원문 그대로 구별했다. source checker와 특수 계약은 변경하지 않았다.

## 실행 조건

DEV 비활성 stateless component, synthetic fixed evidence, 모델 gpt-5.4-mini, 한 번의 탐색 실행이다. 실행 metadata의 candidateDirty=false이나 반복 수·합성 component 한계 때문에 qualification 대상이 아니다. 평가자가 원격 호출·재시도·게시를 추가하지 않았다.

## 배터리 범위

CMP2·CMP3·CMP6·CMP8·CMP9 전체 5개다. `tools.agent_compose_eval.CASES`의 source checker와 `editor_renderer_contract_flaws`를 재사용하지만 full compose·실시간 조회·reference resolver·브라우저 UI·write는 평가하지 않는다.

## 정량 결과

| 지표 | 관측 |
|---|---:|
| 실제 Guard accepted / 사용 가능 / automatic pass | 모두 0/5 |
| 유효한 최종 blocked envelope | 5/5 |
| 실제 빈 HTML | 5/5 |
| 직접 평가 평균 | 2.00 / 5 |
| 수신 Dify 응답 | 5 |
| 전체 토큰 | 26,141 |
| metrics.durationSeconds | 18.9초 |
| case latency 합계 | 18.917초 |
| case latency p50 / p95 | 3.631초 / 4.827초 |
| 자동·직접 판정 불일치 | 0 |

aggregation: 각 축 0.2 가중평균 후 material_omission cap 3 적용, 전체 5개 case 평균. 각 case는 2.00이라 cap 추가 감점은 없다. percentileMethod: `nearest-rank-over-all-case-attempts`. cachedTokens/costUsd=null은 미관측이며 0이 아니다. calls는 수신 Dify 응답 수이지 내부 모델 step 수가 아니다.

## 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`.
evaluatorAgentFamily: `codex`.
evaluatorAgentModel: `current Codex agent; exact model ID not exposed`.
directRawOutputReview: `true`.
ltmLlmUsedAsJudge: `false`.
reviewerCount: `1`.
blindedReview: `false`.

같은 Codex 작업 에이전트가 이번 원문을 직접 읽고 점수를 결정했다. 기존 양식만 재사용했으며 자동 실패율이나 과거 점수를 정성점수로 변환하지 않았다. `score_human_case`와 `aggregate_human_reviews`는 checklist 완결성·상한·산술 검증에만 사용했다.

| 축 | 각 case / 평균 | 실제 관측 |
|---|---:|---|
| 요청 충족 | 1 | 초안·seed·확인 질문이 전달되지 않음 |
| 사실·근거 | 3 | 차단된 주장은 노출되지 않았으나 유용한 근거·불확실성 구분도 없음 |
| 계약·실행 | 2 | legal blocked envelope이나 삽입할 내용·복구 안내 없음 |
| 안전·불확실성 | 3 | write·완료 발명 없이 차단, 필요한 확인/open fact 없음 |
| 표현·렌더링 | 1 | 빈 HTML |

사실·근거 3점은 정확도 60%가 아니며, 차단된 plan의 사실을 승인한다는 뜻도 아니다.

## 배터리·case 특수 검토요소

각 case의 공통 checklist 32개와 특수 요소 3개 모두 applicability와 증거를 JSON에 남겼다(총 160+15개). 특수 요구는 모두 실제 결과에서 충족되지 않았다.

| case | 요청·특수 요구 | 실제 결과 |
|---|---|---|
| CMP2 | DL-9095 배경·범위·작업·DoD | 전체 내용 없음 |
| CMP3 | 정확 seed·확인 표시, p95 방향/원인 발명 금지 | seed·확인 표시 없음; 차단 전 내용은 노출되지 않음 |
| CMP6 | 담당 mention·2홉 100노드·문서 링크 | 세 요소 모두 없음 |
| CMP8 | DL-9090 목적·범위, 자식 실행 책임 분리 | 부모 본문 없음 |
| CMP9 | 현재 DL-9095 근거만으로 간결한 본문 | grounded 본문 없음 |

## 배터리별 실제 출력과 평가

5건 모두 실제 응답은 `status=blocked, payload={}, claims=[], citations=[], payload_claims=[], missing_inputs=[]`, warning은 `editor_assembler:invalid_plan`이다. projection은 `ok=false, needsInfo=false, error=[], html=""`이며 각 2.00점이다. 전체 앱의 오류 UI를 검증했다는 의미는 아니다.

차단 전 plan은 진단용으로만 분리했다. baseline commit `c7eca692d46e1562f7a4fadbe48c176003073449`의 assembler를 읽어 순수 함수를 메모리에서 재현했으며, 개발 중인 v2 코드로 과거 실패를 해석하지 않았다. frozen normalized source SHA-256은 `42c3983eb5beff78c876c8939d3798893d20630e572e89483e3d3c86abc03cb6`다.

| case | 첫 실패 | 추가 진단 — 실제 전달 결과로 채점하지 않음 |
|---|---|---|
| CMP2 | 없는 seed_html에 seed block 생성 | JSON 객체 전체 quote, enum 밖 '완료 조건'; 작업 heading 빠짐 |
| CMP3 | 사용자 seed 문장을 Jira source의 quote로 복제 | 원문은 intent에만 있음; 댓글을 4섹션 템플릿으로 바꾸고 p95 확인 질문 없음 |
| CMP6 | 없는 seed_html에 seed block 생성 | canonical ID를 source_id로 사용; 실제 mention·기준·링크 대신 코멘트 작성 방법 설명 |
| CMP8 | 없는 seed_html에 seed block 생성 | 부모 목적·범위는 일부 보존하나 작업/DoD를 본문 정리 행위로 표현 |
| CMP9 | 없는 seed_html에 seed block 생성 | 존재하지 않는 fixture.ticket.lineage 반복 참조, 중복 인용과 비어 있는 연속 heading |

CMP3는 새 측정값·높음/낮음을 명시적으로 발명하지 않았지만, “p95 변화와 원인”을 적으라는 제안에는 아직 확인되지 않은 관측을 전제하는 문제가 있다. 이를 사용자에게 노출된 수치 환각으로 과장하지 않았다.

## 자동 checker와 사람 판정 불일치

0건이다. 자동 checker는 빈 actual response를 실패로 처리했고 직접 판독도 사용 불가로 판단했다. `rendererFlaws=[]`는 ok=false 결과에서 렌더 검사를 통과했다는 의미가 아니다. 내부 plan의 부분 장점을 최종 결과 대신 채점하지 않았다.

## 실패·재시도·제한사항

원본 SHA·5개 request ID·source 특수 계약·160+15 checklist·산술 검증을 통과했다. 원본과 기존 리뷰는 보존했고 추가 원격 호출·fixture/checker 변경을 하지 않았다.

한 번의 탐색 실행, 단일 비맹검 구현 참여 평가자, 고정 합성 근거에 한정된다. 원격 모델 품질의 통계적 우열이나 전체 LTM 사용성·보안 통과를 주장할 수 없다. 이번 baseline은 활성화 근거가 아니며 후속 후보를 동일 범위에서 별도 평가해야 한다.
