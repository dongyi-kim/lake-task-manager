"""mdhtml.py — Markdown → 에디터(TipTap) 형태 HTML.

**왜 Markdown 인가**: Bulk 생성은 사람이 JSON 을 손으로 쓰거나 LLM 이 만들어 온다. 그 자리에
HTML 을 넣게 하면 JSON 안에서 따옴표를 이스케이프해야 해 사람도 LLM 도 자주 틀린다. Markdown 은
둘 다 자연스럽게 쓰고, 앱도 이미 '마크다운 표 붙여넣기' 를 쓰고 있다.

**왜 하필 TipTap 형태 HTML 인가**: 저장 형식은 환경마다 다르다(prod=JEDITOR HTML, mock/local=wiki).
그 분기는 이미 `jira_client.desc_field_value` 가 하고, 그 입력이 **에디터가 만드는 HTML** 이다.
그래서 여기서는 그 형태로만 맞춰 주면 체크박스·표·목록이 두 환경 모두에서 제대로 저장된다:
  - 체크박스: `<ul data-type="taskList"><li data-checked="true|false">…</li></ul>`
    (wiki 는 wikihtml._tasklist, prod 는 htmlsafe.flatten_task_lists 가 이 형태를 기대한다)
  - 표: 표준 `<table><tr><th|td>` / 목록: `<ul|ol><li>`

지원 범위(의도적으로 좁게 — 티켓 본문에 실제로 쓰는 것만):
  제목 #~####, 불릿 -/*, 번호 1., 체크박스 - [ ] / - [x], 표(| a | b |), 코드펜스 ```,
  인용 >, 수평선 ---, 인라인(**굵게** *기울임* `코드` [링크](url)).
"""

import html as _html
import re

__all__ = ["markdown_to_html"]

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)")
_STRIKE = re.compile(r"~~(.+?)~~", re.S)
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![\"'>=])\bhttps?://[^\s<>\"')]+")
# 링크는 **웹(http/https) 만** 허용한다 — Bulk 생성엔 파일 업로드 경로가 없어서 첨부·로컬경로
# (file://, C:\…, /mnt/…)는 만들 수 없고, javascript: 같은 스킴은 애초에 넣을 이유가 없다.
_WEB_URL = re.compile(r"^https?://", re.I)

_H = re.compile(r"^(#{1,4})\s+(.*)$")
_TASK = re.compile(r"^[-*]\s+\[([ xX])\]\s*(.*)$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_ORDERED = re.compile(r"^(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")


def _inline(text):
    """인라인 마크업 → HTML. **먼저 이스케이프**한 뒤 마크업을 되살린다 —
    반대로 하면 사용자 텍스트의 '<' 가 태그로 먹혀 내용이 사라진다."""
    out = _html.escape(text, quote=False)

    # 코드부터 뽑아 자리표시자로 치환한다 — 코드 안의 *별표* 가 기울임으로 먹히면 안 된다.
    codes = []

    def _stash(m):
        codes.append(_html.escape(m.group(1), quote=False))
        return "\x00%d\x00" % (len(codes) - 1)

    out = _INLINE_CODE.sub(_stash, out)

    # 이미지 문법은 첨부를 만들 수 없다 → 웹 URL 이면 링크로, 아니면 글자로 남긴다(내용 유실 방지).
    out = _IMAGE.sub(lambda m: ('<a href="%s">%s</a>'
                                % (_html.escape(m.group(2), quote=True), m.group(1) or m.group(2)))
                     if _WEB_URL.match(m.group(2)) else (m.group(1) or m.group(2)), out)
    # 링크는 웹(http/https)만 — 그 외 스킴/경로는 링크로 만들지 않고 텍스트로 둔다.
    out = _LINK.sub(lambda m: ('<a href="%s">%s</a>'
                               % (_html.escape(m.group(2), quote=True), m.group(1) or m.group(2)))
                    if _WEB_URL.match(m.group(2)) else (m.group(1) or m.group(2)), out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    out = _STRIKE.sub(r"<s>\1</s>", out)
    # 링크로 감싸이지 않은 맨 URL 도 링크로(본문에 그냥 붙여 넣는 경우가 많다)
    out = _BARE_URL.sub(lambda m: '<a href="%s">%s</a>' % (m.group(0), m.group(0)), out)

    for i, c in enumerate(codes):
        out = out.replace("\x00%d\x00" % i, "<code>%s</code>" % c)
    return out


def _cells(line):
    """`| a | b |` → ['a', 'b'] (양끝 파이프 제거, 이스케이프된 \\| 는 살린다)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    parts, buf, esc = [], "", False
    for ch in s:
        if esc:
            buf += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == "|":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [p.strip() for p in parts]


def markdown_to_html(md):
    """Markdown → 에디터 형태 HTML. 빈 입력이면 ""."""
    if not md or not str(md).strip():
        return ""
    lines = str(md).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 코드 펜스 ``` — 안쪽은 마크업을 해석하지 않는다
        if stripped.startswith("```"):
            i += 1
            body = []
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1                                  # 닫는 펜스 소비
            out.append("<pre><code>%s</code></pre>"
                       % _html.escape("\n".join(body), quote=False))
            continue

        if _HR.match(line):
            out.append("<hr>")
            i += 1
            continue

        m = _H.match(stripped)
        if m:
            lv = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lv, _inline(m.group(2).strip()), lv))
            i += 1
            continue

        # 표 — 헤더행 + 구분행(|---|---|) 이 이어질 때만 표로 본다
        if _TABLE_ROW.match(line) and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            head = _cells(line)
            i += 2
            rows = []
            while i < n and _TABLE_ROW.match(lines[i]):
                rows.append(_cells(lines[i]))
                i += 1
            buf = ["<table><tbody><tr>"]
            buf += ["<th>%s</th>" % _inline(c) for c in head]
            buf.append("</tr>")
            for r in rows:
                buf.append("<tr>")
                # 열 수가 헤더와 달라도 깨지지 않게 맞춘다(모자라면 빈 칸)
                buf += ["<td>%s</td>" % _inline(r[c] if c < len(r) else "")
                        for c in range(len(head))]
                buf.append("</tr>")
            buf.append("</tbody></table>")
            out.append("".join(buf))
            continue

        # 체크박스 목록 — TipTap taskList 형태여야 wiki/prod 양쪽이 체크박스로 저장한다
        if _TASK.match(stripped):
            items = []
            while i < n:
                mm = _TASK.match(lines[i].strip())
                if not mm:
                    break
                items.append('<li data-checked="%s">%s</li>'
                             % ("true" if mm.group(1).lower() == "x" else "false",
                                _inline(mm.group(2).strip())))
                i += 1
            out.append('<ul data-type="taskList">%s</ul>' % "".join(items))
            continue

        if _BULLET.match(stripped):
            items = []
            while i < n:
                s2 = lines[i].strip()
                if _TASK.match(s2) or not _BULLET.match(s2):
                    break
                items.append("<li>%s</li>" % _inline(_BULLET.match(s2).group(1).strip()))
                i += 1
            out.append("<ul>%s</ul>" % "".join(items))
            continue

        if _ORDERED.match(stripped):
            items = []
            while i < n:
                mm = _ORDERED.match(lines[i].strip())
                if not mm:
                    break
                items.append("<li>%s</li>" % _inline(mm.group(2).strip()))
                i += 1
            out.append("<ol>%s</ol>" % "".join(items))
            continue

        if _QUOTE.match(stripped):
            body = []
            while i < n and _QUOTE.match(lines[i].strip()):
                body.append(_QUOTE.match(lines[i].strip()).group(1))
                i += 1
            out.append("<blockquote><p>%s</p></blockquote>"
                       % "<br>".join(_inline(b) for b in body))
            continue

        # 문단 — 빈 줄이 나올 때까지 모아 <br> 로 잇는다(연속 줄은 한 문단)
        body = []
        while i < n:
            s2 = lines[i].strip()
            if (not s2 or _H.match(s2) or _TASK.match(s2) or _BULLET.match(s2)
                    or _ORDERED.match(s2) or _QUOTE.match(s2) or _HR.match(lines[i])
                    or s2.startswith("```") or _TABLE_ROW.match(lines[i])):
                break
            body.append(s2)
            i += 1
        if body:
            out.append("<p>%s</p>" % "<br>".join(_inline(b) for b in body))

    return "".join(out)
