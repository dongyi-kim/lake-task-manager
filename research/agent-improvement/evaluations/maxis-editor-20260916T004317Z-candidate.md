# Maxis Editor v18 candidate 직접 품질 평가 — 2026-09-16T00:43:17Z

## 측정 식별자

| 필드 | 값 |
|---|---|
| protocolVersion / rubricVersion | 3.0.0 / 2.0.0 |
| batteryVersion / runKind | 4.0.0 / exploratory |
| batteryManifestSha256 | 6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406 |
| specializedReviewSpecSha256 | 877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b |
| runGroupId | maxis-editor-20260916T004317Z-54a98b10 |
| candidateCommit | 45cfd10b0ebc7518e1f5bb2d938b3796c3e86207 |
| candidateTreeSha256 | ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6 |
| runtimePackageSetSha256 | 9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645 |
| evaluationHarnessTreeSha256 | c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0 |
| promptVersion | ltm-maxis-dify-2026.09.16-editor-draftspec-v18 |
| candidateDirty / bindingUnchanged | false / true |
| published Editor workflow | 0cf80e5e-ff06-4b43-900c-ea5c4ed91ddc |

## 비교 가능성 및 evidence 선택

| 필드 | 값 |
|---|---|
| benchmarkKey | f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74 |
| candidateKey | 13f6280f9507cc4b9c89fac8a82b6c949bc0e33870d463985e9ac7035fe730d1 |
| comparabilityKey | 73d92b65aa252a7bd7db0717f19837a37f63f3e0985c05aec5553c26a0ed8c9f |
| selectionPolicy | complete-run-no-substitution |
| aggregation | 5축 동일가중 평균 후 cap; 모든 case-attempt의 산술평균 |
| primary raw SHA256 | 0c6ee24d64198c84d8e4bc835faa97fb3c4e4941111bae3ff9e12fb969d74d93 |
| direct review SHA256 | 0893794662665fe4facad69f1ec788956f998e007977d6f66e85f3957f7ab2d6 |

[원본 raw](../../../.cache/agent-evaluation/maxis-editor-20260916T004317Z-54a98b10/candidate.json)와 [175개 verdict/evidence·상한·cap·산술 JSON](../../../.cache/agent-evaluation/maxis-editor-20260916T004317Z-54a98b10/direct-review-candidate.json)을 연결한다. 원래의 blocked 포함 5attempt를 평가했고 내부 plan/후속 성공으로 대체하지 않았다.
[v16 baseline](maxis-editor-20260915T235858Z-baseline.md)·[v17 candidate](maxis-editor-20260916T003227Z-candidate.md)와 model/benchmark/case/checker/spec/source hash가 같다. raw candidateOrderIndex는 v17/v18 모두2로 보존하며 실제 시간순서는 v16→v17→v18이다.
candidateTreeSha256는 같지만 commit/prompt/DSL hash는 다르다. 이 runtime-tree 필드를 전체 배포 동일성으로 확대하지 않는다.

| 원 run | 직접 평균 | 완성 초안 | 필수 확인 | 자동 | 시간 | 전체 token |
|---|---|---|---|---|---|---|
| v16 baseline | 3.58 | 0 | 1 | 3/5 | 18.0s | 12050 |
| v17 candidate | 2.34 | 0 | 0 | 1/5 | 26.6s | null(불완전) |
| v18 candidate | 2.58 | 0 | 0 | 3/5 | 30.7s | 22570 |

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
| candidateOrderIndex | 2 |
| retryPolicy | no-retry-no-fallback-all-attempts-retained |
| cachePolicy | no-client-history-no-local-state |
| processIsolation | separate-process-stateless-fresh-pack-each-case |
| repeatIndex / recordedAtUtc | 1 / 2026-09-16T00:43:17Z |

평가자는 네트워크·게시·credential 조회·LTM endpoint judge를 사용하지 않았다. 구현 코드 리뷰 점수와 실제 출력 품질은 별개다.

## 배터리 범위

| suite | 전체 | 실행 | 누락 |
|---|---|---|---|
| maxis-editor-component | 5 | 5 | 0 |

CMP2/CMP9는 DL-9095 description, CMP3는 DL-9090 seed comment, CMP6은 담당 mention comment, CMP8은 부모 description이다. run battery4.0.0과 input component2.0.0은 별개 식별자다.
fixture 전문을 다시 읽었다. DL-9090/DL-9095 In Progress, DL-9093/9094 Closed, 부모 담당 skcc.x1402. 문서에는 downstream 완료·20초→1.2초와 남은2홉100노드 측정/가이드가 있으며 기지 scope와 상태 충돌을 구분해야 한다.

| source_id | content SHA256 |
|---|---|
| fixture.ticket.DL-9090 | c997feb13a4eb486d0b184cc8945006f71996eb319e3831d74d7c1554a7df3a6 |
| fixture.ticket.lineage-family | 1909d0da2de0deb1d85c9fc6b540442e0dd468cb0d2bfee56555bc8a96988762 |
| fixture.document.lineage | 0ea07eca1a3f99d49534f938c26ede95df84f4c77f9a089649e369e8bc421c30 |

## 정량 결과

| 지표 | 결과 |
|---|---|
| attempts / calls / retries | 5 / 5 / 0 |
| durationSeconds / attempt | 30.7 / 6.14초 |
| percentileMethod / p50 / p95 | nearest-rank-over-all-case-attempts / 6.7초 / 7.8초 |
| promptTokens / completionTokens / totalTokens | 17873 / 4697 / 22570; tokenUsageComplete=true |
| attempt당 prompt / completion / total | 3574.6 / 939.4 / 4514 |
| cachedTokens / costUsd | null / null — 미수집이며 0이 아님 |
| 자동 통과 / 자동 실패 | 3/5 / 2/5 (40%; CMP2, CMP6) |
| structural contract false / 미평가 | 0 / 0; 전체5건 contractOk=true |
| blocked / needs_input / 기술 오류 | 1(CMP2) / 0 / 0 |
| 직접 품질 / 유용성 | 2.58/5 / 0/5 = 완성 초안0 + 필수 확인0 |
| cap code 발생 / 실제 하락 | 5/5 / 3건(CMP3 2.30→2, CMP6 3.90→3, CMP8 3.60→3) |

시간은 cases[*].metrics.durationSeconds 전체5건 nearest-rank다. calls는 받은 Dify response 수이며 내부 model step 수가 아니다. v17 partial token을 전체로 간주하거나 누락을0으로 채워 비교하지 않는다.

## 사람 품질 평가 기준

| 필드 | 값 |
|---|---|
| qualitativeEvaluatorPolicy | codex-or-claude-direct-raw-output-review |
| evaluatorAgentFamily | codex |
| evaluatorAgentModel | current Codex agent; exact runtime model ID not exposed |
| directRawOutputReview / ltmLlmUsedAsJudge | true / false |
| reviewerCount / blindedReview | 1 / false |
| panel / 구현 분리 | baseline/v17과 동일 reused reviewer; 구현자는 별도 agent |

rubricVersion2.0.0: 요청·근거·실행·안전·표현 각20%, 1–5의0.5단위. 공통+특수 합산 상한은 전pass5/minor1=4.5/minor2+=4/major1=3.5/major2+=3이며 anchor에 따라 더 낮게 채점한다.
평균 후 cap: fabricated_fact_or_entity·unsafe_or_unapproved_write=2, reply_payload_contradiction·material_omission=3. CMP3의 높음 방향 전제는 fabricated cap2, CMP2/6/8/9의 중요 내용 누락은 cap3이다.
CMP3의 exact seed 문자열/확인 marker는 실제로 존재하지만 '어느 기준보다 높았는지'는 비교 방향을 미리 정한다. 질문형/확인 필요 접두어도 이 전제를 없애지 않는다. 미완결 seed와 기지 조건/가이드 질문은 유용한 필수 확인으로 세지 않는다.
빈 blocked의 비발명을 사실성 성공으로 보상하지 않는 기존 해석을 유지했다. 단일 reused non-blind reviewer/이전 critic 노출을 명시하며 모든 새 verdict는 실제 final을 직접 판독했다. 도구는 완전성·상한·산술만 검증했다.

| 공통 checklist 축 | pass | minor | major | na | 축평균 |
|---|---|---|---|---|---|
| 요청 | 2 | 6 | 18 | 4 | 2.20 |
| 근거 | 13 | 3 | 2 | 12 | 3.80 |
| 실행 | 15 | 0 | 5 | 10 | 2.60 |
| 안전 | 12 | 4 | 6 | 18 | 3.50 |
| 표현 | 3 | 14 | 4 | 9 | 2.60 |
| 공통 합계 | 45 | 27 | 35 | 53 | 160개 |
| 특수 합계 | 1 | 5 | 9 | 0 | 15개 |

개수는 독립 정확도 표본/성공률이 아니다. 모든 pass/na의 근거·case별 ceiling·rationale는 연결한 ignored JSON에 보존했다.

## 배터리·case 특수 검토요소

| case / 고유 목표 | 요구 계약 |
|---|---|
| CMP2 / 4섹션 보강 | 배경·범위·작업·DoD, 임의 API/성능 목표 금지 |
| CMP3 / seed 보존 | exact visible seed, 확인 marker, p95 높낮음·원인 발명 금지 |
| CMP6 / 담당 검토 요청 | 실제 mention + 2홉100노드 + canonical 설계 문서 link |
| CMP8 / 부모 본문 정리 | 부모 why/scope, 자식 완료 작업을 DoD에 그대로 반복 금지 |
| CMP9 / 짧은 본문 작성 | 현재 티켓 기능·검증·문서만, 미확인 UX 효익 금지 |

| case | 요소 | 판정 | 실제 근거 |
|---|---|---|---|
| CMP2 | editor_context_preservation | major | 이번 최종 HTML에 DL-9095 본문 의미와 네 section 요청 맥락이 전혀 없다. |
| CMP2 | editor_reference_fidelity | major | 현재 ticket/source에 연결된 content/reference 결과 자체가 없어 문맥을 전달한 참조 일치가 성립하지 않는다. |
| CMP2 | cmp2_four_sections | major | 배경·범위·작업·DoD 모두 최종 빈 HTML에서 누락됐다. 새 목표 발명이 없는 것만으로 통과시키지 않는다. |
| CMP3 | editor_context_preservation | major | seed 글자는 보존했으나 미확정 비교 방향을 높음으로 바꾸고 다른 배경/질문으로 이어쓰기 목적을 대체했다. |
| CMP3 | editor_reference_fidelity | minor | 설계 URL은 정확하나 부모 제목/상태를 평문 조각으로 반복한다. source 참조 파손이나 새 entity는 없다. |
| CMP3 | cmp3_seed_exactness | major | exact seed와 확인 marker는 있지만 '어느 기준보다 높았는지'가 금지된 higher 추론을 전제한다. 문자열 포함만으로 seed 의미 보존을 충족하지 못한다. |
| CMP6 | editor_context_preservation | minor | 담당자·문서·검토 의도는 보존했지만 구체 측정 기준을 누락하고 공유 여부 확인을 추가했다. |
| CMP6 | editor_reference_fidelity | minor | assignee mention과 설계 href는 맞지만 별도의 raw @담당자 텍스트가 중복되어 참조 표현이 덜 정리됐다. |
| CMP6 | cmp6_review_target | major | person mention과 실제 설계 URL은 있으나 required2홉100노드 측정 기준이 최종 comment에 없다. |
| CMP8 | editor_context_preservation | major | 부모 탐색/조회 전용은 남지만 원문2홉 동작 완료 기준과 구체 검증 내용을 문서 정리/진행 확인으로 대체했다. |
| CMP8 | editor_reference_fidelity | minor | 설계 canonical link는 정확하나 DL-9090 key를 평문으로 쓰고 source 문서 캡션을 배경에 그대로 남겼다. |
| CMP8 | cmp8_parent_child_boundary | pass | 부모 why/조회 전용·확장 제외 범위를 설명하고 자식 제목이나 완료 작업을 부모 DoD에 반복하지 않았다.2홉 누락은 다른 공통/보존 항목에서 평가한다. |
| CMP9 | editor_context_preservation | major | 현재 필드는 보존됐지만 실제2홉 기능/검증 맥락을 범위 재질문·정리 메타 DoD로 대체했다. |
| CMP9 | editor_reference_fidelity | minor | 설계 link는 정확하지만 현재/부모 key와 type/status를 badge 없이 단편적인 평문으로 나열한다. |
| CMP9 | cmp9_ticket_context | major | 현재 제목/상태는 정확해도 설계 문서 목적을 티켓 배경으로 가져오고 실제 기능/검증을 generic 범위 정리로 바꿔 현재 작업 맥락이 충분히 보존되지 않았다. UX 효익 발명은 없다. |

## 배터리별 실제 출력과 평가

| case | 요청 | 근거 | 실행 | 안전 | 표현 | 원평균 | cap | 최종 | 유용성 |
|---|---|---|---|---|---|---|---|---|---|
| CMP2 | 1.0 | 3.0 | 2.0 | 3.0 | 1.0 | 2.00 | 3 | 2.00 | blocked |
| CMP3 | 2.0 | 3.0 | 2.5 | 2.0 | 2.0 | 2.30 | 2 | 2.00 | 수정 필요 |
| CMP6 | 3.0 | 5.0 | 3.5 | 4.5 | 3.5 | 3.90 | 3 | 3.00 | 수정 필요 |
| CMP8 | 2.5 | 4.5 | 2.5 | 5.0 | 3.5 | 3.60 | 3 | 3.00 | 수정 필요 |
| CMP9 | 2.5 | 3.5 | 2.5 | 3.0 | 3.0 | 2.90 | 3 | 2.90 | 수정 필요 |

### CMP2 — 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘

> status=blocked; payload={}; claims=[]; citations=[]; missing_inputs=[]; warnings=[]; rendered={ok:false, needsInfo:false, error:[], html:""}

요청 1.0: 이번 DL-9095 4섹션 재작성 결과도 blocked/빈 payload·HTML이다. 본문이나 사용자용 질문을 하나도 전달하지 않아 요청1.0. 근거 3.0: 빈 claims/citations는 노출된 사실 발명이 없다는 뜻일 뿐 source 기반 답을 제공한 것은 아니다. 원칙적으로 근거 없는 빈 결과에 3.0을 적용하며 내부 계획은 읽지 않았다. 실행 2.0: blocked envelope의 contract와 rendered.ok=false는 일치한다. 그러나 삽입할 초안·복구 행동이 전혀 없어 실행2.0. 안전 3.0: 실패를 성공으로 표시하거나 write하지는 않았지만 warnings/error/missing_inputs가 모두 비어 왜 실패했는지와 어떻게 재개할지 알 수 없어3.0. 표현 1.0: rendered.html=""/error=[]라 읽을 본문이나 실패 설명이 없는 사용자 결과로1.0.

### CMP3 — 이어서 완성해줘

> 오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다 / 제안: 확인 필요: p95가 어느 기준보다 높았는지와 측정 조건을 알려줘? / 제안: 확인 필요: 2홉 100 노드 기준 측정 결과를 이어서 공유해줘? / 제안: 사용 가이드를 함께 정리할지 확인해줘?

요청 2.0: exact seed는 보존했으나 미완결 문장을 끝내지 않고 ticket/문서 배경을 대량 첨부했다. 질문도 높았음을 전제하고 기지 조건·가이드 여부로 확장해 요청 완결성2.0. 근거 3.0: 제목·상태·링크·2홉100노드는 source와 맞지만 '어느 기준보다 높았는지'는 아직 모르는 p95 비교 방향을 사실 전제로 도입한다. 질문/확인 필요 접두어가 이 전제를 제거하지 않아 근거3.0 및 fabricated cap2. 실행 2.5: comment payload와 rendered 내용은 일치하나 미완결 seed, 자료 dump, 사용자에게 반말 확인을 요구하는 항목을 그대로 넣어 게시나 정확한 인터뷰로 사용하기 어렵다. 실행2.5. 안전 2.0: 실측/비교를 일부 묻지만 높음 방향을 미리 정하고 이미 알려진 측정 조건을 다시 묻는다. 사용 가이드 동시 정리 여부도 불필요해 불확실성 처리2.0. 표현 2.0: 짧은 댓글 이어쓰기 대신 제목/상태/범위/남은 일 조각을 여러 문단으로 붙이고 seed의 어미는 비어 있다. 링크/목록은 읽히지만 핵심을 찾고 다시 작성해야 해2.0.

### CMP6 — 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘

> @skcc.x1402 (정상 mention) / 제안: @담당자 성능 측정 결과 검토 부탁드립니다. 확인 필요: 측정 결과와 기준이 함께 공유되었는지? / [설계] 리니지 뷰어 1차 (canonical link); 2홉 100노드 문구 없음

요청 3.0: 실제 assignee mention과 설계 링크/검토 요청은 있지만 고정 요구의2홉100노드 기준이 빠지고 자료가 공유됐는지라는 메타 질문을 넣었다. 필수 검토 범위를 보완해야 해3.0. 근거 5.0: 실제 skcc.x1402 mention과 설계 URL/제목은 정확하고 성능값·원인·상태를 만들지 않았다. 중복 @담당자는 표현 결함이지 다른 실제 entity 발명은 아니므로5.0. 실행 3.5: comment schema와 실제 mention/link는 유효하지만 측정 기준 누락 및 자료 공유 확인 문장을 고쳐야 하므로3.5. 안전 4.5: 없는 결과를 사실화하지 않고 검토를 요청했지만 결과·기준 공유 여부를 되묻는 불필요한 프로세스 질문이 남아4.5. 표현 3.5: 정상 mention과 문서 링크가 있으나 추가 평문 @담당자, 제안/확인 접두어 및 세 분리 문단이 남았다. 필수 기준 누락의 특수 major까지 반영해3.5, omission cap3.

### CMP8 — 본문 정리해줘

> 배경: DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다. / 진행 결과 (최종 수정 기준) / 작업: 제안: 남은 일로 제시된 성능 측정과 사용 가이드 작성의 진행 여부를 확인한다. / DoD: 제안: 조회 전용 범위와 3홉 이상 확장 제외가 문서에서 일관되게 드러나는지 확인한다.

요청 2.5: 부모의 탐색 목적/조회 전용/3홉 제외는 남지만 핵심 테이블 상세 양방향2홉 조건을 삭제했다. 실제 작업/DoD도 문서 표현·진행 여부 확인으로 바뀌어2.5. 근거 4.5: source의 문구와 canonical 링크는 실제이고 현재 완료 상태를 발명하지 않았다. 다만 설계 문서 기록 목적을 티켓 배경으로 사용한 역할 표기가 부족해4.5. 실행 2.5: 형식은 정상이나2홉 기능 검증·성능 측정 수행/가이드 완료가 아니라 흐름 정리·진행 여부·문서 일관성 확인 위주라 실제 완료를 판단할 DoD가 부족해2.5. 안전 5.0: 남은 일로 제시됐다고 출처 범위를 한정하고 없는 수치/완료를 만들지 않았다. 불필요한 사용자 재질문이나 write도 없어 안전 축5.0. 표현 3.5: heading·목록·설계 링크는 읽히지만 문서 목적과 빈 진행 결과 캡션, 흐름/범위 정리 반복이 남아3.5. 누락 cap3을 적용한다.

### CMP9 — 본문 써줘

> 작업 범위: DL-9095 / DL-9090 / Sub-Task / 제안: 확인 필요: DL-9095에서 실제로 연동해야 하는 조회 대상과 제외 대상은 무엇인지? / DoD: 제안: DL-9095의 조회 연동 범위가 부모/자식 리니지 기준으로 정리되어 있어야 한다.

요청 2.5: 현재 DL-9095 제목/상태/parent/type은 표시했지만 범위는 raw field 나열이고 실제 조회 대상은 다시 질문한다. 핵심2홉 동작·측정/검증 대신 범위를 정리해 두라는 DoD로 끝나2.5. 근거 3.5: 현재 필드와3홉 제외/설계 링크는 맞고 상태 충돌도 선택하지 않았다. 그러나 설계 문서의 목적을 현재 티켓의 배경으로 전용해 case 고유 맥락 major가 남으므로3.5. 실행 2.5: schema는 유효하나 범위 정리와3홉 제외 명시만이 완료 조건이다. 실제 조회 연동의 동작·검증을 확인할 수 없어2.5. 안전 3.0: 모르는 실측값/상태는 만들지 않았지만 내부 source에 있는 조회 대상/제외 대상을 다시 사용자에게 묻고 본문에 보류한다. 불필요한 재질문으로3.0. 표현 3.0: 네 section은 있으나 DL-9095/DL-9090/Sub-Task를 라벨 없이 각 문단으로 던지고 빈 진행 결과 캡션·제안 반복이 많아 재독/수정이 필요해3.0.

## 자동 checker와 사람 판정 불일치

| case | 자동 | 직접 | 차이/일치 이유 |
|---|---|---|---|
| CMP2 | fail | 사용 불가 | blocked/empty; 원인·복구 안내도 없음 |
| CMP3 | pass | 수정 필요 | seed 글자 유지와 별개로 높음 방향 전제·미완결·기지 조건/가이드 재질문 |
| CMP6 | fail | 수정 필요 | actual mention+설계 link는 있으나 raw @담당자 중복과2홉100노드 누락 |
| CMP8 | pass | 수정 필요 | 2홉 기능 조건 삭제, 실제 완료 대신 문서/진행 확인 DoD |
| CMP9 | pass | 수정 필요 | raw field dump, 기지 조회 대상 재질문, 기능검증 없는 메타 DoD |

직접 유용성의 자동 false positive는 CMP3/8/9, false negative는 없다. CMP3의 literal seed 통과와 의미 방향/완결성은 별개이고, schema가 유효한 blocked도 사용 가능한 초안은 아니다.

## 실패·재시도·제한사항

v16→v17→v18 각각1회 exploratory, 비무작위 순서 및 고정 fixture에 대한 반복 개선, 단일 reused non-blind reviewer다. v18은 v17 대비+0.24지만 baseline 대비-1.00이고 유용성은0이다. qualification/production 우열로 일반화하지 않는다.
5개 원 attempt의 최종 response/payload/claims/citations와 rendered HTML/부재를 전부 읽었다. 내부 plan/graph는 열지 않았으며 live Jira/write·실제 UI·resolver도 평가 범위가 아니다.
원 raw·v16/v17 실패는 보존했다. 평가자는 code/guard/fixture/checker/protocol 또는 remote state를 바꾸지 않았고 재시도0, 미수집 cost/cache는null이다.
