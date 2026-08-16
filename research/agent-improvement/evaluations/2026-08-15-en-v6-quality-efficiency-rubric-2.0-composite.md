# English Agent v6 품질·효율 개선 평가 — rubric 2.0.0

> 결론: 사람이 실제 출력과 실행 근거를 직접 판독한 최신 English v6 품질은 **4.48/5**.
> KO Base 3.75 대비 +0.73점, 이전 English v5 4.18 대비 +0.30점. 최신 EN의 정량 지표는
> 708.0초, 217 calls, 1,024,445 tokens, $2.935185로 KO Base보다 각각 19.27%, 9.58%,
> 21.73%, 21.05% 감소했고 이전 EN보다 각각 37.88%, 6.47%, 11.74%, 11.34% 감소.
> 다만 KO Base와 이전 EN은 full run, 최신 EN은 사용자의 "성공분은 재실행하지 말고 실패분만
> 이어서 실행" 지시에 따른 **exploratory closure composite**. 동일 조건 qualification 또는
> 단일 commit full-run 우열로 해석하면 안 됨

## 측정 식별자

| 항목 | 값 |
|---|---|
| protocolVersion / rubricVersion | `2.0.0` / `2.0.0` |
| runKind | `exploratory-closure-composite` |
| final candidateCommit | `85bdf4e0d77dee078130aee84499ba281bc56d67` |
| promptVersion | `en-role-contract-v6` |
| KO baseline | `ko-role-contract-v6`, commit `8b18b23e6179488373e53ef234d79bc2bccff596` |
| previous EN baseline | `en-role-contract-v5`, commit `450096a2db8c0271aad91cecd6054972c740f4fd` |
| model routing | main `gpt-4o`, simple `gpt-4o-mini` |
| provider / data | actual OpenAI API / `jira820-mock-v1` |
| dataManifestSha256 | `3084e7a7994fa1726515ddfd124fec70b114b3b8d21caa0df639b6d34946f93b` |
| cache / isolation | case별 cold private cache / separate process / mock world mutation 없음 |
| qualitative evaluator | Codex가 raw output과 evaluation evidence를 직접 판독 |
| LTM LLM judge | 사용하지 않음 |
| reviewer / blind | 1명 / non-blind |
| qualificationEligible | `false` — selective closure, 1 repetition, mixed candidate commits |

### 세 후보와 배터리 동일성

| 후보 | 실행 종류 | promptVersion | commit |
|---|---|---|---|
| KO Base | full exploratory, 1회 | `ko-role-contract-v6` | `8b18b23e6179488373e53ef234d79bc2bccff596` |
| 이전 EN | full exploratory, 1회 | `en-role-contract-v5` | `450096a2db8c0271aad91cecd6054972c740f4fd` |
| 최신 EN | selective closure composite, 1회분 조합 | `en-role-contract-v6` 이후 개선분 | 최종 `85bdf4e0d77dee078130aee84499ba281bc56d67` 포함 복수 commit |

| Suite | version | KO Base manifest | 이전 EN v5 manifest | 최신 EN v6 manifest | 직접 비교 |
|---|---|---|---|---|---|
| conversation | `2.0.0` | `799d045…7203` | `799d045…7203` | `799d045…7203` | 동일 battery |
| editor | `2.0.0` | `70567cb…d7eea` | `70567cb…d7eea` | `70567cb…d7eea` | 동일 battery |
| create | `3.0.0` | `e7bfd58…90e` | `e7bfd58…90e` | `e7bfd58…90e` | 동일 battery |

`dataManifestSha256`, battery/review manifest, model routing, provider, mock profile은 세 후보 모두 동일.
이전 EN은 사람 점수 4.18을 산출한 발표 기준 full run `r03`으로 통일. 기존 최신 보고서는 `r03`의 사람 점수와
미채점 실행 `r02`의 정량값을 섞어 썼으며, 여기서 `r03` 기준으로 정정

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

- KO Base와 이전 EN은 각 후보의 44 case full exploratory run. 최신 EN은 아래 선택 규칙으로 만든 closure composite
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

### KO Base / 이전 English v5 / 최신 English v6 3자 비교

| 지표 | KO Base full | 이전 EN v5 full | 최신 EN v6 closure | 최신 EN ↔ KO | 최신 EN ↔ 이전 EN |
|---|---:|---:|---:|---:|---:|
| 시간 | 877.0s | 1,139.8s | 708.0s | **-19.27%** | **-37.88%** |
| LLM calls | 240 | 232 | 217 | **-9.58%** | **-6.47%** |
| prompt token | 1,249,432 | 1,106,188 | 974,569 | **-22.00%** | **-11.90%** |
| completion token | 59,413 | 54,518 | 49,876 | **-16.05%** | **-8.51%** |
| total token | 1,308,845 | 1,160,706 | 1,024,445 | **-21.73%** | **-11.74%** |
| cached token | 633,856 | 579,840 | 649,984 | +2.54% | +12.10% |
| costUsd | $3.717710 | $3.310650 | $2.935185 | **-21.05%** | **-11.34%** |

효율 개선의 주원인: 불필요한 role 반복 호출 제거, 질문-only 경로 조기 종료, deterministic query·reply repair,
긴 raw search 결과 대신 artifact 보존+compact context 사용. 이전 EN보다 cached token은 12.10% 늘었지만
전체 prompt와 completion이 각각 11.90%, 8.51% 줄어 총량과 비용은 감소

## 사람 품질 결과

### 세 후보 종합

| Suite | KO Base | 이전 EN v5 | 최신 EN v6 | 최신 EN - KO | 최신 EN - 이전 EN |
|---|---:|---:|---:|---:|---:|
| conversation | 3.97 | 3.80 | **4.23** | +0.26 | +0.43 |
| editor | 3.96 | 4.17 | **4.57** | +0.61 | +0.40 |
| create | 3.63 | 4.28 | **4.52** | +0.89 | +0.24 |
| **전체 44 case** | **3.75** | **4.18** | **4.48** | **+0.73** | **+0.30** |

KO Base의 강점은 완전 이력 S3와 일부 보수적 생성 판단. 주요 약점은 S7 외부 조사 부재, 필수정보 인터뷰 실패,
reply↔payload 불일치. 이전 EN은 생성 완결성과 효율을 개선했지만 모호성 질문과 일부 사실·계약 오류가 남았고,
최신 EN은 이 두 계열을 집중적으로 보완. 다만 최신 EN은 selective closure이므로 점수 차이를 통계적 우열로
해석하지 않음

### 최신 EN 세부 축

| Suite | F | G | C | S | R | 종합 |
|---|---:|---:|---:|---:|---:|---:|
| conversation | 4.43 | 4.07 | 4.29 | 4.50 | 3.86 | **4.23** |
| editor | 4.61 | 4.56 | 4.44 | 4.78 | 4.44 | **4.57** |
| create | 4.70 | 4.46 | 4.48 | 4.61 | 4.36 | **4.52** |
| **전체 44 case** | **4.64** | **4.42** | **4.44** | **4.62** | **4.30** | **4.48** |

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

- KO Base 평가표: `2026-08-15-base-rubric-2.0-full.md` (`2026-08-15-base-rubric-2.0-full-r01` 기록)
- previous EN baseline: `2026-08-15-en-v5-rubric-2.0-full-r03/`
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
- 사람 품질: 4.48/5, KO Base 3.75와 이전 English v5 4.18보다 방향성 개선
- 효율: KO Base 대비 time -19.27%, token -21.73%, cost -21.05%; 이전 EN 대비 time -37.88%,
  token -11.74%, cost -11.34%
- 배포 판단: **개선 방향은 승인 가능**, 단 이 문서는 qualification이 아님. 정식 승격 판단에는 final commit에서
  full battery 5회 이상, 후보 순서 counterbalance, 동일 네트워크 조건의 별도 qualification 실행 필요

---

## 2026-08-16 확장 기준선 — Meeting·Context change 추가

> 기존 44-case English v6 closure composite에 신규 `meeting` 5건과 `ctx-chg` 4건을 이어 붙인
> **53-case 전체 배터리 개선 전 기준선**. 신규 9건의 사람 품질은 **2.82/5**, 결합 기준선은 **4.20/5**.
> 신규 suite에서 회의 인터뷰·명시 담당자 보존·대화 전환이 구조적으로 무너져, 기존 배터리만의 4.48이
> 새 기능 범위를 대표하지 못함을 확인

### 측정 식별자와 비교 가능성

| 항목 | 값 |
|---|---|
| protocolVersion / rubricVersion | `2.0.0` / `2.0.0` |
| 신규 runKind | `exploratory`, 1회 |
| 신규 runGroupId | `baseline-main-meeting-ctx-20260816` |
| 신규 candidateCommit | `5f343c50098d68b64652e25e99c2f5ed29d2acae` |
| Agent runtime 기준 | PR #8 병합 main `8662d6e27ccb3531dcd38935600791fb1e0c5d73`와 동일 |
| promptVersion | `en-role-contract-v7` |
| model routing | complex `gpt-4o`, simple `gpt-4o-mini` |
| dataManifestSha256 | `87e592d3cc136e62e135e5d81c76c91121da0e85d18fdc0b74bd0304f0521621` |
| selection / retry | `complete-run-no-substitution` / `no-silent-retry` |
| cache / process | case별 cold private cache / suite별 별도 process |
| qualitative evaluator | Codex 직접 raw 전문 판독, LTM LLM judge 미사용 |
| reviewer / blind | 1명 / non-blind |
| qualificationEligible | `false` — 신규 suite 1회 및 기존 44건과 다른 commit·data manifest의 composite |

| Suite | version | batteryManifestSha256 | specializedReviewSpecSha256 | comparabilityKey |
|---|---|---|---|---|
| meeting | `1.0.0` | `4eef1d8e848d6dc13d9e6b1978889199e5e9b1b9eecccc4049696ff74288cfd3` | `d5d24bf1aac3bb40117824721807a19e3ce49bbe872d4d0ade74c03ad47c6e83` | `a12001ab10ce974be41ab7ab98d9095fda774e41a5dc3a927f4057ea00957022` |
| ctx-chg | `1.0.0` | `b775a2e64ad5a89426678fa28348e37fc66aa65f6b45f40070cd83fa5210bb30` | `63858d0bb8086270c302225817b70df970c9cc84716a98a4c7d4dbb0aeed20fc` | `70cfcee9247c22ef0d99496ba0f2064e1bb345f3494e44c6cb094e1e3379d45e` |

기존 44건은 위 문서 앞부분의 selective closure composite이고 신규 9건은 한 clean commit의 full suite 실행.
따라서 53-case 점수는 이후 개선 범위를 정하는 **상정 기준선**이며 단일 commit qualification으로 해석 금지.
이후 개선본은 5개 suite를 한 commit·동일 data manifest에서 모두 다시 실행해 별도 비교

### 확장 배터리와 실행 격리

| Suite | case | 공통 검토 계약 |
|---|---:|---|
| meeting | 5 | 회의 요약·Task 생성·결정 댓글·필드/본문 수정·후속 Task. 모든 case가 사람 표기 정규화, 내부/외부 조사 후 미해결 인터뷰, 답변 전 write 보류를 다중 turn으로 검증 |
| ctx-chg | 4 | 완전한 주제 전환, 정보 공유 후 다른 요청, write 요청 반복 취소·대체, 잠깐 다른 주제를 거친 뒤 이전 대상 복귀 |

- 사람 표기: `@이다은`, `{{최민서:1042}}`, `하은님`, `현우차장`, `준서TL`
- 모호 후보: `skcc.x1103` 이준서 / `skcc.x1327` 임준서. 단일 후보로 확정되지 않으면 질문 필수
- 기술 공백: PSR·RGP는 Jira·Confluence·comment에도 정의되지 않음. 내부 조사 후 사용자 인터뷰 필수
- write는 모두 승인 전 pending까지만. meeting·ctx-chg 9/9에서 world와 provider Store fingerprint 불변,
  suite process `9724` / `28204`와 private cache 분리

### 신규 정량 결과와 53-case 상정 기준선

| Suite | 자동 결과 | 시간 | p50 / p95 | calls | prompt / completion / total token | cached | costUsd |
|---|---:|---:|---:|---:|---:|---:|---:|
| meeting | 0/5 | 247.0s | 42.8 / 85.2s | 52 | 304,827 / 16,000 / 320,827 | 141,568 | 0.922067 |
| ctx-chg | 0/4 | 91.7s | 20.9 / 33.4s | 32 | 151,423 / 6,409 / 157,832 | 86,656 | 0.442646 |
| **신규 9건** | **0/9** | **338.7s** | — | **84** | **456,250 / 22,409 / 478,659** | **228,224** | **1.364713** |
| 기존 44건 EN v6 closure | 44/44 | 708.0s | — | 217 | 974,569 / 49,876 / 1,024,445 | 649,984 | 2.935185 |
| **53-case 상정 기준선** | **44/53** | **1,046.7s** | — | **301** | **1,430,819 / 72,285 / 1,503,104** | **878,208** | **4.299898** |

신규 case당 평균 53,184 token으로 기존 44건 평균 22,765 token의 2.34배. 주요 원인은 MTG1 turn 2의
빈 comment query가 2,316건을 반환한 것, MTG3에서 Research Analyst를 5회 호출한 것, 이미 명시된 action-only
회의 요청에도 Jira·web·people workload 조사가 중복 실행된 것

### 신규 사람 품질과 결합 점수

| Suite | F 요청 | G 근거 | C 계약 | S 안전·질문 | R 표현 | 종합 |
|---|---:|---:|---:|---:|---:|---:|
| meeting | 2.60 | 2.50 | 2.10 | 1.70 | 3.30 | **2.44** |
| ctx-chg | 2.75 | 3.50 | 3.38 | 3.25 | 3.63 | **3.30** |
| **신규 9건** | **2.67** | **2.94** | **2.67** | **2.39** | **3.44** | **2.82** |
| 기존 44건 EN v6 closure | 4.64 | 4.42 | 4.44 | 4.62 | 4.30 | **4.48** |
| **53-case 상정 기준선** | **4.31** | **4.17** | **4.14** | **4.24** | **4.16** | **4.20** |

점수는 공통 5축 각 20%, 0.5 간격. 신규 case는 suite 공통 3개와 case 고유 특수요소를 기존 축에
합산해 ceiling 적용. `P/m/M/n`은 pass/minor/major/not-applicable

### 공통 checklist 전수 판정

열 안의 순서는 고정

- F: `intent/scope/compound/constraints/relevance/closure`
- G: `entity_resolution/field_accuracy/counts_completeness/temporal/source_conflict/fact_inference_boundary`
- C: `cross_output_consistency/schema_validity/domain_legality/approval_fidelity/query_execution/operational_specificity`
- S: `required_input_interview/question_economy/material_ambiguity/confidence_calibration/side_effect_control/protected_invariants/untrusted_data/failure_transparency`
- R: `answer_first/structure/conciseness/ticket_rendering/person_document_rendering/list_scaling`

| Case | F | G | C | S | R | 실제 근거 요약 |
|---|---|---|---|---|---|---|
| MTG1 | `M/M/M/M/M/M` | `M/M/m/M/M/M` | `M/n/n/n/m/M` | `M/M/M/M/P/n/P/M` | `m/P/M/m/M/m` | 첫 turn 질문 없음. 둘째 turn은 준서 개인의 DL-5515 등 무관 8건을 회의 연표로 출력하고 PoC 상태도 “미수행/완료” 충돌 |
| MTG2 | `m/m/M/M/m/M` | `M/M/P/P/m/m` | `M/P/P/P/m/M` | `M/M/M/M/P/P/n/P` | `P/P/M/m/M/P` | 구조가 이미 3 Task로 확정됐는데 재질문. 최종 담당자를 i2044/x1210/i2044로 덮어쓰고 P3·Catalog·label을 발명 |
| MTG3 | `P/P/P/M/m/P` | `M/P/P/n/n/m` | `M/P/P/P/m/M` | `M/M/M/M/P/P/n/P` | `M/m/m/P/M/P` | 인터뷰 전 댓글 pending 생성. 답변 후 x1327을 준서뿐 아니라 이다은 mention에도 잘못 사용 |
| MTG4 | `M/P/P/m/P/P` | `M/P/P/P/P/m` | `M/P/P/P/P/m` | `M/M/M/m/P/P/n/m` | `P/P/m/P/m/P` | “댓글을 남기지 마”인데 댓글 내용을 질문. 둘째 turn 필드값은 대체로 정확하나 RGP 소유자 x1103을 본문에서 누락 |
| MTG5 | `M/M/m/M/P/M` | `m/m/M/P/n/m` | `M/n/P/P/m/M` | `M/M/M/m/P/P/n/m` | `m/m/m/P/M/P` | PSR만 묻고 준서 동명이인은 누락. 단일 Task 요청을 리뷰/증빙 두 Task로 바꾼 뒤 다시 구조 질문, pending 없음 |
| CTX1 | `M/m/n/P/M/M` | `m/P/n/P/P/M` | `M/P/P/P/n/P` | `n/P/n/m/P/P/n/P` | `M/P/M/m/n/m` | priority-only pending은 정확하지만 reply 전체가 취소된 fdc 현재상태·참조·남은 공백 |
| CTX2 | `P/P/P/P/P/m` | `P/P/m/P/P/P` | `P/P/P/P/P/P` | `n/P/n/P/P/P/n/P` | `P/P/m/P/n/P` | DL-9090 3개 중 2개 완료·DL-9095 남음·성능/문서 정확. 완료 child key를 생략했지만 현재/남은 요청에는 충분 |
| CTX3 | `M/M/M/M/M/M` | `n/m/n/n/n/m` | `M/n/P/P/n/M` | `n/M/M/M/P/P/n/m` | `M/m/m/m/n/P` | priority/due와 댓글을 모두 취소했는데 마지막에 다시 댓글 내용 질문. 최신 제목 pending 없음 |
| CTX4 | `M/m/P/P/m/M` | `m/P/P/P/m/m` | `m/P/P/P/P/P` | `n/M/M/m/P/P/n/P` | `m/P/m/P/m/P` | turn 2의 이다은 업무 요청을 무시하고 DL-9090을 반복. final DL-9095 comment-only pending 자체는 정확 |

### suite·case 특수요소 판정

| Case | suite 공통 3개 | case 고유 | 판정 근거 |
|---|---|---|---|
| MTG1 | `M/M/M` | `mtg1_source_coverage=m`, `mtg1_decision_summary=M` | 내부·외부 조회는 했으나 첫 turn 인터뷰 없음, 최종 관련 entity와 담당·기한 붕괴 |
| MTG2 | `M/M/M` | `mtg2_task_mapping=M` | 3건·parent·due는 유지했지만 세 명 담당 모두 오배정 |
| MTG3 | `M/M/M` | `mtg3_comment_scope=M` | 대상/comment-only는 맞아도 인터뷰 전 draft와 사람 mention 오류 |
| MTG4 | `M/M/m` | `mtg4_exact_update=M` | exact field set은 맞지만 필요한 인터뷰 대신 금지된 comment 질문, owner 누락 |
| MTG5 | `M/M/M` | `mtg5_research_gap_interview=M` | 두 후보를 제시하지 않았고 답변 후에도 단일 Task로 재개 실패 |
| CTX1 | `M/M/P` | `ctx1_unrelated_switch=M` | final pending 교체는 성공, 사용자 표시 답변은 과거 topic으로 오염 |
| CTX2 | `P/P/P` | `ctx2_shared_info_boundary=P` | 공유한 fdc 정보를 새 조회에 섞지 않음 |
| CTX3 | `M/M/M` | `ctx3_superseded_writes=M` | 취소된 comment intent가 최종 request를 덮어씀 |
| CTX4 | `M/M/P` | `ctx4_return_to_prior_topic=m` | final target은 맞지만 중간 topic 전환 실패 |

### 신규 case별 실제 출력과 Codex 평가

| Case | 점수 | F/G/C/S/R | 실제 출력 핵심 | Codex 직접 평가 |
|---|---:|---|---|---|
| MTG1 | 1.90 | 2/1.5/2/1.5/2.5 | “현재 진행 중인 Task: DL-5515, DL-5510” | 모호성 질문을 생략하고 이준서의 개인 업무를 회의 이력으로 오염. “PoC 미수행”과 “완료”도 충돌해 사용 불가 |
| MTG2 | 2.30 | 2.5/2.5/1.5/1.5/3.5 | 실제 담당자 `skcc.i2044 / skcc.x1210 / skcc.i2044` | 회의에서 확정된 i2011/x1402/x1103을 People Advisor가 전부 대체. 불필요한 구조 질문과 필드 발명 |
| MTG3 | 2.30 | 3/2/2/1.5/3 | `{{mention:skcc.x1327}} 이다은님` | comment-only와 두 target은 맞지만 사람 identity를 훼손하고 인터뷰 전에 이미 draft 생성 |
| MTG4 | 3.30 | 3.5/3.5/3.5/2/4 | “남길 댓글의 내용… 알려 주세요” | 금지한 댓글을 질문하는 context 오류. 최종 필드는 대부분 정확하지만 소유자 mention 누락 |
| MTG5 | 2.40 | 2/3/1.5/2/3.5 | “두 개의 Task… 이 형태로 진행할까요?” | 한 Task라는 요청을 두 건으로 바꾸고 준서 동명이인은 끝까지 질문하지 않음 |
| CTX1 | 3.20 | 2.5/3/3.5/4/3 | exact priority pending + fdc 설명 전체 | 실행 payload는 안전하지만 사용자가 보는 답변이 완전히 이전 topic이라 승인 판단 방해 |
| CTX2 | 4.60 | 4.5/4.5/4.5/5/4.5 | “하위 3개 중 2개 완료… DL-9095… 성능 측정과 문서 정리” | 정보 공유와 새 조회를 올바르게 분리. 자동 checker가 불필요하게 완료 child key를 요구한 false negative |
| CTX3 | 2.10 | 1.5/3/1.5/1.5/3 | “남길 댓글의 내용이나 전달 목적” | 댓글 취소와 title-only 최종 요청을 무시. pending도 없어 최신 request precedence 실패 |
| CTX4 | 3.30 | 2.5/3.5/4/2.5/4 | turn 2에도 DL-9090 반복, final DL-9095 댓글은 정확 | 아예 다른 사람 업무 요청을 무시한 major. 복귀 후 최종 action은 실사용 가능 |

### 자동 checker와 사람 판정 불일치

| 유형 | Case | 자동 | 사람 | 판단 |
|---|---|---:|---:|---|
| false negative | CTX2 | fail | 4.60 | 현재/남은 작업 요청에 완료 child key DL-9093/9094 전문 나열까지 요구한 checker가 과도 |
| false negative 일부 | CTX4 | fail | 3.30 | final 단일 대상의 합법적 `update_ticket/key`를 checker가 `update_tickets/keys`만 허용. 다만 중간 turn 실패 때문에 case red 자체는 유지 |
| aligned red | MTG1–5, CTX1, CTX3 | fail | 1.90–3.30 | 인터뷰·identity·최신 context·reply↔payload의 실제 major 결함 |

### 신규 기준선에서 도출한 개선 우선순위

1. 최신 turn이 새 목표 또는 취소를 선언하면 과거 situation·evidence·pending intent를 답변 생성 context에서 제외
2. 회의록 사람 표기를 먼저 canonical identity로 정규화. 명시 담당자는 People Advisor가 재추천하거나 덮어쓰지 않음
3. 한 명으로 확정되지 않는 부분 이름+호칭과 내부 조사 후에도 남는 행동 핵심 용어를 한 번의 인터뷰로 묶고 write 보류
4. 사용자가 item count·구조를 확정했으면 `Task 여러 개/하나/Sub-Task` 구조 질문 금지
5. comment-only / no-comment / exact-fields 제약을 Work Architect보다 앞선 deterministic contract로 보존
6. 빈 comment query 금지, result cap과 artifact 요약 적용, 동일 role·동일 entity 반복 조회 제거

### Raw evidence

- `.cache/agent-evaluation/baseline-main-meeting-ctx-20260816/meeting-b1.0.0-r01.json`
- `.cache/agent-evaluation/baseline-main-meeting-ctx-20260816/ctx-chg-b1.0.0-r01.json`

raw에는 모든 input/reply/question/pending/evaluationEvidence/usage와 case별 world·provider fingerprint를 보존.
Git에는 포함하지 않음
