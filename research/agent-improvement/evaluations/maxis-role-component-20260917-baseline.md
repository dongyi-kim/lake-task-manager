# MAXIS Role component — 2026-09-17 baseline 직접 진단

## 식별자와 판정 범위

- source: `2851c282d51f77693fd2a846d62a2fece6a11454`, dirty=false.
- protocol `3.0.0` / rubric `2.0.0` / `maxis-role-component` battery `1.0.0`.
- manifest: `967b14832b8eea63aff0c46cf66aefc4288679bcd171e03fa9d152c4a4a8f555`.
- benchmarkKey: `61a1e6be9a6fd29140c5ad7e5a41406f61ad15cebe34cc09fee71cbe2beefcb9`.
- candidateKey: `c5fa03df405cbf01cc4319c6a524fd5e1a3d08da90edbd4f462c8eb2fe8c31b7`.
- comparabilityKey: `f44a80ba7736b06ded46d33d71124329af815c2f92bb03af06ad912b25a857f4`.
- raw: `.cache/agent-evaluation/maxis-roles-20260917T072313Z-c095e091/baseline.json`.
- raw SHA-256: `221b9943ed74255cfcc09824e92978538f14df8e374e17db14e0bb4f9a00135a`.

DEV/Dify 기존 v19, 설정 모델 `gpt-5.4-mini`, 11/11 사례 각각 1회, 고정 순서,
stateless/no-retry/no-fallback. 원래 Conversation/Create/Editor battery와 다른 합성 component다.
실제 Jira·승인 실행·UI를 포함하지 않는다. 다른 버전의 점수·속도와 증감 비교하지 않는다.

Codex 작업 에이전트 1개가 raw `pack/response/context/errorCode`를 직접 읽은 진단을
메인 Codex가 응답·집계와 교차 확인해 기록했다. 비블라인드이며 정확한 evaluator model ID는
이 실행의 도구 계약에서 노출되지 않았다. LTM runtime LLM/production endpoint를 judge로
사용하지 않았다. 아래는 **질적 실패 분류**이고, 전체 rubric checklist 채점은 미실시했으므로
축별·종합 수치 품질 점수와 qualification 판정을 부여하지 않는다.

## 실제 입력과 출력

정상 8개 사례의 fixture 지정 context는 전달됐다. Source·context로 확인되지 않은 원인·담당자·
날짜·효과는 기대값이 아니다. overflow만 `situation/evidence/knowledge_brief/interpretation`을
잃는다. `interpretation`은 개수 한도, 나머지는 pack 예산 손실이다. 이 사례는 구조적 stress이며
실제 typed 업무 흐름을 대표하지 않는다. stale은 1시간 전 부분 근거, missing은 요청/근거가 빈 입력이다.

| Case | 실제 경계/응답 핵심 | 요청 ms / 관측 token | 직접 판단 |
|---|---|---:|---|
| role-request | Chrome·2홉·빈 화면·그래프 표시 요청. `blocked`, `citation_source_unknown`, payload `{}` | 4,973 / 4,808 | Bug 요청 계획 미제공. 잘못된 source ID를 만들었는지 원문 claim이 없어 확정 불가 |
| role-query | 데이터 자산 이력 요청·request_plan·keywords 전달. `needs_input`, `missing_inputs=[role_knowledge]` | 506 / 0 | typed QueryPlan 미제공. 사용자 목표 모호성이 아닌 정책 검색 문제로 표시됨 |
| role-knowledge | DL-9044 배치 주기 2시간→30분 근거 전달. `blocked`, `claim_not_grounded` | 3,265 / 4,102 | 지식 brief 미제공. 생성 claim이 제거되어 사실성 자체 평가 불가 |
| role-work | Chrome·2홉 Bug 요청과 계획/근거 전달. `needs_input`, `role_knowledge` | 593 / 0 | 초안/필수 질문 미제공. Jira metadata 공백보다 정책 검색 단계에서 먼저 막힘 |
| role-auditor | Bug 초안·근거 전달. `needs_input`, `role_knowledge` | 381 / 0 | 증상 누락·부모/필드 검증 등 실제 감사 수행을 입증하지 못함 |
| role-result | 2시간→30분 변경과 brief 전달. `blocked`, `claim_not_grounded` | 2,581 / 3,732 | 사용자에게 줄 이력 답변 미제공 |
| role-portfolio | doneSP=5/totalSP=10, progressPct=50 전달. `needs_input`, `role_knowledge` | 427 / 0 | 50% 설명 미제공. 산식 정확도/PMO fast path 효용을 검증하지 못함 |
| role-editor | target/kind/seed 3개 모두 전달, fresh·partial=false, 3,488B pack. `bad_request`, 응답 없음 | 1,967 / 미관측 | 입력 내용 부족이라고 단정 불가. HTTP 상태/오류 단계가 없어 client와 서버 기원 미확정 |
| role-overflow | 부분/잘림·후반 context 누락. `blocked`, `claim_not_grounded` | 3,984 / 4,579 | 안전 중단과 일치하지만 손실 설명·구체적 다음 단계 없음. 실제 업무 성공률 분모로 쓰지 않음 |
| role-stale | stale·partial 및 원래 시각 유지. `needs_input`, `role_knowledge` | 352 / 0 | 정책 검색에 먼저 막혀 오래된 근거 판단 품질을 분리 검증하지 못함 |
| role-missing | 요청·근거 없음. `blocked`, `source_free_provenance_forbidden`, missing_inputs `[]` | 2,668 / 3,740 | 빈 입력에서 업무 사실을 내보내지 않은 경계는 유지. 무엇이 필요한지 구체적인 질문은 없음 |

`needs_input` 5건은 모두 `knowledge_guard:no_role_knowledge_hit`와 빈 governed retrieval,
관측 token=0을 보였다. 코드의 사전 Knowledge 차단 경로와 일치하지만 native trace가 없으므로
내부 모델 호출 수를 0으로 확정하지 않는다. 이는 원하는 정책 조항 회수·사용을 증명한 결과가 아니다.

나머지 5건은 governed 문서 identity·ACL·ledger가 검증됐으나 최종 업무 payload는 모두 비었다.
`claim_not_grounded`는 코드의 어휘 겹침 검사와 관계되지만, 이 raw에는 차단 이전 claim 전문이 없다.
허구 생성과 검사 오탐의 비율을 계산하거나 구체 실패 원인을 추정해 prompt를 덧붙이지 않는다.

## 자동 판정과 질적 판단의 차이

응답 계약 수락은 10건, `automaticContractPass`는 5건이다. 후자 5건은 전부 `blocked`여서
업무 결과 통과가 아니다. 정책 no-hit 질문 또한 사용자가 제공할 업무 정보에 대한 유용한 질문으로
자동 분류하지 않는다. `ok` 0건을 모든 LTM 기능의 성공률 0%로 확대 해석하지 않는다.

수신 10/11, request wall time 합계 21,697ms, nearest-rank p50 1,967ms/p95 4,973ms.
Token은 10/11만 관측돼 부분 합계 20,961이며 전체 합계는 null이다. native LLM 호출·가격·
사용자 작업/수정 시간은 미관측이다. 차단 응답의 낮은 지연을 성능 향상이라고 부르지 않는다.

## 다음 실험

1. Role별 정책 검색 query/ACL/조항 회수부터 분리 진단한다. guard를 끄거나 `role_knowledge`를
   사용자 업무 질문으로 돌리지 않는다.
2. 인용 ID·claim-source 대응을 실제 배포 graph의 안전한 진단으로 관측하고 source/model/guard
   중 소유자를 확인한다. Knowledge를 현재 Jira 사실로 승격하지 않는다.
3. Editor는 오류 단계·상태를 보존하고 실제 app payload와 graph의 입력 호환성을 진단한다.
   기존 전용 Editor fixture의 성공 결과로 이 실패를 교체하지 않는다.

재평가 시 새 run group을 쓰고 모든 시도를 보존한다. 이번 단일 exploratory component 결과는
배포·authoritative route 개방 근거가 아니다. 전체 업무 배터리/holdout/반복 평가는 별도다.
