# -*- coding: utf-8 -*-
"""'=== 제목 ===' 구분선 기반 description 영역 분할."""
from app.sections import split_sections as S


def _one(html):
    """구분선 없이 통짜 1개인지 — (title, html) 로만 비교(kv 등 부가 키는 무시)."""
    r = S(html)
    return len(r) == 1 and r[0]["title"] is None and r[0]["html"] == html


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
    assert _one("<p>본문</p><h2>헤딩</h2>")


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
        assert _one(h), h


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
    assert _one('<div class="callout callout-info"><p>=== x ===</p></div><p>뒤</p>')
    assert _one('<div class="panel"><div class="panel-body"><p>=== x ===</p></div></div>')


# ── key : value 영역 → 표 (VoC 시스템 주입 블록) ──────────────────────

def _voc(body):
    return ("<p>==================== 신청정보 ====================</p>"
            "<p>" + body + "</p>")


def test_kv_section_becomes_table():
    r = S(_voc("신청자 : 홍길동<br/>요청 부서 : 데이터플랫폼<br/>희망일 : 2026-08-01"))
    kv = r[-1]["kv"]
    assert [x["k"] for x in kv] == ["신청자", "요청 부서", "희망일"]
    assert [x["html"] for x in kv] == ["홍길동", "데이터플랫폼", "2026-08-01"]


def test_kv_value_may_contain_colon():
    """URL·시각처럼 값에 콜론이 들어가도 첫 콜론에서만 자른다."""
    kv = S(_voc("URL : http://x.example:8080/a<br/>적재 : 일 1회 (02:00)"))[-1]["kv"]
    assert kv[0]["html"] == "http://x.example:8080/a"
    assert kv[1]["html"] == "일 1회 (02:00)"


def test_kv_needs_every_line_to_match():
    """한 줄이라도 key:value 가 아니면 표로 보지 않는다(일반 문장 보호)."""
    assert S(_voc("신청자 : 홍길동<br/>그냥 문장이다"))[-1]["kv"] is None
    assert S(_voc("문장인데 콜론이 있다: 이렇게."))[-1]["kv"] is None


def test_kv_ignores_rich_blocks():
    """표·리스트 등이 섞이면 손대지 않는다."""
    h = ("<p>==================== 신청정보 ====================</p>"
         "<p>a : 1</p><table><tr><td>x</td></tr></table>")
    assert S(h)[-1]["kv"] is None


def test_kv_single_row_not_a_table():
    assert S(_voc("신청자 : 홍길동"))[-1]["kv"] is None


def test_kv_with_nbsp():
    """WYSIWYG 의 &nbsp; 가 키/값에 남으면 안 된다."""
    kv = S(_voc("신청자&nbsp;:&nbsp;홍길동<br/>부서&nbsp;:&nbsp;데이터"))[-1]["kv"]
    assert [x["k"] for x in kv] == ["신청자", "부서"]


def test_kv_present_on_undivided_description():
    """구분선이 없는 설명도 kv 판정을 탄다(키가 항상 존재)."""
    assert "kv" in S("<p>본문</p>")[0]


def test_kv_fullwidth_colon():
    """한글 IME 로 전각 콜론(：)이 섞여 들어오는 경우."""
    kv = S(_voc("시스템명 ： LAKE<br/>환경 : 운영"))[-1]["kv"]
    assert [(x["k"], x["html"]) for x in kv] == [("시스템명", "LAKE"), ("환경", "운영")]


def test_kv_blank_lines_inside_block():
    """블록 '안쪽'에 빈 줄이 섞여도 표가 되어야 한다(빈 줄 인코딩 여러 형태)."""
    for body in ("<p>a : 1</p><p></p><p>b : 2</p>",
                 "<p>a : 1</p><p>&nbsp;</p><p>b : 2</p>",
                 "<p>a : 1<br/><br/>b : 2</p>",
                 "<p>a : 1<br/>&nbsp;<br/>b : 2</p>",
                 "<div>a : 1</div><div><br></div><div>b : 2</div>",
                 "<p>a : 1</p><p><br/></p><p>b : 2</p>"):
        h = "<p>==================== 신청정보 ====================</p>" + body
        kv = S(h)[-1]["kv"]
        assert kv and [x["k"] for x in kv] == ["a", "b"], body


def test_kv_survives_invisible_chars():
    """WYSIWYG 가 흘려 넣는 보이지 않는 문자(ZWSP/ZWNJ/BOM).

    str.strip() 은 이들을 공백으로 보지 않는다 → '빈 줄이 아닌 빈 줄'이 생기고,
    그 한 줄 때문에 **영역 전체가 표에서 탈락**한다. 잔여 문자는 문서 맨 끝에
    붙기 마련이라 '마지막 영역만 표가 안 되는' 형태로 나타난다(실제 제보 증상).
    """
    D = "<p>==================== {3} 테이블정보 ====================</p>"
    body = "<p>스키마 : A<br/>테이블명 : B</p>"
    for tail in ("", "<p>&#8203;</p>", "<p>&#65279;</p>", "<p>&#8204;</p>",
                 "<p>&#8203;&nbsp;</p>"):
        kv = S(D + body + tail)[-1]["kv"]
        assert kv and len(kv) == 2, tail


def test_divider_with_invisible_char():
    """구분선 안에 ZWSP 가 껴도 구분선으로 인식해야 한다."""
    r = S("<p>====&#8203; 신청정보 ====</p><p>a : 1<br/>b : 2</p>")
    assert [s["title"] for s in r] == ["신청정보"]


def test_kv_when_whole_body_is_one_paragraph():
    """시스템 주입 내용이 통째로 하나의 <p> 로 묶여 마지막 영역만 진짜 </p> 를 무는 형태."""
    big = ("<p>==================== {2} 테이블정보 ====================<br/>"
           "스키마 : DW_MART<br/>테이블명 : FCT_A<br/>"
           "==================== {3} 테이블정보 ====================<br/>"
           "스키마 : DW_MART<br/>테이블명 : FCT_B</p>")
    r = S(big)
    assert [s["title"] for s in r] == ["{2} 테이블정보", "{3} 테이블정보"]
    assert all(s["kv"] and len(s["kv"]) == 2 for s in r)
