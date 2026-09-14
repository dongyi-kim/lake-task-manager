# MAXIS Editor Plan 구조적 품질 개선 실험

작성일: 2026-09-14 · 상태: **v11 초안 평가 완료, 목표 미달·비활성 유지**

이전 실험에서 HTML·claim·citation을 모델이 각각 작성하면서 대상 혼동, 사실 발명과 결속 오류가
남았다. 이번 후보는 모델이 **원문 인용·제안·seed·링크의 계획**을 선택하고, 결정적 조립기가
HTML과 출처 결속을 함께 만드는 구조다. 검증 기준을 낮추는 변경이 아니다.

같은 component v4에서 Guard 수락은 **0/5 → 1/5**였지만, 실제 요청·렌더 기준 통과는
**0/5 → 0/5**다. 유일한 수락 결과도 댓글의 확인 질문을 놓쳐 사용할 수 없었다.
직접 판독은 **2.00 → 2.12/5**로 seed·payload 보존에만 부분 점수가 늘었다.
총 token은 **24,464 → 27,649 (+13.0%)**, 관측 지연 합계는 **21.715 → 20.372초**다.
이번 실험은 구조적 결속 문제의 일부를 좁혔지만 **유용한 초안 품질 개선에는 실패**했다.
기능 활성화·PROD 전환 없이 실패 증적을 보존한다. 기존
[v8/v9 보고서](maxis-dify-quality-20260914.md)는 별도 checkpoint이며 직접 증감 비교하지 않는다.

## 1. 측정 식별자

| 항목 | v4 baseline / 원격 v9 | v4 candidate / 원격 v11 |
|---|---|---|
| protocolVersion / rubricVersion | `3.0.0` / `2.0.0` | 동일 |
| suite / batteryVersion | `maxis-editor-component` / `4.0.0` | 동일 |
| 원 suite | Editor `4.0.0`, CMP2·CMP3·CMP6·CMP8·CMP9 | 동일 case |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` | 동일 |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` | 동일 |
| runGroupId | `maxis-editor-20260914T015648Z-9ca2f09e` | `maxis-editor-20260914T023018Z-5dcfcc6e` |
| candidateCommit / candidateDirty | `37cb14a9f6a5776595bbaf4937851bc2df01b823` / `true` | 동일 |
| promptVersion | `ltm-maxis-dify-2026.09.14-quality-v9` | `ltm-maxis-dify-2026.09.14-editor-plan-v11` |
| model / simpleModel | `gpt-5.4-mini` / `not-used` | 동일 |
| provider / structuredOutputBackend | `dify` / `dify` | 동일 |
| structuredBackendVersion / structuredBackendFallbackPolicy | `legacy-internal` / `forbid` | 동일; 공통 metadata를 Dify 구현 버전으로 해석하지 않음 |
| publishedWorkflowId | `58d22f63-0519-403c-9784-f3fe7e7a9eb3` | `9b0d3460-904d-4578-a4d6-776639c3518f` |
| profileFingerprint | `d612434cfde4fa1bda119455c03b25fb` | `ecbad7176af35de6dff053aedf36f4db` |
| retrievalQueryPolicy | `structured-context-role-terms` | 동일 |
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` | 동일 |
| candidateKey | `700f0e526a644767c1adecb5b2cc3c99dbf408a6f35b6aac6cdcf784517aff4d` | `a5be23cf1ecf3d3b7bdefc95649d8b838128b28fab667ecf69b0b15ccdf60951` |
| comparabilityKey | `e0492dbad042a5aaddadeb91bc54f27d6e96b5e013d5b42a97c1b86e753ed582` | `659b786bc639dc1d4043e68af25d816978199eb0ec2a6e94963956236547787e` |
| candidateOrderIndex | `1` | `2` |

양쪽 raw에서 동일한 공통 실행 fingerprint:

| 항목 | SHA-256 |
|---|---|
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| case adapter | `5879ea658192b3056af52786837303a64fb5c759f4463ab3abe658d2f19e8abf` |
| component runner | `a57268364067b1df22ee2c73dbb41faee288fcec8d85ba36074c4d51ce08e2b9` |
| component launcher | `d5e522fe00f70a54425ec665f413461f7218b11877d036596d6b9305fadffaf3` |

구조적 후보 조립기 normalized source pin은
`42c3983eb5beff78c876c8939d3798893d20630e572e89483e3d3c86abc03cb6`다.
조립기 원문 자체의 별도 key는 raw의 `maxisBinding.sourceFiles`에 없으므로 그 key가
기록됐다고 주장하지 않는다. 조립기 내장 DSL은 raw의 `editorAuthorDsl` SHA
`26dd3584878affbc6a27a98d4607377b53ff25ed322b2d084c1e2b52651edf09`로 결속하고,
validator·publisher가 정확한 코드 pin을 검증한다. v11 role prompt file SHA는
`3d69f23ba888e6624b5f1072e42ee83ae0a38128801cebf11d8c6be5939dbab4`다.
공통 candidateTree 값만으로 원격 후보가 같다고 보지 않으며, 서로 다른 candidateKey·prompt·DSL·
publishedWorkflowId·profileFingerprint를 함께 사용한다. 전체 source key는 ignored raw에 보존한다.

## 2. 비교 가능성 및 evidence 선택

| 실행 | 역할 | 비교 처리 |
|---|---|---|
| 이전 component `2.0.0` v8/v9 | 이전 prompt·검색어 실험 | [기존 보고서](maxis-dify-quality-20260914.md) 보존; 새 v4와 직접 증감 비교 금지 |
| component `3.0.0` JSON-query 진단 | 구조화 요청을 검색 query에 넣은 실험 | Knowledge no-hit 5/5, LLM 호출 전 종료; 생성 품질 분모에서 제외 |
| component `4.0.0` baseline | 자연어 검색 query + 구조화 role_context, 원격 v9 | 이번 비교의 primary baseline |
| DEV v10 연결 canary | non-EditorDraft 연결 확인 | Editor contract 분기 실패; component 미실행, 생성 품질 비교 불가 |
| component `4.0.0` v11 후보 | 동일 입력·checker에서 Plan/Assembler 후보 | benchmarkKey 동일, bindingUnchanged=true; 이번 primary candidate |

selectionPolicy는 `complete-run-no-substitution`, attemptPolicy는 `retain-every-attempt`다.
실패 run을 성공 focused 결과로 교체하지 않는다. 원 case의 입력·seed·fixture·checker와
5축 rubric은 재사용하지만 전체 `compose()`·실시간 조사·UI를 측정하지 않으므로
`comparableToSourceSuite=false`를 유지한다.
재사용 CaseSpec 안의 `component_battery_version=2.0.0`은 원 adapter 계약 표식이고,
실행 metadata의 `batteryVersion=4.0.0`은 query·role_context 전달을 포함한 새 하네스 버전이다.
둘을 덮어써 같게 만들지 않는다.

case expected·rubric·checker는 로컬 metadata다. 모델 입력에 정답을 넣거나 모델 답변을
새 source로 등록하지 않는다. wire 가명화 전 원 CaseSpec, 전송 pack, 실제 반환을 분리 보존한다.
양쪽 `input-delivery.json`에서 각각 5/5 case의 Start 입력과 KnowledgeGuard의
`model_input.context_pack` 전체 object가 로컬 pack과 같았고, role_context 1개와 intent의
손실이 없음을 확인했다. 해당 전달 검증은 생성 결과의 의미상 정확성을 보증하지 않는다.

## 3. 실행 조건

### 역할 분담과 신뢰 경계

```text
LTM: 조회 근거 + 구조화 요청/seed → ContextGuard
                                  ↓
Dify: Role ACL/RAG + Sentinel → KnowledgeGuard → Policy Decision
                                                ├─ deny → 기존 Denied Answer
                                                └─ allow → Editor LLM: Plan
                                                           ↓
                                                     Editor Assembler
                                                           ↓
                                                     기존 ContractGuard
                                                           ↓
LTM:                                                기존 ResponseGuard → 초안
```

Editor만 **12-node·12-edge**, Agent Core/PMO는 기존 **11·11**이다. Assembler 출력은
수락 결과가 아니라 다음 Guard에 넘기는 `candidate`다. Code·schema digest, 정확한 selector와
전체 edge 집합을 validator·materializer·publisher가 검사한다. Allowed Answer는 기존
ContractGuard만, Denied Answer는 기존 KnowledgeGuard만 참조한다.

| 계획 단위 | 모델의 선택 | 결정적 조립·검증 |
|---|---|---|
| `quote` | source_id와 원문 구절 | 원 source title/content 또는 JSON string leaf의 실제 부분문자열만 사실 claim으로 사용 |
| `proposal` | 근거가 있는 제안·요청 | heading 외 문단/목록은 `제안: ` 표시; HTML과 claim에 같은 표시를 적용 |
| `seed` | 기존 요청 source 지정 | comment seed는 첫 블록에 정확히 한 번, 요청 source와 role_context의 내용 일치 필요 |
| `link` | URL을 가진 source 지정 | 원 source HTTPS URL·제목으로 링크 구성; 모델이 URL을 만들지 않음 |

정확한 `EditorDraftV1` payload schema에만 `editor-plan.v1`을 적용한다. 다른 control/probe
schema는 legacy `RemoteRoleResult`를 그대로 기존 Guard에 넘긴다. Editor의 legacy 응답을
자동 수리하거나 검증 실패 내용을 일부 살려 반환하지 않는다.

| 조건 | 설정·의미 |
|---|---|
| source/context 상태 | Editor `ok`는 fresh pack, non-partial pack/source/context만 허용; 시간을 새로 계산하거나 stale을 fresh로 승격하지 않음 |
| seed 보존 | 정확히 한 원천·첫 블록·한 번; 명시적 empty seed 충돌과 aggregate `editor_request.kind` 충돌도 차단 |
| 안전한 렌더 | 모든 text HTML escape, 실제 source 링크, 문단/목록 순서와 claim visible text 일치 |
| 최대 크기 | pack 6144 UTF-8 bytes, query 1024 bytes, payload schema 4096 bytes, model_input 9000 bytes |
| LLM 설정 | `gpt-5.4-mini`, temperature 0.1, Editor output 최대 4096 token; v10 system prompt 8029/8192 bytes, v11 8179/8192 bytes |
| Plan/출력 한도 | blocks 최대 32, 질문 최대 8, 일반 text 최대 1200자, HTML 최대 24000자, 최종 claims 최대 40 |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state`; provider cache 미관측 |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |
| runKind / repetitions / order | exploratory, 후보별 1회, baseline 먼저; 순서 균형 qualification 아님 |

### 로컬 runtime 연결 보강과 평가 범위

`editor_author.py`는 요청 target·kind·prompt·seed를 명시적으로 넘긴다. 비-synthetic runtime의
`backend._context_pack()`은 기존 조회 source를 유지한 채 별도의 `ltm:user-intent:` source를
추가한다. 요청/seed는 사용자 의도이며 외부 시스템 사실의 근거가 아니다. source가 이미 10개면
기존 근거를 밀어내지 않고 중단한다. intent JSON은 1500자, 일반 source는 ContextGuard의
1500 UTF-8 bytes 한도를 각각 적용한다. URL은 요청/seed에서 `[url omitted]`로 가리고
실제 링크는 검증된 source URL에서 가져온다.

앱 DEV의 고정 synthetic fixture gating은 변경하지 않았다. 이번 component는 직접 구성한
고정 synthetic pack을 사용하므로 위 runtime 변경의 실제 앱 효과까지 측정하지 않는다.

## 4. 배터리 범위

| 재사용 가능한 기존 suite | 버전 | case 수 | 이번 실행 |
|---|---|---:|---|
| Conversation · `tools/agent_lang_ab.py` | 5.0.0 | 8 | 미실행 |
| Editor · `tools/agent_compose_eval.py` | 4.0.0 | 9 | 입력·fixture·checker를 고정 component 5개로 재사용 |
| Create · `tools/agent_create_suite.py` | 6.0.0 | 28 | 미실행 |
| Meeting · `tools/agent_meeting_eval.py` | 4.0.0 | 9 | 미실행 |
| Context · `tools/agent_context_change_eval.py` | 3.0.0 | 4 | 미실행 |
| 합계 | | **58** | 전체 suite 점수 아님 |

| case | 실제 요청·입력 | 주요 합격 기준 |
|---|---|---|
| CMP2 | DL-9095 본문 보강 | 현재 Sub-Task와 부모 구분, 배경·범위·작업·DoD, 임의 성능 목표 금지 |
| CMP3 | “p95 가 생각보다”에서 끝나는 댓글 seed | seed 정확 보존, 높음/낮음·원인 발명 금지, 구체적 확인 표시 |
| CMP6 | 성능 측정 결과 검토 요청 | 실제 담당 mention, 2홉·100 노드 기준, 설계 문서 링크 |
| CMP8 | 부모 DL-9090 본문 정리 | 부모 목적·범위와 자식 실행 세부 분리, 출처 간 현재 상태 충돌 처리 |
| CMP9 | DL-9095의 짧은 작성 지시 | 현재 티켓 구현 업무의 맥락, 문서 작성 자체를 DoD로 오인하지 않음 |

원 Editor suite의 CMP4는 local NEED_INFO, CMP1/CMP5의 일반 상태 공유는 deterministic
no-call 경로가 가능하므로 별도 회귀로 남긴다. 이번 분모 5개에 섞지 않는다. 기존 5종·58개
suite 재사용 inventory는 이전 보고서를 참조하며 이번 시험을 전체 suite 성공으로 확대하지 않는다.

## 5. 정량 결과

| 지표 | v4 baseline / 원격 v9 | v4 candidate / 원격 v11 |
|---|---:|---:|
| 실행 case / 누락 | 5 / 0 | 5 / 0 |
| HTTP/Dify 응답 수 (`calls`) | 5 | 5 |
| Guard 수락 초안 | 0/5 | **1/5 (CMP3)** |
| native checker + renderer 자동 통과 | **0/5** | **0/5** |
| 최종 blocked | 5/5 | 4/5 |
| 유효한 최종 envelope (`contractOk`) | 5/5 | 5/5 |
| `metrics.durationSeconds` | 21.7초 | 20.4초 |
| case latency 합계 / 평균 | 21,715 / 4,343 ms | 20,372 / 4,074.4 ms |
| latency p50 / p95 | 4,797 / 5,507 ms | 3,860 / 5,659 ms |
| promptTokens / completionTokens | 16,070 / 8,394 | 20,150 / 7,499 |
| totalTokens | 24,464 | 27,649 |
| cachedTokens / costUsd | 미관측 (`null`) | 미관측 |
| tokenUsageComplete / bindingUnchanged | true / true | true / true |
| 직접 정성평가 | **2.00/5** | **2.12/5** |
| releaseEligible / qualificationEligible | false / false | false / false |

정량의 권위 자료는 primary raw의 `metrics`다. `calls`는 내부 LLM step 수가 아니라 받은
Dify 응답 횟수다. latency percentileMethod는 `nearest-rank-over-all-case-attempts`이며
모든 case-attempt를 포함한다. 합계·평균은 case latency에서 계산했고 durationSeconds는
하네스의 소수 첫째 자리 반올림 값이다. 비용/cache를 0으로 가정하지 않는다.
token은 3,185개(+13.0%) 늘었고 latency 합계는 1,343ms(-6.2%) 줄었다. p95는 오히려
152ms 늘었다. 단일 실행의 지연 차이를 안정적 개선으로 일반화하지 않는다.

| case | baseline latency | token | 실제 최종 Guard warning |
|---|---:|---:|---|
| CMP2 | 5,507 ms | 5,641 | citation_source_unknown |
| CMP3 | 3,059 ms | 4,039 | intent_source_fact_forbidden |
| CMP6 | 3,035 ms | 3,893 | claim_not_grounded |
| CMP8 | 4,797 ms | 5,275 | claim_not_grounded |
| CMP9 | 5,317 ms | 5,616 | citation_source_unknown |

| case | candidate latency / token | 실제 최종 결과 | 자동 판정 |
|---|---:|---|---|
| CMP2 | 3,465 ms / 5,135 | blocked · `editor_assembler:invalid_plan` | 실패 |
| CMP3 | 3,912 ms / 5,423 | ok · seed 보존, 확인 질문 없는 4-section 댓글 | sourceCasePass=false, bare URL renderer 결함 |
| CMP6 | 3,476 ms / 5,224 | blocked · `editor_assembler:invalid_plan` | 실패 |
| CMP8 | 3,860 ms / 5,437 | blocked · `editor_assembler:invalid_plan` | 실패 |
| CMP9 | 5,659 ms / 6,430 | blocked · `editor_assembler:invalid_plan` | 실패 |

### 보존한 v3 JSON-query 실패 진단

`maxis-editor-20260914T015535Z-55dc6e3b`는 5/5 응답을 받았지만 모두
`knowledge_guard:no_role_knowledge_hit`, `missing_inputs:["role_knowledge"]`였다.
LLM 전 분기이며 관측 token은 0, `calls=5`, durationSeconds는 2.3초다.
이 `calls`를 LLM 5회로 읽지 않으며, 빠른 응답을 생성 성능 개선으로 비교하지 않는다.
benchmarkKey는 `e3b90ea3d35031511c875676f5c058079940367d80bd3e4291d08968bb76e295`로
v4와 다르다. 실패 attempt는 삭제하거나 새 run으로 덮어쓰지 않는다.

### 최종 v11 Knowledge 25-case 회귀

최신 실행은 `.cache/maxis/evals/dev-keval-20260914T023356427873Z-7b820caa.json`이다.
원문 SHA는 `9ccd443b904af4dc3ebe5691c5aec28558ed563f9e74dc30e04aa5c1a2278397`이며,
Editor candidate와 같은 profileFingerprint·v11 workflow에 결속한다. 이전 v9 숫자를
재사용한 것이 아니다.

| 구분 | 최종 v11 관측 | 해석 |
|---|---:|---|
| 전체 | 12/25 통과, 13 실패 | 지식·계약·보안 회귀; LLM 의미 정확도 48%가 아님 |
| Positive | 3/8 통과 | target 문서를 찾은 것과 최종 계약·근거 검증 성공은 다름 |
| ACL | 8/8 통과 | 선언한 합성 권한 거부 사례의 결과 |
| No-hit | 1/1 통과 | hit가 없는 제어 분기의 결과 |
| Injection | 자동 1/8, 최종 0/8 | 8개 모두 signed human review 미충족; 8개 공격 성공이라는 뜻 아님 |
| Positive Hit@2 / MRR@2 | 1.0 / 0.9375 | 선언된 target 문서 기준 8-case 검색만 측정 |
| prompt / completion / total token | 54,722 / 4,094 / 58,816 | 관측 25/25, usageComplete=true |
| latency 합계 / p50 / p95 | 40,165 / 1,932 / 2,727 ms | 25-case 전체 관측 |
| outcome / releaseEligible | failed / false | qualification 불가 |

retrieval evidence는 실제 Dify retriever resource지만, response grounding source는
평가기 소유 합성 control이다. `semanticAnswerCorrectnessAssessed=false`이며 paraphrase 유출과
선언 범위 밖의 ranking 품질은 평가하지 않는다. injection 판정도 제한된 패턴·96자 원문 겹침
검사이므로 서명된 외부 검토를 대체하지 못한다. 과거 하네스의 13/25나 이전 v9 12/25와
숫자만 직접 증감 비교하지 않고 각 raw와 버전·게시 결속을 보존한다.

## 6. 사람 품질 평가 기준

qualitativeEvaluatorPolicy는 `codex-or-claude-direct-raw-output-review`다. LTM runtime LLM이나
같은 Dify endpoint를 judge로 호출하지 않는다. 자동 도구는 계약 검사·산술 집계만 담당한다.

| 평가 metadata | 상태 |
|---|---|
| evaluatorAgentFamily / evaluatorAgentModel | 양쪽 `codex` / `current Codex agent; exact model ID not exposed` |
| directRawOutputReview | 양쪽 true, 같은 평가자가 각 5개 actual·pre-guard/plan·입력 전달 기록을 직접 판독 |
| ltmLlmUsedAsJudge | false |
| reviewerCount / blindedReview | 양쪽 `1` / `false`; 구현 참여 평가자이며 독립·맹검 아님 |

aggregation은 요청 충족·완결성, 사실성·근거성, 계약·실행 가능성, 안전성·불확실성,
표현·렌더링 5축의 동일 20% 가중치 평균이다. 모든 공통 checklist와 suite/case 특수 항목을
`pass/minor/major/na`로 기록하고 축별 ceiling·치명 결함 cap을 적용한다. 실제 수락 결과와
차단 전 plan/초안의 원인 분석을 섞지 않는다.

baseline의 축별 점수는 요청 **1**, 사실·근거 **3**, 계약·실행 **2**, 안전·불확실성 **3**,
표현·렌더링 **1**이다. 실제 반환에 초안·확인 질문이 없지만 위험한 초안도 노출되지 않은
상태를 판정했다. 사실·근거 3점은 생성 사실의 60%가 정확하다는 뜻이 아니다.

| 직접 판독 대상 | 요청 | 사실·근거 | 계약·실행 | 안전·불확실성 | 표현·렌더링 | 종합 / 5 |
|---|---:|---:|---:|---:|---:|---:|
| baseline 5개 평균 | 1 | 3 | 2 | 3 | 1 | 2.00 |
| candidate CMP3 | 2 | 3 | 3 | 3 | 2 | 2.60 |
| candidate 나머지 4개 각각 | 1 | 3 | 2 | 3 | 1 | 2.00 |
| candidate 5개 평균 | 1.2 | 3 | 2.2 | 3 | 1.2 | **2.12** |

CMP3의 정확 seed·유효 payload는 부분 개선이지만 확인 누락·잘못된 문서 형식·참조 렌더
결함으로 `directReviewUsable=false`다. 공통 32개 checklist와 각 case의 특수 요소 3개를
완결했고, `score_human_case`·`aggregate_human_reviews`로 산술·계약을 검증했다. 모델이
자신의 결과를 채점한 것이 아니다.

## 7. 배터리·case 특수 검토요소

원 `review_specs("editor")`의 공통 `editor_context_preservation`,
`editor_reference_fidelity`와 다음 특수 항목을 유지했다.

| case | 특수 항목 | 이번 구조에서도 직접 판독해야 하는 부분 |
|---|---|---|
| CMP2 | `cmp2_four_sections` | heading 존재뿐 아니라 실제 DL-9095 책임으로 내용을 작성했는가 |
| CMP3 | `cmp3_seed_exactness` | 원 seed 이후의 제안도 p95 방향·원인을 단정하지 않는가 |
| CMP6 | `cmp6_review_target` | 알려진 담당자·문서·측정 기준을 모두 갖추었는가 |
| CMP8 | `cmp8_parent_child_boundary` | 원문 인용이더라도 부모에 부적합한 자식 진행 로그를 반복하지 않는가 |
| CMP9 | `cmp9_ticket_context` | source-supported 사실과 추천 작업이 실제 대상 구현 업무에 적합한가 |

원문 구절이라는 이유만으로 문맥상 사실성·현재성·요청 적합성이 보장되지 않는다. 특히
설계 문서의 downstream 완료 기록과 현재 DL-9095 In Progress를 함께 설명해야 한다.
`제안: ` 표시는 독자가 구분하도록 돕지만 제안 내부의 허위 사실을 자동으로 판별하지 않는다.

## 8. 배터리별 실제 출력과 평가

v4 baseline의 실제 반환은 5건 모두 다음 형태이며, 초안 HTML은 빈 문자열이었다.

```json
{"status":"blocked","payload":{},"claims":[],"citations":[],"payload_claims":[],"missing_inputs":[]}
```

v3 진단은 `{"status":"needs_input","missing_inputs":["role_knowledge"]}`라는
Knowledge 제어 결과를 반환했다. 본문 작성에 필요한 사용자 질문의 품질을 측정한 것이 아니다.

v11 CMP3의 실제 수락 HTML 일부:

```html
<p>오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다</p>
<!-- 중간 원문 인용 생략 -->
<h2>배경</h2>
<!-- 이후 본문과 작업 범위·작업·완료 조건 heading 및 내용 생략 -->
```

seed는 정확히 보존됐지만 미완성 비교 방향을 확인하는 질문·표시가 없다. 댓글 이어쓰기 요청을
설명문 4-section으로 확장했다. quote 안의 fixture Jira wiki link가 평문 URL로 남아
`final renderer bare URL outside anchor`도 검출됐다. 합성 문서 URL의 렌더 결함이며 실제
비밀 URL 유출 증적은 아니다. `mode=comment`, `status=ok`만으로 요청 충족을 보장하지 못했다.
나머지 4건은 `editor_assembler:invalid_plan`의 빈 blocked 결과였다. 상세 축별 판독은
[직접 평가 기록](evaluations/maxis-editor-20260914-v4.md)에 연결한다.

보존한 raw 경로:

- `.cache/agent-evaluation/maxis-editor-20260914T015535Z-55dc6e3b/baseline.json`
- `.cache/agent-evaluation/maxis-editor-20260914T015648Z-9ca2f09e/baseline.json`
- `.cache/agent-evaluation/maxis-editor-20260914T015648Z-9ca2f09e/human-review.json`
- `.cache/agent-evaluation/maxis-editor-20260914T023018Z-5dcfcc6e/candidate.json`
- `.cache/agent-evaluation/maxis-editor-20260914T023018Z-5dcfcc6e/llm-candidates.json`
- `.cache/agent-evaluation/maxis-editor-20260914T023018Z-5dcfcc6e/input-delivery.json`
- [v4 직접 판독 기록](evaluations/maxis-editor-20260914-v4.md)
- `.cache/agent-evaluation/maxis-editor-20260914T023018Z-5dcfcc6e/human-review.json`

| primary·candidate 증적 | SHA-256 |
|---|---|
| baseline 실제 반환 `baseline.json` | `9a985abd5ea48271c03ddac393a8be258dce8c4ca75b4ca8048bf828770de95e` |
| 실제 반환 `candidate.json` | `e51caf063610a2eaac7c5058023d6410d46afef7b930cde9d73502ab1d07f0a9` |
| 차단 전 `llm-candidates.json` | `d2c1e473dde9d65bb3494afdcd92bf06a05efab409ce45f529fe8e46c04b0be0` |
| 직접 평가 `human-review.json` | `e8e8817ba516613dc2c7558150f7203ceb7f475f10be9b66cff20eb45c086193` |

원시 text·trace·sentinel·credential을 git에 옮기지 않는다. 연구 문서에는 필요한 최소 발췌와
판정만 남기고 source binding·입력·전체 출력은 ignored raw에서 검토한다.

## 9. 자동 checker와 사람 판정 불일치

baseline은 자동 통과와 직접 사용 가능 판정 모두 0/5이며, 실제 반환 기준 불일치는 **0건**이다.
candidate도 두 판정 모두 0/5, 불일치 **0건**이다. Guard 수락 1/5는 별도 안전·계약 신호이므로
사용 가능 판정과의 불일치로 집계하지 않는다. 차단 전 plan의 heading 존재나 target 식별 개선을
actual 성공 분자로 넣지 않는다.

## 10. 실패·재시도·제한사항

### 실제 plan 실패의 책임 계층

직접 평가자가 원본 plan을 동결된 로컬 Assembler helper로 재생하고, 개별 block 검증으로
중첩 오류를 확인했다. 코드 수정·원격 재호출·수리한 응답의 성공 집계는 없었다.

| case | 실제 첫 실패 | 별도로 확인한 plan 결함·한계 |
|---|---|---|
| CMP2 | 원 요청에 seed_html이 없는데 seed block 생성 | JSON object 전체를 quote, `완료 조건` heading enum 불일치, 별도 작업 section 누락 |
| CMP6 | 같은 불필요 seed block | 추가 block validation 오류는 없음; 실제 mention·link 대신 작성 방법을 서술한 후보 |
| CMP8 | 같은 불필요 seed block | 추가 block validation 오류는 없음; 부모 본문에 자식 완료·현재 상태 충돌을 설명하지 않은 후보 |
| CMP9 | 같은 불필요 seed block | JSON object 전체 quote 2개; 대상 식별은 가까워졌지만 실제 구현 업무 대신 본문 작성으로 범위 이동 |
| CMP3 | Assembler 통과 | 정확 seed 보존 후에도 확인 질문 누락·description 템플릿·실제 bare URL 렌더 결함 |

네 차단 사례 모두 source ID는 존재하고 mode 결속도 맞았다. 첫 실패는 **실제 source에 없는
seed를 모델이 요청한 것**이며 context가 전송 중 누락된 것이 아니다. CMP2/CMP9의 JSON 전체
quote는 단순 ID 수정으로 해결할 수 없다. CMP6/CMP8의 다른 block이 개별 검증을 통과해도,
seed만 제거한 결과가 downstream Guard나 의미 평가를 통과한다고 추정하지 않는다.

| 항목 | 현재 기록·제한 |
|---|---|
| 구현 리뷰 | 4회 `5 → 8 → 7.5 → 9`; 평가기 개선에서 구조 변경·신뢰 경계 재검토로 이어진 코드 점수 |
| 리뷰 독립성 | 뒤 두 round는 도구 제약으로 같은 critic 재사용; 독립된 4인 검증으로 표현하지 않음 |
| 최종 코드와 리뷰 범위 | 9/10 리뷰는 assembler `fc76f38d…` 대상. 이후 지적된 empty seed·aggregate kind 충돌 2건을 수정하고 6개 테스트로 확인한 최종 pin이 `42c3983e…`다. 최종 pin의 추가 독립 리뷰를 주장하지 않음 |
| 마지막 보강 | seed 누락/중복/순서·명시 empty 충돌, aggregate kind 충돌, 제안 표시, declared fresh/partial 경계 |
| 단위 검증 | 최종 assembler 89 tests 포함 관련 274 tests 통과; 중첩 집합이므로 수량을 합산하지 않음 |
| 최종 통합 검증 | 정확한 최종 v11에서 925 tests 통과 / 84.63초, 알려진 LangChain DeprecationWarning 1개; validator 통과 |
| 이전 검증 증적 | v10 코드 동결 시 925 tests / 84.15초 및 publisher dry-run 통과; 최종 v11 측정과 구분해 보존 |
| v11 scoped 검증 | prompt 변경 후 208 tests 통과. 나머지 negative test는 size gate 선행으로 예상 오류가 달라져 동일 길이 tamper로 수정한 뒤 1개 통과; 최종 validator 통과 |
| 원격 상태 | DEV v11 3앱 canary 통과·verified=true, component 자동 통과 0/5; 기능 비활성·route 변경 없음 |
| 평가 범위 | 합성 5-case component. 실제 Jira 조사·전체 compose/UI·승인·쓰기는 미평가 |
| 반복·순서 | 1회 탐색, 고정 baseline-first; dirty worktree. clean commit/5회 순서 균형 qualification 아님 |
| URL·seed | 요청/seed의 URL은 runtime에서 가림; 링크 원문을 포함한 완전한 seed fidelity는 별도 UI/실앱 검증 필요 |
| 시간·상태 | Assembler는 ContextGuard가 선언한 fresh/partial을 소비; 원시 시스템의 최신성을 독립 증명하지 않음 |

이번 판단은 **가드 통과 1건이 생겼지만 사용 가능한 초안 0/5로 목표 미달**이다.
초안 생성 실험은 여기서 마치고 코드·측정 조건·실패 원문을 보존한다. 생성이 가능해졌다는
이유로 활성화하거나 PROD 전환하지 않는다.

| 다음 우선순위 | 수정할 책임 계층 | 확인할 성공 기준 |
|---|---|---|
| 1. 요청별 plan 계약 분리 | description/comment·seed 유무별 허용 block/schema | 없는 seed block 생성을 구조적으로 배제하고, 댓글에는 필요한 확인만; 4-heading 본문 규칙 침투 금지 |
| 2. 대상·부모와 원문 단위 분리 | evidence projection·plan 입력 | target/parent 명확 구분, verbatim fact unit 선택으로 source key·quote 범위 오류 감소 |
| 3. 참조를 typed 단위로 정규화 | 근거 전처리·reference adapter | wiki 원문 문자열을 완성 HTML로 오인하지 않고 링크·mention 원천과 결속 |
| 4. 역할별 검색·복구 UX 검증 | Knowledge/질문 정책 | no-hit·partial·blocked에서 요청과 맞는 확인/복구 안내 제공 |
| 5. 회귀·qualification | 동일 v4 및 전체 source suite | no-call을 별도 보존, clean candidate로 최소 5회·순서 균형·직접 판독 후 실앱 경로 검증 |

추가 prompt 문장 누적이나 fail 기준 완화로 성공률을 만들지 않는다. 원문 인용도 의미상
적절성과 현재 상태 일치를 직접 확인해야 하며, evidence에 expected를 넣거나 model 출력을
새 factual source로 등록하는 우회는 허용하지 않는다.

DEV 게시 receipt는 `.cache/maxis/dev-publish-quality-20260914-v10.json`에 보존했다.
Editor workflow는 `8947a278-535c-4501-a40b-14ade9ffe2b4`, Agent Core는
`b6077810-1475-4afd-80a7-40c0df0a12cb`, PMO는 `06165dfe-595f-4e14-baff-05a89195e04f`다.
3앱 import/publish 시 API key binding 동등성 검사를 통과했으며, 모델은 `gpt-5.4-mini`를
유지했다. key 값은 기록하지 않는다. DEV 게시 완료를 로컬 기능 활성화나 PROD 자격 충족으로
해석하지 않는다.

### v10 연결 canary 실패와 후속 분기 수정

`.cache/maxis/dev-canary-quality-20260914-v10.json`에서 Core·PMO는 통과했지만,
Editor는 HTTP 200이어도 `remote_status_not_ok`로 실패했다. 연결 canary의 payload schema는
EditorDraft가 아닌데 모델이 `editor-plan.v1`을 선택했다. Assembler의 non-Editor pass-through를
거친 이 응답은 변경하지 않은 ContractGuard에서 `response_fields_mismatch`로 차단됐다.
진단은 `.cache/maxis/editor-probe-v10-diagnostic.json`에 보존했다.

이는 transport 성공과 계약 성공이 다름을 보여 주는 실패이며, 본문 작성 5-case 평가 결과가
아니다. profileFingerprint `db2cb06f901ed65472bedc85fcc11f36`은 verified=false였고
`componentEvaluationStarted=false`, `routesChanged=false`였다. canary를 우회하거나 Guard를
완화해 평가를 시작하지 않았다.

후속 v11은 prompt에 `payload_schema_json.properties.contract.const`의 정확한 분기와
seed가 없을 때 seed block 금지를 명시하는 소규모 변경이다. 조립기 pin·Guard는 유지한다.
v10 실패를 새 결과로 덮어쓰지 않는다.

v11 게시 receipt는 `.cache/maxis/dev-publish-quality-20260914-v11.json`이다. workflow는
Editor `9b0d3460-904d-4578-a4d6-776639c3518f`, Agent Core
`0fe436fd-5bef-4679-911f-38c239671a33`, PMO `c396021a-36ad-4bf5-812c-e19e2bc48c78`다.
3앱 연결 canary가 통과했고 관측 지연은 각각 Editor 2437ms, Core 2421ms, PMO 2421ms였다.
profile은 verified=true지만 기능은 비활성이다. 연결 성공은 실제 본문 생성 품질과 별도다.
안전한 [v11 canary receipt](../../.cache/maxis/dev-canary-quality-20260914-v11.json)는
저장 profile의 `lastProbe` 시각 `2026-09-14T02:29:46.861519Z`와 대조했다. 최종 배포 결속과
활성화 판단은 [v11 배포 증적](../../deploy/maxis-dify/deployments/dev-editor-plan-2026.09.14.yml)에
기록했다. 이전 v10 실패 receipt와 v3/v4 raw는 그대로 보존했다.

### 구현 근거

- [`editor_assembler.py`](../../deploy/maxis-dify/code/editor_assembler.py): typed plan 조립, source·seed·상태 검증
- [`editor-plan.v1.schema.json`](../../deploy/maxis-dify/contracts/editor-plan.v1.schema.json): 정확한 plan shape
- [`validate_bundle.py`](../../deploy/maxis-dify/validate_bundle.py), [`materialize_environment.py`](../../deploy/maxis-dify/materialize_environment.py): Editor-only graph·source pin·배포 환경 검증
- [`backend.py`](../../app/agent/maxis/backend.py), [`editor_author.py`](../../app/agent/editor_author.py): runtime 요청/seed·intent source 연결
- [`run_editor_evals.py`](../../deploy/maxis-dify/run_editor_evals.py): immutable case·구조화 요청·raw·정량 집계
- [`EVALUATION.md`](../../app/agent/EVALUATION.md): versioned 비교·직접 판독·qualification 기준
