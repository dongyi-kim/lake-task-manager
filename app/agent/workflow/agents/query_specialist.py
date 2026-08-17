"""Query Specialist — 복합 요청의 atomic task를 typed read plan으로 변환한다."""

from __future__ import annotations

import json
import re

from app.agent.prompts.roles import SYSTEM_QUERY_SPECIALIST
from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.contracts import CompactQueryPlan, QueryPlan
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
                   "confluence", "wiki", "github", "marker", "citation",
                   "hotfix", "poc", "p0", "p1", "p2", "p3", "p4", "critical", "major", "minor",
                   # Meeting-note scaffolding and Jira field labels are not public technologies.
                   "comment", "comments", "component", "components", "description", "docx", "fields",
                   "from", "labels", "meeting", "memo", "notes", "optimizer", "priority", "reader",
                   "summary", "writer",
                   # Deployment environments are internal context, not public technologies.
                   "prod", "production", "stage", "staging", "qa", "dev", "development"}

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

_CREATION_CONTROL_WORDS = {
    "epic", "task", "ticket", "issue", "story", "feature", "improvement",
    "create", "make", "add", "select", "choose", "proceed", "request", "please",
    "에픽", "태스크", "티켓", "작업", "이슈", "스토리", "피처", "임프로브먼트",
    "생성", "추가", "선택", "진행", "요청", "부탁", "알아서", "기존", "신규", "새로",
    "범위", "마감", "최소", "기능", "하나", "한개", "네가", "제가", "우리가",
    "위한", "위해", "참고", "참조",
}

_CREATION_CONTROL_PATTERN = re.compile(
    r"^(?:만들|골라|고르|정해|진행해|부탁해|요청해|생성해|추가해|선택해|"
    r"해야|해주세요|해줘|해주|바랍니다)(?:[가-힣]*)$",
    re.I,
)

# Query planning needs a separate, stricter notion of a *material subject*.  Words such as
# ``구현`` and ``1차`` are useful in a ticket title, so ``_creation_subject_terms`` keeps them
# available after a real topic.  On their own, however, they are only execution controls and
# must not compile into a project-wide ``text ~ 구현`` lookup.  Keep this guard vocabulary
# separate so normal subject ranking and ASK/navigation queries do not change.
_CREATION_SUBJECT_GUARD_MARKER = "creation_target_required"
_CREATION_TARGET_REQUIRED_REASON = (
    "생성/중복 조회에 사용할 구체적인 기술·업무 대상이 없고, "
    "parent·범위·단계·마감 같은 실행 필드만 있어 조회를 생략함"
)
_CREATION_SUBJECT_GUARD_CONTROLS = _CREATION_CONTROL_WORDS | {
    "parent", "top", "level", "scope", "phase", "stage", "due", "deadline",
    "existing", "current", "first", "mvp", "implement", "implementation",
    "develop", "development", "deploy", "deployment", "validate", "validation",
    "build", "change", "fix", "improve", "improvement", "optimize", "optimization",
    "refactor", "refactoring", "update", "upgrade", "migrate", "migration",
    "work", "plan", "finish", "complete", "completion", "execute", "execution", "run",
    "enhance", "enhancement", "service", "system", "process", "application", "app",
    "module", "code", "data",
    "i", "me", "my", "you", "your",
    "은", "는", "이", "가", "을", "를", "에", "로", "차",
    "나", "내", "저", "제", "너", "네",
    "부모", "상위", "최상위", "단계", "기한", "마감일", "연결", "지정",
    "구현", "개발", "적용", "배포", "검증", "완료", "개선", "변경", "수정",
    "구축", "최적화", "리팩터링", "마이그레이션", "전환", "업데이트",
    "서비스", "시스템", "프로세스", "애플리케이션", "앱", "모듈", "코드", "데이터", "화면",
    "선택해", "진행해",
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


def _public_github_query(public_query: str) -> str:
    """Project a privacy-safe public web subject into a bounded GitHub query.

    ``_public_external_query`` has already stripped private identifiers and untranslated
    text. GitHub does not benefit from navigation words such as ``official documentation``;
    removing them also prevents a bare "GitHub에서 찾아줘" authorization from becoming a
    content subject by itself.
    """
    noise = {"official", "documentation", "document", "docs", "github", "repository", "repo"}
    tokens = [
        token for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{1,}", str(public_query or ""))
        if token.casefold().strip(".+-") not in noise
    ]
    return " ".join(tokens[:8])


def _user_authored_text(state) -> str:
    """Return only the latest human utterance for public-disclosure decisions.

    Internal retrieval may reuse a frozen request across a verified continuation. Public
    disclosure is intentionally stricter: every web/GitHub call must be authorized and
    constructible from the *current* human utterance. A prior public technology name or web
    permission must never make a later control-only refinement disclose frozen context.
    """
    latest = last_user_text(state).strip()
    return latest or request_text(state).strip()


def _internal_user_request_text(state) -> str:
    """Return the bounded human request used by internal Jira/Confluence retrieval.

    Unlike public search, an in-scope continuation must retain the frozen subject while the
    latest turn supplies fields such as parent, phase, and due date. ``_creation_subject_literals``
    also recovers legacy checkpoints whose nominal frozen field was replaced by an interview
    answer, but only after Session has declared a true continuation.
    """
    if state.get("turn_continuation"):
        return "\n".join(_creation_subject_literals(state))
    return last_user_text(state).strip() or request_text(state).strip()


def _public_query_subject_text(state) -> str:
    """Compose a public subject only after the latest turn explicitly authorizes lookup.

    ``_external_research_allowed`` remains current-turn-only. Once a true continuation says
    merely "공식 문서도 찾아줘", the frozen human request may supply the public technology
    name without making the user repeat it. An implicit technology lookup or an unrelated new
    turn never receives frozen text.
    """
    current = _user_authored_text(state).strip()
    explicitly_authorized = any(word in current.casefold() for word in _EXTERNAL_WORDS)
    if state.get("turn_continuation") and explicitly_authorized:
        frozen = request_text(state).strip()
        if frozen and frozen != current:
            return f"{frozen}\n{current}"
    return current


def _external_research_allowed(state) -> bool:
    """일반 사내 ticket 작업에 임의 웹 검색을 붙이지 않는다.

    사용자가 외부 조사를 말했거나 CDC/StarRocks처럼 내부 module명이 아닌 고유 기술 토큰을
    요청에 쓴 경우만 허용한다. ticket key/user id/URL은 기술 토큰으로 세지 않는다.
    """
    text = _user_authored_text(state)
    low = text.lower()
    if any(w in low for w in _EXTERNAL_WORDS):
        return True
    # An explicit existing parent plus a concrete create instruction is already scoped by
    # verified internal context.  Treating every Latin acronym as a public technology made
    # `CDC 재처리` search the U.S. Centers for Disease Control and injected unrelated web
    # results into an otherwise complete Jira draft.  Explicit external-research wording
    # above still wins, and open-ended technology/meeting research keeps the normal path.
    keys = [str(key).upper() for key in (state.get("mentioned_keys") or [])
            if re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", str(key).upper())]
    explicit_parent_create = bool(
        (state.get("intent") or "") == Intent.PLAN_WORK
        and keys
        and re.search(r"(?:아래|밑|하위|상위|에픽|epic)", text, re.I)
        and re.search(r"(?:만들|생성|추가|등록|초안)", text, re.I)
        and not re.search(r"회의|미팅", text, re.I)
    )
    if explicit_parent_create:
        return False
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
    asked = _internal_user_request_text(state).casefold()
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


def _normalize_meeting_research_queries(state, plan: dict) -> None:
    """Bind informal meeting research to its technical topic and explicit sources.

    Models tend to search the format words ``회의 메모`` or an attachment filename.  Those
    are not the subject and once matched an unrelated attachment UI fixture.  For ASK-style
    meeting research, code owns three invariants: explicit ticket keys are opened, generic
    note searches use the reconstructed topic, and comment lookup stays on those keys.
    """
    try:
        from app.agent.workflow.meeting_context import is_meeting_request, meeting_subject
        if not is_meeting_request(state) or (state.get("intent") or "") != Intent.ASK:
            return
        topic = meeting_subject(state)
    except Exception:
        return
    keys = [str(key).upper() for key in (state.get("mentioned_keys") or [])
            if re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", str(key).upper())]
    generic = re.compile(r"회의|미팅|meeting|memo|notes?", re.I)
    topic_terms = [term for term in topic.split() if len(term) >= 3]
    internal_topic = topic_terms[0] if topic_terms else topic
    for query in plan.get("queries") or []:
        source = str(query.get("source") or "")
        material = " ".join(str(query.get(key) or "") for key in ("query", "where"))
        if source == "confluence" and topic:
            query["query"], query["where"] = internal_topic, ""
        elif source == "comments" and topic:
            query["query"], query["where"] = internal_topic, ""
        elif source == "web" and topic:
            query["query"], query["where"] = f"{topic} official documentation", ""
        elif source == "jira" and topic and generic.search(material):
            query["query"], query["where"] = topic, ""

    if keys and not any(
            q.get("source") == "jira" and any(re.search(
                rf"(?:^|\bin\s*\([^)]*){re.escape(key)}(?:\b|\))",
                str(q.get("where") or ""), re.I) for key in keys)
            for q in plan.get("queries") or []):
        plan.setdefault("queries", []).insert(0, {
            "id": "meeting-explicit-tickets", "source": "jira", "query": "",
            "where": "key in (" + ", ".join(keys) + ")", "order_by": "",
            "fields": ["key", "summary", "description", "status", "assignee", "duedate", "comment"],
            "completeness": "all", "page_size": min(50, max(1, len(keys))), "depends_on": [],
        })
    if topic:
        for source in ("jira", "confluence"):
            if any(q.get("source") == source and topic.casefold() in str(q.get("query") or "").casefold()
                   for q in plan.get("queries") or []):
                continue
            plan.setdefault("queries", []).append({
                "id": f"meeting-topic-{source}", "source": source, "query": topic, "where": "",
                "order_by": "updated DESC", "fields": [], "completeness": "all",
                "page_size": 25, "depends_on": [],
            })


def _creation_subject_literals(state) -> list[str]:
    """Return the frozen creation request followed by the latest human refinement.

    ``request_text`` is the normal frozen authority. Older checkpoints and one
    pre-research interview path could nevertheless overwrite it with a short answer such
    as ``Epic은 네가 골라줘``. On an explicit continuation only, recover the closest
    earlier *human* utterance whose literal technical anchors agree with the typed
    RequestPlan. The plan is used solely to choose between human strings; it can never
    introduce a search term. A topic-change turn never scans old conversation.
    """
    frozen = request_text(state).strip()
    latest = last_user_text(state).strip()
    original = frozen
    # Session owns the context-boundary decision. Inferring continuation here from stale
    # questions/structure would let an unrelated new request resurrect an older topic.
    continuation = bool(state.get("turn_continuation"))
    if continuation:
        plan = state.get("request_plan") or {}
        plan_material = " ".join((
            str(plan.get("goal") or ""),
            json.dumps(plan.get("tasks") or [], ensure_ascii=False, default=str),
            " ".join(str(value) for value in (state.get("keywords") or [])),
        ))
        plan_anchors = {value.casefold() for value in _retrieval_anchors(plan_material)}
        # A previous QueryRunner turn may have opened the exact Jira records before asking
        # for parent/scope fields. Those details are bounded, verified, and reset by Session
        # on a new request. Their subject vocabulary is therefore a safe relevance check for
        # choosing an earlier *human* utterance; it never introduces a term by itself.
        ledger = state.get("materialized_ticket_sources") or {}
        ledger_material = json.dumps(
            (ledger.get("ticketDetails") or []) if isinstance(ledger, dict) else [],
            ensure_ascii=False, default=str,
        )
        ledger_anchors = {value.casefold() for value in _retrieval_anchors(ledger_material)}
        authority_anchors = plan_anchors | ledger_anchors
        original_anchors = {value.casefold() for value in _retrieval_anchors(original)}
        best_overlap = len(authority_anchors & original_anchors)
        humans = [str(getattr(message, "content", "") or "").strip()
                  for message in (state.get("messages") or [])
                  if getattr(message, "type", "") == "human"]
        # The final human row is ``latest``. Search backward so the closest qualifying
        # pre-interview request wins without pulling an older, unrelated conversation.
        if humans and latest and humans[-1] == latest:
            humans = humans[:-1]
        for candidate in reversed([value for value in humans if value]):
            anchors = {value.casefold() for value in _retrieval_anchors(candidate)}
            overlap = len(authority_anchors & anchors)
            if overlap >= 2 and overlap > best_overlap:
                original, best_overlap = candidate, overlap

    rows = []
    for value in (original, latest):
        if value and value not in rows:
            rows.append(value)
    return rows


def _creation_guard_token(raw: str) -> str:
    """Return one authoritative material token, or empty for execution-only control text."""
    value = str(raw or "").strip().strip(".,;:!?…()[]{}\"'`")
    if not value:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d+(?:차|개|건)?", value):
        return ""
    # Normalize only common grammar suffixes.  This is not a semantic stemmer: it merely
    # lets ``범위는`` and ``구현까지`` meet their explicit control vocabulary below.
    for suffix in ("으로부터", "에서는", "으로", "에서", "에게", "까지", "부터",
                   "하는", "하며", "하고", "해서", "해야해", "해야", "해주세요",
                   "해줘", "로", "을", "를", "은", "는", "이", "가", "의", "에"):
        if value.endswith(suffix) and len(value) - len(suffix) >= 1:
            value = value[:-len(suffix)]
            break
    folded = value.casefold()
    if (not value or folded in _CREATION_SUBJECT_GUARD_CONTROLS
            or _CREATION_CONTROL_PATTERN.fullmatch(value)):
        return ""
    return value


def _authoritative_creation_material_anchors(state) -> list[str]:
    """Extract subject anchors only from the current/frozen human creation request.

    RequestArchitect keywords are intentionally excluded: after a context-boundary mistake a
    model can remember the old topic, but that recollection is not authoritative retrieval
    permission.  A true continuation already exposes its frozen human request through
    ``_creation_subject_literals``.
    """
    anchors: list[str] = []
    seen: set[str] = set()
    authored = "\n".join(_creation_subject_literals(state))
    for raw in re.findall(
            r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*-\d+(?![A-Za-z0-9])|"
            r"[A-Za-z][A-Za-z0-9_.+-]{1,}|[가-힣]+|\d+차|\d{4}-\d{2}-\d{2}",
            authored, re.I):
        # Exact ticket identities are handled structurally by ``mentioned_keys``/``where``;
        # they are not lexical subject terms.
        if re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", raw, re.I):
            continue
        value = _creation_guard_token(raw)
        folded = value.casefold()
        if value and folded not in seen:
            seen.add(folded)
            anchors.append(value)
    return anchors


def _creation_anchor_specificity(value: str) -> int:
    """Classify one non-control subject anchor without a corpus-dependent rarity guess.

    Structured identifiers and technical Latin names are independently specific enough for
    a duplicate lookup (``fdc.table_id``, ``DeltaSketch``). A Korean domain noun of three or more
    syllables is also a usable target. Short ordinary nouns need a second independent anchor.
    """
    token = str(value or "").strip()
    if not token:
        return 0
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]{1,}", token):
        if re.search(r"[0-9_.+-]", token):
            return 3
        # Product-style casing/acronyms are identifier-shaped. A longer lowercase token is
        # a conservative rarity proxy; short generic English nouns still need a partner.
        identifier_cased = (
            token.isupper() or token[:1].isupper()
            or any(character.isupper() for character in token[1:])
        )
        return 2 if identifier_cased or len(token) >= 6 else 1
    if re.fullmatch(r"[가-힣]+", token):
        return 2 if len(token) >= 3 else 1
    return 1


def _creation_target_required_reason(state, explicit_keys: list[str] | None = None) -> str:
    """Return a fail-loud marker only for demonstrably control-only PLAN_WORK text.

    One high-information identifier/technical anchor is sufficient; two weaker independent
    nouns are also sufficient. Exact referenced ticket keys remain safe structural reads. The
    r25 failure (``Epic ... 1차 구현 ... 마감``) contains only execution controls and is
    rejected before Jira execution.
    """
    if str(state.get("intent") or "") != Intent.PLAN_WORK:
        return ""
    literals = "\n".join(_creation_subject_literals(state)).strip()
    if not literals:
        return ""
    exact = [str(key).strip().upper() for key in (explicit_keys or [])
             if re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", str(key).strip(), re.I)]
    if exact or re.search(r"\b[A-Z][A-Z0-9]*-\d+\b", literals, re.I):
        return ""
    anchors = _authoritative_creation_material_anchors(state)
    if len(anchors) >= 2 or any(_creation_anchor_specificity(value) >= 2
                                for value in anchors):
        return ""
    return _CREATION_TARGET_REQUIRED_REASON


def _query_plan_creation_target_required(plan: dict) -> str:
    """Trust only compiler provenance paired with the no-executable-read invariant."""
    if ((plan or {}).get("compiler_guard") == _CREATION_SUBJECT_GUARD_MARKER
            and not ((plan or {}).get("queries") or [])):
        return _CREATION_TARGET_REQUIRED_REASON
    return ""


def _explicit_creation_parent_keys(state) -> set[str]:
    """Return only keys that human text unambiguously assigns as a parent.

    ``mentioned_keys`` is a reference set, not a hierarchy contract. A request may say
    ``DL-123 참고해서 Epic 골라줘``; treating that related Task as an explicit parent
    suppresses the very Epic search the user delegated. Keep the boundary syntactic and
    conservative. Runtime tier validation remains Work/Auditor's responsibility.
    """
    text = "\n".join(_creation_subject_literals(state))
    keys = {
        value.upper() for value in [
            *re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", text, re.I),
            *(str(key) for key in (state.get("mentioned_keys") or [])),
        ]
        if re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", str(value).strip(), re.I)
    }
    confirmed: set[str] = set()
    for key in keys:
        ticket = re.escape(key)
        patterns = (
            # `DL-10 아래`, `DL-10 Epic 아래`, `DL-10을 부모 Task로`
            rf"\b{ticket}\b\s*(?:은|는|이|가|을|를)?\s*"
            rf"(?:(?:Epic|에픽|상위\s*(?:Epic|에픽|티켓|Task)|부모\s*(?:티켓|Task|태스크)?)\s*)?"
            rf"(?:아래|밑(?:에)?|하위|under)(?=\s|에|로|$|[,.])",
            rf"\b{ticket}\b\s*(?:은|는|이|가|을|를)?\s*"
            rf"(?:상위|부모)\s*(?:Epic|에픽|티켓|Task|태스크)?\s*(?:으로|로)?",
            # `상위 Epic은 DL-10`, `부모 티켓: DL-10`
            rf"(?:상위|부모)\s*(?:Epic|에픽|티켓|Task|태스크)?\s*"
            rf"(?:은|는|이|가|:)?\s*\b{ticket}\b",
            # `Epic DL-10 아래`, `Epic DL-10에 Task 추가`
            rf"(?:Epic|에픽)\s*(?:키|티켓)?\s*(?:은|는|:)?\s*\b{ticket}\b\s*"
            rf"(?:아래|밑(?:에)?|하위|에\s*(?:Task|태스크|티켓))",
            rf"\b{ticket}\b\s*(?:에|으로)\s*(?:Sub-?Task|서브\s*태스크|Task|태스크|티켓)"
            rf"[^.\n]{{0,24}}(?:생성|추가|만들|등록)",
        )
        if any(re.search(pattern, text, re.I) for pattern in patterns):
            confirmed.add(key)
    return confirmed


def _creation_subject_terms(state, limit: int = 5) -> list[str]:
    """Compile the duplicate-search subject from user-authored text.

    Request Architect keywords are useful hints but are model-authored and can contain a
    control decision such as ``Epic 생성`` even when the user said to select an existing
    Epic. The frozen original request is therefore the authority. We strip conversational
    control/hierarchy words while preserving domain actions such as ``전환`` or ``구축``;
    those actions can distinguish an implementation ticket from a research ticket.
    """
    terms: list[str] = []
    seen: set[str] = set()

    def normalize_token(raw: str, *, keyword_phrase: bool = False) -> str:
        value = str(raw or "").strip().strip(".,;:!?…()[]{}\"'`")
        if not value:
            return ""
        # Korean particles and connective endings are grammar, not search subjects.
        for suffix in ("으로부터", "에서는", "으로", "에서", "에게", "까지", "부터",
                       "하는", "하며", "하고", "해서", "해야해", "해야", "해주세요",
                       "해줘", "해", "로", "을", "를", "은", "는", "이", "가", "의", "에"):
            if value.endswith(suffix) and len(value) - len(suffix) >= 2:
                value = value[:-len(suffix)]
                break
        folded = value.casefold()
        if (not value or folded in _CREATION_CONTROL_WORDS
                or _CREATION_CONTROL_PATTERN.fullmatch(value)
                or re.fullmatch(r"\d+(?:차|개|건)?|\d{4}-\d{2}-\d{2}", value)):
            return ""
        return value

    def keyword_token_set() -> set[str]:
        hinted: set[str] = set()
        for phrase in state.get("keywords") or []:
            tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{1,}|[가-힣]+", str(phrase))
            for token in tokens:
                value = normalize_token(token, keyword_phrase=len(tokens) > 1)
                if value:
                    hinted.add(value.casefold())
        return hinted

    hinted = keyword_token_set()
    for literal in _creation_subject_literals(state):
        candidates = []
        local_seen: set[str] = set()
        for index, token in enumerate(re.findall(
                r"[A-Za-z][A-Za-z0-9_.+-]{1,}|[가-힣]+|\d{4}-\d{2}-\d{2}", literal)):
            value = normalize_token(token)
            if not value or value.casefold() in local_seen:
                continue
            local_seen.add(value.casefold())
            # Model keywords never introduce text, but overlap with the literal request is a
            # useful semantic rank. Public/technical identifiers are next. Conversational
            # lead-ins therefore cannot consume the five-term budget before the actual subject.
            internal = value.casefold() in _INTERNAL_LATIN
            is_technical = bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]{1,}", value)) \
                and not internal
            priority = (0 if value.casefold() in hinted else
                        1 if is_technical else 3 if internal else 2)
            candidates.append((priority, index, value))

        anchors = [index for priority, index, _value in candidates if priority <= 1]
        if anchors:
            first_anchor = min(anchors)
            # Free-form requests often start with an arbitrarily long conversational clause.
            # Content after the first verified keyword/technical anchor is more likely to finish
            # that subject; pre-anchor prose remains available only when the budget has room.
            candidates = [
                (4 if priority == 2 and index < first_anchor else priority, index, value)
                for priority, index, value in candidates
            ]

        # Frozen original text has source precedence. The latest interview answer can add a
        # genuine qualifier only when the original subject did not fill the bounded query.
        selected = sorted(sorted(candidates, key=lambda row: (row[0], row[1]))[:limit],
                          key=lambda row: row[1])
        for _priority, _index, value in selected:
            if value.casefold() in seen:
                continue
            seen.add(value.casefold())
            terms.append(value)
            if len(terms) >= limit:
                return terms

    # Only supplement a sparse literal subject. This is deliberately lower precedence:
    # keywords are semantic hints generated by a model, not an authority for user intent.
    if len(terms) < 2:
        for phrase in state.get("keywords") or []:
            tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{1,}|[가-힣]+", str(phrase))
            for token in tokens:
                value = normalize_token(token, keyword_phrase=len(tokens) > 1)
                if value and value.casefold() not in seen:
                    seen.add(value.casefold())
                    terms.append(value)
                if len(terms) >= limit:
                    return terms
    return terms


def _materialized_parent_reference_keys(state, subject_terms: list[str]) -> list[str]:
    """Return relevant previously opened Epics for exact continuation revalidation.

    A first research turn can open the correct Epic before the user delegates parent choice
    in a later turn. Search hits alone are never promoted. We seed only successful, bounded
    ``get_ticket`` details whose opened type is Epic and whose material overlaps at least two
    retained subject anchors; QueryRunner then opens the exact key again and confirms its type.
    """
    if not state.get("turn_continuation"):
        return []
    ledger = state.get("materialized_ticket_sources") or {}
    if not isinstance(ledger, dict):
        return []
    wanted = {str(value).strip().casefold() for value in subject_terms if str(value).strip()}
    if len(wanted) < 2:
        return []
    selected: list[str] = []
    for row in ledger.get("ticketDetails") or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        fields = row.get("fields") or {}
        field_type = fields.get("issuetype")
        if isinstance(field_type, dict):
            field_type = field_type.get("name")
        raw_type = row.get("type") or row.get("issuetype") or field_type
        if str(raw_type or "").strip().casefold() != "epic":
            continue
        key = str(row.get("key") or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", key):
            continue
        material = " ".join(str(row.get(name) or "") for name in (
            "summary", "title", "description",
        )).casefold()
        overlap = {term for term in wanted if term in material}
        if len(overlap) >= 2 and key not in selected:
            selected.append(key)
        if len(selected) == 2:
            break
    return selected


def _compile_compact_query_plan(out: dict) -> dict:
    """Expand the model's compact retrieval AST into the runtime QueryPlan contract."""
    compact = CompactQueryPlan.model_validate(out)
    page_sizes = {"web": 5, "github": 5, "comments": 25}
    queries = []
    for index, read in enumerate(compact.reads, 1):
        queries.append({
            "id": f"read-{index}-{read.source}",
            "source": read.source,
            "query": read.subject.strip(),
            "where": read.where.strip(),
            "order_by": "updated DESC",
            "fields": [],
            "completeness": "all" if read.exhaustive else "page",
            "page_size": page_sizes.get(read.source, 50),
            "depends_on": [],
        })
    return QueryPlan(
        queries=queries, joins=[], uncertainty=compact.uncertainty,
    ).model_dump(exclude={"compiler_guard"})


def _compact_request_context(state) -> dict:
    """Project only authoritative retrieval inputs; omit verbose/stale conversation."""
    request = request_text(state)
    latest = last_user_text(state)
    tasks = []
    for task in (state.get("request_plan") or {}).get("tasks") or []:
        if not isinstance(task, dict):
            continue
        tasks.append({
            "kind": str(task.get("kind") or ""),
            "instruction": str(task.get("instruction") or "")[:500],
            "criteria": [str(value)[:240]
                         for value in (task.get("completion_criteria") or [])[:4]],
        })
        if len(tasks) >= 8:
            break
    return {
        "request_excerpt": _bounded_retrieval_excerpt(request, 1800),
        "latest_user_excerpt": (_bounded_retrieval_excerpt(latest, 900)
                                if latest and latest != request else ""),
        "literal_anchors": _retrieval_anchors(f"{request}\n{latest}"),
        "tasks": tasks,
        "keywords": [str(value)[:120] for value in (state.get("keywords") or [])[:12]],
        "ticket_keys": [str(value) for value in (state.get("mentioned_keys") or [])[:20]],
    }


def _bounded_retrieval_excerpt(text: str, limit: int) -> str:
    """Bound verbose minutes/attachments while retaining both request and final instruction."""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    head = limit * 3 // 5
    tail = limit - head
    omitted = len(value) - limit
    return f"{value[:head]}\n[… {omitted} chars omitted; see atomic tasks/anchors …]\n{value[-tail:]}"


def _retrieval_anchors(text: str, limit: int = 48) -> list[str]:
    """Extract exact identifiers from the full text, including material outside excerpts."""
    found, seen = [], set()
    # Scan strict identifiers first across the entire input. Otherwise dozens of ordinary
    # English words near the beginning of a long attachment could consume the cap before a
    # ticket key or table identifier near the middle.
    patterns = (
        re.compile(
            r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*-\d+(?![A-Za-z0-9])|"
            r"(?<![A-Za-z0-9_.])(?:skcc\.)?[a-z]\d{3,}(?![A-Za-z0-9_])|"
            r"(?<![A-Za-z0-9_.])[A-Za-z][A-Za-z0-9]*(?:[_.+-][A-Za-z0-9]+)+(?![A-Za-z0-9_.])",
            re.I,
        ),
        re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]{2,}\b"),
    )
    for pattern in patterns:
        for match in pattern.finditer(str(text or "")):
            value = match.group(0).strip(".,;:!?()[]{}")
            folded = value.casefold()
            if not value or folded in seen or folded in _INTERNAL_LATIN:
                continue
            seen.add(folded)
            found.append(value)
            if len(found) >= limit:
                return found
    return found


def _reject_unsupported_relational_plan(plan: dict) -> None:
    """Fail loudly instead of pretending QueryRunner performs cross-read substitution."""
    joins = [value for value in (plan.get("joins") or []) if str(value).strip()]
    dependent = [str(row.get("id") or "<unnamed>")
                 for row in (plan.get("queries") or []) if row.get("depends_on")]
    if joins or dependent:
        raise ValueError(
            "QueryPlan dependencies/joins are unsupported; compile independent reads or add "
            f"a deterministic domain service (depends_on={dependent}, joins={joins})"
        )


def _deterministic_plan_retrieval(state) -> bool:
    """Whether a write plan needs only compiler-owned prerequisite retrieval.

    Work Architect's recovery intentionally refuses some complex drafts. Query planning
    has a narrower decision: a write-only RequestPlan always needs the same scoped Jira
    duplicate/target read plus any privacy-safe external lookup. It does not need a model
    to paraphrase that requirement. Explicit research/query/analyze tasks keep the semantic
    path because they may require additional sources or distinct filters.
    """
    if (state.get("intent") or "") != Intent.PLAN_WORK:
        return False
    try:
        from app.agent.workflow.meeting_context import is_meeting_request
        if is_meeting_request(state):
            return False
    except Exception:
        return False

    try:
        from app.agent.workflow.agents.work_architect import _recover_delegated_creation
        if _recover_delegated_creation(state):
            return True
    except Exception:
        pass

    tasks = [task for task in (state.get("request_plan") or {}).get("tasks") or []
             if isinstance(task, dict)]
    if not tasks or not any(bool(task.get("write_intent")) for task in tasks):
        return False
    semantic_read_kinds = {"query", "research", "analyze"}
    return not any(str(task.get("kind") or "") in semantic_read_kinds for task in tasks)


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
    plan.pop("compiler_guard", None)
    asked = _internal_user_request_text(state).strip()
    explicit_comments = any(word in asked.casefold() for word in _COMMENT_WORDS)
    explicit_people = bool(re.search(
        r"담당|할당|배정|누가|사람|인원|멤버|member|assignee", asked, re.I))
    # New-work history needs one scoped duplicate lookup, not a status-by-status fan-out.
    # The model used to emit five equivalent Jira queries plus speculative people/comments
    # reads. Apart from latency, pseudo-JQL in those rows could silently return no evidence.
    # Keep explicitly requested source classes; replace all Jira variants below with one
    # canonical query that the deterministic runner understands.
    plan["queries"] = [
        query for query in (plan.get("queries") or [])
        if query.get("source") != "jira"
        and (query.get("source") != "comments" or explicit_comments)
        and (query.get("source") != "people" or explicit_people)
    ]
    explicit_keys = [
        str(key).upper() for key in (state.get("mentioned_keys") or [])
        if re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", str(key).upper())
    ]
    target_required = _creation_target_required_reason(state, explicit_keys)
    if target_required:
        # This field is absent from CompactQueryPlan and is reset at ``apply`` entry, so only
        # this deterministic compiler can establish the runtime no-read provenance.
        plan["compiler_guard"] = _CREATION_SUBJECT_GUARD_MARKER
        plan["queries"] = []
        if target_required not in plan.setdefault("uncertainty", []):
            plan["uncertainty"].append(target_required)
        return
    terms = _creation_subject_terms(state)
    if not terms and not explicit_keys:
        return
    public_technical = [term for term in terms
                        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]{1,}", term)
                        and not re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", term, re.I)
                        and term.casefold() not in _INTERNAL_LATIN]
    # QueryRunner has an intentional 2-of-3 compiler for public technology triples. Keep
    # that bounded recall boundary instead of turning five mixed tokens into strict AND:
    # related writer/reader/validation tickets often omit the umbrella product or Korean
    # deliverable word, while the exact-duplicate guard still opens and compares each body.
    duplicate_terms = public_technical[:3] if len(public_technical) >= 3 else terms[:5]
    where = "key in (" + ", ".join(explicit_keys) + ")" if explicit_keys else ""
    plan.setdefault("queries", []).insert(0, {
        "id": "internal-duplicate-check",
        "source": "jira",
        "query": "" if explicit_keys else " ".join(duplicate_terms),
        "where": where,
        "order_by": "updated DESC",
        "fields": ["key", "summary", "status", "issuetype", "assignee", "updated"],
        "completeness": "all",
        "page_size": 50,
        "depends_on": [],
    })
    # When the user delegates an existing Epic choice, selection itself needs an opened,
    # auditable source. Work drafting may choose from the same Jira scope later, but that
    # service result is not a Research evidence artifact. Acquire a narrow Epic candidate
    # set here so QueryRunner materializes the selected parent before approval rendering.
    user_text = "\n".join(_creation_subject_literals(state))
    delegates_parent = bool(re.search(
        r"(?:Epic|에픽)[^.\n]{0,32}(?:골라|고르|선택|정해|알아서)|"
        r"(?:골라|고르|선택|정해)[^.\n]{0,24}(?:Epic|에픽)",
        user_text, re.I,
    ))
    explicit_parent_keys = _explicit_creation_parent_keys(state)
    if delegates_parent and not explicit_parent_keys:
        # Three Latin anchors use QueryRunner's stable 2-of-3 lexical boundary. This keeps
        # a relevant parent that omits an umbrella product name without broad all-Epic reads.
        non_key_terms = [term for term in terms
                         if not re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", term, re.I)]
        parent_terms = public_technical[:3] if len(public_technical) >= 2 else non_key_terms[:3]
        reference_keys = [key for key in explicit_keys if key not in explicit_parent_keys]
        for key in _materialized_parent_reference_keys(state, terms):
            if key not in reference_keys:
                reference_keys.append(key)
        if parent_terms or reference_keys:
            plan["queries"].insert(1, {
                "id": "parent-candidate-check", "source": "jira",
                "query": " ".join(parent_terms), "where": "issueType = Epic",
                "order_by": "updated DESC",
                "fields": ["key", "summary", "status", "issuetype", "assignee", "updated"],
                "completeness": "all", "page_size": 50, "depends_on": [],
                # Compiler-owned relationship seed. QueryRunner opens these exact tickets,
                # follows parent/Epic fields, and runs the lexical query only if none resolve.
                "parent_reference_keys": reference_keys,
            })


class QuerySpecialist(StructuredAgent):
    name = Node.QUERY_SPECIALIST

    def node(self):
        model_node = super().node()

        def run(state):
            # A concrete delegated creation still needs the Jira duplicate check, but it
            # does not need a small model to restate that single deterministic query. In
            # local-model measurements the model expanded this one query until max_tokens,
            # then spent a second call trying to repair the truncated JSON. Reuse the same
            # scope/pagination normalizer below so this fast path changes neither search
            # coverage nor project scoping.
            if _deterministic_plan_retrieval(state):
                result = self.apply(state, {
                    "queries": [], "joins": [], "uncertainty": [],
                })
                result["trace"] = note(
                    state, self.name,
                    f"결정적 선행 조회 {len((result.get('query_plan') or {}).get('queries') or [])}개 설계",
                )
                return result
            return model_node(state)

        return run

    def system(self, state):
        return persona(state, SYSTEM_QUERY_SPECIALIST, lite=True)

    def task(self, state):
        context = json.dumps(
            _compact_request_context(state), ensure_ascii=False, separators=(",", ":"))
        return (
            "# Task\n\nReturn a compact retrieval AST, not an answer or action. "
            "Each read contains only source, literal search subject, structural where, and "
            "whether every page is required. Preserve the user's subject exactly. Never put "
            "ticket creation, update, selection, or workflow instructions in `subject`; those "
            "describe the requested action, not evidence to retrieve. Use at most 8 reads; "
            "keep every subject, where, and uncertainty string within 240 characters.\n\n"
            "## Authoritative Retrieval Context\n\n" + context
        )

    def schema(self):
        return CompactQueryPlan.model_json_schema()

    def apply(self, state, out):
        # Direct callers and persisted fixtures may still pass the historical runtime
        # QueryPlan. Model transport always uses the compact schema above; accepting the
        # old shape here keeps OpenAI/native callers and stored state migration-safe.
        plan = (_compile_compact_query_plan(out)
                if isinstance(out, dict) and "reads" in out
                else QueryPlan.model_validate(out).model_dump())
        # ``uncertainty`` is model-owned prose and cannot claim compiler provenance. Legacy
        # runtime plans may carry the typed field, but re-applying QuerySpecialist must
        # recompute it from the current/frozen human authority rather than trust the input.
        plan.pop("compiler_guard", None)
        plan["uncertainty"] = [
            str(value) for value in (plan.get("uncertainty") or [])
            if not str(value or "").strip().startswith(
                _CREATION_SUBJECT_GUARD_MARKER + ":")
        ]
        # Validate the contract before normalization can prune a missing dependency and make
        # an unsupported relational plan appear independent.
        _reject_unsupported_relational_plan(plan)
        # Source coverage is a user contract, not a model preference.
        _ensure_creation_duplicate_query(state, plan)
        _ensure_explicit_comment_query(state, plan)
        _normalize_meeting_research_queries(state, plan)
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
            authored_public_text = _public_query_subject_text(state)
            # Meeting normalization may split a private identifier (`secret_client_code`
            # → `secret client code`, `skcc.x1402` → person-like fragments). Never use that
            # derived topic as disclosure provenance. Every external source is compiled from
            # the original human text through the same private-token sanitizer.
            private_authored = bool(
                _PRIVATE_EXTERNAL_PATTERN.search(authored_public_text)
                or _contains_known_user_token(authored_public_text)
            )
            public_query = _public_external_query(authored_public_text)
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
                unanchored_translation_allowed = not original_terms and not private_authored
                if translated and (original_terms & translated_terms
                                   or unanchored_translation_allowed):
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
            # GitHub uses the same disclosure boundary as web. Model-authored repository
            # searches are candidates only: sanitize them, require overlap with the current
            # authorized public subject, and rebuild the rows so no internal identifier can
            # reach ``search_github``. An explicit current-turn GitHub request receives one
            # compiler-owned row even when the model omitted it.
            github = [q for q in plan["queries"] if q.get("source") == "github"]
            github_requested = "github" in _user_authored_text(state).casefold()
            github_base = _public_github_query(public_query)
            github_candidates = [github_base]
            github_terms = set(_query_identity(github_base).split())
            for query in github:
                translated = _public_github_query(
                    _safe_model_external_query(query.get("query") or ""),
                )
                translated_terms = set(_query_identity(translated).split())
                unanchored_translation_allowed = not github_terms and not private_authored
                if translated and (github_terms & translated_terms
                                   or unanchored_translation_allowed):
                    github_candidates.append(translated)
            github_variants = []
            for candidate in github_candidates:
                if candidate and _query_identity(candidate) not in {
                        _query_identity(existing) for existing in github_variants}:
                    github_variants.append(candidate)
            github_variants = github_variants[:2]
            plan["queries"] = [q for q in plan["queries"] if q.get("source") != "github"]
            github_template = github[0] if github else {
                "id": "external-github", "source": "github", "where": "",
                "order_by": "updated DESC", "fields": [], "completeness": "page",
                "page_size": 5, "depends_on": [],
            }
            if github or github_requested:
                used_ids = {q.get("id") for q in plan["queries"]}
                for index, candidate in enumerate(github_variants):
                    query = dict(github_template)
                    wanted = str(github_template.get("id") or "external-github") if index == 0 \
                        else "external-github-alias"
                    while wanted in used_ids:
                        wanted += "-2"
                    used_ids.add(wanted)
                    query.update({"id": wanted, "source": "github", "query": candidate,
                                  "where": ""})
                    plan["queries"].append(query)
                if not github_variants:
                    plan.setdefault("uncertainty", []).append(
                        "GitHub research was requested, but no privacy-safe public subject was available.")
        # Normalization can deterministically rebuild public/comment rows after the creation
        # compiler runs. A target-required plan is terminal for acquisition, so reassert the
        # exact no-executable-read half of the provenance invariant at the final boundary.
        if plan.get("compiler_guard") == _CREATION_SUBJECT_GUARD_MARKER:
            plan["queries"] = []
        return {"query_plan": plan,
                "trace": note(state, self.name, f"조회 {len(plan['queries'])}개 설계")}


__all__ = ["QuerySpecialist", "_external_research_allowed", "_public_external_query",
           "_public_github_query", "_user_authored_text", "_internal_user_request_text",
           "_public_query_subject_text",
           "_safe_model_external_query", "_ensure_explicit_comment_query",
           "_normalize_meeting_research_queries",
           "_known_user_tokens", "_strip_known_user_tokens", "_jira_query_is_only_people",
           "_normalize_model_jira_query", "_normalize_query_fields",
           "_dedupe_equivalent_queries", "_ensure_creation_duplicate_query",
           "_creation_subject_literals", "_explicit_creation_parent_keys",
           "_creation_subject_terms", "_authoritative_creation_material_anchors",
           "_creation_target_required_reason", "_query_plan_creation_target_required",
           "_materialized_parent_reference_keys",
           "_compile_compact_query_plan",
           "_compact_request_context", "_bounded_retrieval_excerpt", "_retrieval_anchors",
           "_reject_unsupported_relational_plan", "_deterministic_plan_retrieval"]
