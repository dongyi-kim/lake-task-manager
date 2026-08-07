"""agent/workflow/agents/base.py — 역할 에이전트의 공통 뼈대(추상 클래스 + 서브그래프).

여섯 역할이 하는 일은 제각각이지만 **모양은 두 가지뿐**이다.

  · `StructuredAgent` — 한 번 묻고 스키마로 받는다. 판단만 하는 역할(Planner·Reviewer).
  · `ToolAgent` — 도구를 부르며 스스로 몇 걸음 걷는다(Historian·Refiner·Assigner·Operator).
    이게 ReAct 다: 생각 → 도구 → 결과를 보고 다시 생각. 몇 걸음 걸을지는 **모델이 정한다**.

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


class Agent(ABC):
    """역할 하나. `name` 은 그래프 노드명과 같아야 한다(State.Node 의 상수를 쓴다)."""

    name: str = "agent"
    temperature: float = 0.2

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
        return _cfg.get_llm(temperature=self.temperature, **kw)

    def structured(self, **kw):
        """스키마로 받는 모델. **스키마에 이름을 붙여서** 넘긴다.

        OpenAI/AOAI 는 구조화 출력을 함수 호출로 구현하므로 스키마가 함수 이름을 가져야 한다.
        이름 없는 JSON Schema 를 그대로 주면 `Unsupported function` 으로 죽는다 — 실 키로
        처음 돌렸을 때 여섯 역할이 전부 여기서 넘어졌다. 역할마다 적어 두면 빠뜨리는 사람이
        생기므로 여기서 한 번에 붙인다.
        """
        return self.llm(**kw).with_structured_output(_named(self.schema(), self.name))

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
            out = self.structured().invoke(
                [SystemMessage(content=self.system(state)),
                 HumanMessage(content=self.task(state))])
            return self.apply(state, _as_dict(out))
        except Exception as e:
            return self.fallback(state, e)


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

    Historian 이 도구를 여덟 번 부른 기록이 사용자 대화창에 남으면 안 되고, 다음 턴의
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
        msg = self.llm().bind_tools(self.tools).invoke(scratch["messages"])
        return {"messages": [msg], "steps": (scratch.get("steps") or 0) + 1}

    def _act(self, scratch: _Scratch) -> dict:
        from langgraph.prebuilt import ToolNode
        return ToolNode(self.tools).invoke(scratch)

    def _route(self, scratch: _Scratch) -> str:
        last = (scratch.get("messages") or [])[-1] if scratch.get("messages") else None
        if (scratch.get("steps") or 0) >= MAX_TOOL_STEPS:
            return "done"
        return "act" if getattr(last, "tool_calls", None) else "done"

    def _conclude(self, state: AgentState, scratch_messages: list) -> dict:
        """걸은 기록을 놓고 **한 번만** 스키마로 정리시킨다."""
        log = _transcript(scratch_messages)
        out = self.structured().invoke([
            SystemMessage(content=self.system(state)),
            HumanMessage(content=f"{self.task(state)}\n\n### 조사한 내용\n{log}\n\n"
                                 "위 조사 결과만을 근거로 정리하라. 조사에 없는 것을 지어내지 마라.")])
        return _as_dict(out)


def _transcript(messages: list, limit: int = 8000) -> str:
    """도구 왕복 기록을 읽을 수 있는 글로. 결론 단계의 유일한 근거다."""
    rows = []
    for m in messages or []:
        t = getattr(m, "type", "")
        if t == "ai":
            for tc in (getattr(m, "tool_calls", None) or []):
                rows.append(f"[도구 호출] {tc.get('name')}({_short(tc.get('args'))})")
            if getattr(m, "content", ""):
                rows.append(f"[생각] {m.content}")
        elif t == "tool":
            rows.append(f"[도구 결과] {getattr(m, 'name', '')}: {_short(m.content, 1500)}")
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
           "MAX_TOOL_STEPS"]
