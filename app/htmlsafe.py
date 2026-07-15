"""
HTML sanitizer — Jira(prod) 의 renderedFields.description 처럼 **신뢰할 수 없는 HTML** 을
allowlist 방식으로 정화해 XSS 를 제거한다. 티켓 상세 다이얼로그가 v-html 로 렌더하기 전
반드시 이 함수를 거친다. (백엔드에서 정화 → 프론트는 정화된 결과만 렌더 → pytest 로 검증)

원칙:
- **허용 태그/속성만 남기고 나머지는 제거**(모르는 건 거부). script/style/iframe 등은 내용까지 삭제.
- on* 이벤트 핸들러, javascript:/vbscript:/data:text 등 위험 URL 제거. a[href]·img[src] 는 안전 scheme 만.
- 링크에는 rel=noopener noreferrer nofollow, target=_blank 강제.
"""

from __future__ import annotations

import re
import urllib.parse
from html import escape, unescape
from html.parser import HTMLParser

# 표시에 필요한 서식 태그만 허용 (구조/텍스트/표/코드/링크/이미지).
_ALLOWED_TAGS = {
    "p", "br", "hr", "b", "strong", "i", "em", "u", "s", "strike", "del", "ins",
    "sub", "sup", "small", "mark", "code", "pre", "kbd", "samp", "tt",
    "blockquote", "q", "cite", "ul", "ol", "li", "dl", "dt", "dd",
    "a", "img", "span", "div", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
}
# 내용까지 통째로 삭제(태그를 지우고 자식 텍스트를 남기면 위험한 것들)
_DROP_SUBTREE = {
    "script", "style", "iframe", "object", "embed", "noscript", "template",
    "svg", "math", "form", "input", "button", "textarea", "select", "option",
    "link", "meta", "base", "title", "head", "frame", "frameset", "applet",
}
_VOID = {"br", "hr", "img", "col"}
# 태그별 허용 속성 (그 외 전부 제거; on* 은 어디서도 불허)
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "col": {"span"},
    "colgroup": {"span"},
    "ol": {"start", "type"},
}
_URL_ATTRS = {"href", "src"}
_SAFE_SCHEMES = ("http://", "https://", "mailto:", "tel:")
# Confluence(문서) 링크 판별 — 실 Jira/mock 모두 URL 로 판별해 뱃지 class(conf-link) 부여.
_CONF_RE = re.compile(r"(?:confluence|/wiki/|/display/|/pages/viewpage|/spaces/)", re.I)
# class 는 모든 허용 태그에서 받되, 값은 아래 토큰(또는 lang-*)만 남긴다 — 임의 클래스 주입 차단.
#   user-hover = 실 Jira DC 의 사용자 맨션 앵커 class(볼드+컬러 스타일 대상). conf-link = 아래에서 부여.
_ALLOWED_CLASSES = {
    "panel", "panel-title", "panel-body", "callout", "code", "user-hover", "conf-link",
    "callout-note", "callout-info", "callout-warning", "callout-tip",
    "callout-success", "callout-error",
}


def _safe_url(value, allow_data_image=False):
    """href/src 로 안전한 URL 인지. 안전 scheme 또는 상대경로/앵커만 허용."""
    v = unescape(value or "").strip()
    low = v.lower()
    # 제어문자 제거 후 재검사 (예: "java\tscript:" 우회 방지)
    low = "".join(ch for ch in low if ord(ch) >= 0x20 or ch in "").replace("\t", "").replace("\n", "").replace("\r", "")
    if low.startswith(_SAFE_SCHEMES):
        return True
    if allow_data_image and low.startswith("data:image/") and "script" not in low:
        return True
    # scheme 이 있으면(콜론이 첫 슬래시/물음표/해시 앞에 존재) 거부, 없으면 상대경로로 허용
    head = low.split("/")[0].split("?")[0].split("#")[0]
    if ":" in head:
        return False           # javascript:, data:text, vbscript: 등
    return True                # /browse/X, #anchor, foo.png 등 상대경로


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._drop_depth = 0     # _DROP_SUBTREE 내부면 >0

    # 위험 서브트리(script 등) 내부는 태그·텍스트 모두 버림
    def handle_starttag(self, tag, attrs):
        if self._drop_depth:
            if tag in _DROP_SUBTREE:
                self._drop_depth += 1
            return
        if tag in _DROP_SUBTREE:
            self._drop_depth = 1
            return
        if tag not in _ALLOWED_TAGS:
            return               # 태그만 제거, 자식 텍스트는 남김
        self.out.append("<" + tag + self._attrs(tag, attrs) + ">")

    def handle_startendtag(self, tag, attrs):
        if self._drop_depth or tag in _DROP_SUBTREE or tag not in _ALLOWED_TAGS:
            return
        self.out.append("<" + tag + self._attrs(tag, attrs) + " />")

    def handle_endtag(self, tag):
        if self._drop_depth:
            if tag in _DROP_SUBTREE:
                self._drop_depth -= 1
            return
        if tag not in _ALLOWED_TAGS or tag in _VOID:
            return
        self.out.append("</" + tag + ">")

    def handle_data(self, data):
        if self._drop_depth:
            return
        self.out.append(escape(data, quote=False))

    # 주석/선언/처리명령은 통째로 무시
    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def unknown_decl(self, data):
        pass

    def handle_pi(self, data):
        pass

    def _attrs(self, tag, attrs):
        allowed = _ALLOWED_ATTRS.get(tag, set())
        parts, classes, href_val = [], [], None
        for k, v in attrs:
            k = (k or "").lower()
            if k.startswith("on") or k in ("style", "srcset", "formaction", "xlink:href"):
                continue                 # 이벤트 핸들러·스타일·기타 위험 속성 제거
            if k == "class":             # 허용 클래스 토큰(또는 lang-*)만 유지
                classes += [t for t in (v or "").split() if t in _ALLOWED_CLASSES or t.startswith("lang-")]
                continue
            if k not in allowed:
                continue
            if k in _URL_ATTRS and not _safe_url(v, allow_data_image=(tag == "img")):
                continue
            if k == "href":
                href_val = v or ""
            parts.append(k + '="' + escape(v or "", quote=True) + '"')
        if tag == "a":
            # Confluence/문서 링크는 URL 로 판별해 뱃지 표식(실 Jira·mock 공통 — prod 에도 적용)
            if href_val and _CONF_RE.search(unescape(href_val)) and "conf-link" not in classes:
                classes.append("conf-link")
            parts.append('target="_blank"')
            parts.append('rel="noopener noreferrer nofollow"')
        if classes:
            parts.insert(0, 'class="' + escape(" ".join(dict.fromkeys(classes)), quote=True) + '"')
        return (" " + " ".join(parts)) if parts else ""


def sanitize_html(html):
    """신뢰 불가 HTML → allowlist 로 정화한 안전 HTML 문자열. 파싱 실패 시 전부 escape."""
    if not html:
        return ""
    try:
        s = _Sanitizer()
        s.feed(str(html))
        s.close()
        return "".join(s.out)
    except Exception:
        return escape(str(html), quote=False)


def text_to_html(text):
    """평문 description(mock/local, 또는 renderedFields 없음) → 안전 HTML. escape + 줄바꿈 <br>."""
    return escape(text or "", quote=False).replace("\r\n", "\n").replace("\n", "<br>")


# 빈 블록(공백/nbsp/br 만 든 <p>·<div>) — 실 Jira 렌더 HTML 이 만드는 과도한 여백 원인
_BLANK = r"(?:\s|&nbsp;| |<br\s*/?>)"
_EMPTY_BLOCK = r"<(?:p|div)(?:\s[^>]*)?>" + _BLANK + r"*</(?:p|div)>"
_BLANK_MARK = '<p class="blank"></p>'
_EMPTY_RUN_RE = re.compile(r"(?:" + _EMPTY_BLOCK + r"\s*)+", re.I)     # 연속 빈 블록(1개+) → 표식 1개
_LEAD_RE = re.compile(r"^(?:" + _BLANK + r"|" + re.escape(_BLANK_MARK) + r")+", re.I)
_TRAIL_RE = re.compile(r"(?:" + _BLANK + r"|" + re.escape(_BLANK_MARK) + r")+$", re.I)
_MANY_BR_RE = re.compile(r"(?:<br\s*/?>\s*){3,}", re.I)


def tidy_html(html):
    """렌더 HTML 정리 — 빈 문단은 '유지'하되 컴팩트하게:
    - 빈 문단/블록(공백·nbsp·br) → 표식 <p class="blank"></p> 로 정규화(연속은 1개로).
      실제 세로 간격은 CSS(.tkt-desc p.blank)에서 작게 → 3줄씩 먹던 빈 줄 해소.
    - 콘텐츠 앞/뒤의 빈 문단·공백만 트림. 과도한 연속 <br> 축소.
    (문단을 지우지 않으므로 글 중간 의도적 빈 줄은 보존.)"""
    if not html:
        return html
    s = _MANY_BR_RE.sub("<br /><br />", html)
    s = _EMPTY_RUN_RE.sub(_BLANK_MARK, s)
    s = _LEAD_RE.sub("", s)
    s = _TRAIL_RE.sub("", s)
    return s.strip()


_IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]*)("[^>]*>)', re.I)


def proxy_images(html, jira_base, allow_host):
    """정화된 HTML 의 <img src> 를 same-origin 인증 프록시(/api/img?u=)로 재작성.
    - 상대경로(/secure/..)  → jira_base 기준 절대화 후 프록시
    - 절대 URL(허용 호스트) → 프록시 (SSO 쿠키 미전달·크로스오리진 문제 회피)
    - data: / 허용 안 된 호스트 / 상대 파일명 → 그대로 (프록시 안 함)
    allow_host: host(str) -> bool. prod 에서만 호출(mock/local 이미지는 same-origin static)."""
    if not html:
        return html
    base = (jira_base or "").rstrip("/")
    scheme = (urllib.parse.urlparse(base).scheme or "https") + ":"

    def _one(src):
        s = (src or "").strip()
        if not s or s.startswith("data:"):
            return src
        if s.startswith("//"):
            host = s[2:].split("/")[0]
            if not allow_host(host):
                return src
            absu = scheme + s
        elif s.startswith("/"):
            absu = base + s
        elif s.startswith(("http://", "https://")):
            host = urllib.parse.urlparse(s).netloc
            if not allow_host(host):
                return src
            absu = s
        else:
            return src            # 상대 파일명 등은 건드리지 않음
        return "/api/img?u=" + urllib.parse.quote(absu, safe="")

    return _IMG_SRC_RE.sub(lambda m: m.group(1) + _one(m.group(2)) + m.group(3), html)
