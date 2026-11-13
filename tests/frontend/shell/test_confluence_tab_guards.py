"""Execute the document close guard against editor submission and snapshot races."""
import json
import os
import shutil
import subprocess

from support.paths import REPO_ROOT


def run_node(body):
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    assert node, "Node is required for document tab guard contracts"
    header = r'''
      import assert from 'node:assert/strict';
      import fs from 'node:fs';
      const root=ROOT;
      const load=(path,imports)=>new Function(...Object.keys(imports),
        fs.readFileSync(root+path,'utf8').replace(/^import .*;\r?$/gm,'').replace('export default','return'))(...Object.values(imports));
      const composer=load('/app/static/components/ui/ConfluenceComposer.js',{
        CommentEditor:{},api:{},saveDraft(){},loadDraft(){},clearDraft(){},newMutationId(){},resolveDraftKey(){},confluenceEditorHtml(){}});
      const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
      function fixture(){
        const editor={ready:true,tick:1,busy:false,aiBusy:false,draftConflict:false,flushDraft:async()=>true};
        const ctx={busy:false,submitting:false,finishingSubmission:false,ready:true,revisionConflict:false,
          form:{title:'Draft',space:'ETL',parent:null},run:{payload:{mutation_id:'unknown-receipt'}},draftId:'draft',
          sourceVersion:3,runHistory:[],$refs:{editor},emitted:0,persisted:0,
          $emit(){this.emitted++;},async persist(){this.persisted++;}};
        ctx.close=options=>composer.methods.close.call(ctx,options);
        return{ctx,editor};
      }
    '''.replace('ROOT', json.dumps(str(REPO_ROOT).replace('\\', '/')))
    result = subprocess.run([node, '--input-type=module', '-e', header + body], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_close_blocks_editor_submission_and_ai_before_parent_adapter_is_busy():
    run_node(r'''
      const {editorSubmissionMethods}=await import(new URL('file:///'+root+'/app/static/components/editor/editorSubmission.js'));
      const {ctx,editor}=fixture(),flush=defer();let writes=0;
      editor._ed={setEditable(){}};editor._pending=new Map();editor.flushDraft=()=>flush.promise;
      editor.submitAdapter=async()=>{writes++;};editor.finishSubmission=async()=>{};editor.$emit=()=>{};
      const publishing=editorSubmissionMethods.submitWithAdapter.call(editor,'<p>Draft</p>','https://jira.test');
      assert.equal(editor.busy,true);assert.equal(ctx.busy,false);
      assert.equal(await ctx.close({requireDurable:true}),false);
      assert.equal(ctx.emitted,0);assert.equal(ctx.persisted,0);assert.equal(writes,0);
      flush.resolve(true);await publishing;assert.equal(writes,1);
      editor.aiBusy=true;assert.equal(await ctx.close({requireDurable:true}),false);
    ''')


def test_close_rechecks_every_await_for_busy_conflict_and_content_changes():
    run_node(r'''
      for(const stage of ['flush','persist'])for(const change of ['parentBusy','editorBusy','aiBusy','conflict','tick','form','receipt','editorRef']){
        const {ctx,editor}=fixture(),gate=defer();
        if(stage==='flush')editor.flushDraft=()=>gate.promise;
        else ctx.persist=()=>{ctx.persisted++;return gate.promise;};
        const closing=ctx.close({requireDurable:true});
        if(stage==='persist')await Promise.resolve();
        if(change==='parentBusy')ctx.busy=true;
        if(change==='editorBusy')editor.busy=true;
        if(change==='aiBusy')editor.aiBusy=true;
        if(change==='conflict')editor.draftConflict=true;
        if(change==='tick')editor.tick++;
        if(change==='form')ctx.form.title='Changed during save';
        if(change==='receipt')ctx.run.payload.mutation_id='another-receipt';
        if(change==='editorRef')ctx.$refs.editor={...editor};
        gate.resolve(true);
        assert.equal(await closing,false,stage+' '+change);assert.equal(ctx.emitted,0);
        if(stage==='flush')assert.equal(ctx.persisted,0);
      }
      const {ctx}=fixture();assert.equal(await ctx.close({requireDurable:true}),true);
      assert.equal(ctx.emitted,1);assert.equal(ctx.run.payload.mutation_id,'unknown-receipt');
    ''')


def test_publish_preparation_blocks_close_before_editor_busy_is_set():
    run_node(r'''
      const {ctx,editor}=fixture(),gate=defer();editor.submit=()=>gate.promise;
      const publishing=composer.methods.publish.call(ctx);
      assert.equal(ctx.submitting,true);assert.equal(editor.busy,false);
      assert.equal(await ctx.close({requireDurable:true}),false);assert.equal(ctx.emitted,0);
      gate.resolve();await publishing;assert.equal(ctx.submitting,false);
    ''')


def test_same_document_tab_is_noop_and_failed_keyboard_activation_restores_focus():
    run_node(r'''
      const {createOpenItemTabs,normalizeItemId}=await import(new URL('file:///'+root+'/app/static/lib/openItemTabs.js'));
      const imports={api:{},confluencePageId:v=>v,openConfluenceDocument(){},readFavorites(){},writeFavorites(){},readNavigationFolds:()=>({}),
        documentHeadings(){},visibleHeading(){},mountConfluenceMacros(){},confluenceLayout:{},confluenceTree:{},confluenceSavedTree:{},confluenceRecents:{},
        readConfluence(){throw Error('Unexpected body request');},reactive:x=>x,createOpenItemTabs,normalizeItemId};
      for(const key of ['ConfluencePanel','ConfluenceDocumentList','ConfluenceComposer','ConfluencePageMenu','ConfluenceTreeRow','ConfluenceReadNotice','OpenItemTabs'])imports[key]={};
      const view=load('/app/static/components/views/ConfluenceView.js',imports);
      const ctx={selectedId:'7',composing:true,_viewActive:true,_tabNavigationGeneration:0,tabNavigationPending:false,
        closeComposerForNavigation:async()=>{throw Error('Same tab must preserve composer');},$nextTick:f=>f()};
      for(const key of ['navigateDocument','restoreNavigationFocus'])ctx[key]=view.methods[key].bind(ctx);
      assert.equal(await ctx.navigateDocument('7'),true);assert.equal(ctx._tabNavigationGeneration,0);
      let focused=0;const body={},tab={isConnected:true,disabled:false,closest:()=>({}),focus(){focused++;}};
      globalThis.document={body,activeElement:tab};ctx.closeComposerForNavigation=async()=>{document.activeElement=body;return false;};
      assert.equal(await ctx.navigateDocument('8'),false);assert.equal(focused,1);assert.equal(ctx.tabNavigationPending,false);
    ''')
