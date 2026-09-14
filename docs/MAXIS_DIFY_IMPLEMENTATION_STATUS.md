# MAXIS / Dify 적용 구현 상태

> 최신 기준: 2026-09-14<br>
> **DEV 품질 후보 v9를 게시·검증했지만 Editor 5-case는 v8/v9 모두 0/5로 품질 개선을 입증하지 못했다. 활성 경로는 local이며 승격하지 않았다.**

## 2026-09-14 품질 평가 업데이트

- 기존 5종·58개 벤치마크 중 Editor 5개를 합성 fixture·가명화·동일 checker/rubric으로 재사용했다.
- v9의 역할 검색어·공통/Editor 지침을 실제 DEV에 적용했다. 3개 canary는 통과했으나
  본문 생성은 근거·인용 검사에서 모두 차단됐다. 안전 검사를 완화하지 않았다.
- 기존 Knowledge suite의 숨은 채점 조건을 실제 계약에 맞춰 harness v2로 정리했다.
  v9 실측은 12/25, ACL 8/8, no-hit 1/1이며 의미 품질 점수가 아니다. 이전 v1 13/25와 직접 비교하지 않는다.
- 다음 핵심은 대상/부모/seed를 명시하는 입력 구조와 짧은 사실 근거 단위다. 프롬프트 확장만으로 해결되지 않았다.
- 로컬 검증 795건 통과. full UI·58-case 전체 qualification·PROD 전환은 수행하지 않았다.

[최신 품질 보고서](../research/agent-improvement/maxis-dify-quality-20260914.md) ·
[최신 DEV 게시·실측 증적](../deploy/maxis-dify/deployments/dev-quality-2026.09.14.yml)

아래 본문은 **2026-09-06 v8 기준 기록**이다. 현재 게시 UUID와 최종 평가 결과는 위 최신 증적을 따른다.

이 문서는 구현·배포 증적과 공모전 심사 피드백을 한 화면에서 검토하기 위한
현황판이다. API key, 프롬프트, 모델 응답, 사용자 데이터, release sentinel 원문은
기록하지 않는다.

## 1. 한눈에 보는 현재 상태

| 영역 | 확인된 DEV 상태 | 승격 판단 |
|---|---|---|
| Dify Workflow | 앱 3개 모두 **11노드·11엣지 Native v8** 게시 | 카나리 3/3 통과 |
| Policy Knowledge | Governed Native V2 **8문서·513 segments** | Role metadata **8/8** 검증 |
| Release 증명 | 독립 Sentinel V3 dataset·document·SHA 결속 | 단일 문서·단일 segment 검증 |
| LTM profile | 후보 bundle과 fingerprint 저장 | active profile은 비어 있고 세 route는 local |
| PROD | 독립 자산·profile 미구성 | fail closed, authoritative route 불가 |
| 품질 평가 | full suite **25/25 실행, 13 pass / 12 fail** | <code>releaseEligible=false</code>, PoC 개선 필요 |
| MAXIS MCP | Phase 1 등록 노드 **0개** | 향후 read-only 경계만 검토 |

기계 판독 가능한 비밀 비포함 증적은
[DEV 배포 증적](../deploy/maxis-dify/deployments/dev-2026.09.yml)에 있다.

## 2. AS-IS와 TO-BE

### AS-IS — LTM 단독 실행

~~~mermaid
flowchart LR
    U[사용자] --> UI[LTM UI]
    UI --> LG[Local LangGraph]
    LG --> C[Jira·Confluence·Bitbucket<br/>local connector]
    C --> E[Local EvidencePack]
    E --> A[Local semantic agents]
    A --> G[Local ResponseGuard]
    G --> H[사용자 검토·승인]
    H --> W[Jira write]
~~~

### TO-BE — 역할은 분리하되 권한은 LTM에 유지

~~~mermaid
flowchart LR
    U[사용자] --> UI[LTM UI]

    subgraph LTM["LTM 신뢰·실행 영역"]
        UI --> O[Orchestrator]
        O --> Q[사실 조회·결정적 계산]
        Q --> EP[Citation-grade<br/>EvidencePack]
        EP --> AD[Dify Adapter]
        RG[ResponseGuard] --> HITL[검토·일회 승인]
        HITL --> EX[Action Executor]
        EX --> SYS[(Jira 등 업무 시스템)]
        AUD[감사·KPI·freshness] --- HITL
    end

    subgraph MAXIS["MAXIS 거버넌스"]
        PRJ[Project·접근정책]
        KG[Governed Knowledge]
        REL[Release Sentinel]
    end

    subgraph DIFY["Dify 실행"]
        WF[3개 Advanced Chatflow<br/>11 nodes / 11 edges]
        PRAG[Policy RAG]
        CG[Knowledge Guard<br/>Contract Guard]
    end

    AD -->|검증된 최소 입력| WF
    KG --> PRAG --> WF
    REL --> WF
    WF --> CG -->|RemoteRoleResult| AD
    AD --> RG
    PRJ -. governance .-> WF
~~~

핵심은 “모든 에이전트를 원격으로 옮긴다”가 아니다. 의미 판단과 초안은
MAXIS/Dify가 담당하고, 최신 사실 조회·인용 근거·결정적 계산·권한·승인·쓰기는
LTM이 계속 통제한다.

## 3. 실제 DEV 자산

### Knowledge와 release binding

| 자산 | 현재 값 |
|---|---|
| Governed dataset | LTM Governed Knowledge 2026.09 Native V2 |
| Governed dataset ID | <code>97823b6c-2e14-4d13-a2cc-f6082e0a453d</code> |
| 문서 / segment | **8 / 513**, 최대 segment 151 UTF-8 bytes |
| Role metadata | <code>ltm_allowed_roles_v1</code>, 8/8 결속, missing·drift·substring collision 0 |
| Sentinel dataset | LTM Release Sentinel dev 2026.09 V3 |
| Sentinel dataset ID | <code>7953d6ec-094e-470e-b262-26a6a561b091</code> |
| Sentinel document ID | <code>b4cfe6ce-65b6-4116-a575-3c2607c9116f</code> |
| Sentinel proof SHA-256 | <code>c4146f938c309f2130355b8febd212745f9799359ddb2d90b83480c5ba177ecb</code> |
| Manifest SHA-256 | <code>f126c2d3deb6134bac1db937fa19e135c3dcb98c363f942c11f76d01fbbff329</code> |

Sentinel 평문은 저장소 문서와 증적에 남기지 않는다. LTM은 평문 대신 환경별 SHA와
Dify 실행 소유 Workflow ID를 결속해 잘못 게시된 앱·Knowledge를 차단한다.

### Dify 앱과 Workflow v8

| 앱 | App ID | 게시 Workflow ID | 카나리 |
|---|---|---|---|
| Agent Core | <code>d061c2e6-72cf-4fc4-b835-4cb124e1f96c</code> | <code>85add2bc-455e-4155-b0af-d8e939e24565</code> | 통과, 2,311 ms |
| Editor Author | <code>dbda2328-0547-4e0c-967d-c14265b27459</code> | <code>a5f28cc2-9ef2-4651-8e77-a1ccde5feb3e</code> | 통과, 2,342 ms |
| PMO Brief | <code>ceb6bdc2-0276-4736-8c8b-59fc69f85cd6</code> | <code>cf3a11c6-c237-4889-a9c4-89dbdd9b00b2</code> | 통과, 2,013 ms |

- Bundle: <code>ltm-maxis-dify-2026.09.06-native-acl-v8</code>
- Candidate fingerprint: <code>cb40a03650a170d74ced6bf26bed764c</code>
- Candidate verification: <code>profile_verified_at_probe=true</code>, <code>candidate_probe_ok=true</code>
- Persisted Service API canary: **3/3 HTTP 200 및 계약·Knowledge binding 통과**
- Active profile: **없음**
- Active route: agent_core / editor_author / pmo_brief 모두 **local**
- PROD: <code>profile_configured=false</code>, <code>active=false</code>, authoritative route **fail closed**

카나리 통과는 최종 품질 승인과 다르다. v8 full suite는 전 건 실행됐지만
13/25만 통과했고, injection 서명 검토와 의미 정답성 평가도 남아 있다. 후보 profile은
활성화하지 않았으며 로컬 경로가 기준이다.

## 4. 최종 11노드 / 11엣지 그래프

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

이 구조는 ACL 거부와 no-hit을 LLM에 보내지 않고 결정적으로 Denied Answer로
직렬화한다. allow 경로도 Contract Guard를 통과해야만 응답을 내보낸다.

정적 뷰어용:
[11노드 Native flow SVG](images/maxis-dify-implementation/06-dify-eleven-node-native-flow.svg)

## 5. Policy RAG와 EvidencePack은 다른 근거다

~~~mermaid
flowchart TB
    PK[Policy RAG<br/>규칙·템플릿·허용 행동] -->|생성 제약| LLM[Contract LLM]
    EP[EvidencePack<br/>업무 시스템 사실·계산·source IDs] -->|claim·citation 근거| LLM
    LLM --> GUARD[Contract Guard + LTM ResponseGuard]
    GUARD --> OUT[검증된 결과]

    PK -. 금지 .-> FC[사실 claim·업무 citation]
    EP --> FC
~~~

| 구분 | Policy RAG | Citation-grade EvidencePack |
|---|---|---|
| 생성 위치 | Dify Governed Knowledge 검색 | LTM connector와 결정적 계산 |
| 용도 | 형식, 정책, 검증 절차, 허용 행동 제한 | 현재 업무 사실, 수치, 인용의 근거 |
| citation 자격 | 없음 | 검증된 source만 있음 |
| 최신성 책임 | Knowledge owner·manifest | LTM 조회 시각·source freshness |
| source가 비었을 때 | claim을 만들 수 없음 | 폐쇄형 control-only payload만 허용 |

즉 Policy 문서가 검색됐다는 이유만으로 실제 Jira 상태나 사람·일정에 관한 사실을
말할 수 없다. claim, citation, payload claim은 EvidencePack의 검증된 source에
결속되어야 한다. <code>ltm:user-intent:</code> source는 사용자의 요청·선호·계획
판단만 지지하며 Jira·Confluence·Bitbucket·Knowledge 사실의 근거로 사용할 수 없다.

## 6. 요청에서 응답까지

~~~mermaid
sequenceDiagram
    actor User as 사용자
    participant LTM
    participant API as 업무 시스템 API
    participant Dify
    participant KB as Policy Knowledge

    User->>LTM: 분석·초안 요청
    LTM->>API: read-only 최신 사실 조회
    API-->>LTM: source ID·조회시각·원문 일부
    LTM->>LTM: 최소 EvidencePack·DLP·schema 구성
    LTM->>Dify: 6개 Start control + EvidencePack
    Dify->>KB: Sentinel / Role metadata-filtered retrieval
    KB-->>Dify: release proof + policy context
    Dify->>Dify: Knowledge Guard → Policy Decision
    alt allow
        Dify->>Dify: Contract LLM → Contract Guard
    else deny 또는 no-hit
        Dify->>Dify: Denied Answer
    end
    Dify-->>LTM: closed RemoteRoleResult
    LTM->>LTM: ResponseGuard·freshness·권한 재검증
    LTM-->>User: 근거와 제안 표시
    User->>LTM: 명시적 일회 승인
    LTM->>API: 직전 재조회 후 제한된 write
~~~

## 7. Agent / MCP / 권한 경계

~~~mermaid
flowchart LR
    subgraph READ["읽기"]
        J[Jira]
        C[Confluence]
        B[Bitbucket]
        RO[향후 read-only MCP]
    end
    subgraph REASON["판단·작성"]
        M[MAXIS 정책·권한]
        D[Dify semantic roles]
    end
    subgraph CONTROL["통제·쓰기"]
        L[LTM schema·DLP·audit]
        H[사용자 HITL]
        X[일회 capability write]
    end

    J --> L
    C --> L
    B --> L
    RO -. Phase 2 후보 .-> L
    L --> D
    M -. governance .-> D
    D --> L
    L --> H --> X
    X --> J
    D -. 직접 credential·write 금지 .-> X
~~~

Phase 1의 MAXIS node registry/MCP 등록 수는 **0**이다. Dify에는 업무 시스템
credential과 write tool을 주지 않는다. 향후 MCP를 도입하더라도 stable identity,
OAuth/mTLS, host allow-list, 필드 최소화가 적용된 **read-only** 경계부터 별도
승격한다.

## 8. 심사 피드백 대응

| 심사 보완점 | 구현·검증으로 연결한 내용 | 아직 필요한 증적 |
|---|---|---|
| 기존 Dashboard 대비 정량 효과 | audit event와 KPI baseline, latency·오류·처리량 측정 지점 | 동일 과제군 전/후 시간절감·오류율·처리량의 signed 결과 |
| Task 생성 판단 지식 체계 | manifest pin, Native V2, Role metadata 8/8, Sentinel V3 | 지식 owner 승인·변경 주기 운영 기록 |
| LTM / Agent / MCP 역할 분담 | 의미 생성은 Dify, 사실·계산·검증·승인·write는 LTM, MCP Phase 1=0 | read-only MCP Phase 2 위협 모델·별도 PoC |
| API 권한·민감정보·HITL·감사 | DLP 최소화, closed schema, dual guard, one-shot 승인, write 직전 재검증·audit digest | 운영 계정 최소 권한 검토와 사용자 수용성 평가 |
| 데이터 최신성·정확도·좁은 PoC | source freshness, sentinel/workflow binding, preflight 25/25, 세 앱 canary와 full-suite baseline | positive 실패 개선, injection 서명 검토, 의미 정답성·shadow 승인 |

## 9. 현재 검증과 남은 게이트

### 확인 완료

- Governed Knowledge: **8문서 / 513 segments**
- Role metadata: **8/8**, 누락·drift·substring collision 0
- Filtered retrieval preflight: **25/25**
  - positive 8/8 target top-2
  - injection fixture 8/8 target top-2
  - ACL-negative 8/8 forbidden absent
  - no-hit 1/1 zero result
- Dify Service API canary: **3/3**
- 세 앱 모두 11노드·11엣지, execution/version/citation/knowledge contract 확인

### v8 full-suite PoC baseline

실행 artifact는
<code>.cache/maxis/evals/dev-keval-20260906T052144589439Z-7b820caa.json</code>이며
SHA-256은
<code>10270e72b8f19baca11a3dbc405bb418fd431094b3a21071042953a9293870bd</code>다.
Harness SHA-256은
<code>df2f1890ce2cd61a95639f49d9697127deb1bdb57d9340b76ccb2c3d27c5a3f1</code>다.
Artifact schema SHA-256은
<code>44a0c94b936bb65909a58dfdd5ef24b7b4aa62ceee83b94c50e470a11c015576</code>다.
실행은 승인된 [평가 launcher](../deploy/maxis-dify/launch_knowledge_evals.py)를 사용했다.
<code>evaluationLauncher</code>를 포함한 source hash 15개도 artifact와 배포 증적에 모두
결속했다.
Artifact 자체도 privacy flags상 원시 prompt·response·answer·검색 content·credential·
sentinel marker를 저장하지 않는 content-free 증적이다.
소스 전환 검증 실행에서는 통과 수가 **13~16/25**로 변동했다. 생성 안정성은 아직
입증되지 않았으며, 아래 공식 수치는 현재 소스와 hash가 일치하는 이 artifact만 기준으로
삼는다.

| 지표 | 실제 결과 | 해석 |
|---|---:|---|
| 전체 | **25/25 실행, 13 pass / 12 fail** | outcome failed, <code>releaseEligible=false</code> |
| Positive | automated **4/8** | 4건 개선 필요 |
| Injection | automated **2/8**, 최종 **0/8** | 8건 모두 signed human review pending |
| Injection Guard | **8/8** | guard 신호 통과와 end-to-end 품질 통과는 다름 |
| ACL | **8/8** | forbidden retrieval 차단 |
| No-hit | **1/1** | deterministic abstention |
| Retrieval | Hit@2 **1.0**, MRR@2 **0.9375** | 대상 문서 검색은 강함 |
| Response shape / outcome status | 각각 **15/25** | 구조화 응답 안정성 개선 필요 |
| Contract / version / sentinel / dataset / identity / Role ACL / ledger / include / exclude | 각각 **25/25** | 계약·binding·정책 무결성은 전 건 통과 |
| Timeout | **0** | 실행 중 timeout 없음 |
| Latency | mean **1,741 ms**, p50 **2,118 ms**, p95 **2,957 ms** | 총 43,529 ms |
| Usage | prompt 50,834 / completion 4,686 / total **55,520 tokens** | 25/25 완전 관측 |

이 결과는 성공 수치가 아니라 **현 상태를 숨기지 않은 PoC baseline**이다. 검색
적중과 ACL 통제는 확인됐지만 positive 응답 품질, response shape, injection human
review가 승격 기준에 미달한다. semantic answer correctness는 아직 평가하지 않았다.

### 아직 완료로 주장하지 않는 항목

- injection 8건 signed human review: **pending**
- semantic answer correctness: **not assessed**
- positive/response-shape 실패 원인 개선과 재평가: **pending**
- dev authoritative activation과 shadow acceptance: **pending**
- PROD 독립 dataset/app/key/profile: **unconfigured**
- PROD remote logging·보안 승인: **pending**

PROD는 DEV dataset, app, key, fingerprint를 재사용하지 않는다. 독립 자산과 서명 평가가
없으면 활성화를 거부하는 fail-closed 상태다.

## 10. 직접 확인 화면

| 화면 | 용도 |
|---|---|
| [01 · Dify Workflow](images/maxis-dify-implementation/01-dify-workflow.png) | Workflow canvas와 노드 구성 확인 |
| [02 · Dify MCP/HTTP](images/maxis-dify-implementation/02-dify-mcp-http.png) | 연계 기능 검토 화면 |
| [03 · Dify Knowledge](images/maxis-dify-implementation/03-dify-knowledge.png) | Knowledge 관리 기능 확인 |
| [04 · Dify Service API](images/maxis-dify-implementation/04-dify-service-api.png) | API 제공 화면 확인 |
| [05 · LTM TO-BE](images/maxis-dify-implementation/05-ltm-maxis-tobe.svg) | 전체 책임 경계 정적 도식 |
| [06 · Native 11-node flow](images/maxis-dify-implementation/06-dify-eleven-node-native-flow.svg) | 현재 게시 topology 정적 도식 |

01~04는 기능 이해를 위한 당시 직접 확인 화면이다. ID·상태의 기준은 화면 캡처가 아니라
현재 환경 binding과 DEV 배포 증적이다.

## 11. 재개 기준

1. positive 4건과 response shape 실패를 분석·개선하고 같은 25-case를 재실행한다.
2. injection 8건의 signed human review와 semantic answer correctness 평가를 완료한다.
3. DEV shadow와 사용자 수용 기준을 통과한 경우에만 dev route 활성화를 검토한다.
4. PROD는 독립 Knowledge·Sentinel·앱·key·profile을 만든 뒤 같은 게이트를 처음부터
   통과시킨다.
