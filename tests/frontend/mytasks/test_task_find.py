"""Loaded card search is independent of network, paging, and folding."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_card_search_membership_navigation_and_unicode():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/mytasks/taskFind.js").as_uri()
    script = r'''
import assert from 'node:assert/strict';
import {taskFindRows, findTaskCards, nextTaskMatch} from MODULE;
const card=(key,title,statusCategory='todo')=>({key,title,statusCategory,assignee:'강수아',epicKey:'DL-99'});
const parent=card('DL-1','부모'), child=card('DL-2','한글 하위','done');
const grouped={kind:'task',key:parent.key,parentCard:parent,cards:[child]};
const compact={kind:'solo',cards:[{...parent,compactPanel:grouped}]};
for(const panel of [grouped,compact]) {
  const rows=taskFindRows([{key:'wbs:A',panels:[panel,panel]}],()=> '공통 Epic');
  assert.equal(rows.length,2,'deduplicate cards repeated by grouped status layout');
  assert.equal(findTaskCards(rows,'  ｄｌ-２ ')[0].key,'DL-2');
  assert.equal(findTaskCards(rows,'한글')[0].parentKey,'DL-1');
  assert.equal(findTaskCards(rows,'강수아').length,2);
  assert.equal(findTaskCards(rows,'공통 epic').length,2);
  assert.equal(findTaskCards(rows,'').length,0);
  assert.equal(nextTaskMatch(rows,'DL-2',1),'DL-1');
  assert.equal(nextTaskMatch(rows,'DL-1',-1),'DL-2');
}
assert.equal(nextTaskMatch([], '', 1),'');
const displayed=taskFindRows([{key:'all',panels:[{kind:'solo',cards:[
  {...card('DL-3','날짜'),assignee:null,dueDays:0},
  {...card('DL-4','예정'),dueDays:3},
  {...card('DL-5','지연'),dueDays:-2},
  {...card('DL-6','완료','done'),resolved:'2026-11-04'},
  {...card('DL-7','하위'),isSub:true,parent:{key:'DL-88',title:'숨은 부모 제목'}},
]}]}]);
for(const [query,key] of [['미할당','DL-3'],['D-DAY','DL-3'],['D-3','DL-4'],
  ['D+2','DL-5'],['26.11.04','DL-6'],['미정','DL-7'],['DL-88','DL-7'],['숨은 부모 제목','DL-7']]) {
  assert.equal(findTaskCards(displayed,query)[0].key,key,query);
}
assert.equal(nextTaskMatch(displayed,'missing',1),'DL-3');
assert.equal(nextTaskMatch(displayed,'missing',-1),'DL-6');
'''.replace("MODULE", json.dumps(module))
    subprocess.run([node, "--input-type=module", "-e", script], check=True, capture_output=True, text=True)


def test_find_loading_includes_background_child_sync():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/mytasks/taskFindMixin.js").as_uri()
    script = r'''
import assert from 'node:assert/strict';
import mixin from MODULE;
const loading=(state)=>mixin.computed.findLoading.call({loading:false,streamAxes:{},childSync:{'DL-1':state}});
for(const state of ['queued','loading','retrying']) assert.equal(loading(state),true,state);
for(const state of ['complete','waiting-auth','failed']) assert.equal(loading(state),false,state);
'''.replace("MODULE", json.dumps(module))
    subprocess.run([node, "--input-type=module", "-e", script], check=True, capture_output=True, text=True)
