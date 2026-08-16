"""Query Specialist — 복합 요청의 atomic task를 typed read plan으로 변환한다."""

from __future__ import annotations

import json
import re

from app.agent.prompts.roles import SYSTEM_QUERY_SPECIALIST
from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.contracts import QueryPlan
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import (AgentState, Intent, Node, conversation,
                                      last_user_text, note, request_text)


_EXTERNAL_WORDS = ("외부 조사", "외부 검색", "외부 자료", "웹 검색", "인터넷", "github", "오픈소스",
                   "시장 사례", "업계 사례", "기술 비교", "리서치", "논문", "공식 문서")
_INTERNAL_LATIN = {"etl", "catalog", "runtime", "workbench", "dataops", "observability",
                   "devops", "epic", "task", "story", "bug", "jira", "ltm", "lake",
                   "manager", "api", "ui", "sub-task", "subtask", "feature", "improvement",
                   "point", "batch", "job", "sql", "jql", "cql", "json", "html", "pmo",
                   "voc", "our", "project", "internal", "external", "official", "documentation",
                   "confluence", "wiki", "marker", "citation",
                   "hotfix", "poc", "p0", "p1", "p2", "p3", "p4", "critical", "major", "minor"}

_PRIVATE_EXTERNAL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*-\d+(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])skcc\.[a-z]\d+(?![A-Za-z0-9])|https?://\S+|"
    r"(?<![A-Za-z0-9_.])[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*(?![A-Za-z0-9_.])|"
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+(?![A-Za-z0-9])",
    re.I,
)

_COMMENT_WORDS = ("댓글", "코멘트", "comment")
_COMMENT_SCOPE_IGNORED = {
    "jira", "ticket", "task", "comment", "confluence", "official", "documentation",
    "운영", "적용", "여부", "조사", "근거", "문서", "티켓", "댓글", "코멘트",
}

_QUERY_IDENTITY_NOISE = {
    "official", "documentation", "document", "docs", "confluence", "wiki",
    "jira", "ticket", "tickets", "comment", "comments", "marker",
}


def _known_user_tokens() -> set[str]:
    """Return configured user IDs and their shorthand suffixes.

    Meeting/create requests often contain ``skcc.x1402`` or just ``x1402``.  The latter
    used to look like a public product name to the web-query sanitizer and once produced
    an ASUS laptop search.  The people roster is the authority: only configured IDs are
    treated as identities, so legitimate public tokens that merely resemble an ID are not
    removed globally.
    """
    try:
        from app.infra.settings import load_people
        ids = {str(uid).strip().casefold()
               for values in (load_people() or {}).values() for uid in (values or [])
               if str(uid).strip()}
    except Exception:
        ids = set()
    return ids | {uid.split(".", 1)[1] for uid in ids if "." in uid}


def _strip_known_user_tokens(text: str) -> str:
    value = str(text or "")
    for token in sorted(_known_user_tokens(), key=len, reverse=True):
        # A sentence-ending period is punctuation, not part of ``x1042``.  Keep dots
        # protected on the left for qualified IDs, but allow punctuation on the right.
        value = re.sub(rf"(?<![A-Za-z0-9_.]){re.escape(token)}(?![A-Za-z0-9_])",
                       " ", value, flags=re.I)
    return value


def _contains_known_user_token(text: str) -> bool:
    return _strip_known_user_tokens(text) != str(text or "")


def _jira_query_is_only_people(query: dict) -> bool:
    """Reject model-written ``issueKey=x1402`` queries made from assignee IDs."""
    if str(query.get("source") or "") != "jira":
        return False
    text = " ".join(str(query.get(key) or "") for key in ("query", "where"))
    if re.search(r"\b[A-Z][A-Z0-9]*-\d+\b", text):
        return False
    values = re.findall(r"(?i)\b(?:issue)?key\s*(?:=|in\s*\()?\s*['\"]?([a-z][a-z0-9.]*)", text)
    return bool(values) and all(value.casefold() in _known_user_tokens() for value in values)


def _normalize_model_jira_query(query: dict) -> bool:
    """Repair planner confusion between Jira read scope and a requested subject.

    Every configured ``search.jira.projects`` value is already applied by the runtime.
    A model sometimes writes a Korean feature title into ``project = ...`` or emits a
    mutation phrase such as ``create issue`` as a read query.  Preserve the former as a
    summary search and reject the latter before it reaches Jira.
    """
    if str(query.get("source") or "") != "jira":
        return True
    raw_query = str(query.get("query") or "").strip()
    raw_where = str(query.get("where") or "").strip()
    combined = " ".join(value for value in (raw_query, raw_where) if value)
    if re.search(r"\{[^}\n]+\}", combined):
        return False
    if re.fullmatch(
            r"(?i)\s*(?:create|make|add|update|modify|delete)\s+(?:an?\s+)?(?:issue|ticket|task)\s*",
            combined):
        return False

    try:
        from app.agent.tools._ctx import search_projects
        allowed = {str(value).strip().casefold()
                   for value in search_projects() if str(value).strip()}
    except Exception:
        allowed = set()

    def replace_project(match: re.Match) -> str:
        value = (match.group("quoted") or match.group("bare") or "").strip()
        if not value or value.casefold() in allowed or value == "YOUR_PROJECT_KEY":
            return ""
        escaped = value.replace('"', '')
        field = "summary" if re.search(r"\s|[가-힣]", escaped) else "text"
        return f'{field} ~ "{escaped}"'

    project_clause = re.compile(
        r"(?i)\bproject\s*=\s*(?:[\"'](?P<quoted>[^\"']+)[\"']|(?P<bare>[A-Za-z0-9_.-]+))")
    for key in ("query", "where"):
        value = project_clause.sub(replace_project, str(query.get(key) or ""))
        value = re.sub(r"(?i)^\s*(?:AND|OR)\s+|\s+(?:AND|OR)\s*$", "", value).strip()
        value = re.sub(r"(?i)\s+AND\s+AND\s+", " AND ", value)
        query[key] = value
    return bool(str(query.get("query") or "").strip() or str(query.get("where") or "").strip())


def _normalize_query_fields(query: dict) -> None:
    """Keep only Jira field identifiers that the runtime can actually project."""
    if str(query.get("source") or "") != "jira":
        return
    query["fields"] = list(dict.fromkeys(
        str(field).strip() for field in (query.get("fields") or [])
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", str(field).strip())
    ))


def _dedupe_equivalent_queries(plan: dict) -> None:
    """Collapse identical retrievals and repair dependency IDs deterministically."""
    # If the planner emitted both a broad summary search and the same search scoped to
    # Epic, keep the narrower read. Status/assignee constraints are not collapsed.
    narrowed: list[dict] = []
    subject_rows: dict[str, dict] = {}
    redirect: dict[str, str] = {}

    def jira_summary_only(query: dict) -> str:
        if str(query.get("source") or "") != "jira":
            return ""
        value = " AND ".join(str(query.get(key) or "").strip()
                             for key in ("query", "where")
                             if str(query.get(key) or "").strip())
        matches = re.findall(r"(?i)summary\s*~\s*[\"']([^\"']+)[\"']", value)
        if len(matches) != 1:
            return ""
        remainder = re.sub(r"(?i)summary\s*~\s*[\"'][^\"']+[\"']", "", value)
        remainder = re.sub(r"(?i)issueType\s*=\s*Epic", "", remainder)
        remainder = re.sub(r"(?i)\bAND\b|[()\s]", "", remainder)
        return matches[0].casefold() if not remainder else ""

    for query in plan.get("queries") or []:
        subject = jira_summary_only(query)
        previous = subject_rows.get(subject) if subject else None
        if previous is None:
            if subject:
                subject_rows[subject] = query
            narrowed.append(query)
            continue
        previous_text = " ".join(str(previous.get(key) or "") for key in ("query", "where"))
        current_text = " ".join(str(query.get(key) or "") for key in ("query", "where"))
        previous_score = int(bool(re.search(r"(?i)issueType\s*=\s*Epic", previous_text)))
        current_score = int(bool(re.search(r"(?i)issueType\s*=\s*Epic", current_text)))
        if current_score > previous_score:
            narrowed[narrowed.index(previous)] = query
            subject_rows[subject] = query
            old_id, keep_id = str(previous.get("id") or ""), str(query.get("id") or "")
        else:
            old_id, keep_id = str(query.get("id") or ""), str(previous.get("id") or "")
        if old_id and keep_id:
            redirect[old_id] = keep_id
    plan["queries"] = narrowed

    kept: list[dict] = []
    by_signature: dict[tuple[str, str, str], dict] = {}
    for query in plan.get("queries") or []:
        signature = (
            str(query.get("source") or "").casefold(),
            " ".join(str(query.get("query") or "").casefold().split()),
            " ".join(str(query.get("where") or "").casefold().split()),
        )
        existing = by_signature.get(signature)
        if existing is None or (not signature[1] and not signature[2]):
            by_signature[signature] = query
            kept.append(query)
            continue
        old_id, keep_id = str(query.get("id") or ""), str(existing.get("id") or "")
        if old_id and keep_id:
            redirect[old_id] = keep_id
        existing["fields"] = list(dict.fromkeys(
            [*(existing.get("fields") or []), *(query.get("fields") or [])]))
        existing["page_size"] = max(int(existing.get("page_size") or 1),
                                    int(query.get("page_size") or 1))
        if query.get("completeness") == "all":
            existing["completeness"] = "all"
    ids = {str(query.get("id") or "") for query in kept}
    for query in kept:
        deps = []
        for dep in query.get("depends_on") or []:
            value = str(dep)
            seen = set()
            while value in redirect and value not in seen:
                seen.add(value)
                value = redirect[value]
            if value in ids and value != str(query.get("id") or "") and value not in deps:
                deps.append(value)
        query["depends_on"] = deps
    plan["queries"] = kept


def _safe_model_external_query(query: str) -> str:
    """Accept an LLM-produced canonical English query only when it is safe to send publicly.

    Query Specialist is already the language-understanding step, so it can map a Korean rendering such as
    ``아파치 아이스버그`` to its canonical spelling. Runtime still owns the privacy boundary: internal ticket
    keys, user IDs, URLs, code/table/parameter identifiers, and untranslated Korean text never leave LTM.
    """
    original = " ".join(str(query or "").split())
    raw = " ".join(_strip_known_user_tokens(original).split())
    if (not raw or _contains_known_user_token(original)
            or _PRIVATE_EXTERNAL_PATTERN.search(raw)
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
    tokens = re.findall(r"[a-z0-9.+-]+", str(query or "").lower())
    return " ".join(token for token in tokens if token not in _QUERY_IDENTITY_NOISE)


def _public_external_query(text: str) -> str:
    """Build a non-identifying public technology query without another LLM call.

    External retrieval is deterministic once the request has already named public technology. Keeping this
    in code both prevents internal identifiers from leaving LTM and removes the former query-generation model
    round trip. Korean-only internal subjects intentionally return empty instead of being leaked or guessed.
    """
    scrubbed = _PRIVATE_EXTERNAL_PATTERN.sub(" ", _strip_known_user_tokens(text))
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
    scrubbed = _PRIVATE_EXTERNAL_PATTERN.sub(" ", _strip_known_user_tokens(text))
    latin = {x.lower().strip("._+-")
             for x in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", scrubbed)}
    return bool(latin - _INTERNAL_LATIN)


def _comment_scope_where(state, jira_query: dict) -> str:
    """Select relevant tickets before reading comments, without exact-phrase loss."""
    keys = [str(key).upper() for key in (state.get("mentioned_keys") or [])
            if re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", str(key).upper())]
    if keys:
        return "key in (" + ", ".join(keys) + ")"
    raw = " ".join(str(value or "") for value in (
        jira_query.get("query"), *(state.get("keywords") or [])))
    terms = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{2,}|[가-힣]{2,}", raw):
        if token.casefold() in _COMMENT_SCOPE_IGNORED or token.casefold() in _INTERNAL_LATIN:
            continue
        if token.casefold() not in {term.casefold() for term in terms}:
            terms.append(token)
    return f'text ~ "{terms[0].replace(chr(34), "")}"' if terms else ""


def _ensure_explicit_comment_query(state, plan: dict) -> None:
    """Keep an explicit Jira-comment source requirement even when the model omitted it."""
    asked = (request_text(state) + " " + conversation(state)).casefold()
    if not any(word in asked for word in _COMMENT_WORDS):
        return
    if any(query.get("source") == "comments" for query in plan.get("queries") or []):
        return
    jira = next((query for query in plan.get("queries") or []
                 if query.get("source") == "jira"), None)
    if not jira:
        return
    where = _comment_scope_where(state, jira)
    if not where:
        plan.setdefault("uncertainty", []).append(
            "댓글 근거가 요청됐지만 안전한 티켓 후보 조건을 만들 수 없음")
        return
    used = {str(query.get("id") or "") for query in plan.get("queries") or []}
    qid = "comments-for-" + str(jira.get("id") or "topic")
    while qid in used:
        qid += "-2"
    companion = {
        "id": qid, "source": "comments", "query": "", "where": where,
        "order_by": "updated DESC", "fields": [],
        "completeness": jira.get("completeness") or "all",
        "page_size": min(int(jira.get("page_size") or 25), 25), "depends_on": [],
    }
    position = plan["queries"].index(jira) + 1
    plan["queries"].insert(position, companion)


def _ensure_creation_duplicate_query(state, plan: dict) -> None:
    """Every create plan checks scoped Jira history before proposing a new ticket.

    Public technology lookup is useful background, but it cannot answer whether the work
    already exists in this organization.  The planner occasionally emitted only a web query
    for a named technology (DUP1: Avro), bypassing the duplicate guard entirely.  Add one
    internal, paginated read when no Jira source exists; configured projects remain enforced
    by ``run_jql_v2``.
    """
    if (state.get("intent") or "") != Intent.PLAN_WORK:
        return
    if any(query.get("source") == "jira" for query in plan.get("queries") or []):
        return
    ignored = {
        "작업", "티켓", "생성", "추가", "신규", "만들자", "만들어", "요청",
        "기능", "개선", "알아서", "task", "ticket", "create", "issue",
    }
    terms = []
    material = [str(value).strip() for value in (state.get("keywords") or [])
                if str(value).strip()]
    if not material:
        material = re.findall(
            r"[A-Za-z][A-Za-z0-9_.+-]{2,}|[가-힣]{2,}",
            request_text(state) or last_user_text(state),
        )
    for value in material:
        clean = re.sub(r"[\"'`]+", "", value).strip()
        if not clean or clean.casefold() in ignored or clean.casefold() in _INTERNAL_LATIN:
            continue
        if clean.casefold() not in {term.casefold() for term in terms}:
            terms.append(clean)
    if not terms:
        return
    plan.setdefault("queries", []).insert(0, {
        "id": "internal-duplicate-check",
        "source": "jira",
        "query": " ".join(terms[:5]),
        "where": "",
        "order_by": "updated DESC",
        "fields": ["key", "summary", "status", "issuetype", "assignee", "updated"],
        "completeness": "all",
        "page_size": 50,
        "depends_on": [],
    })


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
        # Source coverage is a user contract, not a model preference.
        _ensure_creation_duplicate_query(state, plan)
        _ensure_explicit_comment_query(state, plan)
        # 빈 comment query + 빈 JQL은 "모든 댓글"이라는 위험한 의미가 된다. 회의록 한 건에서
        # 2천여 댓글을 읽은 실측이 있었고, 관련성도 비용도 망가졌다. 대상/검색어가 하나도
        # 없으면 실행하지 않고 계획의 불확실성으로 남긴다.
        kept = []
        dropped_blank_comments = False
        for query in plan["queries"]:
            if _jira_query_is_only_people(query):
                plan.setdefault("uncertainty", []).append(
                    "담당자 ID를 티켓 키로 해석한 Jira 조회는 실행하지 않음")
                continue
            if not _normalize_model_jira_query(query):
                continue
            _normalize_query_fields(query)
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
        _dedupe_equivalent_queries(plan)
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
           "_safe_model_external_query", "_ensure_explicit_comment_query",
           "_known_user_tokens", "_strip_known_user_tokens", "_jira_query_is_only_people",
           "_normalize_model_jira_query", "_normalize_query_fields",
           "_dedupe_equivalent_queries", "_ensure_creation_duplicate_query"]
