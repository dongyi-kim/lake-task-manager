# Maxis Editor 단위 분기 candidate 직접 평가 — 2026-09-15 14:06 UTC

## 측정 식별자

실제 최종 출력 5건의 직접 평균은 **3.52/5**다. 유용 1/5는 **완성 초안 0 + 필수 사실 확인 1**이며, 게시 가능한 댓글/본문이 1건이라는 뜻이 아니다.

| 필드 | 값 |
|---|---|
| protocolVersion | 3.0.0 |
| rubricVersion | 2.0.0 |
| batteryVersion | 4.0.0 (maxis-editor-component) |
| batteryManifestSha256 | 6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406 |
| specializedReviewSpecSha256 | 877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b |
| runKind | exploratory |
| runGroupId | maxis-editor-20260915T140612Z-c475c70f |
| candidateCommit | 70f510400c8a05b21c099d1e8ad44b1945afc3f2 |
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
| candidateKey | 29213e28ec37afcf71d6f56b8decfab4b144985ba28f757d0b5aa8c360a69b15 |
| comparabilityKey | 104d40e9c2729453c74fa88486b833b0cd772d55e8c559f52aab748d4d1bdbcb |
| selectionPolicy | complete-run-no-substitution |
| aggregation | 5축 동일가중 평균 후 cap; 모든 case-attempt의 산술평균 |
| primary raw SHA256 | a0e86a8180a5c454c2107f35766b1365c1f23489759a2ff40aa45defee51feea |
| direct review SHA256 | 8196f7c3676a3d9d5d3f955d73b83b1fcd64729753b79f2f0c873480c19d83a7 |

[원본 raw](../../../.cache/agent-evaluation/maxis-editor-20260915T140612Z-c475c70f/candidate.json)와 [175개 verdict/evidence·축 상한·cap·산술 JSON](../../../.cache/agent-evaluation/maxis-editor-20260915T140612Z-c475c70f/direct-review-candidate.json)을 연결한다. 원 run 전체만 평가하며 내부 plan/나중 성공으로 대체하지 않았다.
[새 baseline 보고서](maxis-editor-20260915T133810Z-baseline.md)와 benchmarkKey·model·5case/input/checker/spec/source hash가 같다. 평균 3.38→3.52(+0.14), 유용1→1/완성초안0→0이다. 단일 탐색이므로 우열·production 전환 근거가 아니다.
raw의 candidateTreeSha256는 baseline과 같지만 commit/promptVersion/배포 DSL hash/candidateKey는 다르다. 기록된 runtime tree hash를 전체 배포 동일성으로 확대 해석하지 않는다.

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
| repeatIndex / recordedAtUtc | 1 / 2026-09-15T14:06:12Z |

model/provider/원본 fixture 및 비활성 component 조건을 고정했다. endpoint/credential 원문은 포함하지 않으며 평가자는 원격 호출·LTM endpoint judge를 사용하지 않았다.

## 배터리 범위

| suite | 전체 | 실행 | 누락 |
|---|---:|---:|---:|
| maxis-editor-component | 5 | 5 | 0 |

실행: CMP2/CMP9 DL-9095 description, CMP3 DL-9090 seed comment, CMP6 DL-9090 담당 mention comment, CMP8 DL-9090 description. component battery4.0.0과 input case component2.0.0은 별개 식별자다.
3개 fixture 전문을 읽었다. 부모 DL-9090 In Progress, DL-9093/9094 Closed, DL-9095 In Progress이며 문서는 다운스트림 완료·20초→1.2초와 남은 2홉100노드 측정/가이드를 기록한다. 문서와 현재 Jira의 완료 상태 충돌을 분리해야 한다.

| source_id | content SHA256 |
|---|---|
| fixture.ticket.DL-9090 | c997feb13a4eb486d0b184cc8945006f71996eb319e3831d74d7c1554a7df3a6 |
| fixture.ticket.lineage-family | 1909d0da2de0deb1d85c9fc6b540442e0dd468cb0d2bfee56555bc8a96988762 |
| fixture.document.lineage | 0ea07eca1a3f99d49534f938c26ede95df84f4c77f9a089649e369e8bc421c30 |

## 정량 결과

| 지표 | 결과 |
|---|---|
| attempts / calls / retries | 5 / 5 / 0 |
| durationSeconds / attempt | 19.6 / 3.92초 |
| percentileMethod / p50 / p95 | nearest-rank-over-all-case-attempts / 4.8초 / 5.3초 |
| promptTokens / completionTokens / totalTokens | 8339 / 3179 / 11518 |
| attempt당 prompt / completion / total | 1667.8 / 635.8 / 2303.6 |
| cachedTokens / costUsd | null / null — 미수집이며 0이 아님 |
| 자동 통과 / 자동 실패 | 3/5 / 2/5 (40%; CMP3, CMP6) |
| structural contract 실패 / blocked / needs_input / 기술 오류 | 0 / 0 / 1 / 0 |
| 직접 품질 / 유용성 | 3.52/5 / 1/5 = 완성 초안0 + 필수 사실 확인1 |
| cap code 발생 | 1/5 (20%; CMP6 원평균3.80→cap3.00) |

시간은 canonical cases[*].metrics.durationSeconds 전체 5건의 nearest-rank다. calls는 받은 Dify response 수이며 내부 모델 step 수가 아니다. token은 baseline22841→11518이나 1회/비무작위 실행이고 cost/cache는 비교 불가하다.

## 사람 품질 평가 기준

| 필드 | 값 |
|---|---|
| qualitativeEvaluatorPolicy | codex-or-claude-direct-raw-output-review |
| evaluatorAgentFamily | codex |
| evaluatorAgentModel | current Codex agent; exact runtime model ID not exposed |
| directRawOutputReview / ltmLlmUsedAsJudge | true / false |
| reviewerCount / blindedReview | 1 / false |
| panel / 구현 분리 | baseline과 동일 reused reviewer; 구현자는 별도 root agent |

rubricVersion2.0.0: 요청·근거·실행·안전·표현 각20%, 1–5의0.5단위. 공통+특수 합산 상한은 전pass5/minor1=4.5/minor2+=4/major1=3.5/major2+=3이며 anchor에 따라 더 낮게 선택한다.
평균 후 cap: fabricated_fact_or_entity·unsafe_or_unapproved_write=2, reply_payload_contradiction·material_omission=3. CMP6은 설계 링크 누락 cap3; CMP9에는 실제 작업·검증 본문이 생겨 이전 메타 지시 누락 cap을 복사하지 않았다.
CMP3은 필요한 실제값/비교값만 묻기 때문에 clarification으로 유용하다. 그러나 최종 HTML에 seed가 없어 frozen exact-visible-seed 계약을 major로 판정, 요청 축3.0으로 제한한다. 보류 이유가 명시된 응답을 숨긴 누락 cap으로 중복 적용하지 않는다.
평가자는 후보 코드의 독립 critic도 맡아 선행 노출이 있다. non-blind/reused 제한을 명시하고 최종 출력 판단에 내부 plan 성공·코드 점수·상태 라벨을 대입하지 않았다. 도구는 누락/상한/cap/산술만 검증했다.

| 공통 checklist 축 | pass | minor | major | na | 축평균 |
|---|---:|---:|---:|---:|---:|
| 요청 | 16 | 5 | 6 | 3 | 3.00 |
| 근거 | 15 | 2 | 4 | 9 | 4.10 |
| 실행 | 16 | 0 | 4 | 10 | 3.60 |
| 안전 | 21 | 2 | 5 | 12 | 4.00 |
| 표현 | 7 | 14 | 2 | 7 | 3.70 |
| 공통 합계 | 75 | 23 | 21 | 41 | 160개 |
| 특수 합계 | 3 | 7 | 5 | 0 | 15개 |

개수는 독립 정확도 표본/성공률이 아니다. 모든 pass/na 포함 항목별 근거·case별 combined ceiling은 연결한 ignored JSON에 보존했다.

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
| CMP2 | editor_context_preservation | minor | 현재 다운스트림 제목/작업 유지 문장을 넣었으나 작업 범위와 DoD는 부모 전체 업스트림까지 포괄해 경계가 덜 정리됐다. |
| CMP2 | editor_reference_fidelity | minor | 현재 제목은 맞지만 평문이고 설계 문서도 href 없이 일반명으로만 언급한다. 깨진 token/새 entity는 없다. |
| CMP2 | cmp2_four_sections | pass | 배경/작업 범위/작업/완료 조건을 모두 생성했고 화면 멈춤 방지는 기존 버그 기반 제안 조건이다. 새 API나 임의 성능 SLA는 없다. |
| CMP3 | editor_context_preservation | minor | p95 실측과 비교 기준이라는 의미는 보존되나 오늘 리니지 뷰어 seed 전문은 최종 출력에 없다. 질문을 유용한 후속 응답으로만 인정한다. |
| CMP3 | editor_reference_fidelity | pass | 이 질문은 외부 entity 참조를 만들지 않으며 빈 references/claims/citations와 일치한다. resolved 문서를 미확인으로 되묻지도 않는다. |
| CMP3 | cmp3_seed_exactness | major | needs_input 질문은 방향을 발명하지 않지만 rendered.html이 비어 exact visible seed 및 seed 뒤 확인 marker라는 고정 결과 계약을 충족하지 못했다. 고정 rubric을 변경하지 않는다. |
| CMP6 | editor_context_preservation | minor | 담당자와 구체 검토 기준·요청은 유지했지만 결과가 기록된 설계 문서 참조라는 핵심 보조 맥락을 Jira 링크로 바꿨다. |
| CMP6 | editor_reference_fidelity | major | mention과 Jira URL 자체는 유효하나 확인된 설계 문서가 아닌 다른 source 종류를 필수 검토 링크 대신 선택했다. |
| CMP6 | cmp6_review_target | major | person mention·2홉 100노드는 있지만 href는 jira.corp.example/browse/DL-9090이다. required resolved design-document reference가 누락됐다. |
| CMP8 | editor_context_preservation | pass | 부모의 테이블 리니지 탐색, 조회 전용, 2홉과 결과/이슈 설계 기록이라는 핵심은 모두 남았다. |
| CMP8 | editor_reference_fidelity | minor | 설계 링크는 정확하나 DL-9092 key/부모 제목은 일반 평문으로 표시된다. 실제 UI badge는 검증하지 않았다. |
| CMP8 | cmp8_parent_child_boundary | minor | 자식 완료 세부는 작업 section에 남았으나 DoD에 그대로 복제하지는 않았다. 부모 목적/범위는 명시되어 case 고유 major 조건 전체에 해당하지 않는다. |
| CMP9 | editor_context_preservation | major | 현재 DL-9095 본문에 부모/설계 문서 목적과 업스트림 범위가 들어가 작업 단위의 핵심 경계가 보존되지 않았다. |
| CMP9 | editor_reference_fidelity | minor | 실제 ticket key는 평문이며 언급된 설계 기록 source의 canonical link가 없다. 새로운 entity/깨진 token은 없다. |
| CMP9 | cmp9_ticket_context | major | 실제 점검·검증 제안은 있으나 현재 티켓보다 넓은 부모/업스트림 사실과 source 상태 충돌을 그대로 사용한다. 미확인 UX 효익 발명은 없다. |

## 배터리별 실제 출력과 평가

| case | 요청 | 근거 | 실행 | 안전 | 표현 | 원평균 | cap | 최종 | 유용성 |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| CMP2 | 3.5 | 5.0 | 3.5 | 5.0 | 4.0 | 4.20 | 없음 | 4.20 | 수정 필요 |
| CMP3 | 3.0 | 5.0 | 5.0 | 5.0 | 5.0 | 4.60 | 없음 | 4.60 | 필수 확인 |
| CMP6 | 3.0 | 5.0 | 3.5 | 4.5 | 3.0 | 3.80 | 3 | 3.00 | 수정 필요 |
| CMP8 | 3.0 | 3.0 | 3.0 | 2.5 | 3.0 | 2.90 | 없음 | 2.90 | 수정 필요 |
| CMP9 | 2.5 | 2.5 | 3.0 | 3.0 | 3.5 | 2.90 | 없음 | 2.90 | 수정 필요 |

### CMP2 — 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘

> 작업 범위: 제안: 테이블 상세에서 업스트림과 다운스트림 2홉 조회를 조회 전용으로 제공한다. / 제안: 다운스트림 조회 연동은 현재 대상 티켓의 작업 범위로 유지한다. / DoD: 제안: 성능 측정 결과와 정리 내용이 설계 문서에 반영된다.

요청 3.5: 현재 다운스트림 제목과 책임을 명시하고 네 섹션을 제공했지만 범위·DoD는 업스트림 제공까지 확장한다. 현재 Sub-Task 책임을 좁히는 중요한 수정이 남아 3.5. 근거 5.0: 현재 제목/진행 중 및 2홉/조회 전용/3홉 제외는 source에 맞다. 화면 멈춤 방지는 기존 버그에 대한 제안 조건이며 측정 결과/새 SLA가 아니어서 5.0. 실행 3.5: 실제 점검·결과 기록·동작 조건을 썼고 schema도 유효하나 업스트림까지 포함한 완료 책임을 현재 티켓에 맞게 조정해야 해 3.5. 안전 5.0: 없는 실측값이나 완료 상태를 선택하지 않고 작업·DoD를 제안으로 남겼으며 알려진 조건을 재질문하지 않아 5.0. 표현 4.0: heading/목록은 분명하지만 제안 반복, 독립 영문 상태/제목, 설계 문서 비링크 표시가 남아 4.0.

### CMP3 — 이어서 완성해줘

> 성능 측정의 실제 값이 필요합니다. 예: p95가 몇 초인지 알려주세요. / 비교 기준이 필요합니다. 예: 이전 값 또는 목표 값이 무엇인지 알려주세요. (status=needs_input, payload={}, rendered.html="")

요청 3.0: p95 실제값과 비교 기준을 구체적으로 묻는 필요한 응답이다. 그러나 최종 HTML이 비어 frozen exact-visible-seed 요구를 충족하지 못하므로 공통/특수 상한에 따라 요청 축은 3.0. 근거 5.0: 과거 API 20→1.2초를 오늘 p95로 오인하지 않고 두 값을 모두 사용자에게 물어 사실·추론 경계를 지켰으므로 5.0. 실행 5.0: needs_input, 빈 payload, 두 missing_inputs와 rendered.needsInfo/error가 일치한다. 후속 작성에 답할 수 있는 구체 질문이므로 5.0. 안전 5.0: 실측값과 이전/목표값이라는 두 필수 입력만 묻고 기지 측정 조건/문서/허가를 재질문하지 않는다. 불확실성 처리 자체는 5.0. 표현 5.0: 짧고 구체적인 두 질문을 명확하게 나눠 전달한다. HTML 초안이 없다는 사실은 계약상 보류이지 질문 렌더 파손은 아니므로 표현은 5.0.

### CMP6 — 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘

> @skcc.x1402 / 제안: 확인 필요: 2홉 100 노드 기준 성능 측정 결과를 검토해 주세요. / [Workbench] 데이터 리니지 뷰어 1차 오픈 (href=https://jira.corp.example/browse/DL-9090)

요청 3.0: 담당 mention과 검토 기준/요청은 있지만 연결한 대상이 설계 문서가 아닌 Jira 티켓이다. 필수 검토 자료 접근을 고쳐야 하므로 3.0. 근거 5.0: mention skcc.x1402, 2홉 100노드, 링크의 DL-9090 제목/URL은 모두 실제 source다. 잘못된 참조 종류 선택이지 entity/사실 발명은 아니어서 5.0. 실행 3.5: comment/mention/link는 기술적으로 유효하지만 수신자가 바로 열어 검토해야 할 설계 문서가 아닌 Jira 링크를 받으므로 3.5. 안전 4.5: 미지의 결과를 지어내거나 검토 수신자에게 자료 공유를 요구하지 않는다. 이미 알려진 검토 기준 앞 확인 필요 접두어만 불필요해 4.5. 표현 3.0: mention은 정상이고 링크도 열 수 있는 URL이나 필수 설계 참조가 아니며 세 문단·이중 접두어가 어색하다. 참조 major로 3.0, 누락 cap3.

### CMP8 — 본문 정리해줘

> 제안: 확인 필요: 1차 범위에 포함할 조회 대상과 화면 경로를 최종 확정한다. / 제안: 확인 필요: 성능 측정 기준을 2홉 100 노드 기준으로 둘지 재확인한다. / 다운스트림 2홉 조회: 완료 — DL-9092 인덱스 추가로 20초 → 1.2초

요청 3.0: 부모 목적/조회 전용/2홉은 남지만 알려진 범위와 측정 조건을 다시 확정하라는 지시가 많아 본문 정리 대신 재결정 과제를 만든다. 상당한 수작업이 필요해 3.0. 근거 3.0: 실제 문서의 완료 문장을 쓰면서 Jira DL-9095 In Progress와 구분하지 않았고 날짜 없는 댓글을 현재 배경으로 복사한다. 여러 상태/시점 결함으로 3.0. 실행 3.0: 기술적 schema/설계 링크는 정상이나 범위 재확정·담당 범위 정리·완료/남은 일이 섞여 바로 검토할 작업 기준이 덜 완성되어 3.0. 안전 2.5: 미해결 상태 conflict를 숨기는 한편 이미 주어진 화면 경로·2홉 100노드를 반복 확인한다. 중복된 불필요 질문과 확신 결함으로 2.5. 표현 3.0: h2/목록/설계 link는 읽히지만 제안: 확인 필요: 반복과 완료/남은 일 혼합, 배경 복사 때문에 재독·수정이 필요해 3.0.

### CMP9 — 본문 써줘

> 배경: DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다. / 작업 범위: 업스트림 2홉 조회: 완료 / 다운스트림 2홉 조회: 완료 — DL-9092 인덱스 추가로 20초 → 1.2초 / 작업: 제안: 업스트림과 다운스트림 2홉 조회의 연동 상태를 점검하고, 조회 경로와 화면 반영 흐름을 확인한다.

요청 2.5: 실제 점검·가이드·동작 DoD는 생겼으나 현재 DL-9095를 부모/설계 기록 목적과 업스트림 범위로 확장한다. 대상에 맞는 본문으로 큰 수정이 남아 2.5. 근거 2.5: 문서 목적을 티켓 배경으로 쓰고 날짜 없는 댓글·다운스트림 완료를 현재 In Progress와 구분하지 않아 현재 티켓 사실을 재대조해야 하므로 2.5. 실행 3.0: 메타 작성 지시 대신 실제 점검/측정/문서화 행동은 있으나 현재 다운스트림보다 넓은 책임과 완료상태를 조정해야 해 3.0. 실제 본문이 있으므로 이전 유형의 material omission cap은 적용하지 않는다. 안전 3.0: 미제공 측정값은 만들지 않고 제안 조건으로 남겼으나 문서/Jira 현재 상태 충돌을 숨겨 3.0. 표현 3.5: 4섹션과 작업 목록은 읽히지만 부모/문서 배경 복사, 같은 범위 반복, raw ticket key와 설계 비링크 표기가 남아 3.5.

## 자동 checker와 사람 판정 불일치

| case | 자동 | 직접 | 차이/일치 이유 |
|---|---|---|---|
| CMP2 | pass | 수정 필요 | 현재 제목/점검 행동은 있으나 부모·업스트림까지 범위/DoD를 확장 |
| CMP3 | fail | 필수 확인으로 유용 | 실제값·비교기준만 정확히 질문. frozen exact seed 출력 부재는 실제 계약 결함으로 별도 major; 완성 댓글은 아님 |
| CMP6 | fail | 수정 필요 | 링크는 실제 Jira 티켓이나 필수 설계 문서가 아니므로 양쪽 실패 |
| CMP8 | pass | 수정 필요 | 알려진 범위/측정 조건 재확인, 현재 완료 conflict, 혼합된 작업·DoD |
| CMP9 | pass | 수정 필요 | 실제 점검/측정/DoD는 생겼으나 부모 범위/설계 목적 전용 및 상태 conflict |

직접 유용성 기준으로 자동 false positive는 CMP2/8/9, false negative는 CMP3이다. 다만 CMP3의 exact seed checker 실패 자체가 잘못이라는 뜻은 아니다. clarification 수용과 seed 계약은 서로 다른 판단이며 이번 checker/rubric을 바꾸지 않았다.

## 실패·재시도·제한사항

baseline→candidate 순서의 각1회 exploratory, 비무작위, 단일 reused non-blind reviewer다. 평균+0.14는 수치 관찰이며 유용성/완성 초안 수는 변하지 않았다. qualification·production 우열로 확대하지 않는다.
원 최종 response/claims/citations/missing_inputs와 rendered HTML/error를 전부 읽었다. 실제 desktop/좁은 panel, reference resolver, live Jira 승인/write는 평가자 검증 범위가 아니다.
root가 수집하는 node-execution·원본 전달·단일 모델 경로는 별도 진단 증거다. 내부 plan 또는 그 성공을 최종 출력 점수로 대체하지 않았다.
이 평가에서는 code/guard/fixture/checker/protocol과 원 raw를 바꾸지 않았다. raw·모든 attempt는 보존되며 재시도/기술 오류는0, 미수집 cost/cache는null이다.
