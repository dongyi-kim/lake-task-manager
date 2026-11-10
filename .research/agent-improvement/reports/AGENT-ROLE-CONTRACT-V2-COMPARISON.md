# Agent 역할·프롬프트 V2 혼합 모델 비교 보고서

> 평가 대상: BASE(`base-3f388b6`) / KO-R(`ko-refactored-v1`) / V2(`ko-role-contract-v2`)
> 실행일: 2026-08-11
> 모델 routing: main/complex=`gpt-4o`, simple=`gpt-4o-mini`
> 데이터: 동일한 mock Jira·Confluence·사람·코멘트
> 인증: 프로젝트가 이미 사용하던 LTM 저장 시크릿의 `openaiApiKey`를 재사용
> 범위: 대화 6개 7턴, Editor Compose 9개, Create 23개

## 1. 결론

V2는 KO-R의 가장 큰 결함이던 진척 조회 거절, context가 있는데 다시 묻는 Compose, 빈 생성
payload를 상당 부분 고쳤다. 일반 조회·이력·사람·내 업무·진척 답변은 세 후보 중 가장 좋거나 BASE와
동급이었다. Jira/Confluence 조회 범위, pagination, 참조 렌더링, JSON/tool fallback 같은 실행 계약도
이전보다 명확해졌다.

그러나 **현재 V2를 BASE 대신 production 기본값으로 채택하면 안 된다.** Create 자동 점수는
`22/23`이지만 사람이 실제 답변과 payload를 함께 읽으면 과잉 분해, 관련 없는 상위 ticket 연결,
reply와 payload의 모순, placeholder 본문, 지시문 누출이 반복된다. 특히 `STR2`, `SUB1`, `SUB3`,
`RULE2`는 자동 통과가 품질 성공을 의미하지 않는 대표적인 false positive다.

사람이 품질만 5점 만점으로 평가한 전체 순위는 다음과 같다.

| 후보 | 대화 6개 | Compose 9개 | Create 23개 | 38개 종합 |
|---|---:|---:|---:|---:|
| BASE | 4.3 | 4.1 | **3.9** | **4.0** |
| KO-R | 3.4 | 3.5 | 3.4 | 3.4 |
| V2 | **4.3** | **4.2** | 3.2 | **3.6** |

따라서 V2는 **KO-R보다 분명히 개선됐지만 BASE를 앞지르지는 못했다.** 조회·조사·작성 계열의 V2
설계는 유지하고, Create의 구조 결정과 reviewer 규칙을 BASE 수준으로 보수한 다음 다시 비교하는 것이
맞다.

## 2. 무엇을 바꿨는가

V2는 영어 prompt를 직역한 버전이 아니다. 공통 계약과 role별 책임을 한국어 원문으로 재작성하되
code, parameter, JSON schema, enum, tool name, Jira field·issue type, JQL, ticket key, user id는 번역하지
않았다. 작성 원칙과 구조는 [AGENT.md](../../../app/agent/AGENT.md)에 정리했다.

| Role | 책임 | 입력 계약 | 출력 계약 |
|---|---|---|---|
| Planner | 복합 요청 분해, 조사·작성·실행 계획 수립 | 사용자 요청, thread context | intent, 단계, 조사 필요성 |
| Query Specialist | 자연어 조건을 허용된 조회식으로 변환 | 계획, search config | bounded JQL/document/comment/person query |
| Query Runner | page 단위 조회와 cursor 진행 | query spec | 결과 page, 다음 cursor, scope 증거 |
| Historian | Jira·Confluence·comment·외부 근거 종합 | query 결과 | 사실, 이력, 상충·공백, provenance |
| Refiner | 요청을 Epic/Task/Sub-Task 초안으로 구체화 | 사용자 조건, 근거 | `draft_items`, `questions`, 구조 계획 |
| Assigner | 담당자 후보와 부하 근거 제안 | 초안, 사람·ticket 정보 | assignment와 대안 |
| Reviewer | 근거·계층·중복·본문 계약 검증 | 초안 전체 | 통과/보류, 문제와 수정 지시 |
| Responder | 사용자에게 보일 결론·질문·승인 요청 작성 | workflow state | 한국어 reply |
| Composer | ticket description/comment 작성 | ticket context, seed, 요청 | HTML 또는 `needsInfo`, typed references |
| Operator | 승인된 변경만 실행 | `approval_token`, 실행 plan | created/updated 결과 |
| Curator | 검색·문서 지식의 정리와 연결 | 자료 묶음 | 정규화된 지식 요약 |
| PMO | Epic 진척·WBS·리스크 집계 | 계층과 상태 | 근거가 있는 진행 보고 |

코드 계약도 함께 정비했다.

- Jira 읽기는 `search.jira.projects`의 **모든 project만** 묵시적으로 적용한다. `project_key` fallback은
  없다. `project_key`는 쓰기 destination이다.
- Confluence 읽기는 `search.confluence.spaces`의 **모든 space만** 적용한다. 빈 search config는
  명시적 오류다.
- `run_jql_v2`는 고정 50건 절단 대신 native pagination과 cursor를 제공한다.
- `search_documents`, `search_comments`, `query_people`를 조회 역할에서 사용한다.
- ticket/document/person 참조를 canonical reference로 정규화하고 badge·mention을 deterministic하게
  렌더링한다.
- 구조화 출력은 `json_schema → json_object → prompt JSON → 1회 repair` 순으로 후퇴하며, native
  tool calling이 없으면 등록된 tool plan으로 전환한다.
- capability probe를 추가했다. 현재 저장된 `openai_compat` endpoint 실측은 connection error였으므로
  해당 서버가 JSON/tool을 지원하지 않는다고 단정하지 않았고, fallback 자체를 unit test로 검증했다.

설계 근거는 OpenAI [Prompting best practices](https://developers.openai.com/api/docs/guides/latest-model#prompting-best-practices),
Anthropic [Building effective agents](https://www.anthropic.com/research/building-effective-agents),
[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents),
Atlassian [Jira search REST API](https://developer.atlassian.com/server/jira/platform/jira-rest-api-example-query-issues-6291606/),
[How and Where to Translate?](https://arxiv.org/abs/2507.22923), [KMMLU](https://arxiv.org/abs/2402.11548),
카카오 [AI 가드레일](https://tech.kakao.com/posts/741), 우아한형제들
[LLMOps](https://techblog.woowahan.com/22839/)을 참고했다. 외부 자료는 prompt 구조·도구 계약을 설계하는
근거로만 사용했고, 실제 채택 판단은 아래 LTM battery 결과로 내렸다.

## 3. 실험 설계와 해석 기준

세 후보 모두 같은 입력, mock world, harness, model routing을 사용했다. 실제 Jira 변경은 하지 않고
승인 전 draft까지만 만들었다. 기존 all-mini 결과는 production topology와 다르므로 여기서 제외했다.

자동 평가는 schema, 필수 필드, item 수, parent, label, 본문 section 같은 **측정 가능한 최소 계약**만
본다. 사람 평가는 다음을 함께 봤다.

1. 사실·상태·날짜가 근거와 맞는가.
2. 이미 가진 context를 쓰고 불필요하게 다시 묻지 않는가.
3. 사용자의 위임과 요청한 계층을 지키는가.
4. reply, 승인 card, 실제 `pending.items`/`draft_items`가 서로 일치하는가.
5. 바로 사용할 수 있는 본문인가, placeholder·지시문·깨진 참조가 남지 않는가.

점수는 5=그대로 사용 가능, 4=가벼운 수정, 3=의미는 맞지만 재작업 필요, 2=중요 오판, 1=사용 불가다.
단일 외부 API run이므로 0.1~0.2점 차이를 통계적 우열로 해석하지 않는다.

## 4. 전체 정량 결과

### 4.1 대화 battery

| 후보 | 턴 | 시간 | LLM calls | prompt tokens | completion tokens | total tokens | cached tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| BASE | 7 | 141.5초 | 35 | 245,357 | 8,529 | 253,886 | 124,032 |
| KO-R | 7 | **133.6초** | **36** | 225,559 | **8,144** | 233,703 | 103,168 |
| V2 | 7 | 251.7초 | 37 | **217,516** | 10,670 | **228,186** | 42,112 |

V2는 BASE보다 token을 25,700개(10.1%) 줄였지만 110.2초(77.9%) 느렸다. KO-R보다 token은
5,517개(2.4%) 적고, 시간은 118.1초(88.4%) 늘었다. 첫 S1 조사 턴 91.5초가 큰 영향을 줬으므로
latency를 prompt 언어 효과로 단정할 수는 없지만, Query Specialist/Historian 추가 호출이 tail
latency를 키운 것은 분명하다.

### 4.2 Compose battery

| 후보 | 자동 통과 | 시간 | calls | total tokens | 추정 cost |
|---|---:|---:|---:|---:|---:|
| BASE | 8/9 | 25.8초 | 미계측 | 미계측 | 미계측 |
| KO-R | 7/9 | **22.3초** | 미계측 | 미계측 | 미계측 |
| V2 | **9/9** | 34.2초 | 8 | 31,708 | $0.091022 |

BASE·KO-R 당시 Compose harness에는 token/call 집계가 없었으므로 없는 값을 추정하지 않았다. V2에서
usage 계측을 추가했다. 자동 통과는 개선됐지만 CMP1 날짜와 CMP3 원인 추정은 사람이 감점했다.

### 4.3 Create battery

| 후보 | 자동 통과 | 시간 | calls | prompt tokens | completion tokens | total tokens | cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| BASE | 19/23 | **629.4초** | **195** | — | — | **1,636,271** | $4.031205 |
| KO-R | 15/23 | 542.3초 | 199 | — | — | 1,495,492 | **$3.453278** |
| V2 | **22/23** | 1,201.7초 | 246 | 2,128,308 | 57,834 | 2,186,142 | $4.879958 |

V2는 BASE보다 자동 통과가 3건 늘었지만 시간 +90.9%, calls +26.2%, tokens +33.6%, cost +21.1%다.
KO-R 대비 자동 통과는 7건 늘었지만 시간 +121.6%, tokens +46.2%, cost +41.3%다. 더 중요한 점은
자동 22건 중 여러 건이 사람 기준 실패라는 것이다.

## 5. 대화 battery: 실제 출력과 평가

각 셀의 정량값은 `시간 / total tokens`다. 인용은 실제 reply에서 차이가 드러나는 부분만 중략했다.

| ID | BASE | KO-R | V2 | 사람 점수 BASE / KO-R / V2 |
|---|---:|---:|---:|---:|
| S1 생성 | 60.1초 / 97,611 | 69.8 / 85,459 | 116.7 / 92,609 | 4.5 / 4.3 / 3.2 |
| S2 Bug | 34.5 / 64,972 | **20.4 / 58,145** | 63.9 / **55,625** | 3.2 / **4.6** / 3.7 |
| S3 이력 | 19.7 / 29,442 | **16.1 / 26,855** | 34.2 / 32,422 | 4.5 / **4.7** / 4.6 |
| S4 사람 | **8.9 / 29,577** | 14.4 / 26,493 | 18.9 / **23,849** | 4.0 / 3.2 / **4.7** |
| S5 내 업무 | 8.6 / 15,487 | **6.3 / 13,596** | 7.9 / **11,248** | **4.8** / 2.5 / **4.8** |
| S6 진척 | 9.7 / 16,797 | **6.6 / 23,155** | 10.1 / **12,433** | 4.5 / 1.0 / **4.8** |

### S1. Iceberg Puffin NDV 생성

> **BASE** — `Task [ETL] Iceberg Puffin NDV 통계 생성 기능 추가`, 상위 `DL-101`, 설계·구현·검증
> Sub-Task 3개.
> **KO-R** — `DL-102` 아래 Task와 설계·구현·테스트 3개.
> **V2** — “작업은 단계별 Sub-Task로 쪼개어 진행됩니다”라고 했지만 실제 child는 “Batch Job 구현”,
> “성능 및 정확성 검증” 2개이며, 관련성이 약한 `DL-5515`, `JIRA820-17`을 참고로 붙였다.

의견: V2는 질문 단계는 정돈됐지만 단계별 설계가 한 단계 줄고 무관한 근거가 늘었다. **BASE > KO-R >
V2**다.

### S2. 리니지 뷰어 Bug

> **BASE** — `Bug [Observability] ...`, 상위 `DL-117`.
> **KO-R** — `Bug [Workbench] ...`, 크롬 재현·기대/실제·담당 `skcc.x1402`.
> **V2** — Workbench와 담당자는 맞지만 상위를 `JIRA820-139`로 연결했다.

의견: 화면 기능은 Workbench이므로 KO-R/V2가 낫다. V2의 cross-project 상위 연결은 생성 시 위험하다.
**KO-R > V2 > BASE**다.

### S3. 데이터 이력

> **BASE** — DAG, 30분 주기, 8개 컬럼, 8건 연표와 진행 Task 2개.
> **KO-R** — 같은 사실에 연표별 근거 번호를 더 촘촘히 연결.
> **V2** — “적재 주기는 30분 1회, 스키마는 8개 컬럼”이라는 결론 뒤 현재 상태·Task·히스토리 표를
> 분리했다.

의견: 세 답변 모두 정확하다. V2의 정보 구조가 가장 읽기 쉽고 KO-R의 traceability가 가장 촘촘하다.
**KO-R ≈ V2 ≥ BASE**다.

### S4. 특정 사람의 현재 업무

> **BASE** — “미완료 티켓은 총 21건”이라고 하고 주요 3건만 제시.
> **KO-R** — 3건만 제시하며 총량을 누락.
> **V2** — “현재 21개의 ETL 관련 작업”이라고 밝히고 10건을 상태·마감·최근 update와 함께 제시.

의견: V2가 가장 완전하면서 token도 가장 적다. 목록을 10건에서 끊었다는 사실만 명시하면 더 좋다.
**V2 > BASE > KO-R**다.

### S5. 지금 시작할 업무

> **BASE** — 마감 지난 P1 `DL-9028` → 오늘 마감 `DL-9029`.
> **KO-R** — 오래된 UI 티켓 `DL-9008`을 먼저 선택.
> **V2** — BASE와 같은 `DL-9028` → `DL-9029`, “즉시 작업”, “오늘 중 완료”로 행동을 명확히 함.

의견: priority와 due date를 함께 본 BASE/V2가 맞다. **BASE ≈ V2 >>> KO-R**다.

### S6. DL-9090 진척

> **BASE** — “하위 작업 3개 중 2개 완료”, `DL-9095` 진행 중, 남은 성능 측정·문서 정리.
> **KO-R** — “WBS에 연결된 Epic이 아니어서 진척률을 집계할 수 없습니다.”
> **V2** — 완료 `DL-9093/9094`, 진행 중 `DL-9095`, 남은 성능 측정·사용 가이드, 마감
> `2026-08-18`까지 제시.

의견: V2는 KO-R의 치명적 회귀를 고쳤고 가장 실무적이다. **V2 > BASE >>> KO-R**다.

## 6. Compose battery: 실제 출력과 평가

| ID | BASE | KO-R | V2 | 시간 BASE / KO-R / V2 | 사람 점수 BASE / KO-R / V2 |
|---|---:|---:|---:|---:|---:|
| CMP1 | ✓ | ✓ | ✓ | 4.1 / 4.9 / 7.6초 | 4.5 / 4.2 / 3.4 |
| CMP2 | ✓ | ✓ | ✓ | 4.2 / 4.3 / 3.7 | 3.6 / 3.3 / 3.6 |
| CMP3 | ✓ | ✓ | ✓ | 2.0 / 2.2 / 4.0 | 4.5 / 3.7 / 3.0 |
| CMP4 | ✓ | ✓ | ✓ | 0 / 0 / 0 | 5.0 / 5.0 / 5.0 |
| CMP5 | ✓ | ✗ | ✓ | 2.4 / 1.4 / 5.4 | 4.5 / 1.0 / **4.8** |
| CMP6 | ✗ | ✗ | ✓ | 1.3 / 1.1 / 2.1 | 1.5 / 1.0 / **5.0** |
| CMP7 | ✓ | ✓ | ✓ | 1.6 / 0.9 / 1.2 | 5.0 / 5.0 / 5.0 |
| CMP8 | ✓ | ✓ | ✓ | 5.7 / 3.6 / 5.9 | 4.5 / 4.5 / 4.2 |
| CMP9 | ✓ | ✓ | ✓ | 4.5 / 3.9 / 4.3 | 4.0 / 4.0 / 3.7 |

CMP4와 CMP7은 세 후보의 안전한 `needsInfo` 답변이 동일하므로 실제 출력 비교를 생략한다.

### CMP1. 진행 공유 코멘트

> **BASE** — “그래프 렌더...업스트림 완료”, “다운스트림 조회 연동 완료”, “남은 작업: 성능 측정 및
> 문서 정리.”
> **KO-R** — 같은 상태에 “사용 가이드 작성”을 추가.
> **V2** — “다음 주 금요일인 8월 18일 마감”이라고 작성.

의견: 2026-08-18은 화요일이다. V2가 source의 날짜는 보존했지만 요일을 만들어 붙여 바로 게시하기
어렵다. **BASE > KO-R > V2**다.

### CMP2. 다운스트림 Task body

> **BASE** — “최근 DL-9071에서 발생한 대량 실패 문제를 해결하기 위해 필요.”
> **KO-R** — “더 나은 데이터 접근성을 제공하기 위해 필수.”
> **V2** — “데이터 흐름의 일관성을 유지하기 위해 필수.”

의견: 세 문장 모두 자료보다 인과를 강하게 만들었다. BASE는 구체적이지만 `DL-9071` 연결이 의심스럽고,
KO-R/V2는 일반론이다. 모두 가벼운 수정이 필요하다.

### CMP3. 작성 중인 p95 코멘트 이어쓰기

> **BASE** — “p95가 생각보다 높게 나왔습니다. 성능 개선이 필요할 것 같습니다.”
> **KO-R** — “기대했던 것보다 높게 나왔습니다”라며 무엇이 높은지 생략.
> **V2** — “주요 원인은 다운스트림 조회 API의 응답 속도가 예상보다 느린 것으로 보입니다.”

의견: V2는 seed를 보존했지만 원인을 새로 추론했다. 사용자의 이어쓰기에는 BASE가 가장 안전하다.

### CMP5. 현재 상태 공유

> **BASE** — 2/3 완료, `DL-9095` 진행 중, 남은 성능 측정·문서 정리.
> **KO-R** — “어떤 구체적인 진행 상황인지 알려 달라”며 거절.
> **V2** — 완료 `DL-9093/9094`, 진행 `DL-9095`, 다운스트림 1.2초, 남은 측정·문서·가이드까지 작성.

의견: context를 실제 문장으로 바꾼 V2가 가장 좋다. **V2 > BASE >>> KO-R**다.

### CMP6. known assignee 멘션

> **BASE** — “검토할 담당자 이름을 알려 주세요.”
> **KO-R** — “현재 담당자는 `[~skcc.x1402]`”라고 하면서도 담당자를 다시 요청.
> **V2** — 실제 mention badge로 `@skcc.x1402 성능 측정 결과를 검토해 주시기 바랍니다.`

의견: V2의 deterministic person reference가 요구를 정확히 해결했다. **V2의 단독 승리**다.

### CMP8. DL-9090 body

> **BASE/KO-R** — 2홉 포함, 3홉 제외, 성능 측정·가이드 DoD.
> **V2** — 같은 범위에 그래프 렌더링, 문서 정리까지 포함.

의견: 모두 usable하다. V2는 parent body에서 child 실행 세부를 조금 많이 반복했다.

### CMP9. 다운스트림 body

> **BASE** — “사용자 요청에 의해 필요성이 제기.”
> **KO-R** — “문제를 사전에 식별하는 데 도움.”
> **V2** — “데이터 플랫폼의 전반적인 성능 향상을 목표.”

의견: 세 후보 모두 입력에 없는 동기를 보강했다. V2의 성능 향상 주장은 특히 확정 근거가 없어 감점한다.

## 7. Create battery: 실제 출력과 평가

자동 결과와 사람 점수를 나란히 보면 false positive가 드러난다.

| ID | 자동 BASE / KO-R / V2 | 시간 BASE / KO-R / V2 | 사람 점수 BASE / KO-R / V2 |
|---|---:|---:|---:|
| ONE1 | ✓ / ✗ / ✗ | 24.0 / 19.8 / 54.0초 | **4.8** / 2.5 / 1.5 |
| ONE2 | ✓ / ✓ / ✓ | 16.4 / 22.0 / 55.4 | **4.5** / **4.5** / 3.3 |
| STR1 | ✓ / ✗ / ✓ | 34.6 / 20.7 / 53.9 | **4.8** / 2.5 / 3.8 |
| STR2 | ✗ / ✗ / ✓ | 59.3 / 57.8 / 107.3 | 2.5 / 2.0 / 1.5 |
| STR3 | ✓ / ✓ / ✓ | 29.7 / 24.5 / 81.1 | 4.0 / **4.2** / 3.3 |
| PAR1 | ✓ / ✗ / ✓ | 29.9 / 14.5 / 49.4 | 4.7 / 2.0 / **4.8** |
| PAR2 | ✓ / ✓ / ✓ | 29.9 / 24.3 / 54.2 | **4.7** / **4.7** / 2.8 |
| SUB1 | ✓ / ✗ / ✓ | 44.9 / 24.6 / 63.8 | 2.5 / **2.8** / 2.0 |
| SUB2 | ✓ / ✗ / ✓ | 29.7 / 14.3 / 46.2 | **4.2** / 1.0 / **4.2** |
| SUB3 | ✓ / ✓ / ✓ | 43.2 / 28.8 / 71.2 | 3.0 / **5.0** / 1.0 |
| PASTE1 | ✓ / ✓ / ✓ | 23.2 / 16.7 / 43.7 | 4.2 / **4.5** / 3.5 |
| PASTE2 | ✗ / ✓ / ✓ | 29.7 / 23.3 / 61.4 | 2.0 / 2.0 / 1.8 |
| ASK1 | ✓ / ✓ / ✓ | 9.3 / 17.1 / 29.8 | **4.0** / **4.0** / 3.5 |
| ASK2 | ✓ / ✗ / ✓ | 38.4 / 30.5 / 52.0 | **4.8** / 1.5 / 4.2 |
| DUP1 | ✓ / ✓ / ✓ | 7.9 / 19.5 / 25.9 | 3.5 / **4.8** / 4.2 |
| ATTR1 | ✓ / ✓ / ✓ | 21.3 / 30.6 / 50.1 | **4.5** / **4.5** / 3.5 |
| ATTR2 | ✓ / ✓ / ✓ | 29.1 / 23.5 / 47.2 | **4.0** / 3.2 / 3.0 |
| STARR1 | ✓ / ✓ / ✓ | 31.2 / 24.7 / 49.7 | **4.8** / 3.8 / 4.5 |
| BUG1 | ✗ / ✓ / ✓ | 23.3 / 18.5 / 39.8 | 4.3 / **4.7** / 4.2 |
| BUG2 | ✓ / ✓ / ✓ | 30.4 / 18.1 / 53.9 | 3.5 / **4.6** / 3.8 |
| BUG3 | ✗ / ✓ / ✓ | 19.1 / 18.1 / 35.6 | **4.0** / 3.5 / 3.5 |
| RULE1 | ✓ / ✓ / ✓ | 6.8 / 11.8 / 32.7 | 2.5 / **4.8** / 4.5 |
| RULE2 | ✓ / ✗ / ✓ | 18.1 / 38.6 / 43.4 | **4.0** / 1.5 / 2.0 |

### ONE1. 단순 단건 요청 + “알아서”

> **BASE** — `Task [Workbench] 쿼리 편집기 단축키 도움말 팝업 추가` 1건.
> **KO-R** — “설계, 구현, 테스트, 검토 및 배포 준비” 5단계를 제안.
> **V2** — 관련 없는 `DL-5367` 아래 설계·구현·테스트·검토·배포 준비로 분해하고 구조 확인을 요구.

의견: 사용자가 위임한 작은 단건을 과분해했다. V2의 가장 선명한 Create 회귀다.

### ONE2. Catalog 체크박스

> **BASE/KO-R** — `Task [Catalog] '내 모듈만' 체크박스`, 상위 `DL-104`.
> **V2** — 같은 Task지만 상위 `DL-5452`.

의견: 본문은 usable하지만 `DL-104 [Catalog] 메타데이터 표준화`가 더 안정적인 상위다.

### STR1. 테이블 30개를 여러 사람에게 분할

> **BASE** — parent Task 1개와 1–10, 11–20, 21–30 child 3개, 담당 분산.
> **KO-R** — “여러 사람에게 나누어 진행”이라고 쓰고 실제 child는 없음.
> **V2** — 실제 child 3개와 담당 분산은 성공했지만 `DataOps`, `DL-5982`를 선택하고 reply에
> `{{ref:DL-5982}}`가 노출됨.

의견: 구조 실행성은 V2가 회복했지만 reference와 module/parent가 불안하다. BASE가 여전히 낫다.

### STR2. 세 산출물의 복합 요청

> **BASE** — 성능 측정·인덱스·가이드 Task 3건이나 module/body가 고르지 않음.
> **KO-R** — 사용 가이드를 중복해 4건.
> **V2** — 5건: Workbench 성능, Workbench 인덱스, Workbench 가이드, Catalog 인덱스, Runtime 인덱스.

의견: V2 자동 통과는 false positive다. 인덱스 작업을 세 module에 중복 생성해 가장 위험하다.

### STR3. 새 Epic과 기존 DL-102 중복

> **BASE** — 새 Epic 대신 ETL Task를 제안.
> **KO-R** — 기존 `DL-102` 아래 단일 Task로 시작.
> **V2** — 기존 `DL-102`를 보존했지만 reply가 `# 명령서`로 시작하고 근거 없는 “20% 개선” DoD를 추가.

의견: 중복 방지는 성공했다. V2는 instruction leakage와 invented target 때문에 바로 게시할 수 없다.

### PAR1. DL-9090 아래 Sub-Task 3개

> **BASE** — 성능 측정·가이드·회귀 test 3개와 담당.
> **KO-R** — “초안 3개”라고 했지만 payload가 비어 있음.
> **V2** — 실제 Sub-Task 3개, parent `DL-9090`, 담당 `skcc.x1402/x1450/x1042`.

의견: V2가 요청을 정확히 실행했다. **V2 ≈ BASE >>> KO-R**다.

### PAR2. 지정 Epic DL-101 아래 Task

> **BASE/KO-R** — `Task [ETL] CDC 재처리 배치 개선`, 상위 `DL-101`.
> **V2** — 요청한 DL-101보다 `JIRA820-212`, `DL-5181`을 앞세워 DataOps 방향과 blocker를 추가.

의견: 사용자가 지정한 parent와 범위를 흐렸다. 조사 결과가 명시적 요청을 덮어쓰면 안 된다.

### SUB1. 기존 Task DL-9095 분할

> **BASE** — “DL-9095 아래에는 Sub-Task를 만들 수 없다”고 하면서 실제 payload에는 parent
> `DL-9095`인 3개가 있음.
> **KO-R** — 단계 초안 설명과 단일 묶음 payload가 어긋남.
> **V2** — “Sub-Task를 만들 수 없다”고 결론내면서 실제 `draft_items`에는 설계·구현·검증 Sub-Task
> 3개가 있다.

의견: 프로젝트 계약은 `Epic → Task → Sub-Task`이므로 DL-9095가 Task라면 분할 가능하다. V2의
reply/reviewer/payload가 서로 충돌한다. 자동 통과를 신뢰할 수 없는 사례다.

### SUB2. DL-9090의 남은 두 작업

> **BASE** — 성능 측정·사용 가이드 Sub-Task 2개.
> **KO-R** — 필요성을 설명하지만 payload 0개.
> **V2** — parent `DL-9090`인 Sub-Task 2개를 실제 생성 초안으로 냄.

의견: V2가 KO-R 회귀를 고쳤다. 두 번째 담당자 공란과 부적절한 대안 근거는 정리할 필요가 있다.

### SUB3. 완료 ticket 아래 회귀 test

> **BASE** — 생성 불가라고 하면서 화면에 초안 표를 노출, 실제 승인 item은 없음.
> **KO-R** — “두 티켓 모두 완료되어 Sub-Task를 추가할 수 없습니다”, payload 없음.
> **V2** — “계획을 진행할 수 없습니다”라고 답했지만 `draft_items`에는 `DL-9093`, `DL-9094` 아래
> Sub-Task 2개가 남아 있음.

의견: V2 자동 통과는 false positive이며 reply와 실행 후보가 정반대다. 이 case는 KO-R이 가장 좋다.

### PASTE1. 컬럼 설명이 보이지 않는 VoC

> **BASE** — Catalog Bug.
> **KO-R** — Workbench Improvement.
> **V2** — Catalog Bug, 재현 경로를 “확인 필요 — 신고자에게 물을 것”로 둔 채 질문 없이 draft 생성.

의견: 기존 기능의 고장인지 기능 gap인지 원문만으로 애매하다. KO-R 해석이 자연스럽고, V2는 draft를
만들려면 `확인 필요`를 확정 사실처럼 숨기지 말아야 한다.

### PASTE2. 야간 batch timeout 채팅

> **BASE** — ETL Bug, `DL-101`.
> **KO-R** — “이 문제는 매일 반복되고 있으며.”
> **V2** — Runtime Bug, `DL-5220`, “매일 반복되는 문제.”

의견: 원문의 “매일 이러면 곤란”은 조건·우려이지 매일 발생했다는 관측이 아니다. 세 후보 모두 사실을
강화했고 V2도 같은 결함을 고치지 못했다.

### ASK1. 매우 모호한 데이터 품질 개선

> **BASE/KO-R** — item 없이 범위·산출물·DoD를 질문.
> **V2** — 관련 `DL-5354`, `DL-5170`을 먼저 제시한 뒤 목표·완료 조건·방식을 질문.

의견: 멈춘 판단은 맞다. V2는 role 계약의 질문 최대 3개를 넘겨 4개 선택지를 내고 관련성이 낮은 작업을
길게 나열했다.

### ASK2. “널 비율 체크, 이번 주” 후속 답변

> **BASE** — `Task [DataOps] 널 비율 체크 구현`, `2026-08-14`.
> **KO-R** — 다시 5단계 구조를 물으며 payload 없음.
> **V2** — `Task [Catalog] 널 비율 체크`, `2026-08-14`, 실제 payload 있음.

의견: V2가 실행성을 회복했다. 다만 reply가 `# 명령서`로 시작하는 누출은 감점이다.

### DUP1. 이미 진행 중인 Avro 전환

> **BASE** — 일반 확인 질문만 하고 existing key를 reply에 쓰지 않음.
> **KO-R** — `DL-9072`, “9개 토픽 중 6개 완료”를 제시.
> **V2** — 같은 중복과 진척을 제시하고 새 ticket 여부를 확인.

의견: KO-R/V2가 중복 안전성에서 낫다. V2는 질문 수를 3개 이하로 줄이고 기존 ticket 계속 사용을
기본 권고로 명시하면 된다.

### ATTR1. P1·금요일·hotfix

> **BASE/KO-R** — Observability Task, P1, `2026-08-14`, `hotfix`.
> **V2** — ETL Task로 해석했지만 P1·날짜·label은 보존. 본문에 “구체적 배경은 사용자에게 확인”을
> 남기고 실제 질문은 하지 않음.

의견: 핵심 attribute 보존은 성공했다. module과 미완성 본문은 BASE/KO-R이 낫다.

### ATTR2. quality-gate label

> **BASE** — Catalog Task, `quality-gate`, 상위 `DL-104`.
> **KO-R** — label은 보존했지만 “필요한 이유를 설명해 주세요” placeholder.
> **V2** — 상위 `DL-9040`, “필요한 이유를 설명합니다”, “점검할 항목을 나열합니다”라는 template 문장.

의견: V2가 schema는 맞췄지만 card가 완성되지 않았다. placeholder를 질문으로 돌리거나 합리적 기본값으로
작성해야 한다.

### STARR1. StarRocks/Puffin/NDV

> **BASE** — ETL Task + 설계·구현·검증 3개, `2026-09-30`.
> **KO-R** — reply는 “새 Epic”이라고 하지만 payload는 Task.
> **V2** — ETL Task + 3개 child, 고유어와 마감 보존, 상위 `DL-102`.

의견: V2가 전문용어와 구조를 잘 보존했다. 상위 근거만 더 엄격히 검증하면 BASE와 동급이다.

### BUG1. 재현 정보가 부족한 intermittent Bug

> **BASE** — 관련 `DL-9090`을 제시하고 선택 질문.
> **KO-R** — 재현 경로·기대·실제를 명시적으로 요청.
> **V2** — 기존 해결 건과 현재 간헐 문제를 구분한 뒤 선택 질문.

의견: 세 후보 모두 생성하지 않은 판단이 맞다. V2는 질문 4개로 role의 최대 3개 계약을 넘었다.

### BUG2. 재현 정보가 충분한 리니지 Bug

> **BASE** — Observability, 담당 `skcc.x1560`.
> **KO-R** — Workbench, 담당 `skcc.x1402`, 상위 `DL-109`.
> **V2** — Workbench, 담당 `skcc.x1402`, 상위 `JIRA820-139`.

의견: module/담당은 KO-R/V2가 맞다. V2의 cross-project 상위 연결은 불필요한 위험이다.

### BUG3. timeout Bug와 기존 이력

> **BASE** — 관련 `DL-5235`, `DL-5405`를 제시하고 질문.
> **KO-R** — 중복 key 없이 이해 확인 후 조사하겠다고 함.
> **V2** — `DL-5235`와 유사하다고 밝히고 재현·기대·실제를 요청.

의견: V2가 중복을 찾았지만 “기존 DL-5235에 추가할지”를 기본 선택으로 주지 않았다. BASE가 조금 더
완전하다.

### RULE1. 부모 없는 Sub-Task

> **BASE** — “부모 티켓 없이 독립적으로 생성”이라는 잘못된 이해를 반복.
> **KO-R** — “Sub-Task는 반드시 부모가 있어야 한다.”
> **V2** — 같은 규칙을 설명하고 일반 Task로 전환을 제안.

의견: KO-R/V2가 정확하다. V2는 부모 key 하나 또는 Task 전환 하나만 물으면 되는데 질문이 과하다.

### RULE2. 생성 시 Story Point 입력 금지

> **BASE** — Story 1건을 만들되 payload에 Story Point를 넣지 않음.
> **KO-R** — Task+5단계로 과분해하고 payload 없음.
> **V2** — Story Point는 넣지 않았지만 body에 “계기를 설명해 주세요”, “범위를 적어주세요”가 남은
> 미완성 Story를 생성.

의견: V2 자동 통과는 schema만 본 결과다. 사람이 쓸 수 있는 초안 기준으로는 실패다.

## 8. 인간 품질 평가의 최종 해석

| 축 | BASE | KO-R | V2 | 해석 |
|---|---:|---:|---:|---|
| 사실·상태 정확성 | **4.1** | 3.5 | 3.8 | V2는 진척·사람 조회가 좋아졌지만 날짜·원인·반복성 추정이 남음 |
| context 활용 | 4.1 | 3.6 | **4.3** | Query Specialist/Historian 추가 효과가 가장 분명한 축 |
| 읽기 품질 | **4.2** | 4.0 | 3.9 | V2의 `# 명령서`, placeholder, raw `{{ref:...}}`가 감점 |
| 완결성·실행성 | **4.1** | 3.1 | 3.6 | V2는 빈 payload를 줄였지만 reply/payload 모순이 남음 |
| 안전성 | 3.8 | **4.1** | 3.7 | V2는 중복 탐지는 좋지만 잘못된 parent와 과잉 생성 위험이 큼 |
| 종합 | **4.0** | 3.4 | **3.6** | V2는 KO-R을 앞섰지만 BASE에는 미달 |

V2를 더 튜닝해 BASE를 앞지를 가능성은 있다. 이미 대화·조사·Compose에서는 그 가능성을 실제로
보였다. 병목은 한국어 자체가 아니라 **Create에서 조사 결과를 구조 결정으로 과도하게 확대하는 것**,
Reviewer가 hierarchy metadata와 실제 payload를 일관되게 보지 못하는 것, Responder가 검증 결과와
반대되는 문장을 내는 것이다.

## 9. 다음 보강 순서

1. `explicit user parent/structure > verified exact duplicate > inferred related ticket` 우선순위를
   deterministic rule로 고정한다.
2. `Task` 아래 `Sub-Task`를 허용하고, 완료 parent는 생성 후보를 제거한다. Reviewer와 payload builder가
   같은 hierarchy source를 사용하게 한다.
3. `reply`, `questions`, `pending.items`, `draft_items`를 최종 단계에서 교차 검증한다. “생성 불가”인데
   item이 있거나 “초안 N건”인데 item이 없으면 hard fail한다.
4. “알아서” + 단순 단건에는 child 분해를 금지한다. 명시적 단계·다인 배정·독립 deliverable이 있을
   때만 분해한다.
5. placeholder 문장과 `# 명령서`, raw `{{ref:...}}`가 최종 응답에 남으면 reject/repair한다.
6. 날짜의 요일은 계산된 값만 쓰고, 조건·희망 문장을 관측 사실로 강화하지 않는다.
7. 질문은 최대 3개로 제한하며 이미 context에 있는 담당자·parent·상태를 다시 묻지 않는다.
8. 위 실패 case를 semantic regression test로 고정한 뒤 mixed routing으로 최소 5회 교차 실행한다.

채택 gate는 다음 세 가지를 동시에 만족해야 한다.

- 사람 품질이 BASE 4.0 이상이며 Create가 3.9 이상
- automatic checker가 아니라 human false-positive case 0건
- BASE 대비 p50/p95 latency와 token/cost 증가가 허용 범위 이내

## 10. 검증과 보존 범위

첫 전체 회귀 테스트의 유일한 실패는 Composer prompt의 기계 계약 문자열 `typed reference` 누락이었다.
이를 보완한 focused test는 `20 passed`였고, 최종 전체 suite는 **`1243 passed, 1 skipped`**로
통과했다. skip 1건과 dependency deprecation warning 5건은 이번 변경의 실패가 아니다.

BASE, KO-R, V2의 대화·Compose·Create 실제 출력 중 차이가 있는 부분과 질문 form, 승인 card,
`pending`/`draft_items`, trace, usage의 판정 결과를 이 보고서에 통합했다. 중복 raw JSON은 저장소에서
정리했다. 이 보고서의 인용은 실제 출력에서 차이가 있는 부분만 중략한 것이며, 자동 checker의 pass/fail을
사람 품질 점수로 대체하지 않았다.
