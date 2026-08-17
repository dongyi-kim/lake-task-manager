from app.agent import model_profiles as profiles


def test_qwen_fast_structured_translates_semantic_reasoning_off():
    row = profiles.resolve("ltm-qwen3.6-35b-a3b", "openai_compat", "fast_structured")
    assert row.model_profile == "qwen3.6-35b-a3b"
    assert row.parameters["temperature"] == 0.1
    assert row.parameters["presence_penalty"] == 0.0
    assert row.parameters["max_tokens"] == 2048
    assert row.parameters["extra_body"]["top_k"] == 20
    assert row.parameters["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False}
    assert row.capabilities["tools"] is False
    assert row.capabilities["json_schema"] is False


def test_qwen_reasoning_uses_official_thinking_profile():
    row = profiles.resolve("mlx-community/Qwen3.6-35B-A3B-4bit",
                           "openai_compat", "reasoning")
    assert row.parameters["temperature"] == 1.0
    assert row.parameters["top_p"] == 0.95
    assert row.parameters["max_tokens"] == 16384
    assert row.parameters["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True


def test_qwen_reasoning_structured_contract_falls_back_to_balanced():
    assert profiles.profile_for_contract(
        "ltm-qwen3.6-35b-a3b", "reasoning", "structured"
    ) == "balanced"
    assert profiles.profile_for_contract(
        "ltm-qwen3.6-35b-a3b", "reasoning", ""
    ) == "reasoning"


def test_gpt4o_structured_reasoning_profile_is_not_rewritten():
    assert profiles.profile_for_contract("gpt-4o", "reasoning", "structured") == "reasoning"


def test_explicit_override_wins_without_leaking_unsupported_parameters():
    row = profiles.resolve("gpt-4o", "openai", "reasoning",
                           role_parameters={"temperature": 0.3},
                           explicit={"temperature": 0.1, "top_k": 50})
    assert row.parameters["temperature"] == 0.1
    assert "top_k" not in row.parameters
    assert "extra_body" not in row.parameters
    assert "reasoning_effort" not in row.parameters


def test_openai_reasoning_model_omits_sampling_and_maps_effort():
    row = profiles.resolve("gpt-5-mini", "openai", "reasoning",
                           explicit={"temperature": 0.2})
    assert row.parameters["reasoning_effort"] == "medium"
    assert "temperature" not in row.parameters


def test_profile_debug_never_contains_credentials():
    row = profiles.resolve("gpt-4o-mini", "openai", "fast_structured")
    assert "api" not in str(row.debug()).lower()
