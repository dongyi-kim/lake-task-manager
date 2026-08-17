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
from urllib.parse import unquote_plus, urlsplit, urlunsplit


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
# Typed source tokens may be followed immediately by ``}}`` or a Markdown
# citation marker. Treating those delimiters as URL characters creates a
# second malformed identity (for example ``.../spec/}}는``).
_URL_RE = re.compile(r"https?://[^\s)>\]}]+", re.I)
_CUT_RE = re.compile(r"^(.*?)\s+(?:—|–|--)\s+(.*)$")
_CONFLUENCE_RE = re.compile(r"confluence|/pages/\d+|/display/|/wiki/", re.I)


def _clean_url(url: str) -> str:
    """Normalize only identity-safe URL parts; preserve the displayed source URL."""
    try:
        p = urlsplit(str(url or "").strip())
        # Confluence emits both an encoded URL (``%5B회의록%5D+...``) and a decoded
        # browser URL (``[회의록]+...``) for the same page.  Identity comparison must
        # decode both percent escapes and the legacy ``+`` space spelling, while the
        # original source URL remains untouched for display and navigation.
        path = re.sub(r"/{2,}", "/", unquote_plus(p.path)).rstrip("/")
        query = unquote_plus(p.query)
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, query, ""))
    except Exception:
        return str(url or "").strip().rstrip("/")


def _valid_url(url: str) -> bool:
    return bool(re.match(r"^https?://", str(url or "").strip(), re.I))


def _observation(text: str, source: str = "") -> str:
    value = re.sub(r"^\s*(?:—|–|--|:)\s*", "", str(text or "")).strip()
    # A legacy/model-written observation can carry its old source marker at the
    # end. The canonical bullet itself receives the new marker, so retaining the
    # old one produces impossible cross-links such as ``[2-a] ... [3-a]`` after
    # renumbering. Remove only trailing citation tokens, not bracketed data in the
    # middle of a finding.
    value = re.sub(r"(?:\s*\[\d+(?:-[a-z])?\])+\s*$", "", value, flags=re.I).rstrip()
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
    def comparison_key(text: str) -> str:
        # The model sometimes writes the same finding once as plain prose and once as
        # ``본문에서 ...``/``댓글에서 ...``.  Provenance belongs on the one surviving
        # observation; the prefix must not manufacture a second finding.
        normalized = re.sub(
            r"^(?:본문|댓글|코멘트|변경\s*이력|문서\s*본문|웹\s*문서|조회\s*결과)에서\s*",
            "", str(text or "").strip(), flags=re.I,
        )
        # Research summaries often restate the same observation as
        # ``X한다는 내용이 기록되어 있음`` while structured evidence carries ``X한다``.
        # These reporting suffixes add no finding or provenance, so compare the
        # underlying proposition instead of manufacturing a second child marker.
        normalized = re.sub(
            r"(?:다는|라는)\s*내용(?:이)?\s*(?:기록|포함)되어\s*(?:있음|있다|있습니다)\.?$",
            "다", normalized, flags=re.I,
        )
        normalized = re.sub(
            r"([가-힣]+)한다고\s*(?:명시|기록|언급)(?:되어\s*)?(?:있음|있다|됨)?\.?$",
            r"\1한다", normalized,
        )
        normalized = re.sub(r"(?:되어\s*)?(?:있음|있다|있습니다)\.?$", "", normalized)
        # Compare common report-style endings by their proposition stem.  This is
        # intentionally last-position only: ``진행 중임`` and ``확인한다`` remain
        # distinct findings, while ``금지됨``/``금지한다`` collapse.
        normalized = re.sub(
            r"(?:되었다|되었습니다|되어\s*있음|됨|하였다|했습니다|한다|하다|함)\.?$",
            "", normalized,
        ).strip()
        normalized = re.sub(r"[\s\"'“”‘’.,;:!?]+", " ", normalized).strip()
        return normalized.casefold()

    key = comparison_key(obs)
    for index, current in enumerate(group["observations"]):
        if comparison_key(current) == key:
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

    def promote_alias(alias: str, identity: str, source: str) -> dict:
        """Upgrade a model-written bare title to the runtime-verified URL source."""
        if alias == identity or alias not in groups:
            return ensure(identity, source)
        alias_group = groups[alias]
        target = groups.get(identity)
        merged = {
            "identity": identity,
            "source": source,
            "observations": [],
            "rows": [*(alias_group.get("rows") or []),
                     *((target or {}).get("rows") or [])],
        }
        for observation in [*(alias_group.get("observations") or []),
                            *((target or {}).get("observations") or [])]:
            _append_observation(merged, observation)
        for row in merged["rows"]:
            row["identity"] = identity
        rebuilt: OrderedDict[str, dict] = OrderedDict()
        for key, value in groups.items():
            if key == alias:
                rebuilt[identity] = merged
            elif key != identity:
                rebuilt[key] = value
        groups.clear()
        groups.update(rebuilt)
        return merged

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
        alias = f"text:{(title or key).casefold()}" if (title or key) else ""
        if _valid_url(url or key):
            actual = url or key
            # Confluence models sometimes emit only the stable page id as a root
            # source even though Query Runner supplied the verified page URL. Fold
            # that id into the URL source just like a bare document title.
            page = re.search(r"/pages/(\d+)(?:/|$)", actual, re.I)
            page_alias = f"text:{page.group(1).casefold()}" if page else ""
            if page_alias and page_alias in groups:
                group = promote_alias(page_alias, identity, source)
            else:
                group = ensure(identity, source)
            if alias and alias in groups and alias != identity:
                group = promote_alias(alias, identity, source)
        else:
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
        alias = f"text:{title.casefold()}"
        mentioned_in_observation = False
        carried: list[str] = []
        # A model can place a related-document link under a ticket as if the link itself
        # were a ticket finding. Promote that link to its own source root; keep only any
        # actual prose finding after the URL under the document source.
        for existing in list(groups.values()):
            if existing.get("identity") == identity:
                continue
            kept = []
            for observation in existing.get("observations") or []:
                urls = _URL_RE.findall(str(observation or ""))
                same_url = any(_clean_url(found.rstrip(".,;:!?")) == _clean_url(url)
                               for found in urls)
                link_only = same_url or (title in str(observation or "") and bool(urls))
                if not link_only:
                    kept.append(observation)
                    continue
                mentioned_in_observation = True
                remainder = str(observation or "")
                remainder = _MD_LINK_RE.sub("", remainder)
                remainder = _URL_RE.sub("", remainder)
                remainder = remainder.replace(title, "").strip(" []()—–-:;,.\t")
                if len(remainder) >= 8:
                    carried.append(_observation(remainder, "document"))
            existing["observations"] = kept
        group = promote_alias(alias, identity, f"[{title}]({url})") \
            if alias in groups else groups.get(identity)
        if group:
            group["source"] = f"[{title}]({url})"
        elif title in body or url in body or mentioned_in_observation:
            group = ensure(identity, f"[{title}]({url})")
        if group:
            for observation in carried:
                _append_observation(group, observation)

    # Drop a model-written source shell that has neither a finding nor a body
    # citation. Structured evidence with a real finding already received an
    # observation above. This removes retrieval noise such as an inspected but
    # unused Confluence page without hiding a source explicitly cited in prose.
    old_identity = {row["old"]: row["identity"] for row in parsed_rows}
    cited_identities = set()
    for citation in _CITATION_RE.finditer(body):
        for token in _citation_tokens(citation.group(1)):
            identity = old_identity.get(token) or old_identity.get(token.split("-", 1)[0])
            if identity:
                cited_identities.add(identity)
    for identity in list(groups):
        if not groups[identity]["observations"] and identity not in cited_identities:
            del groups[identity]

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
            if row and row["identity"] in groups and row["identity"] not in identity_order:
                identity_order.append(row["identity"])
    identity_order.extend(identity for identity in groups if identity not in identity_order)
    number = {identity: index + 1 for index, identity in enumerate(identity_order)}

    marker_map: dict[str, str] = {}
    for row in parsed_rows:
        if row["identity"] not in groups:
            continue
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
