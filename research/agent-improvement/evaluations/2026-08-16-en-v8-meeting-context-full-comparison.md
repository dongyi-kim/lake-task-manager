# English Agent v8 — Meeting·Context-change 포함 전체 배터리 비교

> 결론: 신규 9개 case를 포함한 53-case 단일 commit 전체 실행에서 사람 품질 **4.30/5**, 자동 계약 **52/53**.
> 확장 기준선 4.20 대비 사람 품질은 **+0.10**, 시간은 **-11.12%**, total token은 **-0.45%**, 비용은 **-0.92%**.
> 특히 신규 `meeting`은 2.44→4.18, `ctx-chg`는 3.30→4.25로 개선. 다만 기존 44 case는 4.48→4.31로 낮아졌고,
> 이번 결과는 1회 exploratory run이므로 통계적 qualification이나 무조건적인 배포 우위 판정으로 사용하지 않음.

## 측정 식별자

| 항목 | 값 |
|---|---|
| protocolVersion / rubricVersion | `2.0.0` / `2.0.0` |
| runKind | `exploratory` |
| runGroupId | `2026-08-16-en-v8-full-meeting-context-r01` |
| repetitions / repeatIndex | `1` / `1` |
| candidateCommit | `b419b143ecbb58c16a0db37772d693df89ae1485` |
| promptVersion | `en-role-contract-v8` |
| model / simpleModel | `gpt-4o` / `gpt-4o-mini` |
| provider / runtimeProfile | `openai` / `production-mixed-v1` |
| data profile | `jira820-mock-v1` |
| dataManifestSha256 | `87e592d3cc136e62e135e5d81c76c91121da0e85d18fdc0b74bd0304f0521621` |
| selectionPolicy | `complete-run-no-substitution` |
| aggregation | case 동일 가중 산술평균, 축별 20% 동일 가중 |
| percentileMethod | `nearest-rank` |
| candidateOrderIndex | `1` |
| retryPolicy | `no-silent-retry` |
| cachePolicy | `cold-private-cache-each-case` |
| processIsolation | `separate-process-private-cache` |
| qualitativeEvaluatorPolicy | `codex-or-claude-direct-raw-output-review` |
| evaluatorAgentFamily / evaluatorAgentModel | `Codex` / `GPT-5 Codex` |
| directRawOutputReview / ltmLlmUsedAsJudge | `true` / `false` |
| reviewerCount / blindedReview | `1` / `false` |
| qualificationEligible | `false` — exploratory 1회, 요구 반복 수 5회 미만 |

| Suite | batteryVersion | batteryManifestSha256 | specializedReviewSpecSha256 | comparabilityKey |
|---|---|---|---|---|
| conversation | `2.0.0` | `799d045350bd36e93be4fed1564bf722a2ab435de148a4357e885576b7ed7203` | `0d9b63458fbae238c4813865baaa8d9093f3fb1c670d55833d2604e6ac0e87ce` | `cdad82e1a8468f285c55d86045308ac8aa90dc21169365b075c9b6aaf9695449` |
| editor | `2.0.0` | `70567cbf773208f30b91d17d33477fd5da490877ad8bc7f1cc67a9748d0d7eea` | `b6f81a25c0c9697c2e57f06706c385a196542ddf78c87731cd51e7eac2475c1b` | `db8ea9df75da3035f86033abb241c0a5a38ffe7e1698554eb0e310fc27da03ce` |
| create | `3.0.0` | `e7bfd58d9a5ee3d6eb9d32dcc16e094770d35cf9166d6423711320dfcc70490e` | `83ec3e40f31167216cf9033b1e55d5f63d076f982a562366c78bc29931d3b363` | `a311c5d2d7454d4170a34cee7aa160145da4f93989ed3d87be153e32d340afe6` |
| meeting | `1.0.0` | `4eef1d8e848d6dc13d9e6b1978889199e5e9b1b9eecccc4049696ff74288cfd3` | `d5d24bf1aac3bb40117824721807a19e3ce49bbe872d4d0ade74c03ad47c6e83` | `a12001ab10ce974be41ab7ab98d9095fda774e41a5dc3a927f4057ea00957022` |
| ctx-chg | `1.0.1` | `6ece39ae523f7da41e0a040c57f883fa0b530746c4b20e4ef9d82df756a812b8` | `63858d0bb8086270c302225817b70df970c9cc84716a98a4c7d4dbb0aeed20fc` | `7dc455b78907862d3e980479d53ee3cea31445f1132ab1cab6d5601eac1d7085` |

## 비교 가능성 및 evidence 선택

- 기준선: `2026-08-15-en-v6-quality-efficiency-rubric-2.0-composite.md`에 확정한 53-case 확장 기준선
- 기준선 구성: 기존 EN v6 44-case closure composite + main runtime 신규 `meeting` 5건·`ctx-chg` 4건 full run
- 현재 후보: 다섯 suite 모두 같은 `candidateCommit`, promptVersion, data manifest, model routing으로 다시 실행한 primary full run
- 현재 결과에는 focused 재실행이나 best-of 결과를 섞지 않았으며, 완료된 첫 전체 실행을 그대로 사용
- 기존 44-case 기준선은 selective closure composite이므로 현재 44-case와의 차이는 방향성 참고값
- `meeting`은 같은 battery 1.0.0이라 직접 비교 가능. `ctx-chg`는 checker가 canonical 단일 `update_ticket`도 허용하도록
  1.0.0→1.0.1로 변경되어 자동 점수는 직접 비교 제한. 입력·특수 검토 명세·사람 평가 기준은 같아 사람 품질은 비교 가능
- 기준선과 현재 모두 1회 실행. 확률적 출력과 API 상태 편차가 있으므로 0.1점 단위 차이를 안정적 우위로 단정하지 않음

## 실행 조건

- 실제 OpenAI API 사용, production과 같은 혼합 라우팅: 복합 작업 `gpt-4o`, 단순 작업 `gpt-4o-mini`
- mock Jira/Confluence/댓글/사람/외부 검색 fixture 사용. 실제 사내·외부 시스템 변경 없음
- 각 case마다 cold private cache, suite별 별도 process. 앞 case의 cache·대화·pending action·world mutation이 다음 case로 전파되지 않음
- Store fingerprint를 실행 전후 비교해 world 변경 없음 확인. 모든 write는 승인 대기 payload까지만 생성
- 기술 오류를 감추는 silent retry 없음. 이번 primary run은 누락·재시도·API 기술 실패 없이 53/53 실행 완료
- 비교 후보 순서는 단일 후보 1번. 가격은 `openai-2026-08-unpriced` snapshot의 harness 계산값

## 배터리 범위

| Suite | 전체 | 실행 | 누락 | 목적 |
|---|---:|---:|---:|---|
| conversation | 7 | 7 | 0 | 생성·Bug·히스토리·사람·우선순위·진척·내외부 조사 |
| editor | 9 | 9 | 0 | 본문·댓글 작성, marker, seed 보존, 정보 부족 차단 |
| create | 28 | 28 | 0 | hierarchy·분해·담당·필드·중복·인터뷰·Bug 구조 |
| meeting | 5 | 5 | 0 | 회의 요약·Task 생성·댓글·필드/본문 수정·후속 Task |
| ctx-chg | 4 | 4 | 0 | 무관 주제 전환·정보/요청 분리·취소·이전 주제 복귀 |
| **전체** | **53** | **53** | **0** | 기존 44 + 신규 9 |

회의록 공통 계약은 모든 `meeting` case에 적용:

- `@이름`, `{{이름:식별자}}`, 이름 일부+호칭을 canonical 사용자로 정규화
- 모호한 사람·호칭·회의 한정 용어는 Jira·Confluence·댓글·사람 directory·안전한 외부 자료를 먼저 조사
- 조사 후에도 행동에 필요한 사람·뜻·범위·기한이 확정되지 않을 때만 구체적인 후보·공백을 인터뷰
- unresolved 상태에서는 create/comment/update 초안을 만들지 않고, 답변 후에는 해결된 질문을 반복하지 않음

## 정량 결과

### 현재 전체 실행

| Suite | 자동 결과 | 시간 | p50 / p95 | calls | prompt / completion / total token | cached | costUsd |
|---|---:|---:|---:|---:|---:|---:|---:|
| conversation | 계약 위반 지표 0 | 105.9s | 13.2 / 37.1s | 36 | 159,716 / 9,626 / 169,342 | 81,280 | 0.495552 |
| editor | 9/9 | 19.3s | 2.5 / 3.3s | 8 | 39,332 / 1,339 / 40,671 | 19,584 | 0.111720 |
| create | 27/28 | 514.1s | 16.9 / 34.8s | 165 | 773,335 / 38,515 / 811,850 | 421,760 | 2.318488 |
| meeting | 5/5 | 211.3s | 39.3 / 52.5s | 55 | 311,142 / 14,486 / 325,628 | 193,280 | 0.922715 |
| ctx-chg | 4/4 | 79.7s | 19.1 / 28.4s | 31 | 142,352 / 6,501 / 148,853 | 91,136 | 0.412078 |
| **전체** | **52/53 상당** | **930.3s** | **15.1 / 45.8s** | **295** | **1,425,877 / 70,467 / 1,496,344** | **807,040** | **4.260553** |

`conversation`은 7개 case 전체에 binary pass를 기록하는 대신 근거·과검증 계약 위반 합계가 모두 0인지 검사.
전체 자동 결과의 `52/53`은 이 7개를 green으로 환산한 요약이며, 사람 품질 52/53 통과를 의미하지 않음.

### 확장 기준선 대비

| 지표 | 53-case 기준선 | 현재 EN v8 | 증감 |
|---|---:|---:|---:|
| 시간 | 1,046.7s | 930.3s | **-11.12%** |
| LLM calls | 301 | 295 | **-1.99%** |
| prompt token | 1,430,819 | 1,425,877 | **-0.35%** |
| completion token | 72,285 | 70,467 | **-2.52%** |
| total token | 1,503,104 | 1,496,344 | **-0.45%** |
| cached token | 878,208 | 807,040 | -8.10% |
| costUsd | 4.299898 | 4.260553 | **-0.92%** |
| 자동 계약 | 44/53 | 52/53 | +8 case |

| 구간 | 기준선 시간 / calls / token / cost | 현재 | 변화 |
|---|---|---|---|
| 기존 44 case | 708.0s / 217 / 1,024,445 / $2.935185 | 639.3s / 209 / 1,021,863 / $2.925760 | 시간 -9.70%, calls -3.69%, token -0.25%, cost -0.32% |
| 신규 9 case | 338.7s / 84 / 478,659 / $1.364713 | 291.0s / 86 / 474,481 / $1.334793 | 시간 -14.08%, calls +2.38%, token -0.87%, cost -2.19% |

신규 case의 call 수는 2회 늘었지만 조사·인터뷰 정확도가 크게 좋아진 상태에서 시간·token·비용이 감소.
CTX2의 정보 공유 전용 첫 turn은 7 calls·36,221 tokens에서 focused 확인 기준 1 call·3,249~3,399 tokens로 축소됐고,
최종 full suite에서도 전체 ctx-chg 비용이 기준선보다 감소.

## 사람 품질 평가 기준

정성평가 주체는 LTM Agent가 아니라 **Codex(GPT-5 Codex)**. raw JSON의 모든 turn에 대해 사용자 입력, 답변,
질문, pending payload, query plan/result, evidence, trace를 직접 읽어 평가. LTM LLM을 judge로 사용하지 않았음.

각 축은 1.0~5.0, 0.5 간격, 20% 동일 가중:

| 축 | 직접 판단 질문 |
|---|---|
| F 요청 충족 | 핵심 의도·전체 범위·복합 요구·명시 제약·관련성·다음 행동을 빠짐없이 충족했는가 |
| G 사실·근거 | entity·field·건수·시간·사람·source가 조회 결과와 일치하고 사실/추론/미확인을 구분했는가 |
| C 계약·실행성 | reply·question·card·payload가 일치하고 hierarchy·field·승인·댓글/수정 범위가 실행 가능한가 |
| S 안전·불확실성 | 꼭 필요한 미확정 정보는 조사 후 질문하고, 불필요한 질문·자의적 가정·side effect·완료 티켓 불법 수정을 막았는가 |
| R 표현·렌더링 | 결론 우선·짧은 문장·heading/table/list·ticket/person/document marker·목록 규모별 badge가 적절한가 |

점수 anchor: 5는 실사용 가능하고 수정 불필요, 4는 사소한 수정, 3은 중요한 누락·추정으로 수정 필요,
2는 주요 요청 실패, 1은 사용 불가 또는 중대한 사실·안전 실패. 공통 checklist에 각 case의 특수 검토요소를 더하고,
특수 major가 있으면 해당 축의 ceiling을 적용. reviewer 1명, non-blind이므로 평가자 편향 한계 존재.

### 사람 품질 결과

| Suite | F | G | C | S | R | 종합 | 기준선 | 증감 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| conversation | 4.00 | 3.86 | 4.21 | 4.29 | 3.64 | **4.00** | 4.23 | -0.23 |
| editor | 4.44 | 4.39 | 4.44 | 4.50 | 4.50 | **4.46** | 4.57 | -0.11 |
| create | 4.43 | 4.30 | 4.45 | 4.45 | 4.11 | **4.35** | 4.52 | -0.17 |
| meeting | 4.10 | 4.00 | 4.00 | 5.00 | 3.80 | **4.18** | 2.44 | **+1.74** |
| ctx-chg | 4.25 | 4.13 | 4.50 | 4.88 | 3.50 | **4.25** | 3.30 | **+0.95** |
| **전체 53** | **4.33** | **4.22** | **4.38** | **4.52** | **4.04** | **4.30** | **4.20** | **+0.10** |

기존 44개만 보면 4.48→4.31(-0.17), 신규 9개는 2.82→4.21(+1.39). 즉 전체 향상은 신규 기능의 구조적 실패를
해소한 효과가 크며 기존 case까지 일괄 향상했다는 뜻은 아님. 축별로 C +0.24, S +0.28이 개선됐지만 R은 -0.12.

## 배터리·case 특수 검토요소

| 범위 | 특수 검토요소 |
|---|---|
| conversation S3 | DL-9041~9047·DL-9062 사건 순서, 현재 상태, source dedupe |
| conversation S7 | Jira·Confluence·comment 내부 근거와 Iceberg/StarRocks 공식 외부 URL, 사실/추론 경계 |
| editor | seed 보존·marker 유효성·정보 없는 작성 차단·ticket/person/document 렌더링 |
| create | 정확한 item 수·tier/type/parent·assignee/date/field·필수 인터뷰 시점·pending payload·중복 근거 |
| meeting 공통 | 사람 표기 정규화, 내부/외부 조사→미해결 인터뷰 순서, 결정/담당/기한/미결 분리, 승인 전 write 금지 |
| MTG1 | DL-7001·Puffin 문서·댓글·공식 자료, 5개 표본·운영 보류·3명 기한·PSR threshold |
| MTG2 | DL-9200 하위 정확히 3 Task, i2011/x1402/x1103, 8/22·8/25·8/28, 회의에 없는 field 발명 금지 |
| MTG3 | DL-9201·DL-9202에만 comment-only, DL-7001·field 변경 제외, reviewer x1327 |
| MTG4 | DL-9203의 summary/priority/due/component/labels/description만 정확히 수정, comment 금지 |
| MTG5 | 준서TL 두 후보와 PSR 의미를 조사 후 질문, 답변 전 draft 금지, assignee x1103·reviewer x1042 |
| ctx-chg 공통 | 최신 요청 우선, 공유 정보와 행동 요청 분리, superseded write 제거, 필요한 과거 context만 복구 |
| CTX1 | 무관 topic 전환 후 priority-only update, fdc context 최종 출력 금지 |
| CTX2 | 첫 turn은 기억만 하고 조사 금지, 이후 DL-9090 현재 진척·모든 child key 포함 |
| CTX3 | priority/due/comment를 차례로 취소한 뒤 제목만 변경 |
| CTX4 | 중간의 다른 사람 업무 질문을 정확히 답하고, 복귀 후 DL-9095 comment-only |

## 배터리별 실제 출력과 평가

표의 축 순서는 `F/G/C/S/R`. 전문은 `.cache/agent-evaluation/2026-08-16-en-v8-full-meeting-context-r01/`의
suite JSON에 보존. 차이가 없거나 정상인 반복 payload는 핵심 발췌만 표기.

### conversation

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| S1 | 3.60 | 4/3/4/4/3 | PoC Task + 설계·구현 Sub-Task 2건 | 구조는 충족. `12건이라 낮음`과 `8건이라 높음`이 충돌하고 담당 표가 중복. 외부 근거는 payload에만 존재 |
| S2 | 4.30 | 4.5/4/4.5/4.5/4 | Chrome·2 hop·빈 화면·그래프를 Bug로 구조화 | 재현성 높음. P3·regression은 요청 외 값이지만 승인 전 초안이라 경미 |
| S3 | 4.60 | 5/4.5/4.5/5/4 | 8개 사건 연표와 현재 상태 | 이력 복원 우수. 같은 DL-9042가 근거 [1], [5]로 중복 인덱싱 |
| S4 | 3.80 | 3.5/4/4/4/3.5 | “현재 작업”에 DL-9201·DL-5878 완료 항목도 포함 | 완료 작업과 일반 조언이 섞여 범위가 흐림. mention badge는 정상 |
| S5 | 4.10 | 4/4.5/4.5/4/3.5 | DL-9028을 최우선으로 결정, DL-9008·9029 병기 | 근거는 충분하나 단일 우선순위 답이 덜 단호하고 badge 뒤 상태가 중복 |
| S6 | 4.30 | 4.5/4.5/4.5/4.5/3.5 | 3개 child의 완료/진행 상태와 모든 key 출력 | 사실 완전성 회복. badge 제목·담당과 이어지는 raw text가 중복 |
| S7 | 3.30 | 2.5/2.5/3.5/4/4 | 내부 이력 뒤 “외부 검증 필요”만 제시 | 공식 외부 URL·검색 결과가 최종 답변에 없고, 연표의 PoC 완료와 현재표의 미수행이 충돌 |

### editor

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| CMP1 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | 완료 2·진행 1·후속·문서 URL | 상태와 근거를 간결하게 보존 |
| CMP2 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | canonical DL-9040 badge + 최소 DoD | nested marker 없이 정상 |
| CMP3 | 4.00 | 4/3.5/4.5/3.5/4.5 | seed를 “p95가 생각보다 높게…”로 완성 | 문장 품질은 좋지만 근거 없는 방향으로 unfinished seed를 확정 |
| CMP4 | 4.80 | 5/5/4.5/5/4.5 | 대상·목적·한 줄을 질문하고 작성 보류 | 정보 없는 자의적 댓글 생성 차단 |
| CMP5 | 4.40 | 4.5/4/4.5/4.5/4.5 | child 상태·200자·문서 link | 요청 범위 충족, 근거 표현 일부 일반화 |
| CMP6 | 4.10 | 3.5/4/4/4.5/4.5 | 담당자를 mention하고 설계 문서를 언급 | 요청한 측정 기준과 실제 문서 reference가 빠짐 |
| CMP7 | 4.80 | 5/5/4.5/5/4.5 | 무관 seed를 잇지 않고 댓글 목적 질문 | context boundary 우수 |
| CMP8 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | parent DoD를 성능·가이드·통합 근거로 압축 | child 실행 이력 반복 없음 |
| CMP9 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | 최소 범위·DoD·canonical badge | 발명 없이 사용 가능 |

### create

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| ONE1 | 3.70 | 4/3/4/4/3.5 | 단일 Task를 생성하되 DL-5560·5334·5122를 참조 | 관련 티켓이 없다고 말한 뒤 무관 참조를 payload에 첨부. DoD도 주관적 |
| ONE2 | 3.90 | 4/4/4/4/3.5 | 체크박스 정리 단일 Task | 구조는 맞지만 DoD 문장이 “체크박스가 하여…”로 깨짐 |
| STR1 | 4.40 | 4.5/4.5/4.5/4.5/4 | 30개를 15+15 두 Sub-Task로 분할 | 수량·parent·담당 정확 |
| STR2 | 3.70 | 4/3.5/3.5/4/3.5 | 성능·index·guide 3 Task | reply는 Epic 불필요라지만 Runtime 항목 parent는 DL-102. 성능/index에 guide-link DoD 혼입 |
| STR3 | 4.30 | 4.5/4.5/4.5/4/4 | DL-102 재사용, ETL Task, 2주 due | 보수적인 hierarchy·기한 산정 우수 |
| PAR1 | 4.40 | 4.5/4.5/4.5/4.5/4 | DL-9090 아래 3 Sub-Task와 정확한 담당 | mapping 정확 |
| PAR2 | 3.30 | 3.5/2.5/4/3/3.5 | DL-101 아래 CDC Task | 답변에 20% 속도·5% 오류를 발명하고 DoD·관련 문서가 오염 |
| SUB1 | 4.90 | 5/5/5/5/4.5 | Sub-Task parent 불가를 설명하고 대안 질문 | hierarchy guard 모범 사례 |
| SUB2 | 4.00 | 4.5/4/3.5/4.5/3.5 | DL-9090 아래 성능·가이드 2건 | payload parent는 맞지만 답변 표에는 `-`; guide DoD 중복 |
| SUB3 | 4.70 | 4.5/5/5/4.5/4.5 | 대상이 모두 Sub-Task라 허용 방법이 없음을 설명 | 안전하고 정확 |
| PASTE1 | 4.30 | 4.5/4/4.5/4.5/4 | 컬럼 설명 미표시 VoC를 Bug로 변환 | 핵심 사실 보존. “선택 컬럼”은 약한 추론 |
| PASTE2 | 4.70 | 5/5/4.5/4.5/4.5 | prod DAG timeout 증상을 구체 Bug로 변환 | 입력 사실을 잘 보존 |
| ASKD1 | 4.80 | 5/5/4.5/5/4.5 | dataset/table/column을 구체 질문 | `알아서`여도 필수 target을 묻는 올바른 인터뷰 |
| ASKD2 | 4.20 | 4.5/4/4/4.5/4 | 인터뷰 후 DL-9090 아래 scope 재구성 | 실행 가능하나 scope·DoD 설명 일부 충돌 |
| ASKD3 | 4.80 | 5/5/4.5/5/4.5 | 댓글 내용·목적을 질문 | 빈 댓글 발명 차단 |
| AMB1 | 4.80 | 5/5/4.5/5/4.5 | test.same01/02 후보 제시 | 동명이인 mutation 안전성 우수 |
| ASK1 | 4.80 | 5/5/4.5/5/4.5 | 편집 target을 질문 | 필요한 정보만 인터뷰 |
| ASK2 | 4.40 | 4.5/4.5/4.5/4.5/4 | 3-turn에서 30개 table·null ratio·이번 주를 보존 | 최종 scope는 정확. 중간에 이미 알려진 null/deadline 질문을 반복 |
| DUP1 | 3.60 | 2.5/3/4.5/4/4 | “중복이 있습니다. 기존/신규 중 선택” | 실제 중복 key·제목·근거를 보여주지 않아 판단 불가. 자동 유일 실패 |
| ATTR1 | 4.10 | 4.5/4/4.5/4/3.5 | 30분·5분·P1·긴급·hotfix 반영 | 값은 정확하나 DoD 문장 깨짐, 제목에 label이 없다는 무의미한 경고 |
| ASKD4 | 4.80 | 5/5/4.5/5/4.5 | 정확한 parent 관계를 질문 | 관계를 추측하지 않음 |
| ATTR2 | 4.20 | 4.5/3.5/4.5/4.5/4 | `quality-gate` label 정확히 보존 | payload에는 Epic이 없는데 reply는 Epic 관련이라 설명 불일치 |
| STARR1 | 4.50 | 4.5/4.5/5/4.5/4 | Story·DL-102·MVP·9/30·3 Sub-Task | 복합 출력 간 일치 우수 |
| BUG1 | 4.30 | 4/4.5/4.5/4/4.5 | lineage 문제 재현 질문 | actual behavior가 입력에 있는데 다시 물어 question economy 저하 |
| BUG2 | 4.40 | 4.5/4.5/4.5/4.5/4 | Chrome·2 hop·빈 화면·그래프·담당 구조화 | 사실 보존 우수 |
| BUG3 | 4.20 | 3.5/4/4.5/4.5/4.5 | repro·expected·actual을 질문 | 일반 질문은 맞지만 특수 기준인 DAG/batch/environment를 직접 묻지 않음 |
| RULE1 | 4.90 | 5/5/5/5/4.5 | parent Task 변경 또는 Task 취소를 제시 | tier 유효성 정확 |
| RULE2 | 4.60 | 4.5/4.5/5/4.5/4.5 | Story payload, 미지원 Story Point 제외 | field 계약 정확 |

### meeting

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| MTG1 | 4.10 | 4/3.5/4/5/4 | 첫 turn에 모호한 사람·PSR을 모두 질문하고, 답변 후 담당·기한·결정표 작성 | 조사→인터뷰 순서 성공. DL-9201 완료와 “실제 PoC 미수행”이 답변 안에서 충돌하고 최종 공식 URL 누락 |
| MTG2 | 4.20 | 4.5/3.5/4/5/4 | 정확히 3 Task, i2011/x1402/x1103, 세 due, parent DL-9200 | 필수 인터뷰·mapping 성공. 회의에 없는 `Catalog` component를 세 건 모두 추정하고 DoD 문구 일부 중복 |
| MTG3 | 3.50 | 3/4/2.5/5/3 | pending은 DL-9201·9202 comment-only, reviewer x1327 | payload는 정확하지만 reply가 “삭제는 지원되지 않음”, “승인할 초안 없음”이라 모순. 댓글 본문에 “DL-7001에는 댓글을 달지 않음” 메타 문구 포함 |
| MTG4 | 4.60 | 4.5/4.5/5/5/4 | 지정 field와 본문만 update, comment 없음 | exact update 우수. reply에서 component를 빠뜨리고 detail badge suffix가 중복 |
| MTG5 | 4.50 | 4.5/4.5/4.5/5/4 | 준서TL 두 후보·PSR 질문 후 assignee x1103, reviewer x1042, due, Epic 반영 | 조사로 못 푼 공백만 인터뷰하고 초안을 재개한 모범 경로 |

### ctx-chg

| Case | 점수 | 축 | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| CTX1 | 4.50 | 4.5/4.5/5/5/3.5 | 마지막 priority-only update만 pending | 이전 fdc topic을 최종 답변에서 제거. 첫 turn reference marker는 일부 raw 형태 |
| CTX2 | 4.30 | 4.5/4.5/4.5/5/3 | “기억해줘” turn은 조사 없이 종료, 다음 turn에 DL-9090 진척과 모든 child key 출력 | 컨텍스트 경계·효율 개선. badge 전 raw key/title/assignee가 중복 |
| CTX3 | 4.90 | 5/5/5/5/4.5 | priority·due·comment 취소 후 제목만 update | superseded action 제거와 최종 scope가 정확 |
| CTX4 | 3.30 | 3/2.5/3.5/4.5/3 | 최종 pending은 DL-9095 comment-only | 중간 @이다은 업무 질문에 이전 사람의 DL-9090·9095를 잘못 답함. 최종 reply도 “삭제 미지원/초안 없음”이라 payload와 모순 |

## 자동 checker와 사람 판정 불일치

| 유형 | Case | 자동 | 사람 | 판단과 후속 |
|---|---|---:|---:|---|
| aligned red | DUP1 | fail | 3.60 | 실제 중복 후보를 제시하지 않은 실질 결함. checker 유지 |
| false positive | MTG3 | pass | 3.50 | checker가 pending target만 보고 모순된 reply와 댓글 본문의 메타 문구를 놓침. reply/payload 일치 검사가 필요 |
| false positive | CTX4 | pass | 3.30 | checker가 final action만 보고 중간 turn의 사람·티켓 오염과 최종 reply 모순을 놓침. 모든 turn의 entity relevance 검사 필요 |
| false positive | S7 | green | 3.30 | contract 위반 수치만 0이고 외부 공식 근거 누락·PoC 상태 충돌을 놓침. 특수 source coverage와 contradiction 검사 필요 |
| auto green·human weakness | PAR2 | pass | 3.30 | payload shape은 맞지만 답변의 20%·5% 수치 발명을 탐지하지 못함 |

`ctx-chg` 1.0.1 checker는 canonical 단일 action인 `update_ticket/key`를 허용하도록 고친 것뿐이며, CTX4의 중간 turn
실패를 green으로 만들기 위한 완화가 아님. 이번 불일치에 맞춰 과거 raw를 재채점하지 않았고 다음 battery version에서 checker를 보강해야 함.

## 실패·재시도·제한사항

- primary full run 기술 실패 0, 누락 0, silent retry 0
- 자동 실패 1: `DUP1`. create 27/28
- 사람 관점 주요 잔여 결함: S7 외부 조사 결과 누락·상태 모순, MTG3 reply/payload 모순, CTX4 중간 context 오염,
  PAR2 수치 발명, 여러 case의 badge 뒤 중복 정보·깨진 DoD
- 개선 도중 focused closure를 수행했으나 primary evidence를 대체하지 않음:
  - `2026-08-16-en-v8-focused-ctx-r02`: CTX3 pass, CTX2 자동 false negative
  - `2026-08-16-en-v8-focused-ctx-r03`: CTX2 pass, 14.0s·5 calls·19,482 tokens
- 기준선은 서로 다른 시점의 기존 44-case closure와 신규 9-case main run을 합친 composite. 현재는 단일 commit full run이라 실행 일관성은
  더 높지만 baseline과의 차이를 정식 회귀 추정치로 해석할 수 없음
- 단일 reviewer·non-blind·단일 repetition. production 우위 판단에는 같은 battery·commit으로 후보당 5회 이상,
  순서 counterbalance, reviewer 추가 또는 blind review가 필요
- cached token 감소율은 전체 token 감소율보다 크지만 provider cache hit 편차가 있으므로 prompt 효율 저하로 단정하지 않음

## 최종 판단과 다음 개선 우선순위

신규 meeting/context-change 지원은 기준선의 구조적 실패에서 실사용 가능한 수준으로 개선. 특히 조사 후 미해결 공백만 묻는
인터뷰, write 보류, 최신 요청 우선, 취소된 action 제거가 안정화됨. 효율도 전체 시간·token·비용 모두 감소.

다만 전체 평균 +0.10은 신규 9개 개선이 기존 44개의 -0.17을 상쇄한 결과. 다음 개선은 새 role 대개편보다 아래의 좁은 계약 보강이 적절:

1. reply와 pending payload의 action 존재·target·필드·상태를 deterministic하게 대조
2. multi-turn case checker가 final turn만 보지 않고 각 turn의 요청 entity와 응답 entity를 비교
3. 외부 조사를 요구한 case는 최종 답변에 공식 URL·핵심 결과·내부 사실과의 경계를 강제
4. 답변에 새 수치·component·상태를 추가할 때 evidence 존재 여부를 확인
5. ticket/person badge가 가진 제목·담당·상태를 뒤 문장에서 반복하지 않도록 renderer 후처리
6. DUP1은 실제 중복 key·제목·유사 근거를 보여준 뒤 사용자에게 기존/신규 선택을 요청

이번 실행의 목적은 배터리 추가, 확장 기준선 수립, 한 차례 품질·효율 개선, 전체 재측정과 비교까지이며 모두 완료.
잔여 결함은 다음 개선 후보로 기록하되 이 보고서의 점수를 사후 수정하지 않음.

### Raw evidence

- `.cache/agent-evaluation/2026-08-16-en-v8-full-meeting-context-r01/conversation-b2.0.0-r01.json`
- `.cache/agent-evaluation/2026-08-16-en-v8-full-meeting-context-r01/editor-b2.0.0-r01.json`
- `.cache/agent-evaluation/2026-08-16-en-v8-full-meeting-context-r01/create-b3.0.0-r01.json`
- `.cache/agent-evaluation/2026-08-16-en-v8-full-meeting-context-r01/meeting-b1.0.0-r01.json`
- `.cache/agent-evaluation/2026-08-16-en-v8-full-meeting-context-r01/ctx-chg-b1.0.1-r01.json`

Raw 결과는 모든 입력·답변·질문·pending payload·trace·사용량·evaluation evidence를 담고 `.cache/`에만 보존.
Git에는 본 경량 보고서와 battery/checker/fixture만 포함.
