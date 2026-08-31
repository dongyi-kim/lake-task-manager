"""Workload filter wiring contracts shared by the toolbar, API memo, and backend route."""
from frontend.static_assets.support import STATIC


def test_assigned_updated_window_is_persisted_and_sent_to_summary_and_detail():
    view = (STATIC / "components" / "views" / "WorkloadView.js").read_text(encoding="utf-8")
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")

    assert 'const ASSIGNED_WINDOWS = [' in view
    for key, label in (("1w", "1주"), ("1m", "1달"), ("all", "전체")):
        assert f'{{ k: "{key}", label: "{label}"' in view
    assert 'assignedWindow: ["1w", "1m", "all"].includes(pref.assignedWindow)' in view
    assert '<span class="wl-opt-l">할당 갱신</span>' in view
    assert '@click="setAssignedWindow(w.k)"' in view
    assert "setAssignedWindow(window)" in view
    assert "api.workloadPerson(pid, doneDays, assignedWindow)" in view
    assert "api.workloadBucket(id, bucket, doneDays, assignedWindow)" in view
    assert "assignedWindow: this.assignedWindow" in view
    assert api.count('"&assignedWindow="') >= 2


def test_failed_people_retry_individually_without_refreshing_successful_rows():
    view = (STATIC / "components" / "views" / "WorkloadView.js").read_text(encoding="utf-8")

    assert "const WORKLOAD_PERSON_RETRY_DELAYS = [800, 2400, 5000];" in view
    assert "queue.push({ pid, attempt: nextAttempt" in view
    assert "active < CONC" in view
    assert "this.pstat[pid] = r;" in view
    assert "retryFailedPeople()" in view
    assert "retryPerson(pid)" in view
    assert '@click.stop="retryPerson(p.id)"' in view
    assert "일시 오류 · 자동 재시도" in view
    assert "새로고침으로 재시도" not in view
