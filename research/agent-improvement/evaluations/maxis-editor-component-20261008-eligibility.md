# MAXIS Editor v25 eligibility — v24 대비 탐색 평가

같은 새 비블라인드 Codex 패널의 직접 평가에서 v24 **2.82/5 → v25 2.18/5**였다. CMP8은 `bad_request`에서 본문 반환으로 회복했지만, CMP2·CMP9는 빈 `blocked`로 바뀌었고 CMP3은 미확정 p95 방향을 “높아”로 전제했다. 전체 업무 품질 개선은 미입증이며 qualification·활성화 근거가 아니다.

## 측정 식별자

| 항목 | 기준 |
|---|---|
| protocolVersion / rubricVersion | `4.0.0` / `2.0.0` |
| suite / batteryVersion | `maxis-editor-component` / `4.0.0` |
| batteryManifestSha256 | `5d9791ee39b3ab2a74fee15b69a38ceb5dfe5a1f1cc571b32422c568549e9241` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| benchmarkKey | `fddff41446ab984b0f0acac8ea3b333d09eb7def5b765e9be0ef40f3261b4b0c` |

| 후보 | v24 baseline | v25 candidate |
|---|---|---|
| candidateCommit | `ff5daa4cb88142939550253d67e78d379c42f0cb` | `c595d0a34bcd394f58e213fc237dd382dfc7428d` |
| promptVersion | `ltm-maxis-dify-2026.10.08-editor-input-v24` | `ltm-maxis-dify-2026.10.08-editor-eligibility-v25` |
| runGroupId | `maxis-editor-20261008T074007Z-88a85efa` | `maxis-editor-20261008T093641Z-72a28d37` |
| recordedAtUtc | `2026-10-08T07:40:09Z` | `2026-10-08T09:36:42Z` |
| candidateKey | `c866a1ef08c374bd6a9a79efd6c7111a5dfe05ac719d2c1a652fb1997e914672` | `c137d6a6341dd00c48ceeb484c0bb9d06d70344af058d4d541766b1c6ea9db22` |
| comparabilityKey | `f371a15a185e0e6d6436dff693332e740bf86e11444264578b0e92a5dabb8fe0` | `328b00155ae0f5255044c12b4488f35029b8d15302729a1ca020c78812280645` |
| Editor publishedWorkflowId | `479ce3d0-27f7-40b1-af0d-612b89ad2be3` | `b3cdd09e-2ca0-415c-86b0-f64f66f268ab` |
| profileFingerprint | `69c43675fbaceda7e2ba514c401e58ea` | `256722648ebe7f579e4d0b4bbd975ef4` |

## 비교 가능성 및 evidence 선택

두 primary raw는 동일 benchmark이며 5개 case의 `input` 전체 객체도 같다. 각 run은 `complete=true`, `bindingUnchanged=true`, 측정 당시 `candidateDirty=false`이다. `selectionPolicy=complete-run-no-substitution`, `attemptPolicy=retain-every-attempt`: 실패·blocked를 포함한 5회씩 모두 평가했고 closure로 교체하지 않았다. 서로 다른 candidateKey/comparabilityKey는 후보 차이이며 비교 차단 사유가 아니다.

v24 source는 ff5, v25 source는 c595가 권위다. 이후 문서 branch/commit은 측정 source를 대체하지 않는다. 동일 `candidateTreeSha256=64989c0c440de49aa71c7edf6684bb33e2751218474903d5f391b1dd63556972`만으로 remote workflow가 같다고 해석하지 않는다. 두 raw의 MAXIS source ledger, DSL pin, published UUID와 promptVersion이 후보 경계를 기록한다.

| 원본/새 판정 파일 | SHA-256 |
|---|---|
| B: `../maxis-editor-selector-input-v2/.cache/agent-evaluation/maxis-editor-20261008T074007Z-88a85efa/candidate.json` | `7a12aa3516581aa1b0b34302a872cd6f5e62ca526a2926916da1030a73b172e8` |
| C: `.cache/agent-evaluation/maxis-editor-20261008T093641Z-72a28d37/candidate.json` | `003367e82a76f87ccf0f963ad95ed27314e99c0f5098a895ab1c6bba1d313d46` |
| B 새 panel: `.cache/agent-evaluation/maxis-editor-20261008T093641Z-72a28d37/human-review-baseline-newv25-panel.json` | `b2bc8a7029a918e7d2fc0ddcb977cabaab51da02d4ce6b22638920fe9b872298` |
| C 새 panel: `.cache/agent-evaluation/maxis-editor-20261008T093641Z-72a28d37/human-review-candidate-newv25-panel.json` | `9eb5f8a4d260f4991dafb38b77de5fabb24469a953615997cf8253bd5ba08c4d` |

경로는 이 worktree 기준이다. 각 sidecar의 `reviews`에는 case별 32개 공통+3개 특수 판정, 실제 근거, 5축 rationale, cap, 발췌가 모두 있다. `aggregate`는 공식 산술 함수 결과에서 중복된 `observations`만 생략한 요약이며, 저장된 모든 필드는 공식 계산과 일치한다. 완전한 근거는 `reviews`에 남긴다. 기존 [v24 보고서](maxis-editor-component-20261008-input-v2.md)와 과거 패널 점수·원본은 그대로 보존하며 이번 패널 점수로 교체하지 않는다.

## 실행 조건

| 항목 | 양 후보 공통 |
|---|---|
| model / simpleModel / provider | `gpt-5.4-mini` / `not-used` / `dify` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| structuredOutputBackend / structuredBackendVersion / structuredBackendFallbackPolicy | `dify` / `legacy-internal` / `forbid` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `6042ddf7e707233f92f9794d72e39b3a68bcd675f226e50bf8c0935d64755727` |
| runKind / repetitions / repeatIndex | `exploratory` / `1` / `1` |
| candidateOrderIndex | raw에서 양쪽 모두 `2`; 실제 기록 순서는 v24→v25이며 무작위·counterbalanced 설계가 아니다 |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state`; provider cache 통제/토큰은 미관측 |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |
| dataProfile / runtimeProfile | `repository-synthetic-editor-component-v1` / `dev-inactive-stateless-component-v1` |
| pricingSnapshot | `unrecorded` |

[게시 기록](../../../deploy/maxis-dify/deployments/dev-editor-eligibility-2026.10.08.yml)의 publication SHA는 `b4ed2b97ef6b0b319c876282c2cdc9b4bb27c02a7e951300116afcd3e5e863ce`, canary SHA는 `50fb4ea20f95bbaa869058706065d89a50feebd743f82baedff67fc712c13745`이다. 저장된 receipt 기준 canary 3/3·verified=true지만 active/enabled=false, 세 route=local이다. 이는 연속 실시간 상태 확인이나 업무 품질·활성화 승인이 아니다. 이 보고서는 API를 호출하지 않았다.

## 배터리 범위

전체/실행/누락은 양쪽 5/5/0: CMP2·CMP3·CMP6·CMP8·CMP9. 대상은 EditorDraft와 저장된 pure markup/synthetic mention 변환뿐이다. full compose, canonical reference resolver, desktop/narrow UI, 실제 Jira 저장/승인은 측정하지 않았다. `comparableToSourceSuite=false`이며 전체 앱 품질 점수로 확장하지 않는다.

공통 synthetic 근거는 DL-9090 parent, DL-9092~9095 family, 리니지 설계 문서다. 문서는 다운스트림 완료와 20초→1.2초, 잔여 성능 측정(2홉 100노드)/가이드를 기록하지만 현재 DL-9095는 In Progress다. source별 상태·시점을 구별해야 한다. 입력 fixture의 hash도 sidecar에 보존했다.

## 정량 결과

`metrics`가 canonical 집계다. case latency는 모든 실패 포함 5개 `latencyMs`의 `percentileMethod=nearest-rank-over-all-case-attempts`로 다시 계산했다.

| 지표 | v24 | v25 |
|---|---:|---:|
| attempts | 5 | 5 |
| metrics.durationSeconds | 22.6 | 25.1 |
| case latency 합계 / 평균 (ms) | 22,571 / 4,514.2 | 25,135 / 5,027 |
| p50 / p95 (ms) | 4,224 / 6,867 | 5,019 / 6,539 |
| received Dify responses / attempt당 | 4 / 0.8 | 5 / 1.0 |
| status ok / blocked / 응답 부재 | 4 / 0 / 1 | 3 / 2 / 0 |
| 자동 pass | 2/5 (40%) | 1/5 (20%) |
| 자동 실패율 | 3/5 (60%) | 4/5 (80%) |
| contractOk=true 관측 / 전체 attempts | 4/5 | 5/5 |
| bad_request / retry | 1 / 0 | 0 / 0 |
| canonical prompt/completion/total tokens | unknown / unknown / unknown | 14,807 / 3,997 / 18,804 |
| v25 token/attempt | — | 2,961.4 / 799.4 / 3,760.8 |
| cachedTokens / costUsd | unknown / unknown | unknown / unknown |

v24 수신 4응답에서만 관측된 부분 usage는 prompt 11,436 + completion 2,408 = 13,844다. 실패 1회의 usage는 없어 전체/attempt당 token 및 비용 절감 비교에 쓰지 않는다. v25 `tokenUsageComplete=true`도 **Dify 응답 usage**가 5개 모두 있다는 의미이지 native workflow 내부 단계·재시도 전체를 계측했다는 뜻이 아니다. `calls`는 수신 응답 수이며 실제 모델 호출 수는 양쪽 unknown, 비용도 unknown이다. 이 단일 표본의 p95 감소를 성능 개선 일반화로 보지 않는다.

## 사람 품질 평가 기준

`qualitativeEvaluatorPolicy=codex-or-claude-direct-raw-output-review`; `evaluatorAgentFamily=codex`; `evaluatorAgentModel=Codex (exact serving model identifier not exposed)`; `directRawOutputReview=true`; `ltmLlmUsedAsJudge=false`; `reviewerCount=1`; `blindedReview=false`; `pairedPanel=true`.

패널 `codex-direct-editor-v24-v25-20261008-newv25-panel`의 같은 reviewer가 양쪽 input/source와 실제 response·claims/citations·rendered를 새로 읽었다. 이전 점수를 재사용하지 않았다. 평가자는 selector 코드 검토 및 canary 도구 작성/검토에도 참여했으므로 독립 블라인드 평가가 아니다. 같은 v24 raw의 과거 패널 점수 2.48과 이번 새 패널 점수 2.82는 별도 관측이다. 패널 간 점수 변동 가능성을 공개하며 과거 점수와 새 점수를 혼합하지 않는다.

`aggregation`: 요청(R)·근거(F)·계약(C)·안전(S)·표현(M) 각 20%, 1–5의 0.5 단위. 공통+특수 결함의 같은 축 상한을 적용한 뒤 가중평균, cap, 모든 case 산술평균 순서다. cap은 fabricated/unsafe=2, reply-payload contradiction/material omission=3. 자동 checker를 점수로 변환하지 않았다. 공식 `aggregate_human_reviews`와 `validate_report`는 산술/형식 검증만 수행한다.

빈 결과의 F=1은 사실을 조작했다는 뜻이 아니라 근거화된 답변을 전달하지 못했다는 anchor 판단이다. 실제 주장·field가 없는 세부 항목은 NA로 남기고 사실/미확인 구분 전달 실패를 기록했다. 빈 응답에 fabrication cap을 적용하지 않았다. NA는 미적용/미관측이지 안전 테스트 통과가 아니다.

| 축 / 공통 item 수(case당) | v24 평균 | v25 평균 | v24 P/m/M/NA | v25 P/m/M/NA |
|---|---:|---:|---|---|
| 요청 / 6 | 2.60 | 2.10 | 10/6/10/4 | 8/4/13/5 |
| 근거 / 6 | 3.50 | 2.50 | 10/5/2/13 | 7/1/6/16 |
| 계약 / 6 | 2.70 | 2.40 | 12/1/5/12 | 13/0/5/12 |
| 안전 / 8 | 3.60 | 2.80 | 11/4/3/22 | 10/1/8/21 |
| 표현 / 6 | 2.80 | 2.30 | 8/10/2/10 | 7/6/3/14 |
| 공통 총 160 | — | — | 51/26/22/61 | 45/12/35/68 |
| 특수 총 15 | — | — | 3/5/7/0 | 2/3/10/0 |

case당 35개, 후보당 175개, 총 350개 판정 모두 sidecar에 근거와 함께 있다. cap-code 보유 case는 양쪽 4/5(80%); 실제 평균을 cap으로 낮춘 case는 양쪽 2/5다. 보조 수동 지표인 ‘중요한 수정 없이 사용 가능’은 1/5→0/5이며 protocol의 별도 qualification 기준이 아니다.

## 배터리·case 특수 검토요소

각 case는 공통 `editor_context_preservation`(요청), `editor_reference_fidelity`(표현), 해당 고유 요소를 모두 평가했다. P=pass, m=minor, M=major. full UI 대신 저장된 HTML/fixture identity에만 근거한다.

| case / 목표·필수 계약 | v24 (맥락/참조/고유) | v25 (맥락/참조/고유) | 실제 근거 |
|---|---|---|---|
| CMP2 / 4개 section·업무 DoD·임의 목표 금지 | m/m/M | M/M/M | v24는 네 heading이 있으나 형식 자체가 DoD; v25는 본문 없음 |
| CMP3 / exact seed·방향 확인 필요 | P/m/M | M/m/M | 양쪽 seed는 정확; v24 확인 marker 부재, v25 “높아” 전제 |
| CMP6 / 담당 mention·2홉 100노드·설계 link | M/P/M | M/P/M | 양쪽 mention/link 대응은 맞지만 측정 기준 누락 |
| CMP8 / 부모 why/scope·자식 제목 DoD 중복 금지 | M/M/M | P/m/m | v24 응답 없음; v25 목적/범위 있음, 완료 실행 세부를 작업 section에 혼합 |
| CMP9 / 현재 기능·검증·문서만, 효익 발명 금지 | m/m/P | M/M/M | v24 기능/검증 제안이며 효익 발명 없음; v25 본문 부재이지 허위 효익 관측은 아님 |

## 배터리별 실제 출력과 평가

| case | v24 R/F/C/S/M → cap 후 | v25 R/F/C/S/M → cap 후 | 주요 판단 |
|---|---|---|---|
| CMP2 | 2.5/4.5/3/4.5/3.5 → 3.00 | 1/1/1.5/2/1 → 1.30 | 형식적 초안에서 빈 blocked로 악화 |
| CMP3 | 2.5/3.5/2.5/3/2.5 → 2.80 | 2/2.5/2.5/2/2.5 → 2.00 | 미완결 지속, v25 미확정 방향 전제 |
| CMP6 | 3/4/3/4/3.5 → 3.00 | 3/5/3/5/3.5 → 3.00 | v25 불필요 확신 감소, 필수 기준 누락 지속 |
| CMP8 | 1/1/1/2/1 → 1.20 | 3.5/3/3.5/3/3.5 → 3.30 | 초안 반환 회복, 현재/문서 완료 충돌은 미해결 |
| CMP9 | 4/4.5/4/4.5/3.5 → 4.10 | 1/1/1.5/2/1 → 1.30 | 쓰일 만한 기능 초안에서 빈 blocked로 악화 |
| suite 평균 | 2.82 | 2.18 | 이 새 패널·단일 표본에서 −0.64 |

CMP2 — v24 DoD의 “배경, 작업 범위, 완료 조건이 구분된 본문으로 정리되어 있어야 한다”는 업무 기능의 완료 조건이 아니다. v25는 `status=blocked, payload={}, missing_inputs=[]`이고 렌더 본문도 비어, 사용 가능한 대안/확인 질문을 주지 않는다.

CMP3 — 양쪽 seed “오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다”는 보존됐다. v24 “p95 수치를 이어서 적고”도 완성된 댓글은 아니다. v25 “제안: p95가 생각보다 높아 원인 구간을 더 좁혀보자”는 뒤 행동이 제안이어도 ‘높아’라는 전제의 근거가 생기지 않는다. 그래서 v25에 fabrication cap=2를 적용했다. 양쪽의 과거 “남은 건 성능 측정” 댓글은 오늘 측정 seed와 시점 구분 없이 붙는다.

CMP6 — 양쪽 `[[person:4872393f24bc]]`는 저장된 pure renderer에서 `skcc.x1402` mention으로 변환되고 설계 href는 fixture와 같다. v25는 “제안: 성능 측정 결과를 검토해 주세요”로 간결해졌다. v24의 “기록해 두었습니다”는 source의 “적고 있습니다”보다 확정적이지만 기록 자체는 source가 뒷받침하므로 fabrication cap은 적용하지 않았다. 양쪽 모두 “2홉 100노드”가 없어 material omission cap=3이다.

CMP8 — v24는 `bad_request`, response/rendered={}다. v25는 “테이블 간 리니지를 화면에서 탐색…1차 범위는 조회 전용”, “3홉 이상 확장은 1차 범위 밖”과 성능 측정/가이드를 담았다. 다만 “다운스트림 2홉 조회: 완료 — …20초 → 1.2초”는 문서의 기록 시점을 밝히지 않고 현재 DL-9095 In Progress와 충돌한다. 이는 수치 발명이 아니라 시점/출처 충돌 누락이다. 부모 DoD에 자식 제목을 그대로 복제한 major는 아니지만 완료 작업과 부모 검증의 분리가 약하다.

CMP9 — v24는 “다운스트림 조회 연동이 동작하고, 리니지 뷰어에서 확인 가능해야 한다”와 “3홉 이상 확장은 포함되지 않아야 한다”를 제안하며 만족도·UX 효과를 발명하지 않았다. 배경/중복 문장만 다듬으면 사용할 수 있다고 판단했다. v25는 CMP2와 같은 빈 blocked여서 현재 티켓 문맥 기반 본문을 제공하지 못했다.

## 자동 checker와 사람 판정 불일치

v24 자동 green CMP2는 업무 DoD 대신 편집 형식 조건을 써 사람 major였다. v25 자동 green CMP8은 본문 반환·표면 구조는 통과했지만 source 시점/상태 충돌이 사람 major였다. 두 false positive를 보존한다. v24 CMP9 자동 green은 이 패널의 경미 수정 후 사용 가능 판단과 양립한다.

나머지 자동 red는 모두 이 패널에서도 중요한 누락/미완결/무응답이 있어 false negative로 판정하지 않았다. 이번 작업은 checker/battery를 변경하지 않았다. 후속 보강 시 의미 변경에 맞는 battery version/manifest를 새로 발행하고 과거 raw를 재채점하거나 결과를 교체하지 않아야 한다.

## 실패·재시도·제한사항

- v24 CMP8 기술 실패와 v25 CMP2·CMP9 blocked를 모두 primary에 포함했다. v25 blocked envelope는 schema-valid일 수 있어 계약 통과와 사용자 품질 실패가 동시에 성립한다. guard 내부 원인/selector 출력은 이 raw만으로 확정하지 않는다.
- 각 후보 1회·5개 합성 사례, 순서 고정, 단일 비블라인드 패널이다. 전체 품질 우열·회귀율·production 전환을 일반화할 수 없으며 `qualificationEligible=false`, `releaseEligible=false`다.
- 양쪽 현재 측정의 source/binding은 raw ledger와 저장 receipt 관측이다. native 실행 UUID·내부 모델 단계·검색 coverage/knowledge freshness를 새로 관측하지 않았으며 전체 비용/실 호출 수는 미상이다. canary와 코드 리뷰 점수는 업무 정성점수가 아니다.
- HTML href의 JSON 표시용 escaping은 깨진 링크로 채점하지 않았다. 평문 ticket key의 minor와 synthetic mention 변환은 관측했지만 실제 UI badge/click, canonical reference resolver, 승인·게시 E2E는 미검증이다.
- 모든 원본 raw와 기존 점수/보고서를 보존했다. 새 sidecar 두 개와 본 보고서만 작성했고 API·runtime LLM judge·추가 진단·재시도는 실행하지 않았다. 다음 개선은 blocked의 원인과 p95 불확실성/검토 기준을 구분해 설계해야 하며 Guard 완화나 품질 개선 완료를 주장하지 않는다.
