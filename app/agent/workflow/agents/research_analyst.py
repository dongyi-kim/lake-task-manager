"""Research Analyst — "이 일이 처음인가"를 밝힌다. 이 에이전트가 이 서비스의 값어치다.

실무에서 "새 업무"의 상당수는 **이미 누군가 시작했거나, 논의만 하고 멈췄거나, 비슷한 걸 다른
이름으로 하고 있다.** 그걸 모른 채 티켓을 새로 만들면 중복이 생기고, 앞사람이 부딪힌 벽에
다시 부딪힌다. 그래서 티켓을 만들기 전에 **반드시** 여기를 지난다.

ToolAgent 인 이유 — 몇 번 검색해야 충분한지는 미리 알 수 없다. 한 번에 나오면 한 번이고,
약어 때문에 안 나오면 말을 바꿔 다시 찾아야 하고, 실마리를 잡으면 링크를 타고 더 들어가야 한다.
그 판단을 코드에 박을 수 없으니 모델에게 맡긴다(ReAct).

**근거 없는 서술을 금지한다.** "예전에 검토된 적 있는 것 같습니다"는 최악이다 — 확인할 수도,
반박할 수도 없다. 모든 문장에 티켓 키를 달게 하고, 없으면 없다고 말하게 한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re as _re

from app.agent.workflow.agents.base import ToolAgent
from app.agent.prompts.roles import SYSTEM_RESEARCH_ANALYST
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import (AgentState, Intent, Node, last_user_text, note,
                                      request_text)
from app.agent.workflow.write_evidence_ledger import (
    build_completed_write_ledger,
    ledger_text as _ledger_text,
)
from app.agent.workflow.typed_fast_path import (
    evaluate_typed_fast_path,
    typed_fast_path_note,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "situation": {
            "type": "string",
            "maxLength": 1600,
            "description": ("Three to six Korean sentences describing the verified current situation. "
                            "Distinguish ongoing, stopped, and decided work. Cite a ticket key or document "
                            "title for every claim. If no result exists, state that directly in Korean."),
        },
        "evidence": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "object", "properties": {
                "key": {"type": "string", "maxLength": 240,
                        "description": "Exact ticket key or document title."},
                "title": {"type": "string", "maxLength": 300},
                "why": {"type": "string", "maxLength": 360,
                        "description": "One Korean sentence explaining direct relevance."},
                "url": {"type": "string", "maxLength": 1000,
                        "description": "Verified document or web URL, otherwise empty."},
                "confidence": {
                    "type": "string",
                    "description": ("Confidence based on authority, directness, recency, and corroboration; "
                                    "use exactly high, medium, low, or unknown; never a generic optimism score."),
                },
                "fitness": {
                    "type": "string",
                    "description": ("How directly this source covers the user's decision claim; use exactly "
                                    "direct, supporting, context-only, or unknown."),
                },
                "limitations": {
                    "type": "string",
                    "maxLength": 360,
                    "description": "One concise unresolved limitation or empty string.",
                },
                "observations": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "object", "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["description", "comment", "field", "document",
                                     "external", "query"],
                        },
                        "text": {
                            "type": "string",
                            "maxLength": 420,
                            "description": "Concise Korean fact observed at that location.",
                        },
                        "observed_at": {
                            "type": "string",
                            "maxLength": 80,
                            "description": ("Exact source date or timestamp when present, otherwise empty. "
                                            "Use it to distinguish historical state from current state."),
                        },
                    }, "required": ["source", "text"]},
                    "description": ("Distinct facts actually used from this same source. Keep body, comment, "
                                    "field history, and document observations under one source item."),
                }}},
            "description": "At most eight sources actually inspected and used for situation.",
        },
        "related_docs": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "object", "properties": {
                "title": {"type": "string", "maxLength": 300},
                "url": {"type": "string", "maxLength": 1000}}},
            "description": "Relevant Confluence documents actually returned by research.",
        },
        "epic_candidate": {
            "type": "string",
            "maxLength": 40,
            "description": "Verified suitable parent Epic key, or empty. Never force an unrelated Epic.",
        },
        "already_exists": {
            "type": "boolean",
            "description": "Whether an existing ticket already performs materially the same work.",
        },
    },
    "required": ["situation", "evidence"],
}


def _prefetched_external_context(query_results: list) -> str:
    """Convert deterministic web/GitHub Query Runner artifacts into reusable research evidence."""
    rows = []
    for item in query_results or []:
        if not isinstance(item, dict) or item.get("source") not in ("web", "github"):
            continue
        result = item.get("result") or {}
        query = str(result.get("query") or "").strip()
        prefix = f"[{item.get('source')} 검색] {query}".strip()
        found = result.get("results") or []
        if found:
            rows.append(prefix)
            for source in found[:8]:
                official = " · 공식" if source.get("official") else ""
                title = source.get("title") or source.get("name") or "source"
                detail = source.get("snippet") or source.get("description") or ""
                rows.append(f"- {title}{official} — {detail} ({source.get('url') or ''})")
        elif result.get("error"):
            rows.append(prefix + " — 검색 실패: " + str(result.get("error"))[:240])
    return "\n".join(rows)


def _query_results_have_material(query_results) -> bool:
    """Whether deterministic QueryPlan execution returned evidence worth an LLM synthesis call."""
    if not isinstance(query_results, list):
        return False
    for item in query_results:
        result = (item or {}).get("result") or {} if isinstance(item, dict) else {}
        if any(result.get(field) for field in
               ("tickets", "documents", "comments", "people", "results")):
            return True
    return False


def _planned_query_result_binding(state) -> tuple[list[dict], list[dict], bool]:
    """Bind every result to one unique planned ``(id, source)`` identity."""
    raw_specs = (state.get("query_plan") or {}).get("queries") or []
    raw_results = state.get("query_results") or []
    if (not isinstance(raw_specs, list) or not isinstance(raw_results, list)
            or not all(isinstance(row, dict) for row in raw_specs + raw_results)):
        return [], [], False
    specs = list(raw_specs)
    results = list(raw_results)

    def identity(row: dict) -> tuple[str, str]:
        return (str(row.get("id") or "").strip(),
                str(row.get("source") or "").strip().casefold())

    planned = [identity(row) for row in specs]
    materialized = [identity(row) for row in results]
    planned_ids = [qid for qid, _source in planned]
    result_ids = [qid for qid, _source in materialized]
    exact = (
        bool(planned)
        and all(qid and source for qid, source in planned + materialized)
        and len(planned) == len(materialized)
        and len(set(planned_ids)) == len(planned_ids)
        and len(set(result_ids)) == len(result_ids)
        and set(planned) == set(materialized)
    )
    if not exact:
        return specs, [], False
    by_identity = {identity(row): row for row in results}
    return specs, [by_identity[key] for key in planned], True


def _query_plan_is_complete(state) -> bool:
    """Whether every planned source produced a usable deterministic result.

    Empty results are complete evidence of an in-scope miss. Missing rows, source mismatches, query errors,
    and failed body materialization keep the ReAct fallback available instead of trading quality for speed.
    """
    specs, results, exact = _planned_query_result_binding(state)
    if not exact:
        return False
    for row in results:
        result = row.get("result")
        if (not isinstance(result, dict) or result.get("error")
                or result.get("incomplete") or result.get("complete") is False
                or result.get("materializationErrors")):
            return False
    return True


def _query_row_identity(row: dict) -> str:
    """Return an exact provider-owned identity; display titles are not identities."""
    url = str(row.get("url") or "").strip()
    if _re.fullmatch(r"https?://[^\s]+", url, _re.I):
        return f"url:{url}"
    value = str(row.get("id") or "").strip()
    return f"id:{value}" if value else ""


def _query_ledger_result_shape(source: str, result: dict) -> tuple[bool, int]:
    """Validate the lossless result shapes supported by the no-Research-call lane."""
    if not isinstance(result, dict) or result.get("materializationErrors"):
        return False, 0
    if source == "confluence":
        candidates = result.get("documents", [])
        bodies = result.get("documentBodies", [])
        if not isinstance(candidates, list) or not isinstance(bodies, list):
            return False, 0
        if any(not isinstance(row, dict) or row.get("error") for row in candidates + bodies):
            return False, 0
        body_ids = {_query_row_identity(row) for row in bodies
                    if _query_row_identity(row) and str(row.get("text") or row.get("body") or "").strip()}
        candidate_ids = {_query_row_identity(row) for row in candidates if _query_row_identity(row)}
        valid = (
            len(body_ids) == len(bodies)
            and len(candidate_ids) == len(candidates)
            and candidate_ids == body_ids
        )
        return valid, len(body_ids)
    if source in {"web", "github"}:
        rows = result.get("results")
        if not isinstance(rows, list):
            return False, 0
        valid = all(
            isinstance(row, dict)
            and not row.get("error")
            and bool(_query_row_identity(row))
            and bool(str(row.get("snippet") or row.get("description") or "").strip())
            for row in rows
        )
        return valid, len(rows)
    # Jira lightweight search rows omit current scalar/detail authority in the Research
    # evidence contract. Keep them on the semantic path until a lossless typed snapshot exists.
    return False, 0


def _single_query_fast_path_decision(state):
    """Allow a zero-Research-call lane only for one typed retrieval outcome.

    QueryRunner has already acquired and bounded the rows.  The final Result role still owns
    explanation; this path only replaces Research's second semantic pass with the same typed
    evidence ledger used by completed writes.  Unsupported result shapes fail closed so list
    options, people resolution, comparisons, and other semantic tools retain the normal path.
    """
    tasks = [row for row in (state.get("request_plan") or {}).get("tasks") or []
             if isinstance(row, dict)]
    specs, results, exact_binding = _planned_query_result_binding(state)
    supported = exact_binding and len(specs) == 1
    material_rows = 0
    for spec, row in zip(specs, results):
        source = str(spec.get("source") or "").strip().casefold()
        result = row.get("result")
        valid, count = _query_ledger_result_shape(source, result)
        material_rows += count
        if not valid:
            supported = False
            break
    return evaluate_typed_fast_path(
        "research.single_bounded_query",
        checks={
            "ask_intent": str(state.get("intent") or "") == Intent.ASK,
            "one_query_outcome": (
                len(tasks) == 1
                and str(tasks[0].get("kind") or "").strip().casefold() == "query"
                and tasks[0].get("write_intent") is not True
            ),
            "query_plan_complete": _query_plan_is_complete(state),
            "exact_result_binding": exact_binding and len(specs) == 1,
            "ledger_result_shape": supported,
            "bounded_without_omission": material_rows <= 8,
        },
    )


_RESEARCH_PROVENANCE_FIELD = "_research_provenance_v1"
_RESEARCH_PROVENANCE_AUTHORITY = "research_analyst"
_EXTERNAL_QUERY_SOURCES = frozenset({"web", "github"})
_RESEARCH_PROVENANCE_KEY = os.urandom(32)


def _sign_research_provenance(stamp: dict) -> str:
    """Create a process-local capability signature that model output cannot mint.

    Research continuity lives inside one local application process. A restart deliberately
    invalidates the capability and forces reacquisition, which is safer than trusting an old
    public URL after its QueryRunner artifact is gone.
    """
    payload = {key: stamp.get(key) for key in (
        "authority", "kind", "url", "source", "query_id", "official",
    )}
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(_RESEARCH_PROVENANCE_KEY, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _absolute_http_url(value) -> str:
    """Return an exact absolute HTTP(S) URL or an empty string.

    URL provenance uses the literal QueryRunner value.  In particular, it does not collapse
    paths, query strings, or trailing slashes: a nearby URL is not the result that was opened.
    """
    from urllib.parse import urlsplit

    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
    except Exception:
        return ""
    return url if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc else ""


def _official_external_requested(state) -> bool:
    """Whether the human requested an official public source, including GitHub wording."""
    value = " ".join(part for part in (
        request_text(state).strip(), last_user_text(state).strip(),
    ) if part)
    external = bool(_re.search(
        r"(?:외부|\bexternal\b|\bpublic\b|\bweb\b|웹|\bgithub\b|깃허브)",
        value, _re.I,
    ))
    return external and bool(_re.search(r"(?:공식|\bofficial\b)", value, _re.I))


def _executed_url_provenance(state) -> dict[str, dict]:
    """Index exact URLs returned by successful QueryRunner result rows.

    This is the acquisition authority.  A URL merely written by a model, copied from a
    prompt, or embedded in search prose never enters this map.  Incomplete/error results are
    excluded because their partial rows cannot satisfy a complete source contract.
    """
    indexed: dict[str, dict] = {}

    def visit(value, *, source: str, query_id: str) -> None:
        if isinstance(value, dict):
            url = _absolute_http_url(value.get("url"))
            if url:
                official = (value.get("official") is True
                            or str(value.get("official") or "").strip().casefold() == "true")
                candidate = {
                    "authority": _RESEARCH_PROVENANCE_AUTHORITY,
                    "kind": "executed_query_result_url",
                    "url": url,
                    "source": source,
                    "query_id": query_id,
                    "official": official,
                }
                candidate["signature"] = _sign_research_provenance(candidate)
                prior = indexed.get(url)
                # Prefer an explicit official hit when two executed queries returned the same URL.
                if prior is None or (official and not prior.get("official")):
                    indexed[url] = candidate
            for child in value.values():
                if isinstance(child, (dict, list, tuple)):
                    visit(child, source=source, query_id=query_id)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, source=source, query_id=query_id)

    for row in state.get("query_results") or []:
        if not isinstance(row, dict):
            continue
        result = row.get("result")
        if (not isinstance(result, dict) or result.get("error")
                or result.get("incomplete") is True or result.get("complete") is False):
            continue
        visit(
            result,
            source=str(row.get("source") or "").strip().casefold(),
            query_id=str(row.get("id") or "").strip(),
        )
    return indexed


def _validated_research_stamp(item: dict) -> dict | None:
    """Validate a durable stamp already stored in server-owned Research state."""
    if not isinstance(item, dict):
        return None
    url = _absolute_http_url(item.get("url"))
    stamp = item.get(_RESEARCH_PROVENANCE_FIELD)
    if not url or not isinstance(stamp, dict):
        return None
    if (stamp.get("authority") != _RESEARCH_PROVENANCE_AUTHORITY
            or stamp.get("kind") != "executed_query_result_url"
            or str(stamp.get("url") or "").strip() != url):
        return None
    source = str(stamp.get("source") or "").strip().casefold()
    if not source:
        return None
    normalized = {
        "authority": _RESEARCH_PROVENANCE_AUTHORITY,
        "kind": "executed_query_result_url",
        "url": url,
        "source": source,
        "query_id": str(stamp.get("query_id") or "").strip(),
        "official": stamp.get("official") is True,
    }
    supplied = str(stamp.get("signature") or "").strip()
    expected = _sign_research_provenance(normalized)
    if not supplied or not hmac.compare_digest(supplied, expected):
        return None
    normalized["signature"] = supplied
    return normalized


def _durable_url_provenance(state) -> dict[str, dict]:
    """Read only prior server-projected URL stamps for continuation turns."""
    indexed: dict[str, dict] = {}
    for field in ("evidence", "related_docs"):
        for item in state.get(field) or []:
            stamp = _validated_research_stamp(item)
            if stamp:
                indexed[stamp["url"]] = stamp
    return indexed


def _durable_external_provenance_allowed(state) -> bool:
    """Allow a prior public-source capability only on a non-reacquiring continuation.

    A current web/GitHub plan or result is authoritative even when it failed or returned
    zero rows; falling back to yesterday's stamped URL would contradict that explicit
    acquisition result. Fresh/legacy checkpoints also cannot opt into durability merely by
    carrying an old evidence object.
    """
    if state.get("turn_continuation") is not True:
        return False
    planned = (state.get("query_plan") or {}).get("queries") or []
    current = state.get("query_results") or []
    return not any(
        isinstance(row, dict)
        and str(row.get("source") or "").strip().casefold() in _EXTERNAL_QUERY_SOURCES
        for row in [*planned, *current]
    )


def _bind_research_url_provenance(state, rows) -> list[dict]:
    """Drop unacquired URL rows and attach a durable server-owned provenance stamp.

    Current exact QueryRunner hits win.  A continuation may reuse only a URL carrying a
    stamp from the prior Research state.  For an official public-source request, a web or
    GitHub hit without ``official=true`` is deliberately unusable.
    """
    current = _executed_url_provenance(state)
    durable = _durable_url_provenance(state)
    durable_allowed = _durable_external_provenance_allowed(state)
    official_required = _official_external_requested(state)
    bound: list[dict] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.pop(_RESEARCH_PROVENANCE_FIELD, None)  # model-authored stamps have no authority
        url = _absolute_http_url(item.get("url"))
        if not url:
            bound.append(item)
            continue
        stamp = current.get(url) or (durable.get(url) if durable_allowed else None)
        if not stamp:
            continue
        if (official_required and stamp.get("source") in _EXTERNAL_QUERY_SOURCES
                and stamp.get("official") is not True):
            continue
        # The durable sidecar is needed for public web/GitHub evidence. Internal Jira and
        # Confluence rows already have their own typed/materialized provenance contracts and
        # should keep their stable public state shape.
        if stamp.get("source") in _EXTERNAL_QUERY_SOURCES:
            item[_RESEARCH_PROVENANCE_FIELD] = dict(stamp)
        bound.append(item)
    return bound


_TYPED_WRITE_ACTIONS = frozenset({"create", "comment", "update"})


def _typed_write_action(state) -> str:
    """Return only a validated continuation action that agrees with current intent.

    The contract is deterministic workflow authority, while messages and model-authored uncertainty are
    not.  Re-validating at this optimization boundary keeps a malformed/stale checkpoint from turning a
    partial acquisition into a zero-call write path.
    """
    value = state.get("continuation_contract")
    if not isinstance(value, dict) or not value:
        return ""
    try:
        from app.agent.workflow.contracts import ContinuationContract
        contract = ContinuationContract.model_validate(value)
    except Exception:
        return ""
    intent = str(state.get("intent") or "").strip()
    if intent and contract.intent != intent:
        return ""
    return contract.action if contract.action in _TYPED_WRITE_ACTIONS else ""


def _materialized_ticket_keys(state) -> set[str]:
    """Collect successfully opened Jira identities from bounded QueryRunner ledgers."""
    keys: set[str] = set()
    groups = []
    for row in state.get("query_results") or []:
        if not isinstance(row, dict) or not isinstance(row.get("result"), dict):
            continue
        groups.append(row["result"].get("ticketDetails") or [])
    sidecar = state.get("materialized_ticket_sources") or {}
    if isinstance(sidecar, dict):
        groups.append(sidecar.get("ticketDetails") or [])
    for group in groups:
        for item in group:
            if not isinstance(item, dict) or item.get("error"):
                continue
            key = str(item.get("key") or "").strip().upper()
            if _re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", key):
                keys.add(key)
    return keys


def _completed_typed_write_action(state) -> str:
    """Return a zero-ReAct action only after complete acquisition and target materialization."""
    action = _typed_write_action(state)
    if not action or not _query_plan_is_complete(state):
        return ""
    contract = state.get("continuation_contract") or {}
    targets = {str(key or "").strip().upper()
               for key in (contract.get("target_keys") or []) if str(key or "").strip()}
    if action in {"comment", "update"} and not targets:
        return ""
    # A named create target is commonly an existing parent or source ticket.  It must be opened just
    # like a comment/update target; a search hit alone cannot authorize the zero-call path.
    if targets and not targets.issubset(_materialized_ticket_keys(state)):
        return ""
    return action


def _prefetched_creation_passthrough() -> dict:
    """Preserve acquired creation evidence when optional LLM synthesis cannot format JSON.

    This compatibility path is used only when no completed QueryPlan contract is available.
    Re-running the same searches cannot repair a formatting failure; it only multiplies latency
    and tokens. Keep the fallback deliberately non-interpretive so it cannot turn an unreviewed
    candidate into a duplicate claim.
    """
    return {
        "situation": (
            "범위 내 사전 조회를 완료했다. 중복 여부와 기술 배경은 아래에 전달된 "
            "원본 조회 자료를 기준으로 초안 단계에서 판단한다."
        ),
        "evidence": [],
        "related_docs": [],
        "epic_candidate": "",
        "already_exists": False,
    }


_SEMANTIC_RESEARCH_KINDS = frozenset({"query", "research", "analyze", "respond"})
_WRITE_OUTCOME_KINDS = frozenset({"plan", "ticket", "comment", "write"})


def _compound_plan_requires_research_synthesis(state) -> bool:
    """Whether a write contains research as a separate user-visible outcome.

    RequestPlan tasks describe requested deliverables, not the graph's internal retrieval stages.  A
    write-only plan can therefore keep the zero-LLM provenance adapter, while an explicit read/analyze
    outcome must be synthesized before the bounded Research state becomes the only evidence seen by the
    final response.  This decision deliberately uses the typed plan instead of guessing from prose verbs.
    """
    action = _typed_write_action(state)
    if not action and (state.get("intent") or "") != Intent.PLAN_WORK:
        return False
    tasks = [task for task in (state.get("request_plan") or {}).get("tasks") or []
             if isinstance(task, dict)]
    has_research = any(str(task.get("kind") or "").strip().lower()
                       in _SEMANTIC_RESEARCH_KINDS for task in tasks)
    has_write = bool(action) or any(
        bool(task.get("write_intent"))
        or str(task.get("kind") or "").strip().lower() in _WRITE_OUTCOME_KINDS
        for task in tasks
    )
    return has_research and has_write


def _research_outcome_tasks(state) -> list[dict]:
    """Project only explicit semantic outcomes into the prefetched synthesis prompt."""
    rows = []
    for task in (state.get("request_plan") or {}).get("tasks") or []:
        if not isinstance(task, dict) or str(task.get("kind") or "").strip().lower() \
                not in _SEMANTIC_RESEARCH_KINDS:
            continue
        rows.append({
            "id": str(task.get("id") or "")[:80],
            "kind": str(task.get("kind") or "")[:40],
            "instruction": str(task.get("instruction") or "")[:500],
            "completion_criteria": [str(value)[:240]
                                    for value in (task.get("completion_criteria") or [])[:4]],
        })
    return rows[:6]


_RESEARCH_LEDGER_STOP = frozenset({
    "research", "query", "analyze", "analysis", "summary", "summarize", "compare",
    "조사", "검색", "분석", "비교", "요약", "정리", "확인", "관련", "내부", "외부",
    "자료", "근거", "결과", "출처", "함께", "제시", "적용", "생성", "작성", "티켓",
    "태스크", "테스크", "task", "issue", "plan", "기준", "요청", "업무", "검증",
})


def _research_ledger_terms(state) -> list[str]:
    """Extract stable subject terms from typed research outcomes for failure-only ranking."""
    texts = []
    for task in _research_outcome_tasks(state):
        texts.append(task.get("instruction") or "")
        texts.extend(task.get("completion_criteria") or [])
    source = " ".join(texts)
    if not source:
        return []

    weighted = []
    try:
        from app.agent.tools._ident import find_identifiers
        weighted.extend(str(value).casefold() for value in find_identifiers(source))
    except Exception:
        pass
    weighted.extend(match.casefold() for match in _re.findall(
        r"[A-Za-z][A-Za-z0-9_.+-]{2,}|[가-힣]{2,}", source))

    terms = []
    for raw in weighted:
        term = raw.strip(".,;:!?()[]{}")
        if _re.fullmatch(r"[가-힣]+", term):
            for suffix in ("으로", "에서", "에게", "까지", "부터", "처럼", "하고", "하며",
                           "한다", "하는", "해야", "해줘", "과", "와", "을", "를", "은",
                           "는", "이", "가", "의", "에", "로"):
                if term.endswith(suffix) and len(term) - len(suffix) >= 2:
                    term = term[:-len(suffix)]
                    break
        if len(term) < 2 or term in _RESEARCH_LEDGER_STOP or term in terms:
            continue
        terms.append(term)
    return terms[:32]


def _completed_write_ledger(state, *, research_focus: bool = False,
                            action: str = "") -> dict:
    """Delegate completed QueryPlan projection after workflow-level decisions are fixed."""
    action = action if action in _TYPED_WRITE_ACTIONS else (_typed_write_action(state) or "create")
    contract = state.get("continuation_contract") or {}
    target_keys = {
        str(key or "").strip().upper() for key in (contract.get("target_keys") or [])
        if str(key or "").strip()
    }
    results = [row for row in (state.get("query_results") or []) if isinstance(row, dict)]
    sidecar = state.get("materialized_ticket_sources") or {}
    sidecar_details = [dict(row) for row in (sidecar.get("ticketDetails") or [])
                       if isinstance(row, dict) and not row.get("error")] \
        if isinstance(sidecar, dict) else []
    duplicate_key = _exact_creation_duplicate_key(state) if action == "create" else ""
    return build_completed_write_ledger(
        results,
        materialized_ticket_details=sidecar_details,
        target_keys=target_keys,
        duplicate_key=duplicate_key,
        action=action,
        research_focus=research_focus,
        research_terms=_research_ledger_terms(state) if research_focus else (),
    )


def _completed_query_ledger(state) -> dict:
    """Project a complete retrieval-only QueryPlan without semantic interpretation."""
    specs, results, exact_binding = _planned_query_result_binding(state)
    if exact_binding:
        exact_binding = all(
            _query_ledger_result_shape(
                str(spec.get("source") or "").strip().casefold(), row.get("result"),
            )[0]
            for spec, row in zip(specs, results)
        )
    results = results if exact_binding else []
    sidecar = state.get("materialized_ticket_sources") or {}
    sidecar_details = [dict(row) for row in (sidecar.get("ticketDetails") or [])
                       if isinstance(row, dict) and not row.get("error")] \
        if isinstance(sidecar, dict) else []
    return build_completed_write_ledger(
        results,
        materialized_ticket_details=sidecar_details,
        target_keys=set(),
        duplicate_key="",
        action="query",
        research_focus=True,
        research_terms=_research_ledger_terms(state),
    )


def _completed_creation_ledger(state, *, research_focus: bool = False) -> dict:
    """Compatibility name for callers and tests that specifically model ticket creation."""
    return _completed_write_ledger(
        state, research_focus=research_focus, action="create",
    )


def _research_outside(agent, asked: str) -> str:
    """기술 검토용 외부 조사 fallback — 검색어와 실행 모두 deterministic.

    Query Specialist normally plans this retrieval. This fallback covers routes that arrive without a web
    QuerySpec. Reusing the privacy-safe query builder removes one simple-model call per external investigation.
    """
    from app.agent.workflow.agents.query_specialist import _public_external_query
    query = _public_external_query(asked)
    if not query:
        return ""

    from app.agent import tools as T
    parts = []
    try:
        w = T.BY_NAME["search_web"].invoke({"query": query, "limit": 4})
        if w.get("results"):
            parts.append("웹 (" + query + "):\n" + "\n".join(
                f"- {r.get('title')} — {r.get('snippet')} ({r.get('url')})" for r in w["results"]))
        elif w.get("error"):
            parts.append("웹 검색 시도 실패: " + str(w.get("error"))[:240])
    except Exception:
        parts.append("웹 검색 시도 실패: 호출 오류")
    if _re.search(r"github|오픈소스|라이브러리|repository|repo\b", asked, _re.I):
        try:
            github_query = query.removesuffix(" official documentation")
            g = T.BY_NAME["search_github"].invoke({"query": github_query, "limit": 4})
            if g.get("results"):
                parts.append("GitHub (" + github_query + "):\n" + "\n".join(
                    f"- {r.get('name')} ★{r.get('stars')} (갱신 {r.get('updated')}) — {r.get('description')}"
                    for r in g["results"]))
            elif g.get("error"):
                parts.append("GitHub 검색 시도 실패: " + str(g.get("error"))[:240])
        except Exception:
            parts.append("GitHub 검색 시도 실패: 호출 오류")
    return "\n\n".join(parts)


def _presurvey(state) -> str:
    """주제형 질문의 사전 조사 — 키워드 검색은 항상, 의미 검색은 필요할 때만.

    ① `search_work_history`(완화 사다리 포함)를 코드가 돌린다 — 근황 질문에 쓰이도록
       **최근 갱신순**으로 정렬해 준다.
    ② 지식·근황·히스토리형 질문이거나 ①이 빈약하면(2건 미만) `deep_search`(의미 검색)까지 —
       "CDC"로 물었지만 "변경분 실시간 반영"이라 적힌 기록도 잡힌다.
    """
    kws = [str(k) for k in (state.get("keywords") or []) if str(k).strip()][:4]
    if not kws:
        return ""
    q = " ".join(kws)
    from concurrent.futures import ThreadPoolExecutor

    from app.agent.tools.rag_tools import deep_search
    from app.agent.tools.search_tools import search_work_history
    # 키워드 검색과 의미 검색(조건부로 쓰일 수 있음)을 **미리 병렬로** 던진다 — 직렬이면
    # 사전 조사만 수 초를 먹는다. deep 결과는 필요할 때만 꺼내 쓴다(투기 실행).
    ex = ThreadPoolExecutor(max_workers=2)
    fut_kw = ex.submit(lambda: search_work_history.invoke({"query": q, "limit": 10}) or {})
    fut_deep = ex.submit(lambda: deep_search.invoke({"topic": q, "limit": 8}) or {})
    r = fut_kw.result()
    jira = sorted(r.get("jira") or [], key=lambda x: str(x.get("updated") or ""), reverse=True)
    asked = last_user_text(state)
    # ★ 사용법 질문에는 **티켓 검색 결과를 싣지 않는다.** 답이 티켓에 없는데 재료에 있으면
    #   모델은 그걸 고른다 — 규칙 발췌를 "1차 출처"라 못 박아 나란히 줘도 졌다(실측 GUIDE7:
    #   "담당자 어떻게 바꿔?" 에 UI 회귀 픽스처 티켓 DL-9010 을 답으로 냈고, 재료에서
    #   dossier 를 걷어낸 뒤에도 같은 답이 나왔다).
    #   고르게 두지 말고 **줄 것만 준다** — 이 갈래에서 티켓은 답이 아니라 소음이다.
    howto = any(w in asked for w in _HOWTO_WORDS)
    parts = []
    if jira and not howto:
        parts.append("키워드 검색 (최근 갱신순):\n" + "\n".join(
            f"- {it.get('key')} \"{it.get('title', '')}\" ({it.get('status', '')}"
            f", 담당 {it.get('assignee') or '없음'}, 갱신 {str(it.get('updated') or '')[:10]})"
            for it in jira[:8]))
    if r.get("confluence") and not howto:
        parts.append("문서:\n" + "\n".join(
            f"- {d.get('title')} ({d.get('url')})" for d in r["confluence"][:4]))
    # ── 문서를 요약해 달라면 **본문을 읽어야** 한다 ─────────────────────────
    # 실측(T3): "적재주기 변경 절차 문서 요약해줘"에 제목과 한 줄 인상만 답하고
    # 정작 문서가 정한 규칙(job 명명·'현재값=최신 변경기록')은 하나도 못 옮겼다.
    # 검색 결과의 excerpt 는 180자라 요약의 재료가 못 된다 — 코드가 본문을 연다.
    if any(w in asked for w in ("문서", "가이드", "절차", "규정", "위키", "페이지")) \
            and any(w in asked for w in ("요약", "정리", "알려줘", "설명", "내용", "뭐라고")):
        try:
            from app.agent import tools as T
            docs = (r.get("confluence") or [])[:2]
            if not docs:
                # 키워드 검색이 문서를 못 집는 일이 잦다(실측 T3: 제목이 그대로 있는
                # 문서를 "확인되지 않았다"고 답했다) — 문서만 따로 한 번 더 찾는다.
                from app.agent.tools._ctx import client as _c, settings as _s
                from app.domain.search import search_all as _sa
                q = _re.sub(r"(문서|가이드|절차|규정|위키|페이지|요약|정리|해줘|알려줘|"
                            r"설명|내용|뭐라고)", " ", asked)
                q = _re.sub(r"\s+", " ", q).strip()
                if q:
                    rr = _sa(_c(), _s(), q, scope="all", limit=8, only=["confluence"]) or {}
                    docs = [{"title": x.get("title"), "url": x.get("url")}
                            for x in ((rr.get("confluence") or {}).get("items") or [])][:2]
            for d in docs:
                u = (d.get("url") or "").strip()
                if not u:
                    continue
                rd = T.BY_NAME["read_document"].invoke({"url_or_id": u}) or {}
                body = str(rd.get("text") or rd.get("body") or "")[:2500]
                if body:
                    parts.append(
                        f"문서 본문 「{d.get('title')}」 ({u}) — ★ 요약은 **이 본문**으로 한다. "
                        "문서가 정한 규칙·기준·명명 규약을 빠뜨리지 말고, 답변에 "
                        f"출처 링크를 함께 남겨라:\n{body}")
        except Exception:
            pass
    # LTM 사용법·사내 규칙 질문 — 정적 지식(search_rules)도 코드가 돌려 넣는다.
    # md 지시만으로는 모델이 티켓 검색 결과에 끌려 규칙 검색을 건너뛰었다(실측).
    if ("LTM" in asked.upper()) or any(w in asked for w in ("사용법", "어떻게 해", "어떻게 바꿔",
                                                            "어디 있", "가이드", "규칙",
                                                            "적재주기", "스키마", "테이블", "정책")):
        # ★ 사용법 질문은 **출처 문서를 이름으로 안다** — 의미 검색의 운에 맡기지 않는다.
        #   실측(GUIDE7): k 를 6까지 늘려도 05-ltm-guide 에서 한 절만 오고 나머지는 티켓
        #   작성 규칙이 유사도에서 이겼다. 그래서 "담당자 변경"은 답했는데 "강제 새로고침"은
        #   "확인되지 않았다"고 했다 — 가이드의 다른 절에 버젓이 있는데도.
        #   가이드는 3KB 다. 통째로 싣는 것이 검색보다 싸고 확실하다.
        if howto:
            try:
                from pathlib import Path as _P
                _g = _P(__file__).resolve().parents[4] / "knowledge" / "05-ltm-guide.md"
                if _g.exists():
                    parts.append("LTM 사용 가이드 (**이 질문의 답은 여기 있다. 티켓이 아니다.** "
                                 "여기 없는 화면·버튼을 지어내지 말고, 없으면 없다고 답하라):\n"
                                 + _g.read_text(encoding="utf-8"))
            except Exception:
                pass
        try:
            from app.agent.tools.rag_tools import search_rules
            hits = search_rules.invoke({"question": asked, "k": 4}) or []
            rows_g = [f"- ({h.get('출처')}) {str(h.get('rule') or '')[:400]}"
                      for h in hits if h.get("rule")]
            if rows_g:
                parts.append("사내 가이드·규칙 (이 질문의 1차 출처 — 여기 있는 대로 답하라):\n"
                             + "\n".join(rows_g))
        except Exception:
            pass
    # "누가 하면 좋을지"류 — 후보 재료(모듈 로스터+워크로드)를 코드가 주입한다.
    # md 규칙만으로는 모델이 '기록 없음'으로 종결했다(실측 2회, gpt-4o 포함).
    if any(w in asked for w in ("누가", "누구", "맡길", "맡으면", "추천")):
        try:
            from app.agent.tools.people_tools import get_team_workload
            module = state.get("module") or ""
            rows_p, src = [], ""
            if module:
                ppl = ((get_team_workload.invoke({"module": module}) or {}).get("people") or [])[:6]
                src = f"{module} 로스터·워크로드"
                rows_p = [f"- {p.get('id')} {p.get('name', '')} 진행중 {p.get('inProgress', 0)}"
                          f" · 열림 {p.get('open', 0)} · 최근 완료 {p.get('done28d', 0)}"
                          for p in ppl]
            if not rows_p:
                # 모듈을 못 짚는 주제("Iceberg 통계")에서는 가드가 통째로 꺼져 **이름 없는
                # 답**이 나갔다(실측 S3: "추천할 수 있는 정보가 부족합니다"로 종결).
                # ① 주제와 닿는 티켓의 담당 이력 ② 그래도 없으면 전 모듈에서 여유 있는 사람.
                seen: dict[str, list] = {}
                for t in (jira or [])[:12]:
                    a = str(t.get("assignee") or "").strip()
                    if a:
                        seen.setdefault(a, []).append(
                            f"{t.get('key')} \"{t.get('summary', '')}\"")
                if seen:
                    src = "주제와 닿는 티켓의 담당 이력"
                    rows_p = [f"- {a} — {', '.join(v[:3])}" for a, v in list(seen.items())[:5]]
                else:
                    src = "전 모듈 워크로드(주제 이력이 없어 부하 기준)"
                    pool = []
                    for mod in ("ETL", "Catalog", "Runtime", "Workbench", "DataOps"):
                        for p2 in ((get_team_workload.invoke({"module": mod}) or {})
                                   .get("people") or []):
                            pool.append((int(p2.get("inProgress") or 0), mod, p2))
                    pool.sort(key=lambda x: x[0])
                    rows_p = [f"- {p2.get('id')} {p2.get('name', '')} ({mod}) 진행중 {n}"
                              f" · 최근 완료 {p2.get('done28d', 0)}" for n, mod, p2 in pool[:5]]
            if rows_p:
                parts.append(
                    f"후보 재료 — {src} (★ 누가 할지는 이걸로 **2~3명의 이름과 근거**를 대라. "
                    "'추천할 정보가 부족하다'로 끝내는 것은 답이 아니다. 근거는 사람마다 "
                    "**소속 모듈 · 현재 부하(진행중 N건) · 유사 이력(있으면 티켓 키)** 을 "
                    "모두 밝힌다. 주제 이력이 아예 없으면 **그 사실을 먼저 한 줄로 말하고** "
                    "무슨 기준으로 골랐는지 밝혀라):\n"
                    + "\n".join(rows_p))
        except Exception:
            pass
    knowledge_ish = any(w in asked for w in (
        "히스토리", "근황", "최근", "현황", "정리", "알려줘", "설명", "무슨", "어떤", "왜", "지식"))
    # 사용법 질문은 의미 검색도 티켓을 물어 온다 — 이 갈래에서 티켓은 답이 아니라 소음이다.
    if (knowledge_ish or len(jira) < 2) and not howto:
        try:
            d = fut_deep.result(timeout=30) or {}
        except Exception:
            d = {}
        if d.get("similar"):
            parts.append("의미 검색 (키워드가 안 겹쳐도 같은 이야기):\n" + "\n".join(
                f"- [{s.get('kind')}] {s.get('title')} (갱신 {s.get('updated')}) — {s.get('excerpt')}"
                for s in d["similar"][:5]))
    ex.shutdown(wait=False)
    return "\n\n".join(parts)


# 이력을 **묻는** 낱말 — 연표 서술을 요구할지 가르는 기준. 'digs'(조사할 값어치가 있나)
# 보다 좁다: "정리해줘"·"알려줘"는 값 질문에도 붙는 말이라 여기 넣으면 안 된다(실측 DATA1).
_HIST_WORDS = ("히스토리", "이력", "연혁", "경위", "근황", "변천", "타임라인", "어떻게 되어",
               "어떻게 왔", "그동안")

# 이 도구(LTM)를 **어떻게 쓰는가**를 묻는 말. 티켓에 답이 없고 knowledge/05 에 있다.
_HOWTO_WORDS = ("LTM", "이 앱", "이 도구", "화면에서", "어디 있", "어디서 바꾸", "어떻게 바꾸",
                "어떻게 해", "어떻게 하나", "사용법", "쓰는 법", "단축키", "새로고침")



def _superseded(value: str, hits, doc_rows) -> bool:
    """구축 티켓에 적힌 값이 **나중에 바뀐 것**인가 — 그러면 '현재'로 실으면 안 된다.

    구축 티켓 본문은 그 시점의 값이다. 이후 변경이 changelog 가 아니라 **코멘트·문서**로만
    남는 일이 흔하다(실측: Job 이름이 `..._2h` → `..._30m` 으로 바뀐 기록이 코멘트에만
    있다). 그걸 모르고 바탕값으로 깔면 **옛 값이 현재 값으로 둔갑한다** — 이 저장소가
    DATA3 로 따로 막고 있는 바로 그 실패다.

    판정: 나중 자료(코멘트·문서 발췌)에 **같은 뿌리로 시작하지만 다른** 토큰이 있으면
    바뀐 것으로 본다. 뿌리는 값의 앞 12글자 — 짧은 값은 검사하지 않는다(오탐이 는다).
    """
    v = str(value or "").strip()
    if len(v) < 12:
        return False
    root = v[:12]
    later = " ".join([str(h.get("snippet") or "") for h in (hits or [])]
                     + [str(b or "") for _d, b in (doc_rows or [])])
    for tok in _re.findall(r"[A-Za-z0-9_.\-]{12,}", later):
        if tok.startswith(root) and tok != v:
            return True
    return False


def _relevant_only(state, ev: list) -> list:
    """근거에서 **질문의 고유어를 하나도 안 가진 티켓**을 뺀다.

    common.md 의 관련성 기준: "'Related' means related to the QUESTION'S SPECIFIC CONCEPTS
    …, not merely the same module or the same team." research_analyst.md 도 같은 말을 하는데,
    **산문으로만** 있어서 실측으로 반복해 샜다:
      · REL14 "Iceberg Puffin NDV 통계" 에 모듈만 같은 DL-5487·5876·5122
      · EDGE13 "메타 등록 안 된 테이블" 에 UI 회귀 픽스처 DL-9001
    노이즈는 신뢰를 깎는다 — "관련 이력 없음"이 정답인 자리를 채워 넣는 것이 더 나쁘다.

    **사용자가 키를 직접 댄 티켓은 건드리지 않는다**(그건 관련성 판단의 대상이 아니다).
    고유어가 아예 없는 질문(일반 대화)에서는 아무것도 빼지 않는다 — 판정 근거가 없으면
    판정하지 않는다.
    """
    req = f"{request_text(state)} {last_user_text(state)}"
    # ★ 사용자가 **틀린 표기**로 물었으면 원문 낱말은 실제 제목과 한 글자도 안 겹친다
    #   (실측 DATA11: 'fdc_flat_summary_ic' 로 물어 확인 후 정확 표기를 골랐는데, 필터가
    #   원문만 보고 근거를 전멸시켰다). 코드가 확정한 **대상**을 판정 낱말에 함께 넣는다.
    subj = _re.match(r"\[대상\]\s*(.+)", str(state.get("topic_dossier") or ""))
    if subj:
        req += " " + subj.group(1).strip()
    named = {str(k).upper() for k in (state.get("mentioned_keys") or [])}
    try:
        from app.agent.tools._ident import find_identifiers
        terms = set(find_identifiers(req))
    except Exception:
        terms = set()
    _COMMON = {"task", "epic", "jira", "test", "data", "table", "api", "the", "and",
               "pipeline", "with", "for", "this", "etl"}
    terms |= {w for w in _re.findall(r"[A-Za-z][A-Za-z0-9_.-]{3,}", req)
              if w.lower() not in _COMMON}
    # 한글 고유어도 본다 — 3자 이상 명사가 제목에 그대로 있으면 같은 주제로 인정한다.
    terms |= {w for w in _re.findall(r"[가-힣]{3,}", req)}
    if not terms:
        return ev
    keep = []
    for e in ev:
        if str(e.get("key") or "").upper() in named:
            keep.append(e)
            continue
        # key 중심 질문의 자식·차단·형제는 제목에 원문 낱말이 없어도 구조 근거다. 반면
        # "같은 module의 진행 중 작업"은 구조 관계가 아니므로 이 예외를 주지 않는다.
        why = str(e.get("why") or "")
        if state.get("mentioned_keys") and _re.search(
                r"부모|자식|하위|차단|막(?:고|는)|형제|링크|선행|후속", why):
            keep.append(e)
            continue
        # `why`는 모델이 만든 해석이므로 관련성 판정의 입력으로 쓰지 않는다. 같은-module
        # generic ticket에 "관련 있을 가능성"이라고 쓴 문장이 스스로 필터를 통과시키는
        # 순환 논증이 된다(STR1 실측). 사실 필드인 key/title만 교차 확인한다.
        hay = f"{e.get('key', '')} {e.get('title', '')}".lower()
        if any(t.lower() in hay for t in terms):
            keep.append(e)
    return keep


def _work_action_kind(text: str) -> str:
    """Classify only coarse deliverable shape for duplicate-claim validation."""
    value = str(text or "")
    if _re.search(r"구현|개발|구축|도입|전환|적용|생성|추가", value, _re.I):
        return "implement"
    if _re.search(r"버그|오류|에러|실패|장애|수정|해결|복구", value, _re.I):
        return "fix"
    if _re.search(r"조사|검토|분석|표준|설계|PoC", value, _re.I):
        return "research"
    if _re.search(r"문서|가이드|보고서|매뉴얼", value, _re.I):
        return "document"
    return ""


_DUPLICATE_TYPE_ALIASES = {
    "task": "task", "태스크": "task", "테스크": "task", "작업": "task",
    "bug": "bug", "버그": "bug",
    "story": "story", "스토리": "story",
    "feature": "feature", "기능": "feature",
    "improvement": "improvement",
    "subtask": "sub-task", "sub-task": "sub-task", "sub task": "sub-task",
    "서브태스크": "sub-task", "서브 태스크": "sub-task", "하위 작업": "sub-task",
    "epic": "epic", "에픽": "epic",
}


def _duplicate_ticket_type(value) -> str:
    raw = _re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    compact = raw.replace("_", "-")
    return _DUPLICATE_TYPE_ALIASES.get(compact,
                                       _DUPLICATE_TYPE_ALIASES.get(compact.replace("-", ""), ""))


def _requested_duplicate_type(text: str) -> str:
    """Return only an issue type explicitly bound to the requested create action."""
    pattern = _re.compile(
        r"(?P<type>sub[\s-]?task|서브\s*태스크|하위\s*작업|task|태스크|테스크|"
        r"story|스토리|bug|버그|feature|improvement|epic|에픽)"
        r"\s*(?:를|을|로)?\s*(?:하나\s*)?(?:만들|생성|등록|올려)",
        _re.I,
    )
    matches = list(pattern.finditer(str(text or "")))
    return _duplicate_ticket_type(matches[-1].group("type")) if matches else ""


_DUPLICATE_ANCHOR_STOP = {
    "task", "태스크", "테스크", "story", "스토리", "bug", "버그", "feature",
    "improvement", "epic", "에픽", "sub-task", "subtask", "서브태스크",
    "티켓", "업무", "작업", "요청", "범위", "기존", "신규", "새로", "하나",
    "만들", "만들어", "만들어줘", "생성", "등록", "올려", "추가", "구현", "개발",
    "구축", "도입", "전환", "적용", "수정", "해결", "복구", "오류", "에러", "실패",
    "장애", "조사", "검토", "분석", "설계", "표준", "poc", "문서", "가이드", "보고서",
    "검증", "수행", "진행", "완료", "필요", "catalog", "runtime", "workbench", "dataops",
    "observability", "devops", "etl", "jira", "lake", "manager",
}


def _duplicate_anchor_tokens(text: str) -> list[str]:
    """Extract request-specific subject words, excluding issue/action boilerplate."""
    value = _re.sub(r"\[[^\]\n]{1,40}\]", " ", str(text or ""))
    out = []
    for raw in _re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,}|[가-힣]{2,}", value):
        token = raw.casefold()
        if _re.fullmatch(r"[a-z][a-z0-9]*-\d+", token):
            continue
        if _re.search(r"[가-힣]", token):
            for suffix in ("으로", "에서", "에게", "까지", "부터", "처럼", "하고", "하며",
                           "하는", "한다", "했다", "해야", "이다", "입니다", "해주세요",
                           "해줘", "줘", "을", "를", "은", "는", "이", "가", "의", "에",
                           "로", "와", "과"):
                if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                    token = token[:-len(suffix)]
                    break
        if len(token) < 2 or token in _DUPLICATE_ANCHOR_STOP or token in out:
            continue
        out.append(token)
    return out


def _query_ticket_duplicate_facts(state) -> list[dict]:
    """Merge QueryRunner search rows and materialized details without losing conflicts."""
    records: dict[str, dict] = {}
    order = []

    def add(item, *, detail=False):
        if not isinstance(item, dict) or item.get("error"):
            return
        key = str(item.get("key") or item.get("ticketKey") or "").strip().upper()
        if not _re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", key):
            return
        if key not in records:
            records[key] = {
                "key": key, "titles": [], "types": [], "tiers": [], "statuses": [],
                "status_categories": [], "done_values": [], "parents": [],
                "descriptions": [], "materialized": False,
            }
            order.append(key)
        record = records[key]
        title = _ledger_text(item.get("summary") or item.get("title"), 300)
        issue_type = _duplicate_ticket_type(
            item.get("type") or item.get("issueType") or item.get("issuetype")
        )
        tier = str(item.get("tier") or "").strip().casefold()
        if not tier and issue_type:
            tier = "epic" if issue_type == "epic" else (
                "subtask" if issue_type == "sub-task" else "task"
            )
        for field, value in (("titles", title), ("types", issue_type), ("tiers", tier),
                             ("statuses", str(item.get("status") or "").strip()),
                             ("status_categories",
                              str(item.get("statusCategory") or "").strip().casefold()),
                             ("parents", str(item.get("parent") or "").strip().upper())):
            if value and value not in record[field]:
                record[field].append(value)
        if "done" in item and isinstance(item.get("done"), bool) \
                and item["done"] not in record["done_values"]:
            record["done_values"].append(item["done"])
        if detail:
            record["materialized"] = True
            description = _ledger_text(item.get("description"), 1200)
            if description and description not in record["descriptions"]:
                record["descriptions"].append(description)

    for query_row in (state.get("query_results") or []):
        if not isinstance(query_row, dict):
            continue
        result = query_row.get("result") or {}
        if not isinstance(result, dict):
            continue
        for item in result.get("tickets") or []:
            add(item)
        for item in result.get("ticketDetails") or []:
            add(item, detail=True)
    return [records[key] for key in order]


def _candidate_is_open(record: dict) -> bool:
    statuses = {str(value).strip().casefold() for value in record.get("statuses") or []}
    categories = {str(value).strip().casefold()
                  for value in record.get("status_categories") or []}
    if True in (record.get("done_values") or []) or "done" in categories:
        return False
    if any(_re.search(r"done|closed|resolved|cancel|완료|종료|취소|폐기", value, _re.I)
           for value in statuses):
        return False
    if categories:
        return categories <= {"new", "indeterminate"}
    if False in (record.get("done_values") or []):
        return True
    return any(_re.search(r"open|to\s*do|in\s*progress|reopen|진행|착수|대기", value, _re.I)
               for value in statuses)


def _exact_creation_duplicate_key(state) -> str:
    """Prove one exact open duplicate from completed, materialized QueryRunner facts.

    This intentionally returns an empty string on every ambiguity. Search similarity, model-written
    ``fitness``, a related PoC, a parent Epic, or a closed ticket cannot block a new creation.
    """
    if str(state.get("intent") or "") != Intent.PLAN_WORK or not _query_plan_is_complete(state):
        return ""
    requested = request_text(state) or last_user_text(state)
    requested_type = _requested_duplicate_type(requested)
    requested_kind = _work_action_kind(requested)
    if not requested_type or requested_type == "epic" or not requested_kind:
        return ""
    requested_tier = "subtask" if requested_type == "sub-task" else "task"

    request_anchors = _duplicate_anchor_tokens(requested)
    try:
        from app.agent.tools._ident import find_identifiers, variants
        identifiers = find_identifiers(requested)
        identifier_forms = {form.casefold() for ident in identifiers[:1] for form in variants(ident)}
    except Exception:
        identifier_forms = set()
    if not identifier_forms and len(request_anchors) < 2:
        return ""

    parent_match = _re.search(
        r"\b([A-Z][A-Z0-9]*-\d+)\b[^.\n]{0,20}(?:아래|하위|under)", requested, _re.I,
    )
    requested_parent = parent_match.group(1).upper() if parent_match else ""

    for record in _query_ticket_duplicate_facts(state):
        if not record.get("materialized") or not record.get("descriptions"):
            continue
        types = set(record.get("types") or [])
        tiers = set(record.get("tiers") or [])
        if types != {requested_type} or tiers != {requested_tier} or not _candidate_is_open(record):
            continue
        if requested_parent and set(record.get("parents") or []) != {requested_parent}:
            continue
        titles = list(record.get("titles") or [])
        normalized_titles = {_re.sub(r"\s+", " ", title).strip().casefold() for title in titles}
        if len(normalized_titles) != 1:
            continue
        title = titles[0]
        body = " ".join(record.get("descriptions") or [])
        if _work_action_kind(title) != requested_kind:
            continue
        if requested_kind != "research" and _re.search(
                r"(?:\bPoC\b|조사|검토|분석|설계|표준)", title, _re.I):
            continue

        title_folded, body_folded = title.casefold(), body.casefold()
        if identifier_forms:
            if not any(form in title_folded for form in identifier_forms) \
                    or not any(form in body_folded for form in identifier_forms):
                continue
        else:
            title_anchors = set(_duplicate_anchor_tokens(title))
            body_anchors = set(_duplicate_anchor_tokens(body))
            request_set = set(request_anchors)
            required = len(request_set) if len(request_set) <= 3 else max(
                3, (len(request_set) * 3 + 3) // 4,
            )
            if len(request_set & title_anchors) < required or not (request_set & body_anchors):
                continue
        return record["key"]
    return ""


def _material_duplicate_exists(state, evidence: list[dict]) -> bool:
    """Use the shared raw-fact proof; model evidence/fitness can never establish a duplicate."""
    return bool(_exact_creation_duplicate_key(state))


def _normalize_evidence_quality(item: dict) -> dict:
    """Accept localized model labels, then keep one stable machine contract downstream."""
    confidence = {
        "high": "high", "높음": "high", "높은": "high",
        "medium": "medium", "중간": "medium", "보통": "medium",
        "low": "low", "낮음": "low", "낮은": "low",
        "unknown": "unknown", "미확인": "unknown", "알수없음": "unknown",
    }
    fitness = {
        "direct": "direct", "직접": "direct", "직접적": "direct",
        "supporting": "supporting", "보조": "supporting", "보조적": "supporting",
        "context-only": "context-only", "context only": "context-only",
        "맥락": "context-only", "맥락만": "context-only",
        "unknown": "unknown", "미확인": "unknown", "알수없음": "unknown",
    }
    out = dict(item or {})
    c = str(out.get("confidence") or "unknown").strip().casefold().replace(" ", "")
    f = str(out.get("fitness") or "unknown").strip().casefold()
    out["confidence"] = confidence.get(c, "unknown")
    out["fitness"] = fitness.get(f, "unknown")
    out["limitations"] = str(out.get("limitations") or "").strip()
    return out


def _normalize_evidence_identity(item: dict, state) -> dict:
    """Bind a model-written evidence row to a verified Query Runner ticket.

    The model occasionally puts a shortened title in ``key`` while its own
    rationale names the exact ticket key. Do not leave that as an unlinked text
    source: promote it only when one unique key appears and that key exists in
    the scoped deterministic query results. URLs remain document/web sources.
    """
    out = dict(item or {})
    key = str(out.get("key") or "").strip().upper()
    if out.get("url") or _re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", key):
        return out
    verified = {}
    for row in (state.get("query_results") or []):
        if not isinstance(row, dict):
            continue
        result = row.get("result") or {}
        for ticket in [*(result.get("tickets") or []), *(result.get("ticketDetails") or [])]:
            if not isinstance(ticket, dict):
                continue
            ticket_key = str(ticket.get("key") or "").strip().upper()
            title = str(ticket.get("summary") or ticket.get("title") or "").strip()
            if _re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", ticket_key):
                verified[ticket_key] = title
    material = " ".join([
        str(out.get("title") or ""), str(out.get("why") or ""),
        *(str(obs.get("text") or "") for obs in (out.get("observations") or [])
          if isinstance(obs, dict)),
    ])
    named = list(dict.fromkeys(
        found.upper() for found in _re.findall(
            r"(?<![A-Z0-9-])([A-Z][A-Z0-9]*-\d+)(?![A-Z0-9-])", material, _re.I
        ) if found.upper() in verified
    ))
    if len(named) == 1:
        out["key"] = named[0]
        if verified[named[0]]:
            out["title"] = verified[named[0]]
    return out


def _ltm_guide() -> str:
    """LTM 사용 가이드 **전문**(knowledge/05). 3KB — 검색보다 싸고 확실하다.

    이 부류의 답이 어느 문서에 있는지 우리는 **이름으로 안다**. 의미 검색에 맡기면 k 를
    늘려도 티켓 작성 규칙이 유사도에서 이겨 가이드의 다른 절이 안 온다(실측 GUIDE7:
    "담당자 변경"은 답하고 "강제 새로고침"은 "확인되지 않았다"고 했다).
    **어디에 답이 있는지 아는 질문에 검색을 쓰는 것은 낭비이자 위험이다.**
    """
    try:
        from pathlib import Path as _P
        g = _P(__file__).resolve().parents[4] / "knowledge" / "05-ltm-guide.md"
        if not g.exists():
            return ""
        head = ("[LTM 사용 가이드 — **이 질문의 답은 여기 있다. 티켓이 아니다.**\n"
                " 여기 없는 화면·버튼을 지어내지 말고, 없으면 없다고 답하라.\n"
                " **과거 이력을 찾는 질문이 아니다** — 이력이 없다는 식으로 끝내는 것은\n"
                " 이 질문에 대해서는 틀린 답이다. 가이드에 적힌 조작 방법을 그대로 알려라]\n")
        return head + g.read_text(encoding="utf-8")
    except Exception:
        return ""


def _topic_dossier(term: str, history: bool = False) -> str:
    """**주제 하나**(테이블·기술·특정 업무 무엇이든)에 얽힌 조각을 코드가 전부 모아 온다.

    이런 질문("fdc.fdc_trace_summary_ic 적재주기가?", "Schema Registry 우리 정책이 뭐지?",
    "그 마이그레이션 어디까지 갔지?")의 답은 어느 티켓에도 통째로 없다 — 요청 게시글·개발
    티켓·장애 티켓·필드 변경 changelog·정책 문서, 심지어 **주제와 상관없어 보이는 다른
    티켓의 코멘트**에 흩어져 있다. 모델의 검색 실력에 맡기면 조각 하나를 빠뜨리고 그럴듯한
    답을 짓는다(그룹 활동 때와 같은 실패 모드). 그래서 취합은 코드가 보장하고, 모델은
    **읽고 판단**만 한다.

    필드 변경 이력을 `ticket_field_history` 로 따로 읽는 이유: 화면 타임라인은 status·
    assignee 같은 것만 남기는 allow-list 라 '적재주기' 변경이 걸러진다.
    """
    from concurrent.futures import ThreadPoolExecutor

    from app.agent.tools import BY_NAME
    from app.agent.tools._ctx import client

    term = (term or "").strip()
    if not term:
        return ""
    try:
        found = BY_NAME["find_mentions"].invoke({"term": term, "limit": 8}) or {}
    except Exception:
        return ""
    hits, docs = found.get("hits") or [], found.get("documents") or []
    if not hits and not docs:
        # ★ 정확 표기 미발견 — 유사 식별자 **후보**만 돌려준다. 추정으로 전체 히스토리를
        # 답하는 대신 객관식으로 확인받는다(사용자 결정 — 오탈자 추정은 어디까지나 추정이다).
        # 공백형(밑줄만 뺀 표기)은 variants 가 정확히 찾아 여기 오지 않는다 — 그건 바로 답한다.
        sim = (found.get("similar") or [])
        if sim:
            lines = "\n".join(f"- {x.get('term')} ({x.get('matched')}/{x.get('of')} 토큰 일치)"
                              for x in sim if x.get("term"))
            return f"[표기 후보] '{term}' 표기로는 기록이 없다. 유사 식별자 후보:\n{lines}"
        return f"[{term}] 사내 티켓·문서 어디에서도 이 이름을 찾지 못했다."

    keys, titles = [], {}
    for h in hits:
        k = h.get("key")
        if k and k not in keys:
            keys.append(k)
            titles[k] = h.get("title") or k

    c = client()

    def _hist(k):
        try:
            return k, (c.ticket_field_history(k, 10) or [])
        except Exception:
            return k, []

    def _body(d):
        try:
            r = BY_NAME["read_document"].invoke({"url_or_id": d.get("url") or ""}) or {}
            return d, (r.get("text") or "")[:900]
        except Exception:
            return d, ""

    def _meta(k):
        """그 티켓이 **언제 일어난 무슨 사건인가** — 상태와 날짜."""
        try:
            f = (c.get_issue(k) or {}).get("fields") or {}
            done = (f.get("resolutiondate") or "")[:10]
            # ★ 본문도 같이 들고 온다 — 이미 이 티켓을 읽고 있으니 **공짜**다.
            #   구축 티켓 본문에 방식·주기·Job 이름이 적히는데(실측 DL-9050:
            #   "적재주기: 실시간 스트리밍(1초 마이크로배치)"), 여태 dossier 에는 **제목만**
            #   실렸다. 그래서 "변경 기록이 없으면 구축 티켓의 값이 현재 값"이라는 지시가
            #   있어도 모델이 옮겨 적을 값 자체가 손에 없었다.
            body = _re.sub(r"<[^>]+>", " ", str(f.get("description") or ""))
            return {"key": k, "status": ((f.get("status") or {}).get("name") or ""),
                    "done": done, "desc": _re.sub(r"[ \t]+", " ", body).strip()[:600],
                    "when": done or (f.get("created") or f.get("updated") or "")[:10]}
        except Exception:
            return {"key": k, "status": "", "done": "", "when": "", "desc": ""}

    with ThreadPoolExecutor(max_workers=4) as ex:
        fut_hist = ex.submit(lambda: list(map(_hist, keys[:5])))
        fut_docs = ex.submit(lambda: list(map(_body, docs[:2])))
        fut_meta = ex.submit(lambda: list(map(_meta, keys[:10])))
        hist_rows, doc_rows, metas = fut_hist.result(), fut_docs.result(), fut_meta.result()

    parts = [f"[대상] {term}"]
    # ★ 상태·날짜를 싣고 **시간순으로** 정렬한다. 예전엔 `- KEY "제목"` 뿐이었는데, 그러면
    #   코멘트·변경 이력에 사실 한 줄이 잡힌 티켓만 답에 남고 **나머지는 통째로 증발한다** —
    #   모델이 그것들에 대해 할 말이 없기 때문이다.
    #   실측(fdc.fdc_trace_summary_ic 히스토리 질의): 재료에 8건이 다 있었는데 답변은
    #   변경 이력·코멘트에 걸린 DL-9044·9045·9062 **3건만** 인용했다. 탄생(VoC 요청 →
    #   Job 개발)도, 주기 단축의 계기가 된 지연 장애도, 지금 진행 중인 안정화 모니터링도
    #   빠져 "왜 30분이 됐나"와 "지금 어디까지 왔나"가 답에서 사라졌다.
    #   날짜와 상태가 붙으면 **줄마다 사건이 되고**, 정렬된 목록이 곧 연표가 된다.
    metas.sort(key=lambda m: m["when"] or "9999-99-99")
    tix = "\n".join(
        f"- {m['when'] or '날짜 미상'} · {m['key']} \"{titles.get(m['key'], '')}\""
        f" · {m['status'] or '상태 미상'}"
        + (f" (해결 {m['done']})" if m["done"] else "")
        for m in metas)
    # 최초 도입·구축 티켓 — **현재 상태의 바탕값**(안 바뀐 값)과 아래 '최초 도입' 블록이
    # 둘 다 이것을 쓴다. 그래서 두 블록보다 먼저 정한다(예전엔 뒤에 있어 UnboundLocalError).
    _BUILT = ("구축", "개발", "생성")
    _ORIGIN = _BUILT + ("도입", "신규")
    built = ([m for m in metas if any(w in (titles.get(m["key"]) or "") for w in _BUILT)]
             or [m for m in metas if any(w in (titles.get(m["key"]) or "") for w in _ORIGIN)])
    if tix and history:
        # ★ 이력 질문에서는 **연표를 하나로 합쳐서** 준다 — 티켓 사건과 필드 변경을 따로
        #   주면 모델이 **둘 중 하나만** 옮긴다. 실측(DATA11): 변경 이력 블록만 보고 2건짜리
        #   표를 냈다(같은 케이스 다른 실행은 5건·8건 — 재료가 갈려 있으면 이 변동이 산다).
        #   한 표로 주면 고를 여지가 없고, 모델이 할 일은 옮겨 적는 것뿐이다.
        # ★ **사건에는 '무슨 일이 있었나'가 있어야 한다**(사용자 지적: "연표 사건에 왜 죄다
        #   티켓 이름만 있는지? 실제로 무슨 변동이 있었는지를 정리해야지").
        #   제목은 그 티켓의 **이름**이지 사건의 내용이 아니다 — "적재 지연" 이라는 제목만
        #   보고는 무엇이 어떻게 됐는지 모른다. 티켓 본문 첫 줄과 그 티켓의 코멘트 한 줄을
        #   붙여 준다(둘 다 코드가 이미 손에 쥐고 있다 — 안 실어 준 것뿐이었다).
        events = []
        cmt_of = {}
        for h in hits:
            if h.get("where") == "comment" and h.get("snippet") and h.get("key"):
                cmt_of.setdefault(h["key"], str(h["snippet"]).strip()[:90])
        for m in metas:
            gist = ""
            for ln in str(m.get("desc") or "").splitlines():
                ln = ln.strip(" *-·")
                if len(ln) >= 8 and not ln.startswith("<"):
                    gist = ln[:90]
                    break
            detail = " · ".join(x for x in (gist, cmt_of.get(m["key"], "")) if x)
            events.append((m["when"] or "", f"{m['key']} \"{titles.get(m['key'], '')}\""
                                            f" · {m['status'] or ''}"
                                            + (f" (해결 {m['done']})" if m["done"] else "")
                                            # ★ **한 줄에 한 사건** — 줄을 나누면 이 목록을
                                            #   줄 단위로 읽는 쪽(정렬·테스트)이 깨진다.
                                            + (f" — 내용: {detail}" if detail else "")))
        for k, rows in hist_rows:
            for r in rows:
                f = str(r.get("field") or "")
                if f in ("status", "resolution", "description", "assignee"):
                    continue
                events.append((str(r.get("date") or ""),
                               f"[{f}] {r.get('from') or '(없음)'} → {r.get('to') or '(없음)'}"
                               f" · {k} · {r.get('author') or ''}"))
        events.sort(key=lambda e: e[0] or "9999-99-99")
        parts.append(
            "이 대상의 **연표** (티켓 사건 + 필드 변경을 날짜순으로 합친 것):\n"
            + "\n".join(f"- {d or '날짜 미상'} · {t}" for d, t in events)
            + "\n★ 사용자가 이력·경위를 물었다. **이 연표를 처음부터 지금까지 빠짐없이, "
              "날짜·사건·근거 3열 표로** 옮긴다 — 여기서 몇 줄만 고르면 '왜 이렇게 "
              "됐나'(요청·구축·장애)와 '지금 어디까지 왔나'(진행 중)가 답에서 사라진다. "
              "★ **사건 칸에 티켓 제목만 옮기지 마라** — 제목은 그 티켓의 이름이지 사건의 "
              "내용이 아니다. 위 `└ 내용:` 줄에 있는 **실제 변동**(무엇이 어떻게 됐나)을 "
              "한 줄로 적고, 티켓 키는 근거 칸에 둔다. 예: '적재 지연 — 06:00 배치가 4시간 "
              "밀렸고 파티션 수를 늘려 해소' 처럼. "
              "**줄글로 늘어놓지 마라** — 사건이 다섯 건을 넘으면 문단은 읽히지 않는다"
              "(실측: 같은 재료로 표를 낸 실행은 읽히고, 줄글로 푼 실행은 8건이 뭉갰다).")

        # ★ **연표만 내면 답이 아니다 — '그래서 지금 어떤가'가 빠진다.**
        #   실사용 지적(2026-08-10): 히스토리 질문에 8행짜리 표만 달랑 나왔다. 표에 진행 중
        #   티켓이 한 줄로 들어 있어 체커는 통과했지만, 읽는 사람이 알고 싶은 **현재 값·
        #   지금 진행 중인 일**은 표에서 스스로 재구성해야 했다. 연표는 과거이고, 질문의
        #   끝은 늘 현재다. 그래서 현재 상태를 **코드가 따로 조립해** 별도 슬롯으로 준다
        #   (모델이 표에서 다시 뽑게 두면 실행마다 들쭉날쭉해진다 — 이 파일의 반복된 교훈).
        now_lines = []
        latest = {}
        # ★ **안 바뀐 사실도 현재 상태다.** 여태 `latest` 는 티켓 changelog 의 *변경*만
        #   모아서, 한 번도 안 바뀐 값(Job 이름·소스·보존기간)은 구축 티켓 본문에 버젓이
        #   있는데도 표에서 빠졌다 — 사용자 지적("현재 상태에 왜 이렇게 데이터가 적지?").
        #   구축 티켓 본문의 `* 항목: 값` 줄을 **바탕값**으로 깔고, 변경 이력이 있으면
        #   그것이 덮는다(변경이 최신이므로).
        if built:
            _b0 = built[0]
            for ln in str(_b0.get("desc") or "").splitlines():
                mkv = _re.match(r"\s*[*\-·]\s*([^:：]{2,14})\s*[:：]\s*(.+)", ln)
                if mkv:
                    fld, val = mkv.group(1).strip(), mkv.group(2).strip()[:60]
                    if fld and val and fld not in ("담당", "운영 담당") \
                            and not _superseded(val, hits, doc_rows):
                        latest[fld] = (_b0.get("when") or "", val, _b0["key"])
        for k, rows in hist_rows:
            for r in rows:
                f = str(r.get("field") or "")
                if f in ("status", "resolution", "description", "assignee") or not r.get("to"):
                    continue
                d = str(r.get("date") or "")
                if d >= latest.get(f, ("", "", ""))[0]:
                    latest[f] = (d, str(r.get("to")), k)
        for f, (d, val, k) in latest.items():
            # 재료를 **답의 모양 그대로** 준다 — 항목·값·근거 3열. 줄글로 주면 모델이
            # 줄글로 옮기고, 근거를 괄호에 우겨 넣는다(실측: "[가장 최근 변경 … · DL-9045]"
            # 가 문장 안에 박혀 참조 목록과 따로 놀았다).
            now_lines.append(f"| {f} | {val} | {k} |")
        ongoing = [m for m in metas if "progress" in (m.get("status") or "").lower()
                   or (m.get("status") or "") in ("In Progress", "Open", "To Do", "Reopened")]
        # ★ **상태 낱말을 답에 옮겨 적지 않는다** — 화면이 티켓 키를 뱃지로 그리고 거기
        #   진행 여부가 이미 붙는다. "(In Progress, …)" 를 덧붙이면 같은 말이 두 번이다
        #   (사용자 지적). 여기 재료에도 상태를 넣지 않아 옮겨 적을 것 자체를 없앤다.
        # ★ 진행 중 작업은 **자기 제목을 가진 덩어리**다(사용자 지적) — 현재 값 표 아래에
        #   줄로 흘려 두면 표의 꼬리처럼 읽힌다. 지금 무엇이 돌고 있는지는 따로 볼 것이다.
        # 전용 ticket bullet 목록은 detail badge 자체가 key/title/assignee/status를 보여 준다.
        # 제목·시작일을 다시 붙이면 긴 평문과 badge 정보가 중복된다(실사용 피드백).
        run_lines = [f"- {{{{ticket-detail:{m['key']}}}}}" for m in ongoing]
        if now_lines or run_lines:
            parts.append("**현재 상태** (연표와 별개로 반드시 답에 넣는다 — 사용자가 이력을 "
                         "묻는 이유는 결국 '지금 어떤가'를 알기 위해서다):\n"
                         + "\n".join(now_lines)
                         + ("\n[현재 진행 중인 Task]\n" + "\n".join(run_lines) if run_lines else "")
                         + "\n★ 현재 값은 **표로** 낸다 — `| 항목 | 값 | 근거 |` 3열, 위 줄을 "
                           "그대로 옮기면 된다. **근거 칸에는 연표와 같은 근거 마커([1],[2]…)를 "
                           "쓰고** 하단 `### 근거` 목록에 그 티켓을 적는다 — 값 옆 괄호에 티켓 "
                           "키를 박아 넣어 근거 체계를 둘로 가르지 마라.\n"
                           "★ 진행 중 작업은 **`### 현재 진행 중인 Task` 라는 자기 제목**을 "
                           "달아 표 아래에 따로 내고 한 줄당 하나의 `{{ticket-detail:KEY}}` "
                           "bullet로 둔다. badge가 가진 key/title/assignee/status를 평문으로 "
                           "되풀이하지 마라.\n"
                           "★ 답은 **현재 상태 + 현재 진행 중인 Task + 연표** 세 덩어리다.")
    elif tix:
        # ★ 이력을 묻지 **않은** 질문에는 연표를 쏟지 않는다. 실측(DATA1): "현재 적재주기는?"
        #   한 줄을 물었는데 8행 연표 + 참조 10개가 나왔다 — 이력 지시를 모든 경로에 실은
        #   탓이다. 목록 자체는 어디서 찾을지 알려 주는 **지도**라 남기되, 서술은 시키지 않는다.
        parts.append(
            "관련 티켓 (지도 — 어느 티켓에 무엇이 있는지):\n" + tix
            + "\n★ 사용자는 이력을 묻지 않았다. **물어본 것만 답한다** — 이 목록은 값의 출처를 "
              "찾는 데 쓰고, 연표로 늘어놓지 마라. 근거로 필요한 티켓만 골라 인용한다.")
    # ★ **최초 도입·구축 티켓을 코드가 지목한다.** knowledge/06 은 "변경 이력이 없으면 최초
    #   도입·구축 티켓에 적힌 값이 현재 값"이라 규정하는데, 그 티켓이 어느 것인지는 모델이
    #   목록에서 찾아야 했다. 실측(DATA4): 적재주기 변경 기록이 없자 **제목에 '실시간 수집
    #   파이프라인 구축'이라 버젓이 적힌 티켓을 두고** "확인된 기록 없음"이라 답했다.
    #   규칙이 실행 가능하려면 그 티켓을 짚어 줘야 한다.
    # 만든 티켓을 먼저 본다 — 요청(VoC)에는 "무엇을 원한다"가 적히고, **구축 티켓에 실제
    # 방식·이름·주기가 적힌다**. 둘 다 없으면 가장 이른 것으로 떨어진다.
    if built:
        b = built[0]        # metas 는 시간순 — 가장 이른 것이 최초다
        # ★ **본문을 함께 싣는다.** 지시만 있고 값이 없으면 모델은 지시를 지킬 수가 없다 —
        #   실측(DATA4 ×4): dossier 에 이 티켓과 지시가 다 있는데도 3회가 "확인된 기록 없음"
        #   이었고, 그중 하나는 "DL-9050에 명시된 값으로, 변경 기록이 없으므로 확인된 바가
        #   없습니다"라고 **티켓을 짚으면서 값을 못 말했다.** 제목("실시간 수집 파이프라인
        #   구축")은 사업 이름이지 속성 값이 아니다. 값은 본문에 있다.
        parts.append(f"최초 도입·구축: {b['when']} · {b['key']} \"{titles.get(b['key'], '')}\""
                     + (f"\n  본문: {b['desc']}" if b.get("desc") else "")
                     + "\n★ **변경 기록이 없는 속성의 현재 값은 이 티켓에 적힌 값이다**"
                       "(knowledge/06). 위 본문에 방식·주기·이름이 적혀 있으면 **그 값을 그대로 "
                       "답하라** — 변경 이력에 없다고 '확인된 기록 없음'으로 답하지 마라.")
    quotes = [f"- {h['key']} · {h.get('author') or '작성자 미상'} · {h.get('date') or ''}: "
              f"\"{h['snippet']}\""
              for h in hits if h.get("where") == "comment" and h.get("snippet")]
    if quotes:
        parts.append("코멘트 근거 (이 문장이 사실의 출처다. 인용할 때 **티켓 키·작성자·날짜는 "
                     "여기 적힌 짝 그대로** 옮겨라 — 다른 행의 작성자를 섞지 마라. ★ 작성자는 "
                     "그 말을 한 사람일 뿐 **대상의 담당자가 아니다**):\n"
                     + "\n".join(quotes[:8]))
    desc = [f"- {h['key']}: \"{h['snippet']}\""
            for h in hits if h.get("where") == "description" and h.get("snippet")]
    if desc:
        parts.append("본문 근거:\n" + "\n".join(desc[:5]))
    # 변경 이력에는 **티켓 제목을 반드시 함께** 싣는다 — 키만 있으면 그 변경이 무엇에 대한
    # 것인지 모델이 알 수 없다. 실측 사고: 주제(Schema Registry)를 코멘트에서 언급했을 뿐인
    # 다른 티켓의 '보존기간 30→90일'을 주제의 속성인 것처럼 답했다.
    # ★ **필드별로 묶는다.** 한 줄씩 섞어 두면 모델이 다른 필드의 값을 물어본 필드의 값으로
    #   옮겨 적는다 — 실측(DATA4): "보존기간 30일 → 90일" 변경을 보고 **"적재주기는 90일"**
    #   이라 단언했다. 대상은 맞고 필드만 틀린 오답이라 눈에 잘 안 띄고, 사용자는 그 숫자를
    #   그대로 보고서에 옮긴다. 묶어 두면 "이 값이 무슨 필드의 값인가"가 구조로 보인다.
    by_field = {}
    for k, rows in hist_rows:
        for r in rows:
            f = str(r.get("field") or "")
            if f in ("status", "resolution", "description", "assignee"):
                continue
            by_field.setdefault(f, []).append(
                f"  - {r['date']} · {r.get('author') or ''}: "
                f"{r.get('from') or '(없음)'} → {r.get('to') or '(없음)'}"
                f"   ({k} \"{titles.get(k, '')}\")")
    if by_field:
        blocks = []
        for f, rows in list(by_field.items())[:6]:
            blocks.append(f"[{f}]\n" + "\n".join(rows[:5]))
        parts.append("변경 이력 — **필드별**. 현재 값 = 그 필드의 가장 최근 변경.\n"
                     "★ 묻지 않은 필드의 값을 물어본 필드의 값으로 옮겨 적지 마라 — "
                     "'보존기간'이 바뀐 것을 '적재주기'라고 답하는 것이 이 자료의 전형적 오독이다.\n"
                     "★ **어떤 필드의 블록이 없다는 것은 '안 바뀌었다'는 뜻이지 '값이 없다'는 "
                     "뜻이 아니다.** 그 필드는 위의 관련 티켓(도입·구축 티켓 제목)과 아래 문서 "
                     "발췌에서 찾아라 — 변경 기록이 없다고 '확인된 기록 없음'으로 답하면, "
                     "제목에 버젓이 적힌 사실을 못 본 것이다(실측).\n"
                     "★ 각 행은 그 티켓의 **대상**에 대한 변경이다 — 제목을 보고 지금 묻는 "
                     "대상의 속성인지도 함께 확인하라.\n" + "\n".join(blocks))
    for d, body in doc_rows:
        if body:
            parts.append(f"문서 「{d.get('title')}」 ({d.get('url')}) 발췌:\n{body}")

    # ── 담당은 **코드가 판정한다.** 프롬프트로 "작성자는 담당자가 아니다"라고 두 번 경고해도
    # 모델은 코멘트 작성자를 담당자로 답했다(실측 2회). 담당이라고 **적힌** 것만 담당이다.
    # ★ 여기 있던 `import re as _re` 를 지웠다 — 모듈 맨 위(17행)에 이미 있는데 **함수 안에서
    #   다시 import 하면 `_re` 가 이 함수의 지역 이름이 되어**, 위쪽 중첩 함수(`_meta`)가
    #   그것을 참조하는 순간 NameError 가 난다. 그런데 `_meta` 의 `except Exception` 이
    #   그걸 삼켜서, **모든 티켓의 날짜·상태가 조용히 '미상'으로 떨어졌다**(실측: 연표가
    #   통째로 '날짜 미상'이 됐다). 함수 안 재import 는 이 파일에서 금지한다.
    # ① 담당이 **이관된 적** 있으면 그 변경 기록이 이긴다 — 최초 구축 티켓만 보고 옛 담당을
    #    현재로 답하는 것이 이 유형의 전형적 실패다(픽스처가 그렇게 심겨 있다).
    handover = [(r["date"], r.get("from") or "", r.get("to") or "", k)
                for k, rows in hist_rows for r in rows
                if "담당" in (r.get("field") or "") and (r.get("to") or "").strip()]
    if handover:
        d0, prev, cur, key0 = max(handover)
        parts.append(f"[담당] 현재 {cur} — {key0} 에서 {prev} 로부터 이관({d0}). "
                     f"{prev} 는 **이전** 담당이다. 현재 담당을 물으면 {cur} 라고 답하라.")
        return "\n\n".join(parts)[:4000]

    owners, blob = [], "\n".join(parts)
    for m in _re.finditer(r"담당[^\n]{0,12}?(skcc\.[a-z]\d{3,5})", blob):
        if m.group(1) not in owners:
            owners.append(m.group(1))
    parts.append("[담당] " + (", ".join(owners) + " (기록에 '담당'으로 적힌 사람)" if owners else
                              "확인된 기록 없음 — 코멘트 작성자·티켓 담당자를 이 대상의 담당으로 "
                              "말하지 마라. 모르면 '확인되지 않음'이라고 답한다."))
    return "\n\n".join(parts)[:4000]


class ResearchAnalyst(ToolAgent):
    name = Node.RESEARCH_ANALYST
    # 조각을 모아야 하는 질문은 걸음이 더 든다(티켓 열기 3~4 + 문서 읽기 + 확인).
    # 상속값 6 으로는 결론 단계 전에 소진됐다. 사전 취합(_dataset_dossier)이 재료를 미리
    # 실어 주므로 PMO 의 12 까지는 필요 없다.
    # 사전취합이 재료를 미리 실어 주므로 걸음은 적을수록 좋다. 10 은 상한까지 도는
    # 일이 잦았고(실측: 생성 턴에서 11회 LLM = 상한 소진), 그 걸음의 대부분이 이미
    # 자료로 가진 것을 도구로 재확인하는 데 쓰였다.
    max_steps = 7

    def _from_completed_typed_write(self, state, action: str) -> dict | None:
        """Project completed acquisition into Research state with zero tool decisions.

        Query Specialist and Query Runner own acquisition.  Once their typed contract is complete and
        every named mutation target has been opened, Research Analyst either passes the bounded factual
        ledger through unchanged or performs exactly one synthesis for an explicit semantic deliverable.
        """
        direct_state = state
        if not state.get("web_context") and any(
                isinstance(row, dict) and row.get("source") in {"web", "github"}
                for row in (state.get("query_results") or [])):
            external = _prefetched_external_context(state.get("query_results") or [])[:6000]
            if external:
                direct_state = {**state, "web_context": external}

        if _compound_plan_requires_research_synthesis(direct_state):
            synthesis_state = {**direct_state, "_research_analyst_prefetched": True}
            try:
                out = self.apply(
                    synthesis_state,
                    self._synthesize_prefetched_query_plan(synthesis_state),
                )
                out["trace"] = (out.get("trace") or []) + [{
                    "node": self.name, "label": "과거 이력 조사",
                    "note": "완료된 복합 조사·작성 QueryPlan을 1회 의미 정리(도구 재호출 생략)",
                }]
                return out
            except Exception:
                # A provider/format failure cannot be repaired by repeating acquisition.  Preserve the
                # real cross-source ledger, ranked only by typed outcome terms.
                try:
                    out = self.apply(
                        direct_state,
                        _completed_write_ledger(
                            direct_state, research_focus=True, action=action,
                        ),
                    )
                except Exception:
                    return None
                out["trace"] = (out.get("trace") or []) + [{
                    "node": self.name, "label": "과거 이력 조사",
                    "note": "복합 조사 의미 정리 실패 — 관련도 기반 bounded 근거 장부 전달",
                }]
                return out

        try:
            out = self.apply(
                direct_state,
                _completed_write_ledger(direct_state, action=action),
            )
        except Exception:
            return None
        out["trace"] = (out.get("trace") or []) + [{
            "node": self.name, "label": "과거 이력 조사",
            "note": "완료된 QueryPlan deterministic 근거 장부 전달(LLM/ReAct 생략)",
        }]
        return out

    def node(self):
        """진척도를 물었으면 조사 뒤 **코드가** get_progress 를 불러 숫자를 붙인다.

        프롬프트(시스템 팁 → 명령서 제약)로 두 번 시도했지만 모델은 search_work_history 의
        docstring("안 나오면 말을 바꿔 다시")에 끌려 검색만 반복하다 걸음을 소진했다(실측 2회).
        진척률 조회는 판단이 아니라 **조회**다 — 모델이 부르길 기대하는 대신 코드가 부른다.
        모델의 몫은 여전히 조사(무엇을 찾고 어디를 열지)다.
        """
        react = super().node()

        def run(state):
            # This gate must precede neighborhood/dossier/external supplements: those are acquisition
            # tools too. A completed typed write already has its bounded source ledger.
            typed_write_action = _typed_write_action(state)
            completed_write_action = _completed_typed_write_action(state)
            if completed_write_action:
                completed = self._from_completed_typed_write(state, completed_write_action)
                if completed is not None:
                    return completed

            query_fast_path = _single_query_fast_path_decision(state)
            if query_fast_path.complete:
                try:
                    out = self.apply(state, _completed_query_ledger(state))
                except Exception:
                    pass
                else:
                    out["trace"] = (out.get("trace") or []) + typed_fast_path_note(
                        state,
                        self.name,
                        "완료된 단일 QueryPlan 근거 장부 전달(Research LLM 생략)",
                        query_fast_path,
                    )
                    return out

            # ── 사전 취합: 사용자가 티켓을 지목했으면 그 주변 지도(계보·라벨·컴포넌트·링크·
            # 참여자)를 **코드가** 만들어 자료로 준다. 모델이 검색을 반복하며 더듬는 대신
            # 지도를 보고 "무엇을 열지"만 고르게 한다.
            keys0 = [k for k in (state.get("mentioned_keys") or []) if k][:2]
            if keys0:
                from app.agent.tools.survey_tools import neighborhood
                maps = []
                for k in keys0:
                    m = neighborhood(k)
                    if m.get("error"):
                        continue
                    rows = "\n".join(f"- {c['key']} [{'+'.join(c['via'])}] {c.get('title', '')}"
                                     for c in m["candidates"][:15])
                    docs = "\n".join(f"- {d.get('title')} ({d.get('url')})"
                                     for d in m["documents"][:5])
                    block = f"[{k}] {m.get('summary', '')}\n후보:\n{rows}"
                    if docs:
                        block += f"\n문서:\n{docs}"
                    if m.get("participants"):
                        block += f"\n참여자: {', '.join(m['participants'])}"
                    maps.append(block)
                if maps:
                    # 상한 — 지도가 프롬프트를 잡아먹으면 배보다 배꼽이다(P-1 다이어트).
                    state = {**state, "seed_map": "\n\n".join(maps)[:2500]}

            # ── 사전 조사(주제형): 티켓 키 없이 주제·키워드로 물으면("CDC 근황",
            # "픽스처가 무슨 테스크야", "임베딩 캐시에 대해 아는 것") **코드가** 키워드 검색을
            # 먼저 돌리고, 지식·근황형이거나 결과가 빈약하면 의미 검색(RAG)까지 돌려 자료로
            # 준다. 모델의 검색 실력에 기대지 않는다 — 실측: 노이즈 단어 하나로 0건을 받고
            # "이력 없음"으로 답했다.
            # ── 사전 취합(주제형): 질문이 **무엇 하나**에 대한 것이면(테이블·기술·특정 업무)
            # 그 주제에 얽힌 조각(티켓·코멘트 인용·필드 변경 이력·문서 본문)을 **코드가**
            # 전부 모아 준다. 데이터 자산 이름이면 무조건, 그 밖의 주제어는 조사형 질문일 때만
            # (일반 대화에서까지 티켓을 여러 건 여는 비싼 취합을 돌릴 이유가 없다).
            from app.agent.tools._ident import find_identifiers, subject_term
            asked_s = last_user_text(state)
            subject = subject_term(asked_s, state.get("keywords"))
            # A meeting title is the stable topic boundary. Model-selected keywords often
            # narrow it to one action (for example ``StarRocks reader verification``), which
            # drops the earlier design ticket and document needed to understand the decision.
            from app.agent.workflow.meeting_context import meeting_subject
            subject = meeting_subject(state) or subject
            digs = any(w in asked_s for w in ("히스토리", "이력", "근황", "최근", "경위", "정리",
                                              "알려줘", "설명", "무슨", "어떤", "왜", "언제",
                                              "누가", "어디", "뭐", "지식", "현재"))
            # ── ★ **대상 없는 조사 요청에는 '없다'가 아니라 '무엇을?'이 답이다** ──────
            # 실측(추천 칩 CHIP4): "특정 주제를 조사하고 싶어 (히스토리, 지식 등)" 에
            # "요청하신 주제에 대한 과거 기록이 내부에 존재하지 않습니다"라고 답했다.
            # 주제가 **아직 없는데** 없다고 답한 것이다 — 사용자는 무엇을 물어야 할지
            # 알려 달라고 온 것이고, 첫 화면 추천 칩이 정확히 이 모양이라 빈도가 높다.
            _PLACEHOLDER = ("특정 주제", "어떤 주제", "무언가", "뭔가", "주제를 조사",
                            "조사하고 싶", "알아보고 싶", "궁금한 게 있")
            if not subject and not find_identifiers(asked_s) \
                    and not (state.get("mentioned_keys") or []) \
                    and any(w in asked_s for w in _PLACEHOLDER):
                return {
                    "situation": "조사할 **대상**이 아직 정해지지 않았다 — 사용자에게 묻는다.",
                    "evidence": [],
                    "questions": [{
                        "question": "무엇을 조사할까요? 대상을 알려주시면 그 주제의 이력·"
                                    "관련 티켓·문서를 모아 정리하겠습니다.",
                        "kind": "text", "options": [], "field": ""}],
                    "trace": note(state, self.name, "조사 대상 확인 질문 — 대상 미지정")}

            # ★ 사용법 질문은 **가이드를 재료로 직결**한다. 조사 경로로 보내면 ResearchAnalyst 의
            #   존재 이유가 "이 일이 처음인가 — 과거 이력 조사"라, 찾을 이력이 없는 질문에
            #   "발견되지 않았습니다"로 끝난다(실측 GUIDE7: 재료는 가이드인데 프레이밍이
            #   조사였다). 답이 어느 문서에 있는지 아는 질문이니 그 문서를 주고 바로 답한다.
            if any(w in asked_s for w in _HOWTO_WORDS):
                guide = _ltm_guide()
                if guide:
                    state = {**state, "topic_dossier": guide}
            elif subject and (find_identifiers(asked_s, " ".join(state.get("keywords") or [])) or digs):
                try:
                    # 이력을 물었는가 — 원 요청과 이번 턴 **둘 다** 본다. 확인 질문에 답한
                    # 턴은 발화가 '보기 하나'라 거기엔 이력 낱말이 없다(실측 DATA13).
                    _hist_ask = any(
                        w in (request_text(state) + " " + asked_s) for w in _HIST_WORDS) \
                        or (state.get("answer_depth") or "") == "explain"
                    # ★ **사용법 질문은 dossier 로 보내지 않는다.** 답이 티켓에 없고
                    #   knowledge/05 에 있는데, 주제 dossier 가 티켓을 물어와 그것으로
                    #   답해 버린다(실측 GUIDE7: "티켓 담당자 어떻게 바꿔?" 에 UI 회귀
                    #   픽스처 티켓 DL-9010 을 답으로 냈다).
                    #   §5-c 의 "사전취합이 자라면 ReAct 에만 있던 도구가 조용히 도달 불능이
                    #   된다"가 그대로 실현된 것인데, 여기서 도달 불능이 된 것은 도구가 아니라
                    #   **_presurvey 에 이미 있던 search_rules 배선**이었다(사전취합이 사전취합을
                    #   가렸다). dossier 를 비우면 _presurvey 가 돌고 거기서 규칙을 싣는다.
                    dossier = ("" if any(w in asked_s for w in _HOWTO_WORDS)
                               else _topic_dossier(subject, history=_hist_ask))
                except Exception:
                    dossier = ""
                # ── 표기 후보 — 추정으로 답하지 않고 **객관식으로 확인**받는다(사용자 결정).
                # 다음 턴에 사용자가 고르면 정확 표기로 정상 조사가 돈다.
                if dossier.startswith("[표기 후보]"):
                    import re as _re
                    cands = _re.findall(r"- (\S+) \(", dossier)[:4]
                    return {
                        # ★ **찾은 것을 먼저 말한다**(두괄식). "기록이 없습니다"를 앞세우면
                        #   사용자는 그 한 줄에서 '없구나'로 읽고 멈춘다 — 정작 후보를
                        #   찾아 놓고도 그렇다(사용자 관점 리뷰 F2, blocker).
                        #   있는 것을 먼저, 없는 것은 그 뒤에.
                        "situation": (f"비슷한 이름 {len(cands)}건을 찾았다: "
                                      f"{', '.join(cands)}. "
                                      f"입력한 '{subject}' 표기 그대로의 기록은 없다 — "
                                      "어느 것인지 확인받고 그 표기로 조사한다."),
                        "evidence": [],
                        "questions": [{
                            "question": f"비슷한 이름을 {len(cands)}건 찾았습니다 — "
                                        f"어느 것인가요? (입력하신 '{subject}' 표기 "
                                        "그대로는 기록이 없습니다)",
                            "kind": "choice",
                            "options": cands + ["이 중에 없음 — 정확한 표기를 알려주세요"],
                            "field": ""}],
                        "trace": note(state, self.name,
                                      f"표기 확인 질문 — 후보 {len(cands)}건")}
                if dossier:
                    state = {**state, "topic_dossier": dossier}

            pre = ""
            # PLAN_WORK already passed a scoped, paginated QueryPlan. Repeating the same keywords through
            # search_work_history + semantic search caused dozens of local comment scans and then injected
            # duplicate context. Keep that broader fallback for open-ended ASK/history routes only.
            completed_query_plan = _query_plan_is_complete(state)
            planned_create_query = ((state.get("intent") or "") == Intent.PLAN_WORK
                                    and isinstance(state.get("query_results"), list)
                                    and bool(state.get("query_results")))
            if not keys0 and state.get("keywords") \
                    and not (planned_create_query or completed_query_plan):
                try:
                    pre = _presurvey(state)
                except Exception:
                    pre = ""
                if pre:
                    # 문서 본문을 실은 경우엔 상한을 늘린다 — 2500자에서 잘려 정작
                    # 요약의 재료(문서가 정한 규칙)가 사라졌다(실측 T3).
                    cap = 6000 if "문서 본문 「" in pre else 2500
                    state = {**state, "pre_survey": pre[:cap]}

            # ── 첨부파일 질의 사전 취합 — "첨부 뭐 있어?" 는 검색이 아니라 조회다.
            # ── 지목한 티켓의 **현재 사실**은 코드가 확정한다 ────────────────
            # 실측(Round P): "DL-9093 이거 왜 늦어지는거지?" 에 이미 Closed 인 티켓을
            # "진행 중", 담당(최하은)을 "확인되지 않음"이라고 답했다 — 이웃 지도(seed_map)만
            # 보고 본체 필드를 안 읽은 것이다. 상태·담당·마감·우선순위와 변동/코멘트를
            # 코드가 실어 준다("왜/지연/상황" 류 질문에서 특히 답의 뼈대다).
            if keys0 and any(w in asked_s for w in ("왜", "늦", "지연", "밀리", "막힘", "블로",
                                                    "상황", "어디까지", "진행", "언제 끝",
                                                    "경위", "문제")):
                try:
                    from app.agent import tools as T
                    rows = []
                    for k in keys0:
                        gt = T.BY_NAME["get_ticket"].invoke({"key": k}) or {}
                        if gt.get("error"):
                            continue
                        rows.append(f"[{k} 현재] " + " · ".join(
                            f"{lab}={gt.get(f) or '없음'}" for lab, f in
                            (("상태", "status"), ("담당", "assignee"), ("마감", "duedate"),
                             ("우선순위", "priority"), ("타입", "type"), ("Epic", "epic"))))
                        from app.agent.tools.survey_tools import progress_report
                        pr = progress_report(k) or {}
                        for lab, fld in (("변동", "timeline"), ("코멘트", "comments"),
                                         ("하위", "children"), ("링크", "links")):
                            vals = pr.get(fld) or []
                            if vals:
                                rows.append(f"[{k} {lab}] " + "; ".join(
                                    str(v if not isinstance(v, dict) else
                                        " ".join(str(v.get(x)) for x in
                                                 ("date", "when", "who", "field", "from",
                                                  "to", "key", "rel", "status", "title",
                                                  "text", "summary") if v.get(x)))[:180]
                                    for v in vals[:6]))
                    if rows:
                        merged = ((state.get("pre_survey") or "") + "\n\n"
                                  + "\n".join(rows)).strip()
                        state = {**state, "pre_survey": merged[:3500]}
                except Exception:
                    pass

            # dossier 직결이 첨부 목록 도구를 건너뛰어 파일은 읽으면서 목록은 '없음'이라는
            # 모순 답이 나왔다(실측). 목록은 코드가 준다.
            if keys0 and any(w in asked_s for w in ("첨부", "파일 목록", "attachment")):
                try:
                    from app.agent import tools as T
                    rows = []
                    for k in keys0:
                        la = T.BY_NAME["list_attachments"].invoke({"ticket_key": k}) or {}
                        files = la.get("files") or []
                        if files:
                            rows.append(f"[{k} 첨부 {len(files)}건] " + "; ".join(
                                f"{f.get('name')} ({f.get('size') or '?'}, {f.get('kind') or ''}"
                                f"{', 읽기 가능' if f.get('readable') else ''})"
                                for f in files[:10]))
                            # 내용까지 물었으면(요약해줘) 읽기도 코드가 한다 — 모델이
                            # read_attachment 를 안 골라 '내용 확인 불가'로 답했다(실측).
                            if any(w in asked_s for w in ("내용", "요약", "들어있", "뭐가 있")):
                                low = asked_s.lower()
                                pick = next(
                                    (f for f in files if f.get("readable") and any(
                                        t and t in low for t in
                                        str(f.get("name", "")).lower()
                                        .replace(".", "_").split("_"))),
                                    next((f for f in files if f.get("readable")), None))
                                if pick:
                                    ra = T.BY_NAME["read_attachment"].invoke(
                                        {"ticket_key": k, "filename": pick.get("name")}) or {}
                                    if ra.get("columns"):     # 표 형식(csv/xlsx/parquet)
                                        body = (f"컬럼: {ra.get('columns')} · "
                                                f"행 {ra.get('rows_total')}건 · 샘플: "
                                                + str(ra.get("sample") or ra.get("matched"))[:600])
                                    else:
                                        body = str(ra.get("text") or "")[:800]
                                    if body:
                                        rows.append(f"[{pick.get('name')} 내용 발췌]\n{body}")
                        else:
                            rows.append(f"[{k}] 첨부파일 없음")
                    if rows:
                        merged = ((state.get("pre_survey") or "") + "\n\n"
                                  + "\n".join(rows)).strip()
                        state = {**state, "pre_survey": merged[:3500]}
                except Exception:
                    pass

            # ── 재배분 사전 취합 — "x1450 일이 많으니 두어 개 x1402 에게" 는 소스 사용자의
            # 미시작 티켓 목록이 재료다. 조회하면 되는 것을 사용자에게 물었다(실측).
            if (state.get("intent") or "") == "modify" \
                    and any(w in asked_s for w in ("넘겨", "재배분", "나눠", "옮겨 줘", "분산")):
                import re as _re
                uids = _re.findall(r"(?:skcc\.)?([a-z]{1,2}\d{2,6})", asked_s)
                if len(uids) >= 2:
                    src = f"skcc.{uids[0]}"
                    try:
                        from app.agent import tools as T
                        w = T.BY_NAME["get_my_workload"].invoke({"user_id": src}) or {}
                        opens = [t for t in (w.get("tickets") or [])
                                 if str(t.get("status", "")).lower() in
                                 ("open", "to do", "reopened")][:10]
                        if opens:
                            blk = (f"[재배분 후보 — {src} 의 미시작 티켓 {len(opens)}건. "
                                   f"이 중 요청 개수만큼 골라 change.keys 에 담고 "
                                   f"assignee 를 skcc.{uids[1]} 로]\n"
                                   + "\n".join(f"- {t.get('key')} \"{t.get('summary', '')}\" "
                                               f"(마감 {t.get('duedate') or '없음'})"
                                               for t in opens))
                            merged = ((state.get("pre_survey") or "") + "\n\n" + blk).strip()
                            state = {**state, "pre_survey": merged[:3500]}
                    except Exception:
                        pass

            # ── 조건 일괄 수정 대상 사전 취합 — "마감 지난 것 전부 P1" 의 대상 집합은
            # 검색이 아니라 **JQL 조회**다. 모델에게 run_jql 을 기대했더니 텍스트 검색만
            # 하다 "대상 없음"으로 답했다(실측 2회). 조건 파싱과 조회는 코드가 한다.
            if (state.get("intent") or "") == "modify" \
                    and any(w in asked_s for w in ("전부", "모두", "일괄", "다 바꿔", "싹")):
                import re as _re
                conds = []
                if _re.search(r"마감[^.\n]{0,8}(지난|지났|초과|넘)", asked_s):
                    conds.append('duedate < now() AND statusCategory != done')
                if "미배정" in asked_s or "담당 없" in asked_s:
                    conds.append("assignee is EMPTY AND statusCategory != done")
                # ★ "N개월/N주/N일 이상 업데이트 없는" — 정체 티켓을 조건으로 잡는다.
                #   실사용 예: "ETL 모듈 3개월 이상 업데이트 없는 티켓에 담당자를 멘션해서
                #   상태 점검을 요청" — 이 조건이 없으면 대상 집합이 안 잡혀 일괄 경로가
                #   통째로 꺼지고, 모델이 아무 티켓이나 골라 댓글을 달게 된다.
                mstale = _re.search(r"(\d+)\s*(개월|달|주|일)[^.\n]{0,14}없", asked_s)
                if mstale:
                    _n = int(mstale.group(1))
                    _d = _n * 30 if mstale.group(2) in ("개월", "달") else (
                        _n * 7 if mstale.group(2) == "주" else _n)
                    conds.append(f"updated <= -{_d}d AND statusCategory != done")
                mod = next((m for m in ("ETL", "Catalog", "Runtime", "Workbench",
                                        "DataOps", "DevOps") if m.lower() in asked_s.lower()), "")
                if conds:
                    from app.agent import tools as T
                    # "티켓들"의 상식적 대상은 Task류다 — Epic 은 보고 단위라 일괄 변경에서
                    # 뺀다(실측: Epic 4건이 P1 일괄 대상에 섞였다).
                    conds.append("issuetype != Epic")
                    # ★ **범위를 좁히고 그 사실을 밝힌다**(사용자 요청 ①구체화).
                    #   Sub-Task 와 VoC 는 성격이 달라 "티켓 전부"에 넣을지가 매번 갈린다 —
                    #   짐작해서 넣으면 남의 일에 알림이 가고, 빼면 정작 필요한 것이 빠진다.
                    #   말하지 않았으면 기본은 Task 류만으로 좁히고, 답변이 그 사실을 말한다.
                    if any(w in asked_s for w in ("서브태스크", "서브 태스크", "sub-task",
                                                  "하위", "전 유형", "voc", "VoC")):
                        scope_note = "[범위] 사용자가 말한 대로 하위 유형까지 포함했다."
                    else:
                        conds.append("issuetype != Sub-task")
                        scope_note = ("[범위] Sub-Task 는 **뺐다**(사용자가 말하지 않았다). "
                                      "답변에서 이 사실을 한 줄로 밝히고, 포함하려면 말씀해 "
                                      "달라고 안내하라 — 대상 집합이 곧 이 작업의 영향 범위다.")
                    jql = " AND ".join(([f'component = "{mod}"'] if mod else []) + conds)
                    try:
                        rj = T.BY_NAME["run_jql"].invoke({"jql": jql, "limit": 30}) or {}
                        rows = rj.get("items") or rj.get("tickets") or []
                        tkeys = [str(t.get("key")) for t in rows if t.get("key")]
                        if tkeys:
                            blk = (scope_note + "\n"
                                   + f"[일괄 수정 대상 — JQL `{jql}` 로 {len(tkeys)}건 확정] "
                                   + ", ".join(f"{t.get('key')} \"{t.get('summary', '')[:30]}\""
                                               for t in rows[:30]))
                            merged = ((state.get("pre_survey") or "") + "\n\n" + blk).strip()
                            state = {**state, "pre_survey": merged[:3500],
                                     "bulk_targets": tkeys}
                    except Exception:
                        pass

            # ── 온보딩/소개 질의 사전 취합 — 모듈 구성은 knowledge(정적 RAG)에, Epic 목록은
            # find_parent_epic 에 이미 있다. 모델이 검색만 하다 "Epic 정의 없음"으로
            # 오답했다(실측 E4). 조회는 코드가 한다.
            if any(w in asked_s for w in ("온보딩", "새로 온", "신규 입사", "소개할", "소개해")) \
                    and any(w in asked_s for w in ("프로젝트", "모듈", "팀", "구성")):
                try:
                    from app.agent import tools as T
                    rows = []
                    hits = T.BY_NAME["search_rules"].invoke(
                        {"question": "모듈 구성 정의 역할", "k": 2}) or []
                    for h in hits:
                        if h.get("rule"):
                            rows.append("[모듈 정의(knowledge)]\n" + str(h["rule"])[:900])
                            break
                    eps = T.BY_NAME["find_parent_epic"].invoke({"query": "", "limit": 20}) or []
                    ep_rows = [f"- {e.get('key')} [{e.get('module') or '-'}] "
                               f"\"{e.get('summary', '')}\""
                               for e in eps if isinstance(e, dict) and e.get("key")]
                    if ep_rows:
                        rows.append("[주요 Epic 목록(실값)]\n" + "\n".join(ep_rows[:20]))
                    if rows:
                        merged = ((state.get("pre_survey") or "") + "\n\n"
                                  + "\n\n".join(rows)).strip()
                        state = {**state, "pre_survey": merged[:3500]}
                except Exception:
                    pass

            # ── 허용값 질의 사전 취합 — "라벨 목록 보여줘·정리 제안" 류는 검색이 아니라
            # **조회**다. 모델이 list_ticket_options 를 고르길 기대했더니 검색만 하다
            # '확인 불가'로 죽었다(실측 2회). 조회는 코드가 한다.
            asked_v = last_user_text(state)
            if any(w in asked_v for w in ("라벨", "컴포넌트", "우선순위 종류", "티켓 타입")) \
                    and any(w in asked_v for w in ("목록", "보여", "정리", "어떤 게", "어떤게",
                                                   "뭐가 있", "리스트", "종류")):
                try:
                    from app.agent import tools as T
                    opts = T.BY_NAME["list_ticket_options"].invoke({"kind": ""}) or {}
                    rows = []
                    for k, label in (("labels", "라벨"), ("components", "컴포넌트"),
                                     ("priorities", "우선순위"), ("taskTypes", "티켓 타입")):
                        vals = opts.get(k) or []
                        if vals:
                            rows.append(f"[{label} 실값 {len(vals)}종] "
                                        + ", ".join(str(x) for x in vals[:40]))
                    if rows:
                        merged = ((state.get("pre_survey") or "") + "\n\n"
                                  + "\n".join(rows)).strip()
                        state = {**state, "pre_survey": merged[:3000]}
                except Exception:
                    pass

            # ── 사전 조사: 웹·GitHub 를 **코드가** 조사해 자료로 준다.
            # 의무 순서를 명령서에 박아도 모델은 사내 티켓을 여는 데 걸음을 다 썼다(실측 3회).
            # 검색어 생성은 모델이 잘하는 일이니 그것만 시키고, 실행은 코드가 보장한다.
            # 트리거 둘: ① 기술 검토형 문구 ② **사내 기록이 빈약한데 기술 용어(영문 토큰)가
            # 있는 요청** — "starrocks iceberg 통계 job 개발"처럼 사내에 없는 신기술 업무는
            # 웹이 알아야 작업 내용을 채울 수 있다(실측: 웹을 안 타서 모듈만 되물었다).
            import re as _re
            asked0 = last_user_text(state)
            wordy = any(w in asked0 for w in ("기술 검토", "방식", "라이브러리", "오픈소스",
                                              "비교", "어떤 기술"))
            thin_internal = "키워드 검색" not in pre        # presurvey 가 사내 티켓을 못 찾았다
            # 제품 내부 module/일반 작업어를 기술명으로 세면 `Workbench ... 추가` 같은
            # 국소 UI 변경도 웹·GitHub 조사로 샌다. 외부 조사는 고유 기술 토큰이 있을 때만
            # 보조 trigger로 삼고, module/티켓 어휘는 제외한다.
            _internal_terms = {"etl", "catalog", "runtime", "workbench", "dataops",
                               "observability", "devops", "epic", "task", "story",
                               "bug", "jira", "ltm", "lake", "manager", "voc"}
            _latin = {x.lower() for x in
                      _re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", asked0)}
            techy = bool(_latin - _internal_terms)
            external_prefetched = any(
                row.get("source") in ("web", "github")
                for row in (state.get("query_results") or []) if isinstance(row, dict))
            if external_prefetched and not state.get("web_context"):
                state = {**state, "web_context": _prefetched_external_context(
                    state.get("query_results") or [])}
            completed_creation_query = ((state.get("intent") or "") == Intent.PLAN_WORK
                                        and completed_query_plan and not typed_write_action)
            if not external_prefetched and (wordy or (thin_internal and techy and not keys0)):
                ctx = _research_outside(self, asked0)
                if ctx:
                    state = {**state, "web_context": ctx}

            # QueryRunner has already done the configured, paginated acquisition and bounded body
            # materialization. For a write-only request, a second semantic pass adds no user-visible
            # deliverable and can erase source identity, so keep the deterministic fast ledger. When the
            # typed RequestPlan also contains an explicit research/summary outcome, however, the Research
            # state is the only evidence contract consumed by ResultIntegrator; synthesize that outcome once
            # rather than silently truncating it to the first 5 Jira/2 document/1 external hits.
            if completed_creation_query:
                if _compound_plan_requires_research_synthesis(state):
                    direct_state = {**state, "_research_analyst_prefetched": True}
                    try:
                        out = self.apply(
                            direct_state,
                            self._synthesize_prefetched_query_plan(direct_state),
                        )
                        out["trace"] = (out.get("trace") or []) + [{
                            "node": self.name, "label": "과거 이력 조사",
                            "note": "완료된 복합 조사·작성 QueryPlan을 1회 의미 정리(도구 재호출 생략)",
                        }]
                        return out
                    except Exception:
                        # Acquisition succeeded; a formatting/provider failure must neither repeat the
                        # searches nor fall back to first-hit truncation. Keep a deterministic, bounded,
                        # cross-source ledger ranked only by the typed research outcome's subject terms.
                        out = self.apply(
                            state,
                            _completed_creation_ledger(state, research_focus=True),
                        )
                        out["trace"] = (out.get("trace") or []) + [{
                            "node": self.name, "label": "과거 이력 조사",
                            "note": "복합 조사 의미 정리 실패 — 관련도 기반 bounded 근거 장부 전달",
                        }]
                        return out

                out = self.apply(state, _completed_creation_ledger(state))
                out["trace"] = (out.get("trace") or []) + [{
                    "node": self.name, "label": "과거 이력 조사",
                    "note": "완료된 QueryPlan deterministic 근거 장부 전달(LLM/ReAct 생략)",
                }]
                return out

            # Legacy callers can still provide an error-free compact result without a formal QueryPlan.
            # Preserve their deterministic zero-hit shortcut, but never turn a scope/config/materialization
            # error into evidence that no duplicate exists.
            legacy_rows = state.get("query_results") or []
            legacy_zero_query = (
                not ((state.get("query_plan") or {}).get("queries") or []) and bool(legacy_rows)
                and all(isinstance(row, dict) and isinstance(row.get("result"), dict)
                        and not row["result"].get("error")
                        and not row["result"].get("materializationErrors")
                        for row in legacy_rows)
            )
            if not typed_write_action \
                    and planned_create_query and (completed_query_plan or legacy_zero_query) \
                    and not _query_results_have_material(state.get("query_results")) \
                    and not state.get("seed_map") \
                    and (not state.get("topic_dossier")
                         or "찾지 못했다" in state.get("topic_dossier", "")):
                out = self.apply(state, {
                    "situation": ("현재 검색 범위에서 요청과 직접 중복되는 사내 티켓·문서를 "
                                  "확인하지 못했다. 신규 초안으로 진행한다."),
                    "evidence": [], "related_docs": [], "already_exists": False,
                })
                out["trace"] = (out.get("trace") or []) + [{
                    "node": self.name, "label": "과거 이력 조사",
                    "note": "QueryPlan 0건 — LLM 요약 생략",
                }]
                return out

            # Query Specialist and Query Runner form the acquisition phase. Once every planned source has
            # completed and Query Runner has opened selected ticket/document bodies, another model-driven
            # search loop repeats the same context without adding evidence. Synthesize exactly once. Any
            # missing/error source is excluded by `_query_plan_is_complete` and retains the ReAct fallback.
            if (state.get("intent") or "") == Intent.ASK and completed_query_plan:
                try:
                    direct_state = {**state, "_research_analyst_prefetched": True}
                    out = self.apply(direct_state, self._conclude(direct_state, []))
                    out["trace"] = (out.get("trace") or []) + [{
                        "node": self.name, "label": "과거 이력 조사",
                        "note": "완료된 QueryPlan 근거 묶음으로 1회 정리(도구 재호출 생략)",
                    }]
                    return out
                except Exception:
                    pass          # 최적화 실패는 기존 ReAct로 복구한다

            # ── L3a 직결: 주제 자료(dossier)를 **코드가 이미 다 모았으면** 걷지 않는다.
            # ReAct 는 "무엇을 열지 모를 때"의 도구다 — 대상 하나의 조각을 코드가 전부
            # 취합한 자산 질의에서 또 걸으면 같은 것을 도구로 재확인하며 3~4호출을 태운다
            # (실측: dossier 경로의 think 가 이미 취합된 티켓을 get_ticket 으로 다시 열었다).
            # conclude 한 번이면 된다 — 재료는 task() 의 자료 블록에 이미 실려 있다.
            # ★ 단, dossier 가 **미발견**("찾지 못했다")이면 직결하지 않는다 — 그 문구로
            # 결론 내리면 다른 도구(허용값 조회 등)로 답할 수 있는 질문까지 '확인 불가'로
            # 끝난다(실측: 라벨 목록 질의가 list_ticket_options 를 못 써 보고 죽었다).
            if state.get("topic_dossier") \
                    and "찾지 못했다" not in state.get("topic_dossier", "") \
                    and "첨부" not in asked_s \
                    and (state.get("intent") or "") == "ask":
                # 첨부 질의를 직결에서 뺀 이유: 파일 **내용** 요약은 read_attachment 를
                # 걸어야 나온다(실측: 직결이 잡아 목록만 답하고 내용은 '없음').
                try:
                    direct_state = {**state, "_research_analyst_prefetched": True}
                    out = self.apply(direct_state, self._conclude(direct_state, []))
                    out["trace"] = (out.get("trace") or []) + [
                        {"node": self.name, "label": "과거 이력 조사",
                         "note": "사전 취합 자료로 바로 정리(조사 생략)"}]
                    return out
                except Exception:
                    pass          # 직결이 죽으면 정상 경로로 — 최적화가 답을 막으면 안 된다

            # ── legacy 생성/계획 직결 ─────────────────────────────────────
            # 정식 완료 QueryPlan은 위 deterministic ledger에서 끝난다. 이전 세션이나 다른 진입점이
            # pre_survey/seed_map/dossier만 전달한 경우에는 기존 1회 structured conclusion을 유지해
            # 호환한다. 이 자료까지 없을 때만 ReAct 탐색으로 내려간다.
            prefetched = bool(state.get("query_results") or state.get("pre_survey")
                              or state.get("seed_map") or state.get("topic_dossier"))
            if (state.get("intent") or "") == Intent.PLAN_WORK \
                    and prefetched and not typed_write_action:
                try:
                    direct_state = {**state, "_research_analyst_prefetched": True}
                    out = self.apply(direct_state, self._conclude(direct_state, []))
                    out["trace"] = (out.get("trace") or []) + [
                        {"node": self.name, "label": "과거 이력 조사",
                         "note": "사전 조회 결과로 바로 정리(도구 재호출 생략)"}]
                    return out
                except Exception as exc:
                    # Acquisition is already complete. A second search loop repeats the
                    # same tools and cannot fix malformed synthesis JSON. Preserve the
                    # compatibility response instead of asserting facts from malformed output.
                    out = self.apply(state, _prefetched_creation_passthrough())
                    out["trace"] = (out.get("trace") or []) + [{
                        "node": self.name, "label": "과거 이력 조사",
                        "note": "요약 형식화 실패 → 사전 조회 원문 전달: " + str(exc)[:120],
                    }]
                    return out

            out = react(state)
            asked = last_user_text(state)
            if not any(w in asked for w in ("진척", "진행률", "현황", "어디까지")):
                return out
            try:
                from app.agent import tools as T
                r = T.BY_NAME["get_progress"].invoke({"target": state.get("module") or ""})
                line = ""
                if r.get("modules"):
                    m = r["modules"][0]
                    tasks = " · ".join(f"'{t.get('task')}' {t.get('donePct')}%"
                                       for t in (m.get("tasks") or [])[:4] if t.get("donePct") is not None)
                    line = f"{m.get('module')} 모듈 진척률 {m.get('donePct')}%"
                    if not state.get("module") and r.get("overallPct") is not None:
                        line = f"전체 {r.get('overallPct')}% · " + line
                    if tasks:
                        line += f" (WBS: {tasks})"
                elif r.get("donePct") is not None:
                    line = f"{r.get('epic')} 진척률 {r.get('donePct')}% ({r.get('children')})"
                elif r.get("overallPct") is not None:
                    line = f"전체 진척률 {r.get('overallPct')}%"
                if line:
                    out["situation"] = ((out.get("situation") or "")
                                        + "\n\n[진척도] " + line).strip()
                    out["trace"] = (out.get("trace") or []) \
                        + note(state, self.name, "진척률 수치 보강")
            except Exception:
                pass                      # 진척률 보강 실패가 조사 결과를 버리게 하면 안 된다
            return out

        return run

    @property
    def tools(self):
        from app.agent.workflow.role_manifest import tools_for_role
        return tools_for_role(self.name)

    def _synthesize_prefetched_query_plan(self, state) -> dict:
        """Synthesize explicit research outcomes once, without an empty tool transcript.

        ``ToolAgent._conclude`` is for a ReAct scratchpad and tells the model to use only that transcript.
        A completed QueryPlan has no scratchpad: its authoritative material is already embedded once in
        ``task(state)``. Keeping a separate path prevents both a contradictory empty-transcript instruction
        and a second copy of the acquired results.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        outcomes = json.dumps(_research_outcome_tasks(state), ensure_ascii=False, default=str)
        return self.invoke_structured(state, [
            SystemMessage(content=self.system(state)),
            HumanMessage(content=(
                self.task(state)
                + "\n\n### User-visible Research Outcome Contract\n\n"
                + outcomes
                + "\n\nAll scoped acquisition is complete. Do not call or propose another tool. "
                  "The outcomes above are user-visible deliverables, not hidden preparation for the write. "
                  "Synthesize their requested comparison, summary, or analysis from Deterministic QueryPlan "
                  "Results. Select at most eight sources by direct claim coverage, retaining exact source "
                  "identity, URL, material observations, dates, conflicts, and limitations. Preserve useful "
                  "internal and external sources when both inform the requested conclusion. Then state only "
                  "the verified context needed by the downstream write; never infer duplicate work from a "
                  "search hit alone."
            )),
        ])

    def system(self, state):
        # 이미 취합된 자료를 한 번 요약하는 직결 경로에는 분류/권한/검색 규칙이 반복된
        # full common prompt가 필요 없다. 역할 계약과 절대 안전 규칙만 담은 lite persona를
        # 사용해 정적 입력도 줄인다. 실제 탐색(ReAct) 경로는 기존 full persona를 유지한다.
        return persona(state, SYSTEM_RESEARCH_ANALYST,
                       lite=bool(state.get("_research_analyst_prefetched")), role_id=self.name)

    def task(self, state):
        kws = ", ".join(state.get("keywords") or []) or last_user_text(state)
        keys = ", ".join(state.get("mentioned_keys") or [])
        query_has_external = any(
            isinstance(row, dict) and row.get("source") in ("web", "github")
            for row in (state.get("query_results") or [])
        ) if isinstance(state.get("query_results"), list) else False
        # Query Runner의 web/github 결과가 이미 아래 JSON에 포함됐으면 같은 snippet을 다시 싣지 않는다.
        # web_context 자체는 downstream Result Integrator가 쓸 수 있게 state에 유지한다.
        web_ctx = "" if query_has_external else (state.get("web_context") or "")
        return f"""\
# Task

Investigate the history related to the work request and establish the verified current situation.

## Constraints

- Ground every claim in an exact ticket key or document title. Write no unsupported sentence.
- Keep one `evidence` item per real ticket, Confluence page, or web document. Put every distinct fact from
  that source in `observations`; use `description`, `comment`, `field`, `document`, `external`, or `query`
  to identify where it was observed. Never create separate evidence items for a ticket body and its comments.
- Set `confidence` from source authority, directness, recency, and corroboration. Set `fitness` from claim
  coverage and internal applicability, and record the decisive `limitations` value. A resolved ticket status is
  workflow metadata, not proof that its DoD or technical result succeeded; require a result body, attachment,
  or comment observation. Compare scope and dates before declaring a conflict. A dated `not yet` record
  followed by later direct completion evidence is normal progression, not an unresolved contradiction.
  Treat a conflict as unresolved only when same-scope contemporary or later sources still disagree; preserve
  both dates and provenance in that case.
- Distinguish ongoing, stopped, and already-decided work. For stopped work, inspect comments for the verified reason.
- Lead with an existing ticket when it already performs materially the same work.
- Do not repeat the same search more than twice with paraphrases. After two empty attempts, treat the in-scope result as empty and spend remaining steps opening a promising ticket through `get_ticket` or supplementing named public technology through `search_web`.
- Write natural-language schema fields in Korean while preserving identifiers and source titles exactly.

## Request Data

Retrieval keywords: {kws}

Explicit ticket keys: {keys or 'none'}

Inferred module: {state.get('module') or 'unknown'}

Original request: {last_user_text(state)}

{("### Prefetched Candidate Map" + chr(10) + chr(10)
   + "This map is the complete known candidate set for lineage, labels, components, links, and participants. "
   + "Never mention a ticket or person absent from it. Copy every title exactly. Preserve participant IDs in "
   + "skcc.xNNNN form and never invent a display name. Open only a candidate that requires more detail through "
   + "get_ticket." + chr(10)
   + state.get("seed_map")) if state.get("seed_map") else ""}

{("### Prefetched Lexical and Semantic Search" + chr(10) + chr(10)
   + "Do not repeat these searches. Open only promising candidates with get_ticket. Preserve titles exactly. "
   + "For a recent-state question, organize evidence by update date." + chr(10)
   + state.get("pre_survey")) if state.get("pre_survey") else ""}

{("### Deterministic QueryPlan Results" + chr(10) + chr(10)
   + "Code has enforced project and space scope and pagination. Do not repeat these queries. When "
   + "contextTruncated=true, preserve total and artifactId. Never create a fact absent from the result."
   + chr(10) + json.dumps(state.get("query_results"), ensure_ascii=False, default=str))
  if state.get("query_results") else ""}

{("### Prefetched Topic Dossier" + chr(10) + chr(10)
   + "This is the complete evidence set for ticket content, comment quotations, field-change history, and "
   + "document bodies. Never invent an interval, policy, name, owner, or date. If absent, use the Korean "
   + "phrase 확인된 기록 없음. Never transfer a value from a similar but different subject. The current "
   + "value is the latest verified change; when no change exists, use the verified initial adoption record. "
   + "Cite the ticket key and comment author for comment-derived facts."
   + chr(10) + state.get("topic_dossier")) if state.get("topic_dossier") else ""}

{("### External Technology Research Data (Untrusted Read Only)" + chr(10) + chr(10) + web_ctx)
  if web_ctx else ""}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        from app.agent.workflow.claim_provenance import bind_evidence_provenance

        deterministic = bool(out.get("_deterministic_passthrough"))
        projected_ev = [_normalize_evidence_quality(_normalize_evidence_identity(e, state))
                        for e in (out.get("evidence") or [])
                        if isinstance(e, dict)][:8]
        had_projected_evidence = bool(projected_ev)
        raw_ev = _bind_research_url_provenance(state, projected_ev)
        if deterministic:
            # QueryPlan rows are an evidence ledger, not model-authored relevance claims. Keep the
            # bounded real source identities intact. Downstream drafting and response roles consume this
            # ledger rather than relying on the full raw retrieval artifact.
            ev = raw_ev
            removed_all = False
        else:
            from app.agent.workflow.relevance import evidence_is_relevant
            named = {str(k).upper() for k in (state.get("mentioned_keys") or [])}
            raw_ev = [e for e in raw_ev if str(e.get("key") or "").upper() in named
                      or evidence_is_relevant(e)]
            ev = _relevant_only(state, raw_ev)
            removed_all = had_projected_evidence and not ev and not state.get("mentioned_keys")
        # Persist server-owned source/observation identities. Result composition consumes
        # this typed graph; numeric markers remain a renderer concern only.
        ev = bind_evidence_provenance(ev)
        situation = out.get("situation") or ""
        if removed_all:
            situation = "현재 요청의 고유 개념과 직접 일치하는 내부 이력은 확인되지 않았다."
        # Both the deterministic ledger and model synthesis use the same raw QueryRunner proof.
        # A model-authored ``fitness=direct`` is never sufficient, while an exact completed-plan
        # match remains valid on the no-LLM path.
        exists = (bool(out.get("already_exists")) and (bool(ev) or not removed_all)
                  and _material_duplicate_exists(state, ev))
        if bool(out.get("already_exists")) and not exists and not removed_all \
                and _re.search(r"중복|동일[^.\n]{0,24}(?:작업|구현|진행)|이미\s*(?:있|진행)",
                               situation, _re.I):
            situation = (
                "조회 후보는 확인했지만 원본 제목·티켓 유형·진행 상태·본문을 교차 확인해도 "
                "정확히 같은 업무가 입증되지 않았다. 후보 근거는 신규 초안의 참고 자료로 전달한다."
            )
        result = {
            "situation": situation,
            "evidence": ev,
            "related_docs": _bind_research_url_provenance(
                state,
                [d for d in (out.get("related_docs") or []) if isinstance(d, dict)][:6],
            ),
            "epic_candidate": (out.get("epic_candidate") or "").strip(),
            "already_exists": exists,
            # 사전 취합 자료를 **State 에 올린다** — 여태 node() 안 지역 사본이라 다음 역할
            # (KnowledgeCurator·ResultIntegrator)의 자료 블록이 늘 비어 있었다. 결론 문장만으로는 조각의
            # 출처(코멘트 작성자·변경 일자)가 사라진다.
            "pre_survey": state.get("pre_survey") or "",
            "web_context": state.get("web_context") or "",
            "topic_dossier": state.get("topic_dossier") or "",
            "bulk_targets": state.get("bulk_targets") or [],
            "trace": note(state, self.name,
                          f"근거 {len(ev)}건" + (" · 정확 중복 open 티켓 있음" if exists else "")),
        }
        # 회의록의 모호한 사람·내부 약어는 일반적인 조사 공백과 다르다. 내부/외부 조사가
        # 끝난 이 시점에도 확정되지 않은 값만 한 번에 인터뷰하고, 답을 받기 전에는 요약·
        # 댓글·티켓 초안을 만들지 않는다.
        from app.agent.workflow.meeting_context import unresolved_questions
        meeting_questions = unresolved_questions({**state, **result})
        if meeting_questions:
            result["questions"] = meeting_questions
            result["trace"] = (result.get("trace") or []) + note(
                state, self.name, f"회의록 미확정 값 {len(meeting_questions)}건 인터뷰")
        return result


__all__ = ["ResearchAnalyst", "_prefetched_external_context", "_query_results_have_material",
           "_query_plan_is_complete", "_exact_creation_duplicate_key", "_relevant_only"]
