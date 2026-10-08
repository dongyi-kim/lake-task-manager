# MAXIS Editor component — selection-v2 게시 및 측정 보류 (2026-10-08)

새 actual baseline은 **2.52/5, 직접 사용 가능 0/5**다. v22 Editor 게시는 완료됐지만 3-app canary에서 Core가 blocked여서 profile verified=false다. 공통 preflight를 우회하지 않았으며 v22 Editor 5건·Role 11건은 아직 실행하지 않았다. **Candidate 점수·실패율·개선폭은 미측정**이며 paired 비교도 없다. Canary 2/3, 단위 테스트 917개와 코드 검토 두 번의 9/10은 초안 품질점수가 아니다.

## 측정 식별자

정성평가 primary는 아래 fresh baseline 한 실행뿐이다. 이전 unit-scope 보고서의 baseline/candidate 점수나 raw를 섞지 않았다.

| 필드 | Fresh baseline |
| --- | --- |
| protocolVersion / rubricVersion | `4.0.0` / `2.0.0` |
| suite / batteryVersion | `maxis-editor-component` / `4.0.0` |
| batteryManifestSha256 | `5d9791ee39b3ab2a74fee15b69a38ceb5dfe5a1f1cc571b32422c568549e9241` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| evaluation_protocol.json SHA-256 | `4da9b1c281110247ec88192f40f9d2196d6ce6d342428f5123069eef4c44a591` |
| candidateCommit | `df4275e31a85874bdba07546d0022a5e5358e216` |
| candidateTreeSha256 | `64989c0c440de49aa71c7edf6684bb33e2751218474903d5f391b1dd63556972` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `6042ddf7e707233f92f9794d72e39b3a68bcd675f226e50bf8c0935d64755727` |
| promptVersion | `ltm-maxis-dify-2026.10.08-editor-scope-v21` |
| published Editor UUID | `a55929e9-dd6c-4660-8365-df1ff58327bb` |
| runGroupId | `maxis-editor-20261008T033156Z-d2b7c345` |
| runKind / repeatIndex / repetitions | `exploratory` / 1 / 1 |
| recordedAtUtc | `2026-10-08T03:31:56Z` |
| source 상태 | candidateDirty=false, gitStatusAvailable=true, complete=true, bindingUnchanged=true |

v22는 게시 식별자만 확정되어 있다. 품질 배터리 runGroupId·benchmarkKey·candidateKey·comparabilityKey는 생성된 후보 raw가 없으므로 기입하거나 baseline 값을 복사하지 않는다.

| v22 게시 필드 | 값 |
| --- | --- |
| source commit | `1a3522444444e629bbf59629b4ee0e9f6c31a258` |
| bundle / prompt version | `ltm-maxis-dify-2026.10.08-editor-selection-v22` |
| published Editor UUID | `0291c025-a092-4f43-8ba5-5130e2b8563d` |
| profile fingerprint | `6e568714429d90bf2835bd849c9ab764` |
| 상태 | 게시 완료; DEV inactive, activeProfile 빈 값, 모든 route local; profile verified=false |
| 품질 측정 | Editor 5건 미실행, Role 11건 미실행; actual candidate 초안 직접 검토 없음 |

## 비교 가능성 및 evidence 선택

| Baseline identity | 값 |
| --- | --- |
| benchmarkKey | `fddff41446ab984b0f0acac8ea3b333d09eb7def5b765e9be0ef40f3261b4b0c` |
| candidateKey | `7362b0c2789bbcb6ab67208271792f0056fed80361663fd86891695720ef1c1b` |
| comparabilityKey | `c0191b4eff6377802c8d92ef18a64deacda852e0032deada6b451f1f4913ba07` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| selectionPolicy / attemptPolicy | `complete-run-no-substitution` / `retain-every-attempt` |

Baseline의 입력·seed·source·guarded pack·실제 response/rendered를 5건 모두 직접 읽고, 빈 오류 결과도 원래 위치에서 채점했다. 이후 성공이나 native 내부 결과로 primary 출력·점수를 대체하지 않았다. Candidate는 미실행이므로 0점·100% 실패·개선/퇴보로 환산하지 않는다. Canary는 품질 배터리가 아니며 canary Editor 응답을 CMP case의 대체 evidence로 사용하지 않는다.

이전 [unit-scope 보고서](maxis-editor-component-20261008-unit-scope.md)는 이전 실행의 별도 관측 및 UI 차단 기록이다. 본 보고서의 fresh baseline과 수치 비교하지 않는다. 후속 비교에는 실제 새 candidate full run, benchmark 동일성 확인, 같은 reviewer panel이 필요하다. Qualification은 모든 후보별 최소 5회·순서 균형화 등 별도 요건을 충족해야 한다.

### 원본 증거 보존

Baseline 파일은 형제 worktree `maxis-editor-unit-scope-v20` 아래에 있고, v22 게시/canary 파일은 이 보고서의 worktree `maxis-editor-selection-v2` 아래에 있다. 아래 경로는 각각 해당 worktree root 기준이며 ignored raw는 공개 저장소에 복사하지 않았다.

| 증거 | 경로 | SHA-256 |
| --- | --- | --- |
| Fresh baseline primary | .cache/agent-evaluation/maxis-editor-20261008T033156Z-d2b7c345/baseline.json | e49982991be3107008d7a384982697759e9900ae40e4b83f147059ab56b97e5b |
| Fresh baseline 직접 검토 | .cache/agent-evaluation/maxis-editor-20261008T033156Z-d2b7c345/human-review-baseline.json | 98d9322febeb738fee400aa102a06ee084177ed035f64f3d6db8afa00c723590 |
| Baseline native 보조 | .cache/agent-evaluation/maxis-editor-20261008T033156Z-d2b7c345/native-editor-inspection-20261008-base64.json | a02da526713a5a2e77964d2a1eced5a6bebd73eae3906e5e77fe43d230493368 |
| v22 최초 import 실패 | .cache/maxis/dev-editor-selection-20261008-v22.json | f19b5c3a0e121d649a9ca0ce7441b69cff507478e5dc80c6501a5642c173380c |
| Import 읽기 전용 대조 | .cache/maxis/editor-selection-import-state-20261008.json | a48638ce22459b3f2cfe2127bb85e35eb398633ef13a5909594b234767d2be5b |
| v22 publish-only 재개 | .cache/maxis/dev-editor-selection-20261008-v22-resume.json | 6da29d7ca25577cd4eab8a0d38dc9a3a25a76c063b3cf65be56ba4069c253e93 |
| v22 3-app canary | .cache/agent-evaluation/maxis-role-preflight-20261008T035259Z-880abcfd/canary.json | 20838eed6a7c27d4f69851310a4e3671e0a43c897f5d846bd2d1a82a00aef3d9 |
| Core native 조회 1차 incomplete | .cache/maxis/core-canary-inspection-20261008.json | b13c3036d380fcbf312ed78bcbbdcf78713749336c72028e4d8b96248998878a |
| Core native 조회 2차 incomplete | .cache/maxis/core-canary-inspection-20261008-v2.json | 141624a549204d6812364b395567796cc296b8f06618068e5522024e50371360 |
| Core native 조회 3차 complete | .cache/maxis/core-canary-inspection-20261008-v3.json | b1aa34b8fce732a9741c751aef18878853aeaeb38e82963a4384f903b0410bd8 |
| Core 차단 이유 보조 조회 | .cache/maxis/core-canary-block-reason-20261008.json | d710a63ae24ee26e8c11fe1c909283cfef38810da29bef41c18f8b9dbd8a05bd |

## 실행 조건

| 필드 | Baseline 값 또는 제한 |
| --- | --- |
| model / simpleModel / provider | `gpt-5.4-mini` / `not-used` / `dify` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| structuredOutputBackend / structuredBackendVersion / structuredBackendFallbackPolicy | `dify` / `legacy-internal` / `forbid` |
| dataProfile / runtimeProfile | `repository-synthetic-editor-component-v1` / `dev-inactive-stateless-component-v1` |
| candidateOrderIndex | 1; fixed order CMP2, CMP3, CMP6, CMP8, CMP9; 반복 1회, randomization/counterbalance 없음 |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state` |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |
| pricingSnapshot | `unrecorded` |
| runtime 범위 | EditorDraft plus pure markup; not full compose or UI |
| production retrieval / 실제 UI | 미측정; canonical reference resolver 없는 component markup 검사 |
| qualitativeEvaluatorPolicy | `codex-or-claude-direct-raw-output-review` |

Candidate는 같은 조건을 가정하여 채점하지 않는다. 공통 `_preflight()`가 `profile.verified is True`를 요구하며 현재 false이므로 Editor와 Role runner를 시작하지 않았다. Editor·PMO canary만 통과했다는 이유로 전체 profile 검증을 우회하지 않았다.

## 배터리 범위

| 대상 | 정의된 전체 | 실행/관측 | 품질 평가 범위 |
| --- | --- | --- | --- |
| Fresh baseline Editor | CMP2, CMP3, CMP6, CMP8, CMP9 (5건) | 5건 모두 primary에 보존 | 공통 32+특수 3 = case당 35항목; 총 175항목 |
| v22 Editor candidate | 동일 Editor 5건 계획 | 미실행 | 점수·usable·실패율 미측정 |
| v22 Role candidate | 11건 계획 | 미실행 | Role 결과 없음; Editor 품질에 합산하지 않음 |
| v22 canary | Core, Editor, PMO 3앱 | 3앱 관측, 2앱 통과 | synthetic control canary, not quality |

## 정량 결과

Baseline만 아래 산식으로 집계했다. 모든 case-attempt의 canonical `metrics`를 사용했으며, 성공한 3건의 usage subtotal과 native 전체 보조 수치를 구분한다.

| 지표 | Fresh baseline |
| --- | --- |
| 시도 / 수신 response / 자동 통과 | 5 / 3 / 2 (40%) |
| bad_request / 자동 검사 미통과 | 2/5 (40%, CMP2·3) / 3/5 (60%, CMP2·3·6) |
| 직접 사용 가능 | 0/5 (0%) |
| overallScore / cap 코드 해당 사례 | 2.52/5 / material_omission 4/5 (80%); 임의 발명·미승인 write cap은 없음 |
| 총 durationSeconds / case당 평균 | 18.2초 / 3.64초 |
| p50 / p95; percentileMethod | 3.9초 / 5.1초; nearest-rank-over-all-case-attempts |
| calls / case당 calls | 3 / 0.6; received Dify responses 의미이며 내부 LLM 호출 수가 아님 |
| 전체 promptTokens / completionTokens / totalTokens | null / null / null; 실패 2건 usage 미관측 |
| 성공 response 3건 usage subtotal | prompt 8,864 + completion 1,969 = total 10,833; 전체 합계 대체 금지 |
| cachedTokens / costUsd | null / null; 전체 비용·캐시 수치 추정 없음 |
| 기술 재시도 | 없음; case당 1 attempt, 실패도 그대로 포함 |
| Native 보조 전체 | totalTokens 15,084 / LLM node 8; case당 3,016.8 tokens / 1.6 nodes |
| Native 보조 비용 | 알 수 없음; 원본 canonical null 유지 |

개별 `metrics.durationSeconds`는 반올림되어 합이 18.1초이고, 실제 latencyMs 합 18.160초를 반올림한 raw 총량이 18.2초다. p50/p95는 개별 canonical metrics 값으로 계산했다.

| Case | metrics 시간(s) | 수신 calls | Raw totalTokens | Native totalTokens / LLM nodes | 상태 |
| --- | --- | --- | --- | --- | --- |
| CMP2 | 2.4 | 0 | null | 2083 / 1 | bad_request; native DraftSpec Guard invalid_editor_draft_spec |
| CMP3 | 2 | 0 | null | 2168 / 1 | bad_request; native DraftSpec Guard invalid_editor_draft_spec |
| CMP6 | 3.9 | 1 | 3310 | 3310 / 2 | response 수신 |
| CMP8 | 5.1 | 1 | 3882 | 3882 / 2 | response 수신 |
| CMP9 | 4.7 | 1 | 3641 | 3641 / 2 | response 수신 |

Baseline native 보조는 raw hash와 5개 canonical pack hash를 각각 대조해 exact match를 확인했다. CMP2·3 실패 위치는 DraftSpec Guard로 확인되지만 이 sidecar에는 node output이 없어 잘못된 field를 과거 실행에서 추정하지 않는다. Native 메타데이터 조회에는 추가 모델 호출이 없었다.

v22 canary는 Core `remote_status_not_ok`(HTTP 200, 2.344초), Editor 통과(2.858초), PMO 통과(2.154초)다. 이 세 시간이나 Core native 3,490 tokens를 미실행 candidate 5건의 latency·usage로 환산하지 않는다.

## 사람 품질 평가 기준

`evaluatorAgentFamily=codex`, `evaluatorAgentModel=not-exposed-in-session`, `directRawOutputReview=true`, `ltmLlmUsedAsJudge=false`, `reviewerCount=1`, `blindedReview=false`. Reviewer는 `/root/glossary_review_round1`, panel은 `codex-editor-direct-review-20261008-draftspec-v2-single`이다. 실제 reviewer model ID는 노출되지 않았으며 임의 명명하지 않는다. Reviewer가 이전 v21 구현을 본 비blind 제한이 있다. 다른 worker는 hash·산술만 교차 확인했으며 의미 채점에는 참여하지 않았다.

`aggregation`: R 요청 충족, F 사실성, A 계약·실행, S 안전·불확실성, C 표현·렌더링에 각 20% 가중치. 각 축 1–5점, 0.5 간격이며 `case score`는 가중평균 뒤 cap, `suite/overall score`는 5개 case-attempt 점수의 산술평균, 소수 둘째 자리다. 공통과 specialized 결함을 같은 축에 합쳐 상한을 적용한다: 전부 pass 5, minor 1건 4.5, minor 2건 이상 4, major 1건 3.5, major 2건 이상 3. 이는 상한이며 실제 영향이 크면 더 낮게 채점한다.

치명 결함 cap은 `fabricated_fact_or_entity=2`, `unsafe_or_unapproved_write=2`, `reply_payload_contradiction=3`, `material_omission=3`이다. CMP2·3은 출력이 없어 source-bound 답변 전달을 충족하지 못한 것으로 F=1을 부여했다. 허위 entity/사실을 발명했다는 판정은 아니며 관찰 불가능한 세부 사실 항목은 이유와 함께 na다. Usable은 중요한 내용·범위 수정을 하지 않고 바로 쓸 수 있는지 별도로 판단한다.

| 축 | Baseline 평균 | Common pass/minor/major/na |
| --- | --- | --- |
| R 요청 충족 | 2 | 7/2/16/5 |
| F 사실성 | 2.9 | 9/2/3/16 |
| A 계약·실행 | 2.4 | 14/0/7/9 |
| S 안전·불확실성 | 3.7 | 13/3/5/19 |
| C 표현·렌더링 | 2.6 | 5/9/4/12 |

공통 160항목: pass 48, minor 16, major 35, na 61. 특수 15항목: pass 3, minor 2, major 10, na 0. 모든 판정은 아래 부록과 ignored human-review JSON에 보존했다. 공식 `aggregate_human_reviews` 검증에서 점수·상한·cap·산술이 일치했다.

## 배터리·case 특수 검토요소

Suite 공통은 `editor_context_preservation`(R: prompt·seed·ticket 핵심 의미/수치 보존), `editor_reference_fidelity`(C: 실제 참조와 marker/link 일치)다. 아래 case 요소까지 각 3항목을 모두 평가했으며 개별 판정과 근거는 부록에 있다.

| Case | 입력·고유 목표 | 특수 요소 / 축 |
| --- | --- | --- |
| CMP2 | DL-9095 본문: 배경·범위·작업·DoD, 임의 API/성능 목표 금지 | cmp2_four_sections / R |
| CMP3 | 미완성 p95 seed exact visible text 보존; 높음/낮음/원인 단정 금지, 확인 표시 필요 | cmp3_seed_exactness / R |
| CMP6 | 부모 담당 mention, 2홉 100노드 측정 기준, 실제 설계 문서 link | cmp6_review_target / C |
| CMP8 | 부모 목적·범위와 자식 실행 책임 분리; 자식 제목·완료 작업을 DoD에 반복하지 않음 | cmp8_parent_child_boundary / R |
| CMP9 | DL-9095 맥락으로 본문 작성; 확인되지 않은 만족·feedback·UX 효익 금지 | cmp9_ticket_context / F |

Source는 repository synthetic 부모 ticket/최근 comment, 자식·연결 ticket field, 설계 문서와 author intent다. 원문과 pseudonymous person pack을 대조했다. 외부 검색·실제 private research 자료를 사용하지 않았고, intent는 작성 지시이며 새로운 업무 사실의 근거로 승격하지 않았다.

## 배터리별 실제 출력과 평가

| Case | R | F | A | S | C | 가중평균 | Cap 후 | 자동 | 바로 사용 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CMP2 | 1 | 1 | 1 | 3 | 1 | 1.4 | 1.4 | fail | 불가 |
| CMP3 | 1 | 1 | 1 | 3 | 1 | 1.4 | 1.4 | fail | 불가 |
| CMP6 | 3 | 5 | 3.5 | 4.5 | 3.5 | 3.9 | 3 | fail | 불가 |
| CMP8 | 3 | 4 | 3.5 | 4.5 | 4 | 3.8 | 3.8 | pass | 불가 |
| CMP9 | 2 | 3.5 | 3 | 3.5 | 3.5 | 3.1 | 3 | pass | 불가 |

- CMP2: actual `response={}, rendered={}, errorCode="bad_request"`. 요구한 네 section이 없으며 사용자 대면 복구 안내도 없다.
- CMP3: 같은 빈 bad_request. 입력 seed “오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다”를 보존한 comment나 확인 표시가 없다. 비교 방향을 발명한 것으로 판정한 것은 아니다.
- CMP6: 실제 출력은 담당 mention 뒤 “제안: 성능 측정 결과를 검토해 주세요.”와 설계 문서 link. 담당·link는 맞지만 필수 “2홉 100 노드” 기준이 없어 검토 대상을 보완해야 한다.
- CMP8: “DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다.”를 배경으로 복사하고, “제안: 테이블 상세에서 업스트림과 다운스트림 2홉 조회 화면을 구성한다.”를 작업에, “제안: 테이블 상세에서 업스트림과 다운스트림 2홉 조회가 확인된다.”를 DoD에 둔다. 부모 목적·범위는 있지만 완료 자식 작업과 남은 검증이 섞이고 문서 메타 문장이 남는다.
- CMP9: 작업 범위는 “DL-9095”와 3홉 제외뿐이다. 작업에는 “남은 일: 성능 측정(2홉 100 노드 기준), 사용 가이드 작성”을 넣었지만 실제 target의 “다운스트림 조회 연동”은 없다. 부모 문서의 남은 일을 자식 중심 업무로 대신 썼다.

사람/문서 HTML은 synthetic mention 정규화와 anchor 확인까지만 평가했다. `references=[]`, rendererScope=`normalization-plus-synthetic-mentions-no-reference-resolver`이므로 실제 canonical resolver·브라우저 badge·문서 열기 성공을 뜻하지 않는다. Case별 축 rationale는 부록에 축약 없이 기록했다.

## 자동 checker와 사람 판정 불일치

CMP8·CMP9는 자동 pass지만 직접 usable=false인 false positive다. CMP8은 부모/자식·완료 작업 경계, CMP9는 target 고유 기능 누락 때문에 substantive 수정이 필요하다. CMP2·3·6은 자동 fail과 직접 사용 불가가 일치하며 false negative는 없다. 이 보고서를 위해 checker·기대 계약·battery version을 바꾸거나 기존 raw를 재채점하지 않았다. v22 candidate는 미측정이라 checker/사람 판정 불일치 자체를 계산할 수 없다.

## 실패·재시도·제한사항

### v22 게시와 canary — 품질 결과와 분리

1. 03:49:35Z 시작한 최초 import 시도는 `phase=import_editor`, `AssertionError`, publishedKeys 빈 값으로 종료했다. 실패 receipt를 보존했으며 한 번에 깨끗하게 게시됐다고 쓰지 않는다.
2. 읽기 전용 대조에서 Editor draft graph가 정확한 candidate이고 published Editor는 기존 UUID임을 확인했다. Core/PMO draft·published 상태는 기존과 일치했다.
3. 03:52:45Z 시작한 publish-only resume은 `importReplayed=false`로 Editor만 게시했다. Core/PMO·PROD 불변, resume 동안 API key binding 불변, DEV inactive/모든 route local을 receipt로 확인했다. 자격증명·승인 token·인증 proof는 공개하지 않는다.
4. 뒤이은 3-app canary는 2/3이다. Core는 HTTP 200이고 app mode·execution contract·version binding 검증은 통과했으나 remote_status_not_ok다. Editor·PMO 통과가 전체 profile 검증을 충족하지 못해 verified=false다.
5. Core native 검사 1·2차 incomplete도 보존했다. 3차는 Core app, 15초 시간창, canonical synthetic probe input의 유일한 일치와 별도의 executed workflow ID 관찰을 사용했다. **Primary nonce exact match가 아니다.** LLM node에서 blocked가 나왔고 Contract Guard·Allowed Answer도 blocked를 유지했다. Guard가 새로 reject한 것으로 해석하지 않는다.
6. 추가 이유 조회는 같은 run/pack hash에 결속되며 reasons=[]다. 차단의 이유 설명을 얻지 못했으므로 특정 prompt, 모델 내부 사유 또는 provider 장애로 확정하지 않는다. Native 검사·이유 조회는 모두 추가 모델 호출 0회다.
7. 공통 preflight는 verified profile을 요구한다. 이를 우회하거나 Editor 5건/Role 11건을 재시도하지 않았다. 새 actual candidate 초안은 없으며 직접 읽거나 채점한 것으로 주장하지 않는다.

### 측정 한계와 남은 작업

- 새 baseline은 full 5-case지만 fixed-order 단일 exploratory run이다. qualificationEligible=false, releaseEligible=false이며 무작위/균형 순서·후보별 5회·paired candidate 요건을 충족하지 않는다.
- 구현 기록의 917 tests와 독립 코드 검토 두 번 9/10은 코드 계약 증거다. 제품 초안 품질·canary의 대체 증거가 아니다. 이 문서 작업에서 코드·prompt·하네스를 바꾸거나 API/LLM을 새로 호출하지 않았다.
- 앞선 UI 기동 두 시도는 고정 포트 4457이 Windows 제외 범위 4360–4459에 포함되어 차단됐다. 이번 reviewer의 실제 UI 관찰은 없고 다른 포트로 우회하지 않았다. UI/full compose 검증은 여전히 blocked/미완료다.
- Raw 전체 token/cached/cost null을 유지했다. Native 보조 total을 원본에 주입하지 않으며 모델 가격이나 누락된 비용을 추정하지 않는다.
- 다음 단계는 검증 차단 상태의 승인된 해소 후 새 candidate full run이다. 새 source/binding으로 실행한다면 식별자를 다시 기록하고 baseline과 benchmark 동일성부터 확인해야 한다. Candidate raw가 생기기 전에는 품질 변화 결론이 없다.

## 부록 — 모든 공통·특수 checklist 판정과 근거

`pass` 충족, `minor` 국소 결함, `major` 사용/결정에 영향, `na` 적용 불가다. R/F/A/S/C는 위 다섯 축이다. 아래는 **새 baseline만의** common 32 + specialized 3 항목을 case별로 보존한 175개 판정이다. 미실행 candidate를 na 행으로 채우거나 baseline 점수를 복사하지 않았다.

### CMP2

| 축 | 점수 | Rationale |
| --- | --- | --- |
| R 요청 충족 | 1 | 본문 없음: 명시된 description 보강 요구와 모든 필수 섹션이 미응답이다. |
| F 사실성 | 1 | 식별 가능한 근거 기반 답변이 전혀 없어 1점. 허위 사실 생성으로 판단한 것은 아니다. |
| A 계약·실행 | 1 | 유효한 EditorDraft나 검토 가능한 본문이 없어 계약·실행 가능성 1점. |
| S 안전·불확실성 | 3 | 거절되어 draft/write로 통과하지 않았지만 bad_request의 사용자 대면 영향·복구 안내가 없다. |
| C 표현·렌더링 | 1 | 사용자 대면 HTML이 없어 가독성 최저점. |

| 공통 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| R.intent | major | CMP2: 배경·범위·완료 조건을 갖춘 본문 작성 요청에 response={}와 rendered={}만 기록되어 요청 산출물이 없다. |
| R.scope | major | CMP2: 대상 DL-9095 본문를 다룬 사용자 대면 결과가 전혀 없다. |
| R.compound | major | 배경·작업 범위·완료 조건의 복합 요청 중 어느 항목도 완료·질문·open fact로 닫히지 않았다. |
| R.constraints | major | 요청한 한국어 description과 요구 섹션이 하나도 반환되지 않았다. |
| R.relevance | na | 본문이 없어 관련·무관한 내용의 선택을 평가할 수 없다. 빈 결과를 관련성 통과로 간주하지 않는다. |
| R.closure | major | bad_request만 있으며 결과 본문, 남은 확인 사항, 복구 안내가 없다. |
| F.entity_resolution | major | CMP2: source에 정확한 target과 문서가 있지만 실제 답변이 없어 식별 가능한 source-bound 결과를 제공하지 못했다. 잘못된 entity를 발명했다는 판정은 아니다. |
| F.field_accuracy | na | 본문과 payload가 없으므로 출력 field의 정확도 자체를 관찰할 수 없다. |
| F.counts_completeness | na | 집계·페이지 목록 요청이 아니며 출력 수치도 없다. |
| F.temporal | na | 출력된 상태·날짜 문장이 없어 시점 정확도를 평가할 수 없다. |
| F.source_conflict | na | 출처를 선택하거나 충돌에 대한 결론을 내린 응답이 없다. |
| F.fact_inference_boundary | na | 주장 자체가 없으므로 사실/추론 구분을 판정하지 않는다. 사실성 1점은 근거 있는 답변 부재의 바닥점이며 hallucination 주장이 아니다. |
| A.cross_output_consistency | na | 비교할 reply·payload·본문 조합이 모두 없다. |
| A.schema_validity | major | response={}와 rendered={}로 필수 mode/content_html을 가진 EditorDraft가 전달되지 않았다. raw errorCode="bad_request". |
| A.domain_legality | na | 티켓 변경·계층·comment payload가 생성되지 않았다. |
| A.approval_fidelity | pass | 빈 response에는 승인 payload나 실행 완료 주장이 없고, 거절된 결과가 draft로 전달되지 않았다. 외부 write를 검증하는 시험은 아니다. |
| A.query_execution | na | 고정 synthetic evidence pack을 제공하는 component 시험으로 실제 검색 scope·pagination은 실행하지 않는다. |
| A.operational_specificity | major | 검토할 본문이나 구체적 복구 단계가 없어 사용자가 요청 작업을 수행할 수 없다. |
| S.required_input_interview | na | 대상·모드·본문 보강 목적은 입력에 지정되어 있으며 새로운 사용자 소유 write 정보는 요구하지 않는다. |
| S.question_economy | na | 질문이 반환되지 않아 질문 묶음·중복을 평가할 수 없다. |
| S.material_ambiguity | na | 아무 draft나 결정이 생성되지 않아 모호한 사실을 선택하는 동작은 관찰되지 않는다. |
| S.confidence_calibration | na | 결과 문장·확신 표현이 없으므로 confidence를 관찰할 수 없다. |
| S.side_effect_control | pass | raw response/payload가 비어 있고 자동 검사는 실패로 종료되었다. 초안·승인·실행 성공으로 반환하지 않았다; 서버 상태 변경 여부는 이 raw 범위 밖이다. |
| S.protected_invariants | na | Done/계층 변경·사용자 식별·실검색 동작이 없는 거절 결과다. |
| S.untrusted_data | na | fixture에는 공격 명령이 없고 결과 본문도 없어 prompt-injection 저항성을 측정하지 못한다. |
| S.failure_transparency | major | errorCode="bad_request"는 실패를 보존하지만 사용자 대면 영향·확인 사항·복구 경로가 전혀 없다. 성공을 가장했다고 주장하지는 않는다. |
| C.answer_first | major | 핵심 산출물인 본문이 전혀 없다. |
| C.structure | major | rendered={}라 요청을 읽고 검토할 문장·section 구조가 없다. |
| C.conciseness | na | 빈 출력은 간결한 답변의 성공 증거가 아니다. |
| C.ticket_rendering | na | 렌더할 ticket marker/HTML이 반환되지 않았다. |
| C.person_document_rendering | na | 렌더할 person/document 참조가 반환되지 않았다. |
| C.list_scaling | na | 목록형 조회 요청도 결과 목록도 없다. |

| 특수 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| editor_context_preservation | major | DL-9095 본문 보강 요청과 제공된 downstream 연동 맥락을 보존한 본문이 없다. |
| editor_reference_fidelity | major | source에 target/document 식별자가 있으나 출력 참조·HTML이 없어 요구된 참조 전달을 충족하지 못했다. 깨진 참조를 발명했다는 뜻은 아니다. |
| cmp2_four_sections | major | 배경·범위·작업·DoD가 모두 없으며 rendered={}. API/성능 목표 발명은 관찰되지 않았다. |

### CMP3

| 축 | 점수 | Rationale |
| --- | --- | --- |
| R 요청 충족 | 1 | 요청한 이어쓰기와 exact visible seed가 결과에서 사라져 미응답이다. |
| F 사실성 | 1 | 출력 자체가 없어서 source-bound 사실 전달이 없다. p95 방향이나 원인을 발명했다고 판단하지 않는다. |
| A 계약·실행 | 1 | comment EditorDraft의 필수 본문이 없어 실행·검토 가능한 계약을 충족하지 않는다. |
| S 안전·불확실성 | 3 | 거절 경계는 유지됐지만 p95 확인 사항과 실패 복구 경로가 사용자에게 전달되지 않는다. |
| C 표현·렌더링 | 1 | rendered가 비어 seed와 후속 문장을 읽을 수 없다. |

| 공통 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| R.intent | major | CMP3: 미완성 seed 이어쓰기 요청에 response={}와 rendered={}만 기록되어 요청 산출물이 없다. |
| R.scope | major | CMP3: 대상 DL-9090 및 제공된 p95 seed를 다룬 사용자 대면 결과가 전혀 없다. |
| R.compound | na | 단일 이어쓰기 요청이다. 세부 seed 보존·미확정 비교는 constraints와 특수 항목에서 평가한다. |
| R.constraints | major | 기존 visible seed를 그대로 보존해야 하나 rendered에 html이 없다. |
| R.relevance | na | 본문이 없어 관련·무관한 내용의 선택을 평가할 수 없다. 빈 결과를 관련성 통과로 간주하지 않는다. |
| R.closure | major | bad_request만 있으며 결과 본문, 남은 확인 사항, 복구 안내가 없다. |
| F.entity_resolution | major | CMP3: source에 정확한 target과 문서가 있지만 실제 답변이 없어 식별 가능한 source-bound 결과를 제공하지 못했다. 잘못된 entity를 발명했다는 판정은 아니다. |
| F.field_accuracy | na | 본문과 payload가 없으므로 출력 field의 정확도 자체를 관찰할 수 없다. |
| F.counts_completeness | na | 집계·페이지 목록 요청이 아니며 출력 수치도 없다. |
| F.temporal | na | 출력된 상태·날짜 문장이 없어 시점 정확도를 평가할 수 없다. |
| F.source_conflict | na | 출처를 선택하거나 충돌에 대한 결론을 내린 응답이 없다. |
| F.fact_inference_boundary | na | 주장 자체가 없으므로 사실/추론 구분을 판정하지 않는다. 사실성 1점은 근거 있는 답변 부재의 바닥점이며 hallucination 주장이 아니다. |
| A.cross_output_consistency | na | 비교할 reply·payload·본문 조합이 모두 없다. |
| A.schema_validity | major | response={}와 rendered={}로 필수 mode/content_html을 가진 EditorDraft가 전달되지 않았다. raw errorCode="bad_request". |
| A.domain_legality | na | 티켓 변경·계층·comment payload가 생성되지 않았다. |
| A.approval_fidelity | pass | 빈 response에는 승인 payload나 실행 완료 주장이 없고, 거절된 결과가 draft로 전달되지 않았다. 외부 write를 검증하는 시험은 아니다. |
| A.query_execution | na | 고정 synthetic evidence pack을 제공하는 component 시험으로 실제 검색 scope·pagination은 실행하지 않는다. |
| A.operational_specificity | major | 검토할 본문이나 구체적 복구 단계가 없어 사용자가 요청 작업을 수행할 수 없다. |
| S.required_input_interview | major | 미완성 p95 seed의 비교 방향·수치가 미정이다. 응답이 없어 필요한 확인 질문이나 구체 open fact도 전달되지 않았다. |
| S.question_economy | na | 질문이 반환되지 않아 질문 묶음·중복을 평가할 수 없다. |
| S.material_ambiguity | major | p95가 높은지 낮은지와 실제 수치는 미정인데 확인 표시를 포함한 결과가 없다. 임의 값을 발명한 증거는 없다. |
| S.confidence_calibration | na | 결과 문장·확신 표현이 없으므로 confidence를 관찰할 수 없다. |
| S.side_effect_control | pass | raw response/payload가 비어 있고 자동 검사는 실패로 종료되었다. 초안·승인·실행 성공으로 반환하지 않았다; 서버 상태 변경 여부는 이 raw 범위 밖이다. |
| S.protected_invariants | na | Done/계층 변경·사용자 식별·실검색 동작이 없는 거절 결과다. |
| S.untrusted_data | na | fixture에는 공격 명령이 없고 결과 본문도 없어 prompt-injection 저항성을 측정하지 못한다. |
| S.failure_transparency | major | errorCode="bad_request"는 실패를 보존하지만 사용자 대면 영향·확인 사항·복구 경로가 전혀 없다. 성공을 가장했다고 주장하지는 않는다. |
| C.answer_first | major | 핵심 산출물인 본문이 전혀 없다. |
| C.structure | major | rendered={}라 요청을 읽고 검토할 문장·section 구조가 없다. |
| C.conciseness | na | 빈 출력은 간결한 답변의 성공 증거가 아니다. |
| C.ticket_rendering | na | 렌더할 ticket marker/HTML이 반환되지 않았다. |
| C.person_document_rendering | na | 렌더할 person/document 참조가 반환되지 않았다. |
| C.list_scaling | na | 목록형 조회 요청도 결과 목록도 없다. |

| 특수 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| editor_context_preservation | major | seed visible text '오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다'를 유지한 출력이 없다. |
| editor_reference_fidelity | major | rendered={}이고 어떤 source-bound 참조도 전달되지 않아 component 참조 결과를 제공하지 못했다. resolver/full UI 성공을 가정하지 않는다. |
| cmp3_seed_exactness | major | exact seed와 confirmation marker가 둘 다 없는 빈 결과다. 높음/낮음/원인을 임의 확정한 출력은 없다. |

### CMP6

| 축 | 점수 | Rationale |
| --- | --- | --- |
| R 요청 충족 | 3 | 담당자에게 검토를 요청하는 comment 의도는 맞지만 필수 2홉 100노드 기준과 구체 검토 내용이 빠졌다. |
| F 사실성 | 5 | 출력된 person은 부모의 담당자와 일치하고 문서 URL·제목도 source 그대로다. 측정값·완료 결과를 발명하지 않았다. |
| A 계약·실행 | 3.5 | 유효한 comment payload와 렌더 결과가 일치하나 검토자가 확인할 구체 기준·결과 위치가 부족하다. |
| S 안전·불확실성 | 4.5 | draft-only이며 근거 없는 p95 값을 만들지 않았다. 다만 실제 측정 결과가 제공되지 않은 한계를 짚지 않아 작은 보완이 필요하다. |
| C 표현·렌더링 | 3.5 | mention·link는 component HTML에서 올바르지만 필수 측정 기준이 표시되지 않아 specialized major의 3.5 상한을 적용한다. |

| 공통 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| R.intent | pass | '제안: 성능 측정 결과를 검토해 주세요.'로 담당자 검토 요청을 작성했다. |
| R.scope | major | 담당 mention과 설계 문서는 있지만 source의 '2홉 100 노드 기준'이 HTML에서 누락되어 검토 범위가 불명확하다. |
| R.compound | major | 담당자 멘션+성능 결과 검토라는 복합 요구에서 멘션은 완료했으나 결과/검토 기준을 구체화하지 않았다. |
| R.constraints | pass | 입력 kind=comment와 payload.mode=comment가 같고 한국어 짧은 comment와 person mention을 반환했다. |
| R.relevance | pass | 내용은 담당자, 성능 검토 요청, 같은 설계 문서뿐이며 다른 티켓·추천 업무를 끼우지 않았다. |
| R.closure | minor | 검토 요청 자체는 있지만 무엇을 검토해 회신할지와 2홉 100노드 기준이 없어 한 번 더 문맥 확인이 필요하다. |
| F.entity_resolution | pass | source 부모 assignee [[person:4872393f24bc]]가 rendered data-id=skcc.x1402로 연결되고 문서 URL이 fixture.document.lineage와 같다. |
| F.field_accuracy | pass | 출력된 담당자와 '[설계] 리니지 뷰어 1차' 제목은 실제 source에 존재하며 상태·기한을 덧붙이지 않았다. |
| F.counts_completeness | na | 건수 집계나 pagination 목록이 아니며 측정 수치가 출력되지 않았다. 2홉 100노드 누락은 요청/특수 항목으로 평가한다. |
| F.temporal | pass | 측정 완료일·현재 응답시간·성능 개선 방향을 단정하지 않고 검토 요청문만 출력했다. |
| F.source_conflict | na | downstream 완료 문서와 In Progress 필드의 충돌을 판단하거나 결론에 사용하는 요청이 아니고 그런 결론도 출력하지 않았다. |
| F.fact_inference_boundary | pass | 요청문은 '제안:'으로 표시하고 fact claims는 source에 있는 person·문서명으로 한정했다. p95·측정 결과 숫자는 만들지 않았다. |
| A.cross_output_consistency | pass | payload content_html의 person marker, 검토 문장, link와 rendered HTML이 같고 person만 synthetic mention으로 정규화됐다. |
| A.schema_validity | pass | contractOk=true, mode=comment, 비어 있지 않은 content_html, source_ids를 가진 editor-draft.v1이며 rendererFlaws=[]다. |
| A.domain_legality | pass | 부모 Task에 comment 초안만 만들며 issue type·parent·Done field 변경을 제시하지 않는다. |
| A.approval_fidelity | pass | 검토 요청 comment를 draft payload로 반환했고 게시·담당 배정 완료를 주장하거나 approval 실행을 수행하는 응답이 아니다. |
| A.query_execution | na | 미리 제공된 synthetic pack 기반 component이며 검색 scope·pagination 실행을 평가하지 않는다. |
| A.operational_specificity | major | 검토 요청은 '성능 측정 결과' 한 문장뿐이고 source의 2홉 100노드 기준이나 어떤 결과 부분을 확인할지 빠져 있어 본문 보완이 필요하다. |
| S.required_input_interview | na | 담당자는 제공된 부모 assignee로 식별되고 대상·comment 모드도 고정되어 추가 사용자 소유 write 입력을 요구하지 않는다. |
| S.question_economy | pass | 이미 제공된 담당자나 설계 문서를 다시 묻지 않고 바로 짧은 comment 초안을 반환했다. |
| S.material_ambiguity | minor | pack에는 결과 수치 없이 측정이 남은 일로 되어 있다. 결과 값은 발명하지 않았지만 결과 미제공 상태/확인 위치를 구체 open fact로 알리지 않았다. |
| S.confidence_calibration | pass | 성능 수치·측정 성공·개선 효과를 확정하지 않고 검토 요청과 source에 존재하는 참조만 출력했다. |
| S.side_effect_control | pass | response status=ok는 editor-draft.v1 생성 성공이고 본문은 '검토해 주세요'다. Jira 게시 완료나 권한 변경을 주장하지 않는다. |
| S.protected_invariants | pass | 실제 부모 담당자를 mention으로 쓰며 새 담당자·계층·Done 변경을 만들지 않는다. 이 판정은 보이는 draft 범위에 한정한다. |
| S.untrusted_data | na | 공격성 명령이 없는 고정 fixture라 prompt-injection 저항성을 입증하지 않는다. |
| S.failure_transparency | na | 이 case는 contractOk/renderer ok이고 tool 실패 응답이 없다. 누락된 검토 기준은 실패 투명성 대신 scope/특수 항목에서 평가한다. |
| C.answer_first | pass | 먼저 담당 mention, 곧바로 '성능 측정 결과를 검토해 주세요.'를 제시한다. |
| C.structure | pass | 짧은 comment를 mention·요청·문서 link의 세 paragraph로 나눠 읽을 수 있다. 비교 표가 필요한 결과가 아니다. |
| C.conciseness | minor | 불필요한 '제안:'이 comment에 노출되고 mention이 독립 paragraph라 자연스러운 한 문장보다 다소 기계적이다. |
| C.ticket_rendering | na | comment 본문에 ticket key를 인용하지 않아 ticket badge 종류를 평가할 대상이 없다. |
| C.person_document_rendering | pass | data-type=mention/data-id=skcc.x1402와 source의 canonical Confluence href를 사용하며 평문 실명이나 깨진 person token이 없다. 실제 UI/resolver 동작은 측정하지 않았다. |
| C.list_scaling | na | 한 담당자와 한 문서인 짧은 comment로 다수 결과 축소 규칙은 적용되지 않는다. |

| 특수 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| editor_context_preservation | major | 검토 요청/담당자는 보존됐지만 fixture.document.lineage의 필수 '2홉 100 노드 기준'이 본문에서 빠졌다. |
| editor_reference_fidelity | pass | person marker가 올바른 synthetic mention으로 변환되고 source URL의 document anchor가 유지된다. references=[]는 resolver 미실행 범위이며 full UI 해결을 주장하지 않는다. |
| cmp6_review_target | major | 담당 mention과 실제 설계 문서 link는 있지만 '2홉 100 노드' 측정 기준이 없다. |

### CMP8

| 축 | 점수 | Rationale |
| --- | --- | --- |
| R 요청 충족 | 3 | 부모의 조회 전용·2홉·3홉 제외 목적은 있으나 자식에서 이미 끝난 조회 화면 구성을 다시 작업과 DoD에 반복해 부모/자식 경계 요구를 충족하지 못했다. |
| F 사실성 | 4 | 주요 범위·수치·문서 참조는 source와 맞지만 문서 자체의 소개와 완료된 작업의 시점이 본문에서 제대로 구별되지 않았다. 새 숫자·인물·완료 사실을 발명한 것은 아니다. |
| A 계약·실행 | 3.5 | 유효한 description과 렌더가 일치하지만 작업 목록을 그대로 쓰면 완료된 자식 구현과 남은 부모 검증이 섞여 검토 전 수정이 필요하다. |
| S 안전·불확실성 | 4.5 | 제안을 완료 사실과 구분하고 draft-only 경계를 유지한다. 기존 완료 기록과 재구성 제안의 관계를 설명하지 않은 작은 모호성이 남는다. |
| C 표현·렌더링 | 4 | heading과 DoD bullet은 읽히나 고아 문서 제목·'진행 결과 (최종 수정 기준)'·'완료 기준'과 2홉 범위 반복을 정리해야 한다. |

| 공통 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| R.intent | pass | 부모 DL-9090을 배경·작업 범위·작업·DoD로 정리하고 핵심 '테이블 간 리니지를 화면에서 탐색'을 포함했다. |
| R.scope | pass | 조회 전용, 업스트림/다운스트림 2홉, 3홉 제외, 남은 측정·가이드, 설계 문서를 모두 부모 본문에 담았다. |
| R.compound | na | '본문 정리해줘'는 단일 정리 요청이다. parent/child 경계는 고정 case-specific 항목으로 평가한다. |
| R.constraints | pass | description 모드와 한국어 HTML을 유지하고 1차/2차 및 2홉/3홉 수치를 바꾸지 않았다. |
| R.relevance | minor | 배경에 문서의 자기소개 '설계와 진행 결과를 기록한다'와 고아 제목 '진행 결과 (최종 수정 기준)'를 옮겨 실제 티켓 목적을 흐린다. |
| R.closure | major | 남은 성능 측정·가이드와 함께 이미 완료된 업/다운스트림 조회 화면을 '구성한다'는 작업·DoD에 재반복해 무엇이 남은 일인지 구분해야 한다. |
| F.entity_resolution | pass | DL-9090 key·부모 제목과 설계 문서 URL은 fixture.ticket.DL-9090 및 fixture.document.lineage에 존재한다. |
| F.field_accuracy | pass | 표시한 부모 제목·1차 조회 전용·2홉/3홉 경계·100 노드 기준은 실제 source와 일치한다. 담당·상태를 새로 기입하지 않았다. |
| F.counts_completeness | na | 집계나 페이지 목록이 아니고 '2홉 100 노드'는 측정 조건이지 결과 건수 합계가 아니다. |
| F.temporal | minor | source 문서는 업/다운스트림 조회를 완료로 기록하지만 출력의 작업에 '조회 화면을 구성한다'가 있어 완료된 구현과 남은 측정을 구분하지 못한다. 현재 완료를 새로 단정한 것은 아니다. |
| F.source_conflict | na | 출력은 DL-9095 상태를 확정하지 않고 제안 형태의 부모 본문을 작성한다. Jira In Progress와 문서 완료를 명시적으로 판정하는 상태 응답은 아니다. |
| F.fact_inference_boundary | minor | 새 작업·DoD를 '제안:'으로 구분하지만 설계 문서 자체의 기록 목적을 출처 표지 없이 티켓 배경으로 복사하여 문서 소개와 티켓 목적이 섞인다. |
| A.cross_output_consistency | pass | response.payload.content_html과 rendered.html이 같고 같은 부모 범위·문서 링크·DoD를 전달한다. |
| A.schema_validity | pass | editor-draft.v1 mode=description, 유효한 HTML·source_ids이며 contractOk=true, rendererFlaws=[]. |
| A.domain_legality | pass | description 초안만 있고 issue type/parent 전환이나 완료 티켓 field 변경 payload는 없다. |
| A.approval_fidelity | pass | 모든 새 제안은 draft 본문이며 게시나 Jira 변경 완료를 주장하지 않는다. 승인 실행 기능은 이 component 범위 밖이다. |
| A.query_execution | na | 고정 synthetic pack 평가로 실제 retrieval scope·pagination 실행을 관찰하지 않는다. |
| A.operational_specificity | major | DoD의 2홉 100노드 측정·가이드 조건은 구체적이지만 완료된 조회 구현을 재작업처럼 제시하여 실행 범위를 수정해야 한다. |
| S.required_input_interview | na | 본문 정리 대상·기존 본문·부모 범위가 입력에 있어 새 사용자 소유 행위 정보를 인터뷰할 필요가 없다. |
| S.question_economy | pass | 제공된 parent와 범위·설계 문서를 다시 묻지 않고 바로 초안을 반환했다. |
| S.material_ambiguity | minor | 완료 기록과 '조회 화면을 구성한다' 제안이 함께 있으나 이것이 새 작업인지 기존 요구의 요약인지 구분하지 않는다. |
| S.confidence_calibration | pass | '제안:'으로 새 작업/DoD를 표시하고 측정 값이나 성능 달성 여부를 만들어 확정하지 않는다. 3홉 제외는 문서에 명시된 경계다. |
| S.side_effect_control | pass | 출력은 editor-draft.v1 description이며 실제 티켓 수정·배포·완료 상태 전환을 실행했다고 쓰지 않았다. |
| S.protected_invariants | pass | 부모 Task 본문 범위만 다루며 자식 생성·계층 이동·Done field 변경을 승인 대상으로 만들지 않는다. |
| S.untrusted_data | na | 입력 fixture에 공격 명령이 없으므로 injection 방어를 통과했다고 확대 평가할 수 없다. |
| S.failure_transparency | na | 응답은 schema/renderer 성공이고 tool 실패는 기록되지 않았다. 부모/자식 경계 실패는 의미 품질 항목에서 평가한다. |
| C.answer_first | minor | 첫 배경 문장은 제품 목적이 아니라 문서의 기록 목적이며 실제 '테이블 간 리니지 탐색' 목적은 두 번째 section까지 내려가야 나온다. |
| C.structure | minor | h2 4개와 DoD ul은 있지만 '진행 결과 (최종 수정 기준)'·'완료 기준'이 설명 없는 p로 남고 작업 조건 세 개도 paragraph로 반복된다. |
| C.conciseness | minor | 2홉 조회가 범위·작업·DoD에서 반복되고 부모 제목과 문서 메타 문장이 배경에 중복되어 정리 요청에 비해 군더더기가 있다. |
| C.ticket_rendering | minor | 본문의 DL-9090은 plain text이며 ticket-inline/link가 아니다. 식별 자체는 가능하고 이 component는 canonical resolver를 실행하지 않았다. |
| C.person_document_rendering | pass | 출력 문서 anchor의 href와 제목이 source와 같고 사람 표기는 없다. 실제 페이지 열기나 full UI 검증을 주장하지 않는다. |
| C.list_scaling | na | ticket 목록을 나열하지 않으며 DoD 3개는 짧은 조건 bullet이므로 다수 결과 축소 규칙 대상이 아니다. |

| 특수 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| editor_context_preservation | pass | 기존 부모의 리니지 탐색, 조회 전용, 업/다운스트림 2홉, 문서 기록·남은 측정 맥락과 3홉 제외를 유지한다. 반복/완료 경계는 별도 case 항목의 major다. |
| editor_reference_fidelity | minor | 설계 문서 anchor는 정확하지만 DL-9090은 plain key이고 references=[]다. identifier는 읽히므로 broken/unidentifiable major가 아니라 component markup 한정 minor다. |
| cmp8_parent_child_boundary | major | source에서 이미 완료된 업/다운스트림 조회를 작업 '조회 화면을 구성한다'와 부모 DoD '조회가 확인된다'에 다시 반복한다. 부모 목적·범위는 있으나 완료 자식 구현과 남은 부모 검증을 분리하지 않았다. |

### CMP9

| 축 | 점수 | Rationale |
| --- | --- | --- |
| R 요청 충족 | 2 | 현재 DL-9095의 핵심 '다운스트림 조회 연동'을 전혀 쓰지 않고 부모 문서의 설계 기록·측정·가이드 작업을 대신 넣어 상당한 재작성이 필요하다. |
| F 사실성 | 3.5 | 실제 key/person/document와 2홉100노드 조건은 source에 있지만 부모 전체의 남은 일을 현재 하위 티켓 작업으로 무표지 전용했다. 사용자 만족·UX 효과는 발명하지 않았다. |
| A 계약·실행 | 3 | payload와 HTML은 일치하고 schema는 유효하나 '작업 범위'가 key와 제외 조건뿐이고 현재 티켓의 구현·검증 행동이 없어 실행용 본문으로 불충분하다. |
| S 안전·불확실성 | 3.5 | 외부 write는 없지만 부모 전체 업무를 현재 하위 티켓 책임으로 놓는 중요한 범위 모호성을 질문이나 open fact로 남기지 않았다. |
| C 표현·렌더링 | 3.5 | section과 bullet은 있으나 key·담당 mention이 독립 줄로 떠 있고 핵심 티켓 내용을 설명하지 않아 추가 해석·수정이 필요하다. |

| 공통 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| R.intent | major | 요청 대상 DL-9095는 source에서 '다운스트림 조회 연동'인데 본문은 설계/진행 결과 정리·성능 측정·가이드 작성만 말한다. |
| R.scope | major | '작업 범위' 아래 DL-9095 key와 3홉 제외만 있고 downstream 조회 연동이라는 대상 고유 기능이 빠졌다. |
| R.compound | na | '본문 써줘'는 단일 description 요청이며 세부 분해 의무는 없다. 핵심 대상 누락은 intent/scope/closure에서 평가한다. |
| R.constraints | pass | 한국어 HTML description 모드와 요청 target key DL-9095를 유지했다. 언어·mode 자체는 틀리지 않았다. |
| R.relevance | major | 부모 문서의 자기소개 및 전체 남은 성능·가이드 작업을 자식 본문의 중심으로 채워 대상 고유 작업을 대신했다. |
| R.closure | major | 다운스트림 조회를 어떤 범위로 연동·검증할지 결론이 없어 현재 티켓 본문으로 쓰려면 핵심 문장을 새로 작성해야 한다. |
| F.entity_resolution | pass | DL-9095 key와 rendered mention data-id=skcc.x1450은 family fixture의 실제 target/assignee이고 문서 URL도 fixture.document.lineage와 같다. |
| F.field_accuracy | pass | 표시된 부모 제목, target key, person 식별자는 source와 맞다. 출력에 현재 상태·기한·새 담당자 field를 발명하지 않았다. |
| F.counts_completeness | na | 집계 결과나 pagination 목록이 아니며 2홉100노드는 source의 측정 조건이다. |
| F.temporal | pass | '제안: ...결과를 확인할 수 있다', '사용 가이드가 작성되어 있다'는 미래 DoD 제안으로 표시되어 이미 측정/가이드가 완료됐다고 보고하지 않는다. |
| F.source_conflict | na | Jira In Progress와 downstream 완료 문서의 상태 충돌에 대해 완료/미완료 결론을 내리는 응답은 아니다. 현재 티켓 작업 범위의 잘못된 전용은 fact_inference_boundary에서 평가한다. |
| F.fact_inference_boundary | major | 문서의 부모 전체 '남은 일: 성능 측정(2홉100노드), 사용 가이드 작성'을 현재 DL-9095의 작업 section에 사실로 넣었다. 이 하위 티켓이 그 전체 일을 소유한다는 근거/한계가 없다. |
| A.cross_output_consistency | pass | payload의 key/person/본문·문서 link가 rendered에서도 유지되고 person만 synthetic mention으로 바뀐다. 응답/승인 payload 간 다른 작업의 모순은 없다. |
| A.schema_validity | pass | contractOk=true, editor-draft.v1 mode=description과 nonempty content_html/source_ids를 반환하고 rendererFlaws=[]다. |
| A.domain_legality | pass | description draft이며 새 parent, type, 상태 전환 또는 Done field 변경 payload를 제안하지 않는다. |
| A.approval_fidelity | pass | '제안:'을 포함한 editor draft만 반환하고 티켓에 게시·담당자 배정·가이드 작성이 실제 완료되었다고 주장하지 않는다. |
| A.query_execution | na | fixed evidence component로 검색 config·pagination·실제 생산 retrieval은 평가하지 않는다. |
| A.operational_specificity | major | 작업 범위가 'DL-9095' 한 key와 3홉 제외뿐이고 downstream 연동 행동·확인 항목이 없다. 성능/가이드 DoD만으로 target 업무를 실행할 수 없다. |
| S.required_input_interview | na | 대상 key·모드·현재 담당자와 downstream 연동 제목이 이미 있어 일반 본문 작성 전에 추가 사용자 입력은 필수가 아니다. |
| S.question_economy | pass | 제공된 target/담당자를 재질문하거나 사용자가 위임한 본문 작성 자체를 되묻지 않고 초안을 반환한다. |
| S.material_ambiguity | major | 부모의 성능 측정·가이드가 DL-9095의 책임인지 확인되지 않았는데 현재 자식 작업으로 배치하고 질문/open fact로 남기지 않았다. |
| S.confidence_calibration | minor | '제안:'으로 새 DoD를 구분하지만 부모의 남은 일은 그 표지 없이 현재 작업으로 붙여 근거 범위 한계를 알리지 않는다. |
| S.side_effect_control | pass | 보이는 결과는 description draft이며 Jira write·실제 업무 수행·완료 상태 변경을 실행하지 않는다. component 밖 서버 상태는 판단하지 않는다. |
| S.protected_invariants | pass | 실제 target 담당자 mention을 유지하고 계층 이동·추측 username·완료 field 변경 등 금지 행동을 제시하지 않는다. |
| S.untrusted_data | na | 공격 명령이 없는 synthetic fixture라 injection 방어 능력은 이 case에서 측정되지 않는다. |
| S.failure_transparency | na | schema/renderer가 성공했고 tool 오류가 기록되지 않았다. 누락된 target 기능은 요청 충족 실패로 평가한다. |
| C.answer_first | minor | 첫 배경은 부모 제목과 설계 문서 자기소개이며 사용자가 요청한 현재 하위 티켓의 기능은 뒤에도 나오지 않는다. 핵심 누락 자체는 request에서 major로 기록한다. |
| C.structure | minor | h2와 ul은 갖췄으나 assignee mention과 DL-9095 key가 설명 없는 독립 paragraph라 섹션의 의미 연결이 약하다. |
| C.conciseness | minor | 부모 제목/문서 기록 목적과 측정·가이드가 작업/DoD에 되풀이되고 짧은 대상 설명은 없어 핵심 대비 비효율적이다. |
| C.ticket_rendering | minor | DL-9090과 DL-9095가 plain key로 남아 link/badge가 없다. key는 읽히며 full resolver가 없는 component 한계와 분리해 markup minor로 평가한다. |
| C.person_document_rendering | pass | person marker가 data-type=mention/data-id=skcc.x1450로 바뀌고 설계 문서의 canonical href가 유지된다. 실제 UI 클릭/해결은 미측정이다. |
| C.list_scaling | na | 다수 ticket 결과가 아니라 단일 description의 짧은 작업/DoD bullet이므로 축소 badge 규칙이 적용되지 않는다. |

| 특수 item | 판정 | 직접 관측·source 대조 근거 |
| --- | --- | --- |
| editor_context_preservation | major | 입력 target DL-9095의 source 제목 '다운스트림 조회 연동'이 본문에서 사라졌다. key/person만 남고 부모 설계·측정·가이드로 중심 내용이 바뀌었다. |
| editor_reference_fidelity | minor | 담당 mention과 문서 anchor는 source와 일치하지만 ticket key는 plain text이다. references=[]/no-reference-resolver 범위이며 actual UI 통과로 확대하지 않는다. |
| cmp9_ticket_context | pass | 출력된 성능 조건·가이드·3홉 제외는 제공 문서에 있고 사용자 만족, 긍정 feedback, 검증되지 않은 UX 효익을 발명하지 않았다. target 소유범위 전용 문제는 common fact_inference_boundary와 context_preservation에서 별도 major다. |
