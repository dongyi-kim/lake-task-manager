# MAXIS Editor v13 — 새 baseline 직접 평가

최종 응답 5개를 요청·합성 source·공통/특수 계약과 대조했다. 실제 사용 가능 **0/5**, 직접 평가 평균 **2.52/5**다. 자동 pass는 3/5이나 세 초안 모두 핵심 수정이 필요하다.

## 측정 식별자

| 필드 | 값 |
|---|---|
| protocolVersion | `3.0.0` |
| rubricVersion | `2.0.0` |
| batteryVersion | `4.0.0` |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| runKind | `exploratory` |
| runGroupId | `maxis-editor-20260915T053427Z-63d18b59` |
| repetitions | `1` |
| candidateCommit | `e1548d39fa4c0d3e6fb3d5a0351838ca04aae4a0` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| promptVersion | `ltm-maxis-dify-2026.09.14-editor-plan-v13` |
| model | `gpt-5.4-mini` |
| simpleModel | `not-used` |
| provider | `dify` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| structuredOutputBackend | `dify` |
| structuredBackendVersion | `legacy-internal` |
| structuredBackendFallbackPolicy | `forbid` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| candidateOrderIndex | `1` |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state` |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` |
| candidateKey | `35cc69b7e774386df3e8da13104a90a3abf2254be2c04b9b0e5a128de9aca231` |
| comparabilityKey | `85a5147fd6692628a42948c2a576763cd2ac9d1393ec942c333ee850fe5a297f` |

## 비교 가능성 및 evidence 선택

selectionPolicy: `complete-run-no-substitution`. 이번 fresh run의 CMP2·CMP3·CMP6·CMP8·CMP9 다섯 응답을 모두 평가했다. 이전 verdict·점수를 재사용하지 않았으며 후속 성공으로 실패를 대체하지 않는다. 후보가 제공되면 동일 evaluator task에서 별도 판정한다. 이전 [보고서](maxis-editor-20260914T035709Z-baseline.md)는 구조 참고이며 이 문서에는 과거 점수 증감을 계산하지 않았다.

- Primary raw: `.cache/agent-evaluation/maxis-editor-20260915T053427Z-63d18b59/baseline.json`; SHA-256 `eb07e9ffd61dbdb7c8f60ff5e12bd499f7db5bea9d3a45ed72689fb4700c3131`.
- Review: [전체 항목 근거 JSON](../../../.cache/agent-evaluation/maxis-editor-20260915T053427Z-63d18b59/direct-review-baseline.json); SHA-256 `0632b5aac3f25b26e0ada1a6c873157e541a1c517d3fec9a7b0390ffe6ce47ca`.
- metadata의 run batteryVersion은 4.0.0, 재사용 input의 component_battery_version은 2.0.0이다. 원문과 checker를 고치지 않고 이 차이를 보존했다. `comparableToSourceSuite=false`.

## 실행 조건

DEV 비활성·stateless Editor component, fixed synthetic evidence, 한 번의 탐색 실행이다. candidateDirty=false, bindingUnchanged=true이며 production qualification은 아니다. 평가자는 추가 원격 API·재시도·write를 실행하지 않았다. 부모 티켓·자식/연결 티켓·설계 문서의 세 source 본문을 읽었고 case 간 동일 SHA를 대조했다.

| source | 원본 content SHA-256 |
|---|---|
| fixture.ticket.DL-9090 | `c997feb13a4eb486d0b184cc8945006f71996eb319e3831d74d7c1554a7df3a6` |
| fixture.ticket.lineage-family | `1909d0da2de0deb1d85c9fc6b540442e0dd468cb0d2bfee56555bc8a96988762` |
| fixture.document.lineage | `0ea07eca1a3f99d49534f938c26ede95df84f4c77f9a089649e369e8bc421c30` |

## 배터리 범위

5/5 case, repeatIndex=1, 누락 0개. 실제 response의 claims/citations/payload 및 rendered HTML을 모두 직접 읽었다. 차단 전 plan은 이번 raw에 없어 원인을 추정하거나 가상의 결과를 채점하지 않았다. full compose·실시간 Jira 조회·브라우저 UI·reference resolver·write 승인은 범위 밖이다.

## 정량 결과

| 지표 | 결과 |
|---|---:|
| actual ok draft / blocked | 3 / 2 |
| automatic pass / 직접 usable | 3/5 / 0/5 |
| 직접 평균 | 2.52/5 |
| 자동 green·직접 unusable | 3 |
| 공통 pass / minor / major / na | 43 / 19 / 47 / 51 |
| 특수 pass / minor / major / na | 4 / 2 / 9 / 0 |
| material_omission cap 코드 발생 | 4/5 |
| 수신 Dify calls / attempts | 5 / 5 |
| prompt / completion / total tokens | 19,985 / 3,198 / 23,183 |
| metrics.durationSeconds 합계 | 18.5초 |
| metrics 기준 latency p50 / p95 | 3.7초 / 4.8초 |
| network 실패 / retry | 0 / 0 |

aggregation: 각 축 0.2 가중평균 후 cap 적용, 전체 5개 case-attempt의 산술평균. percentileMethod: `nearest-rank-over-all-case-attempts`; 정량 집계 source는 공통 `metrics`만 사용했다. cachedTokens·costUsd=null은 미관측이며 0이 아니다. calls는 수신 응답 수이고 Dify 내부 모델 step 수는 아니다. 치명 코드 발생 80%는 material_omission에 의한 값이며 사실 발명·미승인 write를 관측했다는 뜻은 아니다.

| case | 요청 | 사실 | 계약 | 안전 | 표현 | raw mean | cap | case score | 사용 가능 |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| CMP2 | 1 | 3 | 2 | 3 | 1 | 2.00 | material_omission:3 | 2.00 | 아니오 |
| CMP3 | 2.5 | 5 | 2.5 | 3 | 2.5 | 3.10 | material_omission:3 | 3.00 | 아니오 |
| CMP6 | 1 | 3 | 2 | 3 | 1 | 2.00 | material_omission:3 | 2.00 | 아니오 |
| CMP8 | 3 | 3 | 3.5 | 3.5 | 2.5 | 3.10 | 없음 | 3.10 | 아니오 |
| CMP9 | 1.5 | 2.5 | 2 | 3.5 | 3 | 2.50 | material_omission:3 | 2.50 | 아니오 |
| 전체 축 평균 | 1.80 | 3.30 | 2.40 | 3.20 | 2.00 | — | — | 2.52 | 0/5 |

## 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`; evaluatorAgentFamily: `codex`.
evaluatorAgentModel: `current Codex agent; exact runtime model ID not exposed`; evaluator task: `/root/scope_quality_review`.
directRawOutputReview: `true`; ltmLlmUsedAsJudge: `false`.
reviewerCount: `1`; blindedReview: `false`.

평가 전담 agent이며 이번 구현을 수정하지 않았다. 루트 구현 agent와 별도이나 후보 정보를 가리지 않은 단일 reviewer라는 한계가 있다. 실제 필요한 초안 또는 결과를 완성할 수 있는 구체적 필수 사실 질문만 usable로 본다. 막연한 재위임 질문·실질 재작성 필요 결과는 usable이 아니다. 빈 blocked 결과에는 보이지 않은 계획의 환각을 귀속하지 않으며 사실성 성공으로 보상하지도 않는다.

각 축 1–5점, 0.5 간격, 가중치 20%; 공통·특수 checklist 결함을 합산해 상한을 적용했다. 자동 도구는 `tools.agent_eval_protocol.score_human_case`·`aggregate_human_reviews`로 누락·상한·산술만 검사했다. `pass`는 관측한 행동, `na`는 구체적인 적용 불가 사유를 기록한다. 공통 160개와 특수 15개를 모두 검증했다.

공통 32항목을 각 case에 적용한 160개 판정의 축별 집계다. 항목별 실제 근거는 위 [전체 항목 근거 JSON](../../../.cache/agent-evaluation/maxis-editor-20260915T053427Z-63d18b59/direct-review-baseline.json)에 보존했다.

| 축 | 항목/case | 5개 case 판정 | pass | minor | major | na |
|---|---:|---:|---:|---:|---:|---:|
| 요청 충족 | 6 | 30 | 3 | 4 | 21 | 2 |
| 사실·근거 | 6 | 30 | 7 | 5 | 5 | 13 |
| 계약·실행 | 6 | 30 | 15 | 0 | 5 | 10 |
| 안전·불확실성 | 8 | 40 | 16 | 4 | 6 | 14 |
| 표현·렌더링 | 6 | 30 | 2 | 6 | 10 | 12 |

## 배터리·case 특수 검토요소

| case | 요소 | 판정 | 실제 근거 |
|---|---|---|---|
| CMP2 | editor_context_preservation | major | DL-9095 배경·범위·작업·DoD의 핵심 의미가 빈 HTML에 전혀 보존되지 않았다. |
| CMP2 | editor_reference_fidelity | major | 현재 티켓·설계 근거와 연결한 사용자용 본문이 전혀 없다. |
| CMP2 | cmp2_four_sections | major | 배경·범위·작업·DoD가 전부 없다. 차단 전 내부 plan을 전달된 사실 발명으로 채점하지 않는다. |
| CMP3 | editor_context_preservation | pass | 전체 visible seed를 첫 문단에 글자 그대로 유지한다. |
| CMP3 | editor_reference_fidelity | pass | 유일한 인용은 eval.editor.intent에 연결되고 보존한 seed와 일치한다. 새로운 사람·문서 링크를 주장하지 않는다. |
| CMP3 | cmp3_seed_exactness | pass | 정확 seed와 '확인 필요'를 모두 유지하며 p95 방향·수치·원인을 만들어내지 않았다. 질문의 실용성 결함은 공통 intent·interview 항목에서 판정했다. |
| CMP6 | editor_context_preservation | major | DL-9090 담당 mention·2홉 100노드·설계 문서 검토 댓글의 핵심 의미가 빈 HTML에 전혀 보존되지 않았다. |
| CMP6 | editor_reference_fidelity | major | 입력의 resolved 담당자·설계 문서가 실제 mention/link로 하나도 전달되지 않았다. |
| CMP6 | cmp6_review_target | major | 담당 mention·2홉 100노드 검토 기준·설계 문서 link 모두 누락됐다. |
| CMP8 | editor_context_preservation | pass | 부모 원문의 테이블 간 탐색·조회 전용·업/다운 2홉·문서 기록 및 설계의 3홉 제외를 보존했다. |
| CMP8 | editor_reference_fidelity | minor | 설계 문서 href는 정확하나 DL-9090·DL-9092가 raw key이며 final references=[]다. 실제 UI resolver는 이 component에서 제외된다. |
| CMP8 | cmp8_parent_child_boundary | major | 자식 다운스트림 완료·20초→1.2초를 작업과 부모 DoD에 반복하고 그래프/업스트림 완료도 작업에 나열해 부모 목적·수용 기준 중심으로 정리하지 못했다. |
| CMP9 | editor_context_preservation | major | 요청 대상은 DL-9095 다운스트림 연동인데 부모 문서의 목적/전역 진행과 본문 작성 행위로 바뀌었다. |
| CMP9 | editor_reference_fidelity | minor | source_ids의 설계 문서는 실재하나 최종 references=[]이고 문서 link 없이 DL-9090·9092 raw key만 표시한다. |
| CMP9 | cmp9_ticket_context | major | 사용자 만족·UX 효익은 발명하지 않았지만 현재 DL-9095 책임을 보존하지 않고 부모 문서 목적·형제 완료 작업을 현재 본문으로 사용했다. |

## 배터리별 실제 출력과 평가

### CMP2

입력: `DL-9095`, 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘. 실제 status=blocked, automaticPass=false, caseScore=2.00.

> status=blocked; payload={}; claims=[]; missing_inputs=[]; warnings=[editor_assembler:invalid_plan]; rendered: ok=false, needsInfo=false, error=[], html=""

- request_fulfillment: 1 — DL-9095 배경·범위·작업·DoD 산출물이 전부 없어 본문 없음의 1점 anchor다.
- factual_grounding: 3 — 노출된 발명은 없으나 source에 근거한 유용한 답도 없다. 비어 있는 답을 사실성 성공으로 보상하지 않으며 내부 차단 계획은 채점하지 않는다.
- contract_actionability: 2 — blocked envelope와 projection은 일치하지만 삽입할 초안·복구 정보가 없어 다음 행동을 수행할 수 없다.
- safety_uncertainty: 3 — 실패를 성공으로 표시하거나 write하지 않고 차단했다. 사용자에게 실패 영향·복구 안내는 전달하지 못한다.
- communication_rendering: 1 — 최종 HTML이 없어 사용자가 읽을 결과 자체가 없다.

### CMP3

입력: `DL-9090`, 이어서 완성해줘. 실제 status=ok, automaticPass=true, caseScore=3.00.

> 오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다 / 제안: 확인 필요: 성능 측정 결과와 문서 정리 내용을 이어서 어떤 문장으로 마무리할까요?

- request_fulfillment: 2.5 — 원문 seed는 정확히 보존했지만 이어 쓴 댓글이 아니라 '어떤 문장으로 마무리할까요?'라는 포괄 질문을 본문에 넣어 핵심 작성 요청을 끝내지 못했다.
- factual_grounding: 5 — 오늘 측정했다는 사용자의 문장만 재사용했고 p95 크기·방향·원인·결과 수치를 보태지 않았다. 완결성 부족과 사실 발명은 구별했다.
- contract_actionability: 2.5 — comment schema와 payload/rendered는 일치한다. 그러나 미완성 문장과 작성자에게 할 질문이 댓글 본문에 들어가 게시 전 실질 수정이 필요하다.
- safety_uncertainty: 3 — p95를 임의 완성하지 않은 점은 적절하다. 확인할 실제 p95·비교 기준을 특정하지 않고 위임받은 문장 선택을 되묻는다.
- communication_rendering: 2.5 — 두 문단의 짧은 형식이나 'p95 가 생각보다'로 문장이 끊기고 '제안: 확인 필요:'와 질문을 이어 사용 가능한 댓글이 아니다.

### CMP6

입력: `DL-9090`, 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘. 실제 status=blocked, automaticPass=false, caseScore=2.00.

> status=blocked; payload={}; claims=[]; missing_inputs=[]; warnings=[editor_assembler:invalid_plan]; rendered: ok=false, needsInfo=false, error=[], html=""

- request_fulfillment: 1 — DL-9090 담당 mention·2홉 100노드·설계 문서 검토 댓글 산출물이 전부 없어 본문 없음의 1점 anchor다.
- factual_grounding: 3 — 노출된 발명은 없으나 source에 근거한 유용한 답도 없다. 비어 있는 답을 사실성 성공으로 보상하지 않으며 내부 차단 계획은 채점하지 않는다.
- contract_actionability: 2 — blocked envelope와 projection은 일치하지만 삽입할 초안·복구 정보가 없어 다음 행동을 수행할 수 없다.
- safety_uncertainty: 3 — 실패를 성공으로 표시하거나 write하지 않고 차단했다. 사용자에게 실패 영향·복구 안내는 전달하지 못한다.
- communication_rendering: 1 — 최종 HTML이 없어 사용자가 읽을 결과 자체가 없다.

### CMP8

입력: `DL-9090`, 본문 정리해줘. 실제 status=ok, automaticPass=true, caseScore=3.10.

> 배경: ...진행 결과 (최종 수정 기준) / 미결... 작업: 다운스트림 2홉 조회: 완료 — DL-9092 인덱스 추가로 20초 → 1.2초... 완료 조건 (DoD): 같은 다운스트림 완료 문장과 '남은 일: 성능 측정(2홉 100 노드 기준), 사용 가이드 작성' 반복.

- request_fulfillment: 3 — 조회 전용·2홉·3홉 제외 목적/범위를 보존했으나 자식 완료 작업과 같은 수치를 부모 작업·DoD에 반복했다. 정리 요청과 부모/자식 분리 계약을 충분히 충족하지 않는다.
- factual_grounding: 3 — 20초→1.2초와 2홉 100노드는 실제 설계 문서 내용이다. 다만 다운스트림 완료를 문서 주장으로 구분하지 않아 In Progress인 자식 Jira 상태와의 충돌을 숨긴다.
- contract_actionability: 3.5 — 초안 schema·HTML은 유효하고 실행을 주장하지 않는다. DoD에 이미 완료한 상태 문장과 '남은 일'을 그대로 혼합해 검토할 완료 기준을 다시 정리해야 한다.
- safety_uncertainty: 3.5 — 없는 값·write를 만들지 않았지만 문서상 완료와 Jira의 진행 상태가 다른 점을 미확인 사항으로 남기지 않는다.
- communication_rendering: 2.5 — 읽을 수 있는 HTML과 정확한 문서 link는 있으나 '진행 결과', '미결', '완료 기준'이 맥락 없는 p로 남고 작업/DoD를 반복해 상당한 정리가 필요하다.

### CMP9

입력: `DL-9095`, 본문 써줘. 실제 status=ok, automaticPass=true, caseScore=2.50.

> 배경: DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다. / 작업: 제안: ...설계와 진행 결과를 본문에 정리한다. / DoD: 제안: ...설계와 진행 결과가 본문에 반영된다.

- request_fulfillment: 1.5 — DL-9095 다운스트림 연동 본문 요청에 부모 DL-9090 설계 문서 기록과 본문 작성 행위의 작업·DoD를 써 현재 티켓의 핵심 책임을 놓쳤다.
- factual_grounding: 2.5 — 인용된 완료·성능 수치는 실제 설계 문서에 있다. 그러나 문서 자체의 목적을 현재 Sub-Task의 목적으로 삼고 Jira 진행 상태 충돌을 설명하지 않아 현재 티켓 본문으로는 근거 연결이 부정확하다.
- contract_actionability: 2 — description payload는 유효하나 '본문에 반영된다'가 DoD여서 실제 다운스트림 연동 작업의 승인·완료 기준으로 사용할 수 없다.
- safety_uncertainty: 3.5 — 외부 write·임의 효익·새 목표 수치를 만들지는 않았다. 상충하는 진행 상태를 한정하지 않아 확인 범위 이상으로 완료를 단정한다.
- communication_rendering: 3 — 4개 heading과 목록은 읽을 수 있으나 설계 내용, 그것을 본문에 넣자는 제안, 넣었는지를 확인하는 DoD가 반복돼 핵심 작업을 찾기 어렵다.

## 자동 checker와 사람 판정 불일치

| case | 자동 | 직접 | 이유 |
|---|---|---|---|
| CMP2 | fail | unusable | 자동 실패와 직접 사용 불가 판정이 일치한다. contractOk는 유효한 blocked envelope만 뜻한다. |
| CMP3 | pass | unusable | 자동 checker는 seed와 확인 표시를 인식해 pass다. 실제 출력은 댓글을 완성하지 않고 막연한 작성 질문을 본문에 넣어 사용 전에 중요한 수정이 필요하다. |
| CMP6 | fail | unusable | 자동 실패와 직접 사용 불가 판정이 일치한다. contractOk는 유효한 blocked envelope만 뜻한다. |
| CMP8 | pass | unusable | 자동 checker는 범위 단어와 본문 형식을 pass로 본다. 직접 판독은 자식 완료 작업의 부모 DoD 반복, dangling 라벨, 문서/Jira 상태 충돌 설명 누락 때문에 실질 수정이 필요하다고 판정한다. |
| CMP9 | pass | unusable | 자동 pass지만 현재 DL-9095 대신 부모 문서와 본문 작성 자체를 작업/DoD로 삼았다. 금지된 효익 발명이 없다는 것만으로 실제 티켓용 본문의 정확한 대상·완결성이 확보되지 않는다. |

CMP3·CMP8·CMP9 세 건의 false positive를 기록했다. fixture/checker/review spec/protocol/guard를 수정하지 않았으며 battery version도 바꾸지 않았다. CMP2·CMP6의 rendererFlaws=[]는 빈 ok=false 결과가 사용자 품질을 통과했다는 뜻이 아니다.

## 실패·재시도·제한사항

CMP2·CMP6은 실제 응답이 `editor_assembler:invalid_plan`으로 차단됐다. remote response를 수신했으므로 network failure와 구별하며 그 앞의 plan을 보지 않고 첫 실패 원인을 추정하지 않는다. CMP3은 정확 seed/미확정을 보존하지만 포괄적인 문장 선택 질문에 멈춘다. CMP8은 부모 목적을 보존해도 완료한 자식 작업·수치를 부모 DoD에 반복한다. CMP9는 현재 DL-9095 대신 부모 문서 목적과 본문 작성 자체를 과업으로 삼는다.

원본 raw hash·동일한 세 source hash·5개 request/case identity·160+15 checklist·점수 ceiling·집계식·필수 보고서 field를 검증했다. 산술 validator 출력의 Windows 기본 인코딩 오류 1회는 `-X utf8`로 로컬 재출력해 해결했으며 모델 재시도나 품질 attempt가 아니다. JSON 내용에 의미 변경 없이 검사했으며 새로운 비용 발생 호출은 없다.

한 번의 탐색 실행, 고정 synthetic projection, 단일 비맹검 reviewer에 한정된다. 가격/캐시 토큰, 실제 UI badge 동작, 원격 검색 coverage, 전체 LTM 품질·보안·활성화 자격을 주장하지 않는다. 원본 raw·과거 보고서를 그대로 보존했다.
