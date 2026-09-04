# MAXIS / Dify 적용 구현 상태

> 기준 시점: 2026-09-05<br>
> 상태: **LTM 측 연동·통제 프레임워크 구현 및 전체 회귀 완료 — 운영 MAXIS/Dify 구성은 별도 필요**

## 작업 위치

- 앱 worktree: `C:\Users\PC\repo\dongyi-kim\lake-task-manager-deploy\.cache\worktrees\maxis-dify-migration`
- 브랜치: `codex/maxis-dify-migration`
- 기준 커밋: `7799e39 release: add v2026.09.03 notes`
- 재개 checkpoint: `98f8ff7 chore: checkpoint MAXIS Dify migration`
- 원래 checkout과 현재 `main`은 변경하지 않았고 원격 저장소에도 push하지 않았다.

## 현재 범위

현재 LTM은 별도 MAXIS API가 아니라 **MAXIS가 관리하는 Dify Service API**의
`/parameters`와 `/chat-messages`를 호출한다. 전체 LangGraph와 대화 상태는 LTM에 남고,
Dify는 한 번에 하나의 의미 Role을 stateless로 실행한다.

| 영역 | 정확한 상태 |
|---|---|
| Agent | 8개 의미 Role 원격 실행 가능. Research·People·Query Runner·Action Executor는 local |
| Workflow | LTM 오케스트레이션과 Dify Role별 3-App 계약·버전 pin 구현 |
| Knowledge/RAG | 8문서 manifest v2, segment hash, 25-case 명세, dataset/sentinel/eval gate 구현 |
| MCP | OAuth·mTLS·Host·stable Jira identity를 요구하는 read-only HTTP 서버 구현 |
| HITL/쓰기 | Jira actor·승인 payload·일회 capability·감사 digest 결속과 write 직전 재확인 구현 |
| Eval | local 계약 회귀, live canary 계약, Role별 shadow 승격, prod attestation gate 구현 |
| 관측/KPI | 내용 없는 local audit·impact·PMO delta 집계 구현 |
| Release | dev/prod profile, fingerprint, 24시간 probe, kill switch, 변경 시 실행 무효화 구현 |

원격 허용 Role은 Request Architect, Query Specialist, Knowledge Curator, Portfolio Analyst,
Work Architect, Auditor, Result Integrator, Editor Author다. MAXIS Auditor는 의미 검토만 하며
최종 schema·인용·권한·승인·쓰기는 LTM 코드가 통제한다. Registry/build spec의 Research
Analyst와 People Advisor 계약은 향후 검토용이고 현재 runtime은 항상 local로 강제한다.

## 구현 완료

### 프로필·Workflow

- dev/prod의 URL, workflow version, dataset, Knowledge version, API Key를 분리했다.
- Key는 공개 상태와 prefs에서 제거하고 Windows DPAPI 또는 환경변수만 사용한다.
- `local → shadow → maxis` 승격과 즉시 차단 kill switch를 기능별로 제공한다.
- prod는 HTTPS, 원격 로그 정책 metadata, 24시간 이내 probe, 현재 profile fingerprint를
  모두 요구한다. 호출 도중 설정이 바뀌어도 응답을 폐기한다.
- Dify 실패를 local 결과로 조용히 바꾸지 않고 Request Architect 실패 후 다른 조회 경로로
  재해석되는 우회도 차단했다.

### Knowledge/RAG·평가

- `config/maxis-knowledge.yml`에 문서 owner·revision·분류·대상 Role·원문/segment hash,
  검토 버전·UTC 시각·reviewer digest를 고정했다.
- 25개 positive/no-hit/injection/ACL 평가 명세의 SHA-256을 governance에 결속했다.
- Dify 응답의 `retriever_resources`가 현재 dataset UUID인지 검증하고 문서명·본문·내부 ID는
  보존하지 않는다. release sentinel, manifest, workflow/knowledge version도 함께 검증한다.
- prod authoritative route는 현재 dataset·manifest·세 Workflow version에 정확히 결속된
  25/25 외부 평가 attestation과 Role별 shadow gate 없이는 저장할 수 없다.
- 설정 화면에 Knowledge 평가 상태, 기대 binding, metadata-only 검증 한계를 표시했다.

### MCP·승인·감사

- HTTP MCP는 `LTM_MCP_DEPLOYMENT_MODE`와 `JIRA_ENV`를 명시적으로 요구한다. production은
  `JIRA_ENV=prod`와 mTLS 또는 승인 proxy, development는 mock/local loopback만 허용한다.
- Dify에는 Jira/Confluence/Bitbucket 읽기 Tool만 노출한다. display name·email은 OAuth와
  Jira 사용자를 같다고 판단하는 근거로 쓰지 않는다.
- 승인 capability는 제안 당시 fresh Jira actor와 payload hash에 묶고, 승인 재개와 각 Jira
  mutation 직전에 사용자를 다시 확인한다. bulk 중 사용자가 바뀌면 남은 항목을 중단한다.
- 변경 proposal/approved/cancelled/execution을 actor·payload·capability·correlation의
  SHA-256 및 process HMAC으로 결속한다. 지원되는 경로는 검증된 execution receipt digest와
  signature까지 연결하고, 미지원 경로는 `not_issued`로 명시한다.
- control-plane prefs는 임시 파일 fsync, 원자 교체, read-back 검증이 실패하면 성공으로
  응답하지 않는다.

## 실제 검증 결과

| 검증 | 결과 |
|---|---:|
| MAXIS/Knowledge/MCP/HITL 핵심 경로 재검증 | 316 passed |
| 전체 pytest | **3,959 passed, 35 skipped** |
| Dify bundle validator | 3 workflows, 6 Start variables, Knowledge/eval coverage 통과 |
| Python compileall | 통과 |
| 브라우저 QA | DEV/PROD, Knowledge 대기, MAXIS 차단·Shadow 허용, 640px 레이아웃, console 오류 없음 |

전체 테스트의 경고 1건은 기존 `langchain-community` sunset 안내이며 이번 기능 실패가 아니다.

## 운영 구성이 필요한 항목

1. MAXIS/Dify prod Project와 세 Chatflow를 실제로 만들고 승인된 HTTPS endpoint에 게시한다.
2. dev/prod Knowledge dataset과 환경별 sentinel을 업로드하고 실제 UUID·게시 Workflow ID를
   profile에 입력한다.
3. 후보 dataset에서 25-case를 실제 실행하고 결과 artifact를 보관한 뒤 exact-binding
   attestation을 등록한다. 저장소의 25건은 아직 **실행 명세**다.
4. Dify 로그 보존·접근 ACL·마스킹을 관리자 화면에서 대조하고 승인 metadata를 등록한다.
5. 실제 API Key를 Secret 경로에 넣고 `/parameters` 및 합성 `/chat-messages` probe를 수행한다.
6. 제한 사용자 PoC에서 Role별 shadow 표본과 사람이 검토한 업무 품질을 확보한다.

## 후속 구현·잔여 위험

- 현재 Dify 자산은 import 가능한 DSL/export가 아닌 build spec이다. 실제 export/hash와 node
  failure-injection을 release gate에 추가해야 한다.
- 단일 Knowledge dataset의 `allowed_roles`는 실행 ACL이 아니다. 제한 문서 도입 전 Role별
  dataset 또는 서버측 metadata filter와 ACL-negative 원격 검증이 필요하다.
- eval attestation과 remote-log 값은 외부 artifact를 읽거나 서명 검증한 결과가 아니라
  관리자 metadata다. 서명된 증적과 owner별 승인 record가 필요하다.
- HTTP MCP는 아직 중앙 배포하지 않았고 Jira OBO credential 교환도 없다. 다중 사용자 운영은
  사용자별 sidecar 또는 delegated Jira gateway를 먼저 결정해야 한다.
- prod 공유 Dify Key를 PC별 DPAPI에 배포하기보다 OAuth/OBO gateway가 중앙 보관하는 구조와
  exact outbound host allowlist가 필요하다.
- `/myself` 확인과 Jira POST는 provider-atomic하지 않다. 일부 댓글·링크·전이·복합 작업은
  typed receipt 미지원이며 고위험 2인 승인·부분실패 보상도 남아 있다.
- audit·impact는 local 파일이다. 중앙 immutable/WORM audit, 수작업 baseline, 처리량·p95·비용,
  fleet-wide 지표가 필요하다.
- kill switch와 두 profile은 PC별 상태다. 이전 prod export/profile 이력과 전사 kill switch,
  실제 rollback 자동화·복구 훈련은 운영 과제다.

상세 아키텍처·환경변수·승격 절차·시각자료는 `docs/MAXIS_DIFY_MIGRATION.md`를 기준으로 한다.
