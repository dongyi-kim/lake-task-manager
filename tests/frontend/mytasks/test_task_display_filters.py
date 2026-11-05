"""Local priority/deadline membership and stable filter choices."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_priority_and_deadline_display_filter_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/mytasks/taskDisplayFilters.js").as_uri()
    script = r'''
import assert from 'node:assert/strict';
import {DUE_FILTER_OPTIONS, deadlineBucket, priorityKey, priorityOptions,
        matchTaskDisplayFilters as matches} from MODULE;

for (const [days, expected] of [[undefined,'none'],[null,'none'],[NaN,'none'],
    [Infinity,'none'],[-1,'overdue'],[0,'soon'],[7,'soon'],[8,'calm']]) {
  assert.equal(deadlineBucket({dueDays:days}),expected,`deadline ${days}`);
}
assert.equal(deadlineBucket({dueDays:0,statusCategory:'done'}),'soon',
  'deadline filtering compares the date, independently of completion');
assert.deepEqual(DUE_FILTER_OPTIONS.map(o=>o.k),['','calm','soon','overdue','none']);
assert.equal(priorityKey({pri:'  P2-Major  '}),'P2-Major');
assert.equal(priorityKey({pri:null}),'');

const high={pri:'P1-Critical',priRank:1,dueDays:7};
assert.ok(matches(high),'defaults show every card');
assert.ok(matches(high,'name:P1-Critical','soon'));
assert.ok(!matches(high,'name:P2-Major','soon'),'both dimensions must match');
assert.ok(!matches(high,'name:P1-Critical','overdue'));
assert.ok(matches({pri:'',dueDays:null},'__none__','none'));
assert.ok(matches({pri:' Unclassified ',priRank:2},'name:Unclassified'));
assert.ok(!matches({pri:'Medium',priRank:2},'name:Unclassified'),
  'names with the same rank remain distinct');

const cards=[{pri:'P2-Major',priRank:2},{pri:'Unclassified',priRank:2},
  {pri:'Medium',priRank:2},{pri:'Custom urgent',priRank:0},high,
  {pri:' P2-Major ',priRank:2},{pri:'',priRank:9},null];
const options=priorityOptions(cards);
assert.equal(options.length,6,'dedupe actual trimmed names');
assert.equal(options[0].label,'Custom urgent');
assert.equal(options[1].label,'P1-Critical');
assert.equal(options.at(-1).k,'__none__');
assert.deepEqual(priorityOptions(cards.slice().reverse()),options,
  'option ordering is independent of stream arrival order');
assert.deepEqual(priorityOptions([], 'name:Prior scope priority'),
  [{k:'name:Prior scope priority',label:'Prior scope priority',rank:999}]);
assert.deepEqual(priorityOptions([], '__none__'),[{k:'__none__',label:'미지정',rank:999}]);
assert.deepEqual(priorityOptions([], ''),[]);
console.log('display filter cases passed');
'''.replace("MODULE", json.dumps(module))
    result = subprocess.run([node, "--input-type=module", "-e", script],
                            check=True, capture_output=True, text=True)
    assert "display filter cases passed" in result.stdout
