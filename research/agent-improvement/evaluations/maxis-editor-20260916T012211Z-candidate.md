# Maxis Editor v19 candidate 직접평가 — 20260916T012211Z

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
| runGroupId | `maxis-editor-20260916T012211Z-411b16d6` |
| repeatIndex | `1` |
| repetitions | `1` |
| candidateCommit | `391c8ab05644ce7c26963733c9d13c644b2aea89` |
| candidateDirty | `false` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| promptVersion | `ltm-maxis-dify-2026.09.16-editor-writing-v19` |
| publishedEditor | `0b618d12-4247-45c3-8d5b-1790804621d4` |

Primary [candidate.json](../../../.cache/agent-evaluation/maxis-editor-20260916T012211Z-411b16d6/candidate.json) · SHA-256 `f7403652bcd63b8323462fdee3447098e5b7be1913742b069bf3f81ba9342483`.
전체 175개 item별 근거·상한·cap: [direct-review-candidate-amendment-1.json](../../../.cache/agent-evaluation/maxis-editor-20260916T012211Z-411b16d6/direct-review-candidate-amendment-1.json) · SHA-256 `a392e44c6772786d62dc85bd041e3e3d2a2f493b05ea380253545052dba4941d`.
원본 raw와 최초 [평가 JSON](../../../.cache/agent-evaluation/maxis-editor-20260916T012211Z-411b16d6/direct-review-candidate.json) SHA `635eb592a1f6bf4c7f6665acab297dc5c80fe661336e1432b21ed3af8b50e62a` 보존. 수정본은 scoringPolicy.usabilityDisaggregation 문구만 바로잡았고 175개 판정·점수·metrics는 동일하다. [수정 receipt](../../../.cache/agent-evaluation/maxis-editor-20260916T012211Z-411b16d6/direct-review-candidate-correction-receipt-1.json) SHA `1230d0574f3610acf8e6effc132299cba806cedaf3dcd9f1b21d84df6f18cf76`.

## 비교 가능성 및 evidence 선택

| 필드 | 값 |
|---|---|
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` |
| candidateKey | `4a259252f27fdab7e84f2ecef08e9cec3263f367bef6f0f07fb92d077bd96bac` |
| comparabilityKey | `2d1bd7329889cbf17eb4323292efa2cb1ac0535cb24b78a7be5f7a4c8c2abc24` |
| selectionPolicy | `complete-run-no-substitution` |
| aggregation | `case = min(Σ(각 축×0.2), 해당 cap); suite/overall = 모든 case-attempt 산술평균` |
| percentileMethod | `nearest-rank-over-all-case-attempts` |

[동일 패널 baseline](maxis-editor-20260916T010302Z-baseline.md)과 benchmark·5 case·source·checker·model·rubric가 같다. 후보 commit/prompt/published identity는 다르다.
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
| candidateOrderIndex | `2` |
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
| CMP2 | fail | blocked | 6 | 1 | 2946 | 844 | 3790 |
| CMP3 | fail | ok | 4.2 | 1 | 2972 | 509 | 3481 |
| CMP6 | fail | ok | 3.8 | 1 | 2972 | 349 | 3321 |
| CMP8 | pass | ok | 4.7 | 1 | 2946 | 714 | 3660 |
| CMP9 | pass | ok | 4.6 | 1 | 2946 | 529 | 3475 |

canonical metrics: attempts 5, durationSeconds 23.3, calls 5; p50 4.6s / p95 6s. calls는 받은 Dify 응답 수이며 내부 LLM 실행 수가 아니다.
전체 prompt/completion/total = 14782 / 2945 / 17727; cachedTokens/costUsd = null/null. attempt당 시간 4.66s.
attempt당 prompt/completion/total = 2956.4 / 589 / 3545.4. baseline 전체 token은 null이므로 18202 부분합과 17727을 비교한 절감률을 만들지 않는다.
자동 2/5, structural contract failure 0, contract 평가 unavailable 0, blocked 1, 기술 실패 0, retry 0.

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
| RF (6×5) | 5 | 9 | 13 | 3 | 2.3 |
| FG (6×5) | 8 | 1 | 7 | 14 | 2.7 |
| CA (6×5) | 19 | 0 | 5 | 6 | 3 |
| SU (8×5) | 13 | 3 | 8 | 16 | 3.4 |
| CR (6×5) | 7 | 11 | 2 | 10 | 3.2 |
| 전체 160 | 52 | 24 | 35 | 49 | 2.4/5 |
 전체 160 item 근거는 linked ignored JSON에 보존한다. checklist 수는 정확도 비율이 아니며 NA는 구체 비적용 사유를 갖는다.

## 배터리·case 특수 검토요소

공통 context→RF, reference fidelity→CR. case 고유 목표: CMP2 네 section; CMP3 exact seed+확인·방향/원인 비발명; CMP6 실제 mention+2홉100노드+문서; CMP8 부모 why/scope·자식 완료 DoD 비반복; CMP9 현재 기능/검증/문서·UX효익 비발명.

| case | 특수 item | 판정 | 실제 근거 |
|---|---|---|---|
| CMP2 | editor_context_preservation | major | DL-9095 재작성 목적과 source 내용이 빈 최종 HTML에 전혀 전달되지 않았다. |
| CMP2 | editor_reference_fidelity | major | source와 연결된 최종 content/reference 결과가 없어 문맥을 보존한 참조 전달이 성립하지 않는다. |
| CMP2 | cmp2_four_sections | major | 배경·범위·작업·DoD 네 section 모두 없다. 발명하지 않았다는 이유로 빈 결과를 성공으로 보지 않는다. |
| CMP3 | editor_context_preservation | major | seed 문자열은 그대로 있으나 이어진 '높아서'가 원래 열려 있던 측정 의미를 다른 확정 사실로 바꿨다. |
| CMP3 | editor_reference_fidelity | pass | 문서 title/href가 source와 일치하고 raw unresolved marker 없이 anchor로 전달됐다. |
| CMP3 | cmp3_seed_exactness | major | exact visible seed만 충족한다. confirmation marker는 없으며 'p95 가 생각보다 높아서'가 금지된 higher inference다. |
| CMP6 | editor_context_preservation | minor | 담당자 성능 검토 요청의 핵심 의도는 보존했으나 실제 검토 기준이 일반화됐다. |
| CMP6 | editor_reference_fidelity | pass | payload person marker→skcc.x1402 mention과 실제 설계 문서 URL이 일치하며 unresolved placeholder가 없다. |
| CMP6 | cmp6_review_target | major | 담당 mention·실제 문서 링크는 충족했지만 2홉 100노드 measurement 기준이 누락됐다. |
| CMP8 | editor_context_preservation | major | 부모 목적/조회 전용은 보존했으나 원본 테이블 상세 업스트림·다운스트림 2홉 기능 완료 조건이 빠졌다. |
| CMP8 | editor_reference_fidelity | minor | 설계 링크는 정확하나 DL-9090은 평문 key로 남는다; 실 UI resolver는 이 component에서 검증하지 않는다. |
| CMP8 | cmp8_parent_child_boundary | pass | 테이블 간 리니지 탐색·조회 전용·3홉 제외로 parent why/scope를 쓰고 자식 title/완료 작업을 부모 DoD에 반복하지 않았다. |
| CMP9 | editor_context_preservation | major | 현재 downstream title은 남았지만 current 기능/검증을 구체화하지 못하고 parent 문서 목적이 배경을 대체했다. |
| CMP9 | editor_reference_fidelity | minor | 문서 canonical link는 정상이나 parent DL-9090은 평문 key다; 실제 UI 판정으로 확대하지 않는다. |
| CMP9 | cmp9_ticket_context | major | UX효익 발명은 없으나 현재 ticket의 기능/검증 맥락 대신 parent 문서 목적·막연한 미결 점검을 작성해 해당 특수 목표를 충족하지 못했다. |

특수 15개 = pass 3, minor 3, major 9, na 0. 원본 spec goal/elements는 JSON에 verbatim 보존했다.

## 배터리별 실제 출력과 평가

| case | RF | FG | CA | SU | CR | raw mean | cap | 최종 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CMP2 | 1 | 3 | 2 | 3 | 1 | 2 | 3 | 2 |
| CMP3 | 2 | 2 | 3 | 2 | 3.5 | 2.5 | 2 | 2 |
| CMP6 | 3 | 2 | 3.5 | 3 | 3.5 | 3 | 2 | 2 |
| CMP8 | 3 | 3.5 | 3.5 | 5 | 4 | 3.8 | 3 | 3 |
| CMP9 | 2.5 | 3 | 3 | 4 | 4 | 3.3 | 3 | 3 |

평균 2.4/5. 유용 0/5 = 완성 초안 0 + 필수 사실 확인 0. cap 해당 5/5, 실제 감산 4/5.

### CMP2 — 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘

> status=blocked; payload={}; claims=[]; citations=[]; missing_inputs=[]; warnings=[]; rendered={ok:false, needsInfo:false, error:[], html:""}

RF: DL-9095의 배경·범위·작업·완료 조건을 요청했지만 payload={}와 html=""만 전달됐다. 질문이나 복구 안내도 없어 요청 충족1. FG: 노출된 사실 발명은 없지만 source에 근거한 답변도 전무하다. 빈 출력 정책에 따라3이며 보이지 않는 차단 전 내용을 채점하지 않는다. CA: contractOk=true blocked와 rendered.ok=false는 일치한다. 하지만 검토/삽입할 draft와 재개 정보가 없어2. SU: 성공·외부 변경을 주장하지 않으나 warnings/missing_inputs/error가 모두 비어 실패 이유와 복구 경로를 알 수 없어3. CR: 본문도 오류 안내도 없어 사용자 대면 가독성1.
caps: material_omission. 유용성: blocked-no-user-output. answerSha256: `7fa2cd083c1cd3da477effa1fdebf21e31322a7340d357dd26129d2b731574d7`.

### CMP3 — 이어서 완성해줘

> 오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다 / 제안: p95 가 생각보다 높아서 조회 전용 범위에서 화면 탐색 경로와 렌더링 부담을 먼저 점검해보자.

RF: seed 문자열은 남았지만 미완성 문장 뒤에 높음을 확정한 별도 제안과 일반 기능 설명이 붙었다. 필요한 비교 방향 확인이 없어 완성 요청의 핵심을 충족하지 못해2. FG: 실측값·비교 방향은 source에 없다. 실제 최종 'p95 가 생각보다 높아서'는 추천문 안에 든 확정 전제이며 현재 측정의 허위 사실이므로2 및 fabricated cap. CA: payload와 렌더가 동일하고 유효한 comment이나 확인되지 않은 측정 방향이 들어 있어 게시 전 핵심 재작성 필요:3. SU: 확인 가능한 source로는 p95 방향을 알 수 없는데 질문/확인 표시 없이 높음을 선택했다. 제안 prefix가 사실 전제를 불확실성으로 바꾸지 않아2. CR: canonical 설계 링크는 읽히지만 미완성 seed 단독 문단, 반복 p95, 일반 기능 제안이 댓글 흐름을 끊어3.5.
caps: fabricated_fact_or_entity. 유용성: requires-material-revision. answerSha256: `5c768502ab592fdbbdd73f490af892cf2aaf9bf38fc6ac5b161badb59cbb0250`.

### CMP6 — 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘

> <span data-type="mention" data-id="skcc.x1402">@skcc.x1402</span> / 제안: 성능 측정 결과를 검토해 주세요. 결과와 이슈는 설계 문서에 기록해 두었습니다. / [설계] 리니지 뷰어 1차 (canonical link)

RF: 정확한 담당 mention과 검토 요청·설계 링크는 있다. 그러나 필수 2홉 100노드 기준이 없고 검토 자료의 완료 기록을 단정해 실사용 전 수정이 필요하다:3. FG: 최신 댓글은 결과를 적고 있는 중이고 설계 문서는 성능 측정을 남은 일로 둔다. 현재 성능 측정 결과/이슈가 이미 기록됐다는 최종 문장은 확인되지 않은 완료 사실이다. 문서 내 과거 개발 진행 결과와 혼동해2 및 fabricated cap. CA: mention과 링크가 실제로 조립되고 comment schema도 유효하지만 기준 누락과 확인되지 않은 검토 자료 위치 때문에 바로 검토 가능한 댓글이 아니다:3.5. SU: known assignee를 재질문하지는 않았다. 다만 실측 결과 기록 여부를 확인 없이 완료형으로 단정해 불확실성을 숨겼다:3. CR: 불필요한 raw @담당자는 없고 3문단이 간결하다. 그러나 특수 검토 대상의 핵심 2홉 100노드 기준이 빠져 해당 축 상한3.5.
caps: fabricated_fact_or_entity, material_omission. 유용성: requires-material-revision. answerSha256: `b8f25e802362b252b305de10b210cc9b277be61f3b824b607dee120f6b8ea4a6`.

### CMP8 — 본문 정리해줘

> 배경: DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다. / 진행 결과 (최종 수정 기준) / 작업 범위: 테이블 간 리니지를 화면에서 탐색할 수 있게 한다. 1차 범위는 조회 전용이다. / DoD: 2홉 100 노드 기준의 성능 측정 결과; 사용 가이드

RF: 부모 기능 목적·조회 전용·3홉 제외·성능/가이드 DoD는 있다. 하지만 원본의 테이블 상세 업/다운스트림 2홉 기능 완료 조건이 사라져 핵심 본문을 보완해야 하므로3. FG: title·조회 전용·3홉 제외·2홉100노드·가이드는 source에 있다. 다만 문서의 기록 목적을 부모 배경으로 전용하고 '진행 결과 (최종 수정 기준)'를 맥락 없이 남겨3.5. 새 수치나 구현 완료는 발명하지 않았다. CA: 유효한 description과 네 section은 있지만 핵심 기능 수용 조건 없이 '구현한다'와 성능/가이드만 남아 실제 완료 판단 전에 보완 필요:3.5. SU: 확인된 범위와 조건을 불필요하게 묻지 않고 제안은 제안으로 표시한다. 새 실측/완료 사실이나 쓰기 실행을 단정하지 않고 부모-자식 변경을 지시하지 않아5. CR: heading·DoD bullet·canonical 문서 링크는 읽기 쉽다. 문서 heading 잔재·title/목적 반복·raw ticket key가 남은 국소 표현 결함으로4.
caps: material_omission. 유용성: requires-material-revision. answerSha256: `f66dd5fce542c56216def1ea44c97a4ef84b0df1c62683df27fb960904c22458`.

### CMP9 — 본문 써줘

> 배경: DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다. / 작업: 제안: 다운스트림 조회 연동 경로를 점검한다. 제안: 미결 항목의 처리 기준을 확인한다. / DoD: 다운스트림 조회 연동이 확인 가능한 경로로 연결된다.

RF: 현재 다운스트림 ticket에 대한 네 section과 제외 범위·설계 링크는 있다. 그러나 배경은 부모 설계 문서의 목적이고 기능/검증은 '확인 가능한 경로' 같은 동어반복이라 상당한 수작업 필요:2.5. FG: current title·3홉 제외·문서 URL은 정확하며 새 UX효익이나 상태 완료는 주장하지 않았다. 하지만 부모 문서의 기록 목적을 현재 subtask 배경으로 잘못 연결하고 현재 기능/검증 맥락을 제대로 선별하지 못해3. CA: schema/초안 경계는 유효하지만 '미결 항목의 처리 기준'이 무엇인지 없고 검증 가능한 downstream 2홉/성능 기준이 없어 실행 전 중요 보완이 필요하다:3. SU: 새 수치·완료·담당 발명은 없고 제안으로 구분했다. 다만 이미 3홉 제외가 확정됐는데 미결 처리 기준을 일반 확인 과제로 남기고 확인 대상도 구체화하지 않아4. CR: 네 section·짧은 bullets·canonical 문서 링크는 읽힌다. 배경의 raw parent key와 범위/DoD의 반복적 표현은 국소 수정이 필요해4.
caps: material_omission. 유용성: requires-material-revision. answerSha256: `4f72e0f15564ede6a5e282e9208b6a2ff0a877cc3b38a21c1a65444daa486f35`.

## 자동 checker와 사람 판정 불일치

| case | 자동 / 직접 유용 | 판독 |
|---|---|---|
| CMP2 | fail / fail | 둘 다 실패: 빈 blocked로 본문 없음. |
| CMP3 | fail / fail | 둘 다 실패: seed는 남지만 높음을 확정하고 confirmation marker 없음. |
| CMP6 | fail / fail | 둘 다 실패: 2홉100노드 누락에 더해 완료 기록의 unsupported premise를 직접 판독. |
| CMP8 | pass / fail | false positive: 네 section/parent 경계는 있으나 원본 양방향2홉 기능 조건 누락과 문서 목적 전용. |
| CMP9 | pass / fail | false positive: 형식/문서 링크는 있으나 current 검증 조건이 없고 DoD가 모호하다. |

유용성 false positive CMP8, CMP9; false negative 없음. contractOk/자동 pass를 사람의 완결성·사실성 통과로 치환하지 않았다. checker/fixture/version을 수정하지 않았다.

## 실패·재시도·제한사항

단일 재사용·비blind 평가자다. 구현을 작성하지 않았지만 이전 독립 code critic 및 과거 실행 노출이 있으므로 완전 맹검/독립 패널 재현으로 주장하지 않는다.
자동 도구는 175개 completeness·spec/metadata 일치·축 ceiling·cap·산술만 검증했다. LTM runtime/동일 endpoint judge 호출, internal plan 대체, focused 성공 치환은 없다.
문서/mention의 synthetic normalization을 확인했을 뿐 실제 desktop/좁은 UI의 badge·링크 조작이나 승인·쓰기 안전성을 검증한 것이 아니다.
CMP3 '높아서', CMP6 '기록해 두었습니다'의 완료 전제는 제안 표지에도 남는 사실 주장이다. CMP8/9의 source 문장 오귀속은 새 외부 사실 발명 cap과 구분하고 material 누락을 적용했다.
동일 baseline→candidate 평균 2.14→2.40(+0.26)이나 useful 0→0, FG3.4→2.7이다. 출력 반환 복구를 품질 완성으로 주장하지 않으며 production qualification 근거가 아니다.
