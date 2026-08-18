"""Pure automatic contracts shared by the manual Agent quality batteries.

These checks deliberately cover only deterministic contradictions.  They are not a
human-quality score and never replace direct raw-output review.  The module is stdlib-only,
does not import the Agent runtime, and is safe to import from unit tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any, Mapping, Sequence


_STRUCTURED_FAILURE_RE = re.compile(
    r"structured\s+(?:output\s+)?(?:실패|fail(?:ed|ure)?|error)|"
    r"(?:실패|error|fail(?:ed|ure)?)\s*:\s*structured\s+(?:output\s+)?|"
    r"(?:work|agent)\s+(?:실패|fail(?:ed|ure)?|error)",
    re.I,
)
_TRACE_FAILURE_NODES = (
    "request_architect", "work_architect", "people_advisor", "auditor",
    "result_integrator", "action_executor",
)
_CANONICAL_KEY_RE = re.compile(r"(?<![A-Z0-9-])([A-Z][A-Z0-9]{1,9}-\d+)(?!\d)", re.I)
_PSEUDO_KEY_RE = re.compile(r"(?<![A-Z0-9-])([A-Z]-\d+)(?!\d)", re.I)
_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_RAW_MENTION_RE = re.compile(
    r"(?<![0-9A-Za-z가-힣._+\-])@[A-Za-z가-힣][A-Za-z0-9가-힣._-]{1,40}",
)
_MARKDOWN_RE = re.compile(
    r"^\s{0,3}#{1,6}\s+\S|"
    r"\[[^\]\n]+\]\((?:https?://|/)[^)\n]+\)|"
    r"(?<!\*)\*\*[^*\n]+\*\*(?!\*)|"
    r"^\s*h[1-6]\.\s+\S",
    re.I | re.M,
)
_BARE_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (value or []) if isinstance(row, Mapping)]


def _trace_rows(output: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _rows(output.get("trace"))
    evidence = _mapping(output.get("evaluationEvidence"))
    rows.extend(_rows(evidence.get("trace")))
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        identity = (str(row.get("node") or ""), str(row.get("note") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(row)
    return unique


def turn_execution_flaws(outputs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Keep any structured Agent failure red even when a later turn looks successful."""
    flaws: list[str] = []
    for index, raw in enumerate(outputs):
        output = _mapping(raw)
        error = output.get("error")
        has_error = bool(error.strip()) if isinstance(error, str) else bool(error)
        explicit_failed = output.get("ok") is False
        trace_failed = any(
            (not str(row.get("node") or "").strip()
             or str(row.get("node") or "").strip().lower() in _TRACE_FAILURE_NODES)
            and bool(_STRUCTURED_FAILURE_RE.search(str(row.get("note") or "")))
            for row in _trace_rows(output)
        )
        if has_error or explicit_failed or trace_failed:
            flaws.append(
                f"turn[{index}] Agent 실행 오류 또는 structured output 실패 기록 — "
                "후속 turn 성공과 무관하게 자동 계약 실패"
            )
    return flaws


def _draft_rows(output: Mapping[str, Any]) -> list[dict[str, Any]]:
    pending = _mapping(output.get("pending"))
    draft = _mapping(output.get("draft"))
    return (_rows(pending.get("items")) or _rows(output.get("draft_items"))
            or _rows(draft.get("items")))


def question_stop_flaws(output: Mapping[str, Any], *, turn_index: int) -> list[str]:
    """Enforce the required-input boundary without judging question quality."""
    questions = _rows(output.get("questions"))
    if not questions:
        return []
    required = [row for row in questions if row.get("required_input") is True]
    optional = [row for row in questions if row.get("required_input") is False]
    unspecified = [row for row in questions if row.get("required_input") is None]
    pending = _mapping(output.get("pending"))
    visible_draft = bool(_draft_rows(output))
    reply_claims = reply_write_kinds(str(output.get("reply") or ""))
    flaws: list[str] = []
    if required and (pending or visible_draft or reply_claims):
        flaws.append(
            f"turn[{turn_index}] required_input=true 질문과 write 초안·성공 주장이 함께 노출됨"
        )
    if not required and not pending and not visible_draft:
        if optional and not unspecified:
            flaws.append(
                f"turn[{turn_index}] optional(required_input=false) 질문만으로 workflow 중단"
            )
        else:
            flaws.append(
                f"turn[{turn_index}] required_input 계약이 없는 질문만으로 workflow 중단"
            )
    return flaws


@dataclass(frozen=True)
class _Effect:
    kind: str
    actions: tuple[str, ...]
    target_count: int


def _pending_effect(pending: Mapping[str, Any]) -> _Effect:
    action = str(pending.get("action") or "").strip()
    items = _rows(pending.get("items"))
    keys = [str(value or "").strip() for value in (pending.get("keys") or [])
            if str(value or "").strip()]
    bulk_targets = bool(keys)
    key = str(pending.get("key") or "").strip()
    if key and not keys:
        keys = [key]
    changes = _mapping(pending.get("changes"))
    comments = _rows(pending.get("comments"))
    comment = str(pending.get("comment") or "").strip()

    actions: list[str] = []
    if action in {"create_ticket", "create_tickets"}:
        mode = str(pending.get("mode") or "").strip().lower()
        actions.append("create_epic" if mode == "epic" else "create_tickets")
        return _Effect("create", tuple(actions), len(items))
    if action in {"transition_ticket", "link_tickets"}:
        actions.append(action)
        if comment or comments:
            actions.append("add_ticket_comments" if bulk_targets or comments
                           else "add_ticket_comment")
        return _Effect("update", tuple(actions), len(keys))
    if action in {"update_ticket", "update_tickets"}:
        # The public approval card reuses ``update_ticket`` for transitions and links:
        # session shaping exposes only ``changes.status``/``changes.link``, while the
        # deterministic final authority retains the executable action name. Normalize
        # those wire shapes before comparing the two contracts.
        if set(changes) == {"status"}:
            declared_update = "transition_ticket"
        elif set(changes) == {"link"}:
            declared_update = "link_tickets"
        else:
            declared_update = "update_tickets" if bulk_targets or action.endswith("s") \
                else "update_ticket"
        actions.append(declared_update)
        if comment or comments:
            actions.append("add_ticket_comments" if bulk_targets or comments
                           else "add_ticket_comment")
        if not changes:
            return _Effect("unknown", tuple(actions), len(keys))
        return _Effect("update", tuple(actions), len(keys))
    if action in {"add_ticket_comment", "add_ticket_comments"}:
        normalized = "add_ticket_comments" if bulk_targets or action.endswith("s") \
            else "add_ticket_comment"
        return _Effect("comment", (normalized,), len(keys))
    return _Effect("unknown", (action,) if action else (), 0)


def _approval_text(reply: str) -> str:
    match = re.search(r"(?mi)^#{1,4}\s*(?:근거|참조|관련\s*문서)\s*$", reply)
    return reply[:match.start()] if match else reply


def reply_write_kinds(reply: str) -> set[str]:
    """Classify only explicit approval/draft claims, not incidental domain prose."""
    text = _approval_text(str(reply or ""))
    claim_lines = "\n".join(
        line for line in text.splitlines()
        if re.search(
            r"승인|아직\s*(?:생성|등록)되지\s*않|"
            r"(?:초안|게시|적용).{0,8}(?:준비|완료)",
            line,
            re.I,
        )
    )
    claim_lines = re.sub(
        r"(?:별도(?:의)?|추가)?\s*(?:수정|변경|업데이트|댓글|코멘트)\s*없이",
        "",
        claim_lines,
        flags=re.I,
    )
    kinds: set[str] = set()
    if re.search(
        r"(?:댓글|코멘트).{0,18}(?:승인|게시\s*준비|초안.{0,6}(?:준비|완료))",
        claim_lines,
        re.I,
    ):
        kinds.add("comment")
    if re.search(
        r"(?:변경|수정|업데이트).{0,18}(?:승인|적용\s*준비|초안.{0,6}(?:준비|완료))",
        claim_lines,
        re.I,
    ):
        kinds.add("update")
    if re.search(
        r"(?:생성|등록)\s*승인|"
        r"(?:티켓|task|sub-?task|epic|작업).{0,12}(?:생성|등록).{0,18}승인",
        claim_lines,
        re.I,
    ):
        kinds.add("create")
    if not kinds and re.search(r"티켓\s*승인\s*(?:전\s*)?초안", claim_lines, re.I):
        kinds.add("create")
    return kinds


def _expected_reply_kinds(effect: _Effect) -> set[str]:
    kinds = {effect.kind} if effect.kind in {"create", "comment", "update"} else set()
    if effect.kind == "update" and any(action.startswith("add_ticket_comment")
                                        for action in effect.actions):
        kinds.add("comment")
    return kinds


def _clean_cell(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\{\{[^:}]+:([^}]+)\}\}", r"\1", text)
    text = re.sub(r"\[~([^\]]+)\]", r"\1", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = str(text or "").splitlines()
    result: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index + 1 < len(lines):
        header_line, separator = lines[index], lines[index + 1]
        if "|" not in header_line or not re.match(
            r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", separator,
        ):
            index += 1
            continue
        headers = [_clean_cell(cell).casefold()
                   for cell in header_line.strip().strip("|").split("|")]
        table_rows: list[list[str]] = []
        index += 2
        while index < len(lines) and "|" in lines[index]:
            row = [_clean_cell(cell) for cell in lines[index].strip().strip("|").split("|")]
            if len(row) >= len(headers):
                table_rows.append(row[:len(headers)])
            index += 1
        result.append((headers, table_rows))
    return result


def _column(headers: Sequence[str], *names: str) -> int | None:
    normalized = {name.casefold() for name in names}
    for index, header in enumerate(headers):
        if header in normalized:
            return index
    return None


def _field(row: Mapping[str, Any], *names: str) -> Any:
    fields = _mapping(row.get("fields"))
    for name in names:
        if row.get(name) not in (None, ""):
            return row.get(name)
        if fields.get(name) not in (None, ""):
            return fields.get(name)
    return ""


def _owner(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("accountId") or value.get("name") or value.get("key") or "")
    return str(value or "")


def _pending_create_rows(pending: Mapping[str, Any]) -> list[dict[str, Any]]:
    roots = _rows(pending.get("items"))
    children = _rows(pending.get("children"))
    if not children:
        children = [child for root in roots for child in _rows(root.get("children"))]
    return [*roots, *children]


def _create_table_flaws(pending: Mapping[str, Any], reply: str) -> list[str]:
    items = _pending_create_rows(pending)
    if not items:
        return []
    flaws: list[str] = []
    for headers, table_rows in _markdown_tables(_approval_text(reply)):
        title_col = _column(headers, "제목", "summary")
        type_col = _column(headers, "유형", "타입", "type")
        if title_col is None or type_col is None:
            continue
        index_col = _column(headers, "#", "번호", "no", "index")
        parent_col = _column(headers, "상위", "parent", "epic", "에픽")
        owner_col = _column(headers, "담당", "담당자", "assignee", "owner")
        due_col = _column(headers, "기한", "마감", "마감일", "due", "due date")
        if len(table_rows) != len(items):
            flaws.append(
                f"reply/pending cardinality 불일치: 표 {len(table_rows)}건, payload {len(items)}건"
            )
        for sequential, cells in enumerate(table_rows):
            item_index = sequential
            if index_col is not None:
                match = re.search(r"\d+", cells[index_col])
                if match:
                    item_index = int(match.group()) - 1
            if item_index < 0 or item_index >= len(items):
                continue
            item = items[item_index]
            if parent_col is not None:
                expected = str(_field(item, "parent", "epic", "epicKey") or "").upper()
                cell = cells[parent_col]
                actual_keys = {value.upper() for value in _CANONICAL_KEY_RE.findall(cell)}
                parent_index = item.get("parent_index")
                expected_summary = ""
                if isinstance(parent_index, int):
                    roots = _rows(pending.get("items"))
                    if 0 <= parent_index < len(roots):
                        expected_summary = str(roots[parent_index].get("summary") or "")
                if expected_summary and expected_summary not in cell:
                    flaws.append(
                        f"reply/pending parent 불일치: item[{item_index}] "
                        f"payload={expected_summary}, reply={cell or '없음'}"
                    )
                elif expected and expected not in actual_keys:
                    flaws.append(
                        f"reply/pending parent 불일치: item[{item_index}] "
                        f"payload={expected}, reply={cell or '없음'}"
                    )
                elif not expected and not expected_summary and actual_keys:
                    flaws.append(
                        f"reply/pending parent 불일치: item[{item_index}] payload=최상위, "
                        f"reply={cell}"
                    )
            if owner_col is not None:
                expected = _owner(_field(item, "assignee", "owner"))
                cell = cells[owner_col]
                if expected and expected not in cell:
                    flaws.append(
                        f"reply/pending owner 불일치: item[{item_index}] "
                        f"payload={expected}, reply={cell or '없음'}"
                    )
                elif not expected and cell and not re.fullmatch(
                    r"(?:—|-|없음|미정|미할당|unassigned)", cell, re.I,
                ):
                    flaws.append(
                        f"reply/pending owner 불일치: item[{item_index}] payload=미할당, "
                        f"reply={cell}"
                    )
            if due_col is not None:
                expected = str(_field(item, "duedate", "due") or "")
                cell = cells[due_col]
                dates = set(_ISO_DATE_RE.findall(cell))
                if expected and expected not in dates:
                    flaws.append(
                        f"reply/pending due 불일치: item[{item_index}] "
                        f"payload={expected}, reply={cell or '없음'}"
                    )
                elif not expected and dates:
                    flaws.append(
                        f"reply/pending due 불일치: item[{item_index}] payload=없음, "
                        f"reply={cell}"
                    )
        break
    return flaws


def _change_table_flaws(pending: Mapping[str, Any], reply: str) -> list[str]:
    changes = _mapping(pending.get("changes"))
    field_keys = {
        "due": ("duedate", "due"),
        "parent": ("parent", "epic"),
        "owner": ("assignee", "owner"),
    }
    exact: dict[str, tuple[bool, str]] = {}
    for contract, keys in field_keys.items():
        selected = next((key for key in keys if key in changes), "")
        value = changes.get(selected) if selected else ""
        exact[contract] = (bool(selected), _owner(value) if contract == "owner"
                           else str(value or ""))
    if not any(present for present, _value in exact.values()):
        return []
    aliases = {
        "due": {"due", "duedate", "due date", "기한", "마감", "마감일"},
        "parent": {"parent", "epic", "상위", "에픽"},
        "owner": {"owner", "assignee", "담당", "담당자"},
    }
    flaws: list[str] = []
    for headers, table_rows in _markdown_tables(_approval_text(reply)):
        field_col = _column(headers, "필드", "field", "항목")
        value_col = _column(headers, "변경", "변경 후", "값", "value", "after", "변경 내용")
        if field_col is None or value_col is None:
            continue
        for cells in table_rows:
            field_name = cells[field_col].strip().casefold()
            for contract, names in aliases.items():
                present, expected = exact[contract]
                if field_name not in names or not present:
                    continue
                cell = cells[value_col]
                if contract == "due":
                    matches = set(_ISO_DATE_RE.findall(cell))
                    contradicted = expected not in matches if expected else bool(matches)
                elif contract == "parent":
                    matches = {value.upper() for value in _CANONICAL_KEY_RE.findall(cell)}
                    contradicted = expected.upper() not in matches if expected else bool(matches)
                else:
                    contradicted = (expected not in cell if expected else bool(
                        cell and not re.fullmatch(
                            r"(?:—|-|없음|미정|미할당|unassigned)", cell, re.I,
                        )
                    ))
                if contradicted:
                    flaws.append(
                        f"reply/pending {contract} 불일치: payload={expected or '없음'}, "
                        f"reply={cell or '없음'}"
                    )
        break
    return flaws


def _write_boundary_flaws(output: Mapping[str, Any], *, turn_index: int) -> list[str]:
    pending = _mapping(output.get("pending"))
    reply = str(output.get("reply") or "")
    reply_kinds = reply_write_kinds(reply)
    if not pending:
        if reply_kinds and not output.get("questions"):
            return [
                f"turn[{turn_index}] reply가 {sorted(reply_kinds)} write 성공·승인 초안을 "
                "주장하지만 pending payload가 없음"
            ]
        return []

    flaws: list[str] = []
    effect = _pending_effect(pending)
    if effect.kind == "unknown" or not effect.actions or effect.target_count < 1:
        flaws.append(
            f"turn[{turn_index}] pending write action/target이 실행 가능한 effect를 만들지 못함"
        )
    expected_reply = _expected_reply_kinds(effect)
    contradicted_reply = reply_kinds - expected_reply
    if contradicted_reply:
        flaws.append(
            f"turn[{turn_index}] reply/pending action 불일치: "
            f"reply={sorted(reply_kinds)}, pending={list(effect.actions)}"
        )

    review = _mapping(output.get("review"))
    review_kinds = reply_write_kinds(str(review.get("summary") or ""))
    contradicted_review = review_kinds - expected_reply
    if contradicted_review:
        flaws.append(
            f"turn[{turn_index}] review/pending narrative action 불일치: "
            f"review={sorted(review_kinds)}, pending={list(effect.actions)}"
        )
    if review.get("ok") is not True:
        flaws.append(
            f"turn[{turn_index}] pending write가 review.ok=true 없이 최종 성공으로 노출됨"
        )
    review_errors = _rows(review.get("errors")) + _rows(review.get("problems"))
    if review.get("ok") is True and review_errors:
        flaws.append(
            f"turn[{turn_index}] review.ok=true이나 blocking errors/problems가 남아 있음"
        )
    for row in review_errors:
        field = str(row.get("field") or row.get("check") or "").strip().lower()
        if field in {"duedate", "due", "parent", "epic", "assignee", "owner",
                     "cardinality", "effect"}:
            flaws.append(
                f"turn[{turn_index}] review가 {field} 불일치를 기록했는데 pending write가 노출됨"
            )

    authority = _mapping(review.get("final_authority"))
    if not authority:
        flaws.append(
            f"turn[{turn_index}] pending write에 deterministic review.final_authority가 없음"
        )
    else:
        kind = str(authority.get("kind") or "")
        if kind != effect.kind:
            flaws.append(
                f"turn[{turn_index}] review/pending kind 불일치: "
                f"review={kind or '없음'}, pending={effect.kind}"
            )
        actions = {str(value) for value in (authority.get("actions") or []) if str(value)}
        if actions != set(effect.actions):
            flaws.append(
                f"turn[{turn_index}] review/pending action 불일치: "
                f"review={sorted(actions)}, pending={list(effect.actions)}"
            )
        try:
            target_count = int(authority.get("target_count"))
        except (TypeError, ValueError):
            target_count = -1
        if target_count != effect.target_count:
            flaws.append(
                f"turn[{turn_index}] review/pending cardinality 불일치: "
                f"review={target_count}, pending={effect.target_count}"
            )

    summary_counts = {int(value) for value in re.findall(
        r"(?:\*\*)?총\s*(\d+)\s*건", _approval_text(reply), re.I,
    )}
    visible_target_count = (len(_pending_create_rows(pending))
                            if effect.kind == "create" else effect.target_count)
    if summary_counts and summary_counts != {visible_target_count}:
        flaws.append(
            f"turn[{turn_index}] reply/pending cardinality 불일치: "
            f"reply={sorted(summary_counts)}, pending={visible_target_count}"
        )
    if effect.kind == "create":
        flaws.extend(f"turn[{turn_index}] {flaw}"
                     for flaw in _create_table_flaws(pending, reply))
    elif effect.kind == "update":
        flaws.extend(f"turn[{turn_index}] {flaw}"
                     for flaw in _change_table_flaws(pending, reply))
    return flaws


def automatic_contract_flaws(outputs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return all-turn automatic defects, separate from specialized human review."""
    flaws = turn_execution_flaws(outputs)
    for index, output in enumerate(outputs):
        flaws.extend(question_stop_flaws(_mapping(output), turn_index=index))
        flaws.extend(_write_boundary_flaws(_mapping(output), turn_index=index))
    return list(dict.fromkeys(flaws))


class _RenderedTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool]] = []
        self.unrendered: list[str] = []
        self.outside_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {str(key).lower(): str(value or "") for key, value in attrs}
        parent_mention = self.stack[-1][1] if self.stack else False
        parent_link = self.stack[-1][2] if self.stack else False
        classes = set(attributes.get("class", "").lower().split())
        mention = parent_mention or attributes.get("data-type", "").lower() == "mention" \
            or "mention" in classes
        link = parent_link or tag.lower() == "a"
        self.stack.append((tag.lower(), mention, link))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == lowered:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        in_code = any(tag in {"code", "pre"} for tag, _mention, _link in self.stack)
        if in_code:
            return
        in_mention = bool(self.stack and self.stack[-1][1])
        in_link = bool(self.stack and self.stack[-1][2])
        if not in_mention:
            self.unrendered.append(data)
        if not in_link:
            self.outside_links.append(data)


def editor_renderer_contract_flaws(result: Mapping[str, Any]) -> list[str]:
    """Reject final Editor success that still contains unresolved renderer grammar."""
    if result.get("ok") is not True:
        return []
    html = str(result.get("html") or "")
    note = str(result.get("note") or "")
    references = _rows(result.get("references"))
    flaws: list[str] = []

    pseudo = sorted({value.upper() for value in _PSEUDO_KEY_RE.findall(html)})
    if pseudo:
        flaws.append("final renderer pseudo ticket identity: " + ", ".join(pseudo))
    unresolved = sorted({str(row.get("key") or "") for row in references
                         if row.get("kind") == "ticket"
                         and row.get("resolved") is not True and row.get("key")})
    if unresolved:
        flaws.append("final renderer unresolved ticket reference: " + ", ".join(unresolved))
    if re.search(r"\{\{ticket-(?:inline|list|detail):\s*<a\b", html, re.I):
        flaws.append("ticket marker 안에 이미 렌더된 anchor를 이중 삽입")
    resolved_keys = {
        str(row.get("key") or "") for row in references
        if row.get("kind") == "ticket" and row.get("resolved") is True
    }
    contradicted = sorted(key for key in resolved_keys
                          if key and "확인되지 않은" in note and key in note)
    if contradicted:
        flaws.append("resolved ticket을 미확인으로 경고: " + ", ".join(contradicted))

    parser = _RenderedTextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # A malformed fragment is itself inspectable below as raw text; the evaluator must
        # not crash and turn one render defect into a missing case record.
        parser.unrendered = [re.sub(r"<[^>]+>", " ", html)]
        parser.outside_links = list(parser.unrendered)
    unrendered = "\n".join(parser.unrendered)
    outside_links = "\n".join(parser.outside_links)
    if _RAW_MENTION_RE.search(unrendered) or "{{mention:" in unrendered \
            or re.search(r"\[~[^\]]+\]", unrendered):
        flaws.append("final renderer raw mention token")
    if _MARKDOWN_RE.search(unrendered):
        flaws.append("final renderer raw Markdown syntax")
    if _BARE_URL_RE.search(outside_links):
        flaws.append("final renderer bare URL outside anchor")
    return list(dict.fromkeys(flaws))


# ``inspect.getsource`` of a public checker does not include helpers or regex constants it
# calls.  Every battery manifest that uses these gates fingerprints the complete dependency
# tuple so a semantic checker change cannot retain an old manifest hash.
AUTOMATIC_CONTRACT_DEPENDENCIES = (
    _STRUCTURED_FAILURE_RE, _TRACE_FAILURE_NODES,
    _CANONICAL_KEY_RE, _ISO_DATE_RE, _mapping, _rows, _trace_rows,
    turn_execution_flaws, _draft_rows, question_stop_flaws, _Effect,
    _pending_effect, _approval_text, reply_write_kinds, _expected_reply_kinds,
    _clean_cell, _markdown_tables, _column, _field, _owner, _pending_create_rows,
    _create_table_flaws, _change_table_flaws, _write_boundary_flaws,
    automatic_contract_flaws,
)
EDITOR_RENDERER_CONTRACT_DEPENDENCIES = (
    _PSEUDO_KEY_RE, _RAW_MENTION_RE, _MARKDOWN_RE, _BARE_URL_RE,
    _mapping, _rows, _RenderedTextParser, editor_renderer_contract_flaws,
)


__all__ = [
    "AUTOMATIC_CONTRACT_DEPENDENCIES", "EDITOR_RENDERER_CONTRACT_DEPENDENCIES",
    "automatic_contract_flaws", "editor_renderer_contract_flaws",
    "question_stop_flaws", "reply_write_kinds", "turn_execution_flaws",
]
