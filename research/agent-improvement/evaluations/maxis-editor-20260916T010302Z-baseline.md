# Maxis Editor v18 baseline 직접평가 — 20260916T010302Z

## 측정 식별자

최종 사용자용 응답·원본 fixture·seed·고정 기대 계약을 직접 대조한 독립 판독. 자동 green이나 내부 계획 성공을 품질로 치환하지 않았다.

| 필드 | 값 |
|---|---|
| protocolVersion | `3.0.0` |
| rubricVersion | `2.0.0` |
| batteryVersion | `4.0.0` |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| runKind | `exploratory` |
| runGroupId | `maxis-editor-20260916T010302Z-09fc43d3` |
| repeatIndex | `1` |
| repetitions | `1` |
| candidateCommit | `2f1ba1cc82a152a630acc9f68a9972074c35f921` |
| candidateDirty | `false` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| promptVersion | `ltm-maxis-dify-2026.09.16-editor-draftspec-v18` |
| publishedEditor | `0cf80e5e-ff06-4b43-900c-ea5c4ed91ddc` |

Primary [baseline.json](../../../.cache/agent-evaluation/maxis-editor-20260916T010302Z-09fc43d3/baseline.json) · SHA-256 `52ec4a05608f24598dca59f35e39bfa64c0b7e2bf9b306e5102b114c0d909d25`.
전체 175개 item별 근거·상한·cap: [direct-review-baseline.json](../../../.cache/agent-evaluation/maxis-editor-20260916T010302Z-09fc43d3/direct-review-baseline.json) · SHA-256 `5a43896ee3fea1c56f5f02ea7628fedff463cea74fde890a7cf5880b1bd40c55`.
원본 raw는 불변 보존. 새 경량 문서는 ignored 평가 JSON의 판정/점수를 요약하며 원본을 대체하지 않는다.

## 비교 가능성 및 evidence 선택

| 필드 | 값 |
|---|---|
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` |
| candidateKey | `0d3800ecaec3c6ae0efc7db521cabfd12e07fc827f0ffb48e5c3ef6c2bc247ac` |
| comparabilityKey | `f654266dd8de2070b60e49972e88ce9e7edddf8af1ef21e37c5e30469fe0d009` |
| selectionPolicy | `complete-run-no-substitution` |
| aggregation | `case = min(Σ(각 축×0.2), 해당 cap); suite/overall = 모든 case-attempt 산술평균` |
| percentileMethod | `nearest-rank-over-all-case-attempts` |

이 실행은 새 clean baseline이다. 과거 v18 성공·실패로 교체하지 않았으며 이후 candidate는 별도 run group으로 비교한다.
complete=true, 5개 원 attempt 전부 사용; focused/closure 결과 대체 없음. run battery 4.0.0, input의 component_battery_version 2.0.0은 서로 다른 식별자다. candidateTreeSha256는 raw 값을 보존한다. 이 digest 하나로 원격 배포 동일성을 추정하지 않으며 commit·prompt·publishedEditor를 함께 기록한다.

## 실행 조건

| 필드 | 값 |
|---|---|
| model | `gpt-5.4-mini` |
| simpleModel | `not-used` |
| provider | `dify` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| structuredOutputBackend | `dify` |
| structuredBackendVersion | `legacy-internal` |
| structuredBackendFallbackPolicy | `forbid` |
| dataProfile | `repository-synthetic-editor-component-v1` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| runtimeProfile | `dev-inactive-stateless-component-v1` |
| candidateOrderIndex | `1` |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state` |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |

고정 순서 CMP2 → CMP3 → CMP6 → CMP8 → CMP9, case별 1 attempt. 단일 탐색 실행이며 counterbalanced ≥5회 qualification이 아니다.
provider cache·가격 정보는 null/unrecorded. reviewer는 network/LLM judge/게시/실제 write를 수행하지 않았다.

## 배터리 범위

maxis-editor-component 전체 5 / 실행 5 / 누락 0. EditorDraft+pure markup 범위이며 full compose·실제 reference resolver·desktop/좁은 UI·승인/게시 검증이 아니다.

| 원본 source | SHA-256 |
|---|---|
| fixture.ticket.DL-9090 | `c997feb13a4eb486d0b184cc8945006f71996eb319e3831d74d7c1554a7df3a6` |
| fixture.ticket.lineage-family | `1909d0da2de0deb1d85c9fc6b540442e0dd468cb0d2bfee56555bc8a96988762` |
| fixture.document.lineage | `0ea07eca1a3f99d49534f938c26ede95df84f4c77f9a089649e369e8bc421c30` |

DL-9090: 조회 전용, 테이블 상세 업/다운스트림 2홉, 결과/이슈 설계 문서 기록. 자식 DL-9093/9094 Closed, DL-9095 In Progress; DL-9092 Closed.
문서: 기존 구현 진행 결과, DL-9092 인덱스 20→1.2초, 남은 성능 측정(2홉100노드)·가이드, 3홉은 2차. 과거 결과를 현재 측정 완료로 확장하지 않는다.

## 정량 결과

| case | 자동 | status/error | 초 | received calls | prompt | completion | total |
|---|---:|---|---:|---:|---:|---:|---:|
| CMP2 | fail | blocked | 7 | 1 | 3565 | 1493 | 5058 |
| CMP3 | fail | bad_request | 2.4 | 0 | null | null | null |
| CMP6 | fail | ok | 4.6 | 1 | 3589 | 427 | 4016 |
| CMP8 | fail | blocked | 5.5 | 1 | 3565 | 1166 | 4731 |
| CMP9 | fail | blocked | 4.7 | 1 | 3565 | 832 | 4397 |

canonical metrics: attempts 5, durationSeconds 24.3, calls 4; p50 4.7s / p95 7s. calls는 받은 Dify 응답 수이며 내부 LLM 실행 수가 아니다.
전체 prompt/completion/total = null / null / null; cachedTokens/costUsd = null/null. attempt당 시간 4.86s.
CMP3 token usage가 없어 전체 token은 null이다. 나머지 4건의 부분합 14284/3918/18202는 전체 합계가 아니며 후보 대비 절감률을 산출할 수 없다.
자동 0/5, structural contract failure 0, contract 평가 unavailable 1, blocked 3, 기술 실패 1, retry 0.

## 사람 품질 평가 기준

| 필드 | 값 |
|---|---|
| qualitativeEvaluatorPolicy | codex-or-claude-direct-raw-output-review |
| evaluatorAgentFamily | codex |
| evaluatorAgentModel | current Codex agent; exact runtime model ID not exposed |
| directRawOutputReview / ltmLlmUsedAsJudge | true / false |
| reviewerCount / blindedReview | 1 / false |
| reusedReviewer / sameEvaluatorPanelAcrossCandidates | true / true |
| implementationAndReviewSameAgent | false |

RF 요청 충족, FG 사실성, CA 계약·실행, SU 안전·불확실성, CR 표현·렌더링. 각 1–5/0.5 간격, 가중치 0.2. 공통+특수 결함을 합친 축 상한은 all-pass 5, minor1 4.5, minor≥2 4, major1 3.5, major≥2 3.
case cap: fabricated_fact_or_entity/unsafe_or_unapproved_write 2; reply_payload_contradiction/material_omission 3. 상한 이하에서 실제 영향/anchor로 점수를 정했다.
유용성은 즉시 쓸 요청 초안 또는 구체적이고 필수인 사실 확인 질문. 완성 초안과 유용한 확인 질문을 분리하며 generic 질문/중요 재작성은 usable이 아니다.
빈 응답의 비발명을 사실성 성공으로 보상하지 않는다(FG3). 유효 blocked와 최종 envelope 없는 기술 실패를 구분하며 hidden plan을 결과로 대체하지 않는다.

| 공통 32 item × 5의 축별 집계 | pass | minor | major | na | 평균 |
|---|---:|---:|---:|---:|---:|
| RF (6×5) | 2 | 2 | 19 | 7 | 1.4 |
| FG (6×5) | 4 | 0 | 4 | 22 | 3.4 |
| CA (6×5) | 12 | 0 | 6 | 12 | 2.1 |
| SU (8×5) | 11 | 1 | 5 | 23 | 3.2 |
| CR (6×5) | 0 | 4 | 8 | 18 | 1.5 |
| 전체 160 | 29 | 7 | 42 | 82 | 2.14/5 |
 전체 160 item 근거는 linked ignored JSON에 보존한다. checklist 수는 정확도 비율이 아니며 NA는 구체 비적용 사유를 갖는다.

## 배터리·case 특수 검토요소

공통 context→RF, reference fidelity→CR. case 고유 목표: CMP2 네 section; CMP3 exact seed+확인·방향/원인 비발명; CMP6 실제 mention+2홉100노드+문서; CMP8 부모 why/scope·자식 완료 DoD 비반복; CMP9 현재 기능/검증/문서·UX효익 비발명.

| case | 특수 item | 판정 | 실제 근거 |
|---|---|---|---|
| CMP2 | editor_context_preservation | major | DL-9095 본문을 배경·작업 범위·완료 조건으로 재작성의 핵심 의미가 빈 최종 HTML에 전달되지 않았다. |
| CMP2 | editor_reference_fidelity | major | CMP2 source에 연결된 content/reference 결과가 전혀 없어 문맥을 보존한 최종 참조 전달이 성립하지 않는다. |
| CMP2 | cmp2_four_sections | major | 필수 네 section을 모두 요청했지만 최종 HTML은 빈 문자열이다. 새 API/목표가 없다는 이유로 빈 출력을 성공으로 채점하지 않는다. |
| CMP3 | editor_context_preservation | major | 오늘 리니지 뷰어 p95 seed의 문장/의미가 사용자에게 전달되지 않았다. |
| CMP3 | editor_reference_fidelity | major | seed와 source 문맥에 연결된 final content/reference가 전혀 없어 전달 일치 결과가 없다. |
| CMP3 | cmp3_seed_exactness | major | exact seed·확인 marker가 모두 없는 기술 실패다. 높음/낮음 발명이 없다는 사실을 seed 계약 성공으로 치환하지 않는다. |
| CMP6 | editor_context_preservation | minor | 담당자·설계 문서·검토 의도는 유지했으나 구체 측정 기준을 빼고 자료 공유 확인을 추가했다. |
| CMP6 | editor_reference_fidelity | minor | actual assignee mention과 canonical 설계 link는 맞지만 별도 raw @담당자 텍스트가 중복돼 참조 표현이 덜 정리됐다. |
| CMP6 | cmp6_review_target | major | person mention과 실제 문서 href는 있지만 required2홉100노드 기준이 최종 코멘트에 없다. |
| CMP8 | editor_context_preservation | major | DL-9090 부모 본문의 목적과 범위 정리의 핵심 의미가 빈 최종 HTML에 전달되지 않았다. |
| CMP8 | editor_reference_fidelity | major | CMP8 source에 연결된 content/reference 결과가 전혀 없어 문맥을 보존한 최종 참조 전달이 성립하지 않는다. |
| CMP8 | cmp8_parent_child_boundary | major | 부모 why/scope와 자식 책임 분리를 보여 줄 최종 본문이 없다. 자식 반복이 없는 빈 문자열은 목적·범위 설명 성공이 아니다. |
| CMP9 | editor_context_preservation | major | DL-9095 현재 다운스트림 티켓 본문 작성의 핵심 의미가 빈 최종 HTML에 전달되지 않았다. |
| CMP9 | editor_reference_fidelity | major | CMP9 source에 연결된 content/reference 결과가 전혀 없어 문맥을 보존한 최종 참조 전달이 성립하지 않는다. |
| CMP9 | cmp9_ticket_context | major | 현재 다운스트림 기능·검증·문서를 설명한 최종 본문이 전혀 없다. 미확인 UX 효익을 발명하지 않았다는 것만으로 현재 맥락 계약을 충족하지 않는다. |

특수 15개 = pass 0, minor 2, major 13, na 0. 원본 spec goal/elements는 JSON에 verbatim 보존했다.

## 배터리별 실제 출력과 평가

| case | RF | FG | CA | SU | CR | raw mean | cap | 최종 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CMP2 | 1 | 3 | 2 | 3 | 1 | 2 | 3 | 2 |
| CMP3 | 1 | 3 | 1 | 2.5 | 1 | 1.7 | 3 | 1.7 |
| CMP6 | 3 | 5 | 3.5 | 4.5 | 3.5 | 3.9 | 3 | 3 |
| CMP8 | 1 | 3 | 2 | 3 | 1 | 2 | 3 | 2 |
| CMP9 | 1 | 3 | 2 | 3 | 1 | 2 | 3 | 2 |

평균 2.14/5. 유용 0/5 = 완성 초안 0 + 필수 사실 확인 0. cap 해당 5/5, 실제 감산 1/5.

### CMP2 — 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘

> status=blocked; payload={}; claims=[]; citations=[]; missing_inputs=[]; warnings=[]; rendered={ok:false, needsInfo:false, error:[], html:""}

RF: DL-9095 본문을 배경·작업 범위·완료 조건으로 재작성 요청에 blocked와 빈 payload/HTML만 반환했다. 결과·구체 질문·복구 경로가 없어 요청 충족1.0. FG: 최종 claims/citations가 비어 잘못된 사실을 노출한 것은 아니다. 다만 source 기반 설명이나 미확인점도 전달하지 않은 결과는 grounded 답변이 아니므로 빈 출력 정책에 따라3.0. 차단 전 내용은 추정하지 않는다. CA: contractOk=true인 blocked와 rendered.ok=false가 일치하지만 실제 검토/삽입할 draft도 재개 방법도 없어 실행 가능성2.0. SU: 성공이나 외부 변경을 주장하지는 않았지만 warnings/missing_inputs/error가 모두 비어 차단의 영향·이유·복구 방법을 사용자에게 알리지 않아3.0. CR: rendered.html=""와 error=[]라 읽을 본문·오류 설명이 없으므로 표현1.0.
caps: material_omission. 유용성: blocked-no-user-output. answerSha256: `8f9c209fa335884c4347807bc68f8c7aaa189a83b7a999e1706cae3f795c9f56`.

### CMP3 — 이어서 완성해줘

> errorCode=bad_request; response={}; rendered={}; contractOk absent; answerSha256 absent; metrics={attempts:1,calls:0,tokenUsageComplete:false}

RF: bad_request로 response/rendered={}다. seed를 이어 쓴 문장도 실측/비교 기준의 필수 질문도 전달하지 못해 요청1.0. FG: 노출된 p95 값/방향 발명은 없으나 오늘 측정이라는 seed의 사실이나 미확인 비교를 설명하는 grounded 답도 없다. 빈 응답의 비발명을 성공으로 보상하지 않아3.0. CA: calls=0이고 final envelope/contractOk/answer hash가 없어 유효한 최종 계약 응답도 실행할 draft도 받지 못한 기술 실패다. 실행1.0. SU: 외부 변경·p95 발명은 관측되지 않지만 필요한 실측/비교 인터뷰와 사용자용 실패·재개 설명이 모두 없다. raw errorCode만 남아2.5. CR: rendered={}로 읽을 댓글이나 오류 안내가 전혀 없어 사용자 대면 표현1.0.
caps: material_omission. 유용성: technical-failure-no-user-output. answerSha256: `unavailable`.

### CMP6 — 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘

> @skcc.x1402 (data-type=mention,data-id=skcc.x1402) / 제안: @담당자 성능 측정 결과 검토 부탁드립니다. 확인 필요: 측정 결과와 기준이 함께 공유되었는지? / [설계] 리니지 뷰어 1차 (canonical link); 2홉100노드 문구 없음

RF: actual assignee mention과 설계 링크/검토 요청은 있으나 required2홉100노드 기준을 누락하고 결과·기준의 공유 여부를 되묻는다. 구체 검토 대상이 부족해3.0. FG: 정상 mention의 skcc.x1402와 설계 제목/URL은 source에 맞고 실측값·상태·원인을 만들지 않았다. 덧붙인 @담당자는 표현 중복이지 다른 실제 사람을 발명한 주장이 아니므로5.0. CA: comment schema와 actual mention/link는 유효하지만 측정 기준을 추가하고 공유 여부 메타 질문을 고쳐야 즉시 검토용으로 쓸 수 있어3.5. SU: 미지 결과를 사실화하거나 write하지 않았지만 검토 문안 작성에 불필요한 자료 공유 여부 확인이 남아4.5. CR: 정상 mention/link 옆에 raw @담당자를 중복하고 제안/확인 접두어 및 세 문단으로 분절했다. 누락된 측정 기준의 특수 major와 함께 표현3.5; material omission cap3.
caps: material_omission. 유용성: requires-material-revision. answerSha256: `1166cd9642a1b0a4ff4efa7628a63be332745467059e3ab6307001c47d307b78`.

### CMP8 — 본문 정리해줘

> status=blocked; payload={}; claims=[]; citations=[]; missing_inputs=[]; warnings=[]; rendered={ok:false, needsInfo:false, error:[], html:""}

RF: DL-9090 부모 본문의 목적과 범위 정리 요청에 blocked와 빈 payload/HTML만 반환했다. 결과·구체 질문·복구 경로가 없어 요청 충족1.0. FG: 최종 claims/citations가 비어 잘못된 사실을 노출한 것은 아니다. 다만 source 기반 설명이나 미확인점도 전달하지 않은 결과는 grounded 답변이 아니므로 빈 출력 정책에 따라3.0. 차단 전 내용은 추정하지 않는다. CA: contractOk=true인 blocked와 rendered.ok=false가 일치하지만 실제 검토/삽입할 draft도 재개 방법도 없어 실행 가능성2.0. SU: 성공이나 외부 변경을 주장하지는 않았지만 warnings/missing_inputs/error가 모두 비어 차단의 영향·이유·복구 방법을 사용자에게 알리지 않아3.0. CR: rendered.html=""와 error=[]라 읽을 본문·오류 설명이 없으므로 표현1.0.
caps: material_omission. 유용성: blocked-no-user-output. answerSha256: `8f9c209fa335884c4347807bc68f8c7aaa189a83b7a999e1706cae3f795c9f56`.

### CMP9 — 본문 써줘

> status=blocked; payload={}; claims=[]; citations=[]; missing_inputs=[]; warnings=[]; rendered={ok:false, needsInfo:false, error:[], html:""}

RF: DL-9095 현재 다운스트림 티켓 본문 작성 요청에 blocked와 빈 payload/HTML만 반환했다. 결과·구체 질문·복구 경로가 없어 요청 충족1.0. FG: 최종 claims/citations가 비어 잘못된 사실을 노출한 것은 아니다. 다만 source 기반 설명이나 미확인점도 전달하지 않은 결과는 grounded 답변이 아니므로 빈 출력 정책에 따라3.0. 차단 전 내용은 추정하지 않는다. CA: contractOk=true인 blocked와 rendered.ok=false가 일치하지만 실제 검토/삽입할 draft도 재개 방법도 없어 실행 가능성2.0. SU: 성공이나 외부 변경을 주장하지는 않았지만 warnings/missing_inputs/error가 모두 비어 차단의 영향·이유·복구 방법을 사용자에게 알리지 않아3.0. CR: rendered.html=""와 error=[]라 읽을 본문·오류 설명이 없으므로 표현1.0.
caps: material_omission. 유용성: blocked-no-user-output. answerSha256: `8f9c209fa335884c4347807bc68f8c7aaa189a83b7a999e1706cae3f795c9f56`.

## 자동 checker와 사람 판정 불일치

| case | 자동 / 직접 유용 | 판독 |
|---|---|---|
| CMP2 | fail / fail | 둘 다 실패: 네 section 없이 빈 blocked. |
| CMP3 | fail / fail | 둘 다 실패: bad_request로 seed/확인 질문 없음; 모델의 사실 발명으로 간주하지 않는다. |
| CMP6 | fail / fail | 둘 다 실패: raw @담당자 중복은 자동 검출. 실제 mention/link가 있어도 2홉100노드 누락으로 직접 사용 불가. |
| CMP8 | fail / fail | 둘 다 실패: parent 목적/범위 결과가 없는 blocked. |
| CMP9 | fail / fail | 둘 다 실패: current ticket 본문이 없는 blocked. |

유용성 false positive 없음; false negative 없음. contractOk/자동 pass를 사람의 완결성·사실성 통과로 치환하지 않았다. checker/fixture/version을 수정하지 않았다.

## 실패·재시도·제한사항

단일 재사용·비blind 평가자다. 구현을 작성하지 않았지만 이전 독립 code critic 및 과거 실행 노출이 있으므로 완전 맹검/독립 패널 재현으로 주장하지 않는다.
자동 도구는 175개 completeness·spec/metadata 일치·축 ceiling·cap·산술만 검증했다. LTM runtime/동일 endpoint judge 호출, internal plan 대체, focused 성공 치환은 없다.
문서/mention의 synthetic normalization을 확인했을 뿐 실제 desktop/좁은 UI의 badge·링크 조작이나 승인·쓰기 안전성을 검증한 것이 아니다.
CMP3 기술 오류는 outcome 실패로 denominator에 남겼으며 노출되지 않은 텍스트의 hallucination으로 평가하지 않았다. 원본 raw와 평가 JSON은 보존된다.
추가 native trace는 별도 진단 증거이며 본 직접평가 점수에 사용하지 않았다. 단일 탐색 결과만으로 DEV 활성화/production qualification을 권고하지 않는다.
