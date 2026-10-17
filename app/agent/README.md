# app/agent — Lake PMO Agent (업무 착수 어시스턴트)

막연한 업무 요구("~~한 업무를 수행해야 한다")를 받아 **과거 히스토리를 뒤져 현재 상황을 정리**하고,
대화로 구체화한 뒤 description·label·component·assignee 를 **근거와 함께** 제안해
Epic/Story/Sub-Task 트리를 만든다. 모든 쓰기는 **사용자 승인 후**(HITL).

**설치** — `pip install -r requirements.txt` (에이전트 의존이 여기 들어 있다). 설치돼 있어도 **LLM 키가 없으면** 라우트만 살고 화면은 비활성이다.
설치 안 돼 있으면 라우트·설정 패널이 아예 안 붙는다(`config.available()` 게이팅).

## 파일

- **config.py** — LLM provider 추상화 **4-way** + 연결 진단.
  `aoai`(채점/사내) · `openai`(개발 PC) · `openai_compat`(향후 자체 LLM) · `fake`(키 없이 테스트).
  `get_llm()` / `get_embeddings()` / `callbacks()` / `status()` / `probe()`.
- **secrets.py** — API 키 저장소(`CACHE_DIR/agent_secrets.json`, 원자적 교체).
  prefs 는 "비밀 아닌 개인 설정"용이라 **파일을 나눈다**(sso_store 와 같은 급).
- **fake.py** — 결정적 Fake LLM/임베딩. `with_structured_output` 도 지원해 Request Architect 분기까지 굴러간다.
- **approval.py** — 쓰기 전 사용자 승인(HITL)을 **도구 경계에서** 강제. 토큰은 초안 내용의
  해시에 묶여 있어 A 를 승인받고 B 를 만들 수 없다. 1회용 · 30분 만료.
- **tools/** — 에이전트가 LTM 을 만지는 역할별 tool registry → [tools/README.md](tools/README.md)
- **retrieval/** — RAG 2계층(정적 규칙 + 동적 증분) → [retrieval/README.md](retrieval/README.md)
- **workflow/** — `role_manifest.py` 기반 LangGraph 멀티 에이전트 + HITL 중단/재개
  → [workflow/README.md](workflow/README.md). 바깥에서 부르는 것은 `workflow/session.py` 하나다.
- **maxis/** — 환경별 MAXIS/Dify 호출·근거·응답·감사 경계. 기존 Role 호출은
  `backend.invoke()`에서 `DifyClient`를 거쳐 원격으로 전달된다.
- **maxis/taskflow.py** — Research → Task draft의 버전 계약·로컬 continuation 기반.
  아직 session/실서버 adapter에 연결하지 않았으며 기존 route·쓰기 권한을 변경하지 않는다.
  [TaskFlow 계약과 활성화 전 조건](../../deploy/maxis-dify/taskflow-v1.md)을 참고한다.

## MAXIS 요청 계측

- `telemetry.py`의 `maxis-request-telemetry.v1`은 원격 요청의 실제 전송 결과와
  요청한 published workflow ID, 서버가 반환한 native workflow run UUID를 연결한다.
  UUID가 없거나 유효하지 않으면 누락 상태를 유지하며, message/task ID는 해시만 감사 로그에 기록한다.
- 기존 `remote_role.invoke` 감사 행의 `remote_request`와 반환 metadata의
  `requestTelemetry`에 동일 관측을 연결한다. HTTP 전송 성공과 로컬 응답 검증 결과
  (`completed`, `needs_input`, `blocked`, `failed`)를 구별한다. `completed`는 계약 수용이지
  사람이 실제 사용할 수 있는 품질의 증명이 아니며, `quality_status`는 `not_evaluated`다.
- `latency_ms`는 HTTP 요청 구간, 감사 행의 `runtime_latency_ms`는 해당 Role 호출의
  로컬 검증을 포함한 구간이다. 사용자 요청 전체나 중첩 Workflow별 소요시간이 아니다.
- 토큰·비용은 Dify 응답의 `metadata.usage`에서 제공된 값만 기록한다. 비용은 유효한 통화가
  함께 제공되어야 하며, 이는 중첩 모델 전체의 합계라는 보장이 없다. 누락 값과 native 모델 호출
  수는 `null`로 유지한다. 기존 `usage` 호환 필드 대신 `requestTelemetry`를 미측정 구분에 사용한다.
  서로 맞지 않는 prompt/completion/total 값도 임의로 보정하지 않으며, 회계 정합성을 검증한 합계가 아니다.
- 원문 질문·응답·조회 내용·추론·키는 이 계측에 저장하지 않는다. HTTP 실패·시간 초과·JSON/근거
  거부도 내용 없는 관측을 감사 로그에 남기며, 감사 요약은 전송·결과별 건수만 추가로 제공한다.

## 규칙

- **환경변수가 항상 이긴다** — 채점/사내 환경은 `AOAI_*` 를 주입해 주므로, 설정 화면을 거치지
  않아도 그대로 도는 것이 정상 경로다. `env > 저장값(prefs/secrets) > 기본값`.
- **AOAI 는 모델명이 아니라 배포명**(`azure_deployment`)이다. 흔한 실수.
- **`AOAI_API_VERSION` 은 채점환경이 주입하지 않는다**(실측). 기본값 `2024-10-21` 을 쓴다 —
  chat·embeddings·function calling·structured output(strict)·streaming 전부 이 버전으로 확인했다.
- **개발 PC 에서 `aoai` 는 403 이 정상**이다(사내 AOAI 가 Private Endpoint 로 잠김, 코드로 우회 불가).
  로컬 개발은 `openai`, 구조 검증은 `fake`.
- **관측이 없다고 앱이 죽지 않는다** — Langfuse 설정이 없으면 `callbacks()` 가 빈 리스트다.
- 비밀값은 화면으로 **원문을 돌려보내지 않는다**(`secrets.masked()`).
- 도구는 **LTM 내부 함수를 직접 호출**한다(HTTP 왕복 없음). `JiraClient`·`domain/*` 를 그대로 쓰므로
  캐시·무효화가 재사용되고, provider 만 바뀌면 **prod SSO 에서도 그대로 동작**한다.
