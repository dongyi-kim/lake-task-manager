# MAXIS Editor v15 — 최종 후보 직접 평가

**완성한 사용 가능 초안 1/5(CMP2)**와 **유용한 필수 사실 확인 1/5(CMP3)**를 구분한다. 기존 usable 정의로는 2/5이며 두 개의 완성 초안이 아니다. 평균 **3.54/5**. CMP6·CMP8·CMP9는 핵심 수정이 필요하다.

## 측정 식별자

| 필드 | 값 |
|---|---|
| protocolVersion | `3.0.0` |
| rubricVersion | `2.0.0` |
| batteryVersion | `4.0.0` |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| runKind | `exploratory` |
| runGroupId | `maxis-editor-20260915T061308Z-f15f0b17` |
| repetitions | `1` |
| candidateCommit | `b72d8201a0c6962c251852dcb4a249d8a99e8c19` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| promptVersion | `ltm-maxis-dify-2026.09.15-editor-scope-v15` |
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
| candidateKey | `4e441820946e33ed59cd9b6c40f2da1d2bd05821d54f69e88f033f4c8ef971d9` |
| comparabilityKey | `724c6d0a7748d9df97d665d6b44bd8b284faf691c48e20d731fb7c2d4f746b1a` |

## 비교 가능성 및 evidence 선택

selectionPolicy: `complete-run-no-substitution`. 새 최종 response/payload/claims/citations와 rendered HTML을 정확한 요청·source에 대조해 먼저 판정했다. 내부 plan이나 자동 pass를 점수로 변환하지 않고 다섯 actual output을 모두 유지했다.

- [Primary raw](../../../.cache/agent-evaluation/maxis-editor-20260915T061308Z-f15f0b17/candidate.json), SHA-256 `c2a188762d39bd3586eb4556a6d2f1b2f855c76751551c8b9f88c70fc0163f43`.
- [전체 175항목 근거 JSON](../../../.cache/agent-evaluation/maxis-editor-20260915T061308Z-f15f0b17/direct-review-candidate.json), SHA-256 `ef328a87476551f23a45dcd28bf01cdb44c29eb604686c6e1dfce35e3709b498`.
- [fresh v13 baseline](maxis-editor-20260915T053427Z-baseline.md)·[v14 중간 결과](maxis-editor-20260915T055601Z-candidate.md)와 benchmarkKey·5개 case SHA·세 source SHA·model/endpoint·evaluator/rubric/checker가 같다. candidate commit/prompt/key는 각각 다르다.
- run batteryVersion=4.0.0과 reused input component_battery_version=2.0.0은 그대로 구별했다. `comparableToSourceSuite=false`; 과거 run을 v15 성공으로 대체하거나 qualification으로 해석하지 않는다.

## 실행 조건

DEV 비활성 stateless component, fixed synthetic evidence, repeatIndex=1. candidateDirty=false, bindingUnchanged=true. evaluator는 원격 API·모델 재시도·write를 실행하지 않았다. 부모·자식/연결 티켓·설계 문서의 실제 내용을 기준으로 평가하고 case별 동일 source hash를 확인했다.

| source | content SHA-256 |
|---|---|
| fixture.ticket.DL-9090 | `c997feb13a4eb486d0b184cc8945006f71996eb319e3831d74d7c1554a7df3a6` |
| fixture.ticket.lineage-family | `1909d0da2de0deb1d85c9fc6b540442e0dd468cb0d2bfee56555bc8a96988762` |
| fixture.document.lineage | `0ea07eca1a3f99d49534f938c26ede95df84f4c77f9a089649e369e8bc421c30` |

## 배터리 범위

CMP2·CMP3·CMP6·CMP8·CMP9 전체 5/5, 누락 0. 최종 EditorDraft/pure HTML까지만 평가한다. live Jira 조회·full compose·브라우저 UI/reference resolver·write/approval은 제외한다. fixture·checker·review spec·protocol·guard를 수정하지 않았다.

## 정량 결과

| 지표 | 결과 |
|---|---:|
| final ok / blocked | 5 / 0 |
| automatic pass | 4/5 |
| 완성 usable draft / useful factual clarification | 1/5 / 1/5 |
| 기존 정의의 useful 합계 / 핵심 수정 필요 | 2/5 / 3/5 |
| 직접 평균 / 자동 green·직접 unusable | 3.54/5 / 2(CMP8,CMP9) |
| 공통 pass / minor / major / na | 72 / 35 / 22 / 31 |
| 특수 pass / minor / major / na | 6 / 5 / 4 / 0 |
| material_omission cap 코드 발생 | 2/5 |
| 수신 Dify calls / attempts / network failure / retry | 5 / 5 / 0 / 0 |
| prompt / completion / total tokens | 19,970 / 3,484 / 23,454 |
| metrics.durationSeconds / latency p50 / p95 | 19.6초 / 4.3초 / 5.4초 |

aggregation: 다섯 축 0.2 가중평균 후 cap, 전체 case-attempt 산술평균. percentileMethod: `nearest-rank-over-all-case-attempts`. 정량 source는 공통 `metrics`만 사용했다. cachedTokens/costUsd=null은 미관측이다. calls는 Dify 수신 응답 수이고 내부 모델 step 수가 아니다. cap 발생 40%는 material_omission으로, 전달된 환각·미승인 write 비율이 아니다.

| case | 요청 | 사실 | 계약 | 안전 | 표현 | raw mean | cap | case score | 실제 사용성 |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| CMP2 | 4 | 4.5 | 4 | 5 | 4 | 4.30 | 없음 | 4.30 | 완성 초안 |
| CMP3 | 4 | 5 | 4 | 4 | 3.5 | 4.10 | 없음 | 4.10 | 필수 확인; 게시불가 |
| CMP6 | 3 | 5 | 3 | 3.5 | 3 | 3.50 | material_omission:3 | 3.00 | 핵심 수정 필요 |
| CMP8 | 2.5 | 4.5 | 2.5 | 5 | 2.5 | 3.40 | 없음 | 3.40 | 핵심 수정 필요 |
| CMP9 | 3 | 2.5 | 3 | 3.5 | 2.5 | 2.90 | material_omission:3 | 2.90 | 핵심 수정 필요 |
| 축 평균 | 3.30 | 4.30 | 3.30 | 4.20 | 3.10 | — | — | 3.54 | 초안1 + 확인1 |

## 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`; evaluatorAgentFamily: `codex`.
evaluatorAgentModel: `current Codex agent; exact runtime model ID not exposed`; evaluator task: `/root/scope_quality_review`.
directRawOutputReview: `true`; ltmLlmUsedAsJudge: `false`; reviewerCount: `1`; blindedReview: `false`.

v13/v14와 동일한 전담 evaluator이고 구현 agent는 별도다. 이전 점수를 목표로 삼지 않고 실제 새 출력으로 판정했다. usable 정의는 완성에 필요한 구체 사실 확인도 포함하므로 CMP3는 useful로 인정하되 완성 댓글과 구별한다. CMP3를 그대로 게시할 수 있다는 주장은 하지 않는다.

각 축 1–5점/0.5 간격·가중치20%; common+special 결함 수로 ceiling, 이후 cap을 적용했다. `tools.agent_eval_protocol.score_human_case`/`aggregate_human_reviews`는 완결성·상한·산술만 검증했다. 공통160개·특수15개의 개별 근거는 링크한 ignored JSON에 있다.

| 축 | 공통 항목/case | 5개 case 판정 | pass | minor | major | na |
|---|---:|---:|---:|---:|---:|---:|
| request_fulfillment | 6 | 30 | 10 | 10 | 10 | 0 |
| factual_grounding | 6 | 30 | 19 | 5 | 2 | 4 |
| contract_actionability | 6 | 30 | 15 | 2 | 3 | 10 |
| safety_uncertainty | 8 | 40 | 23 | 5 | 2 | 10 |
| communication_rendering | 6 | 30 | 5 | 13 | 5 | 7 |

## 배터리·case 특수 검토요소

| case | 요소 | 판정 | 실제 근거 |
|---|---|---|---|
| CMP2 | editor_context_preservation | pass | 현재 DL-9095 다운스트림 책임·parent 관계·3홉 제외·2홉100노드를 유지했다. |
| CMP2 | editor_reference_fidelity | minor | 세 source_ids가 실제 근거와 맞으나 raw key와 문서 제목만 있으며 canonical link/badge를 보완할 수 있다. |
| CMP2 | cmp2_four_sections | pass | 요청된 네 섹션을 실제 다운스트림 범위·측정·기록으로 구성하고 임의 API나 성능 목표를 발명하지 않았다. |
| CMP3 | editor_context_preservation | pass | 첫 문단의 사용자 visible seed를 정확히 유지하고 성능 측정 맥락에서 후속 질문을 제공했다. |
| CMP3 | editor_reference_fidelity | pass | seed는 eval.editor.intent에 연결되며 문서 링크 선택 질문은 제공된 parent source의 문서 기록 맥락과 일치한다. 없는 resolved 참조를 만들어내지 않았다. |
| CMP3 | cmp3_seed_exactness | pass | exact seed와 확인 marker를 유지하고 p95 방향·원인을 발명하지 않았다. 질문 경제성은 공통 checklist에서 별도 감점했다. |
| CMP6 | editor_context_preservation | minor | 담당 mention·성능 검토·2홉100노드는 유지하지만 결과 공유 허가를 추가해 사용자 요청과 다른 확인을 섞었다. |
| CMP6 | editor_reference_fidelity | major | 담당자 복원은 맞으나 제공 설계 문서를 실제 링크로 전달하지 않았다. |
| CMP6 | cmp6_review_target | major | person mention과 2홉100노드를 갖췄으나 유일한 href는 Jira DL-9090으로 필수 설계 문서 참조가 없다. |
| CMP8 | editor_context_preservation | pass | 부모 목적·조회 전용·2홉·문서 기록과 3홉 제외를 유지한다. |
| CMP8 | editor_reference_fidelity | minor | 설계 문서 link는 정확하지만 DL-9092는 raw key로 표시하며 ticket resolver는 component에 없다. |
| CMP8 | cmp8_parent_child_boundary | minor | 최종 DoD의 자식 완료 작업 직접 반복은 없어졌다. 다만 작업에는 자식 그래프/조회/성능 세부를 계속 편집 지시로 나열해 부모·자식 책임 분리가 부분적이다. |
| CMP9 | editor_context_preservation | major | 현재 DL-9095의 제한된 책임 대신 부모 문서 목적·업/다운 전체 범위를 배경/범위로 사용했다. 후반 다운스트림 작업만은 관련 있다. |
| CMP9 | editor_reference_fidelity | minor | 실제 source들을 사용하지만 문서 href 없이 제목/raw key만 표시해 참조 접근성을 보완해야 한다. |
| CMP9 | cmp9_ticket_context | major | 효익 발명은 없지만 현재 Sub-Task 대신 부모 문서/형제 완료를 주요 본문으로 삼고 다운스트림 완료와 Jira 진행 상태를 구분하지 않았다. |

## 배터리별 실제 출력과 평가

### CMP2

입력: `DL-9095`, 이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘. final=ok, score=4.30.

> DL-9095는 DL-9090의 하위 작업으로 ... 다운스트림 조회 연동을 담당한다. / 작업 범위는 다운스트림 조회 연동에 한정한다. / DoD: 2홉 100 노드 기준 성능 측정이 수행된다. 결과와 이슈가 [설계] 리니지 뷰어 1차 문서에 기록된다.

- request_fulfillment 4: DL-9095의 실제 하위 관계와 다운스트림 책임, 네 섹션·범위 제외·측정·문서 기록을 제공한다. 배경 한 문장이 내용을 쓰라는 편집 지시로 남는 국소 결함이 있다.
- factual_grounding 4.5: parent 관계·다운스트림 책임·3홉 제외·2홉100노드는 근거와 같다. 확인된 사실과 새 작업 제안 모두에 '제안:'을 붙여 구분이 약간 흐리다.
- contract_actionability 4: 현재 티켓에 맞는 실행 가능한 작업/검증 초안이다. '조회 연동이 완료된다'는 DoD가 다소 순환적이고 실제 문서 href가 없어 국소 보완이 필요하다.
- safety_uncertainty 5: 임의 수치·날짜·담당·효익·완료 상태를 만들지 않았고 범위를 다운스트림으로 한정했다. 불필요한 사용자 질문이나 외부 write도 없다.
- communication_rendering 4: 네 heading과 범위/작업/DoD 목록이 명확하다. 제안 접두어·편집 지시 한 문장·raw key·문서 링크 부재는 작은 편집으로 해결할 수 있다.

### CMP3

입력: `DL-9090`, 이어서 완성해줘. final=ok, score=4.10.

> 오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다 / 제안: 확인 필요: 성능 측정 결과의 p95 값은 얼마인가? / 성능 측정 기준이 2홉 100 노드인지, 다른 비교 조건이 있는가? / 결과를 기록할 문서 링크를 그대로 반영할까?

- request_fulfillment 4: exact seed를 유지하고 실제 p95 값과 측정 조건을 묻는 구체적인 필수 확인을 제공한다. 완성 댓글은 아니며 문서 링크 반영 여부를 되묻는 부가 질문은 불필요하다.
- factual_grounding 5: 사용자 seed를 그대로 보존하고 p95 높음/낮음·수치·원인을 발명하지 않았다. 질문은 사실을 확정하지 않는 형태다.
- contract_actionability 4: 유효한 comment 초안과 p95 값/측정 조건 질문으로 후속 작업을 진행할 수 있다. 질문들이 content_html에 있고 missing_inputs=[]이므로 게시할 댓글과 확인 단계를 구분해 다뤄야 한다.
- safety_uncertainty 4: 측정값을 먼저 묻고 비교 방향을 임의 완성하지 않았다. 다만 기대 대비 방향을 명확히 묻지 않으며 문서 링크 선택을 불필요하게 재질문한다.
- communication_rendering 3.5: 짧은 문단별 질문은 이해할 수 있으나 미완성 seed와 반복 '제안: 확인 필요:'가 댓글 본문에 남는다. 필수 확인용으로 유용하며 그대로 게시하는 완성 댓글은 아니다.

### CMP6

입력: `DL-9090`, 담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘. final=ok, score=3.00.

> 제안: [담당자 mention] 성능 측정 결과 검토 부탁드립니다. 확인 필요: 2홉 100 노드 기준 측정 결과를 공유해도 될까요? / 링크: [Workbench] 데이터 리니지 뷰어 1차 오픈 → Jira DL-9090

- request_fulfillment 3: 담당 mention과 성능 검토 요청은 있으나 실제 설계 문서가 빠지고 이미 요청한 검토용 결과 공유 허가를 다시 묻는다.
- factual_grounding 5: 담당자 복원과 티켓 제목·2홉100노드는 제공 근거와 일치하며 수치·상태·원인을 새로 단정하지 않는다. 검토 링크 누락과 불필요한 질문을 사실 발명으로 보지는 않는다.
- contract_actionability 3: 유효한 댓글과 mention은 있지만 결과 문서 대신 Jira 링크를 보여 검토 위치가 빠졌다. 검토 요청 뒤에 공유 허가 질문을 섞어 발송 전에 실질 수정이 필요하다.
- safety_uncertainty 3.5: 임의 값/사람·write를 만들지 않았다. 그러나 작성하라고 맡긴 검토 요청을 '측정 결과를 공유해도 될까요?'라는 불필요한 허가 질문으로 멈추게 한다.
- communication_rendering 3: mention span과 Jira href는 정상이다. 필수 설계 문서가 없고 검토 요청·공유 허가 질문의 대상/순서가 혼합돼 핵심 수정이 필요하다.

### CMP8

입력: `DL-9090`, 본문 정리해줘. final=ok, score=3.40.

> 작업: 업스트림 2홉 조회를 완료 상태로 정리한다. 다운스트림 ...20초에서 1.2초로 개선된 내용으로 정리한다. / DoD: 결과·이슈는 [설계] 리니지 뷰어 1차 문서에 기록한다. 3홉 이상 확장은 1차 범위 밖으로 두고 2차에서 다룬다.

- request_fulfillment 2.5: 부모 목적·조회 전용·2홉 범위를 보존하지만 작업은 완료/남은 일을 '정리한다'는 편집 지시다. DoD는 문서 기록과 3홉 제외만 있어 실제 부모 수용 기준을 다시 써야 한다.
- factual_grounding 4.5: In Progress는 실제 부모 상태이고 성능20초→1.2초·2홉100노드·완료된 업스트림은 source에 있다. 확인된 현황을 모두 제안 문구로 감싸 사실·제안 경계가 흐릴 뿐 없는 상태/수치를 발명하지 않았다.
- contract_actionability 2.5: HTML/description 구조는 유효하나 작업/DoD를 부모 기능 검증 기준으로 쓸 수 없다. 배경에 원문 완료 기준은 남았지만 최종 DoD는 문서 기록/범위 제외여서 실질 재정리가 필요하다.
- safety_uncertainty 5: 존재하는 parent 상태·과거 성능·측정 기준만 사용하고 미확인 다운스트림 현재 완료를 확정하지 않는다. 추가 사용자 질문·범위 밖 write도 없다.
- communication_rendering 2.5: 정확한 설계 link와 heading은 있으나 단독 상태/완료 기준 p, 목적·2홉 반복, '정리한다' 안내가 겹쳐 사용자 본문으로 다듬어야 한다.

### CMP9

입력: `DL-9095`, 본문 써줘. final=ok, score=2.90.

> 배경: DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다. 다운스트림 2홉 조회: 완료 — ...20초 → 1.2초. / 작업 범위: [Workbench] 데이터 리니지 뷰어 1차 오픈 / In Progress. / 작업: 다운스트림 조회 연동의 남은 성능 측정을 진행한다.

- request_fulfillment 3: 후반의 다운스트림 성능/연동 작업은 관련 있으나 배경·작업 범위를 부모 DL-9090/설계 문서 전체에서 가져와 현재 DL-9095의 좁은 책임이 누락된다.
- factual_grounding 2.5: 수치와 문서 내용 자체는 source에 있지만 부모 목적/상태를 현재 Sub-Task 맥락으로 옮기고 문서의 다운스트림 완료를 Jira In Progress와 구분하지 않는다. 제안 두 개의 citation도 실제 내용 대신 짧은 사용자 지시에 연결된다.
- contract_actionability 3: 유효한 description과 다운스트림 연동/측정 DoD는 일부 유용하다. 부모·형제 범위를 현재 티켓 범위에 섞어 승인 전 대상 범위를 고쳐야 한다.
- safety_uncertainty 3.5: 새 숫자·효익·외부 write는 만들지 않았으나 문서의 완료 주장을 현재 Jira 상태와 구분하지 않아 확신이 근거 이상이다.
- communication_rendering 2.5: 네 섹션/목록은 읽을 수 있지만 부모 문서/상태 조각과 반복된 업/다운2홉이 현재 티켓 목적을 가리고 실제 설계 href도 빠졌다.

## 자동 checker와 사람 판정 불일치

| case | 자동 | 직접 | 이유 |
|---|---|---|---|
| CMP2 | pass | usable draft | 자동 pass와 직접 usable이 일치한다. 핵심은 현재 티켓에 맞으며 편집 지시 한 문장·접두어·문서 링크는 국소 보완이다. |
| CMP3 | pass | useful clarification | 자동 pass와 useful clarification 판정이 일치한다. 값 자체를 구체적으로 물어 다음 단계에 유용하다. publishable completed comment는 아니므로 완성 draft 1/5와 clarification 1/5를 별도로 보고한다. |
| CMP6 | fail | unusable | 자동 fail과 직접 unusable이 일치한다. 설계 문서 링크를 추가하고 불필요한 공유 허가 질문을 정리해야 한다. |
| CMP8 | pass | unusable | 자동 pass지만 작업은 현황 편집 지시이고 DoD는 문서 기록·범위 제외다. 자식 완료 작업의 DoD 반복은 완화됐지만 실제 부모 완료 기준을 다시 작성해야 한다. |
| CMP9 | pass | unusable | 자동 pass지만 배경·범위의 주체가 현재 DL-9095 대신 부모이고 다운스트림 완료를 상충 Jira 상태와 구분하지 않는다. 후반에 관련 작업이 있어도 핵심 수정이 필요하다. |

불일치는 CMP8·CMP9다. CMP6의 mention/HTML이 정상이어도 실제 설계 문서가 없고 불필요한 공유 허가 질문이 남아 자동/직접 모두 실패다. CMP3의 p95 값 질문은 막연한 작성 재위임과 달리 다음 단계에 유용하지만, 문서 링크 재질문과 비교 방향 확인은 다듬어야 한다.

## 실패·재시도·제한사항

진단용 [plan artifact](../../../.cache/agent-evaluation/maxis-editor-20260915T061308Z-f15f0b17/llm-candidates.json), SHA-256 `c37783e09a9c2486c88b909bb5749f4c35e0a03fd393cbfa5e07a892cdc2d8f0`; [prepared delivery](../../../.cache/agent-evaluation/maxis-editor-20260915T061308Z-f15f0b17/prepared-input-delivery.json), SHA-256 `8e31b9fab5d1cccea02b515b86a1e2b1191e6c909acf0ef391f941d1c8b34366`.

최종 평가 뒤 diagnostic의 contract/status/section 형태와 delivery 기록만 확인했다. 5/5 original pack·guard pack·prepared view가 로컬과 동일, source 불변, fact unit46개, 7,907–7,981 UTF-8 bytes다. 모든 final이 guard accepted라는 사실은 의미 품질 통과와 다르다. pre-assembly 전문을 점수로 사용하지 않았다.

원본/진단 hash·5개 identity·160+15 checklist·ceiling·산술·필수 보고서 field를 검증했다. v15에서 빈 결과는 없어졌지만 CMP8의 작업/DoD 편집 지시와 CMP9의 부모 범위/완료 단정은 남는다. 이전 run과 fixtures/checker/protocol/guards는 보존했다.

한 번의 exploratory synthetic 실행, 단일 비맹검 reviewer, 고정 source projection에 한정된다. 가격/캐시 토큰과 실제 UI·검색 coverage·승인/실행은 미검증이며 통계적 우열이나 production 활성화 자격을 주장하지 않는다. 완성 초안1과 유용한 확인1은 반드시 구분해서 해석한다.
