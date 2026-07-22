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
        '<pre><code class="language-python">print(1)</code></pre>'


def test_lists_nested():
    h = "<ul><li>하나</li><li>둘<ul><li>중첩</li></ul></li></ul>"
    assert html_to_wiki(h) == "* 하나\n* 둘\n** 중첩"


def test_table():
    h = "<table><tbody><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></tbody></table>"
    assert html_to_wiki(h) == "||A||B||\n|1|2|"


def test_blockquote():
    assert html_to_wiki("<blockquote><p>인용</p></blockquote>") == "bq. 인용"


def test_roundtrip_wiki_stable():
    # wiki -> html -> wiki 가 안정적(제출형 wiki 기준 idempotent)
    wiki = ("h1. 제목\n\n본문 *굵게* _기울_ {{code}} [~u01]\n\n"
            "* 하나\n* 둘\n\n{code:js}\nlet a = 1;\n{code}\n\n"
            "bq. 인용\n\n||A||B||\n|1|2|\n\n[링크|https://x.com]")
    assert html_to_wiki(wiki_to_html(wiki)) == wiki
