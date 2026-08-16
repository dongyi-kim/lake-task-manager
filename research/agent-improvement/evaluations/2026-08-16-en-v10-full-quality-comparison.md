# English Agent v10 — 전체 품질·효율 개선 비교

> 결론: 53-case 실제 OpenAI API 전체 실행은 자동 계약 **50/53**, 실패한 3건만 구조적으로 수정해 선택 재실행한
> closure composite는 **53/53**. Codex가 최종 raw output을 직접 읽어 매긴 사람 품질은 **4.56/5**.
> 53-case 확장 기준선 4.20, EN v8 4.30보다 방향성은 개선됐고, v8 대비 closure composite의 시간은
> **17.84%**, total token은 **9.15%**, 비용은 **11.15%** 감소. 다만 battery/checker version이 달라졌고
> 1회 exploratory run에 focused 결과를 합성했으므로 통계적 qualification이나 완전한 5점 품질을 뜻하지 않음.

## 변경 내용

- case별 문구 보정 전에 `identity`·`common`·role 경계·최종 산출물 계약을 먼저 재검토
- 최신 요청 우선, 취소된 action 제거, passive memory turn 단축, write 전 필수 인터뷰를 공통 계약으로 승격
- 회의록의 사람 표기·호칭·결정·담당·기한을 canonical identity와 실행 payload로 연결하는 공통 meeting context 계층 도입
- Jira·Confluence·comment·people·web 조회 결과를 최종 답변과 pending payload까지 보존하고 무관 evidence를 제거
- 입력에 이미 완전한 VoC/Bug 사실이 있으면 generic 재현 질문으로 되돌리지 않고 결정론적으로 Bug 초안 복원
- comment-only·field-only·create 등 최종 산출물 유형을 authoritative contract로 고정해 중간 role의 다른 action을 차단
- 실제 API가 tool calling을 지원하지 않는 경우에도 prompt JSON fallback과 local tool orchestration이 같은 계약을 유지하도록 보강
- 외부 HTTPS는 `certifi` CA 파일을 명시적으로 사용. sandbox의 Windows 인증서 저장소 접근을 우회하고 insecure TLS 재시도 제거
- query·person resolver 범위와 passive turn 호출을 줄여 품질 개선과 동시에 시간·token·비용 감소

## 측정 식별자

| 항목 | 값 |
|---|---|
| protocolVersion / rubricVersion | `2.0.0` / `2.0.0` |
| runKind | `exploratory` |
| primary runGroupId | `2026-08-16-en-v10-full-quality-r01` |
| closure focused groups | `2026-08-16-en-v10-focused-r03`, `2026-08-16-en-v10-focused-r04`, MTG3는 `focused-r02` |
| repetitions / repeatIndex | `1` / `1` |
| primary candidateCommit | `59a87d30b821594120b52515be4e0262a44fc01e` |
| final candidateCommit | `d3e4673ad1fa12fe14b2ce8c065da3772b9ed073` |
| promptVersion | `en-role-contract-v10` |
| model / simpleModel | `gpt-4o` / `gpt-4o-mini` |
| provider / runtimeProfile | `openai` / `production-mixed-v1` |
| data profile | `jira820-mock-v1` |
| dataManifestSha256 | `87e592d3cc136e62e135e5d81c76c91121da0e85d18fdc0b74bd0304f0521621` |
| primary selectionPolicy | `complete-run-no-substitution` |
| closure selectionPolicy | `primary-unaffected-cases-plus-latest-focused-failure-closure` |
| aggregation | case 동일 가중 산술평균, 축별 20% 동일 가중 |
| percentileMethod | `nearest-rank` |
| candidateOrderIndex | `unrecorded` |
| retryPolicy | `no-silent-retry` |
| cachePolicy | `cold-private-cache-each-case` |
| processIsolation | `separate-process-private-cache` |
| qualitativeEvaluatorPolicy | `codex-or-claude-direct-raw-output-review` |
| evaluatorAgentFamily / evaluatorAgentModel | `Codex` / `GPT-5 Codex` |
| directRawOutputReview / ltmLlmUsedAsJudge | `true` / `false` |
| reviewerCount / blindedReview | `1` / `false` |
| qualificationEligible | `false` — 1회 exploratory, candidateOrder 미기록, focused closure 합성 |

### Battery identity

Primary full-run identity:

| Suite | batteryVersion | batteryManifestSha256 | specializedReviewSpecSha256 | comparabilityKey |
|---|---|---|---|---|
| conversation | `3.0.0` | `ce5db38f609ee25a5104f2d42fa759882bca1a19d6a09a1fdc984fa5cbac021b` | `44b07943f34c78ea23f21c31aa856c8105e50fc493d575b4add6901dd111bb53` | `0fbfcc420da089011e8342f2ad652b2cbe71bf961548c3cec2af9413cf8e6852` |
| editor | `3.0.0` | `93c51fdfd97a571fcd4c8acbb52c49f0dbd267afca712c3ca0093fce29843a7f` | `61030118c370d5d795ec0a15a4d8ce1a8ceb646b9a39c1eced588cc236f8ed40` | `d08d8de8a6b68d1743e3e6f04d68d6ba28e1c14bdf1919224b9a678c1e8ae98c` |
| create | `4.0.2` | `be136915fca70e233ddf0fc360007076f9d633d46175b11ff90c548468707ee5` | `39fcbea7f3c527ff8bb76f9d1252b06769ae2f0d522943a08e8acd89a8eaaa0d` | `ee66f6be6b1325c0aadba6a8eb9fd3d11e8223b0d3d9bb31a7087946b11f7fcd` |
| meeting | `2.0.1` | `0c05a88016ef46d057b1fc46381b42abde8b6971f515089b905f87f930cc1f96` | `bc19f05cbeff5efb4a0f2f37c80558d35f15d3f99874400c15b0b1044b6c1f23` | `779cf63452e6cc592e59d426a58e8ede9c9aa01b921310e8b3a0d417b7b6b9ad` |
| ctx-chg | `2.0.0` | `a64966b582826173a63c03a7b1d9403e98078897f9dd66a5bafcaf444dc884ff` | `61db5b23296cdf2ad3e42586e1a11514548a90cfd71a56ad6351b8916d58ece7` | `e104b2a962c41302c43022499fcb14cc22144528f734a7204ebe0fc676b4ad48` |

Closure에서 `create`는 같은 `4.0.2` manifest, PASTE1 commit `9fadfa4`; `meeting` 최종 MTG1은 `2.0.3`,
manifest `e4b9a04fd61bf41f461d5f9c291e61e959da537c57a2a715c1f30aac73364a45`, comparabilityKey
`a42ee9e818111b33b47af212eff0573cb1980c8fbaa660af96b15d49ee8f7b6d`. MTG3는 `2.0.2`의 최종 통과 출력.

## 비교 가능성 및 evidence 선택

- Primary evidence는 `59a87d3`의 53-case complete run. 결과를 숨기거나 focused 성공으로 덮어쓰지 않음
- 최종 closure composite는 사용자가 지시한 “실패한 부분만 재실행”에 따라 primary의 영향 없는 50건과
  최종 PASTE1·MTG1·MTG3를 합친 운영 개선 확인용 결과
- PASTE1은 runtime 계약 개선 뒤 같은 create 4.0.2로 재실행. MTG1·MTG3는 결정 보존과 checker를 함께 고쳐
  meeting 2.0.1→2.0.3으로 변경됐으므로 자동점수는 primary와 직접 비교 제한
- 확장 기준선과 EN v8은 case ID 53개가 같지만 battery manifest·특수 검토 기준이 다름. 아래 과거 수치는
  **방향성 참고**이며 protocol상 qualification 절대 증감으로 사용하지 않음
- 사람 평가는 final code에서 채택한 각 case의 실제 출력만 직접 검토. LTM runtime LLM의 자기평가를 사용하지 않음

## 실행 격리

- suite는 별도 process, case는 cold private cache. 배터리 간 대화·pending action·mock mutation 공유 없음
- 모든 case에서 `worldUnchanged=true`, `providerStoreUnchanged=true`; 실제 Jira/Confluence write 없음
- provider-side prompt cache는 `cachedTokens`로만 집계하고 case 간 application cache 재사용 금지
- focused 실행도 해당 case만 새 process/private cache로 수행
- 외부 web 조회는 명시적 CA bundle 사용. Windows certificate store 접근 거부와 insecure fallback 모두 없음

## 실행 조건

- 실제 OpenAI API 사용. 복합 workflow는 `gpt-4o`, 단순 editor/분류 경로는 `gpt-4o-mini`
- mock Jira·Confluence·comment·people·외부 검색 fixture 사용, data manifest 고정
- 기술 오류를 성공으로 바꾸는 silent retry 없음. primary 53건 모두 실행 완료, API connection/JSON 기술 실패 0건
- primary full 이후 실패 3건만 focused 재실행. 성공 case를 다시 뽑아 best-of 하지 않음
- 가격은 harness의 당시 snapshot 계산값. provider 청구서와 소수점 차이가 날 수 있음
- 변경 범위 전체 오프라인 회귀 `1623 passed, 1 skipped`; 이후 focused 회귀 291건과 TLS 회귀 80건 통과
- raw JSON은 `.cache/agent-evaluation/`에만 저장되어 git에서 제외

## 배터리 범위

| Suite | 전체 | primary 실행 | closure 채택 | 누락 | 목적 |
|---|---:|---:|---:|---:|---|
| conversation | 7 | 7 | 7 | 0 | 생성·Bug·이력·사람·우선순위·진척·내외부 조사 |
| editor | 9 | 9 | 9 | 0 | 본문·댓글·seed·참조·정보 부족 차단 |
| create | 28 | 28 | 28 | 0 | hierarchy·분해·필드·중복·인터뷰·Bug 구조 |
| meeting | 5 | 5 | 5 | 0 | 요약·Task·결정 댓글·필드 수정·후속 Task |
| ctx-chg | 4 | 4 | 4 | 0 | 주제 전환·공유 정보 분리·취소·복귀 |
| **전체** | **53** | **53** | **53** | **0** | 기존 44 + meeting/context 9 |

## 정량 결과

### Primary full run

| Suite | 자동 결과 | 시간 | p50 / p95 | calls | prompt / completion / total token | cached | costUsd |
|---|---:|---:|---:|---:|---:|---:|---:|
| conversation | 계약 위반 0, 7/7 상당 | 81.7s | 12.2 / 26.5s | 28 | 125,988 / 6,894 / 132,882 | 60,160 | 0.363991 |
| editor | 9/9 | 26.9s | 2.4 / 5.4s | 8 | 43,892 / 1,427 / 45,319 | 22,528 | 0.124000 |
| create | 27/28 | 406.1s | 11.9 / 26.7s | 148 | 741,606 / 31,010 / 772,616 | 435,712 | 2.164118 |
| meeting | 3/5 | 175.3s | 31.6 / 54.4s | 41 | 235,291 / 11,971 / 247,262 | 108,544 | 0.707938 |
| ctx-chg | 4/4 | 76.8s | 13.5 / 27.2s | 29 | 162,312 / 5,807 / 168,119 | 86,528 | 0.442745 |
| **전체** | **50/53** | **766.8s** | **11.9 / 31.6s** | **254** | **1,309,089 / 57,109 / 1,366,198** | **713,472** | **3.802792** |

### Failure-closure composite

| Suite | 자동 결과 | 시간 | calls | prompt / completion / total token | cached | costUsd |
|---|---:|---:|---:|---:|---:|---:|
| conversation | 7/7 상당 | 81.7s | 28 | 125,988 / 6,894 / 132,882 | 60,160 | 0.363991 |
| editor | 9/9 | 26.9s | 8 | 43,892 / 1,427 / 45,319 | 22,528 | 0.124000 |
| create | 28/28 | 408.0s | 149 | 743,921 / 31,199 / 775,120 | 446,464 | 2.171795 |
| meeting | 5/5 | 170.9s | 40 | 226,317 / 11,700 / 238,017 | 117,760 | 0.682793 |
| ctx-chg | 4/4 | 76.8s | 29 | 162,312 / 5,807 / 168,119 | 86,528 | 0.442745 |
| **전체** | **53/53** | **764.3s** | **254** | **1,302,430 / 57,027 / 1,359,457** | **733,440** | **3.785324** |

Composite case latency는 p50 **12.2s**, p95 **27.2s**. primary의 실패 case 3개 지표를 빼고 최종 채택한
PASTE1(r03), MTG1(r04), MTG3(r02) 지표를 더해 산출.

### 과거 결과와 방향성 비교

| 지표 | 53-case 확장 기준선 | EN v8 | EN v10 closure |
|---|---:|---:|---:|
| 시간 | 1,046.7s | 930.3s | **764.3s** |
| LLM calls | 301 | 295 | **254** |
| prompt token | 1,430,819 | 1,425,877 | **1,302,430** |
| completion token | 72,285 | 70,467 | **57,027** |
| total token | 1,503,104 | 1,496,344 | **1,359,457** |
| cached token | 878,208 | 807,040 | **733,440** |
| costUsd | 4.299898 | 4.260553 | **3.785324** |
| 자동 계약 | 44/53 | 52/53 | **53/53** |
| Codex 사람 품질 | 4.20 | 4.30 | **4.56** |

v8 대비 v10 closure는 시간 -17.84%, calls -13.90%, total token -9.15%, 비용 -11.15%.
확장 기준선 대비는 시간 -26.98%, calls -15.61%, total token -9.56%, 비용 -11.97%.
배터리 version 차이로 품질점수의 산술 증감을 qualification 판정에 사용하지 않음.

## 사람 품질 평가 기준

정성평가자는 **Codex(GPT-5 Codex)**. raw JSON의 사용자 입력, 모든 turn의 답변·질문·pending payload,
query plan/result, evidence, trace를 직접 읽음. LTM LLM과 별도 LLM-as-judge는 점수 산출에 사용하지 않음.

각 축은 1.0~5.0, 0.5 간격, 20% 동일 가중:

| 축 | 판단 질문 |
|---|---|
| F 요청 충족 | 핵심 의도·전체 범위·복합 요구·명시 제약·최신 turn을 빠짐없이 충족했는가 |
| G 사실·근거 | entity·field·수치·시간·사람·source가 실제 조회와 일치하고 사실/추론/미확인을 구분했는가 |
| C 계약·실행성 | reply·question·card·payload가 일치하고 hierarchy·field·action이 실제 실행 가능한가 |
| S 안전·불확실성 | 필수 미확정 정보만 조사 후 질문하고, 불필요한 질문·가정·side effect·불법 수정을 막았는가 |
| R 표현·렌더링 | 결론 우선, 짧은 문장, heading/table/list, ticket/person/document marker가 용도에 맞는가 |

### Checklist 판정과 점수 상한

- `pass`: 구체 근거로 충족
- `minor`: 핵심 사용은 가능하나 국소 수정 필요. 해당 축 최고 4.5
- `major`: 핵심 누락·오판·실행 위험. 해당 축 최고 3.0
- `na`: 적용 불가 사유가 명확한 경우만 제외
- 모든 적용 항목 pass일 때만 5.0. minor 2건 이상이면 최고 4.0, major 2건 이상이면 최고 2.0
- `알아서`는 선택 재량 위임이지 필수 입력 면제가 아님. 필요한 질문은 가점, 불필요한 재질문과 필수 질문 누락은 감점

### 축별 checklist와 점수 anchor

| 축 | 핵심 checklist | 5 / 4 / 3 anchor |
|---|---|---|
| F | intent, completeness, constraints, relevance, latest-turn authority | 전부 완료 / 사소한 누락 / 중요한 범위 재작업 |
| G | entity fidelity, claim-source chain, chronology, counts, conflict disclosure | 전부 검증 / 작은 정밀도 결함 / 핵심 주장 근거 부족 |
| C | schema, reply-payload parity, hierarchy, field validity, action boundary | 즉시 실행 / 경미한 보정 / 실행 전 중요 수정 |
| S | required interview, question economy, ambiguity, side effect, Done guard | 정확히 통제 / 작은 caveat / 필수 질문 누락·불필요 차단 |
| R | information hierarchy, concision, badge/link/mention, no duplication | 수정 불필요 / 국소 표현 수정 / 읽기·렌더링 재구성 필요 |

Reviewer 1명, non-blind. 따라서 0.1점 차이는 평가자 편향 범위로 보고 큰 결함의 방향만 해석.

### 사람 품질 결과

| Suite | F | G | C | S | R | 종합 | EN v8 참고 |
|---|---:|---:|---:|---:|---:|---:|---:|
| conversation | 4.79 | 4.64 | 4.43 | 4.71 | 4.21 | **4.56** | 4.00 |
| editor | 4.72 | 4.56 | 4.50 | 4.78 | 4.33 | **4.58** | 4.46 |
| create | 4.68 | 4.39 | 4.66 | 4.61 | 4.29 | **4.53** | 4.35 |
| meeting | 4.80 | 4.50 | 4.50 | 4.70 | 4.30 | **4.56** | 4.18 |
| ctx-chg | 5.00 | 4.75 | 4.88 | 5.00 | 4.38 | **4.80** | 4.25 |
| **전체 53** | **4.74** | **4.49** | **4.60** | **4.69** | **4.29** | **4.56** | **4.30** |

가장 큰 잔여 약점은 표현·렌더링 4.29와 사실·근거 4.49. 자동 계약 만점과 사람 품질 만점은 다름.
특히 STARR1의 기술 의미 왜곡, PAR1의 username을 model로 오독한 본문, 일부 generic DoD·잘못된 경고는 여전히 수정 가치가 큼.

## 배터리·case 특수 검토요소

| 범위 | 특수 검토요소 | 최종 판정·실행 근거 |
|---|---|---|
| conversation 공통 | 질문별 Jira·Confluence·comment·people·web 경로와 claim-source chain | pass — 7건 모두 필요한 source를 실행하고 핵심 entity 보존 |
| S3 | DL-9041~9047·DL-9062 사건, 현재 상태, source dedupe | minor — 사건·현재값 완전, DL-9042 근거 번호 중복 |
| S7 | 내부 적용 이력과 Iceberg/Puffin 공식 외부 URL, conflict | pass — 내부/외부/충돌을 분리하고 공식 URL 제시 |
| editor 공통 | seed 보존, marker 유효성, 정보 없는 작성 차단 | pass — CMP3/4/7 포함 자의적 완성 차단 |
| CMP8 | 부모 목적·범위와 자식 실행 세부 분리 | minor — 경계는 지켰으나 문장 깨짐 |
| create 공통 | item 수, type/tier/parent, assignee/date/field, 질문 시점, reply-payload parity | pass — 28건 자동 계약 통과 |
| PAR1 | 성능·가이드·회귀와 x1402/x1450/x1042 매핑 | major(G) — payload mapping은 맞지만 본문에서 username을 model로 해석 |
| PASTE1 | VoC를 재현·기대·실제로 변환, 불필요한 질문 금지 | pass — 최종 focused에서 Bug 전문과 payload 보존 |
| STARR1 | StarRocks/Puffin/NDV 의미, DL-102, 3단계 child | major(G/R) — 구조는 정확하나 기술 목적 설명을 일반화·왜곡 |
| meeting 공통 | 사람 표기 정규화, 내부/외부 조사→미해결 인터뷰, 결정/담당/기한, write 보류 | pass — 최종 5건 모두 action·identity 계약 충족 |
| MTG1 | DL-7001·Puffin·5개 표본·운영 보류·3명 기한·PSR threshold | minor — 정보 완전, context 한 곳의 축약 mention 표기 결함 |
| MTG2 | DL-9200 하위 정확히 3 Task, 세 담당·세 기한 | minor — payload 정확, 불필요한 구조 경고·component 추정 |
| MTG3 | DL-9201·9202 comment-only, DL-7001·field 변경 제외, reviewer x1327 | pass — 두 대상에만 결정 전문 댓글 |
| MTG4 | DL-9203 지정 field/body만 수정, comment 금지 | minor — action 정확, 이미 같은 due를 payload에 반복 |
| MTG5 | 준서TL·PSR 조사 후 인터뷰, x1103/x1042·due·Epic | pass — 미해결 값만 질문 후 정확히 재개 |
| ctx-chg 공통 | 최신 요청 우선, superseded write 제거, 필요한 과거 context만 복원 | pass — 4건 모두 이전 action·무관 topic 미혼입 |
| CTX1 | fdc 중단 후 DL-9203 priority-only | minor(R) — 최종 범위 정확, 현재 priority를 `—`로 표시 |
| CTX2 | 기억 전용 turn 뒤 DL-9090 진행상황만 조회 | pass — fdc 일정 미혼입, 3 child와 남은 작업 제시 |
| CTX3 | priority/due→comment를 취소한 뒤 title-only | pass — 최종 payload에 제목만 유지 |
| CTX4 | 사람 업무 질문 뒤 DL-9095 comment-only 복귀 | pass — 정확한 남은 child 한 건만 대상 |

## 배터리별 실제 출력과 평가

축 순서는 `F/G/C/S/R`. 전문은 아래 raw cache JSON에 보존하며, 표에는 차이가 드러나는 핵심 문장과 payload만 발췌.

### conversation

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| S1 | 4.10 | 4.5/4/3.5/4.5/4 | Puffin NDV PoC Task + Batch Job·Puffin 저장 Sub-Task 2건 | 구조·용어·parent 보존. 두 child 책임이 일부 겹치고 배정 설명이 mention 뒤 raw username 반복 |
| S2 | 4.80 | 5/5/4.5/5/4.5 | Chrome, 2홉 확장, 빈 화면, 기대 그래프를 Bug로 분리 | 입력 사실과 재현성이 매우 좋고 질문 없이 즉시 사용 가능 |
| S3 | 4.50 | 5/4.5/4.5/4.5/4 | 현재 DAG·30분·8컬럼 + 8개 사건 연표 + 모든 근거 | 이력·현재 상태 완전. 같은 DL-9042가 [1]/[5]로 중복 인덱싱 |
| S4 | 4.80 | 4.5/5/5/5/4.5 | `[~skcc.i2011] · 미완료 21건`, 상태·우선순위·최근 5건·외 16건 | 여러 사람 질문을 1명으로 축소하던 과거 결함 제거. 전체 규모·pagination 표현 적절 |
| S5 | 4.90 | 5/5/5/5/4.5 | 최우선 한 건만 ticket detail과 선택 이유로 제시 | 후보 나열 없이 priority·기한·상태 근거로 단일 결정 |
| S6 | 4.00 | 4.5/4.5/4/4/3 | `하위 3개 중 2개 완료`, 남은 DL-9095와 source conflict | 사실은 정확. badge 정보 뒤 평문 중복과 일반적인 마감 조언이 불필요 |
| S7 | 4.80 | 5/4.5/4.5/5/5 | 내부 PoC·운영 제약 + Apache Iceberg·StarRocks 공식 URL + 충돌 공개 | 외부 공식 근거와 내부 사실을 분리한 전문 조사 형태. 일부 내부 상태 conflict는 명시 |

### editor

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| CMP1 | 4.80 | 5/5/4.5/5/4.5 | 완료 2/3, 진행 DL-9095, 남은 성능·문서, 설계 문서 link | Jira 상태를 완료 보고보다 우선하며 간결 |
| CMP2 | 4.30 | 4.5/4/4.5/4.5/4 | 배경·범위·작업·DoD 4섹션과 canonical DL-9040 | 형식 정상. DoD와 참조가 최소 수준이라 전문성은 보통 |
| CMP3 | 4.80 | 5/5/4.5/5/4.5 | p95 seed 유지, 높음/낮음 방향은 확인 필요 | 문법만으로 결론을 발명하지 않은 안전한 작성 |
| CMP4 | 5.00 | 5/5/5/5/5 | 대상·목적·남길 핵심을 질문하고 작성 보류 | `알아서`가 아니어도 필수 입력만 묻는 모범 경계 |
| CMP5 | 4.50 | 4.5/4.5/4.5/5/4 | Jira 기준 진행상황을 짧게 공유 | 상태 뒤집힘 없음. 끝의 상투적 협조 문장은 삭제 가능 |
| CMP6 | 4.70 | 5/4.5/4.5/5/4.5 | 담당 mention + 2홉 100노드 기준 + 설계 문서 link | 사람·검토 대상·문서가 모두 렌더링 가능 |
| CMP7 | 5.00 | 5/5/5/5/5 | 무관 레시피를 쓰지 않고 티켓 댓글 목적 질문 | context relevance guard 정상 |
| CMP8 | 3.80 | 4/4/3.5/4/3.5 | 부모 목적·범위·자식 책임만 요약 | 경계는 지켰으나 `기록한다할 것` 문장 깨짐과 generic 내용 때문에 재작성 필요 |
| CMP9 | 4.30 | 4.5/4/4.5/4.5/4 | 확인된 기능·검증·문서만 최소 본문 | 발명 없음. DoD와 근거가 다소 약함 |

### create

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| ONE1 | 4.00 | 4.5/3.5/4/4/4 | 단일 Task `[Workbench] 쿼리 편집기 단축키 도움말 팝업 추가` | 단일성 정확. primary payload에 client fragment 사용 가이드가 evidence로 들어간 결함은 이후 공통 evidence filter로 수정 |
| ONE2 | 4.20 | 4.5/4/4.5/4/4 | 단일 Story `[Catalog] '내 모듈만' 필터 추가` | 과잉 분해 없음. 사용자 효익 표현 일부가 입력보다 넓음 |
| STR1 | 4.80 | 5/5/5/5/4 | 부모 Task + 15명씩 두 Sub-Task, 합계 30명 | 누락·중복 없는 정확한 partition과 담당 매핑 |
| STR2 | 3.90 | 4/3.5/4/4/4 | 성능·인덱스·가이드 정확히 3 Task | 산출물 수는 정확. Runtime만 DL-5220에 배치되고 DoD가 generic |
| STR3 | 4.20 | 4.5/4/4.5/4/4 | 기존 DL-102 아래 ETL Task, 2주 기한 | 중복 Epic 방지와 보수적 배치 우수. 본문은 일반적 |
| PAR1 | 3.70 | 4/3/4/4/3.5 | DL-9090 아래 성능·가이드·회귀 3 Sub-Task와 정확한 담당 | payload는 정확하지만 본문이 username을 `X1402 모델`처럼 해석한 의미 결함 |
| PAR2 | 3.60 | 4/3/4/4/3 | DL-101 아래 `[ETL] CDC 재처리 배치 개선` | parent 정확. `아키텍처 결정 기록` 등 비canonical 참조와 약한 DoD |
| SUB1 | 5.00 | 5/5/5/5/5 | Sub-Task parent 불가, 형제/상위 Task/취소 선택 | hierarchy guard 모범 |
| SUB2 | 4.10 | 4.5/4/4/4/4 | DL-9090 아래 성능·가이드 2 Sub-Task | 생성 shape 정확. parent가 있는데도 제목/본문 부재 경고를 보여 주는 표현 결함 |
| SUB3 | 5.00 | 5/5/5/5/5 | 여러 대상이 모두 Sub-Task라 생성 보류 | 불법 계층을 만들지 않고 대안만 질문 |
| PASTE1 | 4.80 | 5/5/5/5/4 | Bug `[Catalog] 조회 화면 컬럼 설명 미표시`; 재현·기대·실제 전문 | 최종 r03에서 추가 질문 없이 VoC를 실행 가능한 Bug로 복원, stale rationale·`#/home` evidence 제거 |
| PASTE2 | 4.80 | 5/5/5/5/4 | Bug `[ETL] prod의 dag_etl_nightly 야간 배치 실패` | DAG·환경·시각·timeout·재실행 사실 보존 |
| ASKD1 | 5.00 | 5/5/5/5/5 | 품질 규칙 대상 dataset/table/column 질문 | `알아서`여도 행동 필수 target은 질문해야 한다는 기준 충족 |
| ASKD2 | 4.30 | 4.5/4/4.5/4.5/4 | 답변 뒤 DL-9090 성능 회귀 Sub-Task | 질문 시점·parent 정확. 성능 저하 없음 DoD에 수치 기준은 미확정 |
| ASKD3 | 5.00 | 5/5/5/5/5 | 댓글 내용·목적 질문, draft 없음 | 빈 댓글 발명 차단 |
| AMB1 | 5.00 | 5/5/5/5/5 | `test.same01` / `test.same02` exact 후보 | 동명이인 선택 전 mutation 없음 |
| ASK1 | 5.00 | 5/5/5/5/5 | 생성 target·scope부터 질문 | 후순위 placement를 먼저 묻지 않음 |
| ASK2 | 4.50 | 4.5/4.5/4.5/5/4 | 30개 table·null ratio·이번 주 기한을 최종 Task에 보존 | multi-turn 정보 손실 없음. DoD/report 문구는 generic |
| DUP1 | 5.00 | 5/5/5/5/5 | DL-9072 key·제목·중복 근거와 기존 확장/별도 분리 선택 | 이전 v8 유일 자동 실패를 결정 가능한 질문으로 개선 |
| ATTR1 | 4.20 | 4.5/4/4.5/4.5/3.5 | 45분 threshold, P1, due, `hotfix` label | payload 값 정확. label이 제목/본문에 없다는 무의미한 경고가 남음 |
| ASKD4 | 5.00 | 5/5/5/5/5 | 정확한 새 threshold 질문 | 기존값을 임의 mutation하지 않음 |
| ATTR2 | 4.30 | 4.5/4/4.5/4.5/4 | 신규 `quality-gate` label을 Task에 보존 | 새 enum을 막지 않음. scope가 입력보다 다소 넓음 |
| STARR1 | 3.20 | 3.5/2.5/4/3.5/2.5 | DL-102 아래 StarRocks Puffin NDV Task + 3 Sub-Task | 구조·고유명사 보존은 성공. 기술 목적을 일반 데이터 수집/저장으로 풀어 의미가 왜곡된 가장 큰 잔여 결함 |
| BUG1 | 5.00 | 5/5/5/5/5 | 화면 경로·브라우저/환경·조건/빈도만 묶어 질문 | 이미 알려진 actual symptom을 되묻지 않음 |
| BUG2 | 4.50 | 5/4.5/4.5/4.5/4 | Chrome·2홉·빈 화면·기대 그래프 Bug | 사실 보존. component 선택은 다소 불확실 |
| BUG3 | 5.00 | 5/5/5/5/5 | DAG/Job·환경·시각·대표 오류를 한 번에 질문 | 중복 후보 식별과 재현 정보 수집 순서 정확 |
| RULE1 | 5.00 | 5/5/5/5/5 | parent 없는 Sub-Task 거절, Task 전환/parent 선택 | domain hierarchy 정확 |
| RULE2 | 4.60 | 4.5/4.5/5/4.5/4.5 | Story 생성, 미지원 Story Point는 follow-up으로 분리 | 지원 field 계약 준수 |

### meeting

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| MTG1 | 4.60 | 5/4.5/4.5/4.5/4.5 | 5개 결정, 운영 보류, 3명 owner·due, PSR threshold, 내부/외부 근거 | 최종 r04는 회의 의미와 조사 결과 완전. context 한 곳의 `[~1042]` 축약만 경미 |
| MTG2 | 4.20 | 4.5/4/4/4.5/4 | DL-9200 아래 정확히 3 Task, i2011/x1402/x1103, 8/22·25·28 | payload 우수. `RGP 기준+템플릿`을 두 Task처럼 경고하고 일부 component를 추정 |
| MTG3 | 4.90 | 5/5/5/5/4.5 | DL-9201·9202에만 동일 결정 전문 댓글, reviewer x1327 | 최종 r02는 comment-only·대상·결정 보존 모두 정확, DL-7001 미변경 |
| MTG4 | 4.30 | 4.5/4/4.5/4.5/4 | DL-9203 summary/priority/due/labels/body만 update | action 범위 정확. 이미 같은 due를 no-op으로 다시 포함하고 본문 말투 일부 구어체 |
| MTG5 | 4.80 | 5/5/4.5/5/4.5 | 준서TL·PSR만 인터뷰 후 x1103 담당, x1042 reviewer, due·Epic | 조사로 못 푼 값만 묻고 최종 Task에 모두 보존 |

### ctx-chg

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| CTX1 | 4.60 | 5/4.5/5/5/3.5 | fdc 조사 중단 후 DL-9203 priority만 P2-Major 초안 | 이전 topic 완전 제거. 현재값을 `—`로 표시한 정밀도만 아쉬움 |
| CTX2 | 4.70 | 5/4.5/4.5/5/4.5 | 기억 전용 turn 뒤 DL-9090 2/3 완료·DL-9095 남음 | 공유한 fdc 일정이 새 조회에 섞이지 않고 모든 child key 보존 |
| CTX3 | 4.90 | 5/5/5/5/4.5 | priority/due와 comment를 취소하고 title-only 최종 초안 | superseded action이 final reply/payload에서 완전히 제거 |
| CTX4 | 5.00 | 5/5/5/5/5 | 이다은 업무 조회 뒤 DL-9095 한 건에 comment-only | 중간 사람 context 미혼입, 과거 DL-9090의 남은 child만 정확히 복원 |

## 자동 checker와 사람 판정 불일치

| Run / case | 자동 | 사람 판정 | 원인·조치 |
|---|---|---|---|
| primary PASTE1 | fail | major — 불필요한 재현 질문 | complete VoC를 Bug seed로 복원하는 공통 경로 추가, r03 pass |
| primary MTG1 | fail | major — 결정/owner 일부 손실 | meeting context와 최종 report 결정 보존 강화, r02 pass |
| r03 MTG1 | fail | 사람 기준 내용은 허용 가능하나 minor 존재 | checker가 `운영 반영은 보류`의 한국어 조사를 허용하지 않은 false negative. stale identity 문장도 제거하고 battery 2.0.3으로 올려 r04 pass |
| primary MTG3 | fail | major — reply와 comment payload 모순 | bulk comment reply를 pending payload에서 기계적으로 복원해 r02 pass |
| final STARR1 | pass | 사람 major(G/R) | 구조 checker는 type/parent/고유명사만 확인해 기술 의미 왜곡을 잡지 못함. 다음 rubric/battery에 semantic fidelity 항목 보강 필요 |
| final PAR1 | pass | 사람 major(G) | assignment mapping checker는 맞지만 본문에서 username을 model로 해석. body semantic checker 필요 |

자동 53/53은 구조·필드·금지 행동 계약의 green이며, 위 human major 두 건 때문에 “인간 품질 만점”으로 해석하지 않음.

## 구조 개선 효과와 남은 개선점

### 효과가 확인된 공통 개선

- `identity`: 역할명을 alias 없이 canonical role ID로 연결하고, 사용자-facing reply와 internal role output 경계를 명시
- `common`: 최신 요청, 결정된 사실, 미확정 필수정보, action 종류, evidence 렌더링을 모든 role이 공유
- `request_architect`: passive memory와 context switch를 조기 분류해 불필요한 전 역할 호출 제거
- `work_architect`: hierarchy·Bug seed·meeting 결정·중복 후보·필수 인터뷰를 payload 전 단계에서 구조화
- `result_integrator`: authoritative pending payload에서 reply를 복원해 댓글/필드/생성 범위 불일치 차단
- `query/research`: search config 범위와 evidence chain을 보존하고 client-only URL·무관 source 제거
- `people`: partial name·호칭을 directory 후보로 정규화하고 미해결일 때만 사용자 인터뷰

### 다음 라운드에서 우선할 구조 개선

1. ticket body의 기술 의미 검증: 고유명사 존재 여부가 아니라 입력의 주어·목적·데이터 흐름 보존 검사
2. username·ticket key·label을 자연어 명사로 재해석하지 못하게 typed entity span을 body 생성까지 유지
3. generic DoD 생성 억제: 입력·조회로 검증 가능한 완료 조건만 허용하고 없으면 확인 필요로 남김
4. warning 생성 규칙: label/parent/component처럼 payload 자체에 존재하는 값을 제목·본문 부재로 경고하지 않음
5. no-op update 제거: 현재값과 새 값이 같으면 pending field에서 제외하고 사용자에게 변경 없음으로 표시
6. evidence dedupe: 같은 ticket이 여러 claim을 뒷받침해도 하단 detail badge는 한 번만 출력하고 claim marker를 공유

## 실패·재시도·제한사항

- primary 자동 실패: PASTE1, MTG1, MTG3. 모두 숨기지 않고 원 raw와 수정 후 focused raw를 보존
- 기술 실패·connection error·invalid JSON: 0건. 자동 실패는 모두 품질/checker 계약 실패
- 재실행: 실패 case만 수행. primary 성공 50건은 재샘플링하지 않음
- final 53/53은 single-commit full run이 아니라 closure composite. production qualification에는 동일 최신 battery로 5회 이상 full run 필요
- meeting checker 변경으로 primary와 closure의 자동점수 직접 비교 제한
- Codex 1인 non-blind 평가. 사람 점수는 재현 가능한 rubric을 따르지만 평가자 편향이 존재
- 실제 외부 Jira/Confluence/provider write 없음. mock 세계에서의 실행성이 production 권한·schema 성공을 보장하지 않음
- primary 이후 common evidence filter를 고쳤지만 성공 case ONE1 전체를 재실행하지 않았으므로 해당 출력 평가는 primary 결함을 그대로 기록

## Raw 결과

- primary: `.cache/agent-evaluation/2026-08-16-en-v10-full-quality-r01/`
- first closure: `.cache/agent-evaluation/2026-08-16-en-v10-focused-r02/`
- final PASTE1: `.cache/agent-evaluation/2026-08-16-en-v10-focused-r03/create-b4.0.2-focused.json`
- final MTG1: `.cache/agent-evaluation/2026-08-16-en-v10-focused-r04/meeting-b2.0.3-focused.json`

Raw는 `.gitignore` 대상. 이 보고서만 장기 비교용으로 commit.
