"""Query Specialist — 복합 요청의 atomic task를 typed read plan으로 변환한다."""

from __future__ import annotations

import json
import re

from app.agent.prompts.roles import SYSTEM_QUERY_SPECIALIST
from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.contracts import QueryPlan
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Node, conversation, note, request_text


_EXTERNAL_WORDS = ("외부 조사", "외부 자료", "웹 검색", "인터넷", "github", "오픈소스",
                   "시장 사례", "업계 사례", "기술 비교", "리서치", "논문", "공식 문서")
_INTERNAL_LATIN = {"etl", "catalog", "runtime", "workbench", "dataops", "observability",
                   "devops", "epic", "task", "story", "bug", "jira", "ltm", "lake",
                   "manager", "api", "ui", "sub-task", "subtask", "feature", "improvement",
                   "point", "batch", "job", "sql", "jql", "cql", "json", "html", "pmo",
                   "voc", "our", "project", "internal", "external", "official", "documentation",
                   "hotfix", "poc", "p0", "p1", "p2", "p3", "p4", "critical", "major", "minor"}


def _public_external_query(text: str) -> str:
    """Build a non-identifying public technology query without another LLM call.

    External retrieval is deterministic once the request has already named public technology. Keeping this
    in code both prevents internal identifiers from leaving LTM and removes the former query-generation model
    round trip. Korean-only internal subjects intentionally return empty instead of being leaked or guessed.
    """
    scrubbed = re.sub(
        r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*-\d+(?![A-Za-z0-9])|"
        r"(?<![A-Za-z0-9])skcc\.[a-z]\d+(?![A-Za-z0-9])|https?://\S+",
        " ", str(text or ""), flags=re.I,
    )
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
    scrubbed = re.sub(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*-\d+(?![A-Za-z0-9])|"
                      r"(?<![A-Za-z0-9])skcc\.[a-z]\d+(?![A-Za-z0-9])|https?://\S+", " ", text,
                      flags=re.I)
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
        external = _external_research_allowed(state)
        if not external:
            plan["queries"] = [q for q in plan["queries"]
                               if q.get("source") not in ("web", "github")]
        else:
            public_query = _public_external_query(
                (request_text(state) + " " + conversation(state)).strip())
            if public_query:
                web = [q for q in plan["queries"] if q.get("source") == "web"]
                if web:
                    # The model chooses whether web is useful; code owns the privacy-safe query literal.
                    for query in web:
                        query["query"] = public_query
                        query["where"] = ""
                else:
                    plan["queries"].append({
                        "id": "external-official",
                        "source": "web",
                        "query": public_query,
                        "where": "",
                        "order_by": "updated DESC",
                        "fields": [],
                        "completeness": "page",
                        "page_size": 5,
                        "depends_on": [],
                    })
        return {"query_plan": plan,
                "trace": note(state, self.name, f"조회 {len(plan['queries'])}개 설계")}


__all__ = ["QuerySpecialist", "_external_research_allowed", "_public_external_query"]
