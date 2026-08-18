# 테스트 운영 정책

## 자동 검증: GitHub Actions

PR 생성·재개 및 PR branch의 새 commit push(`synchronize`), `main` push마다
`.github/workflows/code-tests.yml`이 외부 업무 시스템과 실 LLM API에 의존하지 않는 전체 코드
suite를 실행한다.

- Python 3.11, Ubuntu
- `JIRA_ENV=mock`
- Fake Jira, fake LLM, 로컬 fixture·로컬 서버만 사용
- 실행 명령: `python -m pytest -q --basetemp=.pytest-tmp`
- GitHub secret, 사내 Jira/Confluence, OpenAI/Azure OpenAI API 호출 없음

전체 suite의 최종 판정은 GitHub Actions 결과를 기준으로 한다. CI가 실패하면 실패한 test와
직접 관련된 test를 로컬에서 재현한 뒤 수정한다.

## 로컬 검증: 변경 범위만

개발 중에는 전체 suite를 매번 반복하지 않는다. 변경한 영역과 직접 연결된 파일만 실행한다.

| 변경 영역 | 로컬 test |
|---|---|
| Agent prompt·role·workflow | `tests/test_agent_prompt_integrity.py tests/test_agent_graph.py tests/test_agent_draft.py` |
| Agent query·tool·reference | `tests/test_agent_query_v2.py tests/test_agent_tools.py tests/test_agent_references.py` |
| Agent 답변 렌더링·UI 정적 자산 | `tests/test_static_assets.py tests/test_postcheck.py` |
| Ticket create/update/action | `tests/test_ticket_actions.py tests/test_epic_create_fields.py tests/test_ticket_view.py` |
| Jira query·mock world | `tests/test_search.py tests/test_world.py tests/test_local_parity.py` |
| 특정 회귀 | 해당 test file 또는 `path::test_name` |

예시:

```powershell
..\.venv\Scripts\python.exe -m pytest -q --basetemp .test-tmp-local `
  tests/test_static_assets.py tests/test_postcheck.py
```

변경이 여러 영역을 가로지르면 각 영역의 관련 test를 합쳐 한 번 실행한다. 전체 suite의 로컬
실행은 CI 장애 재현, dependency 변경, test infrastructure 변경처럼 전체 환경 검증 자체가
필요한 경우에만 수행한다.

## 수동 검증: 실 API 배터리

실 LLM/API 배터리는 비용, secret, 외부 상태, 모델 변동성과 사람 판독이 필요하므로 GitHub
Actions에 넣지 않는다. 승인된 로컬 환경에서 필요한 suite만 수동 실행한다.

| 목적 | 수동 도구 |
|---|---|
| 대화·검색 품질 | `tools/agent_scenarios.py`, `tools/agent_lang_ab.py` |
| Compose | `tools/agent_compose_eval.py` |
| Create | `tools/agent_create_suite.py` |
| 사용자 화면 raw 수집 | `tools/agent_eval_launcher.py user-review` |

`user-review`도 다른 실 LLM 배터리와 동일하게 승인된 launcher를 통해서만 실행한다. 후보
LTM 모델이나 동일 production endpoint를 별도 judge로 호출하지 않으며, Codex/Claude 작업
에이전트가 저장된 raw output을 직접 읽어 정성 판정한다.
| 성능·토큰·TTFT | `tools/agent_eval_launcher.py perf` |

수동 배터리는 다음 경우에만 수행한다.

- prompt, role routing, model, tool description처럼 실제 모델 행동을 바꾸는 변경
- release 후보의 정성 품질·비용·latency 확인
- 사용자가 실 API 실행을 명시적으로 승인한 실험

실행 결과와 실제 응답 전문은 `research/agent-improvement/{results,logs,reports}`에 보존한다.
자동 점수만으로 품질 통과를 선언하지 않고 사람이 출력 전문을 읽어 평가한다.
