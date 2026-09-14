# MAXIS/Dify LTM 품질 개선·기존 벤치마크 재사용 검토

작성일: 2026-09-14 · 상태: **탐색 비교 실행 완료, 실사용 가능한 생성 결과 개선 미달성**

기존 LTM의 5종·58개 case를 재사용할 수 있다. 이번에는 사용자 입력, 미완성 seed,
근거와 실제 생성물을 대조하기 쉬운 Editor 5개 case를 별도 component 평가로 구성했다.
가명화·검색 질의 문제를 진단한 뒤 동일 benchmark에서 baseline과 v9 후보를 각각 1회 실행했다.
최종 수락 가능한 생성물은 **0/5 → 0/5**로 개선되지 않았고, 총 token은 **17.5%**, 관측된
latency 합계는 **31.4%** 증가했다. 일부 heading·대상 식별은 나아졌지만 사실 발명과 근거 결속
실패가 남아 모든 결과가 Guard에서 차단되었다. 최종 반환의 직접 정성평가도 **2.00/5 → 2.00/5**다.
profile 활성화나 production 전환의 근거가 아니다.

이 문서는 실제 실행 증적, Guard 전 생성 후보의 직접 판독, 기존 suite 재사용 방법과 다음 수정
지점을 보존한다. Guard 전 후보는 디버깅 증거이며 사용자에게 삽입된 성공 결과가 아니다.

## 1. 측정 식별자

| 항목 | 측정 계약 |
|---|---|
| protocolVersion | `3.0.0` |
| rubricVersion | `2.0.0` |
| component suite | `maxis-editor-component` |
| batteryVersion | 현재 `2.0.0`; 초기 진단 run은 `1.0.0` 및 `2.0.0` |
| source suite / version | `editor` / `4.0.0` |
| fixtureVersion | `ltm-editor-component-synthetic.v2`, 기준일 `2026-09-14` |
| candidateCommit | 두 비교 raw 모두 `d799a98b282e9ba05406bb5428264bc346ee33ba`, `candidateDirty=true`; 실제 변경 자산은 source hash로 별도 결속 |
| promptVersion | baseline `ltm-maxis-dify-2026.09.06-native-acl-v8`, candidate `ltm-maxis-dify-2026.09.14-quality-v9` |
| model / simpleModel | 두 후보 모두 `gpt-5.4-mini` / `not-used` |
| provider | `dify` |
| structuredOutputBackend / structuredBackendFallbackPolicy | `dify` / `forbid` |
| structuredBackendVersion | 초기 공통 metadata는 `legacy-internal`; Dify 원격 구현 버전으로 해석하지 않음 |
| runKind / repetitions | `exploratory` / run당 1회; qualification 아님 |
| selectionPolicy | `complete-run-no-substitution` |
| attemptPolicy | `retain-every-attempt` |

초기 진단 증적의 식별자는 다음과 같다. 새 후보가 성공해도 이 증적을 덮어쓰거나 점수를 대체하지 않는다.

| 항목 | 진단 1 | 진단 2 |
|---|---|---|
| runGroupId | `maxis-editor-20260914T011526Z-a03a82c3` | `maxis-editor-20260914T012001Z-1edc2a56` |
| batteryVersion | `1.0.0` | `2.0.0` |
| batteryManifestSha256 | `a55864079a9e59ea46278531017cb13640b0ace3d74cd15877037393294dcdf5` | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` | 동일 |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` | 동일 |
| candidateTreeSha256 | `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6` | 동일; 원격·component 자산은 별도 source binding 참조 |
| runtimePackageSetSha256 | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` | 동일 |
| evaluationHarnessTreeSha256 | `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0` | 동일; 새 component 파일은 별도 binding 참조 |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` | 동일 |
| benchmarkKey | `c5df5705e19f28081208bf4df72874647da44ee0ab22e06c1732feb21c949c5a` | `4a25ae0d52a24bd3661062c23134183542c8cd5d2e4c44747c662f5b3255499e` |
| candidateKey | `aa1bdabe4662dcced3714ef531f70c7fb535266e40c38e3c4e2b7adebbe22b57` | `52920dfdb7da7a0f47f7162e413bb7b0607e23bf9a7b9d5326b37eba9c73b92c` |
| comparabilityKey | `9c9e16699a69dd0b65d5570d291278dccc26d00ef800ffc3164a693a399b0f2e` | `68ce115a0343c20fbd013d0bf7f7ba2d0a8448b5af3093e7f8f0eb781935771d` |

실제 비교에 사용한 primary raw의 식별자는 다음과 같다. 초기 진단 raw의 점수를 여기에 섞지 않는다.

| 항목 | baseline | candidate |
|---|---|---|
| runGroupId | `maxis-editor-20260914T012540Z-bdd04f79` | `maxis-editor-20260914T013255Z-8c1d3a17` |
| batteryVersion | `2.0.0` | `2.0.0` |
| batteryManifestSha256 | `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406` | 동일 |
| specializedReviewSpecSha256 | `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b` | 동일 |
| benchmarkKey | `dedab77e712a5c1e3319ba9fd9a18335910f0a12f24f53d6cb2e50f12a45103c` | 동일 |
| candidateKey | `b57dbac11c762c656dde31fded4ed3f37ccc1daa1d93b25bdfacf55758d9ac31` | `cc515cb12c22b0c0fa688799ba10d95d7c066da11cbd6b18f3550798f69cd88b` |
| comparabilityKey | `de8f3dbe14ddf32f4aec3baee92596cbb21f4e8e07c5181cd3a780596ebc5076` | `36594e881b4aafd5d0531ce325e375803b12cd5f6c4f7d7f998dc22606b8f4e9` |
| published Workflow | `a5f28cc2-9ef2-4651-8e77-a1ccde5feb3e` | `58d22f63-0519-403c-9784-f3fe7e7a9eb3` |
| profileFingerprint | `cb40a03650a170d74ced6bf26bed764c` | `d612434cfde4fa1bda119455c03b25fb` |
| retrievalQueryPolicy | `current` | `role-terms` |
| bindingUnchanged | `true` | `true` |

두 비교 raw의 dataManifestSha256, candidateTreeSha256, runtimePackageSetSha256,
evaluationHarnessTreeSha256, providerEndpointIdentitySha256는 위 진단 표에 기록한 값과 동일하다.
공통 tree digest만으로 Dify 후보를 식별하지 않고 `maxisBinding.sourceFiles`의 prompt/DSL/guard,
published Workflow와 profile fingerprint를 candidateKey에 포함했다. source binding은 각 run 중
변경되지 않았다. qualificationEligible은 둘 다 `false`다.

## 2. 비교 가능성 및 evidence 선택

이번 component는 실제 `EditorDraftV1` 계약과 순수 markup 렌더링을 측정한다. 전체 LTM
`compose()`의 조사·계보 수집·일반 상태 공유 shortcut·후처리·실제 UI는 포함하지 않는다.
따라서 원 `editor 4.0.0` suite와 case ID가 같아도 `comparableToSourceSuite=false`다.
원 suite의 성공률이나 latency와 절대 증감을 계산하지 않는다.

기존 Knowledge 평가의 **13/25**는 검색 identity, ACL, 응답 계약, injection 관련 조건 및
서명 검토 요건을 묶은 결과다. 정상 경로에서 기대하는 payload 자체가
`{"status":"retrieved"}`인 제어 응답이며, `semantic_answer_correctness_assessed=false`다.
이 수치를 “LLM 업무 답변 정확도 52%” 또는 “본문 생성 품질 52%”라고 해석할 수 없다.

현재 앱의 DEV profile 경로는 `_context_pack()`에서 사용자 원문과 자료를 고정 synthetic fixture로
치환하고 `invoke()`에서 exact fixture만 허용한다. 이는 연결·계약 검사 경계이며,
해당 경로에 기존 사용자 scenario만 연결해서는 의미 품질을 측정할 수 없다. 이번 전용 평가기는
고정 repository fixture만 별도 process에서 전달하며 앱의 DEV gating을 완화하지 않는다.

case의 `expected`, rubric, native checker, source manifest는 로컬 검수 metadata다.
`build_pack()`은 검증된 projection의 `instruction`, 원 입력/seed, 선택된 fixture 근거와
실제 payload schema만 사용한다. 정답 문자열·checker·case별 기대 결과를 모델 입력에 넣지 않는다.
가명화 전 원본 CaseSpec도 로컬에 보존하므로 fixture·wire·output을 각각 대조할 수 있다.

초기 두 진단은 battery/manifest/benchmarkKey가 다르므로 A/B 품질 비교가 아니다. 이후 실제 비교는
동일한 component version·case·fixture·renderer·checker·model에서 수행했다. 후보 차원은
v8/current query와 v9/role-terms 묶음이다. Prompt와 검색 정책을 함께 바꾼 bundle 비교이므로
관측 차이를 어느 한 변경의 단독 효과로 분리할 수 없다. 모든 실패 attempt를 보존한다.

## 3. 실행 조건

| 항목 | 설정 / 측정 의미 |
|---|---|
| 데이터 | native `World._build_dataset_fixtures` 및 `_build_dataset_docs`를 고정 날짜·메모리 dict에서 실행 |
| fixture 선택 | `DL-9090`, 직접 자식 `DL-9093~9095`, 연결 Bug `DL-9092`, 리니지 설계 문서 |
| 선택 범위 | 부모 현재 필드·최신 댓글, 자식/연결 티켓 현재 필드와 본문, 설계 문서 원문; 전체 history 조회와 다름 |
| 가명화 | 고정 synthetic 전용 salt + 기존 ContextGuard 방식; raw fixture UID는 outbound 금지 |
| person_map | opaque marker→fixture UID의 로컬 렌더 전용 map; Dify에 전송하지 않음 |
| input provenance | case ID allowlist, 원 입력/seed 보존, canonical snapshot 비교, 소스 변경 감지 |
| 요청 제한 | `context_pack` 최대 6144 UTF-8 bytes, query 최대 1024 bytes; 초과 시 중단 |
| retryPolicy | `no-retry-no-fallback-all-attempts-retained` |
| cachePolicy | `no-client-history-no-local-state`; provider 내부 cache는 미관측 |
| processIsolation | `separate-process-stateless-fresh-pack-each-case` |
| candidateOrderIndex | 탐색 비교에서 baseline `1`, candidate `2`; 순서 균형 qualification은 아직 미실시 |
| 감사·검증 | 목적지 결속, app key 재사용, payload schema, ResponseGuard, Workflow/Knowledge version 및 Sentinel 검증 유지 |
| 활성화 | component 결과는 activation/release gate가 아니며 `releaseEligible=false` |

현재 후보의 `role-terms` 정책은 원 query를 prefix로 보존하고
`Policy search terms: HTML TipTap Description`을 붙인다. 적용 대상은 canonical
`editor_author`와 정확한 `EditorDraftV1` schema 조합이다. 이 문구는 고정 역할 정책 검색용
일반 용어이며 업무 사실, case 정답, 문서 ID, 근거 claim 또는 쓰기 권한이 아니다.
다른 역할·schema에 적용하지 않으며 query가 길어지면 원문을 자르는 대신 예외로 종료한다.

## 4. 배터리 범위

### 기존 suite 재사용 inventory

| 원 suite / 파일 | version | 전체 case | 주요 검토 |
|---|---|---:|---|
| Conversation / `tools/agent_lang_ab.py` | `5.0.0` | 8, `S1~S8` | 생성·버그·이력·사람·업무·진척·내외부 조사·복합 근거 |
| Editor / `tools/agent_compose_eval.py` | `4.0.0` | 9, `CMP1~CMP9` | 맥락 작성·seed 보존·불확실성·본문 구조·참조 렌더 |
| Create / `tools/agent_create_suite.py` | `6.0.0` | 28 | 인터뷰·계층·parent·담당·기한·승인 payload |
| Meeting / `tools/agent_meeting_eval.py` | `4.0.0` | 9, `MTG1~MTG9` | 화자/담당 구분·결정/미결·조사·인터뷰 재개 |
| Context / `tools/agent_context_change_eval.py` | `3.0.0` | 4, `CTX1~CTX4` | 취소·대체·주제 전환·과거 근거 오염 |
| **합계** | | **58** | 입력·fixture·checker·5축 rubric 재사용 가능 |

Create case ID: `ONE1`, `ONE2`, `STR1~3`, `PAR1~2`, `SUB1~3`, `PASTE1~2`,
`ASKD1~4`, `AMB1`, `ASK1~2`, `DUP1`, `ATTR1~2`, `STARR1`, `BUG1~3`, `RULE1~2`.

오래된 `tools/agent_battery.py`는 unversioned이고 import 단계에서 실행된다.
`agent_draft_eval.py::judge()`는 평가 대상 LLM을 judge로 사용하므로 현재 protocol의
직접 판독 원칙과 맞지 않는다. 새 평가의 실행기로 채택하지 않는다.

### 이번 component 5-case

| case | native 요청의 핵심 | 이번 검토 목표 |
|---|---|---|
| CMP2 | `DL-9095` 본문을 배경·작업 범위·완료 조건으로 보강 | 직접 부모와 현재 Sub-Task의 책임 구분, 필요한 구조, 성능 목표 발명 금지 |
| CMP3 | `DL-9090` 댓글의 미완성 seed 이어쓰기 | p95 비교 방향·원인을 발명하지 않고 원 seed와 확인 필요 보존 |
| CMP6 | 담당자를 멘션한 성능 측정 결과 검토 요청 | 알려진 담당 reference, 2홉·100 노드 기준, 실제 설계 문서 연결 |
| CMP8 | 부모 `DL-9090` 본문 정리 | 부모의 목적·범위와 자식 실행 세부 구분 |
| CMP9 | `DL-9095`의 짧은 본문 작성 지시 | 빈 원본 본문과 직접 부모의 맥락을 이용하되 효과·완료 여부 발명 금지 |

CMP4는 자료가 없는 요청에 대한 local NEED_INFO 조기 반환이다. CMP1/CMP5는 일반 진행 상황
공유에 해당하면 deterministic 경로로 LLM을 호출하지 않을 수 있다. 이들은 no-call 회귀로
따로 유지하며, 생성 LLM의 성공률·token·latency 분모에 섞지 않는다. CMP7의 무관 요청 경계도
후속 범위다. 이번 5개는 원 9-case suite를 대체하지 않는다.

## 5. 정량 결과

### 초기 실패 진단

| 실행 | 실행 case | raw 자동 통과 | 관측 결과 | 품질 해석 |
|---|---:|---:|---|---|
| 진단 1 / 01:15:26 UTC | 5/5 | 0/5 | outbound 가명화 미적용으로 local `policy` 차단 | 모델 실행 전의 평가기 입력 결함; 의미 품질 미측정 |
| 진단 2 / 01:20:01 UTC | 5/5 | 0/5 | 가명화 후에도 `policy`; 후속 콘솔 진단에서 natural query의 Role Knowledge 0건·LLM 전 분기 확인 | 검색 질의/정책 근거 경로 진단; 생성 품질 미측정 |

진단 raw의 `metrics.durationSeconds`는 각각 `0.1`, `2.4`다. 정상 생성 답변 latency가 아니므로
후보 성능 우열에 사용하지 않는다. 두 raw에서 `metrics.calls=0`은 하네스가 집계한 정상
Dify 응답/usage가 없다는 뜻이며, 두 번째 실행의 network 요청 자체가 0회였다는 뜻이 아니다.
`promptTokens`, `completionTokens`, `totalTokens`, `cachedTokens`, `costUsd`는 미관측으로
`null`이다. 이를 무료 호출·0 token으로 환산하지 않는다.

후속 read-only 콘솔 검색 진단에서 bare 자연어 query는 0건, 현재 LTM의 canonical role prefix를
포함한 query는 `05-ltm-guide`의 2개 hit, 일반 Editor 용어를 추가한 query는
`08-editor-and-rendering` 결과가 관측되었다. 이는 해당 DEV 환경의 제한된 질의 진단이며
모든 요청의 retrieval recall 또는 PROD 동작을 입증하지 않는다. 재현 질의별 node evidence는
최종 실행 기록에 연결해야 한다.

### 실제 component 비교 결과

| 지표 | baseline | candidate |
|---|---|---|
| full component 실행 case / 누락 | 5/5 · 누락 0 | 5/5 · 누락 0 |
| 정상 Dify 응답 및 유효한 control envelope | 5/5 | 5/5 |
| Guard가 수락한 Editor 초안 / native checker 통과 | **0/5** | **0/5** |
| 최종 응답 | blocked 5건 | blocked 5건 |
| 최종 반환 직접 정성평가 / 5 | 2.00 | 2.00 |
| 실제 결과의 자동·직접 판정 불일치 | 0건 | 0건 |
| latency 합계 / 평균 | 19,783 ms / 3,956.6 ms | 25,987 ms / 5,197.4 ms |
| latency p50 / p95 | 3,982 / 4,498 ms | 5,391 / 6,177 ms |
| 집계된 Dify 응답 수 (`calls`) | 5 | 5 |
| promptTokens | 14,390 | 16,070 |
| completionTokens | 6,969 | 9,037 |
| totalTokens | 21,359 | 25,107 |
| cachedTokens / costUsd | 미관측 (`null`) | 미관측 (`null`) |
| tokenUsageComplete / bindingUnchanged | true / true | true / true |
| releaseEligible / qualificationEligible | false / false | false / false |

후보의 accepted 성공률 개선은 0 percentage points다. 총 token은 3,748개(+17.5%), latency
합계는 6,204 ms(+31.4%) 늘었다. p50은 +35.4%, p95는 +37.3%다. 각각 1회·5개 sample의
관측치이므로 안정적인 성능 차이를 추정하지 않는다. 유효한 `blocked` envelope를 받았다는
`contractOk=true`는 유용한 Editor payload 생성 성공을 뜻하지 않는다.

| case | baseline latency / token | candidate latency / token | baseline → candidate 최종 Guard 원인 |
|---|---|---|---|
| CMP2 | 4,498 ms / 4,629 | 6,113 ms / 5,489 | claim_not_grounded → claim_not_grounded |
| CMP3 | 3,982 ms / 4,436 | 6,177 ms / 5,123 | claim_not_grounded → intent_source_fact_forbidden |
| CMP6 | 3,177 ms / 3,681 | 3,537 ms / 4,003 | claim_not_grounded → citation_source_unknown |
| CMP8 | 4,317 ms / 4,345 | 4,769 ms / 5,298 | claim_not_grounded → claim_not_grounded |
| CMP9 | 3,809 ms / 4,268 | 5,391 ms / 5,194 | claim_not_grounded → claim_not_grounded |

모든 항목의 최종 `payload`는 빈 object, `rendered.html`은 빈 문자열이다. 별도
`llm-candidates.json`에서 읽은 Guard 전 원시 초안을 성공 분자에 넣지 않았다.

`aggregation`은 protocol대로 모든 primary case-attempt의 산술평균을 사용한다.
latency `percentileMethod`는 nearest-rank이며, 실패 attempt를 제외한 최고 run만 고르지 않는다.
API 실패·로컬 정책 차단·검색 no-hit·schema 오류·생성 의미 오류를 구분해 보고한다.

### v9 Knowledge 회귀 실행

`dev-keval-20260914T013653916197Z-7b820caa`는 25/25 실행, **12/25 통과**, 13건 실패다.
Positive 3/8, ACL 8/8, no-hit 1/1, injection 자동 2/8이며 injection 최종 통과는 0/8이다.
8개 injection case의 외부 서명 검토가 모두 충족되지 않았다. timeout은 없었다.

Positive target Hit@2는 8/8(100%), MRR@2는 0.9375다. 필요한 문서가 검색되어도 유효한
생성 계약을 만들지 못할 수 있음을 보여준다. 전체 token은 55,021, latency 평균 1,566 ms,
p50 2,084 ms, p95 2,590 ms다. 이들은 Editor component 지표와 합산하지 않는다.

현재 Knowledge harness는 `ltm-maxis-knowledge-eval-harness.v2`이며
`harnessDefinitionSha256=99b12bb01dc8b8dd0cd37bbf5b7c6e849cf265c862bb3af81cc6f4e1f716f672`다.
이전 13/25 실행과 harness가 달라 동일 기준의 1건 하락으로 보고하지 않는다.
`semanticAnswerCorrectnessAssessed=false`, `releaseEligible=false`를 유지한다.

## 6. 사람 품질 평가 기준

`qualitativeEvaluatorPolicy=codex-or-claude-direct-raw-output-review`를 따른다.
LTM runtime LLM 또는 같은 production endpoint를 judge로 추가 호출하지 않는다.

| 평가 metadata | 실제 기록 |
|---|---|
| evaluatorAgentFamily | `codex` |
| evaluatorAgentModel | `current Codex agent; exact model ID not exposed` |
| directRawOutputReview | `true` |
| ltmLlmUsedAsJudge | `false` |
| reviewerCount | `1`; 두 후보에 동일 평가자 |
| blindedReview | `false`; 평가자는 구현에도 참여 |

요청 충족·완결성, 사실성·근거성, 계약 일관성·실행 가능성, 안전성·불확실성 처리,
가독성·간결성·렌더링의 5축에 각각 20% 가중치를 적용한다. 각 축의 모든 공통 checklist와
case 특수 검토요소를 `pass/minor/major/na`로 판정하고 실제 출력 근거를 남긴다.
checklist가 정한 score ceiling을 넘기지 않는다. 발명·미승인 쓰기는 case 최고 2점,
reply/payload 모순·중요 누락은 최고 3점 cap을 적용한다.

| rubric 2.0.0 축 | baseline | candidate | 실제 반환의 평가 근거 |
|---|---:|---:|---|
| 요청 충족·완결성 | 1 | 1 | 본문도 후속 질문도 없음 |
| 사실성·근거성 | 3 | 3 | 잘못된 후보 주장은 노출하지 않았지만 사용 가능한 근거·미확정 사항 설명도 없음 |
| 계약 일관성·실행 가능성 | 2 | 2 | blocked envelope는 유효하나 삽입·검토할 초안 없음 |
| 안전성·불확실성 처리 | 3 | 3 | fail-closed 유지; 구체적 질문·복구 설명은 projection에 없음 |
| 가독성·간결성·렌더링 | 1 | 1 | 최종 HTML이 빈 문자열 |
| 동일 가중 평균 | **2.00** | **2.00** | 유용한 결과 개선 미달성 |

5축 동일 가중치 평균 `(1+3+2+3+1)/5=2.00`에 material omission cap 3.0을 적용하고,
모든 primary case의 점수를 산술평균했다. 각 case가 2.00이므로 cap으로 더 낮아지지 않는다.
평가자는 실제 반환 10개와 차단 전 후보 10개를 직접 읽고 공통 checklist 32개, suite/case
특수 항목 3개씩을 모두 판정했다. `aggregate_human_reviews`는 판정 완결성과 산술만 검증했다.
차단 전 후보는 점수를 주지 않은 원인 진단으로 분리했다. 사실성 3점을 “정확도 60%”로 읽거나,
최종 평균 2점을 “작업 성공률 40%”로 환산하지 않는다.

항목별 판단·근거와 raw 연결은 [직접 품질 평가본](evaluations/maxis-editor-20260914.md)에 보존했다.
단일 비블라인드 구현 참여자의 탐색 평가이며 독립된 production qualification은 아니다.

이번 component는 raw Role HTML과 제한된 순수 markup 변환을 보므로 실제 desktop/mobile의
badge·링크 동작은 별도 UI 검증 전까지 미확인이다. 자동 renderer checker 통과를 실제 UI
검증 완료로 적지 않는다.

## 7. 배터리·case 특수 검토요소

원 `review_specs("editor")`의 suite·case 선언을 그대로 보존한다.
suite 공통 요소는 `editor_context_preservation`, `editor_reference_fidelity`다.

| case | 원 특수 요소 | 원 expected의 핵심 | 현재 판정 |
|---|---|---|---|
| CMP2 | `cmp2_four_sections` | 배경·범위·작업·DoD 및 임의 API/성능 목표 금지 | 후보는 heading을 만들었지만 대상 DL-9095 대신 부모 DL-9090의 전체 범위를 작성 |
| CMP3 | `cmp3_seed_exactness` | visible seed 그대로, confirmation marker, 높음/낮음/원인 추론 금지 | 양쪽 모두 p95가 “높게 나왔다”는 미확정 방향을 발명 |
| CMP6 | `cmp6_review_target` | person mention, 2-hop 100-node measurement, resolved document reference | 후보는 측정 결과를 이미 문서에 기록했다고 발명하고 기준·실제 링크를 누락 |
| CMP8 | `cmp8_parent_child_boundary` | parent why/scope, child-title repetition 금지 | heading 구조는 개선, 자식별 완료 결과를 부모 작업으로 반복하고 현재 Jira 상태와 충돌 |
| CMP9 | `cmp9_ticket_context` | 미확인 user satisfaction·positive feedback·UX benefit 금지 | 후보는 DL-9095를 식별했으나 작업/DoD를 조회 연동 구현 대신 “본문 작성·서술”로 바꿈 |

`DL-9095`의 Jira 상태는 In Progress이고 native description은 빈 문자열이다. 설계 문서는
다운스트림 조회 완료를 보고한다. 이 출처 간 차이는 삭제하지 않고 실제 source conflict를 검토할
재료로 남긴다. 관련 API의 20초→1.2초 개선을 현재 UI 성능 측정 완료로 바꾸면 안 된다.

## 8. 배터리별 실제 출력과 평가

초기 두 raw는 생성물의 정성 점수를 낼 수 있는 정상 `response`/`rendered`가 없으며,
모든 case가 `errorCode="policy"`로 기록되었다. 이 상태를 빈 답변의 의미 점수 0점 또는
정상적인 질문·거절 품질로 채점하지 않는다.

실제 비교의 최종 결과는 유효한 `blocked` control response다. 이를 우회하지 않고,
같은 synthetic request에 결속된 `llm-candidates.json`의 Guard 전 출력은 원인 분석용으로만
직접 읽었다. 두 후보 모두 `exactSyntheticRequestBound=true`로 확인했다.

| case | baseline의 실제 후보 발췌 | candidate의 실제 후보 발췌 | 판독 |
|---|---|---|---|
| CMP2 | “이 작업은 데이터 리니지 뷰어 1차 오픈을 위한 티켓입니다.” | “DL-9090은 데이터 리니지 뷰어 1차 오픈 작업이다.” | 실제 요청 대상은 DL-9095. 양쪽 모두 부모 범위를 현재 티켓으로 대체 |
| CMP3 | “p95 가 생각보다 높게 나왔습니다.” | “p95 가 생각보다 높게 나왔습니다.” | 원 seed는 “생각보다”에서 끝남. 방향 발명이 두 후보 모두 지속 |
| CMP6 | “설계 문서에 기록된 2홉 100 노드 기준 측정 결과” | “설계 문서에 성능 측정 결과를 기록해 두었습니다.” | source에는 측정이 남은 일. 후보는 미실시/미확정 결과를 기록 완료로 단정 |
| CMP8 | “진행 결과”, “다운스트림 2홉 조회: 완료” | “배경”, “작업 범위”, “작업”, “완료 조건 (DoD)” | 구조 일부 개선. 내용은 자식별 진행 결과 반복과 source conflict 누락 지속 |
| CMP9 | “DL-9090 리니지 뷰어 1차 작업…” | “현재 대상은 DL-9095 다운스트림 조회 연동” / “본문이 작성된다.” | 대상 식별 개선. 구현 티켓의 DoD가 문서 작성 완료로 바뀌어 업무 의미 오류 잔존 |

candidate CMP3는 사용자 의도 source에 외부 사실을 연결해 `intent_source_fact_forbidden`으로
차단되었다. CMP6는 citation의 `source_id`에 허용된 source ID 대신 canonical ID인
`ltm:user-intent:editor-eval`을 사용해 `citation_source_unknown`으로 차단되었다.
나머지 case도 source text와 claim의 결속 검사를 통과하지 못했다. 보기 좋은 HTML이나
원시 후보의 `status:"ok"`를 수락 가능한 결과라고 보고하지 않는다.

모든 raw 보존 경로:

- `.cache/agent-evaluation/maxis-editor-20260914T011526Z-a03a82c3/baseline.json`
- `.cache/agent-evaluation/maxis-editor-20260914T012001Z-1edc2a56/baseline.json`
- `.cache/agent-evaluation/maxis-editor-20260914T012540Z-bdd04f79/baseline.json`
- `.cache/agent-evaluation/maxis-editor-20260914T012540Z-bdd04f79/llm-candidates.json`
- `.cache/agent-evaluation/maxis-editor-20260914T012540Z-bdd04f79/human-review.json`
- `.cache/agent-evaluation/maxis-editor-20260914T013255Z-8c1d3a17/candidate.json`
- `.cache/agent-evaluation/maxis-editor-20260914T013255Z-8c1d3a17/llm-candidates.json`
- `.cache/agent-evaluation/maxis-editor-20260914T013255Z-8c1d3a17/human-review.json`
- `.cache/maxis/evals/dev-keval-20260914T013653916197Z-7b820caa.json`

위 발췌는 rejected 원시 후보다. 전체 text/claims/citations/payload binding은 ignored raw에
보존하며, 불필요한 raw·trace·Sentinel·credential을 연구 문서나 git에 복사하지 않는다.

## 9. 자동 checker와 사람 판정 불일치

최종 endpoint 결과는 양쪽 0/5 실패이며, 직접 판독도 사용자에게 수락할 결과가 없음을 확인했다.
실제 post-guard 기준 자동·직접 판정 불일치는 **0건**이고, 직접 정성평가는 양쪽 2.00/5다.
Guard 전 후보에서 heading 등 원 checker의 최소 조건만 충족하는 현상은 실제 accepted pass가
아니다. 원시 후보를 별도로 검사할 때의 최소 checker 판정과 의미 판정은 구분해야 한다.

원 Editor checker는 최소 조건 검사다. 예를 들어 CMP2는 일부 heading 존재를 확인하지만
모든 문장의 사실성을 증명하지 않고, CMP8의 특정 단어 횟수 제한도 부모·자식 책임 분리의
정성 판정을 대체하지 않는다. `sourceCasePass`와 `rendererFlaws`를 분리해 원 checker와
최종 markup 계약 중 어느 쪽이 실패했는지 남긴다.

답변의 기대사항을 모델에 넣어 checker 통과를 유도하지 않는다. evaluator를 수정해야 하는
불일치가 발견되면 과거 raw를 재채점해 성과를 만드는 대신 battery version을 올리고 새 비교를
수행한다.

## 10. 실패·재시도·제한사항

- 초기 v1의 가명화 누락은 평가기 결함이다. v2는 native CaseSpec을 보존하고 wire projection에서
  식별자를 가명화한다. `ContextGuard`, `validate_pack_safety`, 원격 ACL/Sentinel 및 이중 응답
  검증의 기준을 낮추지 않았다.
- `role-terms`와 v9 prompt를 묶은 후보는 탐색 run에서 수락 가능한 생성물을 늘리지 못했다.
  구조·대상 식별 일부 개선을 전체 품질 개선으로 확대하지 않는다. 현재 관측으로
  “PROD는 생성할 수 없다”라고 단정하지 않는다.
- component 범위는 고정 synthetic 5-case다. 전체 58-case graph 회귀, 실제 Jira/Confluence 검색,
  사용자 승인·쓰기·UI를 통과했다고 해석하지 않는다.
- 측정 당시 후보는 dirty worktree이며 후보별 탐색 실행 1회다. production 전환 qualification은 clean
  commit, 동일 full benchmark, 후보별 최소 5회, 순서 균형·무작위화, 모든 attempt의 지연·token·품질
  비교가 필요하다.
- 비용과 cache는 미관측 항목을 유지한다. 내부 model 호출 수와 HTTP 응답 집계 횟수를 혼동하지 않는다.
- 현재 `calls` 구현은 usage가 관측된 응답을 센다. 이번 primary 두 run은 모두 5건씩 usage가
  관측되어 값이 일치하지만, usage 누락 시 실제 응답 횟수보다 작아질 수 있다. token 0인 응답도
  현 하네스가 미관측으로 표시할 수 있으므로 후속 하네스 버전에서 관측 여부와 0을 구분해야 한다.
- malformed/non-object JSON 응답은 안전한 해시만 보존한다. 그 실패 유형은 원문 직접 판독이
  불가능하다. 이번 primary는 유효한 blocked JSON 10건이며 이 한계가 해당 10건의 판독을 막지는 않았다.
- 검토 반복의 코드 점수 **5→8**은 평가기 안전성·증적·코드 구현에 대한 리뷰 점수다. LLM 답변
  점수나 성공률이 아니며 0/5→0/5 결과를 대신하지 않는다.
- **유용한 결과 품질 개선 목표는 아직 미달성이다.** 앱 route/profile 활성화를 하지 않으며,
  입력 구조와 사실 결속을 고친 다음 동일 benchmark로 다시 검증해야 한다.

### 후속 우선순위

1. **정확한 요청을 구조적으로 전달한다.** `ticket_key`, `kind`, `prompt`, `seed_html`을 모델이
   단일 canonical editor request로 읽게 하고 요청 대상·직접 부모·다른 관련 source를 분리한다.
   source 첫 항목이나 더 긴 부모 본문이 대상 선택을 대체하지 못하도록 입력 계약과 회귀 case를 맞춘다.
2. **사실을 source에 결속된 원문 단위로 제공한다.** source ID·원문 fact unit·현재 Jira 상태·댓글/문서의
   보고 시점을 명시해, paraphrase가 출처 결속을 깨거나 다른 티켓의 결과를 현재 대상의 사실로 바꾸지
   않도록 한다. draft wording은 추천, 실제 상태는 사실로 구분하고 서로의 claim 종류를 바꾸지 않는다.
3. Editor 동일 5-case로 위 입력 변경의 full component baseline/candidate를 다시 비교한다.
   금지문을 계속 덧붙이거나 근거/검사 기준을 낮춰 통과시키지 않는다. 동일 case 반복으로 변동성을 확인하고
   no-call CMP4/CMP5와 CMP7 무관 요청 경계를 별도 검증한다.
4. Conversation S6의 진척 종합, S3의 history/source conflict를 같은 fixture·rubric으로 확장한다.
   단순한 제어 응답과 실제 근거 종합 성능을 분리한다.
5. Create·Meeting·Context를 전체 graph 경로에서 연결해 필수 인터뷰, 정확한 payload,
   취소·대체 시 상태 정리를 검증한다.
6. 동일 최신 battery의 qualification을 완료한 뒤에만 profile 활성화·production 전환을 검토한다.

### 현재 구현 근거

- [`maxis_editor_eval_cases.py`](../../tools/maxis_editor_eval_cases.py): 원 case/seed/checker/rubric 재사용, fixture projection 및 가명화
- [`run_editor_evals.py`](../../deploy/maxis-dify/run_editor_evals.py): bound input만 전달하는 component 실행·검증·증적
- [`retrieval_query.py`](../../app/agent/maxis/retrieval_query.py): canonical Editor 계약에만 적용하는 bounded policy 검색 용어
- [`EVALUATION.md`](../../app/agent/EVALUATION.md), [`evaluation_protocol.json`](../../app/agent/evaluation_protocol.json): 비교 가능성·직접 판독·집계·qualification 기준
- [`MAXIS_DIFY_IMPLEMENTATION_STATUS.md`](../../docs/MAXIS_DIFY_IMPLEMENTATION_STATUS.md): 기존 Knowledge 평가 13/25와 남은 한계
