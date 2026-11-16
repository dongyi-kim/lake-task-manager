"""Rendered comment folds follow actual line geometry, including wrapped text."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_comment_folding_counts_rendered_lines_and_resets_for_new_evidence():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    module = (REPO_ROOT / "app/static/components/tracking-comment.js").as_uri()
    result = subprocess.run([node, "--input-type=module", "-e", """
      import assert from 'node:assert/strict';
      const {default: component, commentLineLayout} = await import(MODULE);
      // Multiple inline fragments and table cells on a row are one rendered line.
      assert.deepEqual(commentLineLayout([
        {top:0,bottom:20}, {top:0,bottom:20}, {top:26,bottom:46}
      ], 0, 20), {foldable:false, previewHeight:46});
      // Three lines can arise from wrapping without any newline in the source.
      assert.deepEqual(commentLineLayout([
        {top:0,bottom:20}, {top:20,bottom:40}, {top:40,bottom:60}
      ], 0, 20), {foldable:true, previewHeight:40});
      const state = {...component.data(), expanded:true, foldable:true,
        $nextTick: callback => callback(), measure: () => {}};
      component.watch.event.handler.call(state);
      assert.equal(state.expanded, false);
      assert.equal(state.foldable, false);
    """.replace("MODULE", json.dumps(module))], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
