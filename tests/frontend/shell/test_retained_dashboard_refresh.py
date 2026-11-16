"""Background dashboard refresh keeps visible data and queues inactive work."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_dashboard_refresh_retains_rows_and_reads_each_person_once():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for dashboard controller regressions")
    script = r'''
      import assert from 'node:assert/strict';
      const listeners=new Map();
      globalThis.window={addEventListener(t,fn){listeners.set(t,fn)},removeEventListener(){},dispatchEvent(){}};
      globalThis.localStorage={getItem(){return null},setItem(){}};
      const {api}=await import(STATIC+'/lib/api.js');
      const {default:Workload}=await import(STATIC+'/components/views/WorkloadView.js');
      const {default:Vit}=await import(STATIC+'/components/views/VitView.js');
      const {default:Tracking}=await import(STATIC+'/components/tracking-view.js');
      const vmOf=view=>{
        const vm=view.data();
        for(const [key,fn] of Object.entries(view.methods)) vm[key]=fn.bind(vm);
        for(const [key,fn] of Object.entries(view.computed||{})) Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
        vm.$nextTick=fn=>{};
        return vm;
      };
      const vm=vmOf(Workload);
      Workload.created.call(vm);
      vm.scheduleMeasure=()=>{};vm._notifyWorkloadError=()=>{};vm.loadDueRisk=async()=>{};
      vm.d={modules:[{module:'M',people:[{id:'a'}]}]};vm.mod='M';vm.peopleLoadEpoch=1;
      vm.pstat.a={id:'a',name:'Alice',inProgress:7};
      api.workloadPerson=async()=>{throw Object.assign(new Error('auth lost'),{status:401})};
      await vm._loadPeople(['a'],true);
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(vm.pstat.a.inProgress,7);
      assert.equal(vm.pstat.a.refreshErrorKind,'auth');
      const reads=[];api.workloadShell=async()=>({modules:[{module:'M',people:[{id:'a'},{id:'b'}]}]});
      api.workloadPerson=async id=>{reads.push(id);return {id,inProgress:8}};
      vm.actOpen={a:true};vm.tkd={a:{open:[{key:'DL-1'}]}};vm.open.M=false;
      const buckets=[];vm._loadDetailBucket=async(id,bucket,opts)=>{buckets.push(bucket);assert.equal(opts.resetRows,false)};
      await vm.hardRefresh();
      assert.deepEqual(reads.sort(),['a','b']);assert.equal(vm.open.M,false);
      assert.deepEqual(buckets.sort(),['done7d','inProgress','open']);
      let inactiveReads=0;vm.requestViewRefresh=()=>{vm._refreshDirty=true;return Promise.resolve()};
      vm._refreshActive=false;api.workloadPerson=async()=>{inactiveReads++};
      listeners.get('cache-policy-changed')();listeners.get('workspace-config-updated')();listeners.get('auth-ok')();
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(inactiveReads,0);assert.equal(vm._refreshDirty,true);
      const vit=vmOf(Vit);vit.detail={'DL-1':{tree:[{key:'old'}]}};vit.detailOpen={'DL-1':true};
      api.vitDetail=async()=>{throw new Error('network lost')};await vit.loadDetail({key:'DL-1'});
      assert.equal(vit.detail['DL-1'].tree[0].key,'old');assert.equal(vit.detailOpen['DL-1'],true);
      const tracking=vmOf(Tracking);tracking._epoch=0;tracking.active=false;
      const run={id:'saved',status:'completed',request:{people:[],modules:[]},issues:[],tree:[],events:[]};
      api.cached=path=>path.endsWith('/saved')?run:undefined;
      let checked=0;api.readCached=async()=>{checked++;return run};
      api.raw=async()=>{throw new Error('new collection or raw fetch forbidden')};
      await tracking.openRun('saved');assert.equal(tracking.run.id,'saved');assert.equal(checked,1);
    '''.replace("STATIC", json.dumps((REPO_ROOT / "app/static").as_uri()))
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_failed_bucket_and_due_risk_refresh_retain_display_but_permission_loss_removes_it():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for dashboard controller regressions")
    script = r'''
      import assert from 'node:assert/strict';
      globalThis.window={addEventListener(){},removeEventListener(){},dispatchEvent(){}};
      globalThis.localStorage={getItem(){return null}};
      const {default:Workload}=await import(STATIC+'/components/views/WorkloadView.js');
      const {default:Vit}=await import(STATIC+'/components/views/VitView.js');
      const {api}=await import(STATIC+'/lib/api.js');
      const vm=Workload.data();
      for(const [key,fn] of Object.entries(Workload.methods)) vm[key]=fn.bind(vm);
      for(const [key,fn] of Object.entries(Workload.computed)) Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
      vm.mod='M';vm.d={modules:[{module:'M',people:[{id:'a'}]}]};vm.pstat={a:{id:'a',name:'Alice'}};
      vm.detailLoadEpoch=1;vm.dueRiskEpoch=1;
      const ticket={key:'DL-1',due:'2000-01-01'};
      vm.dueRiskFor='M|all|voc';vm.dueRisk={parts:{
        'a|open':{id:'a',bucket:'open',status:'success',rows:[ticket]},
        'a|inProgress':{id:'a',bucket:'inProgress',status:'success',rows:[]}}};
      vm._fetchBucketRows=async()=>({status:'error',kind:'other',rows:[],error:new Error('offline')});
      await vm.loadDueRisk({force:true});
      assert.equal(vm.dueRisk.over.length,1);assert.equal(vm.dueRisk.failures,2);assert.equal(vm.dueRisk.complete,false);
      vm.tkd={a:{open:[ticket]}};
      await vm._loadDetailBucket('a','open',{resetRows:false});
      assert.equal(vm.tkd.a.open[0].key,'DL-1');assert.equal(vm.bucketStateOf('a','open').status,'error');
      vm._fetchBucketRows=async()=>({status:'permission',kind:'permission',rows:[]});
      await vm._loadDetailBucket('a','open',{resetRows:false});assert.deepEqual(vm.tkd.a.open,[]);
      const vit=Vit.data();for(const [key,fn] of Object.entries(Vit.methods)) vit[key]=fn.bind(vit);
      vit.detail={'DL-1':{tree:[ticket]}};api.vitDetail=async()=>({partial:true,tree:[]});
      await vit.loadDetail({key:'DL-1'});assert.equal(vit.detail['DL-1'].tree[0].key,'DL-1');
      api.vitDetail=async()=>{throw Object.assign(new Error('forbidden'),{status:403})};
      await vit.loadDetail({key:'DL-1'});assert.equal(vit.detail['DL-1'],undefined);
      const {compile}=await import(STATIC+'/vendor/vue.esm-browser.prod.js');
      for(const view of [Workload,Vit]) compile(view.template,{decodeEntities:raw=>raw,onError(error){throw error}});
    '''.replace("STATIC", json.dumps((REPO_ROOT / "app/static").as_uri()))
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_partial_module_and_bucket_responses_keep_prior_complete_rows():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for dashboard controller regressions")
    script = r'''
      import assert from 'node:assert/strict';
      globalThis.window={addEventListener(){},removeEventListener(){},dispatchEvent(){}};
      globalThis.localStorage={getItem(){return null}};
      const {default:Workload}=await import(STATIC+'/components/views/WorkloadView.js');
      const {default:Vit}=await import(STATIC+'/components/views/VitView.js');
      const {api}=await import(STATIC+'/lib/api.js');
      const bind=view=>{const vm=view.data();for(const [key,fn] of Object.entries(view.methods))vm[key]=fn.bind(vm);return vm;};
      const vit=bind(Vit);vit._loadSeq=1;vit.mods={M:[{key:'DL-1',summary:'prior'}]};
      api.vitModule=async()=>({partial:true,issues:[],missing:2});
      await vit.loadModule('M');assert.equal(vit.mods.M[0].summary,'prior');assert.equal(vit.modPartial.M,2);
      const workload=bind(Workload);workload.detailLoadEpoch=1;
      workload.tkd={a:{open:[{key:'DL-1',epicName:'Confirmed Epic'}]}};
      workload._fetchBucketRows=async(id,bucket,days,window,opts)=>{
        opts.onPartial([{key:'DL-1'}],1,new Error('Epic unavailable'));
        return {status:'partial',rows:[{key:'DL-1'}],error:new Error('Epic unavailable')};
      };
      await workload._loadDetailBucket('a','open',{resetRows:false});
      assert.equal(workload.tkd.a.open[0].epicName,'Confirmed Epic');
      assert.equal(workload.bucketStateOf('a','open').status,'partial');
    '''.replace("STATIC", json.dumps((REPO_ROOT / "app/static").as_uri()))
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
