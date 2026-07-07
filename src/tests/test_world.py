"""world 결정성·인덱스·fidelity(실 Jira statusCategory/type)·dedup 케이스."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
from app.settings import load_plan     # noqa: E402
from app.world import SUBTASK_TYPE, get_world   # noqa: E402


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
