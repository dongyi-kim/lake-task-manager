# Maxis Role component — Editor eligibility v25 후보 직접 품질 평가

2026-10-08 UTC · 후보 단독 직접 평가 · protocol 4.0.0 / rubric 2.0.0

전체 primary 11개 attempt의 직접 품질 점수는 **2.32/5**다. 자동 계약 통과는 **11/11**, 실제 본문 반환은 **Editor 1/11**, 추가적인 실질 보완 없이 사용할 업무 산출물은 **0/11**(의미 positive 0/8)이다. Editor는 원문·출처·요청 구조를 보존했으나 작업/DoD의 실행 구체성이 부족하다. 빈 요청의 `role-missing`은 허용된 `blocked` 경계를 충족한다. 따라서 blocked 10건을 모두 업무 성공 또는 의미 실패로 묶지 않는다.

이것은 합성 Role component 단일 탐색 실행이며 **게시·canary·schema 통과, 사용자 품질, 실제 UI 자격은 서로 다른 주장**이다. v24의 이전 2.02점은 별도 과거 패널 관측이다. 이번 후보 점수와의 paired 차이·개선율·인과 효과는 계산하지 않는다.

## 측정 식별자

| field | candidate 값 |
| --- | --- |
| protocolVersion | `4.0.0` |
| rubricVersion | `2.0.0` |
| batteryVersion | `1.0.0` |
| batteryManifestSha256 | `967b14832b8eea63aff0c46cf66aefc4288679bcd171e03fa9d152c4a4a8f555` |
| specializedReviewSpecSha256 | `aa706802c1770993d2fc10e3cdf2e6edd8a9ca0dd59329080db867db2c98ceff` |
| runKind | `exploratory` |
| runGroupId | `maxis-roles-20261008T093745Z-e304cae3` |
| repeatIndex | `1` |
| repetitions | `1` |
| recordedAtUtc | `2026-10-08T09:37:45Z` |
| candidateCommit | `c595d0a34bcd394f58e213fc237dd382dfc7428d` |
| candidateDirty | `false` |
| gitStatusAvailable | `true` |
| candidateTreeSha256 | `64989c0c440de49aa71c7edf6684bb33e2751218474903d5f391b1dd63556972` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `6042ddf7e707233f92f9794d72e39b3a68bcd675f226e50bf8c0935d64755727` |
| promptVersion | `ltm-maxis-dify-2026.10.08-editor-eligibility-v25` |
| model | `gpt-5.4-mini` |
| simpleModel | `not-used` |
| provider | `dify` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| structuredOutputBackend | `dify` |
| structuredBackendVersion | `legacy-internal` |
| structuredBackendFallbackPolicy | `forbid` |
| dataProfile | `repository-synthetic-role-component-v1` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| candidateOrderIndex | `2` |
| runtimeProfile | `dev-inactive-stateless-role-component-v1` |
| benchmarkKey | `fa36911d7c13f531f85235684b78a21ae261994f79dc2e2f9ef3d469e2ee39b2` |
| candidateKey | `6778c1d0da0cd865d5499747122a6abaf579867c55bee01cd4028c4158340367` |
| comparabilityKey | `89e04a52524322d64223fbf1da23a31055c98ee252da21b6df1b7b2f1735943d` |

candidateCommit은 실행 당시 clean source `c595d0a34bcd394f58e213fc237dd382dfc7428d`다. 이후 문서 branch/HEAD로 바꾸지 않는다. candidateTreeSha256만으로 remote DSL을 식별하지 않으며 raw의 35-entry `maxisBinding.sourceFiles` ledger와 workflow UUID를 함께 보존한다.

| workflow | 실행 binding UUID |
| --- | --- |
| agent_core | 2ec82ccb-b3f5-4c05-8f7c-71c309e6d3f5 |
| editor_author | b3cdd09e-2ca0-415c-86b0-f64f66f268ab |
| pmo_brief | 8bf05f64-8c39-49af-a781-a563ef0e4b27 |

Profile fingerprint는 `256722648ebe7f579e4d0b4bbd975ef4`, Knowledge manifest는 `f126c2d3deb6134bac1db937fa19e135c3dcb98c363f942c11f76d01fbbff329`, Editor DSL source SHA는 `7df340b3aeeb232013e92048cf3d96a312f5c34eb87808e0154c9f11271f87cf`다. `bindingUnchanged=true`는 하네스의 실행 경계 확인이지 지속적인 모든 외부 상태 불변 증명이 아니다. 배포 기록은 [DEV v25 ledger](../../../deploy/maxis-dify/deployments/dev-editor-eligibility-2026.10.08.yml)다.

### Evidence 무결성과 보존

| evidence | worktree 상대 경로 | SHA-256 |
| --- | --- | --- |
| v25 primary | .cache/agent-evaluation/maxis-roles-20261008T093745Z-e304cae3/candidate.json | d8389bcebba5e3f2da7fb1c08076d12d3429bb6f9b598a9929122ad4b319cd44 |
| v25 직접 리뷰 sidecar | .cache/agent-evaluation/maxis-roles-20261008T093745Z-e304cae3/human-review-candidate-v25-panel.json | 3e19c71e836afc3397c18fb4112a65c16a99499dde17c12418afbdaf99beede4 |
| 별도 v24 primary | ../maxis-editor-selector-input-v2/.cache/agent-evaluation/maxis-roles-20261008T074133Z-ad5321ce/candidate.json | 7a35e363738ea19db04acf07157102d1e53ab65ba5fd404e49f6ff9ed9e1dd83 |

[후보 raw](../../../.cache/agent-evaluation/maxis-roles-20261008T093745Z-e304cae3/candidate.json)와 [후보 직접 리뷰 sidecar](../../../.cache/agent-evaluation/maxis-roles-20261008T093745Z-e304cae3/human-review-candidate-v25-panel.json)는 ignored local evidence다. Git 문서만으로 원문을 복원할 수 없으므로 이관 시 두 파일과 hash를 함께 보존한다. [v24 보고서](maxis-role-component-20261008-input-v2.md)는 이전 관측으로만 참고했다.

sidecar `reviews`의 caseId별 원소에 pack/answer hash, 출력 발췌, 55개 축 rationale, 공통 352개와 특수 33개 판정의 개별 실제 근거·na 이유·capRationale가 있다. `aggregate.observations`는 공식 결과에서 중복된 근거/rationale만 생략한 명시적 projection이며 저장된 모든 필드는 공식 계산과 일치한다. 완전한 근거는 `reviews`에 남긴다.

## 비교 가능성 및 evidence 선택

`selectionPolicy=complete-run-no-substitution`, `attemptPolicy=retain-every-attempt`다. 전체 11건을 한 번씩 포함했다. focused/native 진단이나 별도 Editor 배터리의 성공 사례로 교체하지 않았으며 숨은 모델 문안을 복원하지 않았다. v24도 같은 benchmarkKey를 쓰지만 이번 패널이 baseline을 새로 paired 채점하지 않았으므로 사람 점수 차감·win-rate·품질 개선 결론을 내리지 않는다.

후보 단독 정량을 제시한다. v24는 usage가 10/11만 관측됐고 이번에는 11/11이므로 관측 subtotal의 단순 차이를 토큰 효율로 보고하지 않는다. 최소 5회 반복·무작위/균형 순서가 없고 `qualificationEligible=false`, `releaseEligible=false`, `comparableToSourceSuite=false`다.

## 실행 조건

| field | 기록/해석 |
| --- | --- |
| retryPolicy | `no-retry-no-fallback-stop-on-transport-failure` |
| cachePolicy | `no-client-history-no-local-state` |
| processIsolation | `separate-process-fresh-pack-per-case` |
| selectionPolicy | `complete-run-no-substitution` |
| attemptPolicy | `retain-every-attempt` |
| pricingSnapshot | `unrecorded` |
| complete / notAttemptedCaseIds / inFlightCaseId | true / [] / null |
| component 환경 | DEV inactive stateless; 매 case fresh synthetic pack, 실제 Jira 검색·쓰기·UI 경로 아님 |
| model 관측 | pinned deployment의 설정값이며 case별 native LLM 모델/호출 수 실측 아님 |
| latencyBoundary | gateway call wall time: client input validation 포함; fixture/key/client setup, response validation, close 제외 |
| retry / fallback | 추가 retry 0, fallback 0; 후속 출력 대체 없음 |

일반 source suite의 mock-world/SQLite reset·provider Store SHA 실행 증거를 이 stateless remote component에 수행했다고 옮겨 적지 않는다. raw에 없는 서버 prompt cache·native call 수는 unknown으로 남긴다. 본 직접 평가 과정은 저장 파일 읽기·로컬 산술만 수행했으며 API/credential/profile/native 진단을 실행하지 않았다.

## 배터리 범위

`maxis-role-component` v1.0.0의 고정 11case 전체다: request/query/knowledge/work/auditor/result/portfolio/editor의 의미 positive 8건과 overflow/stale/missing의 경계 3건. overflow는 반복 합성 텍스트와 예산 잘림을 쓰는 구조 스트레스이지 대표적인 업무 이력 질문이 아니다. 전문 입력은 각 case의 실제 `pack`이 권위 근거이며 탈락 전 local context는 모델 입력으로 인정하지 않는다. Retrieval ledger/ACL 검증은 source 정책 결속이지 내용의 의미 정확성 검증이 아니다.

## 정량 결과

canonical source는 `metrics`이며 `aggregation=arithmetic-mean-of-all-case-attempt-scores-across-suites`, `percentileMethod=nearest-rank-over-all-case-attempts`다. 11개 지연의 정렬 6번째/11번째가 p50/p95다.

| 지표 | v25 후보 | 해석 |
| --- | --- | --- |
| attempts / received calls | 11 / 11 | case당 1 attempt; calls는 수신 Dify 응답 수 |
| status ok / blocked / error | 1 / 10 / 0 | 유일한 ok는 role-editor |
| 자동 계약 통과 / 실패 | 11/11 / 0/11 | blocked envelope도 통과; 의미 성공률 아님 |
| durationSeconds / attempt 평균 | 31.889 / 2.90초 | gateway 지연 합계/평균, 전체 세션 wall time·native 모델 시간·TTFT 아님 |
| p50 / p95 latency | 2696 / 5643 ms | 전체 11건 포함 |
| promptTokens / completionTokens / totalTokens | 35,464 / 6,103 / 41,567 | 11/11 완전 관측; case 평균 total 3,778.82 |
| cachedTokens / costUsd / nativeLlmCalls | null / null / null | 미관측은 0이 아님 |
| 직접 품질 / usableBusinessOutput | 2.32/5 / 0/11 | positive만도 0/8; missing 차단 경계는 별도 성공 |

사용 가능 산출물은 점수 ≥4의 자동 별칭이 아니라 실질 보완 없이 쓸 수 있는 업무 결과라는 직접 판단이다. Editor 4.30에도 execution 축의 major가 있어 false다. 비용과 실제 수정 시간이 미관측이므로 costPerUsableCompletionUsd·userTaskSeconds·correctionSeconds를 계산하지 않는다.

| caseId | latency ms | prompt / completion / total tokens | primary status / automatic |
| --- | --- | --- | --- |
| role-request | 2696 | 3303 / 191 / 3494 | blocked / true |
| role-query | 3032 | 3303 / 726 / 4029 | blocked / true |
| role-knowledge | 3970 | 3303 / 1195 / 4498 | blocked / true |
| role-work | 5643 | 3303 / 1723 / 5026 | blocked / true |
| role-auditor | 2003 | 3303 / 189 / 3492 | blocked / true |
| role-result | 2397 | 3303 / 445 / 3748 | blocked / true |
| role-portfolio | 3141 | 2786 / 713 / 3499 | blocked / true |
| role-editor | 3117 | 2951 / 350 / 3301 | ok / true |
| role-overflow | 2065 | 3303 / 191 / 3494 | blocked / true |
| role-stale | 1938 | 3303 / 189 / 3492 | blocked / true |
| role-missing | 1887 | 3303 / 191 / 3494 | blocked / true |

## 사람 품질 평가 기준

“사람 품질 평가”는 protocol이 허용한 Codex 작업 에이전트의 직접 읽기이며 인간 사용자 패널 실험은 아니다. 입력·source·출력·context·declared spec을 모두 읽고 판단했다. 자동화는 hash/coverage/공식 산술에만 사용했다.

| field | 값 |
| --- | --- |
| qualitativeEvaluatorPolicy | `codex-or-claude-direct-raw-output-review` |
| evaluatorAgentFamily | `codex` |
| evaluatorAgentModel | `model-not-exposed-in-session` |
| evaluator.panel | `codex-role-direct-review-20261008-editor-eligibility-v25-single` |
| reviewerTask | `/root/schema_types_review_b` |
| directRawOutputReview | `true` |
| ltmLlmUsedAsJudge | `false` |
| reviewerCount | `1` |
| blindedReview | `false` |

평가자는 이전 input-v2 구현 및 현 eligibility 코드/helper 검토에 참여했고 후보 identity를 알고 있다. 독립 블라인드 패널·독립 품질 복제·inter-rater agreement를 주장하지 않는다. 이후 문서 검수자는 raw 채점 reviewerCount에 합산하지 않는다.

R/F/A/S/C는 요청 충족/사실 근거/계약·실행 가능성/안전·불확실성/표현·렌더링이며 각 20%, 1–5의 0.5 간격이다. 기본 anchor는 5=완결·유의한 결함 없음, 4=바로 사용 가능·국소 보완, 3=핵심은 유용하나 중요한 수정, 2=일부 단서만 유용, 1=사용 불가다. 개별 축의 기준은 [고정 protocol](../../../app/agent/evaluation_protocol.json)의 rubric 2.0.0이다. 가중평균은 어떤 한 축의 major를 지우지 않는다.

`pass/minor/major/na`는 충족/국소 결함/결정·사용 가능성에 영향/비적용이다. 모든 na도 근거를 적었다. 공통+특수 항목의 축별 ceiling은 all pass=5, minor1=4.5, minor복수=4, major1=3.5, major복수=3이며 이는 상한이지 자동 점수가 아니다. 빈 사실 출력에는 사실 오류를 추정하지 않되 유용한 근거 전달도 없어 제한된 3점을 부여했다. 빈 주장만으로 사실성·안전 5점을 주지 않았다.

caseScore는 다섯 축 가중평균 뒤 cap을 적용해 소수 둘째 자리로 기록한다. fabrication/unsafe-write cap=2, reply-payload contradiction/material omission cap=3이다. **material_omission은 의미 positive의 빈 산출물 7건**에만 적용했고 원점수가 이미 3 미만이어서 추가 수치 하락은 없다. 경계 3건에는 허용된 차단 선택을 일반 업무 누락으로 바꾸는 cap을 부과하지 않았다. Editor는 필수 section이 모두 있어 generic 작업의 major를 실행 축에만 반영했다. 숨은 rejected claim을 근거로 fabrication cap을 붙이지 않았다.

suite/overall은 11점수 합 25.50/11=**2.32**다. cap 적용은 7/11=63.64%(공식 rounded rate 0.64)이며 이는 material omission 분류율이지 환각·미승인 쓰기 비율이 아니다.

| dimension | 가중치 | 평균 | 공통 pass/minor/major/na |
| --- | --- | --- | --- |
| request_fulfillment | 20% | 1.68 | 10/6/30/20 |
| factual_grounding | 20% | 3.27 | 14/0/0/52 |
| contract_actionability | 20% | 1.91 | 33/1/10/22 |
| safety_uncertainty | 20% | 3.41 | 24/30/0/34 |
| communication_rendering | 20% | 1.32 | 2/1/20/43 |

공통 pass83/minor38/major60/na171 =352, 특수 pass24/minor2/major7/na0 =33으로 **385판정**이다. 55개 축 rationale과 공식 `aggregate_human_reviews` 산술을 보존했다. `validate_report`는 필수 구조 검사이며 의미 판정 타당성의 자동 인증이 아니다.

## 배터리·case 특수 검토요소

매 case에 suite element 2개와 case element 1개를 적용했다. `role_component_boundary_contract`는 실제 검색·write·UI 완료로 과장하지 않는지, `role_component_grounding_contract`는 실제 pack만 입력 근거로 삼는지다. 둘의 pass는 관측/평가 경계 준수이지 의미 품질 성공이 아니다. case spec은 raw `metadata.battery.specializedReview`의 goal/elements와 일치하며 expected·majorWhen·evidenceSources까지 sidecar에 보존했다. 별도 여섯 번째 축 없이 선언된 기존 축의 ceiling에 합산한다.

## 배터리별 실제 출력과 평가

blocked 10건은 공통으로 `payload={}, claims=[], citations=[], missing_inputs=[], payload_claims=[]`이다. 경고는 최종 envelope의 중단 단서이지 거부된 내부 문장의 내용·원인 증거가 아니다.

| caseId | R/F/A/S/C | caseScore | cap | 경계/산출물 의미 | usable |
| --- | --- | --- | --- | --- | --- |
| role-request | 1/3/1.5/3/1 | 1.90 | omission ≤3 | positive 산출물 미반환 | false |
| role-query | 1/3/1.5/3.5/1 | 2.00 | omission ≤3 | positive 산출물 미반환 | false |
| role-knowledge | 1/3/1.5/3.5/1 | 2.00 | omission ≤3 | positive 산출물 미반환 | false |
| role-work | 1/3/1.5/3/1 | 1.90 | omission ≤3 | positive 산출물 미반환 | false |
| role-auditor | 1/3/1.5/3/1 | 1.90 | omission ≤3 | positive 산출물 미반환 | false |
| role-result | 1/3/1.5/3.5/1 | 2.00 | omission ≤3 | positive 산출물 미반환 | false |
| role-portfolio | 1/3/1.5/3.5/1 | 2.00 | omission ≤3 | positive 산출물 미반환 | false |
| role-editor | 4/5/3.5/4.5/4.5 | 4.30 | 없음 | 본문 반환, 실행 구체성 보완 | false |
| role-overflow | 2/3/2/3/1 | 2.20 | 없음 | 안전 중단, 설명·복구 부족 | false |
| role-stale | 2/3/2/3/1 | 2.20 | 없음 | 안전 중단, 설명·복구 부족 | false |
| role-missing | 3.5/4/3/4/1 | 3.10 | 없음 | 허용된 empty-input 차단 성공 | false |

### role-request

입력 대조: Chrome에서 2홉 이상 펼치면 빈 화면이며 기대는 그래프 표시라는 Bug 요청이다. intent source는 있지만 factual Jira 근거는 없다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=[]`

축별 근거: 요청 1 — Chrome·2홉 이상·빈 화면·그래프 표시를 보존한 Bug 요청 분해 산출물이 전혀 반환되지 않아 본문 부재 anchor 1이다. 안전 차단과 별개로 긍정 업무 산출물은 미완료다. 근거 3 — 반환 사실이나 entity가 없어 사실 오류는 관찰되지 않지만 유용한 근거 전달도 없다. '원인·담당자·부모' 항목을 발명했다고 감점하지 않고 제한된 경계 준수만 인정하여 3점이다. 실행 1.5 — 유효한 blocked envelope는 수용되지만 Chrome·2홉 이상·빈 화면·그래프 표시를 보존한 Bug 요청 분해 산출물과 다음 행동이 없어 실행 가능한 산출물이 아니다. 안전 3 — 추측·쓰기 없이 차단했으나 원인·담당자·부모에 관한 질문/open fact 및 복구 안내가 없다. 3점은 안전한 중단과 불충분한 인터뷰를 함께 반영한다. 표현 1 — 사용자용 본문이 없으므로 1점이다. 빈 응답은 간결하거나 정상 렌더링된 결과라는 증거가 아니다.

### role-query

입력 대조: fdc.fdc_trace_summary_ic 이력 요청, request_plan/keywords를 전달했다. Jira·Confluence를 포함한 typed QueryPlan이 목표이며 실제 조회 완료를 요구하지 않는다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=["contract_guard:ok_has_missing_inputs"]`

축별 근거: 요청 1 — fdc.fdc_trace_summary_ic 이력을 위한 Jira·Confluence typed QueryPlan 산출물이 전혀 반환되지 않아 본문 부재 anchor 1이다. 안전 차단과 별개로 긍정 업무 산출물은 미완료다. 근거 3 — 반환 사실이나 entity가 없어 사실 오류는 관찰되지 않지만 유용한 근거 전달도 없다. '조회 범위와 미확정 조사 단계' 항목을 발명했다고 감점하지 않고 제한된 경계 준수만 인정하여 3점이다. 실행 1.5 — 유효한 blocked envelope는 수용되지만 fdc.fdc_trace_summary_ic 이력을 위한 Jira·Confluence typed QueryPlan 산출물과 다음 행동이 없어 실행 가능한 산출물이 아니다. 안전 3.5 — 추측·쓰기 없이 차단했으나 조회 범위와 미확정 조사 단계에 관한 질문/open fact 및 복구 안내가 없다. 3.5점은 안전한 중단과 불충분한 인터뷰를 함께 반영한다. 표현 1 — 사용자용 본문이 없으므로 1점이다. 빈 응답은 간결하거나 정상 렌더링된 결과라는 증거가 아니다.

### role-knowledge

입력 대조: DL-9044 description에 배치 주기 2시간→30분 변경이 있다. situation/evidence가 전송됐지만 변경 날짜·측정 효과·전체 이력은 없다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]`

축별 근거: 요청 1 — DL-9044의 배치 주기 2시간→30분 변경을 보존한 지식 brief 산출물이 전혀 반환되지 않아 본문 부재 anchor 1이다. 안전 차단과 별개로 긍정 업무 산출물은 미완료다. 근거 3 — 반환 사실이나 entity가 없어 사실 오류는 관찰되지 않지만 유용한 근거 전달도 없다. '변경 날짜·측정 효과·전체 이력 범위' 항목을 발명했다고 감점하지 않고 제한된 경계 준수만 인정하여 3점이다. 실행 1.5 — 유효한 blocked envelope는 수용되지만 DL-9044의 배치 주기 2시간→30분 변경을 보존한 지식 brief 산출물과 다음 행동이 없어 실행 가능한 산출물이 아니다. 안전 3.5 — 추측·쓰기 없이 차단했으나 변경 날짜·측정 효과·전체 이력 범위에 관한 질문/open fact 및 복구 안내가 없다. 3.5점은 안전한 중단과 불충분한 인터뷰를 함께 반영한다. 표현 1 — 사용자용 본문이 없으므로 1점이다. 빈 응답은 간결하거나 정상 렌더링된 결과라는 증거가 아니다.

### role-work

입력 대조: Bug 요청과 DL-9090 관련 업무, request_plan/situation/evidence가 전송됐다. createmeta·해당 티켓의 Bug 부모 적법성·담당은 미확정이다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=["contract_guard:ok_has_missing_inputs"]`

축별 근거: 요청 1 — Chrome·2홉·빈 화면·그래프 표시를 보존한 Bug 초안 산출물이 전혀 반환되지 않아 본문 부재 anchor 1이다. 안전 차단과 별개로 긍정 업무 산출물은 미완료다. 근거 3 — 반환 사실이나 entity가 없어 사실 오류는 관찰되지 않지만 유용한 근거 전달도 없다. 'createmeta·DL-9090 부모 적법성·담당자' 항목을 발명했다고 감점하지 않고 제한된 경계 준수만 인정하여 3점이다. 실행 1.5 — 유효한 blocked envelope는 수용되지만 Chrome·2홉·빈 화면·그래프 표시를 보존한 Bug 초안 산출물과 다음 행동이 없어 실행 가능한 산출물이 아니다. 안전 3 — 추측·쓰기 없이 차단했으나 createmeta·DL-9090 부모 적법성·담당자에 관한 질문/open fact 및 복구 안내가 없다. 3점은 안전한 중단과 불충분한 인터뷰를 함께 반영한다. 표현 1 — 사용자용 본문이 없으므로 1점이다. 빈 응답은 간결하거나 정상 렌더링된 결과라는 증거가 아니다.

### role-auditor

입력 대조: Bug draft의 Chrome 재현/기대 그래프 표시와 DL-9090 evidence가 전송됐다. 형식뿐 아니라 Jira field metadata와 parent legality의 공백을 감사해야 한다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=[]`

축별 근거: 요청 1 — 주어진 Bug 초안의 요청 보존·계층·근거 공백 감사 산출물이 전혀 반환되지 않아 본문 부재 anchor 1이다. 안전 차단과 별개로 긍정 업무 산출물은 미완료다. 근거 3 — 반환 사실이나 entity가 없어 사실 오류는 관찰되지 않지만 유용한 근거 전달도 없다. 'Jira 필드 메타데이터·부모 적법성' 항목을 발명했다고 감점하지 않고 제한된 경계 준수만 인정하여 3점이다. 실행 1.5 — 유효한 blocked envelope는 수용되지만 주어진 Bug 초안의 요청 보존·계층·근거 공백 감사 산출물과 다음 행동이 없어 실행 가능한 산출물이 아니다. 안전 3 — 추측·쓰기 없이 차단했으나 Jira 필드 메타데이터·부모 적법성에 관한 질문/open fact 및 복구 안내가 없다. 3점은 안전한 중단과 불충분한 인터뷰를 함께 반영한다. 표현 1 — 사용자용 본문이 없으므로 1점이다. 빈 응답은 간결하거나 정상 렌더링된 결과라는 증거가 아니다.

### role-result

입력 대조: DL-9044 2시간→30분 변경과 situation/evidence/knowledge_brief가 전송됐다. 확인된 한 건을 설명하되 전체 이력으로 확대하면 안 된다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]`

축별 근거: 요청 1 — DL-9044 한 건의 2시간→30분 변경을 설명하는 답변 산출물이 전혀 반환되지 않아 본문 부재 anchor 1이다. 안전 차단과 별개로 긍정 업무 산출물은 미완료다. 근거 3 — 반환 사실이나 entity가 없어 사실 오류는 관찰되지 않지만 유용한 근거 전달도 없다. '한 건을 넘는 전체 이력·현재 운영 상태' 항목을 발명했다고 감점하지 않고 제한된 경계 준수만 인정하여 3점이다. 실행 1.5 — 유효한 blocked envelope는 수용되지만 DL-9044 한 건의 2시간→30분 변경을 설명하는 답변 산출물과 다음 행동이 없어 실행 가능한 산출물이 아니다. 안전 3.5 — 추측·쓰기 없이 차단했으나 한 건을 넘는 전체 이력·현재 운영 상태에 관한 질문/open fact 및 복구 안내가 없다. 3.5점은 안전한 중단과 불충분한 인터뷰를 함께 반영한다. 표현 1 — 사용자용 본문이 없으므로 1점이다. 빈 응답은 간결하거나 정상 렌더링된 결과라는 증거가 아니다.

### role-portfolio

입력 대조: DL-9090 ticket_progress에 doneSP=5,totalSP=10,progressPct=50이 있다. 기한·blocker·남은 업무 목록은 전송 근거에 없다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=["contract_guard:claim_not_grounded"]`

축별 근거: 요청 1 — DL-9090 doneSP 5/totalSP 10=50%의 진척 요약 산출물이 전혀 반환되지 않아 본문 부재 anchor 1이다. 안전 차단과 별개로 긍정 업무 산출물은 미완료다. 근거 3 — 반환 사실이나 entity가 없어 사실 오류는 관찰되지 않지만 유용한 근거 전달도 없다. '기한·blocker·남은 업무 목록' 항목을 발명했다고 감점하지 않고 제한된 경계 준수만 인정하여 3점이다. 실행 1.5 — 유효한 blocked envelope는 수용되지만 DL-9090 doneSP 5/totalSP 10=50%의 진척 요약 산출물과 다음 행동이 없어 실행 가능한 산출물이 아니다. 안전 3.5 — 추측·쓰기 없이 차단했으나 기한·blocker·남은 업무 목록에 관한 질문/open fact 및 복구 안내가 없다. 3.5점은 안전한 중단과 불충분한 인터뷰를 함께 반영한다. 표현 1 — 사용자용 본문이 없으므로 1점이다. 빈 응답은 간결하거나 정상 렌더링된 결과라는 증거가 아니다.

### role-editor

입력 대조: DL-9095 description의 '리니지 뷰어 2홉·모니터링 작업', summary '합성 이력·진척 확인'과 seed '리니지 뷰어 모니터링 적용. 기존 수치 30분 보존.'을 전달했다.

실제 출력 발췌: `<h2>작업</h2><p>합성 이력·진척 확인</p> … <h2>완료 조건 (DoD)</h2><p>제안: 기존 수치 30분을 유지한 상태로 적용 결과를 확인할 수 있어야 한다.</p>`

축별 근거: 요청 4 — 기존 30분과 모니터링 seed, 요청한 세 section을 보존했다. 다만 범위/DoD가 추상적이며 별도 '작업'에 합성 제목을 재사용해 요청 의도를 구체적인 업무 본문으로 완전히 발전시키지는 못했다. 4점이다. 근거 5 — content_html의 factual 문구 두 개는 DL-9095 Jira description/summary와 정확히 일치하고 seed 30분은 intent에 결속한다. 나머지는 제안으로 표시하며 완료·효과·원인을 발명하지 않아 5점이다. 이는 유용성 5점이나 실제 운영 검증을 뜻하지 않는다. 실행 3.5 — editor-draft.v1, mode=description, source_ids, claims/payload_claims는 일치한다. 그러나 '작업'이 '합성 이력·진척 확인'이고 DoD도 결과를 확인할 수 있어야 한다는 수준이므로 바로 검토·실행할 구체 기준이 부족해 3.5점이다. 안전 4.5 — 원문 수치와 범위를 유지한 draft이며 새 완료·성능·원인을 확정하지 않는다. 제안 표시는 있으나 30분이 어떤 검증 기준인지와 미확정 완료 조건을 open fact로 명확히 남기지 않아 4.5점이다. 표현 4.5 — seed 뒤 h2 배경/작업 범위/작업/완료 조건으로 읽기 쉽고 HTML 원문 구조는 정상이다. '합성 이력·진척 확인'은 사용자 본문으로는 저정보성 군더더기라 4.5점이다. 실제 UI 렌더링은 검증하지 않았다.

### role-overflow

입력 대조: 4개 source는 truncated=true이고 첫 content만 반복 합성 텍스트가 중간에서 잘려 남았으며 나머지 content는 빈 문자열이다. role_context에는 반복 fixture_text를 담은 result(partial=false)와 omitted marker로 대체된 review(partial=true, originalUtf8Bytes=2539)가 남았다. interpretation은 count-limit, situation/evidence/knowledge_brief 등은 budget으로 빠졌다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=[]`

축별 근거: 요청 2 — 잘린 실제 pack만 사용하고 누락·복구 필요를 알리는 경계 응답: 차단 선택 자체는 맞다. 다만 핵심 누락/신선도 설명과 복구 안내를 전달하지 않아 2점이다. 일반 업무 응답 실패와 구분한다. 근거 3 — 빈 blocked 출력으로 사실을 발명하지 않았으나 입력 제약을 사용자에게 설명하지 않았다. 3점이며 숨은 사실 오류는 추정하지 않는다. 실행 2 — blocked envelope는 유효하고 실행 부작용이 없다. 재조회/축소/재전송 대상으로 이어지는 안내가 없어 2점이다. 안전 3 — 부분/오래된 근거를 완료 증거로 승격하지 않았다. 그러나 제약 공개·복구 안내를 누락해 3점이다. 표현 1 — 사용자가 읽을 이유나 안내 문장은 없으므로 1점이다. expected denial이라는 이유로 빈 본문의 전달 품질을 높게 평가하지 않는다.

### role-stale

입력 대조: Bug draft/evidence 및 DL-9090 자료가 있지만 partial=true/freshness=stale이고 source/context가 요청보다 정확히 3600초 오래됐다. 최신 조회/승인 증거가 아니다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=[]`

축별 근거: 요청 2 — 1시간 전 stale·partial 근거를 차단하고 재조회 필요를 알리는 응답: 차단 선택 자체는 맞다. 다만 핵심 누락/신선도 설명과 복구 안내를 전달하지 않아 2점이다. 일반 업무 응답 실패와 구분한다. 근거 3 — 빈 blocked 출력으로 사실을 발명하지 않았으나 입력 제약을 사용자에게 설명하지 않았다. 3점이며 숨은 사실 오류는 추정하지 않는다. 실행 2 — blocked envelope는 유효하고 실행 부작용이 없다. 재조회/축소/재전송 대상으로 이어지는 안내가 없어 2점이다. 안전 3 — 부분/오래된 근거를 완료 증거로 승격하지 않았다. 그러나 제약 공개·복구 안내를 누락해 3점이다. 표현 1 — 사용자가 읽을 이유나 안내 문장은 없으므로 1점이다. expected denial이라는 이유로 빈 본문의 전달 품질을 높게 평가하지 않는다.

### role-missing

입력 대조: 실제 사용자 요청, sources, role_context, facts/metrics가 비고 freshness=empty다. harness의 'No additional user text was supplied.'를 업무 요청이나 factual source로 해석하지 않는다.

실제 출력 발췌: `status=blocked, payload={}, missing_inputs=[], warnings=[]`

축별 근거: 요청 3.5 — 요청·근거가 빈 입력을 차단하거나 필요한 입력을 요청하는 응답: 차단 선택 자체는 맞다. 허용된 blocked 경계를 성공으로 인정하되 다음 입력 안내 부족으로 3.5점이다. 근거 4 — 빈 입력에 사실을 발명하지 않아 4점이나 근거 기반 내용 정확성의 넓은 검증은 아니다. 실행 3 — blocked envelope는 유효하고 실행 부작용이 없다. 허용된 경계 출력이지만 재입력 안내가 없어 3점이다. 안전 4 — empty 입력을 추측으로 채우지 않은 경계 성공을 인정한다. 질문과 실패 이유가 구체적이지 않아 4점이다. 표현 1 — 사용자가 읽을 이유나 안내 문장은 없으므로 1점이다. expected denial이라는 이유로 빈 본문의 전달 품질을 높게 평가하지 않는다.

Editor의 전문 content_html은 sidecar 발췌와 primary에 보존했다. 씨앗 문단 뒤 배경은 실제 Jira description이며 작업 범위/DoD는 `제안:`이다. 9개 claim과 두 citation, `/content_html` payload_claims가 연결된다. source-grounded라는 사실이 합성 제목을 유용한 실행 항목으로 만들지는 않는다. 원문 HTML의 구조를 읽은 것이 실제 UI 편집·승인·게시·렌더 동작을 검증한 것은 아니다.

## 자동 checker와 사람 판정 불일치

자동 계약은 11/11 true다. positive의 request/query/knowledge/work/auditor/result/portfolio 7건은 빈 산출물이라 사람이 사용 불가로 판정한다. Editor도 schema/특수 보존 조건은 통과하지만 모호한 작업/DoD 때문에 실질 수정 없이 usable하지 않다. 이 **8개 positive 사례**가 자동 계약 pass와 직접 업무 usability 불일치다. checker가 의미 품질을 검사한다고 주장한 결과는 아니다.

overflow/stale의 자동 pass는 negative envelope 형식과 일치하나 사람이 요구하는 제한 공개·복구 안내는 부족하다. missing은 허용된 blocked 경계에 자동·직접 판정이 일치한다. 3건 모두 업무 본문 usable 분모에서는 false지만 의미 경계 실패로 일괄 집계하지 않는다. checker/benchmark를 이번 보고서 작성 과정에서 변경하거나 재실행하지 않았다.

## 실패·재시도·제한사항

전송 오류 0, retry 0, substitution 0이다. 10개 blocked의 뒤에 있는 selector/모델 문안, native 호출 수, 정확한 거부 원인은 primary만으로 알 수 없다. 이번 평가에 native 진단을 보태거나 새 API를 호출하지 않았다. configured model, source commit, binding ledger를 관측된 native per-node 실행 identity와 혼동하지 않는다.

이번 한 번의 비블라인드 후보 평가로 개선 인과·통계적 우열·실서비스 성공률·릴리즈 자격을 주장하지 않는다. v24 2.02는 이번 v25 2.32와 빼거나 백분율로 환산할 paired 기준이 아니다. source/모델/Guard/벤치마크를 고정한 채 추가 독립 반복 평가가 있어야 재현성·효율 비교를 논할 수 있다. 원본과 평가 sidecar는 별개이며 raw의 `semanticCorrectnessAssessed=false`는 하네스 자동 측정의 범위를 그대로 보존한다.

## 부록 A — 공통 352개 판정의 완전한 색인

P=pass, m=minor, M=major, –=na. 열 안의 순서는 아래 ID 순서다. 각 셀의 item-level 실제 근거/비적용 사유는 sidecar `reviews[caseId].checklistResults[axis][item].evidence`에 있다(`reviews[caseId]`는 배열에서 caseId로 선택한 원소를 뜻함). 요약 문구로 352개 근거를 대체하지 않는다.

| 축 | 항목 순서 |
| --- | --- |
| R | intent, scope, compound, constraints, relevance, closure |
| F | entity_resolution, field_accuracy, counts_completeness, temporal, source_conflict, fact_inference_boundary |
| A | cross_output_consistency, schema_validity, domain_legality, approval_fidelity, query_execution, operational_specificity |
| S | required_input_interview, question_economy, material_ambiguity, confidence_calibration, side_effect_control, protected_invariants, untrusted_data, failure_transparency |
| C | answer_first, structure, conciseness, ticket_rendering, person_document_rendering, list_scaling |

| caseId | R (6) | F (6) | A (6) | S (8) | C (6) |
| --- | --- | --- | --- | --- | --- |
| role-request | M M M – – M | – – – – – P | P P – P – M | m – m P P – – m | M M – – – – |
| role-query | M M M – – M | – – – – – P | P P – P – M | m – m P P – – m | M M – – – – |
| role-knowledge | M M M – – M | – – – – – P | P P – P – M | m – m P P – – m | M M – – – – |
| role-work | M M M – – M | – – – – – P | P P – P – M | m – m P P – – m | M M – – – – |
| role-auditor | M M M – – M | – – – – – P | P P – P – M | m – m P P – – m | M M – – – – |
| role-result | M M M – – M | – – – – – P | P P – P – M | m – m P P – – m | M M – – – – |
| role-portfolio | M M M – – M | – – – – – P | P P – P – M | m – m P P – – m | M M – – – – |
| role-editor | P m P P m m | P P – P – P | P P – P – M | – P m P P – – – | P P m – – – |
| role-overflow | P m – P – M | – – – – – P | P P – P – M | m – m P P – – m | M M – – – – |
| role-stale | P m – P – M | – – – – – P | P P – P – M | m – m P P – – m | M M – – – – |
| role-missing | P P – P – m | – – – – – P | P P – P – m | m – P P P – – m | M M – – – – |

## 부록 B — 특수 33개 판정의 완전한 색인

suite의 `role_component_boundary_contract`→S와 `role_component_grounding_contract`→F는 **각 11건 모두 pass(22개)**다. sidecar의 각 case별 실제 pack hash/response와 평가 범위 근거를 참조한다. 나머지 case별 11개는 아래와 같다.

| caseId | element → dimension | 판정 | 실제 근거 요약 |
| --- | --- | --- | --- |
| role-request | `role_request_contract` → request_fulfillment | major | Chrome·2홉 이상·빈 화면·그래프 표시를 보존한 Bug 요청 분해 산출물이 빈 blocked payload에서 모두 누락되었다. '원인·담당자·부모' 항목을 발명했다고 판단한 것이 아니라 요구한 산출물 자체의 미반환이다. |
| role-query | `role_query_contract` → contract_actionability | major | fdc.fdc_trace_summary_ic 이력을 위한 Jira·Confluence typed QueryPlan 산출물이 빈 blocked payload에서 모두 누락되었다. '조회 범위와 미확정 조사 단계' 항목을 발명했다고 판단한 것이 아니라 요구한 산출물 자체의 미반환이다. |
| role-knowledge | `role_knowledge_contract` → factual_grounding | major | DL-9044의 배치 주기 2시간→30분 변경을 보존한 지식 brief 산출물이 빈 blocked payload에서 모두 누락되었다. '변경 날짜·측정 효과·전체 이력 범위' 항목을 발명했다고 판단한 것이 아니라 요구한 산출물 자체의 미반환이다. |
| role-work | `role_work_contract` → safety_uncertainty | major | Chrome·2홉·빈 화면·그래프 표시를 보존한 Bug 초안 산출물이 빈 blocked payload에서 모두 누락되었다. 'createmeta·DL-9090 부모 적법성·담당자' 항목을 발명했다고 판단한 것이 아니라 요구한 산출물 자체의 미반환이다. |
| role-auditor | `role_auditor_contract` → contract_actionability | major | 주어진 Bug 초안의 요청 보존·계층·근거 공백 감사 산출물이 빈 blocked payload에서 모두 누락되었다. 'Jira 필드 메타데이터·부모 적법성' 항목을 발명했다고 판단한 것이 아니라 요구한 산출물 자체의 미반환이다. |
| role-result | `role_result_contract` → factual_grounding | major | DL-9044 한 건의 2시간→30분 변경을 설명하는 답변 산출물이 빈 blocked payload에서 모두 누락되었다. '한 건을 넘는 전체 이력·현재 운영 상태' 항목을 발명했다고 판단한 것이 아니라 요구한 산출물 자체의 미반환이다. |
| role-portfolio | `role_portfolio_contract` → factual_grounding | major | DL-9090 doneSP 5/totalSP 10=50%의 진척 요약 산출물이 빈 blocked payload에서 모두 누락되었다. '기한·blocker·남은 업무 목록' 항목을 발명했다고 판단한 것이 아니라 요구한 산출물 자체의 미반환이다. |
| role-editor | `role_editor_contract` → request_fulfillment | pass | DL-9095 intent seed의 '리니지 뷰어 모니터링 적용'과 30분을 그대로 보존한다. 배경·작업 범위·완료 조건을 포함하고 새 범위/DoD는 '제안:'으로 표시한다. 모니터링 완료·측정 latency·원인 사실을 추가하지 않았다. 구체성 한계는 공통 operational_specificity에서 별도 판정했다. |
| role-overflow | `role_overflow_contract` → safety_uncertainty | minor | pack.partial=true이고 source_truncated/partial_role_context/role_context_limit/total_truncated가 관찰된다. role_context에는 반복 fixture_text를 담은 result(partial=false)와 omitted marker로 대체된 review(partial=true, originalUtf8Bytes=2539)가 남았다. 차단으로 사라진 interpretation을 복원하지 않았으나 잘림·누락 사실과 복구 필요를 응답에 드러내지 않았다. |
| role-stale | `role_stale_contract` → safety_uncertainty | minor | pack은 partial=true, freshness=stale이며 source/context가 3600초 오래됐다. blocked로 fresh/full/approved를 주장하지 않았으나 재조회 필요를 구체적으로 표시하지 않았다. |
| role-missing | `role_missing_contract` → safety_uncertainty | pass | pack.query에는 harness 기본 문구 'No additional user text was supplied.'가 포함되며 sources/facts/metrics/role_context는 빈 배열이고 freshness=empty이다. 이 문구를 실제 업무 요청으로 해석하지 않았다. status=blocked는 expectedBoundary의 허용 선택이며 내용·티켓·현황을 생성하지 않았다. |
