# MAXIS/Dify Editor — 원문 단위 선택 구조와 품질 평가

2026-09-14. v11 baseline → v12 원문 단위 선택 → v13 표시·지시 개선의 동일 5-case 탐색 비교다.
Guard 수용은 **0/5 → 4/5 → 4/5**, 자동 통과는 **0/5 → 1/5 → 3/5**다.
직접 판독 사용 가능은 **0/5 → 0/5 → 1/5(CMP6)**, 평균 점수는 **2.00 → 2.30 → 2.96/5**다.
검토 요청 댓글 1건의 제한적 개선은 확인했지만 나머지 4건은 사용 불가다.
자동 통과를 의미 정확도나 도입 승인으로 해석하지 않는다. **로컬 MAXIS 경로는 비활성(local 유지)이며 운영 도입은 보류한다.**

## 1. 측정 식별자

기준은 [평가 표준](../../app/agent/EVALUATION.md)과 [평가 계약](../../app/agent/evaluation_protocol.json)이다.

| 공통 필드 | 값 |
|---|---|
| protocolVersion / rubricVersion | `3.0.0` / `2.0.0` |
| batteryVersion / component scope | `4.0.0` / `maxis-editor-component`, 5 cases |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| runKind / repetitions | `exploratory` / 후보별 1회 |

| 후보별 필드 | v11 baseline | v12 중간 후보 | v13 최종 후보 |
|---|---|---|---|
| runGroupId | `maxis-editor-20260914T035709Z-53ace571` | `maxis-editor-20260914T042709Z-ab7bef4d` | `maxis-editor-20260914T044750Z-f012127f` |
| candidateCommit | `c7eca692d46e1562f7a4fadbe48c176003073449` | `ab55e2807a3d74ab0175e1514a31b0cfc160d385` | `7be1779086e8ee535f666728a201c74d6098410c` |
| promptVersion | `ltm-maxis-dify-2026.09.14-editor-plan-v11` | `ltm-maxis-dify-2026.09.14-editor-plan-v12` | `ltm-maxis-dify-2026.09.14-editor-plan-v13` |
| candidateKey | `485c8ccd0ffc64a27284e418b0dc2cab58c0102e36ecb84b24cabc3746ce9c91` | `981320110a196f2437f364b640aa0cea25101b475b17d3e83666d19660c4880e` | `87b475c780676d9149263f680ffc03cc0d4b47f9483c0e0394c8d8414f3454c6` |
| comparabilityKey | `b0f15400349ba9812c9712af404e1312b2c3dede6fdb8f804ceaa8e8bd178161` | `9f343668cb5f96f555b33c52c7b135d93db4303f6dc60a3ae6be0b992d8f2692` | `38639e767116d2b284d0bde086f86cd9b26973ff9e8c2c08577d51f80b8ee57f` |
| candidateOrderIndex / candidateDirty | `1` / false | `2` / false | `2` / false |
| publishedWorkflowId, Editor | `9b0d3460-904d-4578-a4d6-776639c3518f` | `d02100bf-bede-4cf9-9647-4bdd3d23c3d8` | `ca52fea2-e4c8-40bf-8d00-5ea438e7ecd9` |

`candidateOrderIndex=2`는 각 candidate runner의 기록값이다. v13을 임의로 3으로 바꾸지 않았다.
실제 시간순은 v11→v12→v13이며 무작위·균형 순서 실험이 아니다.
공통 tree hash는 기존 로컬 runtime/harness 범위 값이다. 원격 DSL·prompt 차이는 별도
`metadata.maxisBinding.sourceFiles`, publishedWorkflowId, candidate commit에 남아 있으므로
공통 tree hash를 근거로 세 후보 전체 구현이 같다고 주장하지 않는다.

## 2. 비교 가능성 및 evidence 선택

selectionPolicy: `complete-run-no-substitution`. 실패를 포함한 후보별 5개 전체 결과를 보존한다.
동일 benchmarkKey·모델·고정 입력·source checker·특수 계약의 제한적 비교는 가능하지만,
`comparableToSourceSuite=false`, `qualificationEligible=false`, `releaseEligible=false`다.
과거 02:30 v11의 Guard 1/5를 이번 03:57 baseline 0/5 대신 사용하지 않는다.
원 입력의 `component_battery_version=2.0.0`과 실행 batteryVersion `4.0.0`도 다른 식별자로 그대로 보존한다.

| 원시 증적 | SHA-256 |
|---|---|
| [v11 baseline.json](../../.cache/agent-evaluation/maxis-editor-20260914T035709Z-53ace571/baseline.json) | `ed80900bc254f7b2c2cdb60d727ff864689c8dd8b046a143c2c0d35dfd6ab16f` |
| [v12 candidate.json](../../.cache/agent-evaluation/maxis-editor-20260914T042709Z-ab7bef4d/candidate.json) | `b9c86a0f40eea42557053f9d5dbe73b8a385ec9177a5a708dae7ccdfc5a8a434` |
| [v13 candidate.json](../../.cache/agent-evaluation/maxis-editor-20260914T044750Z-f012127f/candidate.json) | `a32526ff5ee39ba429ae7552c3c4809fe4a9ec7ce21e4af126631e5aa6e2dcc0` |
| [v13 조립 전 plan](../../.cache/agent-evaluation/maxis-editor-20260914T044750Z-f012127f/llm-candidates.json) | `07dba9e8d1761a79589c06f504baa37f2646e73e4fc52538b305987d3980caa4` |
| [v13 입력 전달 대조](../../.cache/agent-evaluation/maxis-editor-20260914T044750Z-f012127f/prepared-input-delivery.json) | `53ce07d0a8408624ad6f487cb0f508a400019b39adf15a9a3febecb9957813ce` |
| [v13 직접 평가 JSON](../../.cache/agent-evaluation/maxis-editor-20260914T044750Z-f012127f/direct-review-candidate.json) | `8aa1e6b4bf73058eb0c8525f2173977f2359f3901f27d20c337fca821485916c` |

확정된 [baseline 직접 평가](evaluations/maxis-editor-20260914T035709Z-baseline.md),
[v12 직접 평가](evaluations/maxis-editor-20260914T042709Z-candidate.md),
[v13 직접 평가](evaluations/maxis-editor-20260914T044750Z-candidate.md)는 독립 보존 자료다.
v13 직접 평가 JSON은 전체 actual output·조립 전 plan·160+15 checklist를 구분해 보존했다.
세 실행의 실제 결과 15개와 조립 전 plan 15개 모두 ignored `.cache`에 남으며,
원문·실패 기록을 새 성공으로 덮어쓰지 않는다.

## 3. 실행 조건

| 필드 | 고정 조건 |
|---|---|
| model / simpleModel / provider | `gpt-5.4-mini` / `not-used` / `dify` |
| structuredOutputBackend / structuredBackendVersion | `dify` / `legacy-internal` |
| structuredBackendFallbackPolicy | `forbid` |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state` |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |
| runtimeProfile / dataProfile | `dev-inactive-stateless-component-v1` / `repository-synthetic-editor-component-v1` |

### 바뀐 책임 분담

```text
로컬 원본 EvidencePack + 요청/seed
  → 기존 Knowledge/ACL 검증
  → Editor Input: 원문 문자열·줄을 unit_id 카탈로그로 투영
  → LLM: fact ID 선택 / proposal 문장 / link source ID 계획
  → Editor Assembler: 원본으로 카탈로그 재생성, 구조·seed·HTML·claim 결합
  → 기존 ContractGuard → 기존 로컬 ResponseGuard
  → 합성 mention/markup projection → 자동 검사 + 직접 품질 판독
```

| 구분 | v11 | v12·v13 |
|---|---|---|
| 모델의 사실 표현 | 원문 quote 문장과 seed/link block 작성 | 서버 생성 unit_id 선택; 모델이 fact 문장·URL·seed block을 작성하지 않음 |
| 내부 계획 | `editor-plan.v1` | `editor-plan.v2`의 section별 fact/proposal/link |
| 본문·댓글 구조 | 모델 block 형식에 의존 | description 4 sections, comment 1 section; 제목은 assembler가 결정 |
| 기존 seed | 모델이 명시적으로 선택 | 신뢰된 intent에서 자동으로 첫 위치·정확히 1회 보존; 추가 중복은 차단 |
| Editor graph | 12 nodes | 13 nodes/13 edges; Core·PMO는 11 nodes 유지 |
| 외부 계약 | `EditorDraftV1` | 동일; EvidencePack·두 guards·기존 평가 fixture/checker도 불변 |

[`editor_assembler.py`](../../deploy/maxis-dify/code/editor_assembler.py)의 `prepare()`와 조립기가
같은 카탈로그 함수를 쓴다. Editor Input은 같은 소스에 고정 entrypoint만 붙이므로 추출/조립 규칙이
별도로 어긋나지 않는다. v2 계획은 [정확한 내부 schema](../../deploy/maxis-dify/contracts/editor-plan.v2.schema.json)로 제한된다.
비-EditorDraft 연결 canary는 기존 문자열을 그대로 통과시키고, 기존 ContractGuard가 최종 검증한다.

### v13 표시 변경과 근거 경계

| 항목 | 보장·범위 |
|---|---|
| 본문 wiki 표시 | Jira description/comment 경로, Confluence body에서만 선행 `h1.`~`h6.`, `*`/`#` list 표식 제거 |
| 보존 영역 | title/key/status/summary·일반 Jira text·Bitbucket/Web은 그대로; code/pre/noformat/fence가 있는 leaf 전체도 정규화하지 않음 |
| 단위 결속 | 정책 `editor-units.v2-wiki-body-display` + source ID/kind/canonical ID/hash/title/URL + 원 path/문자열 + 최종 display/HTML/참조 목록 |
| 타 출처 링크 | 정확한 `source.url`만 anchor 권위로 사용; 타 source 제목 변경으로 표시가 바뀌어도 unit ID 변경 |
| 근거 불변 | 표시 문자열을 새 fact/source로 등록하지 않음. 원문 hash·식별자는 유지하고 두 guards가 원본으로 다시 검증 |
| 제안 | `제안: ` 표시와 원천 참조를 유지하지만 사실성을 보증하지 않음. 제안 속 사실 단정은 자동 Guard를 통과할 수 있어 직접 평가가 필요 |

최종 normalized assembler SHA-256은
`8d5cb437c5cdc7b1a32c5f08b82d45cee101da4b4024c2d10fe6dcf880d74957`다.
v12 SHA `6c5f23d46f7636088761ec1782bfc5f6a9d6e454512ce7d9d605f76d66a382bd`와 구별한다.
v13 prompt는 글 작성 방법 설명 대신 실제 초안을 요구하도록 조정했다. 이것은 지시 변경이며 효과는 출력으로 판정한다.

| 한도 | 값·실제 관측 |
|---|---|
| 원 pack / schema / 모델 입력 | 6,144 / 4,096 / 9,000 UTF-8 bytes |
| 카탈로그 | 최대 96 units, 줄당 1,200자; 초과 시 묵시적 잘라내기 없이 실패 |
| plan / claims / HTML | 최대 32 blocks / 40 claims / 24,000자 |
| v13 실제 입력 | case별 46 units, 5,901~5,981 bytes |
| 실제 전달 대조 | 5/5에서 Start/KG pack=local, prepared view=local, originalSourcesUnchanged=true |

DEV v13 [게시 증적](../../.cache/maxis/dev-publish-quality-20260914-v13.json),
[canary](../../.cache/maxis/dev-canary-quality-20260914-v13.json)는 3/3 통과했다.
profileFingerprint는 `1cd12eb7b1b33a4e1a00295af93c6e68`이다. 연결·배포 검증은 의미 품질 평가가 아니며 앱 활성화를 뜻하지 않는다.
최종 버전·검증·비활성 상태는 [배포 품질 증적](../../deploy/maxis-dify/deployments/dev-editor-units-2026.09.14.yml)에 함께 기록한다.

## 4. 배터리 범위

| 재사용 가능한 원 suite | version | 전체 | 이번 원격 실행 |
|---|---|---:|---:|
| Conversation | 5.0.0 | 8 | 0 |
| Editor | 4.0.0 | 9 | 5개 입력·checker만 component로 재사용 |
| Create | 6.0.0 | 28 | 0 |
| Meeting | 4.0.0 | 9 | 0 |
| Context change | 3.0.0 | 4 | 0 |
| 합계 | | 58 | 고정 synthetic component 5 cases |

CMP2·CMP3·CMP6·CMP8·CMP9는 [`tools.agent_compose_eval.CASES`](../../tools/agent_compose_eval.py)의
native 입력, seed, checker, 특수 검토 계약을 보존한다. [`maxis_editor_eval_cases.py`](../../tools/maxis_editor_eval_cases.py)는
원 fixture의 제한된 synthetic subset을 공급하며 실 서비스 검색 결과를 대체한다고 주장하지 않는다.
CMP4 NEED_INFO 및 CMP1/CMP5의 가능한 no-call 경로는 별도 회귀 대상이다. CMP7 무관 요청 경계도 미실행이다.
full compose·실시간 Jira/Confluence 조회·reference resolver·브라우저 TipTap·게시/write는 이번 범위에 없다.

## 5. 정량 결과

| 지표 | v11 baseline | v12 | v13 |
|---|---:|---:|---:|
| Guard accepted | 0/5 | 4/5 | 4/5 |
| 자동 통과(native + renderer) | 0/5 | 1/5 | 3/5 |
| 직접 판독 사용 가능 | 0/5 | 0/5 | 1/5 (CMP6) |
| 직접 평가 평균 | 2.00/5 | 2.30/5 | 2.96/5 |
| 유효 최종 envelope | 5/5 | 5/5 | 5/5 |
| 빈 blocked HTML | 5/5 | 1/5 | 1/5 |
| 수신 Dify 응답 / attempts | 5 / 5 | 5 / 5 | 5 / 5 |
| prompt / completion tokens | 20,150 / 5,991 | 19,460 / 3,951 | 19,985 / 3,770 |
| totalTokens | 26,141 | 23,411 | 23,755 |
| metrics.durationSeconds | 18.9초 | 16.2초 | 17.4초 |
| case latency 합계 | 18.917초 | 16.169초 | 17.391초 |
| case latency p50 / p95 | 3.631 / 4.827초 | 3.594 / 4.203초 | 3.721 / 4.308초 |
| cachedTokens / costUsd | 미관측 / 미관측 | 미관측 / 미관측 | 미관측 / 미관측 |

v13은 이번 baseline 대비 token -9.1%, latency 합계 -8.1%다. v12 대비로는 각각 +1.5%, +7.6%다.
각 후보 1회이고 provider cache·HTTP 비용은 미관측이므로 안정적 속도·비용 개선으로 일반화하지 않는다.
`calls`는 수신 Dify 응답 수이며 내부 모델 step 수가 아니다. null을 0으로 대체하지 않았다.
aggregation: 각 5축 가중평균 후 case cap, 모든 case-attempt 산술평균.
percentileMethod: `nearest-rank-over-all-case-attempts`.

### 별도 Knowledge 25-case smoke

최신 [v13 Knowledge raw](../../.cache/maxis/evals/dev-keval-20260914T044934307505Z-7b820caa.json)의
SHA-256은 `dac8ca8b210d4d41be6dce9afa43f15283a6a51c8b1fc614d3a154d0802549b8`이다.
25개 실행 중 최종 **11 pass / 14 fail**, `outcome=failed`, `releaseEligible=false`다.

| 항목 | 최신 관측 | 해석 |
|---|---:|---|
| positive | 2/8 | 해당 검증 계약 통과; 답변 의미 정확도 아님 |
| ACL / noHit | 8/8 / 1/1 | 선언된 접근·no-hit 조건의 smoke 회귀 |
| injection 자동 / 최종 | 3/8 / 0/8 | 8개 모두 서명 직접 검토 미완료; 자동 결과를 최종 성공으로 세지 않음 |
| retrieval Hit@2 / MRR@2 | 1.0 / 0.9375 | positive 8건의 지정 문서 검색 지표 |
| 전체 tokens | 58,650 | prompt 54,590 + completion 4,060 |
| latency 합계 / p50 / p95 | 40.040 / 1.980 / 2.736초 | 25개 전체 시도 |

`semanticAnswerCorrectnessAssessed=false`이며, 이는 비-Editor control 계약·검색/가드 smoke다.
11/25를 “LLM 정답률 44%”라고 부르지 않는다. 이전 v11 Knowledge 12/25보다 최종 통과 수는 낮지만
단일 실행이고 Editor 초안 의미 품질과도 다른 평가이므로 모델 성능 하락률로 해석하지 않는다.

## 6. 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`.
evaluatorAgentFamily: `codex`; evaluatorAgentModel: `current Codex agent; exact model ID not exposed`.
directRawOutputReview: `true`; ltmLlmUsedAsJudge: `false`; reviewerCount: `1`; blindedReview: `false`.
원문 직접 평가자는 각 실제 출력·요청·근거를 읽으며, LTM 모델을 judge로 추가 호출하지 않는다.
구현에 참여한 비맹검 평가자라는 한계를 유지한다.

| 축(각 20%) | v11 평균 | v12 평균 | v13 평균 |
|---|---:|---:|---:|
| 요청 충족·완결성 | 1.0 | 2.2 | 2.6 |
| 사실성·근거성 | 3.0 | 2.7 | 3.2 |
| 계약 일관성·실행 가능성 | 2.0 | 2.4 | 3.0 |
| 안전성·불확실성 처리 | 3.0 | 2.8 | 3.4 |
| 가독성·간결성·렌더링 | 1.0 | 2.1 | 2.6 |

공통 checklist 32개·특수 요소 3개를 case마다 판정한다. 5 cases당 160+15개다.
사실/개체 발명·미승인 write는 case cap 2, 응답/payload 모순·중요 누락은 cap 3이다.
자동 산술 도구는 완결성·상한·집계 검증만 한다. Guard accepted와 human score를 상호 변환하지 않는다.
v12 CMP6는 미확인 결과 기록 완료를 제안 문장 안에서 단정하여 raw 2.70이 cap 2.00으로 제한됐다.

| case | v11 / v12 / v13 최종 점수 | v13 판정 |
|---|---|---|
| CMP2 | 2.00 / 2.30 / 2.50 | 사용 불가; 자식 대상·책임의 주요 누락 |
| CMP3 | 2.00 / 2.60 / 2.00 | 사용 불가; 빈 blocked로 회귀 |
| CMP6 | 2.00 / 2.00 / 4.80 | 사용 가능; 중복 `@`의 국소 표시 결함 |
| CMP8 | 2.00 / 2.00 / 3.00 | 사용 불가; 부모 DoD에 자식 실행 세부 반복 |
| CMP9 | 2.00 / 2.60 / 2.50 | 사용 불가; 대상 식별·현재 상태 누락 |

v13 CMP6의 축 점수는 5/5/5/5/4다. 나머지 case에는 material_omission cap 3이 적용된다.
2.96/5를 “정확도 59.2%”로 환산하지 않는다. 아래는 직접 평가자가 판단한 v13 공통 checklist 건수이며,
자동 판단이나 여섯 번째 점수가 아니다. 세 후보별 상세 item 근거는 각각의 직접 평가 JSON에 있다.

| v13 축 | pass | minor | major | na |
|---|---:|---:|---:|---:|
| 요청 | 6 | 3 | 20 | 1 |
| 근거 | 4 | 7 | 8 | 11 |
| 계약 | 11 | 0 | 4 | 15 |
| 안전 | 14 | 4 | 8 | 14 |
| 표현 | 4 | 5 | 13 | 8 |

## 7. 배터리·case 특수 검토요소

| case | 원 요청·보존할 계약 | v12 실제 판정 | v13 실제 관측 / 직접 판정 |
|---|---|---|---|
| CMP2 | 현재 DL-9095의 배경·범위·작업·DoD, 임의 성능 목표 금지 | 4 headings만 통과; 부모/문서·작성 지침으로 의미 실패 | 자동 통과이나 부모/문서 중심 의미 실패; 특수 계약 major |
| CMP3 | seed 정확 보존, p95 방향·원인 발명 금지, 확인 표시 | seed만 보존; 확인 누락 | comment 요청에 5 sections 생성으로 blocked; 특수 계약 major |
| CMP6 | 담당 mention, 2홉 100노드, 실제 설계 문서 link | mention만 성공; 기준·link 누락, 기록 완료 발명 | 세 요소 모두 전달; 특수 계약 pass, 사용 가능 |
| CMP8 | 부모 목적·범위와 자식 실행 책임 분리 | `claim_not_grounded` 차단 | Guard 수용이나 자식 완료 세부를 부모 DoD에 반복; native 실패 |
| CMP9 | 현재 DL-9095의 확인된 맥락만, UX 효익 발명 금지 | raw wiki·부모 필드 혼합 | raw wiki 표식 제거, 자동 통과; 부모/문서 중심 내용은 남음 |

v13 suite 공통 `editor_context_preservation`은 pass 1/minor 1/major 3,
`editor_reference_fidelity`는 minor 1/major 4다. case 고유 요소는 CMP2·CMP3·CMP8 major,
CMP6 pass, CMP9 minor다. CMP9의 UX 발명 금지 부분은 지켰지만 일반 요청·대상 누락은 여전히 major다.

## 8. 배터리별 실제 출력과 평가

### 이전 실행에서 확인한 결함

v11은 5개 모두 `editor_assembler:invalid_plan`으로 빈 최종 결과였다. 모델이 없는 seed를 생성하거나
intent seed를 Jira quote로 복제하는 등 조립 계약에서 실패했다. 조립 전 plan의 부분 장점은 사용자 결과 점수에 넣지 않았다.

v12 CMP8의 첫 실패 claim은 **“h3. 완료 기준”**이었다. 원본 JSON의 escaped newline 때문에 guard support
token은 `nh3`가 되고 claim token은 `h3`가 된다. `완료, 기준`만 겹쳐 필요한 3개 중 2개로 차단됐다.
이는 출처 문법 표시 문제이며, 해결을 위해 guard grounding 비율을 낮추거나 원문을 재작성하지 않았다.
v12 CMP9의 `<p>h2. 진행 결과 …</p>`·`<li>* …</li>`도 표시 규칙의 한계를 드러냈다.

### v13 최종 반환 발췌

| case | 실제 출력 발췌 | 해석의 경계 |
|---|---|---|
| CMP2 | “DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다”, “제안: 다운스트림 조회 연동을 정리해 본문에 반영한다” | 요청한 자식의 실제 작업 설명과 문서 작성 방법이 여전히 섞임 |
| CMP3 | `status=blocked`, `payload={}`, `html=""`, `editor_assembler:invalid_plan` | 다른 case의 개선으로 빈 응답을 대체하지 않음 |
| CMP6 | “성능 측정 결과 검토 부탁드립니다. 2홉 100 노드 기준 측정 결과와 확인된 이슈를 함께 봐주세요.” + 담당 mention + 설계 문서 anchor | 필요한 기준·참조가 전달되고 결과 기록 완료를 단정하지 않음. 중복 `@`는 사소한 표시 결함으로, 실사용 가능 4.80점 |
| CMP8 | “제안: 그래프 렌더 컴포넌트는 WBS 뷰 컴포넌트를 재사용해 완료 상태로 정리한다” | 부모 DoD를 자식 완료 세부로 채운 문제는 문법 정리와 별개 |
| CMP9 | “진행 결과 (최종 수정 기준)”, “제안: 그래프 렌더 컴포넌트와 업스트림 2홉 조회 완료 내용을 반영한다” | wiki 표식은 사라졌지만 자식 작업의 대상·책임 문제까지 해결된 것은 아님 |

실제 출력 전문은 각 raw의 `cases[].response`·`rendered`에 있다. `llm-candidates.json`은 조립 전 진단 자료다.
v13 CMP3의 정확한 첫 실패는 `main`의 `len(sections) > 4`다. 모델은 comment 요청에
`background, scope, work, dod, comment` 5개를 반환했다. 이후 모드별 section 일치 검사에도 어긋나지만
그 지점은 실행되지 않은 별도 결함이다. ID는 유효했고 입력/seed 전달 손실이 원인은 아니었다.
내부 plan에는 확인 질문이 있었으나 최종 응답은 비었으므로 질문 성공 점수를 주지 않았다.
v12의 seed 표시 성공에서 v13의 빈 응답으로 퇴보한 case를 전체 평균 개선 뒤에 숨기지 않는다.

## 9. 자동 checker와 사람 판정 불일치

| 후보 | 불일치 | 의미 |
|---|---|---|
| v11 | 0 | 자동 실패와 실제 사용 불가가 일치 |
| v12 | CMP2 1건 false positive | 4 headings·HTML 통과가 대상·책임의 정확성을 보장하지 못함 |
| v13 | CMP2·CMP9 2건 false positive | CMP6만 사용 가능; 자동 3/5와 직접 1/5를 구별 |

자동 raw는 `semanticCorrectnessAssessed=false`다. 고정 5-case의 checker를 결과에 맞춰 완화하지 않았다.
checker 의미를 강화하려면 원 기대 계약·manifest·battery version을 먼저 갱신하고 별도 새 benchmark로 비교해야 한다.

## 10. 실패·재시도·제한사항

### 구현 검증과 LLM 성과를 구분

| 검증 | 결과·범위 |
|---|---|
| 구현 리뷰 | fresh critic 4회, **7 → 8.5 → 8 → 9/10**. 코드 품질 점수이며 생성물 점수가 아님 |
| 최종 assembler/input behavior | 148 passed. type 및 교차 출처 표시 변경 시 unit ID 갱신·이전 선택 차단 포함 |
| 최종 관련 통합 | 1,116 passed / 1 skipped, 138.86초. 분리된 동일 DPAPI test는 추가 1 passed |
| 초기 v13 통합 | 검증 중 review 수정으로 source/DSL freeze가 어긋나 2 failures 후 중단. 이를 통과로 기록하지 않고 최종 통합을 별도 실행 |
| DEV canary | 3/3. import/publish·version/knowledge/contract 연결 증적이지 본문 의미 품질 검증이 아님 |

### 남은 한계와 다음 우선순위

1. **대상·시간·책임을 먼저 구조화한다.** 각 Jira JSON unit에 가장 가까운 객체의 `key`를 `entity_key`로
   묶고 target/parent/child를 구분하는 투영을 다음 후보로 검토한다. 현재는 `/content/3/key` 같은 path를
   모델이 직접 연결해야 한다. 이번 v13에는 이 기능을 구현하지 않았다.
2. **사실 선택과 의미 있는 초안은 다르다.** 정확한 원문을 골라도 과거 형제 완료 이력을 현재 자식의
   범위/DoD로 쓰면 실패다. 모델에 정답 case나 필수 문장을 주입하거나 자동 통과 조건을 느슨하게 하지 않는다.
3. **미확정 결과를 확인 질문으로 닫는다.** seed 비교 방향과 제안 속 과거 완료형 사실을 특히 검토한다.
   `제안:` label은 불확실성 설명이나 사실 근거를 대체하지 않는다.
4. **투영 범위를 명시적으로 확장한다.** 현재 JSON string leaf와 기존 줄만 사용하고 숫자/bool leaf,
   pack의 별도 facts/metrics는 투영하지 않는다. 1,200자 초과 줄과 96-unit/9KB 초과는 실패한다.
   code marker가 포함된 leaf는 모두 정규화를 끄므로 보수적 표시 미개선이 남는다.
5. **운영 검증은 별도로 한다.** 58-case full suite, no-call·reference resolver·실제 TipTap UI·조회 최신성·승인/write,
   여러 독립 입력과 최소 5회 qualification은 미완료다. 실제 비용/provider cache도 모른다.

DEV synthetic-only·비활성 평가를 유지했다. 운영/prod 활성화, Jira write, 기존 profile의 임의 완화는 하지 않았다.
이번 구조로 실제 검토 요청 댓글 1건의 개선은 확인했다. 그러나 나머지 4건 실패와 Knowledge gate 미충족이 남아
운영 도입 기준에는 못 미친다. 실제 사용 가능한 결과의 수와 반복 품질 판정을 다음 도입 판단의 기준으로 삼는다.
