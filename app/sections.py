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
from html import unescape
from html.parser import HTMLParser

# 줄로 끊어 보는 태그 — 이 경계마다 '한 줄'이 끝난 것으로 본다
_LINE_BREAKERS = {"br", "p", "div", "ul", "ol", "li", "table", "thead", "tbody",
                  "tr", "td", "th", "blockquote", "pre", "hr",
                  "h1", "h2", "h3", "h4", "h5", "h6"}
_VOID = {"br", "hr", "img"}

# '=' 3개 이상 + 제목 + '=' 3개 이상 (양끝 공백 무시)
_DIV_RE = re.compile(r"={3,}\s*(.*?)\s*={3,}", re.S)


# 화면에 안 보이는 문자들 — WYSIWYG 가 문단 끝에 흘려 넣는다(ZWSP/ZWNJ/BOM 등).
# str.strip() 은 이것들을 공백으로 보지 않아서, '빈 줄인데 빈 줄이 아닌' 줄이 생긴다.
# key:value 판정에서는 그 한 줄 때문에 **영역 전체가 표에서 탈락**한다.
# 잔여 문자는 문서 맨 끝에 붙기 마련이라 '마지막 영역만 표가 안 되는' 형태로 나타난다.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_SPACEY = {0x00a0: " ", 0x3000: " ", 0x2002: " ", 0x2003: " ", 0x2009: " "}


def _txt(raw):
    """HTML 조각 → 판정용 평문(태그 제거 · 엔티티 해제 · 보이지 않는 문자 제거)."""
    t = unescape(re.sub(r"<[^>]*>", "", raw or ""))
    return t.translate(_INVISIBLE).translate(_SPACEY).strip()


def _txt_inline(text):
    """이미 태그가 제거된 텍스트용 정규화."""
    return unescape(text or "").translate(_INVISIBLE).translate(_SPACEY).strip()


def _is_divider(line):
    """구분선이면 (True, 제목|None). 제목이 비면 None.

    WYSIWYG 에디터는 공백을 `&nbsp;` 로 낸다. 엔티티를 먼저 풀지 않으면
    `====&nbsp;신청정보&nbsp;====` 가 구분선으로 안 잡히고, 잡혀도 제목에
    `&nbsp;` 가 그대로 남는다.
    """
    s = _txt_inline(line)
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
            self.stack.append((tag, self.get_starttag_text() or ("<%s>" % tag)))

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
        for i in range(len(self.stack) - 1, -1, -1):   # 짝 안 맞는 닫힘은 무시
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _sec(title, html):
    why = []
    return {"title": title, "html": html, "kv": _kv_rows(html, why),
            "kvSkip": (why[0] if why else None)}


def split_sections(html):
    """정화된 HTML → [{'title': str|None, 'html': str}].

    구분선이 없으면 원본 그대로 한 덩어리([{'title': None, 'html': html}]).
    """
    if not html or "===" not in html:
        return [_sec(None, html or "")]

    c = _Cutter(html)
    c.feed(html)
    c.close()
    c._flush(len(html), len(html))                  # 마지막 줄(닫는 태그 없이 끝나는 경우)

    # 블록 사이([]) 또는 '속 빈 문단 컨테이너' 안에서만 자른다 — 그 외는 태그가 깨진다
    cuts = [x for x in c.cuts if _splittable(x[3])]
    if not cuts:
        return [_sec(None, html)]

    out, pos, title, prefix = [], 0, None, ""
    for start, end, t, stack in cuts:
        # 잘리느라 열린 채 끊긴 컨테이너를 역순으로 닫고, 다음 조각에서 원래 태그로 다시 연다
        chunk = prefix + html[pos:start] + "".join("</%s>" % tg for tg, _ in reversed(stack))
        prefix = "".join(raw for _, raw in stack)
        out.append({"title": title, "html": _clean(chunk)})
        title, pos = t, end
    out.append({"title": title, "html": _clean(prefix + html[pos:])})

    out = [s for s in out if _has_content(s["html"])] or [{"title": None, "html": html}]
    for sec in out:
        why = []
        sec["kv"] = _kv_rows(sec["html"], why)   # 표로 그릴 수 있으면 행 목록, 아니면 None
        sec["kvSkip"] = why[0] if why else None  # 아니면 그 이유(진단용)
    return out


# 자를 수 있는 컨테이너 — <p>/<div> 중 **class 가 없는** 것.
# 판정 기준이 'class 유무'인 이유: 패널·콜아웃 같은 의미는 class 로만 표현된다.
#   <div class="callout callout-info"> 안에서 자르면 콜아웃이 두 개로 쪼개진다 → 거부.
#   <p dir="auto"> 처럼 표현용 속성은 의미가 없다 → 허용.
# (실 사내 Jira 는 <p dir="auto"> 를 쓴다. sanitizer 가 dir 을 떼주긴 하지만,
#  '속성이 아예 없을 것'을 조건으로 두면 sanitizer 정책이 바뀌는 순간 분할이 조용히 멈춘다.)
_PLAIN_TAG_RE = re.compile(r"^<(?:p|div)(?:\s[^>]*)?>$", re.I)
_HAS_CLASS_RE = re.compile(r"\sclass\s*=", re.I)


def _splittable(stack):
    return all(_PLAIN_TAG_RE.match(raw) and not _HAS_CLASS_RE.search(raw)
               for _, raw in stack)


_EMPTY_P_RE = re.compile(r"<(p|div)(?:\s[^>]*)?>(?:\s|&nbsp;)*</\1>")
# 조각 '경계'에 남은 <br> — 구분선을 들어낸 자리라 그대로 두면 빈 줄로 보인다.
# (문단 중간의 <br> 는 사용자가 의도한 줄바꿈이므로 건드리지 않는다)
# 여는 태그가 겹칠 수 있어(<div><p>) 태그 묶음 전체를 하나로 본다.
_LEAD_BR_RE = re.compile(r"^((?:<(?:p|div)(?:\s[^>]*)?>)+)(?:\s*<br\s*/?>)+")
_TAIL_BR_RE = re.compile(r"(?:<br\s*/?>\s*)+((?:</(?:p|div)>)+)$")


def _clean(html):
    """분할하며 생긴 빈 문단·경계 <br> 제거 + 양끝 공백 정리."""
    h = (html or "").strip()
    while True:                       # <div><p></p></div> 처럼 중첩된 빈 껍데기까지
        h2 = _EMPTY_P_RE.sub("", h)
        if h2 == h:
            break
        h = h2
    h = _LEAD_BR_RE.sub(r"\1", h)
    return _TAIL_BR_RE.sub(r"\1", h).strip()


# ── key : value 영역 → 표 ────────────────────────────────────────────
# VoC 티켓은 시스템이 아래 블록을 주입하는데, 내용이 전부 "{key} : {value}" 다.
#     ==================== 신청정보 ====================
#     ==================== {2} 시스템정보 ====================
# 제목으로 알아보지 않고 **내용 모양**으로 판정한다 — {%d} 같은 변형까지 자동으로 걸린다.

# 표로 만들 수 없는 블록(표·리스트·인용·코드·이미지)이 섞여 있으면 손대지 않는다
_RICH_RE = re.compile(r"<(?:table|ul|ol|pre|blockquote|img|h[1-6])[\s>]", re.I)
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)")
MIN_KV_ROWS = 2                      # 한 줄짜리를 표로 만들면 오히려 산만하다
# 반각/전각 콜론 — 사내 문서는 한글 IME 로 전각(：)이 섞여 들어오는 경우가 있다
_COLONS = (":", "：")


def _balanced(frag):
    """조각 안의 태그 짝이 맞는지 — 안 맞으면 잘라 쓰면 안 된다(<b>k : v</b> 같은 경우)."""
    depth = {}
    for close, tag in _TAG_RE.findall(frag):
        t = tag.lower()
        if t in ("br", "img", "hr"):
            continue
        depth[t] = depth.get(t, 0) + (-1 if close else 1)
        if depth[t] < 0:
            return False
    return not any(depth.values())


def _split_kv(raw):
    """한 줄 HTML → (키 평문, 값 HTML). key:value 가 아니면 None."""
    depth = 0
    for i, ch in enumerate(raw):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch in _COLONS and depth == 0:
            left, right = raw[:i], raw[i + 1:]
            if "<" in left:                       # 키에 마크업이 끼면 잘라 쓰기 위험
                return None
            key = _txt_inline(left)
            if not key or len(key) > 60:          # 문장에 콜론이 낀 경우를 걸러낸다
                return None
            if not _balanced(right):
                return None
            return key, right.strip()
    return None


def _kv_rows(html, why=None):
    """영역 전체가 'key : value' 로만 이뤄졌으면 [{k, html}], 아니면 None.

    why: 리스트를 주면 '표로 안 만든 이유'를 한 줄 담아 준다(진단용).
         실 데이터를 밖으로 내보내지 않고 원인을 짚기 위한 장치다.
    """
    def no(reason):
        if why is not None:
            why.append(reason)
        return None

    if not html:
        return no("내용 없음")
    m = _RICH_RE.search(html)
    if m:
        return no("표로 만들 수 없는 블록이 섞임: <%s>" % m.group(0).strip("<> "))

    rows = []
    for n, raw in enumerate(re.split(r"<br\s*/?>|</?(?:p|div)[^>]*>", html), 1):
        text = _txt(raw)
        if not text:
            continue                              # 빈 줄은 무시
        kv = _split_kv(raw)
        if not kv:
            # 왜 어긋났는지까지 — 콜론이 아예 없는지 / 키가 이상한지
            if not any(c in text for c in _COLONS):
                return no("%d번째 줄에 콜론이 없음: %.40s" % (n, text))
            return no("%d번째 줄을 key:value 로 못 나눔(키에 마크업/길이 초과 등): %.40s" % (n, text))
        rows.append({"k": kv[0], "html": kv[1]})
    if len(rows) < MIN_KV_ROWS:
        return no("key:value 줄이 %d개뿐(최소 %d개 필요)" % (len(rows), MIN_KV_ROWS))
    return rows


def _has_content(html):
    """태그를 걷어낸 뒤 실제 내용이 있는지(빈 조각 제거용)."""
    return bool(re.sub(r"<[^>]*>|&nbsp;|\s", "", html or ""))
