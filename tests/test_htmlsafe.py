"""HTML sanitizer 테스트 — 티켓 상세의 신뢰 불가 description(HTML) 정화 검증.

두 축을 충분히 커버한다:
  1) 보안: XSS 벡터(script/이벤트핸들러/위험 URL/우회 트릭)가 모두 무력화되는가.
  2) 서식 보존: 정상적인 서식 태그(p/ul/table/code/a/img 등)는 살아남는가.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.htmlsafe import proxy_images, sanitize_html, text_to_html, tidy_html   # noqa: E402


# ── 1. 위험 요소 제거 ───────────────────────────────────────────────
def test_script_tag_and_content_removed():
    out = sanitize_html("<p>안녕</p><script>alert(1)</script>")
    assert "<script" not in out.lower()
    assert "alert(1)" not in out          # 내용까지 삭제
    assert "안녕" in out


def test_uppercase_and_mixed_case_script_removed():
    # 대/혼합 대소문자 script 도 본문까지 제거. 사이/뒤 일반 텍스트는 보존.
    out = sanitize_html("<SCRIPT>alert(1)</SCRIPT>보임<ScRiPt>alert(2)</ScRiPt>끝")
    assert "alert" not in out
    assert "<script" not in out.lower()
    assert "보임" in out and "끝" in out


def test_style_iframe_object_svg_removed():
    for payload in [
        "<style>body{background:url(javascript:alert(1))}</style>x",
        "<iframe src='javascript:alert(1)'></iframe>x",
        "<object data='data:text/html,<script>alert(1)</script>'></object>x",
        "<svg><script>alert(1)</script></svg>x",
        "<math><mtext></mtext></math>x",
    ]:
        out = sanitize_html(payload)
        assert "alert" not in out
        assert "<iframe" not in out.lower() and "<object" not in out.lower()
        assert "<svg" not in out.lower() and "<style" not in out.lower()
        assert out.endswith("x")


def test_event_handlers_stripped():
    out = sanitize_html('<b onclick="alert(1)" onmouseover="x()">굵게</b>')
    assert "onclick" not in out.lower()
    assert "onmouseover" not in out.lower()
    assert "alert(1)" not in out
    assert "굵게" in out and "<b" in out


def test_img_onerror_stripped_but_img_kept():
    out = sanitize_html('<img src="x.png" onerror="alert(1)">')
    assert "onerror" not in out.lower()
    assert "alert" not in out
    assert "<img" in out and 'src="x.png"' in out


def test_javascript_href_removed():
    for href in ["javascript:alert(1)", "JAVASCRIPT:alert(1)", "  javascript:alert(1)",
                 "jav\tascript:alert(1)", "vbscript:msgbox(1)"]:
        out = sanitize_html('<a href="' + href + '">클릭</a>')
        assert "javascript" not in out.lower().replace("java\tscript", "")
        assert "vbscript" not in out.lower()
        assert "href=" not in out          # 위험 href 는 통째로 제거
        assert "클릭" in out               # 텍스트는 유지


def test_data_text_html_href_removed_but_http_kept():
    bad = sanitize_html('<a href="data:text/html,<script>alert(1)</script>">x</a>')
    assert "href=" not in bad
    good = sanitize_html('<a href="https://jira.example/browse/DL-1">DL-1</a>')
    assert 'href="https://jira.example/browse/DL-1"' in good


def test_relative_and_anchor_href_allowed():
    out = sanitize_html('<a href="/browse/DL-9">a</a><a href="#top">b</a>')
    assert 'href="/browse/DL-9"' in out
    assert 'href="#top"' in out


def test_img_data_image_allowed_data_text_blocked():
    ok = sanitize_html('<img src="data:image/png;base64,iVBORw0KGgo=">')
    assert "<img" in ok and "src=" in ok
    bad = sanitize_html('<img src="data:text/html,<script>alert(1)</script>">')
    assert "src=" not in bad               # data:text 은 이미지가 아니므로 제거


def test_style_attribute_removed():
    out = sanitize_html('<div style="position:absolute;background:url(javascript:alert(1))">x</div>')
    assert "style=" not in out.lower()
    assert "javascript" not in out.lower()
    assert "x" in out


def test_entity_escaped_payload_stays_inert():
    # 이미 escape 된 스크립트 문자열은 그대로 무해한 텍스트로 남아야
    out = sanitize_html("&lt;script&gt;alert(1)&lt;/script&gt;")
    assert "<script" not in out.lower()
    assert "alert(1)" in out               # 텍스트로 표시(실행 아님)


def test_links_get_noopener_and_target():
    out = sanitize_html('<a href="https://x.test/">L</a>')
    assert 'rel="noopener noreferrer nofollow"' in out
    assert 'target="_blank"' in out


def test_unknown_tag_dropped_but_text_kept():
    out = sanitize_html("<foo><bar>텍스트</bar></foo>")
    assert "<foo" not in out and "<bar" not in out
    assert "텍스트" in out


def test_comment_and_declaration_removed():
    out = sanitize_html("<!-- <script>alert(1)</script> --><p>본문</p><!DOCTYPE html>")
    assert "alert" not in out
    assert "본문" in out


# ── 2. 정상 서식 보존 ───────────────────────────────────────────────
def test_basic_formatting_preserved():
    src = "<p>문단 <strong>강조</strong> <em>기울임</em></p><ul><li>항목1</li><li>항목2</li></ul>"
    out = sanitize_html(src)
    for frag in ["<p>", "<strong>", "강조", "<em>", "<ul>", "<li>", "항목1", "항목2"]:
        assert frag in out


def test_table_and_code_preserved():
    src = ("<table><thead><tr><th>k</th></tr></thead>"
           "<tbody><tr><td colspan=\"2\">v</td></tr></tbody></table>"
           "<pre><code>print(1)</code></pre>")
    out = sanitize_html(src)
    assert "<table>" in out and "<th>" in out and "<td" in out and 'colspan="2"' in out
    assert "<pre>" in out and "<code>" in out and "print(1)" in out


def test_heading_and_blockquote_preserved():
    out = sanitize_html("<h2>제목</h2><blockquote>인용</blockquote>")
    assert "<h2>" in out and "제목" in out and "<blockquote>" in out and "인용" in out


def test_allowed_classes_kept_arbitrary_dropped():
    out = sanitize_html('<div class="callout callout-warning evil-class">경고</div>')
    assert 'class="callout callout-warning"' in out    # 허용 토큰만
    assert "evil-class" not in out
    # code 하이라이트용 lang-* 는 접두 허용
    out2 = sanitize_html('<code class="lang-python">x</code>')
    assert 'class="lang-python"' in out2
    # 허용 토큰이 하나도 없으면 class 속성 자체 제거
    out3 = sanitize_html('<p class="attacker">x</p>')
    assert "class=" not in out3


def test_mention_userhover_class_kept():
    # 실 Jira 맨션 앵커(a.user-hover)는 class 유지 → 프론트가 볼드+컬러 스타일
    out = sanitize_html('<a class="user-hover" rel="kim" href="/secure/ViewProfile.jspa?name=kim">홍길동</a>')
    assert 'class="user-hover"' in out
    assert 'href="/secure/ViewProfile.jspa?name=kim"' in out


def test_confluence_link_gets_conf_link_class_by_url():
    # 소스에 class 가 없어도 Confluence URL 이면 정화기가 conf-link 부여(실 Jira·mock 공통, prod 적용)
    for url in [
        # 실제 사내 신형 패턴: /spaces/{space}/pages/{id}/{title}?{qs} — space 가 여러 곳·jira 와 달라도 OK
        "https://wiki.corp.com/spaces/DATAENG/pages/123456/My+Doc?focusedCommentId=7",
        "https://kms.corp.com/spaces/PMO/pages/98765/%ED%9A%8C%EC%9D%98%EB%A1%9D",
        "https://confluence.example/display/DL/Page",     # 구형 DC
        "https://x.test/pages/viewpage.action?pageId=42",  # 구형 viewpage
    ]:
        out = sanitize_html('<a href="' + url + '">문서</a>')
        assert 'class="conf-link"' in out, url
        assert 'href="' + url + '"' in out
    # 일반 링크(Jira 브라우즈 등)는 뱃지 아님
    for url in ["https://x.test/browse/DL-1", "https://x.test/secure/attachment/1/a.png"]:
        assert "conf-link" not in sanitize_html('<a href="' + url + '">보통</a>'), url


def test_malformed_html_does_not_crash():
    for src in ["<p>unclosed", "<b><i>x</b></i>", "<<>>", "<a href=", "<td colspan>x</td>"]:
        out = sanitize_html(src)
        assert isinstance(out, str)


def test_empty_and_none():
    assert sanitize_html("") == ""
    assert sanitize_html(None) == ""


# ── 3. 평문 → HTML ─────────────────────────────────────────────────
def test_text_to_html_escapes_and_nl2br():
    out = text_to_html("[체크리스트]\n- [ ] <b>주의</b>\n- 완료")
    assert "&lt;b&gt;" in out              # 태그가 실행 안 되게 escape
    assert "<b>" not in out
    assert out.count("<br>") == 2          # 줄바꿈 2개 → <br> 2개


def test_text_to_html_none():
    assert text_to_html(None) == ""


# ── 4. 이미지 프록시 재작성 (prod: 인증 이미지 same-origin 화) ──────────
def _allow(h):
    return h in ("jira.corp.com", "cdn.corp.com")


def test_proxy_relative_path_absolutized():
    out = proxy_images('<img src="/secure/attachment/1/a.png" alt="" />', "https://jira.corp.com", _allow)
    assert "/api/img?u=" in out
    assert "https%3A%2F%2Fjira.corp.com%2Fsecure%2Fattachment%2F1%2Fa.png" in out


def test_proxy_absolute_allowed_host():
    out = proxy_images('<img src="https://cdn.corp.com/x.png">', "https://jira.corp.com", _allow)
    assert "/api/img?u=https%3A%2F%2Fcdn.corp.com%2Fx.png" in out


def test_proxy_disallowed_host_untouched():
    out = proxy_images('<img src="https://evil.example/x.png">', "https://jira.corp.com", _allow)
    assert "/api/img" not in out and "evil.example" in out


def test_proxy_data_and_relative_filename_untouched():
    for src in ["data:image/png;base64,AAAA", "pic.png"]:
        out = proxy_images('<img src="' + src + '">', "https://jira.corp.com", _allow)
        assert "/api/img" not in out


def test_proxy_none_and_empty():
    assert proxy_images("", "https://jira.corp.com", _allow) == ""
    assert proxy_images("<p>no image</p>", "https://jira.corp.com", _allow) == "<p>no image</p>"


# ── 5. tidy_html — 빈 문단 유지(컴팩트 표식)·연속 축소·앞뒤 트림 ────────
def test_tidy_keeps_single_blank_as_marker():
    # 글 중간 의도적 빈 문단은 보존하되 표식(p.blank)으로 정규화
    out = tidy_html("<p>A</p><p>&nbsp;</p><p>B</p>")
    assert out == '<p>A</p><p class="blank"></p><p>B</p>'


def test_tidy_collapses_consecutive_blanks_to_one():
    out = tidy_html("<p>A</p><p>&nbsp;</p><p><br></p><p>B</p>")
    assert out == '<p>A</p><p class="blank"></p><p>B</p>'


def test_tidy_trims_leading_trailing_blanks():
    assert tidy_html("<p>&nbsp;</p><p>본문</p><p><br></p>") == "<p>본문</p>"
    assert tidy_html("<br><br> <p>본문</p>&nbsp; <br>") == "<p>본문</p>"
    assert tidy_html("  앞뒷 공백  ") == "앞뒷 공백"


def test_tidy_collapses_excess_br():
    assert tidy_html("a<br><br><br><br>b") == "a<br /><br />b"


def test_tidy_keeps_real_content_with_nbsp():
    out = tidy_html("<p>a&nbsp;b</p>")
    assert "a" in out and "b" in out and "<p>" in out and "blank" not in out


def test_tidy_none_empty():
    assert tidy_html("") == ""
    assert tidy_html(None) is None
