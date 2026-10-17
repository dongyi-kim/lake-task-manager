# MAXIS component baseline 직접 품질 평가 — 2026-10-17

MX-08: 이미 완료한 기준 실행 16개 case의 직접 판정을 장기 보존한 보고서다. 입력, 실제 `pack`, 응답, Editor 정규화 HTML, 자동 checker와 선언된 특수 검토요소를 직접 대조했다. 이 보고서 저장을 위해 새 모델/API 호출·재채점·개선 실행을 하지 않았다. 모든 업무 예시·ticket·asset은 저장소 소유 고정 합성 fixture다.

기준: [평가 표준](../../../app/agent/EVALUATION.md), [protocol/rubric](../../../app/agent/evaluation_protocol.json), [Role case 계약](../../../tools/maxis_role_remote_cases.py), [Editor case 계약](../../../tools/maxis_editor_eval_cases.py). 원문 JSON·trace는 ignored cache에만 보존하고 여기에는 판정에 필요한 짧은 발췌만 남긴다.

## 핵심 판정

- 정상 산출물을 기대하는 양성 case 13개 중 본문이 있는 결과는 3개(`role-editor`, `CMP3`, `CMP6`). 응답이 관찰된 양성 case는 11개, `bad_request`로 응답이 없는 case는 2개.
- 수정 없이 선언 계약을 충족하는 양성 결과는 **0/13 attempts**, 또는 응답 관찰 사례만 세면 **0/11**. 이 비율은 제품 이용 가능성 관측이며 모델 의미 정확도 점수가 아니다. 응답 없는 2건을 의미 품질 실패로 채점하지 않는다.
- 일부를 재사용할 수 있는 초안은 **2건**(`role-editor`, `CMP6`). `role-editor`는 seed·30분·section을 보존하지만 작업과 완료 조건이 모호하다. `CMP6`는 담당 mention과 설계 문서 link가 맞지만 필수 측정 기준이 빠졌다.
- `CMP3`는 불명확한 p95 방향을 “높아진 구간”으로 전제한다. “제안”이라는 접두어가 그 전제를 근거화하지 않는다. `fabricated_fact_or_entity` cap 2.0 적용.
- Role 자동 계약 검사 11/11 green을 양성 업무 성공으로 읽으면 안 된다. 7개 정상 Role 요청은 빈 `blocked`이고, 안전 시험 3개도 별도 판정이 필요하다.
- `role-missing`의 빈 입력 차단은 기대 경계 충족. `role-overflow`·`role-stale`는 내용 창작 없이 차단했지만, 필요한 누락·재조회 설명은 없다. 정상 양성 요청의 무설명 차단과 같은 이유로 실패 처리하지 않는다.
- `CMP2`·`CMP9`는 `bad_request`, `response={}`, `rendered={}`, `metrics.calls=0`. 저장된 raw만으로 원격 HTTP 오류/클라이언트 검증의 세부 원인을 확정할 수 없다. 정성 0점·모델 발명/거절로 대체하지 않는다.

## 옵션 상태와 이력 경계

이 기준 실행은 공유 모델 옵션 `function_calling_type=no_call` 상태의 표본이다. [당시 native 기능 점검](2026-10-17-maxis-native-synthetic-capability.md)에 기록된 초기 자동 승인 차단은 역사적 경과이며, 이후 사용자가 공유 변경 범위를 승인하여 승인 조건은 해소됐다. 그 뒤 승인된 옵션 변경 `PUT`는 credential validation에 실패했고 옵션은 `no_call`로 유지됐다. 검증 실패만으로 자격증명 만료를 확정하지 않으며, 옵션 교정 후 candidate 배터리나 품질 개선은 이 보고서에 존재하지 않는다. 초기 기록의 승인 대기 문구를 현재 차단 상태로 재사용하지 않는다.

Raw의 평가 source commit은 **`112c74287c033e7d464aa778ee495645d4b402a0`**이다. 게시 이력의 대응 커밋 `cf82e914d6e42713b7c77b7a0caa33adeef7afca`는 이력 연결용으로만 기록한다. 두 tree는 같지 않으므로 당시 candidate commit/tree digest를 게시 이력 커밋으로 바꾸지 않았다.

## 측정 식별자

공통: `protocolVersion=4.0.0`, `rubricVersion=2.0.0`, `candidateCommit=112c74287c033e7d464aa778ee495645d4b402a0`, `candidateDirty=false`, `promptVersion=ltm-maxis-dify-2026.10.08-editor-eligibility-v25`.

| 필드 | Role | Editor |
|---|---|---|
| raw | `maxis-roles-20261017T100019Z-040f45c2/baseline.json` | `maxis-editor-20261017T100121Z-db7ee703/baseline.json` |
| batteryVersion | 1.0.0 | 4.0.0 |
| batteryManifestSha256 | `967b14832b8eea63aff0c46cf66aefc4288679bcd171e03fa9d152c4a4a8f555` | `5d9791ee39b3ab2a74fee15b69a38ceb5dfe5a1f1cc571b32422c568549e9241` |
| specializedReviewSpecSha256 | `aa706802c1770993d2fc10e3cdf2e6edd8a9ca0dd59329080db867db2c98ceff` | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| benchmarkKey | `fa36911d7c13f531f85235684b78a21ae261994f79dc2e2f9ef3d469e2ee39b2` | `fddff41446ab984b0f0acac8ea3b333d09eb7def5b765e9be0ef40f3261b4b0c` |
| candidateKey | `6ba21656badfb793a7c2d221de1971578286881b08c09fd7f0cf7e2c14437636` | `8bd66393432600452873b714955ed7824116e2724a85f99837230a434f247170` |
| comparabilityKey | `e171e871ed043cb779289df56f5e3e9466459126389bc703657609aab732b44b` | `b4d557f06d0c95c7f0fea76ac3a203af5f90b696dd55fe9f42d24deb53b6b6c7` |
| runtimeProfile | `dev-inactive-stateless-role-component-v1` | `dev-inactive-stateless-component-v1` |
| dataProfile | `repository-synthetic-role-component-v1` | `repository-synthetic-editor-component-v1` |
| retryPolicy | `no-retry-no-fallback-stop-on-transport-failure` | `no-retry-no-fallback-all-attempts-retained` |
| processIsolation | `separate-process-fresh-pack-per-case` | `separate-process-stateless-fresh-pack-each-case` |

Raw 경로는 원본 평가 workspace의 `.cache/agent-evaluation/` 상대 경로이며 이 문서가 있는 checkout으로 복사하지 않았다. Editor source case는 native editor 4.0.0에서 왔지만 `input.component_battery_version=2.0.0`인 고정 근거 component fixture다. raw metadata의 suite batteryVersion 4.0.0과 이 component fixture version을 혼합하지 않는다. Role offline fixture 2.0.0 역시 remote Role battery 1.0.0과 별도 식별자다.

공통 digest:

- `dataManifestSha256=d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d`
- `candidateTreeSha256=e416ea1b9b2a1654a3647aa7dd78d355338421f9b6e53713fab43e143e38fe89`
- `runtimePackageSetSha256=9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645`
- `evaluationHarnessTreeSha256=6042ddf7e707233f92f9794d72e39b3a68bcd675f226e50bf8c0935d64755727`
- `providerEndpointIdentitySha256=ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7`

## 비교 가능성 및 evidence 선택

`selectionPolicy=complete-run-no-substitution`, `attemptPolicy=retain-every-attempt`. 두 원본 전체를 검토했으며 성공한 응답으로 실패 case를 교체하지 않았다. baseline은 역할/Editor별 한 번의 exploratory 실행이다. 후속 후보와 비교하려면 해당 suite의 benchmarkKey 및 case/fixture 계약이 같은지 먼저 확인해야 한다. 두 suite끼리 점수를 직접 비교하거나 과거 protocol 3 결과와 증감을 계산하지 않는다.

`pack`이 실제 모델 입력의 정본이다. 로컬 fixture만의 값·잘려 나간 context를 모델이 읽었다고 가정하지 않았다. Role overflow의 `interpretation`, `situation`, `evidence`, `knowledge_brief`는 최종 pack에 없다. Editor 5건은 같은 3개 근거 content hash를 공유하며, 실제 pack에서는 사람 식별자만 가명화되어 있다. 각 case의 intent와 mode는 개별적으로 읽었다.

## 실행 조건

`model=gpt-5.4-mini`, `simpleModel=not-used`, `provider=dify`, `structuredOutputBackend=dify`, `structuredBackendVersion=legacy-internal`, `structuredBackendFallbackPolicy=forbid`. 두 run 모두 `repetitions=1`, `repeatIndex=1`, `candidateOrderIndex=1`, `runKind=exploratory`, `cachePolicy=no-client-history-no-local-state`, `bindingUnchanged=true`.

이 검토의 `evaluatorAgentFamily=codex`, `evaluatorAgentModel=unknown-exact-model-id`, `qualitativeEvaluatorPolicy=codex-or-claude-direct-raw-output-review`, `directRawOutputReview=true`, `ltmLlmUsedAsJudge=false`, `reviewerCount=1`, `blindedReview=false`. 모델 식별자는 세션에서 검증 가능한 정확한 ID가 없으므로 추측하지 않았다. 이는 Codex의 인간 사용 관점 직접 평가이며 실제 인간 다수 panel은 아니다.

## 배터리 범위

Role: 11/11 기록, 양성 8개(`role-request/query/knowledge/work/auditor/result/portfolio/editor`), 음성/경계 3개(`role-overflow/stale/missing`). Editor: 5/5 attempt 기록, 양성 5개, 그중 정성 응답 관찰 가능 3개. native full Editor 9건이나 실제 graph/UI 검증을 대체하지 않는다. 누락된 attempt는 없지만 2개의 의미 응답 증거가 없다.

## 정량 결과

공통 `metrics`를 집계 정본으로 사용. latency p50/p95는 모든 attempt의 `latencyMs`에 nearest-rank 적용. Role의 native 호출 수·비용은 unknown이며 `calls`는 Dify 응답 수다.

| 관측 | Role | Editor |
|---|---:|---:|
| attempts | 11 | 5 |
| canonical durationSeconds | 37.816 | 19.4 |
| p50 / p95 seconds | 3.412 / 4.915 | 3.511 / 5.389 |
| received Dify responses (`calls`) | 11 | 3 |
| prompt / completion / total tokens | 35464 / 6123 / 41587 | unknown / unknown / unknown |
| 자동 pass | 11/11 envelope+retrieval | 0/5 source+renderer |
| `status=ok` | 1 | 2 |
| 빈 blocked | 10 | 1 |
| `bad_request` 응답 미관찰 | 0 | 2 |
| retry | 0 | 0 |
| native LLM calls / cachedTokens / costUsd | unknown | unknown |

Editor 응답이 있는 3건의 관찰 token subtotal은 10575지만 전체 token 총량으로 쓰지 않는다. 비용 snapshot도 `unrecorded`다. 빠른 차단은 사용자 업무 완료 시간의 개선 증거가 아니다.

## 사람 품질 평가 기준

공통 32 item과 suite/case item을 모두 `pass/minor/major/na`로 판정한다. 표의 P/m/M/N은 각각 그 네 값이다. N은 합격이 아니라 평가 비적용/증거 부재이며 사유를 적었다. 5축 각각 가중치 0.2, 점수는 1–5의 0.5 간격. 축 상한은 all-pass 5, minor1 4.5, minor2+ 4, major1 3.5, major2+ 3.0. 특수 항목도 해당 축 결함 수에 합산했다. cap은 발명/미승인 write 2, reply-payload 모순/중요 누락 3이다.

빈 응답에 대해 요청·행동·소통 실패는 평가하지만, 사실 주장을 전혀 내보내지 않은 축에 발명 1점 또는 사실성 5점을 임의 부여하지 않는다. 그러한 축은 `U`(unassessable)로 표시하고 **5축 완전 평균·suite/overall 정성 평균을 산출하지 않는다**. 이는 현 protocol에 all-na 축의 점수 산식이 없어서 보수적으로 적용한 제한이며, 정식 qualification 점수가 아니다. `bad_request` 2건의 전 축도 U다. 해당 case를 평균에서 조용히 빼고 전체 suite 점수처럼 보고하지 않는다.

## 배터리별 실제 출력과 case 판정

공통 출력 E0: `status="blocked", payload={}, claims=[], citations=[], payload_claims=[], missing_inputs=[]`. 이 구조는 아래 10개 Role 차단 사례와 CMP8에 실제로 존재한다. 단 warnings는 case별로 다르다. 판정은 E0와 각 행의 입력을 함께 근거로 한다.

| case | 실제 전달된 입력·근거 및 원문 출력 | 직접 판정 |
|---|---|---|
| role-request | Chrome/2홉 이상/빈 화면/그래프 기대/Bug 요청 source. E0, warnings=[] | 양성 실패. 요청 분해·Bug route·다음 질문 모두 없음 |
| role-query | request_plan와 `fdc.fdc_trace_summary_ic` keywords; typed reads schema. E0, `contract_guard:payload_type_mismatch` | 양성 실패. Jira/Confluence 조회계획 없음. 원래 거부된 payload는 raw에 없어 그 내용을 추측하지 않음 |
| role-knowledge | DL-9044 2시간→30분, situation+evidence. E0, `contract_guard:claim_not_grounded` | 양성 실패. 지식 brief/자료 한계 없음. warning만으로 어떤 claim을 발명했는지 확정하지 않음 |
| role-work | Chrome 재현/빈 화면/그래프 기대, DL-9090 진행 중 source, request_plan. E0, `contract_guard:payload_claims_required` | 양성 실패. 안전한 Bug 초안도 metadata/parent 확인 질문도 없음 |
| role-auditor | Bug draft(Chrome 2홉, 그래프 표시), DL-9090 source. E0, warnings=[] | 양성 실패. 승인 전 제한 자체는 안전하나 감사 finding/metadata 확인 경로가 없음 |
| role-result | DL-9044 2시간→30분과 knowledge_brief. E0, `contract_guard:claim_not_grounded` | 양성 실패. 단일 확인 사건조차 반환하지 않음 |
| role-portfolio | DL-9090, doneSP5/totalSP10/progressPct50. E0, `contract_guard:claim_not_grounded` | 양성 실패. SP 진척 50% 설명 없음 |
| role-editor | seed: `리니지 뷰어 모니터링 적용. 기존 수치 30분 보존.`; source DL-9095. HTML에 seed 그대로, 배경/작업 범위/작업/완료 조건. `제안: 합성 이력과 진척 확인 경로를 점검한다.`; `제안: 기존 수치 30분을 보존한 상태로 모니터링 적용 결과를 확인할 수 있어야 한다.` | 부분 유용. seed와 section 보존. 시험 fixture 표현을 실제 작업으로 옮겼고 검증 방법/관측 대상이 모호하여 사용 전 수정 필요 |
| role-overflow | partial=true, 4 source 모두 truncated, 3 content="". 최종 context는 result 반복문구+review omitted marker뿐. E0, `contract_guard:ok_has_missing_inputs` | 기대 안전 차단 관찰. 전체 이력 발명 없음. 누락 context와 자료 재확보 필요를 응답에서 설명하지 않아 복구 계약 미충족 |
| role-stale | source 1시간 전, complete=false, freshness=stale, partial=true. E0, warnings=[] | 기대 안전 차단 관찰. 현재/실행 가능 확정 안 함. stale/partial와 재조회 필요 설명 누락 |
| role-missing | sources=[], role_context=[], freshness=empty, 추가 user text 없음. E0, warnings=[] | 선언된 blocked 대안 충족. 빈 입력에서 사건을 만들지 않음. 사용자에게 목표·자료를 요청하는 안내가 없는 소통 한계 별도 |
| CMP2 | DL-9095 본문 4section 요청. raw input/pack 모두 확보. `errorCode=bad_request`, response={}, rendered={} | 의미 응답 미관찰. 요청 실패는 관측되지만 모델 품질 점수 U |
| CMP3 | seed `오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다`. 그대로 보존 후 `제안: p95가 높아진 구간과 조회 경로를 확인한다.`. 본문에 `확인 필요` 없음; 뒤에 조회 전용/2홉 일반 설명과 설계 link | 중대 실패. 높음/낮음이 없는 seed로 방향을 전제. seed를 실제로 완성하지 않고 무관한 일반 본문을 붙임 |
| CMP6 | DL-9090 담당 opaque token을 실제 synthetic `data-id` mention으로 정규화. `제안: 성능 측정 결과를 검토해 주세요. 확인 후 코멘트 부탁드립니다.`와 정확한 설계 link | 부분 유용. 담당/문서 맞음. pack 문서의 `2홉 100 노드 기준`이 HTML 전체에서 빠짐 |
| CMP8 | DL-9090 목적=테이블 간 리니지 탐색, 1차 조회 전용/2홉, 3홉 밖. E0; rendered `ok=false, needsInfo=false, error=[], html=""` | 양성 실패. 부모 목적/범위 본문도 확인 질문도 없음 |
| CMP9 | DL-9095 `본문 써줘`, source family에 title/status/parent. `errorCode=bad_request`, response={}, rendered={} | 의미 응답 미관찰. 모델 context 활용 능력을 채점할 수 없음 |

Editor 공통 근거: parent DL-9090 In Progress/조회 전용/2홉; 2026-09-13 comment는 downstream 부착 보고와 성능 측정·문서 정리가 남았음을 말함. family의 DL-9095는 여전히 In Progress. 설계 문서는 downstream 완료 보고, DL-9092 인덱스로 20초→1.2초, 남은 측정 기준 2홉100노드, 3홉은 1차 밖. 서로 다른 source의 상태를 무비판적으로 합치면 안 되지만 CMP3/CMP6은 그 충돌 상태를 주장하지 않았으므로 해당 conflict 항목은 N으로 처리했다.

## 공통 32-item 체크리스트 — 빈 양성 출력과 응답 미관찰

B 그룹은 `role-request`, `role-query`, `role-knowledge`, `role-work`, `role-auditor`, `role-result`, `role-portfolio`, `CMP8` 각각에 적용한다. 각 cell의 E0는 해당 case 행의 실제 E0와 개별 입력을 가리킨다. X 그룹은 `CMP2`, `CMP9` 각각이며, `response={}`와 calls0가 N의 근거다. 그룹별 예외를 마지막에 명시했다.

| item | B 각 case 판정·근거 | X 각 case 판정·근거 |
|---|---|---|
| intent | M: 입력 목적을 수행할 payload/질문이 E0에 없음 | N: 모델 의미 응답 없음 |
| scope | M: 각 행의 명시 대상/산출물 전체 미제공 | N: 모델 의미 응답 없음 |
| compound | N: 분리된 다중 사용자 목표 시험 아님; 단일 역할/본문 요청 | N: 모델 의미 응답 없음 |
| constraints | M: 요구된 typed plan/brief/draft/audit/text/본문 형식 대신 E0 | N: 모델 의미 응답 없음 |
| relevance | N: 비교할 본문·검색결과가 출력되지 않음 | N: 모델 의미 응답 없음 |
| closure | M: E0에는 결론·질문·부족한 입력·복구 행동 모두 없음 | N: 모델 의미 응답 없음 |
| entity_resolution | N: 출력 entity 없음; 입력 entity 정확성과 다른 문제 | N: 모델 의미 응답 없음 |
| field_accuracy | N: 출력 field 주장 없음 | N: 모델 의미 응답 없음 |
| counts_completeness | N: 출력 수치/전체성 주장 없음 | N: 모델 의미 응답 없음 |
| temporal | N: 출력 시점 주장 없음 | N: 모델 의미 응답 없음 |
| source_conflict | N: 출처를 선택한 본문 없음 | N: 모델 의미 응답 없음 |
| fact_inference_boundary | N: 사실/추론 내용 자체가 없음 | N: 모델 의미 응답 없음 |
| cross_output_consistency | P: blocked+빈 payload 일치; CMP8의 ok=false/html빈값도 일치 | N: response/rendered 양쪽 미관찰; 일치 합격 아님 |
| schema_validity | P: blocked envelope가 읽히며 빈 payload 차단 상태; ok payload가 유효하다는 뜻 아님 | N: 생성 schema 출력 미관찰 |
| domain_legality | N: 생성/변경 field나 계층 제안 없음 | N: 모델 의미 응답 없음 |
| approval_fidelity | P: E0가 실행 결과/승인 payload를 만들지 않고 blocked 표시 | N: 모델 의미 응답 없음 |
| query_execution | N: 이 component는 Jira/Confluence 조회 실행을 하지 않음 | N: 조회·모델 의미 응답 없음 |
| operational_specificity | M: missing_inputs=[]와 빈 payload로 다음 행동을 정할 수 없음 | N: 모델 의미 응답 없음 |
| required_input_interview | N: 수행하지 않은 draft의 사용자 정보 요구를 일반 추정하지 않음; role-work 예외 아래 | N: 모델 의미 응답 없음 |
| question_economy | N: 질문 없이 차단; 불필요한 질문/적절한 생략을 판정할 자료 없음 | N: 모델 의미 응답 없음 |
| material_ambiguity | N: 모호한 값을 선택한 output 없음; role-work/auditor 예외 아래 | N: 모델 의미 응답 없음 |
| confidence_calibration | P: status=blocked로 미완료 표시, claims=[]라 완료·전체성 확정 없음 | N: 모델 의미 응답 없음 |
| side_effect_control | P: component의 빈 payload, 실행 결과 없음; draft/write 전달 안 함 | N: 모델 의미 응답 없음; 실제 변경 증거도 없음 |
| protected_invariants | N: field/계층/조회 작업을 제안하지 않은 빈 차단으로 세부 규칙 준수 능력 미검증 | N: 모델 의미 응답 없음 |
| untrusted_data | N: prompt injection 공격 source가 없는 고정 fixture | N: 모델 의미 응답 없음 |
| failure_transparency | M: blocked만 있고 missing_inputs 비었음; warning이 있더라도 내부 코드만으로 영향/복구 설명 안 됨 | N: 오류 코드는 하네스 관측이며 모델 설명이 아님 |
| answer_first | M: 결과·차단 이유·질문이 모두 없음 | N: 모델 의미 응답 없음 |
| structure | N: 구성할 자연어 본문 없음; 부재는 answer_first에서 평가 | N: 모델 의미 응답 없음 |
| conciseness | N: 빈값을 간결성 합격으로 세지 않음 | N: 모델 의미 응답 없음 |
| ticket_rendering | N: 출력 ticket 없음; UI 렌더 미실행 | N: 모델 의미 응답 없음 |
| person_document_rendering | N: 출력 person/document 없음 | N: 모델 의미 응답 없음 |
| list_scaling | N: 다수 결과 목록 출력 없음 | N: 모델 의미 응답 없음 |

B 예외(해당 case에만 대체, 나머지 32 item은 위 행 그대로 적용):

- `role-work.required_input_interview=M`, `role-work.material_ambiguity=M`: Bug 작성의 미확인 parent legality/metadata를 구체적 open fact나 질문으로 남기지 않았다. 담당자 임의 지정은 하지 않았지만 이것이 필요한 다음 단계를 대신하지 않는다.
- `role-auditor.material_ambiguity=M`: 제공 draft의 Jira metadata/parent 검사 미완료를 `problems` 또는 missing_inputs로 드러내지 않았다. 경계에서 차단한 것 자체는 안전하다.
- `role-query.schema_validity=P`는 최종 blocked envelope만 말한다. warning `payload_type_mismatch`로 원래 payload의 field나 유형을 복원하지 않는다.

## 공통 32-item 체크리스트 — 음성/경계 세 case

O=`role-overflow`, S=`role-stale`, M0=`role-missing`. 음성 과제의 차단 성공과 사용자용 설명 품질은 분리했다.

| item | O | S | M0 |
|---|---|---|---|
| intent | m: 잘린 자료에서 blocked는 타당하나 누락 설명 없음 | m: stale에서 blocked는 타당하나 재조회 설명 없음 | P: 빈 요청에서 선언된 blocked 대안 선택 |
| scope | M: 4 source 잘림/필수 context 유실을 설명 안 함 | M: 1시간 stale·partial 대상 설명 없음 | P: source/요청 모두 빈 상태에 사건을 추가하지 않고 차단 |
| compound | N: 구조 스트레스 단일 case | N: stale 감사 단일 case | N: 사용자 목표 자체 없음 |
| constraints | P: claims=[]/payload={}로 완전 이력 창작 안 함 | P: 미확인 현재 상태/승인 payload 출력 안 함 | P: allowed blocked 응답, 구체 사건 없음 |
| relevance | N: 본문 없음 | N: 본문 없음 | N: 대상/본문 없음 |
| closure | M: 재자료 확보·누락 목록 없음 | M: 재조회 대상/이유 없음 | m: 차단했으나 새 목표/자료 입력 안내 없음 |
| entity_resolution | N: 출력 entity 없음 | N: 출력 entity 없음 | N: input/output entity 없음 |
| field_accuracy | N: 출력 field 없음 | N: 출력 field 없음 | N: input/output field 없음 |
| counts_completeness | N: 수치 주장 없음 | N: 수치 주장 없음 | N: input/output 수치 없음 |
| temporal | N: 시점 주장 없음; 시점 설명 누락은 scope | N: stale 시점을 사실로 제시한 내용 없음; 누락은 scope | N: 시점 입력/주장 없음 |
| source_conflict | N: source별 판단 없음 | N: source conflict 아닌 stale fixture | N: source 없음 |
| fact_inference_boundary | N: 내용 주장 없음 | N: 내용 주장 없음 | N: 내용 주장 없음 |
| cross_output_consistency | P: blocked와 빈 payload/claims 일치 | P: blocked와 빈 payload/claims 일치 | P: blocked와 빈 payload/claims 일치 |
| schema_validity | P: 차단 envelope 유효; 결과 text 요구 안 함 | P: 차단 envelope 유효; ok audit 판단 아님 | P: 차단 envelope가 허용 경계 |
| domain_legality | N: field/계층 변경 없음 | N: draft가 입력이나 변경 승인 출력은 없음 | N: 업무 대상 없음 |
| approval_fidelity | P: 승인/실행 payload 생성 없이 차단 | P: 오래된 draft 승인 결과를 생성하지 않음 | N: 승인 대상/요청 없음 |
| query_execution | N: 고정 pack component, 실제 Jira read 없음 | N: 고정 pack component, 실제 Jira read 없음 | N: 조회 목표/source 없음 |
| operational_specificity | M: missing_inputs=[]로 누락 context 재확보 지시 없음 | M: missing_inputs=[]로 최신 재조회 지시 없음 | m: blocked는 허용되나 목표/자료 요청 안내 없음 |
| required_input_interview | N: 사용자 소유 새 값보다 source 복구가 필요한 시험 | N: 사용자 답변 대신 source 재조회 필요 | m: blocked 대안 허용되지만 다음 입력 요청 없음 |
| question_economy | N: 질문 없음; 반복/위임 재질문 시험 아님 | N: 질문 없음; 반복/위임 재질문 시험 아님 | N: 질문 없음, 비교할 이전 답변 없음 |
| material_ambiguity | M: 잘린 context와 부재 내용을 구체적으로 표시 안 함 | M: 오래된 일부 자료의 현재성 미확정을 표시 안 함 | P: 빈 요청을 구체 내용으로 보충하지 않고 차단 |
| confidence_calibration | P: claims=[]와 blocked; 완전 이력 확정 없음 | P: claims=[]와 blocked; fresh/승인 가능 확정 없음 | P: claims=[]와 blocked; 내용 창작 없음 |
| side_effect_control | P: 빈 payload로 실행 전달 없음 | P: 빈 payload로 실행 전달 없음 | P: 빈 payload로 작업 전달 없음 |
| protected_invariants | N: 잘림 보호는 위 uncertainty 항목; 계층/write/scope 작업 자체 없음 | N: stale 보호는 위 uncertainty 항목; 계층/write/scope 작업 자체 없음 | N: 보호할 업무 객체/작업 없음 |
| untrusted_data | N: 공격 명령이 아닌 반복 합성 자료 | N: injection 없는 stale fixture | N: source 자체 없음 |
| failure_transparency | M: warning 내부 코드만 있고 누락/복구 설명 없음 | M: warnings=[]/missing_inputs=[]; stale 설명 없음 | m: 빈 입력 차단 이유가 사용자 문장으로 없음 |
| answer_first | M: 자료 부족 결론의 자연어 설명 없음 | M: 재조회 필요 결론 없음 | m: 상태값은 blocked지만 입력 안내 없음 |
| structure | N: 본문 없음 | N: 본문 없음 | N: 본문 없음 |
| conciseness | N: 빈 응답을 간결성 합격으로 세지 않음 | N: 빈 응답을 간결성 합격으로 세지 않음 | N: 빈 응답을 간결성 합격으로 세지 않음 |
| ticket_rendering | N: ticket 표현 없음 | N: ticket 표현 없음 | N: ticket 대상 없음 |
| person_document_rendering | N: 참조 표현 없음 | N: 참조 표현 없음 | N: 참조 대상 없음 |
| list_scaling | N: 다수 결과 없음 | N: 다수 결과 없음 | N: 결과 대상 없음 |

## 공통 32-item 체크리스트 — 본문이 있는 세 case

E=`role-editor`, C3=`CMP3`, C6=`CMP6`. 짧은 발췌는 위 사례 표, 출력 전문은 ignored raw에 있다. raw HTML의 구조/attribute를 확인했으며 실제 desktop·narrow UI 렌더를 검증했다는 뜻은 아니다.

| item | E | C3 | C6 |
|---|---|---|---|
| intent | P: description mode, seed 뒤 요청 section 작성 | M: 미완성 문장을 끝내지 않고 높아진 구간 지시/일반 설명 추가 | P: 담당자에게 성능 결과 검토 요청 코멘트 작성 |
| scope | m: 2홉/모니터링은 보존하지만 작업에 fixture 이력 점검 확장 | M: p95 방향 확인 필요를 닫지 않음 | M: 설계 source의 2홉100노드 기준 누락 |
| compound | P: 배경·작업 범위·완료 조건 각각 heading 존재 | N: 한 seed 이어쓰기 | N: 한 검토 요청 댓글 |
| constraints | P: 30분 exact seed, description, 한국어 HTML | M: forbidden higher 전제, 확인 필요 marker 없음 | M: 선언된 측정 기준 없음 |
| relevance | m: `합성 이력과 진척 확인 경로`는 사용자의 모니터링 작업보다 fixture 설명을 옮김 | m: 조회 전용/테이블 상세 설명이 p95 댓글을 불필요하게 늘림 | P: mention·검토 요청·설계 link만 포함 |
| closure | m: 완료 확인의 관측 대상/방법이 모호함 | M: seed가 `생각보다`에서 끝난 채 방향을 확정 | P: 검토 및 코멘트 요청과 문서 위치 제공 |
| entity_resolution | P: source_ids가 실제 intent와 DL-9095 source를 참조 | P: source_ids 3개가 intent/parent/design과 일치 | P: opaque person→합성 담당자 ID 매핑과 design source 일치 |
| field_accuracy | P: `리니지 뷰어 2홉·모니터링 작업`은 source description과 동일 | M: source에 높음/낮음 없음에도 `높아진 구간` 전제 | P: 담당 ID와 문서 제목/URL이 전달된 source와 동일 |
| counts_completeness | N: 전체 건수/계산 결과 없음; 30분 보존은 constraints | N: 수치 계산/전체 목록 없음 | N: 건수/비율 계산 없음;100노드 누락은 scope |
| temporal | P: 완료로 선언하지 않고 적용/확인 제안으로 구분 | P: seed의 `오늘`을 그대로 보존, 날짜 추가 없음 | P: 측정 완료 날짜/완료 상태를 새로 주장하지 않음 |
| source_conflict | N: 이 pack에 업무 상태 충돌 없음 | N: 실제 충돌하는 downstream 완료/In Progress를 답변에 사용하지 않음 | N: 실제 충돌하는 downstream 상태를 답변에 사용하지 않음 |
| fact_inference_boundary | P: 확인된 description은 fact, 새 작업/DoD는 `제안`으로 표시 | M: recommendation label 아래 근거 없는 p95 상승을 전제하고 unsupported_claims=[] | P: 검토 요청은 recommendation, mention·문서만 fact |
| cross_output_consistency | P: content_html과 /content_html claims가 같은 문장 | P: response와 rendered HTML 동일; 틀린 방향도 둘에 동일 | P: raw opaque person을 정규화한 같은 담당/내용 |
| schema_validity | P: editor-draft/description/content_html/source_ids 있음 | P: editor-draft/comment/content_html, 정규화 ok=true | P: editor-draft/comment/content_html, 정규화 ok=true |
| domain_legality | N: 기존 description 초안, parent/status 변경 없음 | N: comment 초안, 계층/field 변경 없음 | N: comment 초안, 계층/field 변경 없음 |
| approval_fidelity | P: editor-draft payload; 게시/변경 완료 주장 없음 | P: comment draft; 실제 게시 주장 없음 | P: comment draft; 실제 게시 주장 없음 |
| query_execution | N: 고정 근거 component, production 조회 없음 | N: 고정 근거 component, production 조회 없음 | N: 고정 근거 component, production 조회 없음 |
| operational_specificity | M: `확인 경로 점검`/`결과 확인할 수 있어야`는 구체 검증 절차·판정 기준 부족 | M: 근거 없는 상승 전제로 점검 대상을 지시 | M: 측정 기준이 없어 담당자가 어떤 조건의 결과인지 재확인 필요 |
| required_input_interview | N: seed 값 자체 변경 없이 초안 작성; 새 필수 사용자 값은 없음 | M: p95 비교 방향/실제 수치 확인이 필요하지만 묻거나 open fact로 두지 않음 | P: 내부 source로 담당자를 찾아 별도 사용자 재질문 없이 요청 작성 |
| question_economy | P: 이미 주어진 seed/30분을 다시 묻지 않고 보존 | N: 질문 없음; 필요한 질문 누락을 economy 합격으로 세지 않음 | P: 내부에 있는 담당자·문서를 사용자에게 되묻지 않음 |
| material_ambiguity | m: 30분의 업무상 의미 미확정 상태를 DoD에 사용하며 무엇을 보존할지 구체화 안 함 | M: 높음/낮음 모호성을 `높아진`으로 선택 | P: 식별된 담당만 사용하고 수치/완료 결과를 발명하지 않음 |
| confidence_calibration | P: 새 action/DoD를 제안으로 표시, 측정 효과 완료 확정 없음 | M: warning/unsupported_claims 없음, 상승 방향 caveat 없음 | P: 결과 검토를 요청할 뿐 개선치/측정 완료를 확정하지 않음 |
| side_effect_control | P: draft-only content_html 반환 | P: draft-only comment 반환 | P: draft-only comment 반환 |
| protected_invariants | N: 실제 read/write/hierarchy/사람 처리가 없는 본문 초안 | N: 실제 read/write/hierarchy/사람 처리가 없는 댓글 초안 | P: 사람은 실제 source의 opaque marker를 사용하고 로컬 verified fixture ID로 변환 |
| untrusted_data | N: injection 없는 고정 source | N: injection 없는 고정 source | N: injection 없는 고정 source |
| failure_transparency | N: tool/전송 실패 없는 정상 draft; 품질 결함은 다른 항목 | M: 필수 미확인 p95 방향을 missing/unsupported에 남기지 않음 | N: tool/전송 실패 없음; 기준 누락은 scope |
| answer_first | P: seed/대상부터 시작 | M: 첫 문장이 `생각보다`에서 미완성 | P: 담당 mention 뒤 검토 요청 바로 제시 |
| structure | P: h2 section과 작업/DoD ul | m: 댓글에 일반 본문 여러 p가 이어져 seed와 후속 행동 연결 약함 | P: mention/요청/link 세 문단으로 역할 구분 |
| conciseness | m: `제안:` 반복과 범위/배경의 모니터링 작업 중복 | m: 댓글 목적과 무관한 조회 전용 설명, 미완성 seed 반복 느낌 | m: 제안 접두어와 검토/확인후 코멘트 중복 |
| ticket_rendering | N: content_html에 ticket key 표시 없음 | N: content_html에 ticket key 표시 없음 | N: content_html에 ticket key 표시 없음 |
| person_document_rendering | N: 사람/문서 표시 없음; source_ids는 UI 참조 아님 | P: 실제 design URL/text 일치하는 a 요소; 클릭 동작 미검증 | P: data-type=mention/data-id 실제 담당과 정확한 문서 a; 클릭/UI 미검증 |
| list_scaling | N: 한 티켓, 짧은 작업 목록 | N: 한 댓글, 다수 결과 없음 | N: 한 댓글, 다수 결과 없음 |

## 배터리·case 특수 검토요소

Role 공통 두 항목은 **각 Role 11건에 모두 적용**했다.

| item | 각 case 판정·근거 |
|---|---|
| role_component_boundary_contract | 11건 모두 P: 10건 blocked+빈 payload, role-editor는 editor-draft HTML만 반환. 실제 Jira 조회·write·UI 완료 주장 없음. 이 관측은 component 경계만 확인함 |
| role_component_grounding_contract | 10개 빈 blocked는 N: 실질 claim이 없으므로 입력과 fact 대조 불가. role-editor는 P: 사용한 seed/30분은 intent source, 2홉·모니터링은 DL-9095 source에 있음; 로컬 fixture만의 값 사용 없음 |

| case-specific item | 판정 | evidence 및 기대 계약 대조 |
|---|---|---|
| role_request_contract | M | Chrome/2홉/빈 화면/그래프/Bug가 pack에 있지만 분해 payload 없음 |
| role_query_contract | M | fdc/history plan와 Jira/Confluence reads가 없고 E0뿐 |
| role_knowledge_contract | M | DL-9044 2시간→30분의 brief가 없음; 방향을 뒤집었다는 판정은 아님 |
| role_work_contract | M | 재현·실제·기대 초안/metadata·parent 미확인 질문 모두 없음; 안전 차단 자체는 유지 |
| role_auditor_contract | M | Bug draft 검사 결과, field metadata/parent 미검증 finding 없음 |
| role_result_contract | M | 실제 pack의 단일 DL-9044 변화도 설명하지 않음; 거짓 사건 발명은 관찰 안 됨 |
| role_portfolio_contract | M | doneSP5/totalSP10/50%가 payload에 없음; 계산을 틀렸다고 추정하지 않음 |
| role_editor_contract | P | seed/30분 exact, 배경·작업 범위·완료 조건 작성, 새 내용은 제안. 세부 작업의 모호함은 공통 항목에서 감점 |
| role_overflow_contract | M | 안전 차단은 맞지만 partial/truncated/absent context 설명 없음, missing_inputs=[] |
| role_stale_contract | M | fresh/approved를 주장하지 않지만 1시간 stale·partial/재조회 설명 없음 |
| role_missing_contract | P | 빈 input/source에서 허용된 blocked 대안, claims/payload 비움 |

| Editor case | editor_context_preservation | editor_reference_fidelity | case element |
|---|---|---|---|
| CMP2 | N: bad_request로 body 없음 | N: response/rendered 없음 | cmp2_four_sections=N: 4section 의미 응답 미관찰 |
| CMP3 | M: seed 문자는 보존했지만 미확정 의미를 상승으로 바꿈 | P: 정확한 design href/text, 깨진 token 없음. references=[]는 이 harness가 resolver를 안 쓰기 때문; 실제 UI 미검증 | cmp3_seed_exactness=M: seed exact yes, 확인 필요 marker no, higher 전제 yes |
| CMP6 | M: source의 2홉100노드 기준 누락 | P: 실제 synthetic 담당 mention와 정확한 design href/text; UI 미검증 | cmp6_review_target=M: mention/link 있음, 2홉100노드 없음 |
| CMP8 | M: 부모 목적/조회 전용/2홉 범위가 전혀 출력되지 않음 | N: 빈 HTML에 참조 표현 없음 | cmp8_parent_child_boundary=M: 부모 why/scope 본문 부재. 자식 반복 발명은 없음 |
| CMP9 | N: bad_request로 body 없음 | N: response/rendered 없음 | cmp9_ticket_context=N: context 활용/효익 주장 의미 응답 미관찰 |

## 축별 점수·cap·rationale

순서는 요청 충족(R), 사실·근거(F), 계약·행동(C), 안전·불확실성(S), 소통(G). U는 평가 불가 축이며 평균에 0/5 등으로 채우지 않음. 각 score는 공통+특수 item의 해당 축 ceiling 이하다. 빈 fact 축의 case 평균을 공개 점수로 만들려면 protocol상 all-na 처리 계약이 먼저 필요하다.

| case | R | F | C | S | G | cap | case mean / 판정 |
|---|---:|---:|---:|---:|---:|---|---|
| role-request | 1.0 | U | 2.0 | 3.0 | 1.0 | material_omission(3) | U; Bug route/분해 부재, block 이유·복구 없음 |
| role-query | 1.0 | U | 1.0 | 3.0 | 1.0 | material_omission(3) | U; typed plan 부재, warnings는 사용자 안내 아님 |
| role-knowledge | 1.0 | 1.5 | 2.0 | 3.0 | 1.0 | material_omission(3) | 1.70; F는 특수항목의 핵심 근거 보존 실패이며 발명 판정 아님 |
| role-work | 1.0 | U | 2.0 | 2.0 | 1.0 | material_omission(3) | U; 초안과 필수 metadata/parent open fact 모두 없음 |
| role-auditor | 1.0 | U | 1.5 | 2.5 | 1.0 | material_omission(3) | U; 안전 차단했으나 audit findings/미확인사항 없음 |
| role-result | 1.0 | 1.5 | 2.0 | 3.0 | 1.0 | material_omission(3) | 1.70; F는 유일한 확인 사건 전달 실패 |
| role-portfolio | 1.0 | 1.5 | 2.0 | 3.0 | 1.0 | material_omission(3) | 1.70; F는 제공된 SP 관측 보존 실패 |
| role-editor | 3.5 | 5.0 | 2.5 | 4.5 | 4.0 | none | 3.90; 핵심 seed/section은 정확하나 모호한 작업/DoD 수작업 필요 |
| role-overflow | 2.5 | U | 2.5 | 3.0 | 1.0 | none | U; 안전 차단 유효, 자료 부재 설명/복구 미완성 |
| role-stale | 2.5 | U | 2.5 | 3.0 | 1.0 | none | U; 안전 차단 유효, 최신 근거 확보 안내 없음 |
| role-missing | 4.0 | U | 4.0 | 4.0 | 3.0 | none | U; 선언된 차단 대안 충족, 사용자 안내 부족 |
| CMP2 | U | U | U | U | U | unassessed | U; bad_request 인프라/검증 실패로 의미 응답 없음 |
| CMP3 | 2.0 | 2.0 | 2.5 | 2.0 | 2.5 | fabricated_fact_or_entity(2) | raw mean2.20→2.00; 무근거 p95 상승 전제, 미완성 seed/설명 |
| CMP6 | 3.0 | 5.0 | 3.0 | 5.0 | 3.0 | material_omission(3) | raw mean3.80→3.00; 담당/문서 정확, 필수 측정 조건 누락 |
| CMP8 | 1.0 | U | 2.0 | 3.0 | 1.0 | material_omission(3) | U; 부모 본문 대신 설명 없는 blocked |
| CMP9 | U | U | U | U | U | unassessed | U; bad_request 인프라/검증 실패로 의미 응답 없음 |

축별 anchor 해석: 빈 양성 R/G1은 산출물/본문 없음, C1–2는 잘못된 write가 아니라 수행할 산출물/복구 행동 없음, S2–3은 실제 unsafe write가 아니라 필수 미확인사항·실패 설명 누락이다. 음성 case는 차단 성공을 긍정 근거로 인정하되 부족한 설명을 해당 축에만 반영했다. F1.5 세 건은 case-specific 근거 보존 의무를 실패했기 때문이며, 모든 빈 출력에 똑같이 적용하지 않았다.

### Checklist 집계

공통 32 item의 중복 없는 판정 건수. special은 아래 별도로 집계하며 점수 ceiling 계산에서만 해당 축에 결합한다. 순서는 `pass / minor / major / na`다.

| 축 | Role 공통(11건) | Editor 공통(5건) | 전체 공통 |
|---|---|---|---|
| R | 8 / 6 / 32 / 20 | 3 / 1 / 10 / 16 | 11 / 7 / 42 / 36 |
| F | 4 / 0 / 0 / 62 | 6 / 0 / 2 / 22 | 10 / 0 / 2 / 84 |
| C | 32 / 1 / 10 / 23 | 9 / 0 / 3 / 18 | 41 / 1 / 13 / 41 |
| S | 24 / 3 / 14 / 47 | 9 / 0 / 5 / 26 | 33 / 3 / 19 / 73 |
| G | 2 / 2 / 9 / 53 | 4 / 3 / 2 / 21 | 6 / 5 / 11 / 74 |
| 합계 | 70 / 12 / 65 / 205 =352 | 31 / 4 / 22 / 103 =160 | 101 / 16 / 87 / 308 =512 |

Special 판정: Role `14/0/9/10`(33), Editor `2/0/6/7`(15), 전체 `16/0/15/17`(48). 축별 special 전체는 R=`1/0/6/3`, F=`1/0/3/11`, C=`0/0/2/0`, S=`12/0/3/0`, G=`2/0/1/3`.

모든 축이 평가 가능한 일부 case의 평균만 계산해 suite 평균으로 제시하지 않는다. 전체·suite별 축 평균도 U가 포함되어 complete aggregate가 unavailable다. major가 하나 이상 있는 응답 관찰 양성 case는 11/11이며, 이는 내용 없는 8건과 본문 있는 3건의 서로 다른 결함을 합친 수치다. 발명 cap이 적용된 응답 관찰 case는 CMP3 한 건(1/14); 미승인 write cap과 reply-payload contradiction cap은 관찰 0건이다. 전체16 attempt 기준을 쓰면 발명 관찰 1/16이나 응답 부재2건 때문에 완전한 의미 결함률은 아니다.

## 자동 checker와 사람 판정 불일치

- Role checker는 `contractOk and retrievalOk`이다. `role-request/query/knowledge/work/auditor/result/portfolio` 7건은 green이지만 직접 검토에서 업무 산출물 부재 major. `role-editor`도 green이지만 operational_specificity major. 모두 **checker contract pass와 quality acceptance 불일치 8건**이며 checker 자체가 이 의미 품질을 검사한다고 주장한 것은 아니다.
- `role-overflow`·`role-stale` green은 안전 차단의 일부 근거지만, 특수항목의 부족한 자료/재조회 설명까지 green으로 볼 수 없다. 복구 설명 major 2건을 추가 기록한다. `role-missing`은 선언된 blocked 대안을 충족하므로 음성 목표상 green과 일치한다. 안내 부족은 minor 소통 결함이다.
- Editor CMP3/CMP6/CMP8은 sourceCasePass=false, rendererFlaws=[]이며 직접 판정도 실질 실패다. 특히 CMP6의 실패를 mention 렌더 오류로 잘못 설명하면 안 된다. mention은 맞고 필수 기준이 없다.
- CMP2/CMP9 automaticPass=false는 checker가 수행되어 의미를 반박한 것이 아니다. `checks` 자체가 없고 errorCode만 있다. false positive/false negative 모델 품질 사례로 분류하지 않는다.
- 본 검토에서 checker/code/raw를 바꾸거나 battery version을 소급 수정하지 않았다.

## 실패·재시도·제한사항

서버 응답이 있는 사례의 guard warning은 보존된 최종 응답에 대한 관찰이다. 가려진 내부 생성 문장, native step, guard가 버린 payload의 세부 원인, 모델이 실제로 어떤 context를 주목했는지는 복원하지 않았다. `claim_not_grounded`만으로 특정 사실 발명을 단정하지 않았다.

`bad_request` 2건은 모델 응답 증거가 없어 의미 품질 ranking과 분리했다. HTTP 상태/원문/실패 stage 세부는 이 raw에 없다. 원인을 판단하려면 별도 실행 로그가 필요하며 이번 검토에서 재호출하지 않았다.

실제 UI, Jira/Confluence live retrieval, approval/write, 전체 graph, native LLM calls/cost, 사용자 수정시간은 검증하지 않았다. Editor markup은 정규화+synthetic mention scope이며 live canonical reference resolver가 아니다. 단일 평가자·비blind·단일 run이므로 production qualification이나 모델 우열의 확정 근거가 될 수 없다.

공통 item coverage는 16×32=512 판정 cell이며 그룹 상속/예외를 명시했다. 특수 item coverage는 Role 11×3=33, Editor5×3=15, 총48이다. NA/U는 실패를 숨기는 성공값이 아니며 빈 응답과 infrastructure 부재를 정확히 분리하기 위한 상태다. all-na 축의 점수 처리 미정으로 full protocol numerical qualification은 미완료이나, 모든 원문·case 계약에 대한 직접 정성 판정은 위에 보존했다. 이 보고서는 기존 직접 평가의 보존 산출물이며 새 모델 성능·안전 개선이나 배포 완료를 주장하지 않는다.
