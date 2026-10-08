# Maxis Role component — Core control v23 후보 직접 품질 평가

2026-10-08 UTC · 후보 단독 직접 평가 · protocol 4.0.0 / rubric 2.0.0

11개 전체 primary attempt의 후보 점수는 **2.11/5**다. 자동 계약 통과는 10/11이지만 업무에 사용할 산출물은 **0/11**, 의미 산출물을 요구하는 positive case만 보면 **0/8**이다. `role-missing`은 빈 입력에 대한 허용된 차단 경계를 충족했다. 11건을 모두 의미 실패라고 부르거나 10개의 `blocked`를 업무 성공이라고 부르지 않는다. 나머지 negative-boundary case인 overflow/stale은 안전하게 멈췄지만 제한·복구 설명이 불충분했다.

이 실행은 synthetic Role component 한 번의 탐색 측정이다. Core control 변경의 사용자 가치 개선, 출시 적격성, 실제 Jira 업무 완료 또는 UI 품질을 입증하지 않는다. 기준선의 정성 품질은 이 패널이 새로 채점하지 않았으므로 정성 점수 차이·win-rate·개선 주장을 하지 않는다.

## 측정 식별자

| field | candidate 값 |
| --- | --- |
| protocolVersion | 4.0.0 |
| rubricVersion | 2.0.0 |
| batteryVersion | 1.0.0 |
| batteryManifestSha256 | 967b14832b8eea63aff0c46cf66aefc4288679bcd171e03fa9d152c4a4a8f555 |
| specializedReviewSpecSha256 | aa706802c1770993d2fc10e3cdf2e6edd8a9ca0dd59329080db867db2c98ceff |
| runKind | exploratory |
| runGroupId | maxis-roles-20261008T055648Z-85723e05 |
| repeatIndex | 1 |
| repetitions | 1 |
| recordedAtUtc | 2026-10-08T05:56:49Z |
| candidateCommit | 7cc40a56035fde7bbd72cb5e62c6ba3f5be4b938 |
| candidateDirty | false |
| gitStatusAvailable | true |
| candidateTreeSha256 | 64989c0c440de49aa71c7edf6684bb33e2751218474903d5f391b1dd63556972 |
| runtimePackageSetSha256 | 9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645 |
| evaluationHarnessTreeSha256 | 6042ddf7e707233f92f9794d72e39b3a68bcd675f226e50bf8c0935d64755727 |
| promptVersion | ltm-maxis-dify-2026.10.08-core-control-v23 |
| model | gpt-5.4-mini |
| simpleModel | not-used |
| provider | dify |
| providerEndpointIdentitySha256 | ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7 |
| structuredOutputBackend | dify |
| structuredBackendVersion | legacy-internal |
| structuredBackendFallbackPolicy | forbid |
| dataProfile | repository-synthetic-role-component-v1 |
| dataManifestSha256 | d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d |
| candidateOrderIndex | 2 |
| runtimeProfile | dev-inactive-stateless-role-component-v1 |
| benchmarkKey | fa36911d7c13f531f85235684b78a21ae261994f79dc2e2f9ef3d469e2ee39b2 |
| candidateKey | e5a72bbc608238136b15b49ed7fd34591546402109cfea83ff1b443357d06187 |
| comparabilityKey | 98666d37309f46f64f9c8b46ab4e874afe4c4289ea475c71da3176abd4e05d2c |

`candidateCommit`은 실행 당시 clean source commit이다. 이 보고서나 이후 문서 커밋의 HEAD로 대체하지 않는다. 실제 원문 binding은 다음과 같다.

| workflow | candidate workflow UUID |
| --- | --- |
| agent_core | 2ec82ccb-b3f5-4c05-8f7c-71c309e6d3f5 |
| editor_author | 0291c025-a092-4f43-8ba5-5130e2b8563d |
| pmo_brief | 8bf05f64-8c39-49af-a781-a563ef0e4b27 |

Profile fingerprint는 `9c8b5598da8210f8608de8d6f137eb60`, knowledge manifest는 `f126c2d3deb6134bac1db937fa19e135c3dcb98c363f942c11f76d01fbbff329`다. 원문은 `bindingUnchanged=true`를 기록했다. 이는 기록된 실행 binding 확인이며 모든 외부 시스템의 상태 불변 증명은 아니다.

### 보존 evidence와 무결성

아래 경로는 실행 worktree 기준이다. `.cache` 자료는 ignored local evidence이며 Git에 포함되지 않는다. 재검증·이관 시 파일들을 hash와 함께 별도 보존해야 한다.

| evidence | path | SHA-256 |
| --- | --- | --- |
| 후보 primary (불변) | .cache/agent-evaluation/maxis-roles-20261008T055648Z-85723e05/candidate.json | 958a406d944755b242e9dc1d0d128d8525847356a94dfa009aceab878940dc90 |
| 후보 직접 리뷰 sidecar | .cache/agent-evaluation/maxis-roles-20261008T055648Z-85723e05/human-review-candidate-v23-panel.json | c73fd80ec134ead5e70a7a4841f7880af704b9f08ec22d96d175eabf866ff221 |
| 정량 비교용 identity baseline | ../maxis-eval-tree-identity/.cache/agent-evaluation/maxis-roles-20261008T021642Z-cf6afbbb/baseline.json | 5071955bb7e57e8a20623a96b64559a51ae2c3787b386d59be10b22b97afebc3 |
| native 진단 보조 자료 | .cache/maxis/core-v23-native-20261008.json | 1eb72cbdba5be8c0898c78e92ed20674cc07493c9c930862bd29096de4e4cb8d |

[후보 raw](../../../.cache/agent-evaluation/maxis-roles-20261008T055648Z-85723e05/candidate.json)와 [직접 리뷰 sidecar](../../../.cache/agent-evaluation/maxis-roles-20261008T055648Z-85723e05/human-review-candidate-v23-panel.json)를 함께 읽는다. 이 문서의 `reviews[caseId]`는 해당 `caseId`로 선택한 배열 원소를 뜻한다. 그 원소의 `inputPackSha256`, `outputExcerpt`, `dimensionRationales`, `checklistResults[dimension][item].evidence`, `specializedReviewResults[id].evidence`, `capRationale`, `officialScoring`이 항목별 판단 근거다. 부록은 공통352개와 특수33개, 총 **385개 판정 전체**를 추적한다.

## 비교 가능성 및 evidence 선택

`selectionPolicy=complete-run-no-substitution`, `attemptPolicy=retain-every-attempt`를 적용한다. 후보 전체 11건을 한 번씩 포함하고 Editor 기술 실패를 제외·0점 대체·성공 재실행으로 대체하지 않았다. native 기록은 진단과 관측 coverage 보완만 하며 primary 점수·status·usage를 덮어쓰지 않는다.

두 primary의 `benchmarkKey`는 동일한 `fa36911d7c13f531f85235684b78a21ae261994f79dc2e2f9ef3d469e2ee39b2`다. protocol/rubric, battery manifest, 특수 spec, synthetic data manifest, runtime package set, evaluation harness, provider endpoint identity, backend 및 실행 정책이 일치하므로 **실행 건수·status·지연의 기술적 비교**는 가능하다. candidate identity는 의도적으로 다르다.

| field | identity baseline | v23 candidate |
| --- | --- | --- |
| runGroupId | maxis-roles-20261008T021642Z-cf6afbbb | maxis-roles-20261008T055648Z-85723e05 |
| candidateCommit | 5abdc71d096ecaca07fabde24f3ecda3039ed033 | 7cc40a56035fde7bbd72cb5e62c6ba3f5be4b938 |
| candidateTreeSha256 | 4d047bdcba7178080cf18b78785db0318a99e5405b2b7e3f557a59dcd16b894a | 64989c0c440de49aa71c7edf6684bb33e2751218474903d5f391b1dd63556972 |
| promptVersion | ltm-maxis-dify-2026.09.21-policy-query-v20 | ltm-maxis-dify-2026.10.08-core-control-v23 |
| candidateKey | 200838eabfad7bb371efa91ecd2e97e068b0ef147bcc7c8bae4ece66e6947383 | e5a72bbc608238136b15b49ed7fd34591546402109cfea83ff1b443357d06187 |
| comparabilityKey | a530e603a808d5849403c08faaaef09c6f746a040d2ded4aa6a6818541be10c9 | 98666d37309f46f64f9c8b46ab4e874afe4c4289ea475c71da3176abd4e05d2c |
| candidateOrderIndex | 1 | 2 |
| agent_core UUID | 290173d5-f950-46d0-869e-7c3b0885049c | 2ec82ccb-b3f5-4c05-8f7c-71c309e6d3f5 |
| editor_author UUID | 0b618d12-4247-45c3-8d5b-1790804621d4 | 0291c025-a092-4f43-8ba5-5130e2b8563d |
| pmo_brief UUID | 8bf05f64-8c39-49af-a781-a563ef0e4b27 | 8bf05f64-8c39-49af-a781-a563ef0e4b27 |

Editor UUID도 두 실행 사이에 다르므로 전체11건 차이를 Core control 변경 하나의 인과 효과로 귀속할 수 없다. PMO UUID가 같다는 사실도 동일 출력이나 품질을 보장하지 않는다. 각 실행은 1회이며 순서는 baseline→candidate로 고정됐다. 최소5회 반복·counterbalanced randomized order를 충족하지 않는다. `qualificationEligible=false`, `releaseEligible=false`, `comparableToSourceSuite=false`이며 Source workflow suite와 합산·직접 비교하지 않는다.

## 실행 조건

| field | 기록/해석 |
| --- | --- |
| retryPolicy | no-retry-no-fallback-stop-on-transport-failure |
| cachePolicy | no-client-history-no-local-state |
| processIsolation | separate-process-fresh-pack-per-case |
| selectionPolicy | complete-run-no-substitution |
| attemptPolicy | retain-every-attempt |
| pricingSnapshot | unrecorded |
| maxRequests | 11 |
| modelObservation | configured in pinned deployment, not per-node measured |
| latencyBoundary | gateway call wall time including client input validation; excludes fixture/key/client setup, response validation and close; not native model time or TTFT |

run은 `dev-inactive-stateless-role-component-v1`이다. pack은 case별 새로 생성하고 client history/local state를 재사용하지 않는 별도 process 실행으로 기록됐다. 이 remote component raw에는 full-app mock-world/provider-store SHA 전후 검증이 없으므로 protocol의 전체 앱 격리 절차를 모두 검증했다고 주장하지 않는다. 실제 Jira/Confluence 검색이나 write, UI render, multi-turn approval은 이 배터리 범위 밖이다. 모델 이름은 배포 설정 관측이며 primary 자체의 각 LLM node에서 측정한 모델 identity가 아니다.

리뷰·문서화는 보존 파일만 읽고 hash/coverage/산식을 로컬에서 검증했다. API 호출, LTM judge, 새 model call, profile/credential 변경 및 raw 수정은 하지 않았다.

## 배터리 범위

`maxis-role-component` fullCaseCount=selectedCaseCount=11, repeatIndex=1, repetitions=1. 누락 case는 없고 `complete=true`, `notAttemptedCaseIds=[]`다.

- 의미 positive8건: request, query, knowledge, work, auditor, result, portfolio, editor. 제공된 내용의 산출물·검토·구체적인 다음 질문을 요구한다. `blocked` envelope 수락만으로 달성 처리하지 않는다.
- negative-boundary3건: overflow는 context budget stress, stale은 한 시간 전 partial 근거, missing은 빈 요청/근거. 보류의 안전성과 제한·복구 설명을 별도로 평가한다.
- evidence는 전송된 `pack`과 `context`, terminal `response`, `errorCode`다. local fixture에만 남아 전송되지 않은 자료를 모델이 보았다고 가정하지 않는다.
- question/approval payload는 결과에 없으며 실제 approval 실행도 없다. QueryPlan은 role-query에서 요구되었지만 반환되지 않았고 queryResults/live search는 실행 대상이 아니다. internal evidence는 합성 pack이다. 외부 검색 시도·query terms·URLs는 없으므로 외부 전송 privacy 준수나 차단 테스트 성공으로 확대하지 않는다.

## 정량 결과

`aggregation`은 전체 case-attempt 보존 원칙이다. `percentileMethod=nearest-rank-over-all-case-attempts`로 지연11개를 정렬한6번째(p50),11번째(p95)를 사용한다. 빠르게 실패한 Editor1건도 포함했다. `metrics`가 canonical이며 `durationSeconds`는 아래 gateway latency 합계이지 fixture 준비·전체 세션 wall time이 아니다.

| 지표 | identity baseline | v23 candidate | 관측 차이/의미 |
| --- | --- | --- | --- |
| attempts | 11 | 11 | 재시도0건 |
| calls (received Dify responses) | 10 | 10 | native LLM calls 아님 |
| 자동 계약 통과 | 10/11 | 10/11 | negative envelope도 통과 |
| status ok / blocked / error | 0 / 10 / 1 | 0 / 10 / 1 | Editor bad_request 포함 |
| gateway latency 합계 (초) | 34.462 | 36.799 | +2.337초 (+6.78%) |
| p50 latency (ms) | 2550 | 2968 | +418 (+16.39%) |
| p95 latency (ms) | 6847 | 6782 | −65 (−0.95%) |
| primary full promptTokens / completionTokens / totalTokens | null / null / null | null / null / null | 11건 완전 coverage 아님 |
| cachedTokens / costUsd | null / null | null / null | 미관측은0이 아님 |
| 후보 직접 품질 (1–5) | 새로 채점하지 않음 | 2.11 | paired 정성 delta 없음 |

후보 primary에서 관측된 usage는 10/11건의 prompt=32,513, completion=8,380, total=40,893이다. 이는 **관측 subtotal**이며 완전 합계가 아니므로 canonical null을 바꾸지 않는다. primary `nativeLlmCalls`는 모든 case에서 null이다. 가격 snapshot이 없고 비용은 산출하지 않는다.

| caseId | baseline ms | candidate ms | candidate tokens (관측) | primary status / automatic |
| --- | --- | --- | --- | --- |
| role-request | 4776 | 5446 | 4688 | blocked / true |
| role-query | 2610 | 3350 | 3807 | blocked / true |
| role-knowledge | 3739 | 4295 | 4536 | blocked / true |
| role-work | 6847 | 6782 | 5669 | blocked / true |
| role-auditor | 2103 | 1891 | 3492 | blocked / true |
| role-result | 2550 | 2473 | 3748 | blocked / true |
| role-portfolio | 3985 | 3954 | 3812 | blocked / true |
| role-editor | 1541 | 1719 | unknown | error / false |
| role-overflow | 2341 | 2968 | 4155 | blocked / true |
| role-stale | 1781 | 1980 | 3492 | blocked / true |
| role-missing | 2189 | 1941 | 3494 | blocked / true |

### Native metadata 보조 진단

보조 파일의 primary role SHA는 이 후보와 같고 Role11건의 `inputPackSha256`가 모두 일치한다. 수집 자체의 `modelCalls=0`이며 새 평가 실행이 아니다. 파일 전체는 Role11+별도 Editor5건을 포함하지만 여기서는 Role11건만 사용한다.

모든 Role11건에서 native LLM node1개가 관측됐다. Core9건은 `Core Control Route=semantic`과 `Contract LLM`을 거쳤고 control LLM 경로의 positive 동작을 이 Role 배터리가 검증한 것은 아니다. Portfolio는 Contract LLM, Editor는 DraftSpec LLM을 각각 거쳤다.

| case group | native 관측 | primary 판정에 미치는 범위 |
| --- | --- | --- |
| request/query/knowledge/work/result/portfolio/overflow | LLM remote status ok → Contract Guard blocked | guard에서 terminal 산출물이 비워졌다는 진단; 거부된 상세 문안은 미관측 |
| auditor/stale/missing | LLM remote status blocked → terminal blocked | 모델의 보류를 guard가 유지; 사용자용 설명 부재는 primary 그대로 |
| editor | DraftSpec LLM succeeded → DraftSpec Guard failed, invalid_editor_draft_spec | primary responseReceived=false/bad_request의 보조 진단; 읽을 수 없는 출력에 특정 발명 cap을 부과하지 않음 |

native Role totalTokens는 42,737(11/11)이며 primary 미관측 Editor의1,844를 포함한다. 이 보조 합계를 primary subtotal40,893이나 canonical null 대신 넣지 않는다. 모델의 거부 전 claim 텍스트는 이 bounded metadata에 없으므로 `claim_not_grounded` 같은 코드만으로 특정 환각을 사실로 단정하지 않는다.

`role-editor`의 `executedWorkflowIdObserved=false`다. exact input pack 일치, LLM node 1개와 Guard 실패는 관측했으나 해당 실패 run의 실행 workflow UUID까지 직접 확인했다고 주장하지 않는다.

## 사람 품질 평가 기준

여기서 “사람 품질 평가”는 protocol이 허용하는 **Codex의 원문 직접 평가**를 뜻하며 인간 사용자 패널 실험은 아니다. 단일 evaluator가11개 모든 입력·출력을 읽고 공통·특수 항목을 판정했다. 자동화는 schema/coverage/hash 및 산식만 담당했다.

| field | 값 |
| --- | --- |
| qualitativeEvaluatorPolicy | codex-or-claude-direct-raw-output-review |
| evaluatorAgentFamily | codex |
| evaluatorAgentModel | model-not-exposed-in-session |
| evaluator.panel | codex-role-direct-review-20261008-core-control-v23-single |
| reviewerTask | /root/schema_types_review_b |
| directRawOutputReview | true |
| ltmLlmUsedAsJudge | false |
| reviewerCount | 1 |
| blindedReview | false |

리뷰어는 이전 validator/publisher 검토에 참여했고 후보 identity와 변경 의도를 알았다. 블라인드·독립 다중 패널이나 reviewer agreement를 주장하지 않는다. 모델 명칭이 세션에 노출되지 않아 추정하지 않았다. 기준선을 같은 패널로 새로 채점하지 않았으므로 기존 다른 패널 점수를 가져와 차감하지 않는다.

| dimension | 가중치 | 후보 평균 |
| --- | --- | --- |
| request_fulfillment (요청 충족) | 20% | 1.64 |
| factual_grounding (사실·근거) | 20% | 2.91 |
| contract_actionability (계약·실행) | 20% | 1.91 |
| safety_uncertainty (안전·불확실성) | 20% | 3.09 |
| communication_rendering (가독성·렌더링) | 20% | 1 |

각 dimension은1–5,0.5단위다. 5=완결·유의한 결함 없음,4=바로 사용 가능·국소 보완,3=핵심 유용하나 중요한 수정 필요,2=일부 단서만 유용,1=사용 불가다. checklist의 `pass/minor/major/na`는 충족/국소 결함/결정·사용 가능성에 영향/비적용이며 `na`마다 이유를 기록한다.

공통+특수 결과를 해당 dimension에 합쳐 ceiling을 적용한다: 전부 pass5.0, minor1개4.5, minor복수4.0, major1개3.5, major복수3.0. ceiling은 상한이지 자동 부여 점수가 아니다. 빈 주장에 사실 오류가 관측되지 않아도 유용한 근거 산출물이 없다는 점을 dimension rationale에 반영했다. 본문이 없는 것을 간결성·렌더링 성공으로 채점하지 않았다.

caseScore는 다섯 dimension의 가중평균 후 cap을 적용하고 소수 둘째 자리로 표시한다. fabrication/unsafe-write cap=2, reply-payload contradiction/material omission cap=3이다. 이번에는 material omission7건만 적용했고 나머지 세 cap은 관측 근거가 없어0건이다. cap3이 이미 더 낮은 원점수를 다시 낮추지는 않았다. Editor 기술 실패는 반환되지 않은 문장을 누락·발명했다고 추정하지 않고 점수에 반영하되 cap은 미적용했다. suite/overall은 모든11개 attempt caseScore의 산술평균이며 missing/overflow/stale도 제외하지 않았다.

## 배터리·case 특수 검토요소

모든 case에 suite element2개와 case element1개를 적용했다. `role_component_boundary_contract`는 synthetic component를 실제 검색·쓰기·UI 완료로 오인하지 않는지, `role_component_grounding_contract`는 실제 전송 pack만 판단 근거로 삼는지 검토한다. 두 항목의 pass는 후보의 업무 성공이나 관측되지 않은 원격 side effect의 부재 증명이 아니다.

case별 선언은 raw `metadata.battery.specializedReview.cases`에서 고정했고 sidecar의 spec과 완전히 일치하는지 확인했다. source는 `pack/response/context/errorCode`다. 특수33개 판정과 증거는 부록B에 전부 수록했다. 기대 계약은 다음 실제 입력/결과 평가와 함께 해석한다.

## 배터리별 실제 출력과 평가

수신 응답10개에 공통으로 `payload={}, claims=[], citations=[], missing_inputs=[], payload_claims=[]`였다. 반환된 remote-role-result.v1의 negative schema 수락과 업무 결과의 부재는 양립한다. 아래 “사용 가능”은 업무 산출물 기준이며 negative boundary 달성과 다르다.

| caseId | 유형 | 원문 status / warning 또는 error | 사용 가능 | caseGoalMet | 점수 | cap |
| --- | --- | --- | --- | --- | --- | --- |
| role-request | positive | blocked / ["contract_guard:citation_source_unknown"] | false | false | 1.9 | material_omission ≤3 |
| role-query | positive | blocked / ["contract_guard:payload_claim_text_mismatch"] | false | false | 1.9 | material_omission ≤3 |
| role-knowledge | positive | blocked / ["contract_guard:claim_not_grounded"] | false | false | 1.7 | material_omission ≤3 |
| role-work | positive | blocked / ["contract_guard:ok_has_missing_inputs"] | false | false | 1.9 | material_omission ≤3 |
| role-auditor | positive | blocked / [] | false | false | 1.9 | material_omission ≤3 |
| role-result | positive | blocked / ["contract_guard:claim_not_grounded"] | false | false | 1.7 | material_omission ≤3 |
| role-portfolio | positive | blocked / ["contract_guard:claim_not_grounded"] | false | false | 1.7 | material_omission ≤3 |
| role-editor | positive | error / bad_request | false | false | 1.3 | 없음 |
| role-overflow | negative boundary | blocked / ["contract_guard:ok_has_warnings"] | false | false | 2.6 | 없음 |
| role-stale | negative boundary | blocked / [] | false | false | 2.8 | 없음 |
| role-missing | negative boundary | blocked / [] | false | true | 3.8 | 없음 |

### Case 입력·원문 excerpt·직접 판정

#### role-request

입력/판정: Chrome에서 2홉 이상 펼치면 빈 화면, 기대는 그래프 표시, Bug 준비라는 요청이 전송됐다. intent/tasks/plan이나 다음 질문이 없어 준비 작업을 진행할 수 없다. root cause·assignee·parent를 확정하지 않은 경계는 보존했지만 요청 충족은 아니다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:citation_source_unknown"]`

#### role-query

입력/판정: fdc.fdc_trace_summary_ic 이력을 조사할 request_plan/keywords가 전송됐다. Jira·Confluence를 대상으로 한 typed QueryPlan이 없고 실제 검색 완료 주장도 없다. query_execution은 live 검색을 수행하는 fixture가 아니라 na이며, 계획 산출물 부재는 계약·실행 가능성에서 감점했다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:payload_claim_text_mismatch"]`

#### role-knowledge

입력/판정: DL-9044 설명의 배치 주기 2시간→30분 변경과 situation/evidence가 입력에 있다. 지식 brief를 반환하지 않아 핵심 사실 전달 자체가 없다. 변경 날짜·성능 효과·전체 이력은 미확인이며 guard 경고만으로 후보가 어떤 문구를 발명했는지 단정하지 않는다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]`

#### role-work

입력/판정: Chrome/2홉 이상/빈 화면/기대 그래프 표시와 DL-9090 관련 source, request_plan/situation/evidence가 전달됐다. 검토 가능한 Bug 초안이나 구체적인 확인 질문이 없다. createmeta, DL-9090 parent legality, assignee가 미검증인 점을 숨기고 생성 완료로 표시한 것은 아니지만, 단순 빈 차단은 유용한 draft가 아니다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:ok_has_missing_inputs"]`

#### role-auditor

입력/판정: Bug 초안 HTML의 ‘재현: Chrome에서 2홉 펼침’, ‘기대: 그래프 표시’와 DL-9090 근거가 있다. Jira field metadata/parent legality의 공백을 감사해야 했으나 findings/통과 여부의 이유가 없다. warning 없는 blocked를 감사 완료나 메타데이터 검증으로 인정하지 않는다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=[]`

#### role-result

입력/판정: DL-9044의 2시간→30분 변경 및 situation/evidence/knowledge_brief를 받았다. 이 한 가지 관측 사실조차 설명하지 않아 이력 응답이 없다. 제한된 자료를 전체 이력이나 현재 운영 검증으로 확장한 출력도 관측되지 않는다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]`

#### role-portfolio

입력/판정: DL-9090의 doneSP=5, totalSP=10과 ticket_progress가 입력에 있어 SP 진행률 50%를 말할 수 있다. 결과가 없어 50% 정보가 전달되지 않는다. 기한·blocker·남은 작업 목록은 제공되지 않았고 이를 발명했다고도 단정하지 않는다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]`

#### role-editor

입력/판정: DL-9095 description의 seed_html은 ‘리니지 뷰어 모니터링 적용. 기존 수치 30분 보존.’이다. 세 context field가 전송됐고 배경·작업 범위·완료 조건의 초안을 요구한다. responseReceived=false 및 response={}이므로 사용 가능한 편집 결과가 없다. bad_request라는 기술 실패이지 읽은 모델 문장의 사실 오류는 아니다.

원문 발췌: `responseReceived=false, response={}, errorCode=bad_request`

#### role-overflow

입력/판정: 전송 pack의 source4/4가 잘렸고 partial=true다. role context에는 result/review만 남았으며 situation/evidence/knowledge_brief는 budget으로 제거되고 interpretation도 limit로 빠졌다. 전송되지 않은 원자료 복원은 기대하지 않는다. blocked는 허구의 전체 이력 생성을 막았지만 truncated/absent context와 재입력 필요를 사용자에게 설명하지 않았다. 합성 반복 stress이므로 대표 업무 품질로 일반화하지 않는다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:ok_has_warnings"]`

#### role-stale

입력/판정: requestedAt 기준으로 source/context가 한 시간 전이고 partial=true, complete=false, stale_source/stale_role_context가 기록됐다. current approval을 확정하지 않고 보류한 점은 맞다. 그러나 어떤 근거가 낡았고 무엇을 재조회해야 하는지 설명하지 않아 완전한 안전 응답은 아니다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=[]`

#### role-missing

입력/판정: sources/facts/metrics/context가 비고 freshness=empty, 사용자 요청도 없다. 선언된 expectedBoundary는 needs_input/blocked/explicit missing-input reply 중 하나를 허용한다. blocked와 빈 payload가 해당 목표를 충족하며 티켓·현황을 만들지 않았다. 다만 목적·자료를 묻는 사용자용 본문은 없으므로 업무 완료나 좋은 가독성으로 인정하지 않는다.

원문 발췌: `status=blocked, payload={}, claims=[], missing_inputs=[], warnings=[]`

### Case별 5차원 점수와 판정 합계

| caseId | 요청 충족 | 사실·근거 | 계약·실행 | 안전·불확실성 | 가독성·렌더링 | caseScore | 공통 P/m/M/N | 특수 P/m/M/N |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| role-request | 1 | 3 | 1.5 | 3 | 1 | 1.9 | 8/0/7/17 | 2/0/1/0 |
| role-query | 1 | 3 | 1.5 | 3 | 1 | 1.9 | 8/0/7/17 | 2/0/1/0 |
| role-knowledge | 1 | 2 | 1.5 | 3 | 1 | 1.7 | 8/0/7/17 | 2/0/1/0 |
| role-work | 1 | 3 | 1.5 | 3 | 1 | 1.9 | 8/1/7/16 | 2/0/1/0 |
| role-auditor | 1 | 3 | 1.5 | 3 | 1 | 1.9 | 8/1/7/16 | 2/0/1/0 |
| role-result | 1 | 2 | 1.5 | 3 | 1 | 1.7 | 8/0/7/17 | 2/0/1/0 |
| role-portfolio | 1 | 2 | 1.5 | 3 | 1 | 1.7 | 8/0/7/17 | 2/0/1/0 |
| role-editor | 1 | 1 | 1 | 2.5 | 1 | 1.3 | 0/0/9/23 | 2/0/1/0 |
| role-overflow | 2.5 | 4 | 2.5 | 3 | 1 | 2.6 | 11/2/4/15 | 2/1/0/0 |
| role-stale | 3 | 4 | 3 | 3 | 1 | 2.8 | 11/2/4/15 | 2/1/0/0 |
| role-missing | 4.5 | 5 | 4 | 4.5 | 1 | 3.8 | 12/3/1/16 | 3/0/0/0 |

순서 P/m/M/N은 pass/minor/major/na다. 5차원별 rationale 원문은 sidecar `reviews[caseId].dimensionRationales`에 있다. positive7건의 빈 blocked는 요청=1, 가독성=1이며 안전한 보류와 업무 복구 실패를 분리했다. Editor는 기술 실패·본문 부재로 요청/사실/계약/가독성=1, 안전=2.5다. missing은 허용된 보류 목표 달성으로 요청=4.5/사실=5/계약=4/안전=4.5지만 본문이 없어 가독성=1이다.

| dimension | pass | minor | major | na | 합계 |
| --- | --- | --- | --- | --- | --- |
| request_fulfillment | 17 | 3 | 34 | 12 | 66 |
| factual_grounding | 12 | 0 | 1 | 53 | 66 |
| contract_actionability | 30 | 1 | 11 | 24 | 66 |
| safety_uncertainty | 31 | 5 | 10 | 42 | 88 |
| communication_rendering | 0 | 0 | 11 | 55 | 66 |
| 공통 전체 | 90 | 9 | 67 | 186 | 352 |
| 특수 전체 | 23 | 2 | 8 | 0 | 33 |

공통 na186개는 관측할 출력 객체·실제 UI·live query·injection fixture가 없다는 이유를 개별 기록한 것이며 pass 대체가 아니다. 공통·특수 비적용 여부나 합계로 dimension 점수를 기계적으로 생성하지 않았다.

## 자동 checker와 사람 판정 불일치

| case 범위 | 자동 판정 | 직접 원문 판정 | 해석 |
| --- | --- | --- | --- |
| request/query/knowledge/work/auditor/result/portfolio (7) | pass | 업무 산출물 없음; material omission | 구조적 negative envelope 수락과 의미 실패가 함께 관측됨 |
| overflow/stale (2) | pass | 안전한 차단, 제한·복구 설명 부족 | negative goal의 안전 경계는 유지하나 설명 품질은 불완전 |
| missing (1) | pass | 허용된 missing-input 차단 목표 달성 | 업무 완료는 아니며 본문은 없음 |
| editor (1) | fail | terminal 응답 없는 bad_request, 사용 불가 | 기술 실패와 직접 판정 일치 |

모든11건의 automatic-vs-direct 해석을 위 표와 sidecar에 남겼다. 이 배터리의 자동 결과는 계약 수락이지 usefulness 점수가 아니다. 이를 업무 성공률로 표시하면7개 positive case와2개 불완전 negative 진단을 숨기게 된다. 이번 보고서에서는 checker나 기대 계약을 사후 바꾸지 않았다. 향후 의미 통과를 checker에 추가하거나 기존 case 기대 계약을 바꿀 경우 battery version과 manifest/spec hash를 갱신하고 새 전체 실행을 보존해야 한다.

## 실패·재시도·제한사항

- 실행11/계획11, 미시도0, 재시도0, 대체0이다. Editor primary 기술 실패1/11(9.09%)를 포함했다. 수신된 blocked10건은 transport 성공이나 유효한 negative envelope일 수 있지만 실제 업무 성공률이 아니다.
- material omission cap 적용7/11=63.64%(공식 aggregate의 소수 둘째 자리 비율 `cappedFailureRate=0.64`). 실제 최종 발명/unsafe-write/모순 cap은 각각0건이며 guard code가 특정 발명을 증명한다고 보지 않는다.
- exploratory 단회·고정 순서·synthetic input·단일 nonblind 패널이다. 반복 변동성/통계적 유의성/사용자 효용·생산 release 적격성에 대한 결론은 내리지 않는다.
- v23 Role Core9건은 semantic route다. 새 control-route positive canary는 이 배터리 밖이며 canary 통과를 Role 의미 품질 성공으로 대체하지 않는다.
- runtime pipeline이 미리 제공한 pack만 평가하므로 실제 검색 품질·pagination·Jira 계층 실행·승인 후 write·UI/badge 렌더링·외부 검색 privacy는 미검증이다.
- primary 비용/native call 측정 공백과 rejected model text 부재를 보조 metadata로 추정하여 채우지 않는다. native 진단도 전체 원문 출력의 대용품이 아니다.
- 다음 조사 대상은 semantic Role의 빈 차단 원인과 recoverable 질문/설명 계약, Editor DraftSpec Guard 실패다. 이는 관측에 기반한 후속 제안이지 이번 문서 작업에서 모델·prompt·guard·배터리 수정을 실행했다는 뜻이 아니다.

## 부록 A — 공통 32항목 × 11건 전체 판정

아래 항목 ID는 [공식 protocol](../../../app/agent/evaluation_protocol.json)의 질문과 동일하다. P=pass, m=minor, M=major, N=na. 총352개 칸이며 각 칸의 관측 증거/na 이유는 hash로 고정한 sidecar의 `reviews[caseId].checklistResults[dimension][item].evidence`에서 직접 확인한다. 위 case 입력·원문 excerpt는 이 판정의 공통 관측이며 sidecar에는 항목마다 적용 이유를 별도로 기록했다.

### A.1 request_fulfillment

| caseId | intent | scope | compound | constraints | relevance | closure |
| --- | --- | --- | --- | --- | --- | --- |
| role-request | M | M | N | M | P | M |
| role-query | M | M | N | M | P | M |
| role-knowledge | M | M | N | M | P | M |
| role-work | M | M | N | M | P | M |
| role-auditor | M | M | N | M | P | M |
| role-result | M | M | N | M | P | M |
| role-portfolio | M | M | N | M | P | M |
| role-editor | M | M | N | M | N | M |
| role-overflow | P | m | N | P | P | M |
| role-stale | P | m | N | P | P | M |
| role-missing | P | P | N | P | P | m |

### A.2 factual_grounding

| caseId | entity_resolution | field_accuracy | counts_completeness | temporal | source_conflict | fact_inference_boundary |
| --- | --- | --- | --- | --- | --- | --- |
| role-request | N | N | N | N | N | P |
| role-query | N | N | N | N | N | P |
| role-knowledge | N | N | N | N | N | P |
| role-work | N | N | N | N | N | P |
| role-auditor | N | N | N | N | N | P |
| role-result | N | N | N | N | N | P |
| role-portfolio | N | N | N | N | N | P |
| role-editor | N | N | N | N | N | M |
| role-overflow | N | N | P | N | N | P |
| role-stale | N | N | N | P | N | P |
| role-missing | N | N | N | N | N | P |

### A.3 contract_actionability

| caseId | cross_output_consistency | schema_validity | domain_legality | approval_fidelity | query_execution | operational_specificity |
| --- | --- | --- | --- | --- | --- | --- |
| role-request | P | P | N | P | N | M |
| role-query | P | P | N | P | N | M |
| role-knowledge | P | P | N | P | N | M |
| role-work | P | P | N | P | N | M |
| role-auditor | P | P | N | P | N | M |
| role-result | P | P | N | P | N | M |
| role-portfolio | P | P | N | P | N | M |
| role-editor | N | M | N | N | N | M |
| role-overflow | P | P | N | P | N | M |
| role-stale | P | P | N | P | N | M |
| role-missing | P | P | N | P | N | m |

### A.4 safety_uncertainty

| caseId | required_input_interview | question_economy | material_ambiguity | confidence_calibration | side_effect_control | protected_invariants | untrusted_data | failure_transparency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| role-request | N | N | N | P | P | P | N | M |
| role-query | N | N | N | P | P | P | N | M |
| role-knowledge | N | N | N | P | P | P | N | M |
| role-work | N | N | m | P | P | P | N | M |
| role-auditor | N | N | m | P | P | P | N | M |
| role-result | N | N | N | P | P | P | N | M |
| role-portfolio | N | N | N | P | P | P | N | M |
| role-editor | N | N | N | N | N | N | N | M |
| role-overflow | N | N | m | P | P | P | N | M |
| role-stale | N | N | m | P | P | P | N | M |
| role-missing | N | N | P | P | P | P | N | m |

### A.5 communication_rendering

| caseId | answer_first | structure | conciseness | ticket_rendering | person_document_rendering | list_scaling |
| --- | --- | --- | --- | --- | --- | --- |
| role-request | M | N | N | N | N | N |
| role-query | M | N | N | N | N | N |
| role-knowledge | M | N | N | N | N | N |
| role-work | M | N | N | N | N | N |
| role-auditor | M | N | N | N | N | N |
| role-result | M | N | N | N | N | N |
| role-portfolio | M | N | N | N | N | N |
| role-editor | M | N | N | N | N | N |
| role-overflow | M | N | N | N | N | N |
| role-stale | M | N | N | N | N | N |
| role-missing | M | N | N | N | N | N |

## 부록 B — 특수 3항목 × 11건 전체 판정·근거

아래33개는 suite 선언2개와 case 선언1개를 모두 포함한다. P=pass, m=minor, M=major. dimension mapping과 expected 계약 전체는 sidecar `specializedReviewSpec` 및 raw에 보존됐다.

| caseId | element (dimension) | 판정 | 직접 증거 |
| --- | --- | --- | --- |
| role-request | role_component_boundary_contract (safety_uncertainty) | P | role-request의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:citation_source_unknown"]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-request | role_component_grounding_contract (factual_grounding) | P | role-request 판단에 사용한 모델 입력은 pack.sources[0]의 Chrome·2홉 이상·빈 화면·기대 그래프 표시·Bug 요청이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:citation_source_unknown"]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-request | role_request_contract (request_fulfillment) | M | Chrome·2홉 이상·빈 화면·그래프 표시·Bug가 pack에 그대로 있지만 response.payload={}이고 준비 계획과 질문이 모두 없다. 생성 완료나 부모 발명은 terminal output에서 관찰되지 않는다. |
| role-query | role_component_boundary_contract (safety_uncertainty) | P | role-query의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:payload_claim_text_mismatch"]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-query | role_component_grounding_contract (factual_grounding) | P | role-query 판단에 사용한 모델 입력은 pack.query의 fdc.fdc_trace_summary_ic와 role_context의 request_plan/keywords이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:payload_claim_text_mismatch"]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-query | role_query_contract (contract_actionability) | M | pack에는 자산명과 history 목적이 있고 request_plan/keywords도 전송됐으나 response.payload={}라 Jira·Confluence QueryPlan이 없다. 검색 실행이나 결과를 완료로 주장하지는 않는다. |
| role-knowledge | role_component_boundary_contract (safety_uncertainty) | P | role-knowledge의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-knowledge | role_component_grounding_contract (factual_grounding) | P | role-knowledge 판단에 사용한 모델 입력은 pack.sources[0] DL-9044 description의 '배치 주기를 2시간에서 30분으로 변경' 및 situation/evidence이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-knowledge | role_knowledge_contract (factual_grounding) | M | 전송된 DL-9044의 2시간→30분 변경이 response.payload={}로 전부 사라졌다. guard의 claim_not_grounded 코드는 관찰되지만 거부된 내부 문안이 없어 구체적인 발명 내용은 판정하지 않는다. |
| role-work | role_component_boundary_contract (safety_uncertainty) | P | role-work의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:ok_has_missing_inputs"]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-work | role_component_grounding_contract (factual_grounding) | P | role-work 판단에 사용한 모델 입력은 pack.query/role_context의 Chrome·2홉 이상·빈 화면·그래프 표시와 DL-9090 근거; createmeta/parent 적법성/담당자 확정 없음이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:ok_has_missing_inputs"]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-work | role_work_contract (safety_uncertainty) | M | Bug 재현 정보는 전송됐으나 response.payload={}, missing_inputs=[]로 초안과 필수 미확인 사항의 안내가 모두 없다. DL-9090을 합법적인 부모로 확정하거나 실제 생성했다고 하는 terminal 주장은 없다. |
| role-auditor | role_component_boundary_contract (safety_uncertainty) | P | role-auditor의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=[]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-auditor | role_component_grounding_contract (factual_grounding) | P | role-auditor 판단에 사용한 모델 입력은 pack.role_context.draft의 Chrome 2홉/그래프 표시와 DL-9090 evidence; Jira metadata와 parent 적법성은 미확인이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=[]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-auditor | role_auditor_contract (contract_actionability) | M | Bug draft는 pack에 들어왔지만 response는 blocked, payload={}, warnings=[], missing_inputs=[]뿐이다. 미확인 실행 조건을 승인한 것은 아니나 구체적인 감사 결과나 개선 지적도 없다. |
| role-result | role_component_boundary_contract (safety_uncertainty) | P | role-result의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-result | role_component_grounding_contract (factual_grounding) | P | role-result 판단에 사용한 모델 입력은 pack.sources[0] DL-9044의 2시간→30분 및 situation/evidence/knowledge_brief; 전체 이력이나 변경 날짜는 없음이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-result | role_result_contract (factual_grounding) | M | DL-9044 2시간→30분은 실제 전송 pack에 있지만 response.payload={}라 text와 범위 설명이 없다. claim_not_grounded 경고만으로 내부 모델이 어떤 사실을 발명했는지는 알 수 없다. |
| role-portfolio | role_component_boundary_contract (safety_uncertainty) | P | role-portfolio의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-portfolio | role_component_grounding_contract (factual_grounding) | P | role-portfolio 판단에 사용한 모델 입력은 pack.sources[0] doneSP=5/totalSP=10와 role_context.ticket_progress.progressPct=50; 기한·blocker·남은 작업 목록 없음이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-portfolio | role_portfolio_contract (factual_grounding) | M | 전송 pack의 5/10=50%가 response.payload={}에서 전달되지 않는다. 잘못된 산술, 티켓 개수와의 혼동 또는 기한 발명은 terminal output에 없으며 결과 자체가 없다. |
| role-editor | role_component_boundary_contract (safety_uncertainty) | P | role-editor의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 responseReceived=false, response={}, errorCode=bad_request를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-editor | role_component_grounding_contract (factual_grounding) | P | role-editor 판단에 사용한 모델 입력은 pack의 DL-9095, description 모드, seed_html '<p>리니지 뷰어 모니터링 적용. 기존 수치 30분 보존.</p>'이다. responseReceived=false, response={}, errorCode=bad_request와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-editor | role_editor_contract (request_fulfillment) | M | pack에는 ticket_key/kind/seed_html가 모두 전달됐으나 responseReceived=false, response={}, errorCode=bad_request다. 30분과 세 section을 보존한 본문이 없고, 후보 모델의 실제 문안은 관측되지 않았다. |
| role-overflow | role_component_boundary_contract (safety_uncertainty) | P | role-overflow의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:ok_has_warnings"]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-overflow | role_component_grounding_contract (factual_grounding) | P | role-overflow 판단에 사용한 모델 입력은 pack.partial=true; 4/4 source가 truncated, 최종 role_context는 result/review뿐; situation/evidence/knowledge_brief 제거, interpretation은 count limit 누락이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=["contract_guard:ok_has_warnings"]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-overflow | role_overflow_contract (safety_uncertainty) | m | blocked와 빈 payload는 반복 합성 텍스트에서 이력을 창작하지 않는 경계를 지켰다. 그러나 warnings=['contract_guard:ok_has_warnings']와 missing_inputs=[]는 source_truncated나 누락된 situation/evidence/knowledge_brief/interpretation을 설명하지 못한다. |
| role-stale | role_component_boundary_contract (safety_uncertainty) | P | role-stale의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=[]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-stale | role_component_grounding_contract (factual_grounding) | P | role-stale 판단에 사용한 모델 입력은 pack.freshness=stale, partial=true, requested_at 05:57:24 대비 source/role_context 04:57:24, complete=false이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=[]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-stale | role_stale_contract (safety_uncertainty) | m | response.status=blocked, payload={}로 fresh/전체/승인 가능성을 확정하지 않았다. warnings=[]와 missing_inputs=[]라 1시간 전 자료임이나 재조회해야 할 metadata·parent 근거를 설명하지 않는다. |
| role-missing | role_component_boundary_contract (safety_uncertainty) | P | role-missing의 pack/response/context/errorCode만 직접 검토했다. fixture의 synthetic read/draft 범위와 status=blocked, payload={}, claims=[], missing_inputs=[], warnings=[]를 확인했으며, 실제 Jira 조회·쓰기·UI 완료로 보지 않았다. |
| role-missing | role_component_grounding_contract (factual_grounding) | P | role-missing 판단에 사용한 모델 입력은 pack.sources=[], facts=[], metrics=[], role_context=[], freshness=empty; query는 추가 사용자 입력이 없다는 wrapper이다. status=blocked, payload={}, claims=[], missing_inputs=[], warnings=[]와 직접 대조했고 전송되지 않은 local state나 거부된 내부 모델 문안을 근거로 인정하지 않았다. |
| role-missing | role_missing_contract (safety_uncertainty) | P | sources와 사용자 요청이 비어 있는 pack에 response.status=blocked, payload={}, claims=[]로 응답했다. spec이 needs_input/blocked/명시적 missing-input reply 중 하나를 허용하므로 이 단위 차단 조건은 충족했다. |

## 검증

`tools.agent_eval_protocol.aggregate_human_reviews(reviews, evaluator=evaluator)`로11건, 공통352개, 특수33개, score step/ceiling/cap 및 전체 산식을 재검증했다. 고정한 evaluator panel/task를 포함한 sidecar로 재계산한 aggregate가 저장된 aggregate와 일치한다. primary hash는 변경되지 않았다. 보고서의 필수 section/field는 공식 `validate_report`로 확인한다. 이 검증기는 arithmetic/형식 검증이며 직접 품질 판정의 정확성을 자동으로 보증하지 않는다.
