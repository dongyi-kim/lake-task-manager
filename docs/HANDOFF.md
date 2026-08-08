# HANDOFF — Agent 기능 인수인계 (다른 세션/에이전트용)

브랜치 **`feature/ai-agent`** (서브모듈 `lake-task-manager-deploy/lake-task-manager`).
이 문서 하나로 다음 세션이 코드 이해 → 검증 → 이어서 작업까지 가능해야 한다.
아키텍처 개요는 [AGENT.md](AGENT.md), 성능은 [PERF-ROUND2.md](PERF-ROUND2.md),
품질 비교는 [DRAFT-COMPARISON.md](DRAFT-COMPARISON.md) 참조.

## 0. 30초 요약

LTM(FastAPI+Vue3 무빌드 SPA) 안에 in-process 로 얹힌 LangGraph 멀티에이전트
"업무 착수 어시스턴트". 8역할(planner/historian/curator/refiner/assigner/reviewer/
operator/responder + pmo) StateGraph, 모든 쓰기는 HITL 승인 토큰(내용 지문 봉인),
mock(JIRA_ENV=mock, jira820 인메모리)에서 개발, 검증은 fake LLM 기본 + gpt-4o-mini 실측.

**핵심 설계 원칙 (전 코드에 반복됨)**
1. **판단은 모델, 보장은 코드** — 반복문·검증·취합은 코드가 하고(사전취합/가드),
   모델은 읽고 판단만. 프롬프트로 지시하고 코드로 한 번 더 막는다.
2. **위임("알아서")이 질문을 이긴다** — 되묻기 금지·기본값 채움을 코드가 강제.
3. **HITL 지문**: 승인 화면에 보인 것과 실행되는 것이 sha256 지문으로 같아야 한다
   (`approval.py`). 카드 편집은 State draft 를 고치고 payload 를 재생성(`session._apply_overrides`).
4. **grounding**: 없는 키·틀린 제목·없는 사람은 코드가 잡는다(답변+티켓 본문 둘 다).

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
| 전체 pytest | `python -m pytest tests/ -q` | **fake LLM + mock** — 그래프 분기·가드·도구 계약·회귀. 현재 **589 passed**, ~2분, $0 |
| 이벤트 스모크 | `LLM_PROVIDER=fake JIRA_ENV=mock python -X utf8 <스크립트>` 로 `session.stream()` 소비 | SSE 이벤트 모양(plan/step/node/token) 확인, $0 |
| 성능 | `python -X utf8 tools/agent_perf.py` | 실 LLM(mini). 4시나리오 역할별 시간·토큰·캐시·TTFT. 합계 ~125s 기준 |
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
   └─ agents/       # base(ToolAgent ReAct)·8역할. refiner.py 가 가장 두껍다(가드 무리)
knowledge/          # 정적 RAG 원천 — 04(구조 판단)·07(본문 가이드+최소 요건 슬롯)·08(렌더링)
tools/agent_*.py    # 위 하네스들
app/static/…/AgentView.js  # 챗 UI(플랜 체크리스트·뱃지 augment·카드 편집·대화 전환)
```

**refiner.py 의 가드 순서가 곧 로직이다** (apply() 안, 순서 민감):
지정담당 강제(_apply_named_assignees) → 껍데기→Sub-Task 변환(+보정 분할) → subtask 모드
children 제거+지정 재적용 → 참고 병합(_merge_refs) → 날조 불릿 제거 → 주제 가드
(_topic_drift→Reviewer 우회 금지) → Epic 실재·모듈 불일치 → 번호·단계 접기(_base_title)
→ 균등 배분 → 자식 담당 채움(부모 담당 폴백) → 구조-산출 어긋남 보정 → 하향 편향
보정(위임이면 _split_into_children) → 우선순위 정규화 → PMO_VIT 제거.

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

배경 사건: 실사용 STARR NDV 테스트에서 주제 이탈·본문 오염·구조 오판이 드러남 →
"배터리 green ≠ 품질" 규율 확립 → 33ac843~6eb8812 라운드로 교정. 상세 갭 장부는
DRAFT-COMPARISON.md.

## 6. 현재 상태

- 589 pytest green · 생성 스위트: 이전 실패 7건 전부 원인별 가드로 교정 후 개별 재실측
  green (STR1/STR2/PAR1/PAR2/SUB1/SUB3/ATTR1 — 단 **전체 20케이스 일괄 재실행은 아직**,
  TPM 여유 있을 때 1회 권장)
- 서버는 rev 6eb8812 로 재기동 필요할 수 있음(마지막 재기동은 8ea1805)
- 미커밋 산출물 없음(작업 전 `git status` 확인)

## 7. Future work (우선순위순)

1. **전체 생성 스위트 20케이스 일괄 재실행** — 개별 green 을 일괄로도 확인(변동성
   추적). 실패 시 원인별 가드(§3 순서 참고)로.
2. **해석 확인 턴 실사용 검증** — STARR류 새 질문으로 브라우저에서: 해석("제가 이해한
   바")이 먼저 오는지, Epic/모듈 choice 가 뜨는지, 답 이후 조사→초안이 짧아졌는지.
3. **DRAFT-COMPARISON 갭 3종**: ①도메인 관계 전개(웹 조사 개념을 refiner 본문 재료로
   더 강하게) ②DoD 판정 방법 포함(knowledge/07 예시 보강) ③작업 범위 '제외' 누락 가드.
4. **judge 루브릭 확장** — 위 갭을 judge 가 잡게 문구 반영(topic 축 선례처럼).
5. **모듈 로스터 키 불일치** — `_module_pool` 이 components↔load_people 키 미스에
   약함(부모 담당 폴백으로 완화). 컴포넌트명 정규화 테이블 검토.
6. **A2A(선택 요소)** 미도입 — 기획문서에 "왜 과잉인가" 서술 유지.
7. 제출 관련: submission 브랜치 갱신은 **사용자 지시가 있을 때만**.

## 8. 작업 규율 (이 저장소에서 지켜온 것)

- 커밋은 원자적으로, 메시지에 실측 근거(어떤 사고를 왜 이렇게 고쳤나)를 남긴다.
- 프롬프트를 고치면 반드시 코드 가드도 검토("프롬프트로 막았는데 재발" 패턴이 많았다).
- heredoc 안 `\n` 이스케이프가 Windows 에서 깨진다 — 파이썬 인라인 스크립트는 파일로
  쓰거나 Edit 도구를 쓸 것.
- 변경 후 실제 앱을 띄워 확인한다. 테스트 통과만으로 완료 보고하지 않는다.
