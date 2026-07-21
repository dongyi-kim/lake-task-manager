# -*- coding: utf-8 -*-
"""description(정화된 HTML)을 '=== 제목 ===' 구분선 기준으로 영역 분할.

사내 티켓은 본문을 아래처럼 나눠 쓰는 관례가 있다.

    안녕하세요
    ==== 신청정보 ====
    이렇게 신청함

이걸 다이얼로그에서 제목 달린 별도 카드로 끊어 보여주기 위한 전처리.

**왜 HTML 을 자르나** — prod 는 Jira 가 렌더한 HTML(renderedFields)만 준다.
원문 wiki 를 잘라 섹션별로 다시 렌더할 수단이 우리에겐 없다.

**왜 파서가 필요한가** — Jira wiki 는 홑 줄바꿈을 `<br/>` 로 낸다. 즉 위 예시는
`<p>안녕하세요<br/>==== 신청정보 ====<br/>이렇게 신청함</p>` 처럼 **한 문단 안**에
들어온다. 블록 단위로는 못 자르고, `<br/>` 위치에서 잘라 문단을 다시 닫고/열어야 한다.

**안전 장치** — 자르는 지점의 열린 태그 스택이 `[]`(블록 사이) 또는 `['p']`(문단 안)일
때만 자른다. 표/패널/리스트 안의 구분선은 무시한다(잘랐다간 태그가 깨지고, 속성 있는
태그를 복원할 수 없다).
"""

import re
from html.parser import HTMLParser

# 줄로 끊어 보는 태그 — 이 경계마다 '한 줄'이 끝난 것으로 본다
_LINE_BREAKERS = {"br", "p", "div", "ul", "ol", "li", "table", "thead", "tbody",
                  "tr", "td", "th", "blockquote", "pre", "hr",
                  "h1", "h2", "h3", "h4", "h5", "h6"}
_VOID = {"br", "hr", "img"}

# '=' 3개 이상 + 제목 + '=' 3개 이상 (양끝 공백 무시)
_DIV_RE = re.compile(r"={3,}\s*(.*?)\s*={3,}", re.S)


def _is_divider(line):
    """구분선이면 (True, 제목|None). 제목이 비면 None."""
    s = line.strip()
    if not s or not _DIV_RE.fullmatch(s):
        return False, None
    title = s.strip("=").strip()
    return True, (title or None)


class _Cutter(HTMLParser):
    """태그 오프셋을 훑어 '자를 구간'을 찾는다. 원본 문자열은 슬라이스로만 쓴다(재직렬화 X)."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.html = html
        # (line, col) → 절대 오프셋
        self._line_off = [0]
        for ln in html.split("\n")[:-1]:
            self._line_off.append(self._line_off[-1] + len(ln) + 1)
        self.stack = []
        self.cuts = []          # (start, end, title, stack_snapshot)
        self._line_start = 0    # 현재 줄 내용이 시작하는 오프셋
        self._last_end = 0      # 마지막으로 처리한 태그의 끝

    def _off(self):
        ln, col = self.getpos()
        return self._line_off[ln - 1] + col

    def _flush(self, sep_start, sep_end):
        """[_line_start, sep_start) 를 한 줄로 보고 구분선인지 판정."""
        raw = self.html[self._line_start:sep_start]
        text = re.sub(r"<[^>]*>", "", raw)          # 인라인 태그 제거 후 텍스트만
        ok, title = _is_divider(text)
        if ok:
            self.cuts.append((self._line_start, sep_start, title, list(self.stack)))
        self._line_start = sep_end

    def handle_starttag(self, tag, attrs):
        s = self._off()
        e = s + len(self.get_starttag_text() or "")
        if tag in _LINE_BREAKERS:
            self._flush(s, e)
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        s = self._off()
        e = s + len(self.get_starttag_text() or "")
        if tag in _LINE_BREAKERS:
            self._flush(s, e)

    def handle_endtag(self, tag):
        s = self._off()
        e = s + len(tag) + 3                        # </tag>
        if tag in _LINE_BREAKERS:
            self._flush(s, e)
        if tag in self.stack:                       # 짝 안 맞는 닫힘은 무시
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i] == tag:
                    del self.stack[i:]
                    break


def split_sections(html):
    """정화된 HTML → [{'title': str|None, 'html': str}].

    구분선이 없으면 원본 그대로 한 덩어리([{'title': None, 'html': html}]).
    """
    if not html or "===" not in html:
        return [{"title": None, "html": html or ""}]

    c = _Cutter(html)
    c.feed(html)
    c.close()
    c._flush(len(html), len(html))                  # 마지막 줄(닫는 태그 없이 끝나는 경우)

    # 문단 안(['p']) 또는 블록 사이([]) 에서만 자른다 — 그 외는 태그가 깨진다
    cuts = [x for x in c.cuts if x[3] == [] or x[3] == ["p"]]
    if not cuts:
        return [{"title": None, "html": html}]

    out, pos, title, prefix = [], 0, None, ""
    for start, end, t, stack in cuts:
        chunk = prefix + html[pos:start]
        if stack == ["p"]:
            chunk += "</p>"                          # 잘리느라 열린 채 끊긴 문단을 닫고
            prefix = "<p>"                           # 다음 조각은 문단을 다시 연다
        else:
            prefix = ""
        out.append({"title": title, "html": _clean(chunk)})
        title, pos = t, end
    out.append({"title": title, "html": _clean(prefix + html[pos:])})

    return [s for s in out if _has_content(s["html"])] or [{"title": None, "html": html}]


_EMPTY_P_RE = re.compile(r"<p>(?:\s|&nbsp;)*</p>")
# 조각 '경계'에 남은 <br> — 구분선을 들어낸 자리라 그대로 두면 빈 줄로 보인다.
# (문단 중간의 <br> 는 사용자가 의도한 줄바꿈이므로 건드리지 않는다)
_LEAD_BR_RE = re.compile(r"^(<p>)(?:\s*<br\s*/?>)+")
_TAIL_BR_RE = re.compile(r"(?:<br\s*/?>\s*)+(</p>)$")


def _clean(html):
    """분할하며 생긴 빈 문단·경계 <br> 제거 + 양끝 공백 정리."""
    h = _EMPTY_P_RE.sub("", html or "").strip()
    h = _LEAD_BR_RE.sub(r"\1", h)
    return _TAIL_BR_RE.sub(r"\1", h)


def _has_content(html):
    """태그를 걷어낸 뒤 실제 내용이 있는지(빈 조각 제거용)."""
    return bool(re.sub(r"<[^>]*>|&nbsp;|\s", "", html or ""))
