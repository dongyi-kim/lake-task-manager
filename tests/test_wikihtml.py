"""HTML(TipTap) <-> Jira wiki 변환 (app/wikihtml) 단위 테스트.

에디터 HTML → 제출(wiki) → Jira 렌더, 수정 로드(wiki → HTML) 경로를 보장한다.
사람 멘션 [~id] <-> <span data-type=mention> 왕복을 특히 가드한다.
"""

from app.wikihtml import html_to_wiki, wiki_to_html


def test_headings_and_inline():
    assert html_to_wiki("<h2>제목</h2>") == "h2. 제목"
    assert html_to_wiki("<p><strong>굵게</strong> <em>기울</em></p>") == "*굵게* _기울_"
    assert html_to_wiki("<p><code>x=1</code></p>") == "{{x=1}}"
    assert html_to_wiki("<p><s>취소</s></p>") == "-취소-"


def test_mention_to_wiki():
    h = '<p><span data-type="mention" data-id="skcc.x1103">@이준서</span> 님</p>'
    assert html_to_wiki(h) == "[~skcc.x1103] 님"


def test_mention_from_wiki():
    html = wiki_to_html("[~skcc.x1103] 님")
    assert 'data-type="mention"' in html
    assert 'data-id="skcc.x1103"' in html


def test_mention_label_resolver():
    html = wiki_to_html("[~u01]", lambda uid: "홍길동")
    assert "@홍길동" in html
    assert 'data-id="u01"' in html


def test_links_and_images():
    assert html_to_wiki('<p><a href="https://x.com">링크</a></p>') == "[링크|https://x.com]"
    assert html_to_wiki('<p><a href="https://x.com">https://x.com</a></p>') == "[https://x.com]"
    assert html_to_wiki('<p><img src="paste-1.png"></p>') == "!paste-1.png!"


def test_code_block():
    h = '<pre><code class="language-python">print(1)</code></pre>'
    assert html_to_wiki(h) == "{code:python}\nprint(1)\n{code}"
    assert wiki_to_html("{code:python}\nprint(1)\n{code}") == \
        '<pre class="jecodeblock"><code class="language-python">print(1)</code></pre>'


def test_lists_nested():
    h = "<ul><li>하나</li><li>둘<ul><li>중첩</li></ul></li></ul>"
    assert html_to_wiki(h) == "* 하나\n* 둘\n** 중첩"


def test_table():
    h = "<table><tbody><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></tbody></table>"
    assert html_to_wiki(h) == "||A||B||\n|1|2|"


def test_table_cell_code_block_survives_as_monospace_lines():
    """표 셀 안 코드블럭 — wiki 표는 행이 한 줄이라 {code} 를 넣으면 표가 깨진다.
    줄마다 {{monospace}} + 강제개행(\\\\)으로 저장해 **표도 코드도** 살린다."""
    h = ('<table><tbody><tr><td><pre><code class="language-python">'
         'a = 1\nb = a | 2</code></pre></td><td>설명</td></tr></tbody></table>')
    w = html_to_wiki(h)
    assert w == "|{{a = 1}}\\\\{{b = a \\| 2}}|설명|"      # 셀 구분자와 안 헷갈리게 '|' 는 이스케이프
    assert w.count("\n") == 0                              # 한 줄 = 표가 안 깨진다
    # 되읽으면 코드(모노스페이스) 줄로 돌아오고, 파이프도 원문 그대로다
    back = wiki_to_html(w)
    assert "<code>a = 1</code>" in back and "<code>b = a | 2</code>" in back
    assert back.count("<td>") == 2                         # 파이프 때문에 셀이 늘지 않는다
    assert html_to_wiki(back) == w                         # 다시 저장해도 같은 형태(왕복 안정)


def test_empty_header_cell_is_not_dropped():
    """빈 헤더 셀을 ''로 쓰면 '||||' 가 돼 되읽을 때 셀이 통째로 사라진다(표 열이 줄어듦)."""
    h = "<table><tbody><tr><th>A</th><th></th></tr><tr><td>1</td><td>2</td></tr></tbody></table>"
    w = html_to_wiki(h)
    assert w == "||A|| ||\n|1|2|"
    assert wiki_to_html(w).count("<th>") == 2


def test_blockquote():
    assert html_to_wiki("<blockquote><p>인용</p></blockquote>") == "bq. 인용"


def test_roundtrip_wiki_stable():
    # wiki -> html -> wiki 가 안정적(제출형 wiki 기준 idempotent)
    wiki = ("h1. 제목\n\n본문 *굵게* _기울_ {{code}} [~u01]\n\n"
            "* 하나\n* 둘\n\n{code:js}\nlet a = 1;\n{code}\n\n"
            "bq. 인용\n\n||A||B||\n|1|2|\n\n[링크|https://x.com]")
    assert html_to_wiki(wiki_to_html(wiki)) == wiki


def test_tasklist_to_native_checkbox_html():
    """체크박스는 사내 Jira(JEDITOR) 네이티브 형식 `<p><input type=checkbox>` 로 저장하고,
    각 체크박스에 고유 id 를 붙인다(문서 내 유일 + 내용 안 바꾸면 재저장에도 동일)."""
    h = ('<ul data-type="taskList">'
         '<li data-type="taskItem" data-checked="true"><label><input type="checkbox" checked></label><div>완료</div></li>'
         '<li data-type="taskItem" data-checked="false"><label><input type="checkbox"></label><div>할 일</div></li>'
         '</ul>')
    from app.wikihtml import html_to_wiki
    a = html_to_wiki(h)
    assert a.count('type="checkbox"') == 2
    assert 'checked="checked"' in a          # 완료 항목만 체크
    assert '완료' in a and '할 일' in a
    assert a.count('<input id="') == 2        # 각 체크박스에 id
    assert html_to_wiki(h) == a               # 내용 동일 → id 도 동일(재저장 churn 없음)


def test_set_checkbox_toggle_by_index():
    """렌더 화면에서 index 번째 체크박스를 토글 → 원본의 그 체크박스만 checked 를 뒤집는다
    (id·나머지 서식은 보존)."""
    from app.jira_client import _set_checkbox
    raw = ('<p dir="auto"><input id="111" type="checkbox" />A</p>'
           '<p dir="auto"><input id="222" type="checkbox" checked="checked" />B</p>')
    on = _set_checkbox(raw, 0, True)
    assert 'id="111" type="checkbox" checked="checked"' in on and 'id="222"' in on
    off = _set_checkbox(raw, 1, False)
    assert 'id="222" type="checkbox" />' in off
    assert _set_checkbox(raw, 9, True) is None    # 없는 index → None


def test_set_checkbox_by_id_with_index_fallback():
    """id 우선(순서 무관), 못 찾으면 index 폴백 — prod 의 id 재발급에도 견고."""
    from app.jira_client import _set_checkbox
    raw = ('<p dir="auto"><input id="AAA" type="checkbox" />A</p>'
           '<p dir="auto"><input id="BBB" type="checkbox" />B</p>')
    # id 로 두 번째(BBB)를 짚는다 — index 를 안 줘도(=None) 정확
    r = _set_checkbox(raw, None, True, cbid="BBB")
    assert 'id="AAA" type="checkbox" />' in r and 'id="BBB" type="checkbox" checked' in r
    # id 를 못 찾으면 index 폴백
    r = _set_checkbox(raw, 0, True, cbid="ZZZ")
    assert 'id="AAA" type="checkbox" checked' in r
    # id 도 없고 index 도 없으면 None
    assert _set_checkbox(raw, None, True, cbid="ZZZ") is None
