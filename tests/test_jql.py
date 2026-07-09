"""fake_jira.jql — 우리 4종 JQL 필터."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
from app.settings import load_people, load_plan   # noqa: E402
from app.world import get_world                    # noqa: E402
from tools.fake_jira import jql                     # noqa: E402


def test_epic_link():
    w = get_world()
    ek = load_plan()["wbs"][0]["epics"][0]["key"]
    keys = jql.filter_keys(w, f'"Epic Link" = {ek}')
    assert keys and all(w.issues[k]["epicKey"] == ek for k in keys)


def test_pmo_vit_ordered():
    w = get_world()
    keys = jql.filter_keys(w, f'project={w.project} AND labels="PMO_VIT" ORDER BY updated DESC')
    assert keys and all("PMO_VIT" in w.issues[k]["labels"] for k in keys)
    ups = [w.issues[k]["updated"] for k in keys]
    assert ups == sorted(ups, reverse=True)


def test_assignee_inprogress():
    w = get_world()
    pid = next(iter(load_people().values()))[0]
    keys = jql.filter_keys(w, f'assignee = "{pid}" AND statusCategory = "In Progress"')
    assert all(w.issues[k]["assignee"] == pid and w.issues[k]["statusCategory"] == "inprogress"
               for k in keys)


def test_done_resolved_7d():
    w = get_world()
    pid = next(iter(load_people().values()))[0]
    keys = jql.filter_keys(w, f'assignee = "{pid}" AND statusCategory = Done AND resolved >= -7d')
    for k in keys:
        it = w.issues[k]
        assert it["assignee"] == pid and it["statusCategory"] == "done" and it["resolved"]
        assert (w.today - it["resolved"]).days <= 7
