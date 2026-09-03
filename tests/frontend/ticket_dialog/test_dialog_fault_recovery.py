"""TicketDialog panel scheduling and recovery contracts."""

import json
import shutil
import subprocess

import pytest

from support.paths import REPO_ROOT


DIALOG = REPO_ROOT / "app" / "static" / "components" / "ui" / "TicketDialog.js"
RECOVERY = REPO_ROOT / "app" / "static" / "components" / "ticket" / "panelRecovery.js"


def test_secondary_panels_start_in_priority_order_without_an_all_settled_barrier():
    src = DIALOG.read_text(encoding="utf-8")
    recovery = RECOVERY.read_text(encoding="utf-8")
    load = src[src.index("async load(force, quiet)"):src.index("typeColor(t)")]

    assert "await vp;" in load
    assert '"editmeta", "childTypes", "ancestors", "comments", "siblings", "attachments"' in recovery
    assert '"documents", "children", "related"' in recovery
    assert "DIALOG_PANEL_ORDER.forEach" in load
    assert "_loadDialogPanel(name, key, my)" in load
    assert "Promise.allSettled" not in load
    assert load.index("await vp;") < load.index("DIALOG_PANEL_ORDER.forEach") < load.index("this.loadTimeline(key, my)")


def test_each_panel_preserves_failure_state_and_can_retry_after_auth():
    src = DIALOG.read_text(encoding="utf-8")

    assert "panelState: {}" in src
    assert 'this._setPanelState(name, "error"' in src
    assert "this.retryFailedPanels();" in src
    assert 'if (this.timelineErr) this.retryTimeline();' in src
    assert 'retryPanel(name) { return this._loadDialogPanel(name, this.keyId, this._req); }' in src
    assert 'v-for="name in failedAuxPanels"' in src
    assert "{{ panelLabel(name) }} 재시도" in src
    assert "panelError('comments')" in src
    assert "@click=\"retryPanel('comments')\"" in src
    assert "panelReady('related') && !related.length" in src
    assert "panelReady('attachments') && !atts.length" in src
    assert "panelReady('documents') && !refDocs.length" in src
    assert "this.related.length > 0" in src and "this.mentionDocs.length > 0" in src


def test_panel_recovery_helpers_are_pure_and_select_only_failed_panels():
    node = shutil.which("node")
    if not node:
        bundled = REPO_ROOT.parents[3] / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
        node = str(bundled) if bundled.exists() else None
    if not node:
        pytest.skip("Node.js is not installed")

    script = f"""
      import {{ DIALOG_PANEL_ORDER, panelStatus, panelsInState, requestErrorText, setPanelStatus }}
        from {json.dumps(RECOVERY.as_uri())};
      const initial = {{}};
      const loading = setPanelStatus(initial, 'comments', 'loading');
      const failed = setPanelStatus(loading, 'attachments', 'error', 'HTTP 401');
      process.stdout.write(JSON.stringify({{
        order: DIALOG_PANEL_ORDER,
        initialUnchanged: Object.keys(initial).length === 0,
        loading: panelStatus(failed, 'comments').state,
        idle: panelStatus(failed, 'missing').state,
        failed: panelsInState(failed, 'error'),
        message: requestErrorText(new Error('boom')),
      }}));
    """
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], check=True, capture_output=True, text=True,
    )
    assert json.loads(completed.stdout) == {
        "order": ["editmeta", "childTypes", "ancestors", "comments", "siblings", "attachments",
                  "documents", "children", "related"],
        "initialUnchanged": True,
        "loading": "loading",
        "idle": "idle",
        "failed": ["attachments"],
        "message": "boom",
    }
