# LTM MAXIS / Dify Workflow bundle

이 디렉터리는 Lake Task Manager가 사용하는 Dify Advanced Chatflow 3개의 검토 가능한
source bundle이다. DEV와 PROD의 환경 binding을 분리하고, import DSL을 materialize한
뒤 validator와 원격 release gate를 통과시키는 구조다.

> 2026-09-14: DEV 구조적 후보 **v11** 게시. Editor **12노드·12엣지**, 나머지 **11·11**, canary **3/3**.
> 동일 component v4에서 원격 v9/v11 Guard 수락 **0/5 → 1/5**, 실제 요청·렌더 통과는 **0/5 → 0/5**다.
> Knowledge harness v2는 최신 **12/25**, 관련 코드 테스트는 **925건 통과**다. 서로 다른 품질 지표다.
> 활성 profile은 비어 있고 모든 route는 local이다. PROD는 변경하지 않았다.
> [최신 증적](deployments/dev-editor-plan-2026.09.14.yml)과
> [품질 보고서](../../research/agent-improvement/maxis-editor-plan-quality-20260914.md)를 먼저 확인한다.

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
| <code>prompts/*.md</code> | 공통 및 앱별 instruction source |
| <code>contracts/*.json</code> | RemoteRoleResult canonical schema 및 Editor 내부 plan schema |
| <code>environments/dev-2026.09.yml</code> | 비밀 없는 DEV dataset/document/model binding |
| <code>environments/prod.template.yml</code> | 독립 PROD 자산 체크리스트 |
| <code>deployments/dev-2026.09.yml</code> | 게시 ID·카나리·평가 상태의 비밀 없는 증적 |
| <code>deployments/dev-quality-2026.09.14.yml</code> | 앞선 v9 게시·실측 실패 기록 |
| <code>deployments/dev-editor-plan-2026.09.14.yml</code> | 최신 v11 게시 ID·실측 실패·비활성 상태 |
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

## 실제 게시 topology

아래는 Core/PMO의 공통 11노드·11엣지 topology다. Editor는 allow 경로에
`Editor Input → Contract LLM → Editor Assembler → Contract Guard`를 사용해 **13노드·13엣지**다.

~~~mermaid
flowchart LR
    S([Start])
    SQ[Sentinel Query]
    RS[(Release Sentinel)]
    AK[Role ACL Key]
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
    AK --> RK
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
4. Role ACL Key → Role Knowledge
5. Release Sentinel → Knowledge Guard
6. Role Knowledge → Knowledge Guard
7. Knowledge Guard → Policy Decision
8. Policy Decision allow → Contract LLM
9. Contract LLM → Contract Guard
10. Contract Guard → Allowed Answer
11. Policy Decision deny/no-hit → Denied Answer

Editor Input은 `editor-draft.v1` 요청의 원본 EvidencePack을 바꾸지 않고, 모델용 요청과
출처별 원문 단위 ID 목록(`editor-input.v1`)을 만든다. 사용자의 요청은 별도 intent로 분리하며
사실 단위로 제공하지 않는다. intent ID는 질문·제안의 출처로만 사용할 수 있다.

모델은 `editor-plan.v2`의 section과 fact unit ID·proposal·link를 선택한다. Assembler가
description의 네 섹션 제목, 기존 seed, HTML·claim·citation을 함께 구성하고, 원본 pack으로
단위 ID를 다시 확인한다. 모델이 원문을 다시 복사하거나 없는 seed를 지정하지 않는다.
v1 schema는 과거 증적용으로 남지만 새 Editor 경로에서는 수락하지 않는다.

non-Editor control/probe 입력과 출력은 그대로 통과한다. 최종 승인 권한은 Assembler가 아니라
기존 ContractGuard와 로컬 ResponseGuard에 있다. 두 가드는 원본 EvidencePack을 사용한다.
형식 검증 통과는 문장 의미·업무 적합성 통과가 아니므로 별도 원문 평가가 필요하다.

현재 단위 투영은 문자열 본문·제목에 한정한다. JSON 숫자/불리언과 pack facts/metrics의
독립 단위화는 미구현이며, 크기 초과·알 수 없는 URL 등은 부분 삭제 대신 실패 처리한다.

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

## 현재 앱·Workflow binding

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

현재 확인된 원격 증거:

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
미입증 상태다. 공식 기준은 현재 소스와 hash가 일치하는 위 artifact 하나뿐이다.

이 수치는 현 v8의 **PoC baseline**이며 성공으로 해석하지 않는다. preflight와 canary,
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

- 현재 environment binding:
  [environments/dev-2026.09.yml](environments/dev-2026.09.yml)
- 현재 비밀 없는 배포 증적:
  [deployments/dev-2026.09.yml](deployments/dev-2026.09.yml)
- 구현 상태와 남은 gate:
  [MAXIS_DIFY_IMPLEMENTATION_STATUS.md](../../docs/MAXIS_DIFY_IMPLEMENTATION_STATUS.md)
- 아키텍처와 운영 경계:
  [MAXIS_DIFY_MIGRATION.md](../../docs/MAXIS_DIFY_MIGRATION.md)
