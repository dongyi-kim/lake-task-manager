"""world 결정성·인덱스·fidelity(실 Jira statusCategory/type)·dedup 케이스."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
from app.infra.settings import load_plan     # noqa: E402
from app.mock.world import SUBTASK_TYPE, get_world   # noqa: E402


def test_world_deterministic_and_indexed():
    w1 = get_world()
    w2 = get_world()
    assert w1 is w2                         # 캐시된 싱글턴
    assert len(w1.issues) > 100
    assert w1.by_label.get("PMO_VIT")
    ek = load_plan()["wbs"][0]["epics"][0]["key"]
    assert w1.epic_children.get(ek)         # 논리 epic key 로 자식 조회


def test_status_and_type_fidelity():
    w = get_world()
    types = {it["type"] for it in w.issues.values()}
    assert SUBTASK_TYPE in types            # "Sub-Task"
    assert {"Epic", "Story", "Task", "Bug"} <= types
    # jira 직렬화 statusCategory 는 실 Jira 키
    st = w.jira_fields(next(iter(w.issues.values())))["status"]["statusCategory"]["key"]
    assert st in {"new", "indeterminate", "done"}
    # 상태명도 사내 워크플로
    names = {it["statusName"] for it in w.issues.values()}
    assert names <= {"Open", "In Progress", "Resolved", "Closed", "Reopened"}


def test_pmo_vit_dedup_case_exists():
    w = get_world()
    vit = set(w.by_label.get("PMO_VIT", []))
    # 최소 1건: 상위(조상)가 이미 PMO_VIT 인 자손 현안 (dedup 대상)
    found = any((w.issues[k]["epicKey"] in vit) or (w.issues[k]["parentKey"] in vit) for k in vit)
    assert found


def test_security_training_fixture_has_all_fourteen_direct_subtasks():
    """미완료자 질의 battery가 한 검색 결과가 아닌 14명 전수를 검증할 수 있어야 한다."""
    world = get_world()
    parent = world.issues["DL-9100"]
    children = [world.issues[key] for key in parent["subtasks"]]
    assert len(children) == 14
    assert all(row["parentKey"] == "DL-9100" and row["type"] == SUBTASK_TYPE
               for row in children)
    assert sum(row["statusCategory"] == "done" for row in children) == 10
    assert sum(row["statusCategory"] != "done" for row in children) == 4
    assert len({row["assignee"] for row in children}) == 14


def test_meeting_battery_fixtures_are_deterministic_and_keep_known_gaps():
    world = get_world()
    epic = world.issues["DL-9200"]
    children = [world.issues[key] for key in ("DL-9201", "DL-9202", "DL-9203")]
    assert epic["type"] == "Epic"
    assert all(row["epicKey"] == "DL-9200" and row["type"] == "Task" for row in children)
    assert [row["assignee"] for row in children] == [
        "skcc.i2011", "skcc.x1402", "skcc.x1042",
    ]
    assert children[0]["statusCategory"] == "done"
    assert children[1]["statusCategory"] == "inprogress"
    assert "PSR" in children[2]["description"] and "RGP" in children[2]["description"]
    titles = {
        page["title"] for pages in world.confluence.values() for page in pages
    }
    assert "[회의록] Iceberg Puffin NDV 도입 실무회의" in titles
    assert "[설계] Puffin NDV 내부 검토 메모" in titles
