"""Query Specialist — 복합 요청의 atomic task를 typed read plan으로 변환한다."""

from __future__ import annotations

import json
import re

from app.agent.prompts.roles import SYSTEM_QUERY_SPECIALIST
from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.contracts import QueryPlan
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Node, conversation, note, request_text


_EXTERNAL_WORDS = ("외부 조사", "외부 검색", "외부 자료", "웹 검색", "인터넷", "github", "오픈소스",
                   "시장 사례", "업계 사례", "기술 비교", "리서치", "논문", "공식 문서")
_INTERNAL_LATIN = {"etl", "catalog", "runtime", "workbench", "dataops", "observability",
                   "devops", "epic", "task", "story", "bug", "jira", "ltm", "lake",
                   "manager", "api", "ui", "sub-task", "subtask", "feature", "improvement",
                   "point", "batch", "job", "sql", "jql", "cql", "json", "html", "pmo",
                   "voc", "our", "project", "internal", "external", "official", "documentation",
                   "hotfix", "poc", "p0", "p1", "p2", "p3", "p4", "critical", "major", "minor"}

_PRIVATE_EXTERNAL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*-\d+(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])skcc\.[a-z]\d+(?![A-Za-z0-9])|https?://\S+|"
    r"(?<![A-Za-z0-9_.])[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*(?![A-Za-z0-9_.])|"
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+(?![A-Za-z0-9])",
    re.I,
)


def _safe_model_external_query(query: str) -> str:
    """Accept an LLM-produced canonical English query only when it is safe to send publicly.

    Query Specialist is already the language-understanding step, so it can map a Korean rendering such as
    ``아파치 아이스버그`` to its canonical spelling. Runtime still owns the privacy boundary: internal ticket
    keys, user IDs, URLs, code/table/parameter identifiers, and untranslated Korean text never leave LTM.
    """
    raw = " ".join(str(query or "").split())
    if (not raw or _PRIVATE_EXTERNAL_PATTERN.search(raw)
            or re.search(r"[가-힣]", raw)):
        return ""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9.+-]{1,}", raw)
    meaningful = [token for token in tokens
                  if token.lower().strip(".+-") not in _INTERNAL_LATIN]
    if not meaningful:
        return ""
    safe = " ".join(tokens[:12])
    if "official" not in safe.lower():
        safe += " official documentation"
    return safe


def _query_identity(query: str) -> str:
    return " ".join(re.findall(r"[a-z0-9.+-]+", str(query or "").lower()))


def _public_external_query(text: str) -> str:
    """Build a non-identifying public technology query without another LLM call.

    External retrieval is deterministic once the request has already named public technology. Keeping this
    in code both prevents internal identifiers from leaving LTM and removes the former query-generation model
    round trip. Korean-only internal subjects intentionally return empty instead of being leaked or guessed.
    """
    scrubbed = _PRIVATE_EXTERNAL_PATTERN.sub(" ", str(text or ""))
    tokens, seen = [], set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{2,}", scrubbed):
        low = token.lower().strip("._+-")
        if low in _INTERNAL_LATIN or low.startswith("customfield_"):
            continue
        if low not in seen:
            seen.add(low)
            tokens.append(token)
    if not tokens:
        return ""
    return " ".join(tokens[:8]) + " official documentation"


def _external_research_allowed(state) -> bool:
    """일반 사내 ticket 작업에 임의 웹 검색을 붙이지 않는다.

    사용자가 외부 조사를 말했거나 CDC/StarRocks처럼 내부 module명이 아닌 고유 기술 토큰을
    요청에 쓴 경우만 허용한다. ticket key/user id/URL은 기술 토큰으로 세지 않는다.
    """
    text = (request_text(state) + " " + conversation(state)).strip()
    low = text.lower()
    if any(w in low for w in _EXTERNAL_WORDS):
        return True
    scrubbed = _PRIVATE_EXTERNAL_PATTERN.sub(" ", text)
    latin = {x.lower() for x in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", scrubbed)}
    return bool(latin - _INTERNAL_LATIN)


class QuerySpecialist(StructuredAgent):
    name = Node.QUERY_SPECIALIST
    temperature = 0.0
    tier = "simple"

    def system(self, state):
        return persona(state, SYSTEM_QUERY_SPECIALIST, lite=True)

    def task(self, state):
        return (
            "# Task\n\nConvert Request Architect's plan into an executable QueryPlan. "
            "Do not answer the user or recommend an action.\n\n"
            "## Request Plan Data\n\n" + json.dumps(state.get("request_plan") or {}, ensure_ascii=False)
            + "\n\n## Retrieval Keywords\n\n" + json.dumps(state.get("keywords") or [], ensure_ascii=False)
            + "\n\n## Explicit Ticket Keys\n\n" + json.dumps(state.get("mentioned_keys") or [], ensure_ascii=False)
            + "\n\n## Recent Conversation Data\n\n" + conversation(state)
        )

    def schema(self):
        return QueryPlan.model_json_schema()

    def apply(self, state, out):
        plan = QueryPlan.model_validate(out).model_dump()
        # 빈 comment query + 빈 JQL은 "모든 댓글"이라는 위험한 의미가 된다. 회의록 한 건에서
        # 2천여 댓글을 읽은 실측이 있었고, 관련성도 비용도 망가졌다. 대상/검색어가 하나도
        # 없으면 실행하지 않고 계획의 불확실성으로 남긴다.
        kept = []
        dropped_blank_comments = False
        for query in plan["queries"]:
            if query.get("source") == "comments" \
                    and not str(query.get("query") or "").strip() \
                    and not str(query.get("where") or "").strip():
                dropped_blank_comments = True
                continue
            kept.append(query)
        plan["queries"] = kept
        if dropped_blank_comments:
            plan.setdefault("uncertainty", []).append(
                "검색어와 티켓 조건이 모두 빈 댓글 전수조회는 실행하지 않음")
        external = _external_research_allowed(state)
        if not external:
            plan["queries"] = [q for q in plan["queries"]
                               if q.get("source") not in ("web", "github")]
        else:
            public_query = _public_external_query(
                (request_text(state) + " " + conversation(state)).strip())
            web = [q for q in plan["queries"] if q.get("source") == "web"]
            candidates = [public_query]
            original_terms = set(_query_identity(public_query).split()) - {
                "official", "documentation"}
            for query in web:
                translated = _safe_model_external_query(query.get("query") or "")
                translated_terms = set(_query_identity(translated).split()) - {
                    "official", "documentation"}
                # If an original public spelling is available, the canonical/translated query must remain
                # anchored to it. This rejects an unrelated model-generated web query.
                if translated and (not original_terms or original_terms & translated_terms):
                    candidates.append(translated)
            variants = []
            for candidate in candidates:
                if candidate and _query_identity(candidate) not in {
                        _query_identity(existing) for existing in variants}:
                    variants.append(candidate)
            # One exact/original-spelling query plus one canonical-English alias is enough. More variants add
            # latency and duplicate evidence without materially improving recall.
            variants = variants[:2]
            plan["queries"] = [q for q in plan["queries"] if q.get("source") != "web"]
            template = web[0] if web else {
                "id": "external-official", "source": "web", "where": "",
                "order_by": "updated DESC", "fields": [], "completeness": "page",
                "page_size": 5, "depends_on": [],
            }
            used_ids = {q.get("id") for q in plan["queries"]}
            for index, candidate in enumerate(variants):
                query = dict(template)
                wanted = str(template.get("id") or "external-official") if index == 0 \
                    else "external-official-alias"
                while wanted in used_ids:
                    wanted += "-2"
                used_ids.add(wanted)
                query.update({"id": wanted, "source": "web", "query": candidate, "where": ""})
                plan["queries"].append(query)
            if not variants:
                plan.setdefault("uncertainty", []).append(
                    "External research was requested, but no privacy-safe canonical technology name was available.")
        return {"query_plan": plan,
                "trace": note(state, self.name, f"조회 {len(plan['queries'])}개 설계")}


__all__ = ["QuerySpecialist", "_external_research_allowed", "_public_external_query",
           "_safe_model_external_query"]
