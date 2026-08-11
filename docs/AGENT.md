# Lake Task Manager Agent — 역할·프롬프트·도구 설계 표준

> AI Bootcamp 최종 과제 **"나만의 End-to-End AI Agent 서비스 개발"** 산출물.
> 사내 PMO 도구 **Lake Task Manager(LTM)** 를 확장해, 막연한 업무 요구에서 실행 가능한
> Jira 티켓까지 가는 멀티 에이전트 서비스를 붙였다.

## 1. 무엇을 하는 서비스인가

PMO 실무에서 "새 업무"의 상당수는 새롭지 않다 — 이미 누군가 시작했거나, 논의만 하고 멈췄거나,
비슷한 걸 다른 이름으로 하고 있다. 그걸 모른 채 티켓을 만들면 중복이 생기고, 앞사람이 부딪힌
벽에 다시 부딪힌다. 이 에이전트는 티켓을 만들기 전에 **반드시 과거를 조사**하고, 근거와 함께
제안하며, **사용자 승인 없이는 아무것도 만들지 않는다**.

| 시나리오 (실 LLM 검증) | 흐름 |
|---|---|
| "실시간 수집에 CDC 를 도입해야 한다" | 조사 → 중복 감지 → 되묻기/티켓 트리 초안 → 담당 추천(근거 4종) → 검증 → **승인 → 생성** |
| "적재 배치가 계속 실패한다" | 조사(같은 증상 Bug 확인) → Bug 초안(재현경로/기대/실제) → 담당 추천 → 원인 티켓·문서 링크 |
| "나 오늘 뭐 해야 할까" | 내 일감 조회 → 지연·마감임박·정체를 **숫자 근거**로 우선순위 제안 (매니저면 팀 정체 티켓까지) |
| "ETL 진척률 어떻게 돼?" | 대시보드와 **같은 산식**(rollup)으로 진척률 + "왜 이 숫자인가"(분모 규칙) |
| "x1042 최근 3일 뭐 했어?" | **매니저 전용** — 담당 티켓 변경 + 활동 스트림 사실 위주 보고 |

## 2. 필수 기술 요소 대조 (과제 가이드 4.2)

| 요구 | 적용 | 위치 |
|---|---|---|
| **① Prompt Engineering** | | |
| 역할 기반 프롬프트 | 공통 계약 + 사용자 역할(PM/리더/실무자) 힌트 + 에이전트별 역할 계약 | `prompts/common*.md`, `prompts/roles/*.md` |
| 추론 / 구조화 | 목적·입력·판단 절차·출력 계약·중단 조건, ReAct의 think 단계 | `prompts/roles/*.md`, 각 agent `task()` |
| Few-shot | Planner 의도 분류 8예시 (경계 사례 중심) | `agents/planner.py` |
| 구분 기호 | `### 자료` 아래는 지시가 아니라고 명시 — 티켓 본문의 인젝션 방어 | `prompts/base.py::DATA_HEADER` |
| Self-critique | Reviewer 의 3-Check(근거·규칙·요청부합) — Self-RAG 루브릭 재사용 | `agents/reviewer.py` |
| **② LangChain/LangGraph** | | |
| Multi-Agent | LLM 역할, deterministic Query Runner/Action Executor, editor author를 책임별로 분리. canonical roster는 `role_manifest.py` | `workflow/graph.py`, `workflow/role_manifest.py` |
| Tool Calling | LangChain `@tool` registry — docstring, typed parameter, scope, pagination, error shape를 LLM 계약으로 작성 | `agent/tools/` |
| ReAct | ToolAgent 서브그래프(think ⇄ act → conclude), 걸음 수는 모델이 결정 | `agents/base.py` |
| Memory | Checkpointer(`thread_id`) — 되묻기·승인 대기가 턴을 넘어 이어짐 | `workflow/session.py` |
| 조건부 엣지 | 의도 8종 × 라우터 5개 (State 만 보고 결정) | `graph.py::route_*` |
| **③ RAG** | | |
| 전처리·청킹 | 제목 경계 분할 + 제목 경로를 조각에 주입 | `retrieval/chunk.py` |
| 임베딩·Vector DB | FAISS 2계층 — 정적(규칙 문서) + 동적(티켓·Confluence 증분) | `retrieval/` |
| 검색 보강 | 실시간 검색(신선도) → manifest 증분 색인 → 의미 재검색("CDC"↔"변경분 실시간 반영") | `retrieval/harvest.py` |
| **④ 서비스 패키징** | | |
| UI | LTM Vue3 SPA 메인 페이지 = 에이전트 대화 (SSE 진행 표시·HITL 승인 카드·근거 클릭) | `static/components/views/AgentView.js` |
| FastAPI 백엔드 | `/api/agent/*` (SSE 스트리밍·설정·승인·연결 테스트) | `agent/routes.py` |

## 3. 선택 요소 (가이드 4.3)

| | 적용 | 비고 |
|---|---|---|
| **A. Structured Output / Function Calling** | 역할 출력은 JSON Schema로 받고 로컬 검증. 서버 미지원 시 `json_schema → json_object → prompt JSON → repair 1회` | 사용자 대면 자유 서술(Responder)은 예외 |
| **B. MCP** | **MCP 서버 구현** — Tools 10종(읽기)·Resources(규칙 문서)·Prompts(시나리오 4종) | `agent/mcp_server.py`, stdio. 쓰기는 HITL 부재로 의도적 미개방 |
| C. A2A | 미적용 | 단일 프로세스 앱에서 네트워크 프로토콜 협업은 과잉 — 역할 협업은 LangGraph 서브그래프가 담당 |

## 4. 설계에서 힘을 준 곳

**HITL 을 두 겹으로.** `interrupt_before=[operator]`(흐름) + 내용 해시에 묶인 승인 토큰(내용).
흐름은 코드 실수로 우회될 수 있지만 토큰은 못 우회한다 — 승인 화면에 보인 초안과 한 글자라도
다르면 도구가 거부한다. 토큰은 1회용(재시도가 중복 생성이 되지 않게).

**권한도 규칙도 프롬프트에 맡기지 않는다.** 타인 활동 조회는 도구가 직접 매니저를 확인해
거부하고, 티켓 검증은 화면의 Bulk 생성과 **같은 함수**(`domain/bulk.validate_bulk`)가 한다.
프롬프트는 티켓 본문(남이 쓴 글)이 섞이는 곳이라 인젝션에 안전하지 않다.

**흩어진 지식은 코드가 모으고, 모델은 판단만 한다.** "이 테이블 지금 적재주기가?",
"Schema Registry 정책이 뭐지?" 같은 질문의 답은 어느 티켓에도 통째로 없다 — 요청 게시글,
개발 티켓 본문, 장애 코멘트, 변경 이력(changelog), 정책 문서, 심지어 **다른 대상 티켓의
코멘트**에 나뉘어 있다. 검색 실력에 맡기면 조각 하나를 빠뜨린 채 그럴듯한 값을 짓는다.
그래서 `find_mentions`(매칭 문장 + 작성자·날짜)·`ticket_field_history`(원본 변경 이력)·
`read_document`(문서 본문)를 코드가 병렬로 돌려 자료로 주입하고, 모델에겐 **"여기 없으면
확인된 기록 없음"** 을 지시한다. 판정 규칙 하나가 이 유형의 정답률을 가른다 —
**현재 값 = 가장 최근 변경 기록**(변경 전 값을 현재로 답하는 것이 전형적 오답이다).

**신선도와 비용을 동시에.** 1차 검색은 항상 실시간(방금 만든 티켓도 잡힘), 임베딩은 바뀐
문서만(manifest 의 `updated`+`content_hash` 2단 판정). 안 바뀐 문서 재임베딩 0건을 테스트가
**실제 임베딩 호출을 세어** 지킨다.

**가짜가 실물보다 관대하면 안 된다.** FakeChat 은 실 OpenAI 가 거부하는 것(이름 없는 스키마)을
똑같이 거부한다 — 그 관대함 때문에 fake 테스트 전부를 통과하고 실 키에서 한꺼번에 죽는 것을
실제로 겪고 고쳤다.

**답변을 실물과 대조한다(접지).** 답변 속 티켓 키·제목·인명을 get_issue/search_users 로
검증해 날조를 잡는다 — 위반 시 실값을 쥐여 주고 1회 재작성, 못 고치면 경고를 보이게 부착.
프롬프트로 세 번 실패한 부류를 코드 검증으로 해결한 사례다.

**비용이 보인다.** tiktoken 으로 보내기 전 상한을 걸고(로그 10만 줄 붙여넣기 차단), 모델이
알려 준 실제 사용량을 대화별로 화면에 표시한다(LLM n회·토큰·달러). 한 턴 $0.002~0.005.

## 5. Agent 구성 요소와 역할 재설계

| 구성 요소 | 이 프로젝트 |
|---|---|
| 추론(Reasoning) | ToolAgent 의 think 단계, Reviewer 3-Check |
| 계획(Planning) | Planner 의도 분류 → 경로 선택, Refiner 의 업무 분해 |
| 메모리(Memory) | LangGraph Checkpointer(`thread_id`) + RAG 2계층 |
| 도구(Tools) | LangChain `@tool` 31종 (LTM 내부 직결 + 웹/GitHub) |
| 감지·작동(Perception/Action) | 실시간 Jira/Confluence 검색 → 승인 후 티켓 생성/변경 |
| 피드백(Feedback) | Reviewer↔Refiner 재작성 루프(≤2회), HITL 승인/거절, Langfuse 트레이스 |

### 5.1 역할 roster와 I/O 경계

아래 표의 source of truth는 `app/agent/workflow/role_manifest.py`다. 기존 class 이름은 checkpoint와
trace 호환을 위해 남아 있지만 prompt와 설계에서는 책임 이름을 사용한다.

| 역할 | model/실행 | 핵심 입력 | 핵심 출력 | 권한 |
|---|---|---|---|---|
| Request Architect (`Planner`) | `simple` Structured | 대화, identity, 기존 plan/draft | `intent`, `request_plan`, 검색 핵심어 | 도구 없음 |
| Query Specialist | `simple` Structured | `request_plan`, key/keyword | strict `QueryPlan` | 도구 없음, 조회 계획만 |
| Query Runner | deterministic | `QueryPlan`, `thread_id` | compact `query_results`, 전체 `query_artifacts` | read-only query/web |
| Research Analyst (`Historian`) | `complex` ToolAgent | query 결과, 후보 지도, 내부·외부 자료 | `situation`, `evidence`, `related_docs`, gaps | read-only search/web |
| Knowledge Curator | `complex` Structured | 조사 결과 | `knowledge_brief` | 도구 없음 |
| Portfolio Analyst (`PMO`) | `complex` ToolAgent | progress/workload/activity | `pmo_findings`, `pmo_caution` | read-only PMO/people |
| Work Architect · Draft Author (`Refiner`) | `complex` Structured | 요청, 조사, capability, 구조 feedback | `structure_plan`, `questions`, `draft`/`change_plan` | draft-only |
| People Advisor (`Assigner`) | `complex` Structured | draft, roster, 이력, workload | 근거가 달린 `assignments` | 도구 없음 |
| Auditor (`Reviewer`) | `complex` Structured | 요청 기준, draft/change, 기계 검증 | blocking `errors`, `warnings`, `review.ok` | 검증-only |
| Action Executor (`Operator`) | deterministic 우선 | 승인 token과 exact payload | `result.created/updated/failed` | 유일한 write 권한 |
| Result Integrator (`Responder`) | `complex` Text | 검증된 모든 결과와 미해결 항목 | 한국어 `reply` | 새 조회·write 없음 |
| Editor Ticket · Comment Author (`compose`) | `complex` Text | `kind`, prompt, seed, ticket context | editor `html`, note, resolved references | draft-only |

역할을 더 나눈 것은 명칭을 늘리기 위해서가 아니다. 자유로운 JQL/CQL 조건, 내부·외부 전문 조사,
복합 요청 분해, 담당 후보 조회, 댓글 작성이 서로 다른 성공 기준과 실패 모드를 가지기 때문이다.
Query Runner와 Action Executor는 LLM 역할이 아니라 deterministic component로 두어 조회 범위와
write 승인 invariant를 prompt 밖에서 보장한다.

### 5.2 Jira/Confluence query invariant

- Jira read scope의 source of truth는 오직 `search.jira.projects` 전체다. `project_key`는 write
  destination이며 조회 fallback이 아니다. config가 비면 조회를 실행하지 않는다.
- Confluence read scope의 source of truth는 오직 `search.confluence.spaces` 전체다. 빈 config는
  전체 space가 아니라 명시적 configuration error다.
- 자유 JQL은 `where`와 `order_by`로 분리하고, 코드가 바깥 `project in (...) AND (...)`를 붙인다.
- `run_jql_v2`는 50건 총량 상한이 없다. Jira의 실제 반환 건수만큼 `startAt`을 옮기고,
  `total`/`hasMore`/opaque `nextCursor`를 반환한다. 전체 target이 필요한 write는 모든 페이지를
  모델 context 밖에서 순회해 exact key snapshot을 승인 payload에 결합한다.
- cursor는 query hash와 `thread_id`에 서명되어 다른 조건·대화에서 재사용할 수 없다.

## 6. 프롬프트 작성 방법과 구조

### 6.1 왜 작성 표준이 필요한가

실사용 피드백을 받을 때마다 해당 문장을 막는 규칙을 기존 prompt 끝에 덧붙이면 당장은 한 case가
좋아지지만 다음 문제가 생긴다.

- 같은 원칙이 common, role, task, tool description에 중복됨.
- 예외가 본 규칙보다 길어져 무엇이 우선인지 불명확해짐.
- 한 역할의 실패를 모든 역할이 읽어 token과 판단 noise가 증가함.
- 문제의 원인이 prompt인지 code/data/schema인지 구분되지 않음.
- 과거 회귀를 고쳤는지 보지 않고 새 문구의 그럴듯함만 검토하게 됨.

이 프로젝트에서는 prompt를 “실패 사례 문장 모음”이 아니라 **계층별 책임이 있는 실행 계약**으로
관리한다.

### 6.2 외부 근거에서 채택한 원칙

| 근거 | 확인된 내용 | 이 프로젝트의 적용 |
|---|---|---|
| [OpenAI Model guidance — Prompting best practices](https://developers.openai.com/api/docs/guides/latest-model#prompting-best-practices) | 반복 지시·불필요한 example·장황한 tool description을 줄이고, 각 지시는 한 번만 쓰며, 대표 task로 변경 전후를 eval. 승인·자율성 경계와 성공 조건을 명시 | 공통 원칙 단일화, role별 tool 최소화, prompt 변경마다 동일 battery 재실행 |
| [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | 복잡성은 측정된 개선이 있을 때만 추가하고, 환경의 ground truth·중단 조건·human checkpoint를 명시 | 고정 조회는 Query Runner, 자유 조사만 ToolAgent, 반복 상한과 HITL 유지 |
| [Anthropic — Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | 도구 목적·입출력·경계를 명확히 하고 realistic eval로 tool call/error/token을 측정. pagination·filter·truncation과 actionable error 권장 | tool registry 충돌 검증, typed parameter, scope metadata, cursor, compact/full artifact 분리 |
| [Atlassian Jira Data Center Search API](https://developer.atlassian.com/server/jira/platform/rest/v10000/api-group-search/) | JQL search는 `startAt`, `maxResults`, `total` pagination metadata를 제공 | `search_issues_page`와 `run_jql_v2`; 요청 크기가 아니라 실제 반환 수로 다음 page 계산 |
| [How and Where to Translate?](https://arxiv.org/abs/2507.22923) | prompt를 identity/rules/candidates/utterance로 분리하면 번역 효과가 component·model·language별로 다름 | 언어를 파일 전체 속성이 아니라 구조 계층별 결정으로 관리. 사용자 발화는 한국어 원문 유지 |
| [OLA: Output Language Alignment](https://arxiv.org/abs/2601.03589) | 한영 혼용 상황에서 최신 model도 기대 출력 언어를 잘못 선택하거나 중간에 언어가 섞임 | 사용자 출력 언어를 common의 명시적 계약으로 두고 별도 language integrity test 실행 |
| [카카오 AI 가드레일](https://tech.kakao.com/posts/741) | 공격 data 번역 과정에서 의미·label이 달라져 재번역·제거·검수가 필요 | 기존 영어 문구의 직역 금지. 요구 의도와 failure mode를 이해해 한국어 원문으로 다시 작성 |
| [우아한형제들 LLMOps](https://techblog.woowahan.com/22839/) | prompt의 분산 관리가 version·재현성을 깨뜨려 중앙 관리와 Golden/Evaluation Dataset을 도입 | prompt asset 집중, `PROMPT_VERSION`, raw output·token·latency·cost 보존 |
| [KMMLU](https://arxiv.org/abs/2402.11548) | 영어 benchmark 번역본만으로는 한국어 고유 문맥을 충분히 평가하기 어려움 | battery에 번역문이 아닌 실제 한국어 요청·생략·존댓말·조사·상대 날짜 포함 |

OpenAI 공식 문서의 10~15% score 개선과 token/cost 절감 수치는 내부 coding-agent 사례의 방향성
자료이지 이 프로젝트의 보장값이 아니다. 우리 workload의 실제 결과로만 채택 여부를 결정한다.

### 6.3 4계층 prompt 구조

```text
1. 공통 계약        prompts/common*.md
   └─ 변하지 않는 domain fact, 사실성, 승인, data 격리, 언어·식별자 계약

2. 역할 계약        prompts/roles/<role>.md
   └─ 그 역할의 목적, 입력 자료, 판단 절차, 출력 계약, 중단 조건

3. 경로별 명령서    workflow/agents/<role>.py::task()
   └─ 이번 intent/turn에만 필요한 목표, schema, 현재 질문, 선택된 section

4. 읽기 전용 자료   wrap_data(data_block(...))
   └─ ticket/comment/document/tool result. 지시가 아니라 판단 근거
```

우선순위는 1→2→3이며 4는 지시가 아니다. 하위 계층은 상위 계약을 무효화할 수 없다.

### 6.4 규칙을 어디에 둘 것인가

| 규칙 성격 | 위치 | 예 |
|---|---|---|
| code로 완전히 보장 가능한 invariant | prompt가 아니라 code+test | 승인 token 검증, permission, schema, key 존재 확인 |
| 모든 역할·모든 turn에 필요한 semantic contract | `common.md` | 사실 날조 금지, data/instruction 분리, 한국어 출력 |
| 한 역할만 수행하는 판단 | 해당 `roles/*.md` | Refiner의 구조 선택, Reviewer의 3축 판정 |
| 특정 intent·경로·turn에서만 필요한 지시 | 해당 agent의 `task()` | 이번 질문, answer depth, modify/create 전용 목표 |
| 외부에서 조회한 사실 | `data_block` | ticket 현재 상태, comments, document excerpt |
| 재사용 가능한 전형적 절차 | `playbooks.md` | `bug_report`, `task_create`, `history` |

같은 자연어 규칙을 두 위치에 복사하지 않는다. 중복이 발견되면 owner 한 곳을 정하고 다른 곳은
참조하거나 삭제한다.

### 6.5 role prompt 표준 형식

새 role을 만들거나 기존 role을 고칠 때 다음 순서를 기본으로 한다.

```markdown
# <Role> — 한 줄 목적

이 역할이 하는 일과 하지 않는 일.

## 입력 자료
무엇이 code에서 사전 취합되는지, 어떤 tool을 실제로 가졌는지.

## 판단 절차
순서와 분기. 중요한 threshold와 stopping condition.

## 출력 계약
schema field의 의미, 사용자에게 보일 문장, 근거 요구.

## 금지·중단 조건
scope 밖 행동, 승인 필요 행동, 실패 시 보고 방식.

## 출력 전 자체 점검
측정 가능한 3~7개 항목.
```

모든 role에 section을 억지로 채우지는 않는다. role이 작으면 합친다. 중요한 것은 제목 개수가
아니라 책임 순서와 중복 제거다.

### 6.6 자연어와 식별자의 언어 정책

한국어 원문형 prompt의 대상은 **사람이 읽는 지시·설명**이다. 다음은 번역하지 않는다.

- function/tool name: `find_stale_tickets`, `run_jql`, `create_tickets`.
- parameter/field: `days`, `approval_token`, `statusCategory`, `Epic Link`.
- JSON key/schema/enum: `questions`, `single_task`, `plan_work`.
- Jira issue type·공식 field: `Epic`, `Story`, `Task`, `Bug`, `Sub-Task`, `Story Point`.
- code, SQL, JQL, HTML tag, ticket key, user id, URL.

설명은 한국어로 쓰되 식별자는 backtick과 원형으로 고정한다. 예:

> `find_stale_tickets(days=2)`로 2일 이상 update가 없는 ticket을 조회한다.

사용자 입력은 한국어 원문을 유지한다. 내부 검색·tool argument를 영어로 바꾸는 별도 pipeline이
필요하다면 prompt 문구 변경이 아니라 독립된 번역 단계와 품질 gate로 설계한다.

### 6.7 feedback를 반영하는 절차

1. **case를 행동 계약으로 다시 쓴다.** “이 문장을 쓰지 마라”가 아니라 “어떤 입력에서 어떤
   상태·출력이어야 하는가”로 정의한다.
2. **원인을 분류한다.** prompt 판단, data 누락, tool contract, schema, code invariant,
   evaluator 결함 중 어디인지 먼저 판정한다.
3. **회귀 test를 먼저 추가한다.** code로 판정 가능한 것은 deterministic test, 의미 품질은
   battery case와 human rubric으로 남긴다.
4. **owner 계층 한 곳만 수정한다.** common에 이미 있는 원칙을 role에 반복하지 않는다.
5. **문구 추가 전에 삭제·통합을 검토한다.** 새 규칙을 넣을 때 모순·중복 문장을 함께 제거한다.
6. **동일 조건으로 비교한다.** model, temperature, data world, scenario, run order를 기록한다.
7. **최종 message까지 평가한다.** structured state가 맞아도 사용자 답변이 사실·근거·caveat를
   빠뜨릴 수 있다.

### 6.8 품질 gate

prompt 변경은 아래를 모두 통과해야 한다.

1. **구조 무결성**: 모든 role asset이 loader에 연결되고 pruning section title이 code와 일치.
2. **식별자 무결성**: function/parameter/schema/enum/Jira field가 번역되지 않음.
3. **정적 회귀**: `test_agent_prompt_integrity`, draft/grounding/postcheck 등 전체 pytest.
4. **대화 battery**: 실제 reply·card·question form, call 수, token, latency, cache token.
5. **Compose/Create battery**: structure뿐 아니라 실제 description/comment body 저장.
6. **사람 정성평가**: 후보 이름을 가리고 정확성, 완결성, 안전성, 읽기 품질을 독립 평가.
7. **반복성**: production 채택 전에는 run order를 바꿔 최소 5회. 1회 결과는 탐색적 증거로 표시.
8. **production model topology 고정**: 모든 후보는 main/complex=`gpt-4o`,
   simple=`gpt-4o-mini`의 같은 routing으로 실행한다. 후보별로 model tuple이나 role tier를
   바꾼 결과는 prompt 비교의 주 결과로 사용하지 않는다.

자동 score가 높아도 근거의 주어·date·상태를 잘못 옮기면 실패다. token/latency 감소도 기존 품질
gate를 통과할 때만 개선으로 센다.

비교 결과 JSON에는 `model`, `simpleModel`, `promptVersion`을 반드시 기록한다. 집계 전에 모든
후보의 `(model, simpleModel)` tuple이 같은지 검증하며, 하나라도 다르면 실행을 중단하거나 별도의
model-routing 실험으로 분리한다. 즉 prompt 언어·구조 실험에서는 **prompt만 독립변수**다.

### 6.9 현재 한국어 원문형 구조

- asset version: `ko-role-contract-v2`.
- 공통 계약은 `common.md`/`common-lite.md` 두 곳으로 제한.
- role roster와 I/O·도구·부작용 경계는 `role_manifest.py`로 고정.
- dynamic task와 tool description의 자연어는 한국어, code contract는 원형 유지.
- 변경 전 BASE/기존 KO와 같은 battery 결과를 비교 보고서에 보존.

## 7. 실행

```bash
pip install -r requirements.txt
python run.py                       # http://127.0.0.1:8000 — 메인 페이지가 에이전트 대화
```

- LLM 설정: 우상단 **설정 → AI 에이전트** (AOAI/OpenAI/호환/fake 4-way + 연결 테스트).
  채점/사내 환경은 `AOAI_*` 환경변수가 자동 주입되므로 설정 없이 돈다(env 가 항상 우선).
- 그래프 다이어그램: `python -m app.agent.workflow.graph` → `.cache/agent_graph.png`
- MCP 서버: `python -m app.agent.mcp_server` (stdio)
- 테스트: `python -m pytest` — 키 없이 전체 회귀 실행(fake LLM)
- 실 LLM 하네스·개발 루프·최근 히스토리·future work: **[HANDOFF.md](HANDOFF.md)** 참조
  (다른 세션이 이어서 작업할 때 이 문서부터)

## 8. 파일 지도

```
app/agent/
├─ config.py          LLM provider 4-way + 연결 진단
├─ secrets.py         API 키 저장(마스킹 조회만)
├─ approval.py        HITL 승인 토큰(내용 해시 · 1회용 · TTL)
├─ usage.py           tiktoken 계량 + 입력 상한(비차단)
├─ fake.py            결정적 Fake LLM(실물과 같은 엄격함)
├─ mcp_server.py      MCP Tools/Resources/Prompts
├─ compose.py         에디터 AI(본문·댓글 생성, 단일 호출 + 뱃지 후처리)
├─ prompts/           프롬프트 자산 — common.md + roles/*.md (한국어 원문형, 식별자는 원형 유지)
├─ routes.py          /api/agent/* (SSE·설정·승인·compose)
├─ tools/             query/search/people/rule/pmo/web/review/write/file 도구 registry
├─ retrieval/         RAG 2계층 (정적 규칙 + 동적 증분)
└─ workflow/          LangGraph — state/graph/session/contracts/role_manifest + 역할 구현
knowledge/            정적 지식(티켓 규칙·산식·인력 정책·분해 절차·본문 가이드·렌더링)
```
