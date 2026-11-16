"""Hierarchy width stays a local, accessible display preference."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    static = (REPO_ROOT / "app/static").as_uri()
    setup = r'''
      import assert from 'node:assert/strict';
      const listeners = new Map(), storage = new Map();
      globalThis.window = {
        addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
        removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
        localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
      };
      globalThis.document = {body: {style: {cursor:'auto', userSelect:'text'}},createElement(){return {};}};
      let observer;
      globalThis.ResizeObserver = class {constructor(fn) {this.notify=fn;observer=this;} observe() {this.connected=true;} disconnect() {this.connected=false;}};
      const {default:component} = await import(STATIC + '/components/tracking-splitter.js');
      function make(width=300, available=1100) {
        const parent = {clientWidth:available}, emitted=[];
        const vm = {width, controls:'tracking-hierarchy', storageKey:'test-width', $el:{parentElement:parent},
          $emit(name, value) {emitted.push([name,value]);this.width=value;},
          ...component.data.call({width})};
        for (const [key, fn] of Object.entries(component.methods)) vm[key]=fn.bind(vm);
        for (const [key, fn] of Object.entries(component.computed)) Object.defineProperty(vm,key,{get:()=>fn.call(vm)});
        return {vm,parent,emitted};
      }
      const active = name => listeners.get(name)?.size || 0;
      const fire = (name,event={}) => [...listeners.get(name) || []].forEach(fn=>fn(event));
    '''.replace("STATIC", json.dumps(static))
    result = subprocess.run([node, "--input-type=module", "-e", setup + script],
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr


def test_keyboard_presets_and_window_bounds_preserve_preferred_width():
    run_js(r'''
      const {vm,parent,emitted}=make();
      component.mounted.call(vm);
      const key = key => vm.resizeKey({key,preventDefault(){}});
      key('ArrowRight');assert.equal(vm.width,316);
      key('ArrowLeft');assert.equal(vm.width,300);
      key('End');assert.equal(vm.width,600);
      parent.clientWidth=900;observer.notify();assert.equal(vm.width,468);
      assert.equal(storage.get('test-width'),'600');
      parent.clientWidth=1200;fire('resize');assert.equal(vm.width,600);
      key('Home');assert.equal(vm.width,300);
      vm.width=560;component.watch.width.call(vm,560);assert.equal(storage.get('test-width'),'560');
      vm.width=900;component.watch.width.call(vm,900);assert.equal(vm.width,600);
      key('ArrowLeft');assert.equal(vm.width,584);
      vm.resetWidth();assert.equal(vm.width,300);
      assert.ok(emitted.every(([name,width])=>name==='resize' && Number.isInteger(width)));
      component.beforeUnmount.call(vm);assert.equal(active('resize'),0);assert.equal(observer.connected,false);
    ''')


def test_drag_tracks_one_pointer_and_cleans_capture_and_global_styles():
    run_js(r'''
      const {vm}=make();component.mounted.call(vm);
      let captured=null;
      const grip={setPointerCapture(id){captured=id;},hasPointerCapture(id){return captured===id;},releasePointerCapture(){captured=null;}};
      const start={button:0,isPrimary:true,pointerId:7,clientX:300,currentTarget:grip,preventDefault(){}};
      vm.startResize({...start,button:2});assert.equal(captured,null);
      vm.startResize(start);assert.equal(captured,7);assert.equal(vm.dragging,true);
      assert.equal(document.body.style.userSelect,'none');assert.equal(active('pointermove'),1);
      fire('pointermove',{pointerId:8,clientX:550});assert.equal(vm.width,300);
      fire('pointermove',{pointerId:7,clientX:550});assert.equal(vm.width,550);
      fire('pointerup',{pointerId:8});assert.equal(vm.dragging,true);
      fire('pointercancel',{pointerId:7});assert.equal(vm.dragging,false);assert.equal(captured,null);
      assert.equal(active('pointermove'),0);assert.equal(document.body.style.userSelect,'text');
      assert.equal(document.body.style.cursor,'auto');assert.equal(storage.get('test-width'),'550');
      vm.startResize(start);component.deactivated.call(vm);
      assert.equal(captured,null);assert.equal(active('resize'),0);assert.equal(active('pointerup'),0);
      component.activated.call(vm);assert.equal(active('resize'),1);
      component.beforeUnmount.call(vm);assert.equal(active('resize'),0);
    ''')


def test_saved_width_restores_and_storage_failure_does_not_break_resizing():
    run_js(r'''
      storage.set('test-width','560');
      const {vm,parent}=make(300,900);component.mounted.call(vm);
      assert.equal(vm.width,468);
      parent.clientWidth=1200;fire('resize');assert.equal(vm.width,560);
      component.beforeUnmount.call(vm);
      storage.set('test-width','invalid');
      const next=make();component.mounted.call(next.vm);assert.equal(next.vm.width,300);
      window.localStorage.setItem=()=>{throw new Error('Storage disabled');};
      next.vm.resizeKey({key:'ArrowRight',preventDefault(){}});assert.equal(next.vm.width,316);
      component.beforeUnmount.call(next.vm);
    ''')


def test_compiled_separator_exposes_current_bounds_and_controls():
    run_js(r'''
      const {vm}=make();component.mounted.call(vm);
      const {compile}=await import(STATIC + '/vendor/vue.esm-browser.prod.js');
      const render=compile(component.template,{decodeEntities:raw=>raw,onError(error){throw error;}});
      const node=render.call(vm,vm,[]);
      assert.equal(node.props.role,'separator');assert.equal(node.props.tabindex,'0');
      assert.equal(node.props['aria-orientation'],'vertical');assert.equal(node.props['aria-controls'],'tracking-hierarchy');
      assert.equal(node.props['aria-valuemin'],240);assert.equal(node.props['aria-valuemax'],600);
      assert.equal(node.props['aria-valuenow'],300);assert.equal(typeof node.props.onPointerdown,'function');
      component.beforeUnmount.call(vm);
    '''.replace("STATIC", json.dumps((REPO_ROOT / "app/static").as_uri())))
