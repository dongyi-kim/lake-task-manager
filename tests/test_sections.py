# -*- coding: utf-8 -*-
"""'=== 제목 ===' 구분선 기반 description 영역 분할."""
from app.sections import split_sections as S


def _titles(html):
    return [s["title"] for s in S(html)]


def test_single_paragraph_with_br():
    """Jira wiki 는 홑 줄바꿈을 <br/> 로 낸다 → 한 문단 안에서 잘려야 한다(가장 흔한 형태)."""
    r = S("<p>안녕하세요<br/>==== 신청정보 ====<br/>이렇게 신청함"
          "<br/>==== 부가정보 ======<br/>부가적임</p>")
    assert [s["title"] for s in r] == [None, "신청정보", "부가정보"]
    assert r[0]["html"] == "<p>안녕하세요</p>"          # 경계 <br> 가 남으면 빈 줄로 보인다
    assert r[1]["html"] == "<p>이렇게 신청함</p>"
    assert r[2]["html"] == "<p>부가적임</p>"


def test_block_level_divider():
    assert _titles("<p>앞</p><p>=== 신청정보 ===</p><p>뒤</p>") == [None, "신청정보"]


def test_no_divider_returns_original():
    h = "<p>본문</p><h2>헤딩</h2>"
    assert S(h) == [{"title": None, "html": h}]


def test_two_equals_is_not_a_divider():
    assert _titles("<p>앞<br/>== 아님 ==<br/>뒤</p>") == [None]


def test_nested_dividers_are_ignored():
    """표·코드블럭·인용·콜아웃·리스트 안의 구분선은 영역을 나누면 안 된다.

    자르면 태그가 깨지고, 속성 있는 태그(div.panel 등)는 복원할 수도 없다.
    """
    for h in ('<pre class="code"><code>=== x ===</code></pre><p>뒤</p>',
              "<blockquote><p>=== x ===</p></blockquote><p>뒤</p>",
              '<div class="callout callout-info"><p>=== x ===</p></div><p>뒤</p>',
              "<ul><li>=== x ===</li></ul><p>뒤</p>",
              '<div class="panel"><div class="panel-body"><p>=== x ===</p></div></div><p>뒤</p>',
              "<table><tr><td>=== x ===</td></tr></table><p>뒤</p>"):
        assert S(h) == [{"title": None, "html": h}], h


def test_toplevel_split_keeps_nested_intact():
    """최상위에서만 자르고, 잘린 조각 안의 표는 그대로 살아 있어야 한다."""
    r = S("<p>머리<br/>=== 실제 ===<br/>본문</p><table><tr><td>=== 표안 ===</td></tr></table>")
    assert [s["title"] for s in r] == [None, "실제"]
    assert "<table>" in r[1]["html"] and "=== 표안 ===" in r[1]["html"]


def test_titleless_divider_still_splits():
    assert _titles("<p>앞<br/>=========<br/>뒤</p>") == [None, None]


def test_empty_leading_section_dropped():
    """구분선이 맨 앞이면 빈 앞영역은 버린다."""
    r = S("<p>=== 첫영역 ===<br/>내용</p>")
    assert [s["title"] for s in r] == ["첫영역"]
    assert r[0]["html"] == "<p>내용</p>"


def test_nbsp_around_divider():
    """WYSIWYG 에디터는 공백을 &nbsp; 로 낸다.

    엔티티를 풀지 않으면 (1) 구분선으로 아예 안 잡히거나 (2) 제목에 &nbsp; 가 남는다.
    실제로 prod 에서 안 나뉜 원인.
    """
    r = S("<p>앞</p><p>====&nbsp;신청정보&nbsp;====</p><p>뒤</p>")
    assert [s["title"] for s in r] == [None, "신청정보"]
    r2 = S("<p>앞</p><p>&nbsp;=== 신청정보 ===&nbsp;</p><p>뒤</p>")
    assert [s["title"] for s in r2] == [None, "신청정보"]


def test_div_containers():
    """WYSIWYG 은 문단을 <p> 대신 <div> 로 내기도 한다."""
    r = S("<div>앞</div><div>=== 신청정보 ===</div><div>뒤</div>")
    assert [s["title"] for s in r] == [None, "신청정보"]
    assert r[0]["html"] == "<div>앞</div>"
    assert r[1]["html"] == "<div>뒤</div>"


def test_nested_plain_containers_reopened():
    """<div><p> 중첩도 자른 뒤 원래 태그로 다시 열어야 한다."""
    r = S("<div><p>앞<br/>=== 신청정보 ===<br/>뒤</p></div>")
    assert [s["title"] for s in r] == [None, "신청정보"]
    assert r[0]["html"] == "<div><p>앞</p></div>"
    assert r[1]["html"] == "<div><p>뒤</p></div>"


def test_div_with_class_is_not_splittable():
    """class 붙은 div(패널·콜아웃) 안에서 자르면 의미가 깨진다 — 열어준 건 '속성 없는' div 뿐."""
    h = '<div class="callout callout-info"><p>=== x ===</p></div><p>뒤</p>'
    assert S(h) == [{"title": None, "html": h}]
    h2 = '<div class="panel"><div class="panel-body"><p>=== x ===</p></div></div>'
    assert S(h2) == [{"title": None, "html": h2}]
