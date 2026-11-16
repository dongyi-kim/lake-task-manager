"""Dialog mutation/refresh behavior using the actual component methods and deferred I/O."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


DIALOG = REPO_ROOT / "app/static/components/ui/TicketDialog.js"
TRANSITION = REPO_ROOT / "app/static/components/ui/TransitionDialog.js"


def run_dialog(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    script = """
      import assert from 'node:assert/strict';
      import { readFileSync } from 'node:fs';
      const source = readFileSync(DIALOG_PATH, 'utf8');
      const method = (start, end, globals = {}) => {
        const a = source.indexOf(start), b = source.indexOf(end, a);
        assert(a >= 0 && b > a, 'actual component method exists');
        return new Function(...Object.keys(globals), 'return ({' + source.slice(a, b) + '});')
          (...Object.values(globals));
      };
      const events = [], listeners = {};
      globalThis.CustomEvent = class { constructor(type, options) { this.type=type; this.detail=options?.detail; } };
      globalThis.window = { addEventListener(name, fn) { listeners[name]=fn; },
        dispatchEvent(event) { events.push(event); }, removeEventListener() {} };
      globalThis.document = { addEventListener() {}, removeEventListener() {},
        documentElement: { getAttribute: () => 'light' } };
    """.replace("DIALOG_PATH", json.dumps(str(DIALOG))) + body
    completed = subprocess.run([node, "--input-type=module", "-e", script],
                               capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert completed.returncode == 0, completed.stderr


def test_confirmed_transition_updates_board_before_readback_and_survives_read_failure():
    run_dialog("""
      let finish;
      const pending = new Promise(resolve => { finish=resolve; });
      const fire = method('    _fireChanged()', '    /** 첨부·문서')._fireChanged;
      const transition = method('    onTransitioned(', '    stLabel(').onTransitioned;
      const state = { keyId:'DL-1', v:{key:'DL-1', status:'Open', statusCategory:'todo'},
        stPick:{to:'Resolved',toCategory:'done'}, load:()=>pending, _fireChanged:fire };
      transition.call(state, {key:'DL-1',status:'Resolved',statusCategory:'done',resolved:'2026-11-16'});
      assert.equal(events.length, 1, 'confirmed transition publishes before readback finishes');
      assert.equal(events[0].detail.view.statusCategory, 'done');
      assert.equal(events[0].detail.view.resolved, '2026-11-16');
      assert.equal(state.v.status, 'Resolved');
      finish(false); await pending; await Promise.resolve();
      assert.equal(events.length, 1, 'failed readback cannot republish old status');
      assert.equal(state.v.statusCategory, 'done');
    """)


def test_failed_field_readback_does_not_publish_old_or_different_ticket():
    run_dialog("""
      const saved = method('    async onFieldSaved()', '    _fireChanged()').onFieldSaved;
      const state = {keyId:'DL-1',load:async()=>false,_fireChanged:()=>events.push('changed')};
      await saved.call(state);
      assert.equal(events.length, 0, 'a failed read is not a successful field snapshot');
      state.load=async()=>{state.keyId='DL-2';return true;};
      await saved.call(state);
      assert.equal(events.length, 0, 'navigation cannot publish the next ticket as the edited one');
    """)


def test_successful_transition_emits_confirmed_target_without_inventing_resolution_date():
    run_dialog(r"""
      const transitionSource = readFileSync(TRANSITION_PATH, 'utf8');
      const start=transitionSource.indexOf('    async sendTransition(html)');
      const end=transitionSource.indexOf('  template:',start);
      const methods=new Function('api','newMutationId',
        'return ({'+transitionSource.slice(start,end).replace(/\s*},\s*$/, '')+'});')(
          {doTransition:async()=>({ok:true})},()=> 'mutation-1');
      const state={ticket:'DL-1',transition:{id:'31',to:'Resolved',toCategory:'done'},
        flushFormDraft:async()=>{},finishFormDraft:async()=>{},
        $emit:(name,view)=>events.push({name,view})};
      await methods.sendTransition.call(state,'');
      assert.equal(events.length,1);assert.equal(events[0].name,'done');
      assert.equal(events[0].view.status,'Resolved');assert.equal(events[0].view.statusCategory,'done');
      assert.equal(Object.hasOwn(events[0].view,'resolved'),false,'target category is not evidence of resolution time');
    """.replace("TRANSITION_PATH", json.dumps(str(TRANSITION))))


def test_external_revision_and_global_refresh_keep_open_edits_and_register_completion():
    run_dialog("""
      const calls=[];
      const mounted=method('  mounted() {','  unmounted() {', {
        api:{},ensureHljsTheme:()=>{},loadTiptap:()=>Promise.resolve()
      }).mounted;
      const state={keyId:'DL-1',v:{key:'DL-1'},descEdit:true,descBase:'draft base',
        sumEdit:true,sumDraft:'unsaved title',composing:true,editingId:'42',editInitial:'draft comment',
        children:[],siblings:[],$nextTick:()=>{},load:(...args)=>{calls.push(args);return Promise.resolve(true);}};
      mounted.call(state);calls.length=0;
      listeners['tickets-updated']({detail:{revision:3}});
      assert.deepEqual(calls,[[true,true]],'same-ticket revision revalidates even with editor open');
      let registered;
      listeners['data-refresh']({detail:{waitUntil:promise=>{registered=promise;}}});
      assert(registered instanceof Promise,'global refresh tracks dialog completion');
      await registered;
      assert.equal(state.descBase,'draft base');assert.equal(state.sumDraft,'unsaved title');
      assert.equal(state.editInitial,'draft comment');assert.equal(state.composing,true);
    """)


def test_quiet_attachment_refresh_updates_data_without_changing_fold_and_retains_data_on_error():
    run_dialog("""
      const api={ticketAttachments:async()=>[{id:'new'}]};
      const panel=method('    _loadDialogPanel(', '    retryPanel(', {
        api,FOLD_AT:5,requestErrorText:error=>error.message
      })._loadDialogPanel;
      const state={keyId:'DL-1',_req:1,atts:[{id:'old'}],attOpen:false,_setPanelState:()=>{}};
      await panel.call(state,'attachments','DL-1',1,true);
      assert.deepEqual(state.atts,[{id:'new'}]);assert.equal(state.attOpen,false);
      api.ticketAttachments=async()=>{throw new Error('offline');};
      await panel.call(state,'attachments','DL-1',1,true);
      assert.deepEqual(state.atts,[{id:'new'}]);assert.equal(state.attOpen,false);
    """)


def test_initial_cached_auxiliary_sections_and_failed_quiet_read_remain_visible():
    run_dialog("""
      const cached={children:[{key:'DL-2'}],siblings:[{key:'DL-3'}],ancestors:[{key:'DL-0'}],
        related:[{key:'DL-4'}],attachments:[{id:'a'}],documents:[{id:'d'}]};
      let reject;
      const api={cached:path=>cached[path.split('/').at(-1)],ticket:()=>new Promise((_,fail)=>{reject=fail;}),
        warmTicket:()=>{}};
      const load=method('    async load(force, quiet)', '    typeColor(', {
        api,DIALOG_PANEL_ORDER:[],recordOpen:()=>{}
      }).load;
      const state={keyId:'DL-1',beginUpdateView:()=>{},loadTimeline:()=>{},v:null,
        err:'prior error',children:[],siblings:[],ancestors:[],atts:[],docs:[],related:[]};
      const loading=load.call(state,false,false);
      assert.deepEqual(state.children,[{key:'DL-2'}],'cached children show before detail returns');
      assert.deepEqual(state.atts,[{id:'a'}]);assert.deepEqual(state.docs,[{id:'d'}]);
      reject(new Error('detail read failed'));assert.equal(await loading,false);
      state.v={key:'DL-1',statusCategory:'done'};state.descEdit=true;state.descBase='unsaved';
      const refresh=load.call(state,true,true);reject(new Error('still unavailable'));
      assert.equal(await refresh,false);assert.equal(state.v.statusCategory,'done');
      assert.deepEqual(state.children,[{key:'DL-2'}]);assert.equal(state.descBase,'unsaved');
    """)
