# Maxis Editor v17 candidate 직접 품질 평가 — 2026-09-16T00:32:27Z

## 측정 식별자

| 필드 | 값 |
|---|---|
| protocolVersion / rubricVersion | 3.0.0 / 2.0.0 |
| batteryVersion / runKind | 4.0.0 / exploratory |
| batteryManifestSha256 | 6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406 |
| specializedReviewSpecSha256 | 877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b |
| runGroupId | maxis-editor-20260916T003227Z-d84e52ed |
| candidateCommit | dddfc92667bf056a842c9946a8069e0b4c21c03f |
| candidateTreeSha256 | ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6 |
| runtimePackageSetSha256 | 9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645 |
| evaluationHarnessTreeSha256 | c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0 |
| promptVersion | ltm-maxis-dify-2026.09.16-editor-draftspec-v17 |
| candidateDirty / bindingUnchanged | false / true |
| published Editor workflow | 99b5abfd-eaf0-4c22-894d-f2b82148b023 |

## 비교 가능성 및 evidence 선택

| 필드 | 값 |
|---|---|
| benchmarkKey | f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74 |
| candidateKey | c61ec05aad535d391f80d77c80b16338d5f473619de47fc9fd4633c1efd4c575 |
| comparabilityKey | a2a383ee240677e6dbcd3dfa77ff9dfabb99b19036b8db15cb2123cef901725c |
| selectionPolicy | complete-run-no-substitution |
| aggregation | 5축 동일가중 평균 후 cap; 모든 case-attempt의 산술평균 |
| primary raw SHA256 | f08df8c09d1b5d258b410a6220bc5250782d6d9b9f63e961f88621bfdaf9d2a1 |
| direct review SHA256 | bafb26baf546cd0613793eca66448ae765e78af28944f44368d44d28413468dd |

[원본 raw](../../../.cache/agent-evaluation/maxis-editor-20260916T003227Z-d84e52ed/candidate.json)와 [175개 verdict/evidence·축 상한·cap·산술 JSON](../../../.cache/agent-evaluation/maxis-editor-20260916T003227Z-d84e52ed/direct-review-candidate.json)을 연결한다. blocked·기술 실패를 포함한 원 run 전체를 평가했다.
[새 baseline](maxis-editor-20260915T235858Z-baseline.md)과 benchmarkKey·model·5case/input/checker/spec/source hash가 같다. 평균3.58→2.34(-1.24), 유용1→0, 완성초안0→0, 필요한 확인1→0이다. 이전 점수를 목표화하거나 후속 출력으로 대체하지 않았다.
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
| repeatIndex / recordedAtUtc | 1 / 2026-09-16T00:32:27Z |

평가자는 네트워크·게시·credential 조회·LTM endpoint judge를 사용하지 않았다. clean commit에서 수집된 최종 응답과 fixture 전문을 판독했으며 구현 코드 리뷰 점수는 직접 품질 점수와 별개다.

## 배터리 범위

| suite | 전체 | 실행 | 누락 |
|---|---|---|---|
| maxis-editor-component | 5 | 5 | 0 |

실행: CMP2/CMP9 DL-9095 description, CMP3 DL-9090 seed comment, CMP6 DL-9090 담당 mention comment, CMP8 DL-9090 description. run battery4.0.0과 input component2.0.0은 별개 식별자다.
fixture 전문을 다시 읽었다. 부모 DL-9090 In Progress, DL-9093/9094 Closed, DL-9095 In Progress이고 부모 담당자는 skcc.x1402이며 문서에는 다운스트림 완료·20초→1.2초 및 남은 2홉100노드 측정/가이드가 있다. 실패 응답을 내부 계획의 내용으로 보충하지 않는다.

| source_id | content SHA256 |
|---|---|
| fixture.ticket.DL-9090 | c997feb13a4eb486d0b184cc8945006f71996eb319e3831d74d7c1554a7df3a6 |
| fixture.ticket.lineage-family | 1909d0da2de0deb1d85c9fc6b540442e0dd468cb0d2bfee56555bc8a96988762 |
| fixture.document.lineage | 0ea07eca1a3f99d49534f938c26ede95df84f4c77f9a089649e369e8bc421c30 |

## 정량 결과

| 지표 | 결과 |
|---|---|
| attempts / calls / retries | 5 / 4 / 0 |
| durationSeconds / attempt | 26.6 / 5.32초 |
| percentileMethod / p50 / p95 | nearest-rank-over-all-case-attempts / 5.6초 / 8.0초 |
| promptTokens / completionTokens / totalTokens | null / null / null; tokenUsageComplete=false |
| 관측된 4건 partial token만 | 13104 / 3919 / 17023; CMP3 누락, 전체 합계 아님 |
| cachedTokens / costUsd | null / null — 미수집이며 0이 아님 |
| 자동 통과 / 자동 실패 | 1/5 / 4/5 (80%; CMP2, CMP3, CMP6, CMP8) |
| structural contract false / 미평가 | 0 / 1(CMP3; contractOk 없음), 수신4건 true |
| blocked / needs_input / 기술 오류 | 2(CMP2/8) / 0 / 1(CMP3 bad_request) |
| 직접 품질 / 유용성 | 2.34/5 / 0/5 = 완성 초안0 + 필수 확인0 |
| cap code 발생 / 실제 점수 하락 | 4/5(material_omission) / CMP6 원평균3.20→3.00 |

시간은 전체 cases[*].metrics.durationSeconds의 nearest-rank다. calls는 받은 Dify response 수이며 내부 모델 step 수가 아니다. 누락된 CMP3 사용량을 0으로 채우거나 partial token을 baseline 전체와 비교하지 않는다.

## 사람 품질 평가 기준

| 필드 | 값 |
|---|---|
| qualitativeEvaluatorPolicy | codex-or-claude-direct-raw-output-review |
| evaluatorAgentFamily | codex |
| evaluatorAgentModel | current Codex agent; exact runtime model ID not exposed |
| directRawOutputReview / ltmLlmUsedAsJudge | true / false |
| reviewerCount / blindedReview | 1 / false |
| panel / 구현 분리 | baseline과 동일 reused reviewer; 구현자는 별도 agent |

rubricVersion2.0.0: 요청·근거·실행·안전·표현 각20%, 1–5의0.5단위. 공통+특수 합산 상한은 전pass5/minor1=4.5/minor2+=4/major1=3.5/major2+=3이며 anchor에 따라 더 낮게 채점한다.
평균 후 cap: fabricated_fact_or_entity·unsafe_or_unapproved_write=2, reply_payload_contradiction·material_omission=3. CMP2/3/8 전체 출력 누락과 CMP6 담당 mention 누락에 cap3; 앞 세 건은 이미 원평균이3 미만이다.
빈 출력은 노출된 사실 발명으로 보지 않지만 grounded 답변이 없어 근거 축3.0으로 제한한다. 기존 패널의 empty-response 해석만 확인했고 새 응답별 evidence를 직접 작성했다. valid blocked envelope와 final envelope조차 없는 기술 실패도 구분한다.
단일 non-blind/reused reviewer이며 이전 출력/코드 critic 노출이 있다. 실제 유용성은 바로 쓸 요청 초안 또는 구체적 필수 사실 확인만 인정한다. 이번 CMP3에는 확인 질문이 없고 @담당자는 실제 mention이 아니다.

| 공통 checklist 축 | pass | minor | major | na | 축평균 |
|---|---|---|---|---|---|
| 요청 | 2 | 2 | 20 | 6 | 1.70 |
| 근거 | 5 | 1 | 4 | 20 | 3.20 |
| 실행 | 12 | 0 | 6 | 12 | 2.20 |
| 안전 | 9 | 3 | 6 | 22 | 2.90 |
| 표현 | 3 | 5 | 7 | 15 | 1.90 |
| 공통 합계 | 31 | 11 | 43 | 75 | 160개 |
| 특수 합계 | 0 | 1 | 14 | 0 | 15개 |

개수는 독립 정확도 표본/성공률이 아니다. 모든 pass/na의 이유, case별 combined ceiling, rationale·cap·산술은 linked ignored JSON에 보존하며 도구는 의미 채점 없이 완전성/산술만 검증했다.

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
| CMP2 | editor_context_preservation | major | DL-9095 다운스트림 본문을 배경·범위·작업·완료 조건으로 재작성의 의미가 html=""에 전혀 보존되지 않았다. |
| CMP2 | editor_reference_fidelity | major | CMP2의 source에 근거한 본문/참조 결과가 아예 없어 핵심 문맥을 연결한 최종 산출물이 없다. 빈 출력의 비발명을 참조 성공으로 보지 않는다. |
| CMP2 | cmp2_four_sections | major | 네 필수 section 모두 html=""에 없으며 새 API/성능 목표가 없는 것을 완성 성공으로 보지 않는다. |
| CMP3 | editor_context_preservation | major | 원본 오늘 리니지 뷰어 p95 seed의 의미/문장이 최종 응답에 전혀 없다. |
| CMP3 | editor_reference_fidelity | major | seed를 포함하는 최종 content/reference 결과 자체를 받지 못해 source 문맥과의 전달 일치를 충족하지 못했다. |
| CMP3 | cmp3_seed_exactness | major | exact visible seed와 확인 marker 모두 없는 기술 실패다. 비교 방향을 발명하지 않은 빈 출력을 성공으로 채점하지 않는다. |
| CMP6 | editor_context_preservation | major | 검토 요청·기준·문서는 남았으나 사용자 핵심 조건인 실제 담당자 mention을 일반 @담당자로 대체했다. |
| CMP6 | editor_reference_fidelity | major | 확인된 assignee skcc.x1402가 아닌 raw @담당자 텍스트이고 actual mention으로 resolve/render되지 않았다. 설계 링크만 정확하다. |
| CMP6 | cmp6_review_target | major | 2홉100노드와 설계 문서 href는 있지만 required person mention이 없다. rendererFlaws도 final renderer raw mention token을 기록한다. |
| CMP8 | editor_context_preservation | major | DL-9090 부모의 목적·범위 중심 본문 정리의 의미가 html=""에 전혀 보존되지 않았다. |
| CMP8 | editor_reference_fidelity | major | CMP8의 source에 근거한 본문/참조 결과가 아예 없어 핵심 문맥을 연결한 최종 산출물이 없다. 빈 출력의 비발명을 참조 성공으로 보지 않는다. |
| CMP8 | cmp8_parent_child_boundary | major | 부모 why/scope와 자식 책임 분리를 보여 줄 본문 자체가 없다. 자식 반복이 없는 빈 문자열을 계약 성공으로 채점하지 않는다. |
| CMP9 | editor_context_preservation | major | 현재 다운스트림 이름은 있지만 부모/설계 문서 목적·업스트림 책임을 가져오고 알려진 화면/홉 범위를 재질문해 현재 작업 경계와 확정 맥락을 보존하지 못했다. |
| CMP9 | editor_reference_fidelity | minor | 설계 link는 canonical이며 정확하다. 다만 부모 key/현재 티켓 제목은 평문이고 문서 제목을 link와 문장에서 중복한다. |
| CMP9 | cmp9_ticket_context | major | 완료 상태 충돌과 미확인 UX 효익은 없지만 설계 문서의 목적을 현재 티켓 배경으로 옮기고 부모 업스트림 책임까지 포함해 현재 ticket의 기능/검증 경계가 잘못된다. |

## 배터리별 실제 출력과 평가

| case | 요청 | 근거 | 실행 | 안전 | 표현 | 원평균 | cap | 최종 | 유용성 |
|---|---|---|---|---|---|---|---|---|---|
| CMP2 | 1.0 | 3.0 | 2.0 | 3.0 | 1.0 | 2.00 | 3 | 2.00 | blocked |
| CMP3 | 1.0 | 3.0 | 1.0 | 2.5 | 1.0 | 1.70 | 3 | 1.70 | 기술 실패 |
| CMP6 | 3.0 | 3.5 | 3.0 | 3.5 | 3.0 | 3.20 | 3 | 3.00 | 수정 필요 |
| CMP8 | 1.0 | 3.0 | 2.0 | 3.0 | 1.0 | 2.00 | 3 | 2.00 | blocked |
| CMP9 | 2.5 | 3.5 | 3.0 | 2.5 | 3.5 | 3.00 | 없음 | 3.00 | 수정 필요 |

### CMP2 — 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘

> status=blocked; payload={}; claims=[]; citations=[]; missing_inputs=[]; warnings=[]; rendered={ok:false, needsInfo:false, error:[], html:""}

요청 1.0: DL-9095 다운스트림 본문을 배경·범위·작업·완료 조건으로 재작성 요청에 status=blocked, payload={}, html=""만 남아 산출물·확인 질문·복구 방법이 전혀 없어 요청 축 1.0. 근거 3.0: 최종 claims/citations가 비어 가짜 entity나 사실이 노출된 것은 아니다. 그러나 source 기반 설명도 미확인점도 전달하지 않은 빈 결과를 근거성 만점으로 보지 않아 3.0; 내부 거절 계획은 채점하지 않았다. 실행 2.0: 유효한 blocked envelope와 ok=false projection은 서로 맞지만 삽입 가능한 draft나 구체적인 사용자 복구 방법이 없어 실행 축 2.0. 안전 3.0: 성공/완료를 주장하거나 외부 write하지는 않았다. 다만 error/warnings/missing_inputs가 모두 비어 실패 영향과 재개 방법을 설명하지 않으므로 3.0. 표현 1.0: rendered.html=""이고 error=[]여서 읽을 결과·오류 설명·구조가 전혀 없다. 사용자 대면 출력으로 1.0.

### CMP3 — 이어서 완성해줘

> errorCode=bad_request; response={}; rendered={}; contractOk absent; answerSha256 absent; metrics={attempts:1,calls:0,tokenUsageComplete:false}

요청 1.0: response/rendered={}이며 bad_request 기술 실패로 exact seed, 이어쓴 문장, 실제값/비교 기준 질문 모두 전달되지 않았다. 요청 축 1.0. 근거 3.0: 노출된 사실이나 p95 발명은 없지만 seed 사실·미확인 비교 방향을 사용자에게 돌려준 grounded 응답도 없다. 빈 결과 정책에 따라 3.0이며 내부 계획은 보지 않았다. 실행 1.0: received calls=0, contractOk 부재, response={}라 유효한 최종 역할 응답도 삽입/답변할 결과도 없다. blocked envelope조차 없는 실행 실패로 1.0. 안전 2.5: 승인 없는 변경이나 성능값 발명은 관측되지 않았다. 그러나 필요한 실측/비교 질문도 사용자용 실패·복구 안내도 전혀 없고 raw errorCode만 있어 2.5. 표현 1.0: rendered={}로 화면용 본문과 설명이 전혀 없어 가독성/표현 축 1.0.

### CMP6 — 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘

> 제안: @담당자 성능 측정 결과를 검토해 주세요. 2홉 100 노드 기준의 측정 결과와 확인이 필요한 이슈가 있으면 함께 알려 주세요. / [설계] 리니지 뷰어 1차 (canonical Confluence link)

요청 3.0: 검토 요청·2홉100노드 기준·설계 링크는 있지만 핵심 요구인 실제 담당 mention이 평문 @담당자로 남았다. 수신자를 지정하는 필수 수정이 필요해 3.0. 근거 3.5: 설계 제목/URL과 2홉100노드는 실제 source다. 그러나 알려진 assignee skcc.x1402를 resolve하지 않고 generic 담당자로 표시해 entity grounding이 불완전하므로 3.5. 가짜 개인을 만들어낸 것은 아니다. 실행 3.0: 유효한 comment와 canonical 문서 링크는 있지만 실제 식별자/mention 없는 수신자 placeholder를 사람이 채워야 한다. 즉시 보낼 comment는 아니므로 3.0. 안전 3.5: 실측값을 지어내지 않고 검토/이슈 회신을 요청한다. 다만 source에 이미 있는 담당자를 실제 식별하지 않은 불명확한 수신자로 남겨 안전한 초안 확정을 위해 수정이 필요해 3.5. 표현 3.0: 설계 link와 짧은 문단은 정상이나 @담당자는 mention badge가 아니고 raw text로 남는다. 필수 사람 참조 major로 3.0이며 누락 cap3을 적용한다.

### CMP8 — 본문 정리해줘

> status=blocked; payload={}; claims=[]; citations=[]; missing_inputs=[]; warnings=[]; rendered={ok:false, needsInfo:false, error:[], html:""}

요청 1.0: DL-9090 부모의 목적·범위 중심 본문 정리 요청에 status=blocked, payload={}, html=""만 남아 산출물·확인 질문·복구 방법이 전혀 없어 요청 축 1.0. 근거 3.0: 최종 claims/citations가 비어 가짜 entity나 사실이 노출된 것은 아니다. 그러나 source 기반 설명도 미확인점도 전달하지 않은 빈 결과를 근거성 만점으로 보지 않아 3.0; 내부 거절 계획은 채점하지 않았다. 실행 2.0: 유효한 blocked envelope와 ok=false projection은 서로 맞지만 삽입 가능한 draft나 구체적인 사용자 복구 방법이 없어 실행 축 2.0. 안전 3.0: 성공/완료를 주장하거나 외부 write하지는 않았다. 다만 error/warnings/missing_inputs가 모두 비어 실패 영향과 재개 방법을 설명하지 않으므로 3.0. 표현 1.0: rendered.html=""이고 error=[]여서 읽을 결과·오류 설명·구조가 전혀 없다. 사용자 대면 출력으로 1.0.

### CMP9 — 본문 써줘

> 배경: DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다. / 제안: 확인 필요: 다운스트림 조회의 범위가 테이블 상세 화면 기준인지? / 제안: 확인 필요: 2홉 조회만 포함하고 3홉 이상은 제외하는지? / DoD: 제안: 테이블 상세에서 업스트림/다운스트림 2홉 조회가 동작한다.

요청 2.5: 현재 다운스트림 제목과 네 section은 있지만 부모 설계 문서 목적을 배경으로 가져오고 업스트림까지 책임을 확장한다. 알려진 화면/2홉 제외 범위도 다시 질문하므로 본문을 상당히 고쳐야 해 2.5. 근거 3.5: 현재 제목·설계 링크·조회 전용·3홉 제외는 실제 source이며 완료 상태 충돌 문장은 출력하지 않았다. 하지만 문서의 기록 목적을 현재 티켓 배경으로 사용하는 source 역할 오인이 남아 case 특수 맥락 major와 함께 근거 축3.5. 실행 3.0: schema와 실제 문서 링크는 정상이며 구현 경로 점검/결과 기록이라는 행동도 있다. 그러나 기지 범위 재확인과 부모 수준 업스트림 책임을 현재 자식에 넣어 바로 확정하기 어려워 3.0. 안전 2.5: 본문 앞에서 3홉 제외를 사실로 쓴 뒤 다시 2홉만/3홉 제외인지 묻고 테이블 상세 경로도 재질문한다. 미확정 값 발명은 없지만 알려진 조건을 반복 보류시켜 2.5. 표현 3.5: heading/목록/canonical link는 읽히지만 제안: 확인 필요: 반복, 문서 제목 중복, DoD 목록 사이 독립 링크와 부모 문서 배경이 재독을 요구해 3.5.

## 자동 checker와 사람 판정 불일치

| case | 자동 | 직접 | 차이/일치 이유 |
|---|---|---|---|
| CMP2 | fail | 사용 불가 | blocked/empty; 원인·복구 안내도 없음 |
| CMP3 | fail | 사용 불가 | bad_request; final response/rendered 없음, 필요한 확인 질문도 없음 |
| CMP6 | fail | 수정 필요 | canonical 설계 링크는 있으나 실제 mention 대신 raw @담당자 |
| CMP8 | fail | 사용 불가 | blocked/empty; 부모 why/scope 초안이 없음 |
| CMP9 | pass | 수정 필요 | 기지 화면/홉 재질문, 부모 문서 목적·업스트림 책임 전용 |

직접 유용성 기준의 자동 false positive는 CMP9, false negative는 없다. structural contract true인 blocked envelope를 초안 성공으로 취급하지 않았다. CMP3 계약은 false가 아니라 미평가이며 기술 실패와 분리 기록했다.

## 실패·재시도·제한사항

baseline→candidate 각각1회 exploratory, 비무작위, 단일 reused non-blind reviewer다. 평균 -1.24와 유용1→0은 이 두 원 run의 관찰이며 qualification/production 일반화 근거가 아니다.
5개 원 attempt와 최종 response/payload/claims/citations 또는 부재를 모두 읽었다. live Jira/write/approval·실제 desktop UI·reference resolver는 직접 검증 범위가 아니다.
내부 plan/graph 진단·코드 리뷰 점수·후속 후보 성공을 v17 원 실패의 내용이나 점수로 대체하지 않았다. bad_request는 실행 artifact의 오류이지 사용자에게 전달된 복구 안내가 아니다.
원 raw는 불변 보존했고 evaluator는 코드/guard/fixture/checker/protocol·remote state를 수정하지 않았다. 재시도0; 누락된 token/cost/cache를0으로 보정하지 않았다.
