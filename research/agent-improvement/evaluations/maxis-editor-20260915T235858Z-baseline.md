# Maxis Editor v16 baseline 직접 품질 평가 — 2026-09-15T23:58:58Z

## 측정 식별자

| 필드 | 값 |
|---|---|
| protocolVersion / rubricVersion | 3.0.0 / 2.0.0 |
| batteryVersion / runKind | 4.0.0 / exploratory |
| batteryManifestSha256 | 6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406 |
| specializedReviewSpecSha256 | 877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b |
| runGroupId | maxis-editor-20260915T235858Z-c4864e8e |
| candidateCommit | e2272a688f0e97d8139310a8ac8a01d1cc5afbd7 |
| candidateTreeSha256 | ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6 |
| runtimePackageSetSha256 | 9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645 |
| evaluationHarnessTreeSha256 | c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0 |
| promptVersion | ltm-maxis-dify-2026.09.15-editor-units-v16 |
| candidateDirty / bindingUnchanged | false / true |
| published Editor workflow | 6cdcdff7-c34e-45e4-8f7e-3f5d5f4311f4 |

## 비교 가능성 및 evidence 선택

| 필드 | 값 |
|---|---|
| benchmarkKey | f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74 |
| candidateKey | 36e71a2a656276afe3812ecb1c7768c345416ae7ec28533feef70c620ab2bd0a |
| comparabilityKey | 8e3f2f56dd3f259c3d878375f034f041e7c0229e451eb8545b455b6104b35e79 |
| selectionPolicy | complete-run-no-substitution |
| aggregation | 5축 동일가중 평균 후 cap; 모든 case-attempt의 산술평균 |
| primary raw SHA256 | 8659023b83ae9d0b6331ae5a86c81f4ab04fa74f07576bbf130ae87bb1d852a0 |
| direct review SHA256 | 793fdb2c39c272b132366e2b8a6546939be7b3db3f6c78748bcc2281ea8b1e8a |

[원본 raw](../../../.cache/agent-evaluation/maxis-editor-20260915T235858Z-c4864e8e/baseline.json)와 [175개 verdict/evidence·축 상한·cap·산술 JSON](../../../.cache/agent-evaluation/maxis-editor-20260915T235858Z-c4864e8e/direct-review-baseline.json)을 연결한다. 원 run 전체만 평가하며 내부 plan이나 후속 성공으로 대체하지 않았다.
이번 새 baseline 5개 최종 출력을 독립 판독했다. 이전 점수를 목표로 복사하지 않았고 후속 candidate 성적은 아직 평가하거나 예측하지 않았다. 같은 고정 배터리·모델·reviewer로 후속 비교할 예정이다.
실행은 clean e2272a6에서 수집됐다. 평가 시점 다른 agent의 코드 수정은 원본 실행과 구분하며 candidateDirty=false를 현재 작업트리 상태로 오인하지 않는다.

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
| repeatIndex / recordedAtUtc | 1 / 2026-09-15T23:58:59Z |

endpoint/credential 원문은 포함하지 않는다. 평가자는 원격 호출·LTM endpoint judge를 사용하지 않고 원본 최종 response와 fixture 전문만 근거로 삼았다.

## 배터리 범위

| suite | 전체 | 실행 | 누락 |
|---|---|---|---|
| maxis-editor-component | 5 | 5 | 0 |

실행: CMP2/CMP9 DL-9095 description, CMP3 DL-9090 seed comment, CMP6 DL-9090 담당 mention comment, CMP8 DL-9090 description. run battery4.0.0과 input case component2.0.0은 별개 식별자다.
3개 fixture 전문을 읽었다. 부모 DL-9090 In Progress, DL-9093/9094 Closed, DL-9095 In Progress이며 문서는 다운스트림 완료·20초→1.2초와 남은 2홉100노드 측정/가이드를 기록한다. 문서의 과거/진행 기록과 현재 Jira 상태를 구분해야 한다.

| source_id | content SHA256 |
|---|---|
| fixture.ticket.DL-9090 | c997feb13a4eb486d0b184cc8945006f71996eb319e3831d74d7c1554a7df3a6 |
| fixture.ticket.lineage-family | 1909d0da2de0deb1d85c9fc6b540442e0dd468cb0d2bfee56555bc8a96988762 |
| fixture.document.lineage | 0ea07eca1a3f99d49534f938c26ede95df84f4c77f9a089649e369e8bc421c30 |

## 정량 결과

| 지표 | 결과 |
|---|---|
| attempts / calls / retries | 5 / 5 / 0 |
| durationSeconds / attempt | 18.0 / 3.6초 |
| percentileMethod / p50 / p95 | nearest-rank-over-all-case-attempts / 4.0초 / 5.4초 |
| promptTokens / completionTokens / totalTokens | 8339 / 3711 / 12050 |
| attempt당 prompt / completion / total | 1667.8 / 742.2 / 2410 |
| cachedTokens / costUsd | null / null — 미수집이며 0이 아님 |
| 자동 통과 / 자동 실패 | 3/5 / 2/5 (40%; CMP3, CMP6) |
| structural contract 실패 / blocked / needs_input / 기술 오류 | 0 / 0 / 1 / 0 |
| 직접 품질 / 유용성 | 3.58/5 / 1/5 = 완성 초안0 + 필수 사실 확인1 |
| cap code 발생 | 1/5 (20%; CMP6 원평균3.50→cap3.00) |

시간은 canonical cases[*].metrics.durationSeconds 전체 5건의 nearest-rank다. calls는 받은 Dify response 수이며 내부 모델 step 수가 아니다. 단일 탐색 결과로 cost/cache 효과나 안정적 성능 향상을 주장하지 않는다.

## 사람 품질 평가 기준

| 필드 | 값 |
|---|---|
| qualitativeEvaluatorPolicy | codex-or-claude-direct-raw-output-review |
| evaluatorAgentFamily | codex |
| evaluatorAgentModel | current Codex agent; exact runtime model ID not exposed |
| directRawOutputReview / ltmLlmUsedAsJudge | true / false |
| reviewerCount / blindedReview | 1 / false |
| panel / 구현 분리 | 기존과 동일 reused reviewer; 코드 구현자는 별도 agent |

rubricVersion2.0.0: 요청·근거·실행·안전·표현 각20%, 1–5의0.5단위. 공통+특수 합산 상한은 전pass5/minor1=4.5/minor2+=4/major1=3.5/major2+=3이며 anchor에 따라 더 낮게 선택한다.
평균 후 cap: fabricated_fact_or_entity·unsafe_or_unapproved_write=2, reply_payload_contradiction·material_omission=3. CMP6 설계 문서 누락에만 cap3을 적용했다. source에 있는 완료 문장의 상태 충돌은 사실 발명 cap으로 바꾸지 않는다.
CMP3은 실제값/비교 기준 질문이 구체적으로 필요해 clarification으로 유용하다. 다만 final HTML에 seed가 없으므로 frozen exact-visible-seed 계약은 major, 요청 축3.0이다. 보류 이유가 명시된 응답에 숨긴 누락 cap을 중복 적용하지 않는다.
평가자는 이전 출력 및 v16 코드의 독립 critic 경험이 있는 단일 reused non-blind reviewer다. 코드/내부 plan 성공을 출력 점수에 대입하지 않았고 도구는 완전성·상한·cap·산술만 검증했다.

| 공통 checklist 축 | pass | minor | major | na | 축평균 |
|---|---|---|---|---|---|
| 요청 | 10 | 8 | 9 | 3 | 2.90 |
| 근거 | 15 | 3 | 2 | 10 | 4.40 |
| 실행 | 16 | 0 | 4 | 10 | 3.40 |
| 안전 | 18 | 2 | 4 | 16 | 3.90 |
| 표현 | 7 | 15 | 1 | 7 | 3.80 |
| 공통 합계 | 66 | 28 | 20 | 46 | 160개 |
| 특수 합계 | 2 | 7 | 6 | 0 | 15개 |

개수는 독립 정확도 표본/성공률이 아니다. 모든 pass/na 포함 항목별 근거와 case별 combined ceiling은 연결한 ignored JSON에 보존했다.

## 배터리·case 특수 검토요소

| case / 고유 목표 | 요구 계약 |
|---|---|
| CMP2 / 4섹션 보강 | 배경·범위·작업·DoD, 임의 API/성능 목표 금지 |
| CMP3 / seed 보존 | exact visible seed, 확인 marker, p95 높낮음·원인 발명 금지 |
| CMP6 / 담당 검토 요청 | 실제 mention + 2홉100노드 + canonical 설계 문서 link |
| CMP8 / 부모 본문 정리 | 부모 why/scope, 자식 완료 작업을 DoD에 그대로 반복 금지 |
| CMP9 / 짧은 본문 작성 | 현재 티켓 기능·검증·문서, 미확인 UX 효익 금지 |

| case | 요소 | 판정 | 실제 근거 |
|---|---|---|---|
| CMP2 | editor_context_preservation | major | 현재 DL-9095 다운스트림을 언급하지만 부모 제목/업스트림/그래프 작업으로 책임 경계를 넓혔고 측정·문서의 scope와 DoD도 서로 다르다. |
| CMP2 | editor_reference_fidelity | minor | 부모 제목은 실제 entity지만 평문이며 별도 문서는 확인된 설계 URL로 연결되지 않는다. 새 entity나 깨진 marker는 없다. |
| CMP2 | cmp2_four_sections | pass | 배경·작업 범위·작업·완료 조건 네 section을 모두 생성했다. 응답 지연 점검은 제안이며 새 API나 수치형 성능 목표를 발명하지 않았다. |
| CMP3 | editor_context_preservation | minor | p95 측정과 비교라는 seed 의미는 질문에 보존되나 오늘 리니지 뷰어 원문 전체는 최종 화면에 없다. |
| CMP3 | editor_reference_fidelity | pass | 질문은 외부 entity 참조를 만들지 않으며 빈 citations/claims와 일치한다. 확인된 문서를 미확인 entity로 재질문하지 않는다. |
| CMP3 | cmp3_seed_exactness | major | 방향·원인은 발명하지 않았지만 rendered.html이 비어 exact visible seed 및 seed 뒤 확인 marker라는 고정 결과 계약을 충족하지 못했다. |
| CMP6 | editor_context_preservation | minor | 담당자/성능 검토 의도는 남지만 결과가 있는 설계 문서 연결을 누락하고 수신자에게 측정값을 달라는 문장을 더했다. |
| CMP6 | editor_reference_fidelity | major | person mention과 Jira URL은 유효하나 필수 설계 문서 참조를 다른 source 종류인 티켓 링크로 대체했다. |
| CMP6 | cmp6_review_target | major | person mention·2홉 100노드는 있지만 실제 href는 Jira DL-9090뿐이며 required resolved design-document link가 없다. |
| CMP8 | editor_context_preservation | minor | 부모 목적·조회 전용·2홉·설계 기록 문장은 남지만 다운스트림 포함/기록 유지의 확정성을 다시 열었다. |
| CMP8 | editor_reference_fidelity | minor | 설계 canonical link는 정확하나 연속 중복하며 부모 제목은 평문이다. resolved 문서를 미확인 entity로 만들거나 깨진 HTML은 없다. |
| CMP8 | cmp8_parent_child_boundary | minor | 업스트림/다운스트림 연동 세부를 작업에 다시 넣었지만 자식 제목/완료 작업을 DoD에 그대로 복제하지 않았다. 부모 목적·범위는 존재한다. |
| CMP9 | editor_context_preservation | major | 현재 다운스트림 작업을 포함하지만 부모/설계 문서 목적과 업스트림 책임을 현재 티켓 배경·범위로 가져와 작업 단위 경계가 보존되지 않았다. |
| CMP9 | editor_reference_fidelity | minor | 실제 ticket key는 평문이고 설계 문서는 canonical URL 없이 언급한다. 가짜 entity/깨진 token은 없다. |
| CMP9 | cmp9_ticket_context | major | 실제 다운스트림 측정/검증 제안은 있지만 부모/업스트림 범위와 충돌하는 완료 상태를 현재 티켓 맥락으로 사용했다. 미확인 UX 효익 발명은 없다. |

## 배터리별 실제 출력과 평가

| case | 요청 | 근거 | 실행 | 안전 | 표현 | 원평균 | cap | 최종 | 유용성 |
|---|---|---|---|---|---|---|---|---|---|
| CMP2 | 3.0 | 4.5 | 3.0 | 5.0 | 4.0 | 3.90 | 없음 | 3.90 | 수정 필요 |
| CMP3 | 3.0 | 5.0 | 5.0 | 5.0 | 5.0 | 4.60 | 없음 | 4.60 | 필수 확인 |
| CMP6 | 3.0 | 5.0 | 3.0 | 3.5 | 3.0 | 3.50 | 3 | 3.00 | 수정 필요 |
| CMP8 | 3.0 | 5.0 | 3.0 | 3.0 | 3.5 | 3.50 | 없음 | 3.50 | 수정 필요 |
| CMP9 | 2.5 | 2.5 | 3.0 | 3.0 | 3.5 | 2.90 | 없음 | 2.90 | 수정 필요 |

### CMP2 — 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘

> 배경: In Progress / [Workbench] 데이터 리니지 뷰어 1차 오픈 / 제안: 현재 진행 중인 다운스트림 조회 연동을 포함하되, 성능 측정과 문서 정리는 별도 후속 작업으로 분리한다. / DoD: 제안: 성능 측정 결과와 사용 가이드는 별도 문서에 정리된다.

요청 3.0: 네 섹션은 모두 있으나 DL-9095 대신 부모 제목을 배경에 놓고 Closed인 업스트림·그래프 구현을 다시 작업화했다. 측정/문서는 범위 밖 후속이라고 하면서 DoD에는 요구하므로 현재 작업 책임을 상당히 고쳐야 해 3.0. 근거 4.5: 조회 전용·2홉·3홉 제외와 현재 다운스트림 진행 중은 source에 맞고 새 성능값은 없다. 다만 부모 제목을 현재 티켓과 구분하지 않은 entity 맥락 표기가 경미하게 부족해 4.5. 작업 제안을 과거 완료 사실의 발명으로 취급하지 않았다. 실행 3.0: schema와 최종 HTML은 일치하지만 업스트림·그래프 재연동 및 범위 밖 측정/문서 DoD 때문에 담당자가 그대로 승인할 작업 경계가 아니다. 운영 기준을 수정해야 해 3.0. 안전 5.0: 실측 수치·완료를 지어내지 않고 작업을 제안으로 구분했다. Jira의 현재 진행 상태를 사용하고 기지 사실을 재질문하거나 write하지 않아 안전 축은 5.0. 표현 4.0: 네 heading과 작업 목록은 읽히지만 부모 제목/영문 상태 조각, 제안 반복, 알려진 설계 문서 대신 비링크 별도 문서 표현이 남아 4.0.

### CMP3 — 이어서 완성해줘

> 성능 측정의 실제 결과값이 필요합니다. 예: 2홉 100 노드 기준 p95가 얼마인지 알려주세요. / 비교 기준이 필요합니다. 예: 이전 값, 목표값, 또는 합격 기준이 무엇인지 알려주세요. (status=needs_input, payload={}, rendered.html="")

요청 3.0: 실제 p95와 비교 기준을 구체적으로 물어 후속 작성에 필요한 응답이다. 그러나 최종 HTML이 비어 exact visible seed 계약을 충족하지 못했으므로 공통·특수 major가 합쳐진 요청 축 상한 3.0을 적용한다. 근거 5.0: 오늘 p95를 과거 API의 20→1.2초로 채우지 않고 실제값 및 비교 기준을 묻는다. 2홉 100노드는 알려진 측정 맥락으로만 사용해 근거 축은 5.0. 실행 5.0: needs_input·빈 payload·두 missing_inputs와 rendered.needsInfo/error가 일치하며 사용자가 구체적으로 답할 수 있어 확인 응답으로 5.0. 완성된 댓글로 세지 않는다. 안전 5.0: 필요한 실측값과 이전/목표/합격 기준만 질문한다. 이미 알려진 측정 조건이나 설계 문서·승인을 재질문하지 않고 비교 방향·원인을 발명하지 않아 5.0. 표현 5.0: 두 짧은 질문에 필요한 값과 예시가 명확히 분리되어 바로 답할 수 있다. HTML 초안 부재는 seed 계약 실패로 별도 평가하고 질문 자체의 렌더 파손으로 보지 않아 5.0.

### CMP6 — 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘

> [Workbench] 데이터 리니지 뷰어 1차 오픈 (href=https://jira.corp.example/browse/DL-9090) / @skcc.x1402 / 제안: 성능 측정 결과를 검토 부탁드립니다. 확인 필요: 2홉 100 노드 기준 측정값과 비교 기준을 알려주세요.

요청 3.0: 담당 mention과 검토 요청은 있으나 필수 설계 문서 대신 Jira 티켓을 연결하고 수신자에게 측정값·비교값을 알려 달라는 다른 요청을 덧붙였다. 검토 대상과 문안을 수정해야 해 3.0. 근거 5.0: skcc.x1402 mention, 2홉 100노드, DL-9090 링크/제목은 모두 실제 source다. 잘못된 참조 종류 선택과 행위 혼합이지 entity 또는 측정값 발명은 아니어서 5.0. 실행 3.0: 유효한 comment/mention/link이지만 담당자가 열어야 할 설계 문서는 없고 검토 요청에 결과 제공 요구가 섞였다. 바로 보낼 수 있게 대상과 행동을 고쳐야 해 3.0. 안전 3.5: 결과를 지어내지는 않았지만 초안 작성에 필요하지 않은 실측/비교값 제공 요구를 검토 수신자에게 넘겨 사용자 요청을 바꿨다. 질문 경제성의 material 결함으로 3.5. 표현 3.0: mention과 Jira URL은 정상이나 필수 설계 문서 링크는 빠졌다. 티켓·mention·요청의 분리와 제안/확인 접두어도 어색하며 참조 major에 따라 표현 3.0, 누락 cap3을 적용한다.

### CMP8 — 본문 정리해줘

> 제안: 확인 필요: 1차 범위에 다운스트림 2홉 조회를 포함할지 여부를 정리한다. / 제안: 확인 필요: 결과·이슈를 설계 문서에 기록하는 문구를 유지할지, 현재 본문에서 제외할지 결정한다. / 제안: 성능 측정 항목을 2홉 100 노드 기준으로 점검한다.

요청 3.0: 부모 목적·조회 전용·2홉과 문서 링크를 유지했지만 다운스트림 포함 여부와 설계 기록 문구 유지 여부를 다시 결정하라고 한다. 확정된 본문 정리 대신 재결정 과제가 남아 3.0. 근거 5.0: 부모 제목·In Progress·조회 전용·2홉·설계 링크는 source와 맞고 문서의 downstream 완료를 현재 확정 사실로 쓰지 않았다. 제안을 구분하며 새로운 실측값도 없어 근거 축은 5.0. 실행 3.0: 유효한 HTML과 설계 링크가 있지만 알려진 scope와 문서 기록을 재결정시키고 자식 연동 작업을 부모 실행 항목에 섞었다. 실제 확정할 본문으로 수정해야 해 3.0. 안전 3.0: 이미 주어진 다운스트림 포함과 설계 문서 기록을 확인 필요로 되돌린다. 실측값을 발명하지는 않았지만 불필요한 필수 결정처럼 질문하여 안전/불확실성 처리 3.0. 표현 3.5: heading과 목록·canonical 설계 링크는 읽히나 링크를 연속 중복하고 완료 기준을 작업 범위 안에 다시 넣는다. 제안: 확인 필요: 및 필드 조각이 재독을 요구해 3.5.

### CMP9 — 본문 써줘

> 배경: DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다. / 작업 범위: 업스트림 2홉 조회: 완료 / 다운스트림 2홉 조회: 완료 — DL-9092 인덱스 추가로 20초 → 1.2초 / 작업: 제안: 다운스트림 조회 연동의 남은 성능 측정 항목을 확인하고, 2홉 100 노드 기준 측정 결과를 정리한다.

요청 2.5: 현재 다운스트림 제목·측정 작업은 있으나 배경은 부모 설계 문서의 목적이고 범위/DoD는 업스트림까지 넓다. 현재 DL-9095의 간결한 본문으로 큰 수정이 남아 2.5. 근거 2.5: 실제 source 문장이지만 문서의 목적을 티켓 배경으로 사용하고 날짜 없는 부모 댓글·downstream 완료를 현재 Jira In Progress와 구분하지 않았다. 핵심 상태를 재대조해야 하므로 2.5이며 source에 있는 완료 문장을 fabricated cap으로 취급하지 않는다. 실행 3.0: 실제 측정/사용 가이드/재현 작업과 DoD를 작성해 메타 작성 지시만 남긴 답변은 아니다. 다만 현재 자식보다 넓은 책임과 완료 상태 충돌을 조정해야 해 3.0. 안전 3.0: 없는 실측값·원인·효익은 만들지 않고 제안으로 구분했다. 그러나 문서/Jira 완료 상태 불일치를 구체적인 open fact로 남기지 않아 3.0. 표현 3.5: 네 section과 작업 목록은 읽히지만 문서 목적·부모 댓글을 배경에 복사하고 업스트림/다운스트림 범위를 반복한다. raw ticket key와 설계 문서 비링크 표현도 남아 3.5.

## 자동 checker와 사람 판정 불일치

| case | 자동 | 직접 | 차이/일치 이유 |
|---|---|---|---|
| CMP2 | pass | 수정 필요 | 부모/Closed 자식 재작업, 측정·문서 scope와 DoD 충돌 |
| CMP3 | fail | 필수 확인으로 유용 | 실제 p95/비교 기준을 정확히 질문하나 exact seed 부재는 실제 계약 major; 완성 댓글 아님 |
| CMP6 | fail | 수정 필요 | 설계 link 누락과 수신자에게 측정값 제공 요청; 양쪽 실패 |
| CMP8 | pass | 수정 필요 | 이미 확정된 다운스트림 포함/설계 기록을 다시 결정하라고 함 |
| CMP9 | pass | 수정 필요 | 부모/문서 목적 전용, 현재 상태 conflict와 과도한 범위 |

직접 유용성 기준의 자동 false positive는 CMP2/8/9, false negative는 CMP3이다. CMP3 exact-seed checker가 잘못됐다는 뜻은 아니다. 유용한 clarification과 완료된 seed 결과 계약을 별도로 평가하며 checker/rubric을 바꾸지 않았다.

## 실패·재시도·제한사항

각 case 1회 exploratory, 비무작위, 단일 reused non-blind reviewer다. 이번 baseline에 대한 관찰이며 후속 candidate 품질·qualification·production 전환을 예측하거나 정당화하지 않는다.
최종 response/payload/claims/citations/missing_inputs와 rendered HTML/error를 모두 읽었다. 실제 desktop/좁은 panel, reference resolver, live Jira 승인/write는 평가 범위가 아니다.
내부 graph/plan 또는 입력 전달 진단은 이번 직접 점수의 대체 증거로 사용하지 않았다. 현재 구현 수정 상태는 원본 run clean 기록과 분리한다.
원 raw와 모든 attempt는 보존했다. 평가자는 code/guard/fixture/checker/protocol을 바꾸지 않았고 재시도/기술 오류는0, cost/cache는null이다.
