"""Fresh saved branches stay authoritative over old descendants and late tree frames."""
import json
import shutil
import subprocess

import pytest
from support.paths import REPO_ROOT


@pytest.mark.parametrize("current_tree", [False, True])
def test_saved_branch_refresh_prunes_removed_descendants_and_updates_both_trees(current_tree):
    script = r'''
      import assert from 'node:assert/strict';
      globalThis.window=new EventTarget();
      const {api}=await import(API);
      const Saved=(await import(SAVED)).default, Tree=(await import(TREE)).default;
      const {ConfluenceTreeQueue}=await import(QUEUE);
      const row=(id,title,ancestors=[])=>({id:String(id),title,ancestors,space:{key:'ETL'},hasChildren:true});
      const root=row(1,'Root'), removed=row(2,'Removed',[root]), grand=row(3,'Orphan',[root,removed]);
      const old=row(4,'Old child',[root]), child=row(5,'Kept descendant',[root,old]);
      const fresh={...old,title:'Renamed child'};
      const previous=[root,removed,grand,old,child];
      let reads=0;
      api.confluenceTreeBranch=async()=>{reads++;return {results:[fresh],nextStart:null};};
      const context={...Saved.data(),...Saved.methods,space:'ETL',favorites:[root],recentDocuments:[],
        treePages:CURRENT?[root,old,child]:[],expanded:{},query:'',searchOverrides:{},
        prioritizeTree:()=>{},savedTreePages:{ETL:CURRENT?[root,old,child]:previous}};
      if(CURRENT){
        context._treeQueue=new ConfluenceTreeQueue({changed:rows=>context.treePages=rows,state:()=>{}});
        context._treeQueue.errors.set('1:50',Object.assign(new Error('previous failure'),{job:{parent:'1',start:50}}));
      }
      await context.expandSavedTree(root);
      if(CURRENT) assert.equal(context._treeQueue.errors.size,0,'successful refresh clears old errors for all parent offsets');
      const nodes=Saved.computed.savedTreeNodes.call(context);
      assert(!nodes.has('2')&&!nodes.has('3'),'removed subtree cannot return through ancestor stubs');
      assert(nodes.has('5'),'a retained child keeps its known descendants');
      assert.equal(nodes.get('4').title,'Renamed child','fresh branch wins over the current tree snapshot');
      if(CURRENT){
        context._treeQueue.indexRows=new Map(previous.map(row=>[row.id,row]));
        context._treeQueue.publish(); // A late index frame must not undo the explicit refresh.
        const current=Tree.computed.tree.call(context).nodes;
        assert(!current.has('2')&&!current.has('3'));
        assert.equal(current.get('4').title,'Renamed child');
      }
      assert.equal(reads,1,'do not fetch the already returned branch a second time');
      api.confluenceTreeBranch=async()=>({results:[{...fresh,hasChildren:false}],nextStart:null});
      await context.expandSavedTree(root);
      assert(!Saved.computed.savedTreeNodes.call(context).has('5'),'a confirmed leaf drops old descendants');
      if(CURRENT){
        const queue=context._treeQueue; let reject;
        queue.read=()=>new Promise((_,fail)=>reject=fail);
        queue.enqueue({parent:'1',start:0,depth:0,refresh:true});
        const late=queue.run(), newer=row(6,'New branch',[root]);
        queue.acceptBranch(root,[{...newer,hasChildren:false}]);
        reject(Object.assign(new Error('old permission result'),{status:403}));
        await late;
        assert(Tree.computed.tree.call(context).nodes.has('6'),'an old read error cannot hide a newer successful branch');
      }
    '''.replace('CURRENT', str(current_tree).lower())
    for token, path in (("API", "api.js"), ("SAVED", "confluenceSavedTree.js"), ("TREE", "confluenceTree.js"), ("QUEUE", "confluenceTreeQueue.js")):
        script = script.replace(token, json.dumps((REPO_ROOT / "app/static/lib" / path).as_uri()))
    result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_late_child_refresh_cannot_restore_a_removed_subtree_but_sibling_can_finish():
    script = r'''
      import assert from 'node:assert/strict';
      globalThis.window=new EventTarget();
      const {api}=await import(API), Saved=(await import(SAVED)).default;
      const row=(id,ancestors=[])=>({id:String(id),title:'Page '+id,ancestors,space:{key:'ETL'},hasChildren:true});
      const parent=row(1), child=row(2,[parent]), grand=row(3,[parent,child]), sibling=row(8), leaf=row(9,[sibling]);
      let finishChild,finishSibling;
      api.confluenceTreeBranch=(_,job)=>job.parent==='2'?new Promise(r=>finishChild=r):
        job.parent==='8'?new Promise(r=>finishSibling=r):Promise.resolve({results:[],nextStart:null});
      const context={...Saved.data(),...Saved.methods,space:'ETL',favorites:[parent,sibling],recentDocuments:[],
        treePages:[],savedTreePages:{ETL:[parent,child,grand,sibling]},prioritizeTree:()=>{}};
      const a=context.expandSavedTree(child),b=context.expandSavedTree(sibling);
      await context.expandSavedTree(parent);
      finishChild({results:[grand],nextStart:null}); finishSibling({results:[leaf],nextStart:null});
      await Promise.all([a,b]);
      const nodes=Saved.computed.savedTreeNodes.call(context);
      assert(!nodes.has('2')&&!nodes.has('3'),'older child response cannot resurrect a pruned branch');
      assert(nodes.has('9'),'independent sibling responses must still be accepted');
    '''
    for token, path in (("API", "api.js"), ("SAVED", "confluenceSavedTree.js")):
        script=script.replace(token,json.dumps((REPO_ROOT/'app/static/lib'/path).as_uri()))
    result=subprocess.run([shutil.which('node'),'--input-type=module','-e',script],
                          text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
