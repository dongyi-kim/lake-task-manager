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
#  ※ input 은 제외(void 요소라 닫는 태그가 없어 subtree 삭제로 처리하면 뒤 내용이 통째로 잘림).
#    태스크리스트 체크박스는 _emit_input 에서 읽기전용으로 별도 렌더.
_DROP_SUBTREE = {
    "script", "style", "iframe", "object", "embed", "noscript", "template",
    "svg", "math", "form", "button", "textarea", "select", "option",
    "link", "meta", "base", "title", "head", "frame", "frameset", "applet",
}
_VOID = {"br", "hr", "img", "col", "input"}
# HTML void 요소 전체 — DROP_SUBTREE 에 든 void(embed/link/meta/base/frame 등)가 '닫는 태그 없음'으로
# 뒤 내용을 삼키지 않도록, 시작 태그에서 subtree 진입 대신 그 태그만 버린다.
_VOID_ALL = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr", "frame"}
# 태그별 허용 속성 (그 외 전부 제거; on* 은 어디서도 불허)
_ALLOWED_ATTRS = {
    "a": {"href", "title", "data-ext", "download"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "col": {"span"},
    "colgroup": {"span"},
    "ol": {"start", "type"},
}
_URL_ATTRS = {"href", "src"}
_SAFE_SCHEMES = ("http://", "https://", "mailto:", "tel:")
# Confluence(문서) 링크 판별 — **URL 경로 패턴** 기반(호스트/스페이스 이름과 무관).
#   신형: {base}/spaces/{space}/pages/{id}/{title}?{qs}  (space 는 jira 와 다를 수·여러 곳일 수 있음)
#   구형(DC): /display/{SPACE}/... · /pages/viewpage.action · Cloud: /wiki/...
_CONF_RE = re.compile(r"(?:confluence|/wiki/|/display/|/spaces/|/pages/)", re.I)
# class 는 모든 허용 태그에서 받되, 값은 아래 토큰(또는 lang-*)만 남긴다 — 임의 클래스 주입 차단.
#   user-hover = 실 Jira DC 의 사용자 맨션 앵커 class(볼드+컬러 스타일 대상). conf-link = 아래에서 부여.
_ALLOWED_CLASSES = {
    "panel", "panel-title", "panel-body", "callout", "code", "jecodeblock", "user-hover", "conf-link",
    "image-wrap",                      # 실 Jira DC 가 본문 이미지를 감싸는 래퍼
    "attachment", "file-badge",        # 첨부 파일 링크([^name]) — 칩으로 그린다
    "callout-note", "callout-info", "callout-warning", "callout-tip",
    "callout-success", "callout-error",
}

# ── 벤더 콜아웃 class 정규화 ──────────────────────────────────────────
# 실 사내 Jira DC 는 {info} 매크로를 <div class="jePanel_info"> 로 렌더한다(dev mock 은
# <div class="callout callout-info">). 정규화 없이는 allowlist 에 없는 class 라 통째로
# 지워져 **prod 에서만 콜아웃이 맨 div 로 보인다**(스타일 0). 여기서 표준형으로 바꿔두면
# CSS·프론트는 `callout callout-*` 하나만 알면 된다.
_CALLOUT_TYPES = {"note", "info", "warning", "tip", "success", "error"}
_VENDOR_CALLOUT_RE = re.compile(r"^jePanel[_-](\w+)$", re.I)


def _expand_class(tok):
    """class 토큰 하나 → 표준 토큰 목록(정규화 대상이 아니면 자기 자신).

    코드블록: **원래 Jira 와 동일한 태그** 를 유지한다 — `<pre class="jecodeblock"><code class="language-X">`.
    (에디터·mock·prod 전부 이 형태로 통일. jecodeblock class 와 language-* class 를 살린다.
     highlight.js 가 language-* 를 읽어 렌더 강조.)"""
    m = _VENDOR_CALLOUT_RE.match(tok)
    if m:
        t = m.group(1).lower()
        # 모르는 타입도 콜아웃 형태는 유지한다(무스타일 div 로 떨어지는 것보다 낫다)
        return ["callout", "callout-" + (t if t in _CALLOUT_TYPES else "note")]
    return [tok]


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
        self._cb_index = 0       # 이 필드에서 몇 번째 체크박스인가 — 원본 순서 = 화면 순서

    # 체크박스는 **토글 가능**하게, 라디오는 읽기전용으로 렌더(그 외 input 은 제거)
    def _emit_input(self, attrs):
        d = {(k or "").lower(): v for k, v in attrs}
        t = (d.get("type") or "").strip().lower()
        if t not in ("checkbox", "radio"):
            return
        chk = " checked" if "checked" in d else ""
        if t == "radio":
            self.out.append('<input type="radio"%s disabled />' % chk)
            return
        # 체크박스: data-cb-index 로 짚는다. 프론트가 수정권한이 있을 때만 클릭을 받아
        # **원본 필드의 이 index 번째 체크박스**를 뒤집어 저장한다(순서가 원본과 같아 index 로 매칭).
        idx = self._cb_index
        self._cb_index += 1
        self.out.append('<input type="checkbox"%s class="tkt-cb" data-cb-index="%d" />'
                        % (chk, idx))

    # 위험 서브트리(script 등) 내부는 태그·텍스트 모두 버림
    def handle_starttag(self, tag, attrs):
        if self._drop_depth:
            if tag in _DROP_SUBTREE and tag not in _VOID_ALL:   # void 는 subtree 진입 안 함
                self._drop_depth += 1
            return
        if tag == "input":
            self._emit_input(attrs)
            return
        if tag in _DROP_SUBTREE:
            if tag in _VOID_ALL:
                return           # void drop 태그: 그 태그만 버리고 이어감(뒤 내용 안 삼킴)
            self._drop_depth = 1
            return
        if tag not in _ALLOWED_TAGS:
            return               # 태그만 제거, 자식 텍스트는 남김
        self.out.append("<" + tag + self._attrs(tag, attrs) + ">")

    def handle_startendtag(self, tag, attrs):
        if self._drop_depth:
            return
        if tag == "input":
            self._emit_input(attrs)
            return
        if tag in _DROP_SUBTREE or tag not in _ALLOWED_TAGS:
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
            if k == "style":
                # style 은 통째로 위험(behaviour·url·expression)하지만, **정렬·글꼴만** 골라
                # 통과시킨다 — 에디터의 text-align/font-family 가 렌더·재편집에서 살아남게.
                safe = _safe_style(v)
                if safe:
                    parts.append('style="' + escape(safe, quote=True) + '"')
                continue
            if k.startswith("on") or k in ("srcset", "formaction", "xlink:href"):
                continue                 # 이벤트 핸들러·기타 위험 속성 제거
            if k == "class":             # 허용 클래스 토큰(또는 코드 언어 language-*/lang-*)만 유지
                toks = [n for t in (v or "").split() for n in _expand_class(t)]
                classes += [t for t in toks
                            if t in _ALLOWED_CLASSES or t.startswith(("lang-", "language-"))]
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
            # ※ 첨부의 내려받기 처리는 proxy_attachment_links 가 맡는다 — 이 시점엔 아직
            #   file-badge 가 붙기 전이라 여기서는 첨부인지 알 수 없다.
            parts.append('target="_blank"')
            parts.append('rel="noopener noreferrer nofollow"')
        if classes:
            parts.insert(0, 'class="' + escape(" ".join(dict.fromkeys(classes)), quote=True) + '"')
        return (" " + " ".join(parts)) if parts else ""


# 맨션 앵커 — 실 Jira 는 텍스트를 displayName('{본명} {회사}')으로 렌더한다.
_MENTION_A_RE = re.compile(r'(<a\b[^>]*\bclass="[^"]*user-hover[^"]*"[^>]*>)([^<]+)(</a>)', re.I)


def shorten_mention_names(html):
    """맨션 앵커 텍스트를 **본명만**으로 줄인다.
    본문에 박히는 이름은 짧아야 하고(에디터도 본명으로 넣는다), 회사까지 붙으면 문장이 길어진다.
    동명이인 구분이 필요한 곳은 @멘션 팝업이지 본문이 아니다."""
    if not html:
        return html
    from .names import real_name
    return _MENTION_A_RE.sub(
        lambda m: m.group(1) + (real_name(unescape(m.group(2))) or m.group(2)) + m.group(3), html)


# style 에서 **정렬·글꼴만** 남긴다. url()·expression 등은 값 자체를 통과시키지 않는다
# (값에 괄호/콜론이 있으면 버린다 → text-align:center, font-family:"..." 처럼 단순한 것만).
_ALIGN_OK = {"left", "right", "center", "justify"}
_FONT_RE = re.compile(r"^[\w \-,'\"]+$")


def _safe_style(v):
    out = []
    for decl in (v or "").split(";"):
        if ":" not in decl:
            continue
        prop, val = decl.split(":", 1)
        prop = prop.strip().lower()
        val = val.strip()
        if "url(" in val.lower() or "expression" in val.lower() or "(" in val and prop != "font-family":
            continue
        if prop == "text-align" and val.lower() in _ALIGN_OK:
            out.append("text-align:" + val.lower())
        elif prop == "font-family" and _FONT_RE.match(val):
            out.append("font-family:" + val)
    return ";".join(out)


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
# ★ 제로폭 문자(zwnj)를 빠뜨리면 Jira/에디터가 &zwnj; 로 채운 빈 문단이 빈 줄로 남는다.
_ZW = r"(?:&zwnj;|&zwj;|&#x?200[bcd];|&#8203;|&#8204;|&#8205;|&#65279;|​|‌|‍|﻿| )"
_BLANK = r"(?:\s|&nbsp;| |<br\s*/?>|" + _ZW + r")"
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


def proxy_attachment_images(html):
    """dev(mock/local) 용 — **Jira 첨부 경로(/secure/…) 이미지만** same-origin 프록시로 재작성.
    첨부는 jira820(in-process 또는 :8080)에 있어 앱 오리진으로는 못 받는다. 반대로 앱이 직접
    서빙하는 static 이미지(/ticket-sample.svg 등)는 그대로 둬야 하므로 접두사로 가른다."""
    if not html:
        return html

    def _one(src):
        s = (src or "").strip()
        if not s.startswith("/secure/"):
            return src
        return "/api/img?u=" + urllib.parse.quote(s, safe="")

    return _IMG_SRC_RE.sub(lambda m: m.group(1) + _one(m.group(2)) + m.group(3), html)


# 앵커 **여는 태그 전체**를 잡는다. href 까지만 잡으면 뒤쪽에 있는 class 를 못 봐서
# class 가 두 번 들어간 태그가 만들어진다(브라우저는 앞의 것만 쓴다 — 실제로 그랬다).
_A_OPEN_RE = re.compile(r"<a\b[^>]*>", re.I)
_HREF_IN_TAG = re.compile(r'\bhref="([^"]*)"', re.I)
_CLASS_IN_TAG = re.compile(r'\bclass="([^"]*)"', re.I)


def proxy_attachment_links(html, jira_base=""):
    """첨부 **링크**(<a href="/secure/attachment/…">)를 same-origin 프록시로 재작성한다.

    이미지와 같은 문제다: 첨부는 Jira 에 있는데 링크는 앱 오리진으로 해석돼 404 가 난다
    (이미지는 이미 프록시하고 있었는데 링크만 빠져 있었다 — 눌러 보면 Not Found).
    함께 file-badge 클래스와 data-ext 를 붙인다 — 첨부는 문장 속 링크가 아니라 '붙어 있는
    물건' 이라 칩으로 읽히는 편이 맞고, 렌더된 코멘트는 JS 를 안 거치므로 종류별 색을 주려면
    CSS 가 볼 수 있는 표식이 필요하다.
    """
    if not html:
        return html
    base = (jira_base or "").rstrip("/")

    def _one(m):
        tag = m.group(0)
        hm = _HREF_IN_TAG.search(tag)
        if not hm:
            return tag
        href = unescape(hm.group(1) or "").strip()
        # ★ '/secure/' 로 시작한다고 다 첨부가 아니다 — 멘션 프로필도 /secure/ViewProfile.jspa 다.
        #   넓게 잡았더니 맨션 앵커가 첨부 링크로 바뀌어 user-hover 클래스와 프로필 링크가 통째로
        #   사라졌다. 첨부 경로만 정확히 집는다.
        if href.startswith("/secure/attachment/"):
            target = href                       # dev — provider 가 base 를 붙인다
        elif base and href.startswith(base + "/secure/attachment/"):
            target = href                       # prod — 절대 URL(호스트 검증은 프록시가 한다)
        else:
            return tag

        new_href = "/api/file?u=" + urllib.parse.quote(target, safe="")
        tag = tag[:hm.start(1)] + escape(new_href, quote=True) + tag[hm.end(1):]

        name = urllib.parse.unquote(target.rstrip("/").split("/")[-1])
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext and len(ext) <= 8 and ext.isalnum() and "data-ext=" not in tag:
            tag = tag.replace("<a", '<a data-ext="' + escape(ext, quote=True) + '"', 1)

        # 새 탭이 아니라 **내려받기**로. target=_blank 로 열면 앱 창(Chromium 앱 모드)에서 팝업이
        # 뜨고 그게 곧바로 다운로드로 바뀌며 임시 이름(해시·확장자 없음)으로 저장된다.
        tag = re.sub(r'\s*target="[^"]*"', "", tag)
        if " download" not in tag:
            tag = tag.replace("<a", '<a download="' + escape(name, quote=True) + '"', 1)

        cm = _CLASS_IN_TAG.search(tag)
        if cm:
            if "file-badge" not in cm.group(1).split():
                tag = tag[:cm.start(1)] + (cm.group(1) + " file-badge").strip() + tag[cm.end(1):]
        else:
            tag = tag.replace("<a", '<a class="file-badge"', 1)
        return tag

    return _A_OPEN_RE.sub(_one, html)


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
