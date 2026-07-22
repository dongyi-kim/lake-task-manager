"""Markdown ↔ Jira wiki 변환 (app/wikimd) 단위 테스트.

에디터(Markdown) → 제출(wiki) → Jira 렌더, 수정 로드(wiki → Markdown) 경로를 보장한다.
특히 볼드/이탤릭 마커 충돌(**b** 가 이탤릭으로 새는 회귀)을 가드한다.
"""

from app.wikimd import md_to_wiki, wiki_to_md


def test_headings():
    assert md_to_wiki("# 제목") == "h1. 제목"
    assert md_to_wiki("### 셋") == "h3. 셋"
    assert wiki_to_md("h2. 둘") == "## 둘"


def test_bold_not_leaking_to_italic():
    # **굵게** 는 반드시 볼드 *굵게* 여야 한다 (이탤릭 _굵게_ 로 새면 회귀)
    assert md_to_wiki("**굵게**") == "*굵게*"
    assert md_to_wiki("본문 **굵게** 와 *기울임*") == "본문 *굵게* 와 _기울임_"
    assert md_to_wiki("__굵게__") == "*굵게*"


def test_inline_code_and_strike():
    assert md_to_wiki("`code`") == "{{code}}"
    assert md_to_wiki("~~취소~~") == "-취소-"
    # 코드 스팬 안의 * 는 서식으로 해석하지 않는다
    assert md_to_wiki("`a*b*c`") == "{{a*b*c}}"


def test_links_and_images():
    assert md_to_wiki("[링크](https://x.com)") == "[링크|https://x.com]"
    assert md_to_wiki("![](paste-1.png)") == "!paste-1.png!"
    assert md_to_wiki("![alt](img.png)") == "!img.png!"


def test_code_block():
    md = "```python\nprint('hi')\n```"
    assert md_to_wiki(md) == "{code:python}\nprint('hi')\n{code}"
    assert wiki_to_md("{code:python}\nprint('hi')\n{code}") == "```python\nprint('hi')\n```"
    # 언어 없는 블록
    assert md_to_wiki("```\nplain\n```") == "{code}\nplain\n{code}"


def test_lists():
    assert md_to_wiki("- 하나\n- 둘") == "* 하나\n* 둘"
    assert md_to_wiki("1. 첫\n2. 둘") == "# 첫\n# 둘"
    # 중첩(2칸 들여쓰기 → 깊이 2)
    assert md_to_wiki("- 상위\n  - 하위") == "* 상위\n** 하위"


def test_blockquote():
    assert md_to_wiki("> 한 줄") == "bq. 한 줄"
    assert md_to_wiki("> 첫\n> 둘") == "{quote}\n첫\n둘\n{quote}"


def test_table():
    md = "| 이름 | 값 |\n|---|---|\n| a | b |"
    assert md_to_wiki(md) == "||이름||값||\n|a|b|"


def test_roundtrip_stable_after_wiki():
    # wiki → md → wiki 가 안정적이어야 한다(제출 형태 wiki 기준으로 idempotent)
    wiki = ("h1. 제목\n\n본문 *굵게* _기울임_ {{code}} -취소-\n\n"
            "* 하나\n* 둘\n\n{code:python}\nx = 1\n{code}\n\n"
            "bq. 인용\n\n||이름||값||\n|a|b|\n\n!paste-1.png!")
    assert md_to_wiki(wiki_to_md(wiki)) == wiki
