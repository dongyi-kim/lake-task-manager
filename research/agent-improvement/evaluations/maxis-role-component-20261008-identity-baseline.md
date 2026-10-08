# MAXIS Role 평가 — protocol 4 fresh baseline

## 결론

평가 source identity 누락을 수정한 뒤 현재 DEV를 다시 측정했다. **11건 중 ok 0, blocked 10, Editor 오류 1**이다. 검색·응답 형식 통과를 업무 성공으로 세지 않는다. 이번 변경은 측정 신뢰성 개선이며 모델 품질·속도 개선이 아니다.

## 변경 범위와 로컬 검증

- 단일 목적 브랜치 `codex/maxis-eval-tree-identity`, 실측 clean commit `5abdc71d096ecaca07fabde24f3ecda3039ed033`.
- `candidate_tree_sha256`의 cache 제외를 저장소 상대 경로로 수정. 상위 `.cache/worktrees` 이름 때문에 runtime source가 누락되던 문제를 방지.
- MAXIS Role source ledger에 `retrieval_query.py`를 결속. 실행 중 바뀌면 다음 요청 전 중단.
- 결함 회귀 4건 red→green, protocol 전환 회귀 1건 추가, 관련 고유 테스트 **99 passed**. 독립 코드 검토 2회 **9/10·9/10**; LLM 답변 점수가 아님.
- protocol 3→4 major 전환, rubric·battery·checker·정답·Dify graph·모델은 그대로. [전환 계약](../../../app/agent/EVALUATION.md)에 새 baseline 필요성을 기록했다.

## 측정 식별자

| 항목 | 값 |
|---|---|
| protocol / rubric | 4.0.0 / 2.0.0 |
| battery | maxis-role-component / 1.0.0 |
| batteryManifestSha256 | `967b14832b8eea63aff0c46cf66aefc4288679bcd171e03fa9d152c4a4a8f555` |
| specializedReviewSpecSha256 | `aa706802c1770993d2fc10e3cdf2e6edd8a9ca0dd59329080db867db2c98ceff` |
| dataManifestSha256 | `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d` |
| benchmarkKey | `fa36911d7c13f531f85235684b78a21ae261994f79dc2e2f9ef3d469e2ee39b2` |
| candidateKey | `200838eabfad7bb371efa91ecd2e97e068b0ef147bcc7c8bae4ece66e6947383` |
| comparabilityKey | `a530e603a808d5849403c08faaaef09c6f746a040d2ded4aa6a6818541be10c9` |
| candidateTreeSha256 | `4d047bdcba7178080cf18b78785db0318a99e5405b2b7e3f557a59dcd16b894a` |
| package set | `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645` |
| harness tree | `6042ddf7e707233f92f9794d72e39b3a68bcd675f226e50bf8c0935d64755727` |
| promptVersion | `ltm-maxis-dify-2026.09.21-policy-query-v20` |
| configured model / provider | gpt-5.4-mini / dify; simple model 미사용 |
| provider endpoint identity | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| profile fingerprint | `66921f4215ab18e68a1a47831e7b5d37` |

## 비교 가능성 및 증거 선택

`maxis-roles-20261008T021642Z-cf6afbbb`, exploratory 1회, repeat 1, 고정 순서, 전체 11/11건·누락 0건. Primary는 한 full run이며 focused 성공으로 실패를 교체하지 않았다. `bindingUnchanged=true`, 실행 전 clean commit이다.

[9월 21일 결과](maxis-role-component-20260921-policy-query.md)는 protocol 3이며 tree 누락 제약이 있다. 같은 사례·게시 버전이지만 **benchmarkKey가 다르므로 우열·절감률을 계산하지 않는다**. 이후 후보를 이 protocol 4 baseline과 같은 배터리로 새롭게 비교한다. 공식 qualification은 후보별 최소 5회, 균형 순서 및 별도의 업무/UI 검증이 필요하다.

## 실행 조건과 연결 확인

- 2026-10-08 UTC, 기존 launcher의 credential-free DEV preflight와 정상 3-app canary **3/3** 후 실행.
- DEV `synthetic_only`, 저장소 합성 fixture만 전송. 내부 용어집·운영 Jira 데이터는 전송하지 않음.
- 별도 process, case별 새 pack, 로컬 대화 이력/업무 state 없음. retry/fallback 없음.
- 모델은 게시 binding의 설정값이며 이번에 native 노드별 실사용 모델을 별도 조회하지 않았다.
- Core `290173d5-f950-46d0-869e-7c3b0885049c`, Editor `0b618d12-4247-45c3-8d5b-1790804621d4`, PMO `8bf05f64-8c39-49af-a781-a563ef0e4b27`.
- active profile은 빈 값, route는 모두 local, canary에서 PROD 설정 불변 확인. Workflow 재게시·키 생성·운영 활성화 없음.

## 정량 결과

| 항목 | 결과 |
|---|---:|
| 시도 / 유효 응답 수신 | 11 / 10 |
| ok / blocked / needs_input / error | 0 / 10 / 0 / 1 |
| 자동 응답 계약 통과 | 10/11 |
| 검색 ledger·역할 ACL 통과 | 10/11 |
| gateway wall 합계 | 34.462초 |
| nearest-rank p50 / p95 | 2.550초 / 6.847초 |
| 10건의 관측 token 부분 합계 | 40,589 |
| 전체 token / 비용 / native LLM 호출 수 | 미관측 |

시간은 모든 11 attempt의 client 입력 검증 포함 gateway 왕복 경계이며 준비·응답 검증·종료 시간을 제외한다. `metrics.calls=10`은 **응답 수신 건수**이지 LLM 호출 수가 아니다. canonical metrics의 전체 token/cost는 null 그대로 보존한다. token 부분 합계는 전체 비용 비교에 사용하지 않는다. Core의 다수 응답이 같은 prompt_tokens=3303을 보고하므로 native 계측 정확성도 확정하지 않는다.

## 실제 출력·사례별 직접 진단

Codex 메인 에이전트가 실제 전송 query·sources·role_context·freshness와 수신 response 전체를 직접 대조했다. 평가 모델의 정확한 ID는 제공되지 않아 미기록, 단일·비블라인드 진단이다. LTM runtime LLM이나 LLM-as-judge는 사용하지 않았다. rubric 2.0.0 공통 5축과 specialized checklist의 완전한 채점은 수행하지 않았으므로 **공식 품질 점수는 부여하지 않는다**.

모든 수신 응답의 실질 내용은 `payload={}`, `claims=[]`, `citations=[]`, `payload_claims=[]`, `missing_inputs=[]`다. 아래는 해당 빈 출력과 오류 이유를 입력 요구에 대조한 질적 진단이다.

| Case | 실제 상태·출력 사유 | ms | 관측 token | 입력 대비 판단 |
|---|---|---:|---:|---|
| role-request | blocked: contract_guard:citation_source_unknown | 4776 | 4951 | 원문 재현 조건을 담은 입력은 전달됐으나 사용할 계획이 없음. 출처 식별 결속 실패. |
| role-query | blocked: contract_guard:payload_type_mismatch | 2610 | 3813 | 대상 자산의 이력 조회 요청은 전달됐으나 typed QueryPlan이 없음. |
| role-knowledge | blocked: contract_guard:claim_not_grounded | 3739 | 4301 | 원문에 2시간→30분 변경이 있지만 재사용할 brief가 없음. Guard 전 문장이 없어 환각/과잉 차단 원인은 미확정. |
| role-work | blocked: contract_guard:ok_has_missing_inputs | 6847 | 5543 | 재현·실제·기대와 미확정 실행 조건을 받았으나 초안/추가질문 상태가 모순됨. |
| role-auditor | blocked: warnings 없음 | 2103 | 3488 | 초안·근거가 전달됐지만 차단 이유·부족한 검증·복구 방법을 반환하지 않음. |
| role-result | blocked: contract_guard:claim_not_grounded | 2550 | 3748 | 한 건의 이력 source를 받았으나 사용자용 이력 설명이 없음. |
| role-portfolio | blocked: contract_guard:claim_not_grounded | 3985 | 3683 | source의 5/10과 role_context의 50%가 전달됐으나 해석 결과가 없음. |
| role-editor | error: bad_request | 1541 | 미관측 | 30분을 포함한 seed와 description 요청을 전송했지만 응답 계약을 받지 못함. native 실패 단계는 이번에 확인하지 않음. |
| role-overflow | blocked: contract_guard:ok_has_missing_inputs | 2341 | 3896 | partial=true와 누락 표시는 입력에 유지됨. 유효한 제한 설명이나 추가질문은 반환되지 않음. |
| role-stale | blocked: warnings 없음 | 1781 | 3488 | stale/partial 전달은 유지됐지만 현재 확정 불가의 이유·재조회 안내 없음. |
| role-missing | blocked: contract_guard:source_free_provenance_forbidden | 2189 | 3678 | sources/facts/metrics/role_context가 빈 입력. 확인 질문 대신 source-free provenance 위반으로 차단됨. |

## 자동 checker와 사람 판정의 차이

자동 계약 10/11 통과는 허용된 blocked envelope를 받았다는 뜻이다. 사용 가능한 업무 답변·초안 0건, 구체적인 필수 확인 질문도 0건으로 직접 관찰했다. 이는 raw의 `usableCompletions=null`을 자동 점수로 바꾼 것이 아니라 빈 출력에 대한 직접 진단이다. Guard가 제거한 모델 원문은 primary에 없으므로 차단 코드만으로 환각과 과잉 차단을 확정하지 않는다. checker·기존 정답은 바꾸지 않았다.

## 원본과 실패 보존

- Primary: `.cache/agent-evaluation/maxis-roles-20261008T021642Z-cf6afbbb/baseline.json`.
- SHA-256: `5071955bb7e57e8a20623a96b64559a51ae2c3787b386d59be10b22b97afebc3`.
- Canary: `.cache/agent-evaluation/maxis-role-preflight-20261008T021613Z-4f39d853/canary.json`.
- SHA-256: `cc731fb4a501e971687c4aab3d5f846944d837d61ed3504733293c2d3896e8e4`.
- Editor bad_request 1건 보존. 재시도·누락·transport 중단 없음. native 실패 단계 추가 조사 미실행.
- 전체 앱/UI·실 Jira·58-case 종합·25-case Knowledge·5회 qualification 미실행. latency는 단일 탐색값이다.

## 다음 개선 단위

1. 빈 근거 사전 종료: 현재 missing은 3,678 tokens·2.189초 후에도 질문이 없다. 기존 엄격한 source-free control 예외를 보존하고 입력 오류와 정상 needs_input을 분리해야 한다. 0-LLM 경로의 retrieval metadata가 로컬 경계를 통과하는지 실제 검증하기 전에는 적용하지 않는다.
2. 기존 Editor 범위 후보를 최신 v20 기반 별도 브랜치에서 적용·재생성한 뒤 같은 배터리로 평가. 구버전 DSL을 덮어씌우지 않는다.
3. Knowledge Curator를 근거 ID 선택 → 원문·인용의 결정적 조립 → 기존 Guard로 분해. 근거 검사 완화나 inference 재분류로 성공률을 만들지 않는다.
