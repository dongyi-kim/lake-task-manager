"""ActionExecutor is a deterministic approval dispatcher, not a model-output role."""

from app.agent.workflow.agents.action_executor import ActionExecutor


def test_action_executor_has_no_model_output_schema():
    executor = ActionExecutor()

    assert executor.schema() == {}
    assert executor.node().__func__ is executor._run.__func__


def test_action_executor_never_enters_the_structured_llm_path(monkeypatch):
    executor = ActionExecutor()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("deterministic ActionExecutor must not invoke a model")

    monkeypatch.setattr(executor, "invoke_structured", fail_if_called)

    result = executor.node()({"thread_id": "contract-test", "trace": []})

    assert result["result"]["created"] == []
    assert result["result"]["updated"] == []
    assert result["result"]["failed"]
