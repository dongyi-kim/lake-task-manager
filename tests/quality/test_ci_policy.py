"""CI는 결정적 코드 test만 자동화하고 유료·외부 API 배터리는 수동으로 유지한다."""

import shlex

from support.paths import REPO_ROOT


ROOT = REPO_ROOT
WORKFLOW = ROOT / ".github" / "workflows" / "code-tests.yml"


def test_code_test_workflow_runs_full_offline_suite_on_pr_and_main():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "types: [opened, synchronize, reopened, ready_for_review]" in text
    assert "branches: [main]" in text
    assert "JIRA_ENV: mock" in text
    assert "requirements-test.txt" in text
    assert "actions/checkout@v5" in text
    assert "actions/setup-python@v6" in text
    assert "fetch-depth: 0" in text
    assert "timeout-minutes: 30" in text
    assert 'owned_root=$(mktemp -d "$runner_root/ltm-tests.XXXXXXXX")' in text
    assert 'export TMPDIR="$scratch/tmp"' in text
    command = ('python -m pytest -q --basetemp="$TMPDIR/cases" -o "cache_dir=$scratch/cache" '
               '--durations=30 -ra --junitxml="$owned_root/results/pytest.xml"')
    commands = [line.strip() for line in text.replace("\\\n", "").splitlines()
                if line.strip().startswith("python -m pytest ")]
    # Exactly one unfiltered whole-suite command; extra test paths/-k would narrow coverage.
    assert [shlex.split(line) for line in commands] == [shlex.split(command)]
    assert command in (ROOT / "docs" / "TESTING.md").read_text(encoding="utf-8")
    assert 'trap cleanup_scratch EXIT' in text
    assert text.count("if: always()") == 2  # Upload results, then clean their owned OS root.
    assert "actions/upload-artifact@v7" in text
    assert "name: code-tests-results" in text
    assert 'path: ${{ env.LTM_CI_TEST_ROOT }}/results/pytest.xml' in text
    assert "include-hidden-files: true" in text
    assert "name: Clean test results" in text
    assert 'target=$(realpath -e "$LTM_CI_TEST_ROOT")' in text
    assert '"$(dirname "$target")" != "$runner_root"' in text


def test_code_test_workflow_never_runs_real_api_batteries_or_secrets():
    text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "agent_scenarios.py",
        "agent_lang_ab.py",
        "agent_compose_eval.py",
        "agent_create_suite.py",
        "agent_user_review.py",
        "agent_quality_read.py",
        "agent_perf.py",
        "secrets.",
        "OPENAI_API_KEY",
        "AOAI_API_KEY",
        "workflow_dispatch",
    )
    assert not [token for token in forbidden if token in text]


def test_repository_guidance_enforces_one_primary_context_per_pr():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents" / "skills" / "ltm-agent-development" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    for text in (agents, skill):
        assert "하나의 주된 변경 컨텍스트" in text or "하나의 주된 컨텍스트" in text
        assert "branch" in text and "PR" in text
        assert "일회성" in text
