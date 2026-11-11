"""Real Vue computeds must observe successive cumulative Task snapshots."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


VIEW = REPO_ROOT / "app/static/components/views/MyTasksView.js"
MODEL = REPO_ROOT / "app/static/components/mytasks/taskModel.js"
HYDRATION = REPO_ROOT / "app/static/components/mytasks/taskHydration.js"
VUE = REPO_ROOT / "app/static/vendor/vue.esm-browser.prod.js"


def run_vue(name, body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    script = f"""
      import {{ reactive, computed, nextTick }} from {json.dumps(VUE.as_uri())};
      import {{ cloneTaskModel, reconcileTaskModel, taskLoadErrorKind, TASK_RETRY_DELAYS }}
        from {json.dumps(MODEL.as_uri())};
      import {{ hydrateTaskGroups }} from {json.dumps(HYDRATION.as_uri())};
      import {{ readFileSync }} from 'node:fs';
      {body}
    """
    process = subprocess.run([node, "--input-type=module", "-e", script],
                             capture_output=True, text=True, check=True, timeout=20)
    observed = json.loads(process.stdout)
    print(json.dumps(observed, ensure_ascii=False))
    if destination := os.environ.get("JQL_VUE_REPRO_DIR"):
        evidence = {"observed": observed, "sourceSha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (VIEW, MODEL, HYDRATION, VUE)}}
        with (Path(destination) / f"{name}.json").open("x", encoding="utf-8") as stream:
            json.dump(evidence, stream, ensure_ascii=False, indent=2)
    return observed


def test_stream_cumulative_snapshot_invalidates_real_vue_computed_membership():
    observed = run_vue("stream-cumulative", f"""
      const source = readFileSync({json.dumps(str(VIEW))}, 'utf8');
      const start = source.indexOf('    async load(opts) {{');
      const end = source.indexOf('    _groupCounts(groups) {{', start);
      if (start < 0 || end < 0) throw new Error('Cannot locate the actual Task load method');
      const loadMethod = source.slice(start, end);
      globalThis.window = {{ crypto: {{ randomUUID: () => 'reactivity-proof' }}, dispatchEvent() {{}} }};
      const observations = [];
      const group = key => ({{ key, title: key, atoms: [{{ key, statusCategory: 'todo' }}], others: [] }});
      const snapshot = keys => ({{ groups: keys.map(group), epics: [], counts: {{ total: keys.length }} }});
      let state, renderedKeys;
      const api = {{ async myTasksStream(options, onEvent) {{
        const values = [['DL-1'], ['DL-1', 'DL-2'], ['DL-1', 'DL-2']];
        for (let index = 0; index < values.length; index++) {{
          onEvent({{ type: 'snapshot', contract: 'task-snapshot.v1', requestToken: options.requestToken,
            sequence: index, done: index === 2, replace: true, model: snapshot(values[index]),
            progress: {{ completed: index + 1, total: 3 }}, partialErrors: [],
            completedLeaves: [{{ axis: 'todo', status: 'success' }}] }});
          await nextTick();
          observations.push({{ modelKeys: state.model.groups.map(row => row.key),
            modelTotal: state.model.counts.total, computedKeys: [...renderedKeys.value] }});
        }}
      }} }};
      const load = new Function('api', 'STATES', 'cloneTaskModel', 'reconcileTaskModel',
        'taskLoadErrorKind', 'TASK_RETRY_DELAYS', 'pushToast', 'reactive',
        'return ({{' + loadMethod + '}}).load;')(
          api, [{{ k: 'todo' }}, {{ k: 'inprogress' }}, {{ k: 'done' }}], cloneTaskModel,
          reconcileTaskModel, taskLoadErrorKind, TASK_RETRY_DELAYS, () => {{}}, reactive);
      state = reactive({{ apiScope: 'module:ETL', openFilter: 'all', progFilter: 'all', doneFilter: '1m',
        model: null, excluded: {{}}, streamAxes: {{ todo: {{}}, inprogress: {{}}, done: {{}} }},
        resetAxisPages() {{}}, _queueEpicMetadata() {{}},
        _emptyTaskModel() {{ return {{ groups: [], epics: [], counts: {{ total: 0 }} }}; }},
        _cacheModel(cache, key, model) {{ cache[key] = model; }},
        _hydrateModel() {{ return Promise.resolve(); }},
        _scheduleTaskRetry() {{ return false; }}, _markPendingAxes() {{}} }});
      renderedKeys = computed(() => state.model?.groups.flatMap(row => row.atoms.map(atom => atom.key)) || []);
      await load.call(state);
      process.stdout.write(JSON.stringify({{ observations, error: state.err }}));
    """)
    assert observed["error"] == ""
    assert observed["observations"][1]["modelKeys"] == ["DL-1", "DL-2"]
    assert observed["observations"][1]["modelTotal"] == 2
    assert observed["observations"][1]["computedKeys"] == ["DL-1", "DL-2"]


def test_child_cumulative_snapshots_invalidate_real_vue_computed_pending_groups():
    observed = run_vue("child-cumulative", """
      const group = (key, pending) => ({ key, atoms: [], others: [], childrenPending: pending,
                                        childrenLoaded: !pending });
      const snapshot = (sequence, firstPending, secondPending) => ({ syncId: 'sync-proof',
        snapshotSequence: sequence, groups: [group('DL-1', firstPending), group('DL-2', secondPending)], epics: [] });
      const state = reactive({ model: snapshot(0, true, true) });
      const pending = computed(() => state.model.groups.filter(row => row.childrenPending).map(row => row.key));
      const observations = [], failures = [];
      const result = await hydrateTaskGroups({ model: state.model,
        loadGroup: async (_sync, key) => ({ contract: 'task-snapshot.v1', type: 'snapshot',
          syncId: 'sync-proof', sequence: key === 'DL-1' ? 1 : 2,
          model: key === 'DL-1' ? snapshot(1, false, true) : snapshot(2, false, false), retryable: false }),
        isCurrent: () => true, canStart: () => true,
        publish(model) {
          state.model = model;
          observations.push({ modelPending: state.model.groups.filter(row => row.childrenPending).map(row => row.key),
                              computedPending: [...pending.value] });
        }, onStates() {}, onFailure: (...args) => failures.push(args), wait: async () => {} });
      await nextTick();
      process.stdout.write(JSON.stringify({ observations, failures, finalStates: result.states,
        finalModelPending: state.model.groups.filter(row => row.childrenPending).map(row => row.key),
        finalComputedPending: [...pending.value] }));
    """)
    assert observed["failures"] == []
    assert observed["finalStates"] == {"DL-1": "complete", "DL-2": "complete"}
    assert observed["finalModelPending"] == []
    assert observed["finalComputedPending"] == []
