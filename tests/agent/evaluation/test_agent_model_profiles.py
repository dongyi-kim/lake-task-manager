from app.agent import model_profiles as profiles


def test_qwen_fast_structured_translates_semantic_reasoning_off():
    row = profiles.resolve("ltm-qwen3.6-35b-a3b", "openai_compat", "fast_structured")
    assert row.model_profile == "qwen3.6-35b-a3b"
    assert row.parameters["temperature"] == 0.1
    assert row.parameters["presence_penalty"] == 0.0
    assert row.parameters["max_tokens"] == 1024
    assert row.parameters["extra_body"]["top_k"] == 20
    assert row.parameters["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False}
    assert row.capabilities["tools"] is False
    assert row.capabilities["json_schema"] is False
    assert row.capabilities["parallel_role_calls"] is False


def test_qwen_reasoning_uses_official_thinking_profile():
    row = profiles.resolve("mlx-community/Qwen3.6-35B-A3B-4bit",
                           "openai_compat", "reasoning")
    assert row.parameters["temperature"] == 1.0
    assert row.parameters["top_p"] == 0.95
    assert row.parameters["max_tokens"] == 8192
    assert row.parameters["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True


def test_qwen_reasoning_structured_contract_falls_back_to_balanced():
    assert profiles.profile_for_contract(
        "ltm-qwen3.6-35b-a3b", "reasoning", "structured"
    ) == "balanced"
    assert profiles.profile_for_contract(
        "ltm-qwen3.6-35b-a3b", "reasoning", ""
    ) == "reasoning"


def test_qwen_semantic_memo_is_bounded_and_disables_unseparated_reasoning():
    assert profiles.profile_for_contract(
        "ltm-qwen3.6-35b-a3b", "reasoning", "semantic_memo"
    ) == "balanced"
    row = profiles.resolve(
        "ltm-qwen3.6-35b-a3b", "openai_compat", "balanced",
        output_contract="semantic_memo", semantic_profile="reasoning",
    )
    assert row.parameters["temperature"] == 0.2
    assert row.parameters["presence_penalty"] == 0.0
    assert row.parameters["max_tokens"] == 2048
    assert row.parameters["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False}
    assert "model_contract_profile" in row.sources


def test_qwen_structured_contract_uses_semantic_role_transport_budget():
    row = profiles.resolve(
        "ltm-qwen3.6-35b-a3b", "openai_compat", "balanced",
        output_contract="structured", semantic_profile="reasoning",
    )
    assert row.task_profile == "balanced"
    assert row.parameters["temperature"] == 0.0
    assert row.parameters["presence_penalty"] == 0.0
    assert row.parameters["max_tokens"] == 4096
    assert row.parameters["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert "model_contract_profile" in row.sources


def test_qwen35_typed_projection_has_work_draft_budget_on_same_model_lane():
    row = profiles.resolve(
        "ltm-qwen3.6-35b-a3b", "openai_compat", "fast_structured",
        output_contract="typed_projection", semantic_profile="fast_structured",
    )
    assert row.model_profile == "qwen3.6-35b-a3b"
    assert row.parameters["temperature"] == 0.0
    assert row.parameters["presence_penalty"] == 0.0
    assert row.parameters["max_tokens"] == 3072
    assert row.parameters["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False}
    assert "model_contract_profile" in row.sources


def test_qwen35_typed_projection_correction_reuses_bounded_family_budget():
    row = profiles.resolve(
        "ltm-qwen3.6-35b-a3b", "openai_compat", "fast_structured",
        output_contract="typed_projection_correction",
        semantic_profile="fast_structured",
    )
    assert row.model_profile == "qwen3.6-35b-a3b"
    assert row.parameters["temperature"] == 0.0
    assert row.parameters["max_tokens"] == 3072
    assert "model_contract_profile" in row.sources


def test_typed_projection_correction_family_fallback_does_not_leak_to_openai():
    row = profiles.resolve(
        "gpt-4o", "openai", "fast_structured",
        output_contract="typed_projection_correction",
        semantic_profile="fast_structured",
    )
    assert row.model_profile == "openai-gpt4o"
    assert row.parameters["max_tokens"] == 2048
    assert "model_contract_profile" not in row.sources


def test_qwen35_typed_projection_budget_does_not_change_openai_profile():
    row = profiles.resolve(
        "gpt-4o", "openai", "fast_structured",
        output_contract="typed_projection", semantic_profile="fast_structured",
    )
    assert row.model_profile == "openai-gpt4o"
    assert row.parameters["max_tokens"] == 2048
    assert "model_contract_profile" not in row.sources


def test_qwen_simple_structured_contract_has_room_for_query_plan_json():
    row = profiles.resolve(
        "/models/Qwen3.5-4B-4bit", "openai_compat", "fast_structured",
        output_contract="structured", semantic_profile="fast_structured",
    )
    assert row.parameters["max_tokens"] == 3072
    assert row.parameters["temperature"] == 0.0


def test_qwen_simple_reasoning_contract_is_deterministic_and_bounded():
    row = profiles.resolve(
        "/models/Qwen3.5-4B-4bit", "openai_compat", "balanced",
        output_contract="structured", semantic_profile="reasoning",
    )
    assert row.parameters["temperature"] == 0.0
    assert row.parameters["max_tokens"] == 3072
    assert row.parameters["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False}


def test_qwen_simple_typed_projection_is_deterministic_and_bounded():
    row = profiles.resolve(
        "/models/Qwen3.5-4B-4bit", "openai_compat", "fast_structured",
        output_contract="typed_projection", semantic_profile="fast_structured",
    )
    assert row.parameters["temperature"] == 0.0
    assert row.parameters["presence_penalty"] == 0.0
    assert row.parameters["max_tokens"] == 3072
    assert row.parameters["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False}


def test_gpt4o_structured_reasoning_profile_is_not_rewritten():
    assert profiles.profile_for_contract("gpt-4o", "reasoning", "structured") == "reasoning"
    assert profiles.resolve("gpt-4o", "openai", "fast_structured").parameters["max_tokens"] == 2048


def test_execution_layer_qualification_distinguishes_projection_from_semantics():
    assert profiles.supports_execution_layer("/models/Qwen3.5-4B-4bit", "projection")
    assert not profiles.supports_execution_layer(
        "/models/Qwen3.5-4B-4bit", "lightweight_semantic")
    assert not profiles.supports_execution_layer(
        "/models/Qwen3.5-4B-4bit", "deep_semantic")


def test_qwen_4b_profile_matches_the_parameter_segment_not_quantization_text():
    """A trailing ``4bit`` quantizer must not classify a 9B/14B model as the 4B projector."""
    positive = (
        "/models/Qwen3.5-4B-4bit",
        "mlx-community/Qwen3.5-4B-Instruct-4bit",
        "ltm-qwen3.5-4b",
    )
    negative = (
        "/models/Qwen3.5-9B-Instruct-4bit",
        "/models/Qwen3.5-14B-AWQ-4bit",
    )

    assert all(profiles.model_profile(model)[0] == "qwen3.5-4b" for model in positive)
    assert all(profiles.model_profile(model)[0] != "qwen3.5-4b" for model in negative)


def test_gpt4o_mini_has_its_own_lightweight_profile_before_gpt4o():
    name, row = profiles.model_profile("gpt-4o-mini")
    assert name == "openai-gpt4o-mini"
    assert "lightweight_semantic" in row["capabilities"]["execution_layers"]
    assert "deep_semantic" not in row["capabilities"]["execution_layers"]
    assert profiles.supports_execution_layer("gpt-4o-mini", "projection")
    assert profiles.supports_execution_layer("gpt-4o-mini", "lightweight_semantic")
    assert not profiles.supports_execution_layer("gpt-4o-mini", "deep_semantic")


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
