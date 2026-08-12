# Lake PMO Agent — 업무 착수 어시스턴트

> SK AX AI Bootcamp 최종 과제 제출물.
> 사내에서 실사용 중인 PMO 대시보드 **Lake Task Manager(LTM)** 위에
> **9역할 LangGraph 멀티에이전트**를 얹어, 자연어로 업무 착수·티켓 조작·현황 조회·
> 지식 질문을 처리합니다. 모든 쓰기는 **사람 승인(HITL) 후**에만 실행됩니다.

## 1. 실행 (Docker — OS 무관)

```bash
docker compose up --build
# → http://localhost:8000  (메인 페이지가 에이전트 대화 화면)
```

- 내장 가상 Jira/Confluence(mock, 12개월 히스토리)로 돌므로 **외부 시스템이 필요 없습니다.**
- 종료: `Ctrl+C` (또는 `docker compose down`)

### LLM 키 설정 — 셋 중 아무 방법이나

| 방법 | 하는 법 |
|---|---|
| ① 환경변수(권장) | `cp .env.example .env` 후 `AOAI_*` 채우고 `docker compose up` — 채점 환경 표준 변수를 그대로 인식합니다 |
| ② 화면에서 | 실행 후 좌측 사이드바 **⚙ 설정** → provider 선택·키 입력 → [지금 확인]으로 연결 테스트 |
| ③ 키 없이 | 설정에서 provider 를 `fake` 로 — 결정적 가짜 LLM 으로 화면·HITL 흐름을 확인할 수 있습니다 |

### 테스트 (선택, 키 불필요)

```bash
docker compose run --rm ltm-agent python -m pytest -q     # 462개, 전부 통과
```

## 2. 이렇게 물어보세요 (대표 시나리오)

첫 화면의 추천 칩을 눌러도 됩니다.

- `실시간 수집 파이프라인에 CDC 방식을 도입해야 한다. 알아서 초안 잡아줘`
  → 과거 조사 → 초안+담당 후보 → **승인 카드**(우측에 티켓 미리보기) → [이대로 생성]
- `데이터 거버넌스 강화 에픽을 하나 새로 만들자` → Epic 인터뷰 → 생성 → Task 연쇄 제안
- `나 오늘 뭐 해야 할까` / `담당자 없는 일 하나 추천해줘`
- `최근 7일간 ETL 모듈 구성원들의 주요 활동 내역` → 로스터→모듈→개인별 3층 보고
- `우선순위가 P1이면서 5일 넘게 업데이트 없는 티켓을 JQL로 찾아줘`
- `DL-101에 라벨 data-quality 추가하고 컴포넌트를 Catalog로 바꿔줘` (승인 후 반영)
- `CDC가 뭐고 우리 프로젝트에서 관련해 뭘 했는지 정리해줘`

> **승인 전에는 아무것도 만들거나 바꾸지 않습니다.** 생성/변경은 항상 승인 카드에서
> 확인 후 실행되며, 승인된 내용과 한 글자라도 다르면 서버가 거부합니다(내용 해시 토큰).

## 3. 과제 요구 기술 대응 요약

| 요구 | 적용 |
|---|---|
| Prompt Engineering | 역할별 md 프롬프트 9종 + 3중 레이어(공통/프로젝트/사용자) · 후카츠 템플릿 · Few-shot · 구분기호 인젝션 방어 · Self-critique(3-Check) |
| LangGraph Multi-Agent | **9역할** StateGraph+서브그래프(ReAct) · Tool Calling `@tool` 31종 · Memory(Checkpointer) · `interrupt_before` HITL |
| RAG | FAISS 2계층 — 정적(규칙·가이드 5문서) + 동적(실시간 검색→증분 색인→의미 재검색) |
| 패키징 | FastAPI `/api/agent/*`(SSE) + Vue3 SPA + Docker |
| 선택 A | 전 역할 Structured Output(JSON Schema 강제, 파싱 0) + Function Calling |
| 선택 B | **MCP 양방향** — 서버(`python -m app.agent.mcp_server`) + 클라이언트(config/agent-mcp.json) |

Agent 개발 지침은 `app/agent/AGENT.md`, 실험·평가 자료는 `research/agent-improvement/`, 기획서는 제출 문서를 참조하세요.

## 4. 구조 (요약)

```
app/
├─ main.py               FastAPI 진입점 (uvicorn app.main:app)
├─ agent/                ★ AI Agent 패키지
│  ├─ workflow/          LangGraph — graph/state/session + agents/ 9역할
│  ├─ tools/             @tool 31종 (HITL 토큰·권한 게이트 내장)
│  ├─ retrieval/         RAG 2계층 (FAISS 정적+동적 증분)
│  ├─ prompts/           common.md + roles/*.md (프롬프트 = 자산)
│  ├─ mcp_server.py      MCP 서버 / mcp_client.py  MCP 클라이언트
│  └─ config.py          LLM provider 4-way + 역할별 모델 티어
├─ static/               Vue3 무빌드 SPA (에이전트 화면 = AgentView.js)
├─ mock/                 가상 세계 데이터 (jira820 위 12개월 히스토리)
└─ ...                   기존 LTM (대시보드·티켓·검색 — production 실사용 코드)
tools/agent_battery.py       실 LLM 검증 배터리(10유형)
tools/agent_scenarios.py     복합 시나리오 12종 + LLM 품질 채점
```

## 5. 참고

- 브라우저는 최초 접속 시 CDN(에디터 라이브러리)에서 리소스를 받습니다 — 인터넷이
  연결된 환경에서 실행해 주세요(LLM API 호출을 위해서도 필요합니다).
- Docker 없이 실행하려면: Python 3.11+ 에서
  `pip install -r requirements.txt` 후
  `python -m uvicorn app.main:app --port 8000` (환경변수 `JIRA_ENV=mock`).
