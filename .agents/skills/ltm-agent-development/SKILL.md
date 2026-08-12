---
name: ltm-agent-development
description: LakeTaskManager의 app/agent, Agent용 domain rule, prompt, role, tool, LangGraph workflow, compose/comment 작성, Jira·Confluence 조회, grounding, HITL 승인, 배터리 및 품질·토큰 개선 작업에 사용한다. 일반 UI·인증·진척률 기능만 수정하거나 과거 보고서만 읽는 작업에는 사용하지 않는다.
---

# LTM Agent development

## 시작

1. repository root의 `AGENTS.md`와 `app/agent/AGENT.md`를 전부 읽는다.
2. 요청과 관련된 source of truth를 코드에서 확인한다. 과거 보고서의 설명을 현재 구현으로 가정하지 않는다.
3. 변경 전 `git status`, 관련 test, prompt version, model routing을 기록한다.

## 원인 분류

실패 사례를 입력·기대 state/action/output 계약으로 바꾸고 다음 owner 중 하나를 고른다.

- prompt 판단: `app/agent/prompts/`, `workflow/agents/*::task()`
- data/query 누락: Query Specialist, Query Runner, tool, search config
- schema/I/O 불일치: `workflow/contracts.py`, `state.py`, `role_manifest.py`
- 안전 불변조건: approval, grounding, postcheck, `domain/ticket_actions.py`
- 표현·렌더링: Result Integrator, Editor Author, canonical reference renderer
- evaluator/fixture 결함: battery checker 또는 mock world

한 실패를 막기 위해 여러 prompt 계층에 같은 문장을 추가하지 않는다. deterministic하게 판정 가능한 조건은 코드와 test로 구현한다.

## 구현

1. 회귀 test를 먼저 추가하거나 기존 battery case에 기대 계약을 명시한다.
2. 가장 좁은 owner 계층만 수정하고 중복·모순 prompt를 함께 제거한다.
3. role I/O, tool 목록 또는 effect를 바꾸면 `role_manifest.py`, graph, state, contracts, Result Integrator까지 함께 맞춘다.
   Role id는 module, graph node, prompt asset에 그대로 쓰며 alias table이나 legacy fallback을 만들지 않는다.
4. Jira write에는 approval fingerprint를 유지한다. Done·ticket tier·search scope·pagination 규칙을 우회하지 않는다.
5. 모호한 사실은 구체적으로 질문하거나 open fact로 남긴다. 숫자·담당·parent·status·metric을 만들지 않는다.
6. code, function/tool name, parameter, schema key/enum, Jira field/type, JQL, HTML, key, user ID, URL은 번역하지 않는다.

## 검증

1. 관련 pytest를 repository 내부 `--basetemp`로 실행한다.
2. prompt·role·tool 변경이면 `test_agent_prompt_integrity`를 반드시 포함한다.
3. 성공 후 임시 basetemp만 안전하게 제거한다.
4. 실 LLM 배터리는 사용자가 승인한 existing project secret만 사용한다.
5. prompt 후보 비교에서 main/complex=`gpt-4o`, simple=`gpt-4o-mini` routing과 mock data를 고정한다.
6. Conversation, Compose, Create의 실제 output 전문과 call·token·latency·cost를 저장한다.
7. 자동 통과와 별개로 사람이 사실성·완결성·안전성·가독성을 평가한다.
8. 결과는 `research/agent-improvement/` 아래에만 저장한다.

## 완료 보고

변경한 계약, 검증 결과, 실 LLM 사용 여부, 남은 모호성·위험, 연구 산출물 경로를 짧게 보고한다. 통계적으로 반복하지 않은 단일 run은 탐색적 결과라고 명시한다.
