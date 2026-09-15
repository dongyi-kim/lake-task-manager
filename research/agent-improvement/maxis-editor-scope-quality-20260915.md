# MAXIS/Dify Editor — 대상·계층·출처 투영 품질 평가

2026-09-15. 새 v13 baseline → v14 → v15의 같은 synthetic 5-case 탐색 실행이다.
Guard 수용은 **3/5 → 3/5 → 5/5**, 자동 통과는 **3/5 → 2/5 → 4/5**다.
직접 평균은 **2.52 → 3.04 → 3.54/5**다. v15의 유용한 결과 2/5는 **사용 가능한 초안 1건(CMP2) + 필수 사실 확인 질문 1건(CMP3)**이다. 완성 초안 2건이라는 뜻이 아니다.
CMP6·CMP8·CMP9는 실질 수정이 필요하다. Dify DEV에는 게시했지만 LTM 원격 profile은 비활성이고 모든 route는 local이다. 코드·연결 검증을 의미 품질로 환산하지 않는다.
작업 브랜치는 `codex/maxis-dify-migration`이다. LTM main·PROD와 상위 deploy 저장소의 포인터는 변경하지 않았다.

## 1. 측정 식별자

기준은 [평가 표준](../../app/agent/EVALUATION.md)과 [평가 계약](../../app/agent/evaluation_protocol.json)이다.

| 공통 필드 | 값 |
|---|---|
| protocolVersion / rubricVersion / batteryVersion | `3.0.0` / `2.0.0` / `4.0.0` |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| runKind / repetitions / repeatIndex | `exploratory` / 후보별 `1` / `1` |

| 후보 필드 | 새 v13 baseline | v14 중간 후보 | v15 최종 후보 |
|---|---|---|---|
| runGroupId | `maxis-editor-20260915T053427Z-63d18b59` | `maxis-editor-20260915T055601Z-06a70475` | `maxis-editor-20260915T061308Z-f15f0b17` |
| candidateCommit | `e1548d39fa4c0d3e6fb3d5a0351838ca04aae4a0` | `bda5a28198a7abc55ad9bf5f96894181115fd926` | `b72d8201a0c6962c251852dcb4a249d8a99e8c19` |
| promptVersion | `ltm-maxis-dify-2026.09.14-editor-plan-v13` | `ltm-maxis-dify-2026.09.15-editor-scope-v14` | `ltm-maxis-dify-2026.09.15-editor-scope-v15` |
| candidateOrderIndex / candidateDirty | `1` / false | `2` / false | `2` / false |
| candidateKey | `35cc69b7e774386df3e8da13104a90a3abf2254be2c04b9b0e5a128de9aca231` | `c4d919bbf2167be23d6416d0db82362c2a34d5715ff18dc21da0f810e6e53945` | `4e441820946e33ed59cd9b6c40f2da1d2bd05821d54f69e88f033f4c8ef971d9` |
| comparabilityKey | `85a5147fd6692628a42948c2a576763cd2ac9d1393ec942c333ee850fe5a297f` | `87cb51e05a47a95b2b560b64ab7dc93fb7652015dbd31a4a4d714a8ad075c2c2` | `724c6d0a7748d9df97d665d6b44bd8b284faf691c48e20d731fb7c2d4f746b1a` |

동일 tree digest는 기존 로컬 runtime/harness 범위다. 원격 DSL·prompt 차이는 각 raw의 `metadata.maxisBinding.sourceFiles`와 publishedWorkflowId에 별도로 있다.
v15 Editor publishedWorkflowId는 `c4ddedaf-b400-48de-8d8b-1294b21ec92a`다. raw의 candidateOrderIndex=2를 임의로 3으로 바꾸지 않았다.

## 2. 비교 가능성 및 evidence 선택

benchmarkKey: `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74`.
selectionPolicy: `complete-run-no-substitution`; 실패 포함 후보별 5개 primary attempt를 모두 보존한다.
같은 benchmark·모델·fixture·checker의 제한된 비교이며 `comparableToSourceSuite=false`, `qualificationEligible=false`, `releaseEligible=false`다.
9월 14일의 2.96점을 이번 baseline으로 재사용하지 않는다. v14 실패는 v15 성공으로 교체하지 않는다.

| Primary raw | SHA-256 |
|---|---|
| [새 baseline](../../.cache/agent-evaluation/maxis-editor-20260915T053427Z-63d18b59/baseline.json) | `eb07e9ffd61dbdb7c8f60ff5e12bd499f7db5bea9d3a45ed72689fb4700c3131` |
| [v14](../../.cache/agent-evaluation/maxis-editor-20260915T055601Z-06a70475/candidate.json) | `3e4ee1aa07376d85db54133dfab472b6664b99be258d956d9086c9f2d688fc0f` |
| [v15](../../.cache/agent-evaluation/maxis-editor-20260915T061308Z-f15f0b17/candidate.json) | `c2a188762d39bd3586eb4556a6d2f1b2f855c76751551c8b9f88c70fc0163f43` |

[baseline 직접 평가](evaluations/maxis-editor-20260915T053427Z-baseline.md)와 [전체 checklist](../../.cache/agent-evaluation/maxis-editor-20260915T053427Z-63d18b59/direct-review-baseline.json),
[v14 직접 평가](evaluations/maxis-editor-20260915T055601Z-candidate.md)·[전체 checklist](../../.cache/agent-evaluation/maxis-editor-20260915T055601Z-06a70475/direct-review-candidate.json),
[v15 직접 평가](evaluations/maxis-editor-20260915T061308Z-candidate.md)·[전체 checklist](../../.cache/agent-evaluation/maxis-editor-20260915T061308Z-f15f0b17/direct-review-candidate.json)에 실제 출력·축별 rationale·후보별 160+15 판정이 있다.
review JSON SHA는 baseline `0632b5aac3f25b26e0ada1a6c873157e541a1c517d3fec9a7b0390ffe6ce47ca`, v14 `c0abbe0bfea763070bde9f008b74a2df94f75fed1244207e28f6f3072c4d45a0`, v15 `ef328a87476551f23a45dcd28bf01cdb44c29eb604686c6e1dfce35e3709b498`이다.

## 3. 실행 조건

| 필드 | 세 후보의 고정 조건 |
|---|---|
| model / simpleModel / provider | `gpt-5.4-mini` / `not-used` / `dify` |
| structuredOutputBackend / structuredBackendVersion / structuredBackendFallbackPolicy | `dify` / `legacy-internal` / `forbid` |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy / processIsolation | `no-client-history-no-local-state` / `separate-process-stateless-fresh-pack-each-case` |
| runtimeProfile / dataProfile | `dev-inactive-stateless-component-v1` / `repository-synthetic-editor-component-v1` |

baseline→v14→v15 순서이며 무작위·균형 순서가 아니다. case마다 fresh pack, 후보마다 clean commit을 사용했다.
v14는 원문 unit의 nearest `entity_key`, 명시적 parent edge 기반 관계, 모드 하나의 `draft_contract`를 추가했다.
사용자·상태 metadata key는 이슈로 오인하지 않고 식별 불가 자식/연결 endpoint, 관계 충돌·순환은 unresolved로 둔다.
target과 교차 source 관계가 바뀌면 unit ID도 바뀐다. 문서·제목·prose로 계층을 만들지 않으며 투영 metadata는 새 사실 근거가 아니다.
v15는 실제 own-key 근거의 `entities[].source_ids`를 추가하고 9KB 경계를 지키도록 derived purpose를 줄였다. 두 변경이 함께 있어 단일 변수의 인과 효과로 해석하지 않는다.
외부 EditorDraftV1·내부 editor-plan.v2·원 EvidencePack·두 Guard·fixture/checker/harness는 유지했다. v14에서 고친 system prompt는 v15에서 그대로다. 일반 repair·retry·Guard 완화는 추가하지 않았다.

최종 [assembler](../../deploy/maxis-dify/code/editor_assembler.py) SHA: `86a35a60c464e973a3aaeb3f46da6551490f30c4dad1ba49b4d2d5b162e95d5e`.
결합 system prompt SHA: `adee9916c3aafd981978a693fc96cb73f013ab0a8661a489c308ab21dc7e41fe`(8,150 bytes).
[v14 전달 대조](../../.cache/agent-evaluation/maxis-editor-20260915T055601Z-06a70475/prepared-input-delivery.json)·[v15 전달 대조](../../.cache/agent-evaluation/maxis-editor-20260915T061308Z-f15f0b17/prepared-input-delivery.json)는 각각 5/5에서 Start/KG pack와 prepared view가 로컬과 일치하고 sources가 불변이다.
각 case 46 units, 실제 입력은 v14 7,872–7,938 bytes / v15 7,907–7,981 bytes다.
v15의 1,024-byte Knowledge content + 2×100-byte document name 경계 테스트는 8,924–8,998 bytes다. 더 큰 유효 조합도 항상 수용한다는 뜻은 아니며 9,000-byte 초과는 차단한다.

## 4. 배터리 범위

| 원 suite | version | 전체 | 이번 후보별 실행 |
|---|---|---:|---|
| Conversation | 5.0.0 | 8 | 0 |
| Editor | 4.0.0 | 9 | CMP2·CMP3·CMP6·CMP8·CMP9 입력/checker의 synthetic component |
| Create / Meeting / Context change | 6.0.0 / 4.0.0 / 3.0.0 | 28 / 9 / 4 | 0 / 0 / 0 |

원 suite 합계 58개 중 고정 component 5개를 후보별 5/5 실행했고 component 누락은 없다.
input의 `component_battery_version=2.0.0`과 run의 `batteryVersion=4.0.0`은 구별한다.
CMP1/4/5/7, 실제 조회·reference resolver·TipTap UI·Jira write/승인·Knowledge 25-case는 이번에 실행하지 않았다.

## 5. 정량 결과

| 지표 | 새 v13 baseline | v14 | v15 |
|---|---:|---:|---:|
| Guard accepted draft / 빈 blocked HTML | 3 / 2 | 3 / 2 | 5 / 0 |
| 자동 통과 | 3/5 | 2/5 | 4/5 |
| 유용한 결과 / 직접 평균 | 0/5 / 2.52 | 1/5 / 3.04 | 2/5 / 3.54 |
| 완성된 사용 가능 초안 / 유용한 확인 질문 | 0 / 0 | 1 / 0 | 1 / 1 |
| 수신 Dify calls / attempts | 5 / 5 | 5 / 5 | 5 / 5 |
| prompt / completion / totalTokens | 19,985 / 3,198 / 23,183 | 19,970 / 3,559 / 23,529 | 19,970 / 3,484 / 23,454 |
| metrics.durationSeconds 합계 | 18.5초 | 17.4초 | 19.6초 |
| metrics 기준 p50 / p95 | 3.7초 / 4.8초 | 4.1초 / 4.2초 | 4.3초 / 5.4초 |
| case-attempt당 시간 / totalTokens | 3.70초 / 4,636.6 | 3.48초 / 4,705.8 | 3.92초 / 4,690.8 |
| 모델 case network 실패 / retry | 0 / 0 | 0 / 0 | 0 / 0 |
| cachedTokens / costUsd | 미관측 / 미관측 | 미관측 / 미관측 | 미관측 / 미관측 |

aggregation: 5축 각 0.2 가중평균 후 cap, 모든 case-attempt 점수의 산술평균.
percentileMethod: `nearest-rank-over-all-case-attempts`; 정량 집계는 공통 `metrics`만 사용했다.
calls는 수신 Dify 응답 수이며 내부 모델 step 수가 아니다. null을 0으로 바꾸지 않는다.
v15는 새 baseline보다 totalTokens +1.2%, 시간 합계 +5.9%다. 단일 실행·미관측 cache/가격 조건이므로 안정적 성능·비용 우열로 일반화하지 않는다.

## 6. 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`; evaluatorAgentFamily: `codex`.
evaluatorAgentModel: `current Codex agent; exact runtime model ID not exposed`; evaluator task: `/root/scope_quality_review`.
directRawOutputReview: `true`; ltmLlmUsedAsJudge: `false`; reviewerCount: `1`; blindedReview: `false`.
구현을 수정하지 않은 동일 평가 agent가 최종 출력 전문을 읽고 판정한다. 보고서 작성자는 이 판정을 요약하며 별도 LTM LLM judge를 호출하지 않는다.
rubric 2.0.0은 요청·사실·계약·안전·표현 각 20%, 1–5점·0.5 간격이다. 후보별 공통 160개·특수 15개를 판정한다.
발명/미승인 write cap은 2, reply/payload 모순·중요 누락 cap은 3이다. checklist는 축 점수 상한이며 자동 통과가 사람 점수가 되지 않는다.

| 축 | baseline 평균; 공통 pass/minor/major/na | v14 평균; 공통 pass/minor/major/na | v15 평균; 공통 pass/minor/major/na |
|---|---|---|---|
| 요청 | 1.80; 3/4/21/2 | 2.40; 6/8/14/2 | 3.30; 10/10/10/0 |
| 사실 | 3.30; 7/5/5/13 | 4.00; 15/2/2/11 | 4.30; 19/5/2/4 |
| 계약 | 2.40; 15/0/5/10 | 3.10; 15/1/4/10 | 3.30; 15/2/3/10 |
| 안전 | 3.20; 16/4/6/14 | 4.10; 23/1/3/13 | 4.20; 23/5/2/10 |
| 표현 | 2.00; 2/6/10/12 | 2.50; 6/6/6/12 | 3.10; 5/13/5/7 |

baseline/v14/v15 공통 합계는 43/19/47/51 · 65/18/29/48 · 72/35/22/31, 특수 합계는 4/2/9/0 · 5/1/9/0 · 6/5/4/0이다.
`material_omission` cap 코드 발생은 4/5 · 3/5 · 2/5(80%·60%·40%)다. 발명/미승인 write 관측률이 아니다. v15는 CMP6·CMP9에 cap 3을 적용했다.

## 7. 배터리·case 특수 검토요소

| case | 유지한 요청·필수 계약 | baseline / v14 고유 판정 | v15 고유 판정; 실제 근거 |
|---|---|---|---|
| CMP2 | DL-9095의 4 sections, 임의 성능 목표 금지 | major / pass | pass; target 책임·4 sections·실제 측정 기준 반환 |
| CMP3 | p95 seed 정확 보존, 방향·원인 미확정 | pass / major | pass; exact seed와 측정값 질문, 방향·원인 발명 없음 |
| CMP6 | 담당 mention·2홉 100노드·실제 설계 문서 link | major / major | major; mention·측정 기준 있으나 설계 문서를 Jira 링크로 대체 |
| CMP8 | DL-9090 부모 목적/범위와 자식 책임 분리 | major / major | minor; DoD의 자식 완료 반복은 줄었으나 작업은 자식 세부 편집 지시 |
| CMP9 | DL-9095 확인된 맥락, 미확인 UX 효익 금지 | major / major | major; 부모 범위·문서 완료를 현재 Sub-Task와 혼합 |

suite 공통 요소는 `editor_context_preservation`과 `editor_reference_fidelity`다. 각 항목의 실제 근거는 전체 checklist에 있다.
공통·특수 결함을 함께 판정했다. CMP8의 고유 항목 minor도 작업/DoD의 실용성 결함을 상쇄하지 않는다. 빈 차단 결과는 내부 plan의 부분 장점이나 숨겨진 발명을 최종 사용자 점수에 반영하지 않는다.

## 8. 배터리별 실제 출력과 평가

| case | baseline / v14 / v15 점수 | v15 요청/사실/계약/안전/표현 | v15 실제 최종 출력 발췌 |
|---|---|---|---|
| CMP2 | 2.00 / 4.30 / 4.30 | 4/4.5/4/5/4 | “DL-9095는 DL-9090의 하위 작업으로…다운스트림 조회 연동을 담당한다.” 뒤 “배경에는…함께 적는다.” |
| CMP3 | 3.00 / 2.00 / 4.10 | 4/5/4/4/3.5 | 정확 seed + “p95 값은 얼마인가?” + “결과를 기록할 문서 링크를 그대로 반영할까?” |
| CMP6 | 2.00 / 3.00 / 3.00 | 3/5/3/3.5/3 | 담당 mention + “2홉 100 노드 기준 측정 결과를 공유해도 될까요?” + Jira anchor |
| CMP8 | 3.10 / 3.90 / 3.40 | 2.5/4.5/2.5/5/2.5 | “In Progress”와 “업스트림 2홉 조회를 완료 상태로 정리한다.”; DoD는 문서 기록·3홉 제외 |
| CMP9 | 2.50 / 2.00 / 2.90 | 3/2.5/3/3.5/2.5 | “DL-9090…설계와 진행 결과를 기록한다.” 및 부모 목적·현재 상태, 다운스트림 연동·성능 측정 |

baseline은 전부 사용 불가다. v14는 CMP2만 usable: target 책임·4 sections·검증 가능한 작업을 갖췄고 링크/반복은 사소한 보완이었다.
v14 CMP6은 필수 설계 문서 누락, CMP8은 부모 DoD의 자식 작업 반복, CMP3/9는 빈 결과여서 사용 불가다.
v15 CMP2는 target 책임·범위·검증을 갖춘 초안이며 문서 href와 편집 지시 한 문장은 국소 보완이다.
CMP3은 필요한 p95 값·측정 조건 확인에 유용하지만 질문이 content_html에 있고 missing_inputs=[]이다. 미완성 seed와 질문을 그대로 게시하는 완성 댓글이 아니며, 문서 링크 재질문도 불필요하다.
CMP6은 설계 문서 누락과 공유 허가 재질문, CMP8은 편집 지시형 작업·불충분한 DoD, CMP9는 부모/자식 범위와 문서 완료/Jira 진행 상태 혼합 때문에 실질 수정이 필요하다.
v15가 v14보다 평균은 높지만 CMP6 안전성·CMP8 작업/DoD는 퇴보했다. 전체 actual response·rendered와 조립 전 plan을 보존했다.

## 9. 자동 checker와 사람 판정 불일치

baseline CMP3·CMP8·CMP9는 자동 pass지만 직접 사용 불가인 false positive 3건, v14는 CMP8 1건이다.
v15 자동 pass는 CMP2·CMP3·CMP8·CMP9다. CMP6은 Guard accepted여도 필수 설계 문서 link가 없어 native checker fail이다.
v15 false positive는 CMP8·CMP9 2건, false negative는 없다. CMP3의 자동 pass는 유용한 확인 질문과 일치하지만 게시 가능한 초안 인증은 아니다.
fixture·checker·review spec·protocol·두 Guard를 고치지 않았으며 battery version도 유지했다.

## 10. 실패·재시도·제한사항

최초 baseline launcher는 case 전 `dev_profile_not_verified`로 종료했다. 기존 DEV canary 3/3 후 새 baseline을 실행했다. setup failure이며 모델 case 실패·품질 재시도가 아니다.
baseline CMP2/6은 응답 후 invalid_plan으로 차단됐다. 당시 조립 전 plan이 없어 첫 실패 원인을 추정하지 않는다.
[v14 plan](../../.cache/agent-evaluation/maxis-editor-20260915T055601Z-06a70475/llm-candidates.json)에서 CMP3은 comment 한 section을 지켰으나 seed를 중복해 `_blocks`에서 먼저 차단됐다. “높게 나왔다”는 미지원 방향도 내부 plan에 있었지만 최종 노출되지 않았다.
v14 CMP9는 올바른 target/sections와 유효 unit을 선택했지만 없는 `fixture.ticket.DL-9095`·`fixture.ticket.DL-9092` source ID를 작성해 `_block`에서 차단됐다. 실제 출처는 family source였다.
v15는 별도 commit/run으로 5/5 envelope가 수용됐다. source_ids mapping과 purpose 축약을 함께 바꿔 개선의 단독 원인을 분리할 수 없다.

| 구현·게시 검증 | 결과와 경계 |
|---|---|
| 최종 관련 통합 | 1,146 passed / 1 skipped, 129.91초 |
| 별도 Windows DPAPI | 1 passed, 0.32초; 통합 skip과 구별한 실제 사용자 context 검증 |
| 입력 경계 focused | 51 passed; 고정 5-case에 1,024-byte Knowledge + 2×100-byte 이름 포함 |
| 중간 통합 | v14 1,139 passed / 1 skipped(128.57초), v15 purpose 축약 전 1,141 passed / 1 skipped; 최종 결과와 구별 |
| 코드 리뷰 | 4 rounds, 7 → 8 → 9 → 9/10; 최종 중요 지적 없음. 코드 점수이며 초안 점수가 아님 |
| 리뷰 독립성 | 플랫폼 agent 제한으로 round 2–4에 같은 critic 재사용; 매회 fresh critic은 아님 |
| DEV v15 canary | [증적](../../.cache/maxis/dev-canary-scope-20260915-v15.json) 3/3, 완료 `2026-09-15T06:12:49.378766Z` |

[DEV v15 canary](../../.cache/maxis/dev-canary-scope-20260915-v15.json)의 profileFingerprint는 `e63368fdcea37dd28797837614c6bcdf`다. 게시·검증·측정 식별자는 [보존용 증적](../../deploy/maxis-dify/deployments/dev-editor-scope-2026.09.15.yml)에 모았다.
activeProfile은 빈 값, routes는 모두 local, prodUnchanged=true다. canary는 게시/연결 계약 검증이며 의미 품질이나 운영 활성화 자격이 아니다.
원본 raw·과거 보고서를 보존했다. 전체 58-case, 다양한 입력과 후보별 5회 이상 qualification, 실제 UI·조회·승인/write는 미완료다.
v15는 빈 차단을 없애고 초안 1건·유용한 확인 질문 1건을 제공했지만 나머지 3건의 의미 결함이 남아 운영 도입 기준을 충족하지 못한다. 운영/prod 활성화와 v15 이후 추가 후보 실험은 수행하지 않았다.
다음 개선 우선순위는 필수 설계 문서 link 선택, 부모 수용 기준과 자식 실행 작업 분리, 작성 지침 표현·이미 아는 입력 재질문 축소다. 이번에 완료한 기능으로 주장하지 않는다.
이후 동일 계약의 전체 58-case, 실제 UI·reference resolver, 후보별 최소 5회 qualification으로 검증 범위를 넓혀야 한다.
