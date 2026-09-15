# Maxis Editor 새 baseline 직접 평가 — 2026-09-15 13:38 UTC

## 측정 식별자

이 문서는 새 baseline의 실제 최종 출력 5건을 독립 판독한 탐색 평가다. 완성 초안 0건, 유용한 필수 확인 질문 1건이며 평균은 **3.38/5**다.

| 필드 | 값 |
|---|---|
| protocolVersion | 3.0.0 |
| rubricVersion | 2.0.0 |
| batteryVersion | 4.0.0 (maxis-editor-component) |
| batteryManifestSha256 | 6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406 |
| specializedReviewSpecSha256 | 877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b |
| runKind | exploratory |
| runGroupId | maxis-editor-20260915T133810Z-1225be87 |
| candidateCommit | f1ee823ba854314cd4bfad7861a76df12b607b44 |
| candidateTreeSha256 | ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6 |
| runtimePackageSetSha256 | 9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645 |
| evaluationHarnessTreeSha256 | c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0 |
| promptVersion | ltm-maxis-dify-2026.09.15-editor-scope-v15 |
| candidateDirty / bindingUnchanged | false / true |

## 비교 가능성 및 evidence 선택

| 필드 | 값 |
|---|---|
| benchmarkKey | f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74 |
| candidateKey | e31828bf1027fafcb6924920f6a02c144e0eee839cf0d3b23fd0ad7215fb4861 |
| comparabilityKey | 33a00004c522ae60d42bb4cef3a2132b68e9f2fd85d4d38e79cc6dbdceb16fcd |
| selectionPolicy | complete-run-no-substitution |
| aggregation | case 5축 동일가중 평균 후 cap; suite/overall 모든 case-attempt 산술평균 |
| primary raw SHA256 | ed669769f8066cb25780093b12cb747e9f5648f8eee49a3264261e022cdf2d50 |
| direct review SHA256 | caa6b300c93510a295e118e77f27b57abd0fe97a6ea5f0d4f78508d7aacf1feb |

[원본 raw](../../../.cache/agent-evaluation/maxis-editor-20260915T133810Z-1225be87/baseline.json)와 [175개 전체 item verdict/evidence·상한·산술 JSON](../../../.cache/agent-evaluation/maxis-editor-20260915T133810Z-1225be87/direct-review-baseline.json)을 연결한다. 완전한 원 run만 평가했으며 closure·내부 plan·focused 성공으로 대체하지 않았다.
[직전 v15 보고서](maxis-editor-20260915T061308Z-candidate.md)는 형식·고정 rubric 참고 경로다. 이 문서는 새 baseline만 기록하며 과거 점수 복사나 후보 성적 예측을 하지 않는다.
동일 CMP2/3/6/8/9와 3개 source content hash를 유지한다. component battery 4.0.0과 input case component 2.0.0은 별개 식별자이며 full source-suite 결과와 비교하지 않는다.

## 실행 조건

| 필드 | 값 |
|---|---|
| model | gpt-5.4-mini |
| simpleModel | not-used |
| provider | dify |
| providerEndpointIdentitySha256 | ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7 |
| structuredOutputBackend | dify |
| structuredBackendVersion | legacy-internal |
| structuredBackendFallbackPolicy | forbid |
| runtimeProfile | dev-inactive-stateless-component-v1 |
| dataProfile | repository-synthetic-editor-component-v1 |
| dataManifestSha256 | d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d |
| repetitions | 1 |
| candidateOrderIndex | 1 |
| retryPolicy | no-retry-no-fallback-all-attempts-retained |
| cachePolicy | no-client-history-no-local-state |
| processIsolation | separate-process-stateless-fresh-pack-each-case |
| repeatIndex / recordedAtUtc | 1 / 2026-09-15T13:38:10Z |

provider endpoint/credential 원문은 보고서에 포함하지 않는다. 호출/재시도 기록은 원 raw를 보존한다. 평가자는 원격 호출·LTM endpoint judge를 사용하지 않았다.

## 배터리 범위

| suite | 전체 | 실행 | 누락 |
|---|---:|---:|---:|
| maxis-editor-component | 5 | 5 | 0 |

실행 case는 CMP2/CMP9 DL-9095 description, CMP3 DL-9090 seed comment, CMP6 DL-9090 mention comment, CMP8 DL-9090 description이다.
3개 fixture 전문을 읽어 대조했다. 부모 DL-9090은 In Progress, 자식 DL-9093/9094는 Closed, DL-9095는 In Progress다.
설계 문서는 다운스트림 완료·20초→1.2초, 남은 2홉 100노드 측정/가이드를 기록한다. 문서 완료와 Jira 진행 중을 현재 사실로 합치지 않는 것이 중요하다.

| source_id | content SHA256 |
|---|---|
| fixture.ticket.DL-9090 | c997feb13a4eb486d0b184cc8945006f71996eb319e3831d74d7c1554a7df3a6 |
| fixture.ticket.lineage-family | 1909d0da2de0deb1d85c9fc6b540442e0dd468cb0d2bfee56555bc8a96988762 |
| fixture.document.lineage | 0ea07eca1a3f99d49534f938c26ede95df84f4c77f9a089649e369e8bc421c30 |

## 정량 결과

| 지표 | 결과 |
|---|---|
| attempts / calls / retries | 5 / 5 / 0 |
| durationSeconds / attempt | 17.6 / 3.52초 |
| percentileMethod / p50 / p95 | nearest-rank-over-all-case-attempts / 3.9초 / 5.1초 |
| promptTokens / completionTokens / totalTokens | 19970 / 2871 / 22841 |
| attempt당 prompt / completion / total | 3994 / 574.2 / 4568.2 |
| cachedTokens / costUsd | null / null — 미수집이며 0이 아님 |
| 자동 통과 / 자동 실패 | 4/5 / 1/5 (20%; CMP6) |
| structural contract 실패 / blocked / 기술 오류 | 0 / 0 / 0 |
| 직접 품질 / 유용성 | 3.38/5 / 1/5 = 완성 초안 0 + 필수 사실 확인 1 |
| cap code 발생 | 2/5 (40%; CMP6, CMP9); 실제 평균 하향은 CMP6만 |

latency는 canonical cases[*].metrics.durationSeconds 전체 5건으로 산출했다. calls는 받은 Dify response 수이며 내부 모델 step 수가 아니다. token usage는 complete지만 비용/cache 비교는 불가하다.

## 사람 품질 평가 기준

| 필드 | 값 |
|---|---|
| qualitativeEvaluatorPolicy | codex-or-claude-direct-raw-output-review |
| evaluatorAgentFamily | codex |
| evaluatorAgentModel | current Codex agent; exact runtime model ID not exposed |
| directRawOutputReview / ltmLlmUsedAsJudge | true / false |
| reviewerCount / blindedReview | 1 / false |
| panel / 구현 분리 | 동일 reused reviewer / root 구현자와 평가자는 다름 |

rubricVersion 2.0.0의 요청·근거·실행·안전·표현 5축은 각20%, 1–5점의 0.5단위다. 공통+특수 결함을 합쳐 전pass 5, minor1 4.5, minor2+ 4, major1 3.5, major2+ 3의 상한을 적용하고 anchor에 따라 그 이하 점수를 선택한다.
평균 후 cap은 fabricated_fact_or_entity/unsafe_or_unapproved_write 2, reply_payload_contradiction/material_omission 3이다. CMP6은 필수 문서 누락, CMP9는 실제 현재 티켓 작업·검증 본문을 작성 지시로 대체한 누락이다.
유용성은 즉시 쓸 초안 또는 다음 작성에 필요한 구체적 사실 확인이다. CMP3은 후자일 뿐 게시 가능한 완성 댓글이 아니다. source가 있는 완료 충돌은 발명 cap으로 부풀리지 않았다.
도구는 tools.agent_eval_protocol로 160공통+15특수의 누락·상한·cap·산술만 검증했다. verdict/evidence와 축 anchor 선택은 실제 출력 직접 판독이다.

| 공통 checklist 축 | pass | minor | major | na | 축평균 |
|---|---:|---:|---:|---:|---:|
| 요청 | 12 | 6 | 9 | 3 | 3.00 |
| 근거 | 14 | 3 | 4 | 9 | 4.00 |
| 실행 | 15 | 1 | 4 | 10 | 3.20 |
| 안전 | 19 | 5 | 5 | 11 | 3.70 |
| 표현 | 7 | 13 | 2 | 8 | 3.50 |
| 공통 합계 | 67 | 28 | 24 | 41 | 160개 |
| 특수 합계 | 4 | 5 | 6 | 0 | 15개 |

판정 개수는 독립 정확도 표본/성공률이 아니다. 개별 pass/na의 실제 근거, case별 축 rationale·combined ceiling은 연결한 ignored JSON에 모두 보존했다.

## 배터리·case 특수 검토요소

| case / 고유 목표 | 요구 계약 |
|---|---|
| CMP2 / 4섹션 보강 | 배경·범위·작업·DoD, 임의 API/성능 목표 금지 |
| CMP3 / seed 보존 | exact visible seed, 확인 marker, p95 높낮음·원인 발명 금지 |
| CMP6 / 담당 검토 요청 | 실제 mention + 2홉 100노드 + canonical 설계 문서 link |
| CMP8 / 부모 본문 정리 | 부모 why/scope, 자식 완료 작업을 부모 DoD에 그대로 반복 금지 |
| CMP9 / 짧은 본문 작성 | 현재 티켓 기능·검증·문서, 미확인 UX 효익 발명 금지 |

| case | 요소 | 판정 | 실제 근거 |
|---|---|---|---|
| CMP2 | editor_context_preservation | major | DL-9095 다운스트림 작업 맥락을 부모 전체 그래프·업스트림 책임으로 확대했다. 현재 작업 자체는 포함되지만 핵심 범위가 달라졌다. |
| CMP2 | editor_reference_fidelity | minor | 깨진 token/중첩 HTML은 없으나 실제 DL-9090 제목만 평문으로 놓여 현재 DL-9095와 부모 관계를 식별할 참조 표시가 없다. |
| CMP2 | cmp2_four_sections | pass | 배경/작업 범위/작업/완료 조건(DoD) 4개가 존재하며 2홉 100노드는 source 조건이다. 별도의 API·성능 목표를 만들지는 않았다. |
| CMP3 | editor_context_preservation | pass | seed의 오늘·리니지 뷰어 성능 측정·p95·생각보다를 그대로 두고 질문을 뒤에 추가했다. |
| CMP3 | editor_reference_fidelity | minor | 설계 문서 이름은 source와 맞지만 href가 없고 내부 조회로 확정된 기록 문서를 맞는가라고 재확인한다. |
| CMP3 | cmp3_seed_exactness | pass | visible seed가 정확히 보존되어 있으며 p95 값은 확인 필요로 묻고 높음/낮음/원인은 생성하지 않았다. |
| CMP6 | editor_context_preservation | major | 담당 검토 요청과 2홉 기준은 보존했지만 실제 결과 문서 없이 결과 공유를 되요청하여 구체 검토 대상이라는 핵심이 빠졌다. |
| CMP6 | editor_reference_fidelity | major | person marker는 올바른 skcc.x1402 mention으로 복원되나 source에 있는 canonical 설계 문서를 전혀 참조하지 않는다. |
| CMP6 | cmp6_review_target | major | person mention과 2홉 100노드는 충족하지만 resolved document reference가 없다. 최종 HTML에는 a href가 하나도 없다. |
| CMP8 | editor_context_preservation | pass | 부모의 테이블 간 리니지 탐색 목적, 조회 전용, 업스트림/다운스트림 2홉과 설계 문서 기록이 실제 본문에 보존된다. |
| CMP8 | editor_reference_fidelity | minor | 설계 문서 href는 canonical이고 깨지지 않았지만 DL-9092가 평문 key로만 나타난다. 실제 UI badge 동작은 이 component로 확인하지 않는다. |
| CMP8 | cmp8_parent_child_boundary | minor | 부모 why/scope는 있고 자식 제목/완료를 DoD에 그대로 복제하진 않았다. 다만 작업 section에 그래프/업스트림/다운스트림 완료 세부를 나열해 부모 수준의 정리가 덜 됐다. |
| CMP9 | editor_context_preservation | major | 현재 DL-9095의 본문을 작성해야 하나 문서 목적/부모 진행 내역과 작성 방법 지시로 핵심 산출물을 대체했다. |
| CMP9 | editor_reference_fidelity | minor | 존재하는 ticket key들은 평문이며 설계 source의 canonical link가 없다. 깨진 marker나 새로운 entity는 없다. |
| CMP9 | cmp9_ticket_context | major | 미확인 UX 효익 발명은 없지만 현재 티켓 대신 부모·형제 완료 사실/설계 기록 목적을 대거 사용하고 DL-9095 상태 충돌을 숨겼다. |

## 배터리별 실제 출력과 평가

| case | 요청 | 근거 | 실행 | 안전 | 표현 | 원평균 | cap | 최종 | 유용성 |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| CMP2 | 2.5 | 4.5 | 3.0 | 5.0 | 4.0 | 3.80 | 없음 | 3.80 | 수정 필요 |
| CMP3 | 4.0 | 5.0 | 4.0 | 4.0 | 4.0 | 4.20 | 없음 | 4.20 | 필수 확인 |
| CMP6 | 3.0 | 5.0 | 3.0 | 3.5 | 3.0 | 3.50 | 3 | 3.00 | 수정 필요 |
| CMP8 | 3.5 | 3.0 | 3.5 | 3.0 | 3.5 | 3.30 | 없음 | 3.30 | 수정 필요 |
| CMP9 | 2.0 | 2.5 | 2.5 | 3.0 | 3.0 | 2.60 | 3 | 2.60 | 수정 필요 |

### CMP2 — 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘

> 배경: [Workbench] 데이터 리니지 뷰어 1차 오픈 / 작업: 제안: 리니지 그래프 렌더 컴포넌트를 적용한다. / 제안: 업스트림 2홉 조회 연동을 반영한다. / DoD: 제안: 다운스트림 2홉 조회가 동작한다.

요청 2.5: 4섹션은 있으나 DL-9095 다운스트림 연동에 부모·형제의 그래프/업스트림 작업과 완료 조건을 더했다. 핵심 대상 범위를 상당히 다시 좁혀야 하므로 2.5. 근거 4.5: 리니지·2홉·100노드·조회 전용·3홉 제외는 source와 일치하고 실행 완료를 발명하지 않았다. 부모 제목을 현재 본문에 관계 설명 없이 넣은 식별 표현만 경미하게 부족해 4.5. 실행 3.0: 유효한 description payload와 렌더 HTML은 일치하지만 DL-9095 담당자가 자기 책임으로 검토할 작업·DoD로 범위를 고쳐야 하므로 3.0. 안전 5.0: 현재 티켓 정보는 이미 주어져 질문이 필요 없고, 모든 작업을 제안으로 남겨 측정 결과나 완료를 추측하지 않았다. 초안 경계를 지켜 5.0. 표현 4.0: heading/list로 읽히지만 거의 매 항목의 제안 접두어와 부모 제목·키 관계 불명확성이 남는다. 구조 자체는 사용 가능하여 4.0.

### CMP3 — 이어서 완성해줘

> 오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다 / 제안: 확인 필요: 성능 측정 결과의 p95 값은 얼마인가? / 제안: 확인 필요: 결과와 이슈를 기록할 문서는 [설계] 리니지 뷰어 1차가 맞는가?

요청 4.0: seed를 정확히 보존하고 p95 실측값을 구체적으로 물어 미확정 사실을 닫는 데 유용하다. 불필요한 문서 재확인과 비교 기준 질문 부족 때문에 4.0이며 완성 댓글로 보지는 않는다. 근거 5.0: p95 방향/원인/수치를 발명하지 않고 2홉 100노드 및 실제 설계 문서만 질문에 사용하여 5.0. 실행 4.0: comment HTML과 질문이 일치하고 p95 값이라는 답할 수 있는 질문을 제공한다. 기대 대비 방향/기준까지 묶으면 바로 이어쓰기 쉬워 4.0. 안전 4.0: 필수 실측값을 묻고 확정을 보류했다. 비교 기준 질문 누락 및 이미 확인된 기록 문서 재질문이라는 경미한 인터뷰 결함을 함께 반영해 4.0. 표현 4.0: seed와 짧은 질문 3개는 읽히지만 제안: 확인 필요: 반복 및 설계 문서의 비링크 표기가 남아 4.0.

### CMP6 — 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘

> 제안: @skcc.x1402 성능 측정 결과 검토 부탁드립니다. 확인 필요: 2홉 100 노드 기준 측정 결과를 공유해 주실 수 있나요?

요청 3.0: 담당 mention과 성능 검토 요청은 있으나 반드시 접근 가능한 설계 문서가 없고 결과를 검토할 사람에게 다시 결과 공유를 요구한다. 주요 요청을 수정해야 해 3.0. 근거 5.0: mention은 실제 DL-9090 담당자로 복원되고 2홉 100노드도 source와 일치한다. 결과 수치를 만들지 않아 사실 축은 5.0. 실행 3.0: comment schema/mention 복원은 정상이나 검토 대상에 도달할 링크 없이 공유 요청과 검토 요청이 섞여 검토 전 수정이 필요하므로 3.0. 안전 3.5: 없는 수치는 확인 필요로 두었지만 source에 있는 기록 문서를 활용하지 않고 검토 수신자에게 결과를 요구해 행동이 지연된다. 즉시 write 위험은 없어 3.5. 표현 3.0: 한 문단과 mention은 읽히지만 필수 설계 링크 접근이 불가능하여 3.0. material_omission cap 3.0 적용.

### CMP8 — 본문 정리해줘

> 배경: In Progress / DL-9092 해결돼서 다운스트림도 붙였습니다. … 작업: 다운스트림 2홉 조회: 완료 — DL-9092 인덱스 추가로 20초 → 1.2초 / 제안: 성능 측정 결과를 확인 필요: 2홉 100 노드 기준으로 정리한다. / DoD: 남은 일: 성능 측정(2홉 100 노드 기준), 사용 가이드 작성

요청 3.5: 부모의 테이블 리니지 탐색 목적과 조회 전용·2홉 범위는 남았다. 하지만 작업·완료 조건에 완료 이력/남은 일/제외 범위가 뒤섞여 마감 상태를 정리해야 하므로 3.5. 근거 3.0: 실제 source 문장을 다수 인용했지만 다운스트림 완료를 현재 사실로 쓰면서 Jira DL-9095 In Progress와 충돌을 구분하지 않았다. 근거는 존재하므로 발명 cap은 아니며 3.0. 실행 3.5: schema와 실제 문서 link는 정상이다. 작업/DoD가 과거 완료·기록 지시·남은 일로 섞여 검토자가 완료판정을 고쳐야 하므로 3.5. 안전 3.0: 없는 실측값을 발명하진 않았지만 완료 상태 충돌을 공개하지 않고 단정한다. material ambiguity/확신 결함을 반영하여 3.0. 표현 3.5: 네 섹션과 정상 설계 link는 읽힌다. 배경에 제목/영문상태/댓글을 복사하고 중복된 3홉 제외와 어색한 확인 필요 문구가 남아 3.5.

### CMP9 — 본문 써줘

> 배경: DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다. / 작업 범위: 제안: DL-9095의 본문은 다운스트림 조회 연동 작업의 현재 범위와 진행 상태를 중심으로 정리한다. / DoD: 제안: 다운스트림 2홉 조회 연동 완료 상태를 본문에 명시한다.

요청 2.0: 현재 DL-9095의 다운스트림 구현 본문을 요청했는데 배경은 설계 문서 목적/부모 완료 내역이고 나머지는 본문을 정리한다·반영한다·명시한다는 작성 지시다. 핵심 산출물을 재작성해야 하므로 2.0. 근거 2.5: 출처에 있는 문장을 사용했지만 문서의 목적을 티켓 배경으로 전용하고 다운스트림 완료를 Jira In Progress와 구분하지 않았다. 현재 티켓 의미·상태 재대조가 필요해 2.5. 실행 2.5: 유효한 HTML이어도 실제 작업·완료 조건 대신 작성 방법 지시가 대부분이다. 현재 티켓 검토·실행에 쓸 구체 초안을 크게 보완해야 하므로 2.5. 안전 3.0: 새 수치/UX 효익은 만들지 않았고 draft 경계도 지켰으나 알려진 현재 상태 충돌을 숨긴 완료 단정 때문에 3.0. 표현 3.0: 4섹션으로 읽히지만 긴 부모/설계 배경과 메타 지시가 반복되어 현재 티켓 핵심을 다시 해석해야 한다. raw ticket key와 문서 기록 목적의 출처 링크 부재도 남아 3.0.

## 자동 checker와 사람 판정 불일치

| case | 자동 | 직접 | 차이/일치 이유 |
|---|---|---|---|
| CMP2 | pass | 수정 필요 | 4섹션은 있으나 현재 Sub-Task에 부모·형제 작업/DoD를 확대한 의미 결함은 탐지하지 않음 |
| CMP3 | pass | 필수 확인으로 유용 | seed·비발명 계약을 지켰지만 완성 댓글 성공으로 확대해 읽으면 안 됨 |
| CMP6 | fail | 수정 필요 | 실제 문서 링크 누락을 양쪽에서 발견; 직접 판독은 검토→결과 공유 혼합도 확인 |
| CMP8 | pass | 수정 필요 | 부모 목적은 보존했으나 상태 conflict·혼합된 작업/DoD는 의미 판독이 필요 |
| CMP9 | pass | 수정 필요 | 형식은 정상이나 실제 본문 대신 작성 지시, 부모/문서 맥락 전용 및 상태 conflict가 남음 |

자동 green 중 CMP2/8/9가 직접 품질상 사용 불가다. 자동 red이지만 직접 허용할 false negative는 없다. checker/fixture를 이번 평가에서 변경하지 않았으며 향후 의미 계약을 바꾸면 battery version을 별도 올려야 한다.

## 실패·재시도·제한사항

1회 exploratory·비무작위·단일 non-blind reused reviewer 평가다. 같은 reviewer의 기존 case 노출 때문에 독립 복제 검증이나 production 우열 증거가 아니다. 추후 후보도 같은 panel/rubric으로 별도 원 raw를 평가해야 한다.
synthetic EditorDraft와 pure markup만 직접 읽었다. 실제 LTM desktop/좁은 side panel, reference resolver, live Jira, 승인/write 실행은 검증하지 않았다.
예전 review 구조와 고정 유용성 정의는 재사용했으나 이번 175개 verdict/evidence는 새 최종 출력에서 작성했다. 코드·guard·fixture·checker·protocol·원 raw는 변경하지 않았다.
미수집 cost/cache는 null이고 기술 실패/재시도는0이다. 중간 plan/diagnostic은 점수에 사용하지 않았다. 원본 raw와 모든 attempt는 보존됐다.
