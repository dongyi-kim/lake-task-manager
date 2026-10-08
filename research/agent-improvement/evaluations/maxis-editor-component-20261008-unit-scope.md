# MAXIS Editor component — unit-scope 독립 관측 (2026-10-08)

두 실행의 `benchmarkKey`가 다르므로 공식 paired 비교가 아니다. 아래 수치는 별도 관측값이며 점수 증감, 속도·토큰 절감률 또는 후보 우월성을 계산하지 않는다. Candidate의 직접 사용 가능한 초안은 **1/5(CMP8)**, baseline은 **0/5**다. Candidate도 품질 gate를 통과하지 못했다. CMP8의 사용 가능 판정은 핵심 정보 추가·재작성 없이 component 초안으로 검토 가능하다는 뜻이며, 무수정 게시나 실제 UI 통과를 뜻하지 않는다.

## 측정 식별자

공통: `protocolVersion=4.0.0`, `rubricVersion=2.0.0`, suite `maxis-editor-component`, `batteryVersion=4.0.0`. Raw case의 별도 `component_battery_version=2.0.0`도 두 실행에서 동일하다.

| 식별자 | Baseline (B) | Candidate (C) |
| --- | --- | --- |
| candidateCommit | `dc981f58389915044293444835e08eea1a9ead1a` | `8739a8c59892a6d4080a5fcc3ef4b5409cbd4bd0` |
| promptVersion | `ltm-maxis-dify-2026.09.21-policy-query-v20` | `ltm-maxis-dify-2026.10.08-editor-scope-v21` |
| runGroupId | `maxis-editor-20261008T023419Z-32344cfd` | `maxis-editor-20261008T025454Z-1d9de92a` |
| candidateTreeSha256 | `4d047bdcba7178080cf18b78785db0318a99e5405b2b7e3f557a59dcd16b894a` | `64989c0c440de49aa71c7edf6684bb33e2751218474903d5f391b1dd63556972` |
| batteryManifestSha256 | `5f8815ec3ffc5534e3a9c071c02ce490995ece0d333ad9a9a87d14476521208d` | `5d9791ee39b3ab2a74fee15b69a38ceb5dfe5a1f1cc571b32422c568549e9241` |
| benchmarkKey | `f1c54a3e4e9ee2e6c473c980b1ed36ca26ccf2c2ad166959f9eaf76f894b6173` | `fddff41446ab984b0f0acac8ea3b333d09eb7def5b765e9be0ef40f3261b4b0c` |
| candidateKey | `e376f022bb1659446a6ddb798ea89ac84bf7bd87d4975d539742295a08be82c1` | `2ac526b984ea3163e2ac33bc05bdd5b9c4e04a20d0339733525328564b4d9a89` |
| comparabilityKey | `0300ec776084de92a76f5addee387f373b942d8b3a3e30876e15691c922eca37` | `eabe93f8958f884971618a62bfe0f8830da7c3e01386d6858e4d7e814124e47f` |
| publishedWorkflowId | `0b618d12-4247-45c3-8d5b-1790804621d4` | `a55929e9-dd6c-4660-8365-df1ff58327bb` |
| primary raw SHA-256 | `60231e5746f97ae0d46a8e1da164bf99ae2b9e15e263bbf50c5d3db71b24ce03` | `922c38e1b6906c7ee22d8a3b4ea189fab61f4de020f32cc2f5900cedc5547d81` |

공통 hash:

| 필드 | 값 |
| --- | --- |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `6042ddf7e707233f92f9794d72e39b3a68bcd675f226e50bf8c0935d64755727` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |

## 비교 가능성 및 evidence 선택

`selectionPolicy=complete-run-no-substitution`, `attemptPolicy=retain-every-attempt`. 각 raw의 5개 primary case를 모두 평가했고 성공한 closure로 실패를 교체하지 않았다. 두 실행은 `complete=true`, `bindingUnchanged=true`; `qualificationEligible=false`, `releaseEligible=false`다.

불일치의 직접 원인은 `app/agent/evaluation_protocol.json` 파일 bytes다. Baseline은 24,364 bytes(360 CRLF + 1 LF, hash `627930f7f306531fcd2295769aecdcc652bd9736d7630498ecfc0f0a7d836bf0`), candidate는 24,365 bytes(361 CRLF, hash `4da9b1c281110247ec88192f40f9d2196d6ce6d342428f5123069eef4c44a591`). Parsed JSON은 같지만 이 차이가 source provenance, case hash, battery manifest와 benchmark를 바꿨다.

다섯 case의 prompt·seed·ticket·source 내용·schema·expected/checker는 동일하다. Input object의 차이는 위 protocol source hash와 이에 종속된 case hash뿐이다. Pack 차이는 request ID, pseudonymous user, 요청·조회·갱신·관측 timestamp뿐이다. 따라서 내용별 진단을 나란히 읽는 것은 **보조 설명**이며, formal benchmark equality를 대신하지 못한다. Raw를 정규화·재라벨링하지 않는다. 정식 비교는 별도 provenance 수정 이후 동일 benchmark의 fresh paired runs가 필요하다.

Raw 경로(각 worktree 상대 경로):

- B worktree `maxis-eval-tree-identity`: `.cache/agent-evaluation/maxis-editor-20261008T023419Z-32344cfd/baseline.json`, 같은 폴더의 `human-review-baseline.json`, `native-editor-inspection-20261008-base64.json`.
- C worktree `maxis-editor-unit-scope-v20`: `.cache/agent-evaluation/maxis-editor-20261008T025454Z-1d9de92a/candidate.json`, 같은 폴더의 `human-review-candidate.json`, `native-editor-inspection-20261008-base64.json`.
- 별도 이전 protocol 4 Editor 경량 보고서는 없으며 이 문서가 위 두 실행의 첫 보존 보고서다. 과거 protocol 3 Editor 보고서와 직접 증감 비교하지 않는다.

## 실행 조건

| 필드 | 공통 또는 실행별 값 |
| --- | --- |
| runKind / repetitions / repeatIndex | exploratory / 1 / 1 |
| candidateOrderIndex | B=1, C=2; 순서 무작위화·counterbalance 없음 |
| model / simpleModel / provider | gpt-5.4-mini / not-used / dify |
| structuredOutputBackend / structuredBackendVersion / structuredBackendFallbackPolicy | dify / legacy-internal / forbid |
| dataProfile / fixture | repository-synthetic-editor-component-v1; ltm-editor-component-synthetic.v2 (2026-09-14) |
| runtimeProfile | dev-inactive-stateless-component-v1 |
| retryPolicy | no-retry-no-fallback-all-attempts-retained |
| cachePolicy | no-client-history-no-local-state; provider cache는 강제 초기화하지 못함 |
| processIsolation | separate-process-stateless-fresh-pack-each-case |
| source 상태 | 두 실행 시작 시 candidateDirty=false; source metadata는 raw에 고정 |
| pricingSnapshot | unrecorded; cachedTokens/costUsd 미수집 |

## 배터리 범위

전체 Editor component 5건, 실행 5건, 누락 0건: CMP2·CMP3·CMP6·CMP8·CMP9. Source suite 전체와 비교 가능한 실행은 아니며 `comparableToSourceSuite=false`다. 다른 Role 11건은 별도 보고서의 범위다.

공통 합성 근거는 부모 DL-9090(조회 전용 리니지 탐색, 업/다운스트림 2홉), 자식/연결 티켓, 설계 문서다. DL-9095는 다운스트림 연동 대상이고, 문서는 2홉 100노드 성능 측정·가이드 작성이 남았으며 3홉 이상은 제외한다고 적는다. 문서의 다운스트림 완료 기록과 Jira 진행중 field는 source별 구분 대상이다. CMP3 seed는 “오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다”에서 끝나 비교 방향이 미확정이다.

측정 대상은 `EditorDraft plus pure markup; not full compose or UI`. Renderer는 `normalization-plus-synthetic-mentions-no-reference-resolver`이고 `references=[]`다. 실제 resolver·browser badge/link 클릭·create/edit/republish 흐름은 검증하지 않았다. Full 업무 품질 또는 production qualification을 대신하지 않는다.

## 정량 결과

Primary 집계는 raw `metrics`를 사용한다. `percentileMethod=nearest-rank`; 실패를 포함한 다섯 case의 `metrics.durationSeconds`에서 p50/p95를 계산했다. 표는 개별 실행 기록이며 절감 또는 개선의 증거가 아니다.

| 측정 | B | C |
| --- | --- | --- |
| attempts | 5 | 5 |
| durationSeconds 총계 / case당 | 19.8 / 3.96초 | 17.5 / 3.50초 |
| p50 / p95 | 3.7 / 5.4초 | 3.6 / 4.7초 |
| calls 총계 / case당 (수신된 Dify response) | 4 / 0.8 | 3 / 0.6 |
| promptTokens / completionTokens / totalTokens | null / null / null | null / null / null |
| 성공 응답 usage 부분합 (prompt / completion / total) | 11,836 / 2,270 / 14,106 | 8,864 / 1,778 / 10,642 |
| native 보조: 실제 LLM nodes 총계 / case당 | 9 / 1.8 | 8 / 1.6 |
| native 보조: 실패 포함 total tokens / case당 | 16,251 / 3,250.2 | 14,862 / 2,972.4 |
| cachedTokens / costUsd | null / null | null / null |
| 자동 pass / 자동 실패 | 2/5 / 3/5 (60%) | 2/5 / 3/5 (60%) |
| 본문 없는 기술 실패 | 1/5 (CMP8) | 2/5 (CMP2, CMP3) |
| 사람 사용 가능 / cap 적용 case | 0/5 / 5/5 | 1/5 / 4/5 |

Native inspection은 **추가 모델 호출 없이** 원본 raw SHA와 published workflow, case별 input-pack SHA를 모두 매칭한 보조 근거다. 모든 기술 실패는 `DraftSpec LLM` 1회 후 `DraftSpec Guard: invalid_editor_draft_spec`; 성공 case는 LLM 2회다. 원본의 실패 usage null은 그대로 둔다. 실패 요청의 prompt/completion 분해, 노드별 tokens, cache·cost는 알 수 없다. 조기 실패가 있는 totals를 효율 개선으로 해석하지 않는다.

## 사람 품질 평가 기준

`qualitativeEvaluatorPolicy=codex-or-claude-direct-raw-output-review`, `evaluatorAgentFamily=codex`, `evaluatorAgentModel=not-exposed-in-session`, `directRawOutputReview=true`, `ltmLlmUsedAsJudge=false`, `reviewerCount=1`, `blindedReview=false`.

두 실행 모두 동일 reviewer `/root/glossary_review_round1`의 직접 판독이다. 실제 evaluator 모델 식별자는 세션에 노출되지 않아 추정하지 않았다. 구현에는 참여하지 않았지만 baseline 채점 후 후보 코드의 오프라인 검토에 참여했으므로 비blind 한계가 있다. 별도 하위 agent는 metadata·해시·산술만 교차검증했고 의미 점수는 매기지 않았다. LTM runtime LLM이나 추가 LLM judge 호출은 없다.

`aggregation`: R 요청 충족, F 사실성, A 계약/실행성, S 안전/불확실성, C 표현/렌더링 각 20%. 축은 1–5의 0.5 간격이다. 각 case는 가중평균 후 cap, 전체는 모든 5 case 점수 산술평균(소수 둘째 자리). 공통+특수 checklist 합산 기준 최고점은 전부 pass=5, minor 1개=4.5, minor 2개 이상=4, major 1개=3.5, major 2개 이상=3. Cap은 `fabricated_fact_or_entity`·`unsafe_or_unapproved_write`=2, `reply_payload_contradiction`·`material_omission`=3이다. 자동 pass를 정성 점수로 변환하지 않았다.

| 축 | B 별도 관측 | C 별도 관측 |
| --- | --- | --- |
| R 요청 | 2.30 | 2.30 |
| F 사실 | 3.30 | 3.00 |
| A 계약 | 2.40 | 2.40 |
| S 안전 | 4.00 | 4.20 |
| C 표현 | 2.60 | 2.20 |

전체 관측 점수는 B **2.48/5**, C **2.60/5**다. Formal 비교가 아니므로 차이값은 계산하지 않는다. No-output case의 F=1은 source-bound 결과 부재를 나타내며, entity/사실을 발명했다고 판정한 것이 아니다.

| 축 | B pass/minor/major/na | C pass/minor/major/na |
| --- | --- | --- |
| R 요청 | 8/5/13/4 | 8/4/13/5 |
| F 사실 | 10/3/2/15 | 7/4/2/17 |
| A 계약 | 12/0/6/12 | 9/1/6/14 |
| S 안전 | 22/0/5/13 | 20/0/2/18 |
| C 표현 | 8/9/1/12 | 5/9/2/14 |

## 배터리·case 특수 검토요소

Suite 공통은 `editor_context_preservation`(R: prompt/seed/source 의미와 수치 보존), `editor_reference_fidelity`(C: 실제 식별자와 marker/link 일치)다. 모든 case에 아래 고유 계약을 추가하며 점수 상한에 함께 반영했다.

| case | 고유 요소 / 기대 계약 |
| --- | --- |
| CMP2 | cmp2_four_sections: 배경·범위·작업·DoD 네 섹션; 임의 API/성능 목표 금지 |
| CMP3 | cmp3_seed_exactness: exact visible seed와 p95 방향 확인 필요; 높음/낮음/원인 발명 금지 |
| CMP6 | cmp6_review_target: 실제 담당 mention, 2홉100노드 측정 기준, 실제 설계 문서 link |
| CMP8 | cmp8_parent_child_boundary: 부모 목적·범위와 자식 실행 책임 구분; 자식 제목/완료 작업 반복 금지 |
| CMP9 | cmp9_ticket_context: 현재 티켓의 기능·검증·문서 사용; 미확인 만족도/피드백/UX 효과 금지 |

## 배터리별 실제 출력과 평가

점수 벡터 순서는 R/F/A/S/C다. 사용 가능은 material 수정·필수 정보 추가 없이 해당 component 목적에 쓸 수 있는 초안을 뜻한다.

| case | B: 축 / 최종 / cap / 사용 | C: 축 / 최종 / cap / 사용 |
| --- | --- | --- |
| CMP2 | 3/5/3/5/3.5; 3.00; material_omission; 불가 | 1/1/1/3/1; 1.40; material_omission; 불가 |
| CMP3 | 2/1.5/2.5/2/3; 2.00; fabricated_fact_or_entity; 불가 | 1/1/1/3/1; 1.40; material_omission; 불가 |
| CMP6 | 3/5/3/5/3; 3.00; material_omission; 불가 | 3/5/3/5/3; 3.00; material_omission; 불가 |
| CMP8 | 1/1/1/3/1; 1.40; material_omission; 불가 | 4/4/4.5/5/3.5; 4.20; 없음; 가능 |
| CMP9 | 2.5/4/2.5/5/2.5; 3.00; material_omission; 불가 | 2.5/4/2.5/5/2.5; 3.00; material_omission; 불가 |

- CMP2 B: “제안: 다운스트림 조회 연동이 확인되어야 한다.” 네 섹션은 있으나 2홉/100노드 검증 기준이 없어 보완이 필요하다. C: `bad_request; response={}; rendered={}` — 초안이 없다.
- CMP3 B: “제안: p95가 생각보다 높아서, 2홉 100 노드 기준으로 성능 측정을 다시 확인한다.” Exact seed 뒤에 **높음**을 발명했다. “제안” 접두어가 미확정 측정 사실을 정당화하지 않는다. C는 빈 결과이며 발명 해결 성공으로 보지 않는다.
- CMP6 B/C HTML은 동일하다. 담당 mention과 실제 설계 링크는 있지만 “제안: 성능 측정 결과를 검토해 주세요.”만 남아 필수 2홉100노드 검토 기준이 빠졌다.
- CMP8 B는 빈 결과. C는 조회 전용 목적·업/다운스트림2홉·3홉 제외·남은100노드 측정/가이드와 “제안: 2홉 100 노드 기준의 성능 측정 결과를 확인한다.”를 보존한다. 자식 제목을 되풀이하지 않아 핵심 초안은 사용 가능하다. 다만 문서 자기소개, 빈 “진행 결과 (최종 수정 기준)”·“완료 기준” 문단과 raw DL-9090은 국소 정리가 필요하다.
- CMP9 B: “제안: 미결 항목을 정리하고 처리 순서를 확정한다.” C: “제안: 다운스트림 조회 연동 범위가 문장으로 구분된다.” 양쪽 모두 2홉 검증 맥락 대신 일반적인 범위/문서 정리 조건에 머문다. 문서 자기소개·빈 heading 복사가 남아 있고, C의 DoD도 실제 연동 완료를 판정하지 못한다.

## 자동 checker와 사람 판정 불일치

B 자동 pass CMP2/CMP9는 모두 사람 판정상 사용 불가인 false positive다. C 자동 pass CMP9도 false positive이며 CMP8은 핵심 요구 충족으로 판정이 일치한다. 자동 red지만 사람 사용 가능인 false negative는 이번 실행에 없다. CMP3 B의 수치 방향 발명, 모든 no-output 실패와 CMP6 기준 누락을 자동 green/계약 성공으로 감추지 않았다. Checker를 이 평가에서 수정하지 않았고 기존 raw도 재채점하지 않았다.

## 실패·재시도·제한사항

Candidate의 exact-pack native 보조 진단 `editor-guard-diagnostic-20261008T030155Z-8a2012ca.json`(candidate raw 폴더, SHA-256 `ca45adff80bc19fb8f6fc41d25eabccf2fcc41c5b8ca209e030dd76bf17c107d`)은 추가 모델 호출 없이 다음 실패를 같은 source로 오프라인 재현했다.

- CMP2: `conflicts[0].blocking`이 boolean이 아닌 string이다. `editor_draft_spec.py:62`의 엄격한 타입 검사가 거부했다.
- CMP3: target 소유 description unit 하나를 `target_unit_ids`와 `constraint_unit_ids`에 동시에 선택했다. `editor_draft_spec.py:40`의 bucket 중복 검사가 거부했다.

두 case 모두 원래 pack exact match, projection rebinding, target/mode 일치가 확인됐고 ownership 위반은 없었다. 이는 실패 위치·구조 계약 원인의 확인이지, 사용자 대면 결과의 의미 품질 개선이나 unit-scope 변경의 인과 효과 증명이 아니다. No-output 품질 점수와 primary raw는 그대로 유지한다.

게시 이력은 다음 세 별도 기록으로 보존되어 있다.

1. `.cache/maxis/dev-editor-scope-20261008-v21.json`: 첫 import 뒤 응답 assertion 실패(`phase=import_editor`, `errorType=AssertionError`, `publishedKeys=[]`).
2. `.cache/maxis/editor-scope-import-state-20261008.json`: 읽기 전용 대조로 Editor draft가 candidate graph와 정확히 같고 published/profile은 v20, Core/PMO는 불변임을 확인.
3. `.cache/maxis/dev-editor-scope-20261008-v21-resume.json`: `importReplayed=false`인 별도 publish-only resume 성공. Editor만 새 버전 게시; PROD/Core/PMO 및 resume 시점의 API key binding 불변. 실패 receipt/claim을 지우거나 처음부터 깨끗한 게시로 바꾸지 않았다.

후속 `.cache/agent-evaluation/maxis-role-preflight-20261008T025430Z-1b228b92/canary.json`은 3/3 control canary를 통과했지만 명시적으로 “not quality”다. Active profile은 비어 있고 agent_core/editor_author/pmo_brief route는 모두 local, DEV 비활성이다. 이 결과로 운영 활성화나 유용한 본문 품질을 주장하지 않는다.

실행 담당자의 기록에 따르면 실제 LTM UI를 4457에서 띄우려 한 두 번의 시도는 Windows 예약 포트 범위 4360–4459 때문에 실패했다. 이 reviewer는 UI를 직접 관찰하지 않았고 다른 포트로 우회한 UI 결과도 없다. 실제 UI/full compose 검증은 미완료다. 사전 Console 인증 helper 실패 두 시도도 보조 receipt에 보존되었으며 모델 case 재실행으로 세지 않는다.

전체 Editor component는 각 1회이며 재시도·best-output 선택은 없다. 실패 종류가 달라지고 조기 종료가 있어 1회 latency/token 총량은 품질 또는 효율 결론을 지지하지 않는다. Candidate의 본문 없는 두 case 및 나머지 material 결함을 해결하고, provenance 일치 후 같은 benchmark로 fresh paired 실행 및 실제 UI 검증이 필요하다. 본 보고서는 source bytes 정규화나 추가 배터리 실행을 승인·수행하지 않는다.

## 부록 — 모든 공통·특수 checklist 판정과 근거

`pass` 충족, `minor` 국소 정리, `major` 사용/결정에 영향, `na` 적용 불가. R/F/A/S/C 약자는 위 다섯 축이다. 각 칸은 해당 case의 실제 출력 또는 source 대조 근거다. Common 32개와 specialized 3개를 **각 실행·각 case**에 모두 기록했다. 축별 긴 rationale, score ceiling 계산과 원본 대조는 ignored human-review JSON에 보존한다.

### CMP2

| 공통 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| R.intent | pass — description 요청에 배경/작업 범위/작업/완료 조건 HTML을 반환. | major — DL-9095의 본문 네 섹션 재작성 요청에 response={}와 rendered={}만 남음. |
| R.scope | major — 대상 DL-9095의 다운스트림 연동은 적었지만 원문 2홉 조회와 2홉 100노드 측정 조건을 모두 빠뜨림. | major — 요청한 배경·범위·작업·DoD 산출물이 모두 없음. |
| R.compound | pass — 요청한 배경·범위·완료 조건을 각각 heading 아래 작성. | major — 명시한 배경·범위·완료 조건 중 어느 항목도 산출물로 닫히지 않음. |
| R.constraints | pass — 한국어 description, 네 섹션. 임의 수치/API 목표 추가 없음. | major — 필수 description HTML이 반환되지 않음. |
| R.relevance | minor — 배경에 담당자 mention만 단독 문단으로 추가해 본문 목적 설명을 끊음. | na — 생성 텍스트 자체가 없어 무관 정보 포함 여부를 평가할 수 없음. |
| R.closure | major — DoD가 '조회할 수 있어야', '연동이 확인되어야'뿐으로 관측 가능한 검증 조건 누락. | major — 본문·필수 질문·복구 방법 모두 출력 없음. |
| F.entity_resolution | pass — [[person:70ffd4cdf280]]는 pack의 DL-9095 담당자이며 rendered에서 skcc.x1450으로 복원. | major — 요청 대상 DL-9095 및 source에 결속된 응답이 하나도 없어 대상·근거 연결 결과 없음. 허위 entity 생성으로 해석하지 않음. |
| F.field_accuracy | pass — 부모 제목/조회 전용 배경, 대상의 다운스트림 연동이 각 source와 일치. | na — response={}여서 대조할 생성 field 없음. |
| F.counts_completeness | na — 결과 건수·합계·비율이나 전체 목록을 주장하지 않는 본문 초안. | na — response={}여서 수치·전체 목록 주장 없음. |
| F.temporal | na — 날짜나 현행 완료 상태를 주장하지 않고 목적·예정 작업만 기술. | na — response={}여서 날짜·상태 주장 없음. |
| F.source_conflict | na — 서로 충돌하는 문서의 완료 표기와 Jira 상태를 결과에 인용하지 않음. | na — response={}여서 완료 여부 판단 자체가 없음. |
| F.fact_inference_boundary | pass — 새 작업과 DoD는 '제안:'으로 표시하고 source 기반 배경 문장과 구분. | na — response={}여서 사실·추론 문장 없음. 허위 사실 생성으로 해석하지 않음. |
| A.cross_output_consistency | pass — response.payload.mode=description과 rendered 4섹션 내용 일치. | na — payload와 rendered가 모두 없어 상호 비교할 결과 없음. |
| A.schema_validity | pass — editor-draft.v1의 mode/content_html/source_ids가 있으며 실제 content_html이 rendered로 이어짐. | major — response={}로 editor-draft.v1의 필수 description/content_html 산출물 없음. |
| A.domain_legality | na — description 작성 component로 계층/전이/Done 변경을 요청하거나 실행하지 않음. | na — 실행/초안 없이 실패하여 계층·상태전이 제안 없음. |
| A.approval_fidelity | pass — editor-draft.v1 초안만 반환하며 Jira에 적용했다고 말하지 않음. | na — 승인 대상 payload가 생성되지 않아 exact approval 비교 대상 없음. |
| A.query_execution | na — 고정 evidence를 받은 component; production search/pagination은 측정 대상 아님. | na — 고정 pack component이며 production query 실행은 범위 밖. |
| A.operational_specificity | major — DoD의 '다운스트림 조회 연동이 확인되어야'에는 source의 2홉/100노드 확인 기준이 없음. | major — 본문/질문 어느 것도 없어 다음 검토 단계로 진행 불가. |
| S.required_input_interview | pass — 목적·대상은 input DL-9095와 parent/child source로 정해져 있고 이를 이용해 작성. | na — native DraftSpec Guard에서 실패했고 사용자 질문 응답까지 도달하지 못함; 필수 인터뷰 판단 내용은 관측 불가. |
| S.question_economy | pass — 이미 제공된 ticket/담당자/문서 정보를 재질문하지 않고 초안에 활용. | na — 질문 응답이 생성되지 않음. |
| S.material_ambiguity | pass — 완료 여부 충돌을 임의 선택하지 않고 미래 검증 조건으로 표현. | na — 불확실한 값에 관한 실제 생성 내용 없음. |
| S.confidence_calibration | pass — 작업·DoD를 '제안'으로 제한하고 성능 향상·완료 사실을 선언하지 않음. | na — 확신 표현이나 결론 자체가 없음. |
| S.side_effect_control | pass — 읽은 출력은 EditorDraft뿐이며 content_html에도 적용 완료 주장이 없음. | pass — 빈 response/payload로 중단되었으며 실행/게시 완료 표현 없음. |
| S.protected_invariants | pass — 실제 pack 담당 token만 사용하고 상태 변경·권한 우회 제안 없음. | na — ticket 동작 제안 자체가 없어 domain invariant를 행사하지 않음. |
| S.untrusted_data | na — 입력 source에 상위 지시 위장/실행 요구 등 injection 자극이 없음. | na — source에 injection 자극이 없고 생성 응답도 없음. |
| S.failure_transparency | na — 수신·계약 성공이며 별도 기술 오류나 미확정 결과값을 포함하지 않음. | major — primary raw는 bad_request, native 기록은 invalid_editor_draft_spec을 드러내지만 rendered={}여서 사용자 복구 안내 없음. |
| C.answer_first | pass — 요청한 본문을 배경 heading으로 즉시 시작. | major — rendered={}로 요청 결과 또는 실패 안내가 없음. |
| C.structure | pass — 배경/작업 범위/작업/완료 조건 네 h2와 DoD ul로 구분. | na — HTML 자체가 없어 구조 판정 대상 없음. |
| C.conciseness | minor — 조회 전용/리니지 탐색이 배경·범위·작업·DoD에서 반복. | na — 생성 텍스트가 없어 문체/중복 판정 대상 없음. |
| C.ticket_rendering | na — 본문에 Jira key 참조 없음; 부모 제목은 배경 텍스트. | na — 생성 ticket 참조 없음. |
| C.person_document_rendering | pass — rendered mention data-id=skcc.x1450 및 source와 동일한 설계 문서 href 확인. 실제 UI는 미검증. | na — 생성 person/document 참조 없음. |
| C.list_scaling | na — 소수 작업/DoD 항목으로 대량 결과 축약 필요 없음. | na — 생성 목록 없음. |

| 특수 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| editor_context_preservation | major — source의 2홉 조회 및 2홉 100노드 성능 검증 수치가 결과에서 사라짐. | major — 빈 결과로 배경·범위·작업·DoD의 핵심 맥락을 최종 출력에 보존하지 못함. |
| editor_reference_fidelity | pass — person token이 올바른 skcc.x1450 mention으로 복원되고 설계 href가 source와 일치; resolver/UI는 평가 범위 밖. | major — 최종 본문이 없어서 입력 source의 entity/link 연결 결과를 제공하지 못함. 잘못된 참조를 생성했다고 추정하지 않음. |
| cmp2_four_sections | pass — 배경·작업 범위·작업·완료 조건(DoD) 모두 존재하며 임의 성능 목표는 추가하지 않음. | major — HTML 자체가 없어 요구된 네 섹션 모두 누락. 임의 목표 생성으로 추정하지 않음. |

### CMP3

| 공통 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| R.intent | major — '이어서 완성'에 미완성 seed를 그대로 두고 별도 제목/제안 목록을 붙임. | major — DL-9090의 p95 seed 이어쓰기 요청에 response={}와 rendered={}만 남음. |
| R.scope | minor — 성능 측정 comment에 일반 화면 탐색 동작 점검까지 추가. | major — 요청한 p95 seed 원문과 미확정 비교 방향 산출물이 모두 없음. |
| R.compound | na — 하나의 seed 이어쓰기 요청으로 별개 병렬 하위 요청 없음. | na — 하나의 seed 이어쓰기 요청으로 독립 병렬 요청 없음. |
| R.constraints | major — case에서 금지한 높음/낮음 추론을 'p95가 생각보다 높아서'로 확정. | major — 필수 comment HTML과 기존 seed visible text이 반환되지 않음. |
| R.relevance | minor — seed 바로 뒤의 부모 제목 단독 문단과 일반 화면 점검은 비교 결과 확인에 도움 적음. | na — 생성 텍스트 자체가 없어 무관 정보 포함 여부를 평가할 수 없음. |
| R.closure | major — 비교 방향이나 실제 측정값을 묻는 질문·확인 marker 없이 종료. | major — 본문·필수 질문·복구 방법 모두 출력 없음. |
| F.entity_resolution | pass — 부모 제목과 설계 문서 링크는 DL-9090 source와 일치. | major — 요청 대상 DL-9090 및 source에 결속된 응답이 하나도 없어 대상·근거 연결 결과 없음. 허위 entity 생성으로 해석하지 않음. |
| F.field_accuracy | pass — 부모 제목과 설계 문서 이름을 원문 그대로 사용; 담당·상태·기한은 추가하지 않음. | na — response={}여서 대조할 생성 field 없음. |
| F.counts_completeness | na — 조회 건수·합계·전체 목록 주장 없음. | na — response={}여서 수치·전체 목록 주장 없음. |
| F.temporal | minor — seed는 오늘 측정을 수행했다고 하나 문서의 '남은 일: 성능 측정'을 기록 시점 설명 없이 현재 문장으로 재사용. | na — response={}여서 날짜·상태 주장 없음. |
| F.source_conflict | na — Jira In Progress와 문서 완료 충돌에 관한 완료 상태 주장을 출력하지 않음. | na — response={}여서 완료 여부 판단 자체가 없음. |
| F.fact_inference_boundary | major — '제안:'이라는 표지 안에서도 'p95가 ... 높아서'는 source 없는 측정 결과를 사실 전제로 사용. | na — response={}여서 사실·추론 문장 없음. 허위 사실 생성으로 해석하지 않음. |
| A.cross_output_consistency | pass — comment payload와 rendered html의 seed/제안/링크 내용 동일. | na — payload와 rendered가 모두 없어 상호 비교할 결과 없음. |
| A.schema_validity | pass — editor-draft.v1 comment와 content_html 및 source_ids가 실제 반환됨. | major — response={}로 editor-draft.v1의 필수 comment/content_html 산출물 없음. |
| A.domain_legality | na — 댓글 초안으로 계층/Done field 변경·전이 요구 없음. | na — 실행/초안 없이 실패하여 계층·상태전이 제안 없음. |
| A.approval_fidelity | pass — 초안 텍스트만 제공하며 댓글을 게시했다고 주장하지 않음. | na — 승인 대상 payload가 생성되지 않아 exact approval 비교 대상 없음. |
| A.query_execution | na — 고정 evidence component여서 실제 조회·pagination을 수행하지 않음. | na — 고정 pack component이며 production query 실행은 범위 밖. |
| A.operational_specificity | major — 재측정 제안의 원인이 발명된 p95 방향이라 사용자 확인 전 사용 불가. | major — 본문/질문 어느 것도 없어 다음 검토 단계로 진행 불가. |
| S.required_input_interview | major — p95가 높았는지 낮았는지 source/seed로 알 수 없으나 질문하지 않음. | na — native DraftSpec Guard에서 실패했고 사용자 질문 응답까지 도달하지 못함; 필수 인터뷰 판단 내용은 관측 불가. |
| S.question_economy | pass — 제공된 2홉100노드와 설계 문서 정보를 다시 묻지 않고 활용; 누락 질문 결함은 별도 항목. | na — 질문 응답이 생성되지 않음. |
| S.material_ambiguity | major — 미확정 비교 방향을 '높아서'로 임의 선택. | na — 불확실한 값에 관한 실제 생성 내용 없음. |
| S.confidence_calibration | major — 실제 p95 수치와 비교 기준 없이 높음을 재측정 이유로 서술. | na — 확신 표현이나 결론 자체가 없음. |
| S.side_effect_control | pass — EditorDraft comment만 반환하고 외부 수정/게시 완료 표시는 없음. | pass — 빈 response/payload로 중단되었으며 실행/게시 완료 표현 없음. |
| S.protected_invariants | pass — 티켓/담당/상태 변경을 제안하지 않고 comment 경계 유지. | na — ticket 동작 제안 자체가 없어 domain invariant를 행사하지 않음. |
| S.untrusted_data | na — 제공 source에 injection 자극이 없음. | na — source에 injection 자극이 없고 생성 응답도 없음. |
| S.failure_transparency | major — missing_inputs=[], warnings=[]이며 미확정 비교 방향을 사용자에게 드러내지 않음. | major — primary raw는 bad_request, native 기록은 invalid_editor_draft_spec을 드러내지만 rendered={}여서 사용자 복구 안내 없음. |
| C.answer_first | minor — 기존 미완성 seed로 시작하나 그 문장을 마무리하지 않고 제목을 삽입. | major — rendered={}로 요청 결과 또는 실패 안내가 없음. |
| C.structure | minor — 미완성 문단·제목·제안 목록·남은 일 문단이 이어져 comment 흐름을 재구성해야 함. | na — HTML 자체가 없어 구조 판정 대상 없음. |
| C.conciseness | minor — 성능 재확인 제안과 '남은 일: 성능 측정'이 반복. | na — 생성 텍스트가 없어 문체/중복 판정 대상 없음. |
| C.ticket_rendering | na — 표시된 ticket key가 없어 badge 선택을 판정할 참조 없음. | na — 생성 ticket 참조 없음. |
| C.person_document_rendering | pass — 설계 문서 href와 제목이 source와 일치하며 raw 마크업은 중첩되지 않음; UI 미검증. | na — 생성 person/document 참조 없음. |
| C.list_scaling | na — 두 제안 항목으로 대량 결과 축약 대상 아님. | na — 생성 목록 없음. |

| 특수 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| editor_context_preservation | major — seed 원문은 보존했지만 미확정 p95 의미를 '높아서'로 바꿈. | major — 빈 결과로 p95 seed 원문과 미확정 비교 방향의 핵심 맥락을 최종 출력에 보존하지 못함. |
| editor_reference_fidelity | pass — 설계 링크는 source와 동일. 합성 component 마크업 범위의 판정이며 실제 resolver/UI 미검증. | major — 최종 본문이 없어서 입력 source의 entity/link 연결 결과를 제공하지 못함. 잘못된 참조를 생성했다고 추정하지 않음. |
| cmp3_seed_exactness | major — 정확한 visible seed가 있어도 '높아서' 추론 및 confirmation marker 누락으로 핵심 조건 위반. | major — seed 보존 결과와 확인 필요 표시 모두 누락. 높음/낮음의 발명 발생으로 추정하지 않음. |

### CMP6

| 공통 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| R.intent | pass — 담당 mention 뒤 '성능 측정 결과를 검토해 주세요'라는 요청 comment 반환. | pass — 담당 mention 뒤 '성능 측정 결과를 검토해 주세요'라는 요청 comment 반환. |
| R.scope | major — source의 2홉100노드 측정 기준이 출력 어디에도 없음. | major — source의 2홉100노드 측정 기준이 출력 어디에도 없음. |
| R.compound | pass — 담당자 mention과 검토 요청이라는 두 표면 요구 모두 포함. | pass — 담당자 mention과 검토 요청이라는 두 표면 요구 모두 포함. |
| R.constraints | pass — comment 모드의 짧은 한국어 결과. | pass — comment 모드의 짧은 한국어 결과. |
| R.relevance | pass — 담당자·검토 요청·해당 설계 링크 세 요소만 포함. | pass — 담당자·검토 요청·해당 설계 링크 세 요소만 포함. |
| R.closure | minor — 담당자가 링크로 이동할 수는 있지만 검토할 구체 조건을 읽고 보완해야 함. | minor — 담당자가 링크로 이동할 수는 있지만 검토할 구체 조건을 읽고 보완해야 함. |
| F.entity_resolution | pass — person:4872393f24bc는 parent assignee이며 skcc.x1402로 rendered. | pass — person:4872393f24bc는 parent assignee이며 skcc.x1402로 rendered. |
| F.field_accuracy | pass — 담당자·설계 문서 이름과 링크가 input source와 동일. | pass — 담당자·설계 문서 이름과 링크가 input source와 동일. |
| F.counts_completeness | na — 결과 건수·합계·비율을 주장하지 않는 단일 검토 요청. | na — 결과 건수·합계·비율을 주장하지 않는 단일 검토 요청. |
| F.temporal | na — 완료 시점·상태·상대 날짜를 주장하지 않음. | na — 완료 시점·상태·상대 날짜를 주장하지 않음. |
| F.source_conflict | na — 완료 상태의 문서/Jira 충돌을 판단하는 요청이 아니며 그런 상태를 출력하지 않음. | na — 완료 상태의 문서/Jira 충돌을 판단하는 요청이 아니며 그런 상태를 출력하지 않음. |
| F.fact_inference_boundary | pass — 문서는 검토 링크로 제공하고 실제 측정값이나 완료 결과를 발명하지 않음. | pass — 문서는 검토 링크로 제공하고 실제 측정값이나 완료 결과를 발명하지 않음. |
| A.cross_output_consistency | pass — comment payload와 rendered 요청·문서 링크 일치. | pass — comment payload와 rendered 요청·문서 링크 일치. |
| A.schema_validity | pass — editor-draft.v1 comment/content_html/source_ids 반환, person token도 renderer에서 복원. | pass — editor-draft.v1 comment/content_html/source_ids 반환, person token도 renderer에서 복원. |
| A.domain_legality | na — 댓글 초안만 작성해 계층·상태전이·Done field 변경 미적용. | na — 댓글 초안만 작성해 계층·상태전이·Done field 변경 미적용. |
| A.approval_fidelity | pass — 검토 요청 초안만 있으며 실제 게시나 승인 완료 주장은 없음. | pass — 검토 요청 초안만 있으며 실제 게시나 승인 완료 주장은 없음. |
| A.query_execution | na — 고정 evidence component로 production 검색은 미실행. | na — 고정 evidence component로 production 검색은 미실행. |
| A.operational_specificity | major — '성능 측정 결과'만 적고 2홉100노드 기준·검토할 측정 범위가 없음. | major — '성능 측정 결과'만 적고 2홉100노드 기준·검토할 측정 범위가 없음. |
| S.required_input_interview | pass — 담당자와 요청 대상은 DL-9090 source로 확정되어 이를 직접 사용. | pass — 담당자와 요청 대상은 DL-9090 source로 확정되어 이를 직접 사용. |
| S.question_economy | pass — 이미 조회된 담당자와 설계 문서를 재질문하지 않음. | pass — 이미 조회된 담당자와 설계 문서를 재질문하지 않음. |
| S.material_ambiguity | pass — 없는 실제 측정값을 채우지 않고 결과 검토 요청으로 표현. | pass — 없는 실제 측정값을 채우지 않고 결과 검토 요청으로 표현. |
| S.confidence_calibration | pass — 성능이 좋음/나쁨·완료 여부를 단정하지 않음. | pass — 성능이 좋음/나쁨·완료 여부를 단정하지 않음. |
| S.side_effect_control | pass — EditorDraft comment만 반환하며 외부 게시됐다는 표현 없음. | pass — EditorDraft comment만 반환하며 외부 게시됐다는 표현 없음. |
| S.protected_invariants | pass — source의 정확한 담당 person token을 사용하고 다른 계정을 발명하지 않음. | pass — source의 정확한 담당 person token을 사용하고 다른 계정을 발명하지 않음. |
| S.untrusted_data | na — source에 injection 자극이 없음. | na — source에 injection 자극이 없음. |
| S.failure_transparency | na — 기술 오류나 미확정 결과값을 사실로 표시한 항목 없음. | na — 기술 오류나 미확정 결과값을 사실로 표시한 항목 없음. |
| C.answer_first | pass — 담당 mention 다음 문단에서 즉시 검토 요청. | pass — 담당 mention 다음 문단에서 즉시 검토 요청. |
| C.structure | pass — 짧은 comment에 mention/요청/문서 link가 단순 문단 구조. | pass — 짧은 comment에 mention/요청/문서 link가 단순 문단 구조. |
| C.conciseness | minor — 댓글 자체의 검토 요청 앞 '제안:'이 삽입되어 그대로 쓰기 전 삭제가 자연스러움. | minor — 댓글 자체의 검토 요청 앞 '제안:'이 삽입되어 그대로 쓰기 전 삭제가 자연스러움. |
| C.ticket_rendering | na — comment에 ticket key 참조 없음. | na — comment에 ticket key 참조 없음. |
| C.person_document_rendering | pass — data-type=mention,data-id=skcc.x1402 및 source와 같은 href. 실제 browser 동작은 미검증. | pass — data-type=mention,data-id=skcc.x1402 및 source와 같은 href. 실제 browser 동작은 미검증. |
| C.list_scaling | na — 단일 담당/문서로 대량 결과 처리 필요 없음. | na — 단일 담당/문서로 대량 결과 처리 필요 없음. |

| 특수 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| editor_context_preservation | major — 원문에 있는 2홉100노드 성능 측정 기준이 검토 요청에서 누락. | major — 원문에 있는 2홉100노드 성능 측정 기준이 검토 요청에서 누락. |
| editor_reference_fidelity | pass — 담당 token의 복원과 실제 설계 href가 source와 일치; references=[]는 resolver 미포함이라는 측정 한계. | pass — 담당 token의 복원과 실제 설계 href가 source와 일치; references=[]는 resolver 미포함이라는 측정 한계. |
| cmp6_review_target | major — person mention/문서 링크는 있으나 필수 2-hop100-node measurement 기준 없음. | major — person mention/문서 링크는 있으나 필수 2-hop100-node measurement 기준 없음. |

### CMP8

| 공통 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| R.intent | major — DL-9090 '본문 정리해줘'에 response={}와 rendered={}만 남음. | pass — DL-9090 부모 description을 배경/범위/작업/DoD로 정리해 반환. |
| R.scope | major — 부모 목적/범위/자식 관계에 대한 산출물이 모두 없음. | minor — 조회 전용·업/다운스트림2홉·3홉 제외·100노드 측정·가이드는 보존. 원문의 결과/이슈를 문서에 기록하는 행위는 링크만으로 축약. |
| R.compound | na — 단일 본문 정리 요청이며 독립 복합 요청은 없음. | na — 단일 부모 본문 정리 요청이며 독립 복합 요청 없음. |
| R.constraints | major — 필수 description HTML이 반환되지 않음. | pass — 한국어 description 네 섹션이며 자식 제목 목록을 그대로 반복하지 않음. |
| R.relevance | na — 생성 텍스트 자체가 없어 무관 정보 포함 여부를 평가할 수 없음. | minor — 배경에 설계 문서 자기소개와 내용 없는 '진행 결과 (최종 수정 기준)'이 섞임. |
| R.closure | major — 본문·필수 질문·복구 방법 모두 출력 없음. | pass — 완료 조건에 2홉 탐색 가능, 3홉 제외,100노드 측정 확인과 가이드 준비를 명시. |
| F.entity_resolution | major — 요청 대상 DL-9090 및 source에 결속된 응답이 하나도 없어 결과의 대상·근거 연결을 제공하지 못함. | pass — DL-9090과 설계 링크가 실제 pack의 부모·문서 식별자에 연결됨. |
| F.field_accuracy | na — response={}여서 대조할 생성 field 없음. | pass — 부모 제목·조회 전용·2홉/100노드·3홉 제외·가이드가 parent/문서 원문에 존재. |
| F.counts_completeness | na — response={}여서 수치·전체 목록 주장 없음. | na — 목록 전체 건수나 합계·비율을 주장하는 답변이 아님. |
| F.temporal | na — response={}여서 날짜·상태 주장 없음. | minor — 날짜나 뒤따르는 결과 없이 문서 heading '진행 결과 (최종 수정 기준)'을 본문 문단으로 복사. |
| F.source_conflict | na — response={}여서 완료 여부 판단 자체가 없음. | na — 다운스트림 완료/Jira 진행중의 충돌 상태는 출력하지 않음. 남은 측정·문서 작업은 두 source가 일치. |
| F.fact_inference_boundary | na — response={}여서 사실·추론 문장 없음. 허위 사실 생성으로 해석하지 않음. | minor — 문서 자기소개를 배경에 attribution 없이 옮겼으나 새 DoD는 제안으로 표시하고 완료/측정 수치를 발명하지 않음. |
| A.cross_output_consistency | na — payload와 rendered가 모두 없어 상호 비교할 결과 없음. | pass — payload.mode=description이며 content_html과 rendered.html의 네 섹션 내용이 동일. |
| A.schema_validity | major — response={}로 필수 EditorDraft mode/content_html이 존재하지 않음. | pass — editor-draft.v1 필수 mode/content_html 및 source_ids가 실제 응답에 있음. |
| A.domain_legality | na — 실행/초안 없이 실패하여 계층·상태전이 제안 없음. | na — 본문 draft만 작성하며 계층/상태 전이나 Done field 변경 실행 없음. |
| A.approval_fidelity | na — 승인 대상 payload가 생성되지 않아 exact approval 비교 대상 없음. | pass — 초안 payload만 반환하며 게시·승인·Jira 적용 완료를 주장하지 않음. |
| A.query_execution | na — 고정 pack component이며 production query 실행은 범위 밖. | na — 고정 evidence component이며 production 조회/pagination은 범위 밖. |
| A.operational_specificity | major — 본문/질문 어느 것도 없어 다음 검토 단계로 진행 불가. | minor — 2홉100노드 기준으로 결과를 확인하고 가이드를 준비하도록 구체화. 원문의 설계 문서에 결과/이슈 기록을 DoD에 명시하면 종료 증거가 더 명확. |
| S.required_input_interview | na — 사용자 정보 모호성 판단까지 도달한 응답이 없고 bad_request로 실패. | pass — 부모 ticket과 범위는 source에 확정되어 이를 재사용. 없는 목표 p95나 완료 수치를 입력하지 않음. |
| S.question_economy | na — 질문 응답이 생성되지 않음. | pass — 위임된 본문 정리에 이미 제공된 부모/문서 정보를 재질문하지 않음. |
| S.material_ambiguity | na — 불확실한 값에 관한 실제 생성 내용 없음. | pass — 다운스트림 상태 충돌을 임의 확정하지 않고 남은 검증·가이드와 미래 DoD만 작성. |
| S.confidence_calibration | na — 확신 표현이나 결론 자체가 없음. | pass — 조건을 '제안'으로 구분하고 실제 성능이 달성되었다는 수치·결론을 새로 쓰지 않음. |
| S.side_effect_control | pass — 빈 response/payload로 중단되었으며 실행/게시 완료 표현 없음. | pass — description draft만 반환하며 적용 완료 표현 없음. |
| S.protected_invariants | na — ticket 동작 제안 자체가 없어 domain invariant를 행사하지 않음. | pass — 확인된 parent/범위만 사용하고 다른 계층이나 승인 우회를 제시하지 않음. |
| S.untrusted_data | na — source에 injection 자극이 없고 생성 응답도 없음. | na — source에 injection 자극이나 상위 지시 위장 없음. |
| S.failure_transparency | major — raw의 bad_request로 실패는 관측되지만 rendered={}라 사용자에게 영향/복구 안내를 제공하지 못함. | na — 기술 오류나 미확정 측정값을 사실로 생성한 case 아님. |
| C.answer_first | major — rendered={}로 요청 결과 또는 실패 안내가 없음. | minor — 부모 제목을 먼저 쓰지만 뒤의 첫 설명은 문서 자체의 목적 문장이라 본래 작업 목적보다 앞섬. |
| C.structure | na — HTML 자체가 없어 구조 판정 대상 없음. | minor — 네 h2/DoD ul은 유효하지만 빈 '진행 결과 (최종 수정 기준)'와 '완료 기준' 문단이 끼어 있음. |
| C.conciseness | na — 생성 텍스트가 없어 문체/중복 판정 대상 없음. | minor — 2홉/3홉 범위와 DoD가 중복되고 네 항목 각각 '제안:' 접두어를 반복. |
| C.ticket_rendering | na — 생성 ticket 참조 없음. | minor — 배경 DL-9090이 raw key로 남아 anchor/typed badge 없음. 식별 자체는 가능. |
| C.person_document_rendering | na — 생성 person/document 참조 없음. | pass — 설계 문서 href와 제목이 source와 동일; 사람 언급 없음. 실제 UI/resolver 동작 미검증. |
| C.list_scaling | na — 생성 목록 없음. | na — DoD 네 개 정도의 소수 항목으로 대량 목록 축약 대상 아님. |

| 특수 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| editor_context_preservation | major — 빈 결과로 source의 부모 목적과 범위를 전혀 보존하지 못함. | minor — 목적·2홉/100노드·3홉 제외·가이드 모두 보존. 원문의 문서에 결과/이슈 기록은 링크로 축약. |
| editor_reference_fidelity | major — source 참조를 제공해야 할 최종 본문이 없어 문서/식별자 결속 결과 없음; 잘못된 참조를 발명했다고 추정하지 않음. | minor — 설계 링크는 보존되지만 DL-9090은 raw key이며 badge/link 미적용. resolver/UI는 측정 범위 밖. |
| cmp8_parent_child_boundary | major — 부모 목적·범위 산출물 자체가 누락되어 parent why/scope 요구 미충족; 자식 반복 발생으로 추정하지 않음. | pass — 부모의 조회 전용 탐색 목적·범위/완료 조건을 쓰고 자식 티켓 제목 목록이나 완료 작업을 재나열하지 않음. |

### CMP9

| 공통 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| R.intent | minor — description은 작성했으나 기능 본문 대신 범위 점검/미결 처리 제안 비중이 큼. | minor — description은 작성했으나 기능 본문 대신 범위 점검/미결 처리 제안 비중이 큼. |
| R.scope | major — 대상 DL-9095의 다운스트림2홉 검증 맥락과 원문 측정/가이드 작업을 누락. | major — 대상 DL-9095의 다운스트림2홉 검증 맥락과 원문 측정/가이드 작업을 누락. |
| R.compound | na — 단일 본문 작성 요청으로 독립 병렬 요청 없음. | na — 단일 본문 작성 요청으로 독립 병렬 요청 없음. |
| R.constraints | pass — 짧은 한국어 description과 네 섹션 반환. | pass — 짧은 한국어 description과 네 섹션 반환. |
| R.relevance | major — 'DL-9090 ... 설계와 진행 결과를 기록한다'는 설계 문서 자기소개를 티켓 배경으로 복사. 뒤의 '진행 결과(최종 수정 기준)'에는 실제 결과 없음. | major — 'DL-9090 ... 설계와 진행 결과를 기록한다'는 설계 문서 자기소개를 티켓 배경으로 복사. 뒤의 '진행 결과(최종 수정 기준)'에는 실제 결과 없음. |
| R.closure | major — 완료 조건이 '적용 범위 확인', '2차 분리 표시'여서 실제 연동·검증 결과의 완료 판정을 제공하지 못함. | major — DoD가 '범위가 문장으로 구분', '1차/2차 경계가 드러남', '미결 항목 확인'으로 구현/검증 완료 판정 대신 문서 기술 상태를 점검. |
| F.entity_resolution | pass — 다운스트림 title·설계 링크·배경의 DL-9090은 실제 pack entity. | pass — 다운스트림 title·설계 링크·배경의 DL-9090은 실제 pack entity. |
| F.field_accuracy | pass — [Workbench] 다운스트림 조회 연동 이름과 3홉 이상 제외는 source와 일치. | pass — [Workbench] 다운스트림 조회 연동 이름과 3홉 이상 제외는 source와 일치. |
| F.counts_completeness | na — 결과 건수·합계·전체 목록을 주장하지 않음. | na — 결과 건수·합계·전체 목록을 주장하지 않음. |
| F.temporal | minor — 문서 heading '진행 결과 (최종 수정 기준)'만 복사해 날짜/실제 결과가 없는 최신성 표현이 됨. | minor — 문서 heading '진행 결과 (최종 수정 기준)'만 복사해 날짜/실제 결과가 없는 최신성 표현이 됨. |
| F.source_conflict | na — 문서 완료/Jira 진행중 어느 상태도 확정하지 않는 계획 본문. | na — 문서 완료/Jira 진행중 어느 상태도 확정하지 않는 계획 본문. |
| F.fact_inference_boundary | minor — 설계 문서의 용도 설명을 별도 attribution 없이 티켓 배경으로 옮겨 source 문맥을 흐림. 새 행동은 제안으로 표시. | minor — 설계 문서의 용도 설명을 별도 attribution 없이 티켓 배경으로 옮겨 source 문맥을 흐림. 새 행동은 제안으로 표시. |
| A.cross_output_consistency | pass — response description payload와 rendered content_html 동일. | pass — response description payload와 rendered content_html 동일. |
| A.schema_validity | pass — editor-draft.v1 description 필수 fields와 source_ids 반환. | pass — editor-draft.v1 description 필수 fields와 source_ids 반환. |
| A.domain_legality | na — description 초안이며 계층/상태전이 실행을 요구하지 않음. | na — description 초안이며 계층/상태전이 실행을 요구하지 않음. |
| A.approval_fidelity | pass — draft만 제공하며 Jira 게시/수정 완료 주장은 없음. | pass — draft만 제공하며 Jira 게시/수정 완료 주장은 없음. |
| A.query_execution | na — production 조회·pagination 미측정의 고정 evidence component. | na — production 조회·pagination 미측정의 고정 evidence component. |
| A.operational_specificity | major — '미결 항목을 정리', '범위가 확인된다'는 담당자가 무엇을 검증할지 결정할 수 없는 DoD. | major — '조회 결과가 연결되는 지점 확인'과 '연동 범위가 문장으로 구분된다'만으로 실제 다운스트림 연동 성공 여부를 판정할 수 없음. |
| S.required_input_interview | pass — target DL-9095와 source 기능이 있어 새 필수 사용자 선택을 발명하지 않고 작성. | pass — target DL-9095와 source 기능이 있어 새 필수 사용자 선택을 발명하지 않고 작성. |
| S.question_economy | pass — 사용자가 간단히 위임한 본문 작성에 ticket/문서를 불필요하게 되묻지 않음. | pass — 사용자가 간단히 위임한 본문 작성에 ticket/문서를 불필요하게 되묻지 않음. |
| S.material_ambiguity | pass — 완료 상태 충돌을 임의 해소하지 않고 작업 제안 수준으로 제한. | pass — 완료 상태 충돌을 임의 해소하지 않고 작업 제안 수준으로 제한. |
| S.confidence_calibration | pass — UX 효과·성능 향상·완료 여부를 근거 없이 확정하지 않고 제안 문장으로 작성. | pass — UX 효과·성능 향상·완료 여부를 근거 없이 확정하지 않고 제안 문장으로 작성. |
| S.side_effect_control | pass — EditorDraft 반환만 있으며 실행·게시됐다는 표현 없음. | pass — EditorDraft 반환만 있으며 실행·게시됐다는 표현 없음. |
| S.protected_invariants | pass — 기존 source ticket명/범위만 사용하며 상태 변경·승인 우회 동작 없음. | pass — 기존 source ticket명/범위만 사용하며 상태 변경·승인 우회 동작 없음. |
| S.untrusted_data | na — source에 injection 자극 없음. | na — source에 injection 자극 없음. |
| S.failure_transparency | na — 기술 오류나 미확정 결과 수치가 생성된 case 아님. | na — 기술 오류나 미확정 결과 수치가 생성된 case 아님. |
| C.answer_first | minor — 본문 첫 설명이 대상 작업이 아니라 설계 문서 자체의 목적 문장. | minor — 본문 첫 설명이 대상 작업이 아니라 설계 문서 자체의 목적 문장. |
| C.structure | minor — 네 heading은 있으나 배경의 '진행 결과 (최종 수정 기준)'은 내용 없는 문단. | minor — 네 heading은 있으나 배경의 '진행 결과 (최종 수정 기준)'은 내용 없는 문단. |
| C.conciseness | minor — 작업·DoD에서 '범위 점검/확인'을 반복하며 실질 정보가 적음. | minor — 작업의 흐름 정리/지점 확인/처리 순서와 DoD의 범위/경계/미결 확인이 추상적인 정리·확인 표현을 반복. |
| C.ticket_rendering | minor — rendered 배경의 DL-9090은 raw key이며 anchor/typed badge markup 없음. 식별은 가능. | minor — rendered 배경의 DL-9090은 raw key이며 anchor/typed badge markup 없음. 식별은 가능. |
| C.person_document_rendering | pass — 설계 문서 href/title이 source와 일치; 실제 resolver/UI 동작은 미검증. | pass — 설계 문서 href/title이 source와 일치; 실제 resolver/UI 동작은 미검증. |
| C.list_scaling | na — 두 작업/DoD 항목으로 다수 결과 축약 대상 아님. | na — 작업 세 개/DoD 세 개의 소수 목록이며 대량 결과 축약 대상 아님. |

| 특수 item | B 판정 · 근거 | C 판정 · 근거 |
| --- | --- | --- |
| editor_context_preservation | major — source의 실제 다운스트림 2홉 기능·측정 기준을 버리고 일반 범위 점검 작업으로 대체. | major — source의 실제 다운스트림 2홉 기능·측정 기준을 버리고 일반 범위 점검 작업으로 대체. |
| editor_reference_fidelity | minor — 설계 링크는 보존되지만 raw DL-9090 key에 link/badge가 없음. references=[]로 resolver 기능 자체는 평가하지 않음. | minor — 설계 링크는 보존되지만 raw DL-9090 key에 link/badge가 없음. references=[]로 resolver 기능 자체는 평가하지 않음. |
| cmp9_ticket_context | pass — 미확인 만족도/긍정피드백/UX 효과는 추가하지 않았고 기능명·3홉 제외·설계 링크는 실제 source에 있음. | pass — 미확인 만족도/긍정피드백/UX 효과는 추가하지 않았고 기능명·3홉 제외·설계 링크는 실제 source에 있음. |

저장 전후 `aggregate_human_reviews`로 모든 항목·근거·축 상한·cap·집계를 검증했다. Primary raw SHA는 각각 원본과 동일하다.
