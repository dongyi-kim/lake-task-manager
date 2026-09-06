# LTM MAXIS / Dify 적용 아키텍처와 운영 가이드

이 문서는 Lake Task Manager(LTM)가 MAXIS와 Dify를 왜 도입하고, 어디까지 맡기며,
어떤 증거를 확인한 뒤 운영 경로로 승격하는지를 설명한다.

> 설계 원칙: **MAXIS/Dify는 정책을 찾아 의미 결과를 제안하고, LTM은 최신 사실·인용·권한·승인·쓰기를 통제한다.**

## 1. 세 시스템의 역할

| 시스템 | 이 설계에서의 의미 | 맡기는 일 | 맡기지 않는 일 |
|---|---|---|---|
| MAXIS | 사내 LLM 서비스의 Project·접근·Knowledge 거버넌스 계층 | 사내 운영 경계, Project, Knowledge 운영, 향후 Agent/MCP 등록 | LTM의 최종 업무 권한과 사용자 승인 대체 |
| Dify | MAXIS 환경 안에서 실행되는 시각적 LLM Workflow·RAG·Service API 엔진 | Role별 의미 판단, 정책 RAG, 구조화 초안, deterministic guard | Jira credential 보관, 직접 업무 변경, 최종 권한 판정 |
| LTM | 사용자 상태와 업무 시스템을 소유하는 로컬 애플리케이션 | 조회, EvidencePack, orchestration, dual validation, HITL, write, audit | 조직 정책 문서를 프롬프트에 하드코딩해 분산 관리 |

현재 연동 endpoint는 별도 MAXIS Agent API가 아니라 MAXIS가 운영하는 Dify
Service API의 <code>/parameters</code>와 <code>/chat-messages</code>다. MAXIS는
거버넌스, Dify는 실행, LTM은 업무 통제라는 세 층을 구분한다.

## 2. 도입 전후

### AS-IS

~~~mermaid
flowchart LR
    U[사용자] --> UI[LTM]
    UI --> LG[Local LangGraph]
    LG --> CONN[업무 시스템 connector]
    CONN --> LA[Local agents]
    LA --> HITL[사용자 승인]
    HITL --> W[Jira write]
~~~

- 역할 지침과 의미 처리 로직이 로컬 코드에 집중된다.
- 조직 정책을 바꾸면 로컬 release가 필요하다.
- 대시보드가 “무슨 일이 있는지”는 보여도, 여러 source를 종합해 “무엇을 왜
  해야 하는지”를 구조화하는 비용은 사용자에게 남는다.

### TO-BE

~~~mermaid
flowchart LR
    U[사용자] --> LTM

    subgraph LOCAL["LTM · authoritative control plane"]
        LTM --> O[Orchestrator]
        O --> R[Connector read]
        R --> EP[Citation-grade EvidencePack]
        V[ResponseGuard] --> H[HITL]
        H --> X[Action Executor]
        X --> J[(Jira)]
        A[Audit·KPI·freshness] --- H
    end

    subgraph GOV["MAXIS · governance plane"]
        P[Project·접근정책]
        K[Governed Knowledge]
        S[Release Sentinel]
    end

    subgraph AI["Dify · semantic execution plane"]
        AC[Agent Core]
        ED[Editor Author]
        PMO[PMO Brief]
        G[Knowledge / Contract Guards]
    end

    EP -->|최소·가명화된 입력| AC
    EP --> ED
    EP --> PMO
    K --> AC
    K --> ED
    K --> PMO
    S --> G
    AC --> G
    ED --> G
    PMO --> G
    G --> V
    P -. governs .-> AI
~~~

차별점은 새 대시보드가 아니라, 여러 업무 source를 근거 그래프로 묶어 Role별로
판단·초안을 생성하고도 기존 권한과 승인 경계를 유지하는 데 있다.

정적 구조도:
[LTM–MAXIS–Dify TO-BE](images/maxis-dify-implementation/05-ltm-maxis-tobe.svg)

## 3. Agent 구성

| Dify 앱 | 원격 Role | 결과의 쓰임 |
|---|---|---|
| Agent Core | request_architect, query_specialist, knowledge_curator, work_architect, auditor, result_integrator | 요청 구조화, 조회 계획, 지식 정리, 작업 분해, 의미 감사, 최종 종합 |
| PMO Brief | portfolio_analyst | 포트폴리오 요약과 위험 설명 |
| Editor Author | editor_author | 설명·댓글 등 rich-text 초안 |

Research Analyst, People Advisor, Query Runner, Action Executor는 현재 원격 registry에
넣지 않는다. 특히 Query Runner와 Action Executor는 사실 조회와 쓰기 권한을 다루므로
LTM 로컬 경계에 남긴다. Dify Auditor는 의미 검토만 수행하며 최종 schema, source,
권한 판정은 두 deterministic guard가 다시 확인한다.

## 4. Dify Native v8 Workflow

세 앱은 같은 보안 topology를 가지며 Role과 response payload schema만 다르다.
최종 게시 그래프는 **11노드·11엣지**다.

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
    OK([Allowed Answer])
    NO([Denied Answer])

    S --> SQ
    SQ --> RS
    S --> AK
    AK --> RK
    RS --> KG
    RK --> KG
    KG --> PD
    PD -->|allow| LLM
    LLM --> CG
    CG --> OK
    PD -->|deny / no-hit| NO
~~~

엣지 수는 다음 11개로 고정된다.

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

ACL 거부와 no-hit은 LLM을 호출하지 않는다. allow도 Contract Guard가 실행 소유
Workflow ID, 환경 version, payload schema, source graph, release proof를 검증한 뒤에만
Allowed Answer로 나온다.

정적 구조도:
[Dify Native 11-node flow](images/maxis-dify-implementation/06-dify-eleven-node-native-flow.svg)

## 5. Policy RAG와 citation-grade EvidencePack

둘 다 문맥처럼 보이지만 보안·정확성 의미가 완전히 다르다.

~~~mermaid
flowchart LR
    subgraph POLICY["Policy RAG"]
        DOC[규칙·템플릿·산식 설명]
        META[Role ACL metadata]
    end

    subgraph FACT["EvidencePack"]
        API[업무 시스템 API]
        SRC[source ID·조회시각]
        CALC[결정적 계산]
    end

    DOC --> KGUARD[Knowledge Guard]
    META --> KGUARD
    API --> SRC --> EPGUARD[LTM Context Guard]
    CALC --> EPGUARD
    KGUARD -->|행동·형식 제약| LLM
    EPGUARD -->|claim·citation 근거| LLM
    LLM --> DUAL[Contract Guard + ResponseGuard]
~~~

### Policy RAG

- Dify Governed Native Knowledge에서 검색한다.
- 허용 행동, 작성 형식, 검증 절차, 조직 규칙을 제한한다.
- Jira 상태·사람·일정·수치에 관한 factual citation으로 사용할 수 없다.
- metadata의 Role allow-list와 Knowledge Guard를 모두 통과해야 한다.

### Citation-grade EvidencePack

- LTM이 Jira·Confluence·Bitbucket 등에서 요청 시점에 읽는다.
- source ID, 조회 시각, 제한된 excerpt, 결정적 계산 관계를 가진다.
- claim과 citation은 검증된 source에 결속된다.
- source가 없다면 모든 claim, citation, payload claim을 거부한다. 예외는 모든 필드가
  required이고 값이 const 또는 단일 enum인 폐쇄형 control-only payload뿐이다.
- <code>ltm:user-intent:</code> source는 사용자 요청·선호·계획 판단 전용이다.
  Jira·Confluence·Bitbucket·Knowledge 사실의 근거로 사용할 수 없다.

이 분리 덕분에 정책 문서가 최신 업무 사실처럼 인용되거나, 모델이 근거 없는
open fact를 만드는 경로를 막을 수 있다.

## 6. 요청 실행 시퀀스

~~~mermaid
sequenceDiagram
    actor User as 사용자
    participant LTM
    participant SYS as Jira/Confluence/Bitbucket
    participant DIFY as Dify Workflow
    participant KB as MAXIS Governed Knowledge

    User->>LTM: 분석·초안 요청
    LTM->>SYS: 최소 권한 read
    SYS-->>LTM: source와 freshness
    LTM->>LTM: EvidencePack·DLP·payload schema
    LTM->>DIFY: stateless request
    DIFY->>KB: Sentinel + Role-filtered policy retrieval
    KB-->>DIFY: release proof + policy context
    DIFY->>DIFY: Knowledge Guard
    alt policy allow
        DIFY->>DIFY: Contract LLM → Contract Guard
    else ACL deny 또는 no-hit
        DIFY->>DIFY: deterministic Denied Answer
    end
    DIFY-->>LTM: RemoteRoleResult v1
    LTM->>LTM: ResponseGuard·권한·freshness 재검증
    LTM-->>User: source가 보이는 제안
    User->>LTM: 명시적 일회 승인
    LTM->>SYS: write 직전 재조회
    LTM->>SYS: 승인 범위 내 write
    LTM->>LTM: 내용 없는 audit digest·KPI
~~~

## 7. Knowledge governance

현재 DEV binding은 다음과 같다.

| 항목 | 검증값 |
|---|---|
| Governed Native V2 | dataset <code>97823b6c-2e14-4d13-a2cc-f6082e0a453d</code> |
| Governed corpus | **8문서 / 513 segments**, 최대 151 UTF-8 bytes |
| Role ACL field | <code>ltm_allowed_roles_v1</code>, field ID <code>f8f7023d-1c0f-4893-bf4b-7422b7d25f69</code> |
| Metadata binding | **8/8**, missing·drift·substring collision 0 |
| Sentinel V3 | dataset <code>7953d6ec-094e-470e-b262-26a6a561b091</code> |
| Sentinel document | <code>b4cfe6ce-65b6-4116-a575-3c2607c9116f</code> |
| Sentinel proof SHA-256 | <code>c4146f938c309f2130355b8febd212745f9799359ddb2d90b83480c5ba177ecb</code> |
| Manifest SHA-256 | <code>f126c2d3deb6134bac1db937fa19e135c3dcb98c363f942c11f76d01fbbff329</code> |

Knowledge 변경은 source manifest, exact segment projection, Role metadata, sentinel,
workflow bundle, profile fingerprint를 함께 바꾼다. 어느 하나라도 drift하면 이전
검증은 무효다.

## 8. Profile과 환경 전환

~~~mermaid
stateDiagram-v2
    [*] --> Local
    Local --> CandidateSaved: DEV profile 저장
    CandidateSaved --> ProbePassed: 3-app canary
    ProbePassed --> EvalBaseline: 25-case 실행
    EvalBaseline --> QualityBlocked: 13/25 · releaseEligible false
    QualityBlocked --> CandidateSaved: 개선·재게시
    EvalBaseline --> EvalSigned: 품질 통과·서명 검토
    EvalSigned --> Shadow: DEV shadow
    Shadow --> DevActive: 사람 승인
    DevActive --> Local: kill switch / drift / expiry

    Local --> ProdBlocked: PROD 독립 자산 없음
    ProdBlocked --> ProdCandidate: 독립 dataset·sentinel·apps·key
    ProdCandidate --> ProdActive: 동일 gate + 운영 승인
    ProdActive --> Local: fail closed
~~~

현재 상태:

- DEV bundle: <code>ltm-maxis-dify-2026.09.06-native-acl-v8</code>
- DEV candidate fingerprint: <code>cb40a03650a170d74ced6bf26bed764c</code>
- DEV candidate: <code>profile_verified_at_probe=true</code>, <code>candidate_probe_ok=true</code>
- persisted canary: **3/3 통과**
- active profile: **비어 있음**
- agent_core / editor_author / pmo_brief route: 모두 **local**
- PROD: profile과 독립 자산 **미구성**, <code>active=false</code>, authoritative route **fail closed**

| 앱 | v8 published Workflow ID | 카나리 |
|---|---|---:|
| Agent Core | <code>85add2bc-455e-4155-b0af-d8e939e24565</code> | 2,311 ms |
| Editor Author | <code>a5f28cc2-9ef2-4651-8e77-a1ccde5feb3e</code> | 2,342 ms |
| PMO Brief | <code>cf3a11c6-c237-4889-a9c4-89dbdd9b00b2</code> | 2,013 ms |

환경은 이름, base URL, dataset, sentinel, Workflow ID, bundle version, fingerprint,
암호화 credential을 하나의 profile로 다룬다. URL이나 key만 바꿔 DEV를 PROD처럼
사용하지 않는다.

## 9. MCP 도입 경계

~~~mermaid
flowchart LR
    D[Dify agent] -. Phase 1 직접 접근 금지 .-> SYS[(업무 시스템)]
    L[LTM] -->|현재 connector read| SYS
    M[향후 MCP] -->|read-only·field minimum| L
    ID[OAuth/mTLS<br/>stable identity<br/>host allow-list] --> M
    H[HITL + one-shot capability] -->|write는 LTM만| SYS
~~~

- Phase 1 MAXIS node registry/MCP 등록: **0**
- 현재 목표: Dify Service API와 EvidencePack 경계의 품질·안전성 검증
- Phase 2 후보: OAuth/mTLS, stable Jira identity, host allow-list를 만족하는
  **read-only MCP**
- 금지: Dify 또는 MCP가 approval token, Jira write credential, 임의 query/write
  권한을 소유하는 구조

MCP는 기능 수를 늘리기 위한 필수 요소가 아니다. EvidencePack의 provenance와
권한 경계를 더 잘 보존할 수 있다는 증거가 있을 때만 별도 PoC로 도입한다.

## 10. 피드백 대응 설계

| 1차 심사 의견 | 개편 설계 | 평가 방법 |
|---|---|---|
| 기존 Dashboard 대비 AI 효과가 불명확 | 다중 source 요청 구조화, 작업 분해, 위험 설명, 초안 생성에 집중 | 동일 시나리오의 사용자 소요시간, 수정 횟수, 오류율, 처리량 |
| Task 생성·판단 지식 체계 필요 | 8문서 manifest, Role metadata, versioned Native Knowledge, sentinel | manifest drift 0, ACL-negative, owner 승인과 변경 이력 |
| LTM/Agent/MCP 역할 불명확 | Dify=의미 생성, LTM=사실·검증·승인·쓰기, MCP Phase 1=0 | permission-boundary 테스트와 위협 모델 검토 |
| API 권한·민감정보·승인·감사 필요 | 최소 EvidencePack, DLP, closed schema, dual guard, HITL, 일회 capability, audit digest | 금칙 필드·무승인 write·재사용 capability negative test |
| 최신성·정확도·좁은 PoC 필요 | source freshness, release binding, 세 앱·synthetic-only DEV부터 시작 | retrieval preflight, signed external eval, shadow acceptance |

정량 효과는 목표치가 아니라 실제 baseline 대비 결과로 제시한다. v8 full suite 수치는
아래 PoC 품질 baseline일 뿐, Dashboard 대비 시간 절감이나 업무 의미 정답성을 입증하지
않는다.

## 11. 현재 검증 상태

### 완료된 범위

- Dify 앱 3개에 11노드·11엣지 Workflow v8 게시
- Service API canary **3/3**
- Governed Native V2 **8문서·513 segments**
- Role metadata **8/8**
- filtered retrieval preflight **25/25**
  - positive 8/8 target top-2
  - injection fixture 8/8 target top-2
  - ACL-negative 8/8 forbidden absent
  - no-hit 1/1 zero result

### 실행된 full-suite baseline

| 구분 | 결과 |
|---|---:|
| 실행 / 통과 / 실패 | **25 / 13 / 12**, timeout 0 |
| Positive automated | **4/8** |
| Injection automated / 최종 | **2/8 / 0/8** |
| Injection Guard | **8/8** |
| ACL / No-hit | **8/8 / 1/1** |
| Retrieval | Hit@2 **1.0**, MRR@2 **0.9375** |
| Response shape / outcome status | 각각 **15/25** |
| Contract / version / sentinel / dataset / identity / Role ACL / ledger / include / exclude | 각각 **25/25** |
| Latency | mean **1,741 ms**, p50 **2,118 ms**, p95 **2,957 ms**, total **43,529 ms** |
| Usage | prompt **50,834** / completion **4,686** / total **55,520 tokens**, 25/25 완전 관측 |

Artifact:
<code>.cache/maxis/evals/dev-keval-20260906T052144589439Z-7b820caa.json</code><br>
SHA-256:
<code>10270e72b8f19baca11a3dbc405bb418fd431094b3a21071042953a9293870bd</code>
Harness SHA-256:
<code>df2f1890ce2cd61a95639f49d9697127deb1bdb57d9340b76ccb2c3d27c5a3f1</code>
Artifact schema SHA-256:
<code>44a0c94b936bb65909a58dfdd5ef24b7b4aa62ceee83b94c50e470a11c015576</code>
실행은 승인된 [평가 launcher](../deploy/maxis-dify/launch_knowledge_evals.py)를 사용했고,
<code>evaluationLauncher</code>를 포함한 source hash ledger는 **15/15** 결속됐다.
소스 전환 검증 실행에서 통과 수가 **13~16/25**로 변동했으므로 생성 안정성은 아직
입증되지 않았다. 공식 기준은 현재 소스와 hash가 일치하는 위 artifact 하나뿐이다.

결과는 <code>outcome=failed</code>, <code>releaseEligible=false</code>다. 특히 injection
8건은 signed human review가 없어 최종 0/8이며, semantic answer correctness도
미평가다. 검색과 권한 통제가 강하다는 사실을 생성 품질 합격으로 확대 해석하지 않는다.
Artifact 자체도 privacy flags상 원시 prompt·response·answer·검색 content·credential·
sentinel marker를 저장하지 않는 content-free 증적이다.

### 승격 전 남은 범위

- positive·response-shape 실패 개선과 재평가: **pending**
- injection 8건 signed human review: **pending**
- semantic answer correctness: **not assessed**
- DEV shadow와 human acceptance: **pending**
- PROD 독립 자산과 profile: **unconfigured**
- PROD remote logging·보안 운영 승인: **pending**

preflight, canary, 높은 retrieval 지표는 배선·검색·계약의 선행 증거이지 최종 업무
품질 점수가 아니다. 현재 full suite는 승격 차단 근거로 사용한다.

## 12. 운영·장애 원칙

- Dify timeout, invalid JSON, schema drift, source mismatch, release mismatch는 local
  fallback 또는 명시적 blocked 결과로 처리한다.
- policy deny/no-hit은 예상 가능한 업무 결과이므로 deterministic Denied Answer를
  반환한다. 인프라 실패를 정상 답변처럼 꾸미지 않는다.
- write 직전 ticket version·actor·승인 payload를 다시 비교한다.
- kill switch, profile 변경, Workflow/Knowledge drift가 생기면 remote route를
  비활성화한다.
- 감사에는 request correlation, version/fingerprint, outcome, latency, 검증 code를
  남기되 프롬프트·응답 원문·credential·sentinel 평문은 남기지 않는다.

## 13. 화면 자료

| 자료 | 확인 포인트 |
|---|---|
| [01 · Dify Workflow](images/maxis-dify-implementation/01-dify-workflow.png) | 시각적 Workflow canvas |
| [02 · Dify MCP/HTTP](images/maxis-dify-implementation/02-dify-mcp-http.png) | 외부 연계 기능 |
| [03 · Dify Knowledge](images/maxis-dify-implementation/03-dify-knowledge.png) | Knowledge 관리 |
| [04 · Dify Service API](images/maxis-dify-implementation/04-dify-service-api.png) | 앱별 API |
| [05 · 전체 TO-BE](images/maxis-dify-implementation/05-ltm-maxis-tobe.svg) | LTM/MAXIS/Dify 책임 경계 |
| [06 · Native v8](images/maxis-dify-implementation/06-dify-eleven-node-native-flow.svg) | 현재 11-node topology |

01~04는 제품 기능 이해를 위한 당시 직접 확인 화면이다. 현재 ID와 상태의 source of
truth는 [DEV 환경 binding](../deploy/maxis-dify/environments/dev-2026.09.yml)과
[DEV 배포 증적](../deploy/maxis-dify/deployments/dev-2026.09.yml)이다.

## 14. 실행 순서

1. Knowledge source와 Role metadata를 review하고 환경별 Native dataset에 적재한다.
2. 환경 binding으로 세 import DSL을 materialize하고 validator를 통과시킨다.
3. 세 앱을 게시하고 실행 소유 Workflow ID를 profile 후보에 pin한다.
4. retrieval preflight와 3-app Service API canary를 실행한다.
5. full suite를 실행하고 artifact digest를 환경·release에 결속한다.
6. injection signed review와 semantic correctness 검토를 완료하고 실패 시 개선·재실행한다.
7. DEV shadow와 사용자 수용 기준을 통과한 뒤 명시적으로 route를 활성화한다.
8. PROD는 독립 dataset, sentinel, app, key, profile을 새로 만들고 1~7을 반복한다.
