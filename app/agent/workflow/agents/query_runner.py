"""Query Runner — QueryPlan을 LLM 없이 등록된 read-only 도구로 실행한다."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import unquote, urlsplit

from app.agent.pagination import PaginationAccumulator
from app.agent.workflow.state import Node, last_user_text, note, request_text


_LEXICAL_IGNORED = {
    "task", "ticket", "jira", "작업", "티켓", "이력", "조회", "검색",
    "위한", "위해", "관련", "새로", "만들자", "만들기", "생성", "생성한다",
}

_PROJECTION_NOISE = {
    "about", "all", "and", "comment", "comments", "create", "detail", "find", "for",
    "from", "issue", "jira", "meeting", "official", "please", "research", "search",
    "task", "ticket", "with", "검토", "검색", "공식", "근거", "댓글", "만들어", "생성",
    "요청", "작업", "조사", "태스크", "티켓", "회의록", "확인",
}


def _projection_focus_terms(value: str) -> list[str]:
    """Extract bounded literal anchors used only to choose excerpts from opened evidence."""
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9._+-]{2,}|[가-힣]{2,}", str(value or "")):
        folded = token.casefold().strip("._+-")
        if folded in _PROJECTION_NOISE or folded in {term.casefold() for term in terms}:
            continue
        terms.append(token)
    return terms[:16]


def _plain_projection_text(value, limit: int, focus_terms=()) -> str:
    """Return a readable, focus-aware excerpt with an absolute character budget."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(str(value or "")))).strip()
    bound = max(0, int(limit or 0))
    if len(text) <= bound:
        return text
    if bound <= 1:
        return text[:bound]
    folded = text.casefold()
    positions = [folded.find(str(term).casefold()) for term in focus_terms if str(term).strip()]
    positions = [position for position in positions if position >= 0]
    # Keep a small leading observation for context and reserve most of the budget for the
    # requested identifier/technology when it occurs later in a long body.
    if positions and min(positions) > bound // 2:
        head = max(40, bound // 4)
        remaining = bound - head - 3
        start = max(0, min(positions) - remaining // 3)
        tail = text[start:start + remaining].strip()
        return (text[:head].rstrip() + " … " + tail)[:bound].rstrip()
    return (text[:bound - 1].rstrip() + "…")[:bound]


def _scalar_projection(value, limit: int = 240) -> str:
    if isinstance(value, dict):
        value = (value.get("displayName") or value.get("name") or value.get("key")
                 or value.get("value") or "")
    return _plain_projection_text(value, limit)


def _project_comment_observation(row: dict, focus_terms=()) -> dict:
    """Project one Jira comment/search hit without carrying its full rich-text payload."""
    if not isinstance(row, dict):
        return {}
    out: dict = {}
    for field, limit in (
        ("ticketKey", 40), ("key", 40), ("ticketSummary", 240), ("summary", 240),
        ("author", 100), ("created", 40), ("date", 40), ("updated", 40), ("url", 500),
    ):
        if row.get(field) not in (None, ""):
            out[field] = _scalar_projection(row[field], limit)
    for field in ("body", "text", "html", "snippet"):
        if row.get(field) not in (None, ""):
            # Normalize every rich-text variant into the conventional body field. Existing
            # consumers already read body first and no raw HTML should enter an LLM prompt.
            out["body"] = _plain_projection_text(row[field], 180, focus_terms)
            break
    return out


def _project_ticket_detail(row: dict, focus_terms=()) -> dict:
    """Typed ticket projection for model input and cross-turn continuation.

    Full ``get_ticket`` output stays in ``query_artifacts``. This row contains only stable
    identity/hierarchy fields plus bounded description and at most two relevant comments.
    """
    if not isinstance(row, dict):
        return {}
    out: dict = {}
    for field, limit in (
        ("key", 40), ("type", 80), ("issuetype", 80), ("status", 100),
        ("summary", 240), ("title", 240),
        ("parentKey", 40), ("epicKey", 40), ("assignee", 100), ("priority", 80),
        ("duedate", 40), ("created", 40), ("updated", 40), ("resolution", 80),
        ("url", 500), ("self", 500), ("error", 240),
    ):
        if row.get(field) not in (None, ""):
            out[field] = _scalar_projection(row[field], limit)
    if "done" in row:
        out["done"] = bool(row.get("done"))
    if row.get("sp") not in (None, ""):
        out["sp"] = row["sp"] if isinstance(row["sp"], (int, float)) \
            else _scalar_projection(row["sp"], 40)
    for field in ("components", "labels"):
        values = row.get(field)
        if isinstance(values, list):
            projected = [_scalar_projection(value, 80) for value in values[:8]]
            out[field] = [value for value in projected if value]
    description = _plain_projection_text(row.get("description"), 360, focus_terms)
    if description:
        out["description"] = description
    comments = [comment for comment in (row.get("comments") or []) if isinstance(comment, dict)]
    if comments:
        anchors = {str(term).casefold() for term in focus_terms}
        ranked = sorted(enumerate(comments), key=lambda pair: (
            -sum(1 for term in anchors if term in str(pair[1]).casefold()), pair[0],
        ))
        chosen = [comments[index] for index, _comment in ranked[:2]]
        projected_comments = [_project_comment_observation(comment, focus_terms)
                              for comment in chosen]
        out["comments"] = [comment for comment in projected_comments if comment]
    elif "comments" in row:
        out["comments"] = []
    if row.get("comments_error"):
        out["comments_error"] = _scalar_projection(row["comments_error"], 160)
    return out


def _project_document_body(row: dict, focus_terms=()) -> dict:
    """Typed Confluence projection; retain the full body only in query artifacts."""
    if not isinstance(row, dict):
        return {}
    out: dict = {}
    for field, limit in (("id", 120), ("title", 300), ("url", 700),
                         ("space", 80), ("updated", 40), ("error", 240)):
        if row.get(field) not in (None, ""):
            out[field] = _scalar_projection(row[field], limit)
    for field in ("text", "body"):
        if row.get(field) not in (None, ""):
            out[field] = _plain_projection_text(row[field], 900, focus_terms)
            break
    return out


_EXTERNAL_SUBJECT_NOISE = {
    "about", "analysis", "analyze", "build", "create", "definition", "develop",
    "development", "docs", "documentation", "document", "explain", "feature", "github",
    "homepage", "implement", "implementation", "intro", "introduction", "is", "link",
    "official", "overview", "pipeline", "please", "repo", "repository", "research",
    "search", "site", "task", "ticket", "url", "website", "what", "work",
    "개발", "개요", "검색", "공식", "구현", "기능", "기록", "내부", "링크",
    "리포지토리", "만들어", "문서", "뭐야", "분석", "사이트", "설명", "소개",
    "외부", "자료", "저장소", "정의", "조사", "조사해줘", "주소", "찾아줘", "태스크",
    "티켓", "파이프라인", "함께", "홈페이지",
}

_COMPILED_EXTERNAL_SUBJECT_RE = re.compile(
    r"(?m)^<ltm-public-subject>(.*?)</ltm-public-subject>$",
)


def _external_subject_terms(value: str) -> list[str]:
    context = str(value or "")
    compiled = _COMPILED_EXTERNAL_SUBJECT_RE.search(context)
    # When QuerySpecialist could not compile a privacy-safe public subject, do not treat
    # Korean workflow prose ("외부 자료를 조사해줘") as a topic anchor. Direct callers that
    # pass plain context keep the legacy extraction behavior used by focused unit tests.
    source = compiled.group(1) if compiled else context
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{1,}|[가-힣]{2,}", source):
        folded = token.casefold().strip(".+-")
        if folded in _EXTERNAL_SUBJECT_NOISE or folded in {term.casefold() for term in terms}:
            continue
        terms.append(token)
    return terms[:8]


def _external_evidence_context(state) -> str:
    """Return the current/frozen human-authorized public subject used by every sink.

    Query text is model/compiler output and therefore cannot make its own result relevant.
    QuerySpecialist already owns the continuation and privacy boundary; reuse that exact
    projection here so QueryRunner and ResultIntegrator cannot disagree about the subject.
    """
    from app.agent.workflow.agents.query_specialist import (
        _public_external_query, _public_query_subject_text,
    )

    authored = _public_query_subject_text(state).strip()
    canonical = _public_external_query(authored).strip()
    # Keep the literal human wording beside the canonical Latin projection: explicit
    # homepage/repository authorization is expressed there, while subject extraction gives
    # the canonical public terms first priority.
    return f"<ltm-public-subject>{canonical}</ltm-public-subject>\n{authored}"


def _committee_draft_alias_matches(
        *, stem: str, filename: str, path: str, host: str,
        metadata: str, context: str) -> bool:
    """Relate an edition-style standard name to an opaque committee draft id.

    A query subject and a result filename may use different identifiers.  That is safe only
    for a typed authority family: a short edition designator in the human subject, an opaque
    one-letter draft serial, and a hierarchical technical-committee path whose working-group
    id is repeated by the result metadata.  Named identifier namespaces are deliberately not
    interchangeable merely because both documents are official.
    """
    subject_has_edition_designator = any(re.fullmatch(
        r"(?:[a-z]{1,3}|[a-z]\+{1,2})\d{2}", term.casefold().strip(".+-"),
    ) for term in _external_subject_terms(context))
    opaque_draft_serial = bool(re.fullmatch(r"[a-z]\d{3,7}", stem, re.I))
    committee_ids = [segment.casefold() for segment in path.split("/")[:-1]
                     if re.fullmatch(r"(?:j?tc|sc|wg)\d{1,5}", segment, re.I)]
    has_committee_hierarchy = (
        len(set(committee_ids)) >= 2
        and any(identity.startswith("wg") for identity in committee_ids)
    )
    working_group_matches = any(
        identity.startswith("wg") and re.search(
            rf"(?<![a-z0-9]){re.escape(identity)}(?![a-z0-9])", metadata, re.I,
        )
        for identity in committee_ids
    )
    working_draft = bool(re.search(
        r"\b(?:working\s+draft|committee\s+draft|draft\s+standard)\b", metadata, re.I,
    ))
    authority_host = bool(re.search(
        r"(?:^|[.-])(?:std|standards?|specs?)(?:[.-]|$)", host, re.I,
    ))
    return bool(
        subject_has_edition_designator
        and opaque_draft_serial
        and filename.casefold().endswith(".pdf")
        and has_committee_hierarchy
        and working_group_matches
        and working_draft
        and authority_host
    )


def _external_hit_has_direct_document_identity(hit: dict, context: str) -> bool:
    """Recognize a direct publication related by a typed document-authority family.

    Search indexes can resolve a human-facing standard edition to a numbered committee draft,
    so literal subject overlap alone is incomplete.  The escape is intentionally narrower
    than ``official``: the URL filename and metadata must agree on one document id, and the
    request/result pair must satisfy the generic committee-draft alias contract.
    """
    if not isinstance(hit, dict) or not re.search(
            r"\b(?:specification|standard|working\s+draft|technical\s+report)\b|표준|규격|사양",
            str(context or ""), re.I):
        return False
    try:
        parsed = urlsplit(str(hit.get("url") or ""))
        path = unquote(parsed.path or "")
        host = (parsed.hostname or "").casefold()
    except Exception:
        return False
    filename = path.rstrip("/").rsplit("/", 1)[-1].casefold()
    stem = re.sub(r"\.(?:pdf|html?)$", "", filename)
    if not re.fullmatch(r"(?=.{4,40}$)(?=[a-z0-9._-]*\d)[a-z][a-z0-9._-]+", stem):
        return False
    metadata = " ".join(str(hit.get(key) or "") for key in (
        "title", "name", "snippet", "description",
    ))
    if not re.search(rf"(?<![a-z0-9]){re.escape(stem)}(?![a-z0-9])", metadata, re.I):
        return False
    return _committee_draft_alias_matches(
        stem=stem, filename=filename, path=path, host=host,
        metadata=metadata, context=context,
    )


def _external_hit_matches_subject(hit: dict, context: str) -> bool:
    """Keep a hit only when its own metadata contains a public-subject anchor.

    An empty subject is a legacy/diagnostic state: the existing navigation filter still
    applies, but this guard does not guess a subject and erase an already-curated direct
    source. Executable external plans normally always carry a compiled public subject.
    """
    if not isinstance(hit, dict):
        return False
    subject = _external_subject_terms(context)
    if not subject:
        return True
    material = " ".join(str(hit.get(key) or "") for key in (
        "title", "name", "url", "snippet", "description",
    )).casefold()
    return (any(term.casefold() in material for term in subject)
            or _external_hit_has_direct_document_identity(hit, context))


def _is_direct_intro_for_context(hit: dict, context: str) -> bool:
    """Retain an intro when the user asked for an overview or it covers the whole subject."""
    overview_requested = bool(re.search(
        r"뭐야|무엇|정의|개요|소개|설명해|what\s+is|overview|introduction|define",
        str(context or ""), re.I,
    ))
    subject = _external_subject_terms(context)
    if not subject:
        return False
    material = " ".join(str(hit.get(key) or "") for key in (
        "title", "name", "url", "snippet", "description",
    )).casefold()
    overlap = sum(1 for term in subject if term.casefold() in material)
    if overview_requested:
        # The intent verb alone is insufficient: `Puffin NDV 설명해줘` must not make a
        # generic StarRocks introduction relevant. At least one requested subject anchor
        # must actually occur in the intro page.
        return overlap >= 1
    required = 1 if len(subject) == 1 else max(2, (len(subject) * 2 + 2) // 3)
    return overlap >= required


def _external_target_matches_subject(hit: dict, context: str) -> bool:
    """Require a navigation request to identify the same public subject as the hit."""
    subject = _external_subject_terms(context)
    if not subject:
        return False
    material = " ".join(str(hit.get(key) or "") for key in (
        "title", "name", "url", "snippet", "description",
    )).casefold()
    return any(term.casefold() in material for term in subject)


def _explicit_home_or_link_request(context: str) -> bool:
    value = str(context or "")
    return bool(re.search(
        r"(?:공식\s*)?(?:홈\s*페이지|웹\s*사이트)|공식.{0,10}(?:사이트|링크|주소)|"
        r"(?:official\s+)?(?:home\s?page|website)|official.{0,12}(?:site|link|url)",
        value, re.I,
    ))


def _explicit_github_repository_request(context: str) -> bool:
    value = str(context or "")
    return bool(re.search(
        r"github.{0,18}(?:저장소|리포지토리|repo(?:sitory)?)|"
        r"(?:저장소|리포지토리|repo(?:sitory)?).{0,18}github",
        value, re.I,
    ))


def _is_generic_external_hit(hit: dict, context: str = "") -> bool:
    """Reject navigation/contribution hits before they enter evidence synthesis.

    Search pages, product landing/intro pages, repository README files, and contribution
    policy pages can be official while proving nothing about the requested feature. Keep the
    complete provider response in ``query_artifacts`` for diagnostics, but expose only direct
    topic documents to Research and Result Integrator.
    """
    if not isinstance(hit, dict):
        return True
    url = str(hit.get("url") or "").strip()
    title = str(hit.get("title") or hit.get("name") or "").strip()
    detail = str(hit.get("snippet") or hit.get("description") or "").strip()
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        path = unquote(parsed.path or "/").rstrip("/").casefold()
    except Exception:
        host, path = "", ""
    material = " ".join((title, detail))
    search_page = bool(re.search(r"(?:^|/)search(?:/|$)", path))
    contribution_page = bool(re.search(
        r"(?:^|/)(?:contributing|code_of_conduct|pull_request_template|cla)"
        r"(?:\.[a-z0-9_-]+)?(?:\.md)?$", path,
    ))
    root_or_docs_landing = path in ("", "/") or bool(re.search(
        r"(?:^|/)(?:docs?|documentation|introduction)(?:/|$)$", path,
    ))
    direct_navigation_target = (
        _explicit_home_or_link_request(context)
        and _external_target_matches_subject(hit, context)
    )
    if url and (search_page or contribution_page
                or (root_or_docs_landing and not direct_navigation_target)):
        return True
    intro_page = bool(re.search(
        r"(?:^|/)(?:introduction|overview)/[^/]*(?:intro|overview)?[^/]*$|"
        r"(?:^|/)[^/]*(?:_intro|_overview)(?:\.[a-z0-9]+)?$",
        path,
    ))
    if intro_page and not _is_direct_intro_for_context(hit, context):
        return True
    if re.search(r"(?:^|/)readme(?:\.[a-z0-9_-]+)?(?:\.md)?$", path):
        segments = [segment for segment in path.split("/") if segment]
        component = segments[-2] if len(segments) >= 2 else ""
        generic_components = {"blob", "main", "master", "docs", "doc", "documentation"}
        direct_component_spec = (
            len(component) >= 3 and component not in generic_components
            and bool(re.search(rf"(?<![a-z0-9]){re.escape(component)}(?![a-z0-9])",
                               material, re.I))
        )
        if not direct_component_spec:
            return True
    if re.search(
            r"search\s+the\s+documentation|contributor\s+license\s+agreement|"
            r"\bCLA\b.{0,80}Markdown|documentation\s+(?:contribution|contributing|"
            r"writing\s+process|templates?)|thank\s+you.{0,80}contribut(?:e|ing)",
            material, re.I):
        return True
    # GitHub repository roots are navigation, even when the search API represents them as
    # a repository result rather than an explicit README URL.
    if host == "github.com" and re.fullmatch(r"/[^/]+/[^/]+", path or ""):
        direct_repository_target = (
            _explicit_github_repository_request(context)
            and _external_target_matches_subject(hit, context)
        )
        if not direct_repository_target:
            return True
    return False


def _external_hit_is_relevant(hit: dict, context: str = "") -> bool:
    """Shared external-evidence admission policy for compact and rendered outputs."""
    return (not _is_generic_external_hit(hit, context)
            and _external_hit_matches_subject(hit, context))


def _merge_materialized_ticket_sources(state, current: dict, *, cap: int = 8) -> dict:
    """Merge verified ticket details across one true continuation boundary.

    Query results and full artifacts remain per-turn. The small ``get_ticket`` ledger is the
    durable authority used by Work/Auditor/Result Integrator, so replacing it with a thin
    follow-up query loses facts that were already opened. New requests never inherit it;
    Session owns that boundary through ``turn_continuation``.
    """
    prior = state.get("materialized_ticket_sources") or {} \
        if state.get("turn_continuation") else {}
    if not isinstance(prior, dict):
        prior = {}
    if not isinstance(current, dict):
        current = {}

    prior_order: list[str] = []
    current_order: list[str] = []
    details: dict[str, dict] = {}
    focus_terms = _projection_focus_terms("\n".join(value for value in (
        request_text(state).strip(), last_user_text(state).strip(),
    ) if value))
    for source, source_order in ((prior, prior_order), (current, current_order)):
        for row in source.get("ticketDetails") or []:
            if not isinstance(row, dict) or row.get("error"):
                continue
            key = str(row.get("key") or "").strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", key):
                continue
            if key not in source_order:
                source_order.append(key)
            # Current exact reads intentionally overwrite stale fields from the durable
            # ledger. Ordering is computed separately so that a newly verified parent can
            # be reserved inside the eight-detail cap.
            # Re-project both current and legacy prior rows here. Session's row cap alone
            # cannot protect a continuation created before this projection existed.
            details[key] = {**_project_ticket_detail(row, focus_terms), "key": key}

    current_parents = [
        str(value or "").strip().upper()
        for value in current.get("parentCandidateKeys") or []
        if str(value or "").strip().upper() in current_order
    ]
    prior_parents = [
        str(value or "").strip().upper()
        for value in prior.get("parentCandidateKeys") or []
        if str(value or "").strip().upper() in prior_order
    ]
    ordered: list[str] = []
    # Every current row is a successful exact materialization from this turn and must win
    # over historical context inside the bounded ledger. Parent candidates come first so
    # Work/Auditor cannot lose the newly verified structural choice; remaining current rows
    # then precede all prior history. Duplicate keys retain the current payload above.
    for key in (*current_parents, *current_order, *prior_parents, *prior_order):
        if key in details and key not in ordered:
            ordered.append(key)
    ordered = ordered[:max(0, int(cap or 0))]
    attempted = (current.get("parentCandidateSearchAttempted") is True
                 or prior.get("parentCandidateSearchAttempted") is True)
    if not ordered:
        return {"parentCandidateSearchAttempted": True} if attempted else {}

    parents: list[str] = []
    for key in (*current_parents, *prior_parents):
        if key in ordered and key not in parents:
            parents.append(key)
    merged = {
        "ticketDetails": [details[key] for key in ordered],
        "parentCandidateKeys": parents,
    }
    if attempted:
        merged["parentCandidateSearchAttempted"] = True
    return merged


def _search_token(token: str) -> str:
    """Jira text 검색용 최소 어간. 형태소 추측 대신 흔한 조사·서술 접미만 제거한다."""
    value = str(token or "").strip().strip(".,;:!?…")
    # 긴 접미부터 제거. 영문 기술어 뒤의 한국어 조사(`Avro로`)도 같은 규칙을 쓴다.
    for suffix in ("으로부터", "에서는", "전환하는", "생성하는", "위해서", "으로", "에서",
                   "에게", "하는", "한다", "했다", "하며", "하고", "처럼", "까지",
                   "부터", "로", "을", "를", "은", "는", "이", "가", "의", "에"):
        if value.endswith(suffix) and len(value) - len(suffix) >= 2:
            value = value[:-len(suffix)]
            break
    return value


def _lexical_terms(text: str, limit: int = 4) -> list[str]:
    terms, seen = [], set()
    for raw in re.findall(r"[A-Za-z0-9가-힣_.-]{2,}", str(text or "")):
        token = _search_token(raw)
        folded = token.casefold()
        if not token or folded in {x.casefold() for x in _LEXICAL_IGNORED} or folded in seen:
            continue
        seen.add(folded)
        terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def _jira_where(where: str, query: str) -> str:
    """Combine typed Jira lexical query with structural filters using safe JQL text clauses."""
    base = str(where or "").strip()
    query = str(query or "").strip()
    if not query:
        return base
    # ``query`` is a lexical subject, never raw JQL.  Silently repairing model-authored
    # pseudo-JQL changed invalid filters into a different lexical search and let Research
    # treat an empty result as verified absence.  Fail explicitly so the existing
    # QueryPlan completeness contract can surface the gap; structural conditions belong in
    # typed ``where`` and configured search projects remain enforced by the executor.
    looks_jql = bool(re.search(
        r"(?:^|\s)(?:project|summary|description|text|status|statusCategory|issueType|issuetype|"
        r"parent|assignee|labels?|component)\s*(?:=|!=|~|\bin\b|\bis\b)", query, re.I))
    if looks_jql:
        raise ValueError(
            "QueryPlan의 query에는 JQL을 넣을 수 없습니다. lexical subject는 query에, "
            "구조 조건은 where에 분리해야 합니다."
        )
    if re.search(r"\b(?:text|summary|description)\s*~", base, re.I):
        return base
    terms = _lexical_terms(query)
    clauses = [f'text ~ "{term}"' for term in terms[:4]]
    # Public technology queries commonly contain umbrella / feature / metric. Requiring
    # all three loses relevant tickets that omit only the umbrella name. Keep the narrow
    # all-Latin three-term form at a 2-of-3 boundary; work phrases retain strict AND.
    if len(clauses) == 3 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9.+-]*", term)
                                 for term in terms):
        lexical = "(" + " OR ".join(
            f"({clauses[left]} AND {clauses[right]})"
            for left, right in ((0, 1), (0, 2), (1, 2))) + ")"
    else:
        lexical = " AND ".join(clauses)
    if not lexical:
        return base
    return f"({base}) AND ({lexical})" if base else lexical


def _needs_evidence_materialization(state, results: list[dict]) -> bool:
    """Whether search hits must be opened before the single research synthesis pass.

    Listing/count requests intentionally keep lightweight rows. Research and create/duplicate-check
    requests need the ticket body, comments, and document body that a ReAct loop would otherwise open
    through several model round trips.
    """
    tasks = (state.get("request_plan") or {}).get("tasks") or []
    if any(str(task.get("kind") or "") == "research" for task in tasks
           if isinstance(task, dict)):
        return True
    if str(state.get("intent") or "") == "plan_work":
        return True
    sources = {str(row.get("source") or "") for row in results if isinstance(row, dict)}
    return len(sources & {"jira", "comments", "confluence", "web", "github"}) >= 2


def _is_parent_candidate_result(row: dict) -> bool:
    """Identify a structurally bounded Epic-candidate read from its compiled JQL."""
    if not isinstance(row, dict) or str(row.get("source") or "") != "jira":
        return False
    result = row.get("result") or {}
    if result.get("parentCandidate") is True:
        return True
    canonical = str((result.get("canonicalJql") or ""))
    return bool(re.search(
        r"\bissuetype\s*(?:=\s*['\"]?epic['\"]?|\bin\s*\(\s*['\"]?epic['\"]?)",
        canonical, re.I,
    ))


def _resolve_parent_reference_candidates(reference_keys: list[str]) -> dict:
    """Resolve child → parent Epic through opened Jira hierarchy fields.

    A child key is not a lexical term that its Epic must repeat. Follow exact ``epicKey``
    or ``parentKey`` fields instead; for a Sub-Task, open its Task parent once more to find
    the Epic. Every hop uses the scoped ``get_ticket`` tool and the final candidate is
    accepted only when its opened issue type is Epic.
    """
    from app.agent import tools as T

    opened: dict[str, dict] = {}
    errors: list[str] = []

    def open_ticket(key) -> dict:
        normalized = str(key or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", normalized):
            return {}
        if normalized in opened:
            return opened[normalized]
        try:
            detail = T.BY_NAME["get_ticket"].invoke({"key": normalized, "comment_limit": 2}) or {}
        except Exception as exc:
            detail = {"key": normalized, "error": str(exc)[:240]}
        opened[normalized] = detail
        if detail.get("error"):
            errors.append(f"{normalized}: {str(detail['error'])[:180]}")
        return detail

    candidates: list[dict] = []
    seen: set[str] = set()
    for reference in list(dict.fromkeys(str(key).upper() for key in reference_keys))[:4]:
        child = open_ticket(reference)
        if not child or child.get("error"):
            continue
        issue_type = str(child.get("type") or "").strip().casefold()
        epic_key = str(child.get("epicKey") or "").strip().upper()
        parent_key = str(child.get("parentKey") or "").strip().upper()
        if issue_type == "epic":
            epic_key = str(child.get("key") or reference).strip().upper()
        elif not epic_key and parent_key:
            parent = open_ticket(parent_key)
            if str(parent.get("type") or "").strip().casefold() == "epic":
                epic_key = parent_key
            else:
                # Jira may represent Task→Epic with either the legacy Epic Link field or
                # the modern parent field. The final opened type check below remains the
                # authority, so following either exact key does not broaden discovery.
                epic_key = str(parent.get("epicKey") or parent.get("parentKey") or "").strip().upper()
        if not epic_key or epic_key in seen:
            continue
        epic = open_ticket(epic_key)
        if (not epic or epic.get("error")
                or str(epic.get("type") or "").strip().casefold() != "epic"):
            continue
        seen.add(epic_key)
        candidates.append(epic)

    return {"candidates": candidates, "errors": errors,
            "openedKeys": list(opened)}


def _materialization_ticket_selection(results: list[dict], *, cap: int = 8,
                                      parent_reserve: int = 2) -> tuple[list[str], list[str]]:
    """Reserve bounded evidence slots for structural parent candidates.

    A complete duplicate query may return dozens of newer Tasks before the subsequent
    ``issueType = Epic`` candidate read. A global ``[:8]`` then made every selected parent
    unverifiable. Reserve at most two slots for the parent read and keep the remaining six
    for duplicate/history diversity. Unused slots are filled in original search order.
    """
    all_keys: list[str] = []
    ordinary: list[str] = []
    parent_candidates: list[str] = []

    def add(target: list[str], value) -> None:
        key = str(value or "").strip().upper()
        if key and key not in target:
            target.append(key)

    for row in results:
        if not isinstance(row, dict):
            continue
        result = row.get("result") or {}
        row_keys: list[str] = []
        for ticket in result.get("tickets") or []:
            add(row_keys, (ticket or {}).get("key"))
        for comment in result.get("comments") or []:
            add(row_keys, (comment or {}).get("ticketKey"))
        for key in row_keys:
            add(all_keys, key)
            add(parent_candidates if _is_parent_candidate_result(row) else ordinary, key)

    limit = max(0, int(cap or 0))
    reserved = parent_candidates[:min(max(0, int(parent_reserve or 0)), limit)]
    selected = [key for key in ordinary if key not in reserved][:limit - len(reserved)]
    selected.extend(reserved)
    for key in all_keys:
        if len(selected) >= limit:
            break
        add(selected, key)
    return selected, [key for key in selected if key in parent_candidates]


def _materialize_evidence(results: list[dict], *, focus: str = "",
                          expand_entities: bool = False,
                          entity_root_keys=(), ticket_cap: int = 8,
                          entity_cap: int = 2) -> dict:
    """Open selected Jira and Confluence hits without another LLM routing loop.

    Search order is already the QueryPlan's relevance/order contract. Preserve order within each
    evidence purpose, deduplicate identities, reserve bounded structural-parent coverage, and cap
    materialization to one human-reviewable set. Individual read failures are explicit so Research
    Analyst can fall back to ReAct instead of silently synthesizing thin evidence.
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.agent import tools as T

    # Reserve two exact-read slots only when research entity expansion is active and a
    # structural root is visible in the lightweight hit set.  Listing/create paths retain
    # the existing eight-ticket selection contract.
    root_hint = any(
        str((ticket or {}).get("issueType") or (ticket or {}).get("type") or "")
        .strip().casefold() == "epic"
        for row in results if isinstance(row, dict)
        for ticket in ((row.get("result") or {}).get("tickets") or [])
        if isinstance(ticket, dict)
    )
    preferred_root_set = {
        str(key or "").strip().upper() for key in entity_root_keys if str(key or "").strip()
    }
    raw_keys = {
        str((ticket or {}).get("key") or "").strip().upper()
        for row in results if isinstance(row, dict)
        for ticket in ((row.get("result") or {}).get("tickets") or [])
        if isinstance(ticket, dict) and (ticket or {}).get("key")
    }
    ticket_limit = max(0, int(ticket_cap or 0))
    # One materialized root is required before any graph traversal.  Never let expansion
    # consume that slot or make the total number of exact ticket reads exceed ticket_cap.
    reserve = min(max(0, int(entity_cap or 0)), max(0, ticket_limit - 1)) \
        if expand_entities and (root_hint or len(raw_keys) == 1
                                or bool(preferred_root_set & raw_keys)) else 0
    initial_cap = max(0, ticket_limit - reserve)
    ticket_keys, parent_candidate_keys = _materialization_ticket_selection(
        results, cap=initial_cap,
    )
    document_refs = []
    for row in results:
        if not isinstance(row, dict):
            continue
        result = row.get("result") or {}
        for document in result.get("documents") or []:
            ref = str((document or {}).get("url") or (document or {}).get("id") or "").strip()
            if ref and ref not in document_refs:
                document_refs.append(ref)

    document_refs = document_refs[:4]

    def open_ticket(key: str) -> dict:
        try:
            value = T.BY_NAME["get_ticket"].invoke({"key": key, "comment_limit": 8}) or {}
            return value if not value.get("error") else {"key": key, "error": value["error"]}
        except Exception as exc:
            return {"key": key, "error": str(exc)[:240]}

    def open_document(ref: str) -> dict:
        try:
            value = T.BY_NAME["read_document"].invoke({"url_or_id": ref}) or {}
            return value if not value.get("error") else {"url": ref, "error": value["error"]}
        except Exception as exc:
            return {"url": ref, "error": str(exc)[:240]}

    with ThreadPoolExecutor(max_workers=max(1, min(6, len(ticket_keys) + len(document_refs)))) as pool:
        ticket_details = list(pool.map(open_ticket, ticket_keys)) if ticket_keys else []
        document_bodies = list(pool.map(open_document, document_refs)) if document_refs else []

    focus_terms = _projection_focus_terms(" ".join((
        str(focus or ""),
        *(str((row.get("result") or {}).get("query") or "") for row in results
          if isinstance(row, dict)),
    )))
    entity_coverage = None
    if expand_entities and reserve:
        try:
            from app.agent.tools._ctx import client, jira_key_allowed
            from app.agent.workflow.source_graph import bounded_entity_expansion

            expanded_keys, entity_coverage = bounded_entity_expansion(
                client(), ticket_details, focus_terms,
                allowed_key=jira_key_allowed, excluded_keys=ticket_keys,
                preferred_root_keys=preferred_root_set, cap=reserve,
            )
        except Exception as exc:
            expanded_keys = []
            entity_coverage = {
                "mode": "bounded_one_hop", "rootKeys": [], "scannedCandidates": 0,
                "eligibleCandidates": 0, "selectedKeys": [], "cap": reserve,
                "truncated": False, "complete": False,
                "error": str(exc)[:240],
                "callBudget": {"root_neighbor_reads": 0, "expanded_detail_reads": 0},
            }
        if expanded_keys:
            with ThreadPoolExecutor(max_workers=min(4, len(expanded_keys))) as pool:
                expanded_details = list(pool.map(open_ticket, expanded_keys))
            ticket_keys.extend(expanded_keys)
            ticket_details.extend(expanded_details)
            entity_coverage["materializedKeys"] = [
                str(row.get("key") or "").strip().upper()
                for row in expanded_details
                if isinstance(row, dict) and not row.get("error") and row.get("key")
            ]
    ticket_projection = [_project_ticket_detail(row, focus_terms) for row in ticket_details]
    document_projection = [_project_document_body(row, focus_terms) for row in document_bodies]

    errors = [str(row.get("error")) for row in ticket_details + document_bodies
              if isinstance(row, dict) and row.get("error")]
    ticket_target = next((row for row in results if row.get("source") == "jira"), None) \
        or next((row for row in results if row.get("source") == "comments"), None)
    document_target = next((row for row in results if row.get("source") == "confluence"), None)
    if ticket_target is not None and ticket_details:
        ticket_target["result"] = dict(ticket_target.get("result") or {},
                                       ticketDetails=ticket_projection,
                                       detailProjection="ticket-detail.v1")
        if entity_coverage is not None:
            ticket_target["result"]["entityCoverage"] = entity_coverage
        if errors:
            ticket_target["result"]["materializationErrors"] = errors
    if document_target is not None and document_bodies:
        document_target["result"] = dict(document_target.get("result") or {},
                                          documentBodies=document_projection,
                                          bodyProjection="document-body.v1")
        if errors:
            document_target["result"]["materializationErrors"] = errors
    # Keep the structural candidate row self-describing so Work can intersect its choices
    # with successfully opened details instead of trusting a lightweight search hit.
    successful = {str(row.get("key") or "").strip().upper()
                  for row in ticket_details if isinstance(row, dict) and not row.get("error")}
    materialized_parents = [key for key in parent_candidate_keys if key in successful]
    for row in results:
        if _is_parent_candidate_result(row):
            row["result"] = dict(row.get("result") or {},
                                 materializedCandidateKeys=materialized_parents)
    return {
        "tickets": len(ticket_details), "documents": len(document_bodies),
        "ticketDetails": ticket_details, "documentBodies": document_bodies,
        "projectedTicketDetails": ticket_projection,
        "projectedDocumentBodies": document_projection,
        "ticketKeys": ticket_keys, "parentCandidateKeys": materialized_parents,
        "errors": errors,
        **({"entityCoverage": entity_coverage} if entity_coverage is not None else {}),
    }


class QueryRunner:
    name = Node.QUERY_RUNNER

    def node(self):
        return self._run

    @staticmethod
    def _all_pages(tool_obj, args: dict) -> tuple[list, dict]:
        pager = PaginationAccumulator(max_pages=200)
        meta = {}
        while True:
            payload = dict(args, cursor=pager.cursor)
            result = tool_obj.invoke(payload) or {}
            meta = meta or {k: result.get(k) for k in (
                "canonicalJql", "canonicalCql", "scopeProjects", "scopeSpaces", "total")}
            bucket = result.get("tickets") or result.get("documents") \
                or result.get("comments") or result.get("people") or []
            if not pager.add_page(bucket):
                break
            if result.get("error"):
                meta["error"] = result["error"]
                break
            if not pager.advance(
                has_more=bool(result.get("hasMore")),
                next_cursor=result.get("nextCursor"),
                total=meta.get("total"),
            ):
                break
        meta.update(pager.metadata())
        meta["complete"] = not bool(meta.get("error") or meta.get("incomplete"))
        return pager.rows, meta

    def _run(self, state):
        from app.agent import tools as T
        from app.agent.tools.query_tools import execute_jql_all
        from app.agent.workflow.agents.query_specialist import (
            _query_plan_creation_target_required,
            _reject_unsupported_relational_plan,
        )

        # Persisted/manual plans bypass QuerySpecialist.apply. Reject unsupported relational
        # contracts here too; silently running them independently would return plausible but
        # semantically wrong evidence.
        query_plan = state.get("query_plan") or {}
        _reject_unsupported_relational_plan(query_plan)
        # Only PLAN_WORK's creation compiler owns this typed marker. The helper also requires
        # an empty executable-read set; model-authored uncertainty text is never provenance.
        # ASK/navigation plans therefore cannot inherit the no-read behavior.
        target_required = (_query_plan_creation_target_required(query_plan)
                           if str(state.get("intent") or "") == "plan_work" else "")
        if target_required:
            # This is a compiler-owned fail-loud no-read plan, not evidence of an in-scope
            # miss.  Preserve a bounded diagnostic artifact and make zero tool calls.  A true
            # continuation may still carry its already verified sidecar; a new turn inherits
            # nothing because the merge boundary is owned by Session.
            materialized_ticket_sources = _merge_materialized_ticket_sources(state, {})
            return {
                "query_results": [],
                "query_artifacts": {
                    "creation-subject-guard": {
                        "kind": "creation_target_required",
                        "targetRequired": True,
                        "queriesSkipped": len(query_plan.get("queries") or []),
                        "reason": target_required,
                    },
                },
                "materialized_ticket_sources": materialized_ticket_sources,
                "assignment_completion": {},
                "trace": note(state, self.name, "생성 대상 anchor 부족 · Jira 조회 생략"),
            }

        results, artifacts = [], {}
        materialized_ticket_sources = {}
        parent_candidate_search_attempted = False
        # 이 유형은 LLM이 만든 단일 JQL만으로 끝낼 수 없다. 제목 검색 결과에서 parent를
        # 고른 뒤 그 parent의 직계 Sub-Task를 전수 조회해야 하므로 deterministic join을 먼저 돈다.
        from app.agent.workflow.assignment_completion import (
            asks_incomplete_assignees, lookup_incomplete_assignees,
        )
        from app.agent.workflow.state import last_user_text
        if asks_incomplete_assignees(last_user_text(state)):
            completion = lookup_incomplete_assignees(
                last_user_text(state), state.get("keywords") or [])
            artifacts["incomplete-assignees"] = completion
            results.append({"id": "incomplete-assignees", "source": "jira",
                            "result": completion})
        for spec in (state.get("query_plan") or {}).get("queries") or []:
            qid, source = str(spec.get("id") or ""), str(spec.get("source") or "")
            complete = spec.get("completeness") or "page"
            try:
                if source == "jira":
                    references = [str(key).strip().upper()
                                  for key in (spec.get("parent_reference_keys") or [])
                                  if re.fullmatch(r"[A-Z][A-Z0-9]*-\d+",
                                                  str(key).strip(), re.I)]
                    hierarchy = (_resolve_parent_reference_candidates(references)
                                 if references else {})
                    candidates = hierarchy.get("candidates") or []
                    if candidates:
                        raw = {
                            "tickets": [{key: detail.get(key) for key in
                                         ("key", "summary", "type", "status", "assignee", "updated")
                                         if detail.get(key) not in (None, "")}
                                        for detail in candidates],
                            "ticketDetails": candidates,
                            "returned": len(candidates), "total": len(candidates), "pages": 0,
                            "parentCandidate": True,
                            "parentResolution": "referenced-ticket-hierarchy",
                            "referenceKeys": references,
                        }
                    elif not str(spec.get("query") or "").strip() and references:
                        # Never turn a failed hierarchy resolution into an all-Epic scan.
                        raw = {
                            "tickets": [], "returned": 0, "total": 0, "pages": 0,
                            "parentCandidate": True,
                            "parentResolution": "unresolved-reference",
                            "referenceKeys": references,
                            "error": ("참조 티켓에서 상위 Epic 관계를 확인하지 못했고 "
                                      "안전한 subject 검색어도 없어 후보 조회를 확대하지 않았습니다."),
                        }
                    else:
                        args = {
                            "where": _jira_where(spec.get("where") or "",
                                                 spec.get("query") or ""),
                            "order_by": spec.get("order_by") or "updated DESC",
                            "fields": spec.get("fields") or [],
                            "page_size": spec.get("page_size") or 50,
                        }
                        if complete == "all":
                            raw = execute_jql_all(**args)
                        else:
                            raw = T.BY_NAME["run_jql_v2"].invoke(args)
                elif source == "confluence":
                    args = {"query": spec.get("query") or "", "where": spec.get("where") or "",
                            "page_size": spec.get("page_size") or 50}
                    if complete == "all":
                        rows, meta = self._all_pages(T.BY_NAME["search_documents"], args)
                        raw = dict(meta, documents=rows, returned=len(rows))
                    else:
                        raw = T.BY_NAME["search_documents"].invoke(args)
                elif source == "comments":
                    if not str(spec.get("query") or "").strip() \
                            and not str(spec.get("where") or "").strip():
                        raw = {"error": "빈 댓글 전수조회는 허용되지 않습니다.",
                               "comments": [], "returned": 0}
                        artifacts[qid] = raw
                        results.append({"id": qid, "source": source, "result": raw})
                        continue
                    args = {"query": spec.get("query") or "", "jql_where": spec.get("where") or "",
                            "page_size": min(spec.get("page_size") or 20, 25)}
                    if complete == "all":
                        rows, meta = self._all_pages(T.BY_NAME["search_comments"], args)
                        raw = dict(meta, comments=rows, returned=len(rows))
                    else:
                        raw = T.BY_NAME["search_comments"].invoke(args)
                elif source == "people":
                    args = {"name": spec.get("query") or "", "module": spec.get("where") or "",
                            "page_size": spec.get("page_size") or 50}
                    if complete == "all":
                        rows, meta = self._all_pages(T.BY_NAME["query_people"], args)
                        raw = dict(meta, people=rows, returned=len(rows))
                    else:
                        raw = T.BY_NAME["query_people"].invoke(args)
                elif source in ("web", "github"):
                    raw = T.BY_NAME["search_" + source].invoke(
                        {"query": spec.get("query") or "", "limit": min(spec.get("page_size") or 5, 10)})
                else:
                    raw = {"error": f"지원하지 않는 source: {source}"}
            except Exception as exc:
                raw = {"error": str(exc)[:240]}
            if (source == "jira" and isinstance(raw, dict) and not raw.get("error")
                    and _is_parent_candidate_result({"source": "jira", "result": raw})):
                parent_candidate_search_attempted = True
            # full target set은 state artifact에 보존하되 LLM에는 각 source 앞부분만 전달한다.
            artifacts[qid] = raw
            compact = dict(raw)
            focus_terms = _projection_focus_terms("\n".join(value for value in (
                request_text(state).strip(), last_user_text(state).strip(),
                str(spec.get("query") or "").strip(),
            ) if value))
            # Exact hierarchy resolution can already contain opened ticket details before
            # the shared materializer runs. Project that path too; raw stays in artifacts.
            if isinstance(compact.get("ticketDetails"), list):
                compact["ticketDetails"] = [
                    _project_ticket_detail(row, focus_terms)
                    for row in compact["ticketDetails"] if isinstance(row, dict)
                ]
                compact["detailProjection"] = "ticket-detail.v1"
            if isinstance(compact.get("documentBodies"), list):
                compact["documentBodies"] = [
                    _project_document_body(row, focus_terms)
                    for row in compact["documentBodies"] if isinstance(row, dict)
                ]
                compact["bodyProjection"] = "document-body.v1"
            if source in ("web", "github") and isinstance(compact.get("results"), list):
                candidates = list(compact["results"])
                # Navigation pages are useful only when the *human request* explicitly
                # asks for a homepage/link/repository. A model-authored query must not
                # upgrade a generic landing page into evidence by adding those words.
                external_context = _external_evidence_context(state)
                generic_removed = 0
                irrelevant_removed = 0
                kept = []
                for row in candidates:
                    if _is_generic_external_hit(row, external_context):
                        generic_removed += 1
                    elif not _external_hit_matches_subject(row, external_context):
                        irrelevant_removed += 1
                    else:
                        kept.append(row)
                compact["results"] = kept
                if generic_removed:
                    compact["genericResultsFiltered"] = generic_removed
                if irrelevant_removed:
                    compact["irrelevantResultsFiltered"] = irrelevant_removed
            # 전체 집합은 artifact에 보존한다. LLM에는 정렬된 앞부분과 total만 싣는다.
            # 50건을 그대로 주입하면 생성 한 건에서도 Research Analyst 입력이 14k tokens까지
            # 불었다(PASTE1 실측). source별로 사람이 한 화면에서 검토할 양만 남긴다.
            caps = {"tickets": 12, "documents": 10, "comments": 12,
                    "people": 20, "results": 8}
            for field, cap in caps.items():
                if isinstance(compact.get(field), list) and len(compact[field]) > cap:
                    compact[field] = compact[field][:cap]
                    compact["contextTruncated"] = True
                    compact["artifactId"] = qid
            results.append({"id": qid, "source": source, "result": compact})
        if _needs_evidence_materialization(state, results):
            research_expansion = any(
                isinstance(task, dict) and str(task.get("kind") or "") == "research"
                for task in ((state.get("request_plan") or {}).get("tasks") or [])
            )
            materialized = _materialize_evidence(results, focus="\n".join(value for value in (
                request_text(state).strip(), last_user_text(state).strip(),
            ) if value), expand_entities=research_expansion,
                entity_root_keys=state.get("mentioned_keys") or ())
            if materialized["tickets"] or materialized["documents"] or materialized["errors"]:
                artifacts["evidence-materialization"] = materialized
            successful_details = [dict(row) for row in
                                  materialized.get("projectedTicketDetails") or []
                                  if isinstance(row, dict) and not row.get("error")][:8]
            materialized_ticket_sources = {
                "ticketDetails": successful_details,
                "parentCandidateKeys": list(materialized.get("parentCandidateKeys") or []),
            } if successful_details else {}
        if parent_candidate_search_attempted:
            materialized_ticket_sources["parentCandidateSearchAttempted"] = True
        materialized_ticket_sources = _merge_materialized_ticket_sources(
            state, materialized_ticket_sources,
        )
        return {"query_results": results, "query_artifacts": artifacts,
                "materialized_ticket_sources": materialized_ticket_sources,
                "assignment_completion": artifacts.get("incomplete-assignees") or {},
                "trace": note(state, self.name, f"조회 {len(results)}개 실행")}


__all__ = ["QueryRunner", "_jira_where", "_needs_evidence_materialization",
           "_is_parent_candidate_result", "_resolve_parent_reference_candidates",
           "_materialization_ticket_selection",
           "_materialize_evidence", "_is_generic_external_hit",
           "_external_hit_is_relevant", "_external_evidence_context",
           "_merge_materialized_ticket_sources"]
