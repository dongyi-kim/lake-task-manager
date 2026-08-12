"""agent/fake.py — 키 없이 도는 결정적 LLM/임베딩.

**왜 필요한가**: 개발 PC(사내 VDI)에서는 사내 AOAI 가 Private Endpoint 로 잠겨 있어
`403 Public access is disabled` 가 난다 — 코드로 우회할 수 없다. 그래서 그래프 분기·State 전이·
도구 호출 계약 같은 **구조**는 LLM 없이 검증할 수 있어야 한다. 테스트도 키 없이 돌아야 한다.

**결정적**: 같은 입력이면 같은 출력. 해시 기반이라 시드도 네트워크도 필요 없다.
구조 검증이 목적이므로 문장은 그럴듯하지 않아도 된다 — 대신 **입력을 되비추어** 어떤 프롬프트가
그 노드에 들어갔는지 테스트에서 확인할 수 있게 한다.

Structured Output(`with_structured_output`)도 지원한다. RequestArchitect 의 의도 분류처럼 스키마를 강제하는
노드가 많아서, 이게 없으면 fake 로는 그래프를 한 걸음도 못 굴린다.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, List, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _last_human(messages: List[BaseMessage]) -> str:
    for m in reversed(messages or []):
        if getattr(m, "type", "") in ("human", "system"):
            return str(getattr(m, "content", ""))
    return str(getattr(messages[-1], "content", "")) if messages else ""


class FakeChat(BaseChatModel):
    """입력을 되비추는 결정적 채팅 모델.

    `responses` 를 주면 그 목록을 순서대로 내보낸다(시나리오 테스트용).
    다 쓰면 다시 되비추기로 돌아간다 — 목록이 짧다고 테스트가 죽지 않게.
    """

    responses: Optional[List[Any]] = None
    _cursor: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def bind_tools(self, tools, **kwargs):
        """ReAct 루프가 fake 로도 굴러가게 한다. 도구 스키마는 쓰지 않고 이름만 기억한다.

        **자기 자신을 돌려준다** — 복제하면 `responses` 커서가 갈라져 시나리오가 어긋난다.
        가짜 모델이니 상태를 공유해도 잃을 게 없다.
        """
        object.__setattr__(self, "_tool_names", [getattr(t, "name", str(t)) for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager: CallbackManagerForLLMRun = None,
                  **kwargs) -> ChatResult:
        item = self._next(messages, kwargs)
        # 시나리오가 {"tool": ..., "args": {...}} 를 주면 **도구 호출**을 낸다.
        # 이게 있어야 "모델이 도구를 부르고 → 결과를 받아 다시 생각하는" 경로를 키 없이 시험한다.
        if isinstance(item, dict) and item.get("tool"):
            msg = AIMessage(content=item.get("content", ""), tool_calls=[{
                "name": item["tool"], "args": item.get("args") or {},
                "id": f"fake_call_{self._cursor}"}])
        else:
            msg = AIMessage(content=str(item))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _next(self, messages, kwargs):
        if self.responses:
            i = self._cursor
            object.__setattr__(self, "_cursor", i + 1)
            if i < len(self.responses):
                return self.responses[i]

        prompt = _last_human(messages)
        schema = kwargs.get("_fake_schema")
        if schema is not None:
            return json.dumps(_fill_schema(schema, prompt), ensure_ascii=False)
        head = re.sub(r"\s+", " ", prompt).strip()[:120]
        return f"[fake] {head} :: {_digest(prompt)[:8]}"

    # LangChain 의 구조화 출력 경로. 스키마를 kwargs 로 흘려 _generate 가 JSON 을 내게 한다.
    def with_structured_output(self, schema, **kwargs):
        # ★ **가짜가 실물보다 관대하면 안 된다.** OpenAI/AOAI 는 구조화 출력을 함수 호출로
        #   구현하므로 스키마에 이름이 있어야 한다. 여기서 조용히 받아 주면 fake 테스트는
        #   전부 통과하고 실 키를 꽂는 순간 여섯 역할이 한꺼번에 죽는다 — 실제로 그랬다.
        if isinstance(schema, dict) and not (schema.get("title") or schema.get("name")):
            raise ValueError(
                "구조화 출력 스키마에 이름이 없습니다(title 또는 name). "
                "실제 OpenAI/AOAI 는 이 스키마를 함수로 만들지 못해 'Unsupported function' 으로 거부합니다.")
        return _FakeStructured(self, schema)


class _FakeStructured:
    """`with_structured_output` 반환값 흉내 — invoke 하면 스키마에 맞는 객체를 돌려준다."""

    def __init__(self, llm: FakeChat, schema):
        self.llm, self.schema = llm, schema

    def invoke(self, input, config=None, **kwargs):
        msgs = input if isinstance(input, list) else [input]
        prompt = _last_human([m if isinstance(m, BaseMessage) else _as_msg(m) for m in msgs])
        data = _fill_schema(self.schema, prompt)
        if hasattr(self.schema, "model_validate"):          # pydantic v2 모델이면 인스턴스로
            try:
                return self.schema.model_validate(data)
            except Exception:
                return data
        return data


def _as_msg(x):
    from langchain_core.messages import HumanMessage
    return x if isinstance(x, BaseMessage) else HumanMessage(content=str(x))


def _fill_schema(schema, prompt: str) -> dict:
    """스키마의 **첫 허용값/타입 기본값**으로 채운다 — 결정적이고, 분기 테스트에 충분하다.

    pydantic 모델과 JSON Schema dict 를 모두 받는다.
    """
    js = schema
    if hasattr(schema, "model_json_schema"):
        js = schema.model_json_schema()
    props = (js or {}).get("properties") or {}
    out = {}
    for name, spec in props.items():
        out[name] = _fill_one(name, spec, prompt, js)
    return out


def _fill_one(name, spec, prompt, root):
    if "enum" in spec and spec["enum"]:
        # 결정적이되 프롬프트에 따라 갈리게 — 분기 커버리지를 위해 해시로 고른다.
        idx = int(_digest(prompt + name)[:4], 16) % len(spec["enum"])
        return spec["enum"][idx]
    if "$ref" in spec or "allOf" in spec:                    # 중첩 모델은 빈 dict 로 (구조만)
        return {}
    t = spec.get("type")
    if t == "integer":
        return int(_digest(prompt + name)[:2], 16) % 8
    if t == "number":
        return round(int(_digest(prompt + name)[:2], 16) % 100 / 100, 2)
    if t == "boolean":
        return int(_digest(prompt + name)[:2], 16) % 2 == 0
    if t == "array":
        return []
    if t == "object":
        return {}
    return f"[fake:{name}] " + re.sub(r"\s+", " ", prompt).strip()[:60]


class FakeEmbeddings(Embeddings):
    """해시 기반 결정적 임베딩.

    같은 글은 같은 벡터, 다른 글은 다른 벡터라 **유사도 검색 경로**(FAISS 인덱싱 → 검색 →
    상위 k)는 그대로 검증된다. 의미적 유사도는 없다 — 그건 실 LLM 경로에서 본다.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _vec(self, text: str) -> List[float]:
        h = hashlib.sha256((text or "").encode("utf-8")).digest()
        raw = (h * ((self.dim // len(h)) + 1))[: self.dim]
        v = [(b - 127.5) / 127.5 for b in raw]
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._vec(text)
