# MAXIS Editor — v12 candidate 직접 평가 (04:27 UTC)

## 측정 식별자

실제 최종 응답 5개, 조립 전 plan 5개, 요청·원본 근거·준비 입력 전달 증적을 직접 읽었다. Guard는 4/5를 수용했지만 **실제 사용 가능 초안은 0/5**다. 자동 통과 CMP2도 의미상 실패다. 이 문서는 v12만 보존하며 후속 후보의 결과로 대체하지 않는다.

| 필드 | 기록값 |
|---|---|
| protocolVersion | `3.0.0` |
| rubricVersion | `2.0.0` |
| batteryVersion | `4.0.0` |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` |
| runKind | `exploratory` |
| runGroupId | `maxis-editor-20260914T042709Z-ab7bef4d` |
| repetitions | `1` |
| candidateCommit | `ab55e2807a3d74ab0175e1514a31b0cfc160d385` |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` |
| promptVersion | `ltm-maxis-dify-2026.09.14-editor-plan-v12` |
| model | `gpt-5.4-mini` |
| simpleModel | `not-used` |
| provider | `dify` |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| structuredOutputBackend | `dify` |
| structuredBackendVersion | `legacy-internal` |
| structuredBackendFallbackPolicy | `forbid` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| benchmarkKey | `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74` |
| candidateKey | `981320110a196f2437f364b640aa0cea25101b475b17d3e83666d19660c4880e` |
| comparabilityKey | `9f343668cb5f96f555b33c52c7b135d93db4303f6dc60a3ae6be0b992d8f2692` |
| candidateOrderIndex | `2` |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state` |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |

## 비교 가능성 및 evidence 선택

selectionPolicy: `complete-run-no-substitution`; 실패 포함 전체 5건을 사용했다. 비교 대상은 [03:57 신규 baseline 직접 평가](maxis-editor-20260914T035709Z-baseline.md)다. 동일 benchmarkKey·모델·endpoint·합성 데이터·source checker·특수 계약·harness를 유지했다. 후보 commit/prompt가 다르므로 candidateKey/comparabilityKey는 다르며, source editor 전체 suite와는 `comparableToSourceSuite=false`다.

- [실제 candidate](../../../.cache/agent-evaluation/maxis-editor-20260914T042709Z-ab7bef4d/candidate.json), SHA-256 `b9c86a0f40eea42557053f9d5dbe73b8a385ec9177a5a708dae7ccdfc5a8a434`
- [조립 전 plan](../../../.cache/agent-evaluation/maxis-editor-20260914T042709Z-ab7bef4d/llm-candidates.json), SHA-256 `1754d1b9a60e9d4088ca2482d69250b6980b1bad16189b0bf3a8b71c5a974469`
- [준비 입력 전달](../../../.cache/agent-evaluation/maxis-editor-20260914T042709Z-ab7bef4d/prepared-input-delivery.json), SHA-256 `654a8d8d739882352e2710f46858f63a74703fbe038b336d6d1ad8c50a271089`
- [직접 평가 JSON](../../../.cache/agent-evaluation/maxis-editor-20260914T042709Z-ab7bef4d/direct-review-candidate.json), SHA-256 `6db5bd21a8c839dba0abe5444e9d0214265a2defc13f5382567112ce74f3199c`

Run batteryVersion 4.0.0과 보존 input의 component_battery_version 2.0.0은 별도 값으로 그대로 기록했다. 원문·checker·fixture·guard·이전 review를 수정하지 않았다.

## 실행 조건

DEV 비활성 stateless component, gpt-5.4-mini, 고정 합성 근거, 탐색 1회다. candidateDirty=false이고 5건 모두 응답을 받았다. 평가자는 추가 원격 호출·재시도·게시·다른 LLM 판정을 수행하지 않았다.

준비 입력 검증에서 5건 모두 Start/KnowledgeGuard 원본 pack 일치, preparedViewEqualsLocal, originalSourcesUnchanged, exactSyntheticRequestBound가 true다. 각 prepared catalog에는 46개 fact unit이 있었다. 이번 누락·오인식의 원인을 요청이나 원본 근거의 전송 손실로 볼 증거는 없다.

## 배터리 범위

CMP2·CMP3·CMP6·CMP8·CMP9 전체다. `tools.agent_compose_eval.CASES`와 `editor_renderer_contract_flaws`를 재사용한 EditorDraft 생성 및 순수 markup projection 평가다. full compose·현재 Jira 조회·reference resolver·브라우저 TipTap UI·게시/write는 평가하지 않는다. mention 성공도 합성 local projection 범위다.

## 정량 결과

| 지표 | 03:57 baseline | v12 candidate |
|---|---:|---:|
| Guard accepted | 0/5 | 4/5 |
| automatic pass (native + renderer) | 0/5 | 1/5 |
| 직접 판독 사용 가능 | 0/5 | 0/5 |
| 유효 최종 envelope | 5/5 | 5/5 |
| 빈 blocked HTML | 5/5 | 1/5 |
| 직접 평가 평균 | 2.00/5 | 2.30/5 |
| 자동·직접 불일치 | 0 | 1 (CMP2 false positive) |
| 수신 Dify 응답 / 시도 | 5 / 5 | 5 / 5 |
| 전체 토큰 | 26,141 | 23,411 |
| prompt / completion 토큰 | 20,150 / 5,991 | 19,460 / 3,951 |
| metrics.durationSeconds | 18.9초 | 16.2초 |
| case latency 합계 | 18.917초 | 16.169초 |
| case latency p50 / p95 | 3.631초 / 4.827초 | 3.594초 / 4.203초 |

case latency 합계는 -14.5%, 토큰은 -10.4%지만 한 번의 관측이므로 안정적 비용·속도 우위는 주장하지 않는다. cachedTokens/costUsd=null은 미관측이지 0이 아니다. calls는 수신 Dify 응답 수이며 내부 모델 step 수와 다르다.

aggregation: 각 축 0.2 가중평균 후 case별 failure cap 적용, 전체 5개 case 평균. percentileMethod: `nearest-rank-over-all-case-attempts`. 토큰·Guard 통과율을 정성점수로 변환하지 않았다.

## 사람 품질 평가 기준

qualitativeEvaluatorPolicy: `codex-or-claude-direct-raw-output-review`.
evaluatorAgentFamily: `codex`.
evaluatorAgentModel: `current Codex agent; exact model ID not exposed`.
directRawOutputReview: `true`.
ltmLlmUsedAsJudge: `false`.
reviewerCount: `1`.
blindedReview: `false`.

같은 Codex 작업 에이전트가 원문을 읽고 5축·공통 32개·특수 3개를 직접 판정했다. 구현 참여 평가자이므로 독립 외부/맹검 평가가 아니다. `score_human_case`와 `aggregate_human_reviews`는 체크리스트 완결성·상한·산술 검증만 수행했다.

| case | 요청 | 근거 | 계약 | 안전 | 표현 | raw → cap 적용 | 사용 가능 |
|---|---:|---:|---:|---:|---:|---:|---|
| CMP2 | 2.0 | 2.5 | 2.0 | 3.0 | 2.0 | 2.30 → 2.30 | 아니오 |
| CMP3 | 2.5 | 3.0 | 2.5 | 2.5 | 2.5 | 2.60 → 2.60 | 아니오 |
| CMP6 | 3.0 | 2.0 | 3.0 | 2.5 | 3.0 | 2.70 → 2.00 | 아니오 |
| CMP8 | 1.0 | 3.0 | 2.0 | 3.0 | 1.0 | 2.00 → 2.00 | 아니오 |
| CMP9 | 2.5 | 3.0 | 2.5 | 3.0 | 2.0 | 2.60 → 2.60 | 아니오 |
| 축 평균 | 2.2 | 2.7 | 2.4 | 2.8 | 2.1 | 최종 평균 2.30 | 0/5 |

각 case에 material_omission cap 3을 적용했다. CMP6는 미확인 결과 기록 완료를 단정하여 fabricated_fact_or_entity cap 2가 추가 적용된다. “제안:” 표시는 과거 완료형 사실을 검증된 사실로 만들지 않는다. CMP8의 근거 3점은 빈 차단 응답이 근거 있는 초안을 전달했다는 뜻이나 정확도 60%가 아니다. 차단 전 plan의 오류를 사용자에게 노출된 사실로 전이하지 않았다.

## 배터리·case 특수 검토요소

공통 160개 + 특수 15개 모두 applicability·증거가 JSON에 있다. 입력 case의 goal/elements와 source hash를 보존했다.

| case | 특수 요구 | 직접 관측 |
|---|---|---|
| CMP2 | 현재 DL-9095 맥락의 배경·범위·작업·DoD | 4개 heading만 충족; 대상 자식 대신 부모/문서 및 작성 지침 |
| CMP3 | seed 정확 보존 + p95 방향/원인 발명 없이 확인 표시 | seed는 처음에 정확히 1회; 확인 표시/질문 없음 |
| CMP6 | 담당 mention + 2홉 100노드 + 설계 문서 link | mention 정상; 기준/link 누락, 결과 기록 완료 단정 |
| CMP8 | 부모 목적·범위와 자식 실행 책임 분리 | 최종 응답은 빈 blocked; 실제 본문 없음 |
| CMP9 | 현재 티켓의 확인된 사실만, 미확인 UX 효익 금지 | 효익 발명 없음; 부모 필드·현재 상태 혼동 및 raw wiki 문법 |

## 배터리별 실제 출력과 평가

### CMP2 — 형식은 통과하지만 다른 일을 한 초안

실제 내용은 “DL-9090 리니지 뷰어 1차 개발의 설계와 진행 결과를 기록한다”, “제안: 배경에는 … 적는다”, “제안: 본문을 배경·작업 범위·완료 조건 순서로 재구성한다”다. 사용자가 지정한 DL-9095는 보이지 않고 family source도 citation에 없다.

4개 heading·원문 수치·정상 HTML만으로는 자식 티켓 본문 작성 성공이 아니다. 부모/형제 완료 이력을 배경 설명 없이 복사하고 작업/DoD를 글 작성 방법으로 바꿔 주요 재작성이 필요하다. 새 성능 목표를 발명한 것은 아니며 그와 별개로 의미상 사용 불가다.

### CMP3 — seed 보존은 성공, 이어쓰기와 확인은 실패

실제 첫 문단은 “오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다”로 정확히 보존된다. 이어지는 문장은 과거 부모 댓글, 독립된 “In Progress”, 문서의 “20초 → 1.2초”, 사용 가이드 완성 제안이다.

새 p95 수치·높음/낮음·원인을 명시적으로 발명하지는 않았다. 그러나 미완성 비교에 확인 질문/표시가 없고 역사적 수치·1인칭 댓글의 시점과 출처도 구별되지 않는다. 댓글 모드와 seed 제약만 성공했으며 사용 가능한 이어쓰기는 아니다.

### CMP6 — mention은 성공, 근거 없는 완료 문장과 빠진 검토 대상

실제 local projection은 알려진 담당자 mention span을 두 번 렌더한다. 본문은 “성능 측정 결과 검토 부탁드립니다. 결과는 설계 문서에 정리해 두었습니다”다.

검토 요청 의도는 맞지만 2홉 100노드 기준과 실제 문서 link가 없다. 인용한 family/design 자료에는 성능 측정이 남은 일이며, 요청도 측정 결과의 기록 완료 사실을 확인하지 않는다. 부모 댓글의 “적고 있습니다”도 이 새 측정 결과의 기록 완료를 증명하지 않는다. recommendation 안의 과거 완료형 문장이라는 이유로 사실 검증을 면제할 수 없다. 실제 write가 발생했다는 주장은 하지 않는다.

### CMP8 — 정확한 첫 차단 원인

최종 결과는 `status=blocked, payload={}, claims=[], citations=[], payload_claims=[], missing_inputs=[]`, warning은 `contract_guard:claim_not_grounded`다. projection은 `ok=false, needsInfo=false, error=[], html=""`다.

조립 전 plan의 의미 평가는 진단에만 사용했다. frozen commit `ab55e2807a3d74ab0175e1514a31b0cfc160d385`의 assembler 및 guard 순수 함수를 메모리에서 읽어 재현했다. 조립 자체는 성공한다. 첫 실패 claim-4는 원문 JSON description에서 분리한 **“h3. 완료 기준”**이다.

- `_source_context`가 만드는 원본 serialized JSON support와 `_ground_tokens`를 그대로 사용했다.
- claim token은 `h3, 완료, 기준`, 지원되는 token은 `완료, 기준`뿐이다. JSON의 escaped newline 때문에 `h3`가 독립 support token으로 존재하지 않는다.
- `max(1, (token_count * 7 + 9) // 10)`는 3개를 요구하지만 실제 overlap은 2개여서 unchanged guard가 거부한다.
- 다른 4건에는 이 fact-token 실패가 없다. 지원 근거를 새로 만들거나 guard 임계값을 낮추지 않았다.

근거 코드: frozen `editor_assembler.py::_catalog` (371–395), `_blocks` (465–522), `contract_guard.py::_source_context` (210–325), `_ground_tokens` (538–539), `_validate_candidate` (673–679). normalized assembler SHA는 `6c5f23d46f7636088761ec1782bfc5f6a9d6e454512ce7d9d605f76d66a382bd`, guard SHA는 `68b0be35e9fdaef2f35cb3e949e5b1826ada2419748c898818e90555797c4bc3`다.

### CMP9 — 대상 연결 일부 성공, 필드 덤프와 raw 문법

실제 내용에 “DL-9095는 DL-9090의 다운스트림 조회 연동 작업”이 있으며 미확인 UX 효과를 발명하지 않았다. 그러나 `<p>h2. 진행 결과 (최종 수정 기준)</p>`, `<li>* …</li>`, 별도 문단 `Task`, `In Progress`, `DL-9090`가 그대로 있다. native checker는 통과하지만 pure renderer가 raw Markdown syntax를 검출한다.

대상 문장 1개와 측정 “확인 필요”는 부분적인 장점이다. 부모의 Task 상태/형제 완료를 무표지로 섞고 작업을 “본문에 반영한다”로 기술하는 문제까지 남아, 문법만 고쳐도 완성된 자식 본문이 되지는 않는다.

## 자동 checker와 사람 판정 불일치

CMP2 1건이다. automatic pass는 heading·기본 내용·renderer 조건을 충족했음을 보여 주지만 의미 정확도를 판정하지 않는다(`semanticCorrectnessAssessed=false`). 직접 평가는 대상/책임/최종 작성물의 적합성을 추가 확인해 사용 불가로 판단했다.

CMP3·CMP6·CMP8·CMP9는 자동 실패와 직접 사용 불가가 일치한다. Guard accepted 4/5를 usable 4/5로 쓰거나, CMP9 native-case pass를 최종 automatic pass로 쓰지 않는다.

## 실패·재시도·제한사항

원본 SHA·5개 request ID·32+3 checklist·case 점수/cap·평균 산술을 검증했다. 불일치 사례와 차단 결과를 포함한 전체 실행을 보존했다. 추가 원격 재실행·fixture/checker/guard 변경은 없었다.

한 번의 탐색·단일 비맹검 구현 참여 평가자·합성 고정 근거·순수 renderer 범위다. 통계적 품질 우위나 full LTM/UI/운영 보안 통과는 주장할 수 없다. 2.00→2.30은 구조적 부분 성공에 대한 해당 실행의 점수 차이일 뿐이며 실사용 성공은 여전히 0→0이다. 활성화 근거로 사용하지 않는다.

다음 개선은 guard 약화가 아니라 (1) 문법/필드 metadata와 서술 사실을 구분하는 source-bound catalog, (2) 현재 대상·부모/자식·시간·미확정 사실을 명시하는 입력, (3) 글 작성 지침 대신 실제 초안을 생성하는 모드별 계약, (4) 제안 속 완료형 사실에 대한 근거 규율과 필요한 확인 질문이다. 이는 후속 후보의 제안이며, 이 기록에 후속 성공을 소급 반영하지 않는다.

