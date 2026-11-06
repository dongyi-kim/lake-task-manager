"""Pagination order and stale ancestor stubs cannot hide collected documents."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_tree_keeps_authoritative_titles_parents_and_all_collected_pages():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    model = (REPO_ROOT / "app/static/lib/confluenceTreeModel.js").as_uri()
    script = """
      import { pageTree } from MODEL;
      import assert from 'node:assert/strict';
      const root = {id:'1', title:'Root', ancestors:[]};
      const moved = {id:'2', title:'Current name', ancestors:[]};
      const child = {id:'3', title:'Nested', ancestors:[root, {id:'2', title:'Old name'}]};
      for (const pages of [[root,moved,child], [child,moved,root]]) {
        const tree = pageTree(pages, {'1':true, '2':true}, 'currentname');
        assert.equal(tree.nodes.get('2').title, 'Current name');
        assert.equal(tree.nodes.get('2').parent, '');
        assert.deepEqual(tree.matches, ['2']);
        assert.equal(tree.rows.length, 3);
      }
      // The first page precedes a move while the second follows it. Keep both
      // reachable, rather than visiting no roots and displaying an empty tree.
      const cycle = pageTree([
        {id:'1', title:'A', ancestors:[{id:'2', title:'B'}]},
        {id:'2', title:'B', ancestors:[{id:'1', title:'A'}]},
        {id:'3', title:'C', ancestors:[{id:'1', title:'A'}]},
      ], {'1':true, '2':true});
      assert.deepEqual(cycle.rows.map(row=>row.id).sort(), ['1','2','3']);
      // Child-first pagination still creates the missing ancestor path and
      // does not require fetching a body or another per-page request.
      const partial = pageTree([child], {'1':true, '2':true});
      assert.deepEqual(partial.rows.map(row=>row.id), ['1','2','3']);
    """.replace("MODEL", json.dumps(model))
    subprocess.run([node, "--input-type=module", "-e", script], check=True, capture_output=True, text=True)
