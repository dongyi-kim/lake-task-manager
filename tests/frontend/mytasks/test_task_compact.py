"""Parent and filtered child status determine width, independently of folding/page state."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_filtered_parent_child_width_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/mytasks/taskModel.js").as_uri()
    script = r'''
import assert from 'node:assert/strict';
import {filteredTaskChildren, compactTaskCategory} from MODULE;
const todo={key:'DL-1',statusCategory:'todo'}, done={key:'DL-2',statusCategory:'done'};
const panel=(cards, parent='todo', flags={})=>({cards,parentCard:{statusCategory:parent},
  group:{childrenLoaded:true,childrenPending:false,...flags}});
assert.equal(compactTaskCategory(panel([todo])),'todo');
assert.equal(compactTaskCategory(panel([todo],'done')),null,'parent must agree');
assert.equal(compactTaskCategory(panel([todo,done])),null);
const mine=filteredTaskChildren([todo],[done],'mine');
assert.equal(compactTaskCategory(panel(mine.shown)),'todo','hidden related child is excluded');
for(const mode of ['all','collapsed']) {
  const visible=filteredTaskChildren([todo],[done],mode);
  assert.equal(compactTaskCategory(panel(visible.shown)),null,'folding is not filtering');
}
const excluded=filteredTaskChildren([todo,done],[],'all',c=>c.key!=='DL-2');
assert.equal(compactTaskCategory(panel(excluded.shown)),'todo');
const none=filteredTaskChildren([todo],[done],'mine',c=>c.key!=='DL-1');
assert.deepEqual(none.shown,[],'filtering all own children must not reveal related children');
assert.equal(compactTaskCategory(panel(none.shown)),'todo','confirmed zero keeps parent width');
assert.equal(compactTaskCategory(panel([], 'todo', {childrenLoaded:false})),null);
assert.equal(compactTaskCategory(panel([todo], 'todo', {childrenUnavailable:true})),null);
assert.equal(compactTaskCategory(panel([todo], 'todo', {childrenPending:true})),null);
assert.equal(compactTaskCategory(panel([{key:'x'}])),null,'unknown child status is not todo');
assert.equal(compactTaskCategory(panel([], 'unknown')),null);
assert.deepEqual(filteredTaskChildren([], [done], 'mine').shown,[done],
  'parent-only scope retains the existing related-child fallback');
console.log('compact cases passed');
'''.replace("MODULE", json.dumps(module))
    result = subprocess.run([node, "--input-type=module", "-e", script],
                            check=True, capture_output=True, text=True)
    assert "compact cases passed" in result.stdout
