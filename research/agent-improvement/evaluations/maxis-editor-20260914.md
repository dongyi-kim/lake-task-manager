# MAXIS Editor 구성요소 직접 품질 평가 — 2026-09-14

## 측정 식별자

이번 결과는 **탐색 증적**이며 활성화 또는 품질 개선 성공 근거가 아니다. 실제 사용할 수 있는 결과는 baseline **0/5**, v9 **0/5**다.

| 필드 | baseline | v9 |
|---|---|---|
| protocolVersion | 3.0.0 | 3.0.0 |
| rubricVersion | 2.0.0 | 2.0.0 |
| batteryVersion | 2.0.0 | 2.0.0 |
| batteryManifestSha256 | 6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406 | 6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406 |
| specializedReviewSpecSha256 | 877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b | 877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b |
| runKind | exploratory | exploratory |
| runGroupId | maxis-editor-20260914T012540Z-bdd04f79 | maxis-editor-20260914T013255Z-8c1d3a17 |
| repetitions | 1 | 1 |
| candidateCommit | d799a98b282e9ba05406bb5428264bc346ee33ba | d799a98b282e9ba05406bb5428264bc346ee33ba |
| candidateTreeSha256 | ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6 | ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6 |
| runtimePackageSetSha256 | 9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645 | 9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645 |
| evaluationHarnessTreeSha256 | c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0 | c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0 |
| promptVersion | ltm-maxis-dify-2026.09.06-native-acl-v8 | ltm-maxis-dify-2026.09.14-quality-v9 |
| model | gpt-5.4-mini | gpt-5.4-mini |
| simpleModel | not-used | not-used |
| provider | dify | dify |
| providerEndpointIdentitySha256 | ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7 | ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7 |
| structuredOutputBackend | dify | dify |
| structuredBackendVersion | legacy-internal | legacy-internal |
| structuredBackendFallbackPolicy | forbid | forbid |
| dataManifestSha256 | d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d | d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d |
| candidateOrderIndex | 1 | 2 |
| retryPolicy | no-retry-no-fallback-all-attempts-retained | no-retry-no-fallback-all-attempts-retained |
| cachePolicy | no-client-history-no-local-state | no-client-history-no-local-state |
| processIsolation | separate-process-stateless-fresh-pack-each-case | separate-process-stateless-fresh-pack-each-case |
| benchmarkKey | dedab77e712a5c1e3319ba9fd9a18335910f0a12f24f53d6cb2e50f12a45103c | dedab77e712a5c1e3319ba9fd9a18335910f0a12f24f53d6cb2e50f12a45103c |
| candidateKey | b57dbac11c762c656dde31fded4ed3f37ccc1daa1d93b25bdfacf55758d9ac31 | cc515cb12c22b0c0fa688799ba10d95d7c066da11cbd6b18f3550798f69cd88b |
| comparabilityKey | de8f3dbe14ddf32f4aec3baee92596cbb21f4e8e07c5181cd3a780596ebc5076 | 36594e881b4aafd5d0531ce325e375803b12cd5f6c4f7d7f998dc22606b8f4e9 |

## 비교 가능성 및 evidence 선택

- selectionPolicy: `complete-run-no-substitution`; attemptPolicy: `retain-every-attempt`.
- 전체 5 case의 첫 실행을 모두 유지했다. 차단 직전의 LLM 초안으로 실제 반환 결과를 대체하지 않았다.
- 동일한 benchmarkKey와 synthetic fixture, 동일한 직접 평가자를 사용했다. candidateKey/comparabilityKey는 후보 및 원격 게시 버전 차이로 다르다.
- source suite와 직접 점수 비교 불가(`comparableToSourceSuite=false`). 원래 Editor 배터리의 일부 case·checker·특수 검토 기준을 별도 component 2.0.0에 재사용한 실험이다.
- 원시 결과와 상세 체크리스트는 로컬 ignored cache에 보존했다.

| 증거 | baseline | v9 |
|---|---|---|
| 실제 반환 | [baseline.json](../../../.cache/agent-evaluation/maxis-editor-20260914T012540Z-bdd04f79/baseline.json) | [candidate.json](../../../.cache/agent-evaluation/maxis-editor-20260914T013255Z-8c1d3a17/candidate.json) |
| 실제 반환 SHA-256 | `7499f16bb54947a79980e9d514be39871e4c6749174ddb1d527d61d663bab293` | `fbb3fccace84e4d7d12c688c3e8c8961d2761d5593a4bdeda063bd5414efc4aa` |
| 직접 평가 | [human-review.json](../../../.cache/agent-evaluation/maxis-editor-20260914T012540Z-bdd04f79/human-review.json) | [human-review.json](../../../.cache/agent-evaluation/maxis-editor-20260914T013255Z-8c1d3a17/human-review.json) |
| 보조 진단 | [llm-candidates.json](../../../.cache/agent-evaluation/maxis-editor-20260914T012540Z-bdd04f79/llm-candidates.json) | [llm-candidates.json](../../../.cache/agent-evaluation/maxis-editor-20260914T013255Z-8c1d3a17/llm-candidates.json) |

## 실행 조건

DEV의 비활성 stateless editor component, 고정 synthetic evidence, Dify 모델 gpt-5.4-mini, 원격 실행 1회씩이다. 실제 Jira/Confluence 자료를 재조회하거나 사용자 앱에서 게시하지 않았다. 본 직접 평가는 새로운 원격 호출 없이 기록된 결과만 읽었다.

후보 순서는 baseline 다음 v9이고, counterbalanced/randomized qualification이 아니다. tracked/untracked 변경이 있는 worktree이며, 5회 반복·clean commit 조건을 충족하지 않는다. 따라서 `releaseEligible=false`다.

## 배터리 범위

기존 `tools.agent_compose_eval.CASES` Editor 4.0.0의 CMP2·CMP3·CMP6·CMP8·CMP9와 `editor_renderer_contract_flaws`를 재사용했다. 원본 fixture는 `World._build_dataset_fixtures + World._build_dataset_docs`, component fixture는 `ltm-editor-component-synthetic.v2`다.

검증 범위는 원격 EditorDraft 생성과 순수 markup projection이다. 전체 compose 경로, 실제 업무 검색·pagination, 사용자 화면, 승인·게시·수정, prompt-injection 배터리를 평가한 결과가 아니다.

## 정량 결과

| 실제 반환 기준 | baseline | v9 |
|---|---:|---:|
| 전체 case | 5 | 5 |
| 사용 가능한 결과 / 자동 통과 | 0/5 | 0/5 |
| 최종 외부 control envelope 유효 | 5/5 | 5/5 |
| 빈 blocked 응답 | 5/5 | 5/5 |
| 직접 정성평가 평균 / 5 | 2.00 | 2.00 |
| 실제 결과의 자동·직접 판정 불일치 | 0 | 0 |

aggregation: 5개 축 동일 가중치 0.2의 평균, `material_omission` cap 3.0 적용 후 case 평균. 각 case 원점수 2.00이므로 cap은 점수를 더 낮추지 않는다. `score_human_case`를 내부 사용한 `aggregate_human_reviews`로 10개 관측의 체크리스트 완결성·특수 항목·산술을 검증했다. 자동 함수가 의미 판단을 수행하지 않았다.

percentileMethod: `nearest-rank-over-all-case-attempts`. 지연·토큰은 원시 `metrics`를 권위 자료로 유지하며 본 판독이 재측정하거나 선택적으로 수정하지 않았다.

## 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`.
evaluatorAgentFamily: `codex`.
evaluatorAgentModel: `current Codex agent; exact model ID not exposed`.
directRawOutputReview: `true`.
ltmLlmUsedAsJudge: `false`.
reviewerCount: `1`.
blindedReview: `false`.

동일 Codex 작업 에이전트가 10개 실제 반환과 10개 차단 전 후보를 직접 읽었다. 후보 Dify 모델을 judge로 사용하지 않았다. 구현에도 참여한 단일 비블라인드 평가자라는 한계가 있다.

| rubric 2.0.0 축 | baseline | v9 | 판단 근거 |
|---|---:|---:|---|
| 요청 충족·완결성 | 1 | 1 | 실제 본문과 후속 질문 모두 없음 |
| 사실성·근거성 | 3 | 3 | 잘못된 후보 주장을 최종 사실로 노출하지 않았지만, 사용 가능한 근거 내용·미확정 사항 설명도 없음 |
| 계약 일관성·실행 가능성 | 2 | 2 | blocked envelope는 유효하지만 삽입·검토할 EditorDraft가 없음 |
| 안전성·불확실성 | 3 | 3 | fail-closed 유지. 다만 구체적 확인 질문·복구 설명이 projection에 없음 |
| 가독성·렌더링 | 1 | 1 | 실제 html이 빈 문자열 |
| 동일 가중 평균 | 2.00 | 2.00 | 사용자 작업 성공의 개선 증거 없음 |

사실성 3점은 “정확도 60%”가 아니다. 반환 사실이 없으므로 허위 사실 유출로 감점하지 않되, 유용한 grounding 제공도 입증하지 못했다는 제한적 판단이다. 차단된 후보의 환각이나 ID 오류를 실제 사용자 응답의 결함으로 전이하지 않았다. 공통 checklist 32개와 해당 suite/case 특수 항목 3개씩에 모든 pass/minor/major/na 및 개별 증거를 JSON에 기록했다.

## 배터리·case 특수 검토요소

각 실행의 `input.review.suite_elements`와 `case_spec`를 그대로 재사용했다. 공통 요구는 입력 문맥 보존과 참조 충실성이다.

| case | 원본 특수 계약 | 실제 반환 판독 |
|---|---|---|
| CMP2 | DL-9095 배경·범위·작업·DoD 4섹션, 임의 목표 금지 | 4섹션과 하위 업무 범위 모두 미제공 |
| CMP3 | 정확한 seed 보존, p95 높음/낮음/원인 발명 금지, 확인 표시 | seed 및 확인 표시 미제공 |
| CMP6 | 확인된 person mention, 2홉 100노드 기준, resolved 문서 링크 | 세 요소 모두 미제공 |
| CMP8 | 부모의 목적·범위와 자식 책임 분리 | 부모 본문 자체 미제공 |
| CMP9 | 현재 DL-9095 맥락 사용, UX 효과 발명 금지 | 현재 티켓에 근거한 본문 미제공; 발명된 효익이 노출됐다는 뜻은 아님 |

자료의 충돌도 직접 확인했다. 설계 문서·부모 댓글은 downstream 완료를 기록하지만 현재 DL-9095는 In Progress다. 실제 답은 이를 출처별로 설명하거나 구체적 미확정 사항으로 남길 필요가 있다. CMP3의 미완성 p95 비교는 사용자 입력 자체의 미확정 정보다.

## 배터리별 실제 출력과 평가

두 실행의 모든 case가 다음과 같은 사용자 결과를 반환했다.

```json
{"status":"blocked","payload":{},"claims":[],"citations":[],"payload_claims":[],"missing_inputs":[]}
```

순수 projection도 모두 `{"ok":false,"needsInfo":false,"error":[],"html":""}`였다. 이 관측만으로 실제 UI에 오류 안내가 없다고 단정하지 않는다. UI는 이 component 실험 범위 밖이다.

| case | baseline 최종 warning | v9 최종 warning | case 점수 baseline → v9 |
|---|---|---|---|
| CMP2 | claim_not_grounded | claim_not_grounded | 2.00 → 2.00 |
| CMP3 | claim_not_grounded | intent_source_fact_forbidden | 2.00 → 2.00 |
| CMP6 | claim_not_grounded | citation_source_unknown | 2.00 → 2.00 |
| CMP8 | claim_not_grounded | claim_not_grounded | 2.00 → 2.00 |
| CMP9 | claim_not_grounded | claim_not_grounded | 2.00 → 2.00 |

### 차단 전 후보: 점수에 넣지 않은 원인 진단

v9의 payload_claims가 모두 `/content_html`만 가리키도록 개선됐고, CMP9는 DL-9095와 부모 DL-9090을 구분했다. CMP8·CMP9에는 4섹션이 생겼다. 그러나 이 부분 변화는 최종 guard를 통과하지 않았으므로 사용자 성공이나 점수 개선이 아니다.

- CMP2는 여전히 부모 DL-9090을 현재 업무처럼 작성하고 별도 작업 섹션이 없다.
- CMP3은 seed 접두부를 남겼어도 “p95 가 생각보다 높게 나왔습니다”를 발명했다. semantic seed 보존 성공이 아니다. v9는 intent source를 fact 근거로 써 차단됐다.
- CMP6은 측정 결과를 문서에 기록했다는 미확인 사실을 추가하고 실제 문서 링크를 빠뜨렸다. v9에서는 2홉 100노드 기준도 빠지고, canonical ID를 source_id로 혼동했다.
- CMP8은 자식 완료 내역을 반복하고 현재 상태 충돌을 설명하지 않았다.
- CMP9은 target이 개선됐지만 “본문 작성·진행 결과 정리”를 실제 구현 업무의 작업·DoD로 오인했다.

HTML과 claim 문장·순서가 서로 달라지는 문제가 반복된다. 다음 반복의 후보는 (1) 요청 target/seed와 부모·현재티켓 증거를 분리한 입력 projection, (2) source-bound claim/block을 먼저 만들고 HTML·citation·binding을 결정적으로 조립하는 구조다. 이는 후속 설계 후보일 뿐 이번에 구현·검증된 해결책이 아니다. production ResponseGuard의 grounding·ID·binding 조건을 낮추거나 fact를 recommendation으로 이름만 바꾸는 방식은 개선으로 인정하지 않는다.

## 자동 checker와 사람 판정 불일치

실제 post-guard 결과 기준 불일치 **0건**이다. 원본 case checker의 모든 실패를 직접 판독도 동의했다. 빈 html에서 rendererFlaws가 없다는 사실은 렌더링 성공이나 내용 적합성을 뜻하지 않는다.

차단 전 후보를 별도로 최소 pattern/heading checker에 넣어 얻는 통과는 사용자에게 전달된 결과의 자동 통과가 아니다. 그런 진단에서 target·책임·사실성 결함이 추가로 드러나더라도 이번 actual-run 성공률의 false negative로 바꾸지 않는다.

## 실패·재시도·제한사항

원격 기술 재시도·fallback 없이 모든 기록을 유지했다. 이 평가 단계에서 추가 원격 호출·게시·활성화·guard 변경을 하지 않았다.

정량 성공률과 정성평가 모두 개선이 입증되지 않았다. **v9는 탐색 증거로 보존하고 활성화하지 않는다.** 5회 반복, clean candidate, 전체 배터리, 실제 앱 경로 및 별도 안전 검증 없이 qualification이나 배포 가능성을 주장하지 않는다.
