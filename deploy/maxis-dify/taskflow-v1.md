# Research → Task draft: TaskFlow v1 기반 계약

상태: **로컬 계약·조정기·Workflow API transport와 session preview/조회 연결 구현**.
`workflow/session.py`의 내부 `taskflow_preview()`가 실제 QueryRunner를 호출하지만 기본
runtime composition은 비어 있다. HTTP/UI 실행 진입점, 기본 route, 승인/쓰기 경로는 변경하지 않는다.
서버의 불변 릴리스·Knowledge ACL/보존·중첩 예산 verifier 구현 전 사용자 실행을 활성화하지 않는다.

## 소유권과 경계

| 소유자 | 책임 | 이번 구현 |
|---|---|---|
| LTM | 현재 권한·scope, snapshot, 추가 조회, 취소, 결과 검증, 승인·Jira 쓰기 | coordinator + session preview 수명 + 실제 QueryRunner 조회; 승인 채택은 후속 |
| Dify | 요청 분류 → bounded research → 근거 정리 → WorkDraft 생성 | 합성 조회 3개 실행 성공, Research 기능 검증 실패; 부모 WorkDraft 미배포 |
| MAXIS | private Research wrapper 등록·서비스 제공 | 등록 미실행; Dify의 합성 Knowledge와 구분 |

목표 호출 관계는 LTM → 부모 TaskFlow → private MAXIS Agent wrapper → 고정된 research-only
workflow다. wrapper에 별도 모델·대화 상태를 만들지 않고 재귀 호출을 허용하지 않는다.
Research Agent의 tool은 `glossary_lookup`, `domain_retrieve`, `evidence_lookup` 세 가지로 제한한다.
도구 세 개는 고정 합성 입력으로만 배포·검증했다. 실제 업무 snapshot과 연결된 목록이 아니며
전체 호출 관계는 여전히 **후속 서버 구현 계약**이다.
LTM의 로컬 PC를 공개하거나 Jira 세션·credential을 원격 모델에 넘기지 않는다.

합성 자산·실패·남은 승인 범위: [2026-10-17 실서버 점검](../../research/agent-improvement/evaluations/2026-10-17-maxis-native-synthetic-capability.md).

## 실행 계약

`app/agent/maxis/taskflow_contracts.py`의 `TaskFlowRequestV1`은 기존 `EvidencePackV1`과
request ID·revision·근거 digest·profile·부모/research/Knowledge 릴리스를 결속한다.
원격 응답은 다음 중 하나다.

- `completed`: 기존 `RemoteRoleResultV1(work_architect)`와 완전한 `WorkDraftV1`.
  item 없는 초안이나 필수 질문이 남은 초안을 완료로 표시할 수 없다. 기존 ResponseGuard를
  다시 통과하며, source가 stale/partial/truncated이면 완료를 차단한다.
- `needs_evidence`: Jira/Confluence/comments만 요청할 수 있는 `CompactQueryPlanV1`.
  LTM이 현재 권한과 scope를 다시 확인하고 실제 조회·pagination을 담당한다.
- `needs_input`: 이유가 있는 필수 질문 최대 3개. 새 답변은 fresh snapshot을 가진 새 request다.
- `blocked`: 제한된 reason code만 반환. recovery에 실행 가능한 초안이나 자유 원문을 넣지 않는다.

추가 조회마다 revision을 올리고 같은 pin과 총예산을 유지한다. 최대 추가 조회 2회,
모델 8회·논리 tool 12회·60초의 공통 ledger를 사용한다. **model/tool reservation은 trusted adapter가
실행 전에 기록해야 한다. 로컬 deadline은 늦은 결과를 거부할 뿐 이미 시작한 서버 실행을
종료하거나 중첩 호출을 제한하지 않는다.** 서버 측 enforcement 검증 없이 활성화하지 않는다.

동일 ID·동일 입력의 동시 요청은 재호출하지 않는다. 완료 결과 재사용에도 현재 권한과 릴리스를
확인한다. 취소·권한 철회 시 저장된 근거와 결과를 버리고, 콘텐츠 없는 ID/hash/principal
tombstone으로 재실행을 막는다. 30분 만료는 접근/`prune()` 때 처리한다. 자동 삭제 타이머가
아니므로 host 연결 시 idle sweep이 필요하다. in-flight adapter의 임시 참조는 호출 종료 전까지
남을 수 있다. 프로세스당 콘텐츠 128건, 기억하는 요청 ID 4,096건에서 새 요청을 차단하며
프로세스 재시작 시 상태가 사라진다. 영속 작업 큐나 서버 보존 서비스가 아니다.

## Trusted adapter가 구현해야 하는 것

`TaskFlowPorts`는 내부 Python 주입 경계이며 HTTP 입력이나 모델 출력으로 선택할 수 없다.

1. `authorize`: dispatch/read/display마다 현재 사용자 권한·scope·자료 철회를 확인한다.
2. `verify_release`: parent와 모든 nested release가 실제 불변 배포인지, Knowledge의 현재
   revision·ACL·보존 통제와 총예산 enforcement가 유효한지 검증한다. 단순 checksum echo나
   운영자 체크박스는 이 검증을 대신할 수 없다.
3. `dispatch`: stateless native 실행, bounded HTTP timeout과 중첩 예산 예약·사용량 계측.
   원격 사후 응답의 call count는 사전 enforcement의 증거가 아니다.
4. `read`: 기존 Query Runner의 scope 및 exhaustive pagination 재사용, source text의
   비신뢰 처리, 개인정보/secret 검사, 새 EvidencePack 구성. query plan은 권한이 아니다.
5. `validate_draft`: 기존 release sentinel/Knowledge proof 검증, 현재 Jira metadata·계층·
   요청 충족 검사. 통과한 draft에도 approval을 발급하거나 Jira write를 실행하지 않는다.

실제 adapter 구현 후 바깥 진입점은 기존 `workflow/session.py`로 유지한다. 새 독립 HTTP
진입점이나 임의 adapter 등록 API를 만들지 않는다. 기존 `Action Executor`만 exact payload
승인 이후 쓴다. 이 단계가 연결되기 전에는 새 coordinator를 일반 실행 기능으로 광고하지 않는다.

## 검증과 배포 조건

### 로컬 session preview 연결

`TaskFlowPreviewHost`는 기존 ask/stream 새 턴에서 fresh Jira actor와 실제 제출 문장의
digest를 결속한다. 기존 checkpoint를 뒤늦게 채택하거나, 다른 actor·thread·문장·revision으로
재사용하지 않는다. 초기 근거는 그 턴의 사용자 의도만 보내며 과거 checkpoint 조회 내용은
재수출하지 않는다. 추가 근거는 `LocalTaskFlowReader`가 기존 compact compiler → QueryRunner →
조회 HMAC/provenance → ContextGuard/DLP 경로로 다시 획득한다. source별 독립 실행으로
묵시적 다중-source 추가 조회를 막고 기존 scope·pagination·불완전 범위 표시를 유지한다.

기존 stop/새 턴은 preview를 취소하고 snapshot은 내용 없는 상태만 보여 준다.
새 턴 취소는 ask/stream이 turn lock을 기다리기 전에 수행한다. 이미 진행 중인 원격 호출은
강제 종료하지 못하지만 그 응답으로 추가 조회·재호출을 진행하지 않는다. checkpoint 변경은
기존 turn lock 안에서 직렬화하며, 이후 실제 준비된 턴만 새 actor/문장 결속을 갖는다.
입장 시 발급한 process-local 단조 증가 admission token도 검증한다. 더 새 요청 또는 stop이
도착한 뒤 늦게 끝난 이전 턴 준비는 새 eligibility를 만들거나 새 admission을 취소할 수 없다.
이 내부 preview는 draft/pending/approval token을 만들거나 graph checkpoint를 수정하지 않는다.
서버 verifier 구현은 별도이며 `enabled=true` 같은 설정으로 통과할 수 없다. DEV의 고정
합성 데이터 정책은 유지하고 로컬 업무 snapshot preview는 허용하지 않는다. 만료 정리는
session 접근 때 수행하며 idle sweep, 실제 UI 진입·승인 채택·실서버 검증은 아직 후속이다.

`tests/agent/integration/test_maxis_taskflow.py`는 실제 계약·ResponseGuard와 fake trusted adapter를
조합한다. 실제 Dify/native Agent, 네트워크 중단, 사용자 UI, 실제 품질을 검증한 결과가 아니다.
`test_maxis_taskflow_session.py`는 실제 session/QueryRunner/ContextGuard와 in-process jira820을
연결하고 외부 authority/release/remote 실행만 test double로 대체한다.
실서버 확인·synthetic DEV 배포·canary → 20 positive/10 edge 독립 사례 → 기존 배터리 회귀 →
동일 조건의 5회 균형 비교 순으로 진행한다. 자동 계약 성공과 실제 사용 가능한 초안의 비율을
분리하며, 기대 source recall·직접 품질·지연·tokens·비용의 미관측값을 0으로 바꾸지 않는다.

## Workflow API transport

`TaskFlowClient`는 기존 DifyClient의 destination/TLS/size/timeout 처리를 재사용하며
`POST /v1/workflows/{published_workflow_uuid}/run`을 호출한다. `taskflow_request` 하나에
정규화한 요청 JSON을 보내고 `data.outputs.result_json`에서 결과를 읽는다. 이 경로는
기존 Chatflow role registry와 분리되어 있으며, 일반 `/workflows/run` body에
`workflow_id`를 넣는 것으로 버전이 고정된다고 가정하지 않는다.

native workflow/run UUID와 성공 상태, result의 request/revision/pin/evidence digest를
각각 대조한다. 요청은 16KiB, 응답은 64KiB 이하다. native tokens/elapsed만 관측하고
비용·중첩 모델 호출·품질은 미확인으로 남긴다. 계약 통과 이후 실제 ResponseGuard와
권한 검사를 받기 전 telemetry result_status는 `not_evaluated`다.

HTTP timeout은 남은 시간으로 줄이지만 HTTPX의 phase별 timeout이다. 늦은 결과를
거부하는 것과 서버 실행의 즉시 취소는 다르다. pin echo도 상관관계 확인이지 서버
graph hash·Knowledge ACL·중첩 예산 검증의 대체물이 아니다.

인증 정정(2026-10-17): 이전 probe는 비밀번호를 raw 문자열로 보냈다. 현재 Dify의
UTF-8→Base64 로그인 형식에 맞춘 요청은 기존 승인 자격증명으로 **HTTP 200**이다.
기존 Core/Editor/PMO Service API `/parameters`도 모두 200/Start 계약 일치다.
인증은 더 이상 확인된 차단 사유가 아니다. 위 adapter·서버 통제·품질 조건은 여전히 별도다.
