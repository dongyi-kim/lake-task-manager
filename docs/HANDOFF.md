# HANDOFF — Agent 기능 인수인계 (다른 세션/에이전트용)

브랜치 **`feature/ai-agent`** (서브모듈 `lake-task-manager-deploy/lake-task-manager`).
이 문서 하나로 다음 세션이 코드 이해 → 검증 → 이어서 작업까지 가능해야 한다.
아키텍처 개요는 [AGENT.md](AGENT.md), 성능은 [PERF-ROUND2.md](PERF-ROUND2.md),
품질 비교는 [DRAFT-COMPARISON.md](DRAFT-COMPARISON.md) 참조.

## 0. 30초 요약

LTM(FastAPI+Vue3 무빌드 SPA) 안에 in-process 로 얹힌 LangGraph 멀티에이전트
"업무 착수 어시스턴트". 9역할(planner/historian/pmo/curator/refiner/assigner/reviewer/
operator/responder) StateGraph, 모든 쓰기는 HITL 승인 토큰(내용 지문 봉인),
mock(JIRA_ENV=mock, jira820 인메모리)에서 개발, 검증은 fake LLM 기본 + gpt-4o-mini 실측.

**핵심 설계 원칙 (전 코드에 반복됨)**
1. **판단은 모델, 보장은 코드** — 반복문·검증·취합은 코드가 하고(사전취합/가드),
   모델은 읽고 판단만. 프롬프트로 지시하고 코드로 한 번 더 막는다.
2. **재료가 이미 손안에 있으면 순회하지 않는다** — 도구 호출 한 번이 곧 LLM 왕복
   한 번이다. 부를 대상이 늘 같으면 코드가 미리 조회한다(§5-b).
3. **위임("알아서")이 질문을 이긴다** — 되묻기 금지·기본값 채움을 코드가 강제.
4. **HITL 지문**: 승인 화면에 보인 것과 실행되는 것이 sha256 지문으로 같아야 한다
   (`approval.py`). 카드 편집은 State draft 를 고치고 payload 를 재생성(`session._apply_overrides`).
5. **grounding**: 없는 키·틀린 제목·없는 사람은 코드가 잡는다(답변+티켓 본문 둘 다).

**지금 어디까지 왔나 (2026-08-09 기준)**: 지속 시나리오 루프 A~U(실측 결함 ~59건 수정)
→ 성능 최적화(163s→53s, 토큰 −72%) → **역할 정합 감사**(§5-c) 완료. 958 tests green.
다음 할 일은 §7 — **A 두 건은 사용자 확인 대기**, B·C 는 바로 진행 가능.

## 1. 실행·개발 루프

```bash
cd lake-task-manager-deploy/lake-task-manager
JIRA_ENV=mock python -m uvicorn app.main:app --port 8000     # 서버 (mock 세계 자동)
# http://localhost:8000 → AI 탭. LLM 설정은 우측 상단 설정(또는 env LAKE_AGENT_*)
```
- **서버 재기동 절차(중요)**: `netstat -ano | grep :8000` → `taskkill //F //PID <pid>`
  → 재기동 → `/api/health` 의 `rev` 가 HEAD 커밋인지 확인. **stale 서버가 두 번 사고를
  냈다**(옛 코드가 :8000 을 잡고 있어 새 라우트 404, mock 세계 리셋으로 티켓 키 재사용
  — DL-9096 오해 사건).
- mock 세계는 인메모리·결정적(`app/mock/world.py`) — 재기동마다 리셋. `world.py` 의
  **rng 순서·범위 변경 금지**(전체 시퀀스가 이동), 데이터셋 픽스처는 DL-9040~9062.

## 2. 테스트·검증 하네스 (돈 안 드는 것부터)

| 하네스 | 명령 | 용도 · 비용 |
|---|---|---|
| 전체 pytest | `python -m pytest tests/ -q` | **fake LLM + mock** — 그래프 분기·가드·도구 계약·회귀. 현재 **958 passed**, ~2분, $0 |
| 이벤트 스모크 | `LLM_PROVIDER=fake JIRA_ENV=mock python -X utf8 <스크립트>` 로 `session.stream()` 소비 | SSE 이벤트 모양(plan/step/node/token) 확인, $0 |
| 성능 | `python -X utf8 tools/agent_perf.py` | 실 LLM(mini). 4시나리오 역할별 시간·토큰·캐시·TTFT. **현재 기준 53s · 18회 · 106k tok** |
| **UI 시나리오** | `python -X utf8 tools/agent_ui_probe.py cases.json [--out r.md]` | 실 LLM + 브라우저. 답변 전문 + **렌더 위반** + **초안 본문 전문**(서버 스냅샷). 텍스트 probe 로는 절반만 보인다 |
| 정적 자산·소스 위생 | (pytest 에 포함) `tests/test_static_assets.py` | 제어문자·JS 파싱·템플릿의 모듈 직접 호출·인라인 핸들러 + **파이썬 소스의 뭉개진 줄·제어문자**. **조용히 깨지는 사고**를 커밋 전에 잡는다, $0 |
| **프롬프트 정합** | (pytest 에 포함) `tests/test_agent_prompt_integrity.py` | md 가 **그 역할에 없는 도구**를 시키면 실패. 도구를 걷어냈는데 md 가 남아 열 군데서 유령 도구를 부르라 시킨 사고를 잡는다, $0 |
| **정성 판독** | `python -X utf8 tools/agent_quality_read.py [모델] [CREATE\|UNASSIGNED\|MODIFY]` | 실 LLM(mini). 답변 전문·초안 본문·담당 근거·변경 계획을 **통째로** 찍는다. 시간·토큰이 아니라 "실제로 뭐라고 나왔나"를 읽는 자리 |
| 조회·지식 배터리 | `python -X utf8 tools/agent_scenarios.py gpt-4o-mini DATA1 DATA5 PROG1 …` | 실 LLM. 체커+judge. DATA*=자산 지식, PROG*=진척 |
| **생성 스위트** | `python -X utf8 tools/agent_create_suite.py gpt-4o-mini [케이스ID…]` | 실 LLM. **20케이스**(ONE/STR/PAR/SUB/PASTE/ASK/DUP/ATTR/RULE/STARR1). 전체 ~$0.3 |
| 초안 심층 평가 | `python -X utf8 tools/agent_draft_eval.py` | 실 LLM. 정량 축 + judge **5축**(topic/body/roles/refs/placement) |
| 브라우저 | 서버 띄우고 AI 탭 | 진행 체크리스트·뱃지·카드 ✎수정·승인 E2E |

**평가 규율(사용자 지시로 확립)**: 배터리 green 은 회귀 방지 신호일 뿐 — 품질 주장은
①실제 출력 전문을 직접 읽은 정성 평가 ②사전에 설계한 기대 결과와 대조 ③(중요 건)
Claude 레퍼런스 초안과 비교(DRAFT-COMPARISON.md)로만 한다. judge 도 개선 대상이다
(topic 축은 원문 첫 턴을 줘야 잡는다 — 이걸 몰라 실사용 사고를 4점으로 채점했었다).

**실 LLM 비용 규율**: 검증 기본은 fake. 실 키(개인 크레딧)는 기능상 필요한 확인에만,
케이스를 골라서 돌린다. 대량 연속 실행은 TPM 백오프로 수치가 오염된다(planner 13~20s
스파이크는 구조 문제가 아니라 레이트리밋 — PERF-ROUND2.md 진단 기록 참조).

## 3. 코드 지도 (agent 부분)

```
app/agent/
├─ config.py        # provider 4-way(get_llm/get_embeddings), stream_usage, 429 재시도, llm_ready()
├─ approval.py      # HITL 토큰·지문(stage/approve/consume/amend_payload)
├─ compose.py       # 에디터 AI(단일 호출) + _badgeify(평문→뱃지 마크업)
├─ usage.py         # Meter — 역할·도구별 시간/토큰/캐시/비용, tiktoken 입력 가드
├─ routes.py        # /api/agent/* (chat/stream SSE·approve·cancel·compose·status)
├─ tools/           # @tool 26+종. _ident.py(식별자), search/survey/write/pmo/rag/file…
├─ retrieval/       # 정적 FAISS(knowledge/) + 동적 증분 인덱스(harvest/chunk/manifest)
├─ prompts/         # common.md(공통 페르소나), roles/*.md(역할 지시), base.py(persona 조립 — 캐시 정렬)
└─ workflow/
   ├─ state.py      # AgentState·Intent·Node·request_text()(원 요청 고정)·trace 리듀서
   ├─ graph.py      # StateGraph 조립 + 라우터(해석확인 분기·빠른 경로·fan-out)
   ├─ session.py    # ask/stream/resume(카드 편집 반영)/snapshot·이벤트 humanize(_events)
   ├─ grounding.py  # 날조 키·제목·사람 검출(답변+초안 본문)
   └─ agents/       # base(StructuredAgent/ToolAgent/TextAgent)·9역할. refiner.py 가 가장 두껍다(가드 무리)
knowledge/          # 정적 RAG 원천 — 04(구조 판단)·07(본문 가이드+최소 요건 슬롯)·08(렌더링)
tools/agent_*.py    # 위 하네스들
app/static/…/AgentView.js  # 챗 UI(플랜 체크리스트·뱃지 augment·카드 편집·대화 전환)
```

**refiner.py 의 가드 순서가 곧 로직이다** — 순서 민감. 두 갈래로 갈라져 있다:

`apply()` (초안 갈래, 494줄): 지정담당 강제(_apply_named_assignees) → 껍데기→Sub-Task
변환(+보정 분할) → subtask 모드 children 제거+지정 재적용 → 참고 병합(_merge_refs) →
날조 불릿 제거 → 주제 가드(_topic_drift→Reviewer 우회 금지) → Epic 실재·모듈 불일치 →
번호·단계 접기(_base_title) → 균등 배분 → 자식 담당 채움(부모 담당 폴백) → 구조-산출
어긋남 보정 → 하향 편향 보정(위임이면 _split_into_children) → 우선순위 정규화 →
PMO_VIT 제거 → **`_change_plan()` 호출** → 초안 병합("하나 더 추가해줘").

`_change_plan()` (변경 갈래, 285줄): 생성 요청에 변경 계획 금지 → 빈 값 제거 →
**필드 범위 제한**(말한 것만) → 우선순위 정규화 → **상대 날짜 코드 계산** + 방향 검증
("미뤄"인데 앞당기면 확인 요청) → before 값 조회 → 전이 해석(상태명→id) → 링크 조립 →
일괄(JQL 대상+필드) 조립 → 사번 실재 확인 → 초안 수정을 티켓 변경으로 오인한 것 폐기.

> 나눈 이유: 초안을 다듬는 일과 기존 티켓을 고치는 일은 재료도 실패 방식도 다른데
> 한 함수(773줄)에 있어 어느 가드가 어느 갈래의 것인지 읽어서는 알 수 없었다.

## 4. 대화 흐름 (2026-08-09 현재)

```
사용자 요청
 └ Planner(의도·sufficient·request_text 고정)
    ├ 막연한 plan_work·첫 턴·비위임 → Refiner(해석 확인: interpretation + 질문 2~3
    │    — 범위/모듈/Epic 배치. 슬롯 룰 _slot_audit: ASK/INFER/LATER) → 사용자 답
    ├ my_day/progress/activity → PMO(사전취합: 그룹활동·티켓진척) → Responder
    ├ ask+식별자 → Historian(topic_dossier 직결) → Curator → Responder
    └ 충분/위임/후속 → Historian(조사) → Refiner(초안) → Assigner∥Reviewer
         → 승인 카드(카드에서 ✎ 직접 수정 가능) → Operator(승인 후 생성) → Responder
```
SSE 이벤트: `start → plan(체크리스트) → step(도구 humanize)·node(단계 완료) →
token(Responder 스트리밍) → final`. UI 는 플랜 단위 [✓]/[▸]/[ ] + 세부 중첩 폴딩.

## 5. 최근 히스토리 (커밋 타임라인, 아래가 최신)

| 커밋 | 내용 |
|---|---|
| `37111d9` | 성능 라운드2(L): 155s→125s, 프롬프트 캐시 정렬, 사전취합 직결, 토큰 스트리밍 |
| `a2267c0` | 진행 표시 = 플랜 체크리스트 + 도구 humanize("도구 실행 결과 수신" 제거) |
| `aa21583` | 언급 전부 뱃지(티켓/문서/사람) — plain text 금지, 표 나열만 예외 |
| `3dcf37c` | prod 대비 사전취합 병렬화(progress_report 5갈래 등) |
| `33ac843` | **품질 코어**: request_text 고정·주제 가드·본문 4섹션 통일(참고 병합·날조 삭제)·draft 전문 주입·knowledge/07 개정 |
| `a328919` | 승인 카드 인라인 편집(제목·본문·라벨·마감·Epic·자식) — 지문 재생성 |
| `8ea1805` | 평가 강화: STARR1·judge 5축(topic)·구조 접기·위임 분할 보정·DRAFT-COMPARISON |
| `6eb8812` | **해석 확인 선행 턴**(호흡 단축) + 위임·지정 보장 가드 6종 + 대화 전환 허용 |
| `2f926ae`~`5d0786a` | **지속 시나리오 루프 Round M~U** — 실측 결함 ~21건(상담형 대안·워크로드 재배분·mentioned_keys 오염·말한 것만 변경·모듈 비교 지표·초안 편집 규율·문서 요약 본문화) |
| `2db9bea`~`49efbe7` | **채팅 UX·렌더 라운드**(사용자 지적 5묶음) — 중단 버튼·대화별 진행 표시·참조/근거 재설계(뱃지→하이퍼링크·출처/설명 2층)·사람 칩·정규식 `\b`가 0x08 로 박힌 사고 근절 + 정적 자산 테스트 도입 |
| `6f889ad`~`697d7c6` | **성능 최적화 라운드** — 163s→53s · 39→18회 · 374k→106k. Refiner·Assigner 도구 제거(사전취합으로), 프롬프트 절 선택 조립, my_day 사전취합 |
| `98dda02` | **역할 정합 감사** — 유령 도구 10건·역할 간 모순·경계 위반·회귀 3건. 아래 §5-c |

배경 사건: 실사용 STARR NDV 테스트에서 주제 이탈·본문 오염·구조 오판이 드러남 →
"배터리 green ≠ 품질" 규율 확립 → 33ac843~6eb8812 라운드로 교정. 상세 갭 장부는
DRAFT-COMPARISON.md.

## 5-b. 성능 설계 원칙 (2026-08-09 최적화 라운드에서 확립)

> **재료가 이미 손안에 있으면 순회하지 않는다.**

도구 호출 한 번은 곧 **LLM 왕복 한 번**이다. 그런데 역할이 가진 도구가 코드 사전취합과
중복이면 모델은 그것을 매 턴 다시 부른다 — 생성 턴 하나에 refiner 만 12회·226k 토큰을
먹은 것이 그래서였다. 판단이 아닌 조회는 코드가 하고, 모델에게는 **결과만** 준다.

| 역할 | 형태 | 근거 |
|---|---|---|
| Refiner | **StructuredAgent(도구 0)** | 허용값·Epic 후보·규칙·전이·검증이 전부 사전취합/Reviewer 와 중복이었다 |
| Assigner | **StructuredAgent(도구 0)** | 부르는 대상이 늘 같다(초안이 정한 모듈) → `_roster_load` 병렬 조회 |
| PMO | ToolAgent(max_steps 8) | my_day·그룹활동·비교·주간보고는 사전취합, 자유 조회만 ReAct |
| Historian | ToolAgent(max_steps 7) | **진짜 조사** — 몇 번 검색해야 충분한지는 미리 모른다. 여기는 남긴다 |

프롬프트도 같은 원리다 — `prompts/roles.py::compose()` 로 **경로에 안 쓰이는 절**을 뺀다
(순수 modify 턴에서 생성 지시 2.3k 토큰). 절 제목이 어긋나면 조용히 아무것도 안 빠지므로
제목 존재를 테스트가 지킨다(`test_section_titles_used_for_pruning_really_exist`).

**측정 기록**(gpt-4o-mini, agent_perf 4시나리오):

| | 시간 | LLM 호출 | 토큰 |
|---|---|---|---|
| 최적화 전 | 163s | 39회 | 374k |
| 최적화 후 | **53s** | **18회** | **106k** |
| | −67% | −54% | −72% |

생성 턴 119s/276k → 24s/54k · 조회 턴 18s/30k → 13s/13k

## 5-c. 역할 정합 감사 (2026-08-09, `98dda02`) — 왜 필요했나

성능 라운드에서 Refiner·Assigner 를 `ToolAgent` → `StructuredAgent` 로 바꾸며 도구를
전부 걷어냈다. **그런데 md 는 그대로 남아** "먼저 `search_rules` 를 불러라",
"`get_module_people` 로 후보를 모아라" 하고 **열 군데서** 시키고 있었다. 코드는 멀쩡히
돌고 849 테스트도 전부 통과했다 — 모델만 없는 도구를 찾아 헤맸다.

> **교훈: 역할의 형태를 바꾸면 그 역할의 프롬프트도 같이 바뀌어야 한다.**
> 코드와 프롬프트는 한 몸인데 테스트는 코드만 본다. 그래서 테스트를 하나 늘렸다
> (`test_agent_prompt_integrity.py` — 고치기 전 md 로 되돌려 실제로 4건 잡는 것 확인).

같은 감사에서 나온 나머지:

| 종류 | 무엇 |
|---|---|
| 역할 간 모순 | refiner.md 는 "prose 로 쓴 Sub-Task 후보는 결함", reviewer.md 는 "절대 문제 삼지 마라" |
| **경계 위반** | Assigner 가 "x1450 은 진행중 15건이라 부적합"이라 써 놓고 **자식 2건이 그 사람에게** 갔다. `draft_text` 에 자식이 안 보여 Assigner 가 배분할 수 없었다 → 자식 담당을 Assigner 로 이관 |
| 사전취합 회귀 | my_day 직결이 '미배정'이라는 **다른 기준**을 가로챘다(ReAct 에만 있던 `find_unassigned_tickets` 가 도달 불능) |
| 답변 결함 | "미뤄줘"인데 8-27→8-14 로 당긴 것을 아무도 안 짚음 · 같은 문장 두 번 |
| 카드 불일치 | `(구조: …)` 근거 줄을 **두 곳에서** 붙여, 재작성 왕복이 있으면 헤더(새 이유)와 근거 줄(옛 이유)이 달랐다 → 맨 끝에서 한 번만, 옛 줄은 지우고 다시 쓴다 |
| 코드 | `refiner.apply` 773→494줄(`_change_plan` 분리) · 줄바꿈이 공백으로 뭉개진 줄 5건 복구 |

> **사전취합의 함정(반복 주의)**: 사전취합이 자라 ReAct 를 건너뛰게 되면, **ReAct 에만
> 있던 도구는 조용히 도달 불능**이 된다. 재료에 넣을 것은 "모델이 부를 법한 것"이 아니라
> **질문이 요구하는 것**이다. 직결(L3a) 을 늘릴 때마다 이 갈래를 점검할 것.

## 5-d. 역할 총정리 (2026-08-09 검토 — 결론과 근거)

현재 **9역할**(+ 그래프 밖 Composer, 코드 노드 `merge_assignments`·`propose`).

| 역할 | 형태 | 도구 | 판단하는 것 |
|---|---|---|---|
| Planner | Structured | 0 | 의도 8종 + 검색어 |
| **Historian** | ToolAgent | 20 | 이 일이 처음인가 — **몇 번 찾아야 할지 미리 모른다** |
| PMO | ToolAgent | 11 | 지금 상태 집계(my_day/progress/activity) |
| Curator | Structured | 0 | 조사 결과 → 지식 브리프(개념/우리상황/참고/공백) |
| **Refiner** | Structured | 0 | 물을 것 / 만들 것 / 고칠 것 |
| Assigner | Structured | 0 | 누가 할 것인가(부모 + 자식) |
| Reviewer | Structured | 0 | 3-Check 자기검열 |
| Operator | ToolAgent | 13 | 승인된 것 실행 |
| Responder | Text | 0 | 사용자에게 말하기 |

### 비대의 정체 — 셋의 병이 다르다

| 역할 | 총줄 | 역할 본체 | 사전취합/후처리 | 진단 |
|---|---|---|---|---|
| Historian | 821 | ~122 | **~598 (73%)** | 역할은 얇고 **코드가 두껍다** |
| PMO | 586 | ~128 | **~364 (62%)** | 같음 |
| Responder | 533 | **task() 215** | ~196 | **프롬프트가 두껍다** |

- **Historian** `node.run` 안에 조건부 사전취합 블록이 **11개**: ①이웃 지도 ②topic_dossier
  ③presurvey ④지목 티켓 현재 사실 ⑤첨부 ⑥재배분 후보 ⑦일괄수정 JQL ⑧온보딩 ⑨허용값
  ⑩웹·GitHub ⑪진척 숫자. 각 블록은 "모델에게 맡겼더니 틀렸다"는 실측에서 나왔다.
- **PMO** 사전취합 5함수(`_my_day`/`_group_activity`/`_ticket_progress`/`_module_compare`/
  `_self_report`) + L3a 직결.
- **Responder** `task()` 는 **10갈래 if/elif** 의 한국어 명령서가 파이썬 문자열로 박혀 있다.
  다른 아홉 역할의 프롬프트는 전부 `roles/*.md` 자산인데 **여기만 원칙 밖**이다
  (`roles.py` 독스트링: "프롬프트는 코드가 아니라 편집하는 자산").

### 결론 — 역할을 나눌 일은 아니다. 단 하나만 빼고

| 후보 | 판단 | 근거 |
|---|---|---|
| **Refiner → Refiner + Modifier** | **분할 권고**(미착수) | 아래 |
| Historian 분할 | 반대 | 조사는 하나다. 다만 재료 코드를 모듈로 뺄 것 |
| PMO 3의도 분할 | 반대 | 지나는 길·도구 묶음이 같다. 재료 코드만 분리 |
| Responder 분할 | 반대 | 입이 갈리면 말투가 갈린다(합친 이유). 명령서를 md 로 뺄 것 |
| Assigner → Refiner 병합 | 반대 | 로스터·부하는 초안이 모듈을 정한 **뒤에야** 조회할 수 있다 |
| Curator → Responder 병합 | 반대 | Responder 는 문장을 만드는 역할이지 지식을 구조화하는 역할이 아니다. gaps 를 스키마로 강제하는 자리가 사라진다 |
| Reviewer 제거 | 반대 | 기계 검증은 이미 코드(`validate_bulk`). 남은 LLM 부분이 Self-RAG 3-Check — 과제 평가의 핵심 개념 |
| 답변 검증 역할 신설 | 불필요 | `grounding.check()` 가 코드로 이미 한다(결정적·무료) |

**Refiner 분할 근거**(추측이 아니라 코드에 남은 증거):

1. **프롬프트가 21% 만 겹친다** — 실측: 생성 전용 2,266 tok / 수정 전용 203 tok / 공통 638 tok
2. **출력이 다르다** — `items[]` vs `change{}`. 한 스키마에 둘 다 있어 매 턴 안 쓰는 절반이 실린다
3. **가드가 이미 갈라져 있다** — `apply`(494) vs `_change_plan`(285)
4. **★ 서로를 오염시킨 가드가 실재한다** — 이 둘은 한 역할이 두 일을 해서만 존재한다:
   - `refiner.py` "새 일을 만들라는 요청에 변경 계획을 내지 않는다" (실측: 부탁한 생성이
     사라지고 시키지 않은 수정이 카드에 올랐다)
   - `refiner.py` "초안 수정 요청인데 기존 티켓 변경 계획을 냈다" (실측: DL-109 로 샜다)

**함께 볼 것**: Historian 의 사전취합 블록 ⑥⑦⑧⑨(재배분·일괄수정·온보딩·허용값)는
**조사가 아니라 조회**다. Historian 에 있는 이유는 Planner 가 거기로 보내기 때문이지
조사라서가 아니다 — Refiner 분할과 같은 성격의 경계 문제라 함께 처리하는 게 낫다.

## 6. 현재 상태 (2026-08-09 지속 루프 Round A~U + 정합 감사 후)

- **958 pytest green**. 지속 시나리오 루프(probe 러너 `tools/agent_probe.py`·브라우저
  러너 `tools/agent_ui_probe.py` — 체커 없는 정성 판독)를 Round A~U 까지 돌며 실측 결함
  **~59건 수정**, 기능 3종 추가(조건 일괄 수정 update_tickets 카드 / 상태 전이
  change.status / 티켓 링크 change.link), 도구 버그 1건 발굴(list_transitions 가 mock
  에서 항상 error). **루프는 사용자 지시로 종료**했고, 이어 성능 최적화 → 역할 정합
  감사(§5-b·5-c) 순으로 진행했다
- 사전취합(코드 보장) 추가분: 허용값(라벨 목록)·온보딩(모듈+Epic)·첨부(목록+내용)·
  PMO_VIT 현안·재배분 후보·일괄 수정 대상(JQL)·주간보고(본인 활동)·Epic 트리
- 최종 보장(코드 조립) 추가분: 전이(요청 상태명 1차)·링크(키 2+관계 낱말)·일괄
  변경(P/마감/담당 파싱)·Epic mode 승격·초안 피드백(승인 대기 draft 수정 재시도)
- mentioned_keys 는 티켓 키 형식만 통과(사번 오염이 조사를 건너뛰게 했던 근본 원인)
- 서버 재기동 시 rev 가 HEAD 인지 확인. 미커밋 산출물 없음(작업 전 `git status`)
- **§7 의 위 두 항목은 사용자 확인 대기 중** — 라우팅이 걸린 구조 변경이라 임의로
  시작하지 않았다. 나머지는 확인 없이 진행 가능.

## 7. Future work (우선순위순)

### A. 구조 — 사용자 확인 대기 (라우팅이 걸림, 둘은 같은 문제의 앞뒤라 함께)

1. **Refiner → Refiner + Modifier 분할** — 근거는 §5-d. 착수 시 순서:
   ①`roles/modifier.md`(수정 전용 절 + 공통) ②`Modifier(StructuredAgent)` 신설,
   `_change_plan` 을 그 역할의 `apply` 로 이동 ③`route_after_planner`/`route_after_historian`
   에서 **초안이 없는 modify** 만 modifier 로(승인 대기 초안 수정은 Refiner 몫 — 판정은
   `(state["draft"] or {}).get("items")`) ④교차 오염 가드 2건 **삭제**(존재 이유가 사라진다)
   ⑤`test_agent_draft.py`·`test_agent_graph.py` 라우팅 케이스 갱신
2. **Historian 의 조회 블록 재배치** — `node.run` 의 ⑥재배분 ⑦일괄수정 JQL ⑧온보딩
   ⑨허용값은 조사가 아니라 조회다. Planner 라우팅과 함께 옮길 곳을 정할 것

### B. 코드 정리 — 확인 없이 진행 가능

3. `historian/material.py` · `pmo/material.py` 로 사전취합 분리(**순수 이동**, 동작 불변).
   각 역할 파일이 ~120줄의 "역할"만 남는다 — 지금은 73%/62% 가 재료 코드다
4. **Responder 명령서 10갈래를 `roles/responder-goals.md` 로.** `roles.py::sections()` 가
   이미 있으므로 `## 상황이름` 절로 두고 `task()` 는 고르기만 한다 — 다른 아홉 역할과
   같은 원칙(프롬프트는 편집하는 자산)

### C. 품질 — 열려 있음

5. **전체 생성 스위트 20케이스 일괄 재실행** — 개별 green 을 일괄로도 확인(변동성 추적)
6. **DRAFT-COMPARISON 갭 3종**: ①도메인 관계 전개(웹 조사 개념을 refiner 본문 재료로
   더 강하게) ②DoD 판정 방법 포함(knowledge/07 예시 보강) ③작업 범위 '제외' 누락 가드
7. **judge 루브릭 확장** — 위 갭을 judge 가 잡게 문구 반영(topic 축 선례처럼)
8. **모듈 로스터 키 불일치** — `_module_pool` 이 components↔load_people 키 미스에
   약함(부모 담당 폴백으로 완화). 컴포넌트명 정규화 테이블 검토
9. **Historian 4회/26k** 가 이제 턴당 최대 항목 — 다만 여기는 실제 조사라 걸음을 줄이면
   "이 일이 처음인가"의 정확도와 맞바꾼다. Planner 5k(few-shot 8예시)도 같은 성격
10. **A2A(선택 요소)** 미도입 — 기획문서에 "왜 과잉인가" 서술 유지
11. 제출 관련: submission 브랜치 갱신은 **사용자 지시가 있을 때만**

## 8. 작업 규율 (이 저장소에서 지켜온 것)

- 커밋은 원자적으로, 메시지에 실측 근거(어떤 사고를 왜 이렇게 고쳤나)를 남긴다.
- 프롬프트를 고치면 반드시 코드 가드도 검토("프롬프트로 막았는데 재발" 패턴이 많았다).
- **역할의 형태를 바꾸면 그 역할의 md 도 같이 바꾼다** — 도구를 걷어냈는데 md 가 남아
  열 군데서 유령 도구를 시킨 사고가 있었다(§5-c). 이제 테스트가 지킨다.
- heredoc 안 `\n` 이스케이프가 Windows 에서 깨진다 — 파이썬 인라인 스크립트는 파일로
  쓰거나 Edit 도구를 쓸 것. 줄바꿈이 **공백으로 뭉개진** 줄도 같은 사고이고(문법은
  멀쩡해서 안 보인다), 이제 `test_static_assets.py` 가 잡는다.
- 변경 후 실제 앱을 띄워 확인한다. 테스트 통과만으로 완료 보고하지 않는다.
- **파이프가 종료 코드를 삼킨다** — `pytest … | tail -1 && git commit` 으로 깨진 파일을
  커밋한 적이 있다(e12df65). 테스트 결과는 따로 확인하고 커밋한다.
