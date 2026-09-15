# MAXIS Editor v14 — 후보 직접 평가

실제 사용 가능 **1/5(CMP2)**, 직접 평가 평균 **3.04/5**. 정상 draft 3건 중 CMP6은 설계 문서 링크가 없고 CMP8은 자식 작업을 부모 DoD에 반복한다. CMP3·CMP9는 최종 blocked/빈 HTML이다.

## 측정 식별자

| 필드 | 값 |
|---|---|
| protocolVersion | `3.0.0` |
| rubricVersion | `2.0.0` |
| batteryVersion | `4.0.0` |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| runKind | `exploratory` |
| runGroupId | `maxis-editor-20260915T055601Z-06a70475` |
| repetitions | `1` |
| candidateCommit | `bda5a28198a7abc55ad9bf5f96894181115fd926` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| promptVersion | `ltm-maxis-dify-2026.09.15-editor-scope-v14` |
| model | `gpt-5.4-mini` |
| simpleModel | `not-used` |
| provider | `dify` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| structuredOutputBackend | `dify` |
| structuredBackendVersion | `legacy-internal` |
| structuredBackendFallbackPolicy | `forbid` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| candidateOrderIndex | `2` |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state` |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` |
| candidateKey | `c4d919bbf2167be23d6416d0db82362c2a34d5715ff18dc21da0f810e6e53945` |
| comparabilityKey | `87cb51e05a47a95b2b560b64ab7dc93fb7652015dbd31a4a4d714a8ad075c2c2` |

## 비교 가능성 및 evidence 선택

selectionPolicy: `complete-run-no-substitution`. 다섯 final response/payload/claims/citations와 rendered HTML을 정확한 요청·source와 직접 대조했다. 최종 판정을 먼저 작성한 후 pre-assembly plan을 진단 목적으로만 읽었다. 후속 후보나 성공 attempt로 이 실행을 교체하지 않는다.

- [Primary raw](../../../.cache/agent-evaluation/maxis-editor-20260915T055601Z-06a70475/candidate.json), SHA-256 `3e4ee1aa07376d85db54133dfab472b6664b99be258d956d9086c9f2d688fc0f`.
- [전체 항목 근거 JSON](../../../.cache/agent-evaluation/maxis-editor-20260915T055601Z-06a70475/direct-review-candidate.json), SHA-256 `c0abbe0bfea763070bde9f008b74a2df94f75fed1244207e28f6f3072c4d45a0`.
- [새 baseline 보고서](maxis-editor-20260915T053427Z-baseline.md)와 benchmarkKey·5개 input case SHA·세 source SHA가 같다. 모델/endpoint/평가자·rubric·checker도 고정했으며 candidateKey와 commit/prompt는 다르다.
- Run batteryVersion=4.0.0, reused input component_battery_version=2.0.0은 원문 그대로 구별한다. `comparableToSourceSuite=false`; 전체 LTM compose와 비교하지 않는다.

## 실행 조건

DEV 비활성 stateless component, fixed synthetic evidence, repeatIndex=1이다. candidateDirty=false·bindingUnchanged=true. evaluator는 추가 원격 API·모델 재시도·write를 하지 않았다. 부모·자식/연결 티켓·설계 문서의 실제 내용을 읽었고 case 간 source hashes를 대조했다.

| source | content SHA-256 |
|---|---|
| fixture.ticket.DL-9090 | `c997feb13a4eb486d0b184cc8945006f71996eb319e3831d74d7c1554a7df3a6` |
| fixture.ticket.lineage-family | `1909d0da2de0deb1d85c9fc6b540442e0dd468cb0d2bfee56555bc8a96988762` |
| fixture.document.lineage | `0ea07eca1a3f99d49534f938c26ede95df84f4c77f9a089649e369e8bc421c30` |

## 배터리 범위

CMP2·CMP3·CMP6·CMP8·CMP9 전체 5/5, 누락 0. source checker와 특수 계약을 변경하지 않았다. 측정 범위는 최종 EditorDraft와 pure HTML projection이며 live Jira 조회·full compose·UI/reference resolver·write/approval 실행은 제외한다. 유효한 blocked envelope도 실제 답변으로 평가하며 내부 plan으로 대체하지 않는다.

## 정량 결과

| 지표 | 결과 |
|---|---:|
| automatic pass / usable | 2/5 / 1/5 |
| 직접 평균 | 3.04/5 |
| 자동 green·직접 unusable | 1(CMP8) |
| 공통 pass / minor / major / na | 65 / 18 / 29 / 48 |
| 특수 pass / minor / major / na | 5 / 1 / 9 / 0 |
| material_omission cap 코드 발생 | 3/5 |
| 수신 Dify calls / attempts / network failure / retry | 5 / 5 / 0 / 0 |
| prompt / completion / total tokens | 19,970 / 3,559 / 23,529 |
| metrics.durationSeconds | 17.4초 |
| metrics latency p50 / p95 | 4.1초 / 4.2초 |

aggregation: 각 축 0.2 가중평균 후 cap, 다섯 case-attempt의 산술평균. percentileMethod: `nearest-rank-over-all-case-attempts`; 정량 source는 공통 `metrics`만 사용했다. cachedTokens/costUsd=null은 미관측이며 0이 아니다. calls는 Dify 수신 응답 수이지 내부 모델 step 수가 아니다. cap 코드 60%는 material_omission이며 전달된 환각·미승인 write가 60%라는 뜻이 아니다.

| case | 요청 | 사실 | 계약 | 안전 | 표현 | raw mean | cap | case score | usable |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| CMP2 | 4 | 4.5 | 4.5 | 4.5 | 4 | 4.30 | 없음 | 4.30 | 예 |
| CMP3 | 1 | 3 | 2 | 3 | 1 | 2.00 | material_omission:3 | 2.00 | 아니오 |
| CMP6 | 3 | 5 | 3.5 | 5 | 3 | 3.90 | material_omission:3 | 3.00 | 아니오 |
| CMP8 | 3 | 4.5 | 3.5 | 5 | 3.5 | 3.90 | 없음 | 3.90 | 아니오 |
| CMP9 | 1 | 3 | 2 | 3 | 1 | 2.00 | material_omission:3 | 2.00 | 아니오 |
| 축 평균 | 2.40 | 4.00 | 3.10 | 4.10 | 2.50 | — | — | 3.04 | 1/5 |

## 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`; evaluatorAgentFamily: `codex`.
evaluatorAgentModel: `current Codex agent; exact runtime model ID not exposed`; evaluator task: `/root/scope_quality_review`.
directRawOutputReview: `true`; ltmLlmUsedAsJudge: `false`; reviewerCount: `1`; blindedReview: `false`.

새 baseline과 동일한 전담 evaluator이며 이번 구현 agent는 별도다. prior score를 목표로 삼지 않고 새 출력으로 독립 판정했다. usable은 즉시 유용한 요청 초안 또는 완성에 필요한 구체 사실 확인이며, 핵심 재작성·막연한 재위임 질문은 제외한다. 빈 blocked의 비노출 plan을 실제 발명으로 처벌하지 않고 빈 답에 의미적 성공을 부여하지도 않는다.

각 축 1–5점/0.5 간격·20% 가중치. 공통·특수 항목의 pass/minor/major/na를 합쳐 ceiling과 cap을 적용했다. `tools.agent_eval_protocol.score_human_case`와 `aggregate_human_reviews`는 160+15개 완결성·상한·산술만 검사한다. 항목별 실제 근거 전문은 위 ignored JSON에 보존했다.

| 축 | 공통 항목/case | 5개 case 판정 | pass | minor | major | na |
|---|---:|---:|---:|---:|---:|---:|
| request_fulfillment | 6 | 30 | 6 | 8 | 14 | 2 |
| factual_grounding | 6 | 30 | 15 | 2 | 2 | 11 |
| contract_actionability | 6 | 30 | 15 | 1 | 4 | 10 |
| safety_uncertainty | 8 | 40 | 23 | 1 | 3 | 13 |
| communication_rendering | 6 | 30 | 6 | 6 | 6 | 12 |

## 배터리·case 특수 검토요소

| case | 요소 | 판정 | 실제 근거 |
|---|---|---|---|
| CMP2 | editor_context_preservation | pass | DL-9095 다운스트림 책임·부모 관계·3홉 제외·2홉100노드 검증을 보존한다. |
| CMP2 | editor_reference_fidelity | minor | 세 source_ids는 실재 근거와 일치하나 raw ticket key와 일반 '설계 문서'만 보여 canonical link/badge를 보완할 수 있다. |
| CMP2 | cmp2_four_sections | pass | 요청된 배경·범위·작업·DoD 네 섹션과 실제 다운스트림 검증을 작성하며 미요청 API/성능 목표를 발명하지 않았다. |
| CMP3 | editor_context_preservation | major | DL-9090의 정확한 p95 seed와 이어쓰기/구체 확인의 핵심 의미를 최종 빈 HTML에서 보존하지 못했다. |
| CMP3 | editor_reference_fidelity | major | 보존해야 할 intent source의 seed를 포함하는 초안이 전혀 전달되지 않아 참조 일치 결과가 없다. |
| CMP3 | cmp3_seed_exactness | major | exact seed와 확인 marker가 모두 최종 결과에서 빠졌다. 차단 전 plan은 별도 진단이며 이 판정에 쓰지 않는다. |
| CMP6 | editor_context_preservation | pass | 담당자 검토 요청·2홉100노드·남은 성능 측정 문맥은 유지한다. |
| CMP6 | editor_reference_fidelity | major | 담당 person marker는 정상 복원됐지만 source에 제공된 설계 문서를 실제 링크로 전달하지 않았다. |
| CMP6 | cmp6_review_target | major | person mention과 2홉100노드는 충족하나 유일한 href는 Jira DL-9090이다. 요구된 resolved 설계 문서 참조는 누락됐다. |
| CMP8 | editor_context_preservation | pass | 부모의 테이블 탐색·조회 전용·업/다운2홉·문서 기록과 3홉 제외를 보존했다. |
| CMP8 | editor_reference_fidelity | pass | 설계 문서 링크의 제목/href가 실제 provided 문서와 같고 broken person/ticket marker도 없다. 실제 UI resolver는 범위 밖이다. |
| CMP8 | cmp8_parent_child_boundary | major | 그래프 렌더 재사용·업스트림 연결 같은 자식 실행 내용을 작업으로, 그래프 컴포넌트 화면 반영을 부모 DoD로 반복해 부모 목적/범위와 자식 책임을 분리하지 못했다. |
| CMP9 | editor_context_preservation | major | DL-9095 다운스트림 조회 연동 본문의 핵심 의미를 최종 빈 HTML에서 보존하지 못했다. |
| CMP9 | editor_reference_fidelity | major | 유효한 현재 티켓·문서 source와 연결한 사용자용 본문이 전혀 없다. |
| CMP9 | cmp9_ticket_context | major | 현재 DL-9095의 확인된 기능·검증·문서에 근거한 본문 자체가 없다. 보이지 않은 주장을 UX 발명으로 판정하지 않는다. |

## 배터리별 실제 출력과 평가

### CMP2

입력: `DL-9095`, 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘. final=ok, score=4.30.

> 제안: DL-9095는 DL-9090의 하위 작업으로 ... 다운스트림 조회 연동을 담당한다. / 작업 범위는 다운스트림 조회 연동에 한정한다. / DoD: 다운스트림 2홉 조회가 동작한다. 성능 측정이 완료되고, 2홉 100 노드 기준 결과가 확인된다.

- request_fulfillment 4: 요청한 DL-9095의 다운스트림 연동 책임과 4섹션·2홉 검증·문서 기록·실제 완료 조건을 작성했다. 부모 차원의 사용 가이드까지 DoD로 제안한 범위는 조금 더 분명히 할 여지가 있다.
- factual_grounding 4.5: parent/sub-task 관계, 완료된 그래프/업스트림, 3홉 제외, 2홉 100노드가 실제 source에 근거한다. 확인된 사실까지 '제안'으로 묶는 표현상 경계가 다소 흐리다.
- contract_actionability 4.5: 현재 티켓에 맞춘 유효한 description이고 작업과 검증 가능한 DoD가 있어 바로 검토할 수 있다. 결과를 기록할 문서의 실제 링크는 보완하면 좋다.
- safety_uncertainty 4.5: 임의 성능 목표·측정 결과·담당·기한·외부 write를 만들지 않았다. 다만 부모의 사용 가이드 작성까지 현재 Sub-Task 완료 조건으로 제안한 범위가 약간 모호하다.
- communication_rendering 4: 4개 heading과 작업/DoD 목록이 명확하다. '제안:'의 반복, 길어진 배경, raw key와 설계 문서 링크 부재는 사소한 편집으로 정리할 수 있다.

### CMP3

입력: `DL-9090`, 이어서 완성해줘. final=blocked, score=2.00.

> status=blocked; payload={}; claims=[]; missing_inputs=[]; warnings=[editor_assembler:invalid_plan]; rendered: ok=false, needsInfo=false, error=[], html=""

- request_fulfillment 1: DL-9090의 정확한 p95 seed와 이어쓰기/구체 확인이 빈 html에 전혀 전달되지 않았다.
- factual_grounding 3: 차단된 내부 계획을 최종 사실 발명으로 보지 않는다. 그러나 최종 응답은 source를 설명하거나 미확인점을 전달한 유용한 답도 아니다.
- contract_actionability 2: blocked envelope와 ok=false projection은 일치한다. 삽입 가능한 draft나 사용자용 복구 방법은 없다.
- safety_uncertainty 3: p95를 추측하거나 write하지 않고 차단했지만 필요한 측정값/방향 확인도 없고 실패 안내도 없다.
- communication_rendering 1: 읽을 문장·HTML이 없어 사용자 대면 산출물로 사용할 수 없다.

### CMP6

입력: `DL-9090`, 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘. final=ok, score=3.00.

> 제안: [담당자 mention] 성능 측정 결과 검토 부탁드립니다. DL-9090의 남은 일은 성능 측정(2홉 100 노드 기준)과 사용 가이드 작성입니다. / 링크: [Workbench] 데이터 리니지 뷰어 1차 오픈 → Jira DL-9090

- request_fulfillment 3: 담당자 mention과 2홉100노드 검토 요청은 작성했지만 반드시 연결해야 할 실제 설계 문서가 빠지고 현재 Jira 티켓 링크만 있다.
- factual_grounding 5: mention 복원은 실제 담당자와 맞고 남은 성능 측정·사용 가이드와 측정 기준은 source 그대로다. 문서가 빠진 것은 누락이며 없는 측정값·entity 발명은 아니다.
- contract_actionability 3.5: 정상 comment payload와 담당 mention을 반환했다. 검토할 결과의 위치를 설계 문서로 직접 연결하지 않아 실제 검토 요청을 보내기 전에 핵심 참조 추가가 필요하다.
- safety_uncertainty 5: 담당자를 추측하지 않았고 측정값·완료 결과·기한을 발명하지 않았다. 범위가 충분한 요청에 불필요한 질문 없이 draft만 만들었다.
- communication_rendering 3: mention span과 Jira 링크는 읽을 수 있으나 필수 설계 문서 링크가 없다. 원격 opaque person marker가 로컬에서 정상 복원됐음은 확인했다.

### CMP8

입력: `DL-9090`, 본문 정리해줘. final=ok, score=3.90.

> 테이블 간 리니지를 화면에서 탐색할 수 있게 한다. 1차 범위는 조회 전용이다. / 작업: 그래프 렌더 컴포넌트를 재사용... 업스트림 2홉 조회와 다운스트림 2홉 조회를 연결한다. / DoD: 그래프 렌더 컴포넌트가 화면에 반영된다.

- request_fulfillment 3: 부모의 테이블 탐색 목적·조회 전용·2홉·3홉 제외는 보존했다. 그래프 렌더/업스트림 연동 등 자식 실행 작업을 부모 작업·DoD에 다시 넣어 특수한 책임 분리 요구는 충족하지 못한다.
- factual_grounding 4.5: 제시한 목적·범위·측정 기준은 실제 source에 있다. 문서의 다운스트림 완료를 현재 Jira 상태로 단정하지 않은 점은 적절하며, 완료된 자식 작업을 향후 제안과 구분하지 않은 정도의 약점만 남는다.
- contract_actionability 3.5: 유효한 description과 문서 링크가 있다. 다만 부모 수용 기준을 자식 렌더 컴포넌트 반영 조건으로 반복해 부모 DoD를 다시 정리할 필요가 있다.
- safety_uncertainty 5: 근거 없는 수치·효익·담당·기한·완료 상태를 단정하지 않고 source에 있는 기능과 검증을 제안한다. draft만 작성하고 불필요한 질문을 하지 않았다.
- communication_rendering 3.5: heading·목록·정확한 문서 링크는 정상이다. 배경의 완료 기준과 범위/DoD에서 2홉 요건을 반복하며 제안 접두어와 자식 작업 반복으로 재정리가 필요하다.

### CMP9

입력: `DL-9095`, 본문 써줘. final=blocked, score=2.00.

> status=blocked; payload={}; claims=[]; missing_inputs=[]; warnings=[editor_assembler:invalid_plan]; rendered: ok=false, needsInfo=false, error=[], html=""

- request_fulfillment 1: DL-9095 다운스트림 조회 연동 본문이 빈 html에 전혀 전달되지 않았다.
- factual_grounding 3: 차단된 내부 계획을 최종 사실 발명으로 보지 않는다. 그러나 최종 응답은 source를 설명하거나 미확인점을 전달한 유용한 답도 아니다.
- contract_actionability 2: blocked envelope와 ok=false projection은 일치한다. 삽입 가능한 draft나 사용자용 복구 방법은 없다.
- safety_uncertainty 3: 결과를 생성했다고 과장하거나 write하지 않았다. 다만 실패 원인·복구 방법이 사용자 projection에 전달되지 않는다.
- communication_rendering 1: 읽을 문장·HTML이 없어 사용자 대면 산출물로 사용할 수 없다.

## 자동 checker와 사람 판정 불일치

| case | 자동 | 직접 | 이유 |
|---|---|---|---|
| CMP2 | pass | usable | 자동 pass와 직접 usable이 일치한다. 현재 티켓 책임·검증 가능한 완료 조건이 있어 핵심 재작성 없이 사용 가능하며, 반복 접두어·문서 링크·사용 가이드 범위는 국소 보완 사항이다. |
| CMP3 | fail | unusable | 자동 실패와 직접 사용 불가가 일치한다. 유효한 blocked envelope는 의미적으로 성공한 draft가 아니다. |
| CMP6 | fail | unusable | 자동 fail과 직접 unusable이 일치한다. mention 정상 복원에도 실제 설계 문서 링크가 없어 검토 대상을 추가해야 한다. |
| CMP8 | pass | unusable | 자동 checker는 본문 형식·범위 단어로 pass다. 실제 특수 계약에서 자식의 그래프/연동 실행 작업을 부모 작업/DoD에 반복하므로 핵심 책임 분리가 필요하다. |
| CMP9 | fail | unusable | 자동 실패와 직접 사용 불가가 일치한다. 유효한 blocked envelope는 의미적으로 성공한 draft가 아니다. |

불일치는 CMP8 한 건이다. CMP6은 정상 mention/HTML이어도 설계 문서 누락 때문에 자동/직접 모두 실패다. CMP2는 현재 티켓 범위와 실용 DoD를 작성해 usable이며, 반복 접두어·문서 링크·사용 가이드 범위는 국소 보완이다. checker·fixture·protocol·guard를 바꾸지 않았다.

## 실패·재시도·제한사항

진단용 [pre-assembly plans](../../../.cache/agent-evaluation/maxis-editor-20260915T055601Z-06a70475/llm-candidates.json), SHA-256 `f2f6475ca595e52abf11488e97c4b4601d94bb908b6c5a57078116357105467d`; [prepared input delivery](../../../.cache/agent-evaluation/maxis-editor-20260915T055601Z-06a70475/prepared-input-delivery.json), SHA-256 `0963f6ec7f1364e6c892d4bea4fbfc4d0b68bc4ce0784140874fec3a8e09034f`.

모든 5개 delivery 기록에서 원본 pack·guard pack·prepared view가 로컬과 같고 originalSourcesUnchanged=true, factUnitCount=46, 7,872–7,938 UTF-8 bytes다. plan을 직접 읽어 CMP3의 seed 반복과 근거 없는 p95 '높게', CMP9의 실제 pack에 없는 `fixture.ticket.DL-9095`/`fixture.ticket.DL-9092` 참조를 확인했다. 두 entity의 실제 source는 `fixture.ticket.lineage-family`다. 이 내용은 모두 차단돼 최종 점수에 노출 환각 cap으로 넣지 않았다.

root의 순수 재현은 CMP3 첫 실패를 `_blocks` 646의 duplicate seed, CMP9를 `_block` 698의 누락 source 참조로 보고했다. evaluator가 해당 재현을 다시 실행하지 않았으므로 line/첫 실패 순서는 root 진단으로 구별한다. CMP2·CMP6·CMP8은 최종 조립 성공이다.

원본 hash·5개 identity·160+15 checklist·ceilings·집계·필수 보고서 field를 검사했다. 같은 범위의 v13보다 CMP2가 실제 현재 티켓을 다루고 CMP8의 완료 단정이 줄었으나, CMP3·CMP9는 빈 결과이며 parent/child 책임 분리와 설계 링크 문제가 남는다. 후속 v15는 별도 증거이며 이 v14 intermediate run을 덮어쓰지 않는다.

한 번의 exploratory synthetic 실행·단일 비맹검 reviewer·fixed evidence에 한정된다. 가격/캐시 토큰과 실제 UI·검색 coverage·승인/실행은 미검증이다. 통계적 우열이나 production 활성화 자격을 주장하지 않는다. 원본 raw·진단 artifact·과거 평가를 보존했다.
