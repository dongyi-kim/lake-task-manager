"""Cache permission removals and Tracking mutations cannot resurrect prior display data."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for dashboard controller regressions")
    script = script.replace("STATIC", json.dumps((REPO_ROOT / "app/static").as_uri()))
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_background_permission_removals_clear_only_matching_dashboard_scope():
    run_node(r'''
      import assert from 'node:assert/strict';
      globalThis.window={location:{origin:'http://local'},addEventListener(){},removeEventListener(){},dispatchEvent(){}};
      globalThis.localStorage={getItem(){return null}};
      const {default:Workload}=await import(STATIC+'/components/views/WorkloadView.js');
      const {default:Vit}=await import(STATIC+'/components/views/VitView.js');
      const bind=view=>{const vm=view.data();for(const [key,fn] of Object.entries(view.methods))vm[key]=fn.bind(vm);for(const [key,fn] of Object.entries(view.computed||{}))Object.defineProperty(vm,key,{get:()=>fn.call(vm)});return vm;};
      const vm=bind(Workload), ticket={key:'DL-1',due:'2000-01-01'};
      vm.mod='M';vm.d={modules:[{module:'M',people:[{id:'a'}]}]};vm.scheduleMeasure=()=>{};
      vm.pstat={a:{id:'a',name:'Alice'}};vm.tkd={a:{open:[ticket],inProgress:[]}};
      vm.dueRisk={over:[{t:ticket}],parts:{'a|open':{id:'a',status:'success',rows:[ticket]}}};
      vm.applyCacheUpdate({path:'/api/workload/person/a?days=14&assignedWindow=all',value:null,removed:true});
      assert.equal(vm.tkd.a.open.length,1);
      vm.applyCacheUpdate({path:'/api/workload/person/a?days=7&assignedWindow=all',value:null,removed:true});
      assert.equal(vm.pstat.a.errorKind,'permission');assert.equal(vm.tkd.a,undefined);assert.equal(vm.dueRisk.over.length,0);
      vm.pstat.a={id:'a'};vm.tkd.a={open:[ticket],done7d:[{key:'DL-2'}]};
      vm.applyCacheUpdate({path:'/api/workload/a/open?days=7&assignedWindow=all',value:null,removed:true});
      assert.deepEqual(vm.tkd.a.open,[]);assert.equal(vm.tkd.a.done7d[0].key,'DL-2');
      const vit=bind(Vit);vit.mods={M:[{key:'DL-1'}],Other:[{key:'DL-2'}]};vit.detail={'DL-1':{tree:[ticket]},'DL-2':{tree:[]}};
      vit.applyCacheUpdate({path:'/api/vit/module/M',value:null,removed:true});
      assert.deepEqual(vit.mods.M,[]);assert.equal(vit.detail['DL-1'],undefined);assert.equal(vit.mods.Other.length,1);
      vit.applyCacheUpdate({path:'/api/vit/DL-2',value:null,removed:true});
      assert.equal(vit.detail['DL-2'],undefined);assert.deepEqual(vit.mods.Other,[]);
    ''')


def test_tracking_resume_and_summary_do_not_reopen_the_prior_terminal_memo():
    run_node(r'''
      import assert from 'node:assert/strict';
      globalThis.window={addEventListener(){},removeEventListener(){},dispatchEvent(){}};
      const {default:View}=await import(STATIC+'/components/tracking-view.js');
      const {api}=await import(STATIC+'/lib/api.js');
      const vm=View.data();for(const [key,fn] of Object.entries(View.methods))vm[key]=fn.bind(vm);
      for(const [key,fn] of Object.entries(View.computed))Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
      vm.$nextTick=()=>{};vm._epoch=0;vm.active=false;vm.options.aiEnabled=true;
      let server={id:'saved',status:'partial',request:{people:[],modules:[]},issues:[],tree:[],events:[]};
      globalThis.fetch=async(path,opts={})=>{
        if(path.endsWith('/resume'))server={...server,status:'running'};
        if(path.endsWith('/summary')){
          const summary={items:[{text:'Saved summary'}],usage:{totalTokens:3}};
          server={...server,aiSummary:summary};return new Response(JSON.stringify(summary));
        }
        return new Response(JSON.stringify(server));
      };
      await api.readCached('/api/tracking/runs/saved');
      vm.adoptRun(structuredClone(server));await vm.runAction('resume');
      await vm.openRun('saved');assert.equal(vm.run.status,'running');
      server={...server,status:'completed'};vm.run=structuredClone(server);
      await vm.summarize();vm.run=null;vm.ai=null;
      await vm.openRun('saved');assert.equal(vm.run.aiSummary.items[0].text,'Saved summary');
    ''')
