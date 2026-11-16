"""The existing status poll forwards its first and subsequent ticket revisions."""
import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


def test_existing_status_poll_forwards_initial_and_changed_ticket_revisions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the status-poll component test")
    source = (REPO_ROOT / "app/static/components/ui/StatusBanner.js").read_text(encoding="utf-8")
    source = source.replace('import { api } from "../../lib/api.js";', 'const api = globalThis.testApi;')
    script = """
      import assert from 'node:assert/strict';
      let revision='scope:0', reads=0;
      const received=[], timers=[];
      globalThis.testApi={
        raw:async () => { reads++; return {mode:'ok',ticketRevision:revision}; },
        ticketRevisionUpdated:token => received.push(token),
      };
      globalThis.window={addEventListener(){},removeEventListener(){}};
      globalThis.setInterval=(fn,ms) => {timers.push({fn,ms}); return 1;};
      globalThis.clearInterval=()=>{};
      const Banner=(await import('data:text/javascript;base64,'+Buffer.from(SOURCE).toString('base64'))).default;
      const view={...Banner.data(),...Banner.methods};
      Banner.mounted.call(view);
      await new Promise(resolve=>setImmediate(resolve));
      assert.deepEqual(received,['scope:0']);
      assert.equal(timers.length,1);
      assert.equal(timers[0].ms,8000);
      revision='scope:1'; timers[0].fn.call(view);
      await new Promise(resolve=>setImmediate(resolve));
      assert.deepEqual(received,['scope:0','scope:1']);
      assert.equal(reads,2);
      Banner.unmounted.call(view);
    """.replace("SOURCE", json.dumps(source))
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
