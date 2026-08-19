"""Product-agnostic claim gates for final response prose.

This module owns semantic downgrades that need evidence relations, but not rendering.
It deliberately reuses Work Architect's typed actor/action/object parser so final prose
cannot acquire a separate product vocabulary.
"""

from __future__ import annotations

import re as _re

from app.agent.workflow.evidence_relations import (
    EvidenceRelation,
    explicitly_unconfirmed_relation,
    is_direct_positive_completion,
    is_unconfirmed_fact,
    parse_relation,
    same_relation,
)
from app.agent.workflow.state import last_user_text


_AFFIRMATIVE_CAPABILITY = _re.compile(
    r"(?:할|될)\s*수\s*있|가능(?:하|함|하다|합니다|한|해)|"
    r"(?:지원|소비|활용|생성|작성|산출)(?:함|한다|합니다|됨|된다|됩니다|할\s*수\s*있)|"
    r"\b(?:can|is\s+able\s+to|supports?|consumes?|reads?|uses?|generates?|"
    r"produces?|writes?|creates?)\b",
    _re.I,
)
_NEGATED_CAPABILITY = _re.compile(
    r"할\s*수\s*없|불가능|미\s*지원|"
    r"(?:지원|소비|활용|생성|작성|산출)(?:하지\s*않|되지\s*않|못함|못한다|불가)|"
    r"\b(?:cannot|can't|can\s+not|unable\s+to|does?\s+not|do\s+not|"
    r"not\s+supported|isn't\s+supported|is\s+not\s+supported)\b",
    _re.I,
)
_BENEFIT_DIMENSIONS = (
    _re.compile(r"최적화|optimi[sz]", _re.I),
    _re.compile(r"성능|처리량|지연\s*시간|performance|throughput|latency", _re.I),
    _re.compile(r"비용|원가|cost", _re.I),
    _re.compile(r"품질|정확도|신선도|일관성|quality|accuracy|freshness|consistency", _re.I),
    _re.compile(r"효율|절감|단축|개선|향상|efficien|saving|reduction|improvement", _re.I),
)
_BENEFIT_ASSERTION = _re.compile(
    r"(?:사용|활용|기여|개선|향상|절감|단축|최적화)(?:될|할)?\s*수\s*있|"
    r"(?:개선|향상|절감|단축|최적화|보장)(?:함|한다|합니다|됨|된다|됩니다)|"
    r"\b(?:can|may|will|would)\b.{0,48}"
    r"(?:improv|optimi[sz]|reduc|save|increase|decrease|benefit)",
    _re.I,
)


def _nested_source_texts(value, *, depth: int = 0) -> list[str]:
    """Flatten bounded state material without depending on one evidence container shape."""
    if depth > 5:
        return []
    if isinstance(value, str):
        compact = " ".join(value.split()).strip()
        return [compact] if compact else []
    if isinstance(value, dict):
        out: list[str] = []
        for nested in list(value.values())[:40]:
            out.extend(_nested_source_texts(nested, depth=depth + 1))
        return out[:160]
    if isinstance(value, (list, tuple)):
        out = []
        for nested in list(value)[:40]:
            out.extend(_nested_source_texts(nested, depth=depth + 1))
        return out[:160]
    return []


def _claim_source_texts(state) -> list[str]:
    out: list[str] = []
    for key in (
        "request_text", "topic_dossier", "pre_survey", "situation",
        "knowledge_brief", "evidence", "materialized_ticket_sources",
    ):
        out.extend(_nested_source_texts(state.get(key)))
    return list(dict.fromkeys(out))[:240]


def _benefit_source_texts(state) -> list[str]:
    """Use research/materialized evidence, not a question that merely names a benefit."""
    out: list[str] = []
    for key in (
        "topic_dossier", "pre_survey", "situation", "knowledge_brief",
        "evidence", "materialized_ticket_sources",
    ):
        out.extend(_nested_source_texts(state.get(key)))
    return list(dict.fromkeys(out))[:240]


def _relation_sentences(value: str) -> list[str]:
    return [
        " ".join(part.split()).strip()
        for part in _re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", str(value or ""))
        if " ".join(part.split()).strip()
    ]


def _unresolved_relations(source_texts: list[str]) -> list[EvidenceRelation]:
    unresolved: list[EvidenceRelation] = []
    for source_text in source_texts:
        sentences = _relation_sentences(source_text)
        for index, sentence in enumerate(sentences):
            if not explicitly_unconfirmed_relation(sentence):
                continue
            candidates = [sentence]
            if index:
                candidates.append(f"{sentences[index - 1]} {sentence}")
            for candidate in candidates:
                relation = parse_relation(candidate)
                if relation and not any(
                        same_relation(relation, existing) for existing in unresolved):
                    unresolved.append(relation)
                    break
    return unresolved[:40]


def _latest_confirmed_relations(state) -> list[EvidenceRelation]:
    latest = last_user_text(state)
    if not latest or not is_direct_positive_completion(latest):
        return []
    return [
        relation for sentence in _relation_sentences(latest)
        for relation in [parse_relation(sentence)]
        if relation and is_direct_positive_completion(sentence)
    ][:12]


def _rewrite_sentences(value: str, rewrite) -> str:
    sentence = _re.compile(
        r"(?m)(^|(?<=[.!?。！？])\s+)([^.!?。！？\n]+(?:[.!?。！？]+|$))"
    )

    def replace(match):
        replacement = rewrite(match.group(2).strip())
        return match.group(1) + replacement

    return sentence.sub(replace, str(value or ""))


def _drop_unconfirmed_capability_claims(value: str, state, source_texts: list[str]) -> str:
    unresolved = _unresolved_relations(source_texts)
    if not unresolved:
        return value
    confirmed = _latest_confirmed_relations(state)

    def rewrite(sentence: str) -> str:
        if (not _AFFIRMATIVE_CAPABILITY.search(sentence)
                or _NEGATED_CAPABILITY.search(sentence)
                or is_unconfirmed_fact(sentence)):
            return sentence
        relation = parse_relation(sentence)
        if not relation:
            return sentence
        matches = [source for source in unresolved if same_relation(source, relation)]
        if not matches:
            return sentence
        if any(
                same_relation(source, confirmation)
                for source in matches for confirmation in confirmed):
            return sentence
        return ""

    return _rewrite_sentences(value, rewrite)


def _benefit_is_supported(sentence: str, source_texts: list[str]) -> bool:
    claim_dimensions = {
        index for index, pattern in enumerate(_BENEFIT_DIMENSIONS) if pattern.search(sentence)
    }
    claim_relation = parse_relation(sentence)
    if not claim_dimensions or not claim_relation:
        return False
    for source_text in source_texts:
        for source_sentence in _relation_sentences(source_text):
            if (is_unconfirmed_fact(source_sentence)
                    or _NEGATED_CAPABILITY.search(source_sentence)):
                continue
            source_dimensions = {
                index for index, pattern in enumerate(_BENEFIT_DIMENSIONS)
                if pattern.search(source_sentence)
            }
            source_relation = parse_relation(source_sentence)
            if (claim_dimensions.issubset(source_dimensions) and source_relation
                    and same_relation(source_relation, claim_relation)):
                return True
    return False


def _drop_unproven_benefits(value: str, source_texts: list[str]) -> str:

    def rewrite(sentence: str) -> str:
        if (not any(pattern.search(sentence) for pattern in _BENEFIT_DIMENSIONS)
                or not _BENEFIT_ASSERTION.search(sentence)
                or _benefit_is_supported(sentence, source_texts)):
            return sentence
        contrast = _re.search(
            r"(?:하지만|으나|이나|지만|반면|\bbut\b|\bhowever\b|\byet\b)\s*[,]?\s*(.+)$",
            sentence, _re.I,
        )
        return contrast.group(1).strip() if contrast else ""

    return _rewrite_sentences(value, rewrite)


def drop_unsupported_guarantees(text: str, state) -> str:
    """Remove unsupported guarantees, benefits, and unresolved capability claims."""
    source_texts = _claim_source_texts(state)
    source = " ".join(source_texts)
    value = str(text or "")
    if "보장" not in source:
        value = _re.sub(
            r"\s*[,，]\s*[^,.\n]{2,120}?(?:을|를|이|가)?\s*보장(?:함|됨|한다|합니다)?(?=[.\n]|$)",
            "", value,
        )
        value = _re.sub(
            r"(?m)^(\s*[-*]?\s*)?([^\n.]{2,160}?보장(?:함|됨|한다|합니다)?)[.]?\s*$",
            lambda match: ((match.group(1) or "") + "해당 보장 효과는 검증 필요"),
            value,
        )
    # Candidate snippets and model prose cannot upgrade an unresolved typed relation or
    # attach a benefit dimension absent from the selected source material.
    value = _drop_unproven_benefits(value, _benefit_source_texts(state))
    value = _drop_unconfirmed_capability_claims(value, state, source_texts)
    value = _re.sub(r"(?m)^\s+$", "", value)
    value = _re.sub(r" {2,}", " ", value)
    value = _re.sub(r"([가-힣]+)이며\.", r"\1임.", value)
    value = _re.sub(r"([가-힣]+)되며\.", r"\1됨.", value)
    value = _re.sub(r"([가-힣]+)하며\.", r"\1함.", value)
    value = _re.sub(r"\s+([.,!?])", r"\1", value)
    return value.strip()
