"""agent/workflow/agents/base.py — 역할 에이전트의 공통 뼈대(추상 클래스 + 서브그래프).

여섯 역할이 하는 일은 제각각이지만 **모양은 두 가지뿐**이다.

  · `StructuredAgent` — 한 번 묻고 스키마로 받는다. **재료가 이미 손안에 있는** 역할
    (RequestArchitect·Auditor·WorkArchitect·PeopleAdvisor·KnowledgeCurator).
  · `ToolAgent` — 도구를 부르며 스스로 몇 걸음 걷는다(ResearchAnalyst·PMO·ActionExecutor).
    이게 ReAct 다: 생각 → 도구 → 결과를 보고 다시 생각. 몇 걸음 걸을지는 **모델이 정한다**.

**어느 쪽인지는 "무엇을 부를지가 판단인가"로 갈린다.** 부를 대상이 늘 같으면(WorkArchitect 의
허용값, PeopleAdvisor 의 모듈 로스터) 코드가 미리 조회해 자료로 주는 것이 옳다 — 도구 호출
한 번은 LLM 왕복 한 번이고, 모델은 매 턴 그걸 다시 부른다(실측: WorkArchitect 12회·86초·226k).
반대로 몇 번 검색해야 충분한지를 **미리 모르는** 조사(ResearchAnalyst)는 ToolAgent 로 남긴다.

**서브그래프는 도구를 쓰는 쪽만 갖는다.** 한 번 부르고 끝나는 역할에 그래프를 씌우는 건 장식이다.
반면 도구 루프는 서브그래프여야 값어치가 있다 — 종료 조건이 한곳에 모이고, 역할마다 도구·모델을
갈아끼울 수 있고, `stream(subgraphs=True)` 가 "지금 도구를 부르는 중"까지 보여 준다.

**노드는 전부 State 의 '갱신분'만 돌려준다.** 컴파일된 서브그래프를 그대로 노드로 붙이면 전체
State 가 반환값이 되어 부모의 리듀서(`add_messages`)에 통째로 다시 먹힌다. 그래서 바깥 그래프에
붙는 것은 언제나 `node()` 가 주는 **함수**다.

**출력은 Structured Output 으로 받는다.** 정규식으로 LLM 응답을 후처리하지 않는다 — 모델이
말투를 조금만 바꿔도 파서가 깨지고, 그 깨짐은 조용하다. 예외는 **사용자에게 그대로 보여줄
자유 서술**뿐이다(`TextAgent`). 그건 우리가 파싱할 일이 없으니 스키마를 씌울 이유도 없다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.agent import config as _cfg
from app.agent.workflow.state import AgentState, note

MAX_TOOL_STEPS = 6      # 도구 왕복 상한. 모델이 같은 도구를 맴돌 때 대화를 끝까지 태우지 않는다
STRUCTURED_END_TOKEN = "<END_JSON>"
SEMANTIC_MEMO_END_TOKEN = "<END_SEMANTIC_MEMO>"


def _call_config(role_id: str, output_contract: str, execution_layer: str = "",
                 execution_stage: str = "") -> dict:
    """Attach non-sensitive per-call labels consumed by :mod:`app.agent.usage`."""
    return {"metadata": {
        "ltm_role_id": str(role_id or ""),
        "ltm_output_contract": str(output_contract or ""),
        "ltm_execution_layer": str(execution_layer or ""),
        "ltm_execution_stage": str(execution_stage or ""),
    }}


def _prompt_json_contract(schema_text: str) -> str:
    """Strict JSON contract with a transport stop marker for non-native providers.

    Some compatible models finish a valid object but do not emit EOS until ``max_tokens``.
    The server removes the stop marker, so the parser still receives exactly one raw JSON
    object and the normal schema validation remains authoritative.
    """
    return (
        "Output format: return exactly one JSON object satisfying the JSON Schema below. "
        "The first output character must be {. Immediately after the object's closing }, emit "
        f"{STRUCTURED_END_TOKEN}. Do not include prose, a preface, Markdown, or a code fence. "
        "The stop marker is transport framing and is not part of the JSON.\n" + schema_text
    )


def _deframe_structured_output(text: str) -> str:
    """Remove only LTM's exact terminal JSON transport marker.

    Some OpenAI-compatible servers accept ``stop`` but return the requested marker as a
    literal suffix instead of consuming it.  The marker is framing that LTM itself asked
    the model to emit, so removing that one exact terminal token is not JSON recovery.
    Prefix prose, a marker in the middle, trailing prose, and partial objects remain
    invalid and are rejected by the strict whole-document parser below.
    """
    value = (text or "").strip()
    if value.endswith(STRUCTURED_END_TOKEN):
        return value[:-len(STRUCTURED_END_TOKEN)].rstrip()
    return value


def _loads_loose(text: str):
    """정확히 하나의 JSON object만 수용한다.

    이름은 이전 import 호환을 위해 유지하지만 동작은 의도적으로 strict하다. LTM이 요청한
    exact terminal transport marker만 제거하며 code fence, 설명 prefix, balanced-brace 추출을
    성공 처리하면 provider의 parse 실패율을 숨기므로 금지한다.
    """
    import json
    t = _deframe_structured_output(text)
    if not t:
        return None
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _response_shape(raw, text: str) -> str:
    """Return safe parse diagnostics without logging response or reasoning content."""
    value = (text or "").strip()
    metadata = getattr(raw, "response_metadata", None) or {}
    finish = metadata.get("finish_reason") or metadata.get("stop_reason") or "unknown"
    return (f"chars={len(value)}, starts_object={value.startswith('{')}, "
            f"ends_object={value.endswith('}')}, finish={finish}")


def _validate_output(value, schema: dict) -> dict:
    """관대한 추출 뒤에는 반드시 동일한 JSON Schema로 엄격 검증한다."""
    from jsonschema import validate
    out = _as_dict(value)
    validate(instance=out, schema=schema)
    return out


def _raise_unrepairable_structured_output(errors: list[str], reason: str,
                                          exc: Exception | None = None) -> None:
    """Fail without a repair call when there is no model output to repair."""
    details = list(errors) + [f"repair 생략: {reason}"]
    error = RuntimeError("structured output 실패 — " + " | ".join(details))
    if exc is not None:
        raise error from exc
    raise error


def _capability_is_unsupported(exc: Exception, capability: str) -> bool:
    """Cache only a protocol rejection, never a bad model value or transient failure."""
    value = " ".join(str(exc or "").casefold().split())
    protocol = {
        "json_schema": ("response_format", "json_schema", "structured output"),
        "json_object": ("response_format", "json_object", "json mode"),
        "tools": ("tools", "tool_calls", "function calling"),
    }.get(capability, (capability,))
    rejection = any(phrase in value for phrase in (
        "unsupported", "not supported", "does not support", "unknown parameter",
        "unrecognized parameter", "extra inputs are not permitted",
    ))
    # ``Invalid schema`` proves the endpoint understood the feature; our schema/value needs
    # correction and must not poison every later role in this process.
    return rejection and any(token in value for token in protocol) and "invalid schema" not in value


def invoke_schema(schema: dict, messages: list, tier: str | None = None,
                  profile: str = "fast_structured", temperature: float | None = None,
                  name: str = "AdhocOutput", llm_factory=None, role_id: str = "",
                  call_label: str = "structured", execution_layer: str = "",
                  execution_stage: str = "") -> dict:
    """Role 밖의 보정 호출도 공통 structured-output fallback을 사용하게 한다."""
    import json
    from app.agent import capabilities

    named = _named(schema, name)
    observed_role = role_id or name
    if tier is not None and execution_layer:
        raise ValueError("tier와 execution_layer를 동시에 지정할 수 없습니다.")
    initial_layer = execution_layer
    initial_tier = (_cfg.execution_tier(initial_layer) if initial_layer
                    else (tier or "complex"))

    def make_llm(*, call_layer: str = "", call_stage: str = "", **overrides):
        active_layer = call_layer or initial_layer
        active_stage = call_stage or execution_stage
        active_tier = (_cfg.execution_tier(active_layer) if active_layer else initial_tier)
        values = {"profile": profile, "output_contract": "structured", **overrides}
        if temperature is not None and "temperature" not in values:
            values["temperature"] = temperature
        if llm_factory:
            if active_layer:
                values["execution_layer"] = active_layer
            if active_stage:
                values["execution_stage"] = active_stage
            return llm_factory(**values)
        return _cfg.get_llm(tier=active_tier, **values)

    capability_profile = capabilities.get(initial_tier).get("checked") or {}
    errors, raw_text, validation_error = [], "", ""
    for capability, method in (("json_schema", "json_schema"),
                               ("json_object", "json_mode")):
        if capability_profile.get(capability) is False:
            continue
        try:
            call_messages = list(messages)
            if method == "json_mode":
                call_messages.append(HumanMessage(content=(
                    "Return exactly one JSON object that satisfies this JSON Schema:\n"
                    + json.dumps(schema, ensure_ascii=False))))
            out = make_llm().with_structured_output(
                named, method=method).invoke(
                    call_messages, config=_call_config(
                        observed_role, call_label, initial_layer, execution_stage))
            out = _validate_output(out, schema)
            capabilities.record(initial_tier, capability, True)
            return out
        except Exception as exc:
            errors.append(f"{capability}: {str(exc)[:160]}")
            if _capability_is_unsupported(exc, capability):
                capabilities.record(initial_tier, capability, False, str(exc))
    try:
        raw = make_llm().invoke(
            list(messages) + [HumanMessage(content=_prompt_json_contract(
                json.dumps(schema, ensure_ascii=False)))],
            stop=[STRUCTURED_END_TOKEN],
            config=_call_config(observed_role, call_label, initial_layer, execution_stage),
        )
    except Exception as exc:
        errors.append(f"prompt_json: {str(exc)[:160]}")
        _raise_unrepairable_structured_output(
            errors, "provider 호출 실패로 교정할 모델 출력이 없습니다.", exc)
    raw_text = str(getattr(raw, "content", raw) or "")
    if not raw_text.strip():
        errors.append("prompt_json: 모델 출력이 비어 있습니다 (chars=0).")
        _raise_unrepairable_structured_output(
            errors, "모델 출력이 비어 있어 교정할 내용이 없습니다.")
    try:
        parsed = _loads_loose(raw_text)
        if parsed is None:
            raise ValueError("JSON 객체를 찾지 못했습니다 (" + _response_shape(raw, raw_text) + ").")
        return _validate_output(parsed, schema)
    except Exception as exc:
        validation_error = str(exc)[:1000]
        errors.append(f"prompt_json: {str(exc)[:160]}")
    try:
        repair_layer = "projection" if initial_layer else ""
        repair_contract = "typed_projection" if repair_layer else "structured"
        raw = make_llm(call_layer=repair_layer, call_stage="repair",
                       profile="fast_structured", output_contract=repair_contract).invoke([
            SystemMessage(content=(
                "Preserve meaning exactly. Repair only JSON syntax and schema violations. "
                f"Return raw JSON, then emit {STRUCTURED_END_TOKEN}. The marker is transport "
                "framing, not JSON. Never use Markdown, a code fence, or an explanation.")),
            HumanMessage(content=("Validation error:\n" + validation_error + "\n\nJSON Schema:\n"
                                  + json.dumps(schema, ensure_ascii=False)
                                  + "\n\nOutput to repair:\n" + raw_text[:12000]))],
            stop=[STRUCTURED_END_TOKEN],
            config=_call_config(observed_role, call_label + "_repair",
                                repair_layer, "repair"),
        )
        parsed = _loads_loose(str(getattr(raw, "content", raw) or ""))
        if parsed is None:
            raise ValueError("repair JSON 객체를 찾지 못했습니다.")
        return _validate_output(parsed, schema)
    except Exception as exc:
        errors.append(f"repair: {str(exc)[:160]}")
        raise RuntimeError("structured output 실패 — " + " | ".join(errors)) from exc


class Agent(ABC):
    """역할 하나. `name` 은 그래프 노드명과 같아야 한다(State.Node 의 상수를 쓴다)."""

    name: str = "agent"
    # 숫자 sampling parameter는 Role에 두지 않는다. task profile -> model profile에서 해석한다.
    # 도구 왕복 상한 — 역할별 재정의 가능. 그룹 질의(로스터 전원 활동 조회)는 6걸음으로
    # 부족했다(실측: 3인 모듈에서 정확히 소진).
    max_steps: int = MAX_TOOL_STEPS

    @property
    def tools(self) -> list:
        return []

    @abstractmethod
    def system(self, state: AgentState) -> str:
        """이 역할의 페르소나와 규칙. 매 호출 만들어지므로 State 를 반영할 수 있다."""

    @abstractmethod
    def task(self, state: AgentState) -> str:
        """이번에 시킬 일. 사용자 발화가 아니라 **이 에이전트에게 주는 지시**다."""

    @abstractmethod
    def schema(self) -> dict:
        """출력 JSON Schema. 파싱하지 않고 스키마로 받는다."""

    def schema_for(self, state: AgentState) -> dict:
        """Return a context-specific output contract when a Role needs one."""
        return self.schema()

    @abstractmethod
    def apply(self, state: AgentState, out: dict) -> dict:
        """모델 출력 → State 갱신분. 여기서만 State 를 만진다."""

    def _role_spec(self):
        from app.agent.workflow.role_manifest import ROLE_SPECS
        spec = ROLE_SPECS.get(str(self.name))
        if spec is None:
            raise RuntimeError(f"Role manifest에 등록되지 않은 runtime Role: {self.name}")
        for cls in type(self).mro():
            if cls in {Agent, ABC, object}:
                continue
            if "tier" in cls.__dict__:
                raise RuntimeError(
                    f"Role {self.name}의 class tier override는 manifest routing과 충돌합니다."
                )
        return spec

    def execution_layer(self, stage: str = "synthesis") -> str:
        from app.agent.workflow.role_manifest import execution_layer_for_role
        self._role_spec()
        return execution_layer_for_role(str(self.name), stage)

    def model_tier(self, stage: str = "synthesis", *, execution_layer: str = "") -> str:
        layer = execution_layer or self.execution_layer(stage)
        return _cfg.execution_tier(layer)

    def llm(self, *, execution_layer: str = "", execution_stage: str = "synthesis", **kw):
        spec = self._role_spec()
        layer = execution_layer or self.execution_layer(execution_stage)
        kw.setdefault("profile", spec.task_profile)
        kw.setdefault("role_id", str(self.name))
        return _cfg.get_llm(tier=self.model_tier(execution_stage, execution_layer=layer), **kw)

    def structured(self, method: str = "json_schema", state: AgentState | None = None,
                   output_contract: str = "structured", **kw):
        """스키마로 받는 모델. **스키마에 이름을 붙여서** 넘긴다.

        OpenAI/AOAI 는 구조화 출력을 함수 호출로 구현하므로 스키마가 함수 이름을 가져야 한다.
        이름 없는 JSON Schema 를 그대로 주면 `Unsupported function` 으로 죽는다 — 실 키로
        처음 돌렸을 때 여섯 역할이 전부 여기서 넘어졌다. 역할마다 적어 두면 빠뜨리는 사람이
        생기므로 여기서 한 번에 붙인다.
        """
        schema = self.schema_for(state or {})
        return self.llm(output_contract=output_contract, **kw).with_structured_output(
            _named(schema, self.name), method=method)

    def _semantic_projection_tier(self) -> str:
        """Return a projection tier only for an opted-in, non-native Role invocation."""
        from app.agent import capabilities
        from app.agent.workflow.role_manifest import ROLE_SPECS

        spec = ROLE_SPECS.get(str(self.name))
        if not spec or spec.semantic_contract != "semantic_projection":
            return ""
        semantic_tier = self.model_tier("synthesis")
        checked = capabilities.get(semantic_tier).get("checked") or {}
        # Native strict schema keeps the existing single-call path. Runtime probe values
        # override the profile floor inside capabilities.get().
        if checked.get("json_schema") is True:
            return ""
        delegated = _cfg.typed_projection_tier(semantic_tier)
        if delegated:
            return delegated
        # A separate projector endpoint is an optimization, not the semantic/formatting
        # boundary itself. When complex and simple resolve to the same model/endpoint, keep
        # the two-stage contract if that model profile is explicitly projection-qualified.
        # This avoids collapsing WorkArchitect back into one reasoning+JSON call merely
        # because the quality-first configuration uses the same 35B model for both lanes.
        try:
            from app.agent.model_profiles import supports_execution_layer
            definition = _cfg.chat_definition(semantic_tier)
            if supports_execution_layer(
                    definition.model, "projection",
                    explicit_model_profile=definition.model_profile):
                return semantic_tier
        except Exception:
            pass
        return ""

    def _invoke_structured_transport(self, state: AgentState, messages: list, *,
                                     output_contract: str = "structured",
                                     capability_tier: str | None = None,
                                     task_profile: str = "",
                                     repair_context: str = "",
                                     execution_layer: str = "",
                                     execution_stage: str = "synthesis") -> dict:
        """Execute one structured transport ladder without making semantic decisions.

        json_schema → json_object → prompt-only JSON → repair 1회 순서다. repair는 실제
        nonempty 모델 출력의 parse/schema failure에만 허용한다. 성공한 결과도
        로컬 JSON Schema 검증을 통과해야 한다. openai_compat 서버가 response_format이나
        tools를 거부해도 role 전체가 ``Invalid json output``으로 사망하지 않게 한다.
        """
        import json
        from app.agent import capabilities

        schema = self.schema_for(state)
        active_layer = execution_layer or self.execution_layer(execution_stage)
        transport_tier = capability_tier or self.model_tier(
            execution_stage, execution_layer=active_layer)
        profile = capabilities.get(transport_tier).get("checked") or {}
        errors, validation_error = [], ""

        def make_llm(**overrides):
            values = {"output_contract": output_contract}
            if task_profile:
                values["profile"] = task_profile
            values.update(overrides)
            return self.llm(execution_layer=active_layer,
                            execution_stage=execution_stage, **values)

        for capability, method in (("json_schema", "json_schema"),
                                   ("json_object", "json_mode")):
            if profile.get(capability) is False:
                continue
            try:
                call_messages = list(messages)
                if method == "json_mode":
                    call_messages.append(HumanMessage(content=(
                        "Return exactly one JSON object satisfying this JSON Schema:\n"
                        + json.dumps(schema, ensure_ascii=False))))
                raw = make_llm().with_structured_output(
                    _named(schema, self.name), method=method).invoke(
                        call_messages, config=_call_config(
                            self.name, output_contract, active_layer, execution_stage))
                out = _validate_output(raw, schema)
                capabilities.record(transport_tier, capability, True)
                return out
            except Exception as exc:
                errors.append(f"{capability}: {str(exc)[:180]}")
                if _capability_is_unsupported(exc, capability):
                    capabilities.record(transport_tier, capability, False, str(exc))

        # response_format을 전혀 지원하지 않는 서버: plain chat에 schema를 명시한다.
        schema_text = json.dumps(schema, ensure_ascii=False)
        prompt_messages = list(messages) + [HumanMessage(content=
            _prompt_json_contract(schema_text))]
        raw_text = ""
        try:
            raw = make_llm().invoke(
                prompt_messages, stop=[STRUCTURED_END_TOKEN],
                config=_call_config(
                    self.name, output_contract, active_layer, execution_stage))
        except Exception as exc:
            errors.append(f"prompt_json: {str(exc)[:180]}")
            _raise_unrepairable_structured_output(
                errors, "provider 호출 실패로 교정할 모델 출력이 없습니다.", exc)
        raw_text = str(getattr(raw, "content", raw) or "")
        if not raw_text.strip():
            errors.append("prompt_json: 모델 출력이 비어 있습니다 (chars=0).")
            _raise_unrepairable_structured_output(
                errors, "모델 출력이 비어 있어 교정할 내용이 없습니다.")
        try:
            parsed = _loads_loose(raw_text)
            if parsed is None:
                raise ValueError("JSON 객체를 찾지 못했습니다 (" + _response_shape(raw, raw_text) + ").")
            return _validate_output(parsed, schema)
        except Exception as exc:
            validation_error = str(exc)[:1000]
            errors.append(f"prompt_json: {str(exc)[:180]}")

        # repair는 원 업무를 다시 판단시키는 호출이 아니라 형식만 교정하는 1회 호출이다.
        try:
            repair_source = (("Semantic memo:\n" + repair_context + "\n\n")
                             if repair_context else "")
            repair_layer = "projection"
            repaired = self.llm(
                execution_layer=repair_layer, execution_stage="repair",
                profile="fast_structured", output_contract="typed_projection").invoke([
                SystemMessage(content=(
                    "Preserve the output's meaning. Repair only JSON syntax and schema violations. "
                    f"Return raw JSON, then emit {STRUCTURED_END_TOKEN}. The marker is transport "
                    "framing, not JSON. Never use Markdown, a code fence, or an explanation.")),
                HumanMessage(content=(repair_source + f"Validation error:\n{validation_error}"
                                      f"\n\nJSON Schema:\n{schema_text}"
                                      f"\n\nOutput to repair:\n{raw_text[:12000]}"))
                ], stop=[STRUCTURED_END_TOKEN],
                config=_call_config(self.name, output_contract + "_repair",
                                    repair_layer, "repair"))
            parsed = _loads_loose(str(getattr(repaired, "content", repaired) or ""))
            if parsed is None:
                raise ValueError("repair 결과에서 JSON 객체를 찾지 못했습니다.")
            return _validate_output(parsed, schema)
        except Exception as exc:
            errors.append(f"repair: {str(exc)[:180]}")
            raise RuntimeError("structured output 실패 — " + " | ".join(errors)) from exc

    def _invoke_semantic_projection(self, state: AgentState, messages: list,
                                    projection_tier: str) -> dict:
        """Run complex semantic judgment once, then project its memo to the Role schema.

        The projector never receives the original prompt/evidence. A projector parse or
        validation failure is repaired inside ``_invoke_structured_transport`` and never
        reruns the semantic model.
        """
        schema = self.schema_for(state)
        fields = ", ".join(str(k) for k in (schema.get("properties") or {}))
        from app.agent.workflow.anchors import (
            format_anchor_contract, format_requested_outcome_contract,
        )
        anchor_contract = format_anchor_contract(state)
        outcome_contract = format_requested_outcome_contract(state)
        memo_instruction = (
            "Produce a compact semantic decision memo for a later typed projection. "
            "Resolve the task using the system instructions and evidence above. Preserve exact "
            "ticket keys, identifiers, names, quoted facts, hierarchy, and unresolved gaps. "
            "Do not invent a value merely to fill a field. Do not output JSON or Markdown. "
            f"Cover these top-level output fields: {fields or '(schema-defined fields)'}. "
            + ((anchor_contract + " Preserve every listed anchor in the semantic memo; do not "
                "translate, renumber, singularize, or silently omit it. ") if anchor_contract else "")
            + ((outcome_contract + " Treat each requested-outcome instruction as an authoritative "
                "user result. Copy its contract id, outcome id, source task id, and instruction "
                "verbatim into the memo. Evidence may refine implementation context but must not "
                "replace, reverse, or omit the requested action and object. Record which root or "
                "child item serves each outcome; distinguish a child stage inherited from its "
                "parent from a child serving a different explicit outcome. ")
               if outcome_contract else "")
            + f"End the memo with {SEMANTIC_MEMO_END_TOKEN}."
        )
        semantic_layer = self.execution_layer("synthesis")
        raw = self.llm(execution_layer=semantic_layer, execution_stage="semantic",
                       output_contract="semantic_memo").invoke(
            list(messages) + [HumanMessage(content=memo_instruction)],
            stop=[SEMANTIC_MEMO_END_TOKEN],
            config=_call_config(self.name, "semantic_memo", semantic_layer, "semantic"),
        )
        metadata = getattr(raw, "response_metadata", None) or {}
        finish = str(metadata.get("finish_reason") or metadata.get("stop_reason") or "").lower()
        if finish in {"length", "max_tokens", "max_output_tokens"}:
            # A truncated memo is more dangerous than malformed JSON: the projector can
            # produce perfectly valid JSON that silently omits the tail of the decision.
            # Fail before projection so callers surface the incomplete semantic stage.
            raise RuntimeError("semantic memo가 출력 길이 한도에서 잘렸습니다.")
        memo = str(getattr(raw, "content", raw) or "").strip()
        if SEMANTIC_MEMO_END_TOKEN in memo:
            memo = memo.split(SEMANTIC_MEMO_END_TOKEN, 1)[0].rstrip()
        if not memo:
            raise RuntimeError("semantic memo가 비어 있습니다.")

        projection_messages = [
            SystemMessage(content=(
                "You are a literal typed projection engine. Convert only the supplied semantic "
                "memo to the target JSON Schema. Preserve exact identifiers and facts. Never "
                "add, reinterpret, or infer information absent from the memo; represent missing "
                "values only as the schema permits.")),
            HumanMessage(content="Semantic memo:\n" + memo
                         + (("\n\n" + anchor_contract
                             + "\nKeep each listed anchor verbatim in every relevant title, body, "
                               "or requested field; never turn an ordinal into a bare suffix.")
                            if anchor_contract else "")
                         + (("\n\n" + outcome_contract
                             + "\nWhen the target schema exposes outcome contract fields, copy the "
                               "contract id and bind every draft item to the applicable opaque "
                               "outcome id(s). Preserve the requested action and object in title, "
                               "scope, and acceptance criteria; never substitute an evidence method "
                               "for the requested result. A nested child stage may inherit its "
                               "parent's mapping; emit child outcome refs only for a distinct explicit "
                               "mapping and never copy every contract id to every child.")
                            if outcome_contract else "")),
        ]
        repair_context = (memo
                          + (("\n\n" + anchor_contract) if anchor_contract else "")
                          + (("\n\n" + outcome_contract) if outcome_contract else ""))
        return self._invoke_structured_transport(
            state, projection_messages, output_contract="typed_projection",
            capability_tier=projection_tier, task_profile="fast_structured",
            repair_context=repair_context, execution_layer="projection",
            execution_stage="projection",
        )

    def invoke_structured(self, state: AgentState, messages: list) -> dict:
        """Choose native/direct or semantic→typed projection from capabilities + manifest."""
        projection_tier = self._semantic_projection_tier()
        if projection_tier:
            return self._invoke_semantic_projection(state, messages, projection_tier)
        return self._invoke_structured_transport(state, messages)

    @abstractmethod
    def node(self):
        """바깥 그래프에 붙일 함수. State 의 **갱신분**만 돌려줘야 한다."""

    def fallback(self, state: AgentState, err: Exception) -> dict:
        """모델/도구가 죽어도 그래프는 답을 내야 한다 — 빈 화면보다 사유가 낫다."""
        return {"error": f"[{self.name}] {str(err)[:300]}",
                "trace": note(state, self.name, f"실패: {str(err)[:120]}")}


class StructuredAgent(Agent):
    """한 번 묻고 스키마로 받는 역할. 그래프를 씌울 게 없다 — 부를 곳이 한 군데뿐이다."""

    def node(self):
        return self._run

    def _run(self, state: AgentState) -> dict:
        try:
            out = self.invoke_structured(state, [
                SystemMessage(content=self.system(state)),
                HumanMessage(content=self.task(state))])
            return self.apply(state, out)
        except Exception as e:
            return self.fallback(state, e)

    def _json_fallback(self, state: AgentState):
        """스키마를 프롬프트로 주고 평문 JSON 을 받아 낸다. 실패하면 None."""
        import json
        try:
            layer = self.execution_layer("synthesis")
            msg = self.llm(execution_layer=layer, execution_stage="fallback").invoke([
                SystemMessage(content=self.system(state)),
                HumanMessage(content=(
                    self.task(state)
                    + "\n\n---\n**Required output format:** Return exactly one JSON object satisfying "
                      "the JSON Schema below. Include no prose, preface, or code fence; begin and end with braces.\n"
                    + json.dumps(self.schema_for(state), ensure_ascii=False)))],
                config=_call_config(self.name, "structured_fallback", layer, "fallback"))
            return _loads_loose(str(getattr(msg, "content", msg) or ""))
        except Exception:
            return None


class TextAgent(Agent):
    """사용자에게 그대로 보여줄 문장을 만드는 역할. 스키마를 씌우지 않는 유일한 자리다.

    우리가 파싱할 일이 없는 출력에 JSON 을 강제하면 문장만 딱딱해진다.
    """

    def schema(self):
        return {}

    def node(self):
        return self._run

    def _run(self, state: AgentState) -> dict:
        try:
            layer = self.execution_layer("synthesis")
            msg = self.llm(execution_layer=layer, execution_stage="synthesis").invoke(
                [SystemMessage(content=self.system(state)),
                 HumanMessage(content=self.task(state))],
                config=_call_config(self.name, "text", layer, "synthesis"))
            return self.apply(state, {"text": str(getattr(msg, "content", msg) or "").strip()})
        except Exception as e:
            return self.fallback(state, e)


class _Scratch(TypedDict, total=False):
    """도구 루프의 **작업 메모**. 바깥 대화(messages)와 섞지 않는다.

    ResearchAnalyst 이 도구를 여덟 번 부른 기록이 사용자 대화창에 남으면 안 되고, 다음 턴의
    컨텍스트에 그게 다시 실리면 토큰만 먹는다. 결론만 State 로 올린다.
    """
    messages: Annotated[list, add_messages]
    steps: int


class ToolAgent(Agent):
    """도구를 부르며 스스로 몇 걸음 걷는 역할(ReAct).

    ```
    think ──(도구 호출 있음)──> act ──> think ...
      └────(없음)────> conclude(구조화 출력) ──> END
    ```

    `conclude` 를 따로 두는 이유: 도구를 부르는 모델에게 동시에 스키마까지 강제하면 둘 다
    나빠진다. 걷는 동안엔 자유롭게 두고, 다 걷고 나서 **한 번만** 스키마로 정리시킨다.
    """

    def build(self):
        """도구 루프 서브그래프. `node()` 가 이걸 돌리고 결론만 State 로 옮긴다."""
        g = StateGraph(_Scratch)
        g.add_node("think", self._think)
        g.add_node("act", self._act)
        g.add_edge(START, "think")
        g.add_conditional_edges("think", self._route, {"act": "act", "done": END})
        g.add_edge("act", "think")
        return g.compile()

    def node(self):
        sub = self.build()

        def run(state: AgentState) -> dict:
            try:
                scratch = sub.invoke({"messages": [
                    SystemMessage(content=self.system(state)),
                    HumanMessage(content=self.task(state))], "steps": 0})
                out = self._conclude(state, scratch["messages"])
                return self.apply(state, out)
            except Exception as e:
                return self.fallback(state, e)

        return run

    def _think(self, scratch: _Scratch) -> dict:
        from app.agent import capabilities
        decision_layer = self.execution_layer("decision")
        decision_tier = self.model_tier("decision", execution_layer=decision_layer)
        profile = capabilities.get(decision_tier).get("checked") or {}
        try:
            if not capabilities.native_tools_allowed(tier=decision_tier):
                raise RuntimeError("provider policy: native tools disabled")
            if profile.get("tools") is False:
                raise RuntimeError("capability probe: tools unsupported")
            # 병렬 tool call은 probe 결과가 true일 때만 켠다. 모르는 서버에는 보수적으로 false.
            msg = self.llm(execution_layer=decision_layer,
                           execution_stage="decision",
                           profile="fast_structured").bind_tools(
                self.tools, parallel_tool_calls=profile.get("parallel_tools") is True
            ).invoke(scratch["messages"], config=_call_config(
                self.name, "tool_decision", decision_layer, "decision"))
            capabilities.record(decision_tier, "tools", True)
        except Exception as exc:
            capabilities.record(decision_tier, "tools", False, str(exc))
            msg = self._think_without_native_tools(scratch)
        return {"messages": [msg], "steps": (scratch.get("steps") or 0) + 1}

    def _think_without_native_tools(self, scratch: _Scratch):
        """tool-calling 미지원 서버용: JSON 계획을 받아 등록된 도구만 코드가 실행한다."""
        import json
        import uuid
        catalog = []
        owned = {t.name: t for t in self.tools}
        for tool_obj in self.tools:
            schema = {}
            try:
                schema = tool_obj.args_schema.model_json_schema()
            except Exception:
                pass
            catalog.append({"name": tool_obj.name,
                            "description": " ".join((tool_obj.description or "").split())[:600],
                            "input_schema": schema})
        instruction = HumanMessage(content=(
            "This server has no native tool-calling support. Plan calls only from the registered catalog "
            "using JSON shaped as {\"tool_calls\":[{\"name\":str,\"args\":object}],\"answer\":str}. "
            "When more retrieval is needed, return tool_calls. When evidence is sufficient, return an empty "
            "array and answer. Never invent an unregistered name.\n\nRegistered tools:\n"
            + json.dumps(catalog, ensure_ascii=False)))
        decision_schema = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "tool_calls": {"type": "array", "items": {"type": "object",
                    "additionalProperties": False, "properties": {
                        "name": {"type": "string", "enum": sorted(owned)},
                        "args": {"type": "object"}}, "required": ["name", "args"]}},
                "answer": {"type": "string"}},
            "required": ["tool_calls", "answer"],
        }
        parsed = invoke_schema(decision_schema,
                               list(scratch.get("messages") or []) + [instruction],
                               execution_layer=self.execution_layer("decision"),
                               execution_stage="decision", profile="fast_structured",
                               name="ToolDecision",
                               llm_factory=self.llm, role_id=self.name,
                               call_label="tool_decision")
        calls = []
        for item in parsed.get("tool_calls") or []:
            name = str((item or {}).get("name") or "")
            args = (item or {}).get("args") or {}
            if name in owned and isinstance(args, dict):
                schema_model = getattr(owned[name], "args_schema", None)
                if schema_model is not None:
                    args = schema_model.model_validate(args).model_dump()
                calls.append({"name": name, "args": args, "id": "fallback_" + uuid.uuid4().hex[:12]})
        return AIMessage(content=str(parsed.get("answer") or "") if not calls else "",
                         tool_calls=calls)

    def _act(self, scratch: _Scratch) -> dict:
        from langgraph.prebuilt import ToolNode
        # 모델이 한 턴에 여러 도구를 부르면(독립 조회 묶음) **동시에** 실행한다 —
        # mock 은 밀리초지만 prod Jira 는 호출당 수백 ms 라 직렬이면 그대로 합산된다.
        last = (scratch.get("messages") or [])[-1]
        calls = list(getattr(last, "tool_calls", None) or [])
        if len(calls) <= 1:
            return ToolNode(self.tools).invoke(scratch)
        from concurrent.futures import ThreadPoolExecutor
        from langchain_core.messages import AIMessage
        node = ToolNode(self.tools)

        def one(tc):
            # 호출 하나짜리 가짜 메시지로 ToolNode 를 태운다 — 도구 조회·에러 처리 재사용.
            fake = AIMessage(content="", tool_calls=[tc])
            return node.invoke({"messages": [fake]})["messages"]

        with ThreadPoolExecutor(max_workers=min(4, len(calls))) as ex:
            outs = list(ex.map(one, calls))
        return {"messages": [m for ms in outs for m in ms]}

    def _route(self, scratch: _Scratch) -> str:
        last = (scratch.get("messages") or [])[-1] if scratch.get("messages") else None
        if (scratch.get("steps") or 0) >= self.max_steps:
            return "done"
        return "act" if getattr(last, "tool_calls", None) else "done"

    def _conclude(self, state: AgentState, scratch_messages: list) -> dict:
        """걸은 기록을 놓고 **한 번만** 스키마로 정리시킨다."""
        log = _transcript(scratch_messages)
        out = self.invoke_structured(state, [
            SystemMessage(content=self.system(state)),
            HumanMessage(content=f"{self.task(state)}\n\n### Tool Transcript Data\n\n{log}\n\n"
                                 "Use only this transcript. Before synthesizing, identify two or three core "
                                 "facts supporting the conclusion and preserve their exact title, key, and "
                                 "number. Never add a fact absent from the transcript.")])
        return out


def _transcript(messages: list, limit: int = 28000) -> str:
    """도구 왕복 기록을 읽을 수 있는 글로. 결론 단계의 **유일한** 근거다.

    상한을 8KB 로 뒀다가 실측 사고: 그룹 활동 질의(도구 6회 × 결과 ≤1.5KB ≈ 9KB+)에서
    앞쪽 기록(로스터·활동 내역)이 통째로 잘려, 모델이 "다음과 같습니다:" 뒤에 **빈 목록**을
    쓴 처참한 답이 나갔다. 근거를 자르면 날조가 아니라 공백이 나온다 — 상한은 도구 상한
    (MAX_TOOL_STEPS × 결과 캡)을 다 담고도 남게 잡는다(≈7k 토큰, 결론 1회 비용으로 수용).
    """
    rows = []
    for m in messages or []:
        t = getattr(m, "type", "")
        if t == "ai":
            for tc in (getattr(m, "tool_calls", None) or []):
                rows.append(f"[Tool Call] {tc.get('name')}({_short(tc.get('args'))})")
            # The decision model's prose is routing scratch, not retrieved evidence. Feeding
            # it to synthesis let an ungrounded/malicious note override the actual tool result.
            # Only validated tool calls and their returned payloads cross this trust boundary.
        elif t == "tool":
            rows.append(f"[Tool Result] {getattr(m, 'name', '')}: {_short(m.content, 1500)}")
    return "\n".join(rows)[-limit:]


def _short(v, n: int = 300) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[:n] + "…"


def _named(schema, name: str):
    """JSON Schema 에 `title` 이 없으면 붙인다. pydantic 모델은 이미 이름이 있으니 그대로."""
    if not isinstance(schema, dict):
        return schema
    if schema.get("title") or schema.get("name"):
        return schema
    return dict(schema, title=name)


def _as_dict(out) -> dict:
    if hasattr(out, "model_dump"):
        out = out.model_dump()
    out = dict(out or {}) if not isinstance(out, dict) else out

    # ★ 스키마 에코 언랩 — 모델이 값 대신 **스키마 래퍼를 흉내** 내서
    #   {"type":"object","properties":{intent:"plan_work",...}} 로 답하는 경우가 있다
    #   (실측: 영어 프롬프트 전환 직후 전 역할에서 발생 — intent·questions 가 전부 유실돼
    #   분류가 죽고 되묻기 폼이 안 떴다). 값은 properties 안에 다 있으므로 벗겨서 쓴다.
    #   판정: 최상위가 스키마 골격 키들뿐이고 properties 가 dict 일 때만 — 실제 필드에
    #   "properties" 라는 이름을 쓰는 역할은 없다.
    if (isinstance(out.get("properties"), dict)
            and set(out) <= {"type", "properties", "required", "title", "description"}):
        out = out["properties"]
    return out


__all__ = ["Agent", "StructuredAgent", "TextAgent", "ToolAgent", "AIMessage",
           "invoke_schema",
           "MAX_TOOL_STEPS"]
