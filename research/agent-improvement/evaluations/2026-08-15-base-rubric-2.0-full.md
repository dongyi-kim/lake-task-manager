# Base Agent 전체 배터리 평가 — rubric 2.0.0

> 결론: Base `ko-role-contract-v6`의 사람 품질은 **3.75/5**. conversation 3.97, editor 3.96,
> create 3.63. 이번 점수는 공통 5축에 case별 특수 검토요소를 합산한 최초 결과. 이력 S3는 8개 티켓과
> 사건을 모두 복원했지만, 내·외부 조사 S7은 외부 조회 부재·내부 핵심 사실 누락·잘못된 출처 설명으로
> 2.70. 생성에서는 필수정보 인터뷰 실패가 여전히 가장 큰 품질 손실

## 측정 식별자

| 항목 | 값 |
|---|---|
| protocolVersion | `2.0.0` |
| rubricVersion | `2.0.0` |
| runKind | `exploratory` |
| runGroupId | `2026-08-15-base-rubric-2.0-full-r01` |
| repetitions | `1` |
| candidateCommit | `8b18b23e6179488373e53ef234d79bc2bccff596` |
| candidateDirty | `false` |
| promptVersion | `ko-role-contract-v6` |
| model | `gpt-4o` |
| simpleModel | `gpt-4o-mini` |
| provider | `openai` |
| dataProfile | `jira820-mock-v1` |
| dataManifestSha256 | `3084e7a7994fa1726515ddfd124fec70b114b3b8d21caa0df639b6d34946f93b` |
| selectionPolicy | `complete-run-no-substitution` |
| attemptPolicy | `retain-every-attempt` |
| aggregation | `arithmetic-mean-of-all-case-attempt-scores-across-suites` |
| percentileMethod | `nearest-rank-over-all-case-attempts` |
| candidateOrderIndex | `1` |
| retryPolicy | `no-silent-retry` |
| cachePolicy | `cold-private-cache-each-case` |
| processIsolation | `separate-process-private-cache` |
| qualitativeEvaluatorPolicy | `codex-or-claude-direct-raw-output-review` |
| evaluatorAgentFamily | `codex` |
| evaluatorAgentModel | `GPT-5` |
| directRawOutputReview | `true` |
| ltmLlmUsedAsJudge | `false` |
| reviewerCount | `1` |
| blindedReview | `false` |

### Battery identity

| Suite | batteryVersion | batteryManifestSha256 | specializedReviewSpecSha256 | comparabilityKey |
|---|---|---|---|---|
| conversation | `2.0.0` | `799d045350bd36e93be4fed1564bf722a2ab435de148a4357e885576b7ed7203` | `0d9b63458fbae238c4813865baaa8d9093f3fb1c670d55833d2604e6ac0e87ce` | `b48cf5b96a3b48c953f1310ced596b1fa83c1337c3698927786b01c97e4b66c0` |
| editor | `2.0.0` | `70567cbf773208f30b91d17d33477fd5da490877ad8bc7f1cc67a9748d0d7eea` | `b6f81a25c0c9697c2e57f06706c385a196542ddf78c87731cd51e7eac2475c1b` | `759013090ccc484aac545b163e8edb825c41a3508306ae54f47d926aa6316cd0` |
| create | `3.0.0` | `e7bfd58d9a5ee3d6eb9d32dcc16e094770d35cf9166d6423711320dfcc70490e` | `83ec3e40f31167216cf9033b1e55d5f63d076f982a562366c78bc29931d3b363` | `73b696c3d0fe082a88981108a2f88ddbeffefe8ae17d06c0cca4e4209f4257a5` |

## 비교 가능성 및 evidence 선택

- full battery 1회 exploratory 결과. qualification 최소 5회를 충족하지 않아 배포 우열 판정용 아님
- 이전 Base 3.72 보고서는 protocol 1.0.0/rubric 1.2.0, 다른 battery·manifest·data hash를 사용.
  **3.72 → 3.75 증감 계산 금지**. 방향성 참고만 가능
- primary evidence는 각 suite의 완전 실행 1회. 자동 closure, best-of, focused 재실행으로 대체하지 않음
- 실제 reply·질문 form·card/payload·Editor HTML·reference와 `evaluationEvidence`를 Codex가 직접 판독.
  LTM의 gpt-4o/gpt-4o-mini는 평가 대상일 뿐 judge로 사용하지 않음
- raw cache: `.cache/agent-evaluation/2026-08-15-base-rubric-2.0-full-r01/`

## 실행 조건

- production mixed routing 재사용: main chat `gpt-4o`, simple job `gpt-4o-mini`
- `JIRA_ENV=mock`, 실제 OpenAI API. 승인된 prompt·Role·Tool 설명과 mock 입력만 전송
- write는 승인 전 draft/pending까지만 평가. 외부 Jira·Confluence mutation 없음
- conversation → editor → create 순차 실행. suite 간 API 경쟁을 피하고 각 suite를 별도 process로 시작
- 가격은 runtime usage의 실제 `costUsd` 합계. provider 가격표가 raw에 고정되지 않아 장기 가격 비교는 제한

## 배터리 범위

| Suite | 전체/실행 | 범위 |
|---|---:|---|
| conversation 2.0.0 | 7/7 scenario, 8 turn | 생성·Bug·완전 이력·사람·우선업무·진척·내외부 조사 |
| editor 2.0.0 | 9/9 | 댓글·본문·seed·모호성·status conflict·mention/reference·부모/자식 경계 |
| create 3.0.0 | 28/28 | 단건·분해·계층·붙여넣기·필수 인터뷰·동명이인·중복·속성·Bug·불변조건 |

총 44개 case observation. 누락·선택 대체 없음

## 실행 격리

| 검토 항목 | 실행 결과 | 판정 |
|---|---|---|
| suite process | conversation `11452`, editor `6388`, create `14356` | 3개 별도 process |
| process cache | `conversation-11452.sqlite3`, `editor-6388.sqlite3`, `create-14356.sqlite3` | suite 간 비공유 |
| case cache/state reset | TTL cache·snapshot·recent·LangGraph·approval·identity·provider를 case 전 초기화 | 44/44 적용 |
| mock world | `worldSha256Before == worldSha256After` | 44/44 보존 |
| 실제 jira820 Store | `providerStoreSha256Before == providerStoreSha256After`, issue count 1,603 | 44/44 보존 |
| background refresh | 평가 process에서 SWR 비활성 | 다음 case 지연 write 차단 |
| raw suite 경로 | conversation/editor/create 파일명 분리 | suite 간 덮어쓰기 없음 |
| 동일 attempt 재실행 | 기존 구현은 같은 run group·suite·repeat를 덮어쓸 수 있었음 | 발견 후 `.claim` 배타 예약·기존 raw 거부로 수정 |

검토 결론: **이번 44개 결과에서는 cache·conversation state·mock world·provider Store 오염이 없음**.
다중 turn case의 thread만 같은 case 안에서 유지. provider-side prompt cache는 client에서 끌 수 없어
`cachedTokens`로 분리 기록했으며, 이는 주로 token/time 비교의 제한사항. candidate 간 qualification에서는
순서를 counterbalance해야 함

## 정량 결과

| Suite | 자동 결과 | 시간 | p50 / p95 | calls | prompt / completion / total token | cached | costUsd |
|---|---:|---:|---:|---:|---:|---:|---:|
| conversation | 계약·근거 위반 0 | 175.2s | 18.9 / 44.6s | 41 | 214,131 / 10,538 / 224,669 | 125,312 | 0.640708 |
| editor | 5/9 | 27.1s | 3.0 / 5.0s | 8 | 39,436 / 1,824 / 41,260 | 19,584 | 0.116830 |
| create | 15/28 | 674.7s | 22.2 / 45.3s | 191 | 995,865 / 47,051 / 1,042,916 | 488,960 | 2.960172 |
| **합계** | — | **877.0s** | — | **240** | **1,249,432 / 59,413 / 1,308,845** | **633,856** | **3.717710** |

prompt token 중 cached token 50.73%. case observation당 평균 29,746.5 token, 19.93초, $0.0845.
Create가 전체 token의 79.68%, 비용의 79.62%를 차지

### 사람 품질 집계

| Suite | F 요청 | G 근거 | C 계약 | S 안전·질문 | R 표현 | 종합 |
|---|---:|---:|---:|---:|---:|---:|
| conversation | 4.21 | 3.79 | 4.00 | 4.21 | 3.64 | **3.97** |
| editor | 4.00 | 4.00 | 3.83 | 4.33 | 3.61 | **3.96** |
| create | 3.52 | 3.66 | 3.55 | 3.55 | 3.84 | **3.63** |
| **전체 44 case** | **3.73** | **3.75** | **3.68** | **3.82** | **3.76** | **3.75** |

## 사람 품질 평가 기준

각 실제 출력을 Codex가 직접 읽고 아래 다섯 축을 각 20%, `1.0–5.0`에서 0.5 간격으로 채점.
case 점수는 다섯 축 산술평균. suite/전체는 모든 case observation 산술평균

| 축 | 판단 질문 |
|---|---|
| F 요청 충족·완결성 | 실제 의도·전체 범위·복합 항목·제약·결론을 빠짐없이 닫았는가 |
| G 사실성·근거성 | entity·field·건수·시간·source conflict와 사실/추론 경계가 실제 근거와 일치하는가 |
| C 계약·실행 가능성 | reply/question/card/payload가 일치하고 schema·계층·승인·조회·DoD가 실행 가능한가 |
| S 안전·불확실성 | 필수정보는 묻고, 조회 가능·이미 답함·안전하게 위임된 선택은 되묻지 않으며 side effect를 통제했는가 |
| R 표현·렌더링 | 결론 우선·heading/table/list·간결성·ticket/person/document marker와 목록 scaling이 적절한가 |

점수 anchor: 5 결함 없음, 4 사소한 수정만 필요, 3 중요한 누락·추정으로 사용 전 수정 필요,
2 주요 요청 실패·신뢰 곤란, 1 사용 불가·중대한 사실/안전 실패

모든 공통 checklist와 특수 요소를 `pass/minor/major/na`로 판정. 같은 축의 공통·특수 결과를 합쳐
상한 적용: 전부 pass 5.0, minor 1건 4.5, minor 2건 이상 4.0, major 1건 3.5,
major 2건 이상 3.0. 특수 요소는 별도 여섯 번째 점수가 아니라 지정된 기존 축을 구체화

## 배터리·case 특수 검토요소

표의 약어와 순서는 다음과 같음. `P/m/M/n`은 pass/minor/major/na

- conversation: `조회/근거/케이스 고유 요소`
- editor: `맥락보존/참조충실/케이스 고유 요소`
- create: `요청↔payload/인터뷰경계/domain형상/케이스 고유 요소`

히스토리는 기대 ticket key와 사건 순서를, 내·외부 조사는 source class·검색어·URL·내부 사실·구분 section을,
생성은 turn별 질문과 실제 pending field를, Editor는 visible seed와 marker/reference를 직접 대조

## 배터리별 실제 출력과 평가

축 표기 순서는 `F/G/C/S/R`. 실제 출력은 판정에 필요한 부분을 발췌. 특수 판정에서 생략한 공통 checklist는
출력 전문과 raw evidence를 기준으로 적용 가능한 항목 pass, 해당 개념이 없는 항목 na로 처리

### conversation

| Case | 점수 | F/G/C/S/R | 실제 출력 발췌 | 특수 판정 | Codex 평가 |
|---|---:|---|---|---|---|
| `S1-생성` | 3.80 | 4.5/3/4/4/3.5 | “PoC… Iceberg 배치적재 테이블… Batch Job” + 설계/구현/검증 3 Sub-Task | `m/m/M/P` | 핵심 산출물·단계는 보존. 외부 기술 검색 없이 “분석 정확성 향상”을 주장하고, 직접 관련 DL-7001을 비관련으로 축소 |
| `S2-버그` | 4.30 | 4.5/4/4.5/4.5/4 | Chrome → 2홉 확장 → 빈 화면 / 기대 그래프 렌더 | `P/m/P` | 재현·기대·실제가 분리된 실행 가능한 Bug. 무관하다고 본 DL-9090을 근거에 남긴 점만 경미 |
| `S3-이력` | 4.80 | 5/5/4.5/5/4.5 | `DL-9041`~`DL-9047`, `DL-9062`; 요청→Job→지연→2h→30m→schema→catalog→monitoring→정합성 | `P/P/P/P` | 특수 계약이 의도한 모범 사례. 현재 상태와 날짜순 사건을 분리하고 8개 티켓을 모두 정확한 사건에 연결 |
| `S4-사람` | 3.50 | 3.5/3.5/3.5/4/3 | “이다은 책임의 현재 업무” 아래 3개 ticket-list | `P/m/M` | 실제 업무 3건은 제시했으나 전체 건수·subset 기준·생략 수가 없어 “현재 맡은 일 전체”인지 검증 불가. 사람 mention도 없음 |
| `S5-내일` | 4.40 | 4.5/4.5/4.5/4.5/4 | P1·마감 초과 `DL-9028`을 1순위, 오늘/내일 마감 후보를 2·3순위 | `P/P/P` | priority·due·status를 함께 비교해 한 가지 최우선 결정을 명확히 제시 |
| `S6-진척` | 4.30 | 4.5/4/4.5/4.5/4 | “하위 3개 중 2개 완료… DL-9095 진행 중… 성능 측정과 문서 정리” | `P/m/m` | child 집계와 남은 티켓 정확. 댓글의 구현 완료 보고와 Jira In Progress를 source별로 더 명시했어야 함 |
| `S7-내외부조사` | 2.70 | 3/2.5/2.5/3/2.5 | “내부 조사 완료… 외부 확인 필요”; webContext 없음; DL-7001을 “2시간→30분 근거”로 표시 | `M/M/M/M/M` | 외부 공식 자료 조회·URL 전무. 내부 문서의 후보 20개·writer 확인을 누락하고, 관련 없는 주기 변경 설명을 출처에 붙인 중대 실패 |

### editor

| Case | 점수 | F/G/C/S/R | 실제 출력 발췌 | 특수 판정 | Codex 평가 |
|---|---:|---|---|---|---|
| `CMP1` | 4.50 | 4.5/4.5/4.5/4.5/4.5 | “구현 완료 보고가 있으나 Jira 상태가 In Progress” | `P/P/P` | source conflict를 확정 완료로 뒤집지 않고 남은 성능·문서 작업까지 연결 |
| `CMP2` | 3.30 | 3.5/3.5/3/4/2.5 | `{{ticket-inline:<a …>DL-9040</a>}}` | `m/M/m` | marker 안에 렌더된 anchor를 다시 넣어 UI 중첩. 사용자 경험·사용자 테스트 효익도 source 없이 추가 |
| `CMP3` | 4.10 | 4.5/4/4/4/4 | seed “p95 가 생각보다” → “p95가 생각보다 높게” | `m/P/m` | 공백 한 칸 정규화로 자동 fail이지만 의미·수치는 보존. 담당자 검토까지 자의로 확장한 점은 경미 |
| `CMP4` | 4.70 | 4.5/5/4.5/5/4.5 | “무엇에 대한 글인지 목적과 대상을 한 줄만…” | `P/n/P` | 정보 없이 본문을 만들지 않고 필요한 대상·목적만 인터뷰 |
| `CMP5` | 4.40 | 4.5/4.5/4.5/4.5/4 | “완료 보고… Jira 상태 In Progress이므로 확인 필요” | `P/n/P` | 짧은 요청에서도 상태 충돌 보존. 말미 상투 문장만 경미 |
| `CMP6` | 4.10 | 4.5/4/4/4.5/3.5 | mention badge + “2홉 100 노드” 검토 요청 | `P/m/m` | 사람 mention과 검토 기준은 정확하나 “설계 문서”를 말하면서 canonical document link/reference 누락 |
| `CMP7` | 4.70 | 4.5/5/4.5/5/4.5 | 김치찌개를 쓰지 않고 현재 ticket의 comment 목적 질문 | `P/n/P` | 무관 요청을 안전하게 차단 |
| `CMP8` | 3.20 | 3/3/3/4/3 | 부모 DoD에 그래프 렌더·업/다운스트림 2홉 등 자식 실행 항목 반복 | `M/m/M` | 부모/자식 책임 분리 실패, 3홉 제외·2차 최적화도 발명. resolved DL-9092를 미확인으로 경고 |
| `CMP9` | 2.60 | 2.5/2.5/2.5/3.5/2 | “사용자 피드백… 만족도 목표값 달성” + 이중 marker | `M/M/M` | 확인되지 않은 UX·만족도 목표를 DoD로 발명하고 marker 중첩·resolved 경고까지 발생 |

### create

| Case | 점수 | F/G/C/S/R | 실제 출력 발췌 | 특수 판정 | Codex 평가 |
|---|---:|---|---|---|---|
| `ONE1` | 4.20 | 4.5/4/4/4.5/4 | Workbench 단축키 팝업 단일 Task | `P/P/P/m` | 과잉 분해 없음. 미확인 사용자 효익·generic 사용자 테스트만 경미 |
| `ONE2` | 4.30 | 4.5/4/4.5/4.5/4 | Catalog ‘내 모듈만’ checkbox Improvement 1건 | `P/P/P/P` | 표시·필터 동작과 원자적 구조 유지 |
| `STR1` | 4.50 | 5/4.5/4.5/4.5/4 | 30개를 15개씩 두 Sub-Task로 분할 | `P/P/P/P` | child 합계·중복·누락 규칙과 서로 다른 담당자 배치 충족 |
| `STR2` | 3.20 | 3/3.5/2.5/3.5/3.5 | 3개 모듈 구조도는 있으나 실제 draft 본문 3건이 사실상 비어 있음 | `M/m/P/M` | 겉보기 분해만 있고 승인 payload가 실행 가능하지 않음. “각기 다른 Epic”도 근거 없음 |
| `STR3` | 3.80 | 3.5/4/3.5/4/4 | `DL-102` 아래 Task 또는 새 Epic 선택 질문 | `m/m/P/m` | 중복 Epic 생성은 보류했지만 기존 Epic 아래 보수적 draft로 닫지 못하고 목표·DoD까지 과잉 질문 |
| `PAR1` | 4.30 | 4.5/4/4.5/4.5/4 | DL-9090 아래 성능·가이드·회귀 3 Sub-Task와 서로 다른 담당자 | `P/P/P/P` | parent·업무-담당 매핑 정확. 일부 미정 목표값은 open fact로 남김 |
| `PAR2` | 3.90 | 4/3.5/3.5/4.5/4 | CDC 개선 Task의 `epic=DL-101` | `m/P/P/m` | key는 보존했으나 조회한 실제 Epic 제목을 reply/payload에서 확인할 수 없음 |
| `SUB1` | 4.30 | 4.5/4/4.5/4.5/4 | 형제 Sub-Task / 최상위 Task / 취소 선택 | `P/P/P/P` | Sub-Task를 parent로 쓰지 않고 합법적 대안 제시 |
| `SUB2` | 4.20 | 4.5/3.5/4.5/4.5/4 | DL-9090 아래 성능 측정·가이드 2건 | `P/P/P/m` | 요청 자식은 중복 없이 생성. 기존 child 설명에서 DL-9094 누락 |
| `SUB3` | 4.10 | 4/3.5/4.5/4.5/4 | 두 대상 모두 Sub-Task라 생성 보류 | `P/P/P/m` | hierarchy 안전. 완료된 DL-9093/9094를 진행 중이라고 잘못 설명 |
| `PASTE1` | 4.10 | 4/4/4/4.5/4 | VoC를 재현·기대·실제 Bug로 변환 | `P/P/P/m` | 핵심 불편을 구조화했으나 재현 경로가 “조회 화면에서 확인” 수준으로 다소 추상적 |
| `PASTE2` | 4.30 | 4.5/4/4.5/4.5/4 | prod `dag_etl_nightly`, timeout, 매일 재발, 재실행 성공 | `P/P/P/m` | 핵심 장애 사실 보존. 대화 시각 10:12~10:15를 본문에서 생략 |
| `ASKD1` | 2.00 | 1.5/2/2/1.5/3 | 대상 없이 `[DataOps] 데이터 품질 작업 수행`과 담당자 생성 | `M/M/P/M` | `알아서`를 대상 위임으로 오해. 범위·품질 규칙을 묻지 않고 generic draft 발명 |
| `ASKD2` | 1.90 | 1.5/2/1.5/1.5/3 | 첫 turn에 임의 “기능 개선”; 답변 후에도 그 항목 + 회귀 테스트 2건 유지 | `M/M/P/M` | 질문 없이 조기 draft, 기존 child 없음이라는 사실 오류, 후속 답을 정확한 단일 Sub-Task로 수렴하지 못함 |
| `ASKD3` | 2.60 | 2/3.5/2/1.5/4 | 질문 없이 빈 `update_ticket` plan | `M/M/P/M` | 댓글 목적·내용을 묻지 않아 실행 내용도 안전한 pending도 없음 |
| `AMB1` | 1.60 | 1/1.5/1.5/1/3 | “알아서… 확인 가능한 동명이로 변경” | `M/M/P/M` | `test.same01/test.same02`를 제시하지 않고 임의 식별. 가장 위험한 사람 mutation 실패 |
| `ASK1` | 3.60 | 3/4/4/3/4 | 범위·완료 조건은 질문하지만 dataset/table/품질 규칙은 묻지 않음 | `m/M/P/M` | draft 보류는 안전하나 작업 target을 확정할 질문 부족 |
| `ASK2` | 2.60 | 2/3/2.5/2/3.5 | 두 번째 turn부터 “널 비율 체크” draft; 세 번째의 신규 30개 table 범위 미반영 | `M/M/P/M` | 필수 target 전 조기 draft와 후속 답 누락 |
| `DUP1` | 3.90 | 4/4.5/4/3/4 | DL-9072 중복 발견 후 범위·계기·자유 의견 3문항 | `P/M/P/M` | 중복 판단은 정확하지만 먼저 물어야 할 계속/중단 결정 대신 후순위 질문으로 진행 차단 |
| `ATTR1` | 3.70 | 3.5/4/3/4/4 | reply에는 30→45분·P1·금요일·hotfix, payload 본문은 문서 업데이트 중심 | `M/P/P/M` | 명시 field는 보존했지만 실제 mutation 값이 description/DoD에 일관되게 남지 않음 |
| `ASKD4` | 2.60 | 2/3.5/2/1.5/4 | 새 threshold 질문 없이 “임계값 조정” Task 생성 | `M/M/P/M` | 우선순위·기한이 있어도 핵심 변경값 없이는 draft를 만들면 안 됨 |
| `ATTR2` | 4.30 | 4.5/4/4.5/4.5/4 | 신규 label `quality-gate` 그대로 보존 | `P/P/P/P` | 없는 label을 거절·대체하지 않고 실행 가능한 신규 값으로 유지 |
| `STARR1` | 2.90 | 2.5/3/2.5/3/3.5 | StarRocks/Puffin/NDV는 유지하나 Epic 없음, 범위는 원본 수집, 단계 중복 | `M/m/P/M` | 사용자가 위임한 Epic 선택과 “최소 기능 1차 구현”을 payload가 충족하지 못함 |
| `BUG1` | 4.40 | 4.5/4.5/4.5/4.5/4 | 재현 경로·기대/실제 질문 후 draft 보류 | `P/P/P/P` | 필요한 진단 정보만 확보한 안전한 처리. 자유 의견 질문만 경미 |
| `BUG2` | 4.30 | 4.5/4/4.5/4.5/4 | Chrome·2홉·빈 화면·그래프 기대를 Bug로 분리 | `P/P/P/m` | 재현 구조 정확. 관련 DL-9090을 본문 reference로 명시하지는 않음 |
| `BUG3` | 4.00 | 4/4/4/4/4 | 재현 경로와 기대 동작 질문 | `P/m/P/m` | 조기 Bug 생성은 막았지만 DAG/batch/environment 식별 질문이 더 구체적이어야 함 |
| `RULE1` | 3.60 | 3/4.5/3.5/3/4 | parent 없음은 거부했으나 범위·배경만 질문 | `m/M/P/M` | hierarchy 위반은 차단. parent Task 선택/최상위 Task 전환/취소 선택을 실제 질문하지 않음 |
| `RULE2` | 4.30 | 4.5/4/4.5/4.5/4 | Story 생성, payload에서 Story Point 제외 | `P/P/P/P` | 미지원 field를 넣지 않고 생성 후 UI 설정 필요성을 투명하게 설명 |

## 자동 checker와 사람 판정 불일치

| 유형 | Case | 자동 | 사람 판정 | 의미 |
|---|---|---|---|---|
| false negative | `CMP3` | fail | 4.10 | seed의 `p95 가` 공백을 `p95가`로 정규화한 것을 exact checker가 실패 처리. 의미 보존 관점에서는 minor |
| missing coverage | `S7` | 계약 위반 0 | 2.70 | conversation 공통 checker가 외부 source 부재·내부 사실 누락·잘못된 source 설명을 잡지 못함. 특수 요소가 포착 |
| aligned red | `CMP2`, `CMP8`, `CMP9` | fail | 2.60–3.30 | marker/reference·부모/자식 경계 결함을 자동·사람 모두 확인 |
| aligned red | `STR2`, `ASKD1/2/3`, `AMB1`, `ASK2`, `ASKD4`, `STARR1` | fail | 1.60–3.20 | 빈/모순 payload, 필수 인터뷰 누락을 자동·사람 모두 확인 |
| conservative red | `STR3`, `ASK1`, `DUP1`, `ATTR1`, `RULE1` | fail | 3.60–3.90 | 안전 경계는 일부 지켰지만 case의 구체 질문·payload 계약까지는 미충족 |

자동 checker를 이번 결과에 맞춰 수정하거나 Agent prompt를 튜닝하지 않음. 이 보고서는 새 평가 기준의 동작을
검토하기 위한 Base 측정이며, 이후 checker 변경은 battery version을 올린 새 결과에서만 반영

## 실패·재시도·제한사항

- 최초 conversation invocation은 shell 제한을 실수로 10초로 설정해 중단. raw 생성 전 기술 실패,
  품질·latency 집계 제외. 별도 process `20324`와 private cache였고 성공 run `11452`에 state가 전달되지 않음.
  기록은 ignored `technical-attempts.json`에 보존
- silent model retry 없음. 각 suite primary run은 전 case 완료
- raw overwrite 검토에서 같은 run 식별자 재사용 취약점을 발견. 평가 infra에 유료 호출 전 `.claim` 배타 예약과
  기존 raw 거부를 추가하고 unit test로 재현 방지. 이번 결과 파일은 처음부터 suite별 고유 경로여서 영향 없음
- provider-side prompt cache를 완전히 비울 수 없어 cached 633,856 token을 별도 기록. 1회·고정 순서이므로
  latency/token의 candidate 우열을 단정할 수 없음
- reviewer 1명, non-blind. 후보와 case 기대를 알고 평가해 절대점수 편향 가능
- 이번 단계에서는 평가 규약·battery·격리 harness·보고서만 개선. Base Agent prompt·Role·Tool runtime은 결과를
  보고 튜닝하지 않음
- qualification 비교는 모든 후보를 동일 protocol/rubric/manifest로 최소 5회, 후보 순서 counterbalance 후 수행

## 평가 기준 피드백 요청

새 기준이 특히 잘 드러낸 부분은 `S3`의 **정확한 ticket/event coverage**, `S7`의 **내부/외부 source와 검색
실행 여부**, create의 **질문 내용과 payload 보류 경계**, Editor의 **marker/reference 실제 렌더링**.
검토가 필요한 지점은 `CMP3`처럼 의미 보존과 글자 단위 exactness가 충돌할 때 minor로 볼지 pass로 볼지,
그리고 S7처럼 여러 특수 major가 같은 사실성 축에 몰릴 때 현재 상한 방식이 충분히 엄격한지 여부
