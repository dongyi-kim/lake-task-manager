"""Run the ticket navigation guard against editor/field state without browser or API."""
import base64
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_guard(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = "data:text/javascript;base64," + base64.b64encode(
        (REPO_ROOT / "app/static/lib/ticketLeaveGuard.js").read_bytes()).decode("ascii")
    source = "import assert from 'node:assert/strict';\nimport {prepareTicketLeave, ticketEditors} from " + json.dumps(module) + ";\n" + script
    result = subprocess.run([node, "--input-type=module", "-e", source],
                            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_leave_blocks_active_writes_nested_forms_and_unsaved_fields():
    run_guard(r"""
const blocked = [
  {uploading:true}, {descBusy:true}, {sumBusy:true}, {linkBusy:true}, {docBusy:true},
  {pendingWrites:1}, {adding:true}, {stPick:{}}, {sumEdit:true,sumDraft:'changed',v:{summary:'saved'}},
  {$refs:{editFieldDue:{busy:true}}}, {$refs:{editFieldLabels:{open:true}}},
  {descEdit:true}, {composing:true}, {editingId:'17'},
];
for (const state of blocked) {
  let notices=0;
  assert.equal(await prepareTicketLeave({$refs:{},...state},{notify:()=>notices++}),false);
  assert.equal(notices,1);
}
assert.equal(await prepareTicketLeave({$refs:{}},{isBusy:()=>true}),false);
assert.equal(await prepareTicketLeave({$refs:{},sumEdit:true,sumDraft:'saved',v:{summary:'saved'}}),true);
""")


def test_leave_flushes_all_editor_scopes_and_retains_unresolved_receipt():
    run_guard(r"""
const flushed=[];
const editor = name => ({tick:1,flushDraft:async()=>{flushed.push(name);return true;}});
const description=editor('description'), compose=editor('new-comment'), edit=editor('comment-id');
compose.submitMutationId='comment-original';compose.pendingSubmitHtml='<p>original payload</p>';
compose.isBlank=()=>false;
const dialog={$refs:{ded:description,newCommentEditor:compose,editingCommentEditor:[edit]}};
assert.equal(await prepareTicketLeave(dialog),true);
assert.deepEqual(flushed,['description','new-comment','comment-id']);
assert.equal(compose.submitMutationId,'comment-original');
assert.equal(compose.pendingSubmitHtml,'<p>original payload</p>');
assert.deepEqual(ticketEditors({$refs:{ded:description,newCommentEditor:description}}),[description]);
compose.isBlank=()=>true;
assert.equal(await prepareTicketLeave(dialog),false);
assert.equal(compose.submitMutationId,'comment-original');
""")


def test_leave_keeps_current_editor_on_save_failure_conflict_or_race():
    run_guard(r"""
const editor={tick:1,flushDraft:async()=>false};
const dialog={$refs:{newCommentEditor:editor},composing:true};
assert.equal(await prepareTicketLeave(dialog),false);
editor.flushDraft=async()=>{throw new Error('IDB failed');};
assert.equal(await prepareTicketLeave(dialog),false);
editor.draftConflict=true;editor.flushDraft=async()=>{throw new Error('must not flush conflict');};
assert.equal(await prepareTicketLeave(dialog),false);
editor.draftConflict=false;editor.flushDraft=async()=>{editor.tick++;return true;};
assert.equal(await prepareTicketLeave(dialog),false);
editor.flushDraft=async()=>{editor.busy=true;return true;};
assert.equal(await prepareTicketLeave(dialog),false);
editor.busy=false;editor.flushDraft=async()=>{dialog.$refs.newCommentEditor={...editor};return true;};
assert.equal(await prepareTicketLeave(dialog),false);
""")
