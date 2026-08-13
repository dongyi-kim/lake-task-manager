"""CI는 결정적 코드 test만 자동화하고 유료·외부 API 배터리는 수동으로 유지한다."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "code-tests.yml"


def test_code_test_workflow_runs_full_offline_suite_on_pr_and_main():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "types: [opened, synchronize, reopened, ready_for_review]" in text
    assert "branches: [main]" in text
    assert "JIRA_ENV: mock" in text
    assert "python -m pytest -q --basetemp=.pytest-tmp" in text
    assert "requirements-test.txt" in text
    assert "actions/checkout@v5" in text
    assert "actions/setup-python@v6" in text


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
