# -*- coding: utf-8 -*-
"""검색 재현율 — **길게 물을수록 못 찾던 것**(실사용 사고 2026-08-10).

사용자 제보: prod 에 `Lake Data의 Iceberg Puffin 통계적용 PoC` 가 **있는데**
"iceberg 통계데이터 생성 작업에 대한 관련 작업내역들 총정리" 로 물으니
"관련 이력 전무" 라고 답했다.

원인은 완화 사다리의 **분모**였다. 토큰별 검색 후 "절반 이상 맞아야 한다"를 요구했는데,
그 절반을 **질의의 모든 토큰**으로 쟀다. 'iceberg' 는 맞았지만 '통계데이터'·'생성' 은
우리 말뭉치에 아예 없는 말이라 한 건도 안 맞았고, 그래서 need=2 에 걸려 버려졌다.

> ★ **아무 티켓에도 없는 낱말은 사용자가 우리와 다르게 부른 것**이지, 관련성을 재는 잣대가
>   아니다. 안 맞은 토큰까지 분모에 넣으면 **자세히 물을수록 결과가 사라진다** — 사용자가
>   친절하게 길게 쓸수록 나빠지는, 가장 나쁜 종류의 실패다.
"""
import os
import sys

import pytest

os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langchain_core", reason="requirements 미설치")

_TARGET = {"key": "DL-7001", "title": "Lake Data의 Iceberg Puffin 통계적용 PoC",
           "status": "In Progress", "assignee": "skcc.x1042",
           "issuetype": "Task", "updated": "2026-08-01"}
_NOISE = {"key": "DL-7002", "title": "[ETL] 경계값 오류 수정", "status": "Closed",
          "assignee": "skcc.x1103", "issuetype": "Bug", "updated": "2026-07-01"}


@pytest.fixture
def stub(monkeypatch):
    """검색을 **부분문자열 AND** 로 흉내 낸다 — 실제 백엔드와 같은 성질."""
    def fake(c, s, q, scope="all", limit=8):
        hits = [t for t in (_TARGET, _NOISE)
                if all(w.lower() in t["title"].lower() for w in q.split())]
        return {"jira": {"items": hits}, "confluence": {"items": []}}

    import app.domain.search
    monkeypatch.setattr(app.domain.search, "search_all", fake)
    from app.agent.tools.search_tools import search_work_history
    return search_work_history


def _keys(r):
    return [i["key"] for i in (r.get("jira") or [])]


def test_a_long_natural_question_still_finds_the_ticket(stub):
    """사용자 원문 그대로 — 우리와 표기가 달라도 찾아야 한다."""
    r = stub.invoke({"query": "iceberg 통계데이터 생성 작업에 대한 관련 작업내역들 총정리"})
    assert "DL-7001" in _keys(r), _keys(r)


def test_unrelated_tickets_still_do_not_leak(stub):
    """재현율을 올리면서 **정밀도를 잃지 않는다.**

    예전 실측 사고의 반대편: "Iceberg Puffin NDV" 질문에 '경계값 오류 수정'이 관련 이력으로
    나왔다. 영문 기술 토큰이 하나라도 맞아야 한다는 가드는 그대로다.
    """
    r = stub.invoke({"query": "Iceberg Puffin NDV 통계 적용"})
    assert _keys(r) == ["DL-7001"], _keys(r)


def test_a_plain_query_that_matches_directly_is_untouched(stub):
    """원 질의가 바로 잡히면 사다리는 타지 않는다(결과가 달라지면 안 된다)."""
    assert _keys(stub.invoke({"query": "오류 수정"})) == ["DL-7002"]


def test_the_topic_path_gets_the_same_recovery(monkeypatch):
    """주제 조사 경로(`find_mentions`)도 **같은 사다리**를 탄다.

    실사용 사고가 정확히 이 경로에서 났다. 완화 사다리가 `search_work_history` 안에만
    있어서, 같은 질문이 어느 도구를 타느냐에 따라 찾히고 안 찾히고가 갈렸다.
    **가드도 회복 경로도 '만드는 자리와 읽는 자리 양쪽'에 있어야 한다.**
    """
    def fake(c, s, q, scope="all", limit=8):
        hits = [t for t in (_TARGET, _NOISE)
                if all(w.lower() in t["title"].lower() for w in q.split())]
        return {"jira": {"items": hits}, "confluence": {"items": []}}

    import app.domain.search
    monkeypatch.setattr(app.domain.search, "search_all", fake)

    class _C:
        def get_issue(self, key):
            return {"fields": {"summary": _TARGET["title"], "description": ""}}

        def issue_comments(self, key, n=20):
            return []

    monkeypatch.setattr("app.agent.tools.search_tools.client", lambda: _C())
    from app.agent.tools.search_tools import find_mentions
    r = find_mentions.invoke({"term": "iceberg 통계데이터 생성"})
    assert any(h.get("key") == "DL-7001" for h in (r.get("hits") or [])), r
