# LTM MAXIS/Dify 적용 아키텍처와 운영 가이드

이 문서는 Lake Task Manager(LTM)의 의미 판단·초안 작성 기능을 MAXIS/Dify로
이관할 때의 구현 경계와 승격 절차를 정의한다. 핵심 원칙은 다음과 같다.

> MAXIS/Dify는 **근거를 바탕으로 제안**하고, LTM은 **사실 조회·계산·검증·승인·실행**한다.

## 1. TO-BE 구조

```mermaid
flowchart LR
    U[사용자] --> UI[LTM UI]

    subgraph TRUST["LTM 신뢰 영역"]
        UI --> QR[Query Runner<br/>Jira·Confluence·Bitbucket 조회]
        QR --> CG[Context Guard<br/>범위·DLP·가명화·freshness]
        RG[Response Guard<br/>Schema·인용·금칙값 검증] --> WS[Evidence Workspace]
        WS --> AP[사용자 검토·일회 승인]
        AP --> EX[Action Executor]
        CALC[결정적 PMO 계산] --> CG
        AUDIT[감사·효과 지표] --- AP
        EX --> JIRA[(Jira 쓰기)]
    end

    subgraph MAXIS["MAXIS 관리 계층"]
        GOV[Project·권한·승인 모델]
        KB[Knowledge<br/>작성 규칙·템플릿·산식 설명]
    end

    subgraph DIFY["Dify 실행 계층"]
        START[6개 Start 변수]
        SENTINEL[Release Sentinel Retrieval]
        RAG[Role Knowledge Retrieval]
        CORE[agent_core Chatflow]
        EDITOR[editor_author Chatflow]
        PMO[pmo_brief Chatflow]
    end

    CG -->|EvidencePack v1만 전송| START
    START --> SENTINEL
    START --> RAG
    SENTINEL --> CORE
    RAG --> CORE
    SENTINEL --> EDITOR
    RAG --> EDITOR
    SENTINEL --> PMO
    RAG --> PMO
    CORE -->|RemoteRoleResult v1| RG
    EDITOR -->|RemoteRoleResult v1| RG
    PMO -->|RemoteRoleResult v1| RG
    GOV -.-> CORE
    KB -.-> CORE
    DIFY -.->|2단계 선택 기능| MCP[OAuth·mTLS Read-only HTTP MCP]
    MCP --> QR
```

### 직접 확인한 현재 Dify 환경

기존 PoC는 Knowledge 조회·문서 추출·LLM·Answer를 잇는 Chatflow이며 게시와 API
접근 메뉴를 제공한다. 합성 요청 시험에서는 입력에 없던 담당자와 날짜를 만들지는 않았지만,
strict JSON·source ID·freshness 계약은 없었다. 따라서 기존 앱을 운영 경로로 바로 쓰지 않고
아래 세 앱 계약으로 재구성한다.

![기존 Dify Chatflow와 합성 데이터 미리보기](images/maxis-dify-implementation/01-dify-workflow.png)

Dify 도구 화면에서 HTTP MCP 서버 등록 기능을 확인했다. 이 화면에 연결할 대상은 로컬 PC가
아니라 OAuth·mTLS를 적용해 중앙 배포한 읽기 전용 LTM MCP다.

![Dify의 HTTP MCP 서버 등록 기능](images/maxis-dify-implementation/02-dify-mcp-http.png)

Knowledge 화면은 Dify 내부 지식 생성·파이프라인과 외부 지식 API 연결을 지원한다. 정적
작성 규칙은 Knowledge로, 최신 Jira 사실은 EvidencePack으로 나누는 이유다.

![Dify Knowledge와 외부 지식 API 진입점](images/maxis-dify-implementation/03-dify-knowledge.png)

현재 dev 앱의 Service API는 Advanced Chat API와 `/chat-messages`를 제공하지만 화면에
표시된 Base URL이 평문 HTTP다. 합성 화면 시험 외에 내부 데이터를 전송하지 않으며,
승인된 HTTPS endpoint가 제공될 때까지 LTM의 보안 정책이 활성화를 거부한다.

![Dify Advanced Chat Service API 화면](images/maxis-dify-implementation/04-dify-service-api.png)

### 역할 분담

| 계층 | 책임 | 소유하지 않는 것 |
|---|---|---|
| LTM | 최신 원천 조회, PMO 산식, 허용 범위, DLP, 결과 검증, 승인, Jira 쓰기 | 원격 Prompt/Workflow 운영 |
| MAXIS | 사내 Project, 승인 모델, Knowledge, 권한·자산 거버넌스 | LTM 승인 토큰과 Jira 쓰기 권한 |
| Dify | 역할 라우팅, RAG, 의미 해석, 초안·보고 문장 생성 | 수치 재계산, 최종 승인, 직접 변경 |
| HTTP MCP | 중앙에서 승인한 읽기 전용 Tool 제공 | 생성·수정·삭제 Tool |

`Query Runner`, `Action Executor`와 승인 토큰은 항상 LTM에 남는다. `Research Analyst`는
현재 public web/GitHub 근거에 대한 tenant·host 계약이 없어 로컬 수집·종합을 유지하고,
검증된 결과만 후속 원격 Role에 전달한다. `People Advisor`도 실행 가능한 담당자 ID를
원격 모델이 만들지 못하도록 이번 단계에서는 로컬 고정이다. Editor의 기존 담당자 표시는
EvidencePack에서 HMAC 기반 `[[person:…]]` 참조로만 전송하고, authoritative 결과에 한해
그 요청에서 검증한 로컬 매핑으로 복원한다.

## 2. 실행 프로필

MAXIS/Dify 설정은 기존 LLM Provider 설정과 분리한다. 코드를 다시 빌드하지 않고
UI 또는 환경변수로 활성 프로필을 바꾼다.

| 항목 | dev | prod |
|---|---|---|
| 데이터 정책 | `synthetic_only` | `internal_read_only` |
| HTTP 허용 | loopback 개발 서버만 | 금지, HTTPS 필수 |
| 용도 | 합성 데이터 계약 시험 | 승인된 내부 업무 데이터 |
| 자격증명 | Windows 사용자 DPAPI 또는 환경변수 | 배포 Secret 환경변수 권장 |

DEV에서는 사용자가 입력한 질문·근거·Role context·사용자 식별값을 그대로 보내지 않는다.
네트워크 직전 모두 고정 합성 fixture와 합성 가명으로 교체하며, DEV는 canary와 shadow 전용이다.
`maxis` authoritative route는 서버와 UI 양쪽에서 거부한다.

환경변수는 저장된 UI 설정보다 우선한다.

```text
LAKE_MAXIS_PROFILE=dev|prod
LAKE_MAXIS_BASE_URL=https://<approved-dify-host>/v1
LAKE_MAXIS_WORKFLOW_BUNDLE_VERSION=<release-manifest-version>
LAKE_MAXIS_KNOWLEDGE_VERSION=<knowledge-manifest-version>
LAKE_MAXIS_KNOWLEDGE_DATASET_ID=<canonical-dify-dataset-uuid>
LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256=<sha256-of-retrieved-release-marker>
LAKE_MAXIS_AGENT_CORE_VERSION=<published-workflow-id>
LAKE_MAXIS_EDITOR_AUTHOR_VERSION=<published-workflow-id>
LAKE_MAXIS_PMO_BRIEF_VERSION=<published-workflow-id>
LAKE_MAXIS_AGENT_CORE_API_KEY=<server-side secret>
LAKE_MAXIS_EDITOR_AUTHOR_API_KEY=<server-side secret>
LAKE_MAXIS_PMO_BRIEF_API_KEY=<server-side secret>
```

API Key는 브라우저에 다시 표시하지 않으며 로그·prefs·Git에 저장하지 않는다. Windows
UI 저장은 현재 사용자 범위 DPAPI를 사용한다. 프로필이나 Key가 바뀌면 이전 연결 검증은
즉시 무효가 된다.

각 dev/prod 프로필은 별도의 Dify Knowledge dataset UUID를 필수로 가진다. LTM은
`config/maxis-knowledge.yml`의 chunk 정책과 추적 문서 각각의 SHA-256, Knowledge version,
dataset UUID를 canonical manifest로 묶어 다시 SHA-256한다. 이 digest는 공개 가능한 배포
식별자로서 프로필 fingerprint, 연결 canary, Agent thread snapshot, 모든 원격
`EvidencePackV1.knowledge_manifest_sha256`에 동일하게 고정한다. 문서·chunk 정책·dataset 중
하나가 달라지면 기존 probe와 진행 중 원격 thread를 재사용하지 않는다.

또한 dataset마다 `LTM_RELEASE_SENTINEL_V1=<환경별 난수>` 문서를 둔다. Dify의 전용
Knowledge Retrieval node가 이 줄을 찾아 `knowledge_proof`로 반환하면 LTM이 로컬 프로필의
SHA-256과 비교한다. 기대 원문·digest는 여섯 Start 변수 어느 곳에도 싣지 않는다. 따라서
단순 manifest echo와 달리 잘못된 dataset binding이나 누락된 Retrieval node를 활성화 전에
찾을 수 있다. 이는 배포 배선 canary이며, 의도적으로 표식을 hard-code한 악성 Workflow를
막는 암호학적 증명은 아니다.

Workflow ID는 현행 Dify가 게시 시 발급한 canonical UUID를 우선 사용한다. 기존 MAXIS
fork가 opaque revision 이름을 쓰는 동안에는 그 값을 강제로 변환하지 않고 호환 입력으로
허용한다. 대신 연결 검증이 실제 `/chat-messages` 요청에 그 ID를 전달하고 응답의
`workflow_version`이 정확히 같은지 확인한다. 서버가 legacy ID를 받지 않는 버전으로
승격되면 검증이 실패하므로 해당 앱을 UUID로 다시 게시한 뒤 프로필을 갱신한다. ID를
요청에서 조용히 빼거나 실패한 버전을 자동 치환하지 않는다.

한 Agent 대화는 시작 시 `profile + route + workflow/knowledge version`을 고정한다.
대화 도중 설정이 달라지면 새 대화를 요구해 서로 다른 버전의 결과가 섞이지 않게 한다.

## 3. 세 개의 Dify 앱 계약

모든 앱은 `POST /v1/chat-messages`, blocking, stateless 방식의 Chatflow로 게시한다.
Start 입력은 임의 상태가 아니라 아래 여섯 필드만 받는다. `context_pack`은 Dify의
Paragraph 입력 호환성을 위해 검증된 `EvidencePackV1`을 canonical JSON 문자열로
직렬화한 값이다.

`context_pack`, `role_id`, `correlation_id`, `output_contract`, `workflow_version`,
`knowledge_version`

게시 앱의 `/parameters`도 이름만 보지 않는다. `context_pack`은 최소 20,000자의 Paragraph,
나머지는 최소 128자의 Text Input이어야 한다(`max_length` 미지정은 무제한으로 인정). 작은
합성 canary만 통과하고 실제 EvidencePack이 잘리는 배포 설정을 활성화 단계에서 차단한다.

| 앱 | 대상 | 입력 | 출력 |
|---|---|---|---|
| `agent_core` | 요청 분석, 조회 계획, 근거 정리, 업무 구조화, 감사, 응답 통합 | `context_pack`, `role_id`, 버전·상관 ID | `RemoteRoleResultV1` |
| `editor_author` | Description·Comment의 의미 초안 | 동일 | `RemoteRoleResultV1(payload=EditorDraftV1)` |
| `pmo_brief` | LTM이 계산한 변화·위험의 설명 | 동일 | `RemoteRoleResultV1(payload=PortfolioResultV1)` |

응답은 Markdown 코드 블록 없이 단일 JSON 객체여야 한다.

```json
{
  "contract": "remote-role-result.v1",
  "role_id": "work_architect",
  "status": "ok",
  "payload": {
    "summary": "근거에서 확인한 사실"
  },
  "claims": [
    {
      "claim_id": "claim_1",
      "kind": "fact",
      "text": "근거에서 확인한 사실",
      "citation_ids": ["citation_1"]
    }
  ],
  "payload_claims": [
    {
      "path": "/summary",
      "claim_ids": ["claim_1"]
    }
  ],
  "citations": [
    {
      "citation_id": "citation_1",
      "source_id": "ctx_...",
      "locator": "필드 또는 문단"
    }
  ],
  "missing_inputs": [],
  "warnings": [],
  "workflow_version": "<published-version>",
  "knowledge_version": "<knowledge-manifest-version>",
  "knowledge_manifest_sha256": "<64-char-manifest-sha256>",
  "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=<retrieved-release-marker>"
}
```

사실과 권고는 반드시 전달받은 `source_id`를 인용해야 한다. 의미 있는 payload 필드는
JSON Pointer 기반 `payload_claims`로 해당 claim과 연결해야 하며, LTM은 식별자·숫자·URL의
정확한 출처 일치와 fact 문장의 70% 이상 lexical grounding을 기계적으로 확인한다. 이는
의미적 진실 판정이 아니므로 확인되지 않은 내용은
`inference`, `open_fact` 또는 `missing_inputs`로 분리한다. Dify는 입력 수치를 다시
계산하거나 담당자·일정·URL을 추측하지 않는다. 또한 응답의
`knowledge_manifest_sha256`은 입력 EvidencePack 값을 그대로 반환해야 하며, LTM은 고정된
프로필의 값과 정확히 일치하지 않으면 연결 검증과 실제 실행을 모두 차단한다.

## 4. 신뢰 경계

### LTM → MAXIS/Dify

`ContextGuard`가 만든 `EvidencePackV1`만 네트워크를 통과한다.

- 최대 source 10개, source당 1,500자, 전체 20,000자
- 허용 Jira Project·Confluence Space·Bitbucket Repository만 포함
- 조회·갱신 시각과 content SHA-256 포함
- 사용자·담당자 식별자는 LTM 프로세스 범위의 비가역 가명으로 치환
- 이메일, 사번 패턴, Authorization/JWT/API Key/Password 차단
- 승인 토큰, 실행 receipt, 전체 대화/상태 객체 전송 금지
- 15분 freshness 표시; 변경 초안의 오래된 근거는 실패 처리

### MAXIS/Dify → LTM

`ResponseGuard`를 통과한 결과만 기존 Role 검증기로 전달한다.

- strict JSON, 추가 필드 금지
- 역할 ID·Workflow 버전·Knowledge 버전 일치
- fact/recommendation의 source 인용 강제
- 미제공 source, 식별자, Secret, 승인·변경 capability 차단
- 기존 Role별 Schema와 결정적 후처리 규칙 재검증
- `maxis` 실패 시 로컬 LLM으로 조용히 대체하지 않음
- `shadow` 결과는 화면·상태·승인 payload에 반영하지 않고 폐기

화면에는 source 본문 대신 제목, 원천 ID, 조회·갱신 시각, freshness와
`사실/추론/권고/미확인` 구분만 표시한다. 링크는 MAXIS 응답 URL을 사용하지 않고, LTM이
검증한 단일 Jira key일 때만 same-origin `/browse/KEY`를 생성한다.

## 5. 읽기 전용 HTTP MCP

현재 구현은 기존 LTM MCP Tool을 재사용하되 HTTP transport에는 별도 인증 경계를 둔다.
다만 1단계 Chatflow는 MCP를 호출하지 않고 LTM이 만든 EvidencePack만 받는다. 아래 중앙
HTTP MCP는 네트워크·IdP 승인을 거친 2단계 읽기 확장 경로다.

- OAuth 2.0 token introspection: `active`, subject, audience, `ltm:read`, expiry 확인
- IdP의 승인된 사용자 claim을 실제 LTM/Jira 세션 사용자와 일치시켜 confused-deputy 차단
- 운영은 HTTPS와 mTLS(또는 승인된 mTLS 종료 Proxy) 필수
- DNS rebinding 방지를 위한 Host 제한
- stateless Streamable HTTP `/mcp`
- Jira/Confluence/Bitbucket **읽기 Tool만** 노출
- Token과 introspection 원문은 로그에 남기지 않음

Dify의 `MCP 서버 추가(HTTP)`에는 중앙에 배포된 URL만 등록한다. 사용자 PC의 LTM
포트를 직접 공개하지 않는다. DCR 사용 여부, OAuth client 발급과 방화벽 정책은 사내
IdP·MAXIS 운영팀 승인 후 결정한다. IdP `sub`와 Jira ID가 다르면
`LTM_MCP_OAUTH_IDENTITY_CLAIM`에 운영팀이 승인한 Jira 식별 claim 이름을 지정한다.
claim 매핑 없이 공용 매니저 Jira 세션을 대신 사용하는 구성은 허용하지 않는다.

## 6. 승격 절차와 중단 조건

```mermaid
flowchart LR
    L[local<br/>기존 동작] --> S[shadow<br/>원격 결과 폐기·비교]
    S --> G{품질·보안·속도 Gate}
    G -->|통과| M[maxis<br/>원격 결과 사용]
    G -->|실패| L
    M -->|장애·정책 위반| K[kill switch]
    K --> L
```

1. 후보 프로필과 세 앱의 버전을 저장한다.
2. 각 앱의 `/parameters` 필수 변수 검사를 통과한다.
3. 각 앱에 익명 합성 `EvidencePackV1`과 `RoleContextV1`을 보내 실제
   `/chat-messages` canary를 실행한다. `RemoteRoleResultV1`, Role, Workflow/Knowledge
   버전, 동적 합성 source 인용, 환경별 Knowledge release sentinel 조회가 모두 맞아야
   프로필이 `verified`가 된다.
4. 프로필을 명시적으로 활성화한다.
5. 역할군을 `local → shadow`로 전환한다. 로컬 결과는 즉시 사용자에게 반환하고,
   MAXIS 호출은 worker 2개·대기 8개로 제한된 비동기 큐에서 실행한다.
6. 같은 `profile fingerprint + workflow + Role + workflow version + knowledge version`에 대해
   **해당 Workflow의 원격 허용 Role 각각** 완료 표본 5건 이상, 호출 성공률 95% 이상,
   계약·인용 유효율 100%, 구조 유사도 80% 이상, 수치가 있는 표본의 수치 일치율 80%
   이상을 모두 통과한 Workflow만 `maxis`로 전환한다. 한 Role의 표본을 다른 payload 계약의
   승격 근거로 합산하지 않는다.
7. 장애·유출 의심·버전 불일치 시 kill switch를 켠다. 진행 중 대화는 새 대화로
   재시작한다.

Canary에는 사내 문서·사용자 ID·승인 정보·API Key가 들어가지 않으며 응답 본문도 저장하지
않는다. 화면과 prefs에는 계약별 성공 여부, 오류 코드, 소요 시간만 남긴다. 원격 transport
실패는 호출 객체가 바뀌어도 `profile fingerprint + workflow key + API Key digest` 범위로
공유된다. 5분 안에 3회 실패하면 2분간 회로가 열리고, 프로필 또는 Key 회전은 새 범위를
사용한다. digest만 프로세스 메모리에 유지하며 원문 Key는 breaker 상태에 저장하지 않는다.

Shadow 비교도 Prompt·로컬 결과·원격 결과·Jira Key·사용자/원본 대화·상관 ID를 저장하지 않는다.
비교 worker 안에서만 구조, 숫자, 비어 있음, 어휘 겹침을 계산하고
`.cache/maxis_shadow_quality.json`에는 불변 배포 차원별 누계와 비율만 기록한다. 큐가 차면
로컬 응답을 지연시키지 않고 해당 shadow 실행을 버린 뒤 `queueDropped` 카운터만 올린다.
큐에는 원격 전송이 허용된 bounded EvidencePack과 가명 요청 ID만 들어가며, Editor shadow는
원본 사용자 ID 역매핑 없이 EvidencePack에 이미 있던 opaque person marker만 허용한다.
`prod` 프로필은 `shadow` 후보로 활성화할 수 있지만, `maxis` route 전환 또는 이미
`maxis`인 route를 가진 상태의 prod 활성화에는 위 Gate가 강제된다. 버전·Knowledge 문서·
dataset이 바뀌어 profile fingerprint가 달라지면 이전 표본은 자동으로 승격 근거에서 제외된다.

운영 MAXIS가 비 HTTPS Service API만 제공하면 실제 업무 데이터 경로는 활성화하지
않는다. endpoint, 인증서 체인, 로그 보존·마스킹 정책이 승인된 뒤 `prod`를 검증한다.

## 7. 심사 피드백에 대한 검증 지표

LTM은 사용자 내용이 아닌 집계 메타데이터만 `.cache/agent_impact.json`에 기록하고,
원격 호출 lifecycle은 내용 없는 `.cache/maxis_audit.jsonl` 감사 ledger로 분리한다.
효과 집계는 feature·Role·local/MAXIS engine·profile·Workflow bundle·Knowledge version·
manifest SHA별 segment로도 나뉘어 Local 초안과 MAXIS 초안의 전환 효과를 같은 배포 단위에서
비교할 수 있다. Jira Dashboard·수작업의 도입 전 baseline은 아직 자동 수집하지 않으므로,
PoC 시작 전 동일 업무 20건의 소요 시간·오류·처리량을 별도 측정해 대조한다. 설정 변경 API와
연결 확인·활성화·route·kill switch는 manager만 실행한다.

| 피드백 | 구현 근거 | 측정 예 |
|---|---|---|
| 기존 Dashboard와 AI 차별성 | PMO Delta Briefing, 근거 기반 Task/Comment 초안 | 승인율, 큰 수정 없는 승인율, 평균 검토 시간 |
| 지식 체계 | Workflow/Knowledge version 고정, source manifest | 버전별 계약 통과율, 인용 완전성 |
| LTM·Agent 역할 불명확 | Context/Response Guard와 로컬 승인·실행 경계 | 원격 쓰기 Tool 0개, 경계 위반 차단 건수 |
| 권한·민감정보·감사 | DPAPI/환경 Secret, DLP, OAuth/mTLS MCP, 감사 이벤트 | Secret 노출 0, 승인 없는 변경 0 |
| 최신성·정확도 | retrieved/updated 시각, freshness, 결정적 PMO 산식, 비동기 shadow 품질 Gate | stale 차단율, 수치·구조·인용 일치율, 호출 성공률·지연 |

효과 지표에는 Prompt·생성문·Jira Key·사용자 ID·API Key·승인 토큰을 저장하지 않는다.

## 8. 운영 준비 체크리스트

- [ ] MAXIS Project와 Dify Workspace의 소유자·운영자 지정
- [ ] 세 Chatflow의 strict JSON 출력과 게시 버전 고정
- [ ] 운영 HTTPS Gateway와 인증서 검증
- [ ] API Key 발급·회전·회수 절차와 배포 Secret 등록
- [ ] Knowledge manifest, 청크 기준, 갱신 책임자 지정
- [ ] dev/prod Dify Knowledge dataset UUID 분리 및 `LAKE_MAXIS_KNOWLEDGE_DATASET_ID` 등록
- [ ] 환경별 release sentinel 문서 업로드 및 `LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256` 등록
- [ ] 설정 화면의 Knowledge manifest SHA와 Dify에 게시한 dataset release 대조
- [ ] 합성 데이터 회귀 → shadow 비교 → 제한 사용자 PoC 순서 준수
- [ ] prod 대상 Workflow별 exact-version shadow 표본 5건과 승격 Gate 통과 확인
- [ ] Dify/MAXIS/LTM 상관 ID로 장애 추적 가능 여부 확인
- [ ] 로그 보존 기간과 입력·출력 마스킹 검토
- [ ] kill switch 훈련과 복구 절차 검증
- [ ] HTTP MCP는 중앙 배포·OAuth/mTLS 승인 전 비활성 유지
