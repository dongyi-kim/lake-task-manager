"""Canonical user-facing evidence index.

The reply used to expose three independently formatted source lists: model-written
``근거/참조`` Markdown, Research Analyst ``evidence``, and ``related_docs``.  Keeping
those paths independent made numbering, duplication, and badge rules drift.  This
module is the one server-side owner of the persisted reply grammar:

    ### 근거
    [1] {{ticket-detail:DL-123}}
    - [1-a] 본문에서 설정값 확인
    - [1-b] 댓글에서 운영상 예외 확인
    [2] [Confluence 문서](https://...)

One real source receives one integer.  Multiple observations from that source receive
lettered child references.  Legacy headings and numbered-list rows are accepted as
input only and are serialized back to the canonical grammar.
"""

from __future__ import annotations

from collections import OrderedDict
import re
from urllib.parse import urlsplit, urlunsplit


_HEADING_RE = re.compile(
    r"(?m)^(?:#{1,4}\s*(?:근거|참조)|\*\*(?:근거|참조)\*\*)\s*$"
)
_NEXT_HEADING_RE = re.compile(r"(?m)^#{1,4}\s+")
_ROOT_RE = re.compile(
    r"^\s*(?:-\s*)?(?:\[(\d+)(?:-([a-z]))?\]|(\d+)[.)])\s*(.*?)\s*$",
    re.I,
)
_CHILD_RE = re.compile(r"^\s*-\s*(?:\[(\d+)-([a-z])\]\s*)?(.*?)\s*$", re.I)
_CITATION_TOKEN = r"\d+(?:-[a-z])?"
_CITATION_RE = re.compile(
    rf"\[((?:{_CITATION_TOKEN})(?:\s*,\s*{_CITATION_TOKEN})*)\](?!\()", re.I,
)
_CITATION_RUN_RE = re.compile(
    rf"\[(?:{_CITATION_TOKEN})(?:\s*,\s*{_CITATION_TOKEN})*\]"
    rf"(?:\s*,?\s*\[(?:{_CITATION_TOKEN})(?:\s*,\s*{_CITATION_TOKEN})*\])+",
    re.I,
)
_KEY_RE = re.compile(r"(?<![0-9A-Za-z-])([A-Z][A-Z0-9]*-\d+)(?![0-9A-Za-z-])")
_TOKEN_RE = re.compile(r"\{\{ticket-(?:list|inline|detail):([A-Z][A-Z0-9]*-\d+)\}\}")
_MD_LINK_RE = re.compile(r"\[([^\n]+?)\]\((https?://[^\s)]+)\)")
_URL_RE = re.compile(r"https?://[^\s)>]+", re.I)
_CUT_RE = re.compile(r"^(.*?)\s+(?:—|–|--)\s+(.*)$")
_CONFLUENCE_RE = re.compile(r"confluence|/pages/\d+|/display/|/wiki/", re.I)


def _clean_url(url: str) -> str:
    """Normalize only identity-safe URL parts; preserve the displayed source URL."""
    try:
        p = urlsplit(str(url or "").strip())
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, ""))
    except Exception:
        return str(url or "").strip().rstrip("/")


def _valid_url(url: str) -> bool:
    return bool(re.match(r"^https?://", str(url or "").strip(), re.I))


def _observation(text: str, source: str = "") -> str:
    value = re.sub(r"^\s*(?:—|–|--|:)\s*", "", str(text or "")).strip()
    if not value:
        return ""
    # Already source-qualified: never produce "본문에서 본문에서 ...".
    if re.match(r"^(?:본문|댓글|코멘트|변경 이력|문서 본문|웹 문서|조회 결과)에서\b", value):
        return value.replace("코멘트에서", "댓글에서", 1)
    labels = {
        "description": "본문에서", "body": "본문에서",
        "comment": "댓글에서", "comments": "댓글에서",
        "field": "변경 이력에서", "change": "변경 이력에서",
        "history": "변경 이력에서", "document": "문서 본문에서",
        "confluence": "문서 본문에서", "external": "웹 문서에서",
        "web": "웹 문서에서", "query": "조회 결과에서",
    }
    prefix = labels.get(str(source or "").strip().lower(), "")
    return f"{prefix} {value}".strip()


def _source_parts(raw: str) -> tuple[str, str, str]:
    """Return ``(identity, canonical source, observation)`` for a legacy root row."""
    value = str(raw or "").strip()
    cut = _CUT_RE.match(value)
    left = (cut.group(1) if cut else value).strip()
    why = (cut.group(2) if cut else "").strip()

    token = _TOKEN_RE.search(left)
    key_match = token or _KEY_RE.search(left)
    if key_match:
        key = key_match.group(1).upper()
        tail = left[key_match.end():].strip(" \t—–-:,{}")
        comment = re.search(r"(?:코멘트|댓글)\s*(\([^)]*\))?", tail, re.I)
        if comment:
            detail = re.sub(r"^(?:댓글|코멘트)에서\s*", "", why).strip()
            meta = (comment.group(1) or "").strip()
            obs = f"댓글{meta}에서 {detail}".strip()
        else:
            obs = _observation(why, "description")
        return f"ticket:{key}", f"{{{{ticket-detail:{key}}}}}", obs

    legacy_link = re.match(r"^(.+?)\s+\((https?://[^\s)]+)\)$", left)
    if legacy_link:
        title, url = legacy_link.group(1).strip(), legacy_link.group(2).strip()
        return (f"url:{_clean_url(url)}", f"[{title}]({url})",
                _observation(why, "document" if _CONFLUENCE_RE.search(url) else "external"))

    link = _MD_LINK_RE.search(left)
    url_match = link or _URL_RE.search(left)
    if url_match:
        if link:
            title, url = link.group(1).strip(), link.group(2).strip()
            source = f"[{title}]({url})"
        else:
            url = url_match.group(0).rstrip(".,;:!?")
            source = url
        return (f"url:{_clean_url(url)}", source,
                _observation(why, "document" if _CONFLUENCE_RE.search(url) else "external"))

    source = left or value
    return f"text:{source.casefold()}", source, _observation(why)


def _split(text: str) -> tuple[str, list[str], str]:
    value = str(text or "")
    heading = _HEADING_RE.search(value)
    if not heading:
        return value.rstrip(), [], ""
    start = heading.end()
    following = _NEXT_HEADING_RE.search(value, start)
    end = following.start() if following else len(value)
    return value[:heading.start()].rstrip(), value[start:end].splitlines(), value[end:].lstrip()


def _append_observation(group: dict, value: str) -> int | None:
    obs = str(value or "").strip()
    if not obs:
        return None
    key = re.sub(r"\s+", " ", obs).casefold()
    for index, current in enumerate(group["observations"]):
        if re.sub(r"\s+", " ", current).casefold() == key:
            return index
    group["observations"].append(obs)
    return len(group["observations"]) - 1


def _citation_tokens(value: str) -> list[str]:
    return [token.strip().lower() for token in str(value or "").split(",") if token.strip()]


def _compact_adjacent_citations(text: str) -> str:
    """Normalize ``[4] [5]`` and ``[4, 5]`` into compact linked markers ``[4][5]``."""
    def merge(match: re.Match) -> str:
        combined: list[str] = []
        for citation in _CITATION_RE.finditer(match.group(0)):
            for token in _citation_tokens(citation.group(1)):
                if token not in combined:
                    combined.append(token)
        return "".join(f"[{token}]" for token in combined)
    return _CITATION_RUN_RE.sub(merge, str(text or ""))


def canonicalize_evidence_index(text: str, evidence: list | None = None,
                                related_docs: list | None = None) -> str:
    """Merge every evidence channel into one stable, hierarchical source index."""
    body, lines, tail = _split(text)
    # The canonical source index is always the last section.  Legacy replies sometimes put
    # another heading after references; preserve that content by moving it before the index.
    if tail:
        body = "\n\n".join(part for part in (body, tail) if part)
        tail = ""
    groups: OrderedDict[str, dict] = OrderedDict()
    parsed_rows: list[dict] = []
    current: dict | None = None

    def ensure(identity: str, source: str) -> dict:
        group = groups.get(identity)
        if group is None:
            group = {"identity": identity, "source": source, "observations": [], "rows": []}
            groups[identity] = group
        elif group["source"].startswith("http") and source.startswith("["):
            # Prefer a human title over a bare URL when either legacy path supplied one.
            group["source"] = source
        return group

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        root = _ROOT_RE.match(line)
        # ``[5-a]`` is a child observation, not another source.
        if root and not root.group(2):
            old = root.group(1) or root.group(3)
            identity, source, obs = _source_parts(root.group(4))
            current = ensure(identity, source)
            obs_index = _append_observation(current, obs)
            row = {"old": old, "identity": identity, "observation": obs_index}
            current["rows"].append(row)
            parsed_rows.append(row)
            continue
        child = _CHILD_RE.match(line)
        if child and current:
            obs_index = _append_observation(current, _observation(child.group(3)))
            if child.group(1):
                row = {"old": f"{child.group(1)}-{child.group(2)}",
                       "identity": current["identity"], "observation": obs_index}
                current["rows"].append(row)
                parsed_rows.append(row)

    # Research Analyst state joins by real source identity.  ``why`` is a fallback only;
    # source-specific observations are preferred and remain lossless.
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        ticket = _KEY_RE.fullmatch(key.upper())
        if ticket:
            identity, source = f"ticket:{ticket.group(1)}", f"{{{{ticket-detail:{ticket.group(1)}}}}}"
        elif _valid_url(url or key):
            actual = url or key
            identity = f"url:{_clean_url(actual)}"
            source = f"[{title or actual}]({actual})" if title else actual
        elif key or title:
            source = title or key
            identity = f"text:{source.casefold()}"
        else:
            continue
        group = ensure(identity, source)
        observations = item.get("observations") or []
        for obs in observations:
            if isinstance(obs, dict):
                _append_observation(group, _observation(obs.get("text"), obs.get("source")))
            elif isinstance(obs, str):
                _append_observation(group, _observation(obs))
        if not observations and not group["observations"]:
            _append_observation(group, _observation(item.get("why"), "query"))

    # Related docs hydrate a title/URL already used by the reply or evidence.  They are not
    # appended merely because retrieval returned them: rejected/guide noise must stay internal.
    for doc in related_docs or []:
        if not isinstance(doc, dict):
            continue
        title = str(doc.get("title") or "").strip()
        url = str(doc.get("url") or "").strip()
        if not title or not _valid_url(url):
            continue
        identity = f"url:{_clean_url(url)}"
        group = groups.get(identity)
        if group:
            group["source"] = f"[{title}]({url})"
        elif title in body or url in body:
            ensure(identity, f"[{title}]({url})")

    if not groups:
        # Remove only an empty legacy heading.  Never invent a source index.
        if _HEADING_RE.search(str(text or "")):
            body = _CITATION_RE.sub("(근거 확인 필요)", body)
        return (body + ("\n\n" + tail if tail else "")).strip()

    row_by_old = {row["old"]: row for row in parsed_rows}
    identity_order: list[str] = []
    for match in _CITATION_RE.finditer(body):
        for old in _citation_tokens(match.group(1)):
            row = row_by_old.get(old) or row_by_old.get(old.split("-", 1)[0])
            if row and row["identity"] not in identity_order:
                identity_order.append(row["identity"])
    identity_order.extend(identity for identity in groups if identity not in identity_order)
    number = {identity: index + 1 for index, identity in enumerate(identity_order)}

    marker_map: dict[str, str] = {}
    for row in parsed_rows:
        group = groups[row["identity"]]
        base = str(number[row["identity"]])
        obs_index = row.get("observation")
        if obs_index is not None and len(group["observations"]) > 1:
            marker_map[row["old"]] = f"{base}-{chr(97 + obs_index)}"
        else:
            marker_map[row["old"]] = base

    def replace_citation(match: re.Match) -> str:
        mapped: list[str] = []
        unresolved = False
        for old in _citation_tokens(match.group(1)):
            current = marker_map.get(old) or marker_map.get(old.split("-", 1)[0])
            if current and current not in mapped:
                mapped.append(current)
            elif not current:
                unresolved = True
        citation = "".join(f"[{current}]" for current in mapped)
        if unresolved:
            citation += (" " if citation else "") + "(근거 확인 필요)"
        return citation

    body = _compact_adjacent_citations(_CITATION_RE.sub(replace_citation, body))
    rendered: list[str] = []
    for identity in identity_order:
        group = groups[identity]
        base = number[identity]
        rendered.append(f"[{base}] {group['source']}")
        observations = group["observations"]
        for index, obs in enumerate(observations):
            marker = f" [{base}-{chr(97 + index)}]" if len(observations) > 1 else ""
            rendered.append(f"-{marker} {obs}".replace("-  ", "- "))

    result = body.rstrip() + "\n\n### 근거\n\n" + "\n".join(rendered)
    if tail:
        result += "\n\n" + tail
    return result.strip()


__all__ = ["canonicalize_evidence_index"]
