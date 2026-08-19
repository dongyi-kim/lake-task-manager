"""agent/usage.py — 토큰을 세고 비용을 보인다. 그리고 **너무 큰 입력을 미리 막는다**.

두 가지를 서로 다른 시점에 한다. 하나로 갈음할 수 없다.

  · **보내기 전** — `tiktoken` 으로 직접 센다. 응답을 받아야만 알 수 있다면 이미 늦다.
    사용자가 로그 10만 줄을 붙여 넣었을 때 "비쌌습니다"가 아니라 **보내지 않는 것**이 맞다.
  · **받은 뒤**  — 모델이 알려 준 실제 사용량을 모은다. tiktoken 추정치는 시스템 프롬프트·
    도구 스키마·함수 호출 오버헤드를 모르므로 실제와 20~30% 어긋난다.

**왜 굳이 보여 주나** — 에이전트는 한 번 물으면 안에서 LLM 을 예닐곱 번 부른다. 사용자에게는
질문 하나로 보이므로, 비용이 얼마나 드는지 감이 잡히지 않는다. 숫자를 보여야 "이건 비싼
질문이었다"를 알고 다음에 다르게 묻는다.

가격은 바뀐다. 여기 적힌 표는 **참고용 추정치**이고, 모르는 모델이면 비용을 숨긴다 —
틀린 숫자를 자신 있게 보여 주는 것보다 안 보여 주는 편이 낫다.
"""

from __future__ import annotations

import hashlib
import logging
import threading

log = logging.getLogger("agent.usage")

# USD / 1M 토큰 (입력, 출력). 2026-08 기준 공개가 — 참고용이다.
# 모르는 모델은 여기 없다 → 비용을 계산하지 않고 토큰 수만 보인다.
PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5.4-mini": (1.50, 9.00),   # ★ 4o-mini 의 10~15배다 — 이름만 보고 싸다고 짐작하면 틀린다
    "gpt-5": (1.25, 10.00),      # reasoning 토큰은 completion 으로 청구된다
    # gpt-5.4 / gpt-5.4-nano / gpt-5.5 는 공식 단가를 확인하지 못했다. 표에 없으면
    # 접두 일치("gpt-5")로 떨어지므로 **비용 표시가 실제와 다를 수 있다** — 쓰기 전에 채울 것.
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}

# 한 번에 모델로 보낼 수 있는 입력 상한. 넘으면 **보내지 않는다**.
# gpt-4o-mini 는 128k 를 받지만, 그만큼 넣는 질문은 십중팔구 실수(로그 통째로 붙여넣기)다.
MAX_INPUT_TOKENS = 24_000


# ── tiktoken 로딩 — **요청 경로에서는 절대 기다리지 않는다** ──────────
# ★ tiktoken 은 첫 사용 때 인코딩 파일을 인터넷에서 받는다. 사내망·폐쇄망·채점 샌드박스처럼
#   막힌 곳에서는 예외가 아니라 **그냥 멈춘다**. 처음엔 "3초만 기다리고 포기"로 막으려 했는데
#   그것도 멈췄다 — 받는 쪽이 스레드를 잡고 안 놓으면 join 타임아웃은 호출자를 풀어 줄 뿐
#   프로세스는 여전히 붙들린다.
#
#   그래서 **기다리지 않는다.** 첫 호출은 곧바로 어림셈으로 답하고, 인코딩은 뒤에서 받는다.
#   준비되면 그때부터 정확해진다. 토큰 계량 때문에 대화가 1초라도 멈추는 건 잘못된 교환이다.
_enc = None
_enc_started = False
_enc_lock = threading.Lock()


def _warm(model: str):
    """뒤에서 인코딩을 준비한다. 실패하면 영원히 어림셈 — 그래도 앱은 돈다."""
    global _enc
    try:
        import tiktoken
        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            # 모르는 모델(사내 배포명·자체 LLM)이라도 세는 것은 세야 한다.
            enc = tiktoken.get_encoding("o200k_base")
        _enc = enc
        log.debug("tiktoken 준비됨 (%s)", model)
    except Exception as e:
        log.info("tiktoken 을 쓸 수 없습니다(%s) — 어림셈으로 셉니다.", str(e)[:120])


def _encoding(model: str):
    """준비돼 있으면 인코더, 아니면 None. **절대 블로킹하지 않는다.**"""
    global _enc_started
    if _enc is None:
        with _enc_lock:
            if not _enc_started:
                _enc_started = True
                threading.Thread(target=_warm, args=(model,), daemon=True).start()
    return _enc


def count(text: str, model: str = "gpt-4o-mini") -> int:
    """토큰 수. 인코더가 아직/영영 없으면 **어림셈**으로 답한다(멈추지 않는다).

    어림셈은 정확하지 않지만 '너무 긴 입력을 막는다'는 목적엔 충분하다 — 막아야 할 입력은
    상한을 아슬아슬하게 넘는 것이 아니라 수십 배로 넘는 것들이다. 사용량 표시는 어차피
    모델이 알려 준 실제값(`Meter`)을 쓰므로, 여기 오차가 화면 숫자를 흐리지도 않는다.
    """
    text = text or ""
    enc = _encoding(model)
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # 한글은 대략 1자≈1.5토큰, 영문은 4자≈1토큰. 섞여 있으니 2자≈1토큰으로 잡는다.
    return max(1, len(text) // 2)


def too_long(text: str, model: str = "gpt-4o-mini", limit: int = MAX_INPUT_TOKENS) -> tuple[bool, int]:
    """(넘었나?, 토큰수). 넘었으면 보내지 말고 사용자에게 줄여 달라고 한다."""
    n = count(text, model)
    return n > limit, n


def cost(model: str, prompt: int, completion: int) -> float | None:
    """USD 추정. **모르는 모델이면 None** — 틀린 숫자를 보이느니 안 보이는 게 낫다."""
    key = (model or "").lower()
    price = PRICES.get(key) or next((v for k, v in PRICES.items() if k in key), None)
    if not price:
        return None
    return round(prompt / 1e6 * price[0] + completion / 1e6 * price[1], 6)


class Meter:
    """한 대화(`thread_id`)의 사용량 누적기.

    LangChain 콜백으로 붙어 **모델이 알려 준 실제 사용량**을 모은다. 에이전트 한 턴이 LLM 을
    여러 번 부르므로, 역할별로 나눠 담아야 "어디가 비싼가"를 볼 수 있다 — 대개 도구 결과를
    통째로 다시 싣는 ResearchAnalyst 이 제일 비싸다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = 0
        self.prompt = 0
        self.completion = 0
        self.model = ""
        # 역할(그래프 노드)별 집계 — "어디가 느리고 비싼가"의 근거(성능 최적화 기준선).
        self.by_node: dict = {}
        # 도구별 집계 — 벽시계와 LLM 합산의 갭이 여기 있다(수정 25s 중 LLM 14s, 나머지가 도구).
        self.by_tool: dict = {}
        # Safe per-call diagnostics: labels and counters only, never prompt/response text or
        # reasoning. This is needed to distinguish semantic, projection, and repair latency.
        self.calls_detail: list[dict] = []
        # Versioned custom events are stored separately from actual LLM calls. They contain
        # registered scalar identifiers only; the raw callback run id is never persisted.
        self.fast_path_events: list[dict] = []
        self.fast_path_invalid_events = 0
        # 프롬프트 캐시 히트 — OpenAI 는 1024+ 토큰 공통 prefix 를 자동 캐시한다.
        # 이 값이 낮으면 시스템 프롬프트 앞부분이 매 호출 달라진다는 뜻이다.
        self.cached = 0

    def add(self, model: str, prompt: int, completion: int,
            node: str = "", seconds: float = 0.0, cached: int = 0,
            output_contract: str = "", finish_reason: str = "",
            execution_layer: str = "", execution_stage: str = "",
            validation_diagnostic: dict | None = None,
            fast_path_scope_id: str = ""):
        with self._lock:
            self.calls += 1
            self.prompt += int(prompt or 0)
            self.completion += int(completion or 0)
            self.cached += int(cached or 0)
            if model:
                self.model = model
            if node:
                row = self.by_node.setdefault(node, {"calls": 0, "tokens": 0, "seconds": 0.0})
                row["calls"] += 1
                row["tokens"] += int(prompt or 0) + int(completion or 0)
                row["seconds"] = round(row["seconds"] + (seconds or 0.0), 1)
            detail = {
                "node": node,
                "model": model,
                "outputContract": output_contract,
                "executionLayer": execution_layer,
                "executionStage": execution_stage,
                "finishReason": finish_reason,
                "promptTokens": int(prompt or 0),
                "completionTokens": int(completion or 0),
                "seconds": round(float(seconds or 0.0), 3),
            }
            labels = {
                "category": "validationCategory",
                "keyword": "validationKeyword",
                "path": "validationPath",
                "missing": "validationMissing",
            }
            for source, target in labels.items():
                value = str((validation_diagnostic or {}).get(source) or "").strip()
                if value:
                    detail[target] = value
            if fast_path_scope_id:
                detail["fastPathScopeId"] = fast_path_scope_id
            self.calls_detail.append(detail)

    def add_fast_path_event(self, event: dict, scope_id: str):
        with self._lock:
            self.fast_path_events.append({**event, "scopeId": scope_id})

    def reject_fast_path_event(self):
        with self._lock:
            self.fast_path_invalid_events += 1

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def snapshot(self) -> dict:
        c = cost(self.model, self.prompt, self.completion)
        out = {"calls": self.calls, "promptTokens": self.prompt,
               "completionTokens": self.completion, "totalTokens": self.total,
               "model": self.model}
        if c is not None:
            out["costUsd"] = c
        if self.cached:
            out["cachedTokens"] = self.cached
        if self.by_node:
            out["byNode"] = dict(self.by_node)
        if self.by_tool:
            out["byTool"] = dict(self.by_tool)
        if self.calls_detail or self.fast_path_events:
            out["callsDetail"] = [dict(row) for row in self.calls_detail]
        if self.fast_path_events:
            out["fastPathEvents"] = [dict(row) for row in self.fast_path_events]
        if self.fast_path_invalid_events:
            out["fastPathInvalidEvents"] = self.fast_path_invalid_events
        return out

    def add_tool(self, name: str, seconds: float):
        with self._lock:
            row = self.by_tool.setdefault(name or "?", {"calls": 0, "seconds": 0.0})
            row["calls"] += 1
            row["seconds"] = round(row["seconds"] + (seconds or 0.0), 2)


def callback(meter: Meter):
    """`Meter` 에 실제 사용량을 흘려 넣는 LangChain 콜백.

    설치가 안 돼 있거나 응답 모양이 다르면 **조용히 아무것도 안 한다** — 계량 때문에 대화가
    죽으면 안 된다. 계량은 있으면 좋은 것이지 없으면 안 되는 것이 아니다.
    """
    try:
        from langchain_core.callbacks import BaseCallbackHandler
    except Exception:
        return None

    class _Handler(BaseCallbackHandler):
        def __init__(self):
            self._t0 = {}
            self._state_lock = threading.RLock()
            self._active_chains: set[str] = set()
            self._parents: dict[str, str] = {}
            self._fast_path_scopes: dict[str, str] = {}
            self._chain_nodes: dict[str, str] = {}

        @staticmethod
        def _safe_scope_id(run_id) -> str:
            return hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:24]

        def _scope_for(self, run_id) -> str:
            current = str(run_id or "")
            visited = set()
            while current and current not in visited:
                visited.add(current)
                scope = self._fast_path_scopes.get(current)
                if scope:
                    return scope
                current = self._parents.get(current, "")
            return ""

        @staticmethod
        def _node_from_metadata(metadata) -> str:
            md = metadata if isinstance(metadata, dict) else {}
            ns = str(md.get("langgraph_checkpoint_ns") or "")
            return (
                str(md.get("ltm_role_id") or "")
                or (ns.split(":", 1)[0] if ns else str(md.get("langgraph_node") or ""))
            )

        def on_chain_start(
            self, serialized, inputs, *, run_id=None, parent_run_id=None, **kwargs,
        ):
            current = str(run_id or "")
            if not current:
                return
            with self._state_lock:
                self._active_chains.add(current)
                parent = str(parent_run_id or "")
                if parent_run_id is not None:
                    self._parents[current] = parent
                node = self._node_from_metadata(kwargs.get("metadata"))
                parent_node = self._chain_nodes.get(parent, "")
                if not node:
                    node = parent_node
                self._chain_nodes[current] = node
                parent_scope = self._fast_path_scopes.get(parent, "")
                self._fast_path_scopes[current] = (
                    parent_scope if parent_scope and node == parent_node
                    else self._safe_scope_id(current)
                )

        def _end_chain(self, run_id):
            current = str(run_id or "")
            with self._state_lock:
                self._active_chains.discard(current)
                self._parents.pop(current, None)
                self._fast_path_scopes.pop(current, None)
                self._chain_nodes.pop(current, None)

        def on_chain_end(self, outputs, *, run_id=None, **kwargs):
            self._end_chain(run_id)

        def on_chain_error(self, error, *, run_id=None, **kwargs):
            self._end_chain(run_id)

        def on_custom_event(
            self, name, data, *, run_id=None, tags=None, metadata=None, **kwargs,
        ):
            try:
                from app.agent.workflow.typed_fast_path import (
                    TYPED_FAST_PATH_EVENT_NAME,
                    parse_typed_fast_path_event,
                    typed_fast_path_registry,
                )
                if name != TYPED_FAST_PATH_EVENT_NAME:
                    return
                current = str(run_id or "")
                with self._state_lock:
                    if not current or current not in self._active_chains:
                        meter.reject_fast_path_event()
                        return
                    event = parse_typed_fast_path_event(data)
                    if event is None:
                        meter.reject_fast_path_event()
                        return
                    spec = typed_fast_path_registry().get(event.path_id) or {}
                    if self._chain_nodes.get(current) != spec.get("ownerNode"):
                        meter.reject_fast_path_event()
                        return
                    scope = self._fast_path_scopes.get(current, "")
                    if not scope:
                        meter.reject_fast_path_event()
                        return
                    meter.add_fast_path_event(event.as_dict(), scope)
            except Exception:
                meter.reject_fast_path_event()

        def _start(self, run_id, kwargs):
            import time as _t
            md = kwargs.get("metadata") or {}
            # 서브그래프 안에서는 노드명이 think/act 다 — 체크포인트 네임스페이스의 부모
            # (research_analyst:.. → research_analyst)로 바꿔야 역할별 집계가 된다.
            ns = str(md.get("langgraph_checkpoint_ns") or "")
            # Explicit Role metadata wins over subgraph plumbing such as think/act.
            node = (str(md.get("ltm_role_id") or "")
                    or (ns.split(":", 1)[0] if ns else str(md.get("langgraph_node") or "")))
            contract = str(md.get("ltm_output_contract") or "")
            layer = str(md.get("ltm_execution_layer") or "")
            stage = str(md.get("ltm_execution_stage") or "")
            validation = {
                key: str(md.get(f"ltm_validation_{key}") or "")
                for key in ("category", "keyword", "path", "missing")
                if md.get(f"ltm_validation_{key}")
            }
            with self._state_lock:
                scope = self._scope_for(kwargs.get("parent_run_id"))
                self._t0[str(run_id)] = (
                    _t.time(), node, contract, layer, stage, validation, scope,
                )

        def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):
            self._start(run_id, kwargs)

        def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
            self._start(run_id, kwargs)

        def on_llm_end(self, response, *, run_id=None, **kwargs):
            try:
                out = response.llm_output or {}
                usage = out.get("token_usage") or out.get("usage") or {}
                if not usage:       # 스트리밍 등에서는 generation 쪽에 붙는다
                    gen = (response.generations or [[]])[0]
                    meta = getattr(gen[0].message, "usage_metadata", None) if gen else None
                    if meta:
                        usage = {"prompt_tokens": meta.get("input_tokens"),
                                 "completion_tokens": meta.get("output_tokens")}
                gen = (response.generations or [[]])[0]
                message = getattr(gen[0], "message", None) if gen else None
                response_meta = getattr(message, "response_metadata", None) or {}
                import time as _t
                with self._state_lock:
                    started = self._t0.pop(
                        str(run_id), (None, "", "", "", "", {}, ""),
                    )
                t0, node, contract, layer, stage, validation, scope = started
                secs = (_t.time() - t0) if t0 else 0.0
                det = usage.get("prompt_tokens_details") or {}
                cached = det.get("cached_tokens") if isinstance(det, dict) else 0
                if not cached:      # usage_metadata 경로(스트리밍)
                    meta = getattr(message, "usage_metadata", None) if message else None
                    cached = ((meta or {}).get("input_token_details") or {}).get("cache_read", 0)
                model = (out.get("model_name") or response_meta.get("model_name")
                         or response_meta.get("model") or "")
                finish = (out.get("finish_reason") or response_meta.get("finish_reason")
                          or response_meta.get("stop_reason") or "")
                meter.add(model,
                          usage.get("prompt_tokens") or 0,
                          usage.get("completion_tokens") or 0,
                          node=node, seconds=secs, cached=cached or 0,
                          output_contract=contract, finish_reason=str(finish),
                          execution_layer=layer, execution_stage=stage,
                          validation_diagnostic=validation,
                          fast_path_scope_id=scope)
            except Exception:
                pass

        def on_llm_error(self, error, *, run_id=None, **kwargs):
            try:
                import time as _t
                with self._state_lock:
                    started = self._t0.pop(
                        str(run_id), (None, "", "", "", "", {}, ""),
                    )
                t0, node, contract, layer, stage, validation, scope = started
                meter.add("", 0, 0, node=node,
                          seconds=(_t.time() - t0) if t0 else 0.0,
                          output_contract=contract, finish_reason="error",
                          execution_layer=layer, execution_stage=stage,
                          validation_diagnostic=validation,
                          fast_path_scope_id=scope)
            except Exception:
                pass

        # 도구 시간 — 벽시계와 LLM 합산의 갭이 어디서 나는지 보인다.
        def on_tool_start(self, serialized, input_str, *, run_id=None, **kwargs):
            import time as _t
            name = (serialized or {}).get("name") or ""
            self._t0[f"tool:{run_id}"] = (_t.time(), name)

        def on_tool_end(self, output, *, run_id=None, **kwargs):
            try:
                import time as _t
                t0, name = self._t0.pop(f"tool:{run_id}", (None, ""))
                if t0:
                    meter.add_tool(name, _t.time() - t0)
            except Exception:
                pass

        def on_tool_error(self, error, *, run_id=None, **kwargs):
            self.on_tool_end(None, run_id=run_id, **kwargs)

    return _Handler()
