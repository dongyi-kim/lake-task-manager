---
name: ltm-agent-development
description: LakeTaskManager의 app/agent, Agent용 domain rule, prompt, role, tool, LangGraph workflow, compose/comment 작성, Jira·Confluence 조회, grounding, HITL 승인, 배터리 및 품질·토큰 개선 작업에 사용한다. 일반 UI·인증·진척률 기능만 수정하거나 과거 보고서만 읽는 작업에는 사용하지 않는다.
---

# LTM Agent development

## 시작

1. repository root의 `AGENTS.md`와 `app/agent/AGENT.md`를 전부 읽는다.
2. 요청과 관련된 source of truth를 코드에서 확인한다. 과거 보고서의 설명을 현재 구현으로 가정하지 않는다.
3. 변경 전 `git status`, 관련 test, prompt version, model routing을 기록한다.

## PR 경계

1. 작업을 시작할 때 요청을 독립적인 목적·root cause·사용자 결과로 분류한다.
2. 하나의 branch와 PR에는 하나의 주된 컨텍스트만 둔다. 제목에 독립 목적 두 개를 `및`·`and`로
   이어야 한다면 별도 branch와 PR로 분리한다.
3. 같은 사용자 흐름을 완성하는 작은 bug fix·test·문서는 포함할 수 있다. 독립적인 Agent 동작 변경과
   infrastructure/CI·광범위 refactor는 서로 필수 관계가 아니면 합치지 않는다.
4. stage 전에 각 changed file을 PR 목적에 매핑한다. 다른 목적의 변경은 별도 commit만 만드는 데서
   끝내지 말고 별도 branch/PR로 옮긴다.
5. 사용자의 일회성 혼합 승인은 해당 PR에만 적용한다.

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

1. 관련 pytest를 `--basetemp=.cache/test-tmp/<고유 실행 ID>`로 실행한다. repository root나 상위
   deploy root에 `.test-tmp-*`, `.pytest-tmp-*`, `.codex-test-temp*`를 만들지 않는다.
2. prompt·role·tool 변경이면 `test_agent_prompt_integrity`를 반드시 포함한다.
3. 성공 후 해당 실행의 고유 basetemp만 안전하게 제거한다. Windows ACL 정리 실패를 피하려고 다른
   root-level 임시 경로로 우회하지 않는다.
4. 실 LLM 배터리는 사용자가 승인한 existing project secret만 사용한다.
   승인 후에는 제한된 sandbox의 외부 socket/Windows native certificate store 경로를 거치지 말고
   network-enabled local process로 실행한다. 실패한 묶음 전체를 반복하지 말고 미완료 case만 새
   raw attempt 경로에서 재개한다.
5. prompt 후보 비교에서 main/complex=`gpt-4o`, simple=`gpt-4o-mini` routing과 mock data를 고정한다.
6. Conversation, Compose, Create의 실제 output 전문과 call·token·latency·cost를 저장한다.
7. 자동 통과와 별개로 Codex 또는 Claude 작업 에이전트가 raw output을 직접 읽고 인간 관점에서
   사실성·완결성·안전성·가독성을 평가한다. LTM runtime LLM·내부 Role·동일 production endpoint를
   evaluator나 LLM-as-judge로 사용하지 않는다.
8. 결과는 `research/agent-improvement/` 아래에만 저장한다.
9. 모든 비교는 `app/agent/EVALUATION.md`와 `evaluation_protocol.json`의 versioned 계약을 사용한다.
10. raw 결과의 `protocolVersion`, `rubricVersion`, `batteryVersion`, manifest, run group, commit,
    model routing, 반복·선택 정책을 확인한다. 누락된 실행은 qualification 결과로 보고하지 않는다.
11. focused/closure 성공 결과로 기존 full-run 실패 점수를 교체하지 않는다. 수정 후에는 새 run group의
    full battery로 다시 비교한다.
12. 보고서 또는 PR Description에 측정 식별자, 비교 가능성, 집계식, rubric, 실제 출력, 실패·재시도·
    제한사항을 포함한다. 다른 version·manifest의 절대점수 증감을 계산하지 않는다.
13. 정성평가자의 agent family/model, direct raw review 여부, LTM LLM judge 미사용, reviewer 수와 blind
    여부를 기록한다. 자동 도구는 deterministic contract 검사와 산술 집계까지만 수행한다.
14. 각 rubric 축의 모든 checklist item을 `pass/minor/major/na`로 판정하고 실제 output 근거를 붙인다.
    축별 rationale과 대표 excerpt를 기록하며 checklist 결과의 score ceiling을 넘기지 않는다.

## 완료 보고

변경한 계약, PR의 단일 컨텍스트, 검증 결과, 실 LLM 사용 여부, 평가 protocol/rubric/battery version,
남은 모호성·위험, 연구 산출물 경로를 짧게 보고한다. 통계적으로 반복하지 않은 단일 run은 탐색적
결과라고 명시한다.
