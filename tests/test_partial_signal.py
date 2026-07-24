"""못 가져온 것과 원래 없는 것을 **구분해서** 알린다.

트리 조립은 티켓을 하나씩 받아 붙이는데, 그중 몇이 실패해도 조립은 계속된다(하나 못 받았다고
화면 전체를 죽일 이유는 없다). 문제는 그 사실이 사라지는 것이다 — 화면에는 '하위 티켓 없음'
으로 뜨고 보는 사람은 진짜 없는 줄 안다. 그래서 실패 건수를 세어 응답에 싣는다.
"""
import pytest

from app.jira_client import JiraClient


@pytest.fixture()
def client(monkeypatch):
    from app.main import _client
    return _client


def test_miss_counter_starts_clean(client):
    client.miss_begin()
    assert client.miss_count() == 0


def test_failed_child_is_counted_not_swallowed(client, monkeypatch):
    """자식 하나가 실패하면 그 수가 남는다 — 조립 결과만 보면 '원래 없었다' 와 구분되지 않는다."""
    client.miss_begin()
    real = client.get_issue

    def flaky(key):
        if key.endswith("13"):          # 특정 하위만 실패
            raise RuntimeError("upstream down")
        return real(key)

    monkeypatch.setattr(client, "get_issue", flaky)
    tree = client._vit_tree("DL-9012", "Task")
    assert client.miss_count() >= 1, "실패한 하위가 세어지지 않았다"
    # 나머지는 그대로 온다 — 하나 실패했다고 전부 버리지 않는다
    assert isinstance(tree, list)


def test_issues_by_keys_counts_missing(client, monkeypatch):
    client.miss_begin()
    monkeypatch.setattr(client, "get_issue", lambda k: (_ for _ in ()).throw(RuntimeError("x")))
    out = client.issues_by_keys(["DL-9001", "DL-9002"])
    assert out == []
    assert client.miss_count() == 2
