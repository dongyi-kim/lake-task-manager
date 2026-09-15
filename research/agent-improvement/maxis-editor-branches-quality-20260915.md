# MAXIS Editor 단위 기능 분리 — v16 구현·DEV 검증

Editor의 **본문 작성·댓글 작성·연결 검사를 별도 단위로 분리**했다. 실제 요청 5건 모두 올바른 분기에서 LLM을 1회만 실행했다. 동일 5-case 탐색 비교에서 총 토큰은 **22,841 → 11,518(49.6% 감소)**였지만, 문서 링크 선택·현재 업무 범위·과거 완료 이력 처리 결함이 남았다. **품질 통과나 운영 활성화로 처리하지 않는다.**

작업 브랜치는 `codex/maxis-dify-migration`. DEV Editor만 게시했으며 Core/PMO·기존 API key·PROD는 변경하지 않았다. LTM의 활성 profile은 비어 있고 모든 route는 `local`이다.

## 측정 식별자

| 항목 | baseline | candidate |
|---|---|---|
| candidateCommit | `f1ee823ba854314cd4bfad7861a76df12b607b44` | `70f510400c8a05b21c099d1e8ad44b1945afc3f2` |
| promptVersion | `ltm-maxis-dify-2026.09.15-editor-scope-v15` | `ltm-maxis-dify-2026.09.15-editor-units-v16` |
| Editor 게시 UUID | `c4ddedaf-b400-48de-8d8b-1294b21ec92a` | `6cdcdff7-c34e-45e4-8f7e-3f5d5f4311f4` |
| runGroupId | `maxis-editor-20260915T133810Z-1225be87` | `maxis-editor-20260915T140612Z-c475c70f` |
| protocolVersion / rubricVersion / batteryVersion | 3.0.0 / 2.0.0 / 4.0.0 | 동일 |
| runKind / candidateDirty | exploratory / false | exploratory / false |

공통 `batteryManifestSha256`: `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406`.
공통 `specializedReviewSpecSha256`: `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b`.
공통 `dataManifestSha256`: `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d`.
공통 `runtimePackageSetSha256`: `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645`.
공통 `evaluationHarnessTreeSha256`: `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0`.

### 구현한 분리

~~~mermaid
flowchart LR
    K[Knowledge Guard] --> P{정책 허용?}
    P -->|아니오| N[거절 · LLM 0회]
    P -->|예| I[코드: 입력 검증·원문 단위 투영]
    I --> R{코드: 검증된 요청 모드}
    R -->|본문| D[Description LLM]
    R -->|댓글| C[Comment LLM]
    R -->|허용된 합성 검사| T[Control LLM]
    D --> A[단일 결과 선택]
    C --> A
    T --> A
    A --> H[코드: HTML·출처 조립]
    H --> G[원격 Contract Guard]
    G --> L[로컬 ResponseGuard]
~~~

| 단위 | 하는 일 | 하지 않는 일 |
|---|---|---|
| Editor Input / Route | 원본 입력·모드·계약 검사, 출처 단위 투영, 배타적 분기 | LLM으로 업무 모드 추측, 원본 근거 변경 |
| Description LLM | 배경·범위·작업·완료 조건의 `editor-plan.v2` 생성 | 댓글·제어 응답·승인·Jira 변경 |
| Comment LLM | comment section의 `editor-plan.v2` 생성 | 본문 전체 구성·연결 검사·Jira 변경 |
| Control LLM | 허용된 activation/retrieval 합성 검사의 `remote-role-result.v1` 생성 | 일반 작성 요청이나 깨진 입력의 대체 처리 |
| Assembler + 양쪽 Guard | 선택한 단위로 HTML·인용 조립, 원본 EvidencePack 기준 검증 | 의미상 올바른 대상·현재성까지 보증 |

Editor는 13노드·13엣지에서 **17노드·19엣지**가 됐다. 노드 수가 늘어도 실제 LLM은 한 분기만 실행한다. 새 canonical Role·Dify 앱·API key는 만들지 않았다. 모델·Knowledge·endpoint·게시 ID의 DEV/PROD binding 구조를 유지했다.

이전 최상위 `oneOf(remote-role-result, editor-plan)`를 제거했다. 본문/댓글에는 각 section만 허용하는 단일 plan 계약, 제어에는 단일 remote result 계약을 준다. plan 내부의 block 종류 선택은 그대로다. 부족한 필수 사실은 `needs_input`과 구체 질문으로 반환하며 완성 초안을 가장하지 않는다.

system prompt UTF-8 크기는 이전 8,150바이트에서 본문 3,230 / 댓글 3,325 / 제어 5,360바이트로 분리했다. 노드별 prompt·schema·모델·selector·edge를 validator와 환경 생성기가 함께 검증한다. 생성 DSL은 [editor_graph.py](../../deploy/maxis-dify/editor_graph.py)로 재현한다.

## 비교 가능성 및 evidence 선택

공통 `benchmarkKey`: `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74`.

| 식별자 | baseline | candidate |
|---|---|---|
| candidateKey | `e31828bf1027fafcb6924920f6a02c144e0eee839cf0d3b23fd0ad7215fb4861` | `29213e28ec37afcf71d6f56b8decfab4b144985ba28f757d0b5aa8c360a69b15` |
| comparabilityKey | `33a00004c522ae60d42bb4cef3a2132b68e9f2fd85d4d38e79cc6dbdceb16fcd` | `104d40e9c2729453c74fa88486b833b0cd772d55e8c559f52aab748d4d1bdbcb` |

`selectionPolicy=complete-run-no-substitution`: 후보별 5건 전체를 보존하고 실패를 나중의 성공이나 내부 plan으로 대체하지 않는다. 동일 fixture·checker·rubric의 탐색 비교이며 단일 변경의 인과 분리는 아니다. 이번 새 baseline을 사용하며 이전 v15의 3.54/5와 섞지 않는다.
`aggregation`: case별 5축 동일 가중 평균 후 cap을 적용하고, 전체 case-attempt 점수를 산술평균한다.

두 raw의 `candidateTreeSha256`는 같은 `ffc0a679…`로, 변경하지 않은 앱 평가 대상 tree를 나타낸다. 이것만으로 배포 전체의 동일성을 주장하지 않는다. commit·native published UUID·DSL SHA·semantic graph SHA를 별도로 고정했다. 전체 값과 artifact SHA는 [deployment evidence](../../deploy/maxis-dify/deployments/dev-editor-branches-2026.09.15.yml)에 기록한다.

## 실행 조건

모델은 양쪽 모두 `gpt-5.4-mini`, temperature 0.1, Editor token limit 4,096이다. simple model은 미사용, provider/structured backend는 `dify`, backend version은 `legacy-internal`, fallback은 금지다. runtime profile은 `dev-inactive-stateless-component-v1`이다.

| 실행 계약 필드 | 양쪽 값 |
|---|---|
| simpleModel | not-used |
| structuredOutputBackend / structuredBackendVersion | dify / legacy-internal |
| structuredBackendFallbackPolicy | forbid |
| repetitions / candidateOrderIndex | 각1회 / baseline 1, candidate 2 |
| retryPolicy | no-retry-no-fallback-all-attempts-retained |
| cachePolicy | no-client-history-no-local-state |
| processIsolation | separate-process-stateless-fresh-pack-each-case |

기존 `launch_editor_evals.py`로 고정 가명 fixture를 보내 baseline → candidate 순서로 각1회 실행했다. 후보별 별도 process, case별 fresh pack이며 history·local state·retry·fallback은 없다. 교차 실행의 source 내용·ID는 같고 `updated_at`/`retrieved_at` 실행 timestamp만 다르다. provider의 cachedTokens·비용은 미관측이므로 cache 조건 일치나 금액 절감을 주장하지 않는다.

`providerEndpointIdentitySha256`: `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7`. credential·release sentinel·실업무 데이터는 보고서에 포함하지 않는다.

## 배터리 범위

| 검증 | 전체 / 실행 / 누락 | 의미 |
|---|---|---|
| maxis-editor-component v4 | 5 / 5 / 0, 후보별 1회 | EditorDraft + 순수 markup의 실제 생성 비교 |
| DEV 서비스 canary | 3 / 3 / 0 | Core·Editor·PMO 합성 제어 계약·연결 확인 |
| candidate native node trace | 5 / 5 / 0 | 같은 요청의 실제 분기·LLM 횟수·근거 전달 확인 |

기존 CMP2/3/6/8/9를 재사용했다. Knowledge 25-case, 전체 58-case suite, full compose 및 브라우저 UI 회귀는 이번에 실행하지 않았다. canary 3/3과 contract 5/5는 작성 품질 합격을 뜻하지 않는다.

native trace에서 5건 모두 예상 분기의 LLM 1개만 성공했다. CMP2/8/9는 Description, CMP3/6은 Comment다. 원래 Start pack 및 Knowledge Guard pack이 로컬 원본과 같고, 준비된 모델 입력도 로컬 asset 결과와 같았다. 원문 단위는 각 46개, 모델 입력은 7,907–7,972바이트였다. 이 확인은 추가 모델 실행이 아닌 기존 실행 기록 조회다.

## 정량 결과

| 지표 | baseline v15 | candidate v16 |
|---|---:|---:|
| 전체 attempts / 응답 calls / retries | 5 / 5 / 0 | 5 / 5 / 0 |
| 총 시간 | 17.6초 | 19.6초 |
| case latency p50 / p95 | 3,863 / 5,104ms | 4,765 / 5,342ms |
| prompt / completion tokens | 19,970 / 2,871 | 8,339 / 3,179 |
| 총 tokens | 22,841 | 11,518 |
| attempt당 총 tokens | 4,568.2 | 2,303.6 |
| attempt당 시간 | 3.52초 | 3.92초 |
| cachedTokens / costUsd | null / null | null / null |
| 구조 contract 수용 | 5/5 | 5/5 |
| 자동 checker·renderer 통과 | 4/5 | 3/5 |
| 기술 실패 / blocked | 0 / 0 | 0 / 0 |

총 토큰 49.6%, prompt token 58.2% 감소지만 completion은 증가했다. 시간은 개선되지 않았다. `percentileMethod=nearest-rank-over-all-case-attempts`로 latency를 계산했고 calls는 받은 Dify 응답 수다. candidate 내부 LLM 1회는 별도 native trace로 확인했다.

## 사람 품질 평가 기준

구현자와 별도인 동일 Codex 평가자가 두 후보의 최종 출력과 fixture 전문을 직접 읽었다. exact runtime model ID는 노출되지 않아 `current Codex agent`로 기록했다. reviewer 1명, 비blind이며 LTM LLM/같은 production endpoint를 judge로 호출하지 않았다. `directRawOutputReview=true`, `ltmLlmUsedAsJudge=false`.

| 평가 계약 필드 | 양쪽 값 |
|---|---|
| qualitativeEvaluatorPolicy | codex-or-claude-direct-raw-output-review |
| evaluatorAgentFamily / evaluatorAgentModel | codex / current Codex agent; exact runtime model ID not exposed |
| reviewerCount / blindedReview | 1 / false |

rubric 2.0.0의 요청 충족·사실성·계약 실행성·안전성·가독성 5축 각 20%, 0.5점 간격을 적용했다. 공통160+특수15 항목 모두 실제 근거를 기록하고 결함별 축 상한과 case cap을 적용한다. 발명/미승인 변경은 최대2, 중요 누락/응답·payload 모순은 최대3이다.

직접 평균은 새 baseline **3.38 → candidate 3.52/5**다. 단일 탐색의 +0.14를 품질 향상 확정으로 해석하지 않는다. 두 후보 모두 유용한 결과 1/5는 **완성 초안 0 + 필수 확인 질문 1**이다. 자동 통과 건수를 사용 가능 초안 건수로 바꾸지 않는다.

| 직접 점수 | baseline | candidate |
|---|---:|---:|
| CMP2 / CMP3 / CMP6 | 3.80 / 4.20 / 3.00 | 4.20 / 4.60 / 3.00 |
| CMP8 / CMP9 | 3.30 / 2.60 | 2.90 / 2.90 |
| 5축 평균(위 순서) | 3.0 / 4.0 / 3.2 / 3.7 / 3.5 | 3.0 / 4.1 / 3.6 / 4.0 / 3.7 |
| 공통 pass / minor / major / na | 67 / 28 / 24 / 41 | 75 / 23 / 21 / 41 |
| 특수 pass / minor / major / na | 4 / 5 / 6 / 0 | 3 / 7 / 5 / 0 |

CMP6은 중요 참조 누락으로 두 후보 모두 3.0 cap을 적용했다. baseline CMP9는 중요 누락 cap 대상이나 원점수 2.6이라 추가 하향은 없다. candidate CMP9에는 실제 점검·검증 본문이 있어 그 cap을 복사하지 않았다. cap 발생률은 2/5 → 1/5다. CMP3의 높은 안전성 점수는 완성 댓글 제공 여부와 별개다.

항목별 판정·근거·축별 점수·집계는 [baseline 직접평가](evaluations/maxis-editor-20260915T133810Z-baseline.md)와 [candidate 직접평가](evaluations/maxis-editor-20260915T140612Z-candidate.md)에 보존한다.

## 배터리·case 특수 검토요소

| case | 고정 기대 | candidate 관찰 / 의미 |
|---|---|---|
| CMP2 | DL-9095 본문, 요청 대상·구조 | 부모 목적과 양방향 범위가 현재 하위 업무 초안으로 확장됨 |
| CMP3 | DL-9090 댓글, visible seed 보존·수치 비발명 | p95 실제 값·비교 기준을 정확히 질문하지만 최종 HTML은 비어 seed 계약 미충족 |
| CMP6 | DL-9090 댓글, 식별된 mention·설계 문서 참조 | mention은 맞고 링크도 있으나 필요한 Confluence 대신 Jira DL-9090 링크 |
| CMP8 | DL-9090 본문, 짧은 근거 기반 구성 | 과거 완료·현재 진행을 섞고 이미 주어진 범위·측정 조건 재확인 |
| CMP9 | DL-9095 본문, 부모 맥락을 참고하되 대상 한정 | 부모의 양방향 완료 이력이 현재 하위 작업·완료 조건을 지배 |

fixture의 부모 DL-9090/대상 DL-9095는 In Progress, 형제 DL-9093/9094는 Closed다. 설계 문서의 완료 이력·20초→1.2초와 현재 Jira 상태는 출처·시점을 나눠야 한다. 해당 측정은 CMP3에서 새로 요구한 p95 값의 대체 정답이 아니다.

## 배터리별 실제 출력과 평가

candidate CMP3은 “성능 측정의 실제 값이 필요합니다. 예: p95가 몇 초인지 알려주세요.”와 “비교 기준이 필요합니다. 예: 이전 값 또는 목표 값이 무엇인지 알려주세요.”를 반환했다. 이는 필요한 사용자 소유 사실의 유용한 확인이지 완성 댓글이 아니다.

CMP6은 “제안: 확인 필요: 2홉 100 노드 기준 성능 측정 결과를 검토해 주세요.”와 Jira 링크를 만들었다. 필수 설계 문서를 실제로 참조하지 못해 baseline에서 남았던 중요 참조 결함을 해결하지 못했다.

CMP2/9는 부모 업무의 업스트림·다운스트림 범위를 현재 대상의 작업·완료 조건으로 넓혔다. CMP8은 역사적 완료와 현재 진행 상태를 구분하지 못하고 기지 조건을 다시 묻는다. 입력에 원문이 도달하지 않은 문제가 아니라 **근거 선택·업무 범위 판단과 문안 작성이 한 의미 작업에 남아 있는 문제**로 후속 실험 대상을 좁힌다. 단일 실행만으로 원인을 확정하지 않는다.

## 자동 checker와 사람 판정 불일치

baseline 자동4/5 → candidate 자동3/5. candidate CMP2/8/9의 green은 schema/markup 조건 충족이며, 대상 확장·시점 충돌 같은 의미 결함은 잡지 못했다. CMP6은 자동/직접 모두 필수 참조 결함을 확인했다.

CMP3의 red에는 중요한 구별이 있다. 부족한 p95를 발명하지 않고 질문한 것은 안전하지만 frozen exact-visible-seed 계약은 충족하지 못했다. 실제 UI의 기존 seed 소실 여부는 이번 component 실행으로 확인하지 않았다. checker를 고치거나 실패를 통과로 재분류하지 않았으며, 정책을 바꾸려면 별도 battery version과 전체 재평가가 필요하다.

## 실패·재시도·제한사항

최종 관련 테스트 **1,284 통과·1 제외(202.53초)**. 제외된 현재 사용자 DPAPI round-trip은 별도 사용자 환경에서 **1 통과(0.35초)**했다. 별도 실행의 기존 pytest cache 권한 경고는 테스트 실패가 아니다. 이는 관련 suite 검증이지 전체 앱/UI 검증이 아니다.

독립 코드 리뷰는 **7 → 8/10, 2회**. 첫 리뷰의 동시 게시·receipt 유실 가능성을 수정해 외부 작업 전 배타 claim을 예약하고 실패도 기록한다. 생성 DSL은 기계적 변경이며, 변경 크기 검토에 따라 이번 단계를 일관된 Editor 분기 단위로 한정했다. agent 수 제한으로 같은 비작성 critic을 재사용했으므로 fresh/blind review는 아니다. 이 8/10은 LLM 출력 품질 점수가 아니다.

### 다음 최소 단계: 근거·범위 선택과 문안 작성 분리

1. **DraftSpec 선택 단위**: target의 현재 사실, 부모 배경, 과거 완료, 충돌, 필수 문서 ID, 부족한 사용자 사실만 분류·선택한다. HTML이나 자연어 완성 초안은 쓰지 않는다.
2. **결정적 검증 단위**: 원본 source/unit 참조·target 관계·필수 문서·누락 상태를 검사한다. 현재성의 의미 판별을 단순 문자열 규칙으로 해결했다고 간주하지 않는다. 정보 부족이면 필요한 질문만 반환한다.
3. **본문 또는 댓글 writer**: 검증된 DraftSpec만 받아 해당 형식의 문안을 작성한다. 대상·출처 재탐색, metadata 봉투 작성, 도구 실행은 맡기지 않는다.
4. **기존 Assembler·양쪽 Guard·승인 경계 유지**: writer 결과를 원본 EvidencePack으로 검사하고, 실제 변경은 기존 ActionExecutor의 exact payload 승인 이후만 허용한다.

위 2개 의미 단계는 아직 구현하지 않았다. 도입하면 요청당 최대2 LLM으로 늘 수 있으므로 동일 frozen 5-case 및 이후 전체 qualification으로 품질·총 토큰·시간을 재측정한다. 문단마다 에이전트를 만들거나 자동 재작성 loop를 추가하지 않는다. Core/PMO 내부 역할 분기는 이 변경과 분리해 진행한다.

현재 실험은 후보별1회·고정순서·단일 비blind 평가자이므로 우열/PROD 전환 판정에 부족하다. 운영 적용 전에는 중요 대상·참조 결함 해소, 전체 최신 battery의 균형 순서·최소5회 qualification, 실제 UI 승인·렌더링 검증, 환경별 release gate가 필요하다. **DEV 실험 자산만 갱신했고 LTM은 기존 로컬 경로를 유지한다.**
