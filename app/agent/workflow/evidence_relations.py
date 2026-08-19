"""Shared, product-neutral evidence relation parsing.

The Work Architect and final claim gate must agree on actor, action, material, direction,
and uncertainty. Keep this module dependency-free so neither consumer invents a parallel
regex vocabulary for individual products or evaluation cases.
"""

from __future__ import annotations

import re as _re
from typing import TypedDict


class EvidenceRelation(TypedDict):
    """Stable public contract shared by drafting and final claim grounding."""

    fact: str
    actors: list[str]
    actions: list[str]
    objects: list[str]
    actor_roles: dict[str, list[str]]


_EVIDENCE_UNCONFIRMED = _re.compile(
    r"확인\s*중|진행\s*중|검증\s*중|미\s*(?:확인|확정|검증)|"
    r"(?:지원\s*여부|사실|결과|동작).{0,36}"
    r"(?:(?:확정|확인)(?:하지|되지)\s*않|검증\s*필요)|"
    r"아직.{0,36}(?:확정|확인|검증|지원)(?:하지|되지)\s*않|"
    r"not\s+(?:yet\s+)?(?:confirmed|verified)|unconfirmed|unverified|pending",
    _re.I,
)
_EVIDENCE_GATE = _re.compile(
    r"(?:검증|확인|증거|승인|조건).{0,36}(?:전|전에는|전까지).{0,80}"
    r"(?:금지|보류|승인하지|반영하지|진행하지|허용하지)|"
    r"(?:금지|보류|승인하지|반영하지|진행하지|허용하지).{0,80}"
    r"(?:검증|확인|증거|승인|조건)|"
    r"(?:do\s+not|must\s+not|cannot).{0,60}(?:rollout|deploy|release|approve)",
    _re.I,
)
_EVIDENCE_COMPLETED_RESULT = _re.compile(
    r"완료(?:했|되|함)|확보(?:했|되|함)|확정(?:했|되|함)|"
    r"성공(?:했|함)|첨부(?:했|되|함)|적용(?:했|되|함)|구축(?:했|되|함)|"
    r"(?:has|was|were)\s+(?:completed|verified|attached|secured)",
    _re.I,
)
_EVIDENCE_NEGATIVE_COMPLETION = _re.compile(
    r"미\s*완료|"
    r"(?:완료|확보|확정|성공|첨부|적용|구축|검증|확인)"
    r".{0,24}(?:되지\s*않|하지\s*않|못하|못했|아니(?:다|었)|전(?:에는|까지|이라도)?\b)|"
    r"\b(?:incomplete|not\s+(?:yet\s+)?completed|has\s+not\s+been\s+completed|"
    r"did\s+not\s+(?:complete|verify)|before\s+(?:completion|verification))\b",
    _re.I,
)
_EVIDENCE_RELATION_ACTION = _re.compile(
    r"생성|만든|작성|산출|소비|읽(?:기|는|어|는다)?|활용|확인|검증|승인|완료|진행|지원|"
    r"(?<![A-Za-z0-9_])(?:generat(?:e|es|ed|ing|ion)|"
    r"produc(?:e|es|ed|ing|tion)|writ(?:e|es|ing|ten)|"
    r"creat(?:e|es|ed|ing|ion)|consum(?:e|es|ed|ing|ption)|"
    r"read(?:s|ing)?|us(?:e|es|ed|ing)|verif(?:y|ies|ied|ying|ication)|"
    r"validat(?:e|es|ed|ing|ion)|approv(?:e|es|ed|ing|al)|"
    r"complet(?:e|es|ed|ing|ion)|progress(?:es|ed|ing)?|"
    r"support(?:s|ed|ing)?)(?![A-Za-z0-9_])",
    _re.I,
)

_RELATION_STATE_STOP = {
    "아직", "여부", "확인", "확정", "검증", "완료", "진행", "지원", "결과",
    "상태", "작업", "현재", "별도", "필요", "증거", "조건", "운영", "반영", "배포", "출시",
    "전", "전에", "전까지", "후", "후에", "이후",
    "않았습니다", "했습니다", "한다", "확정하지", "중입니다", "중이며", "함께",
    "not", "yet", "confirmed", "unconfirmed", "verified", "pending", "complete",
    "completed", "verification", "status", "result", "support", "is", "are", "was",
    "were", "be", "been", "being",
}
_COMPOSITION_GENERIC_MATERIAL = {
    "support", "validation", "verification", "consumption", "consume", "consumes",
    "지원", "검증", "확인", "소비", "여부", "결과", "상태",
}
_RELATION_IDENTITY_STOP = {
    "a", "an", "the", "of", "to", "for", "from", "with", "without", "by",
    "can", "could", "may", "might", "will", "would", "shall", "should", "able",
}


def is_relation_gate(value: str) -> bool:
    """Return whether prose encodes a bounded approval or rollout gate."""
    return bool(_EVIDENCE_GATE.search(str(value or "")))


def is_relation_state_or_action(value: str) -> bool:
    """Return whether a token is lifecycle noise or a complete action token."""
    key = str(value or "").casefold()
    return key in _RELATION_STATE_STOP or bool(_EVIDENCE_RELATION_ACTION.fullmatch(key))


def is_generic_material_term(value: str) -> bool:
    """Return whether a token is too generic to identify a material relation."""
    return str(value or "").casefold() in _COMPOSITION_GENERIC_MATERIAL


def generic_material_terms() -> frozenset[str]:
    """Expose an immutable copy for callers that build bounded exclusion sets."""
    return frozenset(_COMPOSITION_GENERIC_MATERIAL)


def explicitly_unconfirmed_relation(value: str) -> bool:
    """Return explicit fact uncertainty, excluding an independent rollout gate."""
    return bool(_EVIDENCE_UNCONFIRMED.search(str(value or "")))


def is_unconfirmed_fact(value: str) -> bool:
    """Classify uncertainty before any overlapping positive lifecycle token."""
    text = str(value or "")
    explicit_uncertainty = explicitly_unconfirmed_relation(text)
    negative_completion = bool(_EVIDENCE_NEGATIVE_COMPLETION.search(text))
    # A pure "before verification, do not roll out" sentence is a gate, not a second
    # dependency. If the same sentence also explicitly says support is unconfirmed, both
    # contracts are material and remain separate.
    if _EVIDENCE_GATE.search(text) and negative_completion and not explicit_uncertainty:
        return False
    return explicit_uncertainty or negative_completion


def is_direct_positive_completion(value: str) -> bool:
    """Require an explicit positive result; issue status alone is not factual content."""
    text = str(value or "")
    return bool(_EVIDENCE_COMPLETED_RESULT.search(text)
                and not is_unconfirmed_fact(text))


def relation_terms(value: str) -> set[str]:
    """Extract conservative subject/relation terms for temporal supersession."""
    terms: set[str] = set()
    for raw in _re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,}|[가-힣]{2,}", str(value or "")):
        # Dots/hyphens are legal inside technical identifiers; edge punctuation is not.
        term = raw.strip("._-").casefold()
        if _re.fullmatch(r"[가-힣]+", term):
            term = _re.sub(r"(?:으로|에서|에게|까지|부터|처럼|보다|의|은|는|이|가|을|를)$",
                           "", term)
        if len(term) >= 2 and term not in _RELATION_STATE_STOP:
            terms.add(term)
    return terms


def relation_actor_identities(value: str) -> list[str]:
    """Extract bounded grammatical technical actors without a role-name vocabulary."""
    text = " ".join(str(value or "").split()).strip()
    actors: list[str] = []

    def add(raw: str) -> None:
        actor = str(raw or "").strip()
        key = actor.casefold()
        if (not actor or key in _RELATION_STATE_STOP
                or _EVIDENCE_RELATION_ACTION.fullmatch(actor)
                or key in {existing.casefold() for existing in actors}):
            return
        actors.append(actor)

    for match in _re.finditer(
            r"([A-Za-z][A-Za-z0-9_.-]{1,})(?=(?:의|가|이|는|은|와|과)\s*)",
            text):
        add(match.group(1))
    direct = _re.match(
        r"^\s*([A-Za-z][A-Za-z0-9_.-]{1,})\s+"
        r"(?:generates?|produces?|writes?|creates?|consumes?|reads?|uses?|"
        r"verifies?|validates?|approves?|completes?|supports|supported|supporting|"
        r"progresses?)\b",
        text, _re.I,
    )
    if direct:
        add(direct.group(1))
    by_actor = _re.search(r"\bby\s+([A-Za-z][A-Za-z0-9_.-]{1,})\b", text, _re.I)
    if by_actor:
        add(by_actor.group(1))
    first_pair = _re.match(
        r"^\s*([A-Za-z][A-Za-z0-9_.-]{1,})\s+([A-Z][A-Za-z0-9_.-]{1,})\b",
        text,
    )
    if first_pair and not _re.search(r"[가-힣]", text):
        token = first_pair.group(1)
        composite_identity = (any(char.isupper() for char in token[1:])
                              or bool(_re.search(r"[0-9_.-]", token)))
        if composite_identity and _EVIDENCE_RELATION_ACTION.search(text):
            add(token)
    return actors[:6]


def parse_relation(value: str) -> EvidenceRelation | None:
    """Extract a generic actor/action/object memo without a product or role vocabulary."""
    text = " ".join(str(value or "").split()).strip()
    actions = list(dict.fromkeys(
        match.group(0).casefold() for match in _EVIDENCE_RELATION_ACTION.finditer(text)
    ))
    terms = sorted(relation_terms(text))
    actors = relation_actor_identities(text)
    if not actions or len(terms) < 2:
        return None
    actor_keys = {actor.casefold() for actor in actors}
    objects = [
        term for term in terms
        if term not in actor_keys
        and term not in _RELATION_STATE_STOP
        and not _EVIDENCE_RELATION_ACTION.fullmatch(term)
    ]
    return {
        "fact": text[:420],
        "actors": actors[:6],
        "actions": actions[:6],
        "objects": objects[:10],
        "actor_roles": relation_actor_roles(text),
    }


def relation_action_role(action: str) -> str:
    value = str(action or "").casefold()
    if _re.search(r"생성|만든|작성|산출|generat|produc|writ|creat", value):
        return "producer"
    if _re.search(r"소비|읽|활용|consum|read|\buse", value):
        return "consumer"
    return ""


def relation_actor_roles(value: str) -> dict[str, list[str]]:
    """Bind explicit technical actors to nearby producer/consumer actions."""
    text = " ".join(str(value or "").split()).strip()
    actors = relation_actor_identities(text)
    if not actors:
        return {}
    actor_hits = sorted(
        (match.start(), actor)
        for actor in actors
        for match in [_re.search(rf"(?<![A-Za-z0-9_.-]){_re.escape(actor)}"
                                 r"(?![A-Za-z0-9_.-])", text, _re.I)]
        if match
    )
    action_hits = [
        (match.start(), relation_action_role(match.group(0)))
        for match in _EVIDENCE_RELATION_ACTION.finditer(text)
        if relation_action_role(match.group(0))
    ]
    out: dict[str, list[str]] = {}
    previous_action = -1
    for action_at, role in action_hits:
        candidates = [actor for actor_at, actor in actor_hits
                      if previous_action < actor_at < action_at]
        if not candidates:
            candidates = [actor for actor_at, actor in actor_hits if actor_at < action_at][-1:]
        for actor in candidates:
            roles = out.setdefault(actor, [])
            if role not in roles:
                roles.append(role)
        previous_action = action_at
    return out


def discriminative_relation_terms(value: str) -> set[str]:
    """Return source-comparable actor/material terms, excluding lifecycle vocabulary."""
    return {
        term for term in relation_terms(value)
        if term not in _COMPOSITION_GENERIC_MATERIAL
        and term not in _RELATION_STATE_STOP
        and not _EVIDENCE_RELATION_ACTION.fullmatch(term)
    }


def _relation_roles(relation: EvidenceRelation) -> set[str]:
    roles = {
        str(role).casefold()
        for values in (relation.get("actor_roles") or {}).values()
        for role in values
        if role
    }
    roles.update(
        role for role in (
            relation_action_role(action) for action in relation.get("actions") or []
        ) if role
    )
    return roles


def _material_anchors(relation: EvidenceRelation) -> set[str]:
    """Exclude actors, lifecycle words, and inflected action fragments from materials."""
    actor_keys = {
        str(actor).casefold() for actor in relation.get("actors") or [] if actor
    }
    return {
        term for term in discriminative_relation_terms(str(relation.get("fact") or ""))
        if term not in actor_keys and not _EVIDENCE_RELATION_ACTION.search(term)
    }


def _material_identities(relation: EvidenceRelation) -> set[str]:
    """Return exact ASCII material identities without a domain-name vocabulary."""
    return {
        term for term in _material_anchors(relation)
        if _re.fullmatch(r"[a-z][a-z0-9_.-]{1,}", term)
        and term not in _RELATION_IDENTITY_STOP
    }


def same_relation(left: EvidenceRelation, right: EvidenceRelation) -> bool:
    """Return a conservative actor/material/direction identity decision.

    This matcher is intentionally fail-closed.  A false positive can erase an unresolved
    dependency or approval gate in Work, so any explicitly parsed actor must agree, any
    identifier-shaped material must be shared, and producer/consumer directions may not
    conflict.  Actor-free prose needs two exact material anchors.
    """
    left_actors = {
        str(actor).casefold() for actor in left.get("actors") or [] if actor
    }
    right_actors = {
        str(actor).casefold() for actor in right.get("actors") or [] if actor
    }
    if (left_actors or right_actors) and not (
            left_actors and right_actors and left_actors & right_actors):
        return False

    left_materials = _material_anchors(left)
    right_materials = _material_anchors(right)
    shared_materials = left_materials & right_materials
    left_identities = _material_identities(left)
    right_identities = _material_identities(right)
    # ``left`` is the source/older anchor and ``right`` is the candidate claim or later
    # observation. Every exact candidate identity must already exist in the anchor. This
    # permits an anchor to be more specific, while a shared platform token cannot hide a
    # changed artifact on the candidate side.
    if right_identities:
        if not right_identities.issubset(left_identities):
            return False
    elif left_identities:
        return False
    if len(shared_materials) < (1 if left_actors and right_actors else 2):
        return False

    left_roles = _relation_roles(left)
    right_roles = _relation_roles(right)
    return not (left_roles and right_roles and left_roles.isdisjoint(right_roles))
