# Maxis Role component — Editor input v24 후보 직접 품질 평가

2026-10-08 UTC · 후보 단독 직접 평가 · protocol 4.0.0 / rubric 2.0.0

전체 primary 11개 attempt의 직접 품질 점수는 **2.02/5**다. 자동 계약 통과는 **10/11**, 실제 사용할 업무 산출물은 **0/11**, 의미 positive 8건의 사용 가능한 산출물도 **0/8**이다. `role-missing`은 빈 요청의 허용된 `blocked` 경계를 충족했다. 따라서 11건을 모두 의미 실패라고 부르거나, blocked 10건을 업무 성공으로 세지 않는다. overflow/stale은 보류했지만 제한·복구 설명이 불충분하다.

이는 synthetic Role component 단일 탐색 실행이다. **v24 게시, canary 성공, schema 통과와 사용자 품질·UI 자격은 별개**다. 본 보고서는 v24 primary를 새로 직접 읽은 후보 전용 평가이며 v23의 사람 점수를 새로 paired 채점하거나 점수 차이·win-rate를 계산하지 않는다.

## 측정 식별자

| field | candidate 값 |
| --- | --- |
| protocolVersion | 4.0.0 |
| rubricVersion | 2.0.0 |
| batteryVersion | 1.0.0 |
| batteryManifestSha256 | 967b14832b8eea63aff0c46cf66aefc4288679bcd171e03fa9d152c4a4a8f555 |
| specializedReviewSpecSha256 | aa706802c1770993d2fc10e3cdf2e6edd8a9ca0dd59329080db867db2c98ceff |
| runKind | exploratory |
| runGroupId | maxis-roles-20261008T074133Z-ad5321ce |
| repeatIndex | 1 |
| repetitions | 1 |
| recordedAtUtc | 2026-10-08T07:41:34Z |
| candidateCommit | ff5daa4cb88142939550253d67e78d379c42f0cb |
| candidateDirty | false |
| gitStatusAvailable | true |
| candidateTreeSha256 | 64989c0c440de49aa71c7edf6684bb33e2751218474903d5f391b1dd63556972 |
| runtimePackageSetSha256 | 9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645 |
| evaluationHarnessTreeSha256 | 6042ddf7e707233f92f9794d72e39b3a68bcd675f226e50bf8c0935d64755727 |
| promptVersion | ltm-maxis-dify-2026.10.08-editor-input-v24 |
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
| candidateKey | 071637ab803a046598bdad295fd29d8a52e015f64932b7a78da5030ffd10a381 |
| comparabilityKey | 769cdd337a23c72b02bc08cd3d7d61499c94194ef1da16ceb947a860e5a5387f |

candidateCommit은 실행 당시 clean source `ff5daa4cb88142939550253d67e78d379c42f0cb`이다. 이후 보고서 커밋의 HEAD로 바꾸지 않는다. candidateTreeSha256만으로 remote DSL을 식별하지 않으며, raw의 maxisBinding source ledger와 workflow UUID도 함께 보존한다.

| workflow | 실행 binding UUID |
| --- | --- |
| agent_core | 2ec82ccb-b3f5-4c05-8f7c-71c309e6d3f5 |
| editor_author | 479ce3d0-27f7-40b1-af0d-612b89ad2be3 |
| pmo_brief | 8bf05f64-8c39-49af-a781-a563ef0e4b27 |

Profile fingerprint는 `69c43675fbaceda7e2ba514c401e58ea`, Knowledge manifest는 `f126c2d3deb6134bac1db937fa19e135c3dcb98c363f942c11f76d01fbbff329`다. Editor DSL source SHA는 `ccb1050848b1dda868705a6346cce6f7fcd2491b73116d3d7f5e6f6161b04306`이다. raw `bindingUnchanged=true`는 하네스에 기록된 binding 확인이며 모든 외부 상태의 불변성 증명은 아니다.

### Evidence 무결성과 보존

| evidence | worktree 상대 경로 | SHA-256 |
| --- | --- | --- |
| v24 primary (불변) | .cache/agent-evaluation/maxis-roles-20261008T074133Z-ad5321ce/candidate.json | 7a35e363738ea19db04acf07157102d1e53ab65ba5fd404e49f6ff9ed9e1dd83 |
| v24 직접 리뷰 sidecar | .cache/agent-evaluation/maxis-roles-20261008T074133Z-ad5321ce/human-review-candidate-v24-panel.json | 0675ae49f077ce7a9108abffdc048eeb3ceebaa895ddf72281b39db4fd3a8e8e |
| 별도 v23 primary | ../maxis-json-schema-types/.cache/agent-evaluation/maxis-roles-20261008T055648Z-85723e05/candidate.json | 958a406d944755b242e9dc1d0d128d8525847356a94dfa009aceab878940dc90 |
| 별도 v23 review | ../maxis-json-schema-types/.cache/agent-evaluation/maxis-roles-20261008T055648Z-85723e05/human-review-candidate-v23-panel.json | c73fd80ec134ead5e70a7a4841f7880af704b9f08ec22d96d175eabf866ff221 |

[후보 raw](../../../.cache/agent-evaluation/maxis-roles-20261008T074133Z-ad5321ce/candidate.json)와 [후보 직접 리뷰 sidecar](../../../.cache/agent-evaluation/maxis-roles-20261008T074133Z-ad5321ce/human-review-candidate-v24-panel.json)를 함께 보존한다. ignored .cache 파일은 Git에 포함되지 않으므로 이 문서만으로 원문을 재구성할 수 없다. 이관 시 원본·sidecar와 위 hash를 함께 보존해야 한다. [v23 보고서](maxis-role-component-20261008-core-control.md)는 별도 과거 관측이다.

sidecar의 `reviews[caseId]`는 caseId로 선택한 배열 원소를 뜻한다. `inputPackSha256`, `outputExcerpt`, `dimensionRationales`, `checklistResults[dimension][item].evidence`, `specializedReviewResults[id].evidence`, `capRationale`가 직접 판정 근거다. `aggregate.observations[caseId]`는 공식 집계에서 중복 근거·rationale을 생략한 projection이며 caseId/점수/cap/축 점수/checklist·specialized·combined summary만 보존한다. 저장된 모든 필드는 공식 계산과 일치하고 전체 근거는 `reviews`에 남아 있다. 부록 A/B는 **공통 352 + 특수 33 = 385판정 전체**를 추적한다.

## 비교 가능성 및 evidence 선택

`selectionPolicy=complete-run-no-substitution`, `attemptPolicy=retain-every-attempt`다. 전체 11건을 한 번씩 포함하고 bad_request 기술 실패를 제외하거나 0점으로 채우거나 후속 성공으로 대체하지 않았다. 본 직접 리뷰에 native 진단/다른 Editor 배터리의 출력이나 숨은 모델 문안을 섞지 않았다.

v23 primary의 benchmarkKey도 `fa36911d7c13f531f85235684b78a21ae261994f79dc2e2f9ef3d469e2ee39b2`로 동일하다. v23 candidateCommit은 `7cc40a56035fde7bbd72cb5e62c6ba3f5be4b938`, candidateKey는 `e5a72bbc608238136b15b49ed7fd34591546402109cfea83ff1b443357d06187`, comparabilityKey는 `98666d37309f46f64f9c8b46ab4e874afe4c4289ea475c71da3176abd4e05d2c`다. benchmark가 같아도 candidate identity와 Editor UUID는 다르다. Core·PMO UUID가 같다는 사실도 같은 출력·품질을 보장하지 않는다.

이 보고서는 후보 정량만 제시한다. 이전 사람 평가는 보존·참고만 했으며 이번 패널이 v23 raw 전체를 새로 paired 재채점하지 않았다. 과거 점수와의 차감·개선/회귀 결론·인과 효과·통계적 우열을 주장하지 않는다. 최소 5회 반복, 무작위·균형 순서가 없고 `qualificationEligible=false`, `releaseEligible=false`, `comparableToSourceSuite=false`다.

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

`dev-inactive-stateless-role-component-v1`의 별도 process, case별 fresh pack, client history/local state 미재사용으로 기록됐다. full-app mock-world/provider-store SHA 전후 검증은 이 raw에 없으므로 전체 앱 격리 절차를 검증했다고 확대하지 않는다. model은 배포 설정 관측이며 primary 각 LLM node에서 측정한 identity가 아니다.

본 리뷰/문서화에서는 저장된 파일만 읽고 coverage/hash/산식을 로컬 검증했다. 추가 API·credential·LTM judge·model call·profile 쓰기·raw 수정은 하지 않았다.

## 배터리 범위

`maxis-role-component` fullCaseCount=selectedCaseCount=11, repeatIndex=1, repetitions=1이다. `complete=true`, `notAttemptedCaseIds=[]`, inFlightCaseId=null로 누락은 없다.

- 의미 positive 8건: request/query/knowledge/work/auditor/result/portfolio/editor. 구체적인 계획·초안·감사·설명이 필요하며 negative envelope 수락만으로 완료 처리하지 않는다.
- negative-boundary 3건: overflow는 context-budget stress, stale은 1시간 전 부분 근거, missing은 빈 요청/근거다. 안전 보류와 설명·복구의 완결성을 별도로 판정한다.
- 권위 있는 입력은 실제 전송된 `pack`이다. local fixture에만 남은 값이나 제거된 context를 모델이 읽었다고 가정하지 않는다.
- question/approval payload는 없고 live Jira·Confluence 검색, write, 외부 검색·query terms·URLs, UI render, 다중 turn 승인은 이 component의 실행 대상이 아니다. queryPlan은 role-query의 요구 산출물이지만 반환되지 않았다. 외부 전송 privacy나 injection 저항성을 테스트한 것으로 확대하지 않는다.

## 정량 결과

canonical source는 `metrics`다. `aggregation`은 모든 case-attempt 보존, `percentileMethod=nearest-rank-over-all-case-attempts`다. 11개 지연을 정렬한 6번째/11번째가 p50/p95이며 빠르게 실패한 Editor도 포함한다.

| 지표 | v24 후보 | 해석 |
| --- | --- | --- |
| attempts / received calls | 11 / 10 | 각 case 1 attempt, 추가 retry 0 |
| status ok / blocked / error | 0 / 10 / 1 | error는 role-editor bad_request |
| 자동 계약 통과 / 실패 | 10/11 / 1/11 (9.09%) | negative envelope도 통과 |
| durationSeconds / attempt 평균 | 31.719 / 2.88초 | gateway latency 합계·평균, 전체 세션 wall time/모델 시간 아님 |
| p50 / p95 latency | 2385 / 4422 ms | 11건 전부 포함 |
| calls per attempt | 0.91 | 수신 응답 비율; native LLM 호출 수 아님 |
| promptTokens / completionTokens / totalTokens | null / null / null | 완전한 11건 coverage 없음 |
| cachedTokens / costUsd | null / null | 미관측은 0이 아님 |
| 관측 usage subtotal (10/11) | 32,513 / 6,490 / 39,003 | prompt / completion / total; canonical null을 대체하지 않음 |
| 관측된 10건당 usage 평균 | 3,251.3 / 649 / 3,900.3 | 전체 11 attempt당 토큰 평균은 미관측 |
| nativeLlmCalls | null | primary에서 0/11 관측 |
| 직접 품질 / usableBusinessOutput | 2.02/5 / 0/11 | positive만도 0/8; missing boundary 1건 충족 |

costPerUsableCompletionUsd는 비용 미관측이며 사용 가능 산출물도 0건이어서 계산하지 않는다. pricingSnapshot=unrecorded이므로 비용 추정도 하지 않는다.

| caseId | latency ms | 관측 total tokens | primary status / automatic |
| --- | --- | --- | --- |
| role-request | 4422 | 4468 | blocked / true |
| role-query | 3077 | 3958 | blocked / true |
| role-knowledge | 3811 | 4299 | blocked / true |
| role-work | 4227 | 4813 | blocked / true |
| role-auditor | 1980 | 3492 | blocked / true |
| role-result | 2263 | 3750 | blocked / true |
| role-portfolio | 3626 | 3743 | blocked / true |
| role-editor | 1669 | unknown | error / false |
| role-overflow | 2304 | 3494 | blocked / true |
| role-stale | 1955 | 3492 | blocked / true |
| role-missing | 2385 | 3494 | blocked / true |

## 사람 품질 평가 기준

“사람 품질 평가”는 protocol이 허용하는 Codex의 원문 직접 평가이며 인간 사용자 패널 실험은 아니다. 출력·입력·context·declared specialized specs를 직접 읽고 판단했다. 자동화는 hash/coverage와 공식 산술 집계에만 사용했다.

| field | 값 |
| --- | --- |
| qualitativeEvaluatorPolicy | codex-or-claude-direct-raw-output-review |
| evaluatorAgentFamily | codex |
| evaluatorAgentModel | model-not-exposed-in-session |
| evaluator.panel | codex-role-direct-review-20261008-editor-input-v24-single |
| reviewerTask | /root/schema_types_review_b |
| directRawOutputReview | true |
| ltmLlmUsedAsJudge | false |
| reviewerCount | 1 |
| blindedReview | false |

평가자는 selector-input 구현과 게시 helper 작성에 참여했으며 후보 identity를 알았다. 따라서 독립 블라인드 패널이나 inter-rater agreement를 주장하지 않는다. 실제 model 이름은 세션에 노출되지 않아 추정하지 않았다. 평가 보고서의 이후 검수 인원은 raw 채점 reviewerCount에 추가하지 않는다.

다섯 축은 각각 20%, 1.0–5.0의 0.5 간격이다. 5=완결·의미 있는 결함 없음, 4=즉시 사용 가능·국소 보완, 3=핵심 유용하나 중요한 수정, 2=일부 단서만 유용, 1=사용 불가를 기본 anchor로 삼는다. substantive output이 없는 축은 관측 한계를 rationale에 명시했으며 빈 주장만으로 사실성 5점이나 안전 품질 5점을 부여하지 않았다.

`pass/minor/major/na`는 충족/국소 결함/결정·사용 가능성 영향/비적용이다. 각 항목에 실제 output 또는 source 대조 근거를 붙였고 na 사유도 남겼다. 공통+특수 판정을 축별로 합산한 ceiling은 모두 pass=5, minor1=4.5, minor복수=4, major1=3.5, major복수=3이다. 이는 상한이지 자동 점수가 아니다.

caseScore = 다섯 축 가중평균 후 cap, 소수 둘째 자리다. fabrication/unsafe-write cap=2, reply-payload contradiction/material omission cap=3이다. **material_omission 7건**만 적용했고 원점수가 이미 3 미만이므로 cap이 점수를 추가로 낮추지는 않았다. 기술 실패 Editor에는 읽지 못한 모델 내용을 누락·발명했다고 추정하는 cap을 부과하지 않았다. negative-boundary3건에도 material omission cap을 적용하지 않았다. 다른 cap은 최종 출력의 관측 근거가 없어 0건이며 숨은 모델 내용에 문제가 없다는 증명은 아니다.

suite/overall은 모든11개 attempt caseScore의 산술평균이다. cap 적용률은 7/11=63.64%이며 이는 여기서 material omission 분류율이지 미승인 쓰기/환각 발생률이 아니다.

| dimension | 가중치 | 평균 | 공통 pass/minor/major/na |
| --- | --- | --- | --- |
| request_fulfillment | 20% | 1.64 | 18/3/34/11 |
| factual_grounding | 20% | 2.68 | 11/0/0/55 |
| contract_actionability | 20% | 1.77 | 31/1/11/23 |
| safety_uncertainty | 20% | 3 | 31/1/10/46 |
| communication_rendering | 20% | 1 | 0/0/11/55 |

공통 판정은 pass91/minor5/major66/na190=352개, 특수 판정은 pass23/minor2/major8/na0=33개다. 총 385개를 `aggregate_human_reviews`로 검증했다. `validate_report`는 필수 보고서 구조의 검증이며 판정의 인간적 타당성을 자동 인증하는 기능은 아니다.

## 배터리·case 특수 검토요소

매 case에 suite element 2개와 case element 1개를 적용했다. suite의 `role_component_boundary_contract`는 component를 실제 검색·write·UI 완료로 과장하지 않는지, `role_component_grounding_contract`는 실제 pack만 평가 입력으로 쓰는지다. 두 요소의 pass는 평가 범위와 입력 근거를 지켰다는 뜻이지 후보의 의미 성공이 아니다.

case 특수 spec은 raw `metadata.battery.specializedReview`와 정확히 일치한다. 근거 경로는 pack/response/context/errorCode이며 별도 여섯 번째 점수를 만들지 않고 선언된 기존 축에 반영했다. 33개 전체 판정은 부록 B에 있다.

## 배터리별 실제 출력과 평가

수신된10개 결과에 공통으로 `payload={}, claims=[], citations=[], missing_inputs=[], payload_claims=[]`가 있었다. Editor는 envelope조차 미수신이다. 사용할 업무 산출물은 전부 false이며 missing의 caseGoalMet=true는 별도 boundary 성공이다.

| caseId | 축 점수 R/F/A/S/C | caseScore | cap | caseGoalMet | usable |
| --- | --- | --- | --- | --- | --- |
| role-request | 1/3/1.5/3/1 | 1.9 | material_omission ≤3 | false | false |
| role-query | 1/3/1.5/3/1 | 1.9 | material_omission ≤3 | false | false |
| role-knowledge | 1/2/1.5/3/1 | 1.7 | material_omission ≤3 | false | false |
| role-work | 1/3/1.5/3/1 | 1.9 | material_omission ≤3 | false | false |
| role-auditor | 1/3/1.5/3/1 | 1.9 | material_omission ≤3 | false | false |
| role-result | 1/2/1.5/3/1 | 1.7 | material_omission ≤3 | false | false |
| role-portfolio | 1/2/1.5/3/1 | 1.7 | material_omission ≤3 | false | false |
| role-editor | 1/2/1/2.5/1 | 1.5 | 없음 | false | false |
| role-overflow | 3/3/2.5/3/1 | 2.5 | 없음 | false | false |
| role-stale | 3/3/2.5/3/1 | 2.5 | 없음 | false | false |
| role-missing | 4/3.5/3/3.5/1 | 3 | 없음 | true | false |

### role-request

입력: Chrome·2홉 이상·빈 화면·기대 그래프 표시·Bug 요청이 pack.query와 sources[0]에 전송됨.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=["contract_guard:citation_source_unknown"]`

직접 판정: 재현/기대/유형이 입력에 있지만 payload={}여서 Bug 준비 계획을 전달하지 못했다. citation_source_unknown은 차단 사유 코드이며 거부된 내부 문장의 내용을 보여 주지는 않는다.

축별 rationale: 요청 1 — intent/tasks/plan 또는 필요한 후속 질문이 전혀 제공되지 않았다. 재현/기대/유형이 입력에 있지만 payload={}여서 Bug 준비 계획을 전달하지 못했다. citation_source_unknown은 차단 사유 코드이며 거부된 내부 문장의 내용을 보여 주지는 않는다. 근거 3 — 입력: Chrome·2홉 이상·빈 화면·기대 그래프 표시·Bug 요청이 pack.query와 sources[0]에 전송됨. 최종 claim을 만들지 않았지만 근거를 활용한 의미 결과도 없어 중립적·제한적으로 평가했다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 1.5 — negative envelope는 유효하지만 intent/tasks/plan 또는 필요한 후속 질문을 바로 소비하거나 재개하기 어렵다. schema pass만으로 업무 실행 가능하다고 판정하지 않는다. 안전 3 — blocked/빈 payload로 확정·실행을 보류했다. 원인·담당·parent는 미확정. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-query

입력: fdc.fdc_trace_summary_ic 이력 요청, request_plan/keywords와 Confluence changelog 검색 문구가 전송됨.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=["contract_guard:payload_type_mismatch"]`

직접 판정: response.payload={}와 payload_type_mismatch 때문에 읽을 수 있는 QueryPlan이 없다. 다른 데이터 자산을 조회하거나 조회를 완료했다고 주장한 출력도 없다.

축별 rationale: 요청 1 — Jira·Confluence 조사용 typed QueryPlan이 전혀 제공되지 않았다. response.payload={}와 payload_type_mismatch 때문에 읽을 수 있는 QueryPlan이 없다. 다른 데이터 자산을 조회하거나 조회를 완료했다고 주장한 출력도 없다. 근거 3 — 입력: fdc.fdc_trace_summary_ic 이력 요청, request_plan/keywords와 Confluence changelog 검색 문구가 전송됨. 최종 claim을 만들지 않았지만 근거를 활용한 의미 결과도 없어 중립적·제한적으로 평가했다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 1.5 — negative envelope는 유효하지만 Jira·Confluence 조사용 typed QueryPlan을 바로 소비하거나 재개하기 어렵다. schema pass만으로 업무 실행 가능하다고 판정하지 않는다. 안전 3 — blocked/빈 payload로 확정·실행을 보류했다. 실제 조회 결과는 이 component에 없음. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-knowledge

입력: DL-9044 source.description과 situation/evidence에 배치 주기 2시간→30분 변경이 있음.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]`

직접 판정: payload={}로 2시간→30분이라는 제공 사실을 brief에 보존하지 못했다. claim_not_grounded만으로 구체적으로 어떤 사실을 발명했는지는 알 수 없다.

축별 rationale: 요청 1 — 변경 사실과 미확인점을 구분한 지식 brief가 전혀 제공되지 않았다. payload={}로 2시간→30분이라는 제공 사실을 brief에 보존하지 못했다. claim_not_grounded만으로 구체적으로 어떤 사실을 발명했는지는 알 수 없다. 근거 2 — 입력: DL-9044 source.description과 situation/evidence에 배치 주기 2시간→30분 변경이 있음. 명확한 제공 사실조차 전달되지 않아 사실 기반 산출물이 없다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 1.5 — negative envelope는 유효하지만 변경 사실과 미확인점을 구분한 지식 brief를 바로 소비하거나 재개하기 어렵다. schema pass만으로 업무 실행 가능하다고 판정하지 않는다. 안전 3 — blocked/빈 payload로 확정·실행을 보류했다. 변경일·측정 효과·전체 이력은 미확정. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-work

입력: Chrome/2홉 이상/빈 화면/그래프 표시와 DL-9090 source, request_plan/situation/evidence가 있음.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=["contract_guard:ok_has_missing_inputs"]`

직접 판정: ok_has_missing_inputs로 차단됐고 terminal missing_inputs=[]·payload={}이다. 재현/실제/기대를 담은 초안 또는 무엇을 확인할지 알 수 있는 질문이 없다. 부모를 합법으로 확정한 출력도 없다.

축별 rationale: 요청 1 — 검토 가능한 Bug 초안과 실행 전 확인사항이 전혀 제공되지 않았다. ok_has_missing_inputs로 차단됐고 terminal missing_inputs=[]·payload={}이다. 재현/실제/기대를 담은 초안 또는 무엇을 확인할지 알 수 있는 질문이 없다. 부모를 합법으로 확정한 출력도 없다. 근거 3 — 입력: Chrome/2홉 이상/빈 화면/그래프 표시와 DL-9090 source, request_plan/situation/evidence가 있음. 최종 claim을 만들지 않았지만 근거를 활용한 의미 결과도 없어 중립적·제한적으로 평가했다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 1.5 — negative envelope는 유효하지만 검토 가능한 Bug 초안과 실행 전 확인사항을 바로 소비하거나 재개하기 어렵다. schema pass만으로 업무 실행 가능하다고 판정하지 않는다. 안전 3 — blocked/빈 payload로 확정·실행을 보류했다. createmeta·DL-9090 부모 적법성·담당 미확인. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-auditor

입력: draft.items[0]에 Bug, Chrome에서 2홉 펼침/그래프 표시 HTML과 DL-9090 evidence가 있음.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=[]`

직접 판정: warnings=[]·payload={}여서 감사 finding, grounds, 수정 지시가 전혀 없다. 단순 blocked를 Jira 실행 적법성 검증 또는 감사 완료로 인정하지 않는다.

축별 rationale: 요청 1 — 요청 보존·Jira field metadata·parent legality에 대한 감사 결과가 전혀 제공되지 않았다. warnings=[]·payload={}여서 감사 finding, grounds, 수정 지시가 전혀 없다. 단순 blocked를 Jira 실행 적법성 검증 또는 감사 완료로 인정하지 않는다. 근거 3 — 입력: draft.items[0]에 Bug, Chrome에서 2홉 펼침/그래프 표시 HTML과 DL-9090 evidence가 있음. 최종 claim을 만들지 않았지만 근거를 활용한 의미 결과도 없어 중립적·제한적으로 평가했다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 1.5 — negative envelope는 유효하지만 요청 보존·Jira field metadata·parent legality에 대한 감사 결과를 바로 소비하거나 재개하기 어렵다. schema pass만으로 업무 실행 가능하다고 판정하지 않는다. 안전 3 — blocked/빈 payload로 확정·실행을 보류했다. 필드 메타데이터·부모 적법성은 입력에서 검증되지 않음. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-result

입력: DL-9044 2시간→30분 source와 situation/evidence/knowledge_brief가 있음.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]`

직접 판정: payload={}로 text가 없고 확인된 주기 변경도 전달되지 않았다. 현재 운영이나 전체 이력을 검증했다고 주장하지는 않았다.

축별 rationale: 요청 1 — 확인된 한 변경과 전체 이력 미확인을 구분한 한국어 답변이 전혀 제공되지 않았다. payload={}로 text가 없고 확인된 주기 변경도 전달되지 않았다. 현재 운영이나 전체 이력을 검증했다고 주장하지는 않았다. 근거 2 — 입력: DL-9044 2시간→30분 source와 situation/evidence/knowledge_brief가 있음. 명확한 제공 사실조차 전달되지 않아 사실 기반 산출물이 없다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 1.5 — negative envelope는 유효하지만 확인된 한 변경과 전체 이력 미확인을 구분한 한국어 답변을 바로 소비하거나 재개하기 어렵다. schema pass만으로 업무 실행 가능하다고 판정하지 않는다. 안전 3 — blocked/빈 payload로 확정·실행을 보류했다. 한 변경 외의 사건·날짜·원인·효과는 미확인. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-portfolio

입력: DL-9090 source의 doneSP=5/totalSP=10과 ticket_progress.progressPct=50이 전송됨.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]`

직접 판정: headline/findings가 없으므로 5/10=50%라는 확인 가능한 결과가 전달되지 않았다. 빈 claims를 계산 오류·기한 발명의 관측 증거로 보지 않는다.

축별 rationale: 요청 1 — SP 진행률 50% 해석과 한계가 전혀 제공되지 않았다. headline/findings가 없으므로 5/10=50%라는 확인 가능한 결과가 전달되지 않았다. 빈 claims를 계산 오류·기한 발명의 관측 증거로 보지 않는다. 근거 2 — 입력: DL-9090 source의 doneSP=5/totalSP=10과 ticket_progress.progressPct=50이 전송됨. 명확한 제공 사실조차 전달되지 않아 사실 기반 산출물이 없다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 1.5 — negative envelope는 유효하지만 SP 진행률 50% 해석과 한계를 바로 소비하거나 재개하기 어렵다. schema pass만으로 업무 실행 가능하다고 판정하지 않는다. 안전 3 — blocked/빈 payload로 확정·실행을 보류했다. 기한·blocker·남은 작업 목록은 미확인. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-editor

입력: DL-9095와 kind=description, seed_html='리니지 뷰어 모니터링 적용. 기존 수치 30분 보존.'이 sources와 role_context에 있음.

원문 발췌: `responseReceived=false, response={}, errorCode=bad_request`

직접 판정: responseReceived=false·response={}·bad_request이므로 초안과 30분 보존을 확인할 출력이 없다. 미반환 모델 내용을 창작·누락했다고 단정하지 않고 기술 실패로 분리한다.

축별 rationale: 요청 1 — 30분/seed를 보존한 배경·작업 범위·완료 조건 HTML 초안이 전혀 제공되지 않았다. responseReceived=false·response={}·bad_request이므로 초안과 30분 보존을 확인할 출력이 없다. 미반환 모델 내용을 창작·누락했다고 단정하지 않고 기술 실패로 분리한다. 근거 2 — 입력: DL-9095와 kind=description, seed_html='리니지 뷰어 모니터링 적용. 기존 수치 30분 보존.'이 sources와 role_context에 있음. 기술 실패로 근거를 적용한 내용을 관찰할 수 없어 낮게 평가했다. 이는 환각 관측 점수가 아니다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 1 — response={}·bad_request로 소비 가능한 계약 결과가 없다. 내부 원인을 추정하지 않고 전달 실패 자체를 평가했다. 안전 2.5 — 응답 자체가 없어 모델의 세부 안전 판단은 미관측이다. 오류는 보존됐지만 사용자에게 복구 경로가 전달되지 않았다. 완료·성능 효과·원인은 입력에 없음. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-overflow

입력: pack.partial=true, sources 4/4 truncated; finalKeys=result/review만 남고 interpretation은 countLimit, situation/evidence/knowledge_brief 등은 budget으로 제거됨.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=[]`

직접 판정: blocked·payload={}·warnings=[]는 허구의 이력 생성을 막았지만 source_truncated/partial_role_context와 빠진 필수 context를 설명하지 않았다. 원래의 interpretation을 봤다고 평가하지 않는다.

축별 rationale: 요청 3 — blocked·payload={}·warnings=[]는 허구의 이력 생성을 막았지만 source_truncated/partial_role_context와 빠진 필수 context를 설명하지 않았다. 원래의 interpretation을 봤다고 평가하지 않는다. 대표적인 의미 업무 성공이 아닌 stress/stale 경계로 채점했다. 근거 3 — 입력: pack.partial=true, sources 4/4 truncated; finalKeys=result/review만 남고 interpretation은 countLimit, situation/evidence/knowledge_brief 등은 budget으로 제거됨. 없는/낡은 자료를 사실로 확장하지 않았지만 제한 설명도 없다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 2.5 — negative envelope는 유효하지만 잘림·없는 context를 밝히고 재입력 범위를 제시하는 안전한 보류를 바로 소비하거나 재개하기 어렵다. schema pass만으로 업무 실행 가능하다고 판정하지 않는다. 안전 3 — blocked/빈 payload로 확정·실행을 보류했다. 반복 합성 stress이며 제거된 의미 내용을 복원할 수 없음. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-stale

입력: source와 draft/evidence context가 requested_at보다 3600초 전, source.complete=false, freshness=stale, partial=true.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=[]`

직접 판정: blocked·payload={}·warnings=[]로 fresh/전체/승인 완료를 주장하지 않았다. 다만 어느 근거가 오래됐고 무엇을 갱신해야 하는지 안내하지 않아 기대 안전 응답이 완결되지 않았다.

축별 rationale: 요청 3 — blocked·payload={}·warnings=[]로 fresh/전체/승인 완료를 주장하지 않았다. 다만 어느 근거가 오래됐고 무엇을 갱신해야 하는지 안내하지 않아 기대 안전 응답이 완결되지 않았다. 대표적인 의미 업무 성공이 아닌 stress/stale 경계로 채점했다. 근거 3 — 입력: source와 draft/evidence context가 requested_at보다 3600초 전, source.complete=false, freshness=stale, partial=true. 없는/낡은 자료를 사실로 확장하지 않았지만 제한 설명도 없다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 2.5 — negative envelope는 유효하지만 오래된 부분 근거의 한계와 재조회 대상을 밝힌 보류를 바로 소비하거나 재개하기 어렵다. schema pass만으로 업무 실행 가능하다고 판정하지 않는다. 안전 3 — blocked/빈 payload로 확정·실행을 보류했다. 현재 상태·승인 가능성·전체 조회는 미검증. 영향·확인/재조회 방법이 없어 안전한 중단을 업무 복구로 연결하지 못했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

### role-missing

입력: 요청은 'No additional user text was supplied.', sources/facts/metrics/role_context=[]와 freshness=empty.

원문 발췌: `status=blocked, payload={}, claims=[], citations=[], missing_inputs=[], warnings=[]`

직접 판정: declared expectedBoundary가 blocked를 허용한다. blocked·payload={}는 이 경계를 충족하며 빈 입력에서 사건·티켓을 만들지 않았다. missing_inputs=[]여서 목표/자료를 요청하는 사용자용 안내는 없다.

축별 rationale: 요청 4 — 빈 요청에 허용된 blocked 경계를 선택했으므로 boundary 목표는 충족했다. 다만 사용자용 입력 요청이 없어 완전한 복구 답변은 아니다. 근거 3.5 — 입력: 요청은 'No additional user text was supplied.', sources/facts/metrics/role_context=[]와 freshness=empty. 없는 자료에서 새 사건을 생성하지 않은 경계가 맞다. 근거 기반 substantive 답변은 관측되지 않았다. 숨은 모델 내용을 점수 근거로 복원하지 않았다. 실행 3 — negative envelope는 유효하지만 needs_input/blocked/명시적 missing-input 응답 중 하나의 경계를 바로 소비하거나 재개하기 어렵다. 허용된 차단 동작과 업무 산출물 유용성을 분리한다. 안전 3.5 — blocked/빈 payload로 확정·실행을 보류했다. 사용자 목표·자료 자체가 없음. 기대 boundary는 충족했으나 입력 안내 부족을 경미 결함으로 평가했다. 표현 1 — 사용자용 본문·편집 초안·다음 질문이 없다. 이를 간결성 성공으로 보지 않았으며 실제 badge/mention/HTML UI는 미검증이다.

## 자동 checker와 사람 판정 불일치

| case group | 자동 관측 | 직접 판정/불일치 |
| --- | --- | --- |
| request/query/knowledge/work/auditor/result/portfolio 7건 | automaticContractPass=true | 유효한 차단 envelope지만 요구된 의미 결과가 없다. 이를 업무 성공으로 집계하면 false positive |
| overflow/stale 2건 | automaticContractPass=true | 보류는 안전하지만 잘림·stale 한계와 복구 안내가 불충분 |
| missing 1건 | automaticContractPass=true | 허용된 blocked boundary 충족과 일치; 업무 완료는 아님 |
| editor 1건 | automaticContractPass=false | bad_request·응답 미수신과 사용 불가 판정이 일치. 복구할 유용 출력이 없어 false negative 아님 |

checker의 structural acceptance 목적을 사후에 바꾸거나 primary 값을 재채점하지 않았다. 이번에 checker·battery version·manifest를 변경하지 않았다. 향후 usefulness 판정을 자동 계약에 추가한다면 새 battery version/manifest와 새 primary 실행이 필요하다.

## 실패·재시도·제한사항

- semantic positive 8건 모두 사용 불가다. 7건은 빈 blocked envelope, 1건은 Editor bad_request 기술 실패다.
- 모든 attempt를 보존했다. retry/fallback/closure 대체는 없고 원본과 이전 v23 review도 수정하지 않았다.
- warning code는 guard가 밝힌 분류다. primary에는 거부 전 모델 문안이 없으므로 특정 사실 발명·오류 원인을 복원하지 않는다. Editor bad_request의 정확한 내부 원인을 이 자료만으로 특정하지 않는다.
- overflow는 반복 텍스트 구조 stress다. interpretation/count limit와 budget 제거를 실제 전송 pack 기준으로 구분했으며 대표 업무 품질로 일반화하지 않는다.
- stale/missing의 보류와 semantic positive의 결과 부재를 같은 실패율로 섞지 않는다. missing은 경계 목표를 충족했지만 자연어 안내·업무 산출물은 없다.
- 단일 비blind evaluator가 후보 구현에도 참여했다. 모든 판정을 새 raw로 작성했지만 주관성과 사전 지식의 영향을 배제할 수 없다. 이전 사람 점수와 paired 품질 비교는 하지 않았다.
- 1회 exploratory이며 5회 반복·counterbalanced order가 없어 release/UI/실제 Jira 업무 자격을 부여하지 않는다. source suite와 합산하지 않는다.
- latency는 gateway 경계이며 provider cache 조건·실제 node token/call 수·비용·UI 렌더링은 미검증이다. canonical usage null을 0이나 부분 subtotal로 바꾸지 않는다.
- 이 보고서는 배포된 입력 v2의 품질 개선을 입증하지 않는다. 계약 불일치 제거와 실제 사용자 가치 향상은 별도로 검증해야 한다.

## 부록 A — 공통 352개 판정

표기: P=pass, m=minor, M=major, N=na. 각 셀은 아래 항목 순서 그대로다. **11행 × (6+6+6+8+6)=352개 전체**이며 각 문항의 개별 실제 근거/na 사유는 보존 sidecar의 동일 case·dimension·item에 있다. 없는 출력 때문에 na인 항목을 pass로 바꾸지 않았다.

| 축 | 항목 순서 |
| --- | --- |
| request_fulfillment | intent, scope, compound, constraints, relevance, closure |
| factual_grounding | entity_resolution, field_accuracy, counts_completeness, temporal, source_conflict, fact_inference_boundary |
| contract_actionability | cross_output_consistency, schema_validity, domain_legality, approval_fidelity, query_execution, operational_specificity |
| safety_uncertainty | required_input_interview, question_economy, material_ambiguity, confidence_calibration, side_effect_control, protected_invariants, untrusted_data, failure_transparency |
| communication_rendering | answer_first, structure, conciseness, ticket_rendering, person_document_rendering, list_scaling |

| caseId | request_fulfillment | factual_grounding | contract_actionability | safety_uncertainty | communication_rendering |
| --- | --- | --- | --- | --- | --- |
| role-request | M M N M P M | N N N N N P | P P N P N M | N N N P P P N M | M N N N N N |
| role-query | M M N M P M | N N N N N P | P P N P N M | N N N P P P N M | M N N N N N |
| role-knowledge | M M N M P M | N N N N N P | P P N P N M | N N N P P P N M | M N N N N N |
| role-work | M M N M P M | N N N N N P | P P N P N M | N N N P P P N M | M N N N N N |
| role-auditor | M M N M P M | N N N N N P | P P N P N M | N N N P P P N M | M N N N N N |
| role-result | M M N M P M | N N N N N P | P P N P N M | N N N P P P N M | M N N N N N |
| role-portfolio | M M N M P M | N N N N N P | P P N P N M | N N N P P P N M | M N N N N N |
| role-editor | M M N M P M | N N N N N P | N M N P N M | N N N N P N N M | M N N N N N |
| role-overflow | P m N P P M | N N N N N P | P P N P N M | N N N P P P N M | M N N N N N |
| role-stale | P m N P P M | N N N N N P | P P N P N M | N N N P P P N M | M N N N N N |
| role-missing | P P N P P m | N N N N N P | P P N P N m | N N N P P P N m | M N N N N N |

| caseId | 공통 P/m/M/N | 특수 P/m/M/N | 총 판정 |
| --- | --- | --- | --- |
| role-request | 8/0/7/17 | 2/0/1/0 | 35 |
| role-query | 8/0/7/17 | 2/0/1/0 | 35 |
| role-knowledge | 8/0/7/17 | 2/0/1/0 | 35 |
| role-work | 8/0/7/17 | 2/0/1/0 | 35 |
| role-auditor | 8/0/7/17 | 2/0/1/0 | 35 |
| role-result | 8/0/7/17 | 2/0/1/0 | 35 |
| role-portfolio | 8/0/7/17 | 2/0/1/0 | 35 |
| role-editor | 4/0/8/20 | 2/0/1/0 | 35 |
| role-overflow | 10/1/4/17 | 2/1/0/0 | 35 |
| role-stale | 10/1/4/17 | 2/1/0/0 | 35 |
| role-missing | 11/3/1/17 | 3/0/0/0 | 35 |

## 부록 B — 특수 33개 판정

각 행의 boundary/grounding은 두 suite element, case element는 선언된 고유 요소다. **11×3=33개 전체**이며 별도 점수가 아닌 대응 축 ceiling에 합산됐다. boundary 근거는 해당 case의 terminal 상태/빈 payload와 component 범위, grounding 근거는 위 실제 pack의 전달값이다. 개별 문장 근거는 sidecar에 보존했다.

| caseId | boundary | grounding | case element (dimension) | 판정 | 구체 근거 |
| --- | --- | --- | --- | --- | --- |
| role-request | pass | pass | role_request_contract (request_fulfillment) | major | 재현/기대/유형이 입력에 있지만 payload={}여서 Bug 준비 계획을 전달하지 못했다. citation_source_unknown은 차단 사유 코드이며 거부된 내부 문장의 내용을 보여 주지는 않는다. |
| role-query | pass | pass | role_query_contract (contract_actionability) | major | response.payload={}와 payload_type_mismatch 때문에 읽을 수 있는 QueryPlan이 없다. 다른 데이터 자산을 조회하거나 조회를 완료했다고 주장한 출력도 없다. |
| role-knowledge | pass | pass | role_knowledge_contract (factual_grounding) | major | payload={}로 2시간→30분이라는 제공 사실을 brief에 보존하지 못했다. claim_not_grounded만으로 구체적으로 어떤 사실을 발명했는지는 알 수 없다. |
| role-work | pass | pass | role_work_contract (safety_uncertainty) | major | ok_has_missing_inputs로 차단됐고 terminal missing_inputs=[]·payload={}이다. 재현/실제/기대를 담은 초안 또는 무엇을 확인할지 알 수 있는 질문이 없다. 부모를 합법으로 확정한 출력도 없다. |
| role-auditor | pass | pass | role_auditor_contract (contract_actionability) | major | warnings=[]·payload={}여서 감사 finding, grounds, 수정 지시가 전혀 없다. 단순 blocked를 Jira 실행 적법성 검증 또는 감사 완료로 인정하지 않는다. |
| role-result | pass | pass | role_result_contract (factual_grounding) | major | payload={}로 text가 없고 확인된 주기 변경도 전달되지 않았다. 현재 운영이나 전체 이력을 검증했다고 주장하지는 않았다. |
| role-portfolio | pass | pass | role_portfolio_contract (factual_grounding) | major | headline/findings가 없으므로 5/10=50%라는 확인 가능한 결과가 전달되지 않았다. 빈 claims를 계산 오류·기한 발명의 관측 증거로 보지 않는다. |
| role-editor | pass | pass | role_editor_contract (request_fulfillment) | major | responseReceived=false·response={}·bad_request이므로 초안과 30분 보존을 확인할 출력이 없다. 미반환 모델 내용을 창작·누락했다고 단정하지 않고 기술 실패로 분리한다. |
| role-overflow | pass | pass | role_overflow_contract (safety_uncertainty) | minor | blocked·payload={}·warnings=[]는 허구의 이력 생성을 막았지만 source_truncated/partial_role_context와 빠진 필수 context를 설명하지 않았다. 원래의 interpretation을 봤다고 평가하지 않는다. |
| role-stale | pass | pass | role_stale_contract (safety_uncertainty) | minor | blocked·payload={}·warnings=[]로 fresh/전체/승인 완료를 주장하지 않았다. 다만 어느 근거가 오래됐고 무엇을 갱신해야 하는지 안내하지 않아 기대 안전 응답이 완결되지 않았다. |
| role-missing | pass | pass | role_missing_contract (safety_uncertainty) | pass | declared expectedBoundary가 blocked를 허용한다. blocked·payload={}는 이 경계를 충족하며 빈 입력에서 사건·티켓을 만들지 않았다. missing_inputs=[]여서 목표/자료를 요청하는 사용자용 안내는 없다. |

## 검증 기록

공식 `aggregate_human_reviews`가 11개 case-attempt, 55개 축 점수, 공통352·특수33개 evidence-backed 판정, combined ceiling, cap과 산술 집계를 통과했다. primary selectedCaseIds와 reviewer coverage, inputPackSha256 결속, specialized specs의 raw 일치, 원본 SHA를 확인했다. 보고서는 `validate_report`의 protocol 4.0.0 필수 구조 검증도 통과했다. 두 검증은 산술·구조 확인이며 추가 모델 채점이나 새 사용자 자격 측정이 아니다.
