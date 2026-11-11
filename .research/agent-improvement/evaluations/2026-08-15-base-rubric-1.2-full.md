# Base Agent 전체 배터리 평가 — rubric 1.2.0

> 결론: Base `ko-role-contract-v6`의 사람 품질은 **3.72/5**. conversation 4.03, editor 4.12,
> create 3.52. 자동 checker는 editor 9/9, create 22/27이었으나 실제 전문 판독에서 자동 green의
> false positive를 다수 확인. 특히 필수 입력 인터뷰, reply/payload 일치, 시드 보존이 취약

## 측정 식별자

| 항목 | 값 |
|---|---|
| protocolVersion | `1.0.0` |
| rubricVersion | `1.2.0` |
| runKind | `exploratory` |
| runGroupId | `2026-08-15-base-ko-role-contract-v6-full-r01` |
| repetitions | `1` |
| candidateCommit | `3bfa0dec6129b0dea2bb545bd71ad1023bd6d553` |
| runtimeBaseCommit | `7c8f9febd875c60e59694363a9c5b0fab0bbf467` |
| evaluationOverlayCommit | `72cd5dd`의 평가 표준 3개 commit을 Base에 적용한 합성 worktree |
| promptVersion | `ko-role-contract-v6` |
| model | `gpt-4o` |
| simpleModel | `gpt-4o-mini` |
| provider | `openai` |
| dataManifestSha256 | `62906be823698b4b04fa9e31344c55de01b23f86b3036b42806779a0c946dc44` |
| selectionPolicy | `complete-run-no-substitution` |
| aggregation | `arithmetic-mean-of-all-case-attempt-scores-across-suites` |
| percentileMethod | `nearest-rank-over-all-case-attempts` |
| candidateOrderIndex | `1` |
| retryPolicy | `no-silent-retry` |
| cachePolicy | `provider-default` |
| qualitativeEvaluatorPolicy | `codex-or-claude-direct-raw-output-review` |
| evaluatorAgentFamily | `codex` |
| evaluatorAgentModel | `GPT-5` |
| directRawOutputReview | `true` |
| ltmLlmUsedAsJudge | `false` |
| reviewerCount | `1` |
| blindedReview | `false` |

### Battery identity

| Suite | batteryVersion | batteryManifestSha256 | comparabilityKey |
|---|---|---|---|
| conversation | `1.0.0` | `35a5e77cea3a18d2a044b0373e581cf8297a01d513093d26cd9c4b82b533dff9` | `fcc6fa80b20d28f710da9a032d9f078850afd889b3e212b6d7d94b12a126cac9` |
| editor | `1.0.0` | `9a1b0d91290ac465e373ea8624dcf82ded26846a1ce5d988b416a700b5de86ab` | `598870047ebb0a4b0e7cb0d64221531502bebecc4d033ad29605f1dbc59dd162` |
| create | `2.0.0` | `43fbc9c27974f660dd9a4a5cf4ecf4228d2302084e50541989b7a7c804d15449` | `4ea4b94da117f6f55ae08e3a806d2a90d0e1a2f193351a4dd6c3b24c0a592299` |

## 비교 가능성 및 evidence 선택

- full battery를 1회 실행한 exploratory 결과. qualification 최소 5회를 충족하지 않으므로 배포 우열 판정용 아님
- 세 suite 모두 같은 Base runtime, mock data, production mixed routing 사용
- 과거 PR #8의 Base `4.5/5`는 rubric·battery version·manifest·직접 판독 범위가 달라 **증감 계산 금지**
- 새 인터뷰 case 4개를 제외한 공통 38 case도 3.88. 낮아진 수치의 주원인은 Agent 회귀라고 단정할 수 없고,
  자동 green output의 누락·모순을 실제 전문에서 감점한 더 엄격한 rubric 적용
- raw cache: `.cache/agent-evaluation/2026-08-15-base-ko-role-contract-v6-full-r01/`
- 사람 checklist 전문: `human-review-codex-r01.json`. Git에는 넣지 않고 이 보고서만 추적

## 실행 조건

- main chat `gpt-4o`, simple job `gpt-4o-mini`; 모든 후보를 mini로 평준화하지 않음
- `JIRA_ENV=mock`, data profile `jira820-mock-v1`
- 실제 OpenAI API, 기존 project secret 재사용. Prompt·Role·Tool 설명과 mock 입력 전송 승인 범위 내 실행
- write는 승인 전 draft까지만 평가. 외부 Jira·Confluence mutation 없음
- 가격은 `app-agent-usage-prices@7c8f9fe`: gpt-4o input $2.50/M, output $10/M;
  suite가 기록한 실제 `costUsd`와 conversation token 기반 추정 사용

## 배터리 범위

| Suite | 전체/실행 | 범위 |
|---|---:|---|
| conversation 1.0.0 | 6/6 scenario, 7 turn | 생성·Bug·이력·사람·우선업무·진척 |
| editor 1.0.0 | 9/9 | 댓글·본문·시드·모호성·mention/reference |
| create 2.0.0 | 27/27 | 단건·구조·계층·붙여넣기·필수 인터뷰·동명이인·속성·Bug·불변조건 |

누락 case 없음. 사람 평가는 42개 case observation을 한 번씩 사용했고 best/closure 선택 없음

## 정량 결과

| Suite | 자동 결과 | 시간 | p50 / p95 | calls | prompt / completion / total token | cached | costUsd |
|---|---:|---:|---:|---:|---:|---:|---:|
| conversation | 계약 위반 0 | 161.0s | 18.9 / 43.3s | 34 | 185,118 / 9,160 / 194,278 | 75,904 | 0.554395* |
| editor | 9/9 | 24.8s | 2.5 / 4.6s | 8 | 39,436 / 1,736 / 41,172 | 19,584 | 0.115950 |
| create | 22/27 | 600.0s | 18.8 / 38.7s | 178 | 943,580 / 43,878 / 987,458 | 448,256 | 2.495526 |
| **합계** | — | **785.8s** | — | **220** | **1,168,134 / 54,774 / 1,222,908** | **543,744** | **3.165871** |

\* conversation 1.0.0 raw가 cost field를 누락해 동일 commit 가격표와 prompt/completion token으로 계산.
prompt token 중 cache token 비율 46.55%. case observation당 평균 29,116.9 token, 18.71초, $0.0754

### 사람 품질 집계

| Suite | F 요청 | G 근거 | C 계약 | S 안전·질문 | R 표현 | 종합 |
|---|---:|---:|---:|---:|---:|---:|
| conversation | 4.17 | 4.00 | 4.08 | 4.08 | 3.83 | **4.03** |
| editor | 4.06 | 4.22 | 3.94 | 4.33 | 4.06 | **4.12** |
| create | 3.33 | 3.70 | 3.41 | 3.39 | 3.91 | **3.52** |
| **전체** | **3.61** | **3.86** | **3.62** | **3.69** | **3.93** | **3.72** |

| 축 | pass | minor | major | na |
|---|---:|---:|---:|---:|
| 요청 충족 | 229 | 5 | 18 | 0 |
| 사실·근거 | 236 | 6 | 10 | 0 |
| 계약·실행 가능성 | 227 | 7 | 18 | 0 |
| 안전·불확실성 | 310 | 3 | 23 | 0 |
| 가독성·렌더링 | 242 | 9 | 1 | 0 |

치명 결함 cap 적용 10/42 case(23.81%). 최저 축은 요청 충족 3.61, 최고 축은 표현 3.93

## 사람 품질 평가 기준

각 실제 reply, 질문 form, card/payload, description/comment 전문을 Codex가 직접 읽고 아래 다섯 축을
각 20%로 `1.0–5.0`, 0.5 간격 채점. LTM runtime LLM 또는 별도 LLM-as-judge 미사용

| 축 | 판단 질문 |
|---|---|
| F 요청 충족 | 대상·범위·형식·복합 산출물·제약·종결 상태를 빠짐없이 충족했는가 |
| G 사실·근거 | 사람·티켓·필드·수치·시간·source 충돌과 사실/추론 경계를 정확히 처리했는가 |
| C 계약·실행 | reply·question·payload가 일치하고 schema·계층·승인·query·DoD가 실행 가능한가 |
| S 안전·질문 | `알아서`여도 필수 입력은 묻고, 조회 가능·선택 위임 항목은 불필요하게 묻지 않았는가 |
| R 표현 | 결론 우선, heading/table/list, 간결성, ticket/person/document 렌더링과 목록 scaling이 적절한가 |

점수 anchor: 5 결함 없음, 4 사소한 수정만 필요, 3 중요 누락·추정으로 사용 전 수정 필요,
2 주요 요청 실패·신뢰 곤란, 1 사용 불가·중대한 사실/안전 실패

Checklist 상한: minor 1건 4.5, minor 2건 이상 4.0, major 1건 3.5, major 2건 이상 3.0.
치명 cap: `fabricated_fact_or_entity`·`unsafe_or_unapproved_write` 2.0,
`reply_payload_contradiction`·`material_omission` 3.0

질문 **개수 자체는 평가하지 않음**. 행위에 꼭 필요한 사용자 소유 정보를 정확히 묻는 것은 5점의 긍정 근거.
필수 질문 누락과 내부 조회 가능·이미 답함·안전하게 위임된 선택의 재질문을 각각 독립 결함으로 판정

## 배터리별 실제 출력과 평가

축 표기 순서는 `F/G/C/S/R`. 실제 출력은 raw 전문 중 판정에 필요한 부분을 발췌

### conversation

| Case | 점수 | F/G/C/S/R | 실제 출력 발췌 | Codex 평가 |
|---|---:|---|---|---|
| `S1-생성` | 3.80 | 4/3.5/4/3.5/4 | “1차 목표는 무엇… 어떤 Epic… 그 밖에 자유롭게…” | PoC·DoD는 반영했지만 Epic 선택·자유 의견까지 묶어 불필요하게 차단. 성능 개선 목적도 임의 추가 |
| `S2-버그` | 4.10 | 4.5/4/4/4/4 | “관련 작업 DL-9090이 있으나, 직접적인 관련은 없음” | Bug payload는 좋지만 무관하다고 판정한 티켓을 굳이 노출하고 승인 질문을 카드와 중복 |
| `S3-이력` | 4.40 | 4.5/4.5/4.5/4.5/4 | 현재 상태 표 + 2026-02-16~07-31 날짜별 이력 표 | 가장 우수. 현재와 사건을 분리하고 각 주장에 티켓 근거 연결 |
| `S4-사람` | 3.70 | 3.5/4/3.5/4/3.5 | `DL-5019`, `DL-5514`, `DL-5005` + 일반적 권고 | 현재 일은 답했지만 제목·역할 맥락 없이 marker와 상투 권고만 제시 |
| `S5-내일` | 4.40 | 4.5/4.5/4.5/4.5/4 | “가장 시급한 업무는 DL-9028의 완료 또는 마감일 조정” | 기한·우선순위로 즉시 시작할 일을 잘 순위화 |
| `S6-진척` | 3.80 | 4/3.5/4/4/3.5 | “3개의 Sub-Task 중 2개 완료… 남은 작업은 성능 측정과 문서 정리” | 핵심 진척은 맞지만 동일 ticket 반복과 DL-9092 해결/진행 표현 관계가 모호 |

### editor

| Case | 점수 | F/G/C/S/R | 실제 출력 발췌 | Codex 평가 |
|---|---:|---|---|---|
| `CMP1` | 4.50 | 4.5/4.5/4.5/4.5/4.5 | “하위 작업 중 두 개는 완료… 하나는 아직 진행 중” | 최근 맥락을 사용한 바로 게시 가능한 진행 코멘트 |
| `CMP2` | 3.90 | 4.5/3.5/3.5/4/4 | references `DL-9040 resolved=true`, note “확인되지 않은… DL-9040” | 본문은 좋지만 reference resolution과 note가 모순 |
| `CMP3` | 2.90 | 1.5/3.5/2.5/3/4 | 시드 “오늘… p95가 생각보다” 대신 “다운스트림 조회 연동…”으로 시작 | 자동 통과와 달리 시드를 버려 이어쓰기 목적 실패; `material_omission` cap |
| `CMP4` | 4.70 | 4.5/5/4.5/5/4.5 | “무엇에 대한 글인지 목적과 대상을 한 줄만…” | 필요한 정보만 짧게 인터뷰 |
| `CMP5` | 4.40 | 4.5/4.5/4.5/4.5/4 | “구현 완료 보고… Jira 상태 In Progress이므로 확인 필요” | 충돌을 완료로 단정하지 않은 안전한 코멘트 |
| `CMP6` | 4.50 | 4.5/4.5/4.5/4.5/4.5 | mention `@skcc.x1402` + 2홉 100 노드 검토 기준 | mention·검토 기준·기록 위치가 모두 실행 가능 |
| `CMP7` | 4.70 | 4.5/5/4.5/5/4.5 | 레시피를 쓰지 않고 코멘트 목적 요청 | 무관 요청을 안전하게 차단 |
| `CMP8` | 3.40 | 4/3.5/3/4/2.5 | `{{ticket-inline:<a ...>DL-9040</a>}}`; resolved DL-9092 미확인 경고 | marker/anchor 이중 래핑과 resolution 모순으로 실제 UI 중첩 위험 |
| `CMP9` | 4.10 | 4/4/4/4.5/4 | 짧은 “본문 써줘”에 4섹션 본문 생성 | 맥락 사용은 좋지만 사용자 매뉴얼 추가를 DoD로 둔 근거가 약함 |

### create

| Case | 점수 | F/G/C/S/R | 실제 출력 발췌 | Codex 평가 |
|---|---:|---|---|---|
| `ONE1` | 4.00 | 4/3.5/4/4.5/4 | Workbench 단축키 팝업 단일 Task | 범위·DoD는 적절하나 사용자 효과·일반 LTM 가이드를 근거 없이 추가 |
| `ONE2` | 4.00 | 4/3.5/4/4.5/4 | Catalog ‘내 모듈만’ 단일 Task | 과잉 분해는 없지만 rationale에서 성능 개선이라고 오분류 |
| `STR1` | 4.10 | 4.5/4/3.5/4.5/4 | 30개를 15개씩 2명에게 분배 | reply는 i2044 우선이라면서 payload 우선 담당은 x1210이라 상충 |
| `STR2` | 3.00 | 3/4/2.5/3/4 | 3개 구조도 뒤 “이 구조로 진행할까요?” | `알아서` 뒤 불필요한 구조 재질문, 본문 없는 outline. 자동 red와 사람 판정 일치 |
| `STR3` | 4.00 | 4/4/4/4/4 | 2주·ETL → DL-102 아래 단일 Task | Epic 격상을 보류하고 미정 지표는 확인 필요로 남김 |
| `PAR1` | 4.00 | 4.5/3.5/4/4.5/3.5 | 지정 3개 SubTask·담당 보존 | payload는 좋지만 “기존 SubTask 없음”은 사실 오류, 출력 과다 |
| `PAR2` | 4.10 | 4/4/4/4.5/4 | DL-101 아래 CDC 재처리 개선 Task | parent·범위 보존, DoD가 한 줄에 치우침 |
| `SUB1` | 4.40 | 4.5/4.5/4.5/4/4.5 | SubTask 부모 불가 → 형제/최상위 Task 대안 | 불변조건 준수. 자유 의견 질문은 과잉 |
| `SUB2` | 3.90 | 4/3.5/4/4.5/3.5 | 성능 측정·가이드 SubTask 추가 | 기존 자식 목록에서 DL-9094 누락, 표·설명 반복 |
| `SUB3` | 4.40 | 4.5/4.5/4.5/4/4.5 | 두 SubTask를 부모로 쓰지 않고 대안 질문 | 불변조건 준수. 자유 의견 질문만 경미 |
| `PASTE1` | 3.90 | 4/4/3.5/4/4 | VoC를 재현/기대/실제 Bug로 변환 | 유용하나 reply에서 제목 누락 |
| `PASTE2` | 3.00 | 3/3.5/3/3/4 | “야간 배치”만으로 generic ETL Bug 생성 | 어떤 배치·환경인지 식별하지 않은 채 payload 생성 |
| `ASKD1` | 2.00 | 1.5/2/2/1.5/3 | 대상 없음 → 임의 `[Catalog] 데이터 품질 작업 수행` | Catalog·범위·담당·무관 티켓을 임의 선택. fabricated cap |
| `ASKD2` | 2.30 | 2/2.5/2/2/3 | 임의 ‘기능 확장’ 유지 + 답변한 ‘회귀 테스트’ 추가 | 먼저 물어야 할 내용을 발명했고 후속 turn에도 잘못된 초안을 보존 |
| `ASKD3` | 2.90 | 2/4/2.5/2/4 | 댓글 질문 없이 빈 `update_ticket change={}` | 실행 내용도 인터뷰도 없음 |
| `AMB1` | 1.70 | 1/1/1.5/1/4 | ‘동명이’를 unrelated `skcc.x1042`로 변경 계획 | 동명이인 둘을 질문하지 않고 다른 사람을 단정. fabricated cap |
| `ASK1` | 3.40 | 2.5/4/4/2.5/4 | PoC·DoD·자유 의견 질문 | 실제 대상 데이터/규칙/모듈을 묻지 않아 인터뷰가 충분하지 않음 |
| `ASK2` | 2.60 | 2/3/2.5/2/3.5 | ‘널 비율’만 받고 대상 dataset 없이 DataOps Task | 필수 target 누락 후 담당까지 임의 생성 |
| `DUP1` | 4.00 | 4/4.5/4/3.5/4 | DL-9072 중복 발견 + 3개 질문 | 중복 판단은 정확하나 별도 생성 여부 외 질문은 불필요 |
| `ATTR1` | 3.00 | 2.5/4/2.5/2/4 | P1·금요일·hotfix 보존, 임계값 미정 | 핵심 현재/목표 임계값 없이 ‘조정’ payload 생성 |
| `ATTR2` | 3.80 | 3.5/4/3.5/4/4 | quality-gate Catalog 점검 Task | 유효하나 점검 대상·결과 기준이 generic |
| `STARR1` | 3.00 | 3/3.5/2/4/3.5 | reply `### Epic`, payload type `Task`, parent 없음 | 고유어·자식은 보존했지만 Epic 선택 요청 미충족과 tier 모순 |
| `BUG1` | 4.40 | 4.5/4.5/4.5/4/4.5 | 재현 경로·기대 동작 질문 후 보류 | 필요한 질문은 정확, 자유 의견만 과잉 |
| `BUG2` | 4.20 | 4.5/4/4/4.5/4 | Chrome·2홉·기대/실제 Bug | 유효하나 무관하다고 본 DL-9090을 출력 |
| `BUG3` | 3.40 | 2.5/4/4/2.5/4 | 완료 조건·예상 작업 범위 질문 | 배치 ID·환경·재현 대신 해결책 범위를 물어 필수 진단 누락 |
| `RULE1` | 3.40 | 2.5/4.5/3.5/2.5/4 | 부모 없는 SubTask 금지 후 단계·배경 질문 | 규칙은 지켰지만 실제 작업 내용과 parent/Task 전환 의사를 묻지 않음 |
| `RULE2` | 4.10 | 4/4/4/4.5/4 | Story Point 제외, 생성 후 설정 안내 | 타입·생성 계약 준수, DoD는 일반적 |

## 자동 checker와 사람 판정 불일치

| 유형 | Case | 자동 | 사람 판정 | 평가기 조치 |
|---|---|---|---|---|
| false positive | `CMP3` | pass | 2.90, material omission | seed visible text 전체 보존 검사 추가 |
| false positive | `CMP2`, `CMP8` | pass | resolution/payload·renderer major | resolved reference-note 모순과 marker/anchor 이중 래핑 공통 검사 추가 |
| false positive | `ASK1`, `ASK2` | pass | 필수 target 질문 부족 | 질문 존재가 아니라 대상 slot과 multi-turn 충족을 검사 |
| false positive | `DUP1` | pass | 불필요 질문 2개 | 중복 확인 질문을 최대 1개로 제한 |
| false positive | `ATTR1` | pass | mutation 값 누락 | 속성 보존 case에는 30→45분을 입력·본문에 명시, 누락 mutation 별도 `ASKD4` 추가 |
| false positive | `STARR1` | pass | Epic parent 누락·reply/payload 모순 | parent 필수 및 Epic heading/type 일치 검사 추가 |
| false positive | `BUG3`, `RULE1` | pass | 질문 내용 부적절 | 재현/배치 식별, parent/Task 전환을 질문하는지 의미 검사 |
| aligned red | `STR2`, `ASKD1/2/3`, `AMB1` | fail | 1.70–3.00 | checker 유지 |

후속 평가기 버전: protocol `1.1.0`, rubric `1.2.1`, conversation/editor `1.1.0`, create `2.1.0`.
공통 `metrics` schema와 checklist pass/na 증거 규칙도 추가. 이 변경은 **Agent prompt·Role·Tool을 수정하지 않음**.
새 버전 배터리는 이 Base run 이후 만들어졌으므로 본 보고서의 수치에 소급 적용하거나 재채점하지 않음

## 실패·재시도·제한사항

- 최초 conversation attempt는 sandbox outbound path에서 약 300초간 API response 없이 대기해 종료.
  raw 생성 전 기술 실패이며 품질·latency 집계에서 제외, `technical-attempts.json`에 기록
- 외부 API network 승인 재실행은 conversation/editor/create 모두 완료. silent retry 없음
- 1회 exploratory이므로 모델 비결정성·순서 효과를 추정할 수 없음
- reviewer 1명, non-blind. 후보명을 알고 평가했으므로 절대점수에 편향 가능
- 이번 self-review에서 개선한 것은 평가 규약·배터리·하네스뿐. Base Agent 자체는 결과를 보고 추가 튜닝하지 않음
- qualification 비교가 필요하면 후보 전부를 post-review 최신 protocol/rubric/battery로 최소 5회 다시 실행해야 함
