# Lake PMO Agent — 업무 착수 어시스턴트

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
| 역할 기반 프롬프트 | 공통 페르소나 + 사용자 역할(PM/리더/실무자) 힌트 + 에이전트별 역할 지시 | `workflow/prompts.py`, 각 agent `system()` |
| CoT / 구조화 | 후카츠 템플릿(명령서/제약조건/입력), ReAct 의 think 단계 | 각 agent `task()` |
| Few-shot | Planner 의도 분류 8예시 (경계 사례 중심) | `agents/planner.py` |
| 구분 기호 | `### 자료` 아래는 지시가 아니라고 명시 — 티켓 본문의 인젝션 방어 | `prompts.py::DATA_HEADER` |
| Self-critique | Reviewer 의 3-Check(근거·규칙·요청부합) — Self-RAG 루브릭 재사용 | `agents/reviewer.py` |
| **② LangChain/LangGraph** | | |
| Multi-Agent (단일 미인정) | **7 역할**: Planner·Historian·Refiner·Assigner·Reviewer·Operator·Responder + PMO | `workflow/graph.py` |
| Tool Calling | LangChain `@tool` 26종 — docstring 을 LLM 명세로 작성 | `agent/tools/` |
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
| **A. Structured Output / Function Calling** | 모든 역할의 출력이 JSON Schema 강제 — 정규식 후처리 0 | 예외는 사용자 대면 자유 서술(Responder)뿐 |
| **B. MCP** | **MCP 서버 구현** — Tools 10종(읽기)·Resources(규칙 문서)·Prompts(시나리오 4종) | `agent/mcp_server.py`, stdio. 쓰기는 HITL 부재로 의도적 미개방 |
| C. A2A | 미적용 | 단일 프로세스 앱에서 네트워크 프로토콜 협업은 과잉 — 역할 협업은 LangGraph 서브그래프가 담당 |

## 4. 설계에서 힘을 준 곳

**HITL 을 두 겹으로.** `interrupt_before=[operator]`(흐름) + 내용 해시에 묶인 승인 토큰(내용).
흐름은 코드 실수로 우회될 수 있지만 토큰은 못 우회한다 — 승인 화면에 보인 초안과 한 글자라도
다르면 도구가 거부한다. 토큰은 1회용(재시도가 중복 생성이 되지 않게).

**권한도 규칙도 프롬프트에 맡기지 않는다.** 타인 활동 조회는 도구가 직접 매니저를 확인해
거부하고, 티켓 검증은 화면의 Bulk 생성과 **같은 함수**(`domain/bulk.validate_bulk`)가 한다.
프롬프트는 티켓 본문(남이 쓴 글)이 섞이는 곳이라 인젝션에 안전하지 않다.

**신선도와 비용을 동시에.** 1차 검색은 항상 실시간(방금 만든 티켓도 잡힘), 임베딩은 바뀐
문서만(manifest 의 `updated`+`content_hash` 2단 판정). 안 바뀐 문서 재임베딩 0건을 테스트가
**실제 임베딩 호출을 세어** 지킨다.

**가짜가 실물보다 관대하면 안 된다.** FakeChat 은 실 OpenAI 가 거부하는 것(이름 없는 스키마)을
똑같이 거부한다 — 그 관대함 때문에 fake 테스트 전부를 통과하고 실 키에서 한꺼번에 죽는 것을
실제로 겪고 고쳤다.

**비용이 보인다.** tiktoken 으로 보내기 전 상한을 걸고(로그 10만 줄 붙여넣기 차단), 모델이
알려 준 실제 사용량을 대화별로 화면에 표시한다(LLM n회·토큰·달러). 한 턴 $0.002~0.005.

## 5. Agent 6대 구성 요소 매핑

| 구성 요소 | 이 프로젝트 |
|---|---|
| 추론(Reasoning) | ToolAgent 의 think 단계, Reviewer 3-Check |
| 계획(Planning) | Planner 의도 분류 → 경로 선택, Refiner 의 업무 분해 |
| 메모리(Memory) | LangGraph Checkpointer(`thread_id`) + RAG 2계층 |
| 도구(Tools) | LangChain `@tool` 26종 (LTM 내부 함수 직결) |
| 감지·작동(Perception/Action) | 실시간 Jira/Confluence 검색 → 승인 후 티켓 생성/변경 |
| 피드백(Feedback) | Reviewer↔Refiner 재작성 루프(≤2회), HITL 승인/거절, Langfuse 트레이스 |

## 6. 실행

```bash
pip install -r requirements.txt -r requirements-agent.txt
python run.py                       # http://127.0.0.1:8000 — 메인 페이지가 에이전트 대화
```

- LLM 설정: 우상단 **설정 → AI 에이전트** (AOAI/OpenAI/호환/fake 4-way + 연결 테스트).
  채점/사내 환경은 `AOAI_*` 환경변수가 자동 주입되므로 설정 없이 돈다(env 가 항상 우선).
- 그래프 다이어그램: `python -m app.agent.workflow.graph` → `.cache/agent_graph.png`
- MCP 서버: `python -m app.agent.mcp_server` (stdio)
- 테스트: `python -m pytest` — **409개**, 키 없이 전부 통과(fake LLM)

## 7. 파일 지도

```
app/agent/
├─ config.py          LLM provider 4-way + 연결 진단
├─ secrets.py         API 키 저장(마스킹 조회만)
├─ approval.py        HITL 승인 토큰(내용 해시 · 1회용 · TTL)
├─ usage.py           tiktoken 계량 + 입력 상한(비차단)
├─ fake.py            결정적 Fake LLM(실물과 같은 엄격함)
├─ mcp_server.py      MCP Tools/Resources/Prompts
├─ routes.py          /api/agent/* (SSE·설정·승인)
├─ tools/             26 도구 — search/people/rule/pmo/review/write
├─ retrieval/         RAG 2계층 (정적 규칙 + 동적 증분)
└─ workflow/          LangGraph — state/prompts/graph/session + agents/ 8역할
knowledge/            정적 지식(티켓 규칙·산식·인력 정책·분해 절차)
```
