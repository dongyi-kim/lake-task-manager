"""HTML(에디터=TipTap) ↔ Jira wiki markup 양방향 변환.

TipTap 은 HTML 을 다루고 Jira DC 8.20 댓글은 wiki markup 으로 저장·렌더한다.
- 제출: 에디터 HTML → html_to_wiki → 저장
- 수정 로드: 저장된 wiki → wiki_to_html → 에디터

사람 멘션:  <span data-type="mention" data-id="사번">@이름</span>  <->  [~사번]
링크/문서·웹 뱃지는 멘션이 아니라 일반 링크로 저장([text|url]) — 읽기 렌더에서 앱이 뱃지화한다.

지원: h1~6 · 굵게/기울임/취소선/인라인코드 · 코드블록(언어) · 불릿/번호 리스트(중첩) ·
      인용 · 링크 · 이미지 · 표 · 수평선 · 사람 멘션.
"""

from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser

# ────────────────────────── HTML → Jira wiki ──────────────────────────

_VOID = {"br", "img", "hr"}
_BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
          "blockquote", "pre", "table", "thead", "tbody", "tr", "td", "th", "hr", "div"}


class _Node:
    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(self, tag=None, attrs=None, text=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []
        self.text = text


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = _Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in _VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(_Node(text=data))


_BROWSE_URL_RE = re.compile(r"/browse/[A-Za-z][A-Za-z0-9]*-\d+")


def _is_mention(n):
    return n.tag == "span" and (n.attrs.get("data-type") == "mention" or "data-mention" in n.attrs)


def _cell_safe(s):
    """코드 텍스트를 wiki 인라인에 안전하게 — '|' 는 표 셀 구분자라 그대로 두면 셀이 쪼개진다.
    Jira wiki 의 이스케이프('\\|')로 바꾼다. '}}' 는 monospace 를 조기 종료시키므로 떼어 놓는다."""
    return (s or "").replace("|", "\\|").replace("}}", "} }")


def _inline(node):
    """인라인 노드/자식들을 wiki 문자열로."""
    out = []
    for c in node.children:
        if c.text is not None:
            out.append(c.text)
            continue
        t = c.tag
        if _is_mention(c):
            uid = c.attrs.get("data-id") or _txt(c).lstrip("@")
            out.append("[~" + uid + "]")
        elif t in ("strong", "b"):
            out.append("*" + _inline(c) + "*")
        elif t in ("em", "i"):
            out.append("_" + _inline(c) + "_")
        elif t in ("s", "del", "strike"):
            out.append("-" + _inline(c) + "-")
        elif t == "pre":
            # 인라인 문맥(주로 표 셀)의 코드블럭. Jira wiki 표는 **행이 한 줄**이라 여러 줄 {code}
            # 를 셀에 담을 수 없다 — 원시 줄바꿈을 그대로 두면 행이 끊겨 표 전체가 깨진다.
            # → 줄마다 {{monospace}} 로 감싸고 wiki 강제개행(\\)으로 잇는다.
            #   표도 안 깨지고 코드도 코드로 보인다(구문강조·언어 표기만 손실 — wiki 표의 한계).
            #   되읽으면 <code> 줄들로 돌아와 다시 저장해도 같은 형태다(왕복 안정).
            txt = _txt(c).strip("\n")
            out.append("\\\\".join(("{{" + _cell_safe(ln) + "}}") if ln.strip() else ""
                                   for ln in txt.split("\n")))
        elif t == "code":
            out.append("{{" + _cell_safe(_txt(c)) + "}}")
        elif t == "div" and "sec-title-node" in (c.attrs.get("class") or ""):
            # 영역 구분선 — 저장 형태는 사내 관습 그대로 '=== 제목 ==='. 새 문법을 만들면
            # Jira 웹에서 연 사람이 못 알아보고 기존 티켓과도 어긋난다.
            out.append("\n=== " + _inline(c).strip() + " ===\n")
        elif t == "a":
            href = (c.attrs.get("href") or "").strip()
            label = _inline(c).strip()
            if "file-badge" in (c.attrs.get("class") or ""):
                # 첨부 파일 뱃지 — Jira 의 **첨부 링크 문법**으로 저장한다. 일반 링크([label|url])
                # 로 쓰면 그 티켓에 붙은 파일이 아니라 특정 URL 을 가리키게 되고, 파일이 다른
                # 곳으로 옮겨지거나 첨부가 지워졌을 때 본문만 남아 깨진 링크가 된다.
                fname = (c.attrs.get("data-file") or label or href).strip()
                out.append("[^" + fname.replace("]", ")") + "]")
            elif href and _BROWSE_URL_RE.search(href):
                # Jira 티켓 링크 — **라벨 없이 URL만** 저장한다. 읽기 렌더가 키/제목/상태를 조회해
                # 리치 뱃지로 바꾸므로 라벨이 불필요하고, 티켓 제목에 흔한 '[' ']' 가 wiki 링크
                # 문법([label|url])을 깨뜨리는 문제도 원천 차단된다.
                out.append("[" + href + "]")
            elif href and label and label != href:
                # 라벨의 wiki 구분자는 링크를 깨뜨린다 → 안전한 문자로 치환.
                safe = label.replace("|", "/").replace("[", "(").replace("]", ")")
                out.append("[" + safe + "|" + href + "]")
            elif href:
                out.append("[" + href + "]")
            else:
                out.append(label)
        elif t == "img":
            src = (c.attrs.get("src") or "").strip()
            if src:
                # 크기 유지 — Jira wiki 이미지 파라미터 !파일|width=300!
                w = (c.attrs.get("width") or "").strip()
                if not w:
                    m = re.search(r"width\s*:\s*(\d+)", c.attrs.get("style") or "")
                    w = m.group(1) if m else ""
                w = re.sub(r"[^0-9]", "", w)
                out.append("!" + src + ("|width=" + w if w else "") + "!")
        elif t == "br":
            out.append("\n")
        else:
            out.append(_inline(c))
    return "".join(out)


def _txt(node):
    if node.text is not None:
        return node.text
    return "".join(_txt(c) for c in node.children)


def _blocks(node, lines, depth=0):
    for c in node.children:
        if c.text is not None:
            if c.text.strip():                       # 벌거벗은 텍스트 → 문단
                lines.append(_inline_wrap(c.text))
                lines.append("")
            continue
        t = c.tag
        cls = c.attrs.get("class") or ""
        if t == "div" and "sec-title-node" in cls:
            # 영역 구분선 — 저장 형태는 사내 관습 그대로 '=== 제목 ==='. 새 문법을 만들면
            # Jira 웹에서 연 사람이 못 알아보고 기존 티켓과도 어긋난다. 앞뒤 빈 줄은 sections.py 가
            # 줄 단위로 자르기 때문에 필요하다(다른 블록에 붙어 있으면 못 자른다).
            lines.append("")
            lines.append("=== " + _inline(c).strip() + " ===")
            lines.append("")
            continue
        if t == "div" and "callout" in cls:
            # Jira 콜아웃 매크로 — <div class="callout callout-info"> <-> {info}…{info}
            m = re.search(r"callout-(\w+)", cls)
            kind = m.group(1) if m else "info"
            inner = []
            _blocks(c, inner)
            lines.append("{" + kind + "}")
            lines.extend([x for x in inner if x.strip()])
            lines.append("{" + kind + "}")
            lines.append("")
        elif t == "p" or t == "div":
            inner = _inline(c).strip()
            if inner:
                lines.append(inner)
                lines.append("")
        elif t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            lines.append("h" + t[1] + ". " + _inline(c).strip())
            lines.append("")
        elif t in ("ul", "ol"):
            _list(c, lines, "#" if t == "ol" else "*")
            lines.append("")
        elif t == "blockquote":
            inner = []
            _blocks(c, inner)
            body = [x for x in inner if x.strip()]
            if len(body) <= 1:
                lines.append("bq. " + (body[0] if body else ""))
            else:
                lines.append("{quote}")
                lines.extend(body)
                lines.append("{quote}")
            lines.append("")
        elif t == "pre":
            code = c.children[0] if c.children and c.children[0].tag == "code" else c
            lang = ""
            m = re.search(r"language-([A-Za-z0-9+#._-]+)", code.attrs.get("class", ""))
            if m:
                lang = m.group(1)
            lines.append("{code:" + lang + "}" if lang else "{code}")
            lines.append(_txt(code).rstrip("\n"))
            lines.append("{code}")
            lines.append("")
        elif t == "table":
            _table(c, lines)
            lines.append("")
        elif t == "hr":
            lines.append("----")
            lines.append("")
        else:
            _blocks(c, lines, depth)


def _inline_wrap(text):
    return text.strip()


def _list(node, lines, marker):
    for li in node.children:
        if li.tag != "li":
            continue
        # li 의 인라인 텍스트(중첩 리스트 제외)
        inline_parts, nested = [], []
        for ch in li.children:
            if ch.tag in ("ul", "ol"):
                nested.append(ch)
            else:
                tmp = _Node("span")
                tmp.children = [ch]
                inline_parts.append(_inline(tmp))
        text = "".join(inline_parts).strip()
        if text:
            lines.append(marker + " " + text)
        for nl in nested:
            _list(nl, lines, (marker + ("#" if nl.tag == "ol" else "*")))


def _cells(tr):
    return [ch for ch in tr.children if ch.tag in ("td", "th")]


def _table(node, lines):
    rows = []

    def collect(n):
        for ch in n.children:
            if ch.tag == "tr":
                rows.append(ch)
            elif ch.tag in ("thead", "tbody", "tfoot"):
                collect(ch)
    collect(node)
    # 셀 안의 줄바꿈(<br>)은 wiki 강제개행(\\)이어야 한다 — 진짜 개행을 넣으면 행이 끊겨 표가 깨진다.
    def cell(c):
        return _inline(c).strip().replace("\n", "\\\\")

    for tr in rows:
        cells = _cells(tr)
        if cells and all(c.tag == "th" for c in cells):
            # 빈 헤더 셀도 공백 한 칸으로 — ''로 두면 '||||' 가 돼 되읽을 때 셀이 사라진다.
            lines.append("||" + "||".join(cell(c) or " " for c in cells) + "||")
        else:
            lines.append("|" + "|".join(cell(c) or " " for c in cells) + "|")


def html_to_wiki(html: str) -> str:
    if not html:
        return ""
    tb = _Tree()
    tb.feed(html)
    tb.close()
    lines = []
    _blocks(tb.root, lines)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# ────────────────────────── Jira wiki → HTML ──────────────────────────
# TipTap 이 파싱 가능한 표준 HTML 로. 사람 멘션 [~id] → <span data-type=mention>.
# 이름 해석기(mr)를 주면 라벨을 본명으로.

# Jira 콜아웃 매크로 종류. 표준 4종(info/note/warning/tip) + mock/사내가 쓰는 2종.
_CALLOUT_KINDS = ("note", "info", "warning", "tip", "success", "error")
_CALLOUT_OPEN_RE = re.compile(r"^\{(" + "|".join(_CALLOUT_KINDS) + r")(?::[^}]*)?\}\s*$")

_WIKI_MONO = re.compile(r"\{\{(.+?)\}\}")
_WIKI_MENTION = re.compile(r"\[~([^\]]+)\]")
_WIKI_IMG = re.compile(r"!([^!\n|]+)(?:\|([^!\n]*))?!")
_WIKI_LINK = re.compile(r"\[(?:([^\]|]+)\|)?([^\]]+)\]")


def _wiki_inline_html(text, mr=None):
    s = escape(text or "", quote=False)
    spans = []

    def stash(m):
        # 셀 안의 리터럴 파이프는 '\\|' 로 저장돼 있다 — 표시할 땐 되돌린다.
        spans.append(escape(m.group(1).replace("\\|", "|"), quote=False))
        return "\x00%d\x00" % (len(spans) - 1)

    s = _WIKI_MONO.sub(stash, s)                                     # {{code}} 보호
    # 멘션 [~id] (이미지·링크보다 먼저)
    def _men(m):
        uid = m.group(1).strip()
        nm = (mr(uid) if mr else uid) or uid
        u = escape(uid, quote=True)
        lbl = escape(nm, quote=True)
        return f'<span data-type="mention" data-id="{u}" data-label="{lbl}">@{escape(nm, quote=False)}</span>'
    s = _WIKI_MENTION.sub(_men, s)
    def _img(m):
        src = escape(m.group(1).strip(), quote=True)
        wm = re.search(r"width\s*=\s*(\d+)", m.group(2) or "")     # !파일|width=300! 크기 유지
        return f'<img src="{src}"' + (f' width="{wm.group(1)}"' if wm else "") + ">"

    s = _WIKI_IMG.sub(_img, s)
    s = _WIKI_LINK.sub(
        lambda m: f'<a href="{escape(m.group(2).strip(), quote=True)}">'
                  f'{escape(m.group(1), quote=False) if m.group(1) else escape(m.group(2).strip(), quote=False)}</a>', s)
    s = re.sub(r"(?<![\w*])\*(\S(?:.*?\S)?)\*(?![\w*])", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w_])_(\S(?:.*?\S)?)_(?![\w_])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w-])-(\S(?:.*?\S)?)-(?![\w-])", r"<s>\1</s>", s)
    s = s.replace("\\\\", "<br>")            # wiki 강제개행 → <br> (표 셀 여러 줄 등)
    s = s.replace("\\|", "|")               # 이스케이프된 셀 구분자 복원

    def pop(m):
        return "<code>" + spans[int(m.group(1))] + "</code>"
    return re.sub(r"\x00(\d+)\x00", pop, s)


def wiki_to_html(wiki: str, mr=None) -> str:
    if not wiki:
        return "<p></p>"
    lines = str(wiki).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        st = line.strip()
        m = re.match(r"^\{(code|noformat)(?::([^}]*))?\}\s*$", st)
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
            cls = f' class="language-{escape(lang, quote=True)}"' if lang else ""
            html.append(f'<pre class="jecodeblock"><code{cls}>'
                        + escape("\n".join(body)) + "</code></pre>")
            continue
        # 콜아웃 매크로 {info}/{note}/{warning}/{tip}/… — 안 잡으면 리터럴 텍스트로 남아 깨진다.
        m = re.match(r"^\{(" + "|".join(_CALLOUT_KINDS) + r")(?::[^}]*)?\}\s*$", st)
        if m:
            kind = m.group(1)
            body, i = [], i + 1
            while i < n and not re.match(r"^\{" + kind + r"(?::[^}]*)?\}\s*$", lines[i].strip()):
                body.append(lines[i])
                i += 1
            i += 1
            html.append(f'<div class="callout callout-{kind}">'
                        + wiki_to_html("\n".join(body), mr) + "</div>")
            continue
        if st == "{quote}":
            body, i = [], i + 1
            while i < n and lines[i].strip() != "{quote}":
                body.append(lines[i].strip())
                i += 1
            i += 1
            html.append("<blockquote>" + "".join(
                "<p>" + _wiki_inline_html(b, mr) + "</p>" for b in body if b) + "</blockquote>")
            continue
        if st.startswith("bq. "):
            html.append("<blockquote><p>" + _wiki_inline_html(st[4:], mr) + "</p></blockquote>")
            i += 1
            continue
        m = re.match(r"^h([1-6])\.\s+(.*)$", st)
        if m:
            html.append(f"<h{m.group(1)}>" + _wiki_inline_html(m.group(2), mr) + f"</h{m.group(1)}>")
            i += 1
            continue
        if re.match(r"^-{4,}$", st):
            html.append("<hr>")
            i += 1
            continue
        if st.startswith("|"):
            rows, i = [], i
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            html.append(_wiki_table_html(rows, mr))
            continue
        m = re.match(r"^([*#]+)\s+", st)
        if m:
            block, i = [], i
            while i < n and re.match(r"^[*#]+\s+", lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            html.append(_wiki_list_html(block, mr))
            continue
        if st == "":
            i += 1
            continue
        # 문단 — 연속 비블록 줄
        para, i = [], i
        while i < n:
            s2 = lines[i].strip()
            if (s2 == "" or s2.startswith(("|", "{code", "{noformat", "{quote", "bq. "))
                    or re.match(r"^h[1-6]\.\s", s2) or re.match(r"^[*#]+\s", s2)
                    or re.match(r"^-{4,}$", s2) or _CALLOUT_OPEN_RE.match(s2)):
                break
            para.append(s2)
            i += 1
        if para:
            html.append("<p>" + "<br>".join(_wiki_inline_html(p, mr) for p in para) + "</p>")
    return "".join(html) or "<p></p>"


def _wiki_list_html(items, mr=None):
    def build(idx, depth):
        first = re.match(r"^([*#]+)", items[idx]).group(1)
        tag = "ol" if first[-1] == "#" else "ul"
        out, i = [], idx
        while i < len(items):
            marks = re.match(r"^([*#]+)\s+(.*)$", items[i])
            lvl, text = len(marks.group(1)), marks.group(2)
            cur = "ol" if marks.group(1)[-1] == "#" else "ul"
            if lvl < depth or (lvl == depth and cur != tag):
                break
            if lvl > depth:
                inner, i = build(i, depth + 1)
                if out:
                    out[-1] = out[-1][:-5] + inner + "</li>"
                continue
            out.append("<li>" + _wiki_inline_html(text, mr) + "</li>")
            i += 1
        return "<" + tag + ">" + "".join(out) + "</" + tag + ">", i

    frags, i = [], 0
    while i < len(items):
        f, i = build(i, 1)
        frags.append(f)
    return "".join(frags)


# 셀 구분자 '|' — 앞에 백슬래시가 붙은 '\\|' 는 리터럴 파이프라 나누지 않는다(코드 안의 파이프).
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _wiki_table_html(rows, mr=None):
    out = ["<table><tbody>"]
    for r in rows:
        r = r.strip()
        if r.startswith("||"):
            cells = [c for c in re.split(r"(?<!\\)\|\|", r) if c != ""]
            tag = "th"
        else:
            inner = r[1:-1] if r.endswith("|") else r[1:]
            cells = _CELL_SPLIT_RE.split(inner)
            tag = "td"
        out.append("<tr>" + "".join(f"<{tag}>" + _wiki_inline_html(c.strip(), mr) + f"</{tag}>"
                                    for c in cells) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)
