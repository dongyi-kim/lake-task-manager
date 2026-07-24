"""우선순위는 사내 등급(P0~P4 + Unclassified)만 쓴다.

Jira 기본 스킴(Highest/High/Medium/…)이 섞이면 '내 Task' 정렬 축이 두 체계로 갈리고,
화면에 뜨는 아이콘도 등급마다 달라진다. dev 데이터가 먼저 지켜야 실 데이터에서 눈에 띈다.
"""
from app.fakebridge import _PRIORITIES
from app.world import get_world

SCHEME = {"P0-Blocker", "P1-Critical", "P2-Major", "P3-Minor", "P4-Trivial", "Unclassified"}


def test_priority_scheme_is_the_company_one():
    assert {n for n, _id in _PRIORITIES} == SCHEME


def test_world_uses_only_scheme_priorities():
    bad = sorted({it.get("priority") for it in get_world().issues.values()
                  if it.get("priority") and it["priority"] not in SCHEME})
    assert not bad, f"사내 등급 밖의 우선순위: {bad}"


def test_new_issue_defaults_to_unclassified():
    """등급을 안 주고 만든 티켓은 '미분류'다 — 아무 등급이나 자동으로 붙이면
    아무도 판단한 적 없는 P2 가 쌓인다."""
    from app.fakebridge import build_store
    st = build_store()
    assert st.config.default_priority == "Unclassified"
    assert st.serializer.priority_obj(None)["name"] == "Unclassified"


def test_every_epic_has_a_short_name():
    """Epic 은 **단축어(Epic Name)** 를 반드시 갖는다.

    비면 화면이 요약을 이름 자리에 앉혀, 단축어와 요약을 나란히 보여 주는 목록이 같은 글자를
    두 번 그린다 — 구별에 아무 도움이 안 된다. 실 Jira 도 Epic 생성 시 필수 입력이다.
    """
    bad = sorted(k for k, it in get_world().issues.items()
                 if it["type"] == "Epic" and not (it.get("epicName") or "").strip())
    assert not bad, f"단축어 없는 Epic: {bad}"


def test_short_name_differs_from_summary():
    """단축어가 요약과 같으면 둘을 나란히 보여 줄 이유가 없다 — dev 데이터부터 달라야
    화면이 제 역할을 하는지 눈으로 확인된다."""
    same = sorted(k for k, it in get_world().issues.items()
                  if it["type"] == "Epic" and (it.get("epicName") or "") == it["summary"])
    assert not same, f"단축어가 요약과 같은 Epic: {same}"
