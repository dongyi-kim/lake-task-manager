"""Markdown(GFM) ↔ Jira wiki markup 양방향 변환.

에디터(Toast UI)는 GFM Markdown 을 다루지만 Jira DC 8.20 댓글은 **wiki markup** 으로
저장·렌더한다. 그래서 제출 시 md_to_wiki, 수정 로드 시 wiki_to_md 로 변환한다.

지원(흔한 요소): 헤딩·굵게·기울임·취소선·인라인코드·코드블록(언어)·인용·불릿/번호 리스트·
링크·이미지·표·수평선. **정직한 한계**: Jira 고유 요소(panel/callout/color 등)와 복잡한
중첩·혼합 서식은 손실될 수 있다. 최종 렌더의 진실은 제출 후 Jira 의 renderedBody 다.

서식 대응 요약
  MD            Jira wiki
  # H           h1.  (…###### → h6.)
  **b** __b__   *b*
  *i* _i_       _i_
  ~~s~~         -s-
  `c`           {{c}}
  ```lang       {code:lang} … {code}
  > q           bq. q  (연속 줄은 {quote} 블록)
  - / *         *   (불릿)
  1.            #   (번호)
  [t](u)        [t|u]
  ![a](u)       !u!
  ---           ----
  | a | b |     || a || b ||  /  | c | d |
"""

from __future__ import annotations

import re

# ────────────────────────── Markdown → Jira wiki ──────────────────────────


def md_to_wiki(md: str) -> str:
    if not md:
        return ""
    lines = str(md).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # fenced code: ```lang … ```
        m = re.match(r"^```+\s*([A-Za-z0-9+#._-]*)\s*$", stripped)
        if m:
            lang = m.group(1) or ""
            body, i = [], i + 1
            while i < n and not re.match(r"^```+\s*$", lines[i].strip()):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence
            head = "{code:" + lang + "}" if lang else "{code}"
            out.append(head)
            out.extend(body)
            out.append("{code}")
            continue

        # heading  # … ######
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            out.append("h" + str(len(m.group(1))) + ". " + _md_inline(m.group(2)))
            i += 1
            continue

        # horizontal rule  --- / *** / ___
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            out.append("----")
            i += 1
            continue

        # GFM table: header row + separator row (|---|---|)
        if stripped.startswith("|") and i + 1 < n and re.match(
                r"^\|?[\s:\-|]+\|?$", lines[i + 1].strip()) and "-" in lines[i + 1]:
            head_cells = _split_row(stripped)
            out.append("||" + "||".join(_md_inline(c) for c in head_cells) + "||")
            i += 2  # skip header + separator
            while i < n and lines[i].strip().startswith("|"):
                cells = _split_row(lines[i].strip())
                out.append("|" + "|".join(_md_inline(c) for c in cells) + "|")
                i += 1
            continue

        # blockquote: consecutive '> ' lines
        if re.match(r"^>\s?", stripped):
            block, i = [], i
            while i < n and re.match(r"^>\s?", lines[i].strip()):
                block.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            if len(block) == 1:
                out.append("bq. " + _md_inline(block[0]))
            else:
                out.append("{quote}")
                out.extend(_md_inline(b) for b in block)
                out.append("{quote}")
            continue

        # list item (bullet - * + / ordered 1.) — depth by leading spaces (2 → 1 level)
        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            block, i = [], i
            while i < n:
                mm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[i])
                if not mm:
                    break
                depth = len(mm.group(1)) // 2 + 1
                ordered = bool(re.match(r"^\d", mm.group(2)))
                marker = ("#" if ordered else "*") * depth
                block.append(marker + " " + _md_inline(mm.group(3)))
                i += 1
            out.extend(block)
            continue

        # blank / paragraph
        if stripped == "":
            out.append("")
            i += 1
            continue
        out.append(_md_inline(stripped))
        i += 1

    return "\n".join(out).strip("\n")


_MD_CODE_SPAN = re.compile(r"`([^`]+)`")
_MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _md_inline(s: str) -> str:
    """인라인 마크다운 → wiki. 코드 스팬·볼드는 stash 로 보호한다.
    (볼드 **x** 를 먼저 *x* 로 바꾸면 이어지는 italic 규칙이 그 *x* 를 _x_ 로 되먹어버린다 →
     볼드를 토큰으로 빼두고 마지막에 *x* 로 복원해야 안전.)"""
    if not s:
        return ""
    spans = []

    def _stash(kind, val):
        spans.append((kind, val))
        return "\x00%d\x00" % (len(spans) - 1)

    s = _MD_CODE_SPAN.sub(lambda m: _stash("code", m.group(1)), s)

    # images before links (![]() 가 []() 규칙에 안 먹히게)
    s = _MD_IMG.sub(lambda m: "!" + m.group(2).strip() + "!", s)
    s = _MD_LINK.sub(lambda m: "[" + m.group(1) + "|" + m.group(2).strip() + "]", s)

    # bold **x** / __x__ → 토큰으로 보호(뒤 italic 규칙과 충돌 방지)
    s = re.sub(r"\*\*(.+?)\*\*", lambda m: _stash("bold", m.group(1)), s)
    s = re.sub(r"__(.+?)__", lambda m: _stash("bold", m.group(1)), s)
    # italic *x* → _x_  (남은 홑별표만) ;  _x_ 는 wiki 도 _ 라 그대로
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"_\1_", s)
    s = re.sub(r"~~(.+?)~~", r"-\1-", s)                            # strikethrough

    def pop(m):
        kind, val = spans[int(m.group(1))]
        return "{{" + val + "}}" if kind == "code" else "*" + val + "*"

    return re.sub(r"\x00(\d+)\x00", pop, s)


def _split_row(row: str):
    """GFM 표 한 줄 → 셀 리스트 (양끝 파이프 제거, 이스케이프 \\| 보존)."""
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    # \| 는 셀 구분이 아님
    parts = re.split(r"(?<!\\)\|", row)
    return [p.replace("\\|", "|").strip() for p in parts]


# ────────────────────────── Jira wiki → Markdown ──────────────────────────


def wiki_to_md(wiki: str) -> str:
    if not wiki:
        return ""
    lines = str(wiki).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # {code[:lang]} … {code}  /  {noformat} … {noformat}
        m = re.match(r"^\{(code|noformat)(?::([^}]*))?\}\s*$", stripped)
        if m:
            lang = ""
            if m.group(1) == "code" and m.group(2):
                lm = re.search(r"(?:^|\|)\s*([A-Za-z0-9+#._-]+)\s*(?:$|\|)", m.group(2))
                if lm and "=" not in lm.group(1):
                    lang = lm.group(1)
            body, i = [], i + 1
            while i < n and not re.match(r"^\{" + m.group(1) + r"\}\s*$", lines[i].strip()):
                body.append(lines[i])
                i += 1
            i += 1
            out.append("```" + lang)
            out.extend(body)
            out.append("```")
            continue

        # {quote} … {quote}
        if stripped == "{quote}":
            body, i = [], i + 1
            while i < n and lines[i].strip() != "{quote}":
                body.append(lines[i].strip())
                i += 1
            i += 1
            out.extend("> " + _wiki_inline(b) for b in body)
            continue

        # bq. single-line quote
        if stripped.startswith("bq. "):
            out.append("> " + _wiki_inline(stripped[4:]))
            i += 1
            continue

        # heading hN.
        m = re.match(r"^h([1-6])\.\s+(.*)$", stripped)
        if m:
            out.append("#" * int(m.group(1)) + " " + _wiki_inline(m.group(2)))
            i += 1
            continue

        # horizontal rule ----
        if re.match(r"^-{4,}$", stripped):
            out.append("---")
            i += 1
            continue

        # table: consecutive lines starting with '|'
        if stripped.startswith("|"):
            rows, i = [], i
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            out.extend(_wiki_table_to_md(rows))
            continue

        # list: * bullet / # numbered (depth by run length)
        m = re.match(r"^([*#]+)\s+(.*)$", stripped)
        if m:
            block, i = [], i
            while i < n:
                mm = re.match(r"^([*#]+)\s+(.*)$", lines[i].strip())
                if not mm:
                    break
                marks = mm.group(1)
                indent = "  " * (len(marks) - 1)
                bullet = "1." if marks[-1] == "#" else "-"
                block.append(indent + bullet + " " + _wiki_inline(mm.group(2)))
                i += 1
            out.extend(block)
            continue

        if stripped == "":
            out.append("")
            i += 1
            continue
        out.append(_wiki_inline(stripped))
        i += 1

    return "\n".join(out).strip("\n")


_WIKI_MONO = re.compile(r"\{\{(.+?)\}\}")
_WIKI_IMG = re.compile(r"!([^!\n|]+)(?:\|[^!\n]*)?!")
_WIKI_LINK = re.compile(r"\[(?:([^\]|]+)\|)?([^\]]+)\]")


def _wiki_inline(s: str) -> str:
    if not s:
        return ""
    spans = []

    def stash(m):
        spans.append(m.group(1))
        return "\x00%d\x00" % (len(spans) - 1)

    s = _WIKI_MONO.sub(stash, s)                                    # {{code}} first

    s = _WIKI_IMG.sub(lambda m: "![](" + m.group(1).strip() + ")", s)   # !img! (before links)
    s = _WIKI_LINK.sub(
        lambda m: "[" + (m.group(1) or m.group(2).strip()) + "](" + m.group(2).strip() + ")", s)

    # bold *b* → **b**  (before italic _)
    s = re.sub(r"(?<![\w*])\*(\S(?:.*?\S)?)\*(?![\w*])", r"**\1**", s)
    s = re.sub(r"(?<![\w_])_(\S(?:.*?\S)?)_(?![\w_])", r"*\1*", s)      # _i_ → *i*
    s = re.sub(r"(?<![\w-])-(\S(?:.*?\S)?)-(?![\w-])", r"~~\1~~", s)    # -s- → ~~s~~

    def pop(m):
        return "`" + spans[int(m.group(1))] + "`"

    return re.sub(r"\x00(\d+)\x00", pop, s)


def _wiki_table_to_md(rows):
    """wiki 표(||h|| / |c|) → GFM. 헤더가 없으면 첫 행을 헤더로."""
    parsed, header_idx = [], None
    for idx, r in enumerate(rows):
        r = r.strip()
        if r.startswith("||"):
            cells = [c for c in r.split("||") if c != ""]
            if header_idx is None:
                header_idx = idx
        else:
            inner = r[1:-1] if r.endswith("|") else r[1:]
            cells = inner.split("|")
        parsed.append([_wiki_inline(c.strip()) for c in cells])
    if not parsed:
        return []
    width = max(len(r) for r in parsed)
    parsed = [r + [""] * (width - len(r)) for r in parsed]
    if header_idx is None:                     # 헤더 표기가 없으면 첫 행을 헤더로 승격
        header_idx = 0
    md = []
    md.append("| " + " | ".join(parsed[header_idx]) + " |")
    md.append("| " + " | ".join(["---"] * width) + " |")
    for idx, r in enumerate(parsed):
        if idx == header_idx:
            continue
        md.append("| " + " | ".join(r) + " |")
    return md
