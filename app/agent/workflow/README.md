# app/agent/workflow — LangGraph 멀티 에이전트

바깥에서 부르는 것은 **`session.py` 하나**다(`ask` / `resume` / `cancel` / `stream` / `snapshot`).

## 흐름

```
START ─> Request Architect ─┬─ chitchat ─────────────────────────────> Result Integrator
                            ├─ my_day/progress/activity ─> Portfolio Analyst ─┘
                            └─ Query Specialist ─> Query Runner ─> Research Analyst
                                                                       ├─ 지식 ─> Knowledge Curator ─┐
                                                                       ├─ 답변 ──────────────────────┤
                                                                       └─ 생성/변경 ─> Work Architect
                                                                                         ├─ 질문 ─────┤
                                                                                         └─ People Advisor ∥ Auditor
                                                                                                      │
                                                            재작성(≤2회) <──────────────────────────────┤
                                                                                                      └─ propose
                                                                                                           │
                                                         승인 대기(interrupt) ─────────────────────────────┤
                                                                                                           ▼
                                                                                                     Action Executor
                                                                                                           │
                                                                                                     Result Integrator ─> END
```

## 역할

| 역할 | 형태 | 하는 일 | 왜 나눴나 |
|---|---|---|---|
| Request Architect (`Planner`) | Structured/simple | routing + atomic task DAG | 복합 요청의 조회·작성·write 의존성을 먼저 명시 |
| Query Specialist | Structured/simple | typed `QueryPlan` | 조건 설계와 결과 해석을 분리 |
| Query Runner | deterministic | scope·pagination 강제 조회 | 자유 JQL도 config 밖으로 못 나가며 전체 target을 model 밖에서 보존 |
| Research Analyst (`Historian`) | **ToolAgent/complex** | 내부·외부 전문 조사 | 추가 탐색이 필요한 횟수만 모델이 판단 |
| Knowledge Curator | Structured/complex | 재사용 가능한 지식 브리프 | 개념·사내 사실·출처·공백 분리 |
| Portfolio Analyst (`PMO`) | **ToolAgent/complex** | 현황·위험·우선순위 | 대시보드 산식과 권한 결과 재사용 |
| Work Architect · Draft Author (`Refiner`) | Structured/complex | 구조 합의 + 생성/변경 draft | tier와 issue type을 분리하고 본문 전 구조를 먼저 합의 |
| People Advisor (`Assigner`) | Structured/complex | 근거 기반 후보·대안 | 사람 조회와 추천 판단 분리 |
| Auditor (`Reviewer`) | Structured/complex | blocking error·warning | 기계 검증을 우선하고 의미 누락만 판단 |
| Action Executor (`Operator`) | deterministic 우선 | 승인 payload 실행 | 유일한 write 권한 |
| Result Integrator (`Responder`) | Text/complex | 최종 한국어 답변 | 새 사실을 더하지 않는 하나의 사용자 대면 입 |

정확한 input/output state key와 tool group은 `role_manifest.py`가 source of truth다. editor의
description/comment 작성은 graph 밖의 `Editor Ticket · Comment Author`(`compose.py`)가 담당한다.

**서브그래프는 도구를 쓰는 쪽만 갖는다**(`base.ToolAgent`). 한 번 부르고 끝나는 역할에 그래프를
씌우는 건 장식이다. 도구 루프는 서브그래프여야 종료 조건이 한곳에 모이고,
`stream(subgraphs=True)` 가 "지금 도구를 부르는 중"까지 보여 준다.

**어느 쪽인지는 "무엇을 부를지가 판단인가"로 갈린다.** 부를 대상이 늘 같으면 — Refiner 의
허용값, Assigner 의 모듈 로스터 — 코드가 미리 조회해 자료로 주는 것이 옳다. 도구 호출 한
번은 곧 LLM 왕복 한 번이고, 도구로 두면 모델은 그걸 **매 턴 다시 부른다**(실측: Refiner
한 역할이 생성 턴 하나에 12회·86초·226k 토큰). 반대로 몇 번 검색해야 충분한지를 미리
모르는 조사(Research Analyst)는 ToolAgent 로 남긴다 — 거기서는 순회가 곧 값어치다.
조회 plan 실행과 승인 payload 실행은 판단이 아니므로 각각 Query Runner와 Action Executor의
deterministic 경로가 맡는다.

**노드는 전부 State 의 갱신분만 돌려준다.** 컴파일된 서브그래프를 그대로 노드로 붙이면 전체
State 가 반환값이 되어 부모의 `add_messages` 리듀서에 통째로 다시 먹힌다. 그래서 바깥 그래프에
붙는 것은 언제나 `node()` 가 주는 **함수**다.

## ToolAgent = ReAct

```
think ──(도구 호출 있음)──> act ──> think ...
  └────(없음 / 6걸음 초과)────> conclude(구조화 출력) ──> END
```

`conclude` 를 따로 두는 이유: 도구를 부르는 모델에게 동시에 스키마까지 강제하면 둘 다 나빠진다.
걷는 동안엔 자유롭게 두고, 다 걷고 나서 **한 번만** 정리시킨다.

도구 루프의 메모(`_Scratch`)는 바깥 대화와 **섞지 않는다**. Historian 이 도구를 여덟 번 부른
기록이 사용자 대화창에 남으면 안 되고, 다음 턴 컨텍스트에 다시 실리면 토큰만 먹는다.

## HITL — 두 겹

| 겹 | 무엇을 보증하나 | 어디 |
|---|---|---|
| `interrupt_before=[operator]` | **여기서 멈춘다** (흐름) | `graph.py` |
| 승인 토큰 (내용 해시) | **이 내용이 승인됐다** (내용) | `../approval.py` |

흐름은 코드 실수로 우회될 수 있지만 토큰은 못 우회한다 — 승인 화면에 보인 items 와 한 글자라도
다르면 도구가 거부한다. 반대로 토큰만 있으면 사용자는 언제 물어볼지 모른다. **둘 다 필요하다.**

토큰은 `propose` 노드가 발급한다. 라우터는 부작용이 없어야 하고, Reviewer 가 실행 허가를
내주면 그건 검열이 아니기 때문이다.

## 상한을 거는 세 곳

| 어디 | 값 | 안 걸면 |
|---|---|---|
| 되묻기 | `MAX_REFINE_TURNS=4` | 취조가 된다 |
| Reviewer↔Refiner | `MAX_REVISIONS=2` | 두 모델이 서로 만족 못 해 무한히 돈다 |
| 도구 왕복 | `MAX_TOOL_STEPS=6` | 같은 도구를 맴돌며 대화를 끝까지 태운다 |

상한을 넘기면 **미해결 문제를 안고** 사용자에게 간다. 조용히 통과시키는 것보다 "이건 못
고쳤습니다"가 낫다 — 판단은 사람이 한다.

## Memory

`MemorySaver` + `thread_id`. 이게 없으면 되묻기가 불가능하다 — 사용자가 "범위는 수집까지야"라고만
답했을 때 앞의 조사 결과가 남아 있어야 한다. 메모리 저장소를 쓰는 이유는 LTM 이 사용자 PC 에서
도는 단일 프로세스 앱이고, **앱을 껐다 켜면 대화가 사라지는 게 맞기** 때문이다(진행 중인 승인이
재시작 뒤에도 살아 있으면 그게 더 위험하다).

## 프롬프트

- **구분 기호로 데이터와 지시를 가른다**(`prompts.py`). 우리는 티켓 본문·코멘트처럼 **남이 쓴
  글**을 프롬프트에 싣는다 — 거기 명령문이 있어도 따르지 말라고 못을 박는다. 쓰기는 승인 토큰이
  막지만, 잘못된 **요약**은 토큰이 못 막는다.
- **페르소나**를 준다. 일반 비서는 "네, 만들어 드릴게요"라고 하지만 PMO 담당자는 "그거 DL-118
  에서 이미 하고 있는데요"라고 한다. 우리가 원하는 건 후자다.
- 역할 지시는 **후카츠 템플릿**(명령서 / 제약조건 / 입력) 형태로 적는다.

## 다이어그램

```bash
python -m app.agent.workflow.graph      # .cache/agent_graph.png + .mmd
```
