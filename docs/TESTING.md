# 테스트 운영 정책

## 자동 검증: GitHub Actions

PR 생성·재개 및 PR branch의 새 commit push(`synchronize`), `main` push마다
`.github/workflows/code-tests.yml`이 외부 업무 시스템과 실 LLM API에 의존하지 않는 전체 코드
suite를 실행한다.

- Python 3.11, Ubuntu
- `JIRA_ENV=mock`
- Fake Jira, fake LLM, 로컬 fixture·로컬 서버만 사용
- 배포 당시 연구 자료를 Git에서 읽을 수 있도록 전체 commit 이력을 checkout
- 고유한 `RUNNER_TEMP` 실행 디렉터리에 pytest 임시 파일·cache·앱 runtime을 격리
- GitHub secret, 사내 Jira/Confluence, OpenAI/Azure OpenAI API 호출 없음

CI가 만든 `$owned_root` 아래 `$scratch`, 그 하위 `$TMPDIR`를 사용한다. pytest 사례는 실제
OS 임시 디렉터리인 `$TMPDIR`의 자식에 둔다. 전체 suite 실행 명령은 다음과 같다.

```sh
python -m pytest -q --basetemp="$TMPDIR/cases" -o "cache_dir=$scratch/cache" --durations=30 -ra --junitxml="$owned_root/results/pytest.xml"
```

전체 suite의 최종 판정은 GitHub Actions 결과를 기준으로 한다. CI가 실패하면 실패한 test와
직접 관련된 test를 로컬에서 재현한 뒤 수정한다.

### 결과와 실행 시간 읽기

- 로그의 `slowest 30 durations`에서 setup/call/teardown 비용을 구분하고, `-ra`의 skip 사유를
  확인한다. 사례 수 감소만으로 속도 개선이나 검증 범위 유지를 판단하지 않는다.
- 성공·실패한 pytest 결과는 `code-tests-results` artifact의 `pytest.xml`로 14일간 보존한다.
  JUnit의 사례별 시간은 setup/call/teardown 합계다. pytest 실행 전 dependency 설치가 실패하면
  보고서가 없을 수 있으며, artifact에는 이 XML만 올린다.
- 성공·실패와 관계없이 pytest 종료 시 scratch를 정리한다. JUnit은 upload가 끝날 때까지
  scratch 밖의 고유 실행 디렉터리에 보관하고, 항상 실행되는 마지막 단계에서 소유 경로를
  확인한 뒤 정리한다. 저장소 안에는 테스트 실행 산출물을 남기지 않는다.
- MCP의 `build_env`는 test call 안에서 환경을 만들므로 setup 시간만으로 비용을 알 수 없다.
  JUnit properties의 `mcp_environment_build_count`와 `mcp_environment_build_seconds`는 각
  test에서 완료한 환경 생성 횟수와 누적 시간이다. 이 값은 사례 전체 시간에 이미 포함되어 있다.
  Jira·인증·SQLite cache·mutation receipt는 계속 test별로 격리한다.
- 기본 CI dependency에는 Playwright/Chromium이 없다. 브라우저 검증의 skip은 통과한 검증이
  아니며, 해당 회귀를 다룰 때는 준비된 로컬 브라우저 환경에서도 확인한다. 템플릿 없는 자산은
  파일별 기계적 검사에서 적용 가능한 규칙만 검사하며 skip 사례로 만들지 않는다.

### 테스트를 통합할 때

같은 입력과 같은 검증을 반복하는 사례는 하나로 합치고, 입력이 다른 동작은 이름 있는
parameter 사례로 보존한다. 자산·Python 소스의 기계적 검사는 파일별로 한 번 읽어 적용 가능한
모든 규칙과 실패 사유를 보고한다. 선택 dependency가 필요한 JS parser는 별도 검사로 유지해
parser가 없더라도 다른 소스 검증이 실행되게 한다. 도메인·HTTP·MCP·브라우저가 서로 다른
경계에서 확인하는 회귀는 단순히 같은 기능을 다룬다는 이유로 삭제하지 않는다.

## 로컬 검증: 변경 범위만

개발 중에는 전체 suite를 매번 반복하지 않는다. 변경한 영역과 직접 연결된 파일만 실행한다.

| 변경 영역 | 로컬 test |
|---|---|
| Agent prompt·role·workflow | `tests/agent/contracts tests/agent/core` |
| Agent 평가·외부 통합 | `tests/agent/evaluation tests/agent/integration` |
| Agent 답변 렌더링·UI | `tests/frontend/static_assets/test_agent_ui_contracts.py tests/routes/test_postcheck.py` |
| Ticket create/update/action | `tests/jira/tickets` |
| Jira query·mock world | `tests/jira/query tests/domain/test_world.py tests/jira/workload/test_local_parity.py` |
| MyTasks·workload | `tests/jira/workload tests/frontend/static_assets/test_task_ui_contracts.py` |
| Ticket dialog·editor | `tests/frontend/ticket_dialog tests/frontend/editor tests/frontend/static_assets/test_ticket_editor_contracts.py` |
| 특정 회귀 | 해당 test file 또는 `path::test_name` |

예시:

```powershell
@'
import os, subprocess, sys
from pathlib import Path
from tempfile import TemporaryDirectory

with TemporaryDirectory() as scratch:
    root = Path(scratch)
    temp = root / "tmp"
    temp.mkdir()
    env = dict(os.environ, TMPDIR=str(temp), TMP=str(temp), TEMP=str(temp),
               JIRA_ENV="mock", PYTHONDONTWRITEBYTECODE="1", LAKE_CACHE_DIR=str(root / "runtime"),
               CACHE_DB_PATH=str(root / "runtime" / "cache.sqlite3"))
    subprocess.run([sys.executable, "-B", "-m", "pytest", "-q",
                    "--basetemp", str(temp / "cases"), "-o", f"cache_dir={root / 'cache'}",
                    "tests/frontend/static_assets", "tests/routes/test_postcheck.py"],
                   env=env, check=True)
'@ | ..\.venv\Scripts\python.exe -B -
```

임시 경로를 새 Python process에 전달한다. process 종료 후에는 성공·실패와 관계없이
`TemporaryDirectory`가 scratch를 정리한다.

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
