# LTM MAXIS / Dify Workflow bundle

이 디렉터리는 Lake Task Manager가 사용하는 Dify Advanced Chatflow 3개의 검토 가능한
source bundle이다. DEV와 PROD의 환경 binding을 분리하고, import DSL을 materialize한
뒤 validator와 원격 release gate를 통과시키는 구조다.

Core source는 연결 검사와 업무 판단을 분리한 **16노드·17엣지**다.
양쪽 LLM과 원본 그래프를 검증한 뒤 환경값을 적용하며, 정책 거절과 기존 Guard는 유지한다.
DEV Core v23에 게시했으며 [Core 계약과 검증 범위](core-control-route.md)를 참고한다.
공유 schema 검증은 JSON 불리언과 숫자를 구분한다. 원본 보존·환경 변환을 포함한 관련 1,004회귀와
독립 검토를 통과했으며, 이 오프라인 결과를 실제 모델 품질 점수로 해석하지 않는다.

> 2026-10-08: **Editor 입력 v2를 DEV v24에 게시, canary 3/3·운영 비활성**.
> selector에서 writer의 plan 계약만 제거했다. 23노드·26엣지, 모델·Knowledge·최종 Guard는 유지한다.
> 기존 전체 배터리 재실행은 Editor 자동 **2/5**, Role 정상 **0/11**(blocked 10·오류 1)이다.
> 새 동일 패널의 [Editor 직접 평가](../../research/agent-improvement/evaluations/maxis-editor-component-20261008-input-v2.md)는 3.08→2.48/5·사용 가능 1/5→0/5다.
> 별도 [Role 후보 직접 평가](../../research/agent-improvement/evaluations/maxis-role-component-20261008-input-v2.md)는 2.02/5·업무 산출물 사용 가능 0/11이다.
> 이전 v23 패널 2.68은 별도 관측으로 보존한다. 단일 탐색·비블라인드 결과이며 품질 향상은 미입증이다. 운영 경로는 모두 local이다.
> [v24 게시·실측 증적](deployments/dev-editor-input-2026.10.08.yml)을 확인한다.

아래 v23은 Editor v24 게시 전의 이력이다. Core 게시 UUID는 v24에서도 유지했다.

> 2026-10-08: **Core v23 DEV 게시 완료, canary 3/3·운영 비활성**.
> 연결 검사만 Control LLM으로 분리했고 실제 Core 업무 9건은 기존 semantic LLM을 각각 한 번 사용했다.
> 기존 배터리 재실행은 Editor 자동 3/5, Role 정상 0/11(blocked 10·오류 1)이다.
> 같은 패널의 [Editor 직접 평가](../../research/agent-improvement/evaluations/maxis-editor-component-20261008-core-control.md)는 2.54→2.68/5이나 사용 가능 1/5→1/5다.
> [Role 후보 직접 평가](../../research/agent-improvement/evaluations/maxis-role-component-20261008-core-control.md)는 2.11/5·사용 가능한 업무 산출물 0/11이다.
> 단일 탐색 실행과 비블라인드 평가이며 전반 품질 향상은 미입증이다. canary·자동 계약 통과를 업무 성공으로 해석하지 않는다.
> Editor v22·PMO v20·모델·Knowledge·PROD 불변, active profile 없음·모든 route local.
> [v23 게시·실측 증적](deployments/dev-core-control-2026.10.08.yml)을 확인한다.

아래 v22는 Core v23 게시 전의 이력이다.

> 2026-10-08: **v22 DEV Editor 게시 완료, 당시 공통 canary 실패로 후보 품질 평가 미실행**.
> 내부 DraftSpec v2에서 동등한 target/constraint 선택을 current 하나로 통합했다.
> 회귀 **917개**와 두 독립 코드 검토를 통과했으나, 이것은 LLM 품질 검증이 아니다.
> 새 v21 기준선은 자동 **2/5**, 직접 품질 **2.52/5**, 사용 가능 **0/5**다.
> v22 연결 검사는 Editor·PMO 통과, Core의 모델 응답이 blocked여서 **2/3**이다.
> 공통 profile verified=false를 우회하지 않았다. 후보 Editor 5건·Role 11건은 아직 실행하지 않았다.
> Core/PMO·모델·Knowledge·PROD 불변, active profile 없음·모든 route local.
> [최신 증적](deployments/dev-editor-selection-2026.10.08.yml),
> [기준선 품질과 후보 평가 제한](../../research/agent-improvement/evaluations/maxis-editor-component-20261008-selection-v2.md)을 확인한다.

아래 v21·v20·v19는 이전 게시 기록이다.

> 2026-10-08: **v21 DEV Editor 게시 완료, 운영 비활성·품질 미통과**.
> 소유 티켓이 없는 Jira 검색 wrapper 제목을 선택 단위에서 제외했다. 23노드·26엣지와 모든 Guard는 유지한다.
> 회귀 **907개**, canary **3/3**. 실제 Editor 자동 통과 **2/5**, Role은 **정상 0·blocked 10·오류 1**이다.
> Editor의 fresh baseline과는 protocol 파일 줄바꿈으로 benchmark identity가 달라 직접 개선율을 주장하지 않는다.
> selector의 타입·중복 선택·없는 링크 선택 오류를 실제 native 입력으로 재현했다. fixed 4457 bind 실패로 UI는 미검증이다.
> Core/PMO·모델·Knowledge·PROD 불변, active profile 없음·모든 route local.
> [v21 증적](deployments/dev-editor-scope-2026.10.08.yml),
> [Editor 직접 품질](../../research/agent-improvement/evaluations/maxis-editor-component-20261008-unit-scope.md),
> [Role 진단](../../research/agent-improvement/evaluations/maxis-role-component-20261008-editor-scope.md)을 확인한다.

> 2026-09-21: **v20 DEV 게시 완료, 운영 비활성·품질 미통과**.
> Core/PMO에 결정적 Policy Query 노드를 추가해 각각 **12노드·12엣지**로 분리했다.
> 동일 Role 11-case에서 정책 no-hit **5→0**, 검색/ACL 검증 **5→10건**이지만
> 정상 답변은 **0/11**, 최종 blocked 10건·Editor 오류 1건이다.
> 지연 합계 **28.154→36.704초**로 증가했으며 속도·token 절감이나 최종 품질 개선은 주장하지 않는다.
> 회귀 **642개**, canary **3/3**. Editor·키·모델·Knowledge·PROD 불변, active profile 없음·모든 route local.
> [v20 증적](deployments/dev-policy-query-2026.09.21.yml)과
> [실측·실패·후속 개선](../../research/agent-improvement/evaluations/maxis-role-component-20260921-policy-query.md)을 확인한다.

아래는 Editor writer 계약을 변경한 v19의 과거 기록이다.

> 2026-09-16: writer 전용 문안 계약의 **v19 DEV 게시 완료, 품질 미통과**.
> Editor **23노드·26엣지**. 후보 5건 모두 selector+writer **LLM 2회**이며,
> 원본 근거·축소 입력·compiler 결과의 일치 **5/5**, writer 입력 **897–1,813바이트**, canary **3/3**.
> fresh v18 baseline → v19 자동 통과 **0/5 → 2/5**, 직접 품질 **2.14 → 2.40/5**,
> 유용한 결과 **0/5 → 0/5**다. 완성 초안·필수 확인 질문 모두 0건이며 의미 품질 개선은 미입증이다.
> 후보는 **17,727 tokens·23.3초**. baseline은 **24.3초**, CMP3 실패로 전체 token이 미관측이다.
> baseline의 부분 합계와 후보 전체를 비교해 절감률을 주장하지 않는다.
> 부모 범위 혼입·비교 방향 발명·미확인 완료 주장·검토 기준 누락이 남았다.
> 활성 profile은 비어 있고 모든 route는 local이다. Core/PMO·API key·PROD는 그대로다.
> Knowledge 25-case·전체 58-case·full compose/UI는 이번에 실행하지 않았다.
> [v19 증적](deployments/dev-editor-writing-2026.09.16.yml)과
> [구조·품질 보고서](../../research/agent-improvement/maxis-editor-writing-quality-20260916.md)를 먼저 확인한다.

앞선 v16 3.58/5·v18 2.58/5는 [과거 실행](deployments/dev-editor-draftspec-2026.09.16.yml) 기록이며,
이번 fresh v18 baseline 2.14/5를 대체하지 않는다. 모두 단일 탐색 실행이다.

API key, 프롬프트 전문, 모델 응답, 사용자 데이터, release sentinel 원문은 이 문서나
deployment evidence에 기록하지 않는다.

## Bundle 구성

| 경로 | 역할 |
|---|---|
| <code>bundle.yml</code> | 세 Workflow, 6개 Start 변수, retrieval/guard/execution 계약 |
| <code>dsl/*.yml</code> | Dify 0.5.0 import source DSL |
| <code>code/knowledge_guard.py</code> | retrieval identity·ACL·크기·policy decision 검증 |
| <code>code/contract_guard.py</code> | output contract·source graph·payload schema 검증 |
| <code>code/editor_assembler.py</code> | Editor Input 원문 단위 투영 + typed plan의 source-bound HTML·claim·citation 조립 |
| <code>code/editor_selector_input.py</code> | Input 전용 선택 범위 투영; 원본 v2와 분리된 selector 입력을 만들며 Guard는 변경하지 않음 |
| <code>code/editor_draft_spec.py</code> | 내부 근거 선택 검증·writer 입력 축소·문안 계약을 기존 assembler plan으로 컴파일 |
| <code>editor_graph.py</code> | 본문·댓글·제어 분기별 schema/prompt 정본과 재현 가능한 Editor DSL 생성기 |
| <code>editor_graph_validation.py</code> | selector·writer·control·stop과 원본 binding 공유 검증기; 환경 생성에도 재사용 |
| <code>policy_query_graph.py</code> | LTM 정본 selector를 Core/PMO Policy Query 노드로 생성·검증; 검색 입력만 분리 |
| <code>core_control_graph.py / core_graph_validation.py</code> | Core 합성 연결 검사와 업무 판단 분리; 생성과 원본 검증을 별도 수행 |
| <code>graph_receipt.py</code> | 검증한 로컬 graph와 Dify export의 실행 설정 비교 |
| <code>prompts/*.md</code> | 공통 및 앱별 instruction source |
| <code>contracts/*.json</code> | RemoteRoleResult와 Editor 선택·문안·assembler plan schema |
| <code>environments/dev-2026.09.yml</code> | 비밀 없는 DEV dataset/document/model binding |
| <code>environments/prod.template.yml</code> | 독립 PROD 자산 체크리스트 |
| <code>deployments/dev-2026.09.yml</code> | 게시 ID·카나리·평가 상태의 비밀 없는 증적 |
| <code>deployments/dev-quality-2026.09.14.yml</code> | 앞선 v9 게시·실측 실패 기록 |
| <code>deployments/dev-editor-plan-2026.09.14.yml</code> | 앞선 v11 게시 ID·실측 실패 기록 |
| <code>deployments/dev-editor-units-2026.09.14.yml</code> | 이전 v13 게시 ID·baseline/v12/v13 실측·비활성 상태 |
| <code>deployments/dev-editor-scope-2026.09.15.yml</code> | 이전 v15 게시 ID·당시 baseline/v14/v15 실측·비활성 상태 |
| <code>deployments/dev-editor-branches-2026.09.15.yml</code> | 이전 v16 게시 ID·당시 v15/v16 비교·실제 분기 확인 |
| <code>deployments/dev-editor-draftspec-2026.09.16.yml</code> | 이전 v18 게시 ID·당시 v16/v17/v18 비교·단계 실행 확인·비활성 상태 |
| <code>deployments/dev-editor-writing-2026.09.16.yml</code> | 이전 v19 게시 ID·fresh v18/v19 비교·writer 계약과 native 실행 확인·비활성 상태 |
| <code>deployments/dev-policy-query-2026.09.21.yml</code> | 이전 v20 Core/PMO 게시 ID·Role 11-case 검색 개선과 남은 출력 실패·비활성 상태 |
| <code>deployments/dev-editor-scope-2026.10.08.yml</code> | 이전 v21 Editor 게시·import 실패와 재개 증거·두 배터리의 실패·비활성 상태 |
| <code>deployments/dev-editor-selection-2026.10.08.yml</code> | 최신 v22 Editor 게시·공통 canary 실패·후보 품질 미실행·비활성 상태 |
| <code>materialize_environment.py</code> | source DSL에 환경 binding을 주입한 import copy 생성 |
| <code>validate_bundle.py</code> | DSL·prompt·code·contract·Knowledge binding 정적 검증 |
| <code>knowledge-evals.yml</code> | release-bound retrieval 평가 명세 |
| <code>launch_knowledge_evals.py</code> | credential-free DEV endpoint preflight 후 평가 runner에 일회성 handoff |
| <code>run_knowledge_evals.py</code> | handoff 없이는 credential 접근 전 종료하는 metadata-only 평가 runner |
| <code>launch_editor_evals.py / run_editor_evals.py</code> | 기존 Editor 5-case 재사용, 고정 가명 fixture의 실제 작성 품질 평가 |
| <code>smoke-cases.yml</code> | 세 앱 최소 contract smoke case |

source DSL은 직접 환경값으로 수정하지 않는다. DEV/PROD 차이는 environment binding과
배포 후 생성된 app/workflow ID를 통해 관리한다.

Editor component 재실행은 `python deploy/maxis-dify/launch_editor_evals.py --label candidate`를
사용한다. component v4에서 `baseline`과 `candidate`는 같은 입력 투영·검색어 정책을 사용하며,
label은 결과 그룹과 순서만 구분한다. 둘 다 **현재 게시된 프로필**을 측정하고 과거 Workflow로
자동 전환하지 않는다. 과거 비교는 보고서의 고정 UUID·raw 증적을 사용한다.
정답/checker/rubric과 raw fixture UID는 원격에 보내지 않는다. 이 평가가 통과해도 full compose/UI
검증이나 activation 승인이 되지 않는다. 아래 v8 수치는 과거 배포 기록으로 읽는다.

## Editor 근거 선택 → 문안 작성 후보

로컬 후보는 selector 전용 `editor-selector-input.v1`을 추가한다. 원문 unit을 구조상 가능한
`current/context/shared/neither` 그룹으로 묶고, 실제 링크가 있는 출처만 `link_choices`로 제시한다.
그룹은 관련성·최신성·현재 사실의 보증이 아니다. 원래 `editor-input.v2` 문자열은 Guard·Enforcer에
그대로 전달하며 Control도 기존 입력을 유지한다. 새 포트는 DraftSpec LLM만 읽는다.
각 입력은 9,000 UTF-8 bytes 이하여야 하며 초과 시 잘라내거나 옛 입력으로 대체하지 않는다.
아직 DEV에 게시하지 않았고 실제 품질 향상은 미검증이다.
[변경 범위와 회귀 기록](../../research/agent-improvement/maxis-editor-selector-eligibility-20261008.md)을 참고한다.

현재 source는 **23노드·26엣지**이며, 새 semantic Role이나 앱을 만들지 않는다.
정상 작성은 selector + 해당 writer의 **LLM 2회**, 필수 정보 확인은 selector만 **1회**,
합성 control 검사는 기존 **1회**, Knowledge 정책 거절은 **0회**다.
DEV/PROD의 endpoint·key·모델·Knowledge·게시 UUID는 기존 환경 binding에 남는다.

~~~mermaid
flowchart LR
    K[Knowledge Guard] --> P{Policy Decision}
    P -->|allow| I[Editor Input: 검증·근거 투영]
    P -->|deny| N[Denied Answer]
    I --> S{Editor Stage}
    S -->|writing| E[DraftSpec LLM: 근거·범위 선택]
    S -->|allowlisted probe| H[Control LLM: 연결 확인]
    E --> V[DraftSpec Guard: 검증·입력 축소]
    V --> R{Editor Route: 검증된 상태·mode}
    R -->|description| D[Description LLM: 본문 문안]
    R -->|comment| C[Comment LLM: 댓글 문안]
    R -->|needs_input / blocked| T[Stop Plan: writer 생략]
    D --> W[Writer Candidate]
    C --> W
    T --> W
    W --> F[DraftSpec Enforcer: 선택 검증·plan 컴파일]
    F --> M[Editor Candidate]
    H --> M
    M --> A[Editor Assembler]
    A --> G[Contract Guard]
    G --> L[LTM ResponseGuard]
~~~

본문·댓글 writer는 `editor-writing.v1`만, 제어 검사는 `remote-role-result.v1`만 생성한다.
selector는 `editor-draft-spec.v2`의 원문 ID 분류·필수 링크·부족한 사실만 선택한다.
기존 target/constraint 두 배열은 writer 권한이 같아 `current_unit_ids`로 통합했다.
current 내부와 context/history 간 중복, v1·혼합 계약은 그대로 거부하며 자동 변환하지 않는다.
DraftSpec은 한 workflow 실행 안에서 사용하는 중간값이다. selector/schema/Guard/Enforcer를
같은 workflow 게시본으로 함께 교체하며, v1 중간값을 v2 실행에 재개·재생하지 않는다.
앱의 외부 저장 계약은 바꾸지 않는다. Dify의 진행 중 실행 snapshot·재개 동작은 아직 검증하지 않았다.
이 계약은 DEV v22에 게시했다. 당시 공통 canary 실패로 보류됐던 배터리는 Core v23 게시 후 실행했다.
writer의 `editor-writer-input.v2`는 요청, section 순서·목적·블록 예산, 선택된 원문과
허용 section만 제공한다. source ID·선택 bucket·링크 목록·native Knowledge context는 주지 않는다.
부모 맥락 fact는 description.background에서만, 역사·충돌 fact는 작성에서 제외한다.
본문 schema는 네 section, 댓글은 comment 하나이며 fact ID·proposal text만 허용한다.
status·missing_inputs·source_ids·link 블록은 writer 출력에 없다. compiler가 proposal에
원본 사용자 intent 출처만 결속하고, 선택된 필수 링크를 기존 `editor-plan.v2`로 조립한다.
원문에서 자동 렌더링되는 링크도 원본 pack으로 계산하며 중복·미선택 링크는 차단한다.
32개 전체 블록 한도에서 추가 링크 예산을 미리 제외해 writer에 알린다.
부모·형제 Jira fact를 current로 승격하는 것도 차단하지만, 문서 constraint의 의미상
적용 범위나 필수 문서 선택의 완전성은 모델 판단이다. proposal의 intent citation 역시
사실성 보증이 아니므로 내용 정확도나 부모 범위 혼입이 해결됐다고 간주하지 않는다.
Control은 기존 activation/retrieval 합성 검사의 닫힌 입력만 허용하며, 깨진 Editor 입력이나
임의 schema가 Control로 우회되지 않는다. 유효한 `needs_input` 선택이면 초안을 비우고 writer를 생략한다.
이 경로는 로컬 테스트로 검증했지만 이번 fresh v18/v19 실측에서 성공한 Stop은 관측하지 못했다.
baseline CMP3은 selector 계약 위반으로 writer 전에 실패했으며 native 완전 일치는 4/5다.
단순 연결 canary는 실제 작성 품질 검증을 대체하지 않는다.

재생성: `python deploy/maxis-dify/editor_graph.py` → `python deploy/maxis-dify/validate_bundle.py`.
환경 생성은 기존 `materialize_environment.py`를 사용한다. 분기별 prompt digest와 code digest를
검토 없이 자동 갱신하지 않는다. [Dify Variable Aggregator](https://docs.dify.ai/en/cloud/use-dify/nodes/variable-aggregator)는
배타적 경로 하나의 결과를 공통 후처리에 연결하는 노드이며 병렬 출력들을 합치는 기능이 아니다.

## 공통 게시 topology

아래는 v20 DEV에 게시한 Core/PMO의 공통 **12노드·12엣지** topology다. 이전 버전의
11노드·11엣지 증적은 보존한다. 연결·검색 검증 통과는 최종 업무 품질이나 운영 활성화 승인이 아니다.
Editor source의 23노드·26엣지와 검색 입력은 변경하지 않는다.

~~~mermaid
flowchart LR
    S([Start])
    SQ[Sentinel Query]
    RS[(Release Sentinel)]
    AK[Role ACL Key]
    PQ[Policy Query: 결정적 검색어 선택]
    RK[(Role Knowledge)]
    KG[Knowledge Guard]
    PD{Policy Decision}
    LLM[Contract LLM]
    CG[Contract Guard]
    AA([Allowed Answer])
    DA([Denied Answer])

    S --> SQ
    SQ --> RS
    S --> AK
    AK --> PQ
    PQ --> RK
    RS --> KG
    RK --> KG
    KG --> PD
    PD -->|allow| LLM
    LLM --> CG
    CG --> AA
    PD -->|deny / no-hit| DA
~~~

1. Start → Sentinel Query
2. Sentinel Query → Release Sentinel
3. Start → Role ACL Key
4. Role ACL Key → Policy Query
5. Policy Query → Role Knowledge
6. Release Sentinel → Knowledge Guard
7. Role Knowledge → Knowledge Guard
8. Knowledge Guard → Policy Decision
9. Policy Decision allow → Contract LLM
10. Contract LLM → Contract Guard
11. Contract Guard → Allowed Answer
12. Policy Decision deny/no-hit → Denied Answer

Policy Query는 Query Specialist·Work Architect·Auditor·Portfolio Analyst의 정확한
Role id와 LTM이 덧붙인 bounded 정책어 suffix가 일치할 때만 정책어를 검색한다.
나머지 Role, control/probe, suffix 불일치 입력은 원래 query를 그대로 사용한다.
어휘와 selector는 `app/agent/maxis/retrieval_query.py` 한 곳에 정의하고 빌더가
dependency-free code로 생성한다. `python -B deploy/maxis-dify/policy_query_graph.py`로
재생성한 뒤 bundle validator와 environment materializer를 통과시킨다.
Knowledge Guard는 계속 원래 `sys.query`와 EvidencePack의 equality를 검사하며,
사용자 의도·근거·LLM 입력·ACL·Sentinel·top-k·출력 가드를 바꾸지 않는다.
이는 모델 호출을 늘리지 않는 검색용 단위 기능이며, 의미 품질 통과를 보장하지 않는다.

Editor Input은 `editor-draft.v1` 요청의 원본 EvidencePack을 바꾸지 않고, 모델용 요청과
출처별 원문 단위 ID 목록(`editor-input.v2`)을 만든다. 사용자의 요청은 별도 intent로 분리하며
사실 단위로 제공하지 않는다. intent ID는 질문·제안의 출처로만 사용할 수 있다.

현재 source는 selector 입력에서 작성용 `draft_contract`를 제거했다.
Selector는 요청·seed·근거 단위·`entities`를 받아 `editor-draft-spec.v2`만 선택한다.
선택 검증 뒤 writer에게만 별도 `draft_contract`와 허용된 section을 제공한다.
따라서 selector에 `editor-plan.v2` skeleton이나 writer의 `sections=[]` 지시를 섞지 않는다.
입력 필드 제거에 맞춰 내부 버전을 v2로 올렸으며 v1/혼합 입력은 거부한다.
`editor-writer-input.v2`, `editor-writing.v1`, `editor-plan.v2`와 외부 응답 계약은 바꾸지 않았다.
이 입력 v2는 DEV v24에 게시했다. 위 과거 v23 Editor 기록은 이전 입력 계약의 결과다.
v24의 자동 결과는 Editor 2/5·Role 정상 0/11이며, 계약 단순화만으로 의미 품질 개선을 주장하지 않는다.
[변경 경계와 검증 기록](../../research/agent-improvement/maxis-editor-selector-input-v2-20261008.md)을 참고한다.

Jira 단위의 `entity_key`와 명시된 `parentKey`/`parent.key`/`fields.parent.key`만으로
target/parent/child를 표시한다. 문서·복수 티켓 제목으로 계층을 추측하지 않으며,
식별 불가 하위 객체와 충돌·순환 관계는 unknown으로 남긴다. 표시는 새 사실 근거가 아니고,
target·교차 출처 관계가 바뀌면 unit ID도 바뀌어 예전 선택을 차단한다.
v15의 entity별 `source_ids`는 해당 key 객체를 실제로 포함한 Jira 출처만 나열한다.
`parentKey`만 언급된 부모나 문서·intent로 가상의 티켓 출처를 만들지 않는다.
이 출처 대응도 unit ID에 묶인다. v15 당시 최대 Knowledge 본문 1,024바이트와 100바이트 문서명 두 개를
넣은 고정 5-case는 8,924–8,998바이트였다. 이 과거 관측은 현재 입력 v2의 측정값이 아니다. 임의 크기의 입력 전체를 보장하는 것은
아니며, 원문을 잘라 넣거나 9,000바이트 한도를 높이지 않고 초과 시 실패한다.
이 투영은 의미상 대상 선택을 돕지만 문장 의미나 최신성 충돌의 해결을 보증하지 않는다.

모델은 `editor-writing.v1`의 section과 fact unit ID·proposal 문안을 선택하고 compiler가
출처·필수 링크를 결속한 `editor-plan.v2`로 변환한다. 기존 Assembler가 description의 네
섹션 제목, 원래 seed, HTML·claim·citation을 구성하며 원본 pack으로 단위 ID를 다시 확인한다.
모델이 원문을 다시 복사하거나 없는 seed를 지정하지 않는다. 과거 `editor-plan.v1`은 수락하지 않는다.

허용된 합성 control/probe 입력·출력만 검증 후 원형으로 전달한다. 최종 출력 수락 권한은 Assembler가 아니라
기존 ContractGuard와 로컬 ResponseGuard에 있다. 두 가드는 원본 EvidencePack을 사용한다.
형식 검증 통과는 문장 의미·업무 적합성 통과가 아니므로 별도 원문 평가가 필요하다.

현재 단위 투영은 문자열 본문·제목에 한정한다. JSON 숫자/불리언과 pack facts/metrics의
독립 단위화는 미구현이며, 크기 초과·알 수 없는 URL 등은 부분 삭제 대신 실패 처리한다.
v13은 명시된 Jira·Confluence 본문의 위키 제목/목록 접두부만 표시층에서 정리한다.
코드 표식이 있는 leaf와 메타데이터는 제외한다. 원문·종류·표시문·HTML·인용 ID를 함께
단위 ID에 묶으므로, 다른 출처의 링크 제목이 바뀌어도 예전 선택 ID를 재사용하지 않는다.

ACL deny와 no-hit은 LLM을 호출하지 않는다. 인프라 오류는 정상 Denied Answer로
위장하지 않고 실패로 전달한다. 공통 부분의 정적 뷰어에서는
[11-node Native flow](../../docs/images/maxis-dify-implementation/06-dify-eleven-node-native-flow.svg)를
사용한다. 해당 기존 그림에는 Editor 추가 노드가 없다.

## Start와 stateless contract

정확히 아래 6개 Start 변수를 required로 구성한다.

| 변수 | UI control | 최소 용량 |
|---|---|---:|
| <code>context_pack</code> | paragraph | 6,144 |
| <code>role_id</code> | text-input | 128 |
| <code>correlation_id</code> | text-input | 128 |
| <code>output_contract</code> | text-input | 128 |
| <code>workflow_version</code> | text-input | 128 |
| <code>knowledge_version</code> | text-input | 128 |

<code>query</code>와 <code>user</code>는 Dify Chat API 필드이며 Start 변수가 아니다.
매 요청은 빈 conversation ID와 이름 자동 생성 비활성으로 stateless 실행한다.

<code>context_pack</code> 안에는 canonical payload schema와 digest, bounded evidence,
request binding이 들어간다. Dify prompt는 Knowledge Guard가 만든 model input만
받으며 raw retrieval, direct Start selector, query, credential, approval token,
conversation memory를 받지 않는다.

## 두 종류의 문맥

### Policy RAG

Role Knowledge는 조직 규칙, 허용 행동, 형식, 검증 절차를 제공한다. 이는 생성 제약이며
Jira·Confluence·Bitbucket의 현재 사실이나 citation source가 아니다. Dify 1.11.1
manual metadata filter와 Knowledge Guard의 exact allow-list를 모두 통과해야 한다.

### Citation-grade EvidencePack

업무 사실과 수치는 LTM connector·결정적 계산이 만든 EvidencePack에서만 온다.
claim, citation, payload claim은 검증된 source에 결속된다.

- sources가 비어 있으면 모든 claim, citation, payload claim을 거부한다.
- 예외는 schema의 모든 필드가 required이고 non-narrative이며 값이 const 또는 단일
  enum인 폐쇄형 control-only payload뿐이다.
- optional, free-text, default, multi-enum, narrative field가 하나라도 있으면 예외가
  적용되지 않는다.
- <code>ltm:user-intent:</code> source가 사용되면 사용자 요청·선호·계획 판단만
  지지할 수 있다. Jira·Confluence·Bitbucket·Knowledge 사실의 근거로 사용할 수 없다.

Policy RAG와 EvidencePack을 합쳐 하나의 “검색 근거”로 취급하지 않는다.

## Guard와 execution policy

Knowledge Guard는 다음을 LLM 호출 전에 확인한다.

- 환경별 Sentinel dataset/document identity와 proof
- Role Knowledge dataset/document metadata identity
- <code>ltm_allowed_roles_v1</code>의 exact Role 권한
- query, Start, EvidencePack binding과 byte/item 제한
- policy allow / deny / no-hit 결정

Contract Guard는 allow 결과에 대해 다음을 확인한다.

- 요청한 published Workflow UUID와 Dify <code>sys.workflow_id</code>의 exact equality
- workflow/knowledge/output contract version
- per-call payload schema와 digest
- evidence graph, claim/citation/payload claim coverage
- credential, token, cookie/session, private key 등 금지 authority field
- source-free control-only 예외의 폐쇄형 schema/value 조건

그 뒤 LTM ResponseGuard가 같은 canonical contract를 다시 검증한다. critical
Knowledge/LLM/Guard/Answer 실패는 terminate하고 automatic retry, default recovery,
failure answer, model fallback을 사용하지 않는다.

## 현재 DEV binding

| 항목 | 값 |
|---|---|
| Governed dataset | <code>97823b6c-2e14-4d13-a2cc-f6082e0a453d</code> |
| Governed corpus | **8 documents / 513 segments**, max 151 UTF-8 bytes |
| Role metadata field | <code>ltm_allowed_roles_v1</code> / <code>f8f7023d-1c0f-4893-bf4b-7422b7d25f69</code> |
| Metadata verification | **8/8**, missing·drift·substring collision 0 |
| Sentinel V3 dataset | <code>7953d6ec-094e-470e-b262-26a6a561b091</code> |
| Sentinel V3 document | <code>b4cfe6ce-65b6-4116-a575-3c2607c9116f</code> |
| Sentinel proof SHA-256 | <code>c4146f938c309f2130355b8febd212745f9799359ddb2d90b83480c5ba177ecb</code> |
| Knowledge manifest SHA-256 | <code>f126c2d3deb6134bac1db937fa19e135c3dcb98c363f942c11f76d01fbbff329</code> |

Sentinel은 Governed Knowledge와 별도 dataset이다. 문서는 1개, segment는 1개이며
LTM profile에는 평문이 아니라 proof SHA만 저장한다.

## 이전 v8 앱·Workflow binding (보존용)

아래는 과거 기록이다. 최신 UUID와 측정 상태는 [v23 증적](deployments/dev-core-control-2026.10.08.yml)을 사용한다.

| 앱 | App ID | published Workflow ID | 카나리 |
|---|---|---|---:|
| Agent Core | <code>d061c2e6-72cf-4fc4-b835-4cb124e1f96c</code> | <code>85add2bc-455e-4155-b0af-d8e939e24565</code> | 2,311 ms |
| Editor Author | <code>dbda2328-0547-4e0c-967d-c14265b27459</code> | <code>a5f28cc2-9ef2-4651-8e77-a1ccde5feb3e</code> | 2,342 ms |
| PMO Brief | <code>ceb6bdc2-0276-4736-8c8b-59fc69f85cd6</code> | <code>cf3a11c6-c237-4889-a9c4-89dbdd9b00b2</code> | 2,013 ms |

- bundle version: <code>ltm-maxis-dify-2026.09.06-native-acl-v8</code>
- candidate fingerprint: <code>cb40a03650a170d74ced6bf26bed764c</code>
- candidate verified: <code>profile_verified_at_probe=true</code>, <code>candidate_probe_ok=true</code>
- persisted Service API canary: **3/3 passed**
- active profile: **empty**
- active routes: all **local**

App API key는 environment YAML, DSL, deployment evidence에 넣지 않는다. LTM의
암호화 credential store 또는 환경별 비밀 주입만 사용한다.

## Materialize와 validate

저장소 root에서 실행한다.

~~~powershell
python deploy/maxis-dify/materialize_environment.py deploy/maxis-dify/environments/dev-2026.09.yml --output-dir .cache/maxis-dify-materialized/dev-2026.09
python deploy/maxis-dify/validate_bundle.py
~~~

materializer는 incomplete UUID, non-HTTPS remote URL, credential 형태의 필드,
알 수 없는 Knowledge file, source DSL in-place overwrite를 거부한다. 생성물은
<code>.cache/</code> 아래에만 둔다.

validator는 source DSL의 nodes/edges, Start controls, prompt/code embedding과 hash,
model dependency, Knowledge binding, stateless shape, smoke/eval coverage를 검사한다.
정적 validator 통과는 원격 검색·품질 평가 통과를 뜻하지 않는다.

## Release gate

원격 평가는 direct runner가 아니라 명시적 launcher로 시작한다. launcher는 API key를
읽지 않은 채 allowlisted DEV endpoint에 bounded probe를 수행하고, 성공한 자식
프로세스에만 destination-bound handoff를 전달한다. 이는 같은 사용자 공격에 대한
인증 경계가 아니라 실수로 runner를 직접 실행하는 것을 막는 process-local gate다.
먼저 pilot만 확인한 뒤 full suite를 실행한다.

~~~powershell
python deploy/maxis-dify/launch_knowledge_evals.py --pilot-only
python deploy/maxis-dify/launch_knowledge_evals.py
~~~

~~~mermaid
flowchart LR
    M[Manifest·segment integrity] --> R[Native retrieval preflight]
    R --> C[3-app canary]
    C --> E[25-case full suite]
    E --> G{releaseEligible?}
    G -->|no| I[개선·재실행]
    I --> E
    G -->|yes + signed review| S[DEV shadow]
    S --> H[Human acceptance]
    H --> A[명시적 route activation]
    A --> P[독립 PROD release]
~~~

아래는 2026-09-06 v8에서 확인한 과거 원격 증거다. v22 게시 당시 canary 2/3·배터리 미실행 및
v23의 canary 3/3·배터리 실행 상태와 각각 구분한다.

- filtered retrieval preflight: **25/25**
  - positive 8/8 target top-2
  - injection fixture 8/8 target top-2
  - ACL-negative 8/8 forbidden absent
  - no-hit 1/1 zero result
- Service API canary: **3/3**
- full suite: **25/25 실행, 13 pass / 12 fail**, timeout 0
- category:
  - positive automated **4/8**
  - injection automated **2/8**, 최종 **0/8**, injection guard **8/8**
  - ACL **8/8**, no-hit **1/1**
- retrieval: Hit@2 **1.0**, MRR@2 **0.9375**
- response shape / outcome status 각각 **15/25**
- contract / version / sentinel / dataset / identity / Role ACL / ledger / include / exclude 각각 **25/25**
- latency: mean **1,741 ms**, p50 **2,118 ms**, p95 **2,957 ms**, total **43,529 ms**
- usage: prompt 50,834 / completion 4,686 / total **55,520 tokens**, 25/25 관측
- outcome: **failed**, <code>releaseEligible=false</code>
- injection 8건 signed human review: **pending**
- semantic answer correctness: **not assessed**

Artifact:
<code>.cache/maxis/evals/dev-keval-20260906T052144589439Z-7b820caa.json</code><br>
SHA-256:
<code>10270e72b8f19baca11a3dbc405bb418fd431094b3a21071042953a9293870bd</code><br>
Harness SHA-256:
<code>df2f1890ce2cd61a95639f49d9697127deb1bdb57d9340b76ccb2c3d27c5a3f1</code>
<br>Artifact schema SHA-256:
<code>44a0c94b936bb65909a58dfdd5ef24b7b4aa62ceee83b94c50e470a11c015576</code>
<br>승인된 [평가 launcher](launch_knowledge_evals.py) 사용,
<code>evaluationLauncher</code> 포함 source hash ledger: **15/15 결속**
<br>소스 전환 검증 실행의 통과 수는 **13~16/25**로 변동했으므로 생성 안정성은
미입증 상태였다. 당시 기준은 당시 소스와 hash가 일치하는 위 artifact 하나뿐이다.

이 수치는 과거 v8의 **PoC baseline**이며 성공으로 해석하지 않는다. preflight와 canary,
높은 retrieval 지표만으로 authoritative route를 열지 않는다. positive 4건과
response-shape 실패를 개선해 재실행하고, injection 서명 검토와 의미 정답성 검토를
마쳐야 한다. Artifact는 dataset, manifest, workflow version, profile, suite digest에
결속하며 privacy flags상 원시 prompt·response·answer·검색 content·credential·sentinel
marker를 저장하지 않는 content-free 증적이다.

## DEV / PROD 분리

PROD는 DEV dataset, sentinel, document UUID, app, Workflow ID, API key, fingerprint를
재사용하지 않는다. <code>prod.template.yml</code>에서 독립 자산을 만들고 같은
materialize → validate → publish → preflight → canary → signed eval → shadow gate를
반복한다.

현재 PROD는 미구성이고 authoritative route는 fail closed다. URL이나 key만 교체해
승격하는 방식은 지원하지 않는다.

## MCP 경계

Phase 1의 MAXIS node registry/MCP 등록은 **0**이다. Jira·Confluence·Bitbucket
credential과 write 권한은 LTM에 남는다. 향후 MCP는 stable identity, OAuth/mTLS,
exact host allow-list, field minimization을 갖춘 **read-only** adapter로만 별도
검토한다. 쓰기는 계속 사용자 HITL과 one-shot capability를 거친 LTM Action Executor가
수행한다.

## Source of truth

### Exploratory Role component evaluation

`python -B deploy/maxis-dify/launch_role_evals.py --label baseline` runs at most
11 repository-owned synthetic cases across the 8 remotely bound Roles. Invoke the
launcher in an explicitly network-authorized separate process. It checks the DEV
destination without credentials, then validates the inactive synthetic-only profile,
all-local routes and pinned deployment before reading existing approved app keys.
No app activation, live Jira input, prompt override, workflow publication or retry is
performed. `--cases role-request role-query` selects only fixed IDs for a new focused
attempt; it does not replace the original run. `baseline`/`candidate` are labels, not
deployment switches. Both use the currently pinned published DEV workflow.

The actual `role_payload`/ContextGuard projection, fresh synthetic attestations and
ResponseGuard contracts are exercised. Sources and profile pins are rechecked before
each request. Transport/auth/rate-limit failures stop remaining calls; attempted and
unattempted IDs are recorded separately. Redacted output, per-role latency, available
usage, retrieval identity/ACL and contract failures remain in a new immutable raw
group under `.cache/agent-evaluation/`. Missing costs/native LLM counts remain unknown.

This is a **component diagnostic, not whole-app qualification**: Work covers one
create-shaped input, Editor covers a compact seed, and overflow is an artificial
structural stress case, not a representative work task. Contract acceptance is not
usefulness. Evaluate raw output directly with Codex/Claude; never use LTM's runtime
LLM as the judge. Production DEV fixed-fixture protection remains unchanged.

- 현재 environment binding:
  [environments/dev-2026.09.yml](environments/dev-2026.09.yml)
- 현재 비밀 없는 배포 증적:
  [deployments/dev-editor-selection-2026.10.08.yml](deployments/dev-editor-selection-2026.10.08.yml)
- 구현 상태와 남은 gate:
  [MAXIS_DIFY_IMPLEMENTATION_STATUS.md](../../docs/MAXIS_DIFY_IMPLEMENTATION_STATUS.md)
- 아키텍처와 운영 경계:
  [MAXIS_DIFY_MIGRATION.md](../../docs/MAXIS_DIFY_MIGRATION.md)
