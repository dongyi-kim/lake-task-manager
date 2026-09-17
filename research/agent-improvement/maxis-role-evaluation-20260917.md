# MAXIS 역할별 실제 응답 평가 기반

## 구현 범위

`codex/maxis-role-evaluation`은 `codex/maxis-performance-baseline`의 `457f0c4`에서 분리했다.
전체 성능 개선 계획 중 **오프라인 입력 진단 → 실제 원격 응답 관측**을 연결하는 변경이다.
앱 동작, prompt, 모델, Knowledge, 게시 Workflow, 활성 route, PROD 설정을 바꾸지 않는다.

역할을 더 만들지 않고 다음 작은 단위로 분리한다.

| 단위 | 책임 |
|---|---|
| `maxis_role_remote_cases` | 고정 합성 입력·출력 기대 계약·직접 검토 기준 |
| 기존 `role_payload` / ContextGuard | 실제 허용 필드·크기·출처·신선도 검증 |
| `launch_role_evals` | 자격 증명 없는 DEV 접속 점검과 자식 프로세스 허용 |
| `run_role_evals` | 배포 결속, 단일 요청, 응답/Knowledge 검증, 시도별 저장 |
| 기존 performance metrics | 실패·미관측을 보존하는 전체/역할별 집계 |

8개 원격 Role의 정상 사례와 overflow/stale/missing을 합친 11개 고정 사례다.
실제 app state, 임의 텍스트/파일/프롬프트를 전달하는 입력 옵션은 없다.
현재 UTC 관측 시각과 배포 manifest를 **attestation 생성 전**에 넣고 실제 Guard를 거친다.
기존 오프라인 11개 결과는 refactor 전후 바이트 동일성을 검사한다.

## 측정 식별자와 비교 가능성

- protocol `3.0.0`, rubric `2.0.0`, `maxis-role-component` battery `1.0.0`.
- fixture `ltm-maxis-role-remote.v1`; 시나리오 참조는 대화/Create/Editor 기존 suite다.
- 원래 전체 battery, 기존 Editor 5건, 오프라인 입력 진단과 입력/평가기준이 같지 않다.
  이들 사이의 점수 증감이나 속도 향상률을 계산하지 않는다.
- benchmark/candidate/comparability key, source hash, commit/dirty, 배포 fingerprint,
  published Workflow ID, Knowledge manifest, 실제 전송 pack hash를 raw에 남긴다.
- 1회 탐색이고 최종 qualification이 아니다. `releaseEligible=false`이며 성능 개선의 증명도 아니다.

## 실행 조건과 보안 경계

`python -B deploy/maxis-dify/launch_role_evals.py --label baseline`을 승인된 네트워크 프로세스에서 실행한다.
기존 승인된 DEV 키만 사용한다. label은 기록 이름이며 서버 버전을 전환하지 않는다.
배포 설정에서 모델 이름을 기록하지만 실제 native node 호출 수·비용은 추정하지 않는다.

launcher가 허용 목적지에 자격 증명 없는 접속 점검을 먼저 수행한다. runner는 전달 표식과 목적지
해시를 검증한 뒤 inactive/synthetic-only DEV, all-local routes, 배포 원장과 환경 분리를 검사한다.
환경 분리 검사에는 암호화 키 저장소 접근이 필요할 수 있다. 모든 선택 입력을 Guard로 검증한 후
요청별 transport 키를 얻는다. PROD나 사용자 live Jira 데이터는 이 경로의 대상이 아니다.

최대 11요청, 사례당 1회, stateless, 재시도·fallback 없음. 인증·통신·제한 오류는 이후 요청을 중단한다.
중단 시 완료/미시도/in-flight 상태를 분리하여 남긴다. `--cases`로 미완료 ID만 새 raw group에서
실행할 수 있지만 원래 run을 덮거나 실패를 대체하지 않는다. 배포·소스 변경 시 추가 전송을 중단한다.

raw는 `.cache/agent-evaluation/<새 그룹>/`의 예약된 새 파일에만 저장한다.
응답의 sentinel/credential/권한 정보/추론 trace는 제거하고 실제 합성 출력은 직접 검토용으로 보존한다.
응답 schema·근거·배포 결속과 Knowledge 문서 identity·ACL·ledger를 독립 항목으로 기록한다.
`latencyMs`는 gateway 요청 검증/전송/응답 수신까지의 wall time이다. 입력 조립·키 조회·client 생성은
`preparationMs`, 응답 검증/close까지 포함한 전체 시도는 `attemptElapsedMs`로 구분한다.
모델 자체 실행 시간이나 TTFT로 해석하지 않는다. 요청 전 실패의 요청 지연은 null이다.
관측된 모든 요청 지연을 집계하고, token 누락은 null이며 부분 합계는 별도다.
`calls`는 수신 Dify 응답 수이지 native LLM 호출 수가 아니다.

## 배터리와 직접 품질 검토

Request, Query, Knowledge, Work(create), Auditor, Result, Portfolio, Editor 각 1개와
Result overflow/missing, Auditor stale이 대상이다. Work 수정/댓글/mixed, 전체 앱 조합, UI,
실제 Jira 대비 사용자 완료/수정 시간은 포함하지 않는다. overflow는 실제 state shape를 대표하지 않는
구조적 stress이고, 이를 개선해도 곧바로 의미 품질 개선으로 해석하지 않는다.

자동 계약 수락은 유용성 점수가 아니다. Codex/Claude가 실제 전송 `pack`과 `response`를 직접 읽고
rubric과 case별 특수 항목을 확인해야 한다. LTM runtime LLM이나 production endpoint를 judge로
사용하지 않는다. 수치 품질 점수는 전체 checklist 판정이 있을 때만 기록한다.

## 후속 변경 경계

1. 실제 응답 기준선에서 실패 Role/원인을 분류한다. 전체 app/e2e 기준선은 별도 구현한다.
2. **별도 runtime branch:** `_role_context` 문자 수와 Guard UTF-8 byte 한도의 불일치 수정.
   한글 1,500자는 3,900자 검사에 통과하지만 4,000B 필드 한도를 넘는 사례가 확인됐다.
   byte budget을 늘리지 않고 typed omission/partial로 처리하며 최종 Guard는 유지한다.
3. 의미상 입력 손실 감소는 Work의 후속 초안 수정 등 실제 typed state 사례를 먼저 추가한 뒤
   입력 선택/축약/충족 검사를 분리한다. 순서 재정렬만으로 전체 개선을 주장하지 않는다.

## 로컬 검증

- 최종 관련 pytest **199개 통과**(runner 50개 포함). 신규 remote fixture/runner와 기존 오프라인 진단,
  ContextGuard, DEV profile gate, prompt integrity, Editor runner 회귀를 포함한다.
- 독립 검토 1차 **6/10**: Role ID를 Workflow key로 잘못 전달한 키 조회와, 형식 오류 응답의
  관측 token이 누락되는 문제를 발견했다. 둘 다 수정하고 실제 key validator를 사용하는
  11-case 검증, 잘못된 event/answer에서 usage 보존 회귀를 추가했다.
- 독립 검토 2차 **8/10**, 추가 차단 결함 없음. 지적된 하네스 준비 비용/요청 지연 혼합을
  위 세 시간 경계로 분리하고 가짜 시계로 성공·실패 모두 검증했다. 코드 검토 점수이지 모델 품질 점수가 아니다.
- 시간 경계 수정에 대한 추가 검토 **9/10**, 남은 actionable concern 없음.
- 네트워크 없는 로컬 배포 사전검사에서는 sandbox의 Windows DPAPI 복호화가 실패했다.
  이 시도는 원격 요청을 보내지 않았으며 모델 품질 실패로 세지 않는다. 실제 평가는 처음부터
  승인된 네트워크/사용자 권한의 launcher 프로세스로 실행해야 한다.

## 실제 DEV 실행 결과

소스 commit **`2851c282d51f77693fd2a846d62a2fece6a11454`**, `candidateDirty=false`에서 실행했다.
배포 bundle은 기존 v19, profile fingerprint `a32fd440f030354eaf4aa947c3a07dc2`, 설정 모델은
`gpt-5.4-mini`다. 실제 native node별 호출/지연/가격은 측정하지 않았다.

첫 launcher 시도는 `dev_profile_not_verified`로 **사례 0건 전송** 상태에서 종료됐다.
기존 검증 시각은 `2026-09-16T01:21:56.593202Z`, TTL 86,400초가 만료된 것이 확인됐다.
이를 우회하지 않고, 자격 증명 없는 접속 preflight 후 기존 3개 앱의 고정 합성 canary를
다시 수행했다. **3/3 통과**해 검증 기록만 갱신했다. 활성 profile은 빈 값, 모든 route는 local,
PROD는 불변이었다. 그 뒤 미실행 11개 사례를 1회 평가했다. 재시도·후보 선택·원본 대체는 없다.

| 관측 | 결과 |
|---|---|
| 전체/선택/시도/미시도 | 11 / 11 / 11 / 0 |
| 정상 수신 응답 / 계약 검사 수락 | 10 / 10 |
| `ok` / `blocked` / `needs_input` / 오류 | 0 / 5 / 5 / 1 |
| Knowledge identity·ACL·ledger 수락 | 5/11 |
| 요청 지연 합계 / p50 / p95 | 21,697 / 1,967 / 4,973 ms |
| 관측 token | 10/11; 부분 합계 20,961 (prompt 16,515 + completion 4,446) |
| 전체 token / native 호출 / 비용 | 모두 미관측 `null` |
| source/profile 실행 전후 결속 | 불변 |
| 품질/운영 승격 | 불가 |

`complete=true`는 선택한 시도의 기록 완료다. 업무 성공이나 품질 통과가 아니다.
`automaticContractPass=true`인 blocked 5건도 빈 payload여서 정상 업무 출력으로 세지 않는다.
다수 응답이 작성 전에 막혔으므로 낮은 지연을 과거 Editor보다 빨라진 성능으로 비교하지 않는다.

### 직접 검토 및 다음 우선순위

Codex가 실제 전송 pack과 응답 11건을 직접 검토했다. LTM LLM judge는 사용하지 않았고,
단일 비블라인드 검토이며 수치 품질 점수는 부여하지 않는다.
case별 증거·제약은 [직접 검토 기록](evaluations/maxis-role-component-20260917-baseline.md)에 보관한다.

1. Query/Work/Auditor/Portfolio/stale의 `no_role_knowledge_hit`와
   `missing_inputs=[role_knowledge]`를 우선 분석한다. 업무 사실 부족과 정책 검색 실패를 구분하고,
   역할별 검색 입력·ACL·검색 조항을 검증한다. 필요 없는 재질문이나 guard 해제로 해결하지 않는다.
2. Request의 `citation_source_unknown`, Knowledge/Result/overflow의 `claim_not_grounded`,
   missing의 `source_free_provenance_forbidden`를 분리한다. 현재 raw는 차단된 원래 claim을
   포함하지 않아 모델의 허구 생성과 보수적인 검사 오탐 중 어느 것인지 확정할 수 없다.
3. Editor `bad_request`는 현재 기록만으로 HTTP 400/422 또는 client validation의 어느
   지점인지 확정할 수 없다. 비밀 없는 오류 단계/상태 관측과 실제 app payload→게시 graph의
   호환성 진단이 다음 작업이다. 기존 Editor 전용 축소 fixture 성공으로 대체하지 않는다.
4. 별도 `codex/maxis-context-utf8`의 **`90a99bd`**에서 한글/이모지 입력 크기 불일치를 수정했다.
   확대 회귀 174개, 독립 검토 2회 모두 9/10이다. 이번 remote baseline은 수정 전 소스이며,
   이 수정이 위 11건의 품질을 개선했다고 주장하지 않는다. 두 변경은 공통 기준선에서 분리한
   형제 branch다. 병합·배포하지 않았다.

### 불변 증적

아래 경로는 이 worktree의 `.cache/agent-evaluation/` 기준이며 원본은 덮어쓰지 않는다.

| 파일 | SHA-256 |
|---|---|
| `maxis-role-preflight-20260917T072214Z-b3ad6f6c/canary.json` | `68a9e0541ab472e51c3464b2b0b977003c9a83f42eb4dd4c71636cc7342adca6` |
| `maxis-roles-20260917T072313Z-c095e091/baseline.json` | `221b9943ed74255cfcc09824e92978538f14df8e374e17db14e0bb4f9a00135a` |

전체 app 58건, Knowledge 25건, UI, holdout, 5회 반복 qualification, 수동 Jira 대비 작업/수정 시간,
전체 계획의 모델/구조 A/B 및 운영 전환은 아직 하지 않았다.
