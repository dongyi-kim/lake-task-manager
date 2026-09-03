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
    recovery = (STATIC / "components" / "workload" / "recovery.js").read_text(encoding="utf-8")

    assert "const WORKLOAD_PERSON_RETRY_DELAYS = [800, 2400, 5000];" in recovery
    assert "queue.push({ pid, attempt: nextAttempt" in view
    assert "active < CONC" in view
    assert "this.pstat[pid] = r;" in view
    assert "retryFailedPeople(kind = null)" in view
    assert "retryPerson(pid)" in view
    assert '@click.stop="retryPerson(p.id)"' in view
    assert "불러오는 중… (재시도: " in view
    assert "일시 오류 · 자동 재시도" not in view
    assert "loading: true, retrying: true" in view
    assert "새로고침으로 재시도" not in view


def test_http_200_error_bundle_uses_the_same_bounded_person_retry_path():
    view = (STATIC / "components" / "views" / "WorkloadView.js").read_text(encoding="utf-8")
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")

    error_guard = view.index("if (r && r.error)")
    success_store = view.index("this.pstat[pid] = r;", error_guard)
    retry_branch = view.index("task.attempt < WORKLOAD_PERSON_RETRY_DELAYS.length", success_store)
    assert error_guard < success_store < retry_branch
    assert "throw incomplete;" in view[error_guard:success_store]
    assert 'errorKind: kind' in view
    person_api = api[api.index("workloadPerson: (u, days, assignedWindow)"):
                     api.index("workloadBucket:", api.index("workloadPerson: (u, days, assignedWindow)"))]
    assert "if (result && (result.error || (result.partial && result.retryable))) _memo.delete(path);" in person_api


def test_partial_parent_resolution_keeps_counts_and_rows_visible_during_targeted_retry():
    view = (STATIC / "components" / "views" / "WorkloadView.js").read_text(encoding="utf-8")
    recovery = (STATIC / "components" / "workload" / "recovery.js").read_text(encoding="utf-8")
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")

    assert "if (r && r.partial && r.retryable)" in view
    assert "this.pstat[pid] = Object.assign({}, r" in view
    assert "queue.push({ pid, attempt: nextAttempt" in view
    assert "onPartial: (rows, attempt, error)" in view
    assert 'bucketState("partial"' in view
    assert "status === 'success' || bucketStateOf(p.id, c.k).status === 'partial'" in view
    assert "incomplete.partialRows = rows;" in recovery
    assert "if (onPartial) onPartial(rows, attempt + 1, incomplete);" in recovery
    assert "result.partial && result.retryable" in api
    assert "rows.some((row) => row && row.epicResolution" in api
    assert "&& row.epicResolution.retryable)) _memo.delete(path);" in api
    assert "t.epicResolution && t.epicResolution.complete === false" in view
    assert "t.epicResolution.retryable ? 'Epic 미확인' : '조회 제외'" in view


def test_partial_activity_response_is_not_stuck_in_browser_memo():
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")
    start = api.index("activity: (user) => {")
    activity = api[start:api.index("myTasks:", start)]
    assert "if (result && result.partial) _memo.delete(path);" in activity


def test_detail_buckets_have_independent_state_and_retry_only_the_failed_bucket():
    view = (STATIC / "components" / "views" / "WorkloadView.js").read_text(encoding="utf-8")
    recovery = (STATIC / "components" / "workload" / "recovery.js").read_text(encoding="utf-8")

    assert "const WORKLOAD_BUCKET_RETRY_DELAYS = [800, 2400, 5000];" in recovery
    assert "open: bucketState(), inProgress: bucketState(), done7d: bucketState()" in view
    assert "retryBucket(id, bucket)" in view
    assert 'this._loadDetailBucket(id, bucket, { resetRows: false, priority: 40 });' in view
    assert '@click.stop="retryBucket(p.id, c.k)"' in view
    assert 'bucketStateOf(p.id, c.k).status === \'success\'' in view
    # A failed request must not become a successful empty list.
    assert "box[bucket] = [];" not in view
    empty_marker = '<div v-if="!(tkd[p.id][c.k] || []).length" class="muted mini">없음</div>'
    assert empty_marker in view
    assert view.rfind(
        'v-else-if="bucketStateOf(p.id, c.k).status === \'success\' || '
        'bucketStateOf(p.id, c.k).status === \'partial\'"',
        0, view.index(empty_marker)) >= 0

    done_change = view[view.index("setDoneDays(d)"):view.index("setAssignedWindow(window)")]
    assert 'this._loadDetailBucket(id, "done7d"' in done_change
    assert '["open", "inProgress"]' not in done_change
    assigned_change = view[view.index("setAssignedWindow(window)"):view.index("setSort(k)")]
    assert '["open", "inProgress"].forEach' in assigned_change
    assert 'this._loadDetailBucket(id, "done7d"' not in assigned_change


def test_workload_reads_share_a_capped_scheduler_and_stale_filters_are_ignored():
    view = (STATIC / "components" / "views" / "WorkloadView.js").read_text(encoding="utf-8")
    recovery = (STATIC / "components" / "workload" / "recovery.js").read_text(encoding="utf-8")

    assert "const WORKLOAD_REQUEST_CONCURRENCY = 3;" in recovery
    assert "this.active < this.limit" in recovery
    assert '"person", pid, doneDays, assignedWindow' in view
    assert '"bucket:" + requestKey' in recovery
    assert "this.inFlight.get(key)" in recovery
    assert "existing.priority = Math.max(existing.priority, priority)" in recovery
    assert "b.freshness - a.freshness" in recovery
    assert "20, epoch)" in view
    assert "epoch !== this.peopleLoadEpoch" in view
    assert "epoch !== this.dueRiskEpoch || this.dueRiskFor !== requestKey" in view
    assert "this.bucketStateOf(id, bucket).requestKey === requestKey" in view
    assert "if (isCurrent && !isCurrent())" in recovery


def test_auth_recovery_targets_failed_people_buckets_and_due_risk_parts():
    view = (STATIC / "components" / "views" / "WorkloadView.js").read_text(encoding="utf-8")
    recovery = (STATIC / "components" / "workload" / "recovery.js").read_text(encoding="utf-8")

    auth_handler = view[view.index('window.addEventListener("auth-ok"'):
                        view.index("});", view.index('window.addEventListener("auth-ok"')) + 3]
    assert 'this.retryFailedPeople("auth")' in auth_handler
    assert 'this.retryFailedBuckets("auth")' in auth_handler
    assert 'this.retryDueRisk("auth")' in auth_handler
    assert 'if (kind === "permission") return;' in view
    assert 'if (status === 403) return "permission";' in recovery
    assert 'if (status === 401 || (error && error.needLogin)) return "auth";' in recovery
    assert 'this.pstat[p.id].errorKind === "permission"' in view
    assert 'status: "permission", kind: "permission", rows: []' in view
    assert 'if (kind === "auth") window.dispatchEvent(new CustomEvent("need-login"));' in view


def test_incomplete_detail_and_due_risk_never_claim_normal_empty_results():
    view = (STATIC / "components" / "views" / "WorkloadView.js").read_text(encoding="utf-8")

    assert "detailComplete(p.id)" in view
    assert '!epicDist(p.id).groups.length && detailComplete(p.id)' in view
    assert "dueRisk.complete ? dueRisk.over.length" in view
    assert "!dueRisk.over.length && !dueRisk.soon.length && dueRisk.complete" in view
    assert "const publish = () =>" in view
    assert "parts[partKey] = Object.assign({ id: p.id, bucket }, result);\n          publish();" in view
    assert "확인된 위험 없음 · 일부 항목 미확인" in view
    assert 'v-if="dueRisk.failures && !dueRiskBusy"' in view
    assert "if (!previous || previous.status !== \"error\"" in view
    assert "curMod && curMod.peopleCount && !statsComplete ? '+' : ''" in view
    assert "statsComplete" in view


def test_first_auth_failure_pauses_remaining_people_until_auth_ok():
    view = (STATIC / "components" / "views" / "WorkloadView.js").read_text(encoding="utf-8")
    assert "authPaused = false" in view
    assert "if (epoch !== this.peopleLoadEpoch || authPaused)" in view
    assert "authPaused = true" in view
    assert "for (const pending of queue.splice(0))" in view
    assert 'message: "인증 복구 대기 중"' in view
    assert "if (!authPaused) pump()" in view
