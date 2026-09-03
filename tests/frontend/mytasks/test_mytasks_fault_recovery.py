"""MyTasks progressive-stream failure semantics."""

import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


VIEW = REPO_ROOT / "app" / "static" / "components" / "views" / "MyTasksView.js"
MODEL = REPO_ROOT / "app" / "static" / "components" / "mytasks" / "taskModel.js"


def test_failed_leaf_axis_is_not_marked_done_or_rendered_as_empty():
    src = VIEW.read_text(encoding="utf-8")

    assert "const failedAxes = new Map();" in src
    assert "failedAxes.set(completed.axis, kind);" in src
    assert "const failure = failedAxes.get(state.k);" in src
    assert 'state: failure !== "auth" && scheduled ? "retrying" : "degraded"' in src
    assert 'if (this.axisDegraded(key)) return "일부 티켓을 불러오지 못했습니다";' in src
    assert 'return "해당 상태의 티켓 없음";' in src

    final_block = src[src.index("if (active && event.done)"):src.index("}, controller.signal)")]
    assert "failedAxes.get(state.k)" in final_block
    assert "failure ? {" in final_block
    assert '} : { state: "done"' in final_block
    assert 'todo: { state: "done"' not in final_block


def test_auth_and_transport_failures_keep_axis_recoverable_but_permission_is_silent():
    src = VIEW.read_text(encoding="utf-8")

    assert 'if (kind === "permission" || kind === "unavailable")' in src
    assert 'state: "done", chunks: axes[completed.axis].chunks, unavailable: true' in src
    assert 'state: kind === "auth" ? "degraded" : "retrying"' in src
    assert 'state: failedAxes.has(completed.axis) ? prior : (prior === "done" ? "done" : "loading")' in src
    assert 'this._markPendingAxes("retrying", kind);' in src
    assert 'this._markPendingAxes("done", kind);' in src
    assert 'this.load({ quiet: true });' in src
    assert 'v-if="axisPending(st.k)"' in src
    assert "{{ axisEmptyText(st.k) }}" in src


def test_group_hydration_has_independent_model_and_same_filter_generation_fence():
    src = VIEW.read_text(encoding="utf-8")

    assert "let workingModel = cloneTaskModel(model);" in src
    assert "const cacheGenerations = this._taskCacheGenerations" in src
    assert "const cacheGeneration = cacheGenerations[key] = (cacheGenerations[key] || 0) + 1;" in src
    assert "this._taskCacheGenerations[cacheKey] === cacheGeneration" in src
    assert "if (!generationCurrent()) return;" in src
    assert "workingModel = reconcileTaskModel(workingModel, next);" in src
    assert "reconcileTaskModel(active ? this.model : cache[cacheKey], next)" not in src
    assert "this._activeRequestToken === requestToken" in src
    assert "this._taskCacheGenerations = {};" in src


def test_buffered_old_stream_event_cannot_mutate_new_same_filter_cache_alias():
    src = VIEW.read_text(encoding="utf-8")

    assert "let streamModel = cloneTaskModel(cache[key] || this.model);" in src
    assert "const streamGenerationCurrent = () => this._taskCacheGenerations" in src
    start = src.index("if (event.replace !== false && event.model &&")
    end = src.index("if (active && event.done)", start)
    replace = src[start:end]
    assert "&& streamGenerationCurrent())" in replace
    assert replace.index("streamGenerationCurrent") < replace.index("reconcileTaskModel")
    assert "const target = active && preserveVisible ? this.model : streamModel;" in replace
    assert "streamModel = reconciled;" in replace
    assert "active ? this.model : cache[key]" not in replace


def test_partial_group_result_retries_missing_and_permission_settles_without_toast():
    src = VIEW.read_text(encoding="utf-8")

    assert "const missing = result.missing || [];" in src
    assert "if (result.retryable)" in src
    assert 'kinds.has("transport") ? "transport" : "other"' in src
    assert "jobs.push(Object.assign({}, job, { attempt: attempt + 1 }))" in src
    assert "settlePermission(job, e);" in src
    settle = src[src.index("const settlePermission"):src.index("// 브라우저 요청은 둘만", src.index("const settlePermission"))]
    assert "group.childrenPending = false;" in settle
    assert "group.childrenUnavailable = true;" in settle
    assert "pushToast" not in settle
    assert 'error.phase === "child-membership"' in src


def test_request_owned_clone_cannot_mutate_visible_or_cached_model_alias():
    node = shutil.which("node")
    if not node:
        bundled = (REPO_ROOT.parents[3] / ".cache" / "codex-runtimes"
                   / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe")
        node = str(bundled) if bundled.exists() else None
    if not node:
        pytest.skip("Node.js is not installed")
    script = f"""
      import {{ cloneTaskModel, reconcileTaskModel }} from {json.dumps(MODEL.as_uri())};
      const visible = {{ syncId: 'new', groups: [{{ key: 'DL-1', title: 'new', atoms: [], others: [] }}], epics: [] }};
      const cache = visible;
      const requestOwned = cloneTaskModel(visible);
      reconcileTaskModel(requestOwned,
        {{ syncId: 'old', groups: [{{ key: 'DL-1', title: 'stale', atoms: [], others: [] }}], epics: [] }});
      process.stdout.write(JSON.stringify({{
        visibleTitle: visible.groups[0].title,
        cacheTitle: cache.groups[0].title,
        ownedTitle: requestOwned.groups[0].title,
        independent: requestOwned !== visible && requestOwned.groups[0] !== visible.groups[0],
      }}));
    """
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], check=True,
        capture_output=True, text=True,
    )
    assert json.loads(completed.stdout) == {
        "visibleTitle": "new", "cacheTitle": "new", "ownedTitle": "stale",
        "independent": True,
    }
