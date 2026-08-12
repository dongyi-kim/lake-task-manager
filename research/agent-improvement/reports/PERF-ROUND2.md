# 성능 최적화 라운드 2 (L-시리즈)

라운드 1(P-시리즈, 396s→155s)에서 남은 병목을 계측으로 재확인하고 6개 레버를 적용했다.
원칙은 같다 — **측정 없이 최적화 없음**, 품질 게이트(배터리) green 유지 못 하면 롤백.

## 결과 요약 (perf 하네스 · gpt-4o-mini · mock)

| 시나리오 | 라운드1 후 | 라운드2 후 | 비고 |
|---|---|---|---|
| 조회 | ~40s | **31s** | pmo 사전취합 직결(L3a) |
| 지식 | ~35s | **27s** | historian 6회→**1회**(dossier 직결) |
| 생성(첫턴) | ~55s | **38s** | reviewer LLM 생략(L3b) + 도구 병렬(L4) |
| 수정 | ~28s | **29s** | 변화 없음(경로상 레버 없음) |
| **합계** | **~155s** | **125s (−19%)** | LLM 34→29회 · 캐시 히트 14k~69k tok/시나리오 |

체감 개선은 수치보다 크다 — **토큰 스트리밍(L1)** 으로 최종 답이 완료 전부터 타이핑되듯
표시된다(SSE 실측: `token` 이벤트 128건이 `final` 전에 도착).

## 적용한 레버

| # | 내용 | 파일 | 효과 |
|---|---|---|---|
| L0 | 계측 확장 — 도구별 시간(`by_tool`)·프롬프트 캐시 히트(`cached_tokens`)·TTFT | `usage.py` · `tools/agent_perf.py` | 병목 판별 근거 |
| — | `_identity()` 5분 캐시 — 턴 시작마다 재조회하던 사용자 정체 | `session.py` | 턴당 2~3s |
| L2 | persona 정렬: **정적(페르소나·역할 md·프로젝트/사용자) 앞 → 동적(날짜·정체·플레이북) 뒤** — OpenAI 는 1024+ 토큰 공통 prefix 를 자동 캐시 | `prompts/base.py` | 캐시 히트 확인(시나리오당 14k~69k tok) |
| L3a | 사전취합 직결 — `topic_dossier`(historian) / `ticket_progress`·`group_activity`(pmo)가 **코드로 이미 취합된** 경우 ReAct 걷기를 생략하고 conclude 1회로 | `historian.py` · `pmo.py` | 지식: historian 6회→1회, 자산질의 27s→20s 실측 |
| L3b | 조건부 Reviewer — 단건·자식 없음·비Epic·기계검증(`validate_bulk`) 통과면 LLM 검열 생략 | `reviewer.py` | 생성 단건에서 LLM 1회 절감 |
| L4 | 도구 병렬 — 한 턴에 tool_calls 여러 건이면 ThreadPool(≤4)로 동시 실행 | `agents/base.py` | mock 에선 미미, 실 API 에서 유효 |
| L1 | 토큰 스트리밍 — `stream_mode=["updates","messages"]`, Responder 의 **Chunk 타입만** `{"type":"token"}` 으로 통과 → AgentView 점진 렌더 | `session.py` · `AgentView.js` | TTFT 17.8s(완료 20.2s 대비) — 긴 답일수록 이득 |
| — | `stream_usage=True` — 스트리밍을 켜자 토큰 계측이 전부 0 이 됐다(스트림 응답에 usage 미탑재). 마지막 청크에 usage 를 싣게 강제 | `config.py` | 계측 복구 |

## 진단 기록 (수치를 읽을 때 주의)

- **planner 9~20s 스파이크는 구조 문제가 아니다.** 단독 호출 1.1s, warm 세션 0.9~1.4s.
  perf 하네스처럼 시나리오를 연달아 돌리면 직전 시나리오의 토큰 버스트가 TPM 한도를 건드려
  다음 시나리오 첫 호출(planner)에 백오프가 얹힌다. 깨끗한 단발 런에서는 사라진다.
  → perf 합계는 **상한**으로 읽을 것. 사용자 체감(단발 턴)은 이보다 빠르다.
- **토큰 스트리밍 2배 중복 버그**: responder 가 스트림 청크와 완성 메시지를 둘 다 방출해
  조립 결과가 정확히 2배가 됐다 → `type(msg).__name__.endswith("Chunk")` 필터로 해결
  (조립 == final 일치 검증).
- **도구 시간은 병목이 아니다**(mock 0.1~0.9s/시나리오). 진짜 레버는 ①직렬 LLM 홉 수
  ②턴 시작 오버헤드 ③체감 지연(스트리밍 부재)이었다.

## 품질 게이트 (전건 green)

- `tools/agent_scenarios.py` DATA1·DATA5·DATA8·PROG1·PROG2 — **5/5 통과**, 품질 4.05 (L3a 검증)
- `tools/agent_create_suite.py` ONE1·ATTR1·RULE2 — **3/3 통과** (L3b 검증)
- 전체 pytest 580 passed
- HTTP SSE 실측: token 128건 → final 1건 순서 확인
