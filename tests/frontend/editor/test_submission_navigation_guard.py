"""Execute real submission methods across preparation and durable cleanup awaits."""
import base64
import json
import os
import shutil
import subprocess

from support.paths import REPO_ROOT


def run_node(body):
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    assert node, "Node is required for submission navigation contracts"
    guard = "data:text/javascript;base64," + base64.b64encode(
        (REPO_ROOT / "app/static/lib/ticketLeaveGuard.js").read_bytes()).decode("ascii")
    header = "import {prepareTicketLeave} from " + json.dumps(guard) + ";\n" + r'''
      import assert from 'node:assert/strict';
      import fs from 'node:fs';
      const root=ROOT;
      const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
      const editorSource=fs.readFileSync(root+'/app/static/components/ui/CommentEditor.js','utf8').replace(/\r\n/g,'\n');
      const method=editorSource.slice(editorSource.indexOf('    async submit() {'),editorSource.lastIndexOf('\n  },\n  template:'));
      const loadSubmit=jiraBase=>new Function('jiraBase','location','hasEditorContent',
        'return ({'+method+'}).submit')(jiraBase,{origin:'https://ltm.test',port:''},html=>!!html);
      const composerSource=fs.readFileSync(root+'/app/static/components/ui/ConfluenceComposer.js','utf8');
      const loadComposer=(saveDraft,clearDraft=()=>{})=>new Function('CommentEditor','api','saveDraft','loadDraft','clearDraft','newMutationId','resolveDraftKey','confluenceEditorHtml',
        composerSource.replace(/^import .*;\r?$/gm,'').replace('export default','return'))({}, {},saveDraft,()=>{},clearDraft,()=> 'new-draft',()=>{},()=>{});
    '''.replace("ROOT", json.dumps(str(REPO_ROOT).replace("\\", "/")))
    result = subprocess.run([node, "--input-type=module", "-e", header + body],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_jira_context_wait_blocks_ticket_and_document_leave_and_duplicate_submit():
    run_node(r'''
      for(const adapter of [true,false]){
        const gate=defer();let writes=0,flushes=0,lookups=0;
        const submit=loadSubmit(()=>{lookups++;return gate.promise;});
        const editor={ready:true,busy:false,submitting:false,_pending:new Map(),
          _ed:{isEditable:true,getHTML:()=>'<p>Draft</p>',getText:()=> 'Draft',setEditable(value){this.isEditable=value;}},
          flushDraft:async()=>{flushes++;return true;},draftScopeName:()=>'',
          finishSubmission:async()=>{},$emit(){},kind:'description',
          submitFn:async()=>{writes++;},submitAdapter:adapter?()=>{}:null,
          submitWithAdapter:async()=>{writes++;}};
        const composer=loadComposer(()=>{}),ctx={ready:true,$refs:{editor},emitted:0,
          $emit(){this.emitted++;},persist:async()=>{throw Error('must not persist while submitting');}};
        const first=submit.call(editor);
        assert.equal(editor.submitting,true,'submission must be locked before Jira context resolves');
        assert.equal(editor._ed.isEditable,false,'preparation must freeze the submitted body');
        assert.equal(await prepareTicketLeave({$refs:{newCommentEditor:editor}}),false);
        assert.equal(await composer.methods.close.call(ctx,{requireDurable:true}),false);
        await submit.call(editor);assert.equal(lookups,1);assert.equal(writes,0);assert.equal(flushes,0);
        gate.resolve('https://jira.test');await first;
        assert.equal(writes,1);assert.equal(editor.submitting,false);assert.equal(ctx.emitted,0);
        assert.equal(editor._ed.isEditable,true);
      }
    ''')


def test_submission_preparation_releases_lock_on_rejection_empty_body_and_adapter_failure():
    run_node(r'''
      for(const mode of ['lookup-failure','empty','adapter-failure']){
        const gate=defer(),submit=loadSubmit(()=>gate.promise);
        const editor={ready:true,busy:false,submitting:false,
          readOnly:true,_ed:{getHTML:()=>mode==='empty'?'':'<p>Draft</p>',getText:()=> 'Draft',setEditable(value){this.isEditable=value;}},
          submitAdapter:()=>{},submitWithAdapter:async()=>{throw Error('adapter failed');}};
        const attempt=submit.call(editor);assert.equal(editor.submitting,true);
        if(mode==='lookup-failure')gate.reject(Error('context unavailable'));else gate.resolve('');
        if(mode==='empty')await attempt;else await assert.rejects(attempt);
        assert.equal(editor.submitting,false,mode+' must unlock subsequent retry');
        assert.equal(editor._ed.isEditable,false,'a read-only editor must remain read-only');
      }
    ''')


def test_finish_run_blocks_close_and_duplicate_cleanup_until_every_durable_step_finishes():
    run_node(r'''
      globalThis.window={confirm:()=>true};
      for(const stage of ['history','form','editor','clear'])for(const fail of [false,true]){
        const gate=defer();let saves=0,finishes=0,flushes=0,emits=0;
        const composer=loadComposer(async key=>{saves++;if((stage==='history'&&saves===1)||(stage==='form'&&saves===2))await gate.promise;},()=>gate.promise);
        const ctx={ready:true,busy:false,submitting:false,finishingSubmission:false,formKey:'form',
          form:{title:'Original',space:'ETL',parent:null},run:{payload:{title:'Original',mutation_id:'receipt'},page:{id:'7'}},runHistory:[],editingPage:stage==='clear'?{id:'7'}:null,
          $refs:{editor:{flushDraft:async()=>{flushes++;return true;},finishSubmission:async()=>{finishes++;if(stage==='editor')await gate.promise;}}},
          $emit(){emits++;},persist:async()=>{throw Error('old run must not be persisted');}};
        const finishing=composer.methods.finishRun.call(ctx);
        for(let i=0;i<5;i++)await Promise.resolve();
        assert.equal(ctx.finishingSubmission,true,stage+' must hold cleanup lock');
        assert.equal(await composer.methods.close.call(ctx,{requireDurable:true}),false);
        const before=saves;await composer.methods.finishRun.call(ctx);assert.equal(saves,before);
        assert.equal(flushes,0);assert.equal(emits,0);
        if(fail)gate.reject(Error('storage unavailable'));else gate.resolve();await finishing;
        assert.equal(ctx.finishingSubmission,false);
        if(fail){assert.equal(ctx.run.payload.mutation_id,'receipt');assert.match(ctx.error,/storage unavailable/);}
        else if(stage==='clear'){assert.equal(emits,1);assert.equal(finishes,1);}
        else {assert.equal(ctx.run,null);assert.equal(ctx.form.title,'');assert.equal(finishes,1);}
      }
    ''')


def test_cancel_actions_keep_preparing_editor_mounted_and_recheck_after_flush():
    run_node(r'''
      const source=fs.readFileSync(root+'/app/static/components/editor/editorDraft.js','utf8').replace(/^import .*;\r?$/gm,'');
      const draft=new Function(source.replace('export const editorDraftMethods =','return'))();
      const ticket=fs.readFileSync(root+'/app/static/components/ui/TicketDialog.js','utf8').replace(/\r\n/g,'\n');
      function cancel(name){const start=ticket.indexOf('    async '+name+'() {'),end=ticket.indexOf('\n    },',start);
        return new Function('return ({'+ticket.slice(start,end+7)+'}).'+name)();}
      for(const flag of ['busy','submitting']){
        let emitted=0,flushed=0;
        const editor={[flag]:true,flushDraft:async()=>{flushed++;return true;},$emit(){emitted++;}};
        await draft.cancelEditing.call(editor);assert.equal(await draft.discardDraft.call(editor),false);
        assert.equal(emitted,0);assert.equal(flushed,0);
        for(const name of ['cancelDescEdit','cancelCompose']){
          const ctx={$refs:{ded:editor,newCommentEditor:editor},descEdit:true,composing:true,stopWatchDesc(){throw Error('must stay mounted');}};
          await cancel(name).call(ctx);assert.equal(ctx.descEdit,true);assert.equal(ctx.composing,true);assert.equal(flushed,0);
        }
      }
      for(const name of ['editor','cancelDescEdit','cancelCompose']){
        const gate=defer();let emitted=0;
        const editor={flushDraft:()=>gate.promise,$emit(){emitted++;}};
        const ctx={$refs:{ded:editor,newCommentEditor:editor},descEdit:true,composing:true,stopWatchDesc(){throw Error('must stay mounted');}};
        const closing=name==='editor'?draft.cancelEditing.call(editor):cancel(name).call(ctx);
        editor.submitting=true;gate.resolve(true);await closing;
        assert.equal(emitted,0);assert.equal(ctx.descEdit,true);assert.equal(ctx.composing,true);
      }
    ''')
