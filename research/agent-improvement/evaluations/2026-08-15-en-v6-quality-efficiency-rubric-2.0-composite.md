# English Agent v6 품질·효율 개선 평가 — rubric 2.0.0

> 결론: 사람이 실제 출력과 실행 근거를 직접 판독한 품질은 **4.48/5**. 이전 English v5의
> 4.18 대비 +0.30. 정량 지표는 708.0초, 217 calls, 1,024,445 tokens, $2.935185로 이전
> 1,234.1초, 232 calls, 1,172,811 tokens, $3.354816보다 각각 42.63%, 6.47%, 12.65%,
> 12.51% 감소. 다만 사용자의 "성공분은 재실행하지 말고 실패분만 이어서 실행" 지시에 따른
> **exploratory closure composite**이므로 qualification 결과나 단일 commit full-run으로 해석하면 안 됨

## 측정 식별자

| 항목 | 값 |
|---|---|
| protocolVersion / rubricVersion | `2.0.0` / `2.0.0` |
| runKind | `exploratory-closure-composite` |
| final candidateCommit | `85bdf4e0d77dee078130aee84499ba281bc56d67` |
| promptVersion | `en-role-contract-v6` |
| baseline | `en-role-contract-v5`, commit `4791f52d74bd922009d97e167d398f1c17aad6df` |
| model routing | main `gpt-4o`, simple `gpt-4o-mini` |
| provider / data | actual OpenAI API / `jira820-mock-v1` |
| dataManifestSha256 | `3084e7a7994fa1726515ddfd124fec70b114b3b8d21caa0df639b6d34946f93b` |
| cache / isolation | case별 cold private cache / separate process / mock world mutation 없음 |
| qualitative evaluator | Codex가 raw output과 evaluation evidence를 직접 판독 |
| LTM LLM judge | 사용하지 않음 |
| reviewer / blind | 1명 / non-blind |
| qualificationEligible | `false` — selective closure, 1 repetition, mixed candidate commits |

Battery identity는 baseline과 동일

| Suite | batteryVersion | batteryManifestSha256 | specializedReviewSpecSha256 |
|---|---|---|---|
| conversation | `2.0.0` | `799d045350bd36e93be4fed1564bf722a2ab435de148a4357e885576b7ed7203` | `0d9b63458fbae238c4813865baaa8d9093f3fb1c670d55833d2604e6ac0e87ce` |
| editor | `2.0.0` | `70567cbf773208f30b91d17d33477fd5da490877ad8bc7f1cc67a9748d0d7eea` | `b6f81a25c0c9697c2e57f06706c385a196542ddf78c87731cd51e7eac2475c1b` |
| create | `3.0.0` | `e7bfd58d9a5ee3d6eb9d32dcc16e094770d35cf9166d6423711320dfcc70490e` | `83ec3e40f31167216cf9033b1e55d5f63d076f982a562366c78bc29931d3b363` |

## 평가 방법

공통 5축을 각각 20%, 1.0–5.0에서 0.5 간격으로 채점. case 점수는 5축 산술평균,
suite와 전체는 case를 같은 가중치로 산술평균

| 축 | 구체적 판단 질문 |
|---|---|
| F 요청 충족·완결성 | 핵심 의도, 전체 범위, 복합 요구, 수치·제약, 결론·다음 행동을 누락 없이 닫았는가 |
| G 사실성·근거성 | entity·field·건수·시간·사람·source가 실제 조회 결과와 일치하고 사실/추론/미확인을 구분했는가 |
| C 계약·실행 가능성 | reply, question, card, payload가 서로 일치하고 tier·type·parent·field·승인·DoD가 실행 가능한가 |
| S 안전·불확실성 | 꼭 필요한 미확정 정보는 묻고, 불필요한 질문·자의적 가정·side effect·완료 ticket 불법 수정은 막았는가 |
| R 표현·렌더링 | 결론 우선, 짧은 문장, heading/table/list, ticket/person/document marker, 목록 규모별 badge가 적절한가 |

점수 anchor: 5는 실사용 전 수정 불필요, 4는 사소한 수정만 필요, 3은 중요한 누락·추정으로 수정 필요,
2는 주요 요청 실패, 1은 사용 불가 또는 중대한 사실·안전 실패. 공통 checklist에 더해 case별 특수 검토요소를
함께 적용. 예를 들어 S3는 8개 ticket과 사건 순서, S7은 내부 source·외부 query·공식 URL·적용 inference,
생성 battery는 질문 시점과 실제 pending payload를 직접 대조

## evidence 선택과 비교 가능성

- 최초 v6 실행에서 통과한 결과는 그대로 유지하고, 자동 또는 사람 판독에서 실패한 case만 수정 후 재실행
- 최종 선택은 best-of가 아니라 **해당 case의 수정 이후 최초 human-pass closure**. 실패 attempt도 raw cache에 보존
- conversation은 최초 full 결과 중 S1–S6을 유지하고, 외부 근거가 부족했던 S7만 최종 기술검색 결과로 교체
- editor는 최초 full 9건 유지
- create는 최초/재개 실행의 성공분을 유지하고, DUP1·STR3·STARR1·PASTE2·ASKD2·ASKD4·BUG1·BUG2를
  이후 최초 human-pass 결과로 교체
- 따라서 최종 commit의 모든 case를 같은 시각에 실행한 결과가 아님. 동일 rubric의 방향성·closure 판단에는
  유효하지만 배포 후보 간 통계적 우열 판정에는 부적합

## 정량 결과

| Suite | 자동 결과 | 시간 | p50 / p95 | calls | prompt / completion / total token | cached | costUsd |
|---|---:|---:|---:|---:|---:|---:|---:|
| conversation | 근거·후검증 위반 0 | 112.4s | 12.3 / 24.8s | 37 | 159,393 / 9,468 / 168,861 | 82,560 | 0.493165 |
| editor | 9/9 | 22.3s | 2.9 / 3.6s | 8 | 38,852 / 1,578 / 40,430 | 24,320 | 0.112910 |
| create | 28/28 | 573.3s | 17.3 / 36.0s | 172 | 776,324 / 38,830 / 815,154 | 543,104 | 2.329110 |
| **합계** | **전체 선택 case 자동 통과** | **708.0s** | — | **217** | **974,569 / 49,876 / 1,024,445** | **649,984** | **2.935185** |

45 turn observation 기준 평균 15.73초, 22,765 token, $0.06523. Create가 total token의 79.57%,
비용의 79.35%로 여전히 가장 큰 비용원

### English v5 대비

| 지표 | v5 baseline | v6 closure | 변화 |
|---|---:|---:|---:|
| 시간 | 1,234.1s | 708.0s | **-42.63%** |
| LLM calls | 232 | 217 | **-6.47%** |
| prompt token | 1,116,439 | 974,569 | **-12.71%** |
| completion token | 56,372 | 49,876 | **-11.52%** |
| total token | 1,172,811 | 1,024,445 | **-12.65%** |
| cached token | 649,216 | 649,984 | +0.12% |
| costUsd | 3.354816 | 2.935185 | **-12.51%** |

효율 개선의 주원인: 불필요한 role 반복 호출 제거, 질문-only 경로 조기 종료, deterministic query·reply repair,
긴 raw search 결과 대신 artifact 보존+compact context 사용. cached token이 거의 같으므로 절감은 cache hit 증가가
아니라 non-cached prompt와 completion 감소에서 발생

## 사람 품질 결과

| Suite | F | G | C | S | R | 종합 | 이전 v5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| conversation | 4.43 | 4.07 | 4.29 | 4.50 | 3.86 | **4.23** | 3.80 |
| editor | 4.61 | 4.56 | 4.44 | 4.78 | 4.44 | **4.57** | 4.17 |
| create | 4.70 | 4.46 | 4.48 | 4.61 | 4.36 | **4.52** | 4.28 |
| **전체 44 case** | **4.64** | **4.42** | **4.44** | **4.62** | **4.30** | **4.48** | **4.18** |

가장 크게 개선된 항목은 필수 인터뷰·모호성 통제, hierarchy, reply↔payload 일치, Editor marker. 가장 낮은 축은
표현·렌더링 4.30으로, 일부 오래된 통과 결과에 plain username과 장황한 승인 문구가 남아 있음

## 실제 출력과 case별 판독

축 순서는 `F/G/C/S/R`. 전문은 아래 raw 경로에 보존하고, 여기서는 차이와 판정에 필요한 부분만 발췌

### conversation

| Case | 점수 | F/G/C/S/R | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| S1 생성 | 4.00 | 4.5/3/4.5/4.5/3.5 | PoC Task + 설계/구현/테스트 3 Sub-Task | 핵심 구조 충족. 동일인을 추천·대안으로 쓰며 부하를 8/12건으로 다르게 적은 근거 충돌과 plain username은 잔존 |
| S2 Bug | 4.30 | 4.5/4/4.5/4.5/4 | Chrome, 2홉, 빈 화면, 그래프 기대를 Bug로 분리 | 재현 가능한 초안. 제목의 module prefix와 실제 component 관계를 조금 더 명확히 할 여지 |
| S3 이력 | 4.80 | 5/5/4.5/5/4.5 | DL-9041~9047, DL-9062를 날짜순 8개 사건으로 복원 | 요청→Job→지연→주기→schema→catalog→monitoring→정합성의 모범 출력 |
| S4 사람 | 3.50 | 3.5/3.5/3.5/4/3 | 진행 2건과 완료 1건, 임의 관리 조언 | 질문은 현재 업무인데 완료 작업까지 포함. 전체 건수·subset 기준과 person mention badge가 없고 조언은 근거 밖 |
| S5 우선업무 | 4.40 | 4.5/4.5/4.5/4.5/4 | P1·마감 초과 DL-9028을 한 건으로 결정 | priority, due, status를 함께 사용한 명확한 결정 |
| S6 진척 | 4.10 | 4.5/4/4/4.5/3.5 | 3개 중 2개 완료, DL-9095 진행, 남은 성능·문서 | 집계 정확. Jira In Progress와 comment상 API 해결의 source conflict를 더 명시하고 담당자를 mention으로 표시해야 함 |
| S7 내외부 조사 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | 내부 후보 20개·writer 확인·PoC 미수행 + Iceberg/StarRocks 공식 URL | 이전 외부 근거 부재 해소. 공식 direct fallback까지 최종 참조에 보존. 외부 확인 완료와 제품별 추가 검증 gap의 문구를 더 선명히 나눌 여지 |

S7 실제 query/result 핵심

```text
query: Iceberg Puffin NDV official documentation
Apache Iceberg Puffin specification — https://iceberg.apache.org/puffin-spec/
StarRocks Iceberg column statistics setting — https://docs.starrocks.io/docs/sql-reference/System_variable/#enable_iceberg_column_statistics
```

### editor

| Case | 점수 | F/G/C/S/R | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| CMP1 | 4.60 | 4.5/4.5/4.5/5/4.5 | 완료 2·진행 1·남은 작업·설계 문서 URL | 상태와 문서 근거 보존 |
| CMP2 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | canonical DL-9040 badge와 최소 DoD | 과거 nested marker 결함 제거 |
| CMP3 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | seed `p95 가 생각보다`와 person mention 유지 | 의미·seed·mention 보존 |
| CMP4 | 4.80 | 5/5/4.5/5/4.5 | 대상·목적 한 줄을 요청하고 본문 생성 보류 | 정보 없는 자의 작성 차단 |
| CMP5 | 4.60 | 4.5/4.5/4.5/5/4.5 | child 상태·2홉 100노드·문서 link | 수치와 상태 보존 |
| CMP6 | 4.20 | 4.5/4/4/4.5/4 | `@skcc.x1402` mention, 설계 문서라고 서술 | 실제 문서 link/reference가 빠져 minor |
| CMP7 | 4.80 | 5/5/4.5/5/4.5 | 무관 seed를 쓰지 않고 ticket comment 목적 질문 | 안전한 편집 경계 |
| CMP8 | 4.60 | 4.5/4.5/4.5/5/4.5 | parent DoD를 성능 측정·가이드·통합 근거로 축약 | child 실행 세부 반복과 resolved 경고 제거 |
| CMP9 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | 최소 범위·DoD·canonical ticket badge | 발명된 만족도 목표와 이중 marker 제거 |

### create

| Case | 점수 | F/G/C/S/R | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| ONE1 | 4.20 | 4.5/4/4/4.5/4 | 단축키 도움말 단일 Task | 과잉 분해 없음. generic 리뷰 DoD·plain username은 minor |
| ONE2 | 4.30 | 4.5/4/4.5/4.5/4 | `내 모듈만` checkbox 단일 Task | 범위와 동작 보존 |
| STR1 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | 30개를 15개씩 2 Sub-Task | 합계·중복·누락 조건과 분담 충족 |
| STR2 | 4.20 | 4.5/4/4/4.5/4 | 성능·index·guide 3 Task 분리 | 독립 업무 분해 정확. 성능 Task의 과도한 하위 단계와 plain 사람 표기는 minor |
| PAR1 | 4.40 | 4.5/4/4.5/4.5/4.5 | DL-9090 아래 3 Sub-Task, 지정 담당자 | parent·담당 mapping 일치 |
| PAR2 | 4.20 | 4.5/4/4/4.5/4 | DL-101 아래 CDC Task | parent 보존. 불필요한 web 검색 실패 문구와 generic 회귀 DoD는 minor |
| SUB1 | 4.70 | 5/4.5/5/4.5/4.5 | Sub-Task 부모 불가, 형제/Task/취소 선택 | hierarchy 안전 경계 정확 |
| SUB2 | 4.20 | 4.5/4/4/4.5/4 | DL-9090 아래 성능·가이드 2건 | 요청 구조 충족. 동일 담당자 반복과 일부 plain reference는 minor |
| PASTE1 | 4.20 | 4.5/4/4/4.5/4 | 컬럼 설명 미표시 VoC를 Bug로 변환 | 사용자 불편·기대 보존. 실제 동작 section과 재현의 구체성 보강 가능 |
| ASKD1 | 4.80 | 5/5/4.5/5/4.5 | 대상 dataset/table/column 한 질문 | `알아서`여도 필수 target은 묻는 올바른 경계 |
| ASKD3 | 4.80 | 5/5/4.5/5/4.5 | comment 내용·목적 한 질문 | 외부 게시 내용을 발명하지 않음 |
| AMB1 | 4.80 | 5/5/4.5/5/4.5 | test.same01/02를 exact username으로 선택 | 동명이인 mutation 안전 |
| ASK1 | 4.80 | 5/5/4.5/5/4.5 | 품질 작업 target 한 질문 | 필요한 정보만 인터뷰 |
| ASK2 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | 3 turn 답을 30개 table·null ratio·이번 주로 수렴 | 후속 답 누락 없이 실행 draft 생성 |
| ATTR1 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | 30→45분, P1, 금요일, hotfix | 명시 field와 payload 일치 |
| ATTR2 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | 신규 `quality-gate` label 유지 | 존재하지 않는 synonym으로 바꾸지 않음 |
| BUG3 | 4.60 | 4.5/4.5/4.5/5/4.5 | connection timeout 재현 경로 질문 | 조기 Bug payload 차단 |
| RULE1 | 4.70 | 5/4.5/5/4.5/4.5 | parent Task 지정/최상위 Task/취소 | Sub-Task 불변조건과 대안 정확 |
| RULE2 | 4.60 | 4.5/4.5/5/4.5/4.5 | Story payload, 미지원 Story Point 제외 | 지원 field 계약 준수 |
| DUP1 | 4.70 | 5/5/4.5/4.5/4.5 | DL-9072 9개 중 6개 완료, 기존/신규 선택 | 중복 근거 후 필요한 결정만 질문 |
| SUB3 | 4.70 | 5/4.5/5/4.5/4.5 | 두 대상이 모두 Sub-Task라 합법적 대안 제시 | hierarchy·완료 상태 안전 |
| PASTE2 | 4.20 | 4.5/4/4/4/4.5 | prod `dag_etl_nightly`, timeout, 재발 실제 증상 | 핵심 장애 사실 복구. 재현·기대를 확인 필요로 두면서 승인 draft까지 만든 점은 보수적으로 minor |
| ASKD4 | 4.80 | 5/5/4.5/5/4.5 | 정확한 새 임계값 한 질문 | 핵심 mutation value 없이는 payload 생성 안 함 |
| BUG1 | 4.80 | 5/5/4.5/5/4.5 | 간헐적 리니지 문제 재현 경로 질문 | 필요한 진단 정보만 요청 |
| ASKD2 | 4.30 | 4.5/4/4.5/4.5/4 | 답변 후 DL-9090 아래 회귀 Sub-Task로 수렴 | scope 수렴·payload 일치. 제목의 `추가` 중복감과 plain username은 minor |
| BUG2 | 4.40 | 4.5/4.5/4.5/4.5/4 | Chrome·2홉·빈 화면·그래프 기대, x1402 추천/x1450 대안 | 실제 증상·담당 대안 모순 해소. person mention 미사용은 minor |
| STR3 | 4.50 | 4.5/4.5/4.5/4.5/4.5 | DL-102 아래 ETL Task, 2주→2026-08-29 | 중복 Epic·자의적 방법·자기 제외 제거 |
| STARR1 | 4.70 | 5/4.5/5/4.5/4.5 | Story, DL-102, 최소기능, 9/30, 3 Sub-Task | 최종 field·담당·child를 verified 값에서 재렌더링해 reply↔payload 일치 |

## 기술명 번역·웹 조사 보강

- 요청에 이미 canonical Latin spelling이 있으면 이를 그대로 보존
- 한국어로 적힌 외부 기술명은 Query Specialist가 검증된 canonical English name을 별도 query로 생성
- 원어와 canonical query가 다를 때 최대 2개만 실행하고, 같은 query는 deterministic dedupe
- first-party official documentation 우선, 검색 index가 rate-limit이면 허용된 공식 URL direct fallback
- ticket key, user ID, private URL, `fdc_summary_trace_ic` 같은 code/table/parameter identifier는 번역도 외부 전송도 금지
- canonical name이 없거나 확신할 수 없으면 web query를 만들지 않고 uncertainty로 남김

검증: 관련 unit 42건 통과. S7 실제 API에서는 DuckDuckGo 202 rate-limit 뒤 official-direct fallback으로
Apache Iceberg와 StarRocks 공식 문서를 가져와 최종 답변까지 유지. 한국어-only 기술명 path와 내부 code identifier
차단은 deterministic contract test로 검증

## 구조적 개선 요약

- `알아서`를 질문 금지로 해석하지 않고 실행에 꼭 필요한 target/value/person/reproduction만 질문
- Epic–Task/Story/Bug/Feature/Improvement–Sub-Task hierarchy와 Done mutation 제약을 deterministic guard로 이동
- 사용자 명시 type·parent·scope·date·priority·label을 final payload 기준으로 reply에 재투영
- 사람 추천은 final assignment에서 canonical table을 다시 만들어 owner/alternate 모순 차단
- 자식 생성 여부, DoD, 실제 Bug symptom, 중복 ticket 결정을 final verified state에서 답변에 재반영
- query result는 artifact에 완전 보존하고 LLM context에는 필요한 상위 subset만 전달
- 외부 조사는 privacy-safe query, 공식 URL 또는 정확한 실패 상태를 보존

## 남은 개선점

1. S4 현재업무: 완료 항목 제외, 전체 건수·표시 subset·생략 수를 deterministic summary로 강제할 필요
2. 사람 표시: 초기 통과 결과 일부에 plain username/실명이 남음. final renderer 전 경로의 mention badge contract 통합 필요
3. S1 assignment evidence: 추천/대안 동일인과 load 수치 충돌을 response postcheck에서도 막을 필요
4. S6 source conflict: Jira 상태와 comment상의 구현 완료를 표의 별도 열로 강제하면 오해 감소
5. PASTE2: 실제 incident가 충분할 때 최소 Bug draft를 허용할지, reproduction/expected가 없으면 무조건 질문할지
   제품 정책을 더 명확히 정할 필요
6. 기술 번역 actual battery: 현재 S7은 입력부터 English canonical name을 포함. 한국어-only public technology case를
   다음 battery minor version에 추가하면 LLM translation 품질까지 반복 측정 가능

## raw evidence와 실패 attempt

선택 raw는 Git에서 제외된 `.cache/agent-evaluation/` 아래 보존

- baseline: `2026-08-15-en-v5-rubric-2.0-full-r02/`
- v6 최초 full: `2026-08-15-en-v6-quality-rubric-2.0-full-r01/`
- create resume: `2026-08-15-en-v6-quality-rubric-2.0-resume-paste1-r05/`
- focused closure: `2026-08-15-en-v8-quality-focused-dup-starr-r07/`,
  `2026-08-15-en-v8-quality-focused-str3-sub3-r08/`
- human-failure closure: `2026-08-15-en-v9-quality-human-failures-r10/`,
  `2026-08-15-en-v10-quality-human-failures-r12/`, `2026-08-15-en-v11-quality-human-failures-r14/`,
  `2026-08-15-en-v13-quality-human-failure-r18/`
- bilingual S7 success: `2026-08-15-en-v14-quality-bilingual-search-r23/`

S7 기술 재측정의 r20은 1초 shell timeout으로 raw 생성 전 종료, r21은 sandbox 외부망 차단 상태에서 180초
timeout, r22는 205.5초 뒤 모든 OpenAI node가 `Connection error`로 끝나 0 token/빈 답변. r23은 승인된
network 실행에서 19.7초로 성공. 실패를 품질·비용 합계에 섞지 않았지만 attempt 자체는 raw/claim으로 보존

## 최종 판정

- 기능 closure: 선택된 conversation/editor/create case 자동 계약 전부 통과
- 사람 품질: 4.48/5, 이전 English v5보다 방향성 개선
- 효율: time·calls·token·cost 모두 개선, 특히 time -42.63%
- 배포 판단: **개선 방향은 승인 가능**, 단 이 문서는 qualification이 아님. 정식 승격 판단에는 final commit에서
  full battery 5회 이상, 후보 순서 counterbalance, 동일 네트워크 조건의 별도 qualification 실행 필요
