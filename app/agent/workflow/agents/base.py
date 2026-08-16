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


def _loads_loose(text: str):
    """모델이 뱉은 텍스트에서 **JSON 한 덩이**를 건져 낸다. 못 건지면 None.

    구조화 출력을 지원하지 않는 서버에서는 답이 이런 꼴로 온다:
        ```json
{...}
```        /        여기 결과입니다: {...}
    엄격한 파서는 둘 다 거부한다 — 그런데 **안에 든 것은 우리가 원하던 그 JSON** 이다.
    관대하게 받아 내되, 지어내지는 않는다(못 찾으면 None 을 돌려 원래 실패 경로로 간다).
    """
    import json
    import re as _re
    t = (text or "").strip()
    if not t:
        return None
    m = _re.search(r"```(?:json)?\s*(.+?)```", t, _re.S)     # 코드펜스 안이 우선
    if m:
        t = m.group(1).strip()
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except Exception:
        pass
    # 앞뒤에 말이 붙은 경우 — 처음 여는 중괄호부터 **짝이 맞는** 자리까지만 떼어 낸다.
    i = t.find("{")
    if i < 0:
        return None
    depth, in_str, esc = 0, False, False
    for j in range(i, len(t)):
        c = t[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    v = json.loads(t[i:j + 1])
                    return v if isinstance(v, dict) else None
                except Exception:
                    return None
    return None


def _validate_output(value, schema: dict) -> dict:
    """관대한 추출 뒤에는 반드시 동일한 JSON Schema로 엄격 검증한다."""
    from jsonschema import validate
    out = _as_dict(value)
    validate(instance=out, schema=schema)
    return out


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


def invoke_schema(schema: dict, messages: list, tier: str = "complex",
                  temperature: float = 0.0, name: str = "AdhocOutput") -> dict:
    """Role 밖의 보정 호출도 공통 structured-output fallback을 사용하게 한다."""
    import json
    from app.agent import capabilities

    named = _named(schema, name)
    profile = capabilities.get(tier).get("checked") or {}
    errors, raw_text = [], ""
    for capability, method in (("json_schema", "json_schema"),
                               ("json_object", "json_mode")):
        if profile.get(capability) is False:
            continue
        try:
            call_messages = list(messages)
            if method == "json_mode":
                call_messages.append(HumanMessage(content=(
                    "Return exactly one JSON object that satisfies this JSON Schema:\n"
                    + json.dumps(schema, ensure_ascii=False))))
            out = _cfg.get_llm(temperature=temperature, tier=tier).with_structured_output(
                named, method=method).invoke(call_messages)
            out = _validate_output(out, schema)
            capabilities.record(tier, capability, True)
            return out
        except Exception as exc:
            errors.append(f"{capability}: {str(exc)[:160]}")
            if _capability_is_unsupported(exc, capability):
                capabilities.record(tier, capability, False, str(exc))
    try:
        raw = _cfg.get_llm(temperature=temperature, tier=tier).invoke(
            list(messages) + [HumanMessage(content=(
                "Return exactly one JSON object satisfying the JSON Schema below. Do not include prose or a code fence.\n"
                + json.dumps(schema, ensure_ascii=False)))])
        raw_text = str(getattr(raw, "content", raw) or "")
        parsed = _loads_loose(raw_text)
        if parsed is None:
            raise ValueError("JSON 객체를 찾지 못했습니다.")
        return _validate_output(parsed, schema)
    except Exception as exc:
        errors.append(f"prompt_json: {str(exc)[:160]}")
    try:
        raw = _cfg.get_llm(temperature=0, tier=tier).invoke([
            SystemMessage(content="Preserve meaning exactly. Repair only JSON syntax and schema violations."),
            HumanMessage(content=(json.dumps(schema, ensure_ascii=False)
                                  + "\n\nOutput to repair:\n" + raw_text[:12000]))])
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
    temperature: float = 0.2
    # 모델 티어 — simple(판단이 얕은 역할: 의도 분류·결정적 실행)은 저렴한 모델을 쓴다.
    # 사용자가 설정창에서 '간단한 역할 모델'을 지정했을 때만 갈라지고, 아니면 하나로 돈다.
    tier: str = "complex"
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

    @abstractmethod
    def apply(self, state: AgentState, out: dict) -> dict:
        """모델 출력 → State 갱신분. 여기서만 State 를 만진다."""

    def llm(self, **kw):
        return _cfg.get_llm(temperature=self.temperature, tier=self.tier, **kw)

    def structured(self, method: str = "json_schema", **kw):
        """스키마로 받는 모델. **스키마에 이름을 붙여서** 넘긴다.

        OpenAI/AOAI 는 구조화 출력을 함수 호출로 구현하므로 스키마가 함수 이름을 가져야 한다.
        이름 없는 JSON Schema 를 그대로 주면 `Unsupported function` 으로 죽는다 — 실 키로
        처음 돌렸을 때 여섯 역할이 전부 여기서 넘어졌다. 역할마다 적어 두면 빠뜨리는 사람이
        생기므로 여기서 한 번에 붙인다.
        """
        return self.llm(**kw).with_structured_output(
            _named(self.schema(), self.name), method=method)

    def invoke_structured(self, state: AgentState, messages: list) -> dict:
        """provider capability에 맞춰 구조화 출력 fallback ladder를 실행한다.

        json_schema → json_object → prompt-only JSON → repair 1회 순서다. 성공한 결과도
        로컬 JSON Schema 검증을 통과해야 한다. openai_compat 서버가 response_format이나
        tools를 거부해도 role 전체가 ``Invalid json output``으로 사망하지 않게 한다.
        """
        import json
        from app.agent import capabilities

        profile = capabilities.get(self.tier).get("checked") or {}
        errors = []
        for capability, method in (("json_schema", "json_schema"),
                                   ("json_object", "json_mode")):
            if profile.get(capability) is False:
                continue
            try:
                call_messages = list(messages)
                if method == "json_mode":
                    call_messages.append(HumanMessage(content=(
                        "Return exactly one JSON object satisfying this JSON Schema:\n"
                        + json.dumps(self.schema(), ensure_ascii=False))))
                raw = self.structured(method=method).invoke(call_messages)
                out = _validate_output(raw, self.schema())
                capabilities.record(self.tier, capability, True)
                return out
            except Exception as exc:
                errors.append(f"{capability}: {str(exc)[:180]}")
                if _capability_is_unsupported(exc, capability):
                    capabilities.record(self.tier, capability, False, str(exc))

        # response_format을 전혀 지원하지 않는 서버: plain chat에 schema를 명시한다.
        schema_text = json.dumps(self.schema(), ensure_ascii=False)
        prompt_messages = list(messages) + [HumanMessage(content=(
            "Output format: return exactly one JSON object satisfying the JSON Schema below. "
            "Do not include prose, a preface, or a Markdown code fence.\n" + schema_text))]
        raw_text = ""
        try:
            raw = self.llm().invoke(prompt_messages)
            raw_text = str(getattr(raw, "content", raw) or "")
            parsed = _loads_loose(raw_text)
            if parsed is None:
                raise ValueError("JSON 객체를 찾지 못했습니다.")
            return _validate_output(parsed, self.schema())
        except Exception as exc:
            errors.append(f"prompt_json: {str(exc)[:180]}")

        # repair는 원 업무를 다시 판단시키는 호출이 아니라 형식만 교정하는 1회 호출이다.
        try:
            repaired = self.llm().invoke([
                SystemMessage(content="Preserve the output's meaning. Repair only JSON syntax and schema violations."),
                HumanMessage(content=f"JSON Schema:\n{schema_text}\n\nOutput to repair:\n{raw_text[:12000]}")])
            parsed = _loads_loose(str(getattr(repaired, "content", repaired) or ""))
            if parsed is None:
                raise ValueError("repair 결과에서 JSON 객체를 찾지 못했습니다.")
            return _validate_output(parsed, self.schema())
        except Exception as exc:
            errors.append(f"repair: {str(exc)[:180]}")
            raise RuntimeError("structured output 실패 — " + " | ".join(errors)) from exc

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
            msg = self.llm().invoke([
                SystemMessage(content=self.system(state)),
                HumanMessage(content=(
                    self.task(state)
                    + "\n\n---\n**Required output format:** Return exactly one JSON object satisfying "
                      "the JSON Schema below. Include no prose, preface, or code fence; begin and end with braces.\n"
                    + json.dumps(self.schema(), ensure_ascii=False)))])
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
            msg = self.llm().invoke([SystemMessage(content=self.system(state)),
                                     HumanMessage(content=self.task(state))])
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
        profile = capabilities.get(self.tier).get("checked") or {}
        try:
            if not capabilities.native_tools_allowed():
                raise RuntimeError("provider policy: native tools disabled")
            if profile.get("tools") is False:
                raise RuntimeError("capability probe: tools unsupported")
            # 병렬 tool call은 probe 결과가 true일 때만 켠다. 모르는 서버에는 보수적으로 false.
            msg = self.llm().bind_tools(
                self.tools, parallel_tool_calls=profile.get("parallel_tools") is True
            ).invoke(scratch["messages"])
            capabilities.record(self.tier, "tools", True)
        except Exception as exc:
            capabilities.record(self.tier, "tools", False, str(exc))
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
        raw = self.llm().invoke(list(scratch.get("messages") or []) + [instruction])
        parsed = _loads_loose(str(getattr(raw, "content", raw) or "")) or {}
        calls = []
        for item in parsed.get("tool_calls") or []:
            name = str((item or {}).get("name") or "")
            args = (item or {}).get("args") or {}
            if name in owned and isinstance(args, dict):
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
            if getattr(m, "content", ""):
                rows.append(f"[Model Note] {m.content}")
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
