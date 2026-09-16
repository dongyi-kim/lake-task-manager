# MAXIS Editor — 근거 선택·문안 작성 분리와 v17/v18 실측

**단위 기능 분리는 구현했지만, 출력 품질 개선은 아직 달성하지 못했다.** 기존 v16을 새로 측정한 뒤 v17, 계약 안내를 보완한 v18을 모두 같은 5-case로 평가했다. 직접 품질은 **3.58 → 2.34 → 2.58/5**, 자동 통과는 **3/5 → 1/5 → 3/5**, 유용한 결과는 **1/5 → 0/5 → 0/5**다. 기존의 유용한 1건도 완성 초안이 아니라 필수 확인 질문이다. v18은 토큰과 지연도 늘어 **운영 활성화하지 않는다**.

별도 브랜치 `codex/maxis-dify-migration`에서 작업했다. DEV Editor만 게시했고 Core/PMO·API key·PROD는 그대로다. LTM 활성 profile은 비어 있고 모든 route는 `local`이다. 이번 범위는 Editor 내부 책임 분리이며 다른 Agent/MCP 기능을 함께 개편하지 않았다.

## 측정 식별자

| 항목 | 새 v16 baseline | v17 | v18 |
|---|---|---|---|
| candidateCommit | `e2272a688f0e97d8139310a8ac8a01d1cc5afbd7` | `dddfc92667bf056a842c9946a8069e0b4c21c03f` | `45cfd10b0ebc7518e1f5bb2d938b3796c3e86207` |
| promptVersion | `ltm-maxis-dify-2026.09.15-editor-units-v16` | `ltm-maxis-dify-2026.09.16-editor-draftspec-v17` | `ltm-maxis-dify-2026.09.16-editor-draftspec-v18` |
| Editor 게시 UUID | `6cdcdff7-c34e-45e4-8f7e-3f5d5f4311f4` | `99b5abfd-eaf0-4c22-894d-f2b82148b023` | `0cf80e5e-ff06-4b43-900c-ea5c4ed91ddc` |
| runGroupId | `maxis-editor-20260915T235858Z-c4864e8e` | `maxis-editor-20260916T003227Z-d84e52ed` | `maxis-editor-20260916T004317Z-54a98b10` |
| runKind / candidateDirty | exploratory / false | exploratory / false | exploratory / false |

공통 `protocolVersion=3.0.0`, `rubricVersion=2.0.0`, `batteryVersion=4.0.0`.
`batteryManifestSha256`: `6a6cc370d7506dbbc285fa71b805553e5079ea07bc9d533afd14a85dd9ce4406`.
`specializedReviewSpecSha256`: `877ceaa039dbe81dc85ad0671fee54065f87179b0cf50cb1c267c2b118dfdb5b`.
`dataManifestSha256`: `d14b02a0c052ef3ae0854259d0fe43025682129578181bde417161ba445ae16d`.
`runtimePackageSetSha256`: `9624da562efdce4b14d340bdd9a68a08cf6ce52bced0e3ee5e482b51ffac9645`.
`evaluationHarnessTreeSha256`: `c2afa056e444152f0332b0f3aa0dfc0d1b1f777252bcf45d8903746642cf1ca0`.

### 구현 구조와 역할 경계

~~~mermaid
flowchart LR
    I[검증된 원본 근거] --> S[Selector LLM: 근거·범위 선택]
    S --> V[Spec Guard: ID·대상 검증]
    V -->|ok| W[본문 또는 댓글 Writer LLM]
    V -->|needs_input / blocked| T[Stop Plan: 작성 생략]
    W --> E[Spec Enforcer: 선택 범위 재검사]
    T --> E
    E --> A[기존 Assembler: 원본으로 HTML·인용 조립]
    A --> G[기존 원격·로컬 Guard]
~~~

| 단위 | 책임 | 맡기지 않는 일 |
|---|---|---|
| Selector | `editor-draft-spec.v1`: 대상·배경·제약·이력·충돌·필수 링크·부족한 정보 선택 | HTML/문안/변경 승인 |
| Spec Guard | 원본 pack과 투영의 일치, ID/대상/상태 검증; writer 입력 축소 | 모델 판단을 사실로 승격, 잘못된 선택 자동 보정 |
| Writer | 허용된 단위로 본문 또는 댓글의 `editor-plan.v2` 작성 | 원본 재검색·선택 범위 확대·Knowledge 재해석 |
| Spec Enforcer | 섹션별 사실/출처 허용값, 필수 링크 정확히 1회, 선택되지 않은 단위 차단 | 의미상 올바른 업무 범위까지 보증 |
| 기존 Assembler + 양쪽 Guard | 원본 EvidencePack으로 HTML/인용 조립·최종 계약 확인 | 근거가 있다는 이유로 최신 상태나 문안 품질을 보증 |

Editor는 **23노드·26엣지**, 설정된 LLM 노드는 selector/본문/댓글/control의 4개다. 정상 작성은 selector+해당 writer 2회다. 유효한 보류 선택은 writer를 생략하도록 구현·로컬 테스트했지만, 이번 실제 두 후보에서 성공한 Stop 경로는 관측하지 못했다. v17 CMP3은 보류 계약 위반으로 Spec Guard에서 실패했고, v18은 5건 모두 writer까지 실행했다. 합성 control과 Knowledge 거절 경로는 기존 구조를 유지했다.

새 canonical Role·Dify 앱·key는 0개다. 원본 pack, Editor Input, Assembler, Knowledge/Contract Guard, 로컬 ResponseGuard는 변경하지 않았다. DEV/PROD endpoint·모델·Knowledge·key·게시 UUID는 기존 환경 binding 구조를 유지한다. [생성기](../../deploy/maxis-dify/editor_graph.py)와 validator가 모든 분기·모델·prompt·schema·원본 pack selector를 검사한다.

v18은 writer에 `allowed_sections`와 `proposal_source_ids_by_section`을 제공한다. 검증기와 같은 순수 함수로 계산해 unit ID와 source ID의 혼동을 줄이도록 했다. 본문 사실에 포함된 자동 링크도 원본에서 계산하여 명시적 링크와 함께 선택 범위·중복을 검사한다. 모델이 선택하지 않은 근거를 서버가 새로 보충하지 않는다.

## 비교 가능성 및 evidence 선택

공통 `benchmarkKey`: `f4f807c9036af03977a78d384783ac6a1d1ba9e4b80430502ee4f7e8e3e84a74`.

| 필드 | v16 | v17 | v18 |
|---|---|---|---|
| candidateKey | `36e71a2a656276afe3812ecb1c7768c345416ae7ec28533feef70c620ab2bd0a` | `c61ec05aad535d391f80d77c80b16338d5f473619de47fc9fd4633c1efd4c575` | `13f6280f9507cc4b9c89fac8a82b6c949bc0e33870d463985e9ac7035fe730d1` |
| comparabilityKey | `8e3f2f56dd3f259c3d878375f034f041e7c0229e451eb8545b455b6104b35e79` | `a2a383ee240677e6dbcd3dfa77ff9dfabb99b19036b8db15cb2123cef901725c` | `73d92b65aa252a7bd7db0717f19837a37f63f3e0985c05aec5553c26a0ed8c9f` |

`selectionPolicy=complete-run-no-substitution`: 각 후보의 5건 전체를 보존했다. v17 실패를 v18 성공이나 내부 plan으로 대체하지 않는다. 새 v16 baseline 3.58/5를 사용하며 이전 보고서의 3.52/5와 섞지 않는다. `aggregation`: case별 5축 동일 가중 평균 후 cap, 전체 case-attempt 산술평균.

세 raw의 `candidateTreeSha256`는 모두 `ffc0a679728c0b429d76208f4620cdfb412761722ae551d9a45124b0eeedc5d6`다. 변경하지 않은 앱 측 평가 tree 해시이므로 전체 배포의 동일성을 뜻하지 않는다. commit·게시 UUID·DSL/semantic graph SHA를 별도로 고정했다. v18 semantic graph SHA는 `339bd6728b318e5b015e49ec97850790310e04f8bde8669eed0f203c6d4d3353`이다.

실행별 원본 SHA·게시·canary·native trace는 [배포 증적](../../deploy/maxis-dify/deployments/dev-editor-draftspec-2026.09.16.yml)에 연결했다. Raw와 항목별 평가 JSON은 ignored `.cache/agent-evaluation/`에 유지하고 비밀·prompt·응답 전문을 배포 YAML에 복제하지 않았다.

## 실행 조건

| 실행 필드 | 값 |
|---|---|
| model / simpleModel / provider | gpt-5.4-mini / not-used / dify |
| temperature / max_tokens | 0.1 / 노드별 4096 |
| providerEndpointIdentitySha256 | `ee68fff6621a9537414a9109539aef2de6442c485b07a6d6271dfa5f1e710af7` |
| structuredOutputBackend / structuredBackendVersion / structuredBackendFallbackPolicy | dify / legacy-internal / forbid |
| repetitions / candidateOrderIndex | 각 1회 / raw에서 baseline 1, 두 후속 candidate 각각 2; 실제 실행 순서 v16→v17→v18 |
| retryPolicy | no-retry-no-fallback-all-attempts-retained |
| cachePolicy | no-client-history-no-local-state |
| processIsolation | separate-process-stateless-fresh-pack-each-case |
| runtimeProfile | dev-inactive-stateless-component-v1 |
| 비용·cache hit | 가격 snapshot/관측값 없음; 0으로 계산하지 않음 |

기존 승인된 DEV 키로 공식 launcher의 network preflight 후 실행했다. 정답·checker·rubric은 원격 모델 입력에 넣지 않았다. v18은 별도 코드/게시 버전의 새 전체 실행이지 v17의 실패 건만 교체한 retry가 아니다.

## 배터리 범위

기존 Editor component 5개를 **변경 없이 재사용**, 후보마다 CMP2/3/6/8/9 전체 실행했다. 원래 58-case full suite, Knowledge 25-case release gate, full compose/UI, 실사용 Jira 변경은 실행하지 않았다. component의 markup 확인은 실제 앱 UI/참조 resolver 전체 검증이 아니다.

v18 native request ID 대조에서 5/5 모두 원본 pack 전달·Editor Input 투영·축소 입력·Enforcer 결과가 로컬 재계산과 같았고, 실제 실행은 selector+해당 writer **각 2회, 총 10회**였다. 입력은 약 7.9KB에서 writer별 **2,497–5,353바이트**로 줄었다. 이는 writer 입력 축소이지 전체 토큰 감소가 아니다.

## 정량 결과

| 지표 | 새 v16 | v17 | v18 |
|---|---:|---:|---:|
| 시도 / 수신 Dify 응답 | 5 / 5 | 5 / 4 | 5 / 5 |
| 자동 통과 | 3/5 | 1/5 | 3/5 |
| ok / needs_input / blocked / 기술 오류 | 4 / 1 / 0 / 0 | 2 / 0 / 2 / 1 | 4 / 0 / 1 / 0 |
| 직접 품질 평균 /5 | 3.58 | 2.34 | 2.58 |
| 유용한 결과: 완성 초안 + 필수 확인 | 0 + 1 | 0 + 0 | 0 + 0 |
| prompt / completion tokens | 8,339 / 3,711 | 전체 미관측 | 17,873 / 4,697 |
| totalTokens | 12,050 | null, 불완전 | 22,570 |
| durationSeconds | 18.0 | 26.6 | 30.7 |
| p50 / p95 | 4.0 / 5.4초 | 5.6 / 8.0초 | 6.7 / 7.8초 |

`percentileMethod=nearest-rank-over-all-case-attempts`. v18의 전체 tokens는 baseline 대비 **87.3% 증가**, 시간은 **70.6% 증가**했다. v17 수신 4건의 부분 token 17,023은 실패한 CMP3을 포함한 전체값이 아니므로 총합 비교에 사용하지 않는다. v17의 CMP3 `calls=0`은 수신 응답 없음이며, native 기록에는 selector LLM 1회 실행이 있다(전체 내부 9회). 기술 오류는 모델이 잘못된 선택 계약을 내어 Dify Code 노드에서 차단된 결과로, 접속 장애와 구분한다.

## 사람 품질 평가 기준

`qualitativeEvaluatorPolicy=codex-or-claude-direct-raw-output-review`; `evaluatorAgentFamily=codex`; `evaluatorAgentModel=current Codex agent; exact runtime model ID not exposed`. 동일 재사용 reviewer 1명이 세 후보의 최종 raw와 fixture를 직접 판독했다. `directRawOutputReview=true`, `ltmLlmUsedAsJudge=false`, `reviewerCount=1`, `blindedReview=false`다. 구현자는 별도이며 정성 판독에는 내부 selector/plan을 최종 응답 대신 사용하지 않았다.

요청·근거·실행·안전·표현 각 20%, 항목별 pass/minor/major/na를 실제 출력에 연결했다. 공통 160+특수 15=후보당175항목. 전pass 상한5, minor1=4.5, minor2+=4, major1=3.5, major2+=3이며 anchor에 따라 더 낮게 평가한다. 평균 후 사실/개체 발명·무승인 변경 cap2, 중요한 누락·응답 모순 cap3을 적용한다. 자동 도구는 완전성·상한·cap·산술만 확인한다.

[v16 직접 평가](evaluations/maxis-editor-20260915T235858Z-baseline.md), [v17 직접 평가](evaluations/maxis-editor-20260916T003227Z-candidate.md), [v18 직접 평가](evaluations/maxis-editor-20260916T004317Z-candidate.md)에 전체 항목별 JSON·축점수·대표 excerpt·cap 근거를 연결했다. 코드 리뷰 점수는 아래 별도 기록이며 LLM 문안 점수가 아니다.

v18 case 점수는 CMP2/3/6/8/9 각각 **2.0/2.0/3.0/3.0/2.9**다. 미확정 p95 방향을 전제한 CMP3에는 사실 발명 cap2, 나머지는 중요한 누락 cap3이 선언됐다. cap이 실제 평균을 낮춘 것은 CMP3/6/8 세 건이다. 공통 항목은 pass45/minor27/major35/na53, 특수는 pass1/minor5/major9/na0. 이 항목 수를 독립 표본의 정확도처럼 해석하지 않는다.

## 배터리·case 특수 검토요소

| case | 필수 검토 |
|---|---|
| CMP2 | 4섹션 보강, 대상 작업과 부모/완료 형제 업무 분리, 임의 목표 금지 |
| CMP3 | exact seed 보존, p95의 높음/낮음·원인 추론 금지, 필수값 확인 |
| CMP6 | 실제 담당 mention·2홉100노드 기준·설계 문서 링크 |
| CMP8 | 부모의 목적/범위·자식 책임 분리, 기능 검증 가능한 DoD |
| CMP9 | 짧은 지시라도 현재 티켓 맥락 유지, 효익 발명/범위 대체 금지 |

모든 case에서 editor_context_preservation과 editor_reference_fidelity도 판정했다. 미실행 항목과 실제 결과 누락을 혼동하지 않았고, 기술 오류/blocked도 원 실행의 결과로 남겼다.

## 배터리별 실제 출력과 평가

| case | v17 → v18 최종 출력과 남은 문제 |
|---|---|
| CMP2 | 양쪽 모두 blocked, 최종 HTML 없음. v18 내부 plan도 work에서 background-only 부모 출처를 사용해 차단됐다. 내부 문안을 성공 초안으로 세지 않는다. |
| CMP3 | v17 bad_request·최종 응답 없음 → v18 seed는 보존. 그러나 “p95가 어느 기준보다 높았는지”로 높음 방향을 전제하고, 미완결 seed 뒤에 배경 나열과 재질문을 붙여 완성/유용한 확인 모두 미달. |
| CMP6 | v17 평문 @담당자 → v18 실제 mention+문서 링크는 생겼으나 @담당자 중복·2홉100노드 기준 누락. |
| CMP8 | v17 blocked → v18 4섹션 작성. 배경은 문서 기록 목적, 완료 조건은 “문서에서 일관되게 드러나는지” 같은 정리 작업에 치우쳐 기능 완료 기준을 대신함. |
| CMP9 | v17 부모/업스트림 범위 확대·기지 조건 재질문 → v18도 ID/status/type 나열, 부모 문서 목적, 실제 연동 검증 대신 범위 정리 DoD가 남음. |

## 자동 checker와 사람 판정 불일치

v17 자동 통과 CMP9, v18 자동 통과 CMP3/8/9 모두 실제 유용성과 달랐다. 형식·섹션·필수 단어 검사로 “검토 없이 붙여 넣을 수 있는 결과”까지 보증할 수 없다. v18 CMP3의 exact seed 유지도 방향 추론과 미완성 문맥을 상쇄하지 않는다. frozen checker/rubric/fixture는 이번 후보에 맞춰 바꾸지 않았다.

## 실패·재시도·제한사항

- 게시 첫 시도는 24자 marked_name으로 거절돼 기존 게시 UUID가 유지됐다. [Dify 1.11.1 공식 API](https://raw.githubusercontent.com/langgenius/dify/1.11.1/api/controllers/console/app/workflow.py)의 20자 제한을 확인해 이름만 17자로 줄인 두 번째 시도에서 같은 초안 게시에 성공했다. 최초 상세 오류 본문은 저장하지 않았으므로 길이 제한 원인은 이 대조에 근거한 추론이다. 실패 receipt도 보존했다.
- v17/v18 canary 각각 3/3은 연결·버전·인용/검색 계약 확인일 뿐 문안 품질 인증이 아니다. 서비스 앱·key 권한과 Core/PMO 게시 ID 유지도 receipt에 확인했다.
- 초기 관련 회귀 1,047통과·DPAPI1제외; 제외 항목은 별도 사용자 환경에서 1통과. 후속 핵심45, 그래프/번들/환경307, 최종 recipient 문구 변경 후 graph+prompt integrity69 통과. 실행 범위가 겹쳐 합산하지 않는다. 전체 앱/UI 테스트를 뜻하지 않는다.
- 독립 code review 4회 점수는 **8→7→9→8/10**. 중복/암시적 링크, legacy 재질문 지시, 검토자 선택 지적을 반영했다. 마지막 수정은 pin/DSL/관련 테스트로 재확인했고 추가 5차 리뷰를 하지 않았다. review-loop는 구조적 결함을 발견했지만 모델의 의미 판단 성공을 보증하지 못했다.
- 생성 DSL의 대규모 기계적 diff와 함께 spec/graph/검증기/테스트를 하나의 동작 가능한 Editor 변경으로 묶었다. Core/PMO·UI·배포 저장소 포인터·main·push/PR/Release는 변경하지 않았다. 실행별 임시 폴더는 정리하고 비교 raw는 보존했다.
- 각 후보 반복1회·순서 고정·동일 non-blind reviewer1명·고정 합성 사례5개다. 반복5회 이상 qualification, 실자료, Knowledge25, full58, UI 검증을 대체하지 않는다. 양쪽 비교는 탐색적이며 단일 원인에 대한 인과 추정도 아니다.

다음 개선의 우선순위는 **노드 추가가 아니라 각 단계가 결정해야 하는 계약을 더 작게 만드는 것**이다. 특히 writer의 출처 ID 재선택 부담과 parent-context의 재사용을 줄이는 계약을 먼저 실험하고, 측정 결과 완성과 검토 요청의 판정을 분리해야 한다. 실제 검토 대상/기준/링크를 typed 슬롯으로 전달하는 방식도 검토한다. 근거를 임의 보충하거나 guard를 느슨하게 해서 통과율을 올리는 방법은 적용하지 않았다.

현재 v18은 DEV의 비활성 실험 후보로만 남긴다. 완성 초안·필수 확인 질문·오류를 분리해 개선을 입증한 다음에만 활성화와 더 넓은 배터리를 논의할 수 있다.
