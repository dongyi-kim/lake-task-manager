# English Agent v12 — 최종 구조 개선·실 UI 검증 누적 비교

> 결론: 마지막 54-case 실제 OpenAI API full run은 자동 계약 **51/54**, Codex 직접 사람 평가는
> **4.42/5**. 실패·사람 품질 저하 case만 구조적으로 개선하고 재실행한 closure는 자동 계약
> **54/54**, 최종 채택 출력의 Codex 직접 평가는 **4.71/5**. 마지막 full battery의 실패와
> 정성 피드백, 실제 LTM Agent UI에서 확인한 출처 표·citation click·ticket hover 피드백을 모두
> 구현에 반영함. 다만 closure는 focused 결과 합성이고 reviewer 1명의 비맹검 exploratory 평가이므로
> 통계적 qualification이나 “모든 답변이 완벽한 5점”을 의미하지 않음.

## 이번 누적판에서 실제로 닫은 항목

| 출처 | 관찰된 문제 | 구조적 반영 | 최종 확인 |
|---|---|---|---|
| full / `DUP1` | 실제 중복 후보 대신 추상적 중복 질문 | 후보 key·title·근거가 있는 decision-ready 질문으로 정규화 | focused pass |
| full / `ATTR1` | 명시한 priority·due·label 일부 유실 | 현재 turn의 literal field를 authoritative payload로 고정 | focused pass |
| full / `CTX1` | 과거 조사 맥락이 최신 field-only 변경에 혼입 | 최신 단일 mutation이 과거 create/research action을 무효화 | focused pass |
| 사람 리뷰 / `S1` | 구현 단계 중복, 부모/자식 책임 중첩, 요청자 발명 | execution stage dedupe, parent/child 책임 분리, 미언급 requester 제거 | Story 1 + 설계/구현/테스트 3 Sub-Task |
| 사람 리뷰 / `S8` | 출처 중복, 주장-근거 연결 약함, 지원하지 않는 최적화·완료 단정 | canonical evidence index, source별 observation, citation binding, unsupported guarantee/status conflict 교정 | focused contract 전부 pass |
| 사람 리뷰 / `CMP5/7/8` | 상투적 맺음말, 무관 요청 작성, 깨진 마지막 문장 | editor relevance guard, generic closer 제거, dangling paragraph 복구 | focused pass |
| 사람 리뷰 / `MTG1/4` | 미해결 호칭·약어, 오래된 상태가 회의 결정을 덮음, 변경 범위 혼입 | research 후 interview, meeting decision 우선, exact mutation 경계 | focused pass |
| 사람 리뷰 / `PASTE2` | 메신저 장애 기록을 빈 Bug form으로 변환 | 환경·DAG/Job·증상·재발·retry를 literal fact로 추출해 Bug 3섹션 조립 | focused pass |
| 사람 리뷰 / `AMB1` | 동명이인 질문 주변 내부 경고 노출 | question-only 답변에서는 grounding diagnostic 비노출 | focused pass |
| 실 UI | `출처 평가` 표가 ticket detail badge 때문에 한 글자 폭으로 붕괴 | 전용 `agent-source-quality` table과 local horizontal scroll | 실제 브라우저 판독 가능 |
| 실 UI | citation과 hover가 실제 동작하는지 불명확 | `[n]` click→근거 panel/flash, ticket hover→type/title/assignee/status/Epic/due/updated 검증 | 실제 브라우저 동작 확인 |
| 공통 표현 | polite ending, 근거/참조 이원화, badge 정보 반복 | 짧은 개조식 후처리, 단일 `### 근거`, badge 정보 재서술 억제 | 회귀 테스트와 UI 확인 |
| 효율 | `prod`를 공개 기술명으로 오인해 무관 웹 검색 | prod/stage/qa/dev와 내부 identifier를 외부 기술 검색에서 제외 | PASTE2 6→5 calls, 29,737→25,188 tok |

## 측정 식별자

| 항목 | 값 |
|---|---|
| protocolVersion / rubricVersion | `2.0.0` / `2.0.0` |
| promptVersion | `en-role-contract-v12` |
| final implementation commit | `5cf2e3d` |
| primary commits | conversation/editor/ctx `9674c5f`, create/meeting `045af04` |
| model / simpleModel | `gpt-4o` / `gpt-4o-mini` |
| provider / runtimeProfile | `openai` / `production-mixed-v1` |
| data profile | `jira820-mock-v1` |
| dataManifestSha256 | `87e592d3cc136e62e135e5d81c76c91121da0e85d18fdc0b74bd0304f0521621` |
| cache / isolation | case별 cold private cache / separate process |
| retry policy | `no-silent-retry` |
| qualitative evaluator | Codex가 raw 전문·payload·query/evidence trace를 직접 읽음 |
| LTM LLM judge 사용 | `false` |
| reviewer / blind | 1명 / non-blind |
| qualificationEligible | `false` — 1회 exploratory full + focused closure 합성 |

Focused raw의 `candidateDirty=true`는 최종 코드를 commit하기 전 같은 working tree에서 측정했기 때문.
최종 구현은 `5cf2e3d`에 고정. 실패·중간 시도도 raw cache에 보존하며 성공 결과로 덮어쓰지 않음.

### Battery identity

| Suite | version | manifest SHA-256 | specialized review SHA-256 |
|---|---|---|---|
| conversation | `3.2.0` | `dce890630ce3a1321bdb07e5ec9337776ebc77a6858c0442851b61cae01c230a` | `cbfec43c80dbe83bc9a52e9aebb19904fdc5fb68b2920112059b44aeb1912ecf` |
| editor | `3.0.0` | `93c51fdfd97a571fcd4c8acbb52c49f0dbd267afca712c3ca0093fce29843a7f` | `61030118c370d5d795ec0a15a4d8ce1a8ceb646b9a39c1eced588cc236f8ed40` |
| create | `4.0.2` | `be136915fca70e233ddf0fc360007076f9d633d46175b11ff90c548468707ee5` | `39fcbea7f3c527ff8bb76f9d1252b06769ae2f0d522943a08e8acd89a8eaaa0d` |
| meeting | `2.0.3` | `e4b9a04fd61bf41f461d5f9c291e61e959da537c57a2a715c1f30aac73364a45` | `bc19f05cbeff5efb4a0f2f37c80558d35f15d3f99874400c15b0b1044b6c1f23` |
| ctx-chg | `2.0.0` | `a64966b582826173a63c03a7b1d9403e98078897f9dd66a5bafcaf444dc884ff` | `61db5b23296cdf2ad3e42586e1a11514548a90cfd71a56ad6351b8916d58ece7` |

## 실행 격리와 비교 가능성

- suite는 별도 process, case는 private cold cache. 모든 primary case에서 mock world와 provider store 불변
- 실제 Jira/Confluence write 없음. 승인 전 pending payload만 검사
- raw JSON은 `.cache/agent-evaluation/`에 저장되어 git 제외. git에는 이 점수·평가 보고서만 보존
- primary 54건은 complete run. closure는 사용자 지시대로 실패·정성 저하 case만 다시 실행
- `2026-08-16-en-v12-focused-r24-s8-conflict-final`은 필터를 지원하지 않는 legacy smoke harness를 잘못 호출한 실행이라 비교에서 제외. 표준 S8은 r25로 다시 실행
- EN v10은 53건, v12는 S8 추가로 54건. battery manifest도 달라 절대 증감은 방향성 참고

## 정량 결과

### 마지막 primary full run

| Suite | 자동 결과 | 시간 | calls | prompt / completion / total token | cached | costUsd |
|---|---:|---:|---:|---:|---:|---:|
| conversation | 8/8 | 138.3s | 41 | 196,349 / 13,057 / 209,406 | 118,144 | 0.582422 |
| editor | 9/9 | 22.5s | 8 | 46,818 / 1,295 / 48,113 | 28,416 | 0.007799 |
| create | 26/28 | 487.9s | 184 | 903,853 / 37,751 / 941,604 | 645,248 | 2.637141 |
| meeting | 5/5 | 224.1s | 53 | 308,997 / 16,440 / 325,437 | 209,664 | 0.936893 |
| ctx-chg | 3/4 | 132.9s | 39 | 207,535 / 8,273 / 215,808 | 142,592 | 0.561877 |
| **전체** | **51/54** | **1,005.7s** | **325** | **1,663,552 / 76,816 / 1,740,368** | **1,144,064** | **4.726132** |

### Focused closure evidence

| 대상 | 최종 결과 | 대표 실행 지표 | raw group |
|---|---:|---:|---|
| CTX1 | 1/1 | 29.6s · 9 calls · 50,116tok · $0.142488 | `focused-r03-context` |
| MTG4 | 1/1 | 30.5s · 9 calls · 52,391tok · $0.153291 | `focused-r04-meeting` |
| ATTR1 | 1/1 | 19.5s · 10 calls · 63,628tok · $0.169570 | `focused-r05-create` |
| DUP1 | 1/1 | 17.1s · 6 calls · 29,832tok · $0.082290 | `focused-r06-create` |
| CMP5/7/8 | 3/3 | 9.4s · editor mixed route | `focused-r09-ui-review` |
| S1 | contract pass | 40.6s · 10 calls · 49,273tok · $0.145209 | `focused-r11-ui-review` |
| MTG1 | 1/1 | 55.3s · 9 calls · 52,406tok · $0.158795 | `focused-r11-ui-review` |
| AMB1 | 1/1 | 9.1s · 4 calls · 20,505tok · $0.054975 | `focused-r14-warning-closure` |
| PASTE2 | 1/1 | 15.3s · 5 calls · 25,188tok · $0.069922 | `focused-r19-runtime-bug-final` |
| S8 | contract 전 항목 pass | 37.5s · 7 calls · 45,697tok · $0.147340 | `focused-r25-s8-conflict-final` |

Focused closure는 자동 실패 3건과 정성 저하 case의 최종 성공을 합친 **54/54 운영 확인값**.
성공 case까지 같은 commit에서 다시 돌린 full run이 아니므로 시간·token·비용의 “closure 전체 합계”를
만들지 않음. best-of 합계로 보이게 만드는 것보다 primary 정량값과 focused 개선값을 분리하는 편이 정확함.

### 이전 누적판과 방향성 비교

| 지표 | EN v10 closure (53) | EN v12 primary (54) | EN v12 focused closure |
|---|---:|---:|---:|
| 자동 계약 | 53/53 | 51/54 | **54/54** |
| 시간 | 764.3s | 1,005.7s | full 합계 미산출 |
| calls | 254 | 325 | full 합계 미산출 |
| total token | 1,359,457 | 1,740,368 | full 합계 미산출 |
| costUsd | 3.785324 | 4.726132 | full 합계 미산출 |
| Codex 사람 품질 | 4.56 | 4.42 | **4.71** |

v12 primary는 S8 한 건 추가를 감안해도 공통 53건 기준 965.4s, 318 calls, 1,695,732tok,
$4.584819로 v10보다 무거움. 즉 **품질은 closure에서 상승했지만 전체 효율 개선은 아직 full-run으로
입증되지 않음**. 확인된 국소 개선은 PASTE2의 무관 web query 제거(6→5 calls, 29,737→25,188tok)와
S8 지연 40.3→37.5s. 다음 효율 라운드는 node별 호출 상한과 evidence 요약 크기를 별도 목표로 삼아야 함.

## 사람 품질 평가 기준

정성평가자는 Codex. LTM LLM의 자기평가나 별도 judge 응답을 점수로 사용하지 않음. 각 축은 1.0~5.0,
0.5 간격, 동일 가중.

| 축 | 구체 판단 checklist | major 예시 |
|---|---|---|
| F 요청 충족 | 최종 intent, 복합 범위, 명시 제약, 최신 turn, 요구한 산출물·건수 전부 충족 | 다른 action 수행, 핵심 대상/사람/건수 누락 |
| G 사실·근거 | entity·수치·날짜·사람·상태 보존, claim-source 연결, 충돌·미확인 구분, 공식/내부 출처 적합성 | 없는 완료·원인 발명, 관련 없는 source로 결론 |
| C 계약·실행성 | reply/question/card/payload 일치, hierarchy/type/field/action 유효, 승인 경계 | 잘못된 parent/type, reply와 payload 불일치 |
| S 안전·불확실성 | 조사로 풀 수 없는 필수값만 interview, 불필요 질문 없음, Done/write/side effect guard | `알아서`를 이유로 필수값 추측, 필요한 질문 누락 |
| R 표현·렌더링 | 결론 우선, 짧은 개조식, heading/table/list, marker/badge/mention/link, 중복 없음 | 내부 경고 노출, badge·code 겹침, 표 붕괴 |

- `pass`: 수정 없이 사용 가능
- `minor`: 핵심 사용 가능하나 국소 수정 필요. 해당 축 최고 4.5
- `major`: 중요한 재작업이나 잘못된 판단 위험. 해당 축 최고 3.0
- 적용 checklist 전부 pass일 때만 5.0. minor 2건 이상이면 축 최고 4.0, major 2건 이상이면 최고 2.0
- 필요한 interview는 가점. 필요 없는 질문 또는 필요한 질문 생략은 감점

### 최종 사람 품질

| Suite | F | G | C | S | R | 종합 |
|---|---:|---:|---:|---:|---:|---:|
| conversation | 4.94 | 4.63 | 4.63 | 4.81 | 4.56 | **4.71** |
| editor | 4.78 | 4.67 | 4.61 | 4.83 | 4.56 | **4.69** |
| create | 4.80 | 4.66 | 4.80 | 4.73 | 4.55 | **4.71** |
| meeting | 4.90 | 4.50 | 4.80 | 4.70 | 4.50 | **4.68** |
| ctx-chg | 5.00 | 4.88 | 4.88 | 4.88 | 4.63 | **4.85** |
| **전체 54** | **4.84** | **4.66** | **4.75** | **4.77** | **4.56** | **4.71** |

## 최종 채택 실제 출력 — 변경이 큰 case

### S1 — 단계별 생성

```text
| 1 | Story | [ETL] Iceberg Puffin NDV 통계정보 생성 PoC |
| 2 | Sub-Task | [ETL] NDV 통계 생성 Batch Job 설계 |
| 3 | Sub-Task | [ETL] NDV 통계 생성 Batch Job 구현 |
| 4 | Sub-Task | [ETL] NDV 통계 생성 Batch Job 테스트 |
```

- 이전: generic 구현과 명시적 구현이 중복되고 UI fixture를 요청자로 발명
- 최종: 설계/구현/테스트가 한 번씩만 존재, 부모 Story와 세 자식의 책임 분리
- 평가: 4.7 — 구조·payload 우수. 담당 추천 근거는 부하 중심이라 도메인 경험 근거가 더 있으면 개선 가능

### S8 — 복합 근거 품질

```text
### 결론
현재 Iceberg Puffin NDV의 운영 적용은 검증 단계 ... 검증 전까지 운영 반영 보류 ...
운영 적용 여부는 아직 확정되지 않음. [2][1][5]

### 출처 평가
| 출처 | 신뢰도 | 요청 적합성 | 한계 |
| {{ticket-detail:DL-9200}} | 높음 | 직접 | 검증 전 운영 반영 금지 상태 |
| [Puffin Spec - Apache Iceberg™](...) | 높음 | 보조 | Puffin 파일 형식 공식 설명 |
```

- Jira·comment·Confluence·web 네 source 계획 실행, 본문 citation이 단일 `### 근거` index로 연결
- source quality 표와 번호별 관찰 보존, 같은 URL·제목·observation 중복 제거
- 완료/미수행이 상충하면 완료 단정을 남기지 않고 확정 불가로 교정
- 평가: 4.6 — 의사결정에는 충분. 일부 limitation 문구가 일반적이고 source table은 좁은 화면에서 scroll 필요

### MTG1 — 조사 후 인터뷰를 거친 회의 요약

```text
### 결정사항
- 1차 PoC는 Iceberg 테이블 5개
- StarRocks reader 검증 전 운영 반영 보류
- PSR: 5/5 표본 NDV 오차 5% 이내 + StarRocks 실제 읽기

| 작업 | 담당 | 기한 |
| writer PoC | [~skcc.i2011] | 2026-08-22 |
| StarRocks reader 검증 | [~skcc.x1402] | 2026-08-25 |
| 검증 기준 초안 | [~skcc.x1042] | 2026-08-28 |
```

- 첫 turn에서 `준서TL` 동명이인과 로컬 용어 PSR만 질문, 확정 후 모든 참석자 mention·결정·담당·기한 보존
- 관련 Jira/Confluence/외부 공식 근거만 사용, StarRocks 실제 소비 지원은 미확인으로 유지
- 평가: 4.5 — action/identity는 강함. 외부 source 설명 일부가 아직 “공식 자료” 수준으로 일반적

### PASTE2 — 장애 대화록 Bug

```html
<h3>재현 경로</h3>
<ol><li>prod 환경에서 dag_etl_nightly 실행 결과를 확인한다.</li></ol>
<h3>기대 동작</h3>
<p>prod 환경의 dag_etl_nightly 실행이 오류 없이 정상 완료됨</p>
<h3>실제 동작</h3>
<p>... 커넥션 타임아웃 ... 어제도 같은 시간대 ... 재실행 ... 매일 반복 ...</p>
```

- 질문 0, Bug 1건, dead source·내부 검증 경고 없음
- 평가: 4.8 — 환경·식별자·재발·retry 충실. 실제 동작 문구는 원 대화 어투를 보존해 약간 구어적

### CTX1 / AMB1 / CMP5

- CTX1: fdc 조사 뒤 `DL-9203 priority만 P1` 요청 → 과거 조사·due/comment 없이 priority-only payload
- AMB1: `민서` 후보 둘을 찾은 뒤 exact username 선택 질문만 표시, 초안·내부 경고 없음
- CMP5: 진행중 checklist와 `DL-9092` badge, 담당 mention을 포함한 완결 HTML. 잘린 문단 없음

## 54-case별 Codex 직접 평가

축 순서 `F/G/C/S/R`. 실제 전문은 raw cache에 보존하고, 여기에는 판단에 필요한 차이 중심 발췌만 기록.

### conversation

| Case | 점수 | 축 | 실제 출력 핵심 | 사람 평가 |
|---|---:|---|---|---|
| S1 | 4.7 | 5/4.5/5/4.5/4.5 | Story + 설계/구현/테스트 3 Sub-Task | 중복 단계·가짜 requester 제거 |
| S2 | 4.8 | 5/5/4.5/5/4.5 | Chrome·2홉·빈 화면·기대 그래프 Bug | 재현 사실 보존, 즉시 사용 가능 |
| S3 | 4.7 | 5/4.5/4.5/5/4.5 | 현재 DAG·30분·8컬럼 + 연표 | 현재/이력/근거 구조 안정 |
| S4 | 4.8 | 5/4.5/4.5/5/5 | mention + 미완료 규모·최근 업무 | 한 명으로 축소하던 회귀 없음 |
| S5 | 4.9 | 5/5/5/5/4.5 | 최우선 ticket 한 건과 선택 이유 | 불필요 후보 나열 없음 |
| S6 | 4.4 | 4.5/4.5/4.5/4.5/4 | 완료 2/3, 남은 ticket·blocker | 사실 정확, 표현 일부 반복 |
| S7 | 4.8 | 5/4.5/4.5/5/5 | 내부 적용 이력 + 외부 공식 URL | 내부 readiness와 spec 분리 |
| S8 | 4.6 | 5/4.5/4.5/4.5/4.5 | 출처 평가 + 단일 근거 index | 사람 의사결정 가능, 일부 limitation 일반적 |

### editor

| Case | 점수 | 축 | 실제 출력 핵심 | 사람 평가 |
|---|---:|---|---|---|
| CMP1 | 4.8 | 5/5/4.5/5/4.5 | 완료/진행/남은 일 + 문서 link | Jira 현재 상태 우선 |
| CMP2 | 4.4 | 4.5/4.5/4.5/4.5/4 | 4섹션 본문 | 구조 정상, DoD는 보통 |
| CMP3 | 4.8 | 5/5/4.5/5/4.5 | p95 seed 유지, 방향은 확인 필요 | 정보 발명 없음 |
| CMP4 | 5.0 | 5/5/5/5/5 | 대상·목적·핵심 질문 후 보류 | 모범 interview 경계 |
| CMP5 | 4.7 | 4.5/4.5/4.5/5/5 | 미완료 checklist + badge + mention | 상투적 끝·절단 제거 |
| CMP6 | 4.7 | 5/4.5/4.5/5/4.5 | mention·ticket·document marker | 렌더링 가능한 참조 |
| CMP7 | 5.0 | 5/5/5/5/5 | 무관 레시피 대신 ticket 목적 질문 | context relevance 정상 |
| CMP8 | 4.5 | 4.5/4.5/4.5/4.5/4.5 | 부모 목적/범위, 자식 실행 세부 | 깨진 문장 복구 |
| CMP9 | 4.3 | 4.5/4/4.5/4.5/4 | 확인된 기능만 최소 본문 | 안전하나 전문성 보통 |

### create

| Case | 점수 | 축 | 실제 출력 핵심 | 사람 평가 |
|---|---:|---|---|---|
| ONE1 | 4.4 | 4.5/4.5/4.5/4.5/4 | 단일 Task 즉시 초안 | 과잉 질문 없음 |
| ONE2 | 4.3 | 4.5/4/4.5/4.5/4 | 작은 수정 1 Task | 과잉 분해 없음 |
| STR1 | 4.8 | 5/4.5/5/5/4.5 | 명시 수량만큼 Sub-Task | 건수·구조 정확 |
| STR2 | 4.2 | 4.5/4/4.5/4.5/3.5 | 모듈별 Task | 실행 가능하나 설명 다소 장황 |
| STR3 | 4.4 | 4.5/4.5/4.5/4.5/4 | 근거 없는 Epic 격상 방지 | 보수적 placement |
| PAR1 | 4.5 | 4.5/4.5/4.5/4.5/4.5 | 세 담당별 Sub-Task | username 오독 제거 |
| PAR2 | 4.1 | 4.5/4/4.5/4/3.5 | 지정 Epic 유지 | 일부 배치 설명 불필요 |
| SUB1 | 5.0 | 5/5/5/5/5 | Sub-Task를 부모로 재사용 금지 | hierarchy 정확 |
| SUB2 | 4.4 | 4.5/4.5/4.5/4.5/4 | 기존 자식과 비중복 추가 | 실행 가능 |
| SUB3 | 5.0 | 5/5/5/5/5 | 잘못된 child 생성 보류 | 안전 경계 정확 |
| PASTE1 | 4.9 | 5/5/5/5/4.5 | VoC→재현/기대/실제 | 불필요 재질문 없음 |
| PASTE2 | 4.8 | 5/5/5/4.5/4.5 | prod DAG 장애 Bug | 대화 사실 보존, dead evidence 없음 |
| ASKD1 | 5.0 | 5/5/5/5/5 | 대상 없음 질문 | `알아서` 오용 없음 |
| ASKD2 | 4.5 | 4.5/4.5/4.5/4.5/4.5 | deliverable 질문 후 Sub-Task | 후속값 정확 반영 |
| ASKD3 | 5.0 | 5/5/5/5/5 | comment 내용/목적 질문 | 발명 없이 보류 |
| AMB1 | 5.0 | 5/5/5/5/5 | 동명이인 exact username 질문 | 내부 경고 없음 |
| ASK1 | 5.0 | 5/5/5/5/5 | 범위 질문 | 초안 선생성 없음 |
| ASK2 | 4.6 | 4.5/4.5/4.5/5/4.5 | 여러 turn 필수값 수집 | 질문 경제성 양호 |
| DUP1 | 5.0 | 5/5/5/5/5 | 실제 중복 후보 선택 질문 | 추상 경고 제거 |
| ATTR1 | 4.7 | 5/4.5/5/4.5/4.5 | priority/due/label literal 보존 | reply-payload 일치 |
| ASKD4 | 5.0 | 5/5/5/5/5 | threshold 값 질문 | 주변 속성으로 핵심값 대체 안 함 |
| ATTR2 | 4.5 | 4.5/4.5/4.5/4.5/4.5 | 신규 label 표시 | 불필요 차단 없음 |
| STARR1 | 4.4 | 4.5/4/4.5/4.5/4.5 | Puffin/NDV + 3단계 child | 기술 의미 과장 제거, 더 정밀한 설명 여지 |
| BUG1 | 5.0 | 5/5/5/5/5 | 재현 정보 질문 | 필요한 interview |
| BUG2 | 4.7 | 5/4.5/5/4.5/4.5 | Bug 3섹션 | payload 유효 |
| BUG3 | 5.0 | 5/5/5/5/5 | 중복·재현 확인 전 보류 | 안전 |
| RULE1 | 5.0 | 5/5/5/5/5 | 불법 최상위 Sub-Task 거절/부모 질문 | hierarchy guard |
| RULE2 | 4.7 | 5/4.5/5/4.5/4.5 | 생성 시 Story Point 제외 | field validity 정확 |

### meeting

| Case | 점수 | 축 | 실제 출력 핵심 | 사람 평가 |
|---|---:|---|---|---|
| MTG1 | 4.5 | 5/4/4.5/4.5/4.5 | 결정·5개 표본·PSR·3담당/기한 | 핵심 완전, source 설명 일부 일반적 |
| MTG2 | 4.5 | 4.5/4.5/4.5/4.5/4.5 | 정확히 3 Task·3담당·3기한 | 실행 가능 |
| MTG3 | 4.9 | 5/5/5/5/4.5 | 두 Task에만 결정 comment | comment-only 경계 정확 |
| MTG4 | 4.7 | 5/4.5/5/4.5/4.5 | 지정 title/field/body만 수정 | 범위 혼입 제거 |
| MTG5 | 4.8 | 5/4.5/5/5/4.5 | 호칭·약어 조사 후 질문, 후속 Task | 인터뷰 후 정확히 재개 |

### ctx-chg

| Case | 점수 | 축 | 실제 출력 핵심 | 사람 평가 |
|---|---:|---|---|---|
| CTX1 | 4.8 | 5/5/5/4.5/4.5 | 최신 priority-only mutation | fdc 조사 완전 폐기 |
| CTX2 | 4.7 | 5/4.5/4.5/5/4.5 | 기억 turn 후 DL-9090 진행 조회 | 공유 정보와 요청 분리 |
| CTX3 | 4.9 | 5/5/5/5/4.5 | 취소된 priority/due/comment 제거, title-only | 마지막 요청 authority |
| CTX4 | 5.0 | 5/5/5/5/5 | 사람 질문 뒤 ticket comment-only 복귀 | 필요한 과거 target만 복원 |

## 실제 UI 검증

로컬 dev `http://127.0.0.1:4457/#/ai`, mock data, hot reload 상태에서 Codex가 직접 브라우저 조작.

| 항목 | 결과 |
|---|---|
| source quality table | 전용 class와 620px min-width, 영역 내부 horizontal scroll로 한 글자 폭 붕괴 해소 |
| citation | 본문 `[1]` click 시 evidence panel open + 해당 source flash 확인 |
| ticket detail hover | 번호/type/title/assignee/status/Epic/due/updated tooltip 확인 |
| ticket/person marker | inline code와 badge가 겹치지 않고 각각 renderer가 처리 |
| narrow 600px | 표 자체는 판독 가능. 앱 전체는 232px sidebar를 가진 desktop shell이라 모바일 최적화는 별도 과제 |

UI test에 사용한 답변은 S8 복합 근거 메모. 시스템 진행 trace는 사용자 답변에 노출되지 않았고,
local debug copy 기능에서만 복사 대상. 실제 브라우저 test tab은 사용자 확인을 위해 열린 상태로 인계.

## 오프라인 회귀

| 파일 | 결과 |
|---|---:|
| `tests/test_agent_grounding.py` | 95 passed |
| `tests/test_agent_compose.py` | 40 passed |
| `tests/test_agent_meeting_context.py` | 34 passed |
| `tests/test_agent_draft.py` | 251 passed |
| `tests/test_agent_relevance.py` | 21 passed |
| `tests/test_static_assets.py` | 401 passed |
| 변경 영역 격리 합계 | **842 passed** |
| CI 동일 전체 `pytest` | **1,713 passed, 1 skipped** |

초기 combined run은 실행 중인 local dev와 pytest가 repository SQLite cache를 공유해 progress fixture의
child 목록을 오염시키는 문제를 재현. compose/topic test가 매 test마다 in-memory Jira client를 bind하도록
격리한 뒤, local dev를 그대로 실행한 상태에서 CI와 같은 전체 suite **1,713 passed, 1 skipped** 확인.

## 남은 한계

- 최종 품질 4.71은 만점 아님. MTG1의 외부 source 설명과 S8 limitation은 더 구체화 가능
- v12 primary의 시간·호출·token은 v10보다 증가. 국소 query 제거는 확인했지만 전체 효율 개선은 다음 full run 필요
- 600px 폭에서 source table은 읽히나 앱 전체 responsive layout은 별도 UI context
- reviewer 1명·1회 실행. 0.1점 차이는 평가자/샘플 변동 범위로 해석

## Raw 결과 경로

- primary: `.cache/agent-evaluation/2026-08-16-en-v12-full-quality-r01/`
- primary create/meeting: `.cache/agent-evaluation/2026-08-16-en-v12-full-quality-r02/`
- final UI-quality selection: `.cache/agent-evaluation/2026-08-16-en-v12-focused-r11-ui-review/`
- final ambiguity closure: `.cache/agent-evaluation/2026-08-16-en-v12-focused-r14-warning-closure/`
- final Bug dialogue closure: `.cache/agent-evaluation/2026-08-16-en-v12-focused-r19-runtime-bug-final/`
- final S8 conflict closure: `.cache/agent-evaluation/2026-08-16-en-v12-focused-r25-s8-conflict-final/`
