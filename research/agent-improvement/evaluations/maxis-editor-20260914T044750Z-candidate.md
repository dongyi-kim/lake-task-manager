# MAXIS Editor — v13 최종 candidate 직접 평가 (04:47 UTC)

## 측정 식별자

실제 최종 응답 5개, 조립 전 plan 5개, 정확한 요청·근거·준비 입력 전달을 직접 판독했다. **사용 가능한 댓글 1/5(CMP6), 직접 평가 평균 2.96/5**다. 자동 통과는 3/5지만 CMP2·CMP9는 대상/책임이 맞지 않아 사용 불가다. 부분 개선은 확인했으나 활성화·production qualification 근거는 아니다.

| 필드 | 기록값 |
|---|---|
| protocolVersion | `3.0.0` |
| rubricVersion | `2.0.0` |
| batteryVersion | `4.0.0` |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| runKind | `exploratory` |
| runGroupId | `maxis-editor-20260914T044750Z-f012127f` |
| repeatIndex | `1` |
| repetitions | `1` |
| candidateCommit | `7be1779086e8ee535f666728a201c74d6098410c` |
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
| runtimeProfile | `dev-inactive-stateless-component-v1` |
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` |
| candidateKey | `87b475c780676d9149263f680ffc03cc0d4b47f9483c0e0394c8d8414f3454c6` |
| comparabilityKey | `38639e767116d2b284d0bde086f86cd9b26973ff9e8c2c08577d51f80b8ee57f` |
| candidateOrderIndex | `2` |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state` |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |

## 비교 가능성 및 evidence 선택

selectionPolicy: `complete-run-no-substitution`. 전체 5건을 포함했고 이전 실패를 이번 성공으로 대체하지 않았다. [03:57 신규 baseline](maxis-editor-20260914T035709Z-baseline.md) 및 [v12 직접 평가](maxis-editor-20260914T042709Z-candidate.md)와 동일 benchmarkKey·case·원본 데이터·checker·특수 계약·harness·모델·endpoint를 사용한다. commit/prompt가 다르므로 candidateKey/comparabilityKey는 서로 다르다.

- [실제 candidate](../../../.cache/agent-evaluation/maxis-editor-20260914T044750Z-f012127f/candidate.json), SHA-256 `a32526ff5ee39ba429ae7552c3c4809fe4a9ec7ce21e4af126631e5aa6e2dcc0`
- [조립 전 plan](../../../.cache/agent-evaluation/maxis-editor-20260914T044750Z-f012127f/llm-candidates.json), SHA-256 `07dba9e8d1761a79589c06f504baa37f2646e73e4fc52538b305987d3980caa4`
- [준비 입력 전달](../../../.cache/agent-evaluation/maxis-editor-20260914T044750Z-f012127f/prepared-input-delivery.json), SHA-256 `53ce07d0a8408624ad6f487cb0f508a400019b39adf15a9a3febecb9957813ce`
- [직접 평가 JSON](../../../.cache/agent-evaluation/maxis-editor-20260914T044750Z-f012127f/direct-review-candidate.json), SHA-256 `8aa1e6b4bf73058eb0c8525f2173977f2359f3901f27d20c337fca821485916c`

원래 editor 전체 suite와는 `comparableToSourceSuite=false`다. run batteryVersion 4.0.0과 보존 input component_battery_version 2.0.0은 원문 그대로 별도 기록했다. checker/특수 기대값을 결과에 맞춰 변경하지 않았다.

## 실행 조건

DEV 비활성 stateless component, clean commit(candidateDirty=false), gpt-5.4-mini, 합성 고정 근거, 탐색 1회다. 5회 반복 qualification이 아니며 후보 순서가 counterbalanced된 통계 비교도 아니다.

5개 모두 Start/KnowledgeGuard pack 일치, preparedViewEqualsLocal, originalSourcesUnchanged, exactSyntheticRequestBound가 true다. prepared catalog는 각각 46개 fact unit이다. 원문 source hash는 전 case와 이전 비교 대상에서 동일하다. 결과의 의미 누락을 입력 전송 손실로 볼 증거는 없다.

평가자는 신규 원격 호출·재시도·게시·다른 LLM judge를 수행하지 않았다. frozen assembler의 로컬 순수 함수만 진단에 사용했다.

## 배터리 범위

CMP2·CMP3·CMP6·CMP8·CMP9 전체 5건, 누락 0건. `tools.agent_compose_eval.CASES`의 native checker와 `editor_renderer_contract_flaws`를 재사용한 EditorDraft 생성 및 순수 HTML/합성 mention projection이다. full compose, live retrieval, canonical reference resolver, 실제 TipTap 브라우저 UI, 공개/게시/write는 측정하지 않았다. “사용 가능”도 이 component 출력의 직접 판독 범위이며 실 브라우저 확인으로 확대하지 않는다.

## 정량 결과

| 지표 | 신규 baseline | v12 | v13 |
|---|---:|---:|---:|
| Guard accepted | 0/5 | 4/5 | 4/5 |
| automatic pass (native + renderer) | 0/5 | 1/5 | 3/5 |
| 직접 사용 가능 | 0/5 | 0/5 | 1/5 |
| 유효 최종 envelope | 5/5 | 5/5 | 5/5 |
| 빈 blocked HTML | 5/5 | 1/5 | 1/5 |
| 직접 평가 평균 | 2.00 | 2.30 | 2.96 |
| 자동 false positive | 0 | 1 | 2 |
| 수신 응답 / 전체 시도 | 5 / 5 | 5 / 5 | 5 / 5 |
| 전체 토큰 | 26,141 | 23,411 | 23,755 |
| metrics.durationSeconds | 18.9초 | 16.2초 | 17.4초 |
| case latency 합계 | 18.917초 | 16.169초 | 17.391초 |
| case latency p50 / p95 | 3.631 / 4.827초 | 3.594 / 4.203초 | 3.721 / 4.308초 |

v13은 prompt 19,985 / completion 3,770 토큰, case-attempt당 전체 4,751토큰·수신 응답 1건·평균 latency 3.4782초다. cachedTokens/costUsd=null은 미관측이지 0이 아니다. calls는 수신 Dify 응답 수이며 내부 모델 step 수를 뜻하지 않는다. 별도 retry 기록은 없고 전체 응답이 보존됐다.

baseline 대비 case latency 합계 -8.1%, 전체 토큰 -9.1%지만 v12 대비는 각각 +7.6%, +1.5%다. 단일 실행의 관측 차이이며 안정적인 속도/비용 우위로 해석하지 않는다.

aggregation: 각 축 0.2 가중평균 후 case failure cap 적용, 전체 5개 case 평균. percentileMethod: `nearest-rank-over-all-case-attempts`. 자동(native + renderer) 실패는 2/5, Guard 차단은 1/5, 직접 의미상 사용 불가는 4/5다. 주요 누락 cap 적용 4/5, 이번 실제 출력에서 fabricated_fact_or_entity·unsafe_or_unapproved_write·reply_payload_contradiction cap 적용 0/5다.

## 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`.
evaluatorAgentFamily: `codex`.
evaluatorAgentModel: `current Codex agent; exact model ID not exposed`.
directRawOutputReview: `true`.
ltmLlmUsedAsJudge: `false`.
reviewerCount: `1`.
blindedReview: `false`.

같은 Codex 작업 에이전트가 실제 원문을 읽고 판정했다. 구현 참여/비맹검 평가이며 별도 외부 심사자로 소개하지 않는다. 과거 점수와 자동 pass 비율을 이번 정성점수로 변환하지 않았다. `score_human_case`·`aggregate_human_reviews`는 completeness·ceiling·cap·산술 검증만 수행했다.

| case | 요청 | 근거 | 계약 | 안전 | 표현 | 최종 점수 | 사용 가능 |
|---|---:|---:|---:|---:|---:|---:|---|
| CMP2 | 2.0 | 2.5 | 2.5 | 3.0 | 2.5 | 2.50 | 아니오 |
| CMP3 | 1.0 | 3.0 | 2.0 | 3.0 | 1.0 | 2.00 | 아니오 |
| CMP6 | 5.0 | 5.0 | 5.0 | 5.0 | 4.0 | 4.80 | 예 — 작은 @ 중복 |
| CMP8 | 3.0 | 3.0 | 3.0 | 3.0 | 3.0 | 3.00 | 아니오 |
| CMP9 | 2.0 | 2.5 | 2.5 | 3.0 | 2.5 | 2.50 | 아니오 |
| 평균 | 2.6 | 3.2 | 3.0 | 3.4 | 2.6 | 2.96 | 1/5 |

CMP6는 cap 없이 raw 4.80이다. mention 앞 추가 @는 공통·특수 rendering 항목 각각 minor이므로 protocol의 합산 상한 4.0을 표현 축에 적용했다. 다른 네 case에는 material_omission cap 3.0을 적용했으며 raw 점수 자체가 그 이하라 추가 감점은 없다. CMP3는 내부 질문이 아니라 실제 빈 응답을 채점했다. 차단 응답의 근거 3점은 정확도 60%나 좋은 답변을 의미하지 않는다.

| 공통 checklist 축 | pass | minor | major | na |
|---|---:|---:|---:|---:|
| 요청 | 6 | 3 | 20 | 1 |
| 근거 | 4 | 7 | 8 | 11 |
| 계약 | 11 | 0 | 4 | 15 |
| 안전 | 14 | 4 | 8 | 14 |
| 표현 | 4 | 5 | 13 | 8 |

공통 160개와 특수 15개 모두 item별 근거/applicability를 JSON에 보존했다. 전체 suite가 하나이므로 위 축별 집계는 suite/overall 집계와 같다.

## 배터리·case 특수 검토요소

| 특수 항목 | pass | minor | major | na | 의미 |
|---|---:|---:|---:|---:|---|
| editor_context_preservation | 1 | 1 | 3 | 0 | CMP6 보존, CMP8 부분 보존, 나머지 주요 누락 |
| editor_reference_fidelity | 0 | 1 | 4 | 0 | CMP6 정확 reference + @ 경미 중복 |
| cmp2_four_sections | 0 | 0 | 1 | 0 | 형식만 맞고 자식 맥락 불일치 |
| cmp3_seed_exactness | 0 | 0 | 1 | 0 | 실제 seed/확인 질문 전달 안 됨 |
| cmp6_review_target | 1 | 0 | 0 | 0 | mention·2홉100노드·정확 문서 link |
| cmp8_parent_child_boundary | 0 | 0 | 1 | 0 | 자식 완료 작업을 부모 DoD로 반복 |
| cmp9_ticket_context | 0 | 1 | 0 | 0 | 효익 발명은 없으나 대상 맥락 부족 |

case 입력의 goal/elements, sourceCaseSha256, checker digest를 그대로 재사용했다. 특수 항목은 별도 여섯 번째 점수가 아니라 대응하는 기존 축 상한에 합산했다.

## 배터리별 실제 출력과 평가

### CMP2 — 깨끗한 형식이나 대상 본문은 아님

실제 내용은 “DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다”, “배경은 … 문서라는 점을 드러낸다”, “다운스트림 조회 연동을 정리해 본문에 반영한다”다. family source citation은 생겼지만 사용자 대상 DL-9095는 HTML에 없다. 그래프/업스트림 형제 작업 완료를 자식 DoD에 함께 넣는다.

4개 heading, 정상 list, 근거 수치는 장점이다. 그러나 부모 문서와 글 작성 방법을 자식 초안 대신 출력했으므로 automatic pass라도 실제 사용 불가다. 새 숫자를 발명했다고 과장하지 않고 잘못된 적용 범위·현재 상태 충돌 미해결을 지적했다.

### CMP3 — 질문을 생각했지만 반환하지 못함

실제 응답은 `status=blocked, payload={}, claims=[], citations=[], payload_claims=[], missing_inputs=[]`, warning은 `editor_assembler:invalid_plan`이다. 순수 projection은 `ok=false, needsInfo=false, error=[], html=""`다. seed도 확인 질문도 사용자에게 전달되지 않았다.

반면 조립 전 plan 끝에는 “확인 필요: 성능 측정 결과의 기준값과 비교 대상은 무엇인가?”라는 구체적인 질문이 있다. 이는 내부 진단상의 부분 진전이며 실제 응답 성공으로 채점하지 않았다.

frozen commit `7be1779086e8ee535f666728a201c74d6098410c`의 순수 assembler를 메모리에서 재현했다. normalized source SHA-256은 `8d5cb437c5cdc7b1a32c5f08b82d45cee101da4b4024c2d10fe6dcf880d74957`다.

- 첫 실패: `main` 624–626의 `len(sections) > 4`. plan은 background/scope/work/dod/comment **5개**를 출력했다.
- 독립적으로도 trusted mode는 comment여서 `_blocks` 496–499의 expected `["comment"]`와 충돌한다. 이 검사는 첫 실패 뒤라 실제 main에서는 도달하지 않았다.
- 선택된 모든 unit ID는 실제 catalog에 있다. unknown source/unit 문제로 오진하지 않는다.
- 실제 mode별 계약과 guard는 유지했으며 plan 수정이나 성공 결과 대체를 하지 않았다. 다른 4건은 동일 frozen assembler에서 조립 성공했다.

### CMP6 — 직접 쓸 수 있는 검토 요청

실제 댓글은 다음 요청과 정확한 문서 anchor다.

> 제안: @ [담당자 mention] 성능 측정 결과 검토 부탁드립니다. 2홉 100 노드 기준 측정 결과와 확인된 이슈를 함께 봐주세요.

이는 이해를 위한 축약이며 실제 HTML은 평가 JSON에 보존했다. mention의 `data-id`는 현재 티켓 담당자 `skcc.x1402`이고, anchor label은 `[설계] 리니지 뷰어 1차`, href는 입력 Confluence source URL과 정확히 같다.

이전의 “결과는 … 정리해 두었습니다” 완료 단정은 사라졌다. “확인된 이슈를 함께 봐주세요”는 검토 대상을 요청하는 표현이지 구체적 미확인 이슈·결과를 새로 사실로 나열한 것이 아니다. 사용자 요청, 담당자, 측정 기준, 문서가 모두 갖춰져 이 component에서는 사용 가능하다.

실제 projection의 `@<span …>@skcc.x1402</span>`처럼 앞의 @가 겹치는 경미한 흠은 남는다. identity·link 접근을 바꾸지 않는 minor이며, full UI/browser 완료를 주장하지 않는다.

### CMP8 — 부모 설명 일부 성공, DoD 책임 구분 실패

부모 목적 “테이블 간 리니지를 화면에서 탐색”, 조회 전용, 업스트림/다운스트림 2홉 범위는 실제 본문에 있다. raw wiki 문법이 제거돼 조립·Guard·renderer도 수용한다.

하지만 DoD는 “그래프 렌더 컴포넌트는 … 완료 상태로 정리한다”, “업스트림 … 완료 상태로 정리한다”, “다운스트림 … 완료 상태로 정리한다”로 자식의 이미 완료된 실행을 반복한다. 부모 결과/수용 기준과 자식 책임을 분리하지 못했고, 문서상 downstream 완료와 현재 DL-9095 In Progress 충돌도 남는다. 실제 transition을 수행했다는 뜻은 아니지만 그대로 부모 본문에 쓰기에는 주요 수정이 필요하다.

### CMP9 — 렌더링 수정이 의미 정확도를 보장하지 않음

실제 본문은 부모 DL-9090 문서의 진행 결과와 “그래프 렌더 컴포넌트와 업스트림 2홉 조회 완료 내용을 반영한다”, “남은 일로 성능 측정과 사용 가이드 작성을 정리한다”다. 사용자 target DL-9095는 없다.

v12의 raw `h2.`/별표·Task/In Progress 필드 덤프가 사라지고 새 UX 효익도 발명하지 않았다. 그러나 부모 이력·글 정리 지침 중심의 본문이라 의미상 사용 불가다. 자동 통과를 실제 자식 본문 완성으로 인정하지 않았다.

## 자동 checker와 사람 판정 불일치

CMP2·CMP9 두 건은 automatic=true, 직접 사용 가능=false인 semantic false positive다. 원래 native checker는 `semanticCorrectnessAssessed=false`이므로 heading/기본 금지어/렌더 조건의 통과만으로 대상·책임 정확성을 보장하지 않는다.

CMP6는 자동과 직접 판독 모두 통과한다. CMP3·CMP8은 자동과 직접 판독 모두 실패한다. 이번 평가에서 checker·fixture·guard 수정은 없고, source battery를 사후 조정하지 않았다.

## 실패·재시도·제한사항

원본 세 JSON의 SHA, 5개 request ID, source/checker/spec, 공통 160개·특수 15개 완결성, 점수 cap·축 평균·집계를 검증했다. baseline/v12/raw 원문은 보존했다. 원격 호출이나 재시도를 추가하지 않았다.

baseline 0/5 → v12 0/5 → v13 1/5의 사용 가능 판정은 검토 요청 댓글의 **좁은 개선**이다. 전체 업무 본문/seed 이어쓰기 품질은 아직 미달이며 2.96 평균이나 코드 테스트/코드 리뷰 점수를 production LLM 품질 합격으로 해석하지 않는다. 한 번의 탐색·단일 구현 참여 평가자·비맹검·고정 합성 component라는 한계 때문에 활성화하지 않는다.

다음 근본 과제는 모드별 생성 구조를 더 명확히 분리하고, 현재 대상·부모/자식 책임·시점을 입력과 내용 선택에 결속하는 것이다. 초안 대신 초안 작성법을 쓰는 문제와 부모 DoD에 자식 상태를 복제하는 문제는 HTML 정규화만으로 해결되지 않는다. 확인 질문이 안전하게 사용자에게 도달하는 경로와 source-bound link/mention의 작은 표시 중복도 후속 검증 대상이다. guard를 낮추거나 실패 원문을 성공으로 대체하는 개선은 제안하지 않는다.
