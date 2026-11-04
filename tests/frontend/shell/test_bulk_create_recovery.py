"""Execute the UI's batch state machine with response loss, not a real Jira delay."""
import json
import shutil
import subprocess

import pytest

from support.paths import STATIC_ROOT


def test_bulk_ui_reconciles_lost_chunk_without_new_ids_or_false_failures():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend behavior tests")
    source = (STATIC_ROOT / "lib/bulkCreateRun.js").read_text(encoding="utf-8")
    dialog = (STATIC_ROOT / "components/ui/BulkCreateDialog.js").read_text(encoding="utf-8")
    script = "const source = " + json.dumps(source) + "; const dialog = " + json.dumps(dialog) + ";\n" + r"""
    import assert from 'node:assert/strict';
    const mod = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
    const {newBulkRun, executeBulkRun, bulkRunResult} = mod;
    const scope={environment:'mock',jiraBase:'http://jira.local',projectKey:'DL',userId:'u01'};
    const items = Array.from({length:10}, (_,i) => ({summary:'row '+i,type:'Task',epic:null}));
    let run = newBulkRun('task', items, 'bulk:ui:test',scope), journal, lost=true, writes=0;
    const receipts=new Map(), calls=[];
    const persist=async value => { journal=JSON.stringify(value); };
    const send=async body => {
      assert.ok(journal.includes(body.clientMutationId), 'receipt must be saved before request');
      calls.push(JSON.parse(JSON.stringify(body)));
      if (!receipts.has(body.clientMutationId)) receipts.set(body.clientMutationId,
        {created:body.items.map((item,index)=>({index,key:'DL-'+(++writes)})),failed:[]});
      if(lost) {lost=false; throw Object.assign(new Error('35 second timeout'),{uncertain:true});}
      return receipts.get(body.clientMutationId);
    };
    let result=await executeBulkRun(run,send,persist);
    assert.equal(writes,5); assert.equal(result.failed.length,0);
    assert.equal(result.pending.length,5); assert.equal(result.notStarted.length,5);
    run=JSON.parse(journal); // close/reopen restores the exact request plan
    result=await executeBulkRun(run,send,persist);
    assert.equal(writes,10); assert.equal(result.created.length,10); assert.equal(result.ok,true);
    assert.deepEqual(calls[0],calls[1]); assert.notEqual(calls[1].clientMutationId,calls[2].clientMutationId);
    await executeBulkRun(run,send,persist); assert.equal(calls.length,3,'confirmed chunks never repeat');

    const partial=newBulkRun('task',items.slice(0,3),'bulk:partial',scope);
    result=await executeBulkRun(partial,async()=>({created:[{index:0,key:'DL-1'}],failed:[
      {index:1,error:'unknown',code:'outcome_unknown',repeatWrite:false},
      {index:2,error:'permission denied'}]}),persist);
    assert.equal(result.created.length,1); assert.equal(result.pending.length,1); assert.equal(result.failed.length,1);
    result=await executeBulkRun(partial,async()=>{throw Object.assign(new Error('parent changed'),{status:400});},persist);
    assert.equal(result.created[0].key,'DL-1'); assert.equal(result.pending.length,2);
    assert.equal(result.failed.length,0,'a later validation error cannot disprove earlier creation');
    const malformed=newBulkRun('task',items.slice(0,2),'bulk:malformed',scope);
    result=await executeBulkRun(malformed,async()=>({created:[{index:0,key:'DL-3'}],
      failed:[{index:0,error:'contradictory duplicate'}]}),persist);
    assert.equal(result.created.length,0); assert.equal(result.pending.length,2);
    let sent=false;
    await assert.rejects(executeBulkRun(newBulkRun('task',items,'bulk:disk',scope),async()=>{sent=true;},
      async()=>{throw new Error('disk full');}),/disk full/);
    assert.equal(sent,false);

    // Closing/reopening updates draft.savedAt but cannot extend the receipt replay window.
    const realNow=Date.now; let clock=realNow(); Date.now=()=>clock;
    const stale=newBulkRun('task',items.slice(0,1),'bulk:expired',scope);
    await executeBulkRun(stale,async()=>{throw new Error('response lost');},persist);
    const firstAttemptAt=stale.firstAttemptAt;
    clock+=7*86400000;
    const reopened=JSON.parse(JSON.stringify({savedAt:clock,fields:{creationRun:stale}}));
    let staleSends=0;
    result=await executeBulkRun(reopened.fields.creationRun,async()=>{staleSends++;},persist);
    assert.equal(staleSends,0); assert.equal(result.pending.length,1); assert.match(result.blocked,/7일/);
    assert.equal(reopened.fields.creationRun.firstAttemptAt,firstAttemptAt);
    stale.chunks[0].states[0]={ok:false,error:'rejected'};
    result=await executeBulkRun(stale,async()=>{staleSends++;},persist);
    assert.equal(staleSends,0); assert.match(result.blocked,/7일/);
    Date.now=realNow;

    const noops=()=>{};
    globalThis.localStorage={getItem:()=>null};
    globalThis.deps={...mod,api:{},newMutationId:()=> 'bulk:dialog:id',formDraft:()=>({}),
      copyText:noops,pushToast:noops,fromBackdrop:noops,
      validateBulk:noops,exampleJson:noops,fieldDocs:noops,buildLlmPrompt:noops,categoryColor:noops,
      errorLines:noops,TypeBadge:{},JsonEditor:{},PriIcon:{},Avatar:{},DueText:{}};
    const names=Object.keys(globalThis.deps).join(',');
    const componentSource='const {'+names+'}=globalThis.deps;\n'+dialog.replace(/^import .*;$/gm,'');
    const {default:component}=await import('data:text/javascript;base64,'+Buffer.from(componentSource).toString('base64'));
    const vm={...component.data(),mode:'task',_formReady:Promise.resolve(),_formKey:'owner-scope',
      formDraftScope:'bulk-create:task',executionScope:scope,
      flushFormDraft:async()=>{},finishFormDraft:async()=>{finished++;},$emit:noops};
    for(const [name,fn] of Object.entries(component.methods)) vm[name]=fn.bind(vm);
    let finished=0, payloads=[];
    let currentScope={...scope}; deps.api.bulkCreateContext=async()=>({...currentScope});
    vm.items=items.slice(0,1);
    deps.api.bulkCreate=async body=>{payloads.push(JSON.parse(JSON.stringify(body))); return payloads.length===1
      ? {created:[],failed:[{index:0,error:'unknown',code:'outcome_unknown'}]}
      : payloads.length===2 ? {created:[],failed:[{index:0,error:'login required',code:'authentication_required'}]}
      : {created:[{index:0,key:'DL-99'}],failed:[]};};
    await vm.create(); assert.equal(vm.result.pending.length,1); assert.equal(finished,0);
    const oldRun=vm.creationRun; vm.retryFailed(); assert.equal(vm.creationRun,oldRun);
    await vm.create(); assert.equal(vm.result.pending.length,1); assert.equal(vm.result.failed.length,0);
    vm.retryFailed(); assert.equal(vm.creationRun,oldRun,'401 reconciliation cannot enable a new-ID retry');
    // The dialog stays open while the server changes context; every authoritative dimension fences writes.
    for(const field of Object.keys(scope)) {
      currentScope={...scope,[field]:'changed'};
      await vm.create(); assert.equal(payloads.length,2); assert.equal(vm.creationRun,oldRun);
      assert.match(vm.errors[0].message,/변경/);
    }
    currentScope={...scope};
    await vm.create(); assert.equal(vm.result.created[0].key,'DL-99'); assert.equal(finished,1);
    assert.deepEqual(payloads[0],payloads[1]);
    assert.deepEqual(payloads[1],payloads[2]);
    stale.firstAttemptAt=realNow()-8*86400000;
    vm.creationRun=stale; vm.result=bulkRunResult(stale); vm.retryFailed(); assert.equal(vm.creationRun,stale);

    // Context changes between chunks: original scope is never replaced by the new endpoint response.
    vm.creationRun=null; vm.executionScope=scope; vm.items=items; vm.result=null; payloads=[];
    deps.api.bulkCreate=async body=>{payloads.push(body); currentScope={...scope,userId:'u02'};
      return {created:body.items.map((_,index)=>({index,key:'DL-'+index})),failed:[]};};
    await vm.create(); assert.equal(payloads.length,1); assert.equal(vm.result.created.length,5);
    assert.equal(vm.result.pending.length,5); assert.equal(vm.creationRun.scope.userId,'u01');

    // First write also refuses a context different from the one captured on dialog open.
    vm.creationRun=null; vm.items=items; vm.result=null;
    await vm.create(); assert.equal(payloads.length,1); assert.equal(vm.creationRun,null);
    assert.notEqual(mod.bulkScopeKey(scope),mod.bulkScopeKey({...scope,projectKey:'OTHER'}));
    """
    result = subprocess.run([node, "--input-type=module"], input=script, text=True,
                            capture_output=True, timeout=20, encoding="utf-8")
    assert result.returncode == 0, result.stderr


def test_bulk_http_marks_transport_loss_as_uncertain_and_retains_ui_plan():
    api = (STATIC_ROOT / "lib/api.js").read_text(encoding="utf-8")
    dialog = (STATIC_ROOT / "components/ui/BulkCreateDialog.js").read_text(encoding="utf-8")
    assert 'jsonReq("/api/bulk/create", "POST", body, { mutation: true })' in api
    assert '"createMutationId", "creationRun"' in dialog
    assert 'c._st.ok === null' in dialog and "같은 요청으로 결과 확인·계속" in dialog
