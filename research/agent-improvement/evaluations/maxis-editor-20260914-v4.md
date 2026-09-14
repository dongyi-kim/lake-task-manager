# MAXIS Editor component v4 — baseline / v11 직접 비교

## 측정 식별자

같은 v4 benchmark에서 baseline과 구조적 editor-plan v11의 각 최종 반환 5개, 내부 후보/plan 5개, 입력 근거를 모두 직접 판독했다. **실제 사용 가능 결과는 0/5 → 0/5다. 활성화하지 않는다.** Guard를 통과한 초안이 0/5 → 1/5로 늘었지만, 그 한 건도 댓글 의도·확인 표시·링크 렌더링을 충족하지 못했다.

| 필드 | baseline | v11 |
|---|---|---|
| protocolVersion | `3.0.0` | 동일 |
| rubricVersion | `2.0.0` | 동일 |
| batteryVersion | `4.0.0` | 동일 |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` | 동일 |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` | 동일 |
| runKind | `exploratory` | 동일 |
| runGroupId | `maxis-editor-20260914T015648Z-9ca2f09e` | `maxis-editor-20260914T023018Z-5dcfcc6e` |
| repetitions | `1` | 동일 |
| candidateCommit | `37cb14a9f6a5776595bbaf4937851bc2df01b823` | 동일 |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` | 동일 |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` | 동일 |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` | 동일 |
| promptVersion | `ltm-maxis-dify-2026.09.14-quality-v9` | `ltm-maxis-dify-2026.09.14-editor-plan-v11` |
| model | `gpt-5.4-mini` | 동일 |
| simpleModel | `not-used` | 동일 |
| provider | `dify` | 동일 |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` | 동일 |
| structuredOutputBackend | `dify` | 동일 |
| structuredBackendVersion | `legacy-internal` | 동일 |
| structuredBackendFallbackPolicy | `forbid` | 동일 |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` | 동일 |
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` | 동일 |
| candidateKey | `700f0e526a644767c1adecb5b2cc3c99dbf408a6f35b6aac6cdcf784517aff4d` | `a5be23cf1ecf3d3b7bdefc95649d8b838128b28fab667ecf69b0b15ccdf60951` |
| comparabilityKey | `e0492dbad042a5aaddadeb91bc54f27d6e96b5e013d5b42a97c1b86e753ed582` | `659b786bc639dc1d4043e68af25d816978199eb0ec2a6e94963956236547787e` |
| candidateOrderIndex | `1` | `2` |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` | 동일 |
| cachePolicy | `no-client-history-no-local-state` | 동일 |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` | 동일 |

## 비교 가능성 및 evidence 선택

selectionPolicy: `complete-run-no-substitution`; attemptPolicy: `retain-every-attempt`. 두 실행의 benchmarkKey, case/spec/fixture/harness hash는 같다. 서로 다른 candidateKey·comparabilityKey는 유지한다. baseline→candidate 순서의 단일 탐색 비교이며 통계적 품질 향상 또는 qualification을 주장하지 않는다.

Run metadata의 batteryVersion은 4.0.0이지만 재사용한 각 input의 component_battery_version은 2.0.0으로 기록되어 있다. 이를 고쳐 쓰거나 이전 v2 점수와 연결하지 않았다. 원본 editor suite와 전체 경로가 다르므로 직접 비교 불가(`comparableToSourceSuite=false`).

- [baseline 실제 반환](../../../.cache/agent-evaluation/maxis-editor-20260914T015648Z-9ca2f09e/baseline.json), SHA-256 `9a985abd5ea48271c03ddac393a8be258dce8c4ca75b4ca8048bf828770de95e`
- [baseline 내부 후보](../../../.cache/agent-evaluation/maxis-editor-20260914T015648Z-9ca2f09e/llm-candidates.json), SHA-256 `2924222cd559553df68e85437635501b383111639a03a04269dae40e63415579`
- [baseline 상세 직접 평가](../../../.cache/agent-evaluation/maxis-editor-20260914T015648Z-9ca2f09e/human-review.json)
- [v11 실제 반환](../../../.cache/agent-evaluation/maxis-editor-20260914T023018Z-5dcfcc6e/candidate.json), SHA-256 `e51caf063610a2eaac7c5058023d6410d46afef7b930cde9d73502ab1d07f0a9`
- [v11 내부 plan](../../../.cache/agent-evaluation/maxis-editor-20260914T023018Z-5dcfcc6e/llm-candidates.json), SHA-256 `d2c1e473dde9d65bb3494afdcd92bf06a05efab409ce45f529fe8e46c04b0be0`
- [v11 입력 전달 확인](../../../.cache/agent-evaluation/maxis-editor-20260914T023018Z-5dcfcc6e/input-delivery.json), SHA-256 `3bcae193d9af3b5cad3adef742247eb871b19d8a89b8447d53ed3db4643894fd`
- [v11 상세 직접 평가](../../../.cache/agent-evaluation/maxis-editor-20260914T023018Z-5dcfcc6e/human-review.json), SHA-256 `e8e8817ba516613dc2c7558150f7203ceb7f475f10be9b66cff20eb45c086193`

## 실행 조건

DEV 비활성 stateless editor component, synthetic fixed evidence, gpt-5.4-mini, 각 1회 반복이다. dirty candidate, 단일 평가자, 비균형 순서의 탐색 실행이므로 qualification은 불가하다. 본 판독은 추가 원격 호출·게시·코드 변경 없이 수행했다.

v11 입력 전달 기록에서 5/5 모두 start/model pack이 local pack과 일치하고 exact author request와 role_context가 전달됐다. CMP3만 실제 seed_html을 제공했다. 나머지 4건의 seed 부재는 전달 유실이 아니다. `bindingUnchanged=true`는 실험 후 binding 확인값이지 의미 품질을 증명하지 않는다.

## 배터리 범위

Editor source battery 4.0.0의 CMP2·CMP3·CMP6·CMP8·CMP9를 재사용했다. 원본 case checker는 `tools.agent_compose_eval.CASES`, 렌더 계약은 `tools.agent_eval_contracts.editor_renderer_contract_flaws`다. 고정된 EditorDraft 생성과 순수 markup projection만 포함한다. 실제 앱 compose·실시간 검색·UI·승인·게시 전체 경로가 아니며, projection의 reference resolver도 포함되지 않는다.

## 정량 결과

| 실제 반환 기준 | baseline | v11 |
|---|---:|---:|
| 전체 관측 | 5 | 5 |
| Guard 이후 status=ok 초안 | 0/5 | 1/5 (CMP3) |
| 유효한 최종 envelope (blocked 포함) | 5/5 | 5/5 |
| native checker + renderer 통과 | 0/5 | 0/5 |
| 직접 판독 사용 가능 | 0/5 | 0/5 |
| 빈 blocked HTML | 5/5 | 4/5 |
| 직접 판독 평균 / 5 | 2.00 | 2.12 |
| 자동·직접 사용 가능 판정 불일치 | 0 | 0 |
| 수신 Dify 응답 / 외부 요청 | 5 | 5 |
| 관측 전체 토큰 | 24,464 | 27,649 |
| case latency 합계 | 21.715초 | 20.372초 |

토큰은 약 13.0% 증가, case latency 합계는 약 6.2% 감소했다. 원본 metrics.durationSeconds의 반올림값은 각각 21.7초/20.4초다. 단일 실행의 변동이며 성능 개선 근거가 아니다. calls는 수신한 Dify 응답 수이지 내부 모델 step 수가 아니다. cachedTokens·costUsd는 미관측(null)이며 0으로 보지 않는다.

aggregation: rubric 2.0.0의 5축 동일 가중치 0.2, material_omission cap 3.0 적용 후 5개 case 전체 평균. CMP3만 2.60, 나머지는 2.00이라 cap이 추가로 낮추지 않는다. percentileMethod: `nearest-rank-over-all-case-attempts`. 원본 latency/usage를 보존했으며 본 판독에서 시간을 재측정하지 않았다.

## 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`.
evaluatorAgentFamily: `codex`.
evaluatorAgentModel: `current Codex agent; exact model ID not exposed`.
directRawOutputReview: `true`.
ltmLlmUsedAsJudge: `false`.
reviewerCount: `1`.
blindedReview: `false`.

동일 Codex 작업 에이전트가 실제 결과와 입력 전문을 직접 읽고 의미를 판단했다. 후보 endpoint를 judge로 사용하지 않았다. `score_human_case`와 `aggregate_human_reviews`는 checklist 완결성·상한·산술 검증에만 사용했다. 구현 참여 평가자라는 한계가 있다.

| 축 | baseline 평균 | v11 평균 | v11 CMP3 | 직접 관측 근거 |
|---|---:|---:|---:|---|
| 요청 충족 | 1.0 | 1.2 | 2 | seed는 정확하나 미완성 p95 댓글을 완결하거나 확인하지 않음 |
| 사실·근거 | 3.0 | 3.0 | 3 | 새 수치 발명은 없으나 문서/Jira 상태 차이·사실/제안 구분 설명 부족 |
| 계약·실행 | 2.0 | 2.2 | 3 | 유효하고 읽을 수 있는 부분 payload이나 삽입 전 중요 수정 필요 |
| 안전·불확실성 | 3.0 | 3.0 | 3 | 새 p95 비교를 추측하지 않음; 필요한 질문/open fact 누락 |
| 표현·렌더링 | 1.0 | 1.2 | 2 | 댓글이 4섹션 설명 템플릿으로 바뀌고 실제 wiki URL 렌더링 실패 |

네 empty blocked는 요청 결과가 없으므로 1/3/2/3/1이다. 거부된 내부 plan의 오류를 실제 응답의 환각 점수로 전이하지 않았고, 안전한 빈 결과를 성공으로 채점하지도 않았다. 사실성 3은 정확도 60%를 뜻하지 않는다. 2.00 → 2.12는 CMP3의 부분 산출물에 대한 제한적 rubric 차이일 뿐, 실사용 품질 통과가 아니다.

## 배터리·case 특수 검토요소

`input.review`의 suite 기준 2개와 case 기준 1개를 그대로 상세 JSON에 기록했다. 각 실행 5건 모두 공통 checklist 32개와 특수 요소 3개에 상태·증거를 남겼다.

| case | 특수 계약 | baseline / v11 실제 판독 |
|---|---|---|
| CMP2 | DL-9095 배경·범위·작업·DoD | 양쪽 결과 없음 |
| CMP3 | 정확한 미완성 seed + 확인 표시, 비교 방향/원인 발명 금지 | 없음 → 정확 seed 보존, 새 수치 발명 없음, 그러나 확인 표시·자연스러운 이어쓰기 없음 |
| CMP6 | 확인된 mention·2홉 100노드·resolved 문서 링크 | 양쪽 결과의 세 요소 모두 없음 |
| CMP8 | DL-9090 부모 목적·범위와 자식 책임 분리 | 양쪽 부모 본문 없음 |
| CMP9 | 현재 DL-9095 맥락, 미확인 효과 금지 | 양쪽 grounded 본문 없음 |

## 배터리별 실제 출력과 평가

baseline 5건, v11 CMP2/6/8/9의 최종 응답은 `status=blocked, payload={}, claims=[], citations=[], payload_claims=[], missing_inputs=[]`, projection은 `ok=false, needsInfo=false, error=[], html=""`이다. 전체 앱 UI의 오류 표시를 검사했다는 의미는 아니다.

| case | baseline 최종 warning / 점수 | v11 최종 warning / 점수 |
|---|---|---|
| CMP2 | citation_source_unknown / 2.00 | editor_assembler:invalid_plan / 2.00 |
| CMP3 | intent_source_fact_forbidden / 2.00 | 없음, status=ok / 2.60 |
| CMP6 | claim_not_grounded / 2.00 | editor_assembler:invalid_plan / 2.00 |
| CMP8 | claim_not_grounded / 2.00 | editor_assembler:invalid_plan / 2.00 |
| CMP9 | citation_source_unknown / 2.00 | editor_assembler:invalid_plan / 2.00 |

v11 CMP3의 실제 HTML은 정확한 아래 문장으로 시작한다.

> 오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다

그러나 이후는 측정 결과/확인 질문이 아니라 출처 목적·기록 규칙을 인용한 뒤 `배경 / 작업 범위 / 작업 / 완료 조건 (DoD)`를 작성한다. 실제 문장 중 하나는 다음과 같다.

> 제안: 이번 코멘트는 성능 측정 결과를 이어서 정리하는 용도이며, 1차 범위 밖인 3홉 이상 확장은 포함하지 않는다.

`mode=comment` 필드는 맞지만 의미상 description 템플릿이다. 새 p95 값·높음/낮음·원인·기준 충족을 발명하지 않은 점과 별개로, 요청을 완성하지 못했다. `missing_inputs=[]`이고 확인 표시도 없다. 출처의 Jira wiki 링크가 그대로 보이는 HTML로 인용되어 anchor 없이 raw URL이 남았다. `references=[]`, 실제 renderer flaw는 `final renderer bare URL outside anchor`다.

또한 `제안:` 문장 안에 프로젝트 사실과 진행 상태를 넣었다. 이 접두부나 recommendation type은 의미상 grounding을 대신하지 않는다. 이번 실제 CMP3의 관련 서술은 대체로 출처와 맞고 문서 완료 기록임을 명시하므로, 이를 곧바로 수치 환각으로 부르지는 않았다. 다만 현재 Jira의 DL-9095 In Progress와 문서의 완료 기록 차이를 설명하지 않았다.

### 내부 plan의 정확한 로컬 실패 진단 — 실제 결과와 별도

원본 plan을 현재 assembler 단계와 블록 단위에서 로컬 검증했다. 원본 수정·원격 재시도·수정 후 결과의 대체 채점은 하지 않았다. 모든 case에서 source ID 조회와 bound mode 판별, declared freshness 검사, comment seed 선행 조건은 통과했다.

| case | 최초 실행 실패 | 별도 블록 검사에서 확인된 추가 실패 |
|---|---|---|
| CMP2 | blocks[0] seed: intent JSON에 seed_html 키 없음 → _text(None) 거부 | blocks[1] JSON 객체 전체는 string leaf 인용 아님; blocks[10] h2 '완료 조건'은 enum 밖 |
| CMP3 | 없음: 제공된 seed와 모든 블록 통과 | 없음. 실제 의미·렌더 결함은 위 별도 판독 |
| CMP6 | blocks[0] seed: seed_html 키 없음 | 블록 단독 형태/인용 검사 추가 실패 없음 |
| CMP8 | blocks[0] seed: seed_html 키 없음 | 블록 단독 형태/인용 검사 추가 실패 없음 |
| CMP9 | blocks[0] seed: seed_html 키 없음 | blocks[1], [2]의 JSON 객체 전체는 string leaf 인용 아님 |

`_blocks`의 seed 처리 line 330이 absent key를 읽고 `_text` line 73에서 fail-closed한다. quote는 line 315의 exact-source-string-leaf 조건, heading은 line 320의 enum 조건에서 거부된다. normalized rstrip UTF-8 assembler SHA-256: `42c3983eb5beff78c876c8939d3798893d20630e572e89483e3d3c86abc03cb6`.

따라서 네 건의 첫 원인은 source ID나 mode 손실이 아니라 **seed가 없는 요청에도 seed block을 생성한 것**이다. CMP6/8에 추가 블록 오류가 없다는 사실은 수정된 plan이 downstream guard까지 통과한다는 뜻이 아니다.

내부 plan의 의미상 진단도 점수와 분리했다. CMP6은 mention 요청 문장 대신 “멘션을 사용하여 코멘트를 작성”하는 방법을 설명하고 실제 person marker/link가 없다. CMP8은 부모에 자식 완료 작업을 반복한다. CMP9은 일부 작업을 티켓 구현보다 본문 작성 행위로 설명한다. CMP2의 작업 heading도 빠졌다. baseline 내부 후보의 p95 값 오인 등 기존 진단은 baseline 상세 JSON에 보존했으며 새 실제 CMP3의 결함으로 전이하지 않았다.

## 자동 checker와 사람 판정 불일치

실제 반환 기준 불일치는 0건이다. baseline의 빈 결과 5건과 v11의 빈 결과 4건은 양쪽 평가가 실패로 판단했다. v11 CMP3도 원본 seed case checker와 renderer가 실패했고 직접 판독도 사용 불가로 동의했다.

Guard의 구조/근거 수락과 의미·렌더 품질 판정은 검사 목적이 다르다. `contractOk=true, status=ok`인 CMP3를 자동 checker의 통과로 오기하지 않는다. 내부 plan의 부분 구조 장점도 실제 결과 대신 채점하지 않았다.

## 실패·재시도·제한사항

이 판독 중 추가 원격 호출·코드 변경·게시를 수행하지 않았다. 원본과 이전 v2 리뷰를 덮어쓰지 않았다. 5개 request ID와 전체 case 연결, 원본 3종 SHA, benchmark/spec 동일성 및 assembler hash를 확인했다. 상세 JSON의 전체 checklist·특수 검토·산술은 protocol 검증을 통과했다.

중간 v10 canary 실패는 별도 증적으로 보존되어 있으며 이 두 5건 배치의 분모에 섞지 않는다. 이번 실행은 1회 탐색·dirty tree·단일 비맹검 구현 참여 평가자·고정 synthetic evidence에 한정된다. 실 앱 품질이나 RAG 검색 범위, 보안 suite 합격으로 일반화할 수 없다.

다음 근본개선 후보는 입력 구조에서 seed 존재·comment/description 허용 블록을 명시적으로 제한하고, JSON 구조가 아닌 source-bound 인용 단위를 제공하며, 인용 내 wiki 참조를 별도 안전한 링크 projection으로 다루는 것이다. 사실을 proposal로 분류하는 것만으로 검증을 우회해서는 안 된다. 고정 case 정답 삽입이나 기존 guard 완화 없이 새 비교·회귀가 필요하다. **이번 결과는 목표 미달이며 비활성을 유지한다.**
